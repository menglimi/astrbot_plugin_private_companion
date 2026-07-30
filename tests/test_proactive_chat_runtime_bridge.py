# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from types import SimpleNamespace

from astrbot.api.message_components import Plain
from astrbot.core.platform.platform import PlatformStatus

from astrbot_plugin_private_companion.proactive_chat_runtime_bridge import (
    ProactiveChatRuntimeBridge,
)


SESSION_ID = "default:FriendMessage:10001"


class _Platform:
    def __init__(self, *, fail: bool = False) -> None:
        self.status = PlatformStatus.RUNNING
        self.fail = fail
        self.sent: list[object] = []

    @staticmethod
    def meta():
        return SimpleNamespace(id="default", name="default")

    async def send_by_session(self, _session, chain) -> None:
        if self.fail:
            raise RuntimeError("adapter rejected send")
        self.sent.append(chain)


class _PlatformManager:
    def __init__(self, platform: _Platform) -> None:
        self.platform_insts = [platform]

    def get_insts(self):
        return list(self.platform_insts)


class _Context:
    def __init__(self, platform: _Platform) -> None:
        self.platform_manager = _PlatformManager(platform)
        self.fallback_sends = 0

    async def send_message(self, _session_id, _chain) -> None:
        self.fallback_sends += 1


class _ProactiveChat:
    version = "v1.2.4"

    def __init__(self, platform: _Platform) -> None:
        self.context = _Context(platform)
        self.session_data = {SESSION_ID: {"unanswered_count": 2}}
        self.prepared_system_prompt = ""
        self.finalized = 0
        self.rescheduled = 0
        self.persisted = 0

    @staticmethod
    def _normalize_session_id(session_id: str) -> str:
        return session_id

    @staticmethod
    def _parse_session_id(session_id: str):
        return tuple(session_id.split(":", 2))

    async def check_and_chat(self, session_id: str) -> None:
        package = await self._prepare_llm_request(session_id)
        if not package:
            await self._schedule_next_chat_and_save(session_id)
            return
        response, user_prompt = await self._generate_llm_response(
            session_id,
            {},
            package["history"],
            package["system_prompt"],
            2,
        )
        if not response:
            await self._schedule_next_chat_and_save(session_id)
            return
        await self._send_proactive_message(session_id, response)
        await self._finalize_and_reschedule(
            session_id,
            package["conv_id"],
            user_prompt,
            response,
            2,
        )

    async def _prepare_llm_request(self, session_id: str):
        return {
            "conv_id": "conv-1",
            "history": [],
            "system_prompt": "原始人格",
            "session_id": session_id,
        }

    async def _generate_llm_response(
        self,
        _session_id,
        _session_config,
        _history,
        system_prompt,
        _unanswered_count,
    ):
        self.prepared_system_prompt = system_prompt
        return "好呀，原始候选。", "原始主动动机"

    async def _send_proactive_message(self, session_id: str, text: str) -> None:
        await self._send_chain_with_hooks(session_id, [Plain(text=text)])

    async def _send_chain_with_hooks(self, _session_id: str, _components: list[object]) -> None:
        raise AssertionError("深度桥接应接管真实发送出口")

    async def _trigger_decorating_hooks(self, _session_id: str, components: list[object]):
        return list(components)

    async def _persist_proactive_message_to_platform_history(self, _session_id, _chain) -> None:
        self.persisted += 1

    async def _finalize_and_reschedule(self, _session_id, *_args) -> None:
        self.finalized += 1

    async def _schedule_next_chat_and_save(self, _session_id) -> None:
        self.rescheduled += 1


class _Owner:
    enable_proactive_chat_integration = True

    def __init__(self, proactive: _ProactiveChat) -> None:
        self.context = SimpleNamespace(
            get_all_stars=lambda: [
                SimpleNamespace(
                    module_path="astrbot_plugin_proactive_chat.main",
                    name="astrbot_plugin_proactive_chat",
                    root_dir_name="astrbot_plugin_proactive_chat",
                    star_cls=proactive,
                )
            ]
        )
        self.reviewed: list[str] = []
        self.recorded: list[str] = []
        self.cancelled: list[str] = []

    async def _prepare_proactive_chat_bridge(self, _session_id, *, unanswered_count=0):
        return {
            "enabled": True,
            "allowed": True,
            "token": "token-1",
            "prompt_fragment": f"深度上下文：未回应 {unanswered_count} 次；当前关系稳定；使用已审核表达。",
        }

    async def _review_proactive_chat_bridge_message(
        self,
        _session_id,
        text,
        *,
        token="",
        attempt_id="",
    ):
        self.reviewed.append(f"{token}:{attempt_id}:{text}")
        return {
            "ok": True,
            "decision": "rewrite",
            "reason": "去掉回复式开头",
            "text": "原始候选。",
        }

    async def _record_proactive_chat_bridge_sent(
        self,
        _session_id,
        text,
        *,
        token="",
        attempt_id="",
    ):
        self.recorded.append(f"{token}:{attempt_id}:{text}")
        return {"recorded": True}

    async def _cancel_proactive_chat_bridge(self, _session_id, *, token=""):
        self.cancelled.append(token)
        return True


class RuntimeBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_deep_bridge_injects_reviews_confirms_and_restores_methods(self):
        platform = _Platform()
        proactive = _ProactiveChat(platform)
        owner = _Owner(proactive)
        bridge = ProactiveChatRuntimeBridge(owner)
        original_prepare = proactive._prepare_llm_request.__func__

        attached = await bridge.refresh()
        await proactive.check_and_chat(SESSION_ID)

        self.assertTrue(attached)
        self.assertIn("private_companion_proactive_chat_deep_bridge_v1", proactive.prepared_system_prompt)
        self.assertIn("当前关系稳定", proactive.prepared_system_prompt)
        self.assertEqual(1, len(platform.sent))
        self.assertEqual("原始候选。", platform.sent[0].chain[0].text)
        self.assertEqual(1, len(owner.reviewed))
        self.assertEqual(1, len(owner.recorded))
        self.assertEqual([], owner.cancelled)
        self.assertEqual(1, proactive.finalized)
        self.assertEqual(1, proactive.persisted)
        self.assertTrue(bridge.status()["attached"])

        bridge.detach(reason="test")

        self.assertIs(original_prepare, proactive._prepare_llm_request.__func__)
        self.assertFalse(hasattr(proactive, "_private_companion_runtime_bridge"))

    async def test_failed_platform_send_does_not_finalize_as_success(self):
        platform = _Platform(fail=True)
        proactive = _ProactiveChat(platform)
        owner = _Owner(proactive)
        bridge = ProactiveChatRuntimeBridge(owner)
        self.assertTrue(bridge.attach(proactive))

        await proactive.check_and_chat(SESSION_ID)

        self.assertEqual([], platform.sent)
        self.assertEqual([], owner.recorded)
        self.assertEqual(["token-1"], owner.cancelled)
        self.assertEqual(0, proactive.finalized)
        self.assertEqual(1, proactive.rescheduled)
        self.assertEqual(1, bridge.status()["counters"]["delivery_failed"])
        await bridge.stop()


if __name__ == "__main__":
    unittest.main()
