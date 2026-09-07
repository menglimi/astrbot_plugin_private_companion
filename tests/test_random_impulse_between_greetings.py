from __future__ import annotations

import unittest

from astrbot_plugin_private_companion.proactive import ProactiveMixin
from astrbot_plugin_private_companion.proactive_engine import ProactiveEngineMixin


class _TopicHarness(ProactiveEngineMixin):
    @staticmethod
    def _proactive_current_agenda_item():
        return None

    @staticmethod
    def _format_plan_item_for_prompt(_item) -> str:
        return "（暂无）"

    @staticmethod
    def _soften_topic_hook(text: str) -> str:
        return text

    @staticmethod
    def _normalize_internal_motive_text(text: str) -> str:
        return text


class RandomImpulseBetweenGreetingsTests(unittest.TestCase):
    def test_far_future_greeting_does_not_block_random_draw(self) -> None:
        now = 1_000_000.0
        evening = [{"state": "queued", "reason": "evening_greeting", "window_start_at": now + 8 * 3600}]
        soon = [{"state": "queued", "reason": "check_in", "window_start_at": now + 1 * 3600}]
        self.assertTrue(ProactiveMixin._random_impulse_slot_open(evening, now=now, delay_hours=(1.0, 3.0)))
        self.assertFalse(ProactiveMixin._random_impulse_slot_open(soon, now=now, delay_hours=(1.0, 3.0)))
        self.assertTrue(ProactiveMixin._random_impulse_slot_open([], now=now, delay_hours=None))

    def test_agenda_placeholder_is_not_a_photo_topic(self) -> None:
        patch = _TopicHarness()._photo_text_plan_field_patch(reason="background_schedule")
        self.assertEqual(patch["topic"], "手边这一小段")
        self.assertNotIn("暂无", patch["motive"])


if __name__ == "__main__":
    unittest.main()


class _DrawHarness(ProactiveMixin):
    def __init__(self, reasons: list[str]) -> None:
        self._reasons = list(reasons)

    def _choose_planned_reason(self) -> str:
        return self._reasons.pop(0)

    @staticmethod
    def _sample_proactive_timestamp(_user, *, now, delay_hours, reason=""):
        return now + 3600.0

    @staticmethod
    def _move_timestamp_into_reason_window(ts, reason, _user=None):
        return ts + 14 * 3600 if reason == "activity_share" else ts


class RandomReasonSlotTests(unittest.TestCase):
    def test_daytime_reason_at_night_is_redrawn(self) -> None:
        now = 1_000_000.0
        reason, slot = _DrawHarness(["activity_share", "quiet_care", "check_in"])._draw_random_reason_slot(
            {}, now=now, delay_hours=(1.0, 3.0)
        )
        self.assertEqual(reason, "quiet_care")
        self.assertEqual(slot, now + 3600.0)

    def test_keeps_earliest_when_every_draw_is_far(self) -> None:
        now = 1_000_000.0
        reason, slot = _DrawHarness(["activity_share"] * 6)._draw_random_reason_slot({}, now=now, delay_hours=(1.0, 3.0))
        self.assertEqual(reason, "activity_share")
        self.assertEqual(slot, now + 15 * 3600.0)
