# -*- coding: utf-8 -*-
from __future__ import annotations

import time
import unittest

from astrbot_plugin_private_companion.daily_state import DailyStateMixin


class _PresenceHarness(DailyStateMixin):
    enable_qq_presence_sync = True
    enable_qq_custom_presence_sync = False

    def __init__(self, updated_at: float) -> None:
        self.calls = 0
        self.data = {
            "detail_enhanced_day": "2026-07-24",
            "qq_presence_state": {
                "mode": "busy",
                "custom_text": "",
                "updated_at": updated_at,
                "ok": False,
            },
        }

    async def _set_qq_online_presence(self, _mode: str) -> tuple[bool, str]:
        self.calls += 1
        return False, "unsupported action"

    def _save_data_sync(self) -> None:
        return None


class QqPresenceSyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_recent_failure_suppresses_same_presence_retry(self) -> None:
        harness = _PresenceHarness(time.time())

        await harness._apply_detail_presence_status(
            {"key": "new-detail"},
            {"presence_status": {"mode": "busy"}},
        )

        self.assertEqual(0, harness.calls)

    async def test_expired_failure_allows_one_presence_retry(self) -> None:
        harness = _PresenceHarness(time.time() - 60 * 60 - 1)

        await harness._apply_detail_presence_status(
            {"key": "new-detail"},
            {"presence_status": {"mode": "busy"}},
        )

        self.assertEqual(1, harness.calls)


if __name__ == "__main__":
    unittest.main()
