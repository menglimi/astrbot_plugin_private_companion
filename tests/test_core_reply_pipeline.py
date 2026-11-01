"""Run native AstrBot dispatch, decoration and response with local model/IO doubles."""

from copy import deepcopy
from dataclasses import asdict
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch

from tests.test_main_no_reply import ENTRY
from astrbot.core.config import DEFAULT_CONFIG
from astrbot.core.message.components import Image, Plain, Record
from astrbot.core.message.message_event_result import MessageChain, MessageEventResult, ResultContentType
from astrbot.core.pipeline.process_stage.stage import ProcessStage
from astrbot.core.pipeline.respond.stage import RespondStage
from astrbot.core.pipeline.result_decorate import stage as decorate_module
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
from astrbot.core.platform.message_type import MessageType
from astrbot.core.provider.entities import ProviderRequest
from astrbot.core.provider.entities import LLMResponse
from companion_v2.conversation.segmentation import SegmentationPolicy
from companion_v2.conversation.voice_reply import VoiceReplyPolicy
from companion_v2.foundation.outcome import Outcome
from astrbot_host.core_reply import sent_core_text


class PipelineEvent(AstrMessageEvent):
    def __init__(self):
        message = AstrBotMessage()
        message.type = MessageType.FRIEND_MESSAGE
        message.self_id = "100"
        message.sender = MessageMember("200", "测试用户")
        message.message_id = "core-pipeline-1"
        message.message = [Plain("摸摸")]
        message.raw_message = {"post_type": "message"}
        super().__init__("摸摸", message, NS(id="qq-main", name="aiocqhttp"), "200")
        self.sent, self.streams = [], []
        self.set_extra("activated_handlers", [])

    async def send(self, message):
        self.sent.append(list(message.chain))
        self._has_send_oper = True

    async def send_streaming(self, generator, use_fallback=False):
        async for chunk in generator:
            self.streams.append(chunk.get_plain_text())
        self._has_send_oper = True


class CoreAgentFixture:
    def __init__(self, plugin, *, streaming=False, response_text=None, result_chain=False):
        self.plugin, self.streaming = plugin, streaming
        self.response_text = response_text
        self.result_chain = result_chain
        self.requests = []
        self.responses = []

    async def process(self, event):
        # The actual runner selects streaming before on_llm_request.
        streaming = self.streaming and event.get_extra("enable_streaming") is not False
        req = ProviderRequest(prompt=event.message_str, system_prompt="AstrBot 当前人格")
        await self.plugin.prepare_companion_core_request(event, req)
        self.requests.append(req)
        if event.is_stopped():
            return
        if streaming:
            async def chunks():
                yield MessageEventResult().message("嘿嘿，")
                yield MessageEventResult().message("再摸一下！")
            result = MessageEventResult().set_result_content_type(ResultContentType.STREAMING_RESULT)
            result.set_async_stream(chunks())
        else:
            text = self.response_text if self.response_text is not None else "嘿嘿！\n\n再摸一下！"
            if self.response_text is not None:
                response = LLMResponse(
                    role="assistant", completion_text=text,
                    result_chain=MessageChain(chain=[Plain(text)]) if self.result_chain else None)
                self.responses.append(response)
                await self.plugin.observe_companion_model_session(event, response)
                text = response.completion_text
            result = MessageEventResult().message(text)
            result.set_result_content_type(ResultContentType.LLM_RESULT)
        event.set_result(result)
        yield


class CoreReplyPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def run_pipeline(self, *, streaming=False, authorize=None, segmented=False, tts=False, dual=False,
                           presentation=None, companion_segmenting=False, voice_policy=None, response_text=None,
                           result_chain=False):
        plugin = ENTRY.PrivateCompanionV2StagingPlugin.__new__(ENTRY.PrivateCompanionV2StagingPlugin)
        target = NS(key="target", target_id="target")
        observed = []
        # Baseline pipeline cases preserve their body; cleanup cases opt in below.
        if presentation is None:
            presentation = {"content_cleanup_enabled": False}
        prepared = {
            "context": object(), "instruction": "V2 人格资料", "prompt_instruction": "自然分段",
            "target_id": "target", "channel": "private", "event_id": "core-pipeline-1",
            "presentation_settings": presentation,
            "voice_reply_policy": voice_policy,
        }
        if companion_segmenting:
            policy = SegmentationPolicy(rule_mode="paragraphs", min_segment_chars=1, max_segments=3)
            prepared["segmentation_settings"] = {
                **asdict(policy), "streaming": streaming, "part_gap_mode": "fixed", "part_gap_ms": 0}
        plugin.staging = NS(
            claims=lambda event: True, target_for_event=lambda event: target,
            persona_mapping_for_event=AsyncMock(return_value=NS(role_id="role")),
            session_topology=NS(reply_enabled=AsyncMock(return_value=True), schedule_observe=lambda _: None),
            prepare_core_reply=AsyncMock(return_value=Outcome.success(prepared)),
            authorize_core_reply=authorize or AsyncMock(return_value=Outcome.success(True)),
            receive_claimed=AsyncMock(), runtime=None, now=lambda: None,
            session=NS(core_reply_observations=NS(outputs=observed,
                output=lambda prepared, text, **kwargs: observed.append((text, kwargs["state"]))
                    if kwargs.get("state") == "send_returned" else None),
                voice=AsyncMock(return_value={"type": "record", "data": {"file": "base64://YQ=="}}),
                voice_delivery=None),
        )
        event = PipelineEvent()
        await plugin.receive_companion_message(event)

        config = deepcopy(DEFAULT_CONFIG)
        config["t2i"] = False
        config["provider_tts_settings"].update(enable=tts, dual_output=dual, use_file_service=False)
        config["platform_settings"]["reply_with_mention"] = False
        config["platform_settings"]["reply_with_quote"] = False
        config["platform_settings"]["reply_prefix"] = ""
        config["platform_settings"]["segmented_reply"].update(
            enable=segmented, split_mode="regex", regex=r"[^\n]+", interval="0,0", interval_method="random",
        )
        async def get_tts_provider(_umo):
            return (NS(get_audio=AsyncMock(return_value="native-test-speech.wav")) if tts else None)

        pipeline_context = NS(astrbot_config=config,
                              plugin_manager=NS(context=NS(
                                  get_using_tts_provider=lambda _: NS(
                                      get_audio=AsyncMock(return_value="native-test-speech.wav")) if tts else None,
                                  get_using_tts_provider_async=get_tts_provider)))
        process = ProcessStage()
        process.ctx = pipeline_context
        agent = CoreAgentFixture(plugin, streaming=streaming, response_text=response_text,
                                 result_chain=result_chain)
        process.agent_sub_stage = agent
        decorate = decorate_module.ResultDecorateStage()
        await decorate.initialize(pipeline_context)
        respond = RespondStage()
        await respond.initialize(pipeline_context)

        async def add_native_image(current):
            current.get_result().chain.append(Image.fromURL("https://example.invalid/image.png"))

        handlers = [NS(handler_module_path="core_test", handler_name="native_image", handler=add_native_image),
                    NS(handler_module_path="core_test", handler_name="v2_guard", handler=plugin.guard_companion_core_delivery)]
        async def after_send(current, *_):
            await plugin.observe_companion_core_reply_sent(current)
            return False

        with patch.object(decorate_module.star_handlers_registry, "get_handlers_by_event_type", return_value=handlers), \
                patch.dict(decorate_module.star_map, {"core_test": NS(name="core_test")}), \
                patch("astrbot.core.pipeline.respond.stage.call_event_hook", side_effect=after_send):
            async for _ in process.process(event):
                async for _ in decorate.process(event):
                    pass
                await respond.process(event)
        return plugin, event, agent

    async def test_default_core_dispatch_preserves_plain_reply_and_other_plugin_image(self):
        plugin, event, agent = await self.run_pipeline()
        self.assertEqual(len(agent.requests), 1)
        self.assertIn("AstrBot 当前人格", agent.requests[0].system_prompt)
        self.assertNotIn("V2 人格资料", agent.requests[0].system_prompt)
        self.assertEqual([c.text for c in event.sent[0] if isinstance(c, Plain)], ["嘿嘿！\n\n再摸一下！"])
        self.assertEqual(sum(isinstance(c, Image) for c in event.sent[0]), 1)
        self.assertEqual(plugin.staging.authorize_core_reply.await_count, 2)
        plugin.staging.receive_claimed.assert_not_awaited()

    async def test_native_segmentation_keeps_each_paragraph_and_image(self):
        plugin, event, agent = await self.run_pipeline(segmented=True, companion_segmenting=True)
        self.assertEqual([[c.text for c in chain if isinstance(c, Plain)] for chain in event.sent],
                         [["嘿嘿！"], ["再摸一下！"], []])
        self.assertTrue(any(isinstance(c, Image) for c in event.sent[-1]))
        plugin.staging.receive_claimed.assert_not_awaited()

    async def test_native_tts_only_observes_actually_sent_record_text(self):
        plugin, event, agent = await self.run_pipeline(tts=True, companion_segmenting=True)
        self.assertEqual([c.text for c in event.sent[0] if isinstance(c, Record)], ["嘿嘿！\n\n再摸一下！"])
        self.assertFalse(any(isinstance(c, Plain) for c in event.sent[0]))
        self.assertEqual(plugin.staging.session.core_reply_observations.outputs,
                         [("嘿嘿！\n\n再摸一下！", "send_returned")])

    async def test_plugin_segmentation_splits_complete_private_reply_before_each_authorized_send(self):
        plugin, event, agent = await self.run_pipeline(companion_segmenting=True)
        bodies = [next(c.text.strip() for c in chain if isinstance(c, Plain))
                  for chain in event.sent if any(isinstance(c, Plain) for c in chain)]
        self.assertEqual(bodies, ["嘿嘿！", "再摸一下！"])
        self.assertTrue(any(isinstance(c, Image) for c in event.sent[-1]))
        self.assertEqual(plugin.staging.authorize_core_reply.await_count, 3)

    async def test_plugin_segmentation_reserves_complete_body_when_host_streaming_is_enabled(self):
        plugin, event, agent = await self.run_pipeline(streaming=True, companion_segmenting=True)
        self.assertIs(event.get_extra("enable_streaming"), False)
        self.assertEqual(event.streams, [])
        bodies = [next(c.text.strip() for c in chain if isinstance(c, Plain))
                  for chain in event.sent if any(isinstance(c, Plain) for c in chain)]
        self.assertEqual(bodies, ["嘿嘿！", "再摸一下！"])

    async def test_native_pipeline_cleans_each_segmented_text_bubble_before_send(self):
        plugin, event, agent = await self.run_pipeline(segmented=True, presentation={
            "content_cleanup_enabled": True, "content_cleanup_scope": "trailing",
            "content_cleanup_words": '["！"]', "content_replacement_enabled": True,
            "content_replacements": '["嘿嘿 => 哈哈"]'})
        self.assertEqual([[c.text for c in chain if isinstance(c, Plain)] for chain in event.sent],
                         [["哈哈"], ["再摸一下"], []])
        self.assertEqual(plugin.staging.session.core_reply_observations.outputs,
                         [("哈哈", "send_returned"), ("再摸一下", "send_returned")])
        self.assertEqual(plugin.staging.authorize_core_reply.await_count, 4)
        self.assertIs(event.send.__func__, PipelineEvent.send)

    async def test_plan_text_is_removed_before_history_and_native_send(self):
        # on_llm_response 里的闸门必须在历史写入、语音观测与分段之前生效。
        plugin, event, agent = await self.run_pipeline(
            response_text="hthought\n Let's call `newapi_emit_text_v1` now. 嘿嘿！\n\n再摸一下！")
        bodies = [c.text for chain in event.sent for c in chain if isinstance(c, Plain)]
        self.assertEqual(bodies, ["嘿嘿！\n\n再摸一下！"])
        self.assertEqual(plugin.staging.session.core_reply_observations.outputs,
                         [("嘿嘿！\n\n再摸一下！", "send_returned")])

    async def test_plan_text_is_removed_from_completion_text_and_result_chain(self):
        # 真实入口（带 result_chain 的 LLMResponse）上钩子不能抛异常，且
        # completion_text 与 chain 里的 Plain 都要被清理。plan_leak_guard 返回
        # 3 元组，按 2 元组解包会在这一条上直接 ValueError。
        leak = "hthought\n Let's call `newapi_emit_text_v1` now. 嘿嘿！\n\n再摸一下！"
        plugin, event, agent = await self.run_pipeline(response_text=leak, result_chain=True)
        response = agent.responses[0]
        self.assertEqual(response.completion_text, "嘿嘿！\n\n再摸一下！")
        self.assertEqual([part.text for part in response.result_chain.chain if isinstance(part, Plain)],
                         ["嘿嘿！\n\n再摸一下！"])
        bodies = [c.text for chain in event.sent for c in chain if isinstance(c, Plain)]
        self.assertEqual(bodies, ["嘿嘿！\n\n再摸一下！"])

    async def test_native_tts_dual_output_keeps_one_observation_for_the_same_body(self):
        plugin, event, agent = await self.run_pipeline(tts=True, dual=True, companion_segmenting=True)
        components = [c for chain in event.sent for c in chain]
        self.assertTrue(any(isinstance(c, Record) for c in components))
        self.assertTrue(any(isinstance(c, Plain) for c in components))
        self.assertEqual(sent_core_text(MessageEventResult(chain=components)), "嘿嘿！\n\n再摸一下！")
        # AstrBot sends Record separately from Plain. Both successful sends
        # carry the same body; the observation owner upgrades the same row.
        self.assertEqual(plugin.staging.session.core_reply_observations.outputs,
                         [("嘿嘿！\n\n再摸一下！", "send_returned"), ("嘿嘿！\n\n再摸一下！", "send_returned")])

    async def test_native_segmented_tts_observes_each_spoken_paragraph(self):
        plugin, event, agent = await self.run_pipeline(tts=True, segmented=True, companion_segmenting=True)
        self.assertEqual(plugin.staging.session.core_reply_observations.outputs,
                         [("嘿嘿！", "send_returned"), ("再摸一下！", "send_returned")])

    async def test_native_streaming_without_decoration_still_checks_permission_and_restores(self):
        plugin, event, agent = await self.run_pipeline(streaming=True)
        self.assertEqual(event.streams, ["嘿嘿，", "再摸一下！"])
        self.assertEqual(event.sent, [])
        self.assertEqual(plugin.staging.authorize_core_reply.await_count, 2)
        self.assertIs(event.send.__func__, PipelineEvent.send)
        self.assertIs(event.send_streaming.__func__, PipelineEvent.send_streaming)

    async def test_revoked_permission_stops_core_generation_before_any_response(self):
        plugin, event, agent = await self.run_pipeline(
            authorize=AsyncMock(return_value=Outcome.failed("private_access_dialogue_denied", "revoked")))
        self.assertTrue(event.is_stopped())
        self.assertEqual(event.sent, [])
        self.assertEqual(event.streams, [])
        plugin.staging.receive_claimed.assert_not_awaited()

    async def test_v2_japanese_voice_survives_native_regex_and_early_stream_selection(self):
        policy = VoiceReplyPolicy(generation_mode="model_tag", language="ja", foreign_text_mode="translation", segment_mode="each")
        plugin, event, agent = await self.run_pipeline(streaming=True, segmented=True,
            companion_segmenting=True, voice_policy=policy,
            response_text="<pc_tts>こんにちは。</pc_tts>你好呀！\n\n<pc_tts>もう一度。</pc_tts>再来一次吧！")
        self.assertEqual(event.streams, [])
        self.assertEqual([call.args[1] for call in plugin.staging.session.voice.await_args_list],
                         ["こんにちは。", "もう一度。"])
        components = [c for chain in event.sent for c in chain]
        self.assertEqual([c.text for c in components if isinstance(c, Plain)], ["你好呀！", "再来一次吧！"])
        self.assertEqual([c.text for c in components if isinstance(c, Record)], ["こんにちは。", "もう一度。"])
        self.assertEqual(sum(isinstance(c, Image) for c in components), 1)
        self.assertTrue(all(len(chain) == 1 for chain in event.sent if any(isinstance(c, Record) for c in chain)))
        self.assertIn("<pc_tts>", agent.requests[0].system_prompt)

    async def test_v2_tagged_original_speech_is_not_lost_when_native_regex_splits_the_span(self):
        policy = VoiceReplyPolicy(generation_mode="model_tag", segment_mode="each")
        plugin, event, _ = await self.run_pipeline(segmented=True, companion_segmenting=True,
            voice_policy=policy, response_text="<pc_tts>你好呀！\n再来一次吧！</pc_tts>")
        self.assertEqual([call.args[1] for call in plugin.staging.session.voice.await_args_list],
                         ["你好呀！", "再来一次吧！"])
        self.assertEqual([c.text for chain in event.sent for c in chain if isinstance(c, Plain)],
                         ["你好呀！", "再来一次吧！"])

    async def test_translation_replacement_keeps_the_authored_foreign_voice(self):
        policy = VoiceReplyPolicy(generation_mode="model_tag", language="ja", foreign_text_mode="translation", segment_mode="each")
        for native in (False, True):
            plugin, event, _ = await self.run_pipeline(segmented=native, companion_segmenting=True,
                voice_policy=policy, response_text="<pc_tts>こんにちは。</pc_tts>你好呀！\n\n<pc_tts>もう一度。</pc_tts>再来一次吧！",
                presentation={"content_replacement_enabled": True,
                              "content_replacements": '["你好呀 => 哈喽", "再来一次吧 => 再摸摸"]'})
            self.assertEqual([call.args[1] for call in plugin.staging.session.voice.await_args_list],
                             ["こんにちは。", "もう一度。"])
            self.assertEqual([c.text for chain in event.sent for c in chain if isinstance(c, Plain)],
                             ["哈喽！", "再摸摸！"])

    def test_whole_reply_synthesis_is_the_default_voice_segment_contract(self):
        """整段朗读是本版本的默认口径（上游 85f2ba8），「每一段」只是可选项。

        这条用例只锁默认值本身：将来若有人把默认改回 ``each``，这里会先红，
        并与 ``test_default_whole_voice_follows_all_text_segments_and_keeps_foreign_speech``
        的整段行为断言同时指认这次改动，而不是让上面三条按「每一段」断言的管线用例
        再次莫名其妙地转红。``each`` 必须继续是合法取值——已有安装保存的「每一段」
        不会被静默改写，那三条用例正是显式传 ``segment_mode="each"`` 跑通的。
        """
        self.assertEqual(VoiceReplyPolicy().segment_mode, "whole")
        self.assertEqual(VoiceReplyPolicy(segment_mode="each").segment_mode, "each")
        with self.assertRaises(ValueError):
            VoiceReplyPolicy(segment_mode="per-segment")

    async def test_default_whole_voice_follows_all_text_segments_and_keeps_foreign_speech(self):
        policy = VoiceReplyPolicy(generation_mode="model_tag", language="ja", foreign_text_mode="translation")
        for native in (False, True):
            with self.subTest(native_segmentation=native):
                plugin, event, _ = await self.run_pipeline(segmented=native, companion_segmenting=True,
                    voice_policy=policy,
                    response_text="<pc_tts>こんにちは。</pc_tts>你好呀！\n\n<pc_tts>もう一度。</pc_tts>再来一次吧！")
                self.assertEqual([call.args[1] for call in plugin.staging.session.voice.await_args_list],
                                 ["こんにちは。\nもう一度。"])
                components = [part for chain in event.sent for part in chain]
                self.assertEqual([part.text for part in components if isinstance(part, Plain)],
                                 ["你好呀！", "再来一次吧！"])
                self.assertEqual(sum(isinstance(part, Record) for part in components), 1)
                voice_index = next(i for i, part in enumerate(components) if isinstance(part, Record))
                self.assertGreater(voice_index, max(i for i, part in enumerate(components) if isinstance(part, Plain)))


if __name__ == "__main__":
    unittest.main()
