"""Expired model reservations are reclaimed off the event loop.

The reclamation pass reads every unconfirmed model call and follows its task and
request rows. On a store with a long history that is seconds of synchronous
SQLite work; run on the loop thread it freezes every event, timer and page read
behind it, which the user sees as a request that never returns.

These tests pin both halves of the fix: the drive pass must run on a worker
thread, and the loop must keep running while that thread is blocked. The last one
pins the blast radius: a failed pass may not fault the host.
"""

from __future__ import annotations

import asyncio
import threading
import time
import unittest

from companion_v2.execution.models import TaskError
from tests.test_host_application import HostFixture


class BudgetReclaimOffloadTests(HostFixture, unittest.IsolatedAsyncioTestCase):
    def prepare(self):
        """A host that reclaims on every drive pass and no ledger of its own."""

        host = self.host()
        host.application.BUDGET_RECLAIM_INTERVAL_SECONDS = 0.0
        return host, host.application

    async def wait_for(self, predicate, *, timeout=20.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            await asyncio.sleep(0.01)
        return False

    async def test_reclaim_pass_runs_on_a_worker_thread(self):
        threads = []
        host, application = self.prepare()

        def reclaim(supervisor, **_options):
            # Startup recovery also reclaims, while the host is still starting and
            # nothing else is being served; only the drive pass is under test.
            if application.state == "running":
                threads.append(threading.current_thread())

        application.gateway.ledger.reclaim_expired = reclaim
        await self.start(host)
        self.assertTrue(await self.wait_for(lambda: threads), "reclamation never ran")
        self.assertIsNot(threads[0], threading.main_thread(),
                          "expired reservations were reclaimed on the event loop thread")

    async def test_loop_keeps_serving_while_reclaim_blocks(self):
        ticks = []
        release = threading.Event()
        host, application = self.prepare()

        def reclaim(supervisor, **_options):
            if application.state == "running":
                release.wait(5.0)

        application.gateway.ledger.reclaim_expired = reclaim
        await self.start(host)

        async def heartbeat():
            while True:
                ticks.append(time.monotonic())
                await asyncio.sleep(0.01)

        beats = asyncio.create_task(heartbeat(), name="test:heartbeat")
        try:
            await asyncio.sleep(0.3)
            during = len(ticks)
        finally:
            beats.cancel()
            release.set()
            await asyncio.gather(beats, return_exceptions=True)
        self.assertGreater(during, 5,
                           f"the loop only ticked {during} times while reclamation blocked")

    async def test_failed_reclaim_does_not_fault_the_host(self):
        attempts = []
        host, application = self.prepare()

        def reclaim(supervisor, **_options):
            if application.state != "running":
                return None
            attempts.append(1)
            if len(attempts) == 1:
                raise TaskError("supervisor_fenced", "only the current controller can reclaim")
            return None

        application.gateway.ledger.reclaim_expired = reclaim
        await self.start(host)
        self.assertTrue(await self.wait_for(lambda: attempts))
        self.assertEqual(application.state, "running")


if __name__ == "__main__":
    unittest.main()
