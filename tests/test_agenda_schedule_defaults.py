import unittest

from companion_v2.agenda.models import Frequency, GapPolicy, Schedule


class ScheduleRestoreTests(unittest.TestCase):
    def test_optional_recurrence_fields_fall_back_to_defaults(self):
        schedule = Schedule.restore({"anchor": "2026-11-10T00:00:00+00:00", "timezone_name": "UTC"})
        self.assertEqual(schedule.frequency, Frequency.ONCE)
        self.assertEqual(schedule.interval, 1)
        self.assertIsNone(schedule.count)
        self.assertEqual(schedule.fold, 0)
        self.assertEqual(schedule.gap_policy, GapPolicy.REJECT)

    def test_boolean_fold_is_accepted_as_zero_or_one(self):
        base = {"anchor": "2026-11-10T00:00:00+00:00", "timezone_name": "UTC",
                "frequency": "once"}
        self.assertEqual(Schedule.restore({**base, "fold": False}).fold, 0)
        self.assertEqual(Schedule.restore({**base, "fold": True}).fold, 1)


if __name__ == "__main__":
    unittest.main()
