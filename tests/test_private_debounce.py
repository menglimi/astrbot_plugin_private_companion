"""Adaptive private debounce policy and per-target state machine."""

import asyncio
import unittest
from unittest.mock import patch

from astrbot_host.private_debounce import PrivateDebounce, PrivateDeliveryDedup
from companion_v2.configuration.models import ConfigurationError
from companion_v2.conversation.private_debounce import (
    Policy,
    merge_texts,
    quiet_seconds,
    visible_characters,
)
from companion_v2.conversation.private_reply_context import merged_input_sequence
from companion_v2.conversation.reply_settings import private_debounce_settings


class PrivateDebounceConfigurationTests(unittest.TestCase):
    def test_defaults_and_custom_values_are_effective(self):
        default = private_debounce_settings({})
        self.assertTrue(default.enabled)
        self.assertEqual((default.min_seconds, default.max_seconds, default.total_seconds),
                         (1.5, 4.0, 8.0))
        custom = private_debounce_settings({
            "private_debounce_min_ms": 2000, "private_debounce_max_ms": 3500,
            "private_debounce_total_ms": 7000})
        self.assertEqual((custom.min_seconds, custom.max_seconds, custom.total_seconds),
                         (2.0, 3.5, 7.0))
        self.assertFalse(private_debounce_settings({"private_debounce_enabled": False}).enabled)

    def test_out_of_range_or_inverted_values_are_rejected(self):
        for values in (
            {"private_debounce_min_ms": 1},
            {"private_debounce_max_ms": 120001},
            {"private_debounce_total_ms": 120001},
            {"private_debounce_min_ms": 4000, "private_debounce_max_ms": 2000},
            {"private_debounce_max_ms": 4000, "private_debounce_total_ms": 3000},
            {"private_debounce_enabled": 1},
        ):
            with self.subTest(values=values), self.assertRaises(ConfigurationError):
                private_debounce_settings(values)


class PrivateDebouncePolicyTests(unittest.TestCase):
    def test_short_messages_wait_longer_than_long_messages(self):
        policy = Policy()
        short = quiet_seconds("在吗", policy)
        medium = quiet_seconds("我想问你个事", policy)
        long = quiet_seconds("这是已经说完的一整段话，包含足够多的上下文和细节。" * 3, policy)
        self.assertGreater(short, medium)
        self.assertGreater(medium, long)
        self.assertGreaterEqual(long, policy.min_seconds)
        self.assertLessEqual(short, policy.max_seconds)

    def test_terminal_punctuation_and_ellipsis_move_the_window(self):
        policy = Policy()
        complete = quiet_seconds("我现在先去睡一会儿。", policy)
        open_ended = quiet_seconds("我现在先去睡一会儿……", policy)
        no_end = quiet_seconds("我现在先去睡一会儿", policy)
        self.assertLess(complete, no_end)
        self.assertGreater(open_ended, no_end)

    def test_window_is_clamped_and_disabled_returns_zero(self):
        policy = Policy(min_seconds=1.5, max_seconds=4.0, total_seconds=8.0)
        for text in ("在", "……", "🥺", "这是一段非常长的消息" * 30, ""):
            with self.subTest(text=text[:20]):
                value = quiet_seconds(text, policy)
                self.assertGreaterEqual(value, 1.5)
                self.assertLessEqual(value, 4.0)
        self.assertEqual(quiet_seconds("晚安", Policy(enabled=False)), 0)
        self.assertEqual(visible_characters("  🥺\n"), 1)
        self.assertEqual(merge_texts(["在吗", "", "我想问你个事"]), "在吗\n我想问你个事")

    def test_invalid_policy_is_rejected(self):
        for values in (
            {"enabled": 1},
            {"min_seconds": 0.001},
            {"min_seconds": 4.0, "max_seconds": 1.5},
            {"max_seconds": 4.0, "total_seconds": 3.0},
        ):
            with self.subTest(values=values), self.assertRaises(ValueError):
                Policy(**values)


class PrivateDebounceStateTests(unittest.IsolatedAsyncioTestCase):
    def make_manager(self):
        return PrivateDebounce(Policy(enabled=True, min_seconds=0.05,
                                      max_seconds=0.08, total_seconds=0.2))

    async def test_later_texts_join_one_pending_turn(self):
        manager = self.make_manager()
        owner = object()
        first = manager.reserve("target", owner, "在吗")
        self.assertIsNotNone(first)
        middle, latest = object(), object()
        self.assertIs(manager.reserve("target", middle, "我想问你个事").pending, first.pending)
        latest_reservation = manager.reserve("target", latest, "就是那个")
        self.assertIs(latest_reservation.pending, first.pending)
        await manager.wait_for_quiet(latest_reservation, latest)
        entries = await manager.finish(latest_reservation, latest)
        self.assertEqual([entry[1] for entry in entries], ["在吗", "我想问你个事", "就是那个"])
        self.assertEqual(manager.combined(latest_reservation, entries), "在吗\n我想问你个事\n就是那个")
        self.assertIsNotNone(manager.reserve("target", object(), "新的一轮"))

    async def test_stale_owner_cannot_abort_the_latest_event(self):
        manager = self.make_manager()
        first, latest = object(), object()
        first_reservation = manager.reserve("target", first, "在吗")
        latest_reservation = manager.reserve("target", latest, "我想问你个事")

        self.assertFalse(await manager.abort(first_reservation, event=first))
        self.assertEqual(await manager.wait_for_quiet(first_reservation, first), "superseded")
        entries = await manager.finish(latest_reservation, latest)
        self.assertEqual([entry[1] for entry in entries], ["在吗", "我想问你个事"])

    async def test_reused_event_object_still_has_distinct_latest_ownership(self):
        manager = self.make_manager()
        event = type("Event", (), {})()
        event.created_at = 1_700_000_000
        first = manager.reserve("target", event, "在吗")
        event.created_at = 1_700_000_001
        latest = manager.reserve("target", event, "我想问你个事")

        self.assertIs(first.pending, latest.pending)
        self.assertNotEqual(first.ticket, latest.ticket)
        self.assertTrue(manager.superseded_by_same_event(first, event))
        self.assertFalse(manager.superseded_by_same_event(latest, event))
        self.assertEqual(await manager.wait_for_quiet(first, event), "superseded")
        self.assertFalse(await manager.abort(first, event=event))
        await manager.wait_for_quiet(latest, event)
        entries = await manager.finish(latest, event)
        self.assertEqual([text for _, text, _ in entries], ["在吗", "我想问你个事"])
        self.assertEqual([item["received_at"] for item in merged_input_sequence(entries)],
                         ["2023-11-14T22:13:20.000000+00:00", "2023-11-14T22:13:21.000000+00:00"])

    async def test_total_ceiling_stops_a_continuous_stream(self):
        # Windows timers can delay the feeder beyond the quiet window under
        # load. Drive arrivals with the injected monotonic clock so this tests
        # the burst ceiling rather than the machine's scheduling latency.
        now = [10.0]
        manager = PrivateDebounce(Policy(enabled=True, min_seconds=0.03,
                                         max_seconds=0.05, total_seconds=0.12),
                                  clock=lambda: now[0])
        latest_event = [object()]
        latest_reservation = [manager.reserve("target", latest_event[0], "在吗")]
        started = now[0]
        timeouts = []

        async def receive_next_message(awaitable, *, timeout):
            awaitable.close()
            timeouts.append(timeout)
            if timeout > 0.02:
                now[0] += 0.02
                latest_event[0] = object()
                latest_reservation[0] = manager.reserve("target", latest_event[0], "嗯")
                return True
            now[0] += timeout
            raise asyncio.TimeoutError

        async def wait_for_latest_owner():
            while True:
                status = await manager.wait_for_quiet(latest_reservation[0], latest_event[0])
                if status != "superseded":
                    return status

        with patch("astrbot_host.private_debounce.asyncio.wait_for", receive_next_message):
            self.assertEqual(await wait_for_latest_owner(), "ready")
        entries = await manager.finish(latest_reservation[0], latest_event[0])
        elapsed = now[0] - started
        self.assertGreater(len(entries), 1)
        self.assertAlmostEqual(elapsed, 0.12)
        self.assertLess(timeouts[-1], timeouts[0])

    async def test_logs_wake_followup_and_quiet_end_with_full_text(self):
        manager = self.make_manager()
        owner = object()
        with self.assertLogs("companion_v2.conversation.debounce_log", level="INFO") as captured:
            first = manager.reserve("target", owner, "在吗？")
            await manager.configure(first, owner, manager.policy)
            follow = manager.reserve("target", object(), "我想问你个事")
            self.assertIs(follow.pending, first.pending)
            await manager.wait_for_quiet(follow, follow.pending.owner)
            entries = await manager.finish(follow, follow.pending.owner)
        output = "\n".join(captured.output)
        self.assertIn("收到唤醒消息", output)
        self.assertIn("reason=私聊消息", output)
        self.assertIn("message=在吗？", output)
        self.assertIn("防抖期间收到后续消息", output)
        self.assertIn("message=我想问你个事", output)
        self.assertIn("防抖结束", output)
        self.assertIn("收到 1 条后续消息，开始正常回复", output)
        self.assertEqual(len(entries), 2)

    async def test_logs_quiet_end_without_a_followup(self):
        manager = self.make_manager()
        owner = object()
        with self.assertLogs("companion_v2.conversation.debounce_log", level="INFO") as captured:
            first = manager.reserve("target", owner, "直接说完")
            await manager.configure(first, owner, manager.policy)
            await manager.wait_for_quiet(first, owner)
            await manager.finish(first, owner)
        output = "\n".join(captured.output)
        self.assertIn("未接收到后续消息，开始正常回复", output)
        self.assertNotIn("防抖期间收到后续消息", output)

    async def test_close_wakes_waiters_and_prevents_new_owners(self):
        manager = self.make_manager()
        owner = object()
        reservation = manager.reserve("target", owner, "在吗")
        waiter = asyncio.create_task(manager.wait_for_quiet(reservation, owner))
        await manager.close()
        self.assertEqual(await waiter, "closed")
        self.assertTrue(manager.closed)
        self.assertIsNone(manager.reserve("target", object(), "在吗"))


class PrivateDeliveryDedupTests(unittest.TestCase):
    def test_same_key_is_duplicate_within_the_window_and_expires(self):
        now = [0.0]
        dedup = PrivateDeliveryDedup(ttl_seconds=120, clock=lambda: now[0])
        key = ("target", "1555918896")

        self.assertFalse(dedup.duplicate(key))
        now[0] = 0.65
        self.assertTrue(dedup.duplicate(key))
        now[0] = 120.0
        self.assertFalse(dedup.duplicate(key))

    def test_targets_and_message_ids_are_independent(self):
        dedup = PrivateDeliveryDedup(clock=lambda: 0.0)

        self.assertFalse(dedup.duplicate(("a", "1")))
        self.assertFalse(dedup.duplicate(("b", "1")))
        self.assertFalse(dedup.duplicate(("a", "2")))
        self.assertTrue(dedup.duplicate(("a", "1")))

    def test_limit_evicts_the_oldest_key(self):
        now = [0.0]
        dedup = PrivateDeliveryDedup(ttl_seconds=600, limit=2, clock=lambda: now[0])

        self.assertFalse(dedup.duplicate(("a", "1")))
        now[0] = 1.0
        self.assertFalse(dedup.duplicate(("a", "2")))
        now[0] = 2.0
        self.assertFalse(dedup.duplicate(("a", "3")))
        self.assertFalse(dedup.duplicate(("a", "1")))

    def test_invalid_arguments_are_rejected(self):
        for ttl, limit in ((0, 10), (-1, 10), (10, 0), (10, -1)):
            with self.subTest(ttl=ttl, limit=limit), self.assertRaises(ValueError):
                PrivateDeliveryDedup(ttl_seconds=ttl, limit=limit)


if __name__ == "__main__":
    unittest.main()
