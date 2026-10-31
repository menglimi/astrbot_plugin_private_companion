"""SQLite task admission and fencing, independent of an asyncio loop or host.

Registration, dispatch and stop acknowledgements are trusted runtime APIs, never
model tools. A fence prevents new writes; it does not stop an old network request.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from contextlib import contextmanager
import contextvars
from datetime import datetime, timedelta, timezone
from functools import wraps
import json
import math
from logging import getLogger
from types import MappingProxyType
from typing import Callable
from uuid import uuid4

from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import ScopeKind, normalize_utc
from companion_v2.identity_policy.object_access import ObjectAccessPolicy
from companion_v2.identity_policy.personas import PersonaDirectory
from companion_v2.identity_policy.repository import PersistentIdentityDirectory
from companion_v2.storage.runtime import StorageRuntime

from .models import (
    ClaimResult, Lane, QUEUED, SideEffect, SupervisorLimits, TERMINAL, TaskClaim, TaskContext,
    TaskError, TaskRecord, TaskRegistration, TaskResult, TaskSpec, WorkerAttempt, WorkerOwnership,
    WorkerStopRecovery, digest, encode, require, stamp,
)


logger = getLogger(__name__)


def outcome(method):
    @wraps(method)
    def call(*args, **kwargs):
        try:
            return Outcome.success(method(*args, **kwargs))
        except TaskError as error:
            return Outcome.failed(error.code, str(error))
    return call


@dataclass
class _ReadOnlyAccess:
    active: bool = True


class TaskSupervisor:
    def __init__(self, runtime: StorageRuntime, registrations: tuple[TaskRegistration, ...], *,
                 limits: SupervisorLimits = SupervisorLimits(), now: Callable[[], datetime] | None = None):
        self.runtime = runtime
        self.identity = PersistentIdentityDirectory(runtime)
        self.policy = ObjectAccessPolicy(runtime)
        entries = {entry.definition.kind: entry for entry in registrations}
        # This is a trusted startup catalog, not the number of running tasks.
        # Optional domains can legitimately bring the catalog above 64 kinds;
        # execution capacity remains governed by SupervisorLimits.
        if not entries or len(entries) != len(registrations):
            raise ValueError("register nonempty unique task kinds")
        self.registrations = MappingProxyType(entries)
        self.limits = limits
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.generation: int | None = None
        self.worker_lifecycle = None
        # A dashboard preview may rebuild an already persisted prompt after a
        # process restart. It may recheck scope/source authority, but it must
        # never acquire or renew an execution lease. Context-local state keeps
        # this read-only exception from weakening concurrent workers.
        self._read_only_access = contextvars.ContextVar("companion_task_read_only_access", default=None)

    @contextmanager
    def read_only_context(self):
        """Recheck persisted materials synchronously without granting execution authority."""
        with self.runtime.read_only_snapshot():
            if self.generation is not None:
                self._active()
            access = _ReadOnlyAccess()
            token = self._read_only_access.set(access)
            try:
                yield
            finally:
                # Context copies (including child tasks) must not retain this
                # exception after the SQLite read-only snapshot has ended.
                access.active = False
                self._read_only_access.reset(token)

    def _reading_only(self):
        access = self._read_only_access.get()
        return access is not None and access.active

    def bind_worker_lifecycle(self, lifecycle):
        """Install trusted host evidence before this supervisor starts accepting work."""
        if self.generation is not None or self.worker_lifecycle is not None:
            raise ValueError("worker lifecycle must be bound once before startup")
        if not all(callable(getattr(lifecycle, method, None)) for method in ("capture", "stopped_proof")):
            raise ValueError("worker lifecycle requires ownership and termination evidence")
        self.worker_lifecycle = lifecycle

    def _now(self):
        return normalize_utc(self.now(), field="supervisor clock")

    def _active(self):
        row = self.runtime.read_one("SELECT * FROM task_supervisor WHERE singleton = 1", ())
        if row is None or self.generation is None or row["generation"] != self.generation:
            raise TaskError("supervisor_fenced", "this supervisor has not started or has been replaced")
        if row["limits_json"] != encode(self.limits):
            raise TaskError("supervisor_limits_changed", "shared quota configuration differs")
        return row

    @outcome
    def start(self, *, expected_generation: int) -> int:
        if type(expected_generation) is not int or expected_generation < 0:
            raise ValueError("expected generation must be a nonnegative integer")
        with self.runtime.transaction() as connection:
            row = connection.execute("SELECT * FROM task_supervisor WHERE singleton = 1").fetchone()
            if (row["generation"] if row else 0) != expected_generation:
                raise TaskError("supervisor_generation_conflict", "another supervisor already started")
            if row and row["limits_json"] != encode(self.limits):
                previous = json.loads(row["limits_json"])
                current = asdict(self.limits)
                capacity_only_increase = (
                    set(previous) == set(current)
                    and all(type(previous[key]) is int and previous[key] <= value
                            for key, value in current.items())
                )
                retained = connection.execute("SELECT COUNT(*) FROM execution_tasks").fetchone()[0]
                if not capacity_only_increase or retained > current["retained_tasks"]:
                    raise TaskError("supervisor_limits_changed", "only safe task capacity increases can be adopted at startup")
                # A larger lane or queue budget does not invalidate retained
                # work. The new generation still fences previous supervisors;
                # existing attempts continue to hold their original slots.
            generation = expected_generation + 1
            connection.execute("""INSERT INTO task_supervisor VALUES (1, ?, ?, ?)
                ON CONFLICT(singleton) DO UPDATE SET generation = excluded.generation,
                limits_json = excluded.limits_json,
                started_at = excluded.started_at""", (generation, encode(self.limits), stamp(self._now())))
        self.generation = generation
        return generation

    @outcome
    def check_generation(self) -> int:
        """Trusted lifecycle check without admitting, claiming or renewing any work."""
        with self.runtime.read_snapshot():
            self._active()
            return self.generation

    @outcome
    def discard_queued(self, task_id: str, *, reason: str, code: str | None = None) -> TaskRecord:
        """Owner lifecycle cleanup; never cancel or release a potentially running attempt.

        ``code`` carries the actual cause when the owner knows it, so the task
        ledger and every receipt derived from it stay diagnosable instead of
        collapsing unrelated refusals into a generic owner shutdown.
        """
        if reason not in ("owner_closed", "owner_expired", "owner_unavailable"):
            raise ValueError("unsupported owner cleanup reason")
        if code is not None:
            from companion_v2.identity_policy.models import _opaque
            _opaque(code, field="task discard code")
        with self.runtime.transaction():
            self._active()
            row = self._row(task_id)
            if row["state"] in QUEUED:
                row = self._change(row, "cancelled", code or reason, self._now())
            return self._record(row)

    @outcome
    def attach(self, *, expected_generation: int) -> int:
        """Attach another local worker connection without taking over the controller."""
        if self._reading_only():
            raise TaskError("supervisor_read_only", "a persisted projection cannot attach a worker")
        with self.runtime.read_snapshot():
            row = self.runtime.read_one("SELECT * FROM task_supervisor WHERE singleton = 1", ())
            if row is None or row["generation"] != expected_generation or row["limits_json"] != encode(self.limits):
                raise TaskError("supervisor_generation_conflict", "no matching active controller")
            self.generation = row["generation"]
            return self.generation

    def _context(self, context: TaskContext, now: datetime):
        current = require(self.identity.resolve_authenticated(context.actor.principal, observed_at=now))
        if (current.subject, current.binding_id, current.binding_revision) != (
                context.actor.subject, context.actor.binding_id, context.actor.binding_revision):
            raise TaskError("binding_rebound", "task belongs to the original account binding")
        principal, scope = current.principal, context.scope
        if (scope.installation_id != self.runtime.installation_id or
                (principal.platform, principal.bot_id, principal.account_id) !=
                (scope.platform, scope.bot_id, scope.account_id)):
            raise TaskError("task_context_mismatch", "task belongs to a different bot or account route")
        if require(self.identity.resolve_role(scope, observed_at=now)) != context.role_binding:
            raise TaskError("role_binding_changed", "task must retain the original persona revision")
        route = PersonaDirectory(self.runtime).route(scope)
        if scope.kind is ScopeKind.GROUP:
            from companion_v2.identity_policy.group_audience import group_audience
            if (route is None or route.user is not None or route.group_id != scope.native_scope_id
                    or context.audience != group_audience(scope, current.subject)):
                raise TaskError("group_task_audience_required", "group tasks require the registered group and collective audience")
            return
        if (scope.kind is not ScopeKind.PRIVATE or route is None or route.user != current.subject
                or context.audience.recipients != (current.subject,)):
            raise TaskError("private_task_required", "this slice requires the original private conversation")

    @staticmethod
    def _scope_action(context, action):
        if context.scope.kind is ScopeKind.GROUP and action not in (
                "conversation.reply", "configuration.read", "text", "extension.tool.invoke",
                "role.expression.read"):
            raise TaskError("group_action_unavailable", "this action is unavailable in the group profile")

    def _registration(self, row):
        data = json.loads(row["record_json"])
        entry = self.registrations.get(row["kind"])
        if entry is None or digest(entry.definition) != data["definition_hash"]:
            raise TaskError("task_definition_changed", "original task implementation or provider binding is unavailable")
        return entry, data

    @outcome
    def check_context_access(self, context: TaskContext, *, action: str, observed_at: datetime):
        """Read-only authority preflight; this never grants a worker execution lease."""
        with self.runtime.read_snapshot():
            self._context(context, observed_at)
            self._scope_action(context, action)
            return require(self.policy.scope_access(context.actor, scope=context.scope, action=action,
                                                   observed_at=observed_at, allow_private_owner=True))

    @outcome
    def authorize_context(self, context: TaskContext, *, action: str, observed_at: datetime):
        """Shared current private scope checks for task-owning local services."""
        with self.runtime.read_snapshot():
            if self.generation is not None or not self._reading_only():
                self._active()
            return require(self.check_context_access(context, action=action, observed_at=observed_at))

    def _authorize(self, row, now: datetime, *, inputs: bool = True):
        entry, data = self._registration(row)
        spec = TaskSpec.restore(data["spec"])
        self._context(spec.context, now)
        self._scope_action(spec.context, entry.definition.action)
        require(self.policy.scope_access(spec.context.actor, scope=spec.context.scope,
                                        action=entry.definition.action, observed_at=now, allow_private_owner=True))
        if inputs:
            self._validate_inputs(entry, spec, now)
        return entry, data, spec

    @staticmethod
    def _validate_inputs(entry, spec, now):
        try:
            result = entry.validate(spec, now)
            if not isinstance(result, Outcome):
                raise TypeError("owner validation must return Outcome")
        except Exception as error:
            raise TaskError("task_validation_failed", "task owner validation failed; private error details omitted") from error
        require(result)

    @staticmethod
    def _record(row) -> TaskRecord:
        data = json.loads(row["record_json"])
        return TaskRecord(row["task_id"], row["revision"], row["state"], row["generation"], row["attempt_no"],
                          row["attempt_id"], TaskSpec.restore(data["spec"]), data.get("operation_id"),
                          data.get("result_ref"), data["effect"], data["reason"])

    def _row(self, task_id):
        row = self.runtime.read_one("SELECT * FROM execution_tasks WHERE task_id = ?", (task_id,))
        if row is None:
            raise TaskError("task_unavailable", "task is not available in this context")
        return row

    def _owned(self, context, task_id, now):
        self._context(context, now)
        row = self._row(task_id)
        if row["origin_key"] != context.key:
            raise TaskError("task_unavailable", "task is not available in this context")
        self._authorize(row, now, inputs=False)
        return row

    def _change(self, row, state, reason, now, *, data=None, **changes):
        data = json.loads(row["record_json"]) if data is None else data
        data["reason"] = reason
        values = {key: row[key] for key in ("generation", "attempt_no", "attempt_id", "next_run_at")}
        values.update(changes)
        with self.runtime.transaction() as connection:
            updated = connection.execute("""UPDATE execution_tasks SET revision = revision + 1,
                state = ?, generation = ?, attempt_no = ?, attempt_id = ?, next_run_at = ?, updated_at = ?,
                record_json = ? WHERE task_id = ? AND revision = ?""",
                (state, values["generation"], values["attempt_no"], values["attempt_id"], values["next_run_at"],
                 stamp(now), encode(data), row["task_id"], row["revision"]))
            if updated.rowcount != 1:
                raise TaskError("task_revision_conflict", "task changed before this transition")
            connection.execute("INSERT INTO execution_events VALUES (?, ?, ?, ?, ?)",
                               (row["task_id"], row["revision"] + 1, state, reason, stamp(now)))
            return self._row(row["task_id"])

    @outcome
    def submit(self, spec: TaskSpec) -> TaskRecord:
        with self.runtime.transaction() as connection:
            self._active()
            now = self._now()
            self._context(spec.context, now)
            entry = self.registrations.get(spec.kind)
            if entry is None:
                raise TaskError("task_kind_unregistered", "task kind is not installed")
            definition = entry.definition
            self._scope_action(spec.context, definition.action)
            require(self.policy.scope_access(spec.context.actor, scope=spec.context.scope,
                                            action=definition.action, observed_at=now, allow_private_owner=True))
            # Generations, attempts and proposed task IDs cannot create a second logical action.
            dedupe = digest([definition.owner, spec.kind, spec.context.scope.key, spec.context.actor.subject,
                             spec.context.role_binding.role, spec.idempotency_key])
            request_hash = digest([definition, spec.context.key, spec.kind, spec.request_ref,
                                   spec.idempotency_key, spec.not_before, spec.deadline_at])
            prior = connection.execute("SELECT * FROM execution_tasks WHERE dedupe_key = ?", (dedupe,)).fetchone()
            if prior:
                if prior["request_hash"] != request_hash:
                    raise TaskError("task_idempotency_conflict", "same logical key has different inputs or authority")
                return self._record(prior)
            if connection.execute("SELECT 1 FROM execution_tasks WHERE task_id = ?", (spec.task_id,)).fetchone():
                raise TaskError("task_id_conflict", "task ID already exists")
            if not now < spec.deadline_at <= now + timedelta(days=7):
                raise TaskError("task_deadline_invalid", "new tasks require a future deadline within seven days")
            total = connection.execute("SELECT COUNT(*) FROM execution_tasks").fetchone()[0]
            queued = connection.execute("""SELECT COUNT(*) FROM execution_tasks
                WHERE lane = ? AND state IN ('created', 'admitted', 'running', 'retry_scheduled', 'cancelling')""",
                (definition.lane.value,)).fetchone()[0]
            if total >= self.limits.retained_tasks or queued >= self.limits.queued_per_lane:
                raise TaskError("task_queue_full", "bounded task storage or this lane's queue is full")
            self._validate_inputs(entry, spec, now)
            data = {"spec": asdict(spec), "definition_hash": digest(definition), "definition": asdict(definition),
                    "scope_key": spec.context.scope.key, "effect": "none", "reason": "queued",
                    "operation_id": None, "result_ref": None}
            connection.execute("INSERT INTO execution_tasks VALUES (?, ?, ?, ?, ?, ?, 1, 'created', ?, 0, NULL, ?, ?, ?, ?, ?)",
                (spec.task_id, spec.context.key, spec.kind, definition.lane.value, dedupe, request_hash, self.generation,
                 stamp(max(now, spec.not_before)), stamp(spec.deadline_at), stamp(now), stamp(now), encode(data)))
            connection.execute("INSERT INTO execution_events VALUES (?, 1, 'created', 'queued', ?)", (spec.task_id, stamp(now)))
            return self._record(self._row(spec.task_id))

    @outcome
    def read(self, context: TaskContext, task_id: str) -> TaskRecord:
        with self.runtime.read_snapshot():
            return self._record(self._owned(context, task_id, self._now()))

    @outcome
    def claim(self, lane: Lane, *, scan_limit: int = 32) -> ClaimResult:
        if not isinstance(lane, Lane) or type(scan_limit) is not int or not 1 <= scan_limit <= 100:
            raise ValueError("claim requires a lane and a bounded scan")
        with self.runtime.transaction() as connection:
            self._active()
            now = self._now()
            held = connection.execute("SELECT COUNT(*) FROM execution_attempts WHERE slot_held = 1 AND lane = ?",
                                      (lane.value,)).fetchone()[0]
            capacity = self.limits.foreground_slots if lane is Lane.FOREGROUND else self.limits.background_slots
            if held >= capacity:
                return ClaimResult(None)
            rows = connection.execute("""SELECT * FROM execution_tasks t WHERE lane = ?
                AND state IN ('created', 'retry_scheduled') AND next_run_at <= ?
                AND (SELECT COUNT(*) FROM execution_attempts a WHERE a.kind = t.kind AND a.slot_held = 1)
                    < json_extract(t.record_json, '$.definition.concurrency')
                AND (SELECT COUNT(*) FROM execution_attempts a WHERE a.kind = t.kind AND a.slot_held = 1
                     AND a.scope_key = json_extract(t.record_json, '$.scope_key'))
                    < json_extract(t.record_json, '$.definition.per_scope_concurrency')
                ORDER BY next_run_at, task_id LIMIT ?""", (lane.value, stamp(now), scan_limit)).fetchall()
            for row in rows:
                if row["deadline_at"] <= stamp(now):
                    self._change(row, "expired", "deadline_before_start", now)
                    continue
                try:
                    entry, data, spec = self._authorize(row, now)
                except TaskError as error:
                    self._change(row, "failed", error.code, now)
                    continue
                definition = entry.definition
                if row["attempt_no"] >= definition.max_attempts:
                    self._change(row, "dead_letter", "attempt_budget_exhausted", now)
                    continue
                slots = connection.execute("""SELECT COUNT(*) AS total,
                    COALESCE(SUM(scope_key = ?), 0) AS scope_total FROM execution_attempts
                    WHERE slot_held = 1 AND kind = ?""", (spec.context.scope.key, row["kind"])).fetchone()
                if slots["total"] >= definition.concurrency or slots["scope_total"] >= definition.per_scope_concurrency:
                    continue
                attempt_id = uuid4().hex
                lease = min(spec.deadline_at, now + timedelta(seconds=definition.timeout_seconds))
                attempt = {"claimed_at": stamp(now), "started_at": None, "finished_at": None,
                           "effect_started": False, "queue_ms": max(0.0, (now - datetime.fromisoformat(row["next_run_at"])).total_seconds() * 1000),
                           "execution_ms": None, "stopped_proof": None}
                connection.execute("INSERT INTO execution_attempts VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
                    (attempt_id, row["task_id"], row["attempt_no"] + 1, self.generation, lane.value,
                     row["kind"], row["origin_key"], spec.context.scope.key, stamp(lease), encode(attempt)))
                self._change(row, "admitted", "claimed", now, generation=self.generation,
                             attempt_no=row["attempt_no"] + 1, attempt_id=attempt_id)
                return ClaimResult(TaskClaim(row["task_id"], attempt_id, self.generation))
            return ClaimResult(None)

    @outcome
    def claim_task(self, context: TaskContext, task_id: str) -> ClaimResult:
        """Claim one trusted latency-critical child without consuming another queued task.

        A refusal names the transient condition that left the row queued; only the
        deadline, authorization and attempt-budget transitions settle the row here.
        """
        with self.runtime.transaction() as connection:
            self._active()
            now = self._now()
            row = self._owned(context, task_id, now)
            if row["state"] not in QUEUED:
                return ClaimResult(None, "task_not_queued")
            if row["next_run_at"] > stamp(now):
                return ClaimResult(None, "task_not_due")
            if row["deadline_at"] <= stamp(now):
                self._change(row, "expired", "deadline_before_start", now)
                return ClaimResult(None)
            lane = Lane(row["lane"])
            held = connection.execute("SELECT COUNT(*) FROM execution_attempts WHERE slot_held = 1 AND lane = ?",
                                      (lane.value,)).fetchone()[0]
            capacity = self.limits.foreground_slots if lane is Lane.FOREGROUND else self.limits.background_slots
            if held >= capacity:
                return ClaimResult(None, "task_lane_full")
            try:
                entry, data, spec = self._authorize(row, now)
            except TaskError as error:
                self._change(row, "failed", error.code, now)
                return ClaimResult(None)
            definition = entry.definition
            if row["attempt_no"] >= definition.max_attempts:
                self._change(row, "dead_letter", "attempt_budget_exhausted", now)
                return ClaimResult(None)
            slots = connection.execute("""SELECT COUNT(*) AS total,
                COALESCE(SUM(scope_key = ?), 0) AS scope_total FROM execution_attempts
                WHERE slot_held = 1 AND kind = ?""", (spec.context.scope.key, row["kind"])).fetchone()
            if slots["total"] >= definition.concurrency or slots["scope_total"] >= definition.per_scope_concurrency:
                return ClaimResult(None, "task_concurrency_limited")
            attempt_id = uuid4().hex
            lease = min(spec.deadline_at, now + timedelta(seconds=definition.timeout_seconds))
            attempt = {"claimed_at": stamp(now), "started_at": None, "finished_at": None,
                       "effect_started": False,
                       "queue_ms": max(0.0, (now - datetime.fromisoformat(row["next_run_at"])).total_seconds() * 1000),
                       "execution_ms": None, "stopped_proof": None}
            connection.execute("INSERT INTO execution_attempts VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
                (attempt_id, row["task_id"], row["attempt_no"] + 1, self.generation, lane.value,
                 row["kind"], row["origin_key"], spec.context.scope.key, stamp(lease), encode(attempt)))
            self._change(row, "admitted", "claimed", now, generation=self.generation,
                         attempt_no=row["attempt_no"] + 1, attempt_id=attempt_id)
            return ClaimResult(TaskClaim(row["task_id"], attempt_id, self.generation))

    def _attempt(self, claim: TaskClaim):
        attempt = self.runtime.read_one("SELECT * FROM execution_attempts WHERE attempt_id = ? AND task_id = ? AND generation = ?",
                                       (claim.attempt_id, claim.task_id, claim.generation))
        if attempt is None:
            raise TaskError("task_attempt_unknown", "attempt does not belong to this task and generation")
        return attempt

    def _fenced(self, claim, now, *, cancelling=False):
        self._active()
        row, attempt = self._row(claim.task_id), self._attempt(claim)
        if (claim.generation != self.generation or row["generation"] != claim.generation
                or row["attempt_id"] != claim.attempt_id or row["state"] in TERMINAL or not attempt["slot_held"]):
            raise TaskError("task_attempt_fenced", "attempt may not change current task state")
        if row["state"] == "cancelling" and not cancelling:
            raise TaskError("task_cancellation_requested", "stop before creating another effect")
        if min(attempt["lease_until"], row["deadline_at"]) <= stamp(now):
            raise TaskError("task_attempt_expired", "attempt lease or logical deadline expired")
        return row, attempt

    def _save_attempt(self, attempt, data, *, stopped=False, release_capacity=False):
        if stopped and release_capacity:
            raise ValueError("stopped evidence already releases execution capacity")
        with self.runtime.transaction() as connection:
            held = 0 if stopped or release_capacity else attempt["slot_held"]
            connection.execute("UPDATE execution_attempts SET record_json = ?, slot_held = ? WHERE attempt_id = ?",
                               (encode(data), held, attempt["attempt_id"]))

    @outcome
    def begin(self, claim: TaskClaim) -> TaskRecord:
        with self.runtime.transaction():
            now = self._now()
            row, attempt = self._fenced(claim, now)
            if row["state"] != "admitted":
                raise TaskError("task_already_started", "claim can begin only once")
            self._authorize(row, now)
            data = json.loads(attempt["record_json"])
            data["started_at"] = stamp(now)
            if self.worker_lifecycle is not None:
                ownership = self.worker_lifecycle.capture()
                if not isinstance(ownership, WorkerOwnership):
                    raise TypeError("host worker ownership must be a validated record")
                data["worker_owner"] = asdict(ownership)
            self._save_attempt(attempt, data)
            return self._record(self._change(row, "running", "started", now))

    @outcome
    def validate(self, claim: TaskClaim) -> TaskRecord:
        with self.runtime.read_snapshot():
            row, _ = self._fenced(claim, self._now())
            self._authorize(row, self._now())
            return self._record(row)

    @outcome
    def begin_effect(self, claim: TaskClaim) -> TaskRecord:
        """Commit the uncertainty barrier BEFORE calling an effectful domain owner."""
        with self.runtime.transaction():
            now = self._now()
            row, attempt = self._fenced(claim, now)
            entry, data, _ = self._authorize(row, now)
            if row["state"] != "running" or entry.definition.side_effect is SideEffect.NONE:
                raise TaskError("task_effect_not_declared", "this task cannot submit an effect")
            attempt_data = json.loads(attempt["record_json"])
            if attempt_data["effect_started"]:
                raise TaskError("task_effect_already_started", "an attempt may submit its atomic effect only once")
            attempt_data["effect_started"] = True
            self._save_attempt(attempt, attempt_data)
            data["effect"] = "unknown"
            return self._record(self._change(row, "running", "effect_boundary", now, data=data))

    @outcome
    def record_operation(self, claim: TaskClaim, operation_id: str) -> TaskRecord:
        """Record an actual owner-issued acceptance, never allocate an operation ID."""
        from companion_v2.identity_policy.models import _opaque
        _opaque(operation_id, field="operation_id")
        with self.runtime.transaction():
            now = self._now()
            row, attempt = self._fenced(claim, now)
            _, data, _ = self._authorize(row, now)
            if not json.loads(attempt["record_json"])["effect_started"]:
                raise TaskError("task_effect_not_started", "owner acceptance requires the original submit boundary")
            if data["operation_id"] not in (None, operation_id):
                raise TaskError("task_operation_conflict", "retry must retain the owner-issued operation ID")
            if data["operation_id"] == operation_id:
                return self._record(row)
            data["operation_id"] = operation_id
            return self._record(self._change(row, row["state"], "owner_accepted", now, data=data))

    def _result(self, row, attempt, result, now, *, execution_ms=None, inputs=True):
        entry, data, _ = self._authorize(row, now, inputs=inputs)
        attempt_data = json.loads(attempt["record_json"])
        effectful = entry.definition.side_effect is not SideEffect.NONE
        if not effectful and (result.effect != "none" or result.operation_id is not None):
            raise TaskError("task_effect_not_declared", "read-only task cannot report an external or local commit")
        if effectful:
            if result.effect == "committed" and not attempt_data["effect_started"]:
                raise TaskError("task_effect_not_started", "commit cannot precede the submit boundary")
            if attempt_data["effect_started"] and result.effect == "none":
                raise TaskError("task_effect_unproven", "submitted work needs an owner receipt or unknown effect")
            if result.status == "succeeded" and result.effect != "committed":
                raise TaskError("task_commit_unproven", "effectful success needs the actual commit receipt")
        if data["operation_id"] is not None and result.operation_id not in (None, data["operation_id"]):
            raise TaskError("task_operation_conflict", "result belongs to a different owner operation")
        data.update(effect=result.effect, result_ref=result.result_ref, receipt_ref=result.receipt_ref,
                    operation_id=result.operation_id or data["operation_id"])
        state, reason = result.status, "owner_result"
        if data.get("cancellation_requested") and result.effect in ("none", "not_committed"):
            state, reason = "cancelled", "stopped_without_commit"
        elif result.retryable and result.effect in ("none", "not_committed"):
            next_run = now + timedelta(seconds=entry.definition.backoff_seconds)
            if next_run >= datetime.fromisoformat(row["deadline_at"]):
                state, reason = "expired", "retry_would_exceed_deadline"
            elif row["attempt_no"] >= entry.definition.max_attempts:
                state, reason = "dead_letter", "attempt_budget_exhausted"
            else:
                # This task already owns its queue reservation; retries cannot be dropped.
                state, reason = "retry_scheduled", "known_safe_retry"
                data["result_ref"] = None
        if attempt_data["finished_at"] is None:
            attempt_data.update(finished_at=stamp(now), execution_ms=execution_ms, stopped_proof="worker_returned")
        attempt_data["owner_result"] = asdict(result)
        attempt_data["operation_id"] = data["operation_id"]
        attempt_data["result_recorded_at"] = stamp(now)
        self._save_attempt(attempt, attempt_data, stopped=True)
        return self._change(row, state, reason, now, data=data,
                            next_run_at=stamp(next_run) if state == "retry_scheduled" else row["next_run_at"])

    @outcome
    def finish(self, claim: TaskClaim, result: TaskResult, *, execution_ms: float | None = None) -> TaskRecord:
        if execution_ms is not None and (type(execution_ms) not in (int, float) or not math.isfinite(execution_ms) or execution_ms < 0):
            raise ValueError("execution duration must be a measured finite nonnegative value")
        with self.runtime.transaction():
            now = self._now()
            row, attempt = self._fenced(claim, now, cancelling=True)
            if row["state"] not in ("running", "cancelling"):
                raise TaskError("task_not_running", "task has not started execution")
            return self._record(self._result(row, attempt, result, now, execution_ms=execution_ms))

    @outcome
    def cancel(self, context: TaskContext, task_id: str, *, expected_revision: int) -> TaskRecord:
        with self.runtime.transaction():
            self._active()
            now = self._now()
            row = self._owned(context, task_id, now)
            if row["revision"] != expected_revision:
                raise TaskError("task_revision_conflict", "cancel refers to an older task revision")
            if row["state"] in TERMINAL or row["state"] == "cancelling":
                return self._record(row)
            if row["state"] in QUEUED or row["state"] == "admitted":
                if row["state"] == "admitted":
                    attempt = self._attempt(TaskClaim(task_id, row["attempt_id"], row["generation"]))
                    data = json.loads(attempt["record_json"])
                    data.update(finished_at=stamp(now), stopped_proof="not_started")
                    self._save_attempt(attempt, data, stopped=True)
                return self._record(self._change(row, "cancelled", "cancelled_before_start", now))
            data = json.loads(row["record_json"])
            data["cancellation_requested"] = True
            return self._record(self._change(row, "cancelling", "cancellation_requested", now, data=data))

    @outcome
    def abandon(self, claim: TaskClaim, *, reason: str) -> TaskRecord:
        """Fence an attempt whose worker may still run. Keep its physical slot reserved."""
        if reason not in ("timeout", "worker_error", "permission_changed", "runner_cancelled", "supervisor_restart"):
            raise ValueError("unknown abandonment reason")
        with self.runtime.transaction():
            self._active()
            row, attempt = self._row(claim.task_id), self._attempt(claim)
            if row["attempt_id"] != claim.attempt_id or row["state"] in TERMINAL:
                return self._record(row)
            now = self._now()
            attempt_data = json.loads(attempt["record_json"])
            if attempt_data["started_at"] is None:
                attempt_data.update(finished_at=stamp(now), stopped_proof="not_started")
                self._save_attempt(attempt, attempt_data, stopped=True)
                state = "expired" if row["deadline_at"] <= stamp(now) else "failed"
            else:
                state = "uncertain"
            return self._record(self._change(row, state, reason, now))

    @outcome
    def acknowledge_stopped(self, claim: TaskClaim, *, proof_ref: str) -> TaskRecord:
        """Trusted quiescence evidence releases capacity, never installs a late result."""
        from companion_v2.identity_policy.models import _opaque
        _opaque(proof_ref, field="stopped proof reference")
        with self.runtime.transaction():
            row, attempt = self._row(claim.task_id), self._attempt(claim)
            data = json.loads(attempt["record_json"])
            if data["stopped_proof"] is None:
                data.update(stopped_proof=proof_ref, finished_at=stamp(self._now()))
                self._save_attempt(attempt, data, stopped=True)
            return self._record(row)

    @outcome
    def stop(self, claim: TaskClaim, *, reason: str) -> TaskRecord:
        """Called only after the supervised coroutine has actually terminated."""
        if reason not in ("timeout", "worker_error", "permission_changed", "runner_cancelled", "cancelled"):
            raise ValueError("unknown stop reason")
        with self.runtime.transaction():
            self._active()
            row, attempt = self._row(claim.task_id), self._attempt(claim)
            if row["attempt_id"] != claim.attempt_id or claim.generation != self.generation:
                raise TaskError("task_attempt_fenced", "old worker cannot change current task state")
            require(self.acknowledge_stopped(claim, proof_ref="supervised_coroutine_stopped"))
            if row["state"] in TERMINAL and row["state"] != "uncertain":
                return self._record(row)
            attempt_data = json.loads(attempt["record_json"])
            data = json.loads(row["record_json"])
            if attempt_data["effect_started"]:
                state = "uncertain"
            elif data.get("cancellation_requested") or reason in ("cancelled", "runner_cancelled"):
                state = "cancelled"
            elif reason == "timeout" or row["deadline_at"] <= stamp(self._now()):
                state = "expired"
            else:
                state = "failed"
            if row["state"] == state and data["reason"] == reason:
                return self._record(row)
            return self._record(self._change(row, state, reason, self._now()))

    @outcome
    def recover(self, *, limit: int = 32) -> tuple[str, ...]:
        """A bounded controller sweep. Never replay work that might have submitted."""
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("recovery requires a bounded page")
        with self.runtime.transaction() as connection:
            self._active()
            now = self._now()
            rows = connection.execute("""SELECT t.* FROM execution_tasks t
                JOIN execution_attempts a ON a.attempt_id = t.attempt_id
                WHERE t.state IN ('admitted', 'running', 'cancelling')
                AND (t.generation != ? OR a.lease_until <= ?) ORDER BY t.task_id LIMIT ?""",
                (self.generation, stamp(now), limit)).fetchall()
            changed = []
            for row in rows:
                claim = TaskClaim(row["task_id"], row["attempt_id"], row["generation"])
                attempt = self._attempt(claim)
                data = json.loads(attempt["record_json"])
                if data["started_at"] is not None:
                    self._change(row, "uncertain", "worker_lost", now)
                    task_data = json.loads(row["record_json"])
                    if task_data["definition"]["side_effect"] == SideEffect.NONE.value:
                        data["capacity_release"] = "superseded_read_only_generation"
                        self._save_attempt(attempt, data, release_capacity=True)
                else:
                    data.update(finished_at=stamp(now), stopped_proof="not_started")
                    self._save_attempt(attempt, data, stopped=True)
                    try:
                        entry, _, _ = self._authorize(row, now)
                        state = "retry_scheduled" if row["attempt_no"] < entry.definition.max_attempts else "dead_letter"
                        reason = "recovered_before_start" if state == "retry_scheduled" else "attempt_budget_exhausted"
                    except TaskError as error:
                        state, reason = "failed", error.code
                    if row["deadline_at"] <= stamp(now):
                        state, reason = "expired", "deadline_before_start"
                    self._change(row, state, reason, now, generation=self.generation, next_run_at=stamp(now))
                changed.append(row["task_id"])
            return tuple(changed)

    @outcome
    def recover_stopped_workers(self, *, limit: int = 32, after_attempt_id: str | None = None) -> WorkerStopRecovery:
        """Release physical slots with host stop evidence, preserving every original result."""
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("worker recovery requires a bounded page")
        with self.runtime.transaction() as connection:
            self._active()
            if self.worker_lifecycle is None:
                return WorkerStopRecovery(0, None, ())
            rows = connection.execute("""SELECT a.*, t.record_json AS task_json FROM execution_attempts a
                JOIN execution_tasks t ON t.task_id = a.task_id
                WHERE a.slot_held = 1 AND a.generation != ? AND a.attempt_id > ?
                ORDER BY a.attempt_id LIMIT ?""", (self.generation, after_attempt_id or "", limit)).fetchall()
            released = []
            for row in rows:
                data = json.loads(row["record_json"])
                if data.get("started_at") is None:
                    continue
                claim = TaskClaim(row["task_id"], row["attempt_id"], row["generation"])
                try:
                    ownership = WorkerOwnership.restore(data["worker_owner"]) if data.get("worker_owner") else None
                    attempt = WorkerAttempt(claim, row["kind"], row["attempt_no"],
                        datetime.fromisoformat(data["started_at"]), ownership,
                        SideEffect(json.loads(row["task_json"])["definition"]["side_effect"]))
                except (ValueError, KeyError, TypeError):
                    continue
                proof = self.worker_lifecycle.stopped_proof(attempt)
                if proof is not None:
                    require(self.acknowledge_stopped(claim, proof_ref=proof))
                    released.append(claim.task_id)
                    logger.info("[CompanionV2] 已回收退出工作者的执行名额 task=%s kind=%s generation=%s proof=%s",
                                claim.task_id, attempt.kind, claim.generation, proof)
            return WorkerStopRecovery(len(rows), rows[-1]["attempt_id"] if rows else None, tuple(released))

    @outcome
    def reconcile(self, context: TaskContext, task_id: str, *, expected_revision: int,
                  result: TaskResult) -> TaskRecord:
        """Current trusted owner supplies its original receipt after stopped work is queried.

        No invocation or retry is performed here. This is not exposed as a model tool.
        """
        if result.retryable or result.status == "uncertain":
            raise ValueError("reconciliation cannot schedule another effect")
        with self.runtime.transaction():
            self._active()
            now = self._now()
            row = self._owned(context, task_id, now)
            if row["revision"] != expected_revision or row["state"] != "uncertain":
                raise TaskError("task_revision_conflict", "only the current uncertain revision can be reconciled")
            attempt = self._attempt(TaskClaim(task_id, row["attempt_id"], row["generation"]))
            attempt_data = json.loads(attempt["record_json"])
            if attempt["slot_held"]:
                raise TaskError("task_worker_not_stopped", "a local fence does not prove the old worker stopped")
            if attempt_data.get("stopped_proof") is None:
                raise TaskError("task_worker_stop_unknown", "this read-only slot was released without proving worker termination")
            return self._record(self._result(row, attempt, result, now, inputs=False))

    @outcome
    def observe(self, context: TaskContext, task_id: str) -> dict:
        """Exact-task, private, redacted diagnostic projection with no result bodies."""
        with self.runtime.read_snapshot():
            row = self._owned(context, task_id, self._now())
            attempt = self.runtime.read_one("SELECT * FROM execution_attempts WHERE attempt_id = ?", (row["attempt_id"],))
            attempt_data = json.loads(attempt["record_json"]) if attempt else {}
            data = json.loads(row["record_json"])
            controller = self.runtime.read_one("SELECT generation FROM task_supervisor WHERE singleton = 1", ())
            return {"task_id": task_id, "revision": row["revision"], "state": row["state"], "lane": row["lane"],
                    "task_generation": row["generation"], "controller_generation": controller["generation"] if controller else None,
                    "attempt_no": row["attempt_no"], "created_at": row["created_at"], "updated_at": row["updated_at"],
                    "next_run_at": row["next_run_at"], "deadline_at": row["deadline_at"],
                    "claimed_at": attempt_data.get("claimed_at"), "started_at": attempt_data.get("started_at"),
                    "finished_at": attempt_data.get("finished_at"), "queue_ms": attempt_data.get("queue_ms"),
                    "execution_ms": attempt_data.get("execution_ms"), "effect": data["effect"],
                    "slot_held": bool(attempt["slot_held"]) if attempt else False,
                    "worker_stopped": attempt_data.get("stopped_proof") is not None,
                    "capacity_release": attempt_data.get("capacity_release"),
                    "reason": data["reason"], "result_body": "redacted", "provider_latency_ms": None,
                    "delivery_latency_ms": None, "recipient_received": "unknown"}
