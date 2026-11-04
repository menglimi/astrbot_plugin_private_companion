"""Native discovery and topology management on isolated AstrBot/V2 data."""

from copy import deepcopy
import asyncio
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch

from tests.test_astrbot_staging_host import WebContext, host_config
from tests.native_adapter_support import FakeEvent, FakeProvider, FakeReverseApi
from tests.test_management_access import NOW
from tests.group_host_support import GroupFixture
from astrbot_host.account_session_api import AccountSessionWebAPI
from astrbot_host.controller import AstrBotStagingController
from astrbot_host import native as native_host
from companion_v2.adapters.astrbot.staging import NativeAdapterTypes
from companion_v2.execution.models import TaskError


class SessionTopologyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        native_host._session_settings_cache.clear()
        native_host._session_settings_reads.clear()
        native_host._session_settings_versions.clear()
        self.temporary = TemporaryDirectory(prefix="companion-session-directory-")
        self.root = Path(self.temporary.name)
        self.native = WebContext()
        self.native.platform.client._wsr_api_clients = {"100": object()}
        self.config = host_config()
        types = NativeAdapterTypes("4.27.2", FakeEvent, FakeProvider, NS, NS, FakeReverseApi)
        self.controller = AstrBotStagingController(self.native, self.config, self.root,
                                                   native_types=types, now=lambda: NOW)
        self.api = AccountSessionWebAPI(self.controller)
        self.sessions = {"qq-main:GroupMessage:300": "group-conversation",
                         "qq-main:GroupMessage:200_300": "member-conversation"}
        self.preferences = {}
        self.metadata_calls = []

        async def current(umo):
            return self.sessions.get(umo)

        async def metadata(**kwargs):
            self.metadata_calls.append(kwargs)
            return ([{"session_id": umo, "conversation_id": cid, "persona_id": "original"}
                     for umo, cid in self.sessions.items()], len(self.sessions))

        async def preferences_get(**kwargs):
            return deepcopy(self.preferences.get(kwargs["scope_id"], {}))

        async def preferences_put(**kwargs):
            self.preferences[kwargs["scope_id"]] = deepcopy(kwargs["value"])

        async def resolve(**kwargs):
            selected = kwargs.get("conversation_persona_id") or "original"
            return selected, {"name": selected}, None, False

        self.native.conversation_manager = NS(get_curr_conversation_id=current,
            db=NS(get_session_conversations=metadata),
            get_conversation=AsyncMock(side_effect=AssertionError("history must not be loaded")))
        self.native.persona_manager = NS(resolve_selected_persona=resolve,
                                        personas={"original": {}, "second": {}},
                                        personas_v3=[{"name": "original"}, {"name": "second"}])
        self.addCleanup(patch.stopall)
        patch("astrbot_host.native.sp.get_async", preferences_get).start()
        patch("astrbot_host.account_session_api.sp.put_async", preferences_put).start()

    async def asyncTearDown(self):
        await self.controller.terminate()
        self.temporary.cleanup()

    async def call(self, method, body=None, username="admin"):
        request = NS(username=username, query={}, body=AsyncMock(return_value=json.dumps(body or {}).encode("utf-8")))
        with patch("astrbot_host.account_session_api.request", request), patch("astrbot_host.setup_api.request", request), \
                patch("astrbot_host.setup_api.display_names", return_value=NS(targets=AsyncMock(side_effect=lambda rows: rows))):
            return await getattr(self.api, method)()

    def group_event(self, *, unique=False):
        event = FakeEvent(self.native)
        kind = NS(value="GroupMessage")
        event.message_obj.type = event.session.message_type = kind
        event.message_obj.group_id = event.message_obj.session_id = "300"
        event.session.session_id = "200_300" if unique else "300"
        event.unified_msg_origin = f"qq-main:GroupMessage:{event.session.session_id}"
        event.message_obj.raw_message = dict(post_type="message", message_type="group", self_id=100,
            user_id=200, sender={"user_id": 200}, group_id=300, message_id="event-1")
        return event

    async def test_existing_group_and_member_sessions_appear_without_model_calls_or_history_reads(self):
        with patch.object(self.native.persona_manager, "resolve_selected_persona",
                          side_effect=AssertionError("session topology must use V2 bindings")):
            response = await self.call("get")
        self.assertEqual(response["status"], "ok", response)
        targets = response["data"]["discovered"]["targets"]
        groups = [row for row in targets if row["scope_kind"] == "group"]
        self.assertEqual(len(groups), 2)
        self.assertEqual({row["group_id"] for row in groups}, {"300"})
        self.assertEqual({row["account_id"] for row in groups}, {"100"})
        self.assertEqual({row["persona_id"] for row in groups}, {""})
        self.assertEqual(len({row["session_key"] for row in groups}), 2)
        self.assertTrue(all(not row["managed"] for row in groups))
        self.assertFalse(self.controller.session_topology.path.exists())
        self.assertIsNone(self.controller.runtime)
        self.assertEqual(self.native.provider.client.state.calls, [])
        self.assertEqual(self.native.platform.client._api._wsr_api.calls, [])
        self.assertEqual(self.metadata_calls[0]["platform"], "qq-main")

    async def test_directory_timeout_identifies_the_failed_step(self):
        async def blocked(**kwargs):
            await asyncio.Event().wait()

        with patch.object(self.native.conversation_manager.db, "get_session_conversations", blocked), \
                patch("companion_v2.adapters.astrbot.native.NATIVE_LOOKUP_TIMEOUT_SECONDS", 0.01):
            response = await self.call("get")
        self.assertEqual(response["status"], "ok", response)
        discovered = response["data"]["discovered"]
        self.assertEqual(discovered["directory"]["complete"], False)
        self.assertEqual(discovered["directory"]["issues"][0]["code"],
                         "host_native_lookup_timeout__session_directory")
        self.assertEqual(discovered["directory"]["issues"][0]["stage"], "session_directory")
        self.assertTrue(any(row.get("participant_id") == "200" for row in discovered["targets"]))

    async def test_one_session_settings_timeout_keeps_other_sessions_and_does_not_guess_identity(self):
        failing = "qq-main:GroupMessage:300"

        async def services(umo):
            if umo == failing:
                await asyncio.Event().wait()
            return {}

        with patch.object(self.controller.session_topology, "services", services), \
                patch("companion_v2.adapters.astrbot.native.NATIVE_LOOKUP_TIMEOUT_SECONDS", 0.01):
            report = await self.controller.session_topology.inventory_report()
        self.assertFalse(report["complete"])
        self.assertIn("host_native_lookup_timeout__session_settings", {item["code"] for item in report["issues"]})
        partial = next(row for row in report["targets"] if row.get("unified_msg_origin") == failing)
        self.assertEqual(partial["scope_kind"], "group")
        self.assertEqual(partial["group_id"], "300")
        self.assertEqual(partial["account_id"], "")
        self.assertEqual(partial["bot_id"], "")
        self.assertEqual(partial["persona_id"], "")
        self.assertEqual(partial["persona_source"], "unresolved")
        self.assertEqual(partial["roles"], [])
        self.assertFalse(partial["managed"])
        self.assertFalse(partial["details_available"])
        complete = next(row for row in report["targets"]
                        if row.get("unified_msg_origin") == "qq-main:GroupMessage:200_300")
        self.assertTrue(complete["details_available"])
        self.assertEqual(complete["persona_id"], "")

    async def test_multiple_logins_require_event_evidence_and_observation_survives_reload(self):
        self.native.platform.client._wsr_api_clients["101"] = object()
        inventory = await self.controller.session_topology.inventory()
        self.assertTrue(all(row["account_id"] == "" for row in inventory if row["scope_kind"] == "group"))
        forged = self.group_event()
        forged.bot = object()
        await self.controller.session_topology.observe(forged)
        self.assertFalse(self.controller.session_topology.path.exists())
        event = self.group_event()
        await self.controller.session_topology.observe(event)
        await self.controller.session_topology.observe(event)
        stored = json.loads(self.controller.session_topology.path.read_text(encoding="utf-8"))
        self.assertEqual(len(stored), 1)
        self.assertNotIn("message", json.dumps(stored))
        from astrbot_host.session_topology import SessionTopology
        self.controller.session_topology = SessionTopology(self.controller)
        rows = await self.controller.session_topology.inventory()
        row = next(row for row in rows if row.get("unified_msg_origin") == event.unified_msg_origin)
        self.assertEqual(row["account_id"], "100")
        self.sessions.pop(event.unified_msg_origin)
        rows = await self.controller.session_topology.inventory()
        self.assertFalse(any(row.get("unified_msg_origin") == event.unified_msg_origin for row in rows))

    async def test_group_name_upgrade_replaces_id_and_survives_missing_metadata_and_reload(self):
        from astrbot_host.session_topology import SessionTopology
        event = self.group_event()
        event.message_obj.sender.nickname = "发言人的名字"
        event.message_obj.group = NS(group_name="300")
        topology = self.controller.session_topology
        await topology.observe(event)
        rows = await topology.inventory()
        row = next(row for row in rows if row.get("unified_msg_origin") == event.unified_msg_origin)
        self.assertNotIn("group_name", row)
        # Older releases stored the speaker as display_name on a group session.
        stored = topology._observed()
        for value in stored.values():
            value.update(display_name="发言人的名字", group_name="300")
        topology.path.write_text(json.dumps(stored), encoding="utf-8")
        rows = await topology.inventory()
        row = next(row for row in rows if row.get("unified_msg_origin") == event.unified_msg_origin)
        self.assertNotIn("display_name", row)
        event.message_obj.group.group_name = "识别出的群名称"
        await topology.observe(event)
        self.controller.session_topology = topology = SessionTopology(self.controller)
        event.message_obj.group = None
        await topology.observe(event)
        rows = await topology.inventory()
        row = next(row for row in rows if row.get("unified_msg_origin") == event.unified_msg_origin)
        self.assertEqual(row["display_name"], "识别出的群名称")
        self.assertEqual(row["group_name"], "识别出的群名称")
        self.assertNotIn("display_name", next(iter(topology._observed().values())))
        event.message_obj.group = NS(group_name="改名后的群")
        await topology.observe(event)
        rows = await topology.inventory()
        row = next(row for row in rows if row.get("unified_msg_origin") == event.unified_msg_origin)
        self.assertEqual(row["display_name"], "改名后的群")

    async def test_observed_group_names_are_account_scoped_even_when_native_reads_fail(self):
        from astrbot_host.session_topology import SessionTopology
        self.native.platform.client._wsr_api_clients["101"] = object()
        umo = "qq-main:GroupMessage:300"
        observed = {umo: [dict(unified_msg_origin=umo, account_id=account,
                              conversation_id=self.sessions[umo], group_name=name,
                              last_seen_at="2026-01-01T00:00:00+00:00")
                          for account, name in (("100", "账号一群名"), ("101", "账号二群名"))]}
        topology = self.controller.session_topology
        with patch.object(topology, "_observed", return_value=dict(enumerate(observed[umo]))):
            rows = await topology.inventory(scope_kind="group")
        names = {row["account_id"]: row.get("display_name") for row in rows
                 if row.get("unified_msg_origin") == umo}
        self.assertEqual(names, {"100": "账号一群名", "101": "账号二群名"})
        rows = SessionTopology._partial_windows(umo, topology.accounts(), [], observed)
        self.assertEqual({row["account_id"]: row["display_name"] for row in rows}, names)

    async def test_background_writes_are_serial_off_thread_and_termination_drains_them(self):
        topology = self.controller.session_topology
        entered, release = threading.Event(), threading.Event()
        loop_thread = threading.get_ident()
        writer_threads = []
        original = topology._store_observation

        def slow_store(key, row):
            writer_threads.append(threading.get_ident())
            entered.set()
            if not release.wait(5):
                raise AssertionError("test did not release the directory writer")
            original(key, row)

        topology._store_observation = slow_store
        first = topology.schedule_observe(self.group_event())
        second = topology.schedule_observe(self.group_event(unique=True))
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
            self.assertEqual(len(writer_threads), 1)
            closing = asyncio.create_task(self.controller.terminate())
            await asyncio.sleep(0)
            self.assertFalse(closing.done())
            self.assertFalse(first.done())
            self.assertFalse(second.done())
            self.assertIsNone(topology.schedule_observe(self.group_event()))
        finally:
            release.set()
        await asyncio.wait_for(closing, 2)

        self.assertEqual(len(writer_threads), 2)
        self.assertTrue(all(identifier != loop_thread for identifier in writer_threads))
        stored = json.loads(topology.path.read_text(encoding="utf-8"))
        self.assertEqual(len(stored), 2)
        self.assertEqual({row["conversation_id"] for row in stored.values()},
                         {"group-conversation", "member-conversation"})
        self.assertFalse(topology._pending)

    async def test_background_native_lookup_timeout_does_not_leave_an_observation(self):
        topology = self.controller.session_topology

        async def blocked(umo):
            await asyncio.Event().wait()

        with patch.object(self.native.conversation_manager, "get_curr_conversation_id", blocked), \
                patch("companion_v2.adapters.astrbot.native.NATIVE_LOOKUP_TIMEOUT_SECONDS", 0.01):
            pending = topology.schedule_observe(self.group_event())
            await asyncio.wait_for(topology.close(), 2)

        self.assertTrue(pending.done())
        self.assertFalse(topology.path.exists())
        self.assertFalse(topology._pending)

    async def test_session_controls_change_native_settings_and_preserve_other_services(self):
        row = next(row for row in await self.controller.session_topology.inventory() if row["scope_kind"] == "group")
        umo = row["unified_msg_origin"]
        self.preferences[umo] = {"tts_enabled": False, "custom_name": "测试群"}
        response = await self.call("save_session", {"session_key": row["session_key"], "llm_enabled": False,
                                                   "session_enabled": False, "persona_id": "second"})
        self.assertEqual(response["status"], "ok", response)
        self.assertEqual(self.preferences[umo], {"tts_enabled": False, "custom_name": "测试群", "llm_enabled": False,
                                                "session_enabled": False, "persona_id": "second"})
        inventory = await self.controller.session_topology.inventory()
        updated = next(item for item in inventory if item["session_key"] == row["session_key"])
        self.assertEqual(updated["persona_id"], "second")
        self.assertFalse(updated["llm_enabled"])
        self.assertFalse(updated["session_enabled"])
        self.assertEqual(updated["persona_override"], "second")
        event = self.group_event(unique="_" in umo)
        self.assertFalse(await self.controller.session_topology.reply_enabled(event))
        response = await self.call("save_session", {"session_key": row["session_key"], "persona_id": "",
                                                   "llm_enabled": True, "session_enabled": True})
        self.assertEqual(response["status"], "ok", response)
        self.assertNotIn("persona_id", self.preferences[umo])
        restored = next(item for item in await self.controller.session_topology.inventory() if item["session_key"] == row["session_key"])
        self.assertEqual(restored["persona_override"], "")
        self.assertEqual(restored["persona_id"], "")
        self.assertTrue(await self.controller.session_topology.reply_enabled(event))
        self.assertEqual(self.native.platform.client._api._wsr_api.calls, [])

    async def test_session_directory_does_not_replace_v2_persona_override_with_astrbot_persona(self):
        row = next(row for row in await self.controller.session_topology.inventory() if row["scope_kind"] == "group")
        umo = row["unified_msg_origin"]
        self.preferences[umo] = {"persona_id": "second"}
        native_host.remember_session_settings(umo, self.preferences[umo])

        async def unexpected_resolver(**kwargs):
            raise AssertionError("an explicit V2 window persona must be authoritative")

        with patch.object(self.native.persona_manager, "resolve_selected_persona", unexpected_resolver):
            current = next(item for item in await self.controller.session_topology.inventory()
                           if item.get("unified_msg_origin") == umo)

        self.assertEqual(current["persona_id"], "second")
        self.assertEqual(current["persona_override"], "second")
        self.assertEqual(current["persona_source"], "v2_window_override")

    async def test_invalid_identity_target_and_persona_never_write_native_settings(self):
        row = next(row for row in await self.controller.session_topology.inventory() if row["scope_kind"] == "group")
        cases = [("api_key:test", {"session_key": row["session_key"], "llm_enabled": False}, "dashboard_identity_required"),
                 ("admin", {"session_key": "forged", "llm_enabled": False}, "account_session_unavailable"),
                 ("admin", {"session_key": row["session_key"], "persona_id": "not-loaded"}, "host_persona_unavailable"),
                 ("admin", {"session_key": row["session_key"], "llm_enabled": "false"}, "account_config_invalid")]
        for username, body, code in cases:
            response = await self.call("save_session", body, username)
            self.assertEqual(response["data"]["code"], code, response)
        self.assertEqual(self.preferences, {})


class SharedTopologyBindingTests(GroupFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        native_host._session_settings_cache.clear()
        native_host._session_settings_reads.clear()
        native_host._session_settings_versions.clear()
        super().setUp()
        self.addCleanup(native_host._session_settings_cache.clear)
        self.addCleanup(native_host._session_settings_reads.clear)
        self.addCleanup(native_host._session_settings_versions.clear)
        self.api = AccountSessionWebAPI(self.controller)
        self.native.persona_manager.personas = self.native.persona_records

        def save(candidate):
            self.controller.config.clear()
            self.controller.config.update(candidate)

        self.controller.save_plugin_config = save

    async def call(self, body):
        request = NS(username="admin", query={}, body=AsyncMock(return_value=json.dumps(body).encode("utf-8")))
        with patch("astrbot_host.account_session_api.request", request), patch("astrbot_host.setup_api.request", request), \
                patch("astrbot_host.setup_api.display_names", return_value=NS(targets=AsyncMock(side_effect=lambda rows: rows))):
            return await self.api.save()

    async def test_native_private_and_group_personas_share_their_mapped_role(self):
        private_umo = "qq-main:FriendMessage:200"
        group_umo = "qq-main:GroupMessage:300"
        unknown_umo = "qq-main:GroupMessage:301"
        rows = [
            {"session_id": private_umo, "conversation_id": "private-cid", "persona_id": "星缘-私聊"},
            {"session_id": group_umo, "conversation_id": "group-cid", "persona_id": None},
            {"session_id": unknown_umo, "conversation_id": "unknown-cid", "persona_id": "default"},
        ]
        conversation_ids = {private_umo: "private-cid", group_umo: "group-cid", unknown_umo: "unknown-cid"}

        async def current(umo):
            return conversation_ids.get(umo)

        async def read_sessions(*, platform, **_kwargs):
            selected = [row for row in rows if row["session_id"].startswith(platform + ":")]
            return selected, len(selected)

        async def resolve_selected_persona(context, umo, conversation_persona_id):
            self.assertIs(context, self.native)
            if umo == private_umo:
                return conversation_persona_id
            if umo == group_umo:
                return conversation_persona_id or "星缘-群聊"
            return conversation_persona_id

        self.controller.config["multi_host"]["personas"] = [
            {"bot_id": "bot-a", "persona_id": "星缘-私聊", "role_id": "星缘"},
            {"bot_id": "bot-a", "persona_id": "星缘-群聊", "role_id": "星缘"},
            {"bot_id": "bot-b", "persona_id": "original", "role_id": "role-a"},
            {"bot_id": "bot-b", "persona_id": "second", "role_id": "role-b"},
        ]
        self.native.conversation_manager.get_curr_conversation_id = current
        self.native.conversation_manager.db = NS(get_session_conversations=read_sessions)
        self.controller.native_types = replace(self.controller.native_types,
                                               persona_selection_lookup=resolve_selected_persona)

        with patch.object(self.controller.session_topology, "services", AsyncMock(return_value={})):
            report = await self.controller.session_topology.inventory_report()

        private = next(row for row in report["targets"] if row.get("unified_msg_origin") == private_umo)
        group = next(row for row in report["targets"] if row.get("unified_msg_origin") == group_umo)
        unknown = next(row for row in report["targets"] if row.get("unified_msg_origin") == unknown_umo)
        self.assertEqual((private["persona_id"], private["roles"][0]["role_id"]), ("星缘-私聊", "星缘"))
        self.assertEqual((group["persona_id"], group["roles"][0]["role_id"]), ("星缘-群聊", "星缘"))
        self.assertEqual(private["persona_source"], "astrbot_selection")
        self.assertEqual(group["persona_source"], "astrbot_selection")
        self.assertEqual(unknown["persona_id"], "default")
        self.assertEqual(unknown["persona_source"], "astrbot_selection")
        self.assertEqual(unknown["roles"], [])
        self.assertFalse(unknown["managed"])
        self.assertEqual(self.controller.session_topology.persona_selection_snapshot(private_umo, "100"),
                         {"bot_id": "bot-a", "persona_id": "星缘-私聊", "role_id": "星缘",
                          "conversation_id": "private-cid", "selection_source": "conversation"})
        self.assertEqual(self.controller.session_topology.persona_selection_snapshot(group_umo, "100"),
                         {"bot_id": "bot-a", "persona_id": "星缘-群聊", "role_id": "星缘",
                          "conversation_id": "group-cid", "selection_source": "conversation"})

    async def test_empty_persona_snapshot_uses_sync_runner_lookup_in_the_directory(self):
        private_umo = "qq-main:FriendMessage:200"
        group_umo = "qq-main:GroupMessage:300"
        rows = [
            {"session_id": private_umo, "conversation_id": "private-cid", "persona_id": ""},
            {"session_id": group_umo, "conversation_id": "group-cid", "persona_id": None},
        ]

        async def current(umo):
            return {private_umo: "private-cid", group_umo: "group-cid"}.get(umo)

        async def read_sessions(*, platform, **_kwargs):
            selected = [row for row in rows if row["session_id"].startswith(platform + ":")]
            return selected, len(selected)

        self.controller.config["multi_host"]["personas"] = [
            {"bot_id": "bot-a", "persona_id": "星缘-私聊", "role_id": "星缘"},
            {"bot_id": "bot-a", "persona_id": "星缘-群聊", "role_id": "星缘"},
        ]
        self.native.conversation_manager.get_curr_conversation_id = current
        self.native.conversation_manager.db = NS(get_session_conversations=read_sessions)
        self.controller.native_types = replace(self.controller.native_types,
            persona_selection_lookup=lambda _context, umo, selected: selected or
                ("星缘-私聊" if umo == private_umo else "星缘-群聊"))

        with patch.object(self.controller.session_topology, "services", AsyncMock(return_value={})):
            report = await self.controller.session_topology.inventory_report()

        selected = {row["unified_msg_origin"]: row for row in report["targets"]
                    if row.get("unified_msg_origin")}
        self.assertEqual((selected[private_umo]["persona_id"], selected[private_umo]["roles"][0]["role_id"]),
                         ("星缘-私聊", "星缘"))
        self.assertEqual((selected[group_umo]["persona_id"], selected[group_umo]["roles"][0]["role_id"]),
                         ("星缘-群聊", "星缘"))

    async def test_manual_private_refresh_updates_only_the_private_snapshot(self):
        private_umo = "qq-main:FriendMessage:200"
        group_umo = "qq-main:GroupMessage:300"
        rows = [
            {"session_id": private_umo, "conversation_id": "private-cid", "persona_id": "original"},
            {"session_id": group_umo, "conversation_id": "group-cid", "persona_id": "original"},
        ]

        async def current(umo):
            return {private_umo: "private-cid", group_umo: "group-cid"}.get(umo)

        async def read_sessions(*, platform, **_kwargs):
            selected = [row for row in rows if row["session_id"].startswith(platform + ":")]
            return selected, len(selected)

        self.controller.config["multi_host"]["personas"] = [
            {"bot_id": "bot-a", "persona_id": "original", "role_id": "original-role"},
            {"bot_id": "bot-a", "persona_id": "second", "role_id": "second-role"},
        ]
        self.native.conversation_manager.get_curr_conversation_id = current
        self.native.conversation_manager.db = NS(get_session_conversations=read_sessions)
        self.controller.native_types = replace(self.controller.native_types,
            persona_selection_lookup=lambda _context, umo, _selected:
                "second" if umo == private_umo else "original")

        with patch.object(self.controller.session_topology, "services", AsyncMock(return_value={})):
            await self.controller.session_topology.inventory_report()
            await self.controller.session_topology.inventory_report(scope_kind="private")

        self.assertEqual(self.controller.session_topology.persona_selection_snapshot(private_umo, "100")["persona_id"],
                         "second")
        self.assertEqual(self.controller.session_topology.persona_selection_snapshot(group_umo, "100")["persona_id"],
                         "original")

    async def test_wildcard_target_installs_default_snapshot_for_new_private_windows(self):
        from dataclasses import replace as dataclass_replace

        settings = dataclass_replace(self.controller.settings, targets=(
            dataclass_replace(self.controller.settings.targets[0], participant_id="*"),))
        self.controller.native_types = replace(self.controller.native_types,
            persona_selection_lookup=lambda _context, _umo, _selected: "original")

        with patch.object(self.controller.session_topology, "_native_sessions", AsyncMock(return_value=({}, []))):
            await self.controller.session_topology.inventory_report(settings=settings)
            await self.controller.session_topology.inventory_report(settings=settings, scope_kind="private")

        snapshot = self.controller.session_topology.persona_selection_snapshot(
            "qq-main:FriendMessage:299", "100")
        self.assertEqual(snapshot["persona_id"], "original")
        self.assertEqual(snapshot["role_id"], "role-a")
        self.assertEqual(snapshot["selection_source"], "configured")

    def test_accounts_keep_registered_bot_ids_without_any_window(self):
        from companion_v2.adapters.astrbot.native import HostAccount

        self.native.platforms["qq-main"].client._wsr_api_clients = {"100": object()}
        self.native.platforms["qq-second"].client._wsr_api_clients = {"101": object()}
        shared = self.controller.config["multi_host"]
        shared["accounts"] = [{"platform_id": "qq-main", "account_id": "100", "bot_id": "registered-main"},
                              {"platform_id": "qq-second", "account_id": "101", "bot_id": "registered-second"}]
        shared["targets"] = []
        shared["group_targets"] = []

        accounts = self.controller.session_topology.accounts()

        self.assertEqual({row["account_id"]: row["bot_id"] for row in accounts},
                         {"100": "registered-main", "101": "registered-second"})
        self.assertEqual({row["platform_name"] for row in accounts}, {"aiocqhttp"})
        settings = replace(self.controller.settings, targets=(), group_targets=(),
                           account_bindings=(HostAccount("qq-main", "100", "settings-main"),
                                             HostAccount("qq-second", "101", "settings-second")))
        accounts = self.controller.session_topology.accounts(settings)
        self.assertEqual({row["account_id"]: row["bot_id"] for row in accounts},
                         {"100": "settings-main", "101": "settings-second"})

    async def test_multiple_native_personas_can_be_saved_under_one_role(self):
        original = deepcopy(self.controller.config["multi_host"])
        personas = deepcopy(original["personas"])
        for item in personas:
            if item["bot_id"] == "bot-a":
                item["role_id"] = "星缘"
        accounts = self.controller.session_topology.accounts()
        discovered = {"accounts": accounts, "personas": personas, "targets": []}
        body = {"enabled": True, "accounts": accounts, "personas": personas,
                "sessions": original["targets"], "groups": original["group_targets"]}
        with patch.object(self.api, "_discovered", AsyncMock(return_value=discovered)):
            response = await self.call(body)

        self.assertEqual(response["status"], "ok", response)
        saved = self.controller.config["multi_host"]["personas"]
        self.assertEqual({item["role_id"] for item in saved if item["bot_id"] == "bot-a"}, {"星缘"})
        private = next(row for row in self.controller.web_api.target_inventory() if row["target_id"] == "first")
        group = next(row for row in self.controller.web_api.target_inventory() if row["target_id"] == "group-a")
        self.assertEqual([role["persona_ids"] for role in private["roles"] if role["role_id"] == "星缘"],
                         [["original", "second"]])
        self.assertEqual([role["role_id"] for role in group["roles"]], ["星缘"])

    async def test_event_role_follows_selected_private_and_group_persona(self):
        await self.start_host()
        private = self.event()
        group = self.group_event()

        self.window_personas[private.unified_msg_origin] = "second"
        self.window_personas[group.unified_msg_origin] = "second"

        private_mapping = await self.controller.persona_mapping_for_event(private)
        group_mapping = await self.controller.persona_mapping_for_event(group)

        self.assertEqual((private_mapping.persona_id, private_mapping.role_id), ("second", "role-b"))
        self.assertEqual((group_mapping.persona_id, group_mapping.role_id), ("second", "role-b"))

        self.controller.settings = replace(self.controller.settings, personas=tuple(
            mapping for mapping in self.controller.settings.personas if mapping.persona_id != "second"))
        self.assertIsNone(await self.controller.persona_mapping_for_event(private))
        self.assertIsNone(await self.controller.persona_mapping_for_event(group))

    async def test_reviewed_role_merge_automatically_restarts_and_keeps_previous_sources(self):
        await self.start_host()
        original = deepcopy(self.controller.config["multi_host"])
        previous = self.controller.authority.context("first", "role-a")
        personas = [{**row, "role_id": "星缘"} if row["bot_id"] == "bot-a" else row
                    for row in original["personas"]]
        discovered = {"accounts": self.controller.session_topology.accounts(), "personas": personas, "targets": []}
        body = {"accounts": discovered["accounts"], "personas": personas,
                "sessions": original["targets"], "groups": original["group_targets"]}
        with patch.object(self.api, "_discovered", AsyncMock(return_value=discovered)):
            result = await self.call(body)
        self.assertEqual(result["status"], "ok", result)
        self.assertTrue(result["data"]["started"], result)
        self.assertTrue(self.controller._accepting)
        context = self.controller.authority.context("first", "星缘")
        self.assertEqual(context.actor.subject, previous.actor.subject)
        self.assertNotEqual(context.scope.conversation_id, previous.scope.conversation_id)
        historical = self.controller.authority.personas.resolve(previous.scope, observed_at=self.clock.now()).unwrap()
        self.assertEqual(historical.binding.role.subject_id, "role-a")

    async def test_partial_account_mapping_preserves_exact_private_and_group_windows(self):
        original = deepcopy(self.controller.config["multi_host"])
        account = next(row for row in self.controller.session_topology.accounts() if row["account_id"] == "100")
        private = self.event()
        group = self.group_event()
        unmapped = self.group_event(identifier="other-persona", unique=True)
        self.window_personas[unmapped.unified_msg_origin] = "second"
        self.native.conversation_manager.session_conversations = {
            event.unified_msg_origin: self.native.conversations["qq-main"] for event in (private, group, unmapped)}
        async def read_sessions(*, page, page_size, platform, **_filters):
            rows = [{"session_id": umo, "conversation_id": cid,
                     "persona_id": self.native.selections.get(umo, self.native.selections[platform])}
                    for umo, cid in self.native.conversation_manager.session_conversations.items()
                    if umo.startswith(platform + ":")]
            return rows, len(rows)

        self.native.conversation_manager.db = NS(get_session_conversations=read_sessions)
        personas = [{"bot_id": "bot-a", "persona_id": "original", "role_id": "星缘"}]
        discovered = {"accounts": self.controller.session_topology.accounts(),
                      "personas": original["personas"], "targets": []}
        with patch.object(self.api, "_discovered", AsyncMock(return_value=discovered)):
            result = await self.call({"accounts": [account], "personas": personas})
        self.assertEqual(result["status"], "ok", result)
        self.assertTrue(result["data"]["started"], result)
        self.assertEqual([row["account_id"] for row in self.controller.config["multi_host"]["accounts"]], ["100"])
        saved = self.controller.config["multi_host"]
        self.assertEqual(saved["targets"], original["targets"][:1])
        self.assertEqual(saved["group_targets"], original["group_targets"][:2])
        self.assertEqual([(target.target_id, target.participant_id) for target in self.controller.settings.targets],
                         [("first", "200")])
        self.assertTrue(all(row["bot_id"] == "bot-a" for row in saved["personas"]))
        native_host.remember_session_settings(unmapped.unified_msg_origin, {"persona_id": "second"})
        with patch("astrbot_host.native.sp.get_async", AsyncMock(return_value={})):
            rows = [row for row in await self.controller.session_topology.inventory() if row["native"]]
        self.assertTrue(next(row for row in rows if row["unified_msg_origin"] == private.unified_msg_origin)["managed"])
        self.assertTrue(next(row for row in rows if row["unified_msg_origin"] == group.unified_msg_origin)["managed"])
        self.assertFalse(next(row for row in rows if row["unified_msg_origin"] == unmapped.unified_msg_origin)["managed"])
        context = self.controller.authority.context(self.controller.settings.targets[0].target_id, "星缘")
        self.assertEqual(context.scope.native_scope_id, "200")
        self.assertEqual(context.actor.subject.subject_id, "onebot-user:200")
        app = self.controller.session.application
        self.assertIn(context.key, app.life_runtime.routines)
        self.assertIsNotNone(app.life_runtime.routines[context.key].instruction)
        self.assertIn(context.key, app.state_maintenance.bindings)
        self.assertIn(context.key, app.presence.contexts)
        self.assertTrue(all(routine.context.scope.native_scope_id != "*"
                            for routine in app.life_runtime.routines.values()))
        self.assertEqual(self.native.provider.client.state.calls, [])

    async def test_new_account_private_template_keeps_existing_routes_and_exact_groups(self):
        candidate = deepcopy(self.controller.config)
        original = deepcopy(candidate["multi_host"])
        accounts = [{"platform_id": item.platform_id, "account_id": item.self_id, "bot_id": item.bot_id}
                    for item in self.controller.settings.accounts]
        candidate["multi_host"]["targets"] = original["targets"][:1]
        candidate["multi_host"]["group_targets"] = original["group_targets"][:2]
        candidate["multi_host"]["personas"] = [row for row in original["personas"] if row["bot_id"] == "bot-a"]
        save_callback = self.controller.save_plugin_config
        self.controller = self.make_controller(candidate)
        self.controller.save_plugin_config = save_callback
        self.api = AccountSessionWebAPI(self.controller)
        discovered = {"accounts": accounts, "personas": original["personas"], "targets": []}
        with patch.object(self.api, "_discovered", AsyncMock(return_value=discovered)):
            result = await self.call({"accounts": accounts, "personas": original["personas"]})

        self.assertEqual(result["status"], "ok", result)
        self.assertTrue(result["data"]["started"], result)
        saved = self.controller.config["multi_host"]
        self.assertEqual(saved["targets"][:1], original["targets"][:1])
        self.assertEqual(saved["group_targets"], original["group_targets"][:2])
        template = saved["targets"][1]
        self.assertEqual((template["platform_id"], template["account_id"], template["bot_id"],
                          template["participant_id"]), ("qq-second", "101", "bot-b", "*"))
        self.assertEqual(len(saved["targets"]), 2)
        self.assertNotEqual(template["target_id"], "first")
        self.assertTrue(all(target.group_id != "*" for target in self.controller.settings.group_targets))
        self.assertEqual(self.native.provider.client.state.calls, [])

    async def test_saved_mapping_receipt_does_not_fail_when_the_following_directory_read_fails(self):
        from companion_v2.execution.models import TaskError
        original = self.controller.config["multi_host"]
        discovered = {"accounts": self.controller.session_topology.accounts(),
                      "personas": original["personas"], "targets": []}
        with patch.object(self.api, "_discovered", AsyncMock(side_effect=[discovered,
                TaskError("setup_targets_unavailable", "synthetic refresh failure")])), \
                patch.object(self.controller, "apply_bindings", AsyncMock(return_value={"saved": True, "started": True})):
            result = await self.call({"accounts": discovered["accounts"], "personas": original["personas"]})
        self.assertEqual(result["status"], "ok", result)
        self.assertTrue(result["data"]["saved"])
        self.assertTrue(result["data"]["directory_refresh_pending"])

    async def test_group_save_preserves_owner_ids_and_applies_to_an_initialized_runtime(self):
        (await self.controller.initialize_profile()).unwrap()
        self.controller.register_routes()
        original_routes = {path for path, _, _, _ in self.native.registered_web_apis}
        original = deepcopy(self.controller.config["multi_host"])
        discovered = {"accounts": self.controller.session_topology.accounts(),
                      "personas": [dict(row) for row in original["personas"]],
                      "targets": [{"scope_kind": "group", "platform_id": "qq-main", "account_id": "100",
                                   "bot_id": "bot-a", "group_id": "399"}]}
        body = {"enabled": True, "accounts": discovered["accounts"], "personas": original["personas"],
                "sessions": original["targets"], "groups": [*original["group_targets"],
                    {"platform_id": "qq-main", "account_id": "100", "bot_id": "bot-a", "group_id": "399"}]}
        with patch.object(self.api, "_discovered", AsyncMock(return_value=discovered)):
            result = await self.call(body)
        self.assertEqual(result["status"], "ok", result)
        current = self.controller.config["multi_host"]
        self.assertEqual(current["targets"], original["targets"])
        self.assertEqual(current["personas"], original["personas"])
        self.assertEqual(current["group_targets"][:3], original["group_targets"])
        self.assertEqual(len(current["group_targets"]), 4)
        self.assertIsNotNone(self.controller.runtime)
        self.assertTrue(self.controller._accepting)
        registered_routes = self.native.registered_web_apis
        route_paths = {path for path, _, _, _ in registered_routes}
        operations = [(path, method) for path, _, methods, _ in registered_routes for method in methods]
        self.assertEqual(len(operations), len(set(operations)))
        self.assertTrue(original_routes.issubset(route_paths))
        added_group = current["group_targets"][-1]["target_id"]
        new_routes = {role["group_memory_api"]
                      for target in self.controller.web_api.target_inventory() if target["target_id"] == added_group
                      for role in target["roles"]}
        self.assertTrue(new_routes and new_routes.issubset(route_paths))
        self.assertEqual(len(self.controller.settings.group_targets), 4)
        self.assertEqual(self.native.provider.client.state.calls, [])
        await self.controller.terminate()
        self.assertEqual(self.native.registered_web_apis, [])

    async def test_four_unresolved_native_groups_can_be_mapped_to_one_explicit_account(self):
        original = deepcopy(self.controller.config["multi_host"])
        accounts = self.controller.session_topology.accounts()
        group_ids = ["1097283005", "145326153", "555551401", "915052038"]
        discovered = {"accounts": accounts, "personas": original["personas"],
            "targets": [{"scope_kind": "group", "platform_id": "qq-main", "account_id": "",
                         "bot_id": "", "group_id": group_id, "native": True,
                         "details_available": False, "roles": []} for group_id in group_ids],
            "directory": {"complete": False, "issues": [{"code": "host_native_lookup_timeout__persona",
                "stage": "persona", "platform_id": "qq-main", "reason": "AstrBot 读取会话生效人格超时。", "count": 4}]}}
        groups = [{"platform_id": "qq-main", "account_id": "100", "bot_id": "bot-a", "group_id": group_id}
                  for group_id in group_ids]
        body = {"enabled": True, "accounts": accounts, "personas": original["personas"],
                "sessions": original["targets"], "groups": groups}
        with patch.object(self.api, "_discovered", AsyncMock(return_value=discovered)):
            result = await self.call(body)

        self.assertEqual(result["status"], "ok", result)
        saved = self.controller.config["multi_host"]["group_targets"]
        self.assertEqual([row["group_id"] for row in saved], group_ids)
        self.assertTrue(all(row["account_id"] == "100" and row["bot_id"] == "bot-a" for row in saved))
        self.assertTrue(all(row["group_id"] != "*" for row in saved))

    async def test_directory_timeout_keeps_all_configured_explicit_groups(self):
        async def blocked(**kwargs):
            await asyncio.Event().wait()

        with patch.object(self.native.conversation_manager, "db",
                          NS(get_session_conversations=blocked), create=True), \
                patch("companion_v2.adapters.astrbot.native.NATIVE_LOOKUP_TIMEOUT_SECONDS", 0.01):
            report = await self.controller.session_topology.inventory_report()

        groups = [row for row in report["targets"] if row.get("scope_kind") == "group"]
        self.assertFalse(report["complete"])
        self.assertEqual({row["target_id"] for row in groups}, {"group-a", "group-b", "group-c"})
        self.assertTrue(all(not row["native"] for row in groups))
        self.assertTrue(all(row["group_id"] != "*" for row in groups))

    async def test_malformed_lists_do_not_interrupt_a_running_role(self):
        await self.start_host()
        before = deepcopy(self.controller.config)
        response = await self.call({"accounts": {}})
        self.assertEqual(response["data"]["code"], "account_config_invalid")
        self.assertTrue(self.controller._accepting)
        (await self.controller.stop()).unwrap()
        response = await self.call({"accounts": {}, "personas": [], "sessions": []})
        self.assertEqual(response["data"]["code"], "account_config_invalid")
        self.assertEqual(self.controller.config, before)

    async def test_failed_save_rolls_back_the_manifest_and_releases_a_runtime_opened_for_editing(self):
        from astrbot_host.multi_authority import MANIFEST
        (await self.controller.initialize_profile()).unwrap()
        original = deepcopy(self.controller.config)
        manifest = self.controller.authority.read(MANIFEST)
        self.controller.runtime.close()
        self.controller.runtime = self.controller.authority = None
        self.controller.session = self.controller.last_session = None
        candidate = deepcopy(original)
        candidate["multi_host"]["group_targets"].append({"target_id": "group-new", "platform_id": "qq-main",
            "account_id": "100", "bot_id": "bot-a", "group_id": "399"})
        def fail_save(_):
            raise OSError("synthetic write failure")
        self.controller.save_plugin_config = fail_save
        with self.assertRaises(OSError):
            await self.controller.apply_bindings(candidate)
        self.assertEqual(self.controller.config, original)
        self.assertIsNone(self.controller.runtime)
        self.controller._open()
        self.assertEqual(self.controller.authority.read(MANIFEST), manifest)
