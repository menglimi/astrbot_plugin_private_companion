# -*- coding: utf-8 -*-

from __future__ import annotations

from types import SimpleNamespace
import unittest

from astrbot_plugin_private_companion.qzone_integration import QzoneMixin


class _QzoneReplyHarness(QzoneMixin):
    async def _qzone_comment_post(self, _event, _post, content: str = "") -> str:
        self.sent_text = content
        return content


class QzoneCommentReplyStyleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.plugin = _QzoneReplyHarness()

    def test_display_name_prefix_and_terminal_emoji_are_removed(self) -> None:
        reply = self.plugin._qzone_clean_comment_reply_text(
            "阿~羽绒服，还没呢，猫在旁边打呼噜😂",
            "阿~羽绒服",
        )

        self.assertEqual(reply, "还没呢，猫在旁边打呼噜")

    async def test_comment_reply_does_not_readd_commenter_name(self) -> None:
        post = SimpleNamespace()
        comment = SimpleNamespace(name="阿~羽绒服")

        sent = await self.plugin._qzone_reply_to_comment(
            None,
            post,
            comment,
            "阿~羽绒服，还没呢😂",
        )

        self.assertEqual(sent, "还没呢")
        self.assertEqual(self.plugin.sent_text, "还没呢")


if __name__ == "__main__":
    unittest.main()
