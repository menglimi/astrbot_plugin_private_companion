"""Use the real SDK runner and native tool schemas without platform network IO."""

from copy import deepcopy
from dataclasses import asdict
import asyncio
import json
from types import SimpleNamespace as NS
import unittest

from tests import test_astrbot_staging_host as _host_import
from astrbot.core.agent.hooks import BaseAgentRunHooks
from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner
from astrbot.core.agent.tool import ToolSet
from astrbot.core.message.components import Plain, Record
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.provider.entities import LLMResponse, ProviderRequest, TokenUsage

from astrbot_host.core_reply import CORE_REPLY_CONTEXT_EXTRA, activate_core_reply_delivery_guard, prepare_core_reply_event
from astrbot_host.core_reply_native_send import SendMessageToUserTool
from astrbot_host.core_reply_media_prompt import finalize_core_reply_media_prompt
from astrbot_host.core_reply_segmentation import prepare_core_reply_segmentation
from astrbot_host.core_reply_voice import observe_core_voice_response, prepare_core_reply_voice_message
from astrbot_host.core_reply_voice_authoring import bind_core_voice_authoring, restore_core_voice_authoring
from companion_v2.conversation.voice_reply import VoiceReplyPolicy
from companion_v2.conversation.segmentation import SegmentationPolicy
from companion_v2.conversation.segment_control import LLM_SEGMENT_MARKER as MARKER
from companion_v2.foundation.outcome import Outcome
from tests.test_core_reply_voice import FakeEvent
from astrbot_host.fish_audio import DRAMA_DIRECTION


class Provider:
    provider_config = {"id": "core-reply-provider"}

    def __init__(self, *outputs):
        self.outputs, self.calls = list(outputs), []

    async def text_chat(self, **kwargs):
        self.calls.append({"contexts": deepcopy(kwargs["contexts"]), "func_tool": kwargs["func_tool"]})
        output = self.outputs.pop(0)
        if isinstance(output, BaseException):
            raise output
        return LLMResponse(role="assistant", completion_text=output,
                           usage=TokenUsage(input_other=100, output=10))


class Hooks(BaseAgentRunHooks):
    async def on_agent_done(self, run_context, response):
        event = run_context.context.event
        observe_core_voice_response(event, response)
        restore_core_voice_authoring(event)


def voice_plan(*speeches):
    return json.dumps({"segments": [{"index": index, "speech": speech}
                      for index, speech in enumerate(speeches, 1)]}, ensure_ascii=False)


class CoreVoiceAuthoringTests(unittest.IsolatedAsyncioTestCase):
    async def run_reply(self, outputs, *, prompt="在干嘛呢", policy=None, provider_instruction="",
                        segmentation=None, presentation=None):
        event = FakeEvent()
        event.get_platform_name = lambda: "aiocqhttp"
        policy = policy or VoiceReplyPolicy(generation_mode="model_tag", segment_mode="each",
                                            language="ja", foreign_text_mode="translation")
        prepared = {"context": object(), "event_id": "voice-turn", "event_ref": "voice-reference",
                    "channel": "private", "native_input": NS(text=prompt), "voice_reply_policy": policy,
                    "voice_provider_instruction": provider_instruction}
        if segmentation is not None:
            prepared["segmentation_settings"] = {**asdict(segmentation), "part_gap_ms": 0}
        if presentation is not None:
            prepared["presentation_settings"] = presentation
        self.rendered = []

        async def render(_, text, **__):
            self.rendered.append(text)
            return {"type": "record", "data": {"file": "base64://YQ=="}}

        async def allow(*_):
            return Outcome.success(True)

        async def prepare(current, message, check, index, count):
            return await prepare_core_reply_voice_message(event, current, message, renderer=render,
                delivery=None, authorize=check, segment_index=index, segment_count=count)

        prepare_core_reply_event(event, prepared, authorize=allow, prepare_send=prepare)
        prepare_core_reply_segmentation(event)
        provider = Provider(*outputs)
        original_tools = ToolSet(tools=[SendMessageToUserTool()])
        req = ProviderRequest(prompt=prompt, system_prompt="角色身份", func_tool=original_tools)
        runner = ToolLoopAgentRunner()
        run = NS(context=NS(event=event), messages=[])
        await runner.reset(provider=provider, request=req, run_context=run, tool_executor=None, agent_hooks=Hooks())
        finalize_core_reply_media_prompt(event, run)
        bind_core_voice_authoring(event, runner)
        async for _ in runner.step():
            pass
        activate_core_reply_delivery_guard(event)
        await event.send(MessageChain(chain=[Plain(runner.final_llm_resp.completion_text)]))
        self.assertIs(runner.req, req)
        self.assertIs(req.func_tool, original_tools)
        self.assertFalse(runner.streaming)
        self.assertNotIn("_iter_llm_responses_with_fallback", runner.__dict__)
        self.assertFalse(any(message.role == "system" and "刚才的回复正文" in str(message.content)
                             for message in run.messages))
        return event, runner, provider

    async def test_missing_tags_are_repaired_by_same_core_runner_then_send_real_record_components(self):
        event, runner, provider = await self.run_reply([
            "刚洗漱完，正陪你聊天呢。", voice_plan("今、顔を洗ったところ。君と話してるよ。")])
        self.assertEqual(len(provider.calls), 2)
        self.assertIsNotNone(provider.calls[0]["func_tool"].get_tool("send_message_to_user"))
        self.assertIsNone(provider.calls[1]["func_tool"])
        self.assertEqual(runner.stats.token_usage.total, 220)
        self.assertEqual(self.rendered, ["今、顔を洗ったところ。君と話してるよ。"])
        self.assertEqual([[type(part) for part in message.chain] for message in event.sent], [[Plain], [Record]])
        self.assertEqual(event.sent[0].get_plain_text(), "刚洗漱完，正陪你聊天呢。")
        self.assertEqual(event.get_extra(CORE_REPLY_CONTEXT_EXTRA)["_voice_authoring_repair"]["state"], "authored")

    async def test_authored_speech_survives_later_response_hook_without_another_model_call(self):
        _, _, provider = await self.run_reply(["<pc_tts>おやすみ。</pc_tts>晚安。"])
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(self.rendered, ["おやすみ。"])

    async def test_same_core_formatter_retains_drama_capabilities_without_showing_controls(self):
        event, _, provider = await self.run_reply([
            "晚安，好好休息。", voice_plan("[優しく]おやすみ。<soft>ゆっくり休んでね。</soft>")],
            provider_instruction=DRAMA_DIRECTION)
        self.assertIn(DRAMA_DIRECTION, provider.calls[1]["contexts"][0].content)
        self.assertEqual(self.rendered, ["[優しく]おやすみ。<soft>ゆっくり休んでね。</soft>"])
        self.assertEqual(event.sent[0].get_plain_text(), "晚安，好好休息。")

    async def test_text_only_request_does_not_repair_or_synthesize(self):
        _, _, provider = await self.run_reply(["好，这次用文字陪你。"], prompt="这次只发文字，不要语音")
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(self.rendered, [])

    async def test_adaptive_plain_reply_does_not_trigger_a_formatting_request(self):
        _, _, provider = await self.run_reply(["我在看代码。"], policy=VoiceReplyPolicy(
            trigger_mode="adaptive", generation_mode="model_tag", language="ja", foreign_text_mode="translation"))
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(self.rendered, [])

    async def test_failed_formatter_keeps_original_body_and_restores_native_request(self):
        event, _, provider = await self.run_reply(["原来的回答。", TimeoutError("synthetic timeout")])
        self.assertEqual(len(provider.calls), 2)
        self.assertEqual(event.sent[0].get_plain_text(), "原来的回答。")
        self.assertEqual(self.rendered, [])
        self.assertEqual(event.get_extra(CORE_REPLY_CONTEXT_EXTRA)["_voice_authoring_repair"]["state"], "failed")

    async def test_formatter_cannot_replace_original_visible_answer(self):
        formatted = json.loads(voice_plan("君と話してる。"))
        formatted["segments"][0]["text"] = "我正在和你聊天。"
        event, _, _ = await self.run_reply(["我在陪你。", json.dumps(formatted, ensure_ascii=False)])
        self.assertEqual(event.sent[0].get_plain_text(), "我在陪你。")
        self.assertEqual(self.rendered, ["君と話してる。"])

    async def test_formatter_without_speech_is_not_repeated_or_forced_into_audio(self):
        event, _, provider = await self.run_reply(["这是一段代码。", voice_plan("")])
        self.assertEqual(len(provider.calls), 2)
        self.assertEqual(self.rendered, [])
        self.assertEqual(event.sent[0].get_plain_text(), "这是一段代码。")

    async def test_three_actual_text_segments_keep_individual_japanese_speech(self):
        draft = "结果还没确认。真是的。再等一会儿吧。"
        speeches = ("まだ結果を確認できてない。", "もう。", "もう少し待ってみよう。")
        event, runner, provider = await self.run_reply([draft, voice_plan(*speeches)],
            segmentation=SegmentationPolicy(min_segment_chars=2))
        self.assertEqual(runner.final_llm_resp.completion_text, draft)
        self.assertEqual([message.get_plain_text() for message in event.sent if message.get_plain_text()],
                         ["结果还没确认。", "真是的。", "再等一会儿吧。"])
        self.assertEqual(self.rendered, list(speeches))
        self.assertEqual([[type(part) for part in message.chain] for message in event.sent],
                         [[Plain], [Record], [Plain], [Record], [Plain], [Record]])
        source = json.loads(provider.calls[1]["contexts"][1].content)
        self.assertEqual([part["text"] for part in source["segments"]],
                         ["结果还没确认。", "真是的。", "再等一会儿吧。"])
        self.assertEqual(len(provider.calls), 2)

    async def test_markers_merged_segments_and_punctuation_cleanup_keep_the_voice_mapping(self):
        draft = f"第一句。\n{MARKER}\n第二句。\n{MARKER}\n第三句。"
        speeches = ("一つ目。二つ目。", "三つ目。")
        event, runner, _ = await self.run_reply([draft, voice_plan(*speeches)],
            segmentation=SegmentationPolicy(llm_controlled=True, max_segments=2, threshold=1),
            presentation={"content_cleanup_enabled": True, "content_cleanup_words": '["。", "\\n"]',
                          "content_cleanup_scope": "trailing"})
        self.assertEqual(runner.final_llm_resp.completion_text, draft)
        self.assertEqual(self.rendered, list(speeches))
        visible = [message.get_plain_text() for message in event.sent if message.get_plain_text()]
        self.assertEqual(len(visible), 2)
        self.assertTrue(all(not part.endswith("。") for part in visible))
        self.assertNotIn(MARKER, "".join(visible))
        self.assertEqual("".join(visible).replace("\n", "").replace("。", ""), "第一句第二句第三句")

    async def test_incomplete_or_duplicate_segment_plan_never_moves_all_speech_to_first_segment(self):
        draft = "第一句。第二句。第三句。"
        invalid_plans = (voice_plan("まとめた音声。"), json.dumps({"segments": [
            {"index": 1, "speech": "一つ目。"}, {"index": 1, "speech": "二つ目。"},
            {"index": 3, "speech": "三つ目。"}]}))
        for invalid in invalid_plans:
            with self.subTest(plan=invalid):
                event, runner, provider = await self.run_reply([draft, invalid],
                    segmentation=SegmentationPolicy(min_segment_chars=2))
                self.assertEqual(runner.final_llm_resp.completion_text, draft)
                self.assertEqual(self.rendered, [])
                self.assertEqual(len(event.sent), 3)
                self.assertEqual(len(provider.calls), 2)

    async def test_partial_voice_can_leave_unsuitable_segments_without_speech(self):
        event, _, _ = await self.run_reply(["我在这儿。你慢慢看。", voice_plan("ここにいるよ。", "")],
            policy=VoiceReplyPolicy(generation_mode="model_tag", language="ja", conversion_scope="partial",
                                    foreign_text_mode="translation"),
            segmentation=SegmentationPolicy(min_segment_chars=2))
        self.assertEqual(self.rendered, ["ここにいるよ。"])
        self.assertEqual([message.get_plain_text() for message in event.sent if message.get_plain_text()],
                         ["我在这儿。", "你慢慢看。"])

    async def test_cancellation_during_formatter_restores_runner_and_never_sends(self):
        event = FakeEvent()
        event.set_extra(CORE_REPLY_CONTEXT_EXTRA, {"prepared": {"voice_reply_policy": VoiceReplyPolicy(
            generation_mode="model_tag", language="ja", foreign_text_mode="translation")}})
        runner = ToolLoopAgentRunner()
        req = ProviderRequest(prompt="在干嘛呢", system_prompt="当前角色")
        run = NS(context=NS(event=event), messages=[])
        await runner.reset(provider=Provider("我在陪你。", asyncio.CancelledError()), request=req,
            run_context=run, tool_executor=None, agent_hooks=Hooks())
        messages = run.messages
        bind_core_voice_authoring(event, runner)
        with self.assertRaises(asyncio.CancelledError):
            async for _ in runner.step():
                pass
        self.assertIs(runner.req, req)
        self.assertIs(run.messages, messages)
        self.assertNotIn("_iter_llm_responses_with_fallback", runner.__dict__)
        self.assertEqual(event.sent, [])
