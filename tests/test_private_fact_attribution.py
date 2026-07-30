import unittest
import time
from datetime import datetime

from astrbot_plugin_private_companion.user_memory import UserMemoryMixin


class _FactAttributionHarness(UserMemoryMixin):
    def __init__(self):
        self.response_review_max_chars = 260
        self.enable_passive_topic_suppression = False
        self.enable_response_self_review = True
        self.response_review_mode = "severe_only"
        self.response_review_provider_id = "review"
        self.mai_style_provider_id = "style"
        self.proactive_reply_context_hours = 12
        self.captured_prompt = ""
        self.review_output = ""

    @staticmethod
    def _response_has_invalid_current_time_anchor(text):
        return False

    @staticmethod
    def _response_has_false_no_reply_claim(text, inbound_text, user):
        return False

    @staticmethod
    def _expression_style_review_enabled():
        return False

    @staticmethod
    def _proactive_topic_signature(text):
        return text

    @staticmethod
    def _task_provider(*values):
        return next((value for value in values if value), "")

    @staticmethod
    def _environment_now():
        return datetime(2026, 7, 13, 1, 50)

    async def _resolve_proactive_persona_prompt(self, user):
        return "冷静、俏皮，承认错误时直接一点。"

    @staticmethod
    def _format_reply_style_prompt():
        return "一到两句，简体中文。"

    @staticmethod
    def _format_display_name_rename_events(_events, *, limit=3):
        return ""

    async def _llm_call(self, prompt, **kwargs):
        self.captured_prompt = prompt
        if self.review_output:
            return self.review_output
        issue_block = prompt.split("【需要修正的问题】", 1)[-1].split("【当前意图/情绪】", 1)[0]
        if "denies_existing_creative_work" in issue_block:
            return "写过呀，有几篇已经收尾了，不过还没有正式出版。"
        return "啊，对，是我先提的。这个点就不惦记冰饮啦。"


class PrivateFactAttributionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.harness = _FactAttributionHarness()

    def test_detects_natural_fact_corrections(self):
        self.assertTrue(self.harness._looks_like_private_fact_correction("明明是小星想的呢"))
        self.assertTrue(self.harness._looks_like_private_fact_correction("你记反了，是你先提的"))
        self.assertFalse(self.harness._looks_like_private_fact_correction("这么晚了还喝冰的"))
        self.assertFalse(self.harness._looks_like_private_fact_correction("我不是想吃冰的，只是随口问问"))

    def test_correction_is_kept_for_two_followup_turns(self):
        user = {"private_inbound_count": 10}
        self.assertTrue(self.harness._record_recent_private_fact_correction(user, "明明是小星想的呢"))

        user["private_inbound_count"] = 12
        self.assertEqual(
            self.harness._active_private_fact_correction(user, "这么晚了还喝冰的"),
            "明明是小星想的呢",
        )
        user["private_inbound_count"] = 13
        self.assertEqual(self.harness._active_private_fact_correction(user, "换个话题"), "")

    def test_guard_explains_memory_narrator_and_current_correction(self):
        user = {"private_inbound_count": 1}
        self.harness._record_recent_private_fact_correction(user, "不是我说的，是你先提的")
        guard = self.harness._format_private_fact_attribution_guard(user, "")

        self.assertIn("“我”是当前 Bot/人格", guard)
        self.assertIn("不得把“Bot 提过", guard)
        self.assertIn("不是我说的，是你先提的", guard)

    def test_private_identity_anchor_keeps_one_configured_address(self):
        self.harness.default_nickname = "你"

        anchor = self.harness._format_private_identity_anchor_for_prompt(
            "u1",
            {
                "nickname": "阿岚",
                "last_display_name": "岚岚",
                "observed_display_names": ["小岚"],
            },
        )

        self.assertIn("只使用“阿岚”", anchor)
        self.assertIn("不必每句都带称呼", anchor)
        self.assertIn("关系阶段、旧记忆、显示名和别名不能据此另造亲昵称呼", anchor)
        self.assertIn("岚岚", anchor)

    def test_recent_proactive_photo_reaction_is_owned_by_bot(self):
        now = time.time()
        user = {
            "last_proactive_action": "photo_text",
            "last_proactive_sent_at": now,
            "last_proactive_message": "你看这个淡紫色的试管反应，超好看的～",
            "last_photo_share_snapshot": {
                "caption": "她把试剂滴进试管时不小心洒出来了一点。",
                "subject_owner": "bot",
                "sent_at": now,
                "expires_at": now + 3600,
            },
        }

        guard = self.harness._format_private_fact_attribution_guard(user, "啊啊啊洒出来了啊啊")

        self.assertIn("本轮主动图片归属", guard)
        self.assertIn("不是在报告自己做了图中的事", guard)
        self.assertIn("属于 Bot/当前人格", guard)

    def test_explicit_user_owned_accident_does_not_use_photo_ownership_guard(self):
        now = time.time()
        user = {
            "last_proactive_action": "photo_text",
            "last_proactive_sent_at": now,
            "last_photo_share_snapshot": {
                "caption": "桌上放着一支试管。",
                "subject_owner": "scene",
                "sent_at": now,
                "expires_at": now + 3600,
            },
        }

        guard = self.harness._format_private_fact_attribution_guard(user, "我把试剂洒了")

        self.assertNotIn("本轮主动图片归属", guard)

    def test_old_or_non_media_proactive_does_not_claim_image_ownership(self):
        old_photo = {
            "last_proactive_action": "photo_text",
            "last_proactive_sent_at": time.time() - 31 * 60,
        }
        non_media = {
            "last_proactive_action": "message",
            "last_proactive_sent_at": time.time(),
        }

        self.assertNotIn(
            "本轮主动图片归属",
            self.harness._format_private_fact_attribution_guard(old_photo, "洒出来了"),
        )
        self.assertNotIn(
            "本轮主动图片归属",
            self.harness._format_private_fact_attribution_guard(non_media, "洒出来了"),
        )

    def test_reply_blames_user_for_bot_photo_accident_is_flagged(self):
        now = time.time()
        user = {
            "last_proactive_action": "photo_text",
            "last_proactive_sent_at": now,
            "last_photo_share_snapshot": {
                "caption": "她把试剂洒出来了一点。",
                "subject_owner": "bot",
                "sent_at": now,
                "expires_at": now + 3600,
            },
        }

        flags = self.harness._response_review_flags(
            "怎么笨手笨脚的呀，有没有溅到手上呀？",
            user,
            inbound_text="啊啊啊洒出来了啊啊",
        )

        self.assertIn("proactive_media_ownership_reversal", flags)

    def test_short_reply_with_unsupported_user_attribution_is_flagged(self):
        user = {"nickname": "阿岚", "private_inbound_count": 1}
        flags = self.harness._response_review_flags(
            "话说阿岚大人上次说的那家小店，后来开门了没？",
            user,
            inbound_text="那家店呢",
        )
        self.assertIn("unverified_fact_attribution", flags)

    def test_followup_cannot_reverse_recent_correction(self):
        user = {"nickname": "阿岚", "private_inbound_count": 4}
        self.harness._record_recent_private_fact_correction(user, "明明是小星先想去的")
        user["private_inbound_count"] = 5

        flags = self.harness._response_review_flags(
            "明明是阿岚大人先拿这件事诱惑我的。",
            user,
            inbound_text="现在都几点了",
        )
        self.assertIn("fact_attribution_after_correction", flags)

    def test_neutral_followup_is_not_flagged(self):
        user = {"nickname": "阿岚", "private_inbound_count": 1}
        flags = self.harness._response_review_flags(
            "是之前提到的那家小店吗？后来开门了没？",
            user,
            inbound_text="那家店呢",
        )
        self.assertNotIn("unverified_fact_attribution", flags)

    async def test_correction_review_receives_persona_and_fact_guard(self):
        user = {
            "umo": "session-a",
            "nickname": "阿岚",
            "private_inbound_count": 2,
            "last_companion_message": "是我先提的。",
        }
        self.harness._record_recent_private_fact_correction(user, "明明是小星想的呢")

        result = await self.harness._review_and_rewrite_response(
            user,
            "这么晚了还喝冰的",
            "明明是阿岚大人先拿冰饮诱惑我的。",
        )

        self.assertIn("是我先提的", result)
        self.assertIn("冷静、俏皮", self.harness.captured_prompt)
        self.assertIn("最近的高优先级纠正：明明是小星想的呢", self.harness.captured_prompt)
        self.assertIn("fact_attribution_after_correction", self.harness.captured_prompt)

    async def test_existing_creative_work_denial_is_rewritten(self):
        user = {"umo": "session-a", "nickname": "阿岚", "private_inbound_count": 1}
        creative_context = (
            "【私下创作近况】\n"
            "真实创作记录：共有 3 个已有正文的文本作品，其中已完成 2 个、仍在写 1 个。\n"
            "这些记录不等于正式出版。"
        )

        result = await self.harness._review_and_rewrite_response(
            user,
            "你写过书吗",
            "没有呀，我没写过自己的作品。",
            creative_context=creative_context,
        )

        self.assertIn("写过呀", result)
        self.assertIn("denies_existing_creative_work", self.harness.captured_prompt)
        self.assertIn("共有 3 个已有正文的文本作品", self.harness.captured_prompt)

    async def test_review_commentary_is_not_used_as_reply_text(self):
        user = {
            "umo": "session-a",
            "nickname": "阿岚",
            "private_inbound_count": 2,
            "last_companion_message": "是我先提的。",
        }
        self.harness._record_recent_private_fact_correction(user, "不是我说的，是你先提的")
        self.harness.review_output = (
            "属于正常人无法容忍的一字废话，存在严重的格式化表达问题，"
            "无法通过清洗正常规整，需要重写\n"
            "Maybe 1% of the time you will leave this at the very end of a run"
        )
        original = "明明是阿岚大人先拿这件事诱惑我的。"

        result = await self.harness._review_and_rewrite_response(user, "是你先提的", original)

        self.assertEqual(original, result)
        self.assertNotIn("无法通过清洗", result)
        self.assertNotIn("Maybe 1%", result)

    def test_review_meta_line_stripper_preserves_sendable_line(self):
        cleaned, reason = self.harness._strip_response_review_meta_leak(
            "好，我重新说清楚。\nMaybe 1% of the time you will leave this at the very end of a run"
        )

        self.assertEqual("好，我重新说清楚。", cleaned)
        self.assertIn("概率说明", reason)


if __name__ == "__main__":
    unittest.main()
