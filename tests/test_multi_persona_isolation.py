# -*- coding: utf-8 -*-
from __future__ import annotations

import ast
import asyncio
from copy import deepcopy
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from quart import Quart

from astrbot_plugin_private_companion.main import (
    PrivateCompanionPlugin,
    _multi_persona_event_context,
)
from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi
from astrbot_plugin_private_companion.plugin_identity import PLUGIN_ID
from astrbot_plugin_private_companion.storage.store_manager import StoreManager


def _plugin_harness(root: str) -> PrivateCompanionPlugin:
    plugin = PrivateCompanionPlugin.__new__(PrivateCompanionPlugin)
    plugin.enable_multi_persona_mode = True
    plugin.multi_persona_ids = ["main", "alt"]
    plugin.plugin_specific_persona_id = "main"
    plugin.config = {
        "multi_persona_ids": ["main", "alt"],
        "multi_persona_window_bindings": {},
    }
    plugin.data_dir = root
    plugin.data_file = str(Path(root) / "companions.json")
    plugin._persona_profiles_dir = str(Path(root) / "persona_profiles")
    plugin._persona_data_profiles = {}
    plugin._persona_window_claims = {}
    plugin._persona_window_conflicts = {}
    plugin._page_current_persona_id = "main"
    plugin._passive_light_injection_cache = {}
    plugin._passive_state_session_cache = {}
    plugin._data_lock = asyncio.Lock()
    plugin._stop_event = asyncio.Event()
    plugin._data_save_task = None
    plugin._data_save_dirty = False
    plugin._persona_data_save_tasks = {}
    plugin._persona_data_save_dirty = set()
    plugin._data_default = {
        "users": {"legacy": {"name": "旧用户"}},
        "daily_plan": {"marker": "旧日程"},
        "bot_diaries": {"2026-08-03": {"content": "旧日记"}},
        "persona_settings": {},
    }
    plugin._new_store = lambda: {
        "users": {},
        "daily_plan": {},
        "bot_diaries": {},
        "persona_settings": {},
    }
    plugin._persona_data_profiles["alt"] = {
        **plugin._new_store(),
        "persona_settings": {"bot_name": "alt"},
        "persona_settings_schema_version": 1,
        "persona_settings_revision": 1,
    }
    plugin._ensure_store_defaults = lambda profile: profile
    plugin._sanitize_store_control_tags_inplace = lambda _profile: 0
    plugin._compact_store_history_inplace = lambda _profile: {}
    plugin._log_store_control_cleanup = lambda *_args, **_kwargs: None
    plugin._save_config_if_possible = AsyncMock(return_value=True)
    return plugin


def _unified_group_event(*, timestamp: int = 100) -> SimpleNamespace:
    raw_message = {
        "post_type": "message",
        "message_type": "group",
        "time": timestamp,
        "user_id": "user-1",
        "group_id": "group-1",
        "self_id": "bot-1",
        "message": "不应进入统一身份存储的原始消息",
    }
    return SimpleNamespace(
        unified_msg_origin="onebot:GroupMessage:group-1",
        message_str="不应进入统一身份存储的原始消息",
        message_obj=SimpleNamespace(raw_message=raw_message, message_id=""),
        get_sender_id=lambda: "user-1",
        get_platform_name=lambda: "onebot",
        is_private_chat=lambda: False,
    )


@_multi_persona_event_context
async def _record_event_persona(plugin, event, delay: float = 0.01):
    active = plugin._active_persona_scope()
    plugin.data["event_trace"] = [active]
    await asyncio.sleep(delay)
    return plugin._active_persona_scope(), list(plugin.data["event_trace"])


class _ConversationManager:
    def __init__(self, persona_id: str) -> None:
        self.persona_id = persona_id

    async def get_curr_conversation_id(self, umo: str) -> str:
        return f"conversation:{umo}"

    async def get_conversation(self, umo: str, conversation_id: str):
        return SimpleNamespace(persona_id=self.persona_id)


class MultiPersonaIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def test_unified_identity_is_persona_scoped_but_wire_group_scope_is_shared(self):
        with tempfile.TemporaryDirectory() as root:
            plugin = _plugin_harness(root)
            plugin.target_platform = "onebot"
            plugin._known_bot_self_ids = lambda: {"bot-1"}
            plugin._extract_group_id_from_event = lambda _event: "group-1"

            snapshots: dict[str, dict[str, object]] = {}
            for persona_id in ("main", "alt"):
                token = plugin._activate_persona_id(persona_id)
                try:
                    event = _unified_group_event(timestamp=100)
                    identity = plugin._unified_person_event_identity(event)
                    attached = plugin._req036_attach_unified_profile_context(
                        event,
                        user={"nickname": "测试用户"},
                        group_id="group-1",
                        source="group_observation",
                    )
                    context = plugin.build_unified_person_context(event)
                    snapshots[persona_id] = {
                        "identity": identity,
                        "attached": attached,
                        "context": context,
                    }
                finally:
                    plugin._deactivate_persona_for_event(token)

            main_identity = snapshots["main"]["identity"]
            alt_identity = snapshots["alt"]["identity"]
            self.assertIsInstance(main_identity, dict)
            self.assertIsInstance(alt_identity, dict)
            main_instance = main_identity["companion_instance_id"]
            alt_instance = alt_identity["companion_instance_id"]
            self.assertNotEqual(main_instance, alt_instance)
            self.assertTrue(main_instance.startswith(f"{PLUGIN_ID}:persona:"))
            self.assertTrue(alt_instance.startswith(f"{PLUGIN_ID}:persona:"))

            main_person_id = snapshots["main"]["attached"]["person_id"]
            alt_person_id = snapshots["alt"]["attached"]["person_id"]
            self.assertNotEqual(main_person_id, alt_person_id)
            for persona_id, instance_id in (
                ("main", main_instance),
                ("alt", alt_instance),
            ):
                attached = snapshots[persona_id]["attached"]
                context = snapshots[persona_id]["context"]
                dto_scope = attached["dto"]["context_overlays"]["group_scope"]
                self.assertEqual("group:onebot:group-1", dto_scope)
                self.assertEqual(dto_scope, context["group_scope"])
                self.assertEqual(
                    dto_scope,
                    context["p3"]["slots"]["scene"]["payload"]["group_scope"],
                )
                self.assertEqual(
                    instance_id,
                    context["p3"]["slots"]["persona"]["payload"]["companion_instance_id"],
                )

    async def test_single_persona_unified_identity_and_scope_keep_legacy_values(self):
        with tempfile.TemporaryDirectory() as root:
            plugin = _plugin_harness(root)
            plugin.enable_multi_persona_mode = False
            plugin.target_platform = "onebot"
            plugin._known_bot_self_ids = lambda: {"bot-1"}

            identity = plugin._unified_person_event_identity(_unified_group_event())

            self.assertEqual(PLUGIN_ID, identity["companion_instance_id"])
            self.assertEqual(
                "group:onebot:group-1",
                plugin._unified_persona_scoped_value("group:onebot:group-1"),
            )

    async def test_source_events_without_message_ids_use_content_free_event_anchors(self):
        with tempfile.TemporaryDirectory() as root:
            plugin = _plugin_harness(root)
            plugin.target_platform = "onebot"
            plugin._known_bot_self_ids = lambda: {"bot-1"}
            token = plugin._activate_persona_id("main")
            try:
                first_event = _unified_group_event(timestamp=100)
                second_event = _unified_group_event(timestamp=100)
                first = plugin._req036_attach_unified_profile_context(
                    first_event,
                    user={"nickname": "测试用户"},
                    group_id="group-1",
                    source="group_observation",
                )
                plugin._req036_attach_unified_profile_context(
                    second_event,
                    user={"nickname": "测试用户"},
                    group_id="group-1",
                    source="group_observation",
                )
                plugin._req036_attach_unified_profile_context(
                    first_event,
                    user={"nickname": "测试用户"},
                    group_id="group-1",
                    source="group_observation",
                )

                person_id = first["person_id"]
                identity_key = first["dto"]["person_ref"]["resolved_identity_key"]
                checkpoint = plugin.data["unified_person"]["binding_checkpoints"][
                    f"{person_id}:{identity_key}"
                ]
                self.assertEqual(2, checkpoint["source_event_count"])
                self.assertEqual(2, len(checkpoint["source_event_fingerprints"]))
                self.assertNotIn(
                    "不应进入统一身份存储的原始消息",
                    repr(plugin.data["unified_person"]),
                )
            finally:
                plugin._deactivate_persona_for_event(token)

    async def test_disabled_portrait_capability_never_calls_memory_reader(self):
        with tempfile.TemporaryDirectory() as root:
            plugin = _plugin_harness(root)
            reader = AsyncMock(return_value={"ok": True, "items": [{"summary": "偏好"}]})
            plugin._memory_companion_bridge = lambda: SimpleNamespace(
                read_unified_profile_portrait=reader
            )
            person_id = "person_" + "a" * 24
            person_ref = {
                "person_id": person_id,
                "resolved_identity_key": "chat-origin-v1:" + "b" * 64,
                "projection_revision": 1,
                "identity_assurance": "observed",
                "profile_status": "active",
            }
            for capability_summary in (
                {},
                {"portrait_usage_enabled": False},
                {"portrait_usage_enabled": "true"},
            ):
                with self.subTest(capability_summary=capability_summary):
                    event = SimpleNamespace(
                        private_companion_unified_profile_context={
                            "person_ref": person_ref,
                            "capability_summary": capability_summary,
                            "context_overlays": {"group_scope": "group:onebot:group-1"},
                        }
                    )
                    self.assertEqual(
                        "智能画像当前未开启。",
                        await plugin._req036_read_group_self_portrait(event),
                    )
            reader.assert_not_called()

            enabled_event = SimpleNamespace(
                private_companion_unified_profile_context={
                    "person_ref": person_ref,
                    "capability_summary": {"portrait_usage_enabled": True},
                    "context_overlays": {"group_scope": "group:onebot:group-1"},
                }
            )
            self.assertIn(
                "偏好",
                await plugin._req036_read_group_self_portrait(enabled_event),
            )
            reader.assert_awaited_once()

    async def test_primary_is_single_store_and_secondary_starts_blank(self):
        with tempfile.TemporaryDirectory() as root:
            plugin = _plugin_harness(root)

            primary = plugin._ensure_persona_profile("main")
            secondary = plugin._ensure_persona_profile("alt")

            self.assertEqual("旧用户", primary["users"]["legacy"]["name"])
            self.assertIs(primary, plugin._data_default)
            self.assertFalse(plugin._persona_profile_path("main").exists())
            self.assertEqual("旧日程", primary["daily_plan"]["marker"])
            self.assertEqual({}, secondary["users"])
            self.assertEqual({}, secondary["daily_plan"])
            self.assertEqual({}, secondary["bot_diaries"])

    async def test_reset_current_persona_backs_up_and_only_replaces_target_profile(self):
        with tempfile.TemporaryDirectory() as root:
            plugin = _plugin_harness(root)
            main_window = "default:FriendMessage:main-reset"
            alt_window = "default:FriendMessage:alt-keep"
            plugin.config["multi_persona_window_bindings"] = {
                main_window: "main",
                alt_window: "alt",
            }
            plugin._passive_state_session_cache = {
                main_window: {"state": "old-main"},
                alt_window: {"state": "keep-alt"},
            }
            plugin._persona_window_claims = {
                main_window: "main",
                alt_window: "alt",
            }
            main = plugin._ensure_persona_profile("main")
            alt = plugin._ensure_persona_profile("alt")
            main["users"] = {"用户甲": {"note": "旧人格记忆"}}
            main["daily_plan"] = {"marker": "旧人格日程"}
            main["persona_lifecycle"] = {"generation": 3}
            alt["users"] = {"用户乙": {"note": "必须保留"}}
            config_before = json.loads(json.dumps(plugin.config, ensure_ascii=False))

            result = await plugin._reset_current_persona_store(
                "main",
                rebuild_today=False,
            )

            self.assertTrue(result["ok"])
            self.assertEqual("main", result["persona_id"])
            self.assertEqual(4, result["generation"])
            self.assertEqual({}, plugin._ensure_persona_profile("main")["users"])
            self.assertEqual({}, plugin._ensure_persona_profile("main")["daily_plan"])
            self.assertEqual(
                {"用户乙": {"note": "必须保留"}},
                plugin._ensure_persona_profile("alt")["users"],
            )
            self.assertEqual(config_before, plugin.config)
            self.assertEqual(
                {"state": "old-main"},
                plugin._passive_state_session_cache[main_window],
            )
            self.assertEqual(
                {"state": "keep-alt"},
                plugin._passive_state_session_cache[alt_window],
            )
            self.assertEqual("main", plugin._persona_window_claims[main_window])
            self.assertEqual("alt", plugin._persona_window_claims[alt_window])

            backup_path = Path(result["backup_path"])
            self.assertTrue(backup_path.is_file())
            backup = json.loads(backup_path.read_text(encoding="utf-8"))
            self.assertEqual("main", backup["persona_id"])
            self.assertEqual("旧人格记忆", backup["data"]["users"]["用户甲"]["note"])
            stored = json.loads(Path(plugin.data_file).read_text(encoding="utf-8"))
            self.assertEqual(4, stored["persona_lifecycle"]["generation"])
            self.assertEqual({}, stored["users"])

    async def test_reset_current_persona_page_uses_selected_persona(self):
        with tempfile.TemporaryDirectory() as root:
            plugin = _plugin_harness(root)
            plugin._rebuild_today_after_reset = AsyncMock(
                return_value=({"date": "2026-08-07"}, {"date": "2026-08-07"}, None)
            )
            plugin._ensure_persona_profile("alt")["users"] = {
                "旧用户": {"note": "需要清除"}
            }
            api = PrivateCompanionPageApi(plugin)
            app = Quart(__name__)

            async with app.test_request_context(
                "/persona/reset-current",
                method="POST",
                json={"persona_id": "alt"},
            ):
                response = await api.reset_current_persona()

            self.assertTrue(response["success"])
            self.assertEqual("alt", response["data"]["persona_id"])
            self.assertEqual({}, plugin._ensure_persona_profile("alt")["users"])
            self.assertEqual("旧用户", plugin._ensure_persona_profile("main")["users"]["legacy"]["name"])
            plugin._rebuild_today_after_reset.assert_awaited_once()

    async def test_reset_current_persona_supports_single_persona_mode(self):
        with tempfile.TemporaryDirectory() as root:
            plugin = _plugin_harness(root)
            plugin.enable_multi_persona_mode = False
            plugin.data_dir = root
            plugin._data_default = {
                "users": {"旧用户": {"note": "单人格旧资料"}},
                "daily_plan": {"marker": "旧日程"},
                "persona_lifecycle": {"generation": 2},
            }
            stored: dict[str, object] = {}
            plugin._write_data_snapshot_sync = lambda snapshot: stored.update(snapshot)

            result = await plugin._reset_current_persona_store(rebuild_today=False)

            self.assertTrue(result["ok"])
            self.assertEqual("", result["persona_id"])
            self.assertEqual(3, result["generation"])
            self.assertEqual({}, plugin.data["users"])
            self.assertEqual({}, plugin.data["daily_plan"])
            self.assertEqual(3, stored["persona_lifecycle"]["generation"])
            backup = json.loads(Path(result["backup_path"]).read_text(encoding="utf-8"))
            self.assertEqual("单人格旧资料", backup["data"]["users"]["旧用户"]["note"])

    async def test_cached_astrbot_routes_keep_concurrent_profiles_isolated(self):
        with tempfile.TemporaryDirectory() as root:
            plugin = _plugin_harness(root)
            private_event = SimpleNamespace(
                unified_msg_origin="default:FriendMessage:10001",
                _private_companion_persona_route_decision={
                    "plugin_persona_id": "main"
                },
            )
            group_event = SimpleNamespace(
                unified_msg_origin="default:GroupMessage:20001",
                _private_companion_persona_route_decision={
                    "plugin_persona_id": "alt"
                },
            )

            private_result, group_result = await asyncio.gather(
                _record_event_persona(plugin, private_event, 0.02),
                _record_event_persona(plugin, group_event, 0.01),
            )

            self.assertEqual(("main", ["main"]), private_result)
            self.assertEqual(("alt", ["alt"]), group_result)
            self.assertEqual(["main"], plugin._data_default["event_trace"])
            self.assertNotIn("main", plugin._persona_data_profiles)
            self.assertEqual(["alt"], plugin._persona_data_profiles["alt"]["event_trace"])
            self.assertEqual("", plugin._active_persona_scope())

    async def test_legacy_binding_never_overrides_astrbot_or_auto_rebinds(self):
        with tempfile.TemporaryDirectory() as root:
            plugin = _plugin_harness(root)
            plugin._ensure_persona_profile("alt")
            explicit_umo = "default:FriendMessage:explicit"
            plugin.config["multi_persona_window_bindings"] = {explicit_umo: "main"}
            plugin._astrbot_effective_persona_for_event = AsyncMock(
                return_value={
                    "persona_id": "alt",
                    "source": "conversation",
                    "exists": True,
                    "explicit_none": False,
                    "umo": explicit_umo,
                    "error": "",
                }
            )

            token, persona_id = await plugin._activate_persona_for_event_context(
                SimpleNamespace(unified_msg_origin=explicit_umo)
            )
            self.assertEqual("alt", persona_id)
            plugin._deactivate_persona_for_event(token)
            self.assertEqual({explicit_umo: "main"}, plugin.config["multi_persona_window_bindings"])
            plugin._save_config_if_possible.assert_not_awaited()

    async def test_default_diary_profile_migration_is_independent_of_routing(self):
        with tempfile.TemporaryDirectory() as root:
            plugin = _plugin_harness(root)
            source = plugin._ensure_persona_profile("main")
            target = plugin._ensure_persona_profile("alt")
            target["runtime_cache"] = {"old": True}
            source["daily_diary_deleted_days"] = ["2026-08-02"]
            source["daily_diary_delete_revision"] = 7

            migrated = await plugin._migrate_persona_profile_async("main", "alt", [])

            self.assertTrue(migrated["ok"])
            self.assertIn("bot_diaries", migrated["keys"])
            self.assertEqual(source["bot_diaries"], target["bot_diaries"])
            self.assertEqual("2026-08-03", target["diary_generated_day"])
            self.assertEqual(["2026-08-02"], target["daily_diary_deleted_days"])
            self.assertEqual(7, target["daily_diary_delete_revision"])
            self.assertNotIn("runtime_cache", target)
            stored = plugin._load_secondary_persona_store_sync(
                "alt"
            ).manager.backend.load_store()
            self.assertIn("旧日记", json.dumps(stored, ensure_ascii=False))

            removed = plugin._switch_persona_for_window("alt", window_key="legacy")
            self.assertFalse(removed["ok"])
            self.assertEqual("plugin_persona_routing_removed", removed["code"])

    async def test_forced_window_rebind_is_retired_without_touching_caches(self):
        with tempfile.TemporaryDirectory() as root:
            plugin = _plugin_harness(root)
            source = plugin._ensure_persona_profile("main")
            target = plugin._ensure_persona_profile("alt")
            source["runtime_cache"] = {"source": True}
            target["pending_context"] = ["target"]
            plugin._default_persona_prompt_cache = "old"
            plugin._default_persona_prompt_cache_persona_id = "main"
            plugin._default_persona_prompt_cache_by_scope = {"main": "old"}
            window = "default:FriendMessage:forced-cache-clear"
            plugin.config["multi_persona_window_bindings"] = {window: "main"}
            plugin._passive_light_injection_cache = {
                "main": {"text": "MAIN_STATE"},
                "alt": {"text": "ALT_STATE"},
            }
            plugin._passive_state_session_cache = {
                window: {"fingerprint": "main-state"}
            }

            switched = await plugin._switch_persona_for_window_async(
                "alt",
                window_key=window,
                force=True,
            )

            self.assertFalse(switched["ok"])
            self.assertEqual("plugin_persona_routing_removed", switched["code"])
            self.assertIn("runtime_cache", source)
            self.assertIn("pending_context", target)
            self.assertIn(window, plugin._passive_state_session_cache)

    async def test_removed_window_switch_cannot_migrate_any_persona(self):
        with tempfile.TemporaryDirectory() as root:
            plugin = _plugin_harness(root)
            plugin.config["multi_persona_ids"] = ["main", "work", "alt"]
            window = "default:FriendMessage:stale-conflict"
            plugin.config["multi_persona_window_bindings"] = {window: "work"}
            main = plugin._ensure_persona_profile("main")
            work = plugin._ensure_persona_profile("work")
            target = plugin._ensure_persona_profile("alt")
            main["users"] = {"main-user": {"name": "主人格用户"}}
            work["runtime_cache"] = {"owner": "work"}
            target["users"] = {"alt-user": {"name": "次人格用户"}}

            result = plugin._switch_persona_for_window(
                "alt",
                window_key=window,
                source_persona_id="main",
                migrate_keys=["users"],
                force=True,
            )

            self.assertFalse(result["ok"])
            self.assertEqual("plugin_persona_routing_removed", result["code"])
            self.assertEqual({"alt-user": {"name": "次人格用户"}}, target["users"])
            self.assertIn("runtime_cache", work)
            self.assertEqual({}, plugin._persona_window_bindings())

    async def test_lightweight_passive_state_cache_is_scoped_by_persona(self):
        with tempfile.TemporaryDirectory() as root:
            plugin = _plugin_harness(root)
            plugin._format_lightweight_state_injection = (
                lambda state, *, include_heading=True: state["text"]
            )

            token = plugin._activate_persona_id("main")
            try:
                main_text = plugin._prepared_lightweight_state_injection(
                    {"text": "MAIN_STATE"}
                )
            finally:
                plugin._deactivate_persona_for_event(token)

            token = plugin._activate_persona_id("alt")
            try:
                alt_text = plugin._prepared_lightweight_state_injection(
                    {"text": "ALT_STATE"}
                )
            finally:
                plugin._deactivate_persona_for_event(token)

            self.assertEqual("MAIN_STATE", main_text)
            self.assertEqual("ALT_STATE", alt_text)
            self.assertEqual("MAIN_STATE", plugin._passive_light_injection_cache["main"]["text"])
            self.assertEqual("ALT_STATE", plugin._passive_light_injection_cache["alt"]["text"])

    async def test_page_persona_selection_does_not_write_window_or_profile_data(self):
        with tempfile.TemporaryDirectory() as root:
            plugin = _plugin_harness(root)
            window = "default:FriendMessage:persistence-rollback"
            plugin.config["multi_persona_window_bindings"] = {window: "main"}
            plugin._persona_window_claims[window] = "main"
            plugin._passive_state_session_cache[window] = {
                "fingerprint": "main-state"
            }
            source = plugin._ensure_persona_profile("main")
            target = plugin._ensure_persona_profile("alt")
            source["users"] = {"main-user": {"name": "主人格用户"}}
            source["runtime_cache"] = {"owner": "main"}
            target["users"] = {"alt-user": {"name": "次人格用户"}}
            target["recent_context"] = ["alt-context"]
            plugin._save_config_if_possible = AsyncMock(side_effect=[False, True])
            api = PrivateCompanionPageApi(plugin)

            self.assertFalse(hasattr(api, "switch_persona"))
            self.assertEqual({}, plugin._persona_window_bindings())
            self.assertEqual("main", plugin._persona_window_claims[window])
            self.assertEqual("main", plugin._page_current_persona_id)
            self.assertEqual(
                {"fingerprint": "main-state"},
                plugin._passive_state_session_cache[window],
            )
            self.assertEqual({"alt-user": {"name": "次人格用户"}}, target["users"])
            self.assertIn("runtime_cache", source)
            self.assertIn("recent_context", target)
            plugin._save_config_if_possible.assert_not_awaited()

    async def test_page_persona_selection_is_local_and_uses_astrbot_authority(self):
        with tempfile.TemporaryDirectory() as root:
            plugin = _plugin_harness(root)
            plugin._ensure_persona_profile("alt")
            api = PrivateCompanionPageApi(plugin)
            paths = {path for path, _handler, _methods, _description in api.route_bindings()}

            self.assertFalse(hasattr(api, "switch_persona"))
            self.assertNotIn("/persona/switch", paths)
            self.assertEqual({}, plugin._persona_window_bindings())
            plugin._save_config_if_possible.assert_not_awaited()

    async def test_legacy_window_binding_file_is_backed_up_but_not_loaded(self):
        with tempfile.TemporaryDirectory() as root:
            plugin = _plugin_harness(root)
            window = "default:FriendMessage:reload-persistence"

            source = Path(root) / "persona_window_bindings.json"
            source.write_text(json.dumps({window: "alt"}), encoding="utf-8")
            status = plugin._retire_legacy_persona_routing_sync()
            self.assertTrue(status["ignored"])
            self.assertTrue(Path(status["backup_path"]).is_file())

            reloaded = _plugin_harness(root)
            reloaded._retire_legacy_persona_routing_sync()
            self.assertEqual({}, reloaded._persona_window_bindings())

    async def test_existing_config_binding_is_preserved_only_in_retired_backup(self):
        with tempfile.TemporaryDirectory() as root:
            plugin = _plugin_harness(root)
            window = "default:GroupMessage:legacy-config-binding"
            plugin.config["multi_persona_window_bindings"] = {window: "main"}

            status = plugin._retire_legacy_persona_routing_sync()
            backup = json.loads(Path(status["backup_path"]).read_text(encoding="utf-8"))
            self.assertEqual("main", backup["config_bindings"][window])
            self.assertEqual({}, plugin._persona_window_bindings())

    async def test_page_route_reads_selected_persona_users_and_schedule(self):
        with tempfile.TemporaryDirectory() as root:
            plugin = _plugin_harness(root)
            plugin._ensure_persona_profile("main")
            alt = plugin._ensure_persona_profile("alt")
            alt["users"] = {"alt-user": {"name": "次人格用户"}}
            alt["daily_plan"] = {"marker": "次人格日程"}
            api = PrivateCompanionPageApi(plugin)
            app = Quart(__name__)

            async def read_profile():
                return {
                    "persona": plugin._active_persona_scope(),
                    "users": list(plugin.data["users"]),
                    "schedule": plugin.data["daily_plan"]["marker"],
                }

            handler = api._persona_scoped_route_handler(read_profile)
            async with app.test_request_context("/?_persona_id=main"):
                main_payload = await handler()
            async with app.test_request_context("/?_persona_id=alt"):
                alt_payload = await handler()

            self.assertEqual(
                {"persona": "main", "users": ["legacy"], "schedule": "旧日程"},
                main_payload,
            )
            self.assertEqual(
                {
                    "persona": "alt",
                    "users": ["alt-user"],
                    "schedule": "次人格日程",
                },
                alt_payload,
            )

    async def test_page_route_rejects_topology_only_persona_without_creating_profile(self):
        with tempfile.TemporaryDirectory() as root:
            plugin = _plugin_harness(root)
            plugin._persona_data_profiles.pop("alt", None)
            alt_path = plugin._persona_profile_path("alt")
            alt_path.unlink(missing_ok=True)
            api = PrivateCompanionPageApi(plugin)
            app = Quart(__name__)

            async def read_scope():
                return plugin._active_persona_scope()

            handler = api._persona_scoped_route_handler(read_scope)
            async with app.test_request_context("/?_persona_id=alt"):
                active = await handler()

            self.assertEqual("main", active)
            self.assertFalse(alt_path.exists())
            self.assertEqual("", plugin._active_persona_scope())

    async def test_scheduler_runs_each_persona_and_uses_effective_proactive_persona(self):
        with tempfile.TemporaryDirectory() as root:
            plugin = _plugin_harness(root)
            seen: list[str] = []

            async def tick():
                seen.append(plugin._active_persona_scope())

            plugin._tick = tick
            plugin._scheduler_maintenance_tasks = lambda: ()
            await plugin._run_scheduler_cycle()

            self.assertEqual(["main", "alt"], seen)
            token = plugin._activate_persona_id("alt")
            try:
                original = SimpleNamespace(persona_id="main", marker="original")
                scoped = plugin._proactive_conversation_with_configured_persona(original)
            finally:
                plugin._deactivate_persona_for_event(token)
            self.assertEqual("alt", scoped.persona_id)
            self.assertEqual("main", original.persona_id)
            self.assertIsNot(scoped, original)

    async def test_disabled_mode_keeps_single_profile_behavior(self):
        with tempfile.TemporaryDirectory() as root:
            plugin = _plugin_harness(root)
            plugin.enable_multi_persona_mode = False
            plugin.plugin_specific_persona_id = "single-persona"

            self.assertIs(plugin.data, plugin._data_default)
            self.assertIsNone(plugin._activate_persona_id("alt"))
            self.assertEqual("single-persona", plugin._effective_plugin_persona_id())
            self.assertEqual([""], plugin._scheduler_persona_ids())

    async def test_string_persona_config_splits_whitespace_and_punctuation(self):
        with tempfile.TemporaryDirectory() as root:
            plugin = _plugin_harness(root)
            plugin.config["multi_persona_ids"] = "main sister,alt，work、night"

            self.assertEqual(
                ["main", "sister", "alt", "work", "night"],
                plugin._configured_multi_persona_ids(),
            )

    async def test_page_persona_normalizer_accepts_astrbot_persona_objects(self):
        page = PrivateCompanionPageApi.__new__(PrivateCompanionPageApi)
        persona = SimpleNamespace(
            persona_id="secondary",
            system_prompt="次人格提示词",
        )
        entries = page._iter_persona_entries([persona])
        self.assertEqual("secondary", entries[0]["id"])
        self.assertEqual("次人格提示词", entries[0]["system_prompt"])
        self.assertEqual("次人格提示词", page._persona_prompt_text(persona))

        class DumpablePersona:
            def model_dump(self):
                return {"id": 77, "persona_id": "dumped", "system_prompt": "导出提示词"}

        dumped = page._iter_persona_entries([DumpablePersona()])
        self.assertEqual("dumped", dumped[0]["persona_id"])

        class KeyedPersona:
            system_prompt = "映射提示词"

        keyed = page._iter_persona_entries({"keyed": KeyedPersona()})
        self.assertEqual("keyed", keyed[0]["id"])
        self.assertEqual("映射提示词", keyed[0]["system_prompt"])

        class BrokenPersona:
            def model_dump(self):
                raise RuntimeError("单条人格导出失败")

        resilient = page._iter_persona_entries([BrokenPersona(), persona])
        self.assertEqual(["secondary"], [item["id"] for item in resilient])

        single = page._iter_persona_entries(
            {"id": 88, "persona_id": "single", "system_prompt": "单条提示词"}
        )
        self.assertEqual(1, len(single))
        self.assertEqual("single", single[0]["persona_id"])

    async def test_roleplay_persona_list_prefers_logical_persona_id(self):
        class AstrBotPersona:
            persona_id = "secondary"
            system_prompt = "次人格提示词"

            def model_dump(self):
                return {
                    "id": 42,
                    "persona_id": self.persona_id,
                    "system_prompt": self.system_prompt,
                }

        class PersonaManager:
            async def get_all_personas(self):
                return [AstrBotPersona()]

        plugin = SimpleNamespace(
            context=SimpleNamespace(persona_manager=PersonaManager()),
            enable_multi_persona_mode=True,
            plugin_specific_persona_id="secondary",
            _primary_persona_id=lambda: "secondary",
            _page_current_persona_id="secondary",
            _persona_profile_ids=lambda: ["secondary"],
            _multi_persona_status=lambda: {
                "enabled": True,
                "primary": "secondary",
                "profiles": ["secondary"],
                "window_bindings": {},
            },
        )
        page = PrivateCompanionPageApi.__new__(PrivateCompanionPageApi)
        page.plugin = plugin
        page._astrbot_config_candidate_paths = lambda: []

        response = await page.list_roleplay_personas()
        items = response["data"]["items"]
        ids = [item["id"] for item in items]

        self.assertIn("secondary", ids)
        self.assertNotIn("42", ids)
        selected = next(item for item in items if item["id"] == "secondary")
        self.assertEqual("secondary（主人格）", selected["label"])
        self.assertEqual("运行态人格", selected["source"])

    async def test_single_mode_persona_label_remains_plugin_selected(self):
        plugin = SimpleNamespace(
            context=SimpleNamespace(persona_manager=None),
            enable_multi_persona_mode=False,
            plugin_specific_persona_id="single",
        )
        page = PrivateCompanionPageApi.__new__(PrivateCompanionPageApi)
        page.plugin = plugin
        page._astrbot_config_candidate_paths = lambda: []

        items = await page._roleplay_persona_items()

        selected = next(item for item in items if item["id"] == "single")
        self.assertEqual("single（插件当前指定）", selected["label"])

    async def test_overview_primary_setting_does_not_follow_selected_persona(self):
        plugin = SimpleNamespace(
            enable_multi_persona_mode=True,
            plugin_specific_persona_id="main",
            _primary_persona_id=lambda: "main",
            persona_setting=lambda key, default=None: "secondary"
            if key == "plugin_specific_persona_id" else default,
        )
        page = PrivateCompanionPageApi.__new__(PrivateCompanionPageApi)
        page.plugin = plugin
        page._schema_setting_keys = lambda public_only=True: set()
        page._schema_provider_keys = lambda public_only=True: set()
        page._schema_bool_keys = lambda: set()
        page._config_get = lambda _key, default=None: default

        settings = page._runtime_settings()

        self.assertEqual("main", settings["plugin_specific_persona_id"])

    async def test_overview_primary_setting_does_not_follow_selected_persona(self):
        plugin = SimpleNamespace(
            enable_multi_persona_mode=True,
            plugin_specific_persona_id="main",
            _primary_persona_id=lambda: "main",
            persona_setting=lambda key, default=None: "secondary"
            if key == "plugin_specific_persona_id" else default,
        )
        page = PrivateCompanionPageApi.__new__(PrivateCompanionPageApi)
        page.plugin = plugin
        page._schema_setting_keys = lambda public_only=True: set()
        page._schema_provider_keys = lambda public_only=True: set()
        page._schema_bool_keys = lambda: set()
        page._config_get = lambda _key, default=None: default

        settings = page._runtime_settings()

        self.assertEqual("main", settings["plugin_specific_persona_id"])

    async def test_lowercase_provider_runtime_alias_reads_canonical_profile_key(self):
        with tempfile.TemporaryDirectory() as root:
            plugin = _plugin_harness(root)
            plugin.daily_plan_provider_id = "primary-plan"
            plugin._persona_data_profiles["alt"]["persona_settings"]["DAILY_PLAN_PROVIDER_ID"] = "alt-plan"
            token = plugin._activate_persona_id("alt")
            try:
                self.assertEqual("alt-plan", plugin.persona_setting("daily_plan_provider_id", "fallback"))
                plugin._persona_data_profiles["alt"]["persona_settings"].pop("DAILY_PLAN_PROVIDER_ID")
                self.assertEqual("primary-plan", plugin.persona_setting("daily_plan_provider_id", "fallback"))
            finally:
                plugin._deactivate_persona_for_event(token)

    async def test_empty_secondary_persona_can_generate_daily_plan(self):
        with tempfile.TemporaryDirectory() as root:
            plugin = _plugin_harness(root)
            plugin.enable_daily_plan = True
            plugin.daily_plan_time = "00:00"
            plugin._ensure_daily_state = AsyncMock()
            plugin._is_daily_plan_due = lambda: True
            plugin._generate_daily_plan = AsyncMock(
                return_value={"date": "2099-01-01", "source": "test", "items": []}
            )
            plugin._ensure_daily_news_reading = AsyncMock()
            plugin._save_data_sync = lambda **_kwargs: None
            token = plugin._activate_persona_id("alt")
            try:
                plan = await plugin._ensure_daily_plan(force=False)
                self.assertEqual("test", plan["source"])
                plugin._generate_daily_plan.assert_awaited_once()
            finally:
                plugin._deactivate_persona_for_event(token)

    async def test_unicode_persona_ids_keep_their_full_logical_identity(self):
        with tempfile.TemporaryDirectory() as root:
            plugin = _plugin_harness(root)

            self.assertEqual("星缘-私聊", plugin._sanitize_persona_id("星缘-私聊"))
            self.assertEqual(
                "中文 Persona V2",
                plugin._sanitize_persona_id("中文 Persona V2"),
            )
            self.assertEqual("姐姐人格", plugin._sanitize_persona_id("姐\n姐人格"))
            joined_name = "星缘\u200dAI"
            self.assertEqual(joined_name, plugin._sanitize_persona_id(joined_name))
            self.assertEqual(
                joined_name,
                plugin._persona_id_from_profile_path(
                    plugin._persona_profile_path(joined_name)
                ),
            )

    async def test_unicode_persona_profile_round_trips_and_is_enumerated(self):
        with tempfile.TemporaryDirectory() as root:
            plugin = _plugin_harness(root)
            persona_id = "星缘-私聊"
            plugin.multi_persona_ids = ["main", persona_id, "alt"]
            plugin.config["multi_persona_ids"] = ["main", persona_id, "alt"]

            profile = plugin._ensure_persona_profile(persona_id)
            profile["unicode_marker"] = "中文资料"
            plugin._save_persona_profile_sync(persona_id)

            path = Path(root) / "persona_profiles" / "星缘-私聊.db"
            self.assertTrue(path.is_file())
            plugin._persona_data_profiles.clear()
            self.assertEqual(
                "中文资料",
                plugin._ensure_persona_profile(persona_id)["unicode_marker"],
            )
            self.assertIn(persona_id, plugin._persona_profile_ids())

            window = "default:FriendMessage:unicode-persona"
            switched = plugin._switch_persona_for_window(
                persona_id,
                window_key=window,
            )
            self.assertFalse(switched["ok"])
            self.assertEqual("plugin_persona_routing_removed", switched["code"])

    async def test_profile_filename_encoding_is_safe_and_reversible(self):
        with tempfile.TemporaryDirectory() as root:
            plugin = _plugin_harness(root)
            persona_id = "../姐姐:主/人格%?"
            plugin.config["multi_persona_ids"] = ["main", persona_id]

            path = plugin._persona_profile_path(persona_id)
            database_path = plugin._persona_profile_db_path(persona_id)
            self.assertEqual(
                (Path(root) / "persona_profiles").resolve(),
                path.parent.resolve(),
            )
            for candidate in (path, database_path):
                self.assertNotIn("/", candidate.name)
                self.assertNotIn(":", candidate.name)
                self.assertNotIn("?", candidate.name)
            plugin._ensure_persona_profile(persona_id)["safe_marker"] = True
            plugin._save_persona_profile_sync(persona_id)

            plugin._persona_data_profiles.clear()
            self.assertTrue(plugin._ensure_persona_profile(persona_id)["safe_marker"])
            self.assertIn(persona_id, plugin._persona_profile_ids())

    async def test_legacy_ascii_profile_filenames_remain_compatible(self):
        with tempfile.TemporaryDirectory() as root:
            plugin = _plugin_harness(root)

            self.assertEqual("main.json", plugin._persona_profile_path("main").name)
            self.assertEqual("alt.json", plugin._persona_profile_path("alt").name)
            self.assertEqual("alt.db", plugin._persona_profile_db_path("alt").name)
            self.assertNotEqual("CON.json", plugin._persona_profile_path("CON").name)
            self.assertEqual(
                "CON",
                plugin._persona_id_from_profile_path(plugin._persona_profile_path("CON")),
            )

    async def test_removed_profile_is_recoverable_but_not_scheduled_or_bound(self):
        with tempfile.TemporaryDirectory() as root:
            plugin = _plugin_harness(root)
            plugin._ensure_persona_profile("alt")["users"] = {
                "old": {"umo": "default:FriendMessage:old"}
            }
            plugin._save_persona_profile_sync("alt")
            plugin.config["multi_persona_ids"] = ["main"]
            stale_window = "default:FriendMessage:stale"
            plugin.config["multi_persona_window_bindings"] = {stale_window: "alt"}

            self.assertIn("alt", plugin._persona_profile_ids())
            self.assertEqual(["main"], plugin._scheduler_persona_ids())
            self.assertIsNone(plugin._activate_persona_id("alt"))
            recovery_token = plugin._activate_persona_id("alt", allow_inactive=True)
            try:
                self.assertEqual(["main"], plugin._scheduler_persona_ids())
                self.assertIn("old", plugin.data["users"])
            finally:
                plugin._deactivate_persona_for_event(recovery_token)
            self.assertEqual(
                "main",
                plugin._persona_id_for_event(
                    SimpleNamespace(unified_msg_origin=stale_window)
                )[0],
            )
            event = SimpleNamespace(unified_msg_origin=stale_window)
            token, activated = await plugin._activate_persona_for_event_context(event)
            try:
                self.assertEqual("main", activated)
                self.assertEqual("main", event.private_companion_persona_id)
            finally:
                plugin._deactivate_persona_for_event(token)

            seen: list[str] = []

            async def record_state():
                seen.append(plugin._active_persona_scope())

            plugin._ensure_daily_state = record_state
            plugin._ensure_daily_plan = AsyncMock()
            plugin._ensure_daily_diary = AsyncMock()
            plugin._maybe_settle_skill_growth = AsyncMock()
            await plugin._startup_prepare_today()
            self.assertEqual(["main"], seen)

    async def test_startup_failure_in_one_persona_does_not_skip_the_next(self):
        with tempfile.TemporaryDirectory() as root:
            plugin = _plugin_harness(root)
            seen: list[str] = []

            async def ensure_state():
                persona_id = plugin._active_persona_scope()
                seen.append(persona_id)
                if persona_id == "main":
                    raise RuntimeError("main startup failed")

            plugin._ensure_daily_state = ensure_state
            plugin._ensure_daily_plan = AsyncMock()
            plugin._ensure_daily_diary = AsyncMock()
            plugin._maybe_settle_skill_growth = AsyncMock()

            await plugin._startup_prepare_today()

            self.assertEqual(["main", "alt"], seen)
            plugin._ensure_daily_plan.assert_awaited_once()
            plugin._ensure_daily_diary.assert_awaited_once()
            plugin._maybe_settle_skill_growth.assert_awaited_once()

    async def test_topology_only_persona_is_not_started_or_materialized(self):
        with tempfile.TemporaryDirectory() as root:
            plugin = _plugin_harness(root)
            plugin._persona_data_profiles.pop("alt", None)
            alt_path = plugin._persona_profile_path("alt")
            alt_path.unlink(missing_ok=True)
            seen: list[str] = []

            async def ensure_state():
                seen.append(plugin._active_persona_scope())

            plugin._ensure_daily_state = ensure_state
            plugin._ensure_daily_plan = AsyncMock()
            plugin._ensure_daily_diary = AsyncMock()
            plugin._maybe_settle_skill_growth = AsyncMock()

            await plugin._startup_prepare_today()

            self.assertEqual(["main"], seen)
            self.assertEqual(["main"], plugin._scheduler_persona_ids())
            self.assertFalse(alt_path.exists())

    async def test_delayed_saves_persist_each_persona_independently(self):
        with tempfile.TemporaryDirectory() as root:
            plugin = _plugin_harness(root)

            main_token = plugin._activate_persona_id("main")
            try:
                plugin.data["users"]["save_marker"] = "main"
                plugin._save_data_sync(sections={"users"})
            finally:
                plugin._deactivate_persona_for_event(main_token)

            alt_token = plugin._activate_persona_id("alt")
            try:
                plugin.data["users"]["save_marker"] = "alt"
                plugin._save_data_sync(sections={"users"})
            finally:
                plugin._deactivate_persona_for_event(alt_token)

            await plugin._flush_scheduled_data_save()

            main = Path(plugin.data_file).read_text(encoding="utf-8")
            alt = plugin._load_secondary_persona_store_sync(
                "alt"
            ).manager.backend.load_store()
            self.assertIn('"save_marker": "main"', main)
            self.assertEqual("alt", alt["users"]["save_marker"])
            self.assertFalse(plugin._persona_profile_path("main").exists())
            self.assertFalse(plugin._persona_data_save_dirty)
            self.assertEqual({}, plugin._persona_data_save_tasks)

    async def test_primary_json_tail_is_twelve_but_secondary_sqlite_keeps_full_history(self):
        with tempfile.TemporaryDirectory() as root:
            plugin = _plugin_harness(root)

            def group_history():
                return {
                    "recent_messages": [
                        {"sender_id": "user", "text": f"member-{index}"}
                        for index in range(15)
                    ],
                    "recent_bot_replies": [
                        {"reply_to_id": "user", "text": f"bot-{index}"}
                        for index in range(15)
                    ],
                }

            plugin._data_default["groups"] = {"group-a": group_history()}
            plugin._write_data_snapshot_sync(deepcopy(plugin._data_default))
            primary = json.loads(Path(plugin.data_file).read_text(encoding="utf-8"))

            alt = plugin._ensure_persona_profile("alt")
            alt["groups"] = {"group-a": group_history()}
            plugin._save_persona_profile_sync("alt")
            secondary = plugin._load_secondary_persona_store_sync(
                "alt"
            ).manager.backend.load_store()

            self.assertEqual(12, len(primary["groups"]["group-a"]["recent_messages"]))
            self.assertEqual(12, len(primary["groups"]["group-a"]["recent_bot_replies"]))
            self.assertEqual(15, len(secondary["groups"]["group-a"]["recent_messages"]))
            self.assertEqual(15, len(secondary["groups"]["group-a"]["recent_bot_replies"]))

    async def test_terminate_queues_final_snapshot_after_timed_out_persona_writer(self):
        with tempfile.TemporaryDirectory() as root:
            plugin = _plugin_harness(root)
            original_writer = plugin._write_data_snapshot_sync
            writer_started = threading.Event()
            writer_release = threading.Event()
            first_write = True

            def blocking_writer(snapshot):
                nonlocal first_write
                if first_write:
                    first_write = False
                    writer_started.set()
                    writer_release.wait(timeout=3)
                return original_writer(snapshot)

            plugin._write_data_snapshot_sync = blocking_writer
            token = plugin._activate_persona_id("main")
            try:
                plugin.data["users"]["final_marker"] = "old"
                plugin._schedule_data_save(sections={"users"}, delay=0.0)
            finally:
                plugin._deactivate_persona_for_event(token)
            started = await asyncio.to_thread(writer_started.wait, 1.0)
            self.assertTrue(started)

            token = plugin._activate_persona_id("main")
            try:
                plugin.data["users"]["final_marker"] = "latest"
            finally:
                plugin._deactivate_persona_for_event(token)
            plugin._stop_event.set()
            plugin._termination_flush_already_attempted = True
            final_save = asyncio.create_task(plugin._save_data_on_terminate())
            await asyncio.sleep(0.05)
            self.assertFalse(final_save.done())
            writer_release.set()
            await asyncio.wait_for(final_save, timeout=2.0)

            stored = Path(plugin.data_file).read_text(encoding="utf-8")
            self.assertIn('"final_marker": "latest"', stored)
            self.assertFalse(plugin._persona_profile_path("main").exists())

    async def test_terminate_saves_all_loaded_persona_profiles(self):
        with tempfile.TemporaryDirectory() as root:
            plugin = _plugin_harness(root)
            plugin._ensure_persona_profile("main")["final_marker"] = "main"
            plugin._ensure_persona_profile("alt")["final_marker"] = "alt"
            await plugin._save_data_on_terminate()

            main_stored = Path(plugin.data_file).read_text(encoding="utf-8")
            alt_stored = plugin._load_secondary_persona_store_sync(
                "alt"
            ).manager.backend.load_store()
            self.assertIn('"final_marker": "main"', main_stored)
            self.assertEqual("alt", alt_stored["final_marker"])
            self.assertFalse(plugin._persona_profile_path("main").exists())

    async def test_sqlite_terminate_preserves_bookshelf_tombstone_on_restart(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            data_file = root_path / "companion.json"
            sqlite_path = root_path / "companion.sqlite3"

            def new_store() -> dict:
                return {
                    "users": {},
                    "bookshelf_items": [],
                    "bookshelf_secret": {},
                    "bookshelf_store_revision": 0,
                    "jm_cosmos_integration": {},
                }

            def ensure_defaults(data: dict) -> dict:
                result = dict(data)
                for section, value in new_store().items():
                    result.setdefault(section, value)
                return result

            manager = StoreManager(
                backend_name="sqlite",
                data_file=data_file,
                sqlite_path=sqlite_path,
                ensure_defaults=ensure_defaults,
                new_store=new_store,
            )
            stale = new_store()
            stale["bookshelf_items"] = [
                {
                    "key": "jm_album:deleted",
                    "type": "jm_album",
                    "album_id": "deleted",
                }
            ]
            stale["bookshelf_store_revision"] = 1
            manager.save_store(stale)
            data_file.write_text(json.dumps(stale, ensure_ascii=False), encoding="utf-8")
            manager.save_sections({}, {"bookshelf_items": 2})

            plugin = _plugin_harness(root)
            plugin.enable_multi_persona_mode = False
            plugin.storage_backend = "sqlite"
            plugin.store_manager = manager
            plugin._data_default = manager.backend.load_store()
            plugin._data_save_task = None
            plugin._data_save_dirty = {}
            plugin._data_save_deleted = {}
            plugin._data_save_dirty_since = {}
            plugin._data_save_section_revisions = {}
            plugin._data_save_full_revision = 0
            plugin._data_save_revision = manager.next_revision() - 1
            plugin._termination_flush_already_attempted = True
            plugin._stop_event.set()

            await plugin._save_data_on_terminate()

            self.assertEqual(
                {"bookshelf_items": 2},
                manager.backend.deleted_section_revisions(["bookshelf_items"]),
            )
            restarted = StoreManager(
                backend_name="sqlite",
                data_file=data_file,
                sqlite_path=sqlite_path,
                ensure_defaults=ensure_defaults,
                new_store=new_store,
            )
            self.assertNotIn("bookshelf_items", restarted.load_initial_store())

    async def test_force_state_results_do_not_cross_persona_scopes(self):
        with tempfile.TemporaryDirectory() as root:
            plugin = _plugin_harness(root)
            plugin._daily_state_generation_lock = asyncio.Lock()
            started = asyncio.Event()
            release = asyncio.Event()
            calls: list[str] = []

            async def generate_state(**_kwargs):
                persona_id = plugin._active_persona_scope()
                calls.append(persona_id)
                if persona_id == "main":
                    started.set()
                    await release.wait()
                return {"persona_id": persona_id}

            plugin._ensure_daily_state_once = generate_state
            main_token = plugin._activate_persona_id("main")
            try:
                main_task = asyncio.create_task(plugin._ensure_daily_state(force=True))
            finally:
                plugin._deactivate_persona_for_event(main_token)
            await started.wait()
            alt_token = plugin._activate_persona_id("alt")
            try:
                alt_task = asyncio.create_task(plugin._ensure_daily_state(force=True))
            finally:
                plugin._deactivate_persona_for_event(alt_token)
            alt_result = await asyncio.wait_for(alt_task, timeout=0.5)
            self.assertFalse(main_task.done())
            release.set()
            main_result = await asyncio.wait_for(main_task, timeout=0.5)
            self.assertEqual(["main", "alt"], calls)
            self.assertEqual("main", main_result["persona_id"])
            self.assertEqual("alt", alt_result["persona_id"])

    async def test_force_diary_results_do_not_cross_persona_scopes(self):
        with tempfile.TemporaryDirectory() as root:
            plugin = _plugin_harness(root)
            plugin._daily_diary_generation_lock = asyncio.Lock()
            started = asyncio.Event()
            release = asyncio.Event()
            calls: list[str] = []

            async def generate_diary(*, force=False):
                del force
                persona_id = plugin._active_persona_scope()
                calls.append(persona_id)
                if persona_id == "main":
                    started.set()
                    await release.wait()
                return {"persona_id": persona_id}

            plugin._ensure_daily_diary_once = generate_diary
            main_token = plugin._activate_persona_id("main")
            try:
                main_task = asyncio.create_task(plugin._ensure_daily_diary(force=True))
            finally:
                plugin._deactivate_persona_for_event(main_token)
            await started.wait()
            alt_token = plugin._activate_persona_id("alt")
            try:
                alt_task = asyncio.create_task(plugin._ensure_daily_diary(force=True))
            finally:
                plugin._deactivate_persona_for_event(alt_token)
            alt_result = await asyncio.wait_for(alt_task, timeout=0.5)
            self.assertFalse(main_task.done())
            release.set()
            main_result = await asyncio.wait_for(main_task, timeout=0.5)
            self.assertEqual(["main", "alt"], calls)
            self.assertEqual("main", main_result["persona_id"])
            self.assertEqual("alt", alt_result["persona_id"])

    async def test_all_astrbot_filter_handlers_bind_persona_context(self):
        source_path = Path(__file__).resolve().parents[1] / "main.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        filter_handlers: list[tuple[str, list[str]]] = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            decorators = [ast.unparse(item) for item in node.decorator_list]
            if any(item.startswith("filter.") for item in decorators):
                filter_handlers.append((node.name, decorators))

        self.assertGreaterEqual(len(filter_handlers), 70)
        missing = [
            name
            for name, decorators in filter_handlers
            if "_multi_persona_event_context" not in decorators
        ]
        self.assertEqual([], missing)


if __name__ == "__main__":
    unittest.main()
