from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from astrbot.api.message_components import Plain
from astrbot.core.message.message_event_result import (
    MessageEventResult,
    ResultContentType,
)
from markdown_it import MarkdownIt

from astrbot_plugin_private_companion.helpers import (
    _strip_internal_message_blocks,
    _strip_outbound_control_blocks,
)
from astrbot_plugin_private_companion.main import (
    PrivateCompanionPlugin,
    _strip_chain_plain_thinking,
)
from astrbot_plugin_private_companion.segmented_message import (
    LLM_SEGMENT_MARKER,
    parse_llm_segment_control,
    sanitize_llm_segment_control_tokens,
)


class StructuredWhitespacePreservationTests(unittest.TestCase):
    def test_control_cleaners_only_normalize_line_endings(self) -> None:
        source = (
            "\r\n# 标题\r\n\r\n"
            "- 一级列表  \r\n"
            "  - 二级列表\r"
            "\t保留 Tab\n"
            "普通  连续空格\n"
            "不换行\u00a0空格｜全角\u3000空格｜行\u2028分隔｜段\u2029分隔\r\n"
        )
        expected = source.replace("\r\n", "\n").replace("\r", "\n")

        self.assertEqual(expected, _strip_internal_message_blocks(source))
        self.assertEqual(expected, _strip_outbound_control_blocks(source))

    def test_markdown_rendering_is_unchanged_after_cleanup(self) -> None:
        source = (
            "# 标题\r\n\r\n"
            "> 引用第一行  \r\n"
            "> 引用第二行\r\n\r\n"
            "- 一级列表\r\n"
            "  - 二级列表\r\n\r\n"
            "```python\r\n"
            "if value:\r\n"
            "\tprint(\"tab\")\r\n"
            "    print(\"spaces\")\r\n"
            "```\r\n\r\n"
            "| A | B |\r\n"
            "|---|---|\r\n"
            "| 1 | 2 |"
        )
        normalized = source.replace("\r\n", "\n").replace("\r", "\n")
        cleaned = _strip_outbound_control_blocks(source)

        self.assertEqual(normalized, cleaned)
        self.assertEqual(
            MarkdownIt("commonmark").enable("table").render(normalized),
            MarkdownIt("commonmark").enable("table").render(cleaned),
        )

    def test_segment_token_sanitizer_preserves_unrelated_whitespace(self) -> None:
        source = (
            "\r\n- 一级列表\r\n"
            "  - 二级列表\r\n"
            "```python\r\n"
            "\tprint(\"tab\")\r\n"
            "    print(\"spaces\")\r\n"
            "```\r\n"
            "普通  连续空格\u00a0不换行空格\r\n"
        )
        expected = source.replace("\r\n", "\n").replace("\r", "\n")

        self.assertEqual(expected, sanitize_llm_segment_control_tokens(source))

    def test_segment_parser_preserves_markdown_inside_each_segment(self) -> None:
        first = (
            "> 引用第一行  \r\n"
            "> 引用第二行\r\n\r\n"
            "- 一级列表\r\n"
            "  - 二级列表"
        )
        second = (
            "```python\r\n"
            "if value:\r\n"
            "\tprint(\"tab\")\r\n"
            "    print(\"spaces\")\r\n"
            "```"
        )
        source = f"{first}\r\n{LLM_SEGMENT_MARKER}\r\n{second}"
        expected = tuple(
            part.replace("\r\n", "\n").replace("\r", "\n")
            for part in (first, second)
        )

        parsed = parse_llm_segment_control(source)

        self.assertTrue(parsed.controlled)
        self.assertEqual(expected, parsed.segments)
        markdown = MarkdownIt("commonmark")
        self.assertEqual(
            [markdown.render(value) for value in expected],
            [markdown.render(value) for value in parsed.segments],
        )

    def test_cross_plain_thinking_cleanup_preserves_llm_boundaries(self) -> None:
        chain = [
            Plain("<thinking>内部推理"),
            Plain(
                "内容</thinking>\r\n"
                f"第一段\r\n{LLM_SEGMENT_MARKER}\r\n第二段"
            ),
        ]

        _strip_chain_plain_thinking(object(), chain)

        self.assertEqual(
            f"第一段\n{LLM_SEGMENT_MARKER}\n第二段",
            chain[0].text,
        )
        self.assertEqual("", chain[1].text)
        parsed = parse_llm_segment_control(chain[0].text)
        self.assertTrue(parsed.controlled)
        self.assertEqual(("第一段", "第二段"), parsed.segments)
        self.assertEqual(1, parsed.exact_boundary_count)

    def test_decorating_hook_keeps_llm_boundaries_before_plugin_rules(self) -> None:
        plugin = PrivateCompanionPlugin.__new__(PrivateCompanionPlugin)
        plugin.enabled = True
        plugin.enable_segmented_proactive_reply = True
        plugin.enable_llm_controlled_segmenting = True
        plugin.enable_segmented_plugin_rules = True
        plugin.enable_segmented_proactive_chat_profiles = False
        plugin.segmented_proactive_scope = "all_llm"
        plugin.segmented_proactive_chat_scope = "all"
        plugin.segmented_proactive_threshold = 200
        plugin.segmented_proactive_min_segment_chars = 8
        plugin.segmented_proactive_max_segments = 5
        plugin.segmented_proactive_split_mode = "words"
        plugin.segmented_proactive_split_words = ["。", "？", "?", "！", "!", "…", "~"]
        plugin.segmented_proactive_match_width_variants = False
        plugin.enable_segmented_proactive_content_cleanup = True
        plugin.segmented_proactive_content_cleanup_scope = "trailing"
        plugin.segmented_proactive_content_cleanup_words = ["，", ",", "；", ";", "。"]
        plugin.enable_segmented_proactive_content_replacement = False
        plugin.enable_framework_error_leak_guard = False
        plugin.enable_daily_case_review_experiment = False
        plugin._proactive_only_blocks_passive_event = lambda *_args: False
        plugin._feature_enabled_or_temp_unlocked = lambda key: key == "enable_segmented_proactive_reply"
        plugin._segmented_scope_allows_event = lambda _event: True
        plugin._segmented_platform_allows = lambda **_kwargs: True
        plugin._restore_response_review_meta_leak_before_send = lambda *_args: False
        plugin._should_defer_segmenting_to_astrbot_tts = AsyncMock(return_value=False)
        plugin._limit_private_routine_check_segments = lambda _text, chunks: chunks
        plugin._plain_text_segments_from_chunks = lambda _chunks: []
        plugin._create_lifecycle_background_task = Mock(
            side_effect=lambda operation, **_kwargs: operation.close()
        )

        source = (
            f"……哈？\r\n{LLM_SEGMENT_MARKER}\r\n"
            f"『尾巴嫌弃地一甩』\r\n{LLM_SEGMENT_MARKER}\r\n"
            "满脑子只剩这些了？真没救了你"
        )
        result = MessageEventResult(
            chain=[Plain(source)],
            result_content_type=ResultContentType.LLM_RESULT,
        )

        class Event:
            unified_msg_origin = "QBot4012710235:GroupMessage:group-id"
            message_str = "测试"

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

        self.assertEqual(3, event._private_companion_llm_segment_count)
        self.assertEqual(
            {"exact": 2, "recovered": 0, "cleaned_only": 0},
            event._private_companion_llm_segment_diagnostics,
        )
        self.assertEqual(["……哈？"], [item.text for item in event.result.chain])
        plugin._create_lifecycle_background_task.assert_called_once()

    def test_final_outbound_guard_preserves_markdown_layout(self) -> None:
        source = (
            "# 标题\r\n\r\n"
            "- 一级列表\r\n"
            "  - 二级列表\r\n\r\n"
            "```python\r\n"
            "if value:\r\n"
            "\tprint(\"tab\")\r\n"
            "    print(\"spaces\")\r\n"
            "```"
        )
        expected = source.replace("\r\n", "\n").replace("\r", "\n")
        plugin = PrivateCompanionPlugin.__new__(PrivateCompanionPlugin)
        plugin.enabled = True
        plugin.enable_tts_enhancement = False
        result = SimpleNamespace(chain=[Plain(source)])
        event = SimpleNamespace(
            unified_msg_origin="QBot4012710235:GroupMessage:group-id",
            get_result=lambda: result,
        )

        asyncio.run(plugin.final_strip_outbound_control_blocks_before_send(event))

        self.assertEqual(expected, result.chain[0].text)
        self.assertEqual(
            MarkdownIt("commonmark").render(expected),
            MarkdownIt("commonmark").render(result.chain[0].text),
        )


if __name__ == "__main__":
    unittest.main()
