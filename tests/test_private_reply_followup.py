"""Post-debounce private follow-ups join the first pending provider request."""

from datetime import datetime, timezone, timedelta
import unittest

from astrbot_host.private_reply_followup import PrivateReplyFollowUps


def event(text, seconds=0, origin="private:session-1"):
    event = type("Event", (), {})()
    event.message_str = text
    event.unified_msg_origin = origin
    event.created_at = datetime(2026, 10, 28, 6, 40, seconds, tzinfo=timezone.utc)
    return event


class PrivateReplyFollowUpTests(unittest.TestCase):
    def test_follow_up_before_first_request_is_restored_once(self):
        now = [10.0]
        manager = PrivateReplyFollowUps(clock=lambda: now[0])
        first, second = event("早上吃了什么"), event("中午又吃了什么", 1)
        active = manager.start("target", first, "event-1", ((first, "早上吃了什么", first.created_at),))
        self.assertIsNotNone(active)
        self.assertIs(manager.fold("target", second, "中午又吃了什么"), active)
        pending, entries = manager.for_request(first)
        self.assertEqual([item[1] for item in pending], ["中午又吃了什么"])
        self.assertEqual([item[1] for item in entries], ["早上吃了什么", "中午又吃了什么"])
        self.assertEqual(manager.for_request(first), ((), entries))
        self.assertIsNone(manager.fold("target", event("再补一句", 2), "再补一句"))
        self.assertTrue(manager.finish(first))
        self.assertIsNone(manager.active_for("target"))

    def test_same_event_and_stale_turns_cannot_extend_the_window(self):
        now = [10.0]
        manager = PrivateReplyFollowUps(clock=lambda: now[0], idle_seconds=5)
        first = event("在吗")
        manager.start("target", first, "event-1", ((first, "在吗", first.created_at),))
        self.assertIsNone(manager.fold("target", first, "在吗"))
        now[0] = 16.0
        self.assertIsNone(manager.fold("target", event("晚了", 2), "晚了"))
        self.assertIsNone(manager.active_for("target"))

    def test_finish_clears_the_session_when_lifecycle_uses_a_new_wrapper(self):
        manager = PrivateReplyFollowUps()
        first = event("第一轮")
        manager.start("target", first, "event-1", ((first, "第一轮", first.created_at),))
        wrapper = event("", 1)
        self.assertTrue(manager.finish(wrapper))
        self.assertIsNone(manager.active_for("target"))
        self.assertIsNone(manager.fold("target", event("新消息", 2), "新消息"))

    def test_request_without_follow_up_still_closes_the_window(self):
        manager = PrivateReplyFollowUps()
        first = event("直接说完")
        manager.start("target", first, "event-1", ((first, "直接说完", first.created_at),))
        self.assertEqual(manager.for_request(first), ((), ((first, "直接说完", first.created_at),)))
        self.assertIsNone(manager.fold("target", event("补充", 1), "补充"))

    def test_window_releases_when_the_first_request_never_arrives(self):
        now = [10.0]
        manager = PrivateReplyFollowUps(clock=lambda: now[0], request_deadline_seconds=5)
        first = event("只发了一个表情")
        self.assertIsNotNone(manager.start(
            "target", first, "event-1", ((first, "只发了一个表情", first.created_at),)))
        now[0] = 16.0
        self.assertIsNone(manager.fold("target", event("后来的正事", 2), "后来的正事"))
        self.assertIsNone(manager.active_for("target"))
        self.assertIsNone(manager.for_request(first))

    def test_entry_folded_before_the_deadline_still_merges_when_the_request_is_late(self):
        now = [10.0]
        manager = PrivateReplyFollowUps(clock=lambda: now[0], request_deadline_seconds=5)
        first = event("在吗")
        manager.start("target", first, "event-1", ((first, "在吗", first.created_at),))
        second = event("再补一句", 1)
        self.assertIsNotNone(manager.fold("target", second, "再补一句"))
        now[0] = 30.0
        pending, entries = manager.for_request(first)
        self.assertEqual([item[1] for item in pending], ["再补一句"])
        self.assertEqual([item[1] for item in entries], ["在吗", "再补一句"])

    def test_expired_window_stops_merging_but_keeps_folded_entries(self):
        now = [10.0]
        manager = PrivateReplyFollowUps(clock=lambda: now[0], request_deadline_seconds=5)
        first = event("在吗")
        manager.start("target", first, "event-1", ((first, "在吗", first.created_at),))
        self.assertIsNotNone(manager.fold("target", event("补充一", 1), "补充一"))
        now[0] = 20.0
        self.assertIsNone(manager.fold("target", event("补充二", 2), "补充二"))
        pending, entries = manager.for_request(first)
        self.assertEqual([item[1] for item in pending], ["补充一"])
        self.assertEqual([item[1] for item in entries], ["在吗", "补充一"])


if __name__ == "__main__":
    unittest.main()
