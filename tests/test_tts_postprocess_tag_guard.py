# -*- coding: utf-8 -*-
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from astrbot.api.message_components import At, Plain, Record
from astrbot_plugin_private_companion.tts_enhancement import TtsEnhancementMixin


class _TtsHarness(TtsEnhancementMixin):
    def __init__(self):
        self.enable_tts_enhancement = True
        self.tts_generation_mode = "postprocess"

    def _feature_enabled_or_temp_unlocked(self, key):
        return key == "enable_tts_enhancement"


class TtsPostprocessTagGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_tts_runs_before_restoring_upstream_plain_segments(self):
        harness = _TtsHarness()
        harness.enabled = True
        harness.tts_generation_mode = "postprocess"
        harness.tts_delivery_mode = "voice_and_text"
        harness.enable_segmented_proactive_reply = False
        harness.segmented_proactive_scope = "proactive_only"
        visible = harness._mark_tts_visible_plain("第一段。第二段。第三段。")
        harness._maybe_convert_plain_reply_to_tts = AsyncMock(
            return_value=[Record(file="voice.wav"), visible]
        )
        harness._build_result_from_chain = lambda chain: SimpleNamespace(chain=list(chain))
        background_tasks = []
        harness._create_lifecycle_background_task = (
            lambda coro, *, label="": background_tasks.append(coro)
        )

        class Event:
            unified_msg_origin = "default:FriendMessage:10001"
            _private_companion_tts_request_applied = True

            def __init__(self):
                self.result = SimpleNamespace(
                    chain=[Plain("第一段。"), Plain("第二段。"), Plain("第三段。")]
                )
                self.sent = []

            def get_result(self):
                return self.result

            def set_result(self, value):
                self.result = value

            @staticmethod
            def chain_result(chain):
                return SimpleNamespace(chain=list(chain))

            async def send(self, result):
                self.sent.append(result)

        event = Event()
        await harness.apply_tts_enhancement_before_send(event)

        harness._maybe_convert_plain_reply_to_tts.assert_awaited_once_with(
            "第一段。第二段。第三段。",
            event,
        )
        self.assertEqual(1, len(event.result.chain))
        self.assertIsInstance(event.result.chain[0], Record)
        self.assertEqual(1, len(background_tasks))
        with patch(
            "astrbot_plugin_private_companion.tts_enhancement.asyncio.sleep",
            new=AsyncMock(),
        ):
            await background_tasks[0]

        self.assertEqual(
            ["第一段。", "第二段。", "第三段。"],
            [item.chain[0].text for item in event.sent],
        )

    def test_changed_visible_text_does_not_reuse_stale_source_segments(self):
        harness = _TtsHarness()
        harness.enable_segmented_proactive_reply = False
        harness.segmented_proactive_scope = "proactive_only"
        event = SimpleNamespace(
            unified_msg_origin="test-session",
            _private_companion_tts_source_plain_segments=("第一段。", "第二段。"),
        )
        visible = harness._mark_tts_visible_plain("改写后的完整正文。")

        chunks = harness._tts_segment_plain_chunk_for_ordered_send(event, [visible])

        self.assertEqual(1, len(chunks))
        self.assertEqual("改写后的完整正文。", chunks[0][0].text)

    def test_slash_command_is_recognized_as_functional_reply_context(self):
        harness = _TtsHarness()
        event = SimpleNamespace(message_str="/fc", is_command=False, is_admin_command=False)

        self.assertEqual("command_prefix", harness._tts_functional_command_reason(event))
        self.assertFalse(harness._event_explicitly_requests_tts(event))

    async def test_functional_command_prompt_prefers_readable_text(self):
        harness = _TtsHarness()
        harness.enabled = True
        harness.tts_generation_mode = "fast_tag"
        harness.context = SimpleNamespace(get_config=lambda _umo: {})
        event = SimpleNamespace(
            unified_msg_origin="test-session",
            message_str="/fc",
            is_command=True,
        )
        request = SimpleNamespace(system_prompt="base")

        await harness.apply_tts_enhancement_request(event, request)

        self.assertIn("功能性回复的语音取舍", request.system_prompt)
        self.assertIn("优先把执行结果", request.system_prompt)
        self.assertNotIn("【语音消息规则】", request.system_prompt)

    async def test_non_main_chain_result_is_not_auto_converted_to_voice(self):
        harness = _TtsHarness()
        harness.enabled = True
        harness.tts_generation_mode = "fast_tag"
        harness._maybe_convert_plain_reply_to_tts = AsyncMock(return_value=[Record(file="voice.wav")])
        original = Plain("正在生成结果。")
        result = SimpleNamespace(chain=[original])

        class Event:
            unified_msg_origin = "test-session"
            message_str = "/fc"

            @staticmethod
            def get_result():
                return result

        await harness.apply_tts_enhancement_before_send(Event())

        harness._maybe_convert_plain_reply_to_tts.assert_not_awaited()
        self.assertIs(original, result.chain[0])

    async def test_non_main_chain_response_is_not_mutated_by_tts_hook(self):
        harness = _TtsHarness()
        harness.tts_generation_mode = "fast_tag"
        response = SimpleNamespace(completion_text="<pc_tts>正在生成结果。</pc_tts>")
        event = SimpleNamespace(
            unified_msg_origin="test-session",
            message_str="/fc",
            is_command=True,
        )

        await harness.protect_tts_enhancement_response_blocks(event, response)

        self.assertEqual("<pc_tts>正在生成结果。</pc_tts>", response.completion_text)

    async def test_main_chain_marker_allows_auto_conversion(self):
        harness = _TtsHarness()
        harness.enabled = True
        harness.tts_generation_mode = "fast_tag"
        harness._maybe_convert_plain_reply_to_tts = AsyncMock(
            return_value=[Record(file="voice.wav")]
        )
        harness._build_result_from_chain = lambda chain: SimpleNamespace(chain=list(chain))

        class Event:
            unified_msg_origin = "test-session"
            message_str = "普通聊天"
            _private_companion_tts_request_applied = True

            def __init__(self):
                self.result = SimpleNamespace(chain=[Plain("自然聊天回复。")])

            def get_result(self):
                return self.result

            def set_result(self, value):
                self.result = value

        event = Event()
        await harness.apply_tts_enhancement_before_send(event)

        harness._maybe_convert_plain_reply_to_tts.assert_awaited_once()
        self.assertTrue(any(isinstance(component, Record) for component in event.result.chain))

    def test_provider_safety_refusal_voice_block_is_removed_without_dropping_fallback(self):
        harness = _TtsHarness()
        source = (
            "<tts>您的请求包含低俗色情内容，不符合公序良俗和社会道德，已被平台拒绝。"
            "我们应当倡导文明健康的交流。</tts>"
            "<tts>备用模型生成的正常语音。</tts>后续正常正文。"
        )

        cleaned, removed = harness._drop_tts_provider_safety_blocks(source)

        self.assertTrue(removed)
        self.assertNotIn("低俗色情", cleaned)
        self.assertNotIn("平台拒绝", cleaned)
        self.assertIn("<tts>备用模型生成的正常语音。</tts>", cleaned)
        self.assertIn("后续正常正文", cleaned)

    def test_provider_safety_term_in_explanation_is_not_treated_as_refusal(self):
        harness = _TtsHarness()
        source = (
            "<tts>The phrase content policy violation can appear in an error log, "
            "but your request is fine.</tts>"
        )

        cleaned, removed = harness._drop_tts_provider_safety_blocks(source)

        self.assertFalse(removed)
        self.assertEqual(source, cleaned)

    async def test_safety_only_voice_block_is_demoted_to_visible_plain_text(self):
        harness = _TtsHarness()
        response = SimpleNamespace(
            completion_text=(
                "<tts>您的请求包含低俗色情内容，不符合公序良俗，已被平台拒绝。</tts>"
            )
        )
        event = SimpleNamespace(
            unified_msg_origin="test-session",
            _private_companion_tts_request_applied=True,
        )

        await harness.protect_tts_enhancement_response_blocks(event, response)

        self.assertEqual(
            "您的请求包含低俗色情内容，不符合公序良俗，已被平台拒绝。",
            response.completion_text,
        )

    async def test_empty_fast_tag_processing_preserves_tagged_source_as_plain_text(self):
        harness = _TtsHarness()
        harness.enabled = True
        harness.tts_generation_mode = "fast_tag"
        harness.tts_voice_language = "ja"
        harness._process_tts_tags = AsyncMock(return_value=[])
        harness._build_result_from_chain = lambda chain: SimpleNamespace(chain=list(chain))
        token = "0123456789abcdef"
        spoken = "今日はゆっくり休んでね。"

        class Event:
            unified_msg_origin = "default:FriendMessage:10001"
            _private_companion_tts_request_applied = True
            _private_companion_tts_block_tokens = {token: f"<tts>{spoken}</tts>"}

            def __init__(self):
                self.result = SimpleNamespace(chain=[Plain(f"[[PCTTS:{token}]]")])

            def get_result(self):
                return self.result

            def set_result(self, value):
                self.result = value

        event = Event()
        await harness.apply_tts_enhancement_before_send(event)

        self.assertEqual(1, len(event.result.chain))
        self.assertEqual(spoken, event.result.chain[0].text)

    async def test_tts_synthesis_and_translation_failure_preserve_spoken_text(self):
        harness = _TtsHarness()
        harness.tts_generation_mode = "fast_tag"
        harness.tts_voice_language = "ja"
        harness.tts_delivery_mode = "voice_and_text"
        harness.tts_foreign_text_mode = "translation"
        harness.tts_frequency_control_mode = "legacy"
        harness._resolve_tts_synthesis_provider = lambda _event, provider: provider
        harness._tts_provider_kind = lambda *_args, **_kwargs: "generic"
        harness._tts_record_component = AsyncMock(return_value=None)
        harness._translate_tts_spoken_to_chinese = AsyncMock(return_value="")
        harness._tts_text_needs_language_conversion = lambda *_args, **_kwargs: False
        spoken = "今日はゆっくり休んでね。"

        chain = await harness._process_tts_tags(
            f"<tts>{spoken}</tts>",
            object(),
            provider_settings={},
            config={},
        )

        self.assertEqual(1, len(chain))
        self.assertEqual(spoken, chain[0].text)

    async def test_provider_safety_refusal_plain_text_never_enters_auto_tts(self):
        harness = _TtsHarness()
        event = SimpleNamespace(unified_msg_origin="test-session")
        refusal = "您的请求包含低俗色情内容，不符合公序良俗，已被平台拒绝。"

        chain = await harness._maybe_convert_plain_reply_to_tts(refusal, event)

        self.assertEqual([], chain)
        self.assertEqual(
            "",
            harness._sanitize_tts_spoken_text(refusal, provider_kind="generic"),
        )

    async def test_tts_processing_skips_provider_refusal_and_keeps_fallback_plain(self):
        harness = _TtsHarness()
        harness.tts_voice_language = "zh"
        harness.tts_delivery_mode = "voice_and_text"
        harness.tts_conversion_scope = "full"
        harness._tts_record_component = AsyncMock(return_value=Record(file="voice.wav"))
        refusal = (
            "您的请求包含低俗色情内容，不符合公序良俗和社会道德，已被平台拒绝。"
            "我们应当倡导文明健康的交流。"
        )
        fallback = "备用模型生成的正常回复。"

        components = await harness._process_tts_tags(
            f"<tts>{refusal}</tts>",
            object(),
            provider_settings={},
            config={},
            fallback_plain=fallback,
        )

        harness._tts_record_component.assert_not_awaited()
        self.assertFalse(any(isinstance(component, Record) for component in components))
        self.assertEqual(
            [fallback],
            [component.text for component in components if isinstance(component, Plain)],
        )

    async def test_send_hook_drops_old_safety_voice_and_keeps_valid_fallback_voice(self):
        harness = _TtsHarness()
        harness.enabled = True
        harness.tts_generation_mode = "fast_tag"
        harness.tts_conversion_scope = "partial"
        harness._process_tts_tags = AsyncMock(return_value=[Plain("processed fallback")])
        harness._build_result_from_chain = lambda chain: SimpleNamespace(chain=list(chain))

        class Event:
            unified_msg_origin = "test-session"
            _private_companion_tts_request_applied = True

            def __init__(self):
                self.result = SimpleNamespace(
                    chain=[
                        Plain(
                            "<tts>您的请求包含低俗色情内容，不符合公序良俗，已被平台拒绝。</tts>"
                            "<tts>备用模型的正常回复。</tts>"
                        )
                    ]
                )

            def get_result(self):
                return self.result

            def set_result(self, value):
                self.result = value

        event = Event()
        await harness.apply_tts_enhancement_before_send(event)

        call = harness._process_tts_tags.await_args
        self.assertNotIn("低俗色情", call.args[0])
        self.assertNotIn("平台拒绝", call.args[0])
        self.assertIn("<tts>备用模型的正常回复。</tts>", call.args[0])
        self.assertEqual("processed fallback", event.result.chain[0].text)

    async def test_explicit_voice_request_can_override_command_default(self):
        harness = _TtsHarness()
        harness.tts_generation_mode = "fast_tag"
        harness.tts_conversion_scope = "full"
        harness.tts_frequency_control_mode = "global"
        harness.tts_constraint_mode = "weak"
        harness.auto_voice_enabled = True
        harness._tts_auto_voice_last_at = {}
        harness._auto_voice_trigger_reason = lambda _text, _event: (True, "auto")
        harness._tts_trigger_probability_allows = lambda _event, reason="": True
        harness._process_tts_tags = AsyncMock(return_value=[Record(file="voice.wav")])
        event = SimpleNamespace(
            unified_msg_origin="test-session",
            message_str="/say 请用语音回复这句话",
            is_command=True,
        )

        result = await harness._maybe_convert_plain_reply_to_tts("我会念给你听。", event)

        self.assertTrue(any(isinstance(component, Record) for component in result))
        harness._process_tts_tags.assert_awaited_once()

    def test_tts_component_metadata_survives_pydantic_components(self):
        harness = _TtsHarness()
        visible = harness._mark_tts_visible_plain("完整中文正文")
        record = harness._annotate_tts_record_component(
            Record(file="voice.wav"),
            "読み上げる内容",
            source_text="对应中文正文",
        )

        self.assertTrue(visible._private_companion_tts_visible_text)
        self.assertEqual("読み上げる内容", record._private_companion_tts_spoken_text)
        self.assertEqual("对应中文正文", record._private_companion_tts_source_text)

    def test_tts_sanitizer_never_collapses_repeated_digits(self):
        harness = _TtsHarness()

        self.assertEqual(
            "价格是1000元，编号1111，数量2025000个",
            harness._sanitize_tts_spoken_text(
                "价格是1000元，编号1111，数量2025000个",
                provider_kind="generic",
            ),
        )

    def test_tts_sanitizer_removes_url_without_dropping_surrounding_speech(self):
        harness = _TtsHarness()
        source = (
            "[softly]もう準備できてるよ。このリンクから話せるよ："
            "https://prepaid.example.com/join/abc123?mode=call。"
            "[warm]ここで待ってるね。"
        )

        sanitized = harness._sanitize_tts_spoken_text(source, provider_kind="fishaudio_s2")

        self.assertNotIn("http", sanitized)
        self.assertNotIn("prepaid.example.com", sanitized)
        self.assertIn("もう準備できてるよ", sanitized)
        self.assertIn("ここで待ってるね", sanitized)
        self.assertIn("[softly]", sanitized)
        self.assertIn("[warm]", sanitized)

    def test_tts_sanitizer_keeps_markdown_label_but_removes_link_target(self):
        harness = _TtsHarness()

        sanitized = harness._sanitize_tts_spoken_text(
            "文档见[接入说明](https://example.com/docs?a=1)，看一下。",
            provider_kind="generic",
        )

        self.assertEqual("文档见接入说明，看一下。", sanitized)

    async def test_tts_processing_keeps_url_visible_but_never_sends_it_to_voice(self):
        harness = _TtsHarness()
        harness.tts_voice_language = "ja"
        harness.tts_delivery_mode = "voice_and_text"
        harness.tts_foreign_text_mode = "translation"
        harness.tts_conversion_scope = "full"
        harness._tts_record_component = AsyncMock(return_value=Record(file="voice.wav"))
        url = "https://prepaid.example.com/join/abc123?mode=call"
        fallback = f"已经准备好了，点这个链接就能进来：{url}"

        components = await harness._process_tts_tags(
            f"<tts>もう準備できてるよ。このリンクから話せるよ：{url}</tts>{fallback}",
            object(),
            provider_settings={},
            config={},
            fallback_plain=fallback,
        )

        spoken = harness._tts_record_component.await_args.args[0]
        visible = "\n".join(
            str(getattr(component, "text", "") or "")
            for component in components
            if isinstance(component, Plain)
        )
        self.assertNotIn("http", spoken)
        self.assertIn(url, visible)

    def test_tts_prompts_keep_urls_outside_voice_text(self):
        harness = _TtsHarness()
        harness.tts_generation_mode = "fast_tag"
        harness.tts_conversion_scope = "full"
        harness.tts_voice_language = "ja"
        harness.tts_delivery_mode = "voice_and_text"
        harness.tts_foreign_text_mode = "translation"
        harness.tts_frequency_control_mode = "global"

        prompt = harness._build_tts_rule_prompt(provider_kind="generic")

        self.assertIn("不要放进 <pc_tts>", prompt)
        self.assertIn("必须在语音块外保留原文", prompt)

    def test_foreign_tts_prompt_requires_visible_chinese_or_plain_reply(self):
        harness = _TtsHarness()
        harness.tts_generation_mode = "fast_tag"
        harness.tts_voice_language = "ja"
        harness.tts_delivery_mode = "voice_and_text"
        harness.tts_foreign_text_mode = "translation"

        prompt = harness._build_tts_rule_prompt(provider_kind="generic")

        self.assertIn("每个 </pc_tts> 后都要紧跟非空", prompt)
        self.assertIn("直接用普通中文回复", prompt)

    async def test_private_fast_tag_is_demoted_to_plain_text(self):
        harness = _TtsHarness()
        response = SimpleNamespace(completion_text="先听我说，<pc_tts>我会陪着你。</pc_tts>别急。")
        event = SimpleNamespace(
            unified_msg_origin="test-session",
            _private_companion_tts_request_applied=True,
        )

        await harness.protect_tts_enhancement_response_blocks(event, response)

        self.assertEqual(response.completion_text, "先听我说，我会陪着你。别急。")
        self.assertNotIn("tts", response.completion_text.lower())

    async def test_standard_fast_tag_is_demoted_to_plain_text(self):
        harness = _TtsHarness()
        response = SimpleNamespace(completion_text="<tts>今日はゆっくりしてね。</tts>今天慢一点也没关系。")
        event = SimpleNamespace(
            unified_msg_origin="test-session",
            _private_companion_tts_request_applied=True,
        )

        await harness.protect_tts_enhancement_response_blocks(event, response)

        self.assertEqual(response.completion_text, "今日はゆっくりしてね。今天慢一点也没关系。")
        self.assertNotIn("<tts", response.completion_text.lower())

    def test_full_scope_rebuilds_partial_foreign_tag_from_complete_visible_reply(self):
        harness = _TtsHarness()
        harness.tts_generation_mode = "fast_tag"
        harness.tts_conversion_scope = "full"
        harness.tts_voice_language = "ja"
        harness.tts_delivery_mode = "voice_and_text"
        harness.tts_foreign_text_mode = "translation"
        source = (
            "嗯，<tts>[嬉しい]テスト音声です。</tts>这是测试译文。"
            "后续普通文本也必须完整保留。测试用户晚安。"
        )

        markup, fallback = harness._enforce_full_tts_scope_markup(source)

        self.assertEqual(markup, f"<tts>{fallback}</tts>")
        self.assertNotIn("テスト音声", markup)
        self.assertIn("这是测试译文", fallback)
        self.assertIn("普通文本", fallback)
        self.assertIn("完整保留", fallback)
        self.assertIn("测试用户晚安", fallback)

    def test_full_scope_does_not_silently_truncate_long_reply(self):
        harness = _TtsHarness()
        harness.tts_generation_mode = "fast_tag"
        harness.tts_conversion_scope = "full"
        harness.tts_voice_language = "zh"
        harness.tts_delivery_mode = "voice_and_text"
        harness.tts_foreign_text_mode = "translation"
        source = "甲乙丙丁" * 500

        markup, fallback = harness._enforce_full_tts_scope_markup(
            "<tts>局部内容</tts>",
            source_text=source,
        )

        self.assertEqual(source, fallback)
        self.assertEqual(f"<tts>{source}</tts>", markup)

    def test_visible_fallback_does_not_truncate_long_tool_text(self):
        harness = _TtsHarness()
        harness.tts_voice_language = "ja"
        visible = "长文本" * 400

        fallback = harness._tts_visible_fallback_text(
            f"<tts>読み上げる部分。</tts>{visible}"
        )

        self.assertEqual(visible, fallback)

    def test_partial_scope_preserves_model_authored_tag(self):
        harness = _TtsHarness()
        harness.tts_generation_mode = "fast_tag"
        harness.tts_conversion_scope = "partial"
        source = "先说一句，<tts>声に出す部分。</tts>其余仍是文字。"

        markup, fallback = harness._enforce_full_tts_scope_markup(source)

        self.assertEqual(source, markup)
        self.assertEqual("", fallback)

    def test_full_chinese_scope_keeps_tagged_and_untagged_content(self):
        harness = _TtsHarness()
        harness.tts_generation_mode = "fast_tag"
        harness.tts_conversion_scope = "full"
        harness.tts_voice_language = "zh"
        harness.tts_delivery_mode = "voice_and_text"
        source = "开头，<tts>这一句先说出来。</tts>后面的正文也必须朗读。"

        markup, fallback = harness._enforce_full_tts_scope_markup(source)

        self.assertEqual("开头，这一句先说出来。后面的正文也必须朗读。", fallback)
        self.assertEqual(f"<tts>{fallback}</tts>", markup)

    def test_full_fast_tag_prompt_contains_no_partial_scope_example(self):
        harness = _TtsHarness()
        harness.tts_generation_mode = "fast_tag"
        harness.tts_conversion_scope = "full"
        harness.tts_frequency_control_mode = "global"
        harness.tts_voice_language = "ja"
        harness.tts_delivery_mode = "voice_and_text"
        harness.tts_foreign_text_mode = "translation"

        prompt = harness._build_tts_rule_prompt()

        self.assertIn("当前转换范围：全量转换", prompt)
        self.assertIn("唯一一对<pc_tts>", prompt)
        self.assertIn("ゆっくり話してね。</pc_tts>我有在好好听哦，你慢慢说", prompt)
        self.assertNotIn("一小段", prompt)
        self.assertNotIn("</pc_tts>这件事可以一点点拆开", prompt)

    async def test_full_postprocess_request_injection_says_entire_reply(self):
        harness = _TtsHarness()
        harness.enabled = True
        harness.tts_generation_mode = "postprocess"
        harness.tts_conversion_scope = "full"
        harness.tts_frequency_control_mode = "legacy"
        harness.tts_voice_language = "ja"
        harness.config = {}
        event = SimpleNamespace(
            unified_msg_origin="test-session",
            message_str="普通聊天",
        )
        request = SimpleNamespace(system_prompt="base")

        await harness.apply_tts_enhancement_request(event, request)

        self.assertIn("当前是全量转换", request.system_prompt)
        self.assertIn("是否将整条回复转成语音", request.system_prompt)
        self.assertNotIn("其中一小段", request.system_prompt)

    async def test_plain_conversion_full_scope_rejects_partial_model_markup(self):
        harness = _TtsHarness()
        harness.tts_generation_mode = "postprocess"
        harness.tts_conversion_scope = "full"
        harness.tts_frequency_control_mode = "legacy"
        harness.tts_voice_language = "ja"
        harness.tts_delivery_mode = "voice_and_text"
        harness.tts_foreign_text_mode = "translation"
        harness._tts_auto_voice_last_at = {}
        harness._convert_text_to_tts_markup = AsyncMock(
            return_value="<tts>最初の一文だけ。</tts>只转换了第一句。"
        )
        harness._process_tts_tags = AsyncMock(return_value=[Plain("processed")])
        event = SimpleNamespace(
            unified_msg_origin="test-session",
            message_str="用语音回复",
        )
        source = "第一句。第二句和第三句也必须完整朗读。"

        result = await harness._maybe_convert_plain_reply_to_tts(source, event)

        call = harness._process_tts_tags.await_args
        self.assertEqual(f"<tts>{source}</tts>", call.args[0])
        self.assertEqual(source, call.kwargs["fallback_plain"])
        self.assertEqual("processed", result[0].text)

    async def test_full_fast_tag_plain_conversion_uses_only_spoken_pass(self):
        harness = _TtsHarness()
        harness.tts_generation_mode = "fast_tag"
        harness.tts_conversion_scope = "full"
        harness.tts_frequency_control_mode = "legacy"
        harness.tts_voice_language = "ja"
        harness.tts_delivery_mode = "voice_and_text"
        harness.tts_foreign_text_mode = "translation"
        harness._tts_auto_voice_last_at = {}
        harness._auto_voice_trigger_reason = lambda _text, _event: (True, "auto_voice")
        harness._convert_text_to_tts_markup = AsyncMock(return_value="不应调用")
        harness._process_tts_tags = AsyncMock(return_value=[Plain("processed")])
        event = SimpleNamespace(
            unified_msg_origin="test-session",
            message_str="普通聊天",
        )
        source = "完整回复只应进入一次语种转换。"

        result = await harness._maybe_convert_plain_reply_to_tts(source, event)

        harness._convert_text_to_tts_markup.assert_not_awaited()
        call = harness._process_tts_tags.await_args
        self.assertEqual(f"<tts>{source}</tts>", call.args[0])
        self.assertEqual(source, call.kwargs["fallback_plain"])
        self.assertEqual("processed", result[0].text)

    async def test_voice_only_partial_scope_preserves_unspoken_text(self):
        harness = _TtsHarness()
        harness.tts_delivery_mode = "voice_only"
        harness.tts_conversion_scope = "partial"
        harness.tts_voice_language = "zh"
        record = Record(file="voice.wav")

        output = await harness._finalize_tts_delivery_chain(
            [Plain("前置正文"), record, Plain("后置重要正文")],
            event=None,
            provider_kind="generic",
            fallback_plain="完整原文",
            successful_spoken=["只朗读这一句"],
            suppress_visible=False,
        )

        self.assertEqual(["Record", "Plain"], [type(item).__name__ for item in output])
        self.assertEqual("前置正文 后置重要正文", output[1].text)

    async def test_voice_only_full_scope_still_hides_mirrored_text(self):
        harness = _TtsHarness()
        harness.tts_delivery_mode = "voice_only"
        harness.tts_conversion_scope = "full"
        harness.tts_voice_language = "zh"
        record = Record(file="voice.wav")

        output = await harness._finalize_tts_delivery_chain(
            [record, Plain("整条回复的镜像文字")],
            event=None,
            provider_kind="generic",
            fallback_plain="整条回复的镜像文字",
            successful_spoken=["整条回复的镜像文字"],
            suppress_visible=False,
        )

        self.assertEqual([record], output)

    def test_routing_component_stays_attached_to_first_voice(self):
        harness = _TtsHarness()
        chunks = harness._split_tts_chain_for_ordered_send(
            [At(qq="10001"), Record(file="voice.wav"), Plain("对应正文")]
        )

        self.assertEqual(
            [["At", "Record"], ["Plain"]],
            [[type(item).__name__ for item in chunk] for chunk in chunks],
        )

    async def test_record_chain_still_cleans_raw_tts_markup(self):
        harness = _TtsHarness()
        record = Record(file="voice.wav")

        cleaned = await harness._sanitize_outbound_tts_chain_without_event(
            [record, Plain("<tts>raw voice</tts>对应正文")],
            umo="test-session",
        )

        self.assertIs(record, cleaned[0])
        self.assertEqual("对应正文", cleaned[1].text)
        self.assertNotIn("tts", cleaned[1].text.lower())

    def test_visible_text_removes_known_emotion_cues_but_keeps_normal_brackets(self):
        harness = _TtsHarness()

        cleaned = harness._sanitize_tts_visible_text("[happy]今天很开心。[公告]明天见。")

        self.assertEqual("今天很开心。[公告]明天见。", cleaned)

    async def test_proactive_outbound_guard_removes_orphan_emotion_cue(self):
        harness = _TtsHarness()

        cleaned = await harness._sanitize_outbound_tts_chain_without_event(
            [Plain("[happy]テストユーザー、音声確認ができました。")],
            umo="default:FriendMessage:10001",
        )

        self.assertEqual(1, len(cleaned))
        self.assertEqual("テストユーザー、音声確認ができました。", cleaned[0].text)

    async def test_group_activity_still_allows_complete_tts_transcript(self):
        harness = _TtsHarness()
        harness.enable_segmented_proactive_reply = True
        harness.segmented_proactive_scope = "all_llm"
        harness.tts_voice_language = "ja"
        harness._segmented_scope_allows_event = lambda _event: True
        harness._event_scope_key = lambda _event: "group:10001"
        harness._scope_has_new_inbound_activity = lambda *_args, **_kwargs: True
        visible_segments = [
            harness._mark_tts_visible_plain("这是第一段。"),
            harness._mark_tts_visible_plain("这是第二段。"),
            harness._mark_tts_visible_plain("这是第三段。"),
        ]
        sent: list[list[object]] = []

        class Event:
            unified_msg_origin = "default:GroupMessage:10001"

            @staticmethod
            def chain_result(chain):
                return chain

            async def send(self, chain):
                sent.append(chain)

        with patch(
            "astrbot_plugin_private_companion.tts_enhancement.asyncio.sleep",
            new=AsyncMock(),
        ):
            await harness._send_tts_chain_chunks_after_first(
                Event(),
                [[item] for item in visible_segments if item is not None],
                started_at=1.0,
            )

        self.assertEqual(
            ["这是第一段。", "这是第二段。", "这是第三段。"],
            [chunk[0].text for chunk in sent],
        )

    async def test_group_activity_still_stops_unmarked_reply_remainder(self):
        harness = _TtsHarness()
        harness._event_scope_key = lambda _event: "group:10001"
        harness._scope_has_new_inbound_activity = lambda *_args, **_kwargs: True
        sent: list[list[object]] = []

        class Event:
            unified_msg_origin = "default:GroupMessage:10001"

            @staticmethod
            def chain_result(chain):
                return chain

            async def send(self, chain):
                sent.append(chain)

        with patch(
            "astrbot_plugin_private_companion.tts_enhancement.asyncio.sleep",
            new=AsyncMock(),
        ):
            await harness._send_tts_chain_chunks_after_first(
                Event(),
                [[Plain("普通第一段。")], [Plain("普通第二段。")]],
                started_at=1.0,
            )

        self.assertEqual(["普通第一段。"], [chunk[0].text for chunk in sent])

    def test_segmented_tts_visible_text_keeps_transcript_marker(self):
        harness = _TtsHarness()
        harness.enable_segmented_proactive_reply = True
        harness.segmented_proactive_scope = "all_llm"
        harness.tts_voice_language = "ja"
        harness._segmented_scope_allows_event = lambda _event: True
        visible = harness._mark_tts_visible_plain("完整中文正文。")

        chunks = harness._tts_segment_plain_chunk_for_ordered_send(
            SimpleNamespace(unified_msg_origin="group:10001"),
            [visible],
        )

        self.assertEqual(1, len(chunks))
        self.assertTrue(chunks[0][0]._private_companion_tts_visible_text)

    def test_segmented_tts_visible_text_uses_reply_splitter_and_keeps_markers(self):
        harness = _TtsHarness()
        harness.enable_segmented_proactive_reply = True
        harness.segmented_proactive_scope = "all_llm"
        harness.tts_voice_language = "ja"
        harness._segmented_scope_allows_event = lambda _event: True
        harness._split_proactive_text = lambda _text: [
            "第一段测试！",
            "第二段仍然保留…",
            "第三段用于核对。",
            "第四段继续验证，",
            "第五段确认完整。",
        ]
        visible = harness._mark_tts_visible_plain(
            "第一段测试！第二段仍然保留…第三段用于核对。"
            "第四段继续验证，第五段确认完整。"
        )

        chunks = harness._tts_segment_plain_chunk_for_ordered_send(
            SimpleNamespace(unified_msg_origin="default:FriendMessage:100000001"),
            [visible],
        )

        self.assertEqual(5, len(chunks))
        self.assertEqual(
            [
                "第一段测试！",
                "第二段仍然保留…",
                "第三段用于核对。",
                "第四段继续验证，",
                "第五段确认完整。",
            ],
            [chunk[0].text for chunk in chunks],
        )
        self.assertTrue(
            all(chunk[0]._private_companion_tts_visible_text for chunk in chunks)
        )

    async def test_proactive_tts_transcript_uses_real_proactive_delivery_path(self):
        harness = _TtsHarness()
        harness._send_chain_components = AsyncMock()
        visible = harness._mark_tts_visible_plain("这是主动语音对应的中文正文。")

        class Event:
            unified_msg_origin = "default:FriendMessage:10001"
            _private_companion_proactive_delivery_umo = "default:FriendMessage:10001"

            @staticmethod
            def chain_result(chain):
                return chain

            send = AsyncMock()

        with patch(
            "astrbot_plugin_private_companion.tts_enhancement.asyncio.sleep",
            new=AsyncMock(),
        ):
            await harness._send_tts_chain_chunks_after_first(
                Event(),
                [[visible]],
                started_at=1.0,
            )

        harness._send_chain_components.assert_awaited_once_with(
            "default:FriendMessage:10001",
            [visible],
            apply_decorating_hooks=False,
        )
        Event.send.assert_not_awaited()

    async def test_fast_tag_send_path_enforces_full_scope_before_synthesis(self):
        harness = _TtsHarness()
        harness.enabled = True
        harness.tts_generation_mode = "fast_tag"
        harness.tts_conversion_scope = "full"
        harness.tts_voice_language = "ja"
        harness.tts_delivery_mode = "voice_and_text"
        harness.tts_foreign_text_mode = "translation"
        harness._process_tts_tags = AsyncMock(return_value=[Plain("processed")])
        harness._build_result_from_chain = lambda chain: SimpleNamespace(chain=chain)
        result = SimpleNamespace(
            chain=[Plain("<tts>あたたかい。</tts>好温暖。后面的正文也要朗读。")]
        )

        class Event:
            unified_msg_origin = "test-session"
            _private_companion_tts_request_applied = True

            def get_result(self):
                return result

            def set_result(self, value):
                self.result = value

        event = Event()
        await harness.apply_tts_enhancement_before_send(event)

        call = harness._process_tts_tags.await_args
        self.assertEqual("<tts>好温暖。后面的正文也要朗读。</tts>", call.args[0])
        self.assertEqual("好温暖。后面的正文也要朗读。", call.kwargs["fallback_plain"])
        self.assertEqual("processed", event.result.chain[0].text)

    def test_windows_playback_passes_path_and_volume_through_environment(self):
        harness = _TtsHarness()
        completed = SimpleNamespace(returncode=0, stdout="", stderr="")

        with patch(
            "astrbot_plugin_private_companion.tts_enhancement.subprocess.run",
            return_value=completed,
        ) as run:
            succeeded = harness._run_windows_media_player_script(
                r"C:\temp\带 空格\voice.wav",
                use_wpf=True,
                volume=37,
            )

        self.assertTrue(succeeded)
        args = run.call_args.args[0]
        kwargs = run.call_args.kwargs
        self.assertNotIn(r"C:\temp\带 空格\voice.wav", args)
        self.assertNotIn("$args", args[-1])
        self.assertIn("$env:PRIVATE_COMPANION_TTS_AUDIO_PATH", args[-1])
        self.assertEqual(kwargs["env"]["PRIVATE_COMPANION_TTS_VOLUME"], "37")
        self.assertTrue(kwargs["env"]["PRIVATE_COMPANION_TTS_AUDIO_PATH"].endswith("voice.wav"))

    def test_windows_playback_removes_temporary_repaired_wav(self):
        harness = _TtsHarness()
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "voice.wav"
            playback = Path(tmp) / "voice.playback.wav"
            source.write_bytes(b"source")
            playback.write_bytes(b"playback")

            with (
                patch.object(harness, "_prepare_windows_wav_for_playback", return_value=str(playback)),
                patch.object(harness, "_run_windows_media_player_script", return_value=True),
            ):
                harness._play_tts_audio_file_windows_silent(str(source), volume=0)

            self.assertTrue(source.exists())
            self.assertFalse(playback.exists())

    async def test_local_playback_failure_enters_backoff(self):
        harness = _TtsHarness()
        harness.enable_tts_local_playback = True
        harness.enable_tts_local_playback_live_only = False
        harness.enable_tts_live_subtitle_sync = False
        harness.tts_local_playback_min_interval_seconds = 0
        harness._tts_local_playback_last_at = 0.0
        harness._tts_local_playback_failures = 0
        harness._tts_local_playback_retry_after = 0.0
        attempts = 0

        def fail_playback(_path):
            nonlocal attempts
            attempts += 1
            raise RuntimeError("播放器不可用")

        harness._open_tts_audio_file_local = fail_playback
        with patch(
            "astrbot_plugin_private_companion.tts_enhancement.time.time",
            return_value=100.0,
        ):
            await harness._after_tts_audio_generated("voice.wav", "测试语音")
            await harness._after_tts_audio_generated("voice.wav", "测试语音")

        self.assertEqual(1, attempts)
        self.assertEqual(1, harness._tts_local_playback_failures)
        self.assertEqual(130.0, harness._tts_local_playback_retry_after)

    async def test_local_playback_success_clears_backoff(self):
        harness = _TtsHarness()
        harness.enable_tts_local_playback = True
        harness.enable_tts_local_playback_live_only = False
        harness.enable_tts_live_subtitle_sync = False
        harness.tts_local_playback_min_interval_seconds = 0
        harness._tts_local_playback_last_at = 0.0
        harness._tts_local_playback_failures = 3
        harness._tts_local_playback_retry_after = 0.0
        harness._open_tts_audio_file_local = lambda _path: None

        with patch(
            "astrbot_plugin_private_companion.tts_enhancement.time.time",
            return_value=200.0,
        ):
            await harness._after_tts_audio_generated("voice.wav", "测试语音")

        self.assertEqual(0, harness._tts_local_playback_failures)
        self.assertEqual(0.0, harness._tts_local_playback_retry_after)


if __name__ == "__main__":
    unittest.main()
