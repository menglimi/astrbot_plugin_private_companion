# -*- coding: utf-8 -*-
from __future__ import annotations

import inspect
import unittest
from types import SimpleNamespace

from astrbot.api.message_components import Plain
from astrbot_plugin_private_companion.daily_state import DailyStateMixin
from astrbot_plugin_private_companion.event_dispatch import EventDispatchMixin
from astrbot_plugin_private_companion.proactive_message import ProactiveMessageMixin
from astrbot_plugin_private_companion.tts_enhancement import TtsEnhancementMixin


class _SegmentHarness(ProactiveMessageMixin, EventDispatchMixin):
    def _feature_enabled_or_temp_unlocked(self, key: str) -> bool:
        return key == "enable_segmented_proactive_reply"


class _SegmentTtsHarness(TtsEnhancementMixin, ProactiveMessageMixin, EventDispatchMixin):
    def _feature_enabled_or_temp_unlocked(self, key: str) -> bool:
        return key == "enable_segmented_proactive_reply"


class SegmentedExternalShareTests(unittest.TestCase):
    def test_external_shares_follow_segmenting_config(self) -> None:
        for reason in ("bili_video_share", "news_share", "web_exploration_share"):
            self.assertFalse(DailyStateMixin._proactive_send_disables_segmenting(reason))

        self.assertTrue(DailyStateMixin._proactive_send_disables_segmenting("creative_share"))
        self.assertTrue(
            DailyStateMixin._proactive_send_disables_segmenting(
                "bili_video_share",
                friend_proactive=True,
            )
        )

    def test_proactive_send_loop_uses_the_reason_gate(self) -> None:
        source = inspect.getsource(DailyStateMixin._tick)

        self.assertIn(
            "disable_segmenting=self._proactive_send_disables_segmenting(",
            source,
        )
        self.assertNotIn(
            'reason in {"creative_share", "bili_video_share", "news_share", "web_exploration_share"}',
            source,
        )

    def test_bilibili_url_survives_word_segmentation(self) -> None:
        harness = _SegmentHarness()
        harness.enable_segmented_proactive_reply = True
        harness.segmented_proactive_threshold = 500
        harness.segmented_proactive_min_segment_chars = 5
        harness.segmented_proactive_max_segments = 5
        harness.segmented_proactive_split_mode = "words"
        harness.segmented_proactive_regex = r"(?<=[。！？!?…~～])\s*|\n+"
        harness.segmented_proactive_split_words = [
            "。", "？", "！", "~", "?", ".", "!", ";", "；", "……", "（", "“", "，", "…"
        ]
        harness.enable_segmented_proactive_content_cleanup = True
        harness.segmented_proactive_content_cleanup_scope = "all"
        harness.segmented_proactive_content_cleanup_rule = r"[\n]"
        harness.segmented_proactive_content_cleanup_words = ["。", "，"]
        text = (
            "嗯…\n测试用户来看看这个示例吧？\n"
            "第一部分用于验证中文逗号，第二部分继续验证分段，第三部分确认链接保持完整，"
            "最后附上地址～https://www.bilibili.com/video/BV1test。"
        )

        segments = harness._split_proactive_text(text)

        self.assertEqual(len(segments), 5)
        self.assertEqual(segments[0], "嗯，测试用户来看看这个示例吧？")
        self.assertEqual(sum("bilibili.com/video/BV1test" in item for item in segments), 1)
        self.assertTrue(segments[-1].endswith("https://www.bilibili.com/video/BV1test"))

    def test_common_file_suffixes_survive_dot_segmentation_and_cleanup(self) -> None:
        harness = _SegmentHarness()
        harness.enable_segmented_proactive_reply = True
        harness.segmented_proactive_threshold = 500
        harness.segmented_proactive_min_segment_chars = 1
        harness.segmented_proactive_max_segments = 20
        harness.segmented_proactive_split_mode = "words"
        harness.segmented_proactive_regex = r".*?[。？！~…\n]+|.+$"
        harness.segmented_proactive_split_words = [".", "。"]
        harness.enable_segmented_proactive_content_cleanup = True
        harness.segmented_proactive_content_cleanup_scope = "all"
        harness.segmented_proactive_content_cleanup_rule = ""
        harness.segmented_proactive_content_cleanup_words = [".", "。"]
        filenames = [
            "对象.jpg",
            "截图.PNG",
            "说明.docx",
            "语音.mp3",
            "视频.mp4",
            "归档.zip",
            "配置.json",
        ]

        segments = harness._split_proactive_text("。".join([*filenames, "下一句."]))

        self.assertEqual(segments, [*filenames, "下一句"])

        segments = harness._split_proactive_text("前一句.对象.jpg.下一句.")

        self.assertEqual(segments, ["前一句", "对象.jpg", "下一句"])

    def test_tts_followup_does_not_split_jpg_suffix_into_its_own_bubble(self) -> None:
        harness = _SegmentTtsHarness()
        harness.enable_segmented_proactive_reply = True
        harness.segmented_proactive_scope = "all_llm"
        harness.segmented_proactive_threshold = 500
        harness.segmented_proactive_min_segment_chars = 1
        harness.segmented_proactive_max_segments = 5
        harness.segmented_proactive_split_mode = "words"
        harness.segmented_proactive_regex = r".*?[。？！~…\n]+|.+$"
        harness.segmented_proactive_split_words = [".", "。", "～"]
        harness.enable_segmented_proactive_content_cleanup = False
        harness.segmented_proactive_content_cleanup_scope = "all"
        harness.segmented_proactive_content_cleanup_rule = ""
        harness.segmented_proactive_content_cleanup_words = []
        harness.enable_segmented_proactive_content_replacement = False
        harness.segmented_proactive_content_replacements = []
        harness.tts_voice_language = "zh"
        harness._segmented_scope_allows_event = lambda _event: True
        event = SimpleNamespace(unified_msg_origin="default:FriendMessage:10001")

        chunks = harness._tts_segment_plain_chunk_for_ordered_send(
            event,
            [Plain("对象.jpg 我这儿可没有这个功能呢～下一句。")],
        )

        texts = [chunk[0].text for chunk in chunks]
        self.assertEqual(texts, ["对象.jpg 我这儿可没有这个功能呢～", "下一句。"])
        self.assertFalse(any(text.startswith("jpg") for text in texts))

    def test_marked_tts_transcript_uses_real_word_segmentation(self) -> None:
        harness = _SegmentTtsHarness()
        harness.enable_segmented_proactive_reply = True
        harness.segmented_proactive_scope = "all_llm"
        harness.segmented_proactive_threshold = 500
        harness.segmented_proactive_min_segment_chars = 5
        harness.segmented_proactive_max_segments = 5
        harness.segmented_proactive_split_mode = "words"
        harness.segmented_proactive_regex = r"(?<=[。！？!?…~～])\s*|\n+"
        harness.segmented_proactive_split_words = [
            "。", "？", "！", "~", "?", ".", "!", ";", "；", "……", "（", "“", "，", "…"
        ]
        harness.enable_segmented_proactive_content_cleanup = True
        harness.segmented_proactive_content_cleanup_scope = "all"
        harness.segmented_proactive_content_cleanup_rule = r"[\n。？！]"
        harness.segmented_proactive_content_cleanup_words = ["。", "，"]
        harness.enable_segmented_proactive_content_replacement = False
        harness.segmented_proactive_content_replacements = []
        harness.tts_voice_language = "ja"
        harness._segmented_scope_allows_event = lambda _event: True
        visible = harness._mark_tts_visible_plain(
            "嗯…这是第一条测试！第二条仍然保留…第三条用于核对。"
            "接着验证最后部分…所有文本都应完整保留。"
        )

        chunks = harness._tts_segment_plain_chunk_for_ordered_send(
            SimpleNamespace(unified_msg_origin="default:FriendMessage:100000001"),
            [visible],
        )

        self.assertEqual(5, len(chunks))
        self.assertTrue(
            all(chunk[0]._private_companion_tts_visible_text for chunk in chunks)
        )
        self.assertEqual(
            "嗯，这是第一条测试！",
            chunks[0][0].text,
        )
        self.assertIn("完整保留", chunks[-1][0].text)


if __name__ == "__main__":
    unittest.main()
