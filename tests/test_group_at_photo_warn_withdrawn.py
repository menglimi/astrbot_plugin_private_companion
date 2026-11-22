"""Regression tests for photo_reference disabled log level, bare [At] group intake,
and clean silent suppression of pre-send ``message_withdrawn`` denials.
"""

from __future__ import annotations

from tests import astrbot_data_root  # noqa: F401 - isolate SDK data root before import

from datetime import datetime, timezone
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, patch

from tests.test_main_no_reply import ENTRY
from astrbot.core.message.components import At, Plain
from astrbot.core.message.message_event_result import MessageChain, MessageEventResult, ResultContentType
from astrbot_host import core_reply_image_prompt as image_prompt_mod
from astrbot_host.core_reply import (
    CORE_REPLY_CONTEXT_EXTRA,
    activate_core_reply_delivery_guard,
    prepare_core_reply_event,
)
from astrbot_host.core_reply_segmentation import (
    activate_core_reply_segmentation,
    prepare_core_reply_segmentation,
)
from companion_v2.adapters.astrbot.group import NativeGroupBridge
from companion_v2.adapters.astrbot.group_mentions import group_message_parts
from companion_v2.conversation.group_timing import SELF_MENTION
from companion_v2.execution.models import TaskError
from companion_v2.foundation.outcome import Outcome


class PhotoReferenceDisabledLogTests(unittest.TestCase):
    def _build_session(self, error: Exception):
        owner = NS(prompt_material=Mock(side_effect=error))
        now_dt = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
        return NS(
            application=NS(
                runtime=None,
                messages=NS(reference_materials=owner, timezone_name="Asia/Shanghai"),
                tasks=NS(now=lambda: now_dt),
                reply_settings=None,
                native_tool_port=None,
            ),
            context=NS(scope=NS(kind=NS(value="group"))),
            prepared=None,
            state={},
            image_locators=(),
        )

    def test_expected_disabled_codes_log_at_debug_not_warning(self):
        offered = (NS(spec=NS(action="image.edit")),)
        values = {"trigger_mode": "autonomous"}
        for code in ("photo_reference_group_disabled", "photo_reference_disabled"):
            with self.subTest(code=code):
                session = self._build_session(TaskError(code, "disabled by config"))
                request = NS(system_prompt="")
                with patch.object(image_prompt_mod, "prompt_instruction", return_value=""), \
                        patch.object(image_prompt_mod, "set_system_instruction"), \
                        patch.object(image_prompt_mod.logger, "warning") as warn, \
                        patch.object(image_prompt_mod.logger, "debug") as debug:
                    image_prompt_mod.inject_core_image_prompt(session, request, offered, values)
                warn.assert_not_called()
                debug.assert_called_once()
                self.assertIn(code, debug.call_args.args)

    def test_unexpected_reference_errors_still_log_warning(self):
        offered = (NS(spec=NS(action="image.edit")),)
        values = {"trigger_mode": "autonomous"}
        session = self._build_session(RuntimeError("boom"))
        request = NS(system_prompt="")
        with patch.object(image_prompt_mod, "prompt_instruction", return_value=""), \
                patch.object(image_prompt_mod, "set_system_instruction"), \
                patch.object(image_prompt_mod.logger, "warning") as warn, \
                patch.object(image_prompt_mod.logger, "debug") as debug:
            image_prompt_mod.inject_core_image_prompt(session, request, offered, values)
        warn.assert_called_once()
        debug.assert_not_called()


class GroupBareAtIntakeTests(unittest.IsolatedAsyncioTestCase):
    def _group_event(self, raw_segments, native_parts, *, message_str=""):
        result = MessageEventResult().message("")
        event = NS(
            message_obj=NS(
                type=NS(value="GroupMessage"),
                message_id="msg-1",
                self_id="100",
                session_id="9001",
                message=list(native_parts),
                message_str=message_str,
                raw_message={
                    "post_type": "message",
                    "message_type": "group",
                    "self_id": 100,
                    "user_id": 200,
                    "group_id": 9001,
                    "message_id": "msg-1",
                    "sender": {"user_id": 200},
                    "message": list(raw_segments),
                },
            ),
            platform_meta=NS(id="qq-main", name="aiocqhttp"),
            session=NS(platform_id="qq-main", platform_name="qq-main",
                       session_id="9001", message_type=NS(value="GroupMessage")),
            unified_msg_origin="qq-main:GroupMessage:9001",
            message_str=message_str,
            is_wake=False,
            is_at_or_wake_command=False,
            _has_send_oper=False,
            extra={},
            _result=result,
        )
        event.get_extra = lambda key, default=None: event.extra.get(key, default)
        event.set_extra = lambda key, value: event.extra.__setitem__(key, value)
        event.get_result = lambda: event._result
        event.set_result = lambda value: setattr(event, "_result", value)
        event.stop_event = Mock()
        event.send = AsyncMock(return_value="sent")
        event.send_streaming = AsyncMock(return_value="streamed")
        return event

    def test_pure_at_self_skeleton_survives_raw_and_stripped_native_parts(self):
        route = NS(account_id="100")
        event = self._group_event(
            [{"type": "at", "data": {"qq": "100"}}],
            [At(qq="100")],
        )
        self.assertEqual(NativeGroupBridge._text_only_parts(event, route), SELF_MENTION)

        # Even if AstrBot stripped At from message_obj.message leaving Plain(""),
        # the authenticated raw OneBot envelope still preserves the self-mention.
        stripped = self._group_event(
            [{"type": "at", "data": {"qq": "100"}}],
            [Plain("")],
        )
        self.assertEqual(NativeGroupBridge._text_only_parts(stripped, route), SELF_MENTION)

        # QQ Official pure <@BOT> with no Plain component keeps SELF_MENTION.
        official = NS(
            platform_meta=NS(name="qq_official"),
            message_obj=NS(
                self_id="qq_official",
                raw_message={"content": "<@BOT>", "mentions": [{"id": "BOT", "is_you": True}]},
                message=[At(qq="qq_official")],
            ),
        )
        parts = group_message_parts(official, "APP")
        self.assertTrue(any(getattr(p, "text", "") == SELF_MENTION for p in parts))

    def test_pure_at_other_and_at_other_with_image_raise_host_text_invalid(self):
        route = NS(account_id="100")
        pure_other = self._group_event(
            [{"type": "at", "data": {"qq": "201"}}],
            [At(qq="201")],
        )
        with self.assertRaises(TaskError) as err1:
            NativeGroupBridge._text_only_parts(pure_other, route)
        self.assertEqual(err1.exception.code, "host_text_invalid")

        other_with_image = self._group_event(
            [{"type": "at", "data": {"qq": "201"}},
             {"type": "image", "data": {"file": "a.jpg", "sub_type": 0}}],
            [At(qq="201"), NS(type="Image", file="a.jpg", url="")],
        )
        with self.assertRaises(TaskError) as err2:
            NativeGroupBridge._text_only_parts(other_with_image, route)
        self.assertEqual(err2.exception.code, "host_text_invalid")

    async def test_main_bare_at_self_sets_placeholder_and_disables_streaming(self):
        plugin = ENTRY.PrivateCompanionV2StagingPlugin.__new__(ENTRY.PrivateCompanionV2StagingPlugin)
        target = NS(target_id="group-9001")
        prepared = {
            "target_id": "group-9001",
            "channel": "group",
            "event_id": "msg-1",
            "turn_id": "core-reply:turn-1",
            "text": "",
            "group_trigger": {"mentioned": True, "mentions_others": False,
                              "mentions_all": False, "bare_mention": True},
        }
        plugin.staging = NS(
            claims=lambda _event: True,
            target_for_event=lambda _event: target,
            runtime=None,
            now=lambda: None,
            session=NS(application=None),
            session_topology=NS(reply_enabled=AsyncMock(return_value=True), schedule_observe=Mock()),
            group_reply_candidate=AsyncMock(return_value=Outcome.success({
                "admitted": True, "reason": "mention", "mentioned": True,
            })),
            prepare_core_reply=AsyncMock(return_value=Outcome.success(prepared)),
            authorize_core_reply=AsyncMock(return_value=Outcome.success(True)),
        )
        plugin._origin_key_for_event = lambda _event: "origin-9001"
        plugin._group_debounce_manager = lambda: NS(observe=Mock(), reserve_group=Mock(return_value=None))
        plugin._record_no_reply = Mock()

        # Without conversation_manager, main.py falls back safely to SELF_MENTION so bare @ is never dropped.
        event_fallback = self._group_event([{"type": "at", "data": {"qq": "100"}}], [At(qq="100")], message_str="")
        with patch.object(ENTRY, "is_notice_event", return_value=False), \
                patch.object(ENTRY, "competing_command_handler", return_value=None), \
                patch.object(ENTRY, "receive_group_llm_command", AsyncMock(return_value=None)), \
                patch.object(ENTRY, "prepare_core_reply_decision", AsyncMock(return_value={"suppress_reply": False})):
            await plugin.receive_companion_message(event_fallback)

        self.assertEqual(event_fallback.message_str, SELF_MENTION)
        self.assertEqual(event_fallback.message_obj.message_str, SELF_MENTION)
        self.assertIsNone(event_fallback.get_extra("provider_request"))
        self.assertIs(event_fallback.get_extra("enable_streaming"), False)
        self.assertTrue(event_fallback.is_wake)
        self.assertTrue(event_fallback.is_at_or_wake_command)

        # With conversation_manager, event.message_str stays empty and a conversation-bound request is attached.
        conv = NS(cid="conv-9001", history="[]")
        plugin.staging.context = NS(
            conversation_manager=NS(
                get_curr_conversation_id=AsyncMock(return_value="conv-9001"),
                get_conversation=AsyncMock(return_value=conv),
                new_conversation=AsyncMock(return_value="conv-new"),
            )
        )
        event = self._group_event([{"type": "at", "data": {"qq": "100"}}], [At(qq="100")], message_str="")
        with patch.object(ENTRY, "is_notice_event", return_value=False), \
                patch.object(ENTRY, "competing_command_handler", return_value=None), \
                patch.object(ENTRY, "receive_group_llm_command", AsyncMock(return_value=None)), \
                patch.object(ENTRY, "prepare_core_reply_decision", AsyncMock(return_value={"suppress_reply": False})):
            await plugin.receive_companion_message(event)

        self.assertEqual(event.message_str, "")
        self.assertEqual(event.message_obj.message_str, "")
        provider_req = event.get_extra("provider_request")
        self.assertIsNotNone(provider_req)
        self.assertEqual(provider_req.prompt, " ")
        self.assertIs(provider_req.conversation, conv)
        self.assertIs(event.get_extra("enable_streaming"), False)
        self.assertTrue(event.is_wake)
        self.assertTrue(event.is_at_or_wake_command)

    async def test_main_group_candidate_host_text_invalid_skips_at_debug_without_warn(self):
        plugin = ENTRY.PrivateCompanionV2StagingPlugin.__new__(ENTRY.PrivateCompanionV2StagingPlugin)
        target = NS(target_id="group-9001")
        plugin.staging = NS(
            claims=lambda _event: True,
            target_for_event=lambda _event: target,
            runtime=None,
            now=lambda: None,
            session=NS(application=None),
            group_reply_candidate=AsyncMock(
                return_value=Outcome.failed("host_text_invalid", "group input must be bounded nonempty text")
            ),
        )
        plugin._origin_key_for_event = lambda _event: "origin-9001"
        plugin._group_debounce_manager = lambda: NS(observe=Mock(), reserve_group=Mock(return_value=None))

        event = self._group_event([{"type": "at", "data": {"qq": "201"}}], [At(qq="201")], message_str="")
        with patch.object(ENTRY, "is_notice_event", return_value=False), \
                patch.object(ENTRY, "competing_command_handler", return_value=None), \
                patch.object(ENTRY, "receive_group_llm_command", AsyncMock(return_value=None)), \
                patch.object(ENTRY, "suppress_default_reply") as suppress, \
                patch.object(ENTRY.logger, "warning") as warn, \
                patch.object(ENTRY.logger, "debug") as debug:
            await plugin.receive_companion_message(event)

        suppress.assert_called_once_with(event)
        warn.assert_not_called()
        debug.assert_called_once()
        self.assertIn("host_text_invalid", debug.call_args.args)


class MessageWithdrawnSilentSuppressionTests(unittest.IsolatedAsyncioTestCase):
    def _fake_event(self, *, streaming=False):
        result = MessageEventResult().message("准备发送的回复")
        if streaming:
            result.result_content_type = ResultContentType.STREAMING_RESULT
        else:
            result.result_content_type = ResultContentType.LLM_RESULT

        class _Event:
            def __init__(self):
                self.extra = {}
                self.sent = []
                self.streamed = []
                self._result = result
                self.send = self._send
                self.send_streaming = self._send_streaming

            def get_extra(self, key, default=None):
                return self.extra.get(key, default)

            def set_extra(self, key, value):
                self.extra[key] = value

            def get_result(self):
                return self._result

            def get_platform_name(self):
                return "aiocqhttp"

            async def _send(self, *args, **kwargs):
                self.sent.append((args, kwargs))
                return "sent"

            async def _send_streaming(self, *args, **kwargs):
                self.streamed.append((args, kwargs))
                return "streamed"

        return _Event()

    async def test_message_withdrawn_suppresses_send_cleanly_without_raising(self):
        event = self._fake_event()
        denied = []
        prepared = {"channel": "group", "event_id": "ev-1"}

        async def authorize(_prepared):
            return Outcome.failed("message_withdrawn", "the trigger message was withdrawn before delivery")

        prepare_core_reply_event(
            event, prepared, authorize=authorize,
            on_denied=lambda current, err: denied.append((current, err)),
        )
        activate_core_reply_delivery_guard(event)

        outcome = await event.send(MessageChain([Plain("准备发送的回复")]))

        self.assertIsNone(outcome)
        self.assertEqual(event.sent, [])
        self.assertEqual(event.get_result().chain, [])
        self.assertEqual(len(denied), 1)
        self.assertEqual(denied[0][1].code, "message_withdrawn")
        self.assertTrue(event.get_extra(CORE_REPLY_CONTEXT_EXTRA)["silent_suppressed"])

        # Subsequent send attempts in the same RespondStage loop also short-circuit silently.
        second = await event.send(MessageChain([Plain("第二段")]))
        self.assertIsNone(second)
        self.assertEqual(event.sent, [])
        self.assertEqual(len(denied), 1)

    async def test_message_withdrawn_stops_segmented_reply_cleanly(self):
        event = self._fake_event()
        authorize_calls = 0

        async def authorize(_prepared):
            nonlocal authorize_calls
            authorize_calls += 1
            return Outcome.failed("message_withdrawn", "the trigger message was withdrawn before delivery")

        prepared = {
            "channel": "private",
            "event_id": "ev-seg-1",
            "segmentation_settings": {
                "enabled": True,
                "mode": "llm_controlled",
                "llm_controlled": True,
                "max_segments": 4,
                "part_gap_ms": 0,
            },
        }
        prepare_core_reply_event(event, prepared, authorize=authorize)
        prepare_core_reply_segmentation(event)
        activate_core_reply_delivery_guard(event)
        activate_core_reply_segmentation(event)

        outcome = await event.send(MessageChain([Plain("第一段\n<<PRIVATE_COMPANION_SPLIT>>\n第二段")]))

        self.assertIsNone(outcome)
        self.assertEqual(event.sent, [])
        self.assertEqual(event.get_result().chain, [])
        self.assertEqual(authorize_calls, 1)

    async def test_message_withdrawn_suppresses_streaming_cleanly_without_raising(self):
        event = self._fake_event(streaming=True)
        denied = []

        async def authorize(_prepared):
            return Outcome.failed("message_withdrawn", "the trigger message was withdrawn before delivery")

        prepare_core_reply_event(
            event, {"channel": "private", "event_id": "ev-stream-1"},
            authorize=authorize,
            on_denied=lambda current, err: denied.append((current, err)),
        )
        activate_core_reply_delivery_guard(event)

        outcome = await event.send_streaming("stream")

        self.assertIsNone(outcome)
        self.assertEqual(event.streamed, [])
        self.assertEqual(event.get_result().chain, [])
        self.assertEqual(len(denied), 1)
        self.assertEqual(denied[0][1].code, "message_withdrawn")

    async def test_main_note_core_send_denied_logs_info_for_message_withdrawn(self):
        plugin = ENTRY.PrivateCompanionV2StagingPlugin.__new__(ENTRY.PrivateCompanionV2StagingPlugin)
        plugin.staging = NS(now=lambda: None)
        plugin._record_no_reply = Mock()
        event = self._fake_event()
        prepared = {"event_ref": "withdrawn-event", "origin_key": "origin", "channel": "group"}

        callbacks = plugin._core_reply_delivery_callbacks(event, prepared, target=None)
        with patch.object(ENTRY.logger, "info") as info, \
                patch.object(ENTRY.logger, "warning") as warn:
            callbacks["on_denied"](prepared, TaskError(
                "message_withdrawn", "the trigger message was withdrawn before delivery"))

        plugin._record_no_reply.assert_called_once()
        self.assertEqual(plugin._record_no_reply.call_args.args[1], "core_reply_send_denied")
        self.assertEqual(plugin._record_no_reply.call_args.kwargs["code"], "message_withdrawn")
        warn.assert_not_called()
        info.assert_called_once()
        self.assertIn("message_withdrawn", info.call_args.args)


if __name__ == "__main__":
    unittest.main()
