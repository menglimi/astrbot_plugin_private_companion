"""One bounded slice of the conversation host's maintenance pass.

``CompanionApplication._drive`` calls a long list of component ``advance()``
methods on every pass. Each of them is a small read over the stored history — but
"small" is a function of how much history the installation has, and a single unit
that grows with it occupies the event loop for its whole duration: intake, timers,
streaming callbacks and page requests all wait behind it. The audit that motivated
this module found thirty-eight such stalls of 30-39 s.

The scheduler keeps the list, its order and the per-unit ``await`` that lets
intake run (a deferred yield, not a timed sleep). What it adds is a ceiling: once
a pass has spent its budget, the remaining units are left for the next pass, which
resumes at the unit that did not run. A rotation always advances, so a unit that
keeps being deferred is served first next time and nothing starves.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import inspect
import logging
import sqlite3
import time
from typing import Callable


logger = logging.getLogger(__name__)
SLOW_UNIT_WARNING_SECONDS = 0.5
SLOW_UNIT_LOG_INTERVAL_SECONDS = 300.0


@dataclass(frozen=True)
class StagedOffload:
    """One prepared off-loop maintenance step for single-pass or coalesced execution."""

    kind: str  # "skip" or "offload"
    scan: Callable[[], object] | None = None
    apply: Callable[[object], object] | None = None
    cleanup: Callable[[], None] | None = None
    value: object = None
    barrier_before: bool = False
    barrier_after: bool = False


@dataclass(frozen=True)
class TickUnit:
    """One named maintenance step a drive pass may defer to a later slice."""

    name: str
    run: Callable[[], object]
    offload: bool = False
    stage: Callable[[], StagedOffload | None] | None = None


@dataclass(frozen=True)
class TickReport:
    """What one slice actually executed.

    ``completed`` is False when the budget ran out before the rotation finished;
    the host then treats the pass as "more work is ready" instead of sleeping.
    ``results`` holds only the units that ran, so a reader must not assume a key
    is present.
    """

    completed: bool
    ran: tuple[str, ...]
    results: dict[str, object]


class TickScheduler:
    """Run ordered maintenance units inside one bounded slice of the loop.

    The first rotation is deliberately unbounded: it runs every unit once, exactly
    as the unconditional pass did before this module existed. A component decides
    things from that first look — the expression service opens its one-minute
    discovery gate from it, whether or not it found work — so delaying a unit inside
    that rotation would change what the first look sees. From the second rotation
    on, the bound applies.
    """

    def __init__(self, units: tuple[TickUnit, ...], *, budget_seconds: float,
                 clock: Callable[[], float] = time.monotonic) -> None:
        if not units or not all(isinstance(unit, TickUnit) and callable(unit.run) for unit in units):
            raise ValueError("a tick rotation needs named callable units")
        if type(budget_seconds) not in (int, float) or not budget_seconds > 0:
            raise ValueError("a tick slice needs a positive budget")
        if len({unit.name for unit in units}) != len(units):
            raise ValueError("tick unit names identify their results and must be unique")
        self._units = tuple(units)
        self._budget_seconds = float(budget_seconds)
        self._clock = clock
        self._cursor = 0
        self._rotated = False
        self._slow_log_at: dict[str, float] = {}
        self._slow_suppressed: dict[str, int] = {}

    @property
    def cursor(self) -> int:
        """Index of the unit the next slice starts from."""

        return self._cursor

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(unit.name for unit in self._units)

    async def advance(self) -> TickReport:
        """Run units from the cursor until the slice is spent, then report.

        At least one unit always runs, so a slice can never be wasted and a slow
        rotation cannot deadlock on the budget check.
        """

        started = self._clock()
        ran: list[str] = []
        results: dict[str, object] = {}
        while True:
            if ran and self._rotated and self._clock() - started >= self._budget_seconds:
                return TickReport(False, tuple(ran), results)
            unit = self._units[self._cursor]
            if not self._rotated and unit.stage is not None:
                batch: list[tuple[TickUnit, StagedOffload]] = []
                probe = self._cursor
                try:
                    while probe < len(self._units):
                        candidate = self._units[probe]
                        if candidate.stage is None:
                            break
                        plan = candidate.stage()
                        if plan is None:
                            break
                        if plan.barrier_before and batch:
                            if plan.cleanup is not None:
                                plan.cleanup()
                            break
                        batch.append((candidate, plan))
                        probe += 1
                        if plan.barrier_after:
                            break
                except BaseException:
                    for _, b_plan in batch:
                        if b_plan.cleanup is not None:
                            b_plan.cleanup()
                    raise
                if batch:
                    applied: set[int] = set()
                    try:
                        if any(plan.kind == "offload" for _, plan in batch):
                            def _run_staged_batch():
                                done_items = []
                                op_err = None
                                for idx, (b_unit, b_plan) in enumerate(batch):
                                    if b_plan.kind == "skip":
                                        done_items.append((idx, b_unit, b_plan, b_plan.value))
                                        continue
                                    try:
                                        scanned = b_plan.scan()
                                    except sqlite3.OperationalError as error:
                                        op_err = (idx, b_unit, error)
                                        break
                                    done_items.append((idx, b_unit, b_plan, scanned))
                                return done_items, op_err

                            done_items, op_err = await asyncio.to_thread(_run_staged_batch)
                        else:
                            done_items = [
                                (idx, b_unit, b_plan, b_plan.value)
                                for idx, (b_unit, b_plan) in enumerate(batch)
                            ]
                            op_err = None

                        for idx, b_unit, b_plan, scanned in done_items:
                            if b_plan.kind == "skip":
                                value = scanned
                            else:
                                value = b_plan.apply(scanned) if b_plan.apply is not None else scanned
                                if inspect.isawaitable(value):
                                    value = await value
                            applied.add(idx)
                            results[b_unit.name] = value
                            ran.append(b_unit.name)
                            self._cursor += 1
                    finally:
                        for idx, (_, b_plan) in enumerate(batch):
                            if idx not in applied and b_plan.cleanup is not None:
                                b_plan.cleanup()
                    if op_err is not None:
                        _idx, fail_unit, error = op_err
                        results[fail_unit.name] = {
                            "deferred": "sqlite_operational_error",
                            "message": str(error)[:200],
                        }
                        return TickReport(False, tuple(ran), results)
                    if self._cursor >= len(self._units):
                        self._cursor = 0
                        self._rotated = True
                        return TickReport(True, tuple(ran), results)
                    continue
            unit_started = time.monotonic()
            awaited = False
            try:
                if unit.offload:
                    results[unit.name] = await asyncio.to_thread(unit.run)
                else:
                    result = unit.run()
                    if inspect.isawaitable(result):
                        awaited = True
                        results[unit.name] = await result
                    else:
                        results[unit.name] = result
            except sqlite3.OperationalError as error:
                # A transient SQLite lock must not terminate the whole host
                # loop. Keep the cursor on this unit so the next slice retries
                # it; the rest of the rotation is deferred, not lost.
                results[unit.name] = {
                    "deferred": "sqlite_operational_error",
                    "message": str(error)[:200],
                }
                return TickReport(False, tuple(ran), results)
            finally:
                unit_finished = time.monotonic()
                elapsed = unit_finished - unit_started
                if not unit.offload and not awaited and elapsed >= SLOW_UNIT_WARNING_SECONDS:
                    last_logged = self._slow_log_at.get(unit.name, 0.0)
                    if unit_finished - last_logged >= SLOW_UNIT_LOG_INTERVAL_SECONDS:
                        self._slow_log_at[unit.name] = unit_finished
                        suppressed = self._slow_suppressed.pop(unit.name, 0)
                        if suppressed > 0:
                            logger.warning(
                                "[CompanionV2] 维护单元主线程耗时过长 unit=%s elapsed_ms=%d suppressed=%d",
                                unit.name,
                                round(elapsed * 1000),
                                suppressed,
                            )
                        else:
                            logger.warning(
                                "[CompanionV2] 维护单元主线程耗时过长 unit=%s elapsed_ms=%d",
                                unit.name,
                                round(elapsed * 1000),
                            )
                    else:
                        self._slow_suppressed[unit.name] = self._slow_suppressed.get(unit.name, 0) + 1
            ran.append(unit.name)
            self._cursor += 1
            if self._cursor >= len(self._units):
                self._cursor = 0
                self._rotated = True
                return TickReport(True, tuple(ran), results)
            # The same yield the drive loop already made between components: it
            # lets intake, cancellation and other routes run inside one slice.
            # On the unbounded first rotation, offloaded batches already yield
            # via ``asyncio.to_thread``; skipping extra yields between fast
            # synchronous units keeps paired startup observations atomic.
            if self._rotated:
                await asyncio.sleep(0)
