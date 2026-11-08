"""One drive pass is bounded, and no maintenance unit starves.

``_drive`` used to call every component ``advance()`` unconditionally. A unit whose
cost grows with the stored history therefore occupied the event loop for its whole
duration, with intake, timers and page reads behind it. The scheduler keeps the
order and the yields but caps a pass; these tests pin the cap, the resumption point
and the guarantee that a deferred unit is served on a later pass.
"""

from __future__ import annotations

import asyncio
import unittest

from companion_v2.host.tick_budget import TickScheduler, TickUnit
from tests.test_host_application import HostFixture


class SteppingClock:
    """A monotonic clock that advances a fixed amount on every reading."""

    def __init__(self, step):
        self.now = 0.0
        self.step = step

    def __call__(self):
        value = self.now
        self.now += self.step
        return value


def recorder(ran):
    def record(name):
        def run():
            ran.append(name)
            return name
        return run
    return record


class TickSchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def test_units_run_in_order_and_report_their_results(self):
        ran = []
        scheduler = TickScheduler(tuple(TickUnit(name, recorder(ran)(name))
                                        for name in ("first", "second", "third")),
                                  budget_seconds=10.0)
        report = await scheduler.advance()
        self.assertEqual(ran, ["first", "second", "third"])
        self.assertTrue(report.completed)
        self.assertEqual(report.ran, ("first", "second", "third"))
        self.assertEqual(report.results, {"first": "first", "second": "second", "third": "third"})
        self.assertEqual(scheduler.cursor, 0)

    async def test_a_spent_slice_defers_the_rest_and_resumes_there(self):
        ran = []
        units = tuple(TickUnit(name, recorder(ran)(name))
                      for name in ("first", "second", "third", "fourth"))
        # Each clock reading costs 50 ms, so a 60 ms slice fits exactly two units.
        scheduler = TickScheduler(units, budget_seconds=0.06, clock=SteppingClock(0.05))
        # The first rotation is never deferred: it is the startup look, and every
        # component decides from it.
        startup = await scheduler.advance()
        self.assertTrue(startup.completed)
        self.assertEqual(ran, ["first", "second", "third", "fourth"])
        ran.clear()

        deferred = await scheduler.advance()
        self.assertEqual(ran, ["first", "second"])
        self.assertFalse(deferred.completed)
        self.assertEqual(deferred.ran, ("first", "second"))
        self.assertEqual(scheduler.cursor, 2)

        resumed = await scheduler.advance()
        self.assertEqual(ran, ["first", "second", "third", "fourth"])
        self.assertTrue(resumed.completed)
        self.assertEqual(scheduler.cursor, 0)

    async def test_one_unit_always_runs_even_when_the_slice_is_already_spent(self):
        ran = []
        scheduler = TickScheduler((TickUnit("only", recorder(ran)("only")),),
                                  budget_seconds=1e-9, clock=SteppingClock(1.0))
        report = await scheduler.advance()
        self.assertEqual(ran, ["only"])
        self.assertTrue(report.completed)

    async def test_the_startup_rotation_is_never_deferred(self):
        """The first look must see exactly what the unconditional pass saw.

        Components decide from that first look — the expression service opens its
        one-minute discovery gate from it whether or not it found work — so a unit
        left for a later slice would answer a different question than before.
        """

        names = ("first", "second", "third")
        ran = []
        scheduler = TickScheduler(tuple(TickUnit(name, recorder(ran)(name)) for name in names),
                                  budget_seconds=1e-9, clock=SteppingClock(1.0))
        report = await scheduler.advance()
        self.assertEqual(ran, list(names))
        self.assertTrue(report.completed)

    async def test_no_unit_starves_when_every_slice_runs_out(self):
        names = ("first", "second", "third", "fourth", "fifth")
        ran = []
        scheduler = TickScheduler(tuple(TickUnit(name, recorder(ran)(name)) for name in names),
                                  budget_seconds=1e-9, clock=SteppingClock(1.0))
        await scheduler.advance()  # the unbounded startup rotation
        ran.clear()
        for _ in range(len(names)):
            await scheduler.advance()
        self.assertEqual(ran, list(names))

    async def test_a_failing_unit_stops_the_slice_and_propagates(self):
        ran = []

        def explode():
            raise RuntimeError("unit failed")

        scheduler = TickScheduler((TickUnit("first", recorder(ran)("first")),
                                   TickUnit("boom", explode),
                                   TickUnit("never", recorder(ran)("never"))),
                                  budget_seconds=10.0)
        with self.assertRaises(RuntimeError):
            await scheduler.advance()
        self.assertEqual(ran, ["first"])

    def test_a_rotation_needs_named_unique_units_and_a_positive_budget(self):
        with self.assertRaises(ValueError):
            TickScheduler((), budget_seconds=1.0)
        with self.assertRaises(ValueError):
            TickScheduler((TickUnit("a", lambda: None), TickUnit("a", lambda: None)),
                          budget_seconds=1.0)
        with self.assertRaises(ValueError):
            TickScheduler((TickUnit("a", lambda: None),), budget_seconds=0)


class DrivePassBudgetTests(HostFixture, unittest.IsolatedAsyncioTestCase):
    async def test_every_component_is_still_served_when_slices_are_tiny(self):
        """The bound may delay a unit, never drop it."""

        from unittest import mock

        host = self.host()
        application = host.application
        application.TICK_BUDGET_SECONDS = 1e-9  # one unit per pass, worst case
        spied = {}
        for name, (component, method) in {
                "state-maintenance": (application.state_maintenance, "advance"),
                "expression-style": (application.expression_style, "advance"),
                "journal-schedule": (application.journal_schedule, "advance"),
                "background": (application.background, "advance")}.items():
            patcher = mock.patch.object(component, method, wraps=getattr(component, method))
            spied[name] = patcher.start()
            self.addCleanup(patcher.stop)

        await self.start(host)
        deadline = asyncio.get_running_loop().time() + 10.0
        while asyncio.get_running_loop().time() < deadline:
            if all(spy.call_count for spy in spied.values()):
                break
            await asyncio.sleep(0.02)
        self.assertTrue(all(spy.call_count for spy in spied.values()),
                        {name: spy.call_count for name, spy in spied.items()})
        # Both background units ran: the planner page and the delivery page.
        spellings = [call.kwargs for call in spied["background"].call_args_list]
        self.assertIn({"planners": False}, spellings)
        self.assertIn({}, spellings)


if __name__ == "__main__":
    unittest.main()
