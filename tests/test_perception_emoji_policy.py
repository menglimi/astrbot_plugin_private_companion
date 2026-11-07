"""Stickers are read as feelings; photos are read as content."""

import unittest

from astrbot_host.core_reply_perception import _POLICY


class PerceptionEmojiPolicyTests(unittest.TestCase):
    def test_stickers_are_read_as_emotion_before_content(self):
        self.assertIn("表情、表情包和贴纸", _POLICY)
        self.assertIn("首先当作情绪表达", _POLICY)
        self.assertIn("先判断它传递的情绪、语气或态度", _POLICY)
        self.assertIn("不逐项描述画面", _POLICY)
        self.assertIn("复述贴纸上的文字", _POLICY)

    def test_photos_and_screenshots_still_keep_content_reading(self):
        self.assertIn("普通照片、截图和文件图按画面内容理解", _POLICY)
        self.assertIn("明确问图片内容或细节时直接回答", _POLICY)

    def test_markers_are_named_so_the_model_can_tell_the_kind(self):
        self.assertIn("[表情]", _POLICY)
        self.assertIn("[表情包]", _POLICY)


if __name__ == "__main__":
    unittest.main()
