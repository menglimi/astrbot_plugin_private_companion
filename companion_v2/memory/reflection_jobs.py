"""Host-driven continuation of explicitly requested, durable reflection work."""

import asyncio
from datetime import timedelta
import json

from companion_v2.execution.models import TaskContext, TaskError, digest, encode, require, stamp
from companion_v2.foundation.refs import SubjectKind, SubjectRef
from companion_v2.identity_policy.models import AuthenticatedHostPrincipal, ResolvedIdentity

from .management import MemoryManagement
from .models import owner_key, timestamp


# This owner's retry policy: refusals that left nothing running and may simply
# clear on their own. Only `advance()` picks `queued` rows back up, so a blocked
# row never retries. Deliberately not the same list as
# `execution.runner.CAPACITY_REFUSALS`: that one decides what the runner keeps
# queued, this one decides what this job tries again.
RETRYABLE = ("memory_index_busy", "memory_index_unavailable", "task_queue_full",
             "memory_task_worker_not_stopped", "task_lane_full", "task_concurrency_limited")


def describe_job(row):
    return {key: row[key] for key in ("job_id", "batch_id", "state", "next_at", "result_code", "created_at", "updated_at")}


class MemoryReflectionJobs:
    def __init__(self, reflection, notify):
        self.reflection, self.runtime, self.notify = reflection, reflection.runtime, notify
        self.workers = {}
        self.closed = False

    def _active(self):
        if self.closed or self.reflection._closed:
            raise TaskError("memory_reflection_closed", "reflection continuation is stopped")
        if self.reflection._require_running:
            self.reflection._require_running()
        require(self.reflection.tasks.check_generation())

    def enqueue(self, context, operator, *, batch_id, request_id):
        """Caller prepares the original batch and registers this intent atomically."""
        self._active()
        MemoryManagement(self.reflection.memory, operator).writer.access.check(context)
        self.reflection._row(context, batch_id)
        job_id = "memory-continue-" + digest([operator.subject, context.key, request_id])
        with self.runtime.transaction() as db:
            self._active()
            existing = db.execute("SELECT * FROM memory_reflection_jobs WHERE job_id = ?", (job_id,)).fetchone()
            if existing is not None:
                if existing["batch_id"] != batch_id or TaskContext.restore(json.loads(existing["context_json"])).key != context.key:
                    raise TaskError("memory_repair_conflict", "continuation key belongs to another original batch")
                return describe_job(existing)
            now = stamp(self.reflection.tasks.now())
            db.execute("INSERT INTO memory_reflection_jobs VALUES (?, ?, ?, ?, ?, 'queued', ?, NULL, ?, ?)",
                (job_id, batch_id, owner_key(context), encode(context), encode(operator), now, now, now))
            result = describe_job(db.execute("SELECT * FROM memory_reflection_jobs WHERE job_id = ?", (job_id,)).fetchone())
        self.notify()
        return result

    def receipt(self, context, operator, request_id):
        MemoryManagement(self.reflection.memory, operator).writer.access.check(context)
        job_id = "memory-continue-" + digest([operator.subject, context.key, request_id])
        row = self.runtime.read_one("SELECT * FROM memory_reflection_jobs WHERE job_id = ? AND owner_key = ?", (job_id, owner_key(context)))
        if row is None:
            return None
        if TaskContext.restore(json.loads(row["context_json"])).key != context.key:
            raise TaskError("memory_reflection_unavailable", "continuation belongs to another authenticated context")
        return describe_job(row)

    def _operator(self, row):
        raw = json.loads(row["operator_json"])
        subject = SubjectRef(raw["subject"]["installation_id"], SubjectKind(raw["subject"]["kind"]), raw["subject"]["subject_id"])
        return ResolvedIdentity(AuthenticatedHostPrincipal(**raw["principal"]), subject,
                                raw["binding_id"], raw["binding_revision"], raw["policy_revision"])

    def _excluded(self):
        batches = tuple(batch_id for batch_id, _ in self.workers.values())
        return (" AND batch_id NOT IN (" + ",".join("?" for _ in batches) + ")", batches) if batches else ("", ())

    def advance(self, capacity):
        if self.closed:
            return
        self._active()
        for job_id, (_, worker) in tuple(self.workers.items()):
            if worker.done():
                if not worker.cancelled():
                    worker.exception()
                del self.workers[job_id]
        capacity = max(0, capacity - len(self.workers))
        if not capacity:
            return
        # Query only due intents. Historical reflections and completed jobs are
        # not decoded on every wake. Exclude active batches before taking LIMIT.
        excluded, arguments = self._excluded()
        rows = self.runtime.read_all("SELECT * FROM memory_reflection_jobs WHERE state = 'queued' AND next_at <= ?" +
            excluded + " ORDER BY next_at, job_id LIMIT ?", (stamp(self.reflection.tasks.now()), *arguments, capacity))
        batches = {batch_id for batch_id, _ in self.workers.values()}
        for row in rows:
            if row["batch_id"] in batches:
                continue
            batches.add(row["batch_id"])
            worker = asyncio.create_task(self._run(dict(row)), name="companion:memory-continuation:" + row["job_id"])
            self.workers[row["job_id"]] = row["batch_id"], worker
            worker.add_done_callback(lambda done: self.notify())

    async def _run(self, row):
        state, code, delay = "blocked", "memory_continuation_failed", 0
        try:
            self._active()
            context = TaskContext.restore(json.loads(row["context_json"]))
            MemoryManagement(self.reflection.memory, self._operator(row)).writer.access.check(context)
            result = await self.reflection.run(context, row["batch_id"])
            if result.is_success:
                chain = self.reflection._repair_chain(context, row["batch_id"])
                current = self.reflection._row(context, chain[-1])
                if current["state"] in ("complete", "empty"):
                    state, code = "complete", None
                elif current["state"] in ("selecting", "prepared", "received", "modelled"):
                    code = json.loads(current["manifest_json"]).get("selection", {}).get("blocked_code")
                    state, delay = "queued", 5 if code else 1
                else:
                    code = "memory_reflection_uncertain" if current["state"] == "running" else "memory_reflection_failed"
            else:
                code = result.failure.code
                if code in RETRYABLE:
                    state, delay = "queued", 5
        except TaskError as error:
            code = error.code
        except (ValueError, KeyError, TypeError):
            code = "memory_continuation_invalid"
        except Exception:
            code = "memory_continuation_failed"
        # Cancellation leaves the original intent queued. On restart run()
        # resumes local checkpoints, but refuses to replay an unknown model call.
        with self.runtime.transaction() as db:
            self._active()
            now = self.reflection.tasks.now()
            db.execute("""UPDATE memory_reflection_jobs SET state = ?, result_code = ?, next_at = ?, updated_at = ?
                WHERE job_id = ? AND state = 'queued'""",
                (state, code, stamp(now + timedelta(seconds=delay)), stamp(now), row["job_id"]))

    def next_wake(self):
        if self.closed:
            return None
        excluded, arguments = self._excluded()
        row = self.runtime.read_one("SELECT MIN(next_at) due FROM memory_reflection_jobs WHERE state = 'queued'" + excluded, arguments)
        return timestamp(row["due"]) if row and row["due"] else None

    @property
    def active_handles(self):
        return tuple("memory-continuation:" + key for key, (_, worker) in self.workers.items() if not worker.done())

    def cancel_running(self):
        for _, worker in self.workers.values():
            worker.cancel()

    async def close(self):
        self.closed = True
        self.cancel_running()
        if self.workers:
            await asyncio.wait(tuple(worker for _, worker in self.workers.values()), timeout=0.25)
