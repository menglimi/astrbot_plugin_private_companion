"""Morning care: mode presets, optional deadline and every-day default."""

import unittest

from astrbot_host.native_extensions.reality.wakeup_alarm import WakeupAlarmMixin


class FakeMorningCare(WakeupAlarmMixin):
    def __init__(self):
        self.data = {"users": {"42": {"user_id": "42", "wakeup_alarm": {}}}}

    def _reality_touch_audio_consented(self, user):
        return True

    def _reality_touch_playback_volume(self):
        return 35

    def _stop_wakeup_contact_session(self, user, *, status="cancelled"):
        pass


class MorningCareTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeMorningCare()
        self.user = self.api.data["users"]["42"]

    def test_gentle_mode_sets_soft_defaults_and_keeps_the_deadline(self):
        alarm = self.api._reality_touch_update_alarm(self.user, {
            "enabled": True, "time": "07:30", "deadline": "09:00", "mode": "gentle",
            "message": "记得吃早餐",
        })
        self.assertEqual(alarm["mode"], "gentle")
        self.assertEqual(alarm["deadline"], "09:00")
        self.assertEqual(alarm["repeat_count"], 1)
        self.assertEqual(alarm["playback_volume"], 25)
        self.assertEqual(alarm["repeat_interval_seconds"], 180)
        self.assertEqual(alarm["days"], list(range(7)))

    def test_persistent_mode_escalates_without_extra_fields(self):
        alarm = self.api._reality_touch_update_alarm(self.user, {
            "enabled": True, "time": "07:00", "mode": "persistent",
        })
        self.assertEqual(alarm["repeat_count"], 4)
        self.assertEqual(alarm["repeat_interval_seconds"], 45)
        self.assertEqual(alarm["max_volume"], 85)

    def test_deadline_must_be_later_than_the_start(self):
        with self.assertRaises(ValueError):
            self.api._reality_touch_update_alarm(self.user, {
                "enabled": True, "time": "08:00", "deadline": "07:30", "mode": "normal",
            })

    def test_missing_mode_falls_back_to_normal(self):
        alarm = self.api._reality_touch_update_alarm(self.user, {"enabled": True, "time": "07:30"})
        self.assertEqual(alarm["mode"], "normal")
        self.assertEqual(alarm["repeat_count"], 2)
        self.assertEqual(alarm["deadline"], "")


if __name__ == "__main__":
    unittest.main()
