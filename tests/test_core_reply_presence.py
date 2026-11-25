"""Typing spans native generation, tool use and the final segmented delivery."""

import asyncio
from dataclasses import asdict
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, patch

from tests.test_main_no_reply import ENTRY
from tests.test_core_reply_pipeline import PipelineEvent
from tests.test_role_presence import FakePresence
from tests.test_role_state import StateFixture
from tests.multi_host_support import MultiFixture
from astrbot.core.message.message_event_result import MessageEventResult, ResultContentType
from astrbot_host.core_reply import (
    CORE_REPLY_CONTEXT_EXTRA, activate_core_reply_delivery_guard,
    prepare_core_reply_event, restore_core_reply_event,
)
from astrbot_host.core_reply_presence import agent_presence_begin, agent_presence_done, result_presence
from astrbot_host.core_reply_segmentation import prepare_core_reply_segmentation
from companion_v2.conversation.native_turns import NativeConversationTurns
from companion_v2.conversation.reset import ConversationWindow, reset_window
from companion_v2.conversation.segmentation import SegmentationPolicy
from companion_v2.foundation.outcome import Outcome


class NativeTypingTests(StateFixture, unittest.IsolatedAsyncioTestCase):
    async def prepare(self):
        self.port = FakePresence()
        host, self.app, self.context, manager = self.setup_state(presence_port=self.port)
        self.configure(manager, typing_enabled=True, typing_refresh_seconds=10)
        await self.start(host)
        self.turn_id = "core-reply:typing-test"
        self.prepared = {"context": self.context, "turn_id": self.turn_id,
            "instruction": "自然聊天", "channel": "private", "event_id": "typing-test",
            "segmentation_settings": {**asdict(SegmentationPolicy(
                rule_mode="paragraphs", min_segment_chars=1)), "part_gap_mode": "fixed", "part_gap_ms": 100}}
        self.app.presence.begin_native_reply(self.context, self.turn_id)
        await self.until(lambda: bool(self.port.pulses))
        self.event = PipelineEvent()
        prepare_core_reply_event(self.event, self.prepared,
            authorize=AsyncMock(return_value=Outcome.success(True)),
            on_delivery=lambda stage: self.app.presence.native_reply_stage(
                self.context, self.turn_id, "delivering") if stage == "returned" else None,
            on_reply_end=lambda: self.app.presence.finish_native_reply(self.context, self.turn_id))
        agent_presence_begin(self.event)
        return self.app.presence.typing[self.app.presence.route(self.context)][2]

    async def test_native_typing_starts_before_observation_and_covers_last_part_gap(self):
        worker = await self.prepare()
        turns = NativeConversationTurns(self.app.runtime)
        self.assertIsNone(self.app.runtime.read_one("SELECT * FROM conversation_native_turns", ()))
        turns.mark(self.prepared, "completed", at=self.clock.now())
        response = NS(role="assistant", completion_text="第一段\n\n第二段")
        agent_presence_done(self.event, response)
        self.assertFalse(worker.done())
        self.assertIsNotNone(self.app.presence.active_turn(self.context))
        result = MessageEventResult().message(response.completion_text).set_result_content_type(ResultContentType.LLM_RESULT)
        self.event.set_result(result)
        activate_core_reply_delivery_guard(self.event)
        prepare_core_reply_segmentation(self.event)
        entered, release = asyncio.Event(), asyncio.Event()

        async def part_gap(_seconds):
            entered.set()
            await release.wait()

        with patch("astrbot_host.core_reply_segmentation.asyncio.sleep", side_effect=part_gap):
            sending = asyncio.create_task(self.event.send(result))
            await asyncio.wait_for(entered.wait(), 2)
            self.assertEqual(len(self.event.sent), 1)
            self.assertFalse(worker.done())
            release.set()
            await sending
        self.assertEqual(len(self.event.sent), 2)
        self.assertIs(self.app.presence.typing[self.app.presence.route(self.context)][2], worker)
        restore_core_reply_event(self.event)
        await self.until(worker.done)
        self.app.presence.advance()
        self.assertIsNone(self.app.presence.active_turn(self.context))
        self.assertEqual(self.app.presence.active_handles, ())
        # Delayed native observation writes cannot reopen the finished lease.
        self.app.presence.advance()
        self.assertEqual(self.app.presence.active_handles, ())

    async def test_intermediate_send_and_empty_final_close_only_at_runner_end(self):
        worker = await self.prepare()
        restore_core_reply_event(self.event)
        self.assertFalse(worker.done())
        agent_presence_done(self.event, NS(role="assistant", completion_text=""))
        await self.until(worker.done)
        self.assertIsNone(self.app.presence.active_turn(self.context))

    async def test_stream_keeps_typing_after_agent_done_until_last_chunk(self):
        worker = await self.prepare()
        result = MessageEventResult().set_result_content_type(ResultContentType.STREAMING_RESULT)
        self.event.set_result(result)
        entered, release = asyncio.Event(), asyncio.Event()

        async def chunks():
            yield MessageEventResult().message("前半句")
            agent_presence_done(self.event, NS(role="assistant", completion_text="前半句后半句"))
            entered.set()
            await release.wait()
            yield MessageEventResult().message("后半句")

        sending = asyncio.create_task(self.event.send_streaming(chunks()))
        await asyncio.wait_for(entered.wait(), 2)
        self.assertFalse(worker.done())
        release.set()
        await sending
        await self.until(worker.done)
        self.assertIsNone(self.app.presence.active_turn(self.context))

    async def test_cancellation_inside_part_gap_closes_typing(self):
        worker = await self.prepare()
        self.event.set_result(MessageEventResult().set_result_content_type(ResultContentType.LLM_RESULT))
        activate_core_reply_delivery_guard(self.event)
        prepare_core_reply_segmentation(self.event)
        gap = asyncio.Event()

        async def part_gap(_seconds):
            gap.set()
            await asyncio.Event().wait()

        with patch("astrbot_host.core_reply_segmentation.asyncio.sleep", side_effect=part_gap):
            sending = asyncio.create_task(self.event.send(MessageEventResult().message("第一段\n\n第二段")))
            await asyncio.wait_for(gap.wait(), 2)
            sending.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await sending
        await self.until(worker.done)
        self.assertIsNone(self.app.presence.active_turn(self.context))

    async def test_overlapping_turn_completion_and_reset_do_not_leave_typing(self):
        worker = await self.prepare()
        self.app.presence.begin_native_reply(self.context, "newer-turn")
        self.app.presence.finish_native_reply(self.context, self.turn_id)
        self.assertEqual(self.app.presence.active_turn(self.context)["batch_id"], "newer-turn")
        self.assertFalse(worker.done())
        reset_window(self.app.runtime, ConversationWindow.from_scope(self.context.scope),
                     reset_id="typing-reset", at=self.clock.now())
        self.app.presence.advance()
        await self.until(worker.done)
        self.assertIsNone(self.app.presence.active_turn(self.context))


class NativeTypingCallbacksTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_message_task_cancellation_closes_even_without_final_hooks(self):
        event, ended, ready = PipelineEvent(), Mock(), asyncio.Event()

        async def message_task():
            prepare_core_reply_event(event, {}, authorize=AsyncMock(), on_reply_end=ended)
            agent_presence_begin(event)
            ready.set()
            await asyncio.Event().wait()

        worker = asyncio.create_task(message_task())
        await ready.wait()
        worker.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await worker
        ended.assert_called_once()

    async def test_main_installs_end_callback_and_keeps_generation_completion_separate(self):
        event = PipelineEvent()
        presence = Mock()
        prepared = {"context": object(), "turn_id": "native-callback-test",
            "instruction": "", "target_id": "target", "event_id": "callback", "channel": "private"}
        target = NS(target_id="target")
        plugin = ENTRY.PrivateCompanionV2StagingPlugin.__new__(ENTRY.PrivateCompanionV2StagingPlugin)
        plugin.staging = NS(claims=lambda _: True, runtime=None, now=lambda: None,
            target_for_event=lambda _: target,
            session_topology=NS(reply_enabled=AsyncMock(return_value=True), schedule_observe=Mock()),
            prepare_core_reply=AsyncMock(return_value=Outcome.success(prepared)),
            authorize_core_reply=AsyncMock(return_value=Outcome.success(True)),
            session=NS(application=NS(presence=presence)))
        await plugin.receive_companion_message(event)
        state = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
        self.assertTrue(callable(state["on_reply_end"]))
        agent_presence_begin(event)
        await plugin.observe_companion_core_agent_done(event, NS(), NS(role="assistant", completion_text="待发送"))
        presence.finish_native_reply.assert_not_called()
        event.set_result(MessageEventResult().message("待发送").set_result_content_type(ResultContentType.LLM_RESULT))
        await plugin.guard_companion_core_delivery(event)
        await event.send(event.get_result())
        await plugin.observe_companion_core_reply_sent(event)
        presence.finish_native_reply.assert_called_once_with(prepared["context"], prepared["turn_id"])
        presence.native_reply_stage.assert_called_with(prepared["context"], prepared["turn_id"], "delivering")
        presence.note_native_reply_sent.assert_called_once_with(prepared["context"])

    async def test_empty_decorated_and_deduplicated_finals_end_without_after_send(self):
        for text, sent in (("", []), ("工具已发", ["工具已发"])):
            event, ended = PipelineEvent(), Mock()
            prepare_core_reply_event(event, {}, authorize=AsyncMock(), on_reply_end=ended)
            event.set_extra("_send_message_to_user_current_session_plain_texts", sent)
            event.set_result(MessageEventResult().message(text).set_result_content_type(ResultContentType.LLM_RESULT))
            result_presence(event)
            ended.assert_called_once()


class NativeTypingPreparationTests(MultiFixture, unittest.IsolatedAsyncioTestCase):
    async def test_slow_preparation_already_holds_lease_and_cancellation_releases_it(self):
        app = await self.start_host()
        for cancel in (False, True):
            entered, release = asyncio.Event(), asyncio.Event()

            async def direction(*_args):
                entered.set()
                await release.wait()
                return ""

            with patch("astrbot_host.fish_audio.provider_direction", side_effect=direction):
                preparing = asyncio.create_task(self.controller.prepare_core_reply(
                    self.event(identifier="slow-typing-" + str(cancel))))
                await asyncio.wait_for(entered.wait(), 2)
                lease = next(iter(app.presence.native_replies.values()))
                context = lease["context"]
                self.assertIsNotNone(app.presence.active_turn(context))
                if cancel:
                    preparing.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await preparing
                else:
                    release.set()
                    prepared = (await preparing).unwrap()
                    app.presence.finish_native_reply(context, prepared["turn_id"])
                self.assertIsNone(app.presence.active_turn(context))
        # Optional presentation remains disabled: preparing alone never sends.
        self.assertEqual(self.native.platforms["qq-main"].client._api._wsr_api.calls, [])
