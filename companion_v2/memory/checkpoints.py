"""One bounded startup pass over durable local work; never dispatch a model."""

import json

from companion_v2.execution.models import TaskContext, TaskError, require

from . import reflection_recovery as recovery


class MemoryCheckpointRecovery:
    PAGE_SIZE = 8

    def __init__(self, reflection, settle_schedule):
        self.reflection, self.runtime = reflection, reflection.runtime
        self.settle_schedule = settle_schedule
        self.state, self.phase, self.cursor = "idle", 0, 0
        self.bounds = ()
        self.examined = self.recovered = self.blocked = 0
        self.codes = {}

    def _active(self):
        if self.reflection._closed:
            raise TaskError("memory_reflection_closed", "reflection is stopped")
        if self.reflection._require_running:
            self.reflection._require_running()
        require(self.reflection.tasks.check_generation())

    def start(self):
        if self.state != "idle":
            raise ValueError("checkpoint recovery has one startup lifecycle")
        with self.runtime.read_snapshot():
            self._active()
            # Rowid seeks visit each startup row at most once, including blocked
            # rows. No OFFSET, unbounded candidate collection or recurring scan.
            self.bounds = tuple(self.runtime.read_one(f"SELECT COALESCE(MAX(rowid), 0) n FROM {table}", ())["n"]
                                for table in ("memory_reflections", "memory_schedules"))
        self.state = "running"
        self._skip_empty_phases()

    def _skip_empty_phases(self):
        while self.phase < len(self.bounds) and self.bounds[self.phase] == 0:
            self.phase += 1
        if self.phase == len(self.bounds):
            self.state = "complete"

    @property
    def report(self):
        return {"state": self.state, "examined": self.examined, "recovered": self.recovered,
                "blocked": self.blocked, "codes": dict(self.codes)}

    def advance(self):
        if self.state != "running":
            return
        self._active()
        table = ("memory_reflections", "memory_schedules")[self.phase]
        rows = self.runtime.read_all(f"SELECT rowid AS scan_id, * FROM {table} WHERE rowid > ? AND rowid <= ? ORDER BY rowid LIMIT ?",
                                     (self.cursor, self.bounds[self.phase], self.PAGE_SIZE))
        for row in rows:
            self._active()
            try:
                # The generation check and local commit share the write lock.
                # An old host cannot commit after a replacement takes over.
                with self.runtime.transaction():
                    self._active()
                    changed = self._reflection(row) if self.phase == 0 else self._schedule(row)
                self.recovered += int(changed)
            except (TaskError, ValueError, KeyError, TypeError) as error:
                # Lifecycle failure belongs to the host, not a recoverable row.
                self._active()
                code = error.code if isinstance(error, TaskError) else "memory_checkpoint_invalid"
                if self.phase == 0 and code in recovery.STALE_INPUTS:
                    with self.runtime.transaction():
                        self._active()
                        recovery.quarantine_stale(self.reflection, TaskContext.restore(json.loads(row["context_json"])),
                                                  row["batch_id"], code)
                self.blocked += 1
                self.codes[code] = self.codes.get(code, 0) + 1
            self.cursor = row["scan_id"]
            self.examined += 1
        if len(rows) < self.PAGE_SIZE or self.cursor >= self.bounds[self.phase]:
            self.phase += 1
            self.cursor = 0
            self._skip_empty_phases()

    def _reflection(self, row):
        if row["state"] in ("complete", "empty"):
            parent_id = json.loads(row["manifest_json"]).get("retry_of")
            parent = self.runtime.read_one("SELECT state FROM memory_reflections WHERE batch_id = ?", (parent_id,)) if parent_id else None
            if parent is None or parent["state"] == "complete":
                return False
        elif row["state"] == "running":
            # A restart leaves an in-flight model call unknown. Settle the batch
            # after the grace period so it stops reading as still running.
            context = TaskContext.restore(json.loads(row["context_json"]))
            return recovery.abandon_stale_running(self.reflection, context, row["batch_id"],
                                                  self.reflection.tasks.now())
        elif row["state"] not in ("received", "modelled"):
            return False
        context = TaskContext.restore(json.loads(row["context_json"]))
        current = self.reflection._row(context, row["batch_id"])
        recovery.ensure_checkpoint_stopped(self.reflection, current)
        if current["state"] == "received":
            recovery.consume(self.reflection, context, row["batch_id"])
        elif current["state"] == "modelled":
            require(self.reflection.apply(context, row["batch_id"]))
        # Do not call run/_after_run: either can authorize a new model repair.
        recovery.finish_parent(self.reflection, context, row["batch_id"])
        return True

    def _schedule(self, row):
        if row["pending_batch"] is None:
            return False
        context = TaskContext.restore(json.loads(row["context_json"]))
        batch = self.reflection._row(context, row["pending_batch"])
        reference = json.loads(batch["manifest_json"]).get("schedule_ref")
        if reference != {"schedule_id": row["schedule_id"], "revision": row["revision"]}:
            raise TaskError("memory_schedule_changed", "the checkpoint belongs to an earlier schedule")
        recovery.current_schedule(self.reflection, context, reference)
        if batch["state"] not in ("selecting", "complete", "empty", "failed", "partial", "running"):
            return False
        if batch["state"] in ("complete", "empty"):
            recovery.ensure_checkpoint_stopped(self.reflection, batch)
        result = require(self.reflection.status(context, row["pending_batch"]))
        self.settle_schedule(row, row["pending_batch"], result, self.reflection.tasks.now())
        return True

    def stop(self):
        if self.state == "running":
            self.state = "stopped"
