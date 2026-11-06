import unittest
from datetime import datetime, timedelta, timezone

from companion_v2.conversation.group_topic_gate import evaluate, score


NOW = datetime(2026, 11, 6, 20, 0, tzinfo=timezone.utc)


def state(replies=(), topics=None):
    return {"replies": [when.isoformat() for when in replies], "topics": dict(topics or {})}


class TopicScoreTests(unittest.TestCase):
    def test_explicit_short_question_scores_higher_than_long_context(self):
        self.assertGreater(score("mention", "这是什么？"), score("context_word", "很长的一段背景说明，需要多轮确认才能回答清楚。" * 5))


class TopicGateTests(unittest.TestCase):
    def test_disabled_gate_never_blocks(self):
        allowed, _, detail = evaluate(state(), event_id="e", reason="mention", text="在吗？",
                                      at=NOW, enabled=False, window_seconds=60, max_replies=3)
        self.assertTrue(allowed)
        self.assertEqual(detail["gate"], "disabled")

    def test_reply_budget_exhausted_skips_new_topic(self):
        recent = [NOW - timedelta(seconds=10), NOW - timedelta(seconds=5), NOW - timedelta(seconds=1)]
        allowed, new_state, detail = evaluate(state(recent), event_id="e", reason="mention",
                                              text="在吗？", at=NOW, enabled=True, window_seconds=60, max_replies=3)
        self.assertFalse(allowed)
        self.assertEqual(detail["decision"], "budget")
        self.assertIn("e", new_state["topics"])

    def test_short_addressed_topic_is_answered_and_consumes_budget(self):
        allowed, new_state, detail = evaluate(state(), event_id="e", reason="mention",
                                              text="在吗？", at=NOW, enabled=True, window_seconds=60, max_replies=3)
        self.assertTrue(allowed)
        self.assertEqual(detail["decision"], "answer")
        self.assertNotIn("e", new_state["topics"])
        self.assertEqual(len(new_state["replies"]), 1)

    def test_hard_topic_is_deferred_when_another_topic_waits(self):
        waiting = {"old": {"created": (NOW - timedelta(seconds=5)).isoformat(), "skipped": 0}}
        long_text = "这是一段需要查资料、确认多个前提才可能回答清楚的长问题。" * 4
        allowed, new_state, detail = evaluate(state(topics=waiting), event_id="e",
                                              reason="context_word", text=long_text,
                                              at=NOW, enabled=True, window_seconds=60, max_replies=3)
        self.assertFalse(allowed)
        self.assertEqual(detail["decision"], "hard_topic")
        self.assertEqual(new_state["topics"]["e"]["skipped"], 1)

    def test_expired_topics_and_replies_are_dropped(self):
        old = (NOW - timedelta(seconds=200)).isoformat()
        stale = {"old": {"created": old, "skipped": 0}}
        allowed, new_state, _ = evaluate(state(replies=(NOW - timedelta(seconds=200),), topics=stale),
                                         event_id="e", reason="mention", text="在吗？", at=NOW, enabled=True,
                                         window_seconds=60, max_replies=3, ttl_seconds=90)
        self.assertTrue(allowed)
        self.assertNotIn("old", new_state["topics"])

    def test_easier_pending_topic_wins_when_current_is_harder(self):
        pending = {"easier": {"created": NOW.isoformat(), "skipped": 0, "score": 9}}
        allowed, new_state, detail = evaluate(state(topics=pending), event_id="e",
                                              reason="context_word", text="这个怎么弄？",
                                              at=NOW, enabled=True, window_seconds=60, max_replies=3)
        self.assertFalse(allowed)
        self.assertEqual(detail["decision"], "easy_first")
        self.assertEqual(new_state["topics"]["e"]["skipped"], 1)

    def test_starvation_forces_a_repeatedly_skipped_topic(self):
        queued = {"e": {"created": NOW.isoformat(), "skipped": 3, "score": -1}}
        allowed, new_state, detail = evaluate(state(topics=queued), event_id="e",
                                              reason="context_word", text="很长的问题" * 10,
                                              at=NOW, enabled=True, window_seconds=60, max_replies=3)
        self.assertTrue(allowed)
        self.assertEqual(detail["decision"], "starvation")
        self.assertNotIn("e", new_state["topics"])

    def test_same_sender_follow_up_lowers_the_score(self):
        previous = {"at": (NOW - timedelta(seconds=10)).isoformat(), "reason": "mention", "sender": "u1"}
        current = state()
        current["answered"] = [previous]
        allowed, new_state, detail = evaluate(current, event_id="e", reason="mention", text="在吗？",
                                              at=NOW, enabled=True, window_seconds=60, max_replies=3,
                                              sender="u1")
        self.assertTrue(allowed)
        self.assertEqual(detail["feedback"], -2)
        self.assertEqual(detail["score"], 5)
        self.assertEqual(new_state["answered"][-1]["sender"], "u1")


if __name__ == "__main__":
    unittest.main()
