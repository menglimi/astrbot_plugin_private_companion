"""Final and streaming group text keep native sends and per-part authorization."""

from dataclasses import asdict
import json
from types import SimpleNamespace as NS
import unittest

from astrbot.core.message.components import At, Image, Plain, Reply
from astrbot.core.message.message_event_result import MessageChain, MessageEventResult
from astrbot_host.core_reply import (CORE_REPLY_CONTEXT_EXTRA, activate_core_reply_delivery_guard,
                                    inject_core_reply_prompt, prepare_core_reply_event)
from astrbot_host.core_reply_segmentation import (
    prepare_core_reply_segmentation, prepare_core_reply_streaming_boundary, split_message,
)
from astrbot_host.core_reply_perception import inject_core_reply_perception_context
from companion_v2.conversation.segment_control import LLM_SEGMENT_MARKER as MARKER
from companion_v2.conversation.segmentation import SegmentationPolicy
from companion_v2.conversation.text_processing import ReplyTextPolicy
from companion_v2.foundation.outcome import Outcome
from tests.test_astrbot_core_reply import FakeEvent


FIRST, SECOND = "第一条完整的回复。", "第二条完整的回复。"


class CoreGroupSegmentationTests(unittest.IsolatedAsyncioTestCase):
    def prepare(self, *, policy=None, authorize=None, channel="group", result_type="LLM_RESULT",
                streaming=False):
        event = FakeEvent()
        event.get_platform_name = lambda: "aiocqhttp"
        event.get_result = lambda: NS(result_content_type=NS(name=result_type))
        prepared = {"channel": channel, "event_id": "segmented-event", "prompt_instruction": "背景",
                    "segmentation_settings": {**asdict(policy or SegmentationPolicy()), "streaming": streaming,
                                              "part_gap_mode": "fixed", "part_gap_ms": 0}}

        async def allow(_):
            return Outcome.success(True)

        prepare_core_reply_event(event, prepared, authorize=authorize or allow)
        prepare_core_reply_segmentation(event)
        prepare_core_reply_streaming_boundary(event)
        return event

    async def test_group_parts_keep_the_configured_gap_without_concurrent_pressure(self):
        from unittest.mock import AsyncMock, patch

        event = self.prepare(channel="group")
        core = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
        core["_reply_segmentation"]["settings"]["part_gap_ms"] = 800
        activate_core_reply_delivery_guard(event)
        with patch("astrbot_host.core_reply_segmentation.asyncio.sleep", new_callable=AsyncMock) as sleep_mock:
            await event.send(MessageChain(chain=[Plain(FIRST + SECOND)]))
            sleep_mock.assert_awaited()
            self.assertEqual(sleep_mock.await_args.args[0], 0.8)

    async def test_group_stream_parts_keep_the_configured_gap(self):
        from unittest.mock import AsyncMock, patch
        from astrbot_host.core_reply_segmentation import send_group_stream

        event = self.prepare(channel="group", streaming=True)
        core = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
        core["_group_segmentation"]["settings"].update(
            {"streaming_mode": "incremental", "part_gap_mode": "fixed", "part_gap_ms": 800})
        activate_core_reply_delivery_guard(event)

        async def chunks():
            yield FIRST
            yield SECOND

        with patch("astrbot_host.core_reply_segmentation.asyncio.sleep", new_callable=AsyncMock) as sleep_mock:
            await send_group_stream(event, chunks())
            sleep_mock.assert_awaited()
            self.assertEqual(sleep_mock.await_args.args[0], 0.8)

    async def test_concurrent_group_turns_skip_inter_segment_sleep_to_prevent_interleaving(self):
        from unittest.mock import AsyncMock, patch
        from astrbot_host.core_reply_tracking import CORE_REPLY_TRACKING_EXTRA

        event = self.prepare(channel="group")
        core = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
        core["_reply_segmentation"]["settings"]["part_gap_ms"] = 800
        admission = NS(has_concurrent_or_newer_core_turn=lambda ctx, eid: True)
        event.set_extra(CORE_REPLY_TRACKING_EXTRA, {
            "admission": admission, "context": object(), "event_id": "segmented-event",
        })
        activate_core_reply_delivery_guard(event)
        with patch("astrbot_host.core_reply_segmentation.asyncio.sleep", new_callable=AsyncMock) as sleep_mock:
            await event.send(MessageChain(chain=[Plain(FIRST + SECOND)]))
            sleep_mock.assert_not_awaited()
        self.assertEqual([args[0].get_plain_text() for args, _ in event.sent], [FIRST, SECOND])

    async def test_final_group_reply_is_split_and_checks_each_actual_send(self):
        checks = []

        async def allow(prepared):
            checks.append(prepared)
            return Outcome.success(True)

        event = self.prepare(authorize=allow)
        activate_core_reply_delivery_guard(event)
        await event.send(MessageChain(chain=[Plain(FIRST + SECOND)]))
        self.assertEqual([args[0].get_plain_text() for args, _ in event.sent], [FIRST, SECOND])
        self.assertEqual(len(checks), 2)
        core = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
        self.assertEqual((core["send_attempts"], core["send_returns"]), (2, 2))

    async def test_private_plugin_notice_is_untouched_and_final_reply_uses_plugin_parts(self):
        for channel in ("group", "private"):
            event = self.prepare(channel=channel)
            await event.send(FIRST + SECOND)
            self.assertEqual(event.sent, [((FIRST + SECOND,), {})])
            activate_core_reply_delivery_guard(event)
            await event.send(FIRST + SECOND)
            self.assertEqual([args[0] for args, _ in event.sent],
                             [FIRST + SECOND, FIRST, SECOND])

    async def test_private_streaming_and_voice_messages_keep_host_delivery(self):
        streamed = self.prepare(channel="private", result_type="STREAMING_RESULT")
        activate_core_reply_delivery_guard(streamed)
        await streamed.send(FIRST + SECOND)
        self.assertEqual(streamed.sent, [((FIRST + SECOND,), {})])

        configured_stream = self.prepare(channel="private", streaming=True)
        self.assertIs(configured_stream.get_extra("enable_streaming"), False)
        activate_core_reply_delivery_guard(configured_stream)
        await configured_stream.send(FIRST + SECOND)
        self.assertEqual([args[0] for args, _ in configured_stream.sent], [FIRST, SECOND])

        disabled = self.prepare(channel="private", policy=SegmentationPolicy(enabled=False), streaming=True)
        self.assertIsNone(disabled.get_extra("enable_streaming"))

    async def test_disabled_segmentation_and_markdown_code_keep_complete_body(self):
        for policy, body in ((SegmentationPolicy(enabled=False), FIRST + SECOND),
                             (SegmentationPolicy(), "```python\nvalue = 1.25\nprint('hello.world')\n```")):
            event = self.prepare(policy=policy)
            activate_core_reply_delivery_guard(event)
            await event.send(body)
            self.assertEqual([args[0] for args, _ in event.sent], [body])

    async def test_unknown_first_send_never_continues_or_replays_later_parts(self):
        event = FakeEvent()
        attempted = []

        async def unknown(message):
            attempted.append(message)
            raise TimeoutError("unknown send")

        event.send = unknown
        prepared = {"channel": "group", "segmentation_settings": {**asdict(SegmentationPolicy()), "part_gap_mode": "fixed", "part_gap_ms": 0}}

        async def allow(_):
            return Outcome.success(True)

        prepare_core_reply_event(event, prepared, authorize=allow)
        prepare_core_reply_segmentation(event)
        activate_core_reply_delivery_guard(event)
        with self.assertRaises(TimeoutError):
            await event.send(FIRST + SECOND)
        await event.send(FIRST + SECOND)
        self.assertEqual(attempted, [FIRST])

    async def test_permission_revocation_between_parts_preserves_only_first_send(self):
        checks = []

        async def authorize(_):
            checks.append(True)
            return Outcome.success(True) if len(checks) == 1 else Outcome.failed("denied", "permission changed")

        event = self.prepare(authorize=authorize)
        activate_core_reply_delivery_guard(event)
        with self.assertRaises(Exception):
            await event.send(FIRST + SECOND)
        self.assertEqual([args[0] for args, _ in event.sent], [FIRST])

    async def test_buffered_and_incremental_qq_streams_send_complete_native_parts(self):
        for mode in ("buffered", "incremental"):
            event = self.prepare(policy=SegmentationPolicy(streaming_mode=mode), result_type="STREAMING_RESULT")

            async def stream():
                yield MessageChain(chain=[Plain(FIRST[:4])])
                yield MessageChain(chain=[Plain(FIRST[4:] + SECOND)])

            await event.send_streaming(stream(), use_fallback=True)
            self.assertEqual([args[0].get_plain_text() for args, _ in event.sent], [FIRST, SECOND])
            self.assertEqual(event.streamed, [])
            core = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
            self.assertEqual((core["send_attempts"], core["send_returns"]), (2, 2))
            self.assertIs(event.send.__func__, FakeEvent._send)

    async def test_split_marker_across_chunks_is_removed_and_total_limit_is_shared(self):
        event = self.prepare(policy=SegmentationPolicy(llm_controlled=True, max_segments=2, streaming_mode="incremental"), result_type="STREAMING_RESULT")

        async def stream():
            yield MessageChain(chain=[Plain("第一条完整回复\n" + MARKER[:10])])
            yield MessageChain(chain=[Plain(MARKER[10:] + "\n第二条完整回复\n" + MARKER + "\n最后补充内容")])

        await event.send_streaming(stream())
        bodies = [args[0].get_plain_text() for args, _ in event.sent]
        self.assertEqual(len(bodies), 2)
        self.assertNotIn("PRIVATE_COMPANION", "".join(bodies))
        self.assertIn("最后补充内容", bodies[-1])

    async def test_empty_control_only_stream_never_counts_a_send(self):
        event = self.prepare(result_type="STREAMING_RESULT")

        async def stream():
            yield MessageChain(chain=[Plain(MARKER)])

        await event.send_streaming(stream())
        core = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
        self.assertEqual(event.sent, [])
        self.assertEqual((core["send_attempts"], core["send_returns"]), (0, 0))

    async def test_already_segmented_native_components_keep_their_original_boundaries(self):
        event = self.prepare()
        first, second = Plain(FIRST + SECOND), Plain("宿主已拆出的另一条。")
        event.get_result = lambda: NS(chain=[first, second], result_content_type=NS(name="LLM_RESULT"))
        activate_core_reply_delivery_guard(event)
        for component in (first, second):
            await event.send(MessageChain(chain=[component]))
        self.assertEqual([args[0].get_plain_text() for args, _ in event.sent], [first.text, second.text])

    def test_quote_mention_and_images_keep_original_order_once(self):
        quote, mention, image = Reply(id="source", sender_id="200"), At(qq="200"), Image.fromURL("https://example.invalid/image.png")
        chain = MessageChain(chain=[quote, mention, Plain(FIRST), Plain(SECOND), image])
        parts = split_message(chain, SegmentationPolicy())
        self.assertEqual(parts[0].chain[:2], [quote, mention])
        self.assertEqual(parts[1].chain[-1], image)
        self.assertEqual([part.get_plain_text() for part in parts], [FIRST, SECOND])
        self.assertEqual(chain.chain, [quote, mention, Plain(FIRST), Plain(SECOND), image])

    def test_group_rule_boundaries_respect_configured_commas_and_keep_ellipsis_together(self):
        policy = SegmentationPolicy(
            split_words=json.dumps(["。", "？", "！", "?", ".", "!", "，"], ensure_ascii=False),
            min_segment_chars=5, max_segments=5)
        source = "笨蛋比折大人，大半夜的突然说什么呢...？真那么想看的话，真就只能看一眼哦？"
        parts = split_message(MessageChain(chain=[Plain(source)]), policy)
        self.assertEqual([part.get_plain_text() for part in parts], [
            "笨蛋比折大人，", "大半夜的突然说什么呢...？",
            "真那么想看的话，", "真就只能看一眼哦？",
        ])

    async def test_rule_segmentation_and_cleanup_do_not_instruct_the_model(self):
        presentation = ReplyTextPolicy(content_cleanup_enabled=True,
            content_cleanup_words='["，", "。"]', content_replacement_enabled=True,
            content_replacements='[{"from":"你好","to":"嗨"}]')
        for channel in ("private", "group"):
            for enabled in (True, False):
                with self.subTest(channel=channel, enabled=enabled):
                    event = self.prepare(channel=channel, policy=SegmentationPolicy(enabled=enabled,
                        prompt="自定义模型分段仅在开启时使用 {{split_marker}}"))
                    prepared = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)["prepared"]
                    prepared["stable_prompt_instruction"] = presentation.instruction
                    prepared["presentation_settings"] = asdict(presentation)
                    request = NS(system_prompt="原人格", extra_user_content_parts=[])
                    inject_core_reply_prompt(event, request)
                    await inject_core_reply_perception_context(event, request)
                    self.assertEqual(request.system_prompt, "原人格")
                    payload = json.loads(request.extra_user_content_parts[0].text.split("\n", 1)[1]
                                         .removesuffix("\n</companion_v2_context>"))
                    self.assertEqual(payload["companion_background"], "背景")
                    self.assertNotIn("reply_format_guidance", payload)
                    for text in ("消息正文组织", "正文呈现偏好", "清理词", "替换", "自定义模型分段", MARKER):
                        self.assertNotIn(text, request.system_prompt + str(payload))

    async def test_model_segmentation_keeps_custom_protocol_out_of_system_prompt(self):
        for channel in ("private", "group"):
            with self.subTest(channel=channel):
                event = self.prepare(channel=channel, policy=SegmentationPolicy(llm_controlled=True,
                    prompt="用 {{ split_marker }} 标记自然的消息边界。"))
                request = NS(system_prompt="原人格", extra_user_content_parts=[])
                inject_core_reply_prompt(event, request)
                await inject_core_reply_perception_context(event, request)
                self.assertEqual(request.system_prompt, "原人格")
                payload = json.loads(request.extra_user_content_parts[0].text.split("\n", 1)[1]
                                     .removesuffix("\n</companion_v2_context>"))
                self.assertEqual(payload["reply_format_guidance"], f"用 {MARKER} 标记自然的消息边界。")

    async def test_native_group_turn_context_uses_free_text_segmentation_instructions(self):
        event = self.prepare(policy=SegmentationPolicy(llm_controlled=True))
        request = NS(system_prompt="原人格", extra_user_content_parts=[])
        inject_core_reply_prompt(event, request)
        self.assertNotIn(MARKER, request.system_prompt)
        await inject_core_reply_perception_context(event, request)
        context = request.extra_user_content_parts[0].text
        self.assertIn(MARKER, context)
        self.assertIn("直接输出聊天正文", context)

    async def test_private_turn_context_uses_private_wording_and_streaming_omits_markers(self):
        event = self.prepare(channel="private", policy=SegmentationPolicy(llm_controlled=True))
        request = NS(system_prompt="原人格", extra_user_content_parts=[])
        inject_core_reply_prompt(event, request)
        await inject_core_reply_perception_context(event, request)
        context = request.extra_user_content_parts[0].text
        self.assertIn(MARKER, context)
        self.assertIn("普通私聊正文", context)

        streamed = self.prepare(channel="private", policy=SegmentationPolicy(llm_controlled=True),
                                streaming=True)
        request = NS(system_prompt="原人格", extra_user_content_parts=[])
        inject_core_reply_prompt(streamed, request)
        await inject_core_reply_perception_context(streamed, request)
        self.assertIn(MARKER, request.extra_user_content_parts[0].text)

        external_stream = self.prepare(channel="private", policy=SegmentationPolicy(llm_controlled=True))
        external_stream.set_extra("enable_streaming", True)
        request = NS(system_prompt="原人格", extra_user_content_parts=[])
        inject_core_reply_prompt(external_stream, request)
        await inject_core_reply_perception_context(external_stream, request)
        self.assertNotIn(MARKER, request.extra_user_content_parts[0].text)


if __name__ == "__main__":
    unittest.main()
