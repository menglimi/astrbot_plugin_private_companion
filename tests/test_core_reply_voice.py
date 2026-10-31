"""The AstrBot-owned reply path still executes V2's configured voice policy."""

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
from astrbot_host.core_reply_voice import prepare_core_reply_voice_message, voice_prompt_instruction
from astrbot_host.core_reply_voice import observe_core_voice_response
from astrbot.core.provider.entities import LLMResponse
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
