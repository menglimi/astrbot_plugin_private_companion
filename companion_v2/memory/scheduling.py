"""Persisted daily memory windows, driven by the existing host wake loop."""

import asyncio
from datetime import datetime, time, timedelta, timezone
import json
import logging
from zoneinfo import ZoneInfo

from companion_v2.execution.models import TaskContext, TaskError, encode, require, stamp
from companion_v2.execution.service import outcome

from .models import owner_key, text, timestamp
from .configuration import validate_schedule
from .checkpoints import MemoryCheckpointRecovery
from .reflection_jobs import MemoryReflectionJobs


logger = logging.getLogger(__name__)
_CONFIGURATION_REFRESH_INTERVAL_SECONDS = 60


def next_due(now, zone_name, local_time):
    zone, clock = ZoneInfo(zone_name), time.fromisoformat(local_time)
    if clock.tzinfo is not None:
        raise ValueError("daily clock uses its separate IANA timezone")
    day = now.astimezone(zone).date()
    candidate = datetime.combine(day, clock, zone).astimezone(timezone.utc)
    if candidate <= now:
        candidate = datetime.combine(day + timedelta(days=1), clock, zone).astimezone(timezone.utc)
    # During a spring gap Python's UTC conversion gives the corresponding later
    # real instant; fall overlaps use fold=0 and the durable occurrence identity.
    return candidate


class MemorySchedule:
    def __init__(self, reflection, *, notify):
        self.reflection, self.notify, self.runtime = reflection, notify, reflection.runtime
        self.workers, self.results = {}, {}
        self.closed = False
        self._configuration_tasks = set()
        self._configuration_locks = {}
        self._configuration_refresh_task = None
        self._next_configuration_refresh = 0.0
        self.recovery = MemoryCheckpointRecovery(reflection, self._settle)
        self.continuations = MemoryReflectionJobs(reflection, notify)

    def start_recovery(self):
        self.recovery.start()

    def sync_configuration(self, context, role_instruction):
        """Derive a durable schedule from the same configuration seen by management."""
        actions = self._sync_configuration(context, role_instruction)
        if actions is not None:
            self._apply_runtime_actions(*actions)

    async def sync_configuration_async(self, context, role_instruction):
        """Move optional schedule IO off the loop, then cancel/wake on the loop."""
        if self.closed:
            return
        key = "memory-daily-" + owner_key(context)
        lock = self._configuration_locks.setdefault(key, asyncio.Lock())
        async with lock:
            if self.closed:
                return
            actions = await asyncio.to_thread(self._sync_configuration, context, role_instruction)
            if actions is not None:
                self._apply_runtime_actions(*actions)

    def schedule_configuration(self, context, role_instruction):
        """Queue optional schedule persistence after the current intake transaction."""
        if self.closed:
            return None
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            try:
                self.sync_configuration(context, role_instruction)
            except (TaskError, ValueError, KeyError):
                self.results["configuration"] = {"state": "blocked", "code": "memory_schedule_configuration"}
            except Exception as error:
                logger.warning("Memory schedule configuration sync failed error_type=%s", type(error).__name__)
                self.results["configuration"] = {"state": "failed", "code": "memory_schedule_configuration"}
            return None
        task = loop.create_task(self._sync_configuration_safely(context, role_instruction),
                                name="companion:memory-schedule-configuration")
        self._configuration_tasks.add(task)
        task.add_done_callback(self._configuration_tasks.discard)
        return task

    async def _sync_configuration_safely(self, context, role_instruction):
        try:
            await self.sync_configuration_async(context, role_instruction)
        except (TaskError, ValueError, KeyError):
            self.results["configuration"] = {"state": "blocked", "code": "memory_schedule_configuration"}
        except Exception as error:
            logger.warning("Memory schedule configuration sync failed error_type=%s", type(error).__name__)
            self.results["configuration"] = {"state": "failed", "code": "memory_schedule_configuration"}

    def _schedule_configuration_refresh(self):
        if self.closed:
            return
        loop = asyncio.get_running_loop()
        now = loop.time()
        if now < self._next_configuration_refresh:
            return
        self._next_configuration_refresh = now + _CONFIGURATION_REFRESH_INTERVAL_SECONDS
        task = self._configuration_refresh_task
        if task is None or task.done():
            self._configuration_refresh_task = loop.create_task(
                self._refresh_configurations(), name="companion:memory-schedule-refresh")

    async def _refresh_configurations(self):
        try:
            rows = await asyncio.to_thread(self.runtime.read_all,
                "SELECT context_json, settings_json FROM memory_schedules WHERE schedule_id LIKE 'memory-daily-%'", ())
        except Exception as error:
            logger.warning("Memory schedule configuration scan failed error_type=%s", type(error).__name__)
            self.results["configuration"] = {"state": "failed", "code": "memory_schedule_configuration"}
            return
        for configured in rows:
            if self.closed:
                return
            try:
                context = TaskContext.restore(json.loads(configured["context_json"]))
                instruction = json.loads(configured["settings_json"])["role_instruction"]
                await self._sync_configuration_safely(context, instruction)
            except (TaskError, ValueError, KeyError):
                self.results["configuration"] = {"state": "blocked", "code": "memory_schedule_configuration"}
            except Exception as error:
                logger.warning("Memory schedule configuration row failed error_type=%s", type(error).__name__)
                self.results["configuration"] = {"state": "failed", "code": "memory_schedule_configuration"}

    def _sync_configuration(self, context, role_instruction):
        settings = self.reflection.memory.settings
        if settings is None:
            return
        values, token = settings.read(context)
        validate_schedule(values)
        key = "memory-daily-" + owner_key(context)
        row = self.runtime.read_one("SELECT * FROM memory_schedules WHERE schedule_id = ?", (key,))
        enabled = values["memory.enabled"] and values["memory.reflection_enabled"]
        if row is None and not enabled:
            return
        if row and not enabled:
            if row["enabled"]:
                with self.runtime.transaction() as connection:
                    connection.execute("UPDATE memory_schedules SET enabled = 0, revision = revision + 1 WHERE schedule_id = ?", (key,))
                return key, False
            return
        if row and (json.loads(row["settings_json"]).get("configuration_token") == token
                    and TaskContext.restore(json.loads(row["context_json"])).key == context.key
                    and json.loads(row["settings_json"])["role_instruction"] == role_instruction):
            return
        result = self._configure(context, schedule_id=key, expected_revision=row["revision"] if row else 0,
            role_instruction=role_instruction, zone=values["memory.reflection_timezone"],
            local_time=values["memory.reflection_time"], enabled=enabled, configuration_token=token)
        if result.is_success:
            return key, enabled

    @outcome
    def configure(self, context, *, schedule_id, expected_revision, role_instruction, zone="Asia/Shanghai", local_time="23:50", enabled=True):
        receipt = require(self._configure(context, schedule_id=schedule_id, expected_revision=expected_revision,
            role_instruction=role_instruction, zone=zone, local_time=local_time, enabled=enabled))
        self._apply_runtime_actions(schedule_id, enabled)
        return receipt

    def _apply_runtime_actions(self, schedule_id, enabled):
        if not enabled and schedule_id in self.workers:
            self.workers[schedule_id].cancel()
        self.notify()

    @outcome
    def _configure(self, context, *, schedule_id, expected_revision, role_instruction, zone="Asia/Shanghai",
                   local_time="23:50", enabled=True, configuration_token=None):
        self.reflection.memory.writer.access.check(context, "memory.reflect")
        text(schedule_id, "schedule_id", 256)
        text(role_instruction, "role_instruction", 65536)
        if type(expected_revision) is not int or expected_revision < 0 or type(enabled) is not bool:
            raise ValueError("schedule revision and enabled state must be explicit")
        now = self.reflection.tasks.now()
        due = next_due(now, zone, local_time)
        with self.runtime.transaction() as connection:
            row = connection.execute("SELECT * FROM memory_schedules WHERE schedule_id = ?", (schedule_id,)).fetchone()
            if row and row["owner_key"] != owner_key(context):
                raise TaskError("memory_schedule_unavailable", "schedule belongs to another relationship")
            if (row["revision"] if row else 0) != expected_revision:
                raise TaskError("memory_schedule_conflict", "schedule was changed")
            start = stamp(datetime.combine(now.astimezone(ZoneInfo(zone)).date(), time(), ZoneInfo(zone)))
            settings = {"zone": zone, "local_time": local_time, "role_instruction": role_instruction, "gap_policy": "shift_forward", "fold": 0}
            if configuration_token is not None:
                if type(configuration_token) is not str or not 1 <= len(configuration_token) <= 256:
                    raise ValueError("configuration token must be bounded text")
                settings["configuration_token"] = configuration_token
            connection.execute("""INSERT INTO memory_schedules VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)
                ON CONFLICT(schedule_id) DO UPDATE SET revision = excluded.revision, context_json = excluded.context_json,
                settings_json = excluded.settings_json, enabled = excluded.enabled, next_at = excluded.next_at""",
                (schedule_id, owner_key(context), expected_revision + 1, encode(context), encode(settings), int(enabled),
                 stamp(due), row["covered_until"] if row else start))
        return {"schedule_id": schedule_id, "revision": expected_revision + 1, "enabled": enabled, "next_at": stamp(due)}

    def advance(self):
        if self.closed:
            return
        self.recovery.advance()
        if self.recovery.state == "running":
            return
        self._schedule_configuration_refresh()
        for key, worker in tuple(self.workers.items()):
            if worker.done():
                del self.workers[key]
                if not worker.cancelled():
                    error = worker.exception()
                    self.results[key] = {"state": "failed", "code": "memory_schedule_failed"} if error else worker.result()
        capacity = self.reflection.tasks.limits.background_slots - len(self.workers)
        self.continuations.advance(capacity)
        capacity -= len(self.continuations.workers)
        now = self.reflection.tasks.now()
        for row in self.runtime.read_all("SELECT * FROM memory_schedules WHERE enabled = 1 AND next_at <= ? ORDER BY next_at, schedule_id LIMIT ?",
                                        (stamp(now), max(0, capacity))):
            key = row["schedule_id"]
            if key in self.workers:
                continue
            settings = json.loads(row["settings_json"])
            with self.runtime.transaction() as connection:
                changed = connection.execute("UPDATE memory_schedules SET next_at = ? WHERE schedule_id = ? AND next_at = ? AND enabled = 1",
                    (stamp(next_due(now, settings["zone"], settings["local_time"])), key, row["next_at"]))
                if not changed.rowcount:
                    continue
            worker = asyncio.create_task(self._run(dict(row), now), name="companion:memory-daily:" + key)
            self.workers[key] = worker
            worker.add_done_callback(lambda done: self.notify())

    async def _run(self, row, now):
        context = TaskContext.restore(json.loads(row["context_json"]))
        try:
            self.reflection.memory.writer.access.check(context, "memory.reflect")
            settings = json.loads(row["settings_json"])
            batch_id = row["pending_batch"]
            if batch_id is None:
                prepared = require(self.reflection.prepare(context,
                    request_id=row["schedule_id"] + ":" + row["next_at"], start=timestamp(row["covered_until"]), end=now,
                    role_instruction=settings["role_instruction"], schedule_ref={"schedule_id": row["schedule_id"], "revision": row["revision"]}))
                batch_id = prepared["batch_id"]
                with self.runtime.transaction() as connection:
                    connection.execute("UPDATE memory_schedules SET pending_batch = ? WHERE schedule_id = ? AND revision = ?",
                                       (batch_id, row["schedule_id"], row["revision"]))
            outcome = await self.reflection.run(context, batch_id)
            if not outcome.is_success:
                # Unknown calls stay in the durable queue and are never replayed.
                # Detach their window so tomorrow's new sources can still run.
                current = self.reflection._row(context, batch_id)
                if current["state"] in ("running", "failed", "partial"):
                    with self.runtime.transaction() as connection:
                        connection.execute("UPDATE memory_schedules SET pending_batch = NULL WHERE schedule_id = ? AND revision = ? AND pending_batch = ?",
                                           (row["schedule_id"], row["revision"], batch_id))
                return {"state": "blocked", "code": outcome.failure.code, "batch_id": batch_id}
            result = outcome.unwrap()
            self._settle(row, batch_id, result, now)
            return result
        except TaskError as error:
            return {"state": "blocked", "code": error.code}

    def _settle(self, row, batch_id, result, now):
        context = TaskContext.restore(json.loads(row["context_json"]))
        preparing = result["state"] == "selecting"
        if result["state"] in ("failed", "partial") and result.get("repair_batch"):
            child_id = self.reflection._repair_chain(context, batch_id)[-1]
            preparing = self.reflection._row(context, child_id)["state"] == "selecting"
        if preparing:
            with self.runtime.transaction() as connection:
                connection.execute("UPDATE memory_schedules SET next_at = ? WHERE schedule_id = ? AND revision = ? AND pending_batch = ? AND enabled = 1",
                                   (stamp(now + timedelta(seconds=1)), row["schedule_id"], row["revision"], batch_id))
        elif result["state"] in ("complete", "empty"):
            batch = self.reflection._row(context, batch_id)
            manifest = json.loads(batch["manifest_json"])
            continuation = self.reflection.selection.continuation_needed(context, manifest)
            incomplete = (continuation or result["remaining_sources"] or result.get("deferred_sources") or result.get("unavailable_sources"))
            with self.runtime.transaction() as connection:
                changed = connection.execute("""UPDATE memory_schedules SET pending_batch = NULL, covered_until = ?
                    WHERE schedule_id = ? AND revision = ? AND pending_batch = ?""",
                    (row["covered_until"] if incomplete else manifest["end"],
                     row["schedule_id"], row["revision"], batch_id))
                if changed.rowcount and (continuation or result["remaining_sources"]):
                    connection.execute("UPDATE memory_schedules SET next_at = ? WHERE schedule_id = ? AND revision = ? AND enabled = 1",
                                       (stamp(now + timedelta(seconds=5)), row["schedule_id"], row["revision"]))
        elif result["state"] in ("failed", "partial", "running"):
            with self.runtime.transaction() as connection:
                connection.execute("UPDATE memory_schedules SET pending_batch = NULL WHERE schedule_id = ? AND revision = ? AND pending_batch = ?",
                                   (row["schedule_id"], row["revision"], batch_id))
                # A partial/failed batch must not leave next_at at tomorrow's
                # normal slot: the remaining sources would wait a whole day.
                connection.execute("UPDATE memory_schedules SET next_at = ? WHERE schedule_id = ? AND revision = ? AND enabled = 1",
                                   (stamp(now + timedelta(seconds=5)), row["schedule_id"], row["revision"]))

    def next_wake(self):
        if self.closed:
            return None
        if self.recovery.state == "running":
            return self.reflection.tasks.now()
        if len(self.workers) + len(self.continuations.workers) >= self.reflection.tasks.limits.background_slots:
            return None  # Completion wakes the host; due rows must not spin while slots are occupied.
        row = self.runtime.read_one("SELECT MIN(next_at) due FROM memory_schedules WHERE enabled = 1", ())
        times = [timestamp(row["due"])] if row and row["due"] else []
        continuation = self.continuations.next_wake()
        if continuation is not None:
            times.append(continuation)
        return min(times) if times else None

    def cancel_running(self):
        self.recovery.stop()
        self.continuations.cancel_running()
        for worker in self.workers.values():
            worker.cancel()

    @property
    def active_handles(self):
        return (tuple("memory-schedule:" + key for key, worker in self.workers.items() if not worker.done())
                + self.continuations.active_handles)

    async def close(self):
        self.closed = True
        self.cancel_running()
        await self.continuations.close()
        pending_configuration = tuple(task for task in self._configuration_tasks if not task.done())
        refresh = self._configuration_refresh_task
        if refresh is not None and not refresh.done():
            pending_configuration += (refresh,)
        if pending_configuration:
            await asyncio.gather(*pending_configuration, return_exceptions=True)
        if self.workers:
            await asyncio.wait(tuple(self.workers.values()), timeout=0.25)
