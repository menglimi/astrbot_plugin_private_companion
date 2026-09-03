from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, Mock, patch

from astrbot.api.message_components import Plain
from astrbot.core.message.message_event_result import (
    MessageEventResult,
    ResultContentType,
)
from markdown_it import MarkdownIt

from astrbot_plugin_private_companion.event_dispatch import EventDispatchMixin
from astrbot_plugin_private_companion.main import PrivateCompanionPlugin
from astrbot_plugin_private_companion.markdown_segment_guard import (
    protect_markdown_blocks,
)
from astrbot_plugin_private_companion.proactive_message import ProactiveMessageMixin
from astrbot_plugin_private_companion.segmented_message import LLM_SEGMENT_MARKER


class _Event:
    unified_msg_origin = "QBot4012710235:GroupMessage:group-id"


class _Harness(ProactiveMessageMixin, EventDispatchMixin):
    def __init__(self) -> None:
        self.enable_segmented_proactive_reply = True
        self.enable_llm_controlled_segmenting = True
        self.enable_segmented_plugin_rules = True
        self.enable_segmented_proactive_chat_profiles = False
        self.segmented_proactive_scope = "all_llm"
        self.segmented_proactive_chat_scope = "all"
        self.segmented_proactive_threshold = 500
        self.segmented_proactive_min_segment_chars = 1
        self.segmented_proactive_max_segments = 5
        self.segmented_proactive_split_mode = "words"
        self.segmented_proactive_split_words = ["。", "？", "！", "…"]
        self.segmented_proactive_match_width_variants = False
        self.segmented_proactive_regex = r".*?[。？！~…\n]+|.+$"
        self.enable_segmented_proactive_content_cleanup = False
        self.segmented_proactive_content_cleanup_scope = "all"
        self.segmented_proactive_content_cleanup_rule = r"[\n。*]"
        self.segmented_proactive_content_cleanup_words = ["\n", "。", "*"]
        self.enable_segmented_proactive_content_replacement = False
        self.segmented_proactive_content_replacements: list[str] = []

    def _feature_enabled_or_temp_unlocked(
        self,
        key: str,
        default: bool = False,
    ) -> bool:
        return bool(getattr(self, key, default))

    @staticmethod
    def _segmented_scope_allows_event(_event: object) -> bool:
        return True

    @staticmethod
    def _segmented_scope_allows_umo(_umo: str) -> bool:
        return True

    @staticmethod
    def _segmented_platform_allows(**_kwargs: object) -> bool:
        return True


class MarkdownSafeSegmentingTests(unittest.TestCase):
    quote = (
        "> 银月当空，夜色正浓。\n"
        "> 别以为这种小把戏就能难住本小姐。"
    )
    quote_list = (
        "> * 狐耳状态：微微抖动，带着一丝被打扰的不满\n"
        "> * 尾巴状态：修长蓬松，随意搭在身侧，偶尔轻轻点地\n"
        "> * 今日评价：**还算让我满意。**"
    )
    source = (
        "……哈？又来这套。拿我当什么测试工具了？\n\n"
        "行吧，既然你非要看，那就勉强满足你一次。\n\n"
        f"{quote}\n\n{quote_list}\n\n"
        "别得意太早，下不为例。"
    )

    def test_protection_round_trip_preserves_exact_markdown(self) -> None:
        protection = protect_markdown_blocks(self.source)

        self.assertTrue(protection.active)
        self.assertNotIn(self.quote, protection.protected_text)
        self.assertNotIn(self.quote_list, protection.protected_text)
        self.assertEqual(self.source, protection.restore(protection.protected_text))

    def test_protection_round_trip_preserves_crlf(self) -> None:
        source = "> 引用一。\r\n> 引用二。\r\n\r\n普通正文。"
        protection = protect_markdown_blocks(source)

        self.assertTrue(protection.active)
        self.assertEqual(source, protection.restore(protection.protected_text))

    def test_words_and_regex_split_only_outside_markdown_blocks(self) -> None:
        renderer = MarkdownIt("commonmark")
        for mode in ("words", "regex"):
            with self.subTest(mode=mode):
                harness = _Harness()
                harness.segmented_proactive_split_mode = mode
                event = _Event()

                segments = harness._split_proactive_text(self.source, event=event)

                self.assertEqual(5, len(segments))
                self.assertTrue(event._private_companion_segmented_markdown_detected)
                self.assertEqual(1, sum(self.quote in item for item in segments))
                self.assertEqual(1, sum(self.quote_list in item for item in segments))
                self.assertFalse(any(">，" in item or ">," in item for item in segments))
                self.assertFalse(any("PCMARKDOWNBLOCK" in item for item in segments))
                quote_segment = next(item for item in segments if self.quote in item)
                list_segment = next(item for item in segments if self.quote_list in item)
                self.assertIn(renderer.render(self.quote), renderer.render(quote_segment))
                self.assertIn(renderer.render(self.quote_list), renderer.render(list_segment))

    def test_budget_merge_uses_one_markdown_paragraph_boundary(self) -> None:
        harness = _Harness()
        harness.segmented_proactive_max_segments = 3

        segments = harness._split_proactive_text(self.source, event=_Event())

        self.assertEqual(3, len(segments))
        self.assertIn(f"{self.quote_list}\n\n别得意太早", segments[-1])
        self.assertNotIn(f"{self.quote_list}\n\n\n", segments[-1])

    def test_fenced_and_unclosed_code_blocks_are_atomic(self) -> None:
        for closing in ("```\n\n结尾。", ""):
            with self.subTest(closed=bool(closing)):
                harness = _Harness()
                harness.segmented_proactive_max_segments = 4
                code = "```python\nif value:\n    print('。')\n"
                source = f"开场一。开场二。\n\n{code}{closing}"

                segments = harness._split_proactive_text(source, event=_Event())

                containing = [item for item in segments if "```python" in item]
                self.assertEqual(1, len(containing))
                self.assertIn("if value:\n    print('。')", containing[0])
                if closing:
                    self.assertIn("```", containing[0][3:])

    def test_nested_lists_tables_and_inline_markup_are_atomic(self) -> None:
        harness = _Harness()
        harness.segmented_proactive_max_segments = 6
        nested_list = "- 一级。\n  - 二级。\n- 末项。"
        table = "| A | B |\n|---|---|\n| 1。 | 2。 |"
        inline = "保留 `code。value`、[链接。](https://example.com/a.b) 和 **加粗。**。"
        source = f"开场。\n\n{nested_list}\n\n{table}\n\n{inline}\n\n结尾。"

        segments = harness._split_proactive_text(source, event=_Event())

        for markdown in (nested_list, table, inline):
            self.assertEqual(1, sum(markdown in item for item in segments))
        self.assertFalse(any("|，|" in item for item in segments))

    def test_missing_parser_fails_closed_for_markdown_shaped_text(self) -> None:
        source = "普通一。\n\n> 引用一。\n> 引用二。\n\n普通二。"
        with patch(
            "astrbot_plugin_private_companion.markdown_segment_guard.MarkdownIt",
            None,
        ):
            protection = protect_markdown_blocks(source)

        self.assertTrue(protection.active)
        self.assertEqual(1, len(protection.replacements))
        self.assertEqual(source, protection.restore(protection.protected_text))

    def test_cleanup_and_replacement_do_not_rewrite_markdown_blocks(self) -> None:
        harness = _Harness()
        harness.enable_segmented_proactive_content_cleanup = True
        harness.enable_segmented_proactive_content_replacement = True
        harness.segmented_proactive_content_replacements = ["引用=>替换"]
        markdown = "> **引用。**\n> * 引用。"
        source = f"普通引用。\n\n{markdown}\n\n结尾引用。"

        segments = harness._split_proactive_text(source, event=_Event())

        markdown_segment = next(item for item in segments if markdown in item)
        self.assertIn(markdown, markdown_segment)
        self.assertTrue(any("普通替换" in item for item in segments))
        self.assertTrue(any("结尾替换" in item for item in segments))

    def test_llm_boundaries_keep_priority_and_markdown_uses_remaining_budget(self) -> None:
        harness = _Harness()
        harness.segmented_proactive_max_segments = 4
        source = (
            f"普通一。普通二。\n{LLM_SEGMENT_MARKER}\n"
            "> 引用一。\n> 引用二。"
        )
        event = _Event()

        segments = PrivateCompanionPlugin._split_llm_controlled_text_for_event(
            harness,
            event,
            source,
        )

        self.assertEqual(3, len(segments))
        self.assertEqual(2, event._private_companion_llm_segment_count)
        self.assertEqual(1, sum("> 引用一。\n> 引用二。" in item for item in segments))

    def test_explicit_markdown_result_is_segmented_and_mode_is_inherited(self) -> None:
        plugin = PrivateCompanionPlugin.__new__(PrivateCompanionPlugin)
        plugin.enabled = True
        plugin.enable_segmented_proactive_reply = True
        plugin.enable_llm_controlled_segmenting = False
        plugin.enable_segmented_plugin_rules = True
        plugin.enable_segmented_proactive_chat_profiles = False
        plugin.segmented_proactive_scope = "all_llm"
        plugin.segmented_proactive_chat_scope = "all"
        plugin.segmented_proactive_threshold = 500
        plugin.segmented_proactive_min_segment_chars = 1
        plugin.segmented_proactive_max_segments = 3
        plugin.segmented_proactive_split_mode = "words"
        plugin.segmented_proactive_split_words = ["。"]
        plugin.segmented_proactive_match_width_variants = False
        plugin.enable_segmented_proactive_content_cleanup = False
        plugin.enable_segmented_proactive_content_replacement = False
        plugin.enable_framework_error_leak_guard = False
        plugin.enable_daily_case_review_experiment = False
        plugin._proactive_only_blocks_passive_event = lambda *_args: False
        plugin._feature_enabled_or_temp_unlocked = (
            lambda key: key == "enable_segmented_proactive_reply"
        )
        plugin._segmented_scope_allows_event = lambda _event: True
        plugin._segmented_platform_allows = lambda **_kwargs: True
        plugin._restore_response_review_meta_leak_before_send = lambda *_args: False
        plugin._should_defer_segmenting_to_astrbot_tts = AsyncMock(return_value=False)
        plugin._limit_private_routine_check_segments = lambda _text, chunks: chunks
        plugin._plain_text_segments_from_chunks = lambda _chunks: []
        plugin._create_lifecycle_background_task = Mock(
            side_effect=lambda operation, **_kwargs: operation.close()
        )
        result = MessageEventResult(
            chain=[Plain("普通一。\n\n> 引用一。\n> 引用二。\n\n普通二。")],
            result_content_type=ResultContentType.LLM_RESULT,
        )
        result.use_markdown(True)

        class Event(_Event):
            message_str = "测试 Markdown 分段"

            def __init__(self) -> None:
                self.result = result

            def get_result(self) -> MessageEventResult:
                return self.result

            def set_result(self, value: MessageEventResult) -> None:
                self.result = value

        event = Event()
        asyncio.run(
            PrivateCompanionPlugin.apply_segmented_llm_reply_scope(plugin, event)
        )

        self.assertTrue(event.result.use_markdown_)
        self.assertIn("普通一。", event.result.chain[0].text)
        plugin._create_lifecycle_background_task.assert_called_once()


if __name__ == "__main__":
    unittest.main()
