# -*- coding: utf-8 -*-
"""Regression: future timestamps must not be treated as "just updated" (P2).

A future timestamp (clock drift, bogus message ts) previously clamped the age
to 0.0 via ``max(0.0, now - ts)``, so the entry looked freshly updated and the
TTL sweep in ``_enforce_group_slang_meanings_budget`` could never evict it.
"""
from __future__ import annotations

import time
import unittest
from datetime import datetime

from astrbot_plugin_private_companion.group_observation import GroupObservationMixin


class _FutureTsHarness(GroupObservationMixin):
    def __init__(self, ttl_days: int = 180) -> None:
        self.group_slang_meanings_ttl_days = ttl_days
        self.max_group_slang_meanings = 120


def _human(offset_seconds: float) -> str:
    return datetime.fromtimestamp(time.time() + offset_seconds).strftime("%Y-%m-%d %H:%M:%S")


class FutureTimestampTtlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = _FutureTsHarness()
        # TTL window that any sane "stale" value must exceed.
        self.ttl_seconds = 180 * 86400

    def test_numeric_future_ts_is_not_treated_as_fresh(self) -> None:
        future_ts = time.time() + 86400  # one day into the future
        item = {"meaning": "x", "source": "llm", "updated_at": future_ts}

        age = self.harness._group_slang_meaning_age_seconds(item)

        self.assertGreater(age, 0.0, "future ts must not be reported as age 0 (fresh)")
        self.assertGreater(
            age,
            self.ttl_seconds,
            "future ts must be evictable by TTL, not immortal",
        )

    def test_string_future_ts_is_not_treated_as_fresh(self) -> None:
        item = {"meaning": "x", "source": "llm", "updated_at": _human(86400)}

        age = self.harness._group_slang_meaning_age_seconds(item)

        self.assertGreater(age, 0.0, "future string ts must not be reported as age 0 (fresh)")
        self.assertGreater(
            age,
            self.ttl_seconds,
            "future string ts must be evictable by TTL, not immortal",
        )

    def test_numeric_future_ts_entry_is_evicted_by_budget_sweep(self) -> None:
        future_ts = time.time() + 86400
        group = {
            "slang_meanings": {
                "未来词": {"meaning": "x", "source": "llm", "updated_at": future_ts},
            }
        }

        removed = self.harness._enforce_group_slang_meanings_budget(group)

        self.assertEqual(removed, 1)
        self.assertNotIn("未来词", group["slang_meanings"])

    def test_string_future_ts_entry_is_evicted_by_budget_sweep(self) -> None:
        group = {
            "slang_meanings": {
                "未来词": {"meaning": "x", "source": "llm", "updated_at": _human(86400)},
            }
        }

        removed = self.harness._enforce_group_slang_meanings_budget(group)

        self.assertEqual(removed, 1)
        self.assertNotIn("未来词", group["slang_meanings"])

    def test_past_ts_entry_is_also_evicted(self) -> None:
        """Sanity: the normal stale path keeps working."""
        item = {"meaning": "x", "source": "llm", "updated_at": time.time() - 400 * 86400}
        age = self.harness._group_slang_meaning_age_seconds(item)
        self.assertGreater(age, self.ttl_seconds)

    def test_fresh_ts_entry_is_kept(self) -> None:
        """Sanity: a genuinely fresh entry is still fresh."""
        item = {"meaning": "x", "source": "llm", "updated_at": time.time() - 60}
        age = self.harness._group_slang_meaning_age_seconds(item)
        self.assertLess(age, self.ttl_seconds)


if __name__ == "__main__":
    unittest.main()
