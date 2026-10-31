"""Participation hints use admitted history, original input time and reply rounds."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
import unittest

from companion_v2.conversation.group_timing import short_input_wait
from companion_v2.conversation.participation_prompts import group_hints
from companion_v2.conversation.native_participation import speaker_history_instruction
from companion_v2.foundation.refs import ScopeKind


NOW = datetime(2026, 9, 27, 10, tzinfo=timezone.utc)


class GroupParticipationHintTests(unittest.TestCase):
    def hints(self, recent=(), batches=None, settings=None, materials=(), sender="200", images=(), native=False,
              current_text=""):
        request = NS(event=NS(received_at=NOW), context=NS(materials=materials), images=images)
        context = NS(actor=NS(principal=NS(native_account_id=sender)))
        return group_hints(request, context, settings or {}, recent, batches or {}, native=native,
                           current_text=current_text)

    def test_mention_attribution_never_claims_another_accounts_name(self):
        other = self.hints(current_text="[提及账号 123] 小天使")
        self.assertIn("其他账号", other)
        self.assertIn("不要接成", other)
        own = self.hints(current_text="[提及机器人] 小天使")
        self.assertIn("明确 @ 了本角色", own)
        speaking_about = self.hints(current_text="给星缘搓了一个家")
        self.assertIn("没有明确 @ 本角色", speaking_about)
        self.assertIn("第三人称", speaking_about)

    def test_recent_group_history_keeps_each_speaker_out_of_the_current_turn(self):
        history = speaker_history_instruction((
            {"speaker_id": "A", "text": "@星缘 小天使"},
            {"speaker_id": "B", "text": "什么"},
        ), "B")
        self.assertIn("[speaker_id=A] @星缘 小天使", history)
        self.assertIn("[speaker_id=B] 什么", history)
        self.assertIn("不同的人", history)
        self.assertIn("current_group_speaker=B", history)
        self.assertEqual(speaker_history_instruction((), "B"), "")

    def test_segmented_reply_is_one_round_and_future_receipts_do_not_count(self):
        recent = [dict(type="confirmed_group_reply", part_id=part, delivered_at=at.isoformat())
                  for part, at in (("a", NOW - timedelta(seconds=2)),
                                   ("b", NOW - timedelta(seconds=1)), ("future", NOW + timedelta(seconds=1)))]
        hint = self.hints(recent, {"a": "round-1", "b": "round-1", "future": "round-2"})
        self.assertIn("有 1 轮已确认 Bot 回复", hint)
        self.assertIn("疲劳参考 1.0 点", hint)
        self.assertIn("距当前输入约 1 秒", hint)

    def test_fatigue_recovers_against_original_input_not_recovery_clock(self):
        recent = [dict(type="confirmed_group_reply", part_id=part, delivered_at=at.isoformat())
                  for part, at in (("a", NOW - timedelta(minutes=91)), ("b", NOW - timedelta(minutes=90)))]
        hint = self.hints(recent, {"a": "round-1", "b": "round-2"})
        self.assertIn("疲劳参考 0.99 点", hint)
        self.assertIn("冷却参考 90 秒", hint)
        self.assertEqual(hint, self.hints(recent, {"a": "round-1", "b": "round-2"}))

    def test_merged_fragments_reduce_interest_once_without_inventing_cold_history(self):
        hint = self.hints(settings={"group_wakeup_cold_group_enabled": True},
                          materials=(NS(purpose="merged_input:2"), NS(purpose="merged_input:3")))
        self.assertIn("已合并当前成员 3 条输入", hint)
        self.assertIn("相对降低 65%", hint)
        self.assertIn("没有可用于估计间隔的此前群历史", hint)
        self.assertNotIn("疲劳参考", hint)

    def test_primary_words_require_authenticated_speaker_and_name_switch_is_independent(self):
        settings = {"group_wakeup_primary_member_ids": '["201"]',
                    "group_wakeup_owner_direct_words": '["专属称呼"]',
                    "group_wakeup_bot_name": "小星", "group_wakeup_bot_name_enabled": False}
        self.assertNotIn("专属称呼", self.hints(settings=settings))
        self.assertNotIn("熟稔与默契", self.hints(settings=settings))
        self.assertIn("专属称呼", self.hints(settings=settings, sender="201"))
        self.assertIn("熟稔与默契", self.hints(settings=settings, sender="201"))
        self.assertNotIn("小星", self.hints(settings=settings, sender="201"))
        self.assertNotIn("群唤醒参考", self.hints(settings={"group_wakeup_enabled": False}))

    def test_group_image_is_only_a_prompt_hint_for_current_authorized_images(self):
        settings = {"group_wakeup_image_enabled": True, "group_wakeup_image_weight": 0.35,
                    "group_wakeup_bot_name": "小星", "group_wakeup_direct_words": '["星星"]',
                    "group_wakeup_primary_member_ids": '["200"]',
                    "group_wakeup_owner_direct_words": '["小星同学"]'}
        self.assertNotIn("图片中与你明确兴趣", self.hints(settings=settings))
        hint = self.hints(settings=settings, images=(object(),))
        self.assertIn("图片中与你明确兴趣", hint)
        self.assertIn("图片候选抽取概率 35%", hint)
        self.assertIn("不要求接话", hint)
        self.assertIn('["星星", "小星"]', hint)
        self.assertIn('["小星同学"]', hint)
        self.assertIn("表情包优先理解其情绪和表达", hint)
        self.assertNotIn("图片中与你明确兴趣", self.hints(settings=settings, native=True))

    def test_dense_group_replies_prompt_a_configured_cooldown_for_weak_candidates(self):
        recent = [dict(type="native_group_reply", turn_id=str(index),
                       returned_at=(NOW - timedelta(seconds=seconds)).isoformat())
                  for index, seconds in enumerate((30, 20, 10))]
        settings = {"group_air_guard_window_seconds": 60,
                    "group_air_guard_max_bot_replies": 3,
                    "group_air_guard_cooldown_seconds": 150}
        hint = self.hints(recent, settings=settings, native=True)
        self.assertIn("最近 60 秒内达到 3 轮 Bot 回复", hint)
        self.assertIn("距该轮密集接话结束约 140 秒仍在 150 秒收口期内", hint)
        self.assertIn("明确 @、清楚直呼角色", hint)
        self.assertIn("成员明确把实质问题交给你时仍结合语境回应", hint)

        expired = [dict(type="native_group_reply", turn_id=str(index),
                        returned_at=(NOW - timedelta(seconds=seconds)).isoformat())
                   for index, seconds in enumerate((180, 170, 160))]
        self.assertNotIn("群聊高频收口参考", self.hints(expired, settings=settings, native=True))

    def test_media_labels_and_thread_metadata_are_not_group_interest_evidence(self):
        hint = self.hints()
        self.assertIn("不是成员表达、兴趣词、情绪或复读内容", hint)
        self.assertIn("话题标题、线程签名等路由元数据也不是兴趣证据", hint)
        self.assertIn("图片只能在单独开启图片唤醒后作为额外线索", hint)

    def test_only_possible_plain_group_fragments_wait(self):
        event = NS(scope=NS(kind=ScopeKind.GROUP))
        for text in ("[提及机器人] 瓦", "好", "晚安", "你好", "抱抱", "谢谢"):
            with self.subTest(text=text):
                self.assertEqual(short_input_wait(event, text, {}), 15)
        for text in ("", "瓦？", "好！", "晚安。", "我要问你"):
            with self.subTest(text=text):
                self.assertEqual(short_input_wait(event, text, {}), 0)
        self.assertEqual(short_input_wait(event, "瓦", {"group_wakeup_enabled": False}), 0)
        self.assertEqual(short_input_wait(event, "瓦", {"group_wakeup_short_text_wait_ms": 0}), 0)
        for field in ("images", "quotes", "forwards", "audios", "videos"):
            self.assertEqual(short_input_wait(NS(scope=event.scope, **{field: (object(),)}), "瓦", {}), 0)
        self.assertEqual(short_input_wait(NS(scope=NS(kind=ScopeKind.PRIVATE)), "瓦", {}), 0)

    def test_native_consecutive_messages_and_high_pressure_bursts_inject_rhythm_guidance(self):
        streak_recent = [
            dict(type="group_member_message", speaker_id="200",
                 received_at=(NOW - timedelta(seconds=6)).isoformat(), text="小星"),
            dict(type="group_member_message", speaker_id="200",
                 received_at=(NOW - timedelta(seconds=2)).isoformat(), text="我有个问题想问你"),
        ]
        streak_hint = self.hints(streak_recent, native=True, sender="200")
        self.assertIn("连发防抖参考：当前成员在近 20 秒内已连续发出 3 条尚未被新回复隔开的消息", streak_hint)
        self.assertIn("相对降低 65%", streak_hint)
        self.assertIn("将这几条连续发言合并为一个整体一次性回应", streak_hint)

        replied_recent = [
            *streak_recent,
            dict(type="native_group_reply", turn_id="turn-1",
                 returned_at=(NOW - timedelta(seconds=1)).isoformat()),
        ]
        self.assertNotIn("连发防抖参考", self.hints(replied_recent, native=True, sender="200"))

        burst_recent = [
            dict(type="group_member_message", speaker_id="201",
                 received_at=(NOW - timedelta(seconds=8)).isoformat(), text="小星看看这个"),
            dict(type="group_member_message", speaker_id="202",
                 received_at=(NOW - timedelta(seconds=5)).isoformat(), text="我也想问"),
            dict(type="group_member_message", speaker_id="200",
                 received_at=(NOW - timedelta(seconds=2)).isoformat(), text="还有我这个"),
        ]
        burst_hint = self.hints(burst_recent, native=True, sender="200")
        self.assertIn("群聊高压分流参考", burst_hint)
        self.assertIn("（1）哪些要回", burst_hint)
        self.assertIn("（2）哪些不回", burst_hint)
        self.assertIn("（3）哪些一起回", burst_hint)


if __name__ == "__main__":
    unittest.main()
