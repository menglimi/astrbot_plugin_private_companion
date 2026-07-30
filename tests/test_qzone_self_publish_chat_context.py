# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
import time

from astrbot_plugin_private_companion.daily_state import DailyStateMixin
from astrbot_plugin_private_companion.llm_tool_actions import LlmToolActionsMixin
from astrbot_plugin_private_companion.qzone_integration import QzoneMixin


class _QzoneChatHarness(LlmToolActionsMixin, QzoneMixin, DailyStateMixin):
    schedule_persona_prompt = "高一学生"
    schedule_worldview_prompt = "现代校园"

    def __init__(self, items) -> None:
        self.enabled = True
        self.enable_qzone_integration = True
        self.data = {"qzone_integration": {"recent_life_publish_texts": items}}


class QzoneSelfPublishChatContextTests(unittest.TestCase):
    def test_recent_bot_posts_are_labeled_and_latest_first(self) -> None:
        harness = _QzoneChatHarness(
            [
                {"text": "早一点发的内容", "image_count": 0},
                {"text": "刚刚发的内容", "image_count": 2},
            ]
        )

        context = harness._qzone_recent_self_publish_chat_context()

        self.assertIn("Bot 自己最近成功发布", context)
        self.assertIn("最新一条：刚刚发的内容；配图 2 张", context)
        self.assertLess(context.index("刚刚发的内容"), context.index("早一点发的内容"))
        self.assertIn("不是当前用户发的内容", context)

    def test_qzone_instruction_disambiguates_bot_and_user(self) -> None:
        instruction = _QzoneChatHarness([{"text": "自己的动态"}])._qzone_tool_instruction()

        self.assertIn("“你”指 Bot 自己，不是当前用户", instruction)
        self.assertIn("自己的动态", instruction)
        self.assertIn("不要让用户自己去看", instruction)

    def test_empty_publish_history_does_not_add_fake_context(self) -> None:
        instruction = _QzoneChatHarness([])._qzone_tool_instruction()

        self.assertNotIn("Bot 自己最近成功发布的 QQ 空间记录", instruction)
        self.assertIn("不要假装已经发布", instruction)

    def test_recent_publish_context_does_not_feed_an_unverified_mother(self) -> None:
        harness = _QzoneChatHarness(
            [{"text": "桌上是妈妈洗的青提，后来翻开练习册。", "image_count": 0}]
        )

        context = harness._qzone_recent_publish_context(harness.data["qzone_integration"])

        self.assertNotIn("妈妈", context)
        self.assertIn("后来翻开练习册", context)

    def test_reusable_draft_is_cleaned_before_it_can_be_published(self) -> None:
        harness = _QzoneChatHarness([])
        state = {
            "last_life_publish_status": "failed:network",
            "last_life_publish_draft_at": time.time(),
            "last_life_publish_draft": "妈妈洗了青提，后来坐回桌边，把剩下的物理题慢慢写完了。",
        }

        draft = harness._qzone_reusable_draft(state, "life_publish")

        self.assertNotIn("妈妈", draft)
        self.assertIn("后来坐回桌边", draft)
        self.assertIn("物理题慢慢写完", draft)


if __name__ == "__main__":
    unittest.main()
