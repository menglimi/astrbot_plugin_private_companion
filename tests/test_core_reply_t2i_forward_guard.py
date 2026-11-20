"""Guard segmented and voiced core replies against t2i and forward_threshold leaks."""

from copy import deepcopy
from dataclasses import asdict
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch

from tests import astrbot_data_root  # noqa: F401  (isolate SDK root before importing AstrBot)
from tests.test_main_no_reply import ENTRY
from astrbot.core.config import DEFAULT_CONFIG
from astrbot.core.message.components import At, Image, Node, Plain, Record, Reply
from astrbot.core.message.message_event_result import MessageChain, MessageEventResult, ResultContentType
from astrbot.core.pipeline.process_stage.stage import ProcessStage
from astrbot.core.pipeline.respond.stage import RespondStage
from astrbot.core.pipeline.result_decorate import stage as decorate_module
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
from astrbot.core.platform.message_type import MessageType
from astrbot.core.provider.entities import LLMResponse, ProviderRequest
from companion_v2.adapters.astrbot.native import NativeConversationInput
from companion_v2.conversation.segment_control import LLM_SEGMENT_MARKER as MARKER
from companion_v2.conversation.segmentation import SegmentationPolicy
from companion_v2.conversation.voice_reply import VoiceReplyPolicy
from companion_v2.foundation.outcome import Outcome


class CoreDecorationEvent(AstrMessageEvent):
    def __init__(self, *, platform="aiocqhttp", message_type=MessageType.FRIEND_MESSAGE):
        message = AstrBotMessage()
        message.type = message_type
        message.self_id = "100"
        message.sender = MessageMember("200", "测试用户")
        message.message_id = "7001"
        message.message = [Plain("聊聊看")]
        message.raw_message = {"post_type": "message"}
        super().__init__("聊聊看", message, NS(id="qq-main", name=platform), "200")
        self.sent = []
        self.set_extra("activated_handlers", [])

    async def send(self, message):
        self.sent.append(list(message.chain))
        self._has_send_oper = True

    async def send_streaming(self, generator, use_fallback=False):
        async for chunk in generator:
            self.sent.append(list(chunk.chain))
        self._has_send_oper = True


class ScriptedAgentStage:
    def __init__(self, plugin, response_text, *, run_response_hook=True):
        self.plugin = plugin
        self.response_text = response_text
        self.run_response_hook = run_response_hook

    async def process(self, event):
        req = ProviderRequest(prompt=event.message_str, system_prompt="AstrBot 原生人格")
        await self.plugin.prepare_companion_core_request(event, req)
        if event.is_stopped():
            return
        text = self.response_text
        if self.run_response_hook:
            response = LLMResponse(role="assistant", completion_text=text)
            await self.plugin.observe_companion_model_session(event, response)
            text = response.completion_text
        result = MessageEventResult().message(text).set_result_content_type(ResultContentType.LLM_RESULT)
        event.set_result(result)
        yield


class CoreReplyDecorationGuardTests(unittest.IsolatedAsyncioTestCase):
    async def run_core_pipeline(
        self,
        response_text,
        *,
        t2i=False,
        t2i_word_threshold=500,
        forward_threshold=1500,
        segmented=False,
        words_count_threshold=150,
        reply_prefix="",
        native_tts=False,
        segmentation_policy=None,
        install_segmentation=True,
        voice_policy=None,
        presentation=None,
        outbound_settings=None,
        channel="private",
        platform="aiocqhttp",
        extra_decorator=None,
        run_response_hook=True,
        claim_event=True,
    ):
        plugin = ENTRY.PrivateCompanionV2StagingPlugin.__new__(ENTRY.PrivateCompanionV2StagingPlugin)
        target = NS(key="target", target_id="target")
        observed = []
        event = CoreDecorationEvent(
            platform=platform,
            message_type=MessageType.GROUP_MESSAGE if channel == "group" else MessageType.FRIEND_MESSAGE,
        )
        native_input = NativeConversationInput(
            "7001", NS(native_account_id="200"), None, None, "聊聊看", None, None, "",
        )
        prepared = {
            "context": object(),
            "instruction": "V2 人格资料",
            "prompt_instruction": "自然交谈",
            "target_id": "target",
            "channel": channel,
            "event_id": "7001",
            "native_input": native_input,
            "presentation_settings": presentation if presentation is not None else {"content_cleanup_enabled": False},
            "voice_reply_policy": voice_policy,
            "outbound_settings": outbound_settings,
        }
        if install_segmentation:
            policy = segmentation_policy or SegmentationPolicy(enabled=True, llm_controlled=True)
            prepared["segmentation_settings"] = {
                **asdict(policy),
                "streaming": False,
                "part_gap_mode": "fixed",
                "part_gap_ms": 0,
            }

        voice_mock = AsyncMock(return_value={"type": "record", "data": {"file": "base64://YQ=="}})
        plugin.staging = NS(
            claims=lambda _event: claim_event,
            target_for_event=lambda _event: target if claim_event else None,
            persona_mapping_for_event=AsyncMock(return_value=NS(role_id="role")),
            session_topology=NS(reply_enabled=AsyncMock(return_value=True), schedule_observe=lambda _: None),
            prepare_core_reply=AsyncMock(return_value=Outcome.success(prepared)),
            authorize_core_reply=AsyncMock(return_value=Outcome.success(True)),
            receive_claimed=AsyncMock(),
            runtime=None,
            now=lambda: None,
            group_reply_candidate=AsyncMock(return_value=Outcome.success({
                "admitted": True, "reason": "mention", "mentioned": True,
                "context_key": "group-ctx", "event_id": "7001",
            })),
            session=NS(
                core_reply_observations=NS(
                    outputs=observed,
                    output=lambda _prepared, text, **kwargs: observed.append((text, kwargs["state"]))
                    if kwargs.get("state") == "send_returned" else None,
                ),
                voice=voice_mock,
                voice_delivery=None,
            ),
        )

        config = deepcopy(DEFAULT_CONFIG)
        config["t2i"] = t2i
        config["t2i_word_threshold"] = t2i_word_threshold
        config["t2i_use_file_service"] = False
        config["provider_tts_settings"].update(enable=native_tts, dual_output=False, use_file_service=False)
        config["platform_settings"]["forward_threshold"] = forward_threshold
        config["platform_settings"]["reply_prefix"] = reply_prefix
        config["platform_settings"]["reply_with_mention"] = False
        config["platform_settings"]["reply_with_quote"] = False
        config["platform_settings"]["segmented_reply"].update(
            enable=segmented,
            words_count_threshold=words_count_threshold,
            split_mode="regex",
            regex=r".*?[。？！~…]+|.+$",
            interval="0,0",
            interval_method="random",
        )
        plugin.context = NS(get_config=lambda _umo=None: config)

        if claim_event:
            await plugin.receive_companion_message(event)
        else:
            event.is_wake = True
            event.is_at_or_wake_command = True

        native_tts_provider = NS(get_audio=AsyncMock(return_value="native-tts.wav")) if native_tts else None

        async def get_tts_provider(_umo):
            return native_tts_provider

        pipeline_context = NS(
            astrbot_config=config,
            plugin_manager=NS(context=NS(
                get_using_tts_provider=lambda _umo: native_tts_provider,
                get_using_tts_provider_async=get_tts_provider,
            )),
        )
        process = ProcessStage()
        process.ctx = pipeline_context
        process.agent_sub_stage = ScriptedAgentStage(plugin, response_text, run_response_hook=run_response_hook)
        decorate = decorate_module.ResultDecorateStage()
        await decorate.initialize(pipeline_context)
        respond = RespondStage()
        await respond.initialize(pipeline_context)

        handlers = [
            NS(handler_module_path="core_test", handler_name="v2_tool_repeat",
               handler=plugin.suppress_companion_native_tool_repeat),
        ]
        if extra_decorator is not None:
            handlers.append(NS(handler_module_path="core_test", handler_name="extra", handler=extra_decorator))
        handlers.append(
            NS(handler_module_path="core_test", handler_name="v2_guard",
               handler=plugin.guard_companion_core_delivery),
        )

        async def after_send(current, *_):
            await plugin.observe_companion_core_reply_sent(current)
            return False

        render_t2i = AsyncMock(return_value="https://example.invalid/rendered.png")
        with patch.object(decorate_module.star_handlers_registry, "get_handlers_by_event_type", return_value=handlers), \
                patch.dict(decorate_module.star_map, {"core_test": NS(name="core_test")}), \
                patch.object(decorate_module.html_renderer, "render_t2i", render_t2i), \
                patch.object(decorate_module.SessionServiceManager, "should_process_tts_request",
                             AsyncMock(return_value=native_tts)), \
                patch("astrbot.core.pipeline.respond.stage.call_event_hook", side_effect=after_send):
            async for _ in process.process(event):
                async for _ in decorate.process(event):
                    pass
                await respond.process(event)
        return plugin, event, render_t2i, voice_mock, native_tts_provider

    async def test_long_segmented_reply_with_authored_tts_skips_t2i_and_never_leaks_split_marker(self):
        seg1 = "第一段开场聊今天的晚风，还顺便回忆起昨天一起走过的那条长巷。" * 8
        seg2 = "第二段接着说明天一早出发前记得带上外套和热茶，别又忘在门边。" * 8
        seg3 = "第三段轻轻道个晚安，好好休息呀。" * 8
        self.assertGreater(len(seg1 + seg2 + seg3), 500)
        draft = (
            f"<pc_tts>一番目のメッセージです。</pc_tts>{seg1}\n{MARKER}\n"
            f"<pc_tts>二番目のメッセージです。</pc_tts>{seg2}\n{MARKER}\n"
            f"<pc_tts>おやすみなさい。</pc_tts>{seg3}"
        )
        voice_policy = VoiceReplyPolicy(
            generation_mode="model_tag", language="ja", foreign_text_mode="translation", segment_mode="each",
        )
        _, event, render_t2i, voice_mock, _ = await self.run_core_pipeline(
            draft,
            t2i=True,
            t2i_word_threshold=500,
            voice_policy=voice_policy,
        )
        render_t2i.assert_not_awaited()
        self.assertEqual([call.args[1] for call in voice_mock.await_args_list],
                         ["一番目のメッセージです。", "二番目のメッセージです。", "おやすみなさい。"])
        sent_texts = [comp.text.strip() for chain in event.sent for comp in chain if isinstance(comp, Plain)]
        self.assertEqual(sent_texts, [seg1, seg2, seg3])
        self.assertEqual(sum(isinstance(comp, Record) for chain in event.sent for comp in chain), 3)
        self.assertNotIn("PRIVATE_COMPANION_SPLIT", str(event.sent))
        self.assertNotIn("pc_tts", str(event.sent))

    async def test_long_single_voice_reply_without_splits_skips_t2i_and_preserves_authored_tts(self):
        body = "这段长独白不带分段标记，但本身带着口语演绎与配音，不能被文本转图片吞掉。" * 16
        self.assertGreater(len(body), 500)
        voice_policy = VoiceReplyPolicy(
            generation_mode="model_tag", language="ja", foreign_text_mode="translation", segment_mode="whole",
        )
        _, event, render_t2i, voice_mock, _ = await self.run_core_pipeline(
            f"<pc_tts>長い独白ですが音声を残します。</pc_tts>{body}",
            t2i=True,
            t2i_word_threshold=500,
            segmentation_policy=SegmentationPolicy(enabled=False),
            voice_policy=voice_policy,
        )
        render_t2i.assert_not_awaited()
        self.assertEqual([call.args[1] for call in voice_mock.await_args_list],
                         ["長い独白ですが音声を残します。"])
        self.assertEqual([comp.text for chain in event.sent for comp in chain if isinstance(comp, Plain)], [body])
        self.assertEqual(sum(isinstance(comp, Record) for chain in event.sent for comp in chain), 1)

    async def test_unsegmented_non_voice_t2i_reply_sanitizes_stray_split_marker_and_secrets(self):
        secret = "ghp_" + "a" * 32
        part1 = "这是一段关闭陪伴分段后的超长文字回复，用于测试图片渲染兜底清理。" * 14
        part2 = f"结尾还混入了一枚意外令牌 {secret}。" * 6
        draft = f"{part1}\n{MARKER}\n<pc_tts>不要泄漏标签</pc_tts>{part2}"
        self.assertGreater(len(part1) + len(part2), 550)
        _, event, render_t2i, _, _ = await self.run_core_pipeline(
            draft,
            t2i=True,
            t2i_word_threshold=500,
            segmentation_policy=SegmentationPolicy(enabled=False),
            voice_policy=None,
        )
        render_t2i.assert_awaited_once()
        rendered_text = render_t2i.await_args.args[0]
        self.assertNotIn(MARKER, rendered_text)
        self.assertNotIn("PRIVATE_COMPANION_SPLIT", rendered_text)
        self.assertNotIn("pc_tts", rendered_text)
        self.assertNotIn(secret, rendered_text)
        self.assertIn("[REDACTED]", rendered_text)
        self.assertEqual([[type(comp) for comp in chain] for chain in event.sent], [[Image]])

    async def test_over_forward_threshold_segmented_reply_with_authored_tts_and_outbound_keeps_bubbles(self):
        seg1 = "第一部分先认真总结白天发生的事情，把每个细节都慢慢讲给你听。" * 24
        seg2 = "第二部分接着补充晚上散步时看到的路灯和小猫，篇幅故意拉得很长。" * 24
        seg3 = "第三部分最后再叮嘱一句早点睡，明天早上见呀。" * 24
        total_len = len(seg1 + seg2 + seg3)
        self.assertGreater(total_len, 1500)
        draft = (
            f"<pc_tts>一回目の音声です。</pc_tts>{seg1}\n{MARKER}\n"
            f"<pc_tts>二回目の音声です。</pc_tts>{seg2}\n{MARKER}\n"
            f"<pc_tts>三回目の音声です。</pc_tts>{seg3}"
        )
        for mode in ("each", "whole"):
            with self.subTest(voice_segment_mode=mode):
                voice_policy = VoiceReplyPolicy(
                    generation_mode="model_tag", language="ja", foreign_text_mode="translation",
                    segment_mode=mode,
                )
                _, event, render_t2i, voice_mock, _ = await self.run_core_pipeline(
                    draft,
                    t2i=True,
                    t2i_word_threshold=500,
                    forward_threshold=1500,
                    channel="group",
                    voice_policy=voice_policy,
                    outbound_settings={"reply_quote_enabled": True, "reply_mention_speaker_enabled": True},
                )
                render_t2i.assert_not_awaited()
                self.assertFalse(any(isinstance(comp, Node) for chain in event.sent for comp in chain))
                sent_texts = [comp.text.strip() for chain in event.sent for comp in chain if isinstance(comp, Plain)]
                self.assertEqual(sent_texts, [seg1, seg2, seg3])
                self.assertNotIn("PRIVATE_COMPANION_SPLIT", str(event.sent))
                self.assertNotIn("pc_tts", str(event.sent))
                first_chain_types = [type(comp) for comp in event.sent[0]]
                self.assertEqual(first_chain_types, [Reply, At, Plain])
                if mode == "each":
                    self.assertEqual([call.args[1] for call in voice_mock.await_args_list],
                                     ["一回目の音声です。", "二回目の音声です。", "三回目の音声です。"])
                    self.assertEqual(sum(isinstance(comp, Record) for chain in event.sent for comp in chain), 3)
                else:
                    self.assertEqual([call.args[1] for call in voice_mock.await_args_list],
                                     ["一回目の音声です。\n二回目の音声です。\n三回目の音声です。"])
                    self.assertEqual(sum(isinstance(comp, Record) for chain in event.sent for comp in chain), 1)

    async def test_over_forward_threshold_without_segmentation_or_voice_keeps_clean_forward_node(self):
        body1 = "关闭分段且无语音时的长回复前半部分。" * 50
        body2 = "关闭分段且无语音时的长回复后半部分。" * 50
        draft = f"{body1}\n{MARKER}\n{body2}"
        self.assertGreater(len(body1 + body2), 1500)
        _, event, render_t2i, _, _ = await self.run_core_pipeline(
            draft,
            t2i=False,
            forward_threshold=1500,
            segmentation_policy=SegmentationPolicy(enabled=False),
            voice_policy=None,
        )
        render_t2i.assert_not_awaited()
        self.assertEqual(len(event.sent), 1)
        self.assertEqual([type(comp) for comp in event.sent[0]], [Node])
        node_text = "".join(part.text for part in event.sent[0][0].content if isinstance(part, Plain))
        self.assertNotIn(MARKER, node_text)
        self.assertNotIn("PRIVATE_COMPANION_SPLIT", node_text)
        self.assertIn(body1, node_text)
        self.assertIn(body2, node_text)

    async def test_genuine_plugin_forward_node_is_never_unwrapped(self):
        plugin_node = Node(uin="999", name="OtherPlugin", content=[Plain("外部插件的转发节点。")])

        async def add_plugin_node(event):
            result = event.get_result()
            result.chain = [plugin_node]

        _, event, _, _, _ = await self.run_core_pipeline(
            "原始回复",
            t2i=True,
            t2i_word_threshold=500,
            forward_threshold=10,
            voice_policy=VoiceReplyPolicy(generation_mode="plain"),
            extra_decorator=add_plugin_node,
        )
        self.assertEqual(len(event.sent), 1)
        self.assertEqual([type(comp) for comp in event.sent[0]], [Node])
        self.assertIs(event.sent[0][0], plugin_node)

    async def test_voice_without_segmentation_wrapper_still_unwraps_forward_node_in_guarded_send(self):
        body = "没有安装分段包装器，但启用了陪伴语音的超长单段回复内容。" * 60
        self.assertGreater(len(body), 1500)
        voice_policy = VoiceReplyPolicy(
            generation_mode="model_tag", language="ja", foreign_text_mode="translation",
        )
        _, event, render_t2i, voice_mock, _ = await self.run_core_pipeline(
            f"<pc_tts>長い返信でも音声は届きます。</pc_tts>{body}",
            t2i=True,
            t2i_word_threshold=500,
            forward_threshold=1500,
            install_segmentation=False,
            voice_policy=voice_policy,
        )
        render_t2i.assert_not_awaited()
        self.assertFalse(any(isinstance(comp, Node) for chain in event.sent for comp in chain))
        self.assertEqual([call.args[1] for call in voice_mock.await_args_list],
                         ["長い返信でも音声は届きます。"])
        self.assertEqual([comp.text for chain in event.sent for comp in chain if isinstance(comp, Plain)], [body])
        self.assertEqual(sum(isinstance(comp, Record) for chain in event.sent for comp in chain), 1)

    async def test_skills_like_uncaptured_voice_tag_and_merged_max_segments_keep_all_speech(self):
        seg1 = "第一句稍微长一些的中文回应。" * 10
        seg2 = "第二句继续往下聊的中文回应。" * 10
        seg3 = "第三句作为补充收尾的中文回应。" * 10
        draft = (
            f"<pc_tts>一つ目の文です。</pc_tts>{seg1}\n{MARKER}\n"
            f"<pc_tts>二つ目の文です。</pc_tts>{seg2}\n{MARKER}\n"
            f"<pc_tts>三つ目の文です。</pc_tts>{seg3}"
        )
        voice_policy = VoiceReplyPolicy(
            generation_mode="model_tag", language="ja", foreign_text_mode="translation", segment_mode="each",
        )
        _, event, render_t2i, voice_mock, _ = await self.run_core_pipeline(
            draft,
            t2i=True,
            t2i_word_threshold=500,
            segmentation_policy=SegmentationPolicy(enabled=True, llm_controlled=True, max_segments=2),
            voice_policy=voice_policy,
            presentation={
                "content_cleanup_enabled": True,
                "content_cleanup_words": '["。"]',
                "content_cleanup_scope": "trailing",
            },
            run_response_hook=False,
        )
        render_t2i.assert_not_awaited()
        self.assertEqual([call.args[1] for call in voice_mock.await_args_list],
                         ["一つ目の文です。\n二つ目の文です。", "三つ目の文です。"])
        sent_texts = [comp.text for chain in event.sent for comp in chain if isinstance(comp, Plain)]
        self.assertEqual(len(sent_texts), 2)
        self.assertTrue(all(not text.endswith("。") for text in sent_texts))
        self.assertNotIn("PRIVATE_COMPANION_SPLIT", "".join(sent_texts))
        self.assertNotIn("pc_tts", "".join(sent_texts))

    async def test_native_tts_and_non_idempotent_cleanup_remain_clean_and_single_pass(self):
        draft = f"第一段，aabb。\n{MARKER}\n第二段，继续。"
        _, event, _, _, native_tts_provider = await self.run_core_pipeline(
            draft,
            native_tts=True,
            segmentation_policy=SegmentationPolicy(enabled=True, llm_controlled=True),
            presentation={
                "content_cleanup_enabled": True,
                "content_cleanup_words": '["ab"]',
                "content_cleanup_scope": "all",
            },
        )
        native_tts_provider.get_audio.assert_awaited_once()
        spoken = native_tts_provider.get_audio.await_args.args[0]
        self.assertNotIn(MARKER, spoken)
        self.assertNotIn("PRIVATE_COMPANION_SPLIT", spoken)
        self.assertEqual(sum(isinstance(comp, Record) for chain in event.sent for comp in chain), 1)

        _, plain_event, _, _, _ = await self.run_core_pipeline(
            "aabb",
            t2i=False,
            segmentation_policy=SegmentationPolicy(enabled=False),
            presentation={
                "content_cleanup_enabled": True,
                "content_cleanup_words": '["ab"]',
                "content_cleanup_scope": "all",
            },
        )
        self.assertEqual([comp.text for chain in plain_event.sent for comp in chain if isinstance(comp, Plain)],
                         ["ab"])

    async def test_native_segmented_reply_with_high_threshold_and_forward_node_preserves_companion_segments(self):
        seg1 = "第一句先讲清背景和前因。" * 45
        seg2 = "第二句补充方案细节与边界。" * 45
        seg3 = "第三句给出最后结论与提示。" * 45
        draft = f"{seg1}\n{MARKER}\n{seg2}\n{MARKER}\n{seg3}"
        self.assertGreater(len(seg1 + seg2 + seg3), 1500)
        _, event, render_t2i, _, _ = await self.run_core_pipeline(
            draft,
            t2i=True,
            t2i_word_threshold=500,
            forward_threshold=1500,
            segmented=True,
            words_count_threshold=3000,
            reply_prefix="[前缀] ",
        )
        render_t2i.assert_not_awaited()
        self.assertFalse(any(isinstance(comp, Node) for chain in event.sent for comp in chain))
        sent_texts = [comp.text.strip() for chain in event.sent for comp in chain if isinstance(comp, Plain)]
        self.assertEqual(sent_texts, ["[前缀] " + seg1, seg2, seg3])

    async def test_rule_segmentation_above_t2i_threshold_skips_t2i_and_sends_bubbles(self):
        s1 = "第一句通过规则切分保留为独立气泡，" * 18 + "以便跨过阈值。"
        s2 = "第二句同样通过规则切分保留为独立气泡，" * 18 + "不触发文本转图片。"
        draft = s1 + s2
        self.assertGreater(len(draft), 500)
        _, event, render_t2i, _, _ = await self.run_core_pipeline(
            draft,
            t2i=True,
            t2i_word_threshold=500,
            segmentation_policy=SegmentationPolicy(enabled=True, llm_controlled=False, threshold=1000, max_segments=2),
            voice_policy=None,
        )
        render_t2i.assert_not_awaited()
        sent_texts = [comp.text for chain in event.sent for comp in chain if isinstance(comp, Plain)]
        self.assertEqual(sent_texts, [s1, s2])

    async def test_voice_authoring_repair_above_t2i_and_forward_threshold_preserves_speech(self):
        from astrbot_host.core_reply_voice_authoring import _spoken_plan
        seg1 = "第一段没有手写标签，但由补全计划生成了对应日文朗读。" * 35
        seg2 = "第二段同样由补全计划绑定，不能在长回复转发包装中丢失。" * 35
        draft = f"{seg1}\n{MARKER}\n{seg2}"
        self.assertGreater(len(seg1 + seg2), 1500)
        voice_policy = VoiceReplyPolicy(
            generation_mode="model_tag", language="ja", foreign_text_mode="translation", segment_mode="each",
        )

        async def attach_repaired_plan(event):
            core = event.get_extra(ENTRY.CORE_REPLY_CONTEXT_EXTRA)
            core["_voice_authored"] = _spoken_plan(
                '{"segments":[{"index":1,"speech":"一つ目の補完音声です。"},{"index":2,"speech":"二つ目の補完音声です。"}]}',
                (seg1 + "\n", seg2),
            )

        _, event, render_t2i, voice_mock, _ = await self.run_core_pipeline(
            draft,
            t2i=True,
            t2i_word_threshold=500,
            forward_threshold=1500,
            voice_policy=voice_policy,
            extra_decorator=attach_repaired_plan,
        )
        render_t2i.assert_not_awaited()
        self.assertFalse(any(isinstance(comp, Node) for chain in event.sent for comp in chain))
        self.assertEqual([call.args[1] for call in voice_mock.await_args_list],
                         ["一つ目の補完音声です。", "二つ目の補完音声です。"])
        sent_texts = [comp.text.strip() for chain in event.sent for comp in chain if isinstance(comp, Plain)]
        self.assertEqual(sent_texts, [seg1, seg2])

    async def test_non_companion_event_keeps_standard_astrbot_t2i_behavior(self):
        long_text = "非陪伴插件托管的普通原生指令长结果，应当保持 AstrBot 默认文本转图片行为。" * 15
        self.assertGreater(len(long_text), 500)
        _, event, render_t2i, _, _ = await self.run_core_pipeline(
            long_text,
            t2i=True,
            t2i_word_threshold=500,
            claim_event=False,
        )
        render_t2i.assert_awaited_once()
        self.assertEqual([[type(comp) for comp in chain] for chain in event.sent], [[Image]])


if __name__ == "__main__":
    unittest.main()
