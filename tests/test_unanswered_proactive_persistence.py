# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

from astrbot_plugin_private_companion.core_store import CoreStoreMixin
from astrbot_plugin_private_companion.helpers import (
    _record_unanswered_proactive,
    _reset_unanswered_proactive,
    _unanswered_proactive_count,
)
from astrbot_plugin_private_companion.proactive import ProactiveMixin


class _PauseHarness(ProactiveMixin):
    proactive_unanswered_pause_after = 3

    @staticmethod
    def _proactive_intensity_effect(_key: str, default):
        return default


class UnansweredProactivePersistenceTests(unittest.TestCase):
    def test_legacy_count_is_migrated_to_the_durable_field(self) -> None:
        user = {"ignored_streak": 2}

        self.assertEqual(2, _unanswered_proactive_count(user))
        self.assertEqual(2, user["unanswered_proactive_count"])
        self.assertEqual(2, user["ignored_streak"])

    def test_increment_and_reset_keep_both_fields_in_sync(self) -> None:
        user = {"unanswered_proactive_count": 1, "ignored_streak": 1}

        self.assertEqual(2, _record_unanswered_proactive(user, sent_at=123.0))
        self.assertEqual(2, user["unanswered_proactive_count"])
        self.assertEqual(2, user["ignored_streak"])
        self.assertEqual(123.0, user["unanswered_proactive_count_updated_at"])

        _reset_unanswered_proactive(user, replied_at=456.0)
        self.assertEqual(0, user["unanswered_proactive_count"])
        self.assertEqual(0, user["ignored_streak"])
        self.assertEqual(456.0, user["unanswered_proactive_count_updated_at"])

    def test_store_startup_migrates_existing_users_before_runtime_reads(self) -> None:
        data = {"users": {"u1": {"ignored_streak": 4}}}

        CoreStoreMixin._ensure_store_defaults(data)

        self.assertEqual(4, data["users"]["u1"]["unanswered_proactive_count"])

    def test_pause_threshold_is_configurable_and_disabled_by_zero(self) -> None:
        harness = _PauseHarness()
        self.assertIn("阈值 3", harness._proactive_unanswered_pause_reason({"ignored_streak": 3}))

        harness.proactive_unanswered_pause_after = 0
        self.assertEqual("", harness._proactive_unanswered_pause_reason({"ignored_streak": 99}))


if __name__ == "__main__":
    unittest.main()
