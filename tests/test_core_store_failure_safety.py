# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from astrbot_plugin_private_companion.core_store import CoreStoreMixin
from astrbot_plugin_private_companion.unified_profile_service import default_capabilities


class _AsyncConfig:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.awaited = False

    async def save_config(self) -> None:
        await asyncio.sleep(0)
        self.awaited = True
        if self.fail:
            raise OSError("配置目录不可写")


class _CoreHarness(CoreStoreMixin):
    def __init__(self) -> None:
        self.config = _AsyncConfig()
        self.store_manager = None
        self._data_save_task = None
        self._data_save_dirty = False
        self._stop_event = asyncio.Event()

    @staticmethod
    def _new_store() -> dict:
        return {"users": {}}

    def _configured_target_ids(self) -> list[str]:
        return ["owner"]

    def _is_bot_self_user_id(self, _user_id: str) -> bool:
        return False


class _StartupHarness(CoreStoreMixin):
    def __init__(self) -> None:
        self.data = {"bot_diaries": []}
        self.diary_calls: list[dict[str, object]] = []

    async def _ensure_daily_state(self) -> None:
        return None

    async def _ensure_daily_plan(self) -> None:
        return None

    async def _ensure_daily_diary(self, **kwargs) -> None:
        self.diary_calls.append(dict(kwargs))

    async def _maybe_settle_skill_growth(self) -> None:
        return None


class _IdentityCoreHarness(CoreStoreMixin):
    def __init__(self) -> None:
        self.data = {"users": {}}
        self.private_user_aliases: dict[str, str] = {}
        self.default_nickname = "你"
        self.default_style = "温柔"
        self.default_nickname_strategy = "platform_display_name"
        self.enable_auto_user_profile_creation = True

    @staticmethod
    def _ensure_private_user_role(_user_id: str, _user: dict) -> bool:
        return False

    @staticmethod
    def _ensure_relationship_user_state(_user: dict, *, created: bool = False) -> bool:
        return False

    @staticmethod
    def _configured_target_ids() -> list[str]:
        return []

    @staticmethod
    def _is_target_private_user(_user_id: str, _user: dict | None = None) -> bool:
        return False

    @staticmethod
    def _is_bot_self_user_id(_user_id: str) -> bool:
        return False

    @staticmethod
    def _platform_kind_for_event(_event: object) -> str:
        return "qq_official"

    @staticmethod
    def _note_private_user_umo(_user_id: str, user: dict, umo: str) -> None:
        user["last_inbound_umo"] = umo

    @staticmethod
    def _schedule_data_save(*_args, **_kwargs) -> None:
        return None


class CoreStoreFailureSafetyTests(unittest.IsolatedAsyncioTestCase):
    def test_photo_scope_empty_list_denies_every_scope_but_missing_config_keeps_legacy_default(self) -> None:
        harness = _CoreHarness()
        harness.photo_generation_allowed_scopes = []
        for scope in ("private_owner", "private_friend", "group", "proactive"):
            with self.subTest(scope=scope):
                harness._photo_generation_scope = lambda *args, _scope=scope, **kwargs: _scope
                self.assertFalse(harness._photo_generation_scope_allowed())

        legacy_harness = _CoreHarness()
        self.assertTrue(legacy_harness._photo_generation_scope_allowed(proactive=True))

        serialized_harness = _CoreHarness()
        serialized_harness.photo_generation_allowed_scopes = "private_owner\nprivate_friend"
        serialized_harness._photo_generation_scope = lambda *args, **kwargs: "private_friend"
        self.assertTrue(serialized_harness._photo_generation_scope_allowed())

    def test_passive_private_profile_ignores_legacy_enabled_and_target_flags(self) -> None:
        harness = _CoreHarness()

        self.assertTrue(
            harness._private_passive_profile_available(
                "legacy",
                {"enabled": False, "manual_disabled": True},
            )
        )
        self.assertFalse(harness._private_passive_profile_available("", {}))

    def test_proactive_capability_keeps_legacy_target_selection_eligible(self) -> None:
        harness = _CoreHarness()
        capabilities = default_capabilities()
        capabilities["proactive_private_enabled"] = True

        self.assertTrue(
            harness._is_target_private_user(
                "legacy",
                {"unified_profile_capabilities": capabilities},
            )
        )

    def test_existing_legacy_private_profile_keeps_permission_before_defaults_fill(self) -> None:
        harness = _IdentityCoreHarness()
        harness.data["users"]["legacy"] = {
            "user_id": "legacy",
            "enabled": True,
            "relationship_role": "friend",
            "proactive_daily_limit": 2,
        }

        user = harness._get_user("legacy")

        capabilities = user["unified_profile_capabilities"]
        self.assertTrue(capabilities["private_companion_enabled"])
        self.assertTrue(capabilities["proactive_private_enabled"])
        self.assertTrue(user["enabled"])
        self.assertEqual("legacy_effective_migration", capabilities["grant_source"])

    def test_existing_legacy_manual_disable_only_keeps_proactive_closed(self) -> None:
        harness = _IdentityCoreHarness()
        harness.data["users"]["legacy"] = {
            "user_id": "legacy",
            "enabled": True,
            "manual_disabled": True,
            "relationship_role": "friend",
            "proactive_daily_limit": 2,
        }

        user = harness._get_user("legacy")

        capabilities = user["unified_profile_capabilities"]
        self.assertTrue(capabilities["private_companion_enabled"])
        self.assertFalse(capabilities["proactive_private_enabled"])
        self.assertTrue(user["enabled"])

    def test_late_imported_manual_grant_repairs_default_closed_capability(self) -> None:
        harness = _IdentityCoreHarness()
        harness.data["users"]["legacy"] = {
            "user_id": "legacy",
            "enabled": False,
            "manual_enabled": True,
            "relationship_role": "friend",
            "proactive_daily_limit": 2,
            "unified_profile_capabilities": default_capabilities(),
        }

        user = harness._get_user("legacy")

        capabilities = user["unified_profile_capabilities"]
        self.assertTrue(capabilities["private_companion_enabled"])
        self.assertTrue(capabilities["proactive_private_enabled"])
        self.assertTrue(user["enabled"])
        self.assertEqual("legacy_default_closed_repair", capabilities["grant_source"])

    def test_official_full_umo_parses_identity_after_long_transport_prefix(self) -> None:
        openid = "openid-owner-with-stable-suffix"
        full_umo = f"official-{'x' * 160}:friendmessage:{openid}"

        normalized = _IdentityCoreHarness._normalize_private_identity_id(full_umo)

        self.assertEqual(openid, normalized)

    def test_official_full_umo_uses_stable_identity_for_runtime_and_auto_profile(self) -> None:
        full_umo = "official-instance:FriendMessage:openid-owner"

        runtime_harness = _IdentityCoreHarness()
        direct = runtime_harness._get_user(full_umo)

        auto_harness = _IdentityCoreHarness()
        profile, created = auto_harness._ensure_auto_private_user_profile(
            SimpleNamespace(unified_msg_origin=full_umo),
            user_id=full_umo,
            sender_display_name="主人",
        )

        self.assertEqual({"openid-owner"}, set(runtime_harness.data["users"]))
        self.assertEqual("openid-owner", direct["user_id"])
        self.assertEqual({"openid-owner"}, set(auto_harness.data["users"]))
        self.assertEqual("openid-owner", profile["user_id"])
        self.assertEqual(full_umo, profile["last_inbound_umo"])
        self.assertTrue(created)

    async def test_startup_diary_check_does_not_force_generation(self) -> None:
        harness = _StartupHarness()

        await harness._startup_prepare_today()

        self.assertEqual(harness.diary_calls, [{}])

    async def test_async_config_save_is_awaited(self) -> None:
        harness = _CoreHarness()

        saved = await harness._save_config_if_possible()

        self.assertTrue(saved)
        self.assertTrue(harness.config.awaited)

    async def test_async_config_save_failure_is_reported(self) -> None:
        harness = _CoreHarness()
        harness.config = _AsyncConfig(fail=True)

        saved = await harness._save_config_if_possible()

        self.assertFalse(saved)
        self.assertTrue(harness.config.awaited)

    async def test_flush_does_not_reschedule_dirty_write_while_stopping(self) -> None:
        harness = _CoreHarness()
        harness._data_save_dirty = True
        harness._stop_event.set()

        await asyncio.wait_for(harness._flush_scheduled_data_save(), timeout=0.2)

        self.assertIsNone(harness._data_save_task)
        self.assertTrue(harness._data_save_dirty)

    def test_store_manager_failure_does_not_fall_back_to_stale_json(self) -> None:
        harness = _CoreHarness()
        harness.store_manager = SimpleNamespace(
            load_sections=lambda *_args, **_kwargs: {},
            load_initial_store=lambda: (_ for _ in ()).throw(OSError("database is locked"))
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            data_file = Path(temp_dir) / "companions.json"
            data_file.write_text('{"users": {"42": {"name": "stale"}}}', encoding="utf-8")
            harness.data_file = str(data_file)

            with self.assertRaisesRegex(OSError, "database is locked"):
                harness._load_data_sync()

    def test_existing_invalid_direct_json_is_not_replaced_with_defaults(self) -> None:
        harness = _CoreHarness()
        with tempfile.TemporaryDirectory() as temp_dir:
            data_file = Path(temp_dir) / "companions.json"
            data_file.write_text('{"users": ', encoding="utf-8")
            harness.data_file = str(data_file)

            with self.assertRaises(Exception):
                harness._load_data_sync()

            self.assertEqual('{"users": ', data_file.read_text(encoding="utf-8"))

    def test_group_only_placeholders_are_removed_from_private_users(self) -> None:
        harness = _CoreHarness()
        harness.default_nickname = "主要用户昵称"
        harness.default_style = "默认语气"
        harness.data = {
            "users": {
                "owner": {"user_id": "owner", "nickname": "主要用户昵称"},
                "group_sender": {
                    "user_id": "group_sender",
                    "nickname": "主要用户昵称",
                    "style": "默认语气",
                    "enabled": False,
                    "relationship_role": "friend",
                    "umo": "default:FriendMessage:group_sender",
                    "last_seen": 20,
                    "last_activity_at": 20,
                    "inbound_count": 3,
                    "relationship_score": 3,
                    "relationship_ledger": [
                        {"reason_code": "group_inbound", "delta": 1},
                        {"reason_code": "group_inbound", "delta": 1},
                    ],
                    "identity_subject_id": "group_sender",
                    "unified_person_id": "person_group_sender",
                    "recent_group_messages": [{"group_id": "100", "text": "群消息"}],
                    "reaction_expression": {"last_sent_at": 12},
                    "last_inbound_umo": "default:GroupMessage:100",
                },
            },
            "unified_person": {
                "identity_links": {
                    "group-link": {
                        "person_id": "person_group_sender",
                        "last_operation_id": "req036.group_observation:fixture",
                    }
                },
                "binding_checkpoints": {
                    "group-checkpoint": {
                        "person_id": "person_group_sender",
                        "last_source_scope": "group:onebot:100",
                    }
                },
            },
        }

        changed = harness._cleanup_orphan_reaction_expression_users()

        self.assertTrue(changed)
        self.assertEqual(["owner"], list(harness.data["users"]))

    def test_private_activity_and_manual_records_survive_orphan_cleanup(self) -> None:
        harness = _CoreHarness()
        harness.default_nickname = "主要用户昵称"
        harness.default_style = "默认语气"
        harness.data = {
            "users": {
                "private": {
                    "user_id": "private",
                    "nickname": "主要用户昵称",
                    "style": "默认语气",
                    "enabled": False,
                    "relationship_role": "friend",
                    "last_private_seen": 10,
                },
                "manual": {
                    "user_id": "manual",
                    "nickname": "主要用户昵称",
                    "style": "默认语气",
                    "enabled": False,
                    "manual_disabled": True,
                    "relationship_role": "friend",
                },
                "profiled": {
                    "user_id": "profiled",
                    "nickname": "独立昵称",
                    "style": "默认语气",
                    "enabled": False,
                    "relationship_role": "friend",
                },
                "private_auto": {
                    "user_id": "private_auto",
                    "nickname": "主要用户昵称",
                    "style": "默认语气",
                    "enabled": False,
                    "relationship_role": "friend",
                    "profile_origin": "private_auto",
                    "auto_profile_created": True,
                },
                "manual_ledger": {
                    "user_id": "manual_ledger",
                    "nickname": "主要用户昵称",
                    "style": "默认语气",
                    "enabled": False,
                    "relationship_role": "friend",
                    "profile_origin": "group_observation",
                    "relationship_score": 2,
                    "relationship_ledger": [{"reason_code": "administrator", "delta": 2}],
                },
            }
        }

        changed = harness._cleanup_orphan_reaction_expression_users()

        self.assertFalse(changed)
        self.assertEqual(
            {"private", "manual", "profiled", "private_auto", "manual_ledger"},
            set(harness.data["users"]),
        )

    def test_reaction_only_scoped_shadow_is_removed_but_canonical_private_user_survives(self) -> None:
        harness = _CoreHarness()
        harness.default_nickname = "主要用户昵称"
        harness.default_style = "默认语气"
        harness._platform_kind_for_umo = lambda _umo: "onebot"
        harness.data = {
            "users": {
                "friend": {
                    "user_id": "friend",
                    "nickname": "好友",
                    "enabled": True,
                    "manual_enabled": True,
                    "relationship_role": "friend",
                    "umo": "default:FriendMessage:friend",
                    "last_inbound_umo": "default:FriendMessage:friend",
                    "private_inbound_count": 2,
                },
                "onebot:friend:0123456789abcdef": {
                    "user_id": "onebot:friend:0123456789abcdef",
                    "nickname": "主要用户昵称",
                    "style": "默认语气",
                    "enabled": False,
                    "relationship_role": "friend",
                    "reaction_expression": {
                        "last_sent_at": 20,
                        "scopes": {"default:FriendMessage:friend": {"last_sent_at": 20}},
                    },
                },
            }
        }

        changed = harness._cleanup_orphan_reaction_expression_users()

        self.assertTrue(changed)
        self.assertEqual(["friend"], list(harness.data["users"]))


if __name__ == "__main__":
    unittest.main()
