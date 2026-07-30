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

    async def test_same_text_from_same_source_is_blocked_after_confirmed_send(self) -> None:
        first = _Event("晚安，睡个好觉哦。")
        await self._send_once(first)

        duplicate = _Event("晚安，睡个好觉哦。", message_id="message-2")
        await self.plugin.suppress_recent_duplicate_outbound_text(duplicate)

        self.assertTrue(duplicate.is_stopped())
        self.assertEqual([], list(duplicate.get_result().chain or []))

    async def test_concurrent_same_text_is_blocked_while_first_send_is_pending(self) -> None:
        first = _Event("晚安，睡个好觉哦。")
        await self.plugin.suppress_recent_duplicate_outbound_text(first)
        self.assertFalse(first.is_stopped())

        duplicate = _Event("晚安，睡个好觉哦。", message_id="message-2")
        await self.plugin.suppress_recent_duplicate_outbound_text(duplicate)

        self.assertTrue(duplicate.is_stopped())

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
