# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from astrbot.api.message_components import Plain

from astrbot_plugin_private_companion.event_dispatch import EventDispatchMixin
from astrbot_plugin_private_companion.main import PrivateCompanionPlugin


class _ActivityHarness(EventDispatchMixin):
    @staticmethod
    def _event_scope_key(_event) -> str:
        return "group:10001"


class _ActivityEvent:
    unified_msg_origin = "default:GroupMessage:10001"

    def __init__(self, raw: dict, *, sender_id: str = "user", self_id: str = "bot") -> None:
        self.message_obj = SimpleNamespace(raw_message=raw, self_id=self_id)
        self.sender_id = sender_id
        self.self_id = self_id

    def get_sender_id(self) -> str:
        return self.sender_id

    def get_self_id(self) -> str:
        return self.self_id


class _RemainderEvent:
    unified_msg_origin = "default:GroupMessage:10001"

    def __init__(self) -> None:
        self.sent: list[object] = []

    @staticmethod
    def chain_result(chain):
        return SimpleNamespace(chain=list(chain))

    async def send(self, result) -> None:
        self.sent.append(result)


class SegmentedRemainderActivityTests(unittest.IsolatedAsyncioTestCase):
    def test_outbound_events_do_not_advance_inbound_activity(self) -> None:
        harness = _ActivityHarness()

        sent_event = _ActivityEvent(
            {"post_type": "message_sent", "self_id": "bot"},
            sender_id="bot",
        )
        harness._note_inbound_activity_for_scope(sent_event)
        self.assertFalse(hasattr(harness, "_recent_inbound_activity_by_scope"))

        self_event = _ActivityEvent(
            {"post_type": "message", "self_id": "bot"},
            sender_id="bot",
        )
        harness._note_inbound_activity_for_scope(self_event)
        self.assertFalse(hasattr(harness, "_recent_inbound_activity_by_scope"))

        inbound_event = _ActivityEvent(
            {"post_type": "message", "self_id": "bot"},
            sender_id="user",
        )
        harness._note_inbound_activity_for_scope(inbound_event)
        self.assertIn("group:10001", harness._recent_inbound_activity_by_scope)

    @staticmethod
    def _build_remainder_harness(*, activity: bool = True):
        plugin = object.__new__(PrivateCompanionPlugin)
        plugin.enable_tts_enhancement = False
        plugin.segmented_proactive_scope = "all_llm"
        plugin._event_scope_key = lambda _event: "group:10001"
        plugin._event_inbound_activity_ts = lambda _event: 1.0
        plugin._segmented_remainder_context_drift_reason = lambda *_args, **_kwargs: ""
        plugin._calc_segmented_proactive_interval = AsyncMock(return_value=0.0)
        plugin._scope_has_new_inbound_activity = lambda *_args, **_kwargs: activity
        plugin._should_cancel_reply_for_missing_or_recalled_trigger = AsyncMock(return_value="")
        plugin._sanitize_segmented_plain_text = lambda _event, text: str(text or "")
        plugin._strip_plaintext_tool_call_envelopes = lambda text: (str(text or ""), [])
        plugin._chain_text_for_forbidden_recall = lambda _chain: ""
        plugin._forbidden_recall_hit = lambda _text: ""
        plugin._segmented_chunk_log_text = lambda chunk: "".join(
            str(getattr(item, "text", "") or "") for item in chunk
        )
        return plugin

    async def test_reaction_expression_remainder_is_sent_in_full(self) -> None:
        plugin = self._build_remainder_harness()
        event = _RemainderEvent()
        chunks = [[Plain("第二段。")], [Plain("第三段。")], [Plain("第四段。")]]

        await PrivateCompanionPlugin._send_segmented_llm_chain_remainder(
            plugin,
            event,
            chunks,
            previous_segment="第一段。",
            source="reaction_expression",
            started_at=1.0,
        )

        self.assertEqual(
            ["第二段。", "第三段。", "第四段。"],
            [result.chain[0].text for result in event.sent],
        )

    async def test_regular_remainder_is_not_stopped_by_new_activity(self) -> None:
        plugin = self._build_remainder_harness()
        event = _RemainderEvent()

        await PrivateCompanionPlugin._send_segmented_llm_chain_remainder(
            plugin,
            event,
            [[Plain("第二段。")], [Plain("第三段。")]],
            previous_segment="第一段。",
            source="decorating_result",
            started_at=1.0,
        )

        self.assertEqual(
            ["第二段。", "第三段。"],
            [result.chain[0].text for result in event.sent],
        )

    async def test_proactive_remainder_uses_live_platform_sender_after_event_finishes(self) -> None:
        plugin = self._build_remainder_harness()
        plugin._send_chain_components = AsyncMock(return_value=True)
        event = _RemainderEvent()
        event._private_companion_external_proactive_source = "proactive_chat"

        await PrivateCompanionPlugin._send_segmented_llm_chain_remainder(
            plugin,
            event,
            [[Plain("第二段。")], [Plain("第三段。")]],
            previous_segment="第一段。",
            source="decorating_result",
            started_at=1.0,
        )

        self.assertEqual([], event.sent)
        self.assertEqual(2, plugin._send_chain_components.await_count)
        first_call = plugin._send_chain_components.await_args_list[0]
        self.assertEqual("default:GroupMessage:10001", first_call.args[0])
        self.assertEqual("第二段。", first_call.args[1][0].text)
        self.assertFalse(first_call.kwargs["apply_decorating_hooks"])

    async def test_synthetic_proactive_remainder_uses_delivery_umo_platform_sender(self) -> None:
        plugin = self._build_remainder_harness()
        plugin._send_chain_components = AsyncMock(return_value=True)
        event = _RemainderEvent()
        event._private_companion_proactive_delivery_umo = "default:GroupMessage:10001"

        await PrivateCompanionPlugin._send_segmented_llm_chain_remainder(
            plugin,
            event,
            [[Plain("第二段。")], [Plain("第三段。")]],
            previous_segment="第一段。",
            source="decorating_result",
            started_at=1.0,
        )

        self.assertEqual([], event.sent)
        self.assertEqual(2, plugin._send_chain_components.await_count)
        first_call = plugin._send_chain_components.await_args_list[0]
        self.assertEqual("default:GroupMessage:10001", first_call.args[0])
        self.assertEqual("第二段。", first_call.args[1][0].text)
        self.assertFalse(first_call.kwargs["apply_decorating_hooks"])

    async def test_synthetic_proactive_remainder_does_not_fallback_to_fake_event(self) -> None:
        plugin = self._build_remainder_harness()
        plugin._send_chain_components = AsyncMock(return_value=False)
        event = _RemainderEvent()
        event._private_companion_proactive_delivery_umo = "default:GroupMessage:10001"

        await PrivateCompanionPlugin._send_segmented_llm_chain_remainder(
            plugin,
            event,
            [[Plain("第二段。")]],
            previous_segment="第一段。",
            source="decorating_result",
            started_at=1.0,
        )

        self.assertEqual([], event.sent)
        plugin._send_chain_components.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
