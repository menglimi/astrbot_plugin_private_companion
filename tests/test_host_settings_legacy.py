"""Legacy policy keys must not block saving account mappings."""

from __future__ import annotations

import unittest

from astrbot_host.settings import HostSettings, repair_host_policy_sections


class LegacyHostSettingsTests(unittest.TestCase):
    def test_unknown_legacy_policy_keys_are_ignored(self):
        parsed = HostSettings.parse({
            "segmentation": {"removed_legacy_key": 1, "max_segments": 3},
            "voice_reply": {"removed_legacy_key": 2, "probability": 0.5},
        })
        self.assertEqual(parsed.segmentation.max_segments, 3)
        self.assertEqual(parsed.voice_reply_policy.probability, 0.5)

    def test_known_but_invalid_values_still_fail(self):
        with self.assertRaises(Exception) as raised:
            HostSettings.parse({"segmentation": {"max_segments": "not-an-int"}})
        self.assertEqual(getattr(raised.exception, "code", ""), "staging_settings_invalid")

    def test_mapping_save_can_repair_invalid_legacy_policy_values(self):
        config = {
            "segmentation": {"enabled": False, "max_segments": "not-an-int"},
            "voice_reply": {"probability": 0.5, "trigger_mode": "retired-mode"},
        }
        repaired = repair_host_policy_sections(config)
        self.assertEqual(repaired, {"segmentation": ["max_segments"],
                                    "voice_reply": ["trigger_mode"]})
        parsed = HostSettings.parse(config)
        self.assertFalse(parsed.segmentation.enabled)
        self.assertEqual(parsed.segmentation.max_segments, 5)
        self.assertEqual(parsed.voice_reply_policy.probability, 0.5)
        self.assertEqual(parsed.voice_reply_policy.trigger_mode, "always")


if __name__ == "__main__":
    unittest.main()
