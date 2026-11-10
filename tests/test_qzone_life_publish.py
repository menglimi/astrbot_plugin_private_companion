"""生活说说：判定、概率、操作键与草稿边界。"""

from datetime import datetime, timedelta, timezone
import unittest

from companion_v2.qzone import life_publish
from companion_v2.qzone.policy import QzoneRuntimeState
from companion_v2.qzone.topics import QzoneScene, QzoneTopic


NOW = datetime(2026, 11, 9, 4, 0, tzinfo=timezone.utc)  # 12:00 Asia/Shanghai
DAY = "2026-11-09"


def settings(**changes):
    values = {"enabled": True, "publish_enabled": True, "life_publish_enabled": True,
              "life_publish_probability": 0.5, "life_publish_min_interval_hours": 24,
              "life_publish_max_daily": 2, "life_publish_style_prompt": ""}
    values.update(changes)
    return values


def state(**changes):
    values = {"publish_day": "", "publish_count": 0, "last_action_at": {}}
    values.update(changes)
    return QzoneRuntimeState(**values)


class DecisionTests(unittest.TestCase):
    def test_master_and_life_switches_are_both_required(self):
        for changes, reason in (({"enabled": False}, "qzone_disabled"),
                                ({"publish_enabled": False}, "publish_disabled"),
                                ({"life_publish_enabled": False}, "life_publish_disabled")):
            self.assertEqual(life_publish.decide(settings(**changes), state(), now=NOW).reason, reason)

    def test_daily_cap_resets_on_a_new_day(self):
        reached = state(publish_day=DAY, publish_count=2)
        self.assertEqual(life_publish.decide(settings(), reached, now=NOW), life_publish.Decision(False, "daily_limit", DAY))
        next_day = life_publish.decide(settings(), reached, now=NOW + timedelta(days=1))
        self.assertTrue(next_day.due)

    def test_interval_counts_failed_attempts_not_only_published_posts(self):
        attempted = state(last_action_at={life_publish.ATTEMPT_KEY: "2026-11-09T02:00:00+00:00"})
        self.assertEqual(life_publish.decide(settings(), attempted, now=NOW).reason, "interval")
        published = state(last_action_at={"publish": "2026-11-07T02:00:00+00:00"})
        self.assertTrue(life_publish.decide(settings(), published, now=NOW).due)

    def test_due_when_every_gate_is_open(self):
        self.assertEqual(life_publish.decide(settings(), state(), now=NOW),
                         life_publish.Decision(True, "due", DAY))


class ProbabilityTests(unittest.TestCase):
    def test_zero_never_and_one_always(self):
        self.assertFalse(life_publish.should_attempt(settings(life_publish_probability=0), 0.0))
        self.assertTrue(life_publish.should_attempt(settings(life_publish_probability=1), 0.999))
        self.assertFalse(life_publish.should_attempt({}, 0.0))

    def test_draw_is_compared_against_the_probability(self):
        half = settings()
        self.assertTrue(life_publish.should_attempt(half, 0.49))
        self.assertFalse(life_publish.should_attempt(half, 0.5))


class KeyAndDraftTests(unittest.TestCase):
    def test_operation_key_is_stable_per_attempt_and_distinct_across_attempts(self):
        first = life_publish.operation_key("owner", DAY, NOW)
        self.assertEqual(first, life_publish.operation_key("owner", DAY, NOW))
        self.assertNotEqual(first, life_publish.operation_key("owner", DAY, NOW + timedelta(hours=1)))
        self.assertNotEqual(first, life_publish.operation_key("other", DAY, NOW))

    def test_draft_strips_fences_quotes_and_rejects_empty_or_oversized(self):
        self.assertEqual(life_publish.parse_draft('```\n“今天风很轻。”\n```'), "今天风很轻。")
        with self.assertRaises(ValueError):
            life_publish.parse_draft("   ")
        with self.assertRaises(ValueError):
            life_publish.parse_draft("字" * (life_publish.MAX_TEXT_CHARS + 1))

    def test_request_carries_profile_diary_and_style(self):
        request = life_publish.build_request(
            {"name": "星缘", "personality": "安静"}, "今天去看了海。",
            now=NOW, style="口语一点")
        self.assertIn("星缘", request["prompt"])
        self.assertIn("性格：安静", request["prompt"])
        self.assertIn("今天去看了海。", request["prompt"])
        self.assertIn("口语一点", request["prompt"])
        self.assertEqual((life_publish.TOPIC, life_publish.SCENE),
                         (QzoneTopic.LIFE_DOCUMENTARY, QzoneScene.ROLE_ACTIVITY))


if __name__ == "__main__":
    unittest.main()
