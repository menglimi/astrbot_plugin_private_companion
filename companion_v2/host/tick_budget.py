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
import time
from typing import Callable


@dataclass(frozen=True)
class TickUnit:
    """One named maintenance step a drive pass may defer to a later slice."""

    name: str
    run: Callable[[], object]


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
            results[unit.name] = unit.run()
            ran.append(unit.name)
            self._cursor += 1
            if self._cursor >= len(self._units):
                self._cursor = 0
                self._rotated = True
                return TickReport(True, tuple(ran), results)
            # The same yield the drive loop already made between components: it
            # lets intake, cancellation and other routes run inside one slice.
            await asyncio.sleep(0)
