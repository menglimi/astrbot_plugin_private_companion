# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import unittest
from datetime import datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from astrbot.api.message_components import Plain, Record

from astrbot_plugin_private_companion.main import PrivateCompanionPlugin
from astrbot_plugin_private_companion.private_image import PrivateImageMixin
from astrbot_plugin_private_companion.tts_enhancement import TtsEnhancementMixin


class _LifecycleHarness:
    _create_lifecycle_background_task = PrivateCompanionPlugin._create_lifecycle_background_task
    _cancel_lifecycle_background_tasks = PrivateCompanionPlugin._cancel_lifecycle_background_tasks
    terminate = PrivateCompanionPlugin.terminate

    def __init__(self) -> None:
        self._stop_event = asyncio.Event()
        self._lifecycle_background_tasks: dict[asyncio.Task, str] = {}
        self._proactive_chat_runtime_bridge = None
        self._task = None
        self._passive_input_status_tasks = {}
        self._startup_maintenance_task = None
        self._startup_background_tasks = {}
        self._group_image_understanding_tasks = {}
        self._troubleshooting_proactive_wakeup_tasks = {}
        self._data_save_task = None
        self.cancelled = False
        self.sent = False

    async def _flush_scheduled_data_save(self) -> None:
        return None

    async def _save_data_on_terminate(self) -> None:
        return None


class _PrivateImageRemainderHarness(PrivateImageMixin):
    def __init__(self) -> None:
        self.enable_segmented_proactive_reply = True
        self.segmented_proactive_scope = "all_llm"
        self.sent: list[list[str]] = []
        self.background_jobs: list[tuple[Any, str]] = []

    @staticmethod
    def _normalize_private_image_reply_text(text: str) -> str:
        return text

    @staticmethod
    async def _private_image_reply_chain(_text: str, _event: Any) -> list[str]:
        return ["whole"]

    @staticmethod
    def _private_image_split_reply_chain(_chain: list[str], *, should_segment: bool) -> list[list[str]]:
        assert should_segment
        return [["first"], ["second"]]

    async def _send_private_image_reply_chain(self, _event: Any, chain: list[str]) -> None:
        self.sent.append(list(chain))

    @staticmethod
    def _private_image_chain_text(chain: list[str]) -> str:
        return "".join(chain)

    @staticmethod
    def _private_image_context_assistant_message(text: str) -> str:
        return text

    def _create_lifecycle_background_task(self, operation: Any, *, label: str) -> None:
        self.background_jobs.append((operation, label))


class _TtsRemainderHarness(TtsEnhancementMixin):
    _reaction_expression_flatten_delivery_components = staticmethod(
        PrivateCompanionPlugin._reaction_expression_flatten_delivery_components
    )
    _reaction_expression_delivery_signature = staticmethod(
        PrivateCompanionPlugin._reaction_expression_delivery_signature
    )
    _reaction_expression_primary_reply_confirmed = (
        PrivateCompanionPlugin._reaction_expression_primary_reply_confirmed
    )
    release_tts_reply_remainder_after_send = (
        PrivateCompanionPlugin.release_tts_reply_remainder_after_send
    )

    def __init__(self) -> None:
        self.enabled = True
        self.enable_tts_enhancement = True
        self.tts_generation_mode = "fast_tag"
        self._reply_turn_generation_by_scope = {}
        self.background_jobs: list[tuple[Any, str]] = []

    @staticmethod
    def _feature_enabled_or_temp_unlocked(key: str) -> bool:
        return key == "enable_tts_enhancement"

    @staticmethod
    def _restore_protected_tts_blocks(text: str, _event: Any) -> str:
        return text

    @staticmethod
    def _normalize_tts_tags(text: str) -> str:
        return text

    @staticmethod
    async def _maybe_convert_plain_reply_to_tts(_text: str, _event: Any) -> list[Any]:
        return [Plain("converted")]

    @staticmethod
    def _tts_record_first_visible_last_chain(chain: list[Any]) -> list[Any]:
        return chain

    @staticmethod
    def _split_tts_chain_for_ordered_send(_chain: list[Any]) -> list[list[Any]]:
        return [[Record(file="voice.wav")], [Plain("caption")]]

    @staticmethod
    def _tts_segment_plain_chunk_for_ordered_send(_event: Any, chunk: list[Any]) -> list[list[Any]]:
        return [chunk]

    @staticmethod
    def _event_inbound_activity_ts(_event: Any) -> float:
        return 1.0

    @staticmethod
    def _build_result_from_chain(chain: list[Any]) -> Any:
        return SimpleNamespace(chain=list(chain))

    def _create_lifecycle_background_task(self, operation: Any, *, label: str) -> None:
        self.background_jobs.append((operation, label))

    def _create_tts_background_task(self, operation: Any, *, label: str) -> None:
        self.background_jobs.append((operation, label))

    _reply_turn_generation = PrivateCompanionPlugin._reply_turn_generation
    _reply_turn_is_current = PrivateCompanionPlugin._reply_turn_is_current

    @staticmethod
    def _event_scope_key(event: Any) -> str:
        return str(getattr(event, "unified_msg_origin", "") or "")


class _ReplyInterceptionHarness:
    _schedule_reply_interception_forward = PrivateCompanionPlugin._schedule_reply_interception_forward
    _send_reply_interception_forward = PrivateCompanionPlugin._send_reply_interception_forward

    def __init__(self) -> None:
        self.enable_reply_interception_forward = True
        self.reply_interception_forward_plugin_blocks = True
        self.reply_interception_forward_rewrites = True
        self.reply_interception_forward_proactive_blocks = True
        self.reply_interception_forward_target_umo = "default:FriendMessage:2"
        self.background_jobs: list[tuple[Any, str]] = []

    @staticmethod
    def _environment_now() -> datetime:
        return datetime(2026, 7, 23, 18, 0, 0)

    def _create_lifecycle_background_task(self, operation: Any, *, label: str) -> None:
        self.background_jobs.append((operation, label))


class BackgroundTaskLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_terminate_cancels_registered_delayed_send(self) -> None:
        harness = _LifecycleHarness()
        started = asyncio.Event()

        async def delayed_send() -> None:
            started.set()
            try:
                await asyncio.sleep(3600)
                harness.sent = True
            except asyncio.CancelledError:
                harness.cancelled = True
                raise

        task = harness._create_lifecycle_background_task(
            delayed_send(),
            label="test_delayed_send",
        )
        self.assertIsInstance(task, asyncio.Task)
        await started.wait()

        await harness.terminate()

        self.assertTrue(harness._stop_event.is_set())
        self.assertTrue(harness.cancelled)
        self.assertFalse(harness.sent)
        self.assertTrue(task.cancelled())
        self.assertEqual({}, harness._lifecycle_background_tasks)

    async def test_stopped_plugin_does_not_accept_new_background_task(self) -> None:
        harness = _LifecycleHarness()
        harness._stop_event.set()
        operation = asyncio.sleep(0)

        task = harness._create_lifecycle_background_task(operation, label="too_late")

        self.assertIsNone(task)
        self.assertIsNone(operation.cr_frame)
        self.assertEqual({}, harness._lifecycle_background_tasks)

    async def test_private_image_remainder_uses_lifecycle_tracker(self) -> None:
        harness = _PrivateImageRemainderHarness()

        first_text = await harness._send_private_image_reply_text(SimpleNamespace(), "reply")

        self.assertEqual("first", first_text)
        self.assertEqual([["first"]], harness.sent)
        self.assertEqual("private_image_reply_remainder", harness.background_jobs[0][1])
        harness.background_jobs[0][0].close()

    @staticmethod
    def _tts_remainder_event(*, proactive: bool = False) -> Any:
        sent: list[Any] = []
        event = SimpleNamespace(
            _private_companion_tts_request_applied=True,
            unified_msg_origin="default:FriendMessage:1",
            result=SimpleNamespace(chain=[Plain("reply")]),
            sent=sent,
        )
        if proactive:
            event._private_companion_proactive_delivery_umo = (
                "default:FriendMessage:1"
            )
        event.set_result = lambda result: setattr(event, "result", result)
        event.get_result = lambda: event.result
        event.chain_result = lambda chain: SimpleNamespace(chain=list(chain))

        async def send(result: Any) -> None:
            sent.append(result)

        event.send = send
        return event

    async def test_passive_tts_remainder_waits_for_platform_send_confirmation(self) -> None:
        harness = _TtsRemainderHarness()
        event = self._tts_remainder_event()

        await harness.apply_tts_enhancement_before_send(event)

        self.assertIsInstance(event.result.chain[0], Record)
        self.assertEqual([], harness.background_jobs)
        self.assertEqual(
            "caption",
            event._private_companion_tts_reply_remainder["chunks"][0][0].text,
        )

        event._has_send_oper = True
        await harness.release_tts_reply_remainder_after_send(event)

        self.assertEqual("tts_reply_remainder", harness.background_jobs[0][1])
        self.assertFalse(hasattr(event, "_private_companion_tts_reply_remainder"))

        await harness.release_tts_reply_remainder_after_send(event)
        self.assertEqual(1, len(harness.background_jobs))
        harness.background_jobs[0][0].close()

    async def test_passive_tts_remainder_is_not_released_when_platform_did_not_send(
        self,
    ) -> None:
        harness = _TtsRemainderHarness()
        event = self._tts_remainder_event()

        await harness.apply_tts_enhancement_before_send(event)
        event._has_send_oper = False
        await harness.release_tts_reply_remainder_after_send(event)

        self.assertEqual([], harness.background_jobs)
        self.assertFalse(hasattr(event, "_private_companion_tts_reply_remainder"))

    async def test_passive_tts_remainder_is_not_released_after_partial_primary_send(
        self,
    ) -> None:
        harness = _TtsRemainderHarness()
        event = SimpleNamespace(
            _has_send_oper=True,
            _private_companion_tts_reply_remainder={
                "chunks": [[Plain("余下正文")]],
                "started_at": 1.0,
            },
            _private_companion_reaction_expression_delivery_tracker={
                "successful_signatures": [("plain", "第一段。")],
            },
            get_result=lambda: SimpleNamespace(
                chain=[Plain("第一段。"), Plain("第二段。")]
            ),
        )

        await harness.release_tts_reply_remainder_after_send(event)

        self.assertEqual([], harness.background_jobs)
        self.assertFalse(hasattr(event, "_private_companion_tts_reply_remainder"))

    async def test_proactive_tts_remainder_starts_immediately(self) -> None:
        harness = _TtsRemainderHarness()
        event = self._tts_remainder_event(proactive=True)

        await harness.apply_tts_enhancement_before_send(event)

        self.assertIsInstance(event.result.chain[0], Record)
        self.assertEqual("tts_reply_remainder", harness.background_jobs[0][1])
        self.assertFalse(hasattr(event, "_private_companion_tts_reply_remainder"))
        harness.background_jobs[0][0].close()

    async def test_passive_tts_remainder_is_released_after_generation_state_reset(self) -> None:
        harness = _TtsRemainderHarness()
        event = self._tts_remainder_event()
        event._private_companion_reply_turn_generation = 1
        event._has_send_oper = True
        harness._reply_turn_generation_by_scope[event.unified_msg_origin] = 1

        await harness.apply_tts_enhancement_before_send(event)
        harness._reply_turn_generation_by_scope.clear()
        await harness.release_tts_reply_remainder_after_send(event)

        self.assertEqual("tts_reply_remainder", harness.background_jobs[0][1])
        with patch(
            "astrbot_plugin_private_companion.tts_enhancement.asyncio.sleep",
            new=AsyncMock(),
        ):
            await harness.background_jobs[0][0]
        self.assertEqual(["caption"], [item.chain[0].text for item in event.sent])

    async def test_committed_tts_remainder_survives_new_turn_during_wait(self) -> None:
        harness = _TtsRemainderHarness()
        event = self._tts_remainder_event()
        event._private_companion_reply_turn_generation = 1
        harness._reply_turn_generation_by_scope[event.unified_msg_origin] = 1
        event._has_send_oper = True

        await harness.apply_tts_enhancement_before_send(event)
        await harness.release_tts_reply_remainder_after_send(event)

        async def note_new_turn(_delay: float) -> None:
            harness._reply_turn_generation_by_scope[event.unified_msg_origin] = 2

        with patch(
            "astrbot_plugin_private_companion.tts_enhancement.asyncio.sleep",
            new=AsyncMock(side_effect=note_new_turn),
        ):
            await harness.background_jobs[0][0]
        self.assertEqual(["caption"], [item.chain[0].text for item in event.sent])

    async def test_unconfirmed_proactive_tts_remainder_still_stops_after_new_turn(self) -> None:
        harness = _TtsRemainderHarness()
        event = self._tts_remainder_event(proactive=True)
        event._private_companion_reply_turn_generation = 1
        harness._reply_turn_generation_by_scope[event.unified_msg_origin] = 1

        await harness.apply_tts_enhancement_before_send(event)

        async def note_new_turn(_delay: float) -> None:
            harness._reply_turn_generation_by_scope[event.unified_msg_origin] = 2

        with patch(
            "astrbot_plugin_private_companion.tts_enhancement.asyncio.sleep",
            new=AsyncMock(side_effect=note_new_turn),
        ):
            await harness.background_jobs[0][0]
        self.assertEqual([], event.sent)

    async def test_reply_interception_forward_uses_lifecycle_tracker(self) -> None:
        harness = _ReplyInterceptionHarness()

        harness._schedule_reply_interception_forward(
            "plugin_block",
            source="test",
            reason="blocked",
            source_session="default:FriendMessage:1",
        )

        self.assertEqual("reply_interception_forward", harness.background_jobs[0][1])
        harness.background_jobs[0][0].close()


if __name__ == "__main__":
    unittest.main()
