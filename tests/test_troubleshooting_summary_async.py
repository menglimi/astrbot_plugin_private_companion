from __future__ import annotations

import asyncio
import threading
import unittest
from types import SimpleNamespace

from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi


class TroubleshootingSummaryAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_slow_summary_runs_off_loop_and_concurrent_calls_share_flight(self) -> None:
        api = PrivateCompanionPageApi(SimpleNamespace())
        entered = threading.Event()
        release = threading.Event()
        calls = 0

        def slow_summary(_data):
            nonlocal calls
            calls += 1
            entered.set()
            release.wait(timeout=2.0)
            return {"total": 1, "items": [{"id": "one"}], "users": []}

        api._proactive_task_summary = slow_summary
        first = asyncio.create_task(api._proactive_task_summary_async({"users": {}}))
        while not entered.is_set():
            await asyncio.sleep(0.005)
        second = asyncio.create_task(api._proactive_task_summary_async({"users": {}}))

        # If the synchronous summary occupied the event loop, this heartbeat
        # could not complete until release was set.
        await asyncio.wait_for(asyncio.sleep(0.01), timeout=0.1)
        self.assertIsNotNone(api._proactive_task_summary_task)
        release.set()
        results = await asyncio.gather(first, second)

        self.assertEqual(1, calls)
        self.assertEqual(results[0], results[1])
        self.assertEqual(1, results[0]["total"])

    async def test_failure_returns_last_good_with_bounded_diagnostic(self) -> None:
        api = PrivateCompanionPageApi(SimpleNamespace())
        api._proactive_task_summary = lambda _data: {
            "total": 2,
            "pending_total": 1,
            "items": [],
            "users": [],
        }
        good = await api._proactive_task_summary_async({"users": {}})
        self.assertEqual(2, good["total"])

        api._proactive_task_summary_cache_at = 0.0

        def fail(_data):
            raise RuntimeError("sensitive internal detail")

        api._proactive_task_summary = fail
        degraded = await api._proactive_task_summary_async({"users": {}})

        self.assertEqual(2, degraded["total"])
        self.assertTrue(degraded["degraded"])
        self.assertEqual(
            {
                "code": "proactive_task_summary_unavailable",
                "error_type": "RuntimeError",
                "using_last_good": True,
            },
            degraded["diagnostic"],
        )
        self.assertNotIn("sensitive internal detail", str(degraded))

    async def test_force_refresh_bypasses_short_cache_after_mutation(self) -> None:
        api = PrivateCompanionPageApi(SimpleNamespace())
        api._proactive_task_summary = lambda data: {
            "total": int(data.get("total") or 0),
            "items": [],
            "users": [],
        }

        first = await api._proactive_task_summary_async({"total": 1})
        cached = await api._proactive_task_summary_async({"total": 2})
        refreshed = await api._proactive_task_summary_async(
            {"total": 2},
            force_refresh=True,
        )

        self.assertEqual(first["total"], 1)
        self.assertEqual(cached["total"], 1)
        self.assertEqual(refreshed["total"], 2)


if __name__ == "__main__":
    unittest.main()
