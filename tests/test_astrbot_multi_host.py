import asyncio
import base64
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import json
import os
import threading
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

from astrbot.core.message.components import Video
from astrbot_host.media_components import private_components
from astrbot_host.video.loader import load_native_video
from companion_v2.execution.models import TaskError, stamp
from companion_v2.foundation.refs import ScopeRef, SubjectKind, SubjectRef
from companion_v2.memory.models import AtomRef, owner_key
from companion_v2.adapters.astrbot.staging import NativeAdapterTypes
from tests.multi_host_support import MultiFixture, multi_config
from tests.native_adapter_support import completion
from tests.staging_bootstrap_support import database_contents, wait_for_batch
from tests.memory_stream_support import StreamFixture
from tests.video_support import video_bytes
from astrbot_host.multi_settings import MultiHostSettings, find_persona_mapping
from astrbot_host.shared_host_migration import migrate_single_target_config
from astrbot_host.multi_authority import MultiAuthority
from astrbot_host.settings import INSTALLATION_PREFIX
from companion_v2.storage.runtime import StorageRuntime
from astrbot_host.multi_api import PREFIX as MULTI_HOST_PREFIX
from astrbot_host.host_options_api import HostOptionsWebAPI
from astrbot_host.native_perception_api import PREFIX as NATIVE_PERCEPTION_PREFIX
from astrbot_host.native import resolve_native_persona_selection


class _Query(dict):
    """Small Starlette-like query mapping for direct Dashboard handler tests."""

    def getlist(self, key):
        value = self.get(key)
        return [] if value is None else [value]


class MultiSettingsTests(unittest.TestCase):
    def test_model_output_limit_uses_the_same_ceiling_as_runtime(self):
        config = multi_config()
        config["model"]["max_output_tokens"] = 65_536
        self.assertEqual(MultiHostSettings.parse(config).common.max_output_tokens, 65_536)
        config["model"]["max_output_tokens"] = 65_537
        with self.assertRaises(ValueError):
            MultiHostSettings.parse(config)

    def test_follow_window_mapping_applies_only_when_it_is_the_bot_single_mapping(self):
        from astrbot_host.multi_settings import PersonaMapping

        default = PersonaMapping("bot-a", "[%None]", "role-default")
        explicit = PersonaMapping("bot-a", "second", "role-second")

        self.assertEqual(find_persona_mapping((default,), "bot-a", "custom"),
                         PersonaMapping("bot-a", "custom", "role-default"))
        self.assertEqual(find_persona_mapping((default, explicit), "bot-a", "custom"), None)
        self.assertEqual(find_persona_mapping((default, explicit), "bot-a", "second"), explicit)
    def test_legacy_mode_is_migrated_and_ambiguous_routes_are_rejected(self):
        config = multi_config()
        config["staging"]["enabled"] = True
        migrated, changed = migrate_single_target_config(config)
        self.assertTrue(changed)
        self.assertFalse(migrated["staging"]["enabled"])
        self.assertTrue(MultiHostSettings.parse(migrated).enabled)

        configs = []
        config = multi_config()
        config["multi_host"]["targets"][1].update(platform_id="qq-main", account_id="100", participant_id="201")
        configs.append(config)
        config = multi_config()
        config["multi_host"]["personas"].append(deepcopy(config["multi_host"]["personas"][0]))
        configs.append(config)
        for config in configs:
            with self.subTest(config=config["multi_host"]["targets"]), self.assertRaises(ValueError):
                MultiHostSettings.parse(config)

    def test_configuration_order_does_not_change_the_stable_installation(self):
        original = multi_config()
        reordered = deepcopy(original)
        reordered["multi_host"]["targets"].reverse()
        reordered["multi_host"]["personas"].reverse()
        self.assertEqual(MultiHostSettings.parse(original), MultiHostSettings.parse(reordered))

    def test_same_persona_uses_one_shared_role_across_bot_accounts(self):
        config = multi_config()
        config["multi_host"]["personas"] = [
            {"bot_id": "bot-a", "persona_id": "星缘-私聊", "role_id": "星缘"},
            {"bot_id": "bot-b", "persona_id": "星缘-私聊", "role_id": "role-bot-b-generated"},
        ]
        parsed = MultiHostSettings.parse(config)
        self.assertEqual({mapping.role_id for mapping in parsed.personas}, {"星缘"})

    def test_differently_named_roles_for_the_same_persona_stay_separate(self):
        config = multi_config()
        config["multi_host"]["personas"] = [
            {"bot_id": "bot-a", "persona_id": "星缘-私聊", "role_id": "星缘"},
            {"bot_id": "bot-b", "persona_id": "星缘-私聊", "role_id": "小星"},
        ]
        parsed = MultiHostSettings.parse(config)
        self.assertEqual({mapping.role_id for mapping in parsed.personas}, {"星缘", "小星"})

    def test_shared_persona_merge_keeps_legacy_role_aliases_for_runtime_reads(self):
        config = multi_config()
        config["multi_host"]["personas"] = [
            {"bot_id": "bot-a", "persona_id": "星缘-私聊", "role_id": "role-bot-a-generated"},
            {"bot_id": "bot-b", "persona_id": "星缘-私聊", "role_id": "role-bot-b-generated"},
        ]
        parsed = MultiHostSettings.parse(config)
        canonical = {mapping.role_id for mapping in parsed.personas}
        self.assertEqual(len(canonical), 1)
        canonical_role = next(iter(canonical))
        for old_role in ("role-bot-a-generated", "role-bot-b-generated"):
            self.assertEqual(parsed.canonical_role_id(old_role), canonical_role)

    def test_existing_manifest_accepts_one_shared_role_after_upgrade(self):
        from astrbot_host.multi_authority import MultiAuthority

        pinned = {"personas": [
            {"bot_id": "bot-a", "persona_id": "星缘-私聊", "role_id": "星缘"},
            {"bot_id": "bot-b", "persona_id": "星缘-私聊", "role_id": "role-bot-b-generated"},
        ]}
        current = {"personas": [
            {"bot_id": "bot-a", "persona_id": "星缘-私聊", "role_id": "星缘"},
            {"bot_id": "bot-b", "persona_id": "星缘-私聊", "role_id": "星缘"},
        ]}
        self.assertEqual(MultiAuthority._binding_differences(pinned, current), ())

    def test_window_links_reference_existing_private_windows_and_one_mode(self):
        config = multi_config()
        config["multi_host"]["window_links"] = [
            {"primary_target_id": "first", "alias_target_ids": ["second"], "mode": "role"},
        ]
        parsed = MultiHostSettings.parse(config)
        self.assertEqual(parsed.window_links[0].alias_target_ids, ("second",))
        self.assertEqual(parsed.window_links[0].mode, "role")
        self.assertEqual(parsed.window_link_for_target("second").primary_target_id, "first")
        bad = deepcopy(config)
        bad["multi_host"]["window_links"][0]["alias_target_ids"] = ["first"]
        with self.assertRaises(ValueError):
            MultiHostSettings.parse(bad)


class NativePersonaSelectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_group_window_ignores_the_private_conversation_default(self):
        from astrbot_host.session_topology import SessionTopology

        lookup = AsyncMock(return_value="group-persona")
        context = NS(get_config=lambda **_kwargs: {
            "agent_runner": {"runner_type": "local",
                             "config": {"persona": {"persona_id": "group-persona"}}}})
        topology = object.__new__(SessionTopology)
        topology.controller = NS(context=context, _multi_host=True,
                                 native_types=NS(persona_selection_lookup=lookup))

        selected = await topology._selected_native_persona(
            "qq-main:GroupMessage:201", "private-persona")

        self.assertEqual(selected, "group-persona")
        lookup.assert_awaited_once_with(topology.controller.context,
                                        "qq-main:GroupMessage:201", None)

    async def test_empty_resolver_result_uses_the_configured_window_default(self):
        configuration = {
            "agent_runner": {"runner_type": "local", "config": {"persona": {"persona_id": "private-persona"}}},
            "provider_settings": {},
        }
        context = NS(
            persona_manager=NS(resolve_selected_persona=AsyncMock(return_value=(None, None, None, False))),
            get_platform_inst=lambda _platform: NS(meta=lambda: NS(name="aiocqhttp")),
            get_config=lambda **_kwargs: configuration,
        )

        selected = await resolve_native_persona_selection(
            context, "qq-main:FriendMessage:200", "")

        self.assertEqual(selected, "private-persona")


class LegacyStagingReceiptTests(unittest.TestCase):
    def authority(self, receipt):
        settings = MultiHostSettings.parse(multi_config())
        authority = object.__new__(MultiAuthority)
        authority.runtime = NS(installation_id=settings.installation_id,
                               read_one=lambda _query, _parameters: receipt)
        authority.settings = settings
        return authority

    def test_only_a_completed_receipt_for_the_configured_native_target_allows_adoption(self):
        settings = MultiHostSettings.parse(multi_config())
        target = settings.target("first")
        receipt = {"owner": "host.staging.bootstrap", "installation_id": settings.installation_id,
                   "platform": target.platform_id, "scope_kind": "private",
                   "native_scope_id": target.participant_id, "bot_id": target.bot_id,
                   "account_id": target.account_id, "state": "completed"}
        self.assertTrue(self.authority(receipt)._legacy_staging_receipt_matches())

        for field, value in (("owner", "other.bootstrap"), ("installation_id", "other-installation"),
                             ("scope_kind", "group"), ("native_scope_id", "201"),
                             ("bot_id", "bot-b"), ("account_id", "101"), ("state", "accepted")):
            with self.subTest(field=field, value=value):
                changed = {**receipt, field: value}
                self.assertFalse(self.authority(changed)._legacy_staging_receipt_matches())


class MultiHostTests(MultiFixture, unittest.IsolatedAsyncioTestCase):
    async def test_saved_large_output_limit_prepares_and_reloads(self):
        prepared = await self.controller.initialize_profile()
        self.assertTrue(prepared.is_success, prepared.failure)
        await self.controller.terminate()

        config = multi_config()
        config["model"]["max_output_tokens"] = 50_000
        self.controller = self.make_controller(config)
        loaded = await self.controller.load_existing()
        self.assertTrue(loaded.is_success, loaded.failure)
        self.assertTrue(loaded.unwrap()["loaded"])
        self.assertEqual(self.controller.session.application.router.profiles["conversation.reply"].max_output_tokens,
                         50_000)

    async def test_legacy_default_mapping_routes_the_window_selected_persona(self):
        config = multi_config()
        config["multi_host"]["personas"] = [
            {"bot_id": "bot-a", "persona_id": "[%None]", "role_id": "role-a"},
            {"bot_id": "bot-b", "persona_id": "original", "role_id": "role-b"},
            {"bot_id": "bot-b", "persona_id": "second", "role_id": "role-b-second"},
        ]
        self.controller = self.make_controller(config)
        await self.start_host()
        self.window_personas["qq-main:FriendMessage:200"] = "second"

        mapping = await self.controller.persona_mapping_for_event(self.event())
        account = self.controller.session.bridge.accounts[("qq-main", "100")]
        _, _, _, projection, instruction = await self.controller.session.bridge._selected_role_snapshot(
            account, "qq-main:FriendMessage:200")

        self.assertEqual((mapping.persona_id, mapping.role_id), ("second", "role-a"))
        self.assertEqual((projection.persona_id, projection.role.subject_id), ("second", "role-a"))
        self.assertEqual(instruction, self.native.persona_records["second"]["prompt"])

    async def test_initialize_profile_rebuilds_and_resumes_a_running_host(self):
        await self.start_host()
        previous = self.controller.session

        result = await self.controller.initialize_profile()

        self.assertTrue(result.is_success, result.failure)
        self.assertEqual(previous.application.state, "stopped")
        self.assertIsNot(self.controller.session, previous)
        self.assertEqual(self.controller.session.application.state, "running")
        self.assertTrue(self.controller._accepting)

    async def test_primary_window_link_moves_alias_capture_to_the_primary_window(self):
        config = multi_config()
        config["multi_host"]["window_links"] = [
            {"primary_target_id": "first", "alias_target_ids": ["second"], "mode": "primary"},
        ]
        self.controller = self.make_controller(config)
        await self.start_host()
        captured = (await self.controller.session.bridge.capture(self.event("second"))).unwrap()
        self.assertEqual(captured.principal.account_id, "100")
        self.assertEqual(captured.route.scope.account_id, "100")
        self.assertEqual(captured.route.scope.native_scope_id, "200")
        accepted = await self.controller.receive_claimed(self.event("second"))
        self.assertTrue(accepted.is_success, accepted.failure)

    async def test_role_window_link_keeps_alias_scope_but_shares_primary_role(self):
        config = multi_config()
        config["multi_host"]["personas"] = [
            {"bot_id": "bot-a", "persona_id": "original", "role_id": "星缘"},
            {"bot_id": "bot-a", "persona_id": "second", "role_id": "小星"},
            {"bot_id": "bot-b", "persona_id": "original", "role_id": "小星"},
            {"bot_id": "bot-b", "persona_id": "second", "role_id": "小星"},
        ]
        config["multi_host"]["window_links"] = [
            {"primary_target_id": "first", "alias_target_ids": ["second"], "mode": "role"},
        ]
        self.controller = self.make_controller(config)
        await self.start_host()
        captured = (await self.controller.session.bridge.capture(self.event("second"))).unwrap()
        self.assertEqual(captured.route.scope.account_id, "101")
        self.assertEqual(captured.role.subject_id, "星缘")
        accepted = await self.controller.receive_claimed(self.event("second"))
        self.assertTrue(accepted.is_success, accepted.failure)

    async def test_window_link_api_saves_primary_mode(self):
        from astrbot_host.account_session_api import AccountSessionWebAPI
        from types import SimpleNamespace as NS

        api = AccountSessionWebAPI(self.controller)
        body = json.dumps({"primary_target_id": "first", "alias_target_ids": ["second"],
                           "mode": "primary"}).encode("utf-8")
        fake_request = NS(username="admin", query={}, body=AsyncMock(return_value=body))
        with patch("astrbot_host.account_session_api.request", fake_request), \
                patch.object(self.controller, "apply_bindings", AsyncMock(return_value={"started": False})) as apply:
            response = await api.link_window()
        self.assertEqual(response["status"], "ok")
        candidate = apply.await_args.args[0]
        self.assertEqual(candidate["multi_host"]["window_links"][0]["alias_target_ids"], ["second"])

    async def test_reviewed_bindings_detect_conflict_before_stopping_the_running_host(self):
        await self.start_host()
        expected = deepcopy(self.controller.config)
        candidate = deepcopy(expected)
        save = self.controller.save_plugin_config = AsyncMock()
        original_probe = self.controller._probe
        session = self.controller.session

        async def changed_during_probe(*args, **kwargs):
            result = await original_probe(*args, **kwargs)
            self.controller.config["concurrent_edit"] = True
            return result

        with patch.object(self.controller, "_probe", side_effect=changed_during_probe), \
                patch.object(session, "close", wraps=session.close) as close:
            with self.assertRaises(TaskError) as caught:
                await self.controller.apply_bindings(candidate, expected_config=expected)
            close.assert_not_called()
        self.assertEqual(caught.exception.code, "legacy_plan_stale")
        save.assert_not_called()
        self.assertIs(self.controller.session, session)
        self.assertTrue(self.controller._accepting)
        self.assertEqual(session.application.state, "running")

    async def test_reviewed_bindings_resume_saved_routes_if_conflict_arrives_during_close(self):
        await self.start_host()
        expected = deepcopy(self.controller.config)
        candidate = deepcopy(expected)
        save = self.controller.save_plugin_config = AsyncMock()
        session = self.controller.session
        original_close = session.close

        async def changed_during_close(*args, **kwargs):
            result = await original_close(*args, **kwargs)
            self.controller.config["concurrent_edit"] = True
            return result

        with patch.object(session, "close", side_effect=changed_during_close):
            with self.assertRaises(TaskError) as caught:
                await asyncio.wait_for(
                    self.controller.apply_bindings(candidate, expected_config=expected), timeout=20)
        self.assertEqual(caught.exception.code, "legacy_plan_stale")
        save.assert_not_called()
        self.assertTrue(self.controller.config["concurrent_edit"])
        self.assertTrue(self.controller._accepting)
        self.assertEqual(self.controller.session.application.state, "running")
        self.assertIsNot(self.controller.session, session)

    async def test_reviewed_bindings_reject_configuration_changed_while_waiting_for_lifecycle(self):
        expected = deepcopy(self.controller.config)
        candidate = deepcopy(expected)
        save = self.controller.save_plugin_config = AsyncMock()
        async with self.controller._lifecycle_lock:
            applying = asyncio.create_task(self.controller.apply_bindings(candidate, expected_config=expected))
            await asyncio.sleep(0)
            self.controller.config["multi_host"]["installation_id"] = "changed-installation"
        with self.assertRaises(TaskError) as caught:
            await asyncio.wait_for(applying, timeout=2)
        self.assertEqual(caught.exception.code, "legacy_plan_stale")
        save.assert_not_called()

    async def test_reviewed_bindings_prepare_the_first_valid_configuration(self):
        candidate = multi_config()
        unconfigured = deepcopy(candidate)
        unconfigured["multi_host"] = {"enabled": False, "targets": [], "personas": []}
        self.controller = self.make_controller(unconfigured)
        self.assertEqual(self.controller.settings_error.code, "host_mappings_required")

        def save(document):
            self.controller.config.clear()
            self.controller.config.update(deepcopy(document))

        self.controller.save_plugin_config = save
        result = await self.controller.apply_bindings(candidate)

        self.assertTrue(result["saved"], result)
        self.assertTrue(result["started"], result)
        self.assertIsNone(self.controller.settings_error)
        self.assertTrue(self.controller.status()["accepting"])

    async def test_reviewed_persona_mapping_uses_astrbot_current_provider_when_blank(self):
        candidate = multi_config()
        candidate["model"]["provider_id"] = ""
        unconfigured = deepcopy(candidate)
        unconfigured["multi_host"] = {"enabled": False, "targets": [], "personas": []}
        self.controller = self.make_controller(unconfigured)
        self.assertEqual(self.controller.settings_error.code, "host_mappings_required")

        def save(document):
            self.controller.config.clear()
            self.controller.config.update(deepcopy(document))

        self.controller.save_plugin_config = save
        result = await self.controller.apply_bindings(candidate)

        self.assertTrue(result["saved"], result)
        self.assertTrue(result["started"], result)
        self.assertEqual(self.controller.settings.common.provider_id, "native-model")
        self.assertEqual(self.controller.config["model"]["provider_id"], "")
        self.assertEqual(self.controller.config["multi_host"]["personas"], candidate["multi_host"]["personas"])
        self.assertIsNotNone(self.controller.runtime)
        self.assertTrue(self.controller.status()["accepting"])
        self.assertIsNone(self.controller.settings_error)

    async def test_reviewed_persona_mapping_saves_when_selected_model_is_unavailable(self):
        candidate = multi_config()
        candidate["model"]["provider_id"] = "temporarily-missing"
        unconfigured = deepcopy(candidate)
        unconfigured["multi_host"] = {"enabled": False, "targets": [], "personas": []}
        self.controller = self.make_controller(unconfigured)

        def save(document):
            self.controller.config.clear()
            self.controller.config.update(deepcopy(document))

        self.controller.save_plugin_config = save
        result = await self.controller.apply_bindings(candidate)

        self.assertTrue(result["saved"], result)
        self.assertFalse(result["started"], result)
        self.assertEqual(result["startup_error"], "host_provider_unavailable")
        self.assertEqual(self.controller.config["multi_host"]["personas"], candidate["multi_host"]["personas"])
        self.assertIsNone(self.controller.runtime)

    async def test_profile_initialization_does_not_load_the_global_identity_grant_snapshot(self):
        with patch("companion_v2.identity_policy.repository.PersistentIdentityDirectory.snapshot",
                   side_effect=AssertionError("profile provisioning should read only the current subject")):
            result = await self.controller.initialize_profile()

        self.assertTrue(result.is_success, result.failure)

    async def test_profile_open_does_not_block_host_event_loop(self):
        started, release = threading.Event(), threading.Event()
        original = StorageRuntime._open_connection

        def slow_open(runtime):
            started.set()
            release.wait(timeout=5)
            return original(runtime)

        try:
            with patch.object(StorageRuntime, "_open_connection", slow_open):
                initializing = asyncio.create_task(self.controller.initialize_profile())
                self.assertTrue(await asyncio.to_thread(started.wait, 2))
                await asyncio.wait_for(asyncio.sleep(0.01), timeout=0.1)
                release.set()
                result = await asyncio.wait_for(initializing, timeout=10)
            self.assertTrue(result.is_success, result.failure)
        finally:
            release.set()

    async def test_verified_persona_uses_v2_binding_and_keeps_current_prompt(self):
        await self.start_host()
        context, _ = await self.accepted()
        prompt = "当前的人格表达已经更新。"
        self.native.persona_records["original"]["prompt"] = prompt
        lookups = self.native.role_lookups

        current = await self.controller.session.bridge.verified_persona(context.scope, context.role_binding)

        self.assertEqual(current["prompt"], prompt)
        self.assertEqual(self.native.role_lookups - lookups, 0)

    async def test_warmed_window_snapshot_selects_its_role_without_native_persona_reads(self):
        await self.start_host()
        bridge = self.controller.session.bridge
        umo = "qq-main:FriendMessage:200"
        account = bridge.accounts[("qq-main", "100")]
        conversation_id = await self.native.conversation_manager.get_curr_conversation_id(umo)
        self.controller.session_topology.update_persona_selection_snapshot(
            umo, "100", "bot-a", "second", "role-b", conversation_id)
        bridge.session_persona_override_lookup = AsyncMock(
            side_effect=AssertionError("installed persona snapshots avoid live session settings"))
        self.assertIsNotNone(bridge.persona_window_snapshot_lookup)
        self.assertEqual(bridge.persona_window_snapshot_lookup(umo, "100")["persona_id"], "second")
        bridge.persona_snapshot_lookup = AsyncMock(side_effect=AssertionError("conversation database read"))
        bridge.persona_selection_lookup = AsyncMock(side_effect=AssertionError("persona resolver call"))

        snapshot = await bridge._selected_role_snapshot(account, umo)

        self.assertEqual((snapshot[0], snapshot[1], snapshot[3].role.subject_id),
                         (conversation_id, "second", "role-b"))
        self.assertEqual(snapshot[2]["name"], "second")
        bridge.session_persona_override_lookup.assert_not_awaited()
        bridge.persona_snapshot_lookup.assert_not_awaited()
        bridge.persona_selection_lookup.assert_not_awaited()

    async def test_installed_window_snapshot_survives_reset_until_manual_refresh(self):
        await self.start_host()
        bridge = self.controller.session.bridge
        umo = "qq-main:FriendMessage:200"
        account = bridge.accounts[("qq-main", "100")]
        old_conversation_id = await self.native.conversation_manager.get_curr_conversation_id(umo)
        self.controller.session_topology.update_persona_selection_snapshot(
            umo, "100", "bot-a", "second", "role-b", old_conversation_id)
        new_conversation_id = "conversation-after-reset"
        bridge.context.conversation_manager.get_curr_conversation_id = AsyncMock(return_value=new_conversation_id)
        bridge.context.conversation_manager.get_conversation = AsyncMock(
            side_effect=AssertionError("installed persona snapshots do not load conversation history"))
        bridge.session_persona_override_lookup = AsyncMock(
            side_effect=AssertionError("installed persona snapshots avoid live session settings"))
        bridge.persona_snapshot_lookup = AsyncMock(
            side_effect=AssertionError("installed persona snapshots avoid conversation persona reads"))
        bridge.persona_selection_lookup = AsyncMock(
            side_effect=AssertionError("installed persona snapshots avoid persona resolution"))

        snapshot = await bridge._selected_role_snapshot(account, umo)

        self.assertEqual((snapshot[0], snapshot[1], snapshot[3].role.subject_id),
                         (new_conversation_id, "second", "role-b"))
        refreshed = self.controller.session_topology.persona_selection_snapshot(umo, "100")
        self.assertEqual((refreshed["persona_id"], refreshed["conversation_id"]),
                         ("second", new_conversation_id))
        bridge.context.conversation_manager.get_curr_conversation_id.assert_awaited_once_with(umo)
        bridge.context.conversation_manager.get_conversation.assert_not_awaited()
        bridge.session_persona_override_lookup.assert_not_awaited()
        bridge.persona_snapshot_lookup.assert_not_awaited()
        bridge.persona_selection_lookup.assert_not_awaited()

    async def test_verified_runtime_change_is_acknowledged_once_across_authority_recreation(self):
        (await self.controller.initialize_profile()).unwrap()
        authority = self.controller.authority
        original = authority.require_manifest()
        changed = {**original, "fingerprint": "new-runtime-fingerprint"}
        with self.assertLogs("astrbot_host.multi_authority", level="INFO") as captured:
            self.assertEqual(authority.require_manifest(changed), original)
            authority.require_manifest(changed)
            recreated = MultiAuthority(authority.runtime, authority.settings, now=authority.now)
            recreated.require_manifest(changed)
        self.assertEqual(len(captured.records), 1)
        self.assertEqual(captured.records[0].levelname, "INFO")
        rejected = deepcopy(changed)
        rejected["binding_contract"]["personas"] = []
        with self.assertRaises(TaskError) as error:
            recreated.require_manifest(rejected)
        self.assertEqual(error.exception.code, "host_binding_changed")

    async def test_existing_profile_reports_retryable_error_until_platform_loads(self):
        (await self.controller.initialize_profile()).unwrap()
        await self.controller.terminate()
        platforms = self.native.platforms
        self.native.platforms = {}

        restored = self.make_controller()
        unavailable = await restored.load_existing()
        self.assertEqual(unavailable.failure.code, "host_platform_unavailable")
        self.assertIsNone(restored.runtime)

        self.native.platforms = platforms
        loaded = await restored.load_existing()
        self.assertTrue(loaded.is_success, loaded.failure)
        self.assertTrue(loaded.unwrap()["loaded"])

    async def test_missing_plugin_module_reports_import_failure_without_exception_values(self):
        with patch.object(self.controller, "_assemble", side_effect=ModuleNotFoundError("synthetic-private-value")):
            with self.assertLogs("astrbot_host.multi_controller", level="ERROR") as captured:
                result = await self.controller.initialize_profile()
        self.assertEqual(result.failure.code, "host_module_unavailable")
        self.assertIn("重新加载 V2 插件", result.failure.reason)
        self.assertIn("error_type=ModuleNotFoundError", "\n".join(captured.output))
        self.assertNotIn("synthetic-private-value", "\n".join(captured.output) + result.failure.reason)
        self.assertTrue((await self.controller.initialize_profile()).is_success)

    async def test_unexpected_initialization_failure_logs_source_locations_without_exception_values(self):
        with patch.object(self.controller, "_assemble", side_effect=ValueError("synthetic-private-value")):
            with self.assertLogs("astrbot_host.multi_controller", level="ERROR") as captured:
                result = await self.controller.initialize_profile()
        self.assertEqual(result.failure.code, "host_control_failed")
        self.assertEqual(self.controller.status()["last_failure"]["operation"], "initialize_profile")
        diagnostic = "\n".join(captured.output)
        self.assertIn("operation=initialize_profile", diagnostic)
        self.assertIn("error_type=ValueError", diagnostic)
        self.assertIn("_initialize_manifest_locked", diagnostic)
        self.assertNotIn("synthetic-private-value", diagnostic)
        self.assertNotIn("synthetic-private-value", result.failure.reason)
        self.assertTrue((await self.controller.initialize_profile()).is_success)
        self.assertIsNone(self.controller.status()["last_failure"])

    def repeated_legacy_config(self, *, damaged):
        config = multi_config()
        config["multi_host"]["targets"][0]["participant_id"] = "*"
        if damaged:
            from companion_v2.execution.models import digest
            config["multi_host"]["targets"].append({"target_id": "legacy-" + digest(["qq-main", "100", "200"])[:20],
                "platform_id": "qq-main", "account_id": "100", "bot_id": "bot-a", "participant_id": "200"})
        return config

    async def test_supervised_task_records_process_and_event_loop_ownership(self):
        app = await self.start_host()
        _, batch = await self.accepted()
        task_id = app.messages.batch(batch)["generation_task_id"]
        attempt = self.controller.runtime.read_one(
            "SELECT record_json FROM execution_attempts WHERE task_id = ?", (task_id,))
        ownership = json.loads(attempt["record_json"])["worker_owner"]
        self.assertEqual(ownership["process_id"], os.getpid())
        self.assertEqual(ownership["loop_id"], str(id(asyncio.get_running_loop())))
        status = self.controller.status()
        self.assertIsNone(status["failure_code"])
        self.assertTrue(status["accepting"])

    async def test_saved_role_mapping_recovers_from_repeated_legacy_import_and_survives_restart(self):
        from astrbot_host.multi_authority import MANIFEST
        saved = self.make_controller(self.repeated_legacy_config(damaged=False))
        (await saved.initialize_profile()).unwrap()
        manifest = saved.authority.read(MANIFEST)
        await saved.terminate()
        damaged = self.repeated_legacy_config(damaged=True)
        persisted = []
        restored = self.make_controller(damaged)

        def save(candidate):
            persisted.append(deepcopy(candidate))
            damaged.clear()
            damaged.update(candidate)

        restored.save_plugin_config = save
        restored.register_routes()
        result = await restored.load_existing()
        self.assertTrue(result.is_success, result.failure)
        self.assertEqual(len(persisted), 1)
        self.assertEqual(restored.settings.binding_contract, manifest["binding_contract"])
        self.assertEqual(restored.authority.read(MANIFEST), manifest)
        self.assertIsNone(restored.last_failure)
        (await restored.start()).unwrap()
        context = restored.authority.context("second", "role-a")
        self.assertEqual(context.scope.account_id, "101")
        actual_paths = {route for route, _, _, _ in self.native.registered_web_apis}
        self.assertTrue(all("/legacy-" not in path for path in actual_paths))
        self.assertEqual(self.native.provider.client.state.calls, [])
        self.assertEqual(self.native.platform.client._api._wsr_api.calls, [])
        await restored.terminate()
        self.assertEqual(self.native.registered_web_apis, [])
        for _ in range(2):
            config, changed = migrate_single_target_config(persisted[-1])
            self.assertFalse(changed)
            reloaded = self.make_controller(config)
            (await reloaded.load_existing()).unwrap()
            (await reloaded.start()).unwrap()
            self.assertTrue(reloaded._accepting)
            self.assertEqual(reloaded.settings.binding_contract, manifest["binding_contract"])
            await reloaded.terminate()
        self.assertEqual(len(persisted), 1)

    async def test_automatic_mapping_recovery_reports_save_failure_and_leaves_saved_binding_intact(self):
        from astrbot_host.multi_authority import MANIFEST
        saved = self.make_controller(self.repeated_legacy_config(damaged=False))
        (await saved.initialize_profile()).unwrap()
        manifest = saved.authority.read(MANIFEST)
        await saved.terminate()
        damaged = self.repeated_legacy_config(damaged=True)
        restored = self.make_controller(damaged)
        before = deepcopy(damaged)
        restored.save_plugin_config = lambda candidate: (_ for _ in ()).throw(OSError("synthetic private details"))
        result = await restored.load_existing()
        self.assertEqual(result.failure.code, "account_config_save_failed")
        self.assertNotIn("private details", result.failure.reason)
        self.assertEqual(damaged, before)
        self.assertEqual(restored.authority.read(MANIFEST), manifest)
        self.assertIsNone(restored.session)
        self.assertFalse(restored._accepting)
        self.assertFalse(restored._lifecycle_lock.locked())

    async def test_legacy_profile_is_reused_only_when_no_shared_database_exists(self):
        legacy_profile = "legacy-profile"
        legacy_root = self.root / "profiles" / legacy_profile
        legacy_runtime = StorageRuntime.open(
            data_root=legacy_root / "runtime", installation_id=INSTALLATION_PREFIX + legacy_profile)
        legacy_runtime.close()

        config = multi_config()
        config["staging"]["profile_id"] = legacy_profile
        controller = self.make_controller(config)
        self.assertTrue(controller.profile_root.samefile(legacy_root))
        self.assertTrue(controller._adopting_legacy_profile)
        self.assertEqual(controller.settings.installation_id, INSTALLATION_PREFIX + legacy_profile)

        shared_root = self.root / "installations" / controller.settings.installation_key
        shared_runtime = StorageRuntime.open(
            data_root=shared_root / "runtime", installation_id=controller.settings.installation_id)
        shared_runtime.close()
        preferred = self.make_controller(config)
        self.assertTrue(preferred.profile_root.samefile(shared_root))
        self.assertFalse(preferred._adopting_legacy_profile)

    async def test_dashboard_routes_bind_each_target_memory_api_and_unregister_cleanly(self):
        self.controller.register_routes()
        registered = {route: handler for route, handler, _, _ in self.native.registered_web_apis}
        expected = {
            MULTI_HOST_PREFIX + suffix
            for suffix in ("/status", "/probe", "/initialize", "/start", "/stop",
                           "/authorize-delivery", "/revoke-delivery")
        }
        materials_route = NATIVE_PERCEPTION_PREFIX + "/materials"
        self.assertTrue({
            "/astrbot_plugin_private_companion/host-options",
            "/astrbot_plugin_private_companion/host-options/voice",
            "/astrbot_plugin_private_companion/host-options/poke",
            "/astrbot_plugin_private_companion/host-options/forward_intake",
            "/astrbot_plugin_private_companion/host-options/balance",
            "/astrbot_plugin_private_companion/storage",
        }.issubset(registered))
        storage_methods = {tuple(methods) for route, _, methods, _ in self.native.registered_web_apis
                           if route == "/astrbot_plugin_private_companion/storage"}
        self.assertEqual(storage_methods, {("GET",), ("POST",)})
        self.assertTrue(expected.issubset(registered))
        self.assertIn(materials_route, registered)
        expected_memory_routes = {
            role["memory_api"] for target in self.controller.web_api.target_inventory()
            for role in target["roles"] if role["memory_api"] is not None
        }
        memory_routes = [route for route in registered if route in expected_memory_routes]
        self.assertEqual(set(memory_routes), expected_memory_routes)
        # Reality 等按动作注册的路由是闭包而非绑定方法；目标绑定断言只看记忆路由。
        memory_handlers = [handler for route, handler in registered.items() if route in memory_routes]
        self.assertEqual({handler.__self__.controller.target_id for handler in memory_handlers},
                         {"first", "second"})
        self.assertTrue(all(handler.__self__.controller.role_id in {"role-a", "role-b"}
                            for handler in memory_handlers))

        request = NS(username="dashboard-admin", query=_Query())
        expected_parsers = {".pdf": True, ".docx": False, ".xlsx": True, ".pptx": False}
        with patch("astrbot_host.multi_api.request", request), patch("astrbot_host.memory_api.request", request), \
                patch("astrbot_host.multi_controller.parser_availability", return_value=expected_parsers):
            status_response = await registered[MULTI_HOST_PREFIX + "/status"]()
            self.assertEqual(status_response["status"], "ok")
            self.assertEqual(status_response["data"]["parser_availability"], expected_parsers)
            self.assertEqual((await registered[MULTI_HOST_PREFIX + "/probe"]())["status"], "ok")
            self.assertEqual((await registered[MULTI_HOST_PREFIX + "/initialize"]())["status"], "ok")
            self.assertEqual((await registered[MULTI_HOST_PREFIX + "/start"]())["status"], "ok")
            with patch("astrbot_host.native_perception_api.request", request):
                materials = await registered[materials_route]()
            self.assertEqual(materials["status"], "ok", materials)
            self.assertEqual(materials["data"]["items"], [])
            self.assertEqual(materials["data"]["history_count"], 0)
            for route in memory_routes:
                memory_settings = next(
                    handler for path, handler in registered.items()
                    if path == route + "/settings"
                )
                self.assertEqual((await memory_settings())["status"], "ok")
            request.query = _Query({"target_id": "first"})
            authorized = await registered[MULTI_HOST_PREFIX + "/authorize-delivery"]()
            self.assertEqual(authorized["status"], "ok")
            self.assertFalse(authorized["data"]["required"])
            request.query = _Query()
            self.assertEqual((await registered[MULTI_HOST_PREFIX + "/stop"]())["status"], "ok")

        self.controller.unregister_routes()
        self.assertEqual(self.native.registered_web_apis, [])

    async def test_lifecycle_host_waits_do_not_block_memory_data_routes(self):
        initialized = await self.controller.initialize_profile()
        self.assertTrue(initialized.is_success, initialized.failure)
        self.controller.register_routes()
        memory_settings = next(handler for route, handler, _, _ in self.native.registered_web_apis
            if "/targets/first/" in route and route.endswith("/memory/settings"))
        dashboard_request = NS(username="dashboard-admin", query={})

        probe_entered, allow_probe = asyncio.Event(), asyncio.Event()
        conversation_manager = self.native.conversation_manager
        original_lookup = conversation_manager.get_curr_conversation_id

        async def hold_probe(umo):
            probe_entered.set()
            await allow_probe.wait()
            return await original_lookup(umo)

        with patch("astrbot_host.memory_api.request", dashboard_request):
            with patch.object(conversation_manager, "get_curr_conversation_id", hold_probe):
                starting = asyncio.create_task(self.controller.start())
                try:
                    await asyncio.wait_for(probe_entered.wait(), timeout=2)
                    response = await asyncio.wait_for(memory_settings(), timeout=1)
                    self.assertEqual(response["status"], "ok", response)
                finally:
                    allow_probe.set()
                started = await starting
                self.assertTrue(started.is_success, started.failure)

            session = self.controller.session
            original_close = session.close
            close_entered, allow_close = asyncio.Event(), asyncio.Event()

            async def hold_close(*, grace_seconds=0.5):
                close_entered.set()
                await allow_close.wait()
                return await original_close(grace_seconds=grace_seconds)

            session.close = hold_close
            stopping = asyncio.create_task(self.controller.stop())
            try:
                await asyncio.wait_for(close_entered.wait(), timeout=2)
                response = await asyncio.wait_for(memory_settings(), timeout=1)
                self.assertEqual(response["status"], "ok", response)
            finally:
                allow_close.set()
                session.close = original_close
            stopped = await stopping
            self.assertTrue(stopped.is_success, stopped.failure)

    async def test_global_probe_does_not_read_window_persona_override(self):
        entered = asyncio.Event()
        conversation_lookups = {}
        manager = self.native.conversation_manager
        current = manager.get_curr_conversation_id

        async def tracked_conversation(umo):
            conversation_lookups[umo] = conversation_lookups.get(umo, 0) + 1
            return await current(umo)

        async def unavailable(_umo):
            entered.set()
            raise AssertionError("global startup must not depend on a window persona override")

        manager.get_curr_conversation_id = tracked_conversation
        self.controller.native_types = replace(
            self.controller.native_types, session_persona_override_lookup=unavailable)
        with patch.object(self.controller.session_topology, "inventory_report",
                          AsyncMock(side_effect=AssertionError("global probe must not read window personas"))) as inventory:
            result = await self.controller.probe()
        self.assertTrue(result.is_success, result.failure)
        self.assertFalse(entered.is_set())
        self.assertEqual(set(conversation_lookups.values()), {2})
        inventory.assert_not_awaited()
        self.assertFalse(self.controller._lifecycle_lock.locked())

    async def test_start_uses_the_current_host_persona_when_no_v2_override_exists(self):
        async def no_override(_umo):
            return None

        self.controller.native_types = replace(
            self.controller.native_types, session_persona_override_lookup=no_override)
        initialized = await self.controller.initialize_profile()
        self.assertTrue(initialized.is_success, initialized.failure)

        started = await self.controller.start()
        self.assertTrue(started.is_success, started.failure)
        self.assertEqual(self.controller.session.application.state, "running")

    async def test_wal_checkpoint_runs_on_a_worker_thread_and_finishes_before_shutdown(self):
        initialized = await self.controller.initialize_profile()
        self.assertTrue(initialized.is_success, initialized.failure)
        application = self.controller.session.application
        loop = asyncio.get_running_loop()
        loop_thread = threading.get_ident()
        checkpoint_finished = asyncio.Event()
        checkpoint_threads = []

        def checkpoint():
            checkpoint_threads.append(threading.get_ident())
            loop.call_soon_threadsafe(checkpoint_finished.set)
            return (0, 0, 0)

        application.runtime.checkpoint_passive = checkpoint
        started = await self.controller.start()
        self.assertTrue(started.is_success, started.failure)
        await asyncio.wait_for(checkpoint_finished.wait(), timeout=2)
        stopped = await self.controller.stop()

        self.assertTrue(stopped.is_success, stopped.failure)
        self.assertNotEqual(checkpoint_threads[0], loop_thread)

    async def test_lookup_failure_identifies_the_native_read_that_stalled(self):
        async def unresponsive(*args, **kwargs):
            await asyncio.Event().wait()

        manager = self.native.conversation_manager
        for method, stage in (("get_curr_conversation_id", "conversation_id"),):
            with self.subTest(stage=stage), patch.object(manager, method, AsyncMock(side_effect=unresponsive)), \
                    patch("companion_v2.adapters.astrbot.native.NATIVE_LOOKUP_TIMEOUT_SECONDS", 0.01):
                result = await self.controller.probe()
                self.assertEqual(result.failure.code, "host_native_lookup_timeout__" + stage)
                self.assertEqual(self.controller.last_failure["code"], result.failure.code)
                self.assertEqual(self.controller.last_failure["reason"], result.failure.reason)
                self.assertFalse(self.controller._lifecycle_lock.locked())

    async def test_loading_saved_wildcard_manifest_does_not_scan_native_windows(self):
        config = multi_config()
        config["multi_host"]["targets"] = [{**config["multi_host"]["targets"][0], "participant_id": "*"}]
        config["multi_host"]["personas"] = [row for row in config["multi_host"]["personas"] if row["bot_id"] == "bot-a"]
        saved = self.make_controller(config)
        (await saved.initialize_profile()).unwrap()
        await saved.terminate()
        restored = self.make_controller(config)
        restored.last_failure = {"code": "host_native_lookup_timeout"}
        with patch.object(restored.session_topology, "inventory", AsyncMock(side_effect=AssertionError("unneeded directory read"))) as inventory, \
                patch.object(self.native.conversation_manager, "get_curr_conversation_id",
                             AsyncMock(side_effect=AssertionError("unneeded conversation read"))) as current:
            result = await restored.load_existing()
        self.assertTrue(result.is_success, result.failure)
        self.assertTrue(result.unwrap()["loaded"])
        inventory.assert_not_awaited()
        current.assert_not_awaited()
        self.assertIsNone(restored.last_failure)
        self.assertFalse(restored._accepting)
        self.assertEqual(self.native.provider.client.state.calls, [])

    async def test_wildcard_probe_allows_directory_to_outlast_one_native_read_budget(self):
        config = multi_config()
        config["multi_host"]["targets"] = [{**config["multi_host"]["targets"][0], "participant_id": "*"}]
        config["multi_host"]["personas"] = [row for row in config["multi_host"]["personas"] if row["bot_id"] == "bot-a"]
        controller = self.make_controller(config)
        self.native.conversation_manager.session_conversations = {
            f"qq-main:FriendMessage:{peer}": "conversation-a" for peer in range(200, 216)}
        self.native.conversation_manager.session_conversations["qq-main:GroupMessage:300"] = "conversation-a"
        lookups = []

        async def services(umo):
            self.assertIn(":FriendMessage:", umo)
            lookups.append(umo)
            await asyncio.sleep(0.1)
            return {"persona_id": "original"}

        with patch.object(self.native.persona_manager, "resolve_selected_persona",
                          side_effect=AssertionError("AstrBot persona resolution must not choose a V2 role")), \
                patch.object(controller.session_topology, "services", services), \
                patch("companion_v2.adapters.astrbot.native.NATIVE_LOOKUP_TIMEOUT_SECONDS", 0.3):
            _, conversations = await controller._probe()
        self.assertEqual(len(conversations), 16)
        self.assertEqual(len(lookups), 16)
        self.assertEqual(self.native.provider.client.state.calls, [])

    async def test_probe_is_read_only_and_first_start_prepares_one_shared_runtime(self):
        before = list(self.root.iterdir())
        with patch.object(self.native.conversation_manager, "get_conversation",
                          AsyncMock(side_effect=AssertionError("probe must not read full host conversation state"))) as conversation:
            self.assertTrue((await self.controller.probe()).is_success)
        conversation.assert_not_awaited()
        self.assertEqual(list(self.root.iterdir()), before)
        started = await self.controller.start()
        self.assertTrue(started.is_success, started.failure)
        app = self.controller.session.application
        self.assertIn("role.self_review", app.router.profiles)
        self.assertIn("role.expression.learn", app.router.profiles)
        contexts = []
        for target in ("first", "second"):
            context, _ = await self.accepted(target)
            contexts.append(context)
        self.assertEqual(len(list(self.root.rglob("companion-v2.sqlite3"))), 1)
        self.assertEqual(contexts[0].actor.subject, contexts[1].actor.subject)
        self.assertNotEqual(owner_key(contexts[0]), owner_key(contexts[1]))
        self.assertEqual(app.state, "running")
        # Each target still sends through only its own native account.
        for platform in self.native.platforms.values():
            self.assertEqual(len(platform.client._api._wsr_api.calls), 1)

    async def test_unregistered_private_persona_does_not_block_start_or_claim(self):
        self.native.persona_records["unregistered"] = {"name": "unregistered", "prompt": "未绑定的人格。"}
        self.window_personas["qq-main:FriendMessage:200"] = "unregistered"
        initialized = await self.controller.initialize_profile()
        self.assertTrue(initialized.is_success, initialized.failure)
        started = await self.controller.start()
        self.assertTrue(started.is_success, started.failure)

        event = self.event()
        self.assertTrue(self.controller.claims(event))
        self.assertIsNone(await self.controller.persona_mapping_for_event(event))

    async def test_dashboard_balance_source_reaches_shared_app_and_survives_reload(self):
        app = await self.start_host()
        api = HostOptionsWebAPI(self.controller)

        def save(candidate):
            self.controller.config.clear()
            self.controller.config.update(candidate)

        self.controller.save_plugin_config = save
        current = api._view()
        saved = api._configure(None, "balance", {"edit_token": current["edit_tokens"]["balance"],
            "changes": {"url": "https://shared.example.com", "api_key": "fixture-secret",
                        "user_id": "6789", "value_divisor": "250000"}})
        port = app.contact_schedule.balance_events.port
        self.assertEqual((port.url, port.headers["New-Api-User"], str(port.divisor)),
                         ("https://shared.example.com/api/user/self", "6789", "250000"))
        self.assertNotIn("fixture-secret", json.dumps(saved))

        config = self.controller.config.copy()
        await self.controller.terminate()
        self.controller = self.make_controller(config)
        loaded = await self.controller.load_existing()
        self.assertTrue(loaded.is_success, loaded.failure)
        restored = self.controller.session.application.contact_schedule.balance_events.port
        self.assertEqual((restored.url, restored.headers["New-Api-User"], str(restored.divisor)),
                         ("https://shared.example.com/api/user/self", "6789", "250000"))
        self.assertEqual(self.controller.session.application.state, "new")

    async def test_two_bots_two_personas_keep_facts_and_sources_isolated_after_switch_back(self):
        app = await self.start_host()
        originals, records = {}, {}
        for target in ("first", "second"):
            context, batch = await self.accepted(target)
            originals[target] = context
            records[(target, "role-a")] = self.remember(app, context, batch)
        for target in ("first", "second"):
            self.clock.advance(timedelta(seconds=1))
            configured = self.controller.settings.target(target)
            self.window_personas[f"{configured.platform_id}:FriendMessage:{configured.participant_id}"] = "second"
            context, batch = await self.accepted(target, "persona-b")
            records[(target, "role-b")] = self.remember(app, context, batch)
            original = records[(target, "role-a")]
            self.assertEqual(app.memory.writer.read(context, AtomRef(original["atom_id"], 1)).failure.code, "memory_unavailable")
            self.assertEqual(app.memory.writer.read(originals[target], AtomRef(original["atom_id"], 1)).failure.code,
                             "role_binding_changed")
        self.assertEqual(len({item["operation_id"] for item in records.values()}), 4)
        self.assertEqual(len({item["atom_id"] for item in records.values()}), 4)
        for target in ("first", "second"):
            self.clock.advance(timedelta(seconds=1))
            configured = self.controller.settings.target(target)
            self.window_personas[f"{configured.platform_id}:FriendMessage:{configured.participant_id}"] = "original"
            context, _ = await self.accepted(target, "back-a")
            for (record_target, role), record in records.items():
                result = app.memory.writer.read(context, AtomRef(record["atom_id"], 1))
                self.assertEqual(result.is_success, target == record_target and role == "role-a")
            query_id = "all-" + target
            app.memory.query.begin(context, query_id)
            result = app.memory.query.query(context, query_id, query_intent="咖啡", kind="sources").unwrap()
            self.assertNotIn("persona-b", json.dumps(result))

    async def test_later_native_conversation_recalls_same_owner_but_cannot_resolve_old_pending(self):
        app = await self.start_host()
        context, batch = await self.accepted()
        record = self.remember(app, context, batch)
        pending = self.remember(app, context, batch, key="pending", pending=True)
        self.native.conversations["qq-main"] = "new-conversation"
        self.clock.advance(timedelta(seconds=1))
        later, _ = await self.accepted(identifier="later")
        self.assertEqual(owner_key(context), owner_key(later))
        self.assertTrue(app.memory.writer.read(later, AtomRef(record["atom_id"], 1)).is_success)
        result = app.memory_commands.resolve_pending(later, pending["operation_id"], decision="accept",
            deadline_at=stamp(self.clock.now() + timedelta(seconds=30)))
        self.assertEqual(result["reason_code"], "memory_pending_context_changed")

    async def test_restart_load_preserves_current_context_and_commits_original_pending(self):
        app = await self.start_host()
        context, batch = await self.accepted()
        pending = self.remember(app, context, batch, pending=True)
        await self.controller.terminate()
        self.controller = self.make_controller()
        result = await self.controller.load_existing()
        self.assertTrue(result.is_success, result.failure)
        self.assertFalse(result.unwrap()["started"])
        loaded = self.controller.authority.context("first", "role-a")
        self.assertEqual(context, loaded)
        self.assertTrue((await self.controller.start()).is_success)
        app = self.controller.session.application
        committed = app.memory_commands.resolve_pending(loaded, pending["operation_id"], decision="accept",
            deadline_at=stamp(self.clock.now() + timedelta(seconds=30)))
        self.assertEqual(committed["status"], "succeeded", committed)
        other = self.controller.authority.context("second", "role-a")
        self.assertEqual(app.memory.writer.operation(other, pending["operation_id"]).failure.code,
                         "memory_operation_unavailable")

    async def test_delivery_authorization_is_compatibility_only_and_delivery_follows_each_target(self):
        """正式版移除了按目标投递授权闸门：端点保留为兼容 no-op，投递按各自目标直达。"""
        self.controller = self.make_controller(multi_config(delivery_mode="onebot_private"))
        app = await self.start_host()
        granted = (await self.controller.authorize_delivery("first")).unwrap()
        self.assertEqual((granted["authorized"], granted["required"]), (True, False))
        for target in ("first", "second"):
            await self.accepted(target)
        self.assertEqual(len(self.native.platforms["qq-main"].client._api._wsr_api.calls), 1)
        self.assertEqual(len(self.native.platforms["qq-second"].client._api._wsr_api.calls), 1)
        revoked = (await self.controller.revoke_delivery("first")).unwrap()
        self.assertEqual((revoked["authorized"], revoked["required"]), (True, False))
        await self.accepted("first", "revoked")
        self.assertEqual(len(self.native.platforms["qq-main"].client._api._wsr_api.calls), 2)
        await self.accepted("second", "authorized")
        action, payload = self.native.platforms["qq-second"].client._api._wsr_api.calls[0]
        self.assertEqual((action, payload["self_id"], payload["user_id"]), ("send_private_msg", "101", 200))

    async def test_other_target_can_progress_while_one_native_lookup_is_waiting(self):
        await self.start_host()
        entered, release = asyncio.Event(), asyncio.Event()
        original = self.controller.session.bridge._role
        async def blocked(account, sender):
            if account.bot_id == "bot-a":
                entered.set()
                await release.wait()
            return await original(account, sender)
        self.controller.session.bridge._role = blocked
        task = asyncio.create_task(self.controller.receive_claimed(self.event()))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            result = await asyncio.wait_for(self.controller.receive_claimed(self.event("second")), 2)
            self.assertTrue(result.is_success, result.failure)
        finally:
            release.set()
            self.assertTrue((await task).is_success)

    async def test_rejected_input_cannot_publish_sources_or_grants_for_unconfigured_user(self):
        app = await self.start_host()
        event = self.event()
        event.message_obj.sender.user_id = "201"
        event.session.session_id = "201"
        event.unified_msg_origin = "qq-main:FriendMessage:201"
        before = database_contents(app.runtime)
        self.assertFalse(self.controller.claims(event))
        self.assertEqual((await self.controller.receive_claimed(event)).failure.code, "host_not_claimed")
        self.assertEqual(database_contents(app.runtime), before)

    async def test_identity_remapping_and_persona_rename_are_rejected_as_binding_changes(self):
        await self.start_host()
        context, batch = await self.accepted()
        app = self.controller.session.application
        record = self.remember(app, context, batch)
        await self.controller.terminate()
        changed = multi_config()
        changed["multi_host"]["personas"][0]["role_id"] = "replacement"
        rejected = self.make_controller(changed)
        # 绑定差异检查先于旧的身份映射冲突，并点名变化的绑定类别。
        self.assertEqual((await rejected.initialize_profile()).failure.code, "host_binding_changed")
        await rejected.terminate()
        renamed = multi_config()
        renamed["multi_host"]["personas"][0]["persona_id"] = "renamed"
        self.native.persona_records["renamed"] = {"name": "renamed", "prompt": self.native.persona["prompt"]}
        renamed_controller = self.make_controller(renamed)
        # 人格映射也在绑定契约里：换绑人格同样要重走绑定（见 page-designs/16-setup.md）。
        self.assertEqual((await renamed_controller.initialize_profile()).failure.code, "host_binding_changed")
        await renamed_controller.terminate()
        self.controller = self.make_controller(multi_config())
        app = await self.start_host()
        current = self.controller.authority.context("first", "role-a")
        self.assertEqual(owner_key(context), owner_key(current))
        self.assertTrue(app.memory.writer.read(current, AtomRef(record["atom_id"], 1)).is_success)

    async def test_revoked_memory_grant_is_not_restored_by_intake_or_reinitialization(self):
        app = await self.start_host()
        context = self.controller.authority.context("first", "role-a")
        identity = self.controller.authority.identities
        identity.revoke_grant(self.controller.authority.grant_id(context.scope, "memory.read"),
            revoked_at=self.clock.now(), expected_revision=identity.revision)
        result = await self.controller.receive_claimed(self.event())
        self.assertEqual(result.failure.code, "authorization_revoked")
        await self.controller.stop()
        result = await self.controller.initialize_profile()
        self.assertEqual(result.failure.code, "authorization_revoked")

    async def test_revoked_dialogue_permission_does_not_break_the_host(self):
        """对话权限是操作员按人收的：收回之后宿主仍要能重开，收信闸负责礼貌拒绝。

        收回前这条链路会在供给阶段直接抛 `authorization_revoked`（既没有对话、
        也没有回话），用户看到的是"插件坏了"。收回 `text` 这类必需能力仍然要硬失败——
        那才是真的配置损坏。
        """
        await self.start_host()
        context = self.controller.authority.context("first", "role-a")
        identity = self.controller.authority.identities
        identifier = self.controller.authority.grant_id(context.scope, "conversation.reply")
        identity.revoke_grant(identifier, revoked_at=self.clock.now(), expected_revision=identity.revision)

        await self.controller.stop()
        self.assertTrue((await self.controller.initialize_profile()).is_success,
                        "收回对话权限后宿主仍应能重开这一次会话")
        # 撤销过的 grant 不能被重开流程悄悄补回来：授权层必须仍然说"已收回"。
        actor = self.controller.authority.actor(self.controller.settings.target("first"))
        access = identity.authorize(actor, scope=context.scope, capability="conversation.reply",
                                    observed_at=self.clock.now())
        self.assertEqual(access.failure.code, "authorization_revoked")

        # 供给路径本身不再把它当故障：上下文仍然可以解析出来（拒绝发生在收信闸）。
        selected = self.controller.authority.select(self.controller.settings.target("first"),
                                                    context.scope, context.role_binding.role)
        self.assertEqual(selected.scope.key, context.scope.key)

    async def test_revoked_required_capability_still_fails_hard(self):
        await self.start_host()
        context = self.controller.authority.context("first", "role-a")
        identity = self.controller.authority.identities
        identity.revoke_grant(self.controller.authority.grant_id(context.scope, "text"),
            revoked_at=self.clock.now(), expected_revision=identity.revision)

        await self.controller.stop()
        result = await self.controller.initialize_profile()
        self.assertEqual(result.failure.code, "authorization_revoked",
                         "必需能力被收回是真故障，不能跟着一起放行")

    async def test_role_level_api_source_skips_wildcard_templates(self):
        config = multi_config()
        config["multi_host"]["targets"] = [
            {"target_id": "wild", "platform_id": "qq-main", "account_id": "100",
             "bot_id": "bot-a", "participant_id": "*"},
            {"target_id": "exact", "platform_id": "qq-second", "account_id": "101",
             "bot_id": "bot-b", "participant_id": "200"},
        ]
        config["multi_host"]["personas"] = [
            {"bot_id": "bot-a", "persona_id": "original", "role_id": "role-shared"},
            {"bot_id": "bot-b", "persona_id": "original", "role_id": "role-shared"},
        ]
        controller = self.make_controller(config)
        self.assertEqual(controller.role_source_target_id("role-shared", "wild"), "exact")
        self.assertEqual(controller.role_source_target_id("role-shared", "exact"), "exact")

    async def test_context_follows_legacy_role_id_after_shared_persona_merge(self):
        config = multi_config()
        config["multi_host"]["personas"] = [
            {"bot_id": "bot-a", "persona_id": "original", "role_id": "role-bot-a-generated"},
            {"bot_id": "bot-b", "persona_id": "original", "role_id": "role-bot-b-generated"},
        ]
        controller = self.make_controller(config)
        initialized = await controller.initialize_profile()
        self.assertTrue(initialized.is_success, initialized.failure)
        canonical_roles = {item.role_id for item in controller.settings.personas}
        self.assertEqual(len(canonical_roles), 1)
        canonical_role = next(iter(canonical_roles))
        alias = next(role for role in controller.settings.role_aliases if role != canonical_role)
        self.assertEqual(controller.settings.canonical_role_id(alias), canonical_role)

        target = controller.settings.target("first")
        stored = controller.authority.read(controller.authority.context_key(target, canonical_role))
        self.assertIsNotNone(stored)
        scope = ScopeRef.restore(stored)
        selected = controller.authority.personas.resolve(
            scope, observed_at=self.clock.now()).unwrap().selected
        old_role = SubjectRef(controller.settings.installation_id, SubjectKind.BOT, alias)
        controller.authority.personas.set_binding(
            replace(selected, role=old_role, revision=selected.revision + 1),
            expected_revision=selected.revision, observed_at=self.clock.now())
        with controller.runtime.transaction() as connection:
            connection.execute("DELETE FROM runtime_metadata WHERE key = ?",
                               (controller.authority.context_key(target, canonical_role),))
        controller.authority.write(controller.authority.context_key(target, alias), scope.document())

        context = controller.authority.context(target.target_id, canonical_role)
        self.assertEqual(context.role_binding.role.subject_id, canonical_role)
        self.assertEqual(context.scope.key, scope.key)
        resolved = controller.authority.identities.resolve_role(
            context.scope, observed_at=self.clock.now())
        self.assertTrue(resolved.is_success, resolved.failure)
        self.assertEqual(resolved.unwrap(), context.role_binding)


class MultiHostVideoTests(MultiFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        MultiFixture.setUp(self)
        self.types = NativeAdapterTypes("4.27.2", self.types.event, self.types.provider,
            self.types.function_tool, self.types.tool_set, self.types.reverse_api,
            video=Video, video_loader=load_native_video, component_reader=private_components,
            session_persona_override_lookup=self.types.session_persona_override_lookup)
        self.controller = self.make_controller()

    def video_event(self, target):
        config = self.controller.settings.target(target)
        event = self.event(target, identifier="same-message-id", text="这是一段视频")
        locator = "base64://" + base64.b64encode(video_bytes()).decode("ascii")
        event.message_obj.message = [Video(file=locator)]
        event.message_obj.raw_message = dict(post_type="message", message_type="private", sub_type="friend",
            self_id=int(config.account_id), user_id=int(config.participant_id),
            sender={"user_id": int(config.participant_id)}, message_id="same-message-id",
            message=[{"type": "video", "data": {"file": locator}}])
        self.window_personas.setdefault(event.unified_msg_origin, "original")
        return event

    async def test_same_native_message_id_keeps_video_sources_isolated_by_account_target(self):
        app = await self.start_host()
        batches = []
        for target in ("first", "second"):
            result = await self.controller.receive_claimed(self.video_event(target))
            self.assertTrue(result.is_success, result.failure)
            batch = result.unwrap()
            await wait_for_batch(self.controller.session, batch)
            batches.append(batch)
        self.assertNotEqual(*batches)
        payloads = app.runtime.read_all("SELECT * FROM conversation_video_payloads ORDER BY source_id", ())
        self.assertEqual(len(payloads), 2)
        self.assertEqual({row["content"] for row in payloads}, {video_bytes()})
        self.assertEqual(len(self.native.provider.client.state.calls), 2)
        video_evidence = set()
        for payload, _ in self.native.provider.client.state.calls:
            user_messages = [message for message in payload["messages"] if message["role"] == "user"]
            self.assertEqual(len(user_messages), 1)
            content = user_messages[0]["content"]
            # This fixture has no native video model. Its text reply receives
            # the unavailable narration for the correct original attachment.
            self.assertIsInstance(content, str)
            materials = json.loads(content)["materials"]
            video = [item for item in materials if item["purpose"].startswith("input_video:")]
            narration = [item for item in materials if item["purpose"] == "current_video_narration"]
            self.assertEqual(len(video), 1)
            self.assertEqual(len(narration), 1)
            self.assertEqual(narration[0]["evidence"], video[0]["evidence"])
            video_evidence.add(video[0]["evidence"])
        self.assertEqual(len(video_evidence), 2)
        for platform in self.native.platforms.values():
            self.assertEqual(len(platform.client._api._wsr_api.calls), 1)


if __name__ == "__main__":
    unittest.main()
