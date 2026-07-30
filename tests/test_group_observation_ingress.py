# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from astrbot_plugin_private_companion.group_observation import GroupObservationMixin
from astrbot_plugin_private_companion.main import PrivateCompanionPlugin


ROOT = Path(__file__).resolve().parents[1]


class _ObservationHarness(GroupObservationMixin):
    def __init__(self) -> None:
        self.update_calls = 0

    @staticmethod
    def _event_components(event):
        return list(getattr(event, "components", []) or [])

    def _update_group_observation(self, group, **kwargs) -> None:
        self.update_calls += 1
        group.setdefault("recent_messages", []).append(
            {
                "sender_id": kwargs.get("sender_id"),
                "text": kwargs.get("text"),
                "message_id": kwargs.get("message_id"),
            }
        )


class _GroupEvent:
    def __init__(self, text: str = "群消息") -> None:
        self.message_str = text
        self.unified_msg_origin = "default:GroupMessage:group-1"
        self._sender_id = "user-1"
        self._stopped = False

    def get_sender_id(self) -> str:
        return self._sender_id

    def stop_event(self) -> None:
        self._stopped = True


class GroupObservationIngressTests(unittest.IsolatedAsyncioTestCase):
    def test_media_only_group_message_gets_readable_observation_text(self) -> None:
        image = type("Image", (), {})()
        record = type("Record", (), {})()
        event = SimpleNamespace(message_str="", components=[image, record])

        text = _ObservationHarness()._group_observation_event_text(event)

        self.assertEqual("[图片] [语音]", text)

    def test_early_and_reply_paths_only_count_same_message_once(self) -> None:
        harness = _ObservationHarness()
        group: dict = {}
        event = SimpleNamespace()

        first = harness._capture_group_observation_once(
            group,
            sender_id="user-1",
            sender_name="用户",
            text="在吗",
            group_id="group-1",
            scene={"talking_to": "group"},
            message_id="message-1",
            event=event,
        )
        second = harness._capture_group_observation_once(
            group,
            sender_id="user-1",
            sender_name="用户",
            text="在吗",
            group_id="group-1",
            scene={"talking_to": "bot", "trigger": "at_bot"},
            message_id="message-1",
            event=event,
        )

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(1, harness.update_calls)
        self.assertEqual("bot", group["recent_messages"][0]["talking_to"])
        self.assertEqual("at_bot", group["recent_messages"][0]["scene_trigger"])

    async def test_group_reply_block_still_records_before_returning(self) -> None:
        plugin = PrivateCompanionPlugin.__new__(PrivateCompanionPlugin)
        event = _GroupEvent()
        plugin._qzone_note_event_bot = Mock()
        plugin._feature_enabled_or_temp_unlocked = Mock(return_value=True)
        plugin._extract_group_id_from_event = Mock(return_value="group-1")
        plugin._group_enabled_for_event = Mock(return_value=True)
        plugin._event_self_id = Mock(return_value="bot-1")
        plugin._group_observation_event_text = Mock(return_value="群消息")
        plugin._message_debounce_command_text = Mock(return_value=False)
        plugin._sender_display_name = Mock(return_value="用户")
        plugin._capture_group_observation_event = AsyncMock(return_value=True)
        plugin._event_existing_reply_result_preview = Mock(return_value="")
        plugin._proactive_only_blocks_passive_event = Mock(return_value=False)
        plugin._group_llm_reply_blocked = Mock(return_value=True)

        await plugin.on_group_message(event)

        plugin._capture_group_observation_event.assert_awaited_once()
        self.assertFalse(event._stopped)

    async def test_group_message_applies_reaction_feedback_in_exact_scope(self) -> None:
        plugin = PrivateCompanionPlugin.__new__(PrivateCompanionPlugin)
        event = _GroupEvent("刚才那张表情包很合适")
        scope_key = event.unified_msg_origin
        user: dict = {}
        plugin.data = {"users": {"user-1": user}}
        plugin._data_lock = asyncio.Lock()
        plugin._qzone_note_event_bot = Mock()
        plugin._feature_enabled_or_temp_unlocked = Mock(return_value=True)
        plugin._extract_group_id_from_event = Mock(return_value="group-1")
        plugin._group_enabled_for_event = Mock(return_value=True)
        plugin._event_self_id = Mock(return_value="bot-1")
        plugin._group_observation_event_text = Mock(return_value=event.message_str)
        plugin._message_debounce_command_text = Mock(return_value=False)
        plugin._sender_display_name = Mock(return_value="用户")
        plugin._reaction_expression_scope_key = Mock(return_value=scope_key)
        plugin._apply_reaction_expression_feedback = Mock(
            return_value={
                "signal": "positive",
                "image_id": "group-image",
                "score": 1,
            }
        )
        plugin._persist_reaction_expression_state = Mock()
        plugin._capture_group_observation_event = AsyncMock(return_value=True)
        plugin._start_group_image_understanding = Mock()
        plugin._event_existing_reply_result_preview = Mock(return_value="")
        plugin._proactive_only_blocks_passive_event = Mock(return_value=True)

        await plugin.on_group_message(event)

        plugin._apply_reaction_expression_feedback.assert_called_once_with(
            user,
            event.message_str,
            scope_key=scope_key,
        )
        plugin._persist_reaction_expression_state.assert_called_once_with()
        plugin._capture_group_observation_event.assert_awaited_once()

    async def test_high_priority_observer_only_records_and_does_not_stop_event(self) -> None:
        plugin = PrivateCompanionPlugin.__new__(PrivateCompanionPlugin)
        event = _GroupEvent()
        plugin._feature_enabled_or_temp_unlocked = Mock(return_value=True)
        plugin._extract_group_id_from_event = Mock(return_value="group-1")
        plugin._group_enabled_for_event = Mock(return_value=True)
        plugin._event_self_id = Mock(return_value="bot-1")
        plugin._group_observation_event_text = Mock(return_value="群消息")
        plugin._message_debounce_command_text = Mock(return_value=False)
        plugin._sender_display_name = Mock(return_value="用户")
        plugin._capture_group_observation_event = AsyncMock(return_value=True)

        await plugin.capture_group_observation_early(event)

        plugin._capture_group_observation_event.assert_awaited_once()
        self.assertFalse(event._stopped)

    def test_observer_priority_and_group_form_default_are_explicit(self) -> None:
        source = (ROOT / "main.py").read_text(encoding="utf-8")
        html = (ROOT / "pages" / "陪伴面板" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "pages" / "陪伴面板" / "app.js").read_text(encoding="utf-8")

        self.assertIn("GROUP_MESSAGE, priority=200000", source)
        self.assertIn('<option value="whitelist">加入白名单并开始观察</option>', html)
        self.assertIn("仅创建群资料（暂不观察）", html)
        self.assertIn("access_warning", script)
        self.assertIn("名单未放行", script)


if __name__ == "__main__":
    unittest.main()
