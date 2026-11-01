"""The AstrBot-owned reply path still executes V2's configured voice policy."""

from dataclasses import asdict
from types import SimpleNamespace as NS
import unittest

from tests import astrbot_data_root  # Isolate the SDK before importing the host.
from astrbot.core.message.components import Image, Plain, Record
from astrbot.core.message.message_event_result import MessageChain

from astrbot_host.core_reply import (
    CORE_REPLY_CONTEXT_EXTRA,
    activate_core_reply_delivery_guard,
    inject_core_reply_prompt,
    prepare_core_reply_event,
)
from astrbot_host.core_reply_outbound import prepare_core_reply_outbound_message
from astrbot_host.core_reply_reaction_delivery import prepare_core_reaction_message
from astrbot_host.core_reply_segmentation import prepare_core_reply_segmentation
from astrbot_host.core_reply_voice import prepare_core_reply_voice_message, voice_prompt_instruction
from astrbot_host.core_reply_voice import capture_core_voice_message, observe_core_voice_response
from astrbot.core.provider.entities import LLMResponse
from companion_v2.conversation.segmentation import SegmentationPolicy
from companion_v2.conversation.voice_reply import VoiceReplyPolicy
from companion_v2.foundation.outcome import Outcome


class FakeEvent:
    def __init__(self):
        self.extra = {}
        self.sent = []
        self.send = self._send
        self.send_streaming = None

    def get_extra(self, key, default=None):
        return self.extra.get(key, default)

    def set_extra(self, key, value):
        self.extra[key] = value

    async def _send(self, message, *args, **kwargs):
        self.sent.append(message)
        return True


class CoreReplyVoiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_core_send_authorizes_before_and_after_tts_then_sends_record(self):
        event = FakeEvent()
        context = object()
        prepared = {"context": context, "event_id": "event-1", "event_ref": "ref-1",
                    "voice_reply_policy": VoiceReplyPolicy(position="after_text")}
        order = []

        async def authorize(_):
            order.append("authorize")
            return Outcome.success(True)

        async def renderer(actual_context, text, *, provider_id, timeout_seconds):
            self.assertIs(actual_context, context)
            self.assertEqual((text, provider_id, timeout_seconds), ("你好", "", 30))
            order.append("render")
            return {"type": "record", "data": {"file": "base64://YQ=="}}

        async def prepare(current, message, check, segment_index, segment_count):
            return await prepare_core_reply_voice_message(
                event, current, message, renderer=renderer, delivery=None, authorize=check,
                segment_index=segment_index, segment_count=segment_count)

        prepare_core_reply_event(event, prepared, authorize=authorize, prepare_send=prepare)
        activate_core_reply_delivery_guard(event)
        await event.send(MessageChain(chain=[Plain("你好")]))

        self.assertEqual(order, ["authorize", "render", "authorize"])
        self.assertEqual(len(event.sent), 1)
        self.assertEqual([type(part) for part in event.sent[0].chain], [Plain, Record])
        self.assertEqual(event.sent[0].chain[1]._companion_v2_spoken_text, "你好")

    async def test_whole_reply_mode_collects_all_segments_before_one_tts_render(self):
        event = FakeEvent()
        policy = VoiceReplyPolicy(segment_mode="whole", position="after_text")
        prepared = {"context": object(), "event_id": "whole-1", "event_ref": "whole-ref",
                    "voice_reply_policy": policy}
        rendered = []

        async def renderer(_, text, **__):
            rendered.append(text)
            return {"type": "record", "data": {"file": "base64://YQ=="}}

        async def prepare(current, message, check, segment_index, segment_count):
            return await prepare_core_reply_voice_message(
                event, current, message, renderer=renderer, delivery=None, authorize=check,
                segment_index=segment_index, segment_count=segment_count)

        prepare_core_reply_event(event, prepared, authorize=_allow, prepare_send=prepare)
        first = await prepare(prepared, MessageChain(chain=[Plain("第一段。")]), _allow, 0, 2)
        second = await prepare(prepared, MessageChain(chain=[Plain("第二段。")]), _allow, 1, 2)

        self.assertEqual(rendered, ["第一段。\n第二段。"])
        self.assertEqual([type(part) for part in first.chain], [Plain])
        self.assertEqual([type(part) for part in second.chain], [Plain, Record])

    async def test_model_tags_speak_only_tagged_text_and_voice_only_keeps_translation(self):
        event = FakeEvent()
        policy = VoiceReplyPolicy(generation_mode="model_tag", language="en",
                                  foreign_text_mode="translation", delivery_mode="voice_only")
        prepared = {"context": object(), "event_id": "event-2", "voice_reply_policy": policy}
        rendered = []

        async def renderer(_, text, **__):
            rendered.append(text)
            return {"type": "record", "data": {"file": "base64://YQ=="}}

        output = await prepare_core_reply_voice_message(
            event, prepared, MessageChain(chain=[Plain("<pc_tts>Hello there.</pc_tts>你好。")]),
            renderer=renderer, delivery=None, authorize=_allow)

        self.assertEqual(rendered, ["Hello there."])
        self.assertEqual([type(part) for part in output.chain], [Plain, Record])
        self.assertEqual(output.chain[0].text, "你好。")

    async def test_failed_tts_keeps_clean_text_and_releases_cooldown_claim(self):
        event = FakeEvent()
        policy = VoiceReplyPolicy(cooldown_seconds=60)
        prepared = {"context": object(), "event_id": "event-3", "voice_reply_policy": policy}
        delivery = FakeDelivery()

        async def renderer(*_, **__):
            return None

        output = await prepare_core_reply_voice_message(
            event, prepared, MessageChain(chain=[Plain("<pc_tts>你好</pc_tts>")]),
            renderer=renderer, delivery=delivery, authorize=_allow)

        self.assertEqual([type(part) for part in output.chain], [Plain])
        self.assertEqual(output.chain[0].text, "你好")
        self.assertEqual(delivery.released, [("claim-1", "core:event")])

    async def test_active_policy_injects_rules_and_disables_streaming(self):
        event = FakeEvent()
        policy = VoiceReplyPolicy(generation_mode="model_tag", trigger_mode="adaptive")
        prepared = {"prompt_instruction": "Use the captured persona.\n\n" + voice_prompt_instruction(policy),
                    "voice_reply_policy": policy}
        prepare_core_reply_event(event, prepared, authorize=_allow)
        # The SDK picks streaming before on_llm_request, so admission must
        # reserve the complete reply before the prompt hook runs.
        self.assertFalse(event.get_extra("enable_streaming"))
        request = NS(system_prompt="base", prompt="hello")

        inject_core_reply_prompt(event, request)

        self.assertFalse(event.get_extra("enable_streaming"))
        self.assertIn("<pc_tts>", request.system_prompt)
        self.assertNotIn("Use the captured persona.", request.system_prompt)
        first_prompt = request.system_prompt
        inject_core_reply_prompt(event, request)
        self.assertEqual(request.system_prompt, first_prompt)

    def test_always_voice_prompt_covers_all_natural_reply_parts(self):
        policy = VoiceReplyPolicy(generation_mode="model_tag", trigger_mode="always",
                                  language="ja", foreign_text_mode="translation")
        instruction = voice_prompt_instruction(policy)
        self.assertIn("各段都保持一致的语音标签", instruction)
        self.assertIn("后续仍属于本次口语回应", instruction)

    async def test_long_spoken_text_is_split_without_losing_content_or_translation(self):
        event = FakeEvent()
        policy = VoiceReplyPolicy(generation_mode="model_tag", language="ja", max_speech_chars=6,
                                  foreign_text_mode="translation")
        prepared = {"voice_reply_policy": policy, "context": object(), "event_id": "long"}
        spoken = []
        async def renderer(_, text, **__):
            spoken.append(text)
            return {"type": "record", "data": {"file": "base64://YQ=="}}
        output = await prepare_core_reply_voice_message(event, prepared,
            MessageChain(chain=[Plain("<pc_tts>こんにちは。元気ですか。</pc_tts>你好，最近还好吗？")]),
            renderer=renderer, delivery=None, authorize=_allow)
        self.assertTrue(all(len(text) <= 6 for text in spoken))
        self.assertEqual("".join(spoken), "こんにちは。元気ですか。")
        self.assertEqual(output.get_plain_text(), "你好，最近还好吗？")
        self.assertEqual(len([part for part in output.chain if isinstance(part, Record)]), 2)

    async def test_extra_c_in_voice_closing_tag_keeps_translation_visible_and_spoken_text(self):
        event = FakeEvent()
        policy = VoiceReplyPolicy(generation_mode="model_tag", language="ja",
                                  foreign_text_mode="translation")
        prepared = {"voice_reply_policy": policy, "context": object(), "event_id": "typo-tag"}
        spoken = []

        async def renderer(_, text, **__):
            spoken.append(text)
            return {"type": "record", "data": {"file": "base64://YQ=="}}

        source = "<pc_tts>ごめんね。呼び方はね。</pcc_tts>抱歉，不是故意。称呼嘛。"
        output = await prepare_core_reply_voice_message(
            event, prepared, MessageChain(chain=[Plain(source)]), renderer=renderer,
            delivery=None, authorize=_allow)

        self.assertEqual(spoken, ["ごめんね。呼び方はね。"])
        self.assertEqual(output.get_plain_text(), "抱歉，不是故意。称呼嘛。")
        self.assertNotIn("pcc_tts", output.get_plain_text())

    def test_unclosed_voice_tag_never_reaches_visible_text(self):
        """02:25 的第 3 条气泡：<pc_tts> 没有闭合时标签字面量不得随正文下发。"""
        event = FakeEvent()
        core = {"prepared": {"event_ref": "unclosed", "voice_reply_policy":
                             VoiceReplyPolicy(generation_mode="model_tag", language="ja")}}
        event.set_extra(CORE_REPLY_CONTEXT_EXTRA, core)

        output = capture_core_voice_message(event, MessageChain(chain=[Plain("正文<pc_tts>よるのおやすみ")]))

        self.assertEqual(output.get_plain_text(), "正文よるのおやすみ")
        self.assertNotIn("tts", output.get_plain_text().casefold())
        self.assertEqual(capture_core_voice_message(event, "正文<pc_tts>よるのおやすみ"), "正文よるのおやすみ")
        self.assertNotIn("_voice_authored", core)

    async def test_segmented_delivery_removes_an_unclosed_voice_tag_before_the_platform_send(self):
        """契约守护：生产接线的分段出口对未闭合标签只发送清理后的正文。

        这条用例在生产同款 ``prepare_send`` 下也通过 origin/main 的实现
        （``prepare_core_reply_voice_message`` 自己没有 policy 分支/无语音分支都会清洗），
        所以它守护的是"分段出口的可见文本契约"，不承担旧实现必红的判别力。
        """
        event = FakeEvent()
        prepared = {"context": object(), "event_id": "segmented", "event_ref": "segmented",
                    "channel": "private",
                    "segmentation_settings": {**asdict(SegmentationPolicy()),
                                              "part_gap_mode": "fixed", "part_gap_ms": 0},
                    "voice_reply_policy": VoiceReplyPolicy(generation_mode="model_tag", language="ja")}
        prepare_core_reply_event(event, prepared, authorize=_allow,
                                 prepare_send=_production_prepare_send(event, _unexpected_voice_render))
        prepare_core_reply_segmentation(event)
        activate_core_reply_delivery_guard(event)

        await event.send(MessageChain(chain=[Plain("正文<pc_tts>よるのおやすみ")]))

        self.assertEqual(len(event.sent), 1)
        delivered = event.sent[0].get_plain_text()
        self.assertNotIn("pc_tts", delivered)
        self.assertNotIn("tts", delivered.casefold())
        self.assertEqual(delivered, "正文よるのおやすみ")

    async def test_native_voice_chain_still_cleans_an_unclosed_voice_tag_before_the_platform_send(self):
        """链上已有原生 Record 时语音准备会提前返回，清理必须发生在平台发送之前。

        ``prepare_core_reply_voice_message`` 见到 ``Record`` 就以 ``native_voice_exists``
        原样返回，此时 ``capture_core_voice_message`` 是最后一道清洗；没有本改动时
        ``<pc_tts>`` 字面量会随这条分段消息下发。
        """
        event = FakeEvent()
        prepared = {"context": object(), "event_id": "native-voice", "event_ref": "native-voice",
                    "channel": "private",
                    "segmentation_settings": {**asdict(SegmentationPolicy()),
                                              "part_gap_mode": "fixed", "part_gap_ms": 0},
                    "voice_reply_policy": VoiceReplyPolicy(generation_mode="model_tag", language="ja")}
        prepare_core_reply_event(event, prepared, authorize=_allow,
                                 prepare_send=_production_prepare_send(event, _unexpected_voice_render))
        prepare_core_reply_segmentation(event)
        activate_core_reply_delivery_guard(event)

        await event.send(MessageChain(chain=[Plain("正文<pc_tts>よるのおやすみ"),
                                            Record(file="base64://YQ==")]))

        self.assertEqual(len(event.sent), 1)
        delivered = event.sent[0]
        self.assertEqual([type(part) for part in delivered.chain], [Plain, Record])
        self.assertEqual(delivered.get_plain_text(), "正文よるのおやすみ")
        self.assertNotIn("pc_tts", delivered.get_plain_text())
        diagnostics = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)["_voice_reply_diagnostics"]
        self.assertEqual(diagnostics[-1]["state"], "native_voice_exists")

    def test_unclosed_voice_tag_is_removed_when_voice_is_plain_generated(self):
        event = FakeEvent()
        core = {"prepared": {"event_ref": "plain", "voice_reply_policy": VoiceReplyPolicy()}}
        event.set_extra(CORE_REPLY_CONTEXT_EXTRA, core)

        output = capture_core_voice_message(event, MessageChain(chain=[Plain("正文<pc_tts>よるのおやすみ")]))

        self.assertEqual(output.get_plain_text(), "正文よるのおやすみ")

    def test_unclosed_voice_tag_cleanup_keeps_the_rest_of_the_chain(self):
        event = FakeEvent()
        core = {"prepared": {"event_ref": "media", "voice_reply_policy":
                             VoiceReplyPolicy(generation_mode="model_tag", language="ja")}}
        event.set_extra(CORE_REPLY_CONTEXT_EXTRA, core)
        image = Image.fromURL("https://example.invalid/a.png")

        output = capture_core_voice_message(
            event, MessageChain(chain=[Plain("正文<pc_tts>よる"), image]))

        self.assertEqual([type(part) for part in output.chain], [Plain, Image])
        self.assertEqual(output.get_plain_text(), "正文よる")

    def test_paired_voice_tag_keeps_its_speech_and_still_hides_the_wrapper(self):
        event = FakeEvent()
        policy = VoiceReplyPolicy(generation_mode="model_tag", language="ja", foreign_text_mode="translation")
        core = {"prepared": {"event_ref": "paired", "voice_reply_policy": policy}}
        event.set_extra(CORE_REPLY_CONTEXT_EXTRA, core)

        output = capture_core_voice_message(
            event, MessageChain(chain=[Plain("<pc_tts>おやすみ</pc_tts>晚安，好好睡")]))

        self.assertEqual(output.get_plain_text(), "晚安，好好睡")
        self.assertEqual(core["_voice_authored"]["visible"], "晚安，好好睡")
        self.assertEqual(core["_voice_authored"]["spans"], ((0, "おやすみ"),))

    def test_unpaired_voice_wrapper_variants_are_removed_from_visible_text(self):
        variants = (("未闭合开标签", "正文<pc_tts>よるのおやすみ", "正文よるのおやすみ"),
                    ("孤立的闭标签", "正文</tts>后续", "正文后续"),
                    ("标签内空白", "< pc_tts >空白变体", "空白变体"),
                    ("大写标签名", "大写变体<PC_TTS>", "大写变体"),
                    ("全角尖括号", "全角＜pc_tts＞变体", "全角变体"),
                    ("开闭标签名混用", "<pc_tts>混用</tts>结尾", "混用结尾"))
        for name, source, expected in variants:
            with self.subTest(variant=name):
                event = FakeEvent()
                core = {"prepared": {"event_ref": "variant", "voice_reply_policy":
                                     VoiceReplyPolicy(generation_mode="model_tag", language="ja")}}
                event.set_extra(CORE_REPLY_CONTEXT_EXTRA, core)

                output = capture_core_voice_message(event, MessageChain(chain=[Plain(source)]))

                self.assertEqual(output.get_plain_text(), expected)
                self.assertNotIn("_voice_authored", core)

    def test_voice_cleanup_log_reports_wrapper_pairing_without_the_text(self):
        cases = (("model_tag 未配对", VoiceReplyPolicy(generation_mode="model_tag", language="ja"),
                  "正文<pc_tts>よるのおやすみ", "正文よるのおやすみ",
                  "pairing=unpaired", "paired_wrappers=0 stray_tags=1"),
                 ("plain 成对", VoiceReplyPolicy(),
                  "<pc_tts>おやすみ</pc_tts>晚安", "おやすみ晚安",
                  "pairing=paired", "paired_wrappers=1 stray_tags=0"))
        for name, policy, source, visible, pairing, counts in cases:
            with self.subTest(case=name):
                event = FakeEvent()
                core = {"prepared": {"event_ref": "pairing", "voice_reply_policy": policy}}
                event.set_extra(CORE_REPLY_CONTEXT_EXTRA, core)

                with self.assertLogs("astrbot", level="INFO") as captured:
                    output = capture_core_voice_message(event, MessageChain(chain=[Plain(source)]))

                self.assertEqual(output.get_plain_text(), visible)
                line = next(line for line in captured.output if "stage=voice_cleanup" in line)
                self.assertIn("state=voice_wrapper_cleanup", line)
                self.assertIn(pairing, line)
                self.assertIn(counts, line)
                self.assertNotIn(visible, line)
                self.assertNotIn(source, line)

    async def test_captured_performance_is_preserved_after_visible_reply_is_segmented(self):
        event = FakeEvent()
        policy = VoiceReplyPolicy(generation_mode="model_tag", segment_mode="each")
        prepared = {"voice_reply_policy": policy, "context": object(), "event_id": "drama"}
        event.set_extra(CORE_REPLY_CONTEXT_EXTRA, {"prepared": prepared})
        response = LLMResponse(role="assistant",
            completion_text="<pc_tts>[温柔地]晚安。<whisper>好好休息。</whisper></pc_tts>")
        observe_core_voice_response(event, response)
        self.assertEqual(response.completion_text, "晚安。好好休息。")
        rendered = []

        async def renderer(_, text, **__):
            rendered.append(text)
            return {"type": "record", "data": {"file": "base64://YQ=="}}

        for index, text in enumerate(("晚安。", "好好休息。")):
            output = await prepare_core_reply_voice_message(event, prepared,
                MessageChain(chain=[Plain(text)]), renderer=renderer, delivery=None,
                authorize=_allow, segment_index=index, segment_count=2)
            self.assertEqual(output.get_plain_text(), text)
        self.assertEqual(rendered, ["[温柔地]晚安。", "<whisper>好好休息。</whisper>"])

    async def test_voice_only_uses_spoken_words_instead_of_control_tags_for_text_comparison(self):
        policy = VoiceReplyPolicy(generation_mode="model_tag", delivery_mode="voice_only")
        prepared = {"voice_reply_policy": policy, "context": object(), "event_id": "drama-only"}

        async def renderer(_, text, **__):
            self.assertEqual(text, "[轻声]晚安。")
            return {"type": "record", "data": {"file": "base64://YQ=="}}

        result = await prepare_core_reply_voice_message(FakeEvent(), prepared,
            MessageChain(chain=[Plain("<pc_tts>[轻声]晚安。</pc_tts>")]), renderer=renderer,
            delivery=None, authorize=_allow)
        self.assertEqual([type(part) for part in result.chain], [Record])

    async def test_qq_voice_is_sent_separately_from_text(self):
        event = FakeEvent()
        event.get_platform_name = lambda: "aiocqhttp"
        policy = VoiceReplyPolicy()
        prepared = {"voice_reply_policy": policy, "context": object(), "event_id": "qq"}
        async def prepare(current, message, check, index, count):
            async def render(*_, **__):
                return {"type": "record", "data": {"file": "base64://YQ=="}}
            return await prepare_core_reply_voice_message(event, current, message,
                renderer=render, delivery=None, authorize=check)
        prepare_core_reply_event(event, prepared, authorize=_allow, prepare_send=prepare)
        activate_core_reply_delivery_guard(event)
        await event.send(MessageChain(chain=[Plain("你好")]))
        self.assertEqual([[type(part) for part in message.chain] for message in event.sent], [[Plain], [Record]])
        self.assertEqual(event.get_extra(CORE_REPLY_CONTEXT_EXTRA)["send_returns"], 2)

    async def test_whole_reply_voice_survives_a_trailing_media_only_segment(self):
        """A reply ends with an image: the joined speech must still be synthesized."""
        event = FakeEvent()
        policy = VoiceReplyPolicy(generation_mode="model_tag", segment_mode="whole")
        prepared = {"context": object(), "event_id": "whole-media", "event_ref": "whole-media",
                    "voice_reply_policy": policy}
        rendered = []

        async def renderer(_, text, **__):
            rendered.append(text)
            return {"type": "record", "data": {"file": "base64://YQ=="}}

        parts = [MessageChain(chain=[Plain("<pc_tts>第一段。</pc_tts>第一段。")]),
                 MessageChain(chain=[Image.fromURL("https://example.invalid/a.png")])]
        async def prepare(current, message, check, index, count):
            return await prepare_core_reply_voice_message(
                event, current, message, renderer=renderer, delivery=None, authorize=check,
                segment_index=index, segment_count=count)

        prepare_core_reply_event(event, prepared, authorize=_allow, prepare_send=prepare)
        output = [await prepare(prepared, message, _allow, index, len(parts))
                  for index, message in enumerate(parts)]

        self.assertEqual(rendered, ["第一段。"])
        self.assertEqual([type(part) for part in output[0].chain], [Plain])
        self.assertEqual([type(part) for part in output[1].chain], [Image, Record])

    async def test_per_segment_voice_does_not_speak_a_media_only_segment(self):
        """Only whole-reply mode waits for the turn; per-segment voice has no text here."""
        event = FakeEvent()
        policy = VoiceReplyPolicy(segment_mode="each")
        prepared = {"context": object(), "event_id": "each-media", "voice_reply_policy": policy}
        rendered = []

        async def renderer(_, text, **__):
            rendered.append(text)
            return {"type": "record", "data": {"file": "base64://YQ=="}}

        prepare_core_reply_event(event, prepared, authorize=_allow)
        output = await prepare_core_reply_voice_message(
            event, prepared, MessageChain(chain=[Image.fromURL("https://example.invalid/a.png")]),
            renderer=renderer, delivery=None, authorize=_allow, segment_index=1, segment_count=2)

        self.assertEqual(rendered, [])
        self.assertEqual([type(part) for part in output.chain], [Image])
        diagnostics = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)["_voice_reply_diagnostics"]
        self.assertEqual(diagnostics[-1]["state"], "no_text")


class FakeDelivery:
    def __init__(self):
        self.released = []

    def claim_core_route(self, *_):
        return {"part_id": "core:event", "cooldown_key": "claim-1"}

    def release(self, claim, part_id):
        self.released.append((claim["cooldown_key"], part_id))


async def _allow(*_):
    return Outcome.success(True)


async def _unexpected_voice_render(*_, **__):
    raise AssertionError("voice reply must not render while the chain already carries a Record")


def _production_prepare_send(event, renderer):
    """Mirror main.py's ``prepare_send`` callback: reaction, voice, then outbound."""
    async def prepare(current, message, authorize, segment_index, segment_count):
        message = await prepare_core_reaction_message(
            event, message, authorize=authorize,
            segment_index=segment_index, segment_count=segment_count)
        message = await prepare_core_reply_voice_message(
            event, current, message, renderer=renderer, delivery=None, authorize=authorize,
            segment_index=segment_index, segment_count=segment_count)
        return prepare_core_reply_outbound_message(
            event, current, message, segment_index=segment_index)

    return prepare
