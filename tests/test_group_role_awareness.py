# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from astrbot_plugin_private_companion.atrelay import AtRelayMixin
from astrbot_plugin_private_companion.group_observation import GroupObservationMixin
from astrbot_plugin_private_companion.interaction_utils import InteractionUtilsMixin
from astrbot_plugin_private_companion.main import PrivateCompanionPlugin


class _RoleHarness(GroupObservationMixin):
    def __init__(self) -> None:
        self.data = {"groups": {"group-1": {"group_id": "group-1", "members": {}}}}
        self._data_lock = asyncio.Lock()
        self._save_data_sync = Mock()
        self._member_getter = AsyncMock(return_value=[])

    def _get_group(self, group_id: str) -> dict:
        return self.data["groups"].setdefault(group_id, {"group_id": group_id, "members": {}})

    async def _get_group_member_list_for_tool(self, event, group_id, *, force_refresh=False):
        return await self._member_getter(event, group_id, force_refresh=force_refresh)

    @staticmethod
    def _event_self_id(_event) -> str:
        return "bot-1"


class _CaptureHarness(_RoleHarness):
    def __init__(self) -> None:
        super().__init__()
        self._refresh_group_role_snapshot = AsyncMock(return_value=True)
        self._schedule_data_save = Mock()

    @staticmethod
    def _capture_group_observation_once(*_args, **_kwargs) -> bool:
        return True

    @staticmethod
    def _event_message_id(_event) -> str:
        return "message-1"


class _PermissionHarness(InteractionUtilsMixin, GroupObservationMixin):
    def __init__(self, refreshed_at: float) -> None:
        self.data = {
            "groups": {
                "group-1": {
                    "role_snapshot": {
                        "complete": True,
                        "refreshed_at": refreshed_at,
                        "owner": {"user_id": "owner-1", "name": "群主", "role": "owner"},
                        "admins": [{"user_id": "admin-1", "name": "管理", "role": "admin"}],
                        "bot": {"user_id": "bot-1", "name": "Bot", "role": "member"},
                    }
                }
            }
        }

    @staticmethod
    def _extract_group_id_from_event(_event) -> str:
        return "group-1"


class _AtRelayHarness(AtRelayMixin):
    atrelay_member_cache_minutes = 60


class GroupRoleAwarenessTests(unittest.IsolatedAsyncioTestCase):
    def test_event_role_and_member_list_build_identity_snapshot(self) -> None:
        harness = _RoleHarness()
        group = harness._get_group("group-1")
        event = SimpleNamespace(
            message_obj=SimpleNamespace(raw_message={"sender": {"role": "admin"}})
        )

        harness._observe_group_role_from_event(
            group,
            event,
            sender_id="admin-1",
            sender_name="管理甲",
        )
        snapshot = harness._apply_group_role_member_list(
            group,
            [
                {"user_id": "owner-1", "card": "群主甲", "role": "owner"},
                {"user_id": "admin-1", "nickname": "管理甲", "role": "admin"},
                {"user_id": "bot-1", "nickname": "Bot", "role": "member"},
            ],
            self_id="bot-1",
            now=time.time(),
        )

        self.assertEqual("admin", harness._group_sender_role_from_event(event))
        self.assertEqual("owner-1", snapshot["owner"]["user_id"])
        self.assertEqual(["admin-1"], [item["user_id"] for item in snapshot["admins"]])
        self.assertEqual("member", snapshot["bot"]["role"])

    def test_role_context_is_absent_from_ordinary_conversation(self) -> None:
        harness = _RoleHarness()
        group = harness._get_group("group-1")
        harness._apply_group_role_member_list(
            group,
            [{"user_id": "owner-1", "nickname": "群主甲", "role": "owner"}],
            self_id="bot-1",
            now=time.time(),
        )

        self.assertFalse(harness._group_role_context_requested("今天吃什么？"))
        self.assertFalse(harness._group_role_context_requested("你是谁？"))
        self.assertEqual("", harness._format_group_role_context_for_prompt(group, "user-1", "今天吃什么？"))

    def test_role_question_injects_identity_and_capability_boundary(self) -> None:
        harness = _RoleHarness()
        group = harness._get_group("group-1")
        harness._apply_group_role_member_list(
            group,
            [
                {"user_id": "owner-1", "card": "群主甲", "role": "owner"},
                {"user_id": "admin-1", "card": "管理甲", "role": "admin"},
                {"user_id": "bot-1", "nickname": "Bot", "role": "member"},
            ],
            self_id="bot-1",
            now=time.time(),
        )

        prompt = harness._format_group_role_context_for_prompt(group, "user-1", "谁是群主和管理员？")

        self.assertIn("Bot 在本群身份：普通成员", prompt)
        self.assertIn("群主甲[QQ:owner-1]", prompt)
        self.assertIn("管理甲[QQ:admin-1]", prompt)
        self.assertIn("不能承诺执行当前工具并未实际支持", prompt)

    def test_moderation_request_also_requests_role_context(self) -> None:
        harness = _RoleHarness()

        self.assertTrue(harness._group_role_context_requested("把他禁言十分钟"))
        self.assertTrue(harness._group_role_context_requested("把这个人移出群"))
        self.assertTrue(harness._group_role_context_requested("你在这个群里是什么身份？"))

    def test_stale_snapshot_is_marked_uncertain(self) -> None:
        harness = _RoleHarness()
        group = harness._get_group("group-1")
        harness._apply_group_role_member_list(
            group,
            [{"user_id": "owner-1", "nickname": "旧群主", "role": "owner"}],
            self_id="bot-1",
            now=time.time() - 25 * 3600,
        )

        prompt = harness._format_group_role_context_for_prompt(group, "user-1", "群主是谁？")

        self.assertIn("身份快照可能已过期", prompt)

    async def test_explicit_request_forces_one_refresh_per_event(self) -> None:
        harness = _CaptureHarness()
        event = SimpleNamespace(unified_msg_origin="default:GroupMessage:group-1")
        kwargs = {
            "group_id": "group-1",
            "sender_id": "user-1",
            "sender_name": "用户",
            "text": "谁是管理员？",
            "scene": {"talking_to": "bot"},
        }

        await PrivateCompanionPlugin._capture_group_observation_event(harness, event, **kwargs)
        await PrivateCompanionPlugin._capture_group_observation_event(harness, event, **kwargs)

        harness._refresh_group_role_snapshot.assert_awaited_once_with(event, "group-1", force=True)

    async def test_refresh_passes_force_through_to_member_provider(self) -> None:
        harness = _RoleHarness()
        harness._member_getter.return_value = [
            {"user_id": "bot-1", "nickname": "Bot", "role": "admin"}
        ]

        refreshed = await harness._refresh_group_role_snapshot(SimpleNamespace(), "group-1", force=True)

        self.assertTrue(refreshed)
        harness._member_getter.assert_awaited_once_with(
            unittest.mock.ANY,
            "group-1",
            force_refresh=True,
        )
        self.assertEqual("admin", harness.data["groups"]["group-1"]["role_snapshot"]["bot"]["role"])

    async def test_force_refresh_bypasses_atrelay_member_cache(self) -> None:
        harness = _AtRelayHarness()
        harness._atrelay_member_cache = {
            "group-1": {"ts": time.time(), "items": [{"user_id": "old", "role": "owner"}]}
        }
        api = SimpleNamespace(
            call_action=AsyncMock(
                return_value=[{"user_id": "new", "role": "owner"}]
            )
        )
        event = SimpleNamespace(bot=SimpleNamespace(api=api))

        cached = await harness._get_group_member_list_for_tool(event, "group-1")
        fresh = await harness._get_group_member_list_for_tool(event, "group-1", force_refresh=True)

        self.assertEqual("old", cached[0]["user_id"])
        self.assertEqual("new", fresh[0]["user_id"])
        api.call_action.assert_awaited_once_with("get_group_member_list", group_id="group-1")

    def test_admin_fallback_uses_only_fresh_snapshot(self) -> None:
        event = SimpleNamespace(
            message_obj=SimpleNamespace(raw_message={}),
            get_sender_id=lambda: "admin-1",
        )

        self.assertTrue(_PermissionHarness(time.time())._is_group_admin_event(event))
        self.assertFalse(_PermissionHarness(time.time() - 25 * 3600)._is_group_admin_event(event))


if __name__ == "__main__":
    unittest.main()
