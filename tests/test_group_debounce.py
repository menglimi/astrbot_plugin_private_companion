"""Native group text windows keep speaker attribution and bounded timing."""

import asyncio
from collections import deque
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from astrbot_host.group_debounce import GroupDebounce, group_plain_text, replace_group_plain_text
from companion_v2.conversation.group_admission import GroupReplyAdmission
from companion_v2.conversation.native_participation import native_participation_turn
from companion_v2.conversation.private_debounce import Policy
from companion_v2.execution.models import stamp


def group_event(text, message_id="1", *, sender="200", kind="Plain"):
    part = NS(type=kind, text=text)
    message = NS(type=NS(value="GroupMessage"), message=[part], message_id=message_id,
                 sender=NS(user_id=sender), message_str=text)
    return NS(message_obj=message, message_str=text, created_at=1_700_000_000)


class GroupDebounceTests(unittest.IsolatedAsyncioTestCase):
    def manager(self):
        return GroupDebounce(policy=Policy(min_seconds=0.03, max_seconds=0.05,
                                          total_seconds=0.12))

    async def test_same_speaker_fragments_use_latest_native_event(self):
        manager = self.manager()
        first, second = group_event("小星", "1"), group_event("帮我看看这个方案？", "2")
        a = manager.reserve_group("group-a", "200", first, group_plain_text(first))
        b = manager.reserve_group("group-a", "200", second, group_plain_text(second))
        self.assertEqual(await manager.wait_for_quiet(a, first), "superseded")
        self.assertEqual(await manager.wait_for_quiet(b, second), "ready")
        entries = await manager.finish(b, second)
        self.assertEqual([item[1] for item in entries], ["小星", "帮我看看这个方案？"])
        self.assertTrue(replace_group_plain_text(second, manager.combined(b, entries)))
        self.assertEqual(second.message_str, "小星\n帮我看看这个方案？")
        self.assertEqual(second.message_obj.message_id, "2")
        self.assertEqual(first.message_str, "小星")
        await manager.close()

    async def test_other_speaker_ends_window_without_merging(self):
        manager = self.manager()
        first, other, later = (group_event("小星", "1"), group_event("等等", "2", sender="201"),
                               group_event("再说一句", "3"))
        a = manager.reserve_group("group-a", "200", first, group_plain_text(first))
        b = manager.reserve_group("group-a", "201", other, group_plain_text(other))
        self.assertIsNone(manager.reserve_group("group-a", "200", later, group_plain_text(later)))
        self.assertEqual(await manager.wait_for_quiet(a, first), "ready")
        self.assertEqual([item[1] for item in await manager.finish(a, first)], ["小星"])
        self.assertEqual(await manager.wait_for_quiet(b, other), "ready")
        self.assertEqual([item[1] for item in await manager.finish(b, other)], ["等等"])
        await manager.close()

    async def test_non_text_event_flushes_prior_window(self):
        manager = self.manager()
        first = group_event("小星")
        a = manager.reserve_group("group-a", "200", first, group_plain_text(first))
        manager.observe("group-a", "200", eligible=False)
        self.assertEqual(await manager.wait_for_quiet(a, first), "ready")
        self.assertIsNotNone(await manager.finish(a, first))
        self.assertIsNone(group_plain_text(group_event("@小星", kind="At")))
        self.assertIsNone(group_plain_text(group_event("[图片]", kind="Image")))
        folded_mention = group_event("小星")
        folded_mention.message_obj.raw_message = {"message": [
            {"type": "at", "data": {"qq": "100"}},
            {"type": "text", "data": {"text": "小星"}}]}
        self.assertIsNone(group_plain_text(folded_mention))
        native_wake = group_event("小星")
        native_wake.is_at_or_wake_command = True
        self.assertIsNone(group_plain_text(native_wake))
        await manager.close()

    async def test_window_has_a_total_deadline_during_continuous_input(self):
        now = [0.0]
        manager = GroupDebounce(policy=Policy(min_seconds=0.03, max_seconds=0.05,
                                              total_seconds=0.12), clock=lambda: now[0])
        first = group_event("开始")
        reservation = manager.reserve_group("group-a", "200", first, group_plain_text(first))
        now[0] = 0.04
        latest = group_event("继续", "2")
        current = manager.reserve_group("group-a", "200", latest, group_plain_text(latest))
        now[0] = 0.09
        third = group_event("还没完", "3")
        current = manager.reserve_group("group-a", "200", third, group_plain_text(third))
        now[0] = 0.12
        self.assertEqual(await manager.wait_for_quiet(reservation, first), "superseded")
        self.assertEqual(await manager.wait_for_quiet(current, third), "ready")
        self.assertEqual(len(await manager.finish(current, third)), 3)
        await manager.close()

    async def test_oversized_burst_flushes_without_truncating_prior_text(self):
        manager = self.manager()
        first, second = group_event("甲" * 5000), group_event("乙" * 4000, "2")
        a = manager.reserve_group("group-a", "200", first, group_plain_text(first))
        self.assertIsNone(manager.reserve_group("group-a", "200", second, group_plain_text(second)))
        self.assertEqual(await manager.wait_for_quiet(a, first), "ready")
        self.assertEqual([item[1] for item in await manager.finish(a, first)], ["甲" * 5000])
        await manager.close()


class GroupCandidateDebounceTests(unittest.TestCase):
    def test_merged_fragments_do_not_count_twice_as_repetition(self):
        admission = GroupReplyAdmission.__new__(GroupReplyAdmission)
        admission.messages = NS(group_context=NS(authorize=Mock()), tasks=NS(now=lambda: None))
        admission._key = lambda _context: "group-a"
        at = datetime(2026, 11, 7, tzinfo=timezone.utc)
        admission.observations = {"group-a": deque((
            {"kind": "member", "speaker_id": "200", "event_id": "first",
             "received_at": stamp(at - timedelta(seconds=1)), "text": "哈哈"},
        ))}
        context = NS(actor=NS(principal=NS(native_account_id="200")))
        participation = {"enable_group_repeat_follow": True,
                         "group_repeat_window_seconds": 30,
                         "group_repeat_count_distinct_users_only": False,
                         "group_repeat_trigger_threshold": 2,
                         "enable_group_interjection": False}
        continuation = {"limit_reached": False, "candidate_reason": ""}
        original = admission.candidate(context, text="哈哈", instruction="", at=at,
            settings={}, participation=participation, event_id="latest", continuation=continuation)
        merged = admission.candidate(context, text="哈哈", instruction="", at=at,
            settings={}, participation=participation, event_id="latest", continuation=continuation,
            merged_event_ids=("first",))
        self.assertEqual(original, "repeat")
        self.assertNotEqual(merged, "repeat")

    def test_merged_current_turn_does_not_repeat_fragments_in_prompt_history(self):
        at = datetime(2026, 11, 7, tzinfo=timezone.utc)
        earlier = {"kind": "member", "speaker_id": "201", "event_id": "other",
                   "received_at": stamp(at - timedelta(seconds=4)), "text": "之前的话"}
        fragment = {"kind": "member", "speaker_id": "200", "event_id": "first",
                    "received_at": stamp(at - timedelta(seconds=1)), "text": "小星"}
        latest = {"kind": "member", "speaker_id": "200", "event_id": "latest",
                  "received_at": stamp(at), "text": "帮我看看"}
        participation = NS(
            runtime=object(), records=object(),
            admission=NS(native_participation_history=Mock(return_value=(
                (earlier, fragment, latest), ()))),
            messages=NS(timezone_name="Asia/Shanghai"),
            behavior=NS(instruction=Mock(return_value="")),
            _interjection_policy=Mock(return_value=NS(address_words=())))
        context = NS(actor=NS(principal=NS(native_account_id="200")))
        native_input = NS(at=at, instruction="", text="小星\n帮我看看",
                          event_id="latest", merged_event_ids=("first",), images=())
        patches = (
            patch("companion_v2.conversation.native_participation.group_participation_settings",
                  return_value={"group_context_max_age_hours": 12, "enable_group_repeat_follow": False}),
            patch("companion_v2.conversation.native_participation.owner_key", return_value="group-a"),
            patch("companion_v2.conversation.native_participation.group_hints", return_value=""),
            patch("companion_v2.conversation.native_participation.observe",
                  return_value={"facts": {"mood": {}}}),
            patch("companion_v2.conversation.native_participation.observation_instruction", return_value=""),
            patch("companion_v2.conversation.native_participation._speech_record", return_value={}),
            patch("companion_v2.conversation.native_participation.interjection_state",
                  return_value={"enabled": False, "initiative": False}),
            patch("companion_v2.conversation.native_participation.interjection_instruction", return_value=""))
        entered = [item.__enter__() for item in patches]
        try:
            turn = native_participation_turn(participation, context, native_input, {})
            observed = entered[3]
            self.assertEqual([item["event_id"] for item in observed.call_args.kwargs["items"]],
                             ["other", "latest"])
            self.assertIn("[speaker_id=201] 之前的话", turn["instruction"])
            self.assertNotIn("[speaker_id=200] 小星", turn["instruction"])
            self.assertIn("这些片段是一次表达", turn["instruction"])
        finally:
            for item in reversed(patches):
                item.__exit__(None, None, None)
