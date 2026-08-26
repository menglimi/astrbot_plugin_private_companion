# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
import time

from astrbot_plugin_private_companion.group_observation import GroupObservationMixin
from astrbot_plugin_private_companion.group_wakeup import GroupWakeupMixin


class _GroupShareHarness(GroupObservationMixin):
    bot_name = "testbot"
    proactive_reply_context_hours = 12

    @staticmethod
    def _group_member_identity_label(user_id, name, *, limit=24):
        return str(name or user_id)[:limit]


class _RealMroGroupShareHarness(GroupWakeupMixin, GroupObservationMixin):
    bot_name = "testbot"
    proactive_reply_context_hours = 12

    @staticmethod
    def _group_member_identity_label(user_id, name, *, limit=24):
        return str(name or user_id)[:limit]


class GroupShareFactGroundingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = _GroupShareHarness()

    @staticmethod
    def _message(index: int, *, to_bot: bool) -> dict:
        if to_bot:
            return {
                "ts": 1000 + index,
                "sender_id": "100000101",
                "name": "群友甲",
                "text": "@testbot 出来回话",
                "talking_to": "bot",
                "talking_to_name": "你",
                "scene_trigger": "at_bot",
                "at_targets": [{"user_id": "bot-id", "name": "testbot", "is_bot": True}],
            }
        return {
            "ts": 1000 + index,
            "sender_id": "100000101",
            "name": "群友甲",
            "text": "@群友乙 最近在干嘛",
            "talking_to": "100000102",
            "talking_to_name": "群友乙",
            "scene_trigger": "at_other",
            "at_targets": [{"user_id": "100000102", "name": "群友乙", "is_bot": False}],
        }

    def test_at_other_never_becomes_bot_harassment(self) -> None:
        group = {"recent_messages": [self._message(index, to_bot=False) for index in range(6)]}

        candidate = self.harness._group_bot_harassment_candidate("300000101", group, now=1100)

        self.assertIsNone(candidate)

    def test_explicit_at_bot_still_becomes_bot_harassment(self) -> None:
        group = {"recent_messages": [self._message(index, to_bot=True) for index in range(3)]}

        candidate = self.harness._group_bot_harassment_candidate("300000101", group, now=1100)

        self.assertIsNotNone(candidate)
        self.assertTrue(candidate["addressed_to_bot"])

    def test_real_plugin_mro_uses_recorded_message_contract(self) -> None:
        harness = _RealMroGroupShareHarness()
        group = {"recent_messages": [self._message(index, to_bot=True) for index in range(3)]}

        candidate = harness._group_bot_harassment_candidate(
            "300000101",
            group,
            now=1100,
        )

        self.assertIsNotNone(candidate)
        self.assertTrue(candidate["addressed_to_bot"])

    def test_at_other_pressure_cannot_replace_the_verified_source_message(self) -> None:
        messages = [self._message(index, to_bot=True) for index in range(2)]
        at_other = self._message(3, to_bot=False)
        at_other["text"] = "@群友乙 笨蛋"
        messages.append(at_other)

        candidate = self.harness._group_bot_harassment_candidate(
            "300000101",
            {"recent_messages": messages},
            now=1100,
        )

        self.assertIsNotNone(candidate)
        self.assertIn("testbot", candidate["text"])
        self.assertNotIn("群友乙", candidate["text"])

    def test_action_context_marks_other_member_as_the_target(self) -> None:
        now = time.time()
        user = {
            "group_share_context": {
                "group_id": "300000101",
                "group_name": "测试群",
                "speaker": "群友甲",
                "text": "@群友乙 最近在干嘛",
                "event_ts": now,
                "created_ts": now,
                "addressed_to_bot": False,
                "source_talking_to": "100000102",
                "source_talking_to_name": "群友乙",
            }
        }

        context = self.harness._format_group_share_action_context(user)

        self.assertIn("测试群（群号 300000101）", context)
        self.assertIn("明确对群友 群友乙说话，不是对 Bot", context)
        self.assertIn("昵称", context)

    def test_source_snapshot_survives_context_cleanup_for_specific_followups(self) -> None:
        user = {
            "last_proactive_reason": "group_share",
            "last_proactive_sent_at": 2000,
            "last_proactive_delivery_umo": "default:FriendMessage:10001",
        }
        share = {
            "group_id": "300000101",
            "group_name": "测试群",
            "kind": "funny",
            "speaker_id": "100000101",
            "speaker": "群友甲[QQ:100000101]",
            "text": "@群友乙 最近在干嘛",
            "summary": "群友甲: @群友乙 最近在干嘛",
            "event_ts": 1900,
            "addressed_to_bot": False,
            "source_talking_to": "100000102",
            "source_talking_to_name": "群友乙[QQ:100000102]",
            "source_trigger": "at_other",
        }
        self.harness._remember_recent_group_share_snapshot(
            user,
            share_context=share,
            shared_text="群里有个挺有意思的片段。",
            sent_at=2000,
            delivery_umo="default:FriendMessage:10001",
        )
        user["group_share_context"] = {}

        context = self.harness._format_recent_group_share_snapshot_for_reply(
            user,
            "具体是哪个群，谁说的？",
            event_umo="default:FriendMessage:10001",
            now=2100,
        )

        self.assertIn("测试群（群号 300000101）", context)
        self.assertIn("来源成员：群友甲", context)
        self.assertIn("不是对 Bot", context)
        self.assertNotIn("[QQ:", context)

    def test_legacy_group_share_without_snapshot_forbids_guessing(self) -> None:
        user = {
            "last_proactive_reason": "group_share",
            "last_proactive_sent_at": 2000,
            "last_proactive_delivery_umo": "default:FriendMessage:10001",
        }

        context = self.harness._format_recent_group_share_snapshot_for_reply(
            user,
            "具体是谁？",
            event_umo="default:FriendMessage:10001",
            now=2100,
        )

        self.assertIn("没有保存可核验", context)
        self.assertIn("不要猜", context)

        section = self.harness._format_recent_group_share_snapshot_for_reply(
            user,
            "具体是谁？",
            event_umo="default:FriendMessage:10001",
            now=2100,
            as_section=True,
        )
        self.assertEqual("群聊主动消息追问的事实边界", section["title"])
        self.assertNotIn("【群聊主动消息追问的事实边界】", section["content"])

    def test_source_snapshot_is_not_injected_into_another_session(self) -> None:
        user = {
            "last_proactive_reason": "group_share",
            "last_proactive_sent_at": 2000,
            "last_proactive_delivery_umo": "default:FriendMessage:10001",
        }
        self.harness._remember_recent_group_share_snapshot(
            user,
            share_context={
                "group_id": "300000101",
                "speaker": "群友甲",
                "text": "测试原文",
                "addressed_to_bot": True,
            },
            shared_text="群里有人找我。",
            sent_at=2000,
            delivery_umo="default:FriendMessage:10001",
        )

        context = self.harness._format_recent_group_share_snapshot_for_reply(
            user,
            "具体是谁？",
            event_umo="default:FriendMessage:20002",
            now=2100,
        )

        self.assertEqual("", context)


if __name__ == "__main__":
    unittest.main()
