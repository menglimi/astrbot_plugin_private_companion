"""Reuse located weather observations for the cottage's read-only view."""

import asyncio
from datetime import timedelta

from .weather_material import planning_weather_material


REFRESH_INTERVAL = timedelta(minutes=5)
MINIMUM_REFRESH_INTERVAL = timedelta(seconds=30)


class WeatherReading:
    """One role page keeps one observation and shares overlapping reads."""

    def __init__(self):
        self.key = None
        self.material = None
        self.fetched_at = None
        self.attempted_at = None
        self.failed = False
        self.pending = None

    async def read(self, port, location, *, max_age, now, force=False):
        key = (port, location, max_age)
        if key != self.key:
            old_pending = self.pending
            self.key, self.material, self.fetched_at, self.attempted_at = key, None, None, None
            self.failed, self.pending = False, None
            if old_pending is not None and not old_pending.done():
                old_pending.cancel()
        at = now()
        refresh_interval = timedelta(minutes=getattr(port, "refresh_minutes", 5))
        interval = MINIMUM_REFRESH_INTERVAL if force else refresh_interval
        throttled = bool(force and self.attempted_at is not None
                         and timedelta(0) <= at - self.attempted_at < MINIMUM_REFRESH_INTERVAL)
        if (not throttled and (self.attempted_at is None or at - self.attempted_at >= interval
                or at < self.attempted_at)):
            if self.pending is None or self.pending.done():
                task = asyncio.create_task(self._fetch(port, location, max_age, now, key, force))
                def _clear_pending(_done, expected=task):
                    if self.pending is expected:
                        self.pending = None
                task.add_done_callback(_clear_pending)
                self.pending = task
            pending = self.pending
            try:
                await asyncio.shield(pending)
            except asyncio.CancelledError:
                if not pending.cancelled() or key == self.key:
                    raise
        at = now()
        if key != self.key or self.material is None:
            return {"state": "unavailable", "location": location.name}
        from datetime import datetime
        expired = datetime.fromisoformat(self.material["valid_until"]) <= at
        if expired:
            return {"state": "unavailable", "location": location.name}
        return {"state": "stale" if self.failed else "live", "location": location.name,
                "condition": self.material["condition"], "temperature_c": self.material["temperature_c"],
                "provider": self.material["provider"], "observed_at": self.material["observed_at"],
                "valid_until": self.material["valid_until"], "fetched_at": self.fetched_at.isoformat(),
                "refresh_after": (self.attempted_at + refresh_interval).isoformat(),
                **({"refresh_throttled": True} if throttled else {})}

    async def _fetch(self, port, location, max_age, now, key, force):
        material = None
        try:
            if port is not None:
                fetch = getattr(port, "refresh", port.fetch) if force else port.fetch
                snapshot = await asyncio.wait_for(fetch(location), timeout=6)
                material = planning_weather_material(snapshot, location, now(), max_age=max_age)
        except Exception:
            pass
        if key != self.key:
            return
        at = now()
        self.attempted_at = at
        self.failed = material is None
        if material is not None:
            self.material, self.fetched_at = material, at
