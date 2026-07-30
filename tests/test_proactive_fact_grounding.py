# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import time
import unittest

from astrbot_plugin_private_companion.proactive_message import ProactiveMessageMixin
from astrbot_plugin_private_companion.user_memory import UserMemoryMixin


class _FactGroundingHarness(ProactiveMessageMixin):
    @staticmethod
    def _proactive_review_strength() -> str:
        return "balanced"

    @staticmethod
    def _wrong_proactive_recipient_address(*_args, **_kwargs) -> str:
        return ""


class _ReviewDisabledHarness(ProactiveMessageMixin):
    enable_response_self_review = False
    enable_passive_response_review = True
    enable_proactive_message_review = False

    def __init__(self) -> None:
        self.rewrite_called = False

    @staticmethod
    def _proactive_review_strength() -> str:
        return "balanced"

    @staticmethod
    def _wrong_proactive_recipient_address(*_args, **_kwargs) -> str:
        return ""

    @staticmethod
    def _local_proactive_send_decision(*_args, **_kwargs):
        return {
            "decision": "rewrite",
            "reason": "来源不一致",
            "reference_text": "旧来源 https://example.com/stale",
            "hard": True,
        }

    async def _rewrite_reference_reply_with_persona(self, *_args, **_kwargs):
        self.rewrite_called = True
        return "不应该执行"


class _LocalRewriteDisabledHarness(_ReviewDisabledHarness):
    @staticmethod
    def _local_proactive_send_decision(*_args, **_kwargs):
        return {
            "decision": "rewrite",
            "reason": "连续未回应时主动偏长",
            "text": "刚写到庭院夜风那段，像在听一首很轻的歌谣。",
            "hard": False,
        }


class _ActualLocalReviewDisabledHarness(ProactiveMessageMixin):
    enable_proactive_message_review = False
    proactive_review_mode = "full"
    proactive_review_strength = "balanced"
    proactive_review_hard_risk_threshold = 0.45
    proactive_review_low_score_threshold = 0.34
    proactive_review_pressure_threshold = 0.55

    @staticmethod
    def _wrong_proactive_recipient_address(*_args, **_kwargs) -> str:
        return ""

    @staticmethod
    def _private_user_role(*_args, **_kwargs) -> str:
        return "owner"

    @staticmethod
    def _planned_proactive_semantics(*_args, **_kwargs) -> dict:
        return {}


class _ReplyContextHarness(UserMemoryMixin):
    proactive_reply_context_hours = 12
    default_nickname = "小弥生"

    def __init__(self) -> None:
        self._data_lock = asyncio.Lock()
        self.data = {
            "users": {
                "10001": {
                    "umo": "default:GroupMessage:20001",
                    "last_proactive_message": "刚刷到 B站《春季新番预告》，想给你看看。",
                    "last_proactive_sent_at": time.time(),
                    "last_proactive_delivery_umo": "default:GroupMessage:20001",
                    "last_proactive_reply_context_consumed_for": 0,
                }
            }
        }

    def _get_user(self, user_id: str):
        return self.data["users"][str(user_id)]

    def _save_data_sync(self) -> None:
        return None


class _ReplyEvent:
    unified_msg_origin = "default:GroupMessage:20001"

    @staticmethod
    def get_sender_id() -> str:
        return "10001"


class ProactiveFactGroundingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = _FactGroundingHarness()

    def test_meal_care_removes_unverified_story_but_keeps_soft_question(self) -> None:
        decision = self.harness._unverified_proactive_fact_decision(
            "测试用户～ 刚刚刷到汉堡的视频，突然觉得你昨天吃的那个汉堡，"
            "看起来好像真的很好吃呢。 今晚有乖乖吃晚饭嘛？",
            reason="meal_care",
            action="message",
            action_context="文字",
        )

        self.assertIsNotNone(decision)
        self.assertEqual(decision["decision"], "rewrite")
        self.assertEqual(decision["text"], "测试用户～ 今晚有乖乖吃晚饭嘛？")
        self.assertTrue(decision["hard"])

    def test_meal_care_drops_stale_meal_attribution_without_safe_remainder(self) -> None:
        decision = self.harness._unverified_proactive_fact_decision(
            "你昨天吃的那个汉堡看起来很好吃。",
            reason="meal_care",
            action="message",
            action_context="文字",
        )

        self.assertIsNotNone(decision)
        self.assertEqual(decision["decision"], "drop")

    def test_real_external_share_source_allows_recent_action_wording(self) -> None:
        decision = self.harness._unverified_proactive_fact_decision(
            "刚刚刷到一个挺有意思的视频，想给你看看。",
            reason="bili_video_share",
            action="message",
            action_context="标题：测试；来源链接：https://www.bilibili.com/video/BV1test",
        )

        self.assertIsNone(decision)

    def test_group_share_context_is_treated_as_a_verified_source(self) -> None:
        self.assertTrue(
            self.harness._proactive_has_verified_recent_fact_source(
                reason="group_share",
                action="message",
                action_context=(
                    "群聊分享线索：测试群（群号 300000101）\n"
                    "代表性片段：群友甲: @testbot 出来回话\n"
                    "消息指向：结构化场景确认该消息对 Bot 说话。"
                ),
            )
        )

    def test_environment_change_cannot_read_stale_web_exploration_link(self) -> None:
        anchor = self.harness._external_share_anchor_text(
            {
                "web_exploration_context": {
                    "source_title": "天气变化与身体感受",
                    "source_url": "https://m.sohu.com/a/test-article",
                }
            },
            reason="environment_change",
            topic="外面开始下雨",
            motive="想趁变化还新鲜时自然提一句",
        )

        self.assertIn("外面开始下雨", anchor)
        self.assertNotIn("sohu.com", anchor)
        self.assertNotIn("天气变化与身体感受", anchor)

    def test_environment_change_with_article_link_is_dropped_locally(self) -> None:
        decision = self.harness._local_proactive_send_decision(
            {},
            "刚看到《外面开始下雨》，有点想丢给你看一眼。https://m.sohu.com/a/stale",
            reason="environment_change",
            action="message",
            motive="自然提一句",
            topic="外面开始下雨",
            action_context="文字",
        )

        self.assertEqual(decision["decision"], "drop")
        self.assertTrue(decision["hard"])

    def test_bilibili_claim_with_reddit_link_is_dropped_even_without_review(self) -> None:
        decision = self.harness._local_proactive_send_decision(
            {},
            "刚刷到 B站《新番里男主角的告白场景》，这个标题有点想丢给你看一眼。"
            "https://www.reddit.com/r/example/comments/test-post/sample/",
            reason="bili_video_share",
            action="message",
            motive="分享视频",
            topic="新番里男主角的告白场景",
            action_context="文字",
        )

        self.assertEqual(decision["decision"], "drop")
        self.assertIn("Reddit", decision["reason"])
        self.assertTrue(decision["hard"])

    def test_disabled_proactive_review_never_calls_reference_rewrite_model(self) -> None:
        harness = _ReviewDisabledHarness()
        decision = asyncio.run(
            harness._review_proactive_message_send_decision(
                {"nickname": "测试用户"},
                "外面开始下雨啦。",
                reason="environment_change",
                action="message",
                motive="自然提一句",
                topic="外面开始下雨",
            )
        )

        self.assertEqual(decision["decision"], "drop")
        self.assertFalse(harness.rewrite_called)

    def test_disabled_review_uses_complete_local_rewrite_without_calling_model(self) -> None:
        harness = _LocalRewriteDisabledHarness()
        decision = asyncio.run(
            harness._review_proactive_message_send_decision(
                {"nickname": "测试用户"},
                "刚写到推门进庭院那段，风穿过常绿树的声音写得有点入迷，像在听一首很轻的歌谣。",
                reason="creative_writing",
                action="message",
                motive="想分享刚写到的片段",
                topic="庄园夜风与茉莉香",
            )
        )

        self.assertEqual(decision["decision"], "rewrite")
        self.assertEqual(decision["text"], "刚写到庭院夜风那段，像在听一首很轻的歌谣。")
        self.assertIn("本地确定性改写", decision["reason"])
        self.assertFalse(harness.rewrite_called)

    def test_local_only_mode_also_uses_complete_local_rewrite(self) -> None:
        harness = _LocalRewriteDisabledHarness()
        harness.enable_proactive_message_review = True
        harness.proactive_review_mode = "local_only"

        decision = asyncio.run(
            harness._review_proactive_message_send_decision(
                {"nickname": "测试用户"},
                "这是一条需要缩短的主动消息。",
                reason="creative_writing",
                action="message",
            )
        )

        self.assertEqual(decision["decision"], "rewrite")
        self.assertIn("仅本地检查模式", decision["reason"])
        self.assertFalse(harness.rewrite_called)

    def test_long_creative_share_is_locally_shortened_instead_of_dropped(self) -> None:
        harness = _ActualLocalReviewDisabledHarness()
        original = "刚写到推门进庭院那段，风穿过常绿树的声音写得有点入迷，像在听一首很轻的歌谣。"

        decision = asyncio.run(
            harness._review_proactive_message_send_decision(
                {"nickname": "测试用户", "ignored_streak": 2},
                original,
                reason="creative_writing",
                action="message",
                motive="想分享刚写到的片段",
                topic="庄园夜风与茉莉香",
            )
        )

        self.assertEqual(decision["decision"], "rewrite")
        self.assertLess(len(decision["text"]), len(original))
        self.assertIn("连续未回应时主动偏长", decision["reason"])

    def test_next_same_session_reply_knows_the_last_proactive_message(self) -> None:
        harness = _ReplyContextHarness()
        first = asyncio.run(harness._format_proactive_reply_context(_ReplyEvent()))
        second = asyncio.run(harness._format_proactive_reply_context(_ReplyEvent()))

        self.assertIn("刚才你主动发出的消息", first)
        self.assertIn("春季新番预告", first)
        self.assertIn("不得声称不知道自己发了什么", first)
        self.assertEqual(second, "")

if __name__ == "__main__":
    unittest.main()
