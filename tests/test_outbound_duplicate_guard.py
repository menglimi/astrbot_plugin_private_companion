from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from astrbot.api.event import MessageEventResult
from astrbot.api.message_components import Image, Plain

from astrbot_plugin_private_companion.main import PrivateCompanionPlugin


class _Event:
    def __init__(
        self,
        text: str,
        *,
        sender_id: str = "user-1",
        self_id: str = "bot-1",
        message_id: str = "message-1",
        group_id: str = "group-1",
    ) -> None:
        self.message_str = text
        self.unified_msg_origin = f"default:GroupMessage:{group_id}"
        self.message_obj = SimpleNamespace(
            raw_message={
                "post_type": "message",
                "message_type": "group",
                "group_id": group_id,
                "user_id": sender_id,
                "self_id": self_id,
                "message_id": message_id,
            }
        )
        self._result = MessageEventResult().message(text)
        self._stopped = False
        self._has_send_oper = False
        self._sender_id = sender_id
        self._self_id = self_id

    def get_result(self):
        return self._result

    def set_result(self, result) -> None:
        self._result = result

    def stop_event(self) -> None:
        self._stopped = True

    def is_stopped(self) -> bool:
        return self._stopped

    def get_sender_id(self) -> str:
        return self._sender_id

    def get_self_id(self) -> str:
        return self._self_id


class OutboundDuplicateGuardTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.plugin = PrivateCompanionPlugin.__new__(PrivateCompanionPlugin)
        self.plugin.enabled = True
        self.plugin._recent_outbound_text_guard = {}

    async def _send_once(self, event: _Event) -> None:
        await self.plugin.suppress_recent_duplicate_outbound_text(event)
        self.assertFalse(event.is_stopped())
        event._has_send_oper = True
        await self.plugin.remember_confirmed_outbound_text(event)

    async def test_same_text_from_distinct_message_is_allowed_after_confirmed_send(self) -> None:
        first = _Event("晚安，睡个好觉哦。")
        await self._send_once(first)

        independent = _Event("晚安，睡个好觉哦。", message_id="message-2")
        await self.plugin.suppress_recent_duplicate_outbound_text(independent)

        self.assertFalse(independent.is_stopped())
        self.assertTrue(list(independent.get_result().chain or []))

    async def test_concurrent_same_text_from_distinct_message_is_allowed(self) -> None:
        first = _Event("晚安，睡个好觉哦。")
        await self.plugin.suppress_recent_duplicate_outbound_text(first)
        self.assertFalse(first.is_stopped())

        independent = _Event("晚安，睡个好觉哦。", message_id="message-2")
        await self.plugin.suppress_recent_duplicate_outbound_text(independent)

        self.assertFalse(independent.is_stopped())

    async def test_same_message_is_blocked_after_confirmed_send(self) -> None:
        first = _Event("晚安，睡个好觉哦。")
        await self._send_once(first)

        retry = _Event("晚安，睡个好觉哦。")
        await self.plugin.suppress_recent_duplicate_outbound_text(retry)

        self.assertTrue(retry.is_stopped())
        self.assertEqual([], list(retry.get_result().chain or []))

    async def test_same_message_is_blocked_while_first_send_is_pending(self) -> None:
        first = _Event("晚安，睡个好觉哦。")
        await self.plugin.suppress_recent_duplicate_outbound_text(first)

        concurrent_branch = _Event("晚安，睡个好觉哦。")
        await self.plugin.suppress_recent_duplicate_outbound_text(concurrent_branch)

        self.assertTrue(concurrent_branch.is_stopped())

    def test_same_inbound_duplicate_survives_slow_failed_tool_loop(self) -> None:
        first = _Event("唔，省流版：这是个 AstrBot 插件")
        candidate = self.plugin._outbound_text_duplicate_candidate(first)

        self.assertEqual(
            "",
            self.plugin._reserve_outbound_text_candidate(candidate, now=100.0),
        )
        self.plugin._confirm_outbound_text_candidate(candidate, now=101.0)

        retry = _Event("唔，省流版：这是个 AstrBot 插件")
        retry_candidate = self.plugin._outbound_text_duplicate_candidate(retry)
        self.assertEqual(
            "sent",
            self.plugin._reserve_outbound_text_candidate(retry_candidate, now=126.0),
        )

    def test_slow_repeat_from_a_new_inbound_message_is_not_blocked(self) -> None:
        first = _Event("我再说明一次。")
        candidate = self.plugin._outbound_text_duplicate_candidate(first)
        self.plugin._reserve_outbound_text_candidate(candidate, now=100.0)
        self.plugin._confirm_outbound_text_candidate(candidate, now=101.0)

        later = _Event("我再说明一次。", message_id="message-2")
        later_candidate = self.plugin._outbound_text_duplicate_candidate(later)
        self.assertEqual(
            "",
            self.plugin._reserve_outbound_text_candidate(later_candidate, now=126.0),
        )

    def test_fuzzy_repeat_from_same_inbound_message_is_blocked(self) -> None:
        first = self.plugin._outbound_text_duplicate_candidate(
            _Event("今天天气真好呀！")
        )
        self.plugin._reserve_outbound_text_candidate(first, now=100.0)
        self.plugin._confirm_outbound_text_candidate(first, now=101.0)

        retry = self.plugin._outbound_text_duplicate_candidate(
            _Event("今天天气真好")
        )
        self.assertEqual(
            "sent",
            self.plugin._reserve_outbound_text_candidate(retry, now=126.0),
        )

    def test_fuzzy_repeat_from_distinct_inbound_message_is_allowed(self) -> None:
        first = self.plugin._outbound_text_duplicate_candidate(
            _Event("今天天气真好呀！")
        )
        self.plugin._reserve_outbound_text_candidate(first, now=100.0)
        self.plugin._confirm_outbound_text_candidate(first, now=101.0)

        independent = self.plugin._outbound_text_duplicate_candidate(
            _Event("今天天气真好", message_id="message-2")
        )
        self.assertEqual(
            "",
            self.plugin._reserve_outbound_text_candidate(independent, now=102.0),
        )

    def test_interleaved_messages_keep_independent_idempotency_state(self) -> None:
        first = self.plugin._outbound_text_duplicate_candidate(_Event("相同回复"))
        second = self.plugin._outbound_text_duplicate_candidate(
            _Event("相同回复", message_id="message-2")
        )

        self.assertEqual("", self.plugin._reserve_outbound_text_candidate(first, now=100.0))
        self.assertEqual("", self.plugin._reserve_outbound_text_candidate(second, now=100.1))
        self.plugin._confirm_outbound_text_candidate(first, now=100.2)

        self.assertEqual(
            "pending",
            self.plugin._reserve_outbound_text_candidate(second, now=100.3),
        )
        self.plugin._confirm_outbound_text_candidate(second, now=100.4)
        self.assertEqual(
            "sent",
            self.plugin._reserve_outbound_text_candidate(first, now=100.5),
        )

    async def test_missing_message_id_keeps_same_sender_short_window_guard(self) -> None:
        first = _Event("适配器没有消息编号", message_id="")
        await self._send_once(first)

        uncertain = _Event("适配器没有消息编号", message_id="message-2")
        await self.plugin.suppress_recent_duplicate_outbound_text(uncertain)

        self.assertTrue(uncertain.is_stopped())

    def test_missing_message_id_fallback_expires_after_sent_window(self) -> None:
        first = self.plugin._outbound_text_duplicate_candidate(
            _Event("适配器没有消息编号", message_id="")
        )
        self.plugin._reserve_outbound_text_candidate(first, now=100.0)
        self.plugin._confirm_outbound_text_candidate(first, now=101.0)

        later = self.plugin._outbound_text_duplicate_candidate(
            _Event("适配器没有消息编号", message_id="message-2")
        )
        self.assertEqual(
            "",
            self.plugin._reserve_outbound_text_candidate(later, now=106.1),
        )

    async def test_self_echo_with_distinct_message_id_is_still_blocked(self) -> None:
        first = _Event("晚安，睡个好觉哦。")
        await self._send_once(first)

        echo = _Event(
            "晚安，睡个好觉哦。",
            sender_id="bot-1",
            self_id="bot-1",
            message_id="echo-message-2",
        )
        await self.plugin.suppress_recent_duplicate_outbound_text(echo)

        self.assertTrue(echo.is_stopped())

    async def test_partial_primary_send_is_not_confirmed_as_complete_outbound_text(
        self,
    ) -> None:
        event = _Event("第一段。第二段。")
        event.set_result(
            MessageEventResult(chain=[Plain("第一段。"), Plain("第二段。")])
        )
        await self.plugin.suppress_recent_duplicate_outbound_text(event)
        candidate = event._private_companion_outbound_text_candidate
        event._has_send_oper = True
        event._private_companion_reaction_expression_delivery_tracker = {
            "successful_signatures": [("plain", "第一段。")],
        }

        await self.plugin.remember_confirmed_outbound_text(event)

        guard_key = self.plugin._outbound_text_guard_key(candidate)
        self.assertEqual(
            "pending",
            self.plugin._recent_outbound_text_guard[guard_key]["state"],
        )

    async def test_same_group_text_for_different_sender_is_not_suppressed(self) -> None:
        first = _Event("晚安，睡个好觉哦。", sender_id="user-1")
        await self._send_once(first)

        other = _Event("晚安，睡个好觉哦。", sender_id="user-2", message_id="message-2")
        await self.plugin.suppress_recent_duplicate_outbound_text(other)

        self.assertFalse(other.is_stopped())

    async def test_media_result_is_not_reserved_by_plain_text_guard(self) -> None:
        event = _Event("配图")
        event.set_result(MessageEventResult(chain=[Plain("配图"), Image(file="image.png")]))

        await self.plugin.suppress_recent_duplicate_outbound_text(event)

        self.assertFalse(event.is_stopped())
        self.assertEqual({}, self.plugin._recent_outbound_text_guard)

    async def test_group_self_echo_is_stopped_before_observation_and_llm(self) -> None:
        event = _Event("晚安，睡个好觉哦。", sender_id="bot-1", self_id="bot-1")
        self.plugin._qzone_note_event_bot = Mock()
        self.plugin._feature_enabled_or_temp_unlocked = Mock(return_value=True)
        self.plugin._message_debounce_command_text = Mock(return_value=False)
        self.plugin._proactive_only_blocks_passive_event = Mock(return_value=False)
        self.plugin._extract_group_id_from_event = Mock(return_value="group-1")
        self.plugin._group_enabled_for_event = Mock(return_value=True)

        await self.plugin.on_group_message(event)

        self.assertTrue(event.is_stopped())


if __name__ == "__main__":
    unittest.main()
