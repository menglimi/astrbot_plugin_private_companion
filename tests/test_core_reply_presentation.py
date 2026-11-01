"""Native passive text uses the same presentation rules as V2 tasks."""

from dataclasses import asdict
import json
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock

from tests import astrbot_data_root  # Isolate the SDK before importing the host.
from astrbot.core.message.components import At, Image, Plain, Record, Reply
from astrbot.core.message.message_event_result import MessageChain
from astrbot_host.core_reply import (
    CORE_REPLY_CONTEXT_EXTRA, activate_core_reply_delivery_guard, prepare_core_reply_event,
)
from astrbot_host.core_reply_presentation import capture_core_reply_tool_names
from astrbot_host.core_reply_segmentation import (
    prepare_core_reply_segmentation, prepare_core_reply_streaming_boundary,
)
from companion_v2.conversation.segmentation import SegmentationPolicy
from companion_v2.conversation.voice_reply import VoiceReplyPolicy, separate_spoken
from companion_v2.execution.models import TaskError
from companion_v2.foundation.outcome import Outcome
from tests.test_astrbot_core_reply import FakeEvent


CLEANUP = {"content_cleanup_enabled": True, "content_cleanup_scope": "trailing",
           "content_cleanup_words": '["。", "，"]'}
REPLACE = {"content_replacement_enabled": True, "content_replacements": '["a => aa"]'}


class CoreReplyPresentationTests(unittest.IsolatedAsyncioTestCase):
    def prepare(self, settings=None, *, channel="private", stream=False, platform="aiocqhttp",
                segmentation=None, sender=None, streamer=None, reason=None):
        event = FakeEvent()
        event.get_platform_name = lambda: platform
        event.get_result = lambda: NS(result_content_type=NS(name="STREAMING_RESULT" if stream else "LLM_RESULT"))
        if sender:
            event.send = sender
        if streamer:
            event.send_streaming = streamer(event)
        prepared = {"channel": channel, "presentation_settings": settings,
                    "group_admission": {"reason": reason} if reason else None}
        if segmentation is not None:
            prepared["segmentation_settings"] = {**asdict(segmentation), "part_gap_mode": "fixed", "part_gap_ms": 0}
        self.authorize = AsyncMock(return_value=Outcome.success(True))
        self.observed = []
        prepare_core_reply_event(event, prepared, authorize=self.authorize,
                                 on_sent=lambda _, text: self.observed.append(text))
        prepare_core_reply_segmentation(event)
        return event

    @staticmethod
    async def chunks(*bodies):
        for body in bodies:
            yield body if isinstance(body, MessageChain) else MessageChain(chain=[Plain(body)])

    @staticmethod
    def texts(event):
        return [message.get_plain_text() if hasattr(message, "get_plain_text") else str(message)
                for (message,), _ in event.sent]

    async def test_private_and_group_cleanup_words_and_regex_preserve_internal_punctuation(self):
        for channel in ("private", "group"):
            for mode in ("words", "regex"):
                with self.subTest(channel=channel, mode=mode):
                    settings = {**CLEANUP, "content_cleanup_mode": mode, "content_cleanup_rule": "[。，]+"}
                    event = self.prepare(settings, channel=channel)
                    activate_core_reply_delivery_guard(event)
                    source = MessageChain(chain=[Plain("甲，乙。丙。， \n")])
                    await event.send(source)
                    self.assertEqual(self.texts(event), ["甲，乙。丙"])
                    self.assertEqual(self.observed, ["甲，乙。丙"])
                    self.assertEqual(source.chain[0].text, "甲，乙。丙。， \n")

    async def test_internal_reaction_metadata_is_removed_before_guard_activation(self):
        event = self.prepare()
        source = MessageChain(chain=[
            Plain("正文。<pc_reaction_"),
            Plain('expression">{"intent":"内部态度"}</pc_reaction_expression>'),
        ])
        await event.send(source)
        self.assertEqual(self.texts(event), ["正文。"])

    async def test_tool_loop_voice_tail_does_not_leak_paired_control_tags(self):
        event = self.prepare()
        activate_core_reply_delivery_guard(event)
        source = MessageChain(chain=[
            Record(file="base64://voice"),
            Plain('这种莫名其妙的功能到底是谁加进来的啊...扫吧'
                  '<pc_reaction_expression>{"purpose":"轻吐槽","emotion":"无奈",'
                  '"intensity":2}</pc_reaction_expression>'),
        ])
        await event.send(source)
        delivered = [args[0] for args, _ in event.sent]
        self.assertTrue(any(isinstance(part, Record)
                            for message in delivered for part in message.chain))
        self.assertIn("这种莫名其妙的功能到底是谁加进来的啊...扫吧", self.texts(event))
        self.assertNotIn("pc_reaction_expression", str(delivered))
        self.assertEqual(source.chain[1].text.count("pc_reaction_expression"), 2)

    async def test_internal_reaction_metadata_is_removed_across_native_stream_chunks(self):
        def native_streamer(event):
            async def send_stream(generator, **_):
                async for chunk in generator:
                    await event.send(chunk)
            return send_stream

        event = self.prepare(stream=True, streamer=native_streamer)
        await event.send_streaming(self.chunks(
            "正文。<pc_reaction_",
            'expression\">{"intent":"内部态度，害羞"}',
            "</pc_reaction_",
            "expression>后续正文。",
        ))
        self.assertEqual(self.texts(event), ["正文。", "后续正文。"])

    async def test_each_native_private_bubble_cleans_its_own_tail(self):
        event = self.prepare(CLEANUP)
        activate_core_reply_delivery_guard(event)
        for body in ("第一段，句中。", "第二段，"):
            await event.send(MessageChain(chain=[Plain(body)]))
        self.assertEqual(self.texts(event), ["第一段，句中", "第二段"])
        self.assertEqual(self.authorize.await_count, 2)

    async def test_unrelated_notice_metadata_repeat_and_disabled_rules_keep_original(self):
        for settings, reason, active in ((CLEANUP, None, False), (CLEANUP, "repeat", True),
                                         ({"content_cleanup_enabled": False}, None, True), (None, None, True)):
            event = self.prepare(settings, reason=reason)
            if active:
                activate_core_reply_delivery_guard(event)
            await event.send("原文。")
            self.assertEqual(self.texts(event), ["原文。"])
        event = self.prepare({**CLEANUP, **REPLACE})
        activate_core_reply_delivery_guard(event)
        metadata = MessageChain(type="reasoning", chain=[Plain("a。")])
        await event.send(metadata)
        self.assertEqual(self.texts(event), ["a。"])
        self.assertEqual(self.authorize.await_count, 0)

    async def test_all_cleanup_preserves_literals_and_media_order(self):
        settings = {**CLEANUP, "content_cleanup_scope": "all", "content_cleanup_words": '["删"]',
                    "content_replacement_enabled": True, "content_replacements": '["hi => 嗨"]'}
        event = self.prepare(settings)
        activate_core_reply_delivery_guard(event)
        quote, mention = Reply(id="source", sender_id="200"), At(qq="200")
        image = Image.fromURL("https://example.invalid/hi.png")
        body = 'hi删 （hi删） “hi删” https://host.test/hi删\n\n`hi删`'
        source = MessageChain(chain=[quote, mention, Plain(body), image], use_markdown_=True)
        await event.send(source)
        sent = event.sent[0][0][0]
        self.assertEqual(sent.get_plain_text(), '嗨 （hi删） “hi删” https://host.test/hi删\n\n`hi删`')
        self.assertEqual(sent.chain[:2], [quote, mention])
        self.assertIs(sent.chain[-1], image)
        self.assertTrue(sent.use_markdown_)
        self.assertEqual(source.chain[2].text, body)

    async def test_empty_cleaned_text_does_not_send_a_header_or_claim_delivery(self):
        event = self.prepare(CLEANUP)
        activate_core_reply_delivery_guard(event)
        await event.send(MessageChain(chain=[Reply(id="source", sender_id="200"), At(qq="200"), Plain("。，")]))
        self.assertEqual(event.sent, [])
        self.assertEqual(self.authorize.await_count, 0)
        self.assertEqual(event.get_extra(CORE_REPLY_CONTEXT_EXTRA)["send_attempts"], 0)
        image = Image.fromURL("https://example.invalid/image.png")
        await event.send(MessageChain(chain=[Plain("。"), image]))
        self.assertEqual(event.sent[0][0][0].chain, [image])

    async def test_group_replaces_before_splitting_and_cleans_each_bubble_once(self):
        settings = {**CLEANUP, **REPLACE,
                    "content_replacements": '["a => aa", "完整。词 => 连词"]'}
        event = self.prepare(settings, channel="group", segmentation=SegmentationPolicy(min_segment_chars=1))
        activate_core_reply_delivery_guard(event)
        await event.send(MessageChain(chain=[Plain("a完整。词。第二段。")]))
        self.assertEqual(self.texts(event), ["aa连词", "第二段"])
        self.assertEqual(self.authorize.await_count, 2)

    async def test_group_non_idempotent_cleanup_runs_once(self):
        event = self.prepare({**CLEANUP, "content_cleanup_scope": "all", "content_cleanup_words": '["ab"]'},
                             channel="group", segmentation=SegmentationPolicy(enabled=False))
        activate_core_reply_delivery_guard(event)
        await event.send("aabb")
        self.assertEqual(self.texts(event), ["ab"])

    async def test_private_and_group_split_configured_commas_before_all_cleanup(self):
        settings = {**CLEANUP, "content_cleanup_scope": "all"}
        policy = SegmentationPolicy(split_words=json.dumps(["，", "…"], ensure_ascii=False),
                                    min_segment_chars=5)
        for channel in ("private", "group"):
            with self.subTest(channel=channel):
                event = self.prepare(settings, channel=channel, segmentation=policy)
                activate_core_reply_delivery_guard(event)
                await event.send(MessageChain(chain=[Plain("哼，才不笨呢，比折大人才是大笨蛋…")]))
                self.assertEqual(self.texts(event), ["哼才不笨呢", "比折大人才是大笨蛋…"])
                self.assertEqual(self.authorize.await_count, 2)

    async def test_private_stream_preferences_use_complete_body_before_comma_cleanup(self):
        settings = {**CLEANUP, "content_cleanup_scope": "all"}
        for mode in ("buffered", "incremental"):
            with self.subTest(mode=mode):
                event = self.prepare(settings, channel="private",
                    segmentation=SegmentationPolicy(streaming_mode=mode, min_segment_chars=5,
                        split_words=json.dumps(["，", "…"], ensure_ascii=False)))
                event.set_extra("enable_streaming", True)
                prepare_core_reply_streaming_boundary(event)
                self.assertIs(event.get_extra("enable_streaming"), False)
                activate_core_reply_delivery_guard(event)
                await event.send(MessageChain(chain=[Plain("哼，才不笨呢，比折大人才是大笨蛋…")]))
                self.assertEqual(self.texts(event), ["哼才不笨呢", "比折大人才是大笨蛋…"])

    async def test_group_streamed_comma_boundaries_survive_all_cleanup(self):
        settings = {**CLEANUP, "content_cleanup_scope": "all"}
        for mode in ("buffered", "incremental"):
            with self.subTest(mode=mode):
                event = self.prepare(settings, channel="group", stream=True,
                    segmentation=SegmentationPolicy(streaming_mode=mode, min_segment_chars=5,
                        split_words=json.dumps(["，", "…"], ensure_ascii=False)))
                await event.send_streaming(self.chunks("哼，才不", "笨呢，比折大人", "才是大笨蛋…"))
                self.assertEqual(self.texts(event), ["哼才不笨呢", "比折大人才是大笨蛋…"])

    async def test_framework_dump_is_replaced_at_the_native_send_boundary(self):
        event = self.prepare()
        activate_core_reply_delivery_guard(event)
        await event.send(MessageChain(chain=[Plain(
            '''Traceback (most recent call last):
  File "one.py", line 1, in first
  File "two.py", line 2, in second''')]))
        self.assertEqual(self.texts(event), ["（这条回复没有正常生成，按安全策略不予展示。）"])

    async def test_forbidden_word_policy_blocks_whole_group_reply_before_splitting(self):
        settings = {"enable_forbidden_word_recall": True, "recall_forbidden_words": '["屏蔽词"]',
                    "recall_forbidden_scope": "group_only"}
        event = self.prepare(settings, channel="group",
                             segmentation=SegmentationPolicy(min_segment_chars=1))
        activate_core_reply_delivery_guard(event)
        await event.send("第一段。含有屏蔽词的第二段。")
        self.assertEqual(event.sent, [])
        self.assertTrue(event.get_extra(CORE_REPLY_CONTEXT_EXTRA)["_text_presentation_removed"])
        self.assertEqual(self.authorize.await_count, 0)

    async def test_segment_markers_and_credentials_never_reach_native_output(self):
        secret = "ghp_" + "a" * 32
        event = self.prepare()
        activate_core_reply_delivery_guard(event)
        await event.send(MessageChain(chain=[Plain(
            f'''第一段
<<PRIVATE_COMPANION_SPLIT>>
第二段，令牌 {secret} password=another-secret-value''')]))
        output = self.texts(event)[0]
        self.assertNotIn("<<PRIVATE_COMPANION_SPLIT>>", output)
        self.assertNotIn(secret, output)
        self.assertNotIn("another-secret-value", output)
        self.assertIn("[REDACTED]", output)

    async def test_only_plaintext_calls_for_tools_offered_this_turn_are_removed(self):
        event = self.prepare()
        request = NS(func_tool=NS(tools=[NS(name="send_message_to_user")]))
        capture_core_reply_tool_names(event, request)
        activate_core_reply_delivery_guard(event)
        await event.send('''可见回复。
```json
{"name":"send_message_to_user","arguments":{"message":"内部调用"}}
```''')
        self.assertEqual(self.texts(event), ["可见回复。"])

        event = self.prepare()
        capture_core_reply_tool_names(event, request)
        activate_core_reply_delivery_guard(event)
        ordinary = '解释示例：{"name":"ordinary_payload","parameters":{"value":1}}'
        await event.send(ordinary)
        self.assertEqual(self.texts(event), [ordinary])

    async def test_group_buffered_and_incremental_stream_clean_complete_bubbles(self):
        for mode in ("buffered", "incremental"):
            event = self.prepare({**CLEANUP, **REPLACE}, channel="group", stream=True,
                segmentation=SegmentationPolicy(streaming_mode=mode, min_segment_chars=1))
            await event.send_streaming(self.chunks("a第一", "段。第二", "段。"))
            self.assertEqual(self.texts(event), ["aa第一段", "第二段"])
            core = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
            self.assertEqual((core["send_attempts"], core["send_returns"]), (2, 2))
            self.assertIs(event.send.__func__, FakeEvent._send)

    async def test_private_qq_native_fallback_cleans_at_actual_bubble_boundaries(self):
        def fallback(event):
            async def send_stream(generator, **kwargs):
                body = ""
                async for chunk in generator:
                    body += chunk.get_plain_text()
                    while "。" in body:
                        part, body = body.split("。", 1)
                        await event.send(MessageChain(chain=[Plain(part + "。")]))
                if body:
                    await event.send(MessageChain(chain=[Plain(body)]))
            return send_stream
        event = self.prepare({**CLEANUP, **REPLACE}, stream=True, streamer=fallback)
        await event.send_streaming(self.chunks("a第一", "段。第二", "段。"))
        self.assertEqual(self.texts(event), ["aa第一段", "第二段"])
        self.assertEqual(self.observed, ["aa第一段", "第二段"])
        core = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
        self.assertEqual((core["send_attempts"], core["send_returns"]), (2, 2))

    async def test_native_direct_stream_matches_across_chunks_and_preserves_literal_context(self):
        def consume(event):
            async def send_stream(generator, **kwargs):
                async for chunk in generator:
                    event.streamed.append(chunk)
            return send_stream
        event = self.prepare({**CLEANUP, "content_replacement_enabled": True,
                              "content_replacements": '["hello => hi"]'},
                             stream=True, platform="webchat", streamer=consume)
        await event.send_streaming(self.chunks("hel", "lo。（hel", "lo。）结尾。"))
        self.assertEqual([chunk.get_plain_text() for chunk in event.streamed], ["hi。（hello。）结尾"])
        self.assertEqual(self.observed, ["hi。（hello。）结尾"])

    async def test_non_qq_fallback_rebuilt_chains_do_not_repeat_replacement(self):
        def fallback(event):
            async def send_stream(generator):
                async for chunk in generator:
                    await event.send(MessageChain(chain=[Plain(chunk.get_plain_text())]))
            return send_stream
        event = self.prepare(REPLACE, stream=True, platform="fallback", streamer=fallback)
        await event.send_streaming(self.chunks("a"))
        self.assertEqual(self.texts(event), ["aa"])

    async def test_empty_streams_do_not_count_success_for_bubble_or_native_adapters(self):
        for platform in ("aiocqhttp", "native"):
            def consume(event):
                async def send_stream(generator):
                    async for chunk in generator:
                        await event.send(chunk)
                return send_stream
            event = self.prepare(CLEANUP, stream=True, platform=platform, streamer=consume)
            await event.send_streaming(self.chunks("。", "，"))
            core = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
            self.assertEqual(event.sent, [])
            self.assertEqual(self.observed, [])
            self.assertEqual((core["send_attempts"], core["send_returns"]), (0, 0))

    async def test_cleanup_does_not_bypass_revocation_or_replay_unknown_sends(self):
        event = self.prepare(CLEANUP, channel="group", segmentation=SegmentationPolicy(min_segment_chars=1))
        self.authorize.side_effect = [Outcome.success(True), Outcome.failed("denied", "revoked")]
        activate_core_reply_delivery_guard(event)
        with self.assertRaises(TaskError):
            await event.send("第一段。第二段。")
        await event.send("第一段。第二段。")
        self.assertEqual(self.texts(event), ["第一段"])
        sender = AsyncMock(side_effect=TimeoutError("unknown"))
        event = self.prepare(CLEANUP, channel="group", segmentation=SegmentationPolicy(min_segment_chars=1), sender=sender)
        activate_core_reply_delivery_guard(event)
        with self.assertRaises(TimeoutError):
            await event.send("第一段。第二段。")
        await event.send("第一段。第二段。")
        self.assertEqual(sender.await_count, 1)
        self.assertEqual(str(sender.await_args.args[0]), "第一段")

    async def test_plan_text_is_removed_at_the_native_send_boundary(self):
        event = self.prepare({})
        activate_core_reply_delivery_guard(event)
        source = MessageChain(chain=[Plain(
            "hthought\n Let's call `newapi_emit_text_v1`. 什么可爱啊，别乱用这种词形容我。")])
        await event.send(source)
        self.assertEqual(self.texts(event), ["什么可爱啊，别乱用这种词形容我。"])
        self.assertEqual(self.observed, ["什么可爱啊，别乱用这种词形容我。"])
        # 原始结果对象不被就地改写，对齐上面既有断言的写法。
        self.assertIn("hthought", source.chain[0].text)

    async def test_plan_only_turn_is_not_sent(self):
        event = self.prepare({})
        activate_core_reply_delivery_guard(event)
        await event.send(MessageChain(chain=[Plain("Let's call `newapi_emit_text_v1` now.")]))
        self.assertEqual(event.sent, [])
        self.assertTrue(event.get_extra(CORE_REPLY_CONTEXT_EXTRA)["_text_presentation_removed"])
        self.assertEqual(self.authorize.await_count, 0)

    async def test_plan_prefix_keeps_a_paired_voice_tag_intact(self):
        # 删除终点不得落在语音标签内：否则开标签被吃掉，</pc_tts> 与朗读原文
        # 会变成可见正文。闸门必须把整个成对标当保护区。
        event = self.prepare({})
        activate_core_reply_delivery_guard(event)
        await event.send(MessageChain(chain=[Plain(
            "hthought\n Let's call `newapi_emit_text_v1`.\n"
            "<pc_tts>おやすみ</pc_tts>晚安，好好睡。")]))
        visible = self.texts(event)
        self.assertEqual(visible, ["<pc_tts>おやすみ</pc_tts>晚安，好好睡。"])
        self.assertEqual(visible[0].count("<pc_tts>"), visible[0].count("</pc_tts>"))
        self.assertNotIn("hthought", visible[0])
        self.assertNotIn("newapi_emit_text_v1", visible[0])
        # 标签内容仍由语音管线消费：成对标能被 separate_spoken 识别为朗读候选，
        # 且可见正文里不留任何标签字符。
        spoken_visible, spoken = separate_spoken(
            visible[0], VoiceReplyPolicy(generation_mode="model_tag", language="ja"))
        self.assertEqual([text for _offset, text in spoken], ["おやすみ"])
        self.assertNotIn("<pc_tts>", spoken_visible)
        self.assertNotIn("</pc_tts>", spoken_visible)

    async def test_plan_prefix_keeps_a_paired_reaction_tag_for_the_reactor(self):
        event = self.prepare({})
        activate_core_reply_delivery_guard(event)
        await event.send(MessageChain(chain=[Plain(
            'hthought\n Let\'s call `newapi_emit_text_v1`. '
            '<pc_reaction_expression>{"emotion":"无奈"}</pc_reaction_expression>哼，才不笨呢。')]))
        visible = self.texts(event)
        self.assertEqual(visible, ["哼，才不笨呢。"])
        self.assertNotIn("pc_reaction_expression", visible[0])
        self.assertNotIn("emotion", visible[0])

    async def test_dropped_protocol_segment_renumbers_voice_segment_index(self):
        # 协议段被丢弃后 segment_index/count 重算：三段文本里中间段是孤立协议词，
        # 被分段层丢掉后 voice_segment_index 与 voice_segment_count 必须按最终分泡数重算。
        seen = []

        async def sender(message, *args, **kwargs):
            core = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
            body = message.get_plain_text() if hasattr(message, "get_plain_text") else str(message)
            seen.append((body, core.get("voice_segment_index"), core.get("voice_segment_count")))
            event.sent.append(((message,), kwargs))
            return "sent"

        policy = SegmentationPolicy(enabled=True, llm_controlled=False, rule_mode="paragraphs",
                                    min_segment_chars=1, max_segments=7)
        event = self.prepare({}, segmentation=policy, sender=sender)
        activate_core_reply_delivery_guard(event)
        await event.send(MessageChain(chain=[Plain("第一段。\n\nhthought\n\n第二段。")]))
        self.assertEqual([body for body, _index, _count in seen], ["第一段。", "第二段。"])
        self.assertEqual([(index, count) for _body, index, count in seen], [(0, 2), (1, 2)])
        self.assertEqual(len(seen), 2)

    async def test_intentionally_removed_reply_is_not_reported_as_a_send_failure(self):
        from tests.test_main_no_reply import ENTRY

        event = self.prepare(CLEANUP)
        activate_core_reply_delivery_guard(event)
        await event.send("。")
        plugin = ENTRY.PrivateCompanionV2StagingPlugin.__new__(ENTRY.PrivateCompanionV2StagingPlugin)
        plugin.staging = NS()
        plugin._record_no_reply = Mock()
        await plugin.observe_companion_core_reply_sent(event)
        plugin._record_no_reply.assert_not_called()
        self.assertIs(event.send.__func__, FakeEvent._send)


if __name__ == "__main__":
    unittest.main()
