# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import unittest

from astrbot_plugin_private_companion.interaction_utils import InteractionUtilsMixin
from astrbot_plugin_private_companion.main import PrivateCompanionPlugin
from astrbot_plugin_private_companion.core_store import CoreStoreMixin
from astrbot_plugin_private_companion.proactive import ProactiveMixin
from astrbot_plugin_private_companion.unified_profile_service import (
    default_capabilities,
    private_companion_gate,
)


class _Event:
    def __init__(self, text: str, *, private: bool = True, sender_id: str = "openid-owner") -> None:
        self.message_str = text
        self.unified_msg_origin = "official:FriendMessage:openid-owner" if private else "official:GroupMessage:group-1"
        self.private = private
        self.sender_id = sender_id
        self.stopped = False

    def get_sender_id(self) -> str:
        return self.sender_id

    def is_private_chat(self) -> bool:
        return self.private

    def stop_event(self) -> None:
        self.stopped = True


class _Harness(InteractionUtilsMixin):
    def __init__(self) -> None:
        self.require_private_opt_in = True
        capabilities = default_capabilities(grant_source="legacy_effective_migration")
        capabilities["private_companion_enabled"] = True
        self.data = {"users": {"openid-owner": {"unified_profile_capabilities": capabilities}}}
        self._data_lock = asyncio.Lock()
        self.replies: list[str] = []
        self.save_count = 0
        self.calls: list[str] = []

    @staticmethod
    def _qzone_note_event_bot(event) -> None:
        return None

    @staticmethod
    def _sender_display_name(_event) -> str:
        return "测试用户"

    def _ensure_auto_private_user_profile(self, _event, *, user_id: str, **_kwargs):
        return self._get_user(user_id), False

    @staticmethod
    def _req036_attach_unified_profile_context(_event, **_kwargs) -> dict:
        return {"state": "profile_exact"}

    @staticmethod
    def _schedule_data_save(*_args, **_kwargs) -> None:
        return None

    @staticmethod
    def _req036_private_gate_for_user(user: dict) -> dict:
        return private_companion_gate(user, "固定拒绝文本")

    async def _req036_reject_unauthorized_private_event(self, event, gate: dict) -> None:
        await self._reply(event, str(gate["reply"]))
        event.stop_event()

    def _get_user(self, user_id: str) -> dict:
        return self.data["users"].setdefault(user_id, {})

    _normalize_private_identity_id = staticmethod(CoreStoreMixin._normalize_private_identity_id)

    @staticmethod
    def _note_private_user_umo(user_id: str, user: dict, umo: str) -> None:
        user["last_inbound_umo"] = umo

    def _bind_private_delivery_umo(self, user_id: str, user: dict, umo: str):
        self.calls.append("bind")
        user["bound_delivery_umo"] = umo
        return True, "已绑定当前私聊"

    def _format_private_delivery_binding_status(self, user_id: str, user: dict) -> str:
        self.calls.append("view")
        return "绑定状态：已绑定当前私聊"

    def _unbind_private_delivery_umo(self, user: dict):
        self.calls.append("unbind")
        user.pop("bound_delivery_umo", None)
        return True, "已取消人工绑定"

    def _save_data_sync(self, **_kwargs) -> None:
        self.save_count += 1

    async def _reply(self, event, text: str, **kwargs) -> None:
        self.replies.append(str(text))

    async def _reply_with_optional_media(self, event, text: str, *args, **kwargs) -> None:
        self.replies.append(str(text))


class _BindingHarness(ProactiveMixin):
    @staticmethod
    def _private_umo_session_id(umo: str) -> str:
        return str(umo).rsplit(":FriendMessage:", 1)[-1] if ":FriendMessage:" in str(umo) else ""

    @staticmethod
    def _note_private_user_umo(_user_id: str, user: dict, umo: str) -> None:
        user["last_inbound_umo"] = umo


class PrivateDeliveryBindingCommandTests(unittest.IsolatedAsyncioTestCase):
    async def _run(self, harness: _Harness, text: str, *, private: bool = True) -> _Event:
        event = _Event(text, private=private)
        await PrivateCompanionPlugin.companion_command(harness, event)
        return event

    async def test_bind_view_and_unbind_use_current_private_conversation(self) -> None:
        harness = _Harness()

        await self._run(harness, "陪伴 绑定 主动消息")
        await self._run(harness, "陪伴 查看主动路由")
        await self._run(harness, "陪伴 解绑主动消息")

        self.assertEqual(["bind", "view", "unbind"], harness.calls)
        self.assertEqual(2, harness.save_count)
        self.assertIn("已绑定当前私聊", harness.replies[0])
        self.assertIn("绑定状态", harness.replies[1])
        self.assertIn("已取消人工绑定", harness.replies[2])

    async def test_binding_is_rejected_in_group_even_when_private_opt_in_is_disabled(self) -> None:
        harness = _Harness()
        harness.require_private_opt_in = False

        event = await self._run(harness, "陪伴 绑定主动消息", private=False)

        self.assertEqual([], harness.calls)
        self.assertIn("私聊窗口", harness.replies[0])
        self.assertTrue(event.stopped)

    async def test_binding_bootstrap_keeps_proactive_capability_closed(self) -> None:
        harness = _Harness()
        harness.data["users"]["openid-owner"]["unified_profile_capabilities"] = default_capabilities()

        await self._run(harness, "陪伴 绑定主动消息")

        self.assertEqual(["bind"], harness.calls)
        capabilities = harness.data["users"]["openid-owner"]["unified_profile_capabilities"]
        self.assertTrue(capabilities["private_companion_enabled"])
        self.assertFalse(capabilities["proactive_private_enabled"])
        self.assertNotIn("固定拒绝文本", harness.replies)

    async def test_non_binding_command_is_not_denied_during_bootstrap(self) -> None:
        gate = private_companion_gate({"unified_profile_capabilities": default_capabilities()})
        self.assertTrue(gate["allowed"])
        self.assertEqual("private_chat_unrestricted", gate["code"])

    async def test_command_normalizes_full_umo_sender_id_before_loading_user(self) -> None:
        harness = _Harness()
        full_umo = "official:FriendMessage:openid-from-umo"
        event = _Event("陪伴 绑定主动消息", sender_id=full_umo)
        event.unified_msg_origin = full_umo

        await PrivateCompanionPlugin.companion_command(harness, event)

        self.assertIn("openid-from-umo", harness.data["users"])
        self.assertNotIn(full_umo, harness.data["users"])

    def test_binding_records_route_without_silently_enabling_target(self) -> None:
        harness = object.__new__(_BindingHarness)
        user = {"user_id": "openid-owner", "enabled": False}
        umo = "official:FriendMessage:openid-owner"

        changed, message = harness._bind_private_delivery_umo("openid-owner", user, umo)

        self.assertTrue(changed)
        self.assertFalse(user["enabled"])
        self.assertNotIn("manual_enabled", user)
        self.assertNotIn("target_user", user)
        self.assertEqual(umo, user["bound_delivery_umo"])
        self.assertIn("配置引导", message)


if __name__ == "__main__":
    unittest.main()
