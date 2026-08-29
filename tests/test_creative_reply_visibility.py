import unittest

from astrbot_plugin_private_companion.daily_state import DailyStateMixin
from astrbot_plugin_private_companion.main import PrivateCompanionPlugin


class _CreativeQueryHarness(DailyStateMixin):
    _is_lightweight_private_passive_inbound = PrivateCompanionPlugin._is_lightweight_private_passive_inbound

    def __init__(self):
        self.enable_creative_writing = True
        self.data = {
            "creative_projects": [
                {
                    "title": "雨停以前",
                    "work_type": "短篇小说",
                    "status": "finished",
                    "current_chars": 1200,
                    "target_chars": 1200,
                    "premise": "雨夜里的小故事",
                    "draft_chunks": [{"text": "雨声落在旧窗台上。"}],
                },
                {
                    "title": "纸灯",
                    "work_type": "随笔",
                    "status": "finished",
                    "current_chars": 800,
                    "target_chars": 800,
                    "premise": "关于一盏旧灯",
                    "draft_chunks": [{"text": "灯影在纸上慢慢晃。"}],
                },
                {
                    "title": "楼梯尽头",
                    "work_type": "短篇小说",
                    "status": "drafting",
                    "current_chars": 500,
                    "target_chars": 1500,
                    "premise": "尚未写完的楼梯",
                    "draft_chunks": [{"text": "最后一级台阶没有影子。"}],
                },
            ]
        }

    def _creative_projects(self):
        return self.data["creative_projects"]

    @staticmethod
    def _creative_work_type(project):
        return str(project.get("work_type") or "文本作品")


class CreativeReplyVisibilityTests(unittest.TestCase):
    def setUp(self):
        self.harness = _CreativeQueryHarness()

    def test_writing_existence_phrases_are_detected(self):
        for text in (
            "你写过书吗",
            "你不是写了小说吗",
            "有没有自己的作品",
            "你写的那本书呢",
            "你出版过书吗",
        ):
            with self.subTest(text=text):
                self.assertTrue(self.harness._user_asks_recent_creative_activity(text))

    def test_reading_question_is_not_misclassified_as_authorship(self):
        self.assertFalse(self.harness._user_asks_creative_work_existence("你现在在看什么书"))
        self.assertFalse(self.harness._user_asks_creative_work_existence("这本书好看吗"))

    def test_creative_question_exits_lightweight_path(self):
        self.assertFalse(self.harness._is_lightweight_private_passive_inbound("你写过书吗"))
        self.assertFalse(self.harness._is_lightweight_private_passive_inbound("现在能看到资料柜吗"))
        self.assertTrue(self.harness._is_lightweight_private_passive_inbound("嗯嗯"))

    def test_creative_context_reports_inventory_and_publication_boundary(self):
        context = self.harness._format_hidden_creative_context_for_reply("你写过书吗", {})

        self.assertIn("共有 3 个已有正文的文本作品", context)
        self.assertIn("已完成 2 个、仍在写 1 个", context)
        self.assertIn("不能回答“没写过书/没有自己的作品”", context)
        self.assertIn("不等于正式出版或发行过实体书", context)
        self.assertIn("楼梯尽头", context)

    def test_bookshelf_inventory_question_injects_real_titles(self):
        context = self.harness._format_hidden_creative_context_for_reply("现在能看到资料柜吗", {})

        self.assertIn("共有 3 个已有正文的文本作品", context)
        self.assertIn("用户正在询问能否看到资料柜", context)
        self.assertIn("禁止用括号动作或假装翻找", context)

    def test_empty_bookshelf_inventory_is_explicit(self):
        self.harness.data["creative_projects"] = []

        context = self.harness._format_hidden_creative_context_for_reply("资料柜里有什么", {})

        self.assertIn("当前资料柜创作区确实没有保存过正文的作品", context)
        self.assertIn("不要假装翻找", context)

    def test_structured_inventory_context_owns_its_branch_title(self):
        self.harness.data["creative_projects"] = []

        section = self.harness._format_hidden_creative_context_for_reply(
            "资料柜里有什么",
            {},
            as_section=True,
        )
        self.assertEqual("资料柜创作区真实库存", section["title"])
        self.assertNotIn("【资料柜创作区真实库存】", section["content"])

    def test_structured_creative_context_keeps_real_work_title(self):
        section = self.harness._format_hidden_creative_context_for_reply(
            "你写过书吗",
            {},
            as_section=True,
        )
        self.assertEqual("私下创作近况", section["title"])
        self.assertIn("标题：楼梯尽头", section["content"])
        self.assertNotIn("标题：私下创作近况", section["content"])


if __name__ == "__main__":
    unittest.main()
