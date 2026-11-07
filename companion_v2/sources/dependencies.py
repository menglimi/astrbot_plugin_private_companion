"""Governance for derived sources, shared by all source consumers."""

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from threading import get_ident

from companion_v2.execution.models import TaskError
from companion_v2.storage.codec import decode_timestamp


_CURRENT = ContextVar("source_validation_traversal", default=None)
_BATCH = ContextVar("source_validation_batch", default=None)

_REACHABLE = """WITH RECURSIVE reachable(id, lineage, revision) AS (
    VALUES (?, ?, ?)
    UNION
    SELECT d.input_id, d.input_lineage, d.input_revision
    FROM source_dependencies d JOIN reachable r
      ON d.dependent_id = r.id AND d.dependent_lineage = r.lineage AND d.dependent_revision = r.revision
) """


def _task():
    try:
        return asyncio.current_task()
    except RuntimeError:
        return None


def _guards(runtime):
    return tuple((owner, guard, guard.closed, guard.check)
                 for owner, guard in getattr(runtime, "_source_owner_guards", {}).items())


@dataclass
class _Traversal:
    runtime: object
    at: object
    thread: int
    task: object
    guards: tuple
    checked: set = field(default_factory=set)
    visiting: set = field(default_factory=set)
    active: bool = True


@dataclass
class _Batch:
    runtime: object
    at: object
    thread: int
    task: object
    connection: object
    rows: dict = field(default_factory=dict)
    edges: dict = field(default_factory=dict)
    active: bool = True


@contextmanager
def validation_batch(runtime, at):
    """Reuse database rows only while one read snapshot pins their contents."""
    with runtime.read_snapshot() as connection:
        if getattr(runtime, "_write_depth", 0):
            yield
            return
        batch = _BATCH.get()
        if (batch is not None and batch.active and batch.runtime is runtime and batch.at == at
                and batch.thread == get_ident() and batch.task is _task()):
            yield
            return
        batch = _Batch(runtime, at, get_ident(), _task(), connection)
        token = _BATCH.set(batch)
        try:
            yield
        finally:
            batch.active = False
            batch.rows.clear()
            batch.edges.clear()
            _BATCH.reset(token)


def _batch_for(state):
    batch = _BATCH.get()
    if (batch is not None and batch.active and batch.runtime is state.runtime and batch.at == state.at
            and batch.thread == state.thread and batch.task is state.task):
        return batch
    return None


def _prefetch(batch, root):
    rows = batch.connection.execute(_REACHABLE + """SELECT r.id AS reach_id,
        r.lineage AS reach_lineage, r.revision AS reach_revision, s.*
        FROM reachable r LEFT JOIN sources s ON s.source_id = r.id
          AND s.lineage_id = r.lineage AND s.source_revision = r.revision""", root)
    for row in rows:
        current = (row["reach_id"], row["reach_lineage"], row["reach_revision"])
        batch.rows[current] = row if row["source_id"] is not None else None
        batch.edges[current] = []
    edges = batch.connection.execute(_REACHABLE + """SELECT d.dependent_id, d.dependent_lineage,
        d.dependent_revision, d.input_id, d.input_lineage, d.input_revision
        FROM source_dependencies d JOIN reachable r ON d.dependent_id = r.id
          AND d.dependent_lineage = r.lineage AND d.dependent_revision = r.revision""", root)
    for edge in edges:
        current = (edge["dependent_id"], edge["dependent_lineage"], edge["dependent_revision"])
        batch.edges[current].append((edge["input_id"], edge["input_lineage"], edge["input_revision"]))


def key(reference):
    return (reference.source_id, reference.version.lineage_id, reference.version.revision)


def validate(runtime, reference, at):
    """Reuse only completed checks in this synchronous runtime/time call stack."""
    state = _CURRENT.get()
    thread, task, guards = get_ident(), _task(), _guards(runtime)
    if (state is not None and state.active and state.runtime is runtime and state.at == at
            and state.thread == thread and state.task is task):
        if state.guards != guards:
            state.checked.clear()
            state.guards = guards
        _walk(state, key(reference))
        return
    state = _Traversal(runtime, at, thread, task, guards)
    token = _CURRENT.set(state)
    try:
        _walk(state, key(reference))
    finally:
        # A copied context may outlive this call or enter another task/thread.
        state.active = False
        state.checked.clear()
        state.visiting.clear()
        _CURRENT.reset(token)


def _walk(state, reference_key):
    runtime, at = state.runtime, state.at
    batch = _batch_for(state)
    pending, entered = [(reference_key, False)], set()
    try:
        while pending:
            current, exiting = pending.pop()
            if batch is not None:
                if current not in batch.rows:
                    _prefetch(batch, current)
                row = batch.rows[current]
            else:
                # Independent calls must observe current revocation and expiry.
                row = runtime.read_one("""SELECT * FROM sources
                    WHERE source_id = ? AND lineage_id = ? AND source_revision = ?""", current)
            if row is None or row["state"] != "active":
                raise TaskError("source_dependency_unavailable", "an original source dependency is unavailable")
            if at < decode_timestamp(row["issued_at"]) or (row["expires_at"] and at >= decode_timestamp(row["expires_at"])):
                raise TaskError("source_dependency_expired", "an original source dependency is outside its validity")
            if exiting:
                from .governance import validate as validate_owner
                from .repositories import _source_from_row
                # Postorder keeps owner reads of declared inputs out of recursion.
                validate_owner(runtime, _source_from_row(row), at)
                state.checked.add(current)
                state.visiting.remove(current)
                entered.remove(current)
            elif current in state.visiting:
                raise TaskError("source_dependency_cycle", "a derived source cannot support itself")
            elif current not in state.checked:
                state.visiting.add(current)
                entered.add(current)
                pending.append((current, True))
                inputs = batch.edges[current] if batch is not None else runtime.read_all("""SELECT input_id, input_lineage, input_revision
                    FROM source_dependencies WHERE dependent_id = ? AND dependent_lineage = ? AND dependent_revision = ?""", current)
                pending.extend((tuple(r), False) for r in inputs)
    finally:
        # An owner can catch a rejected nested read. Its unfinished nodes must not
        # become cached successes or remain marked as a recursive visit.
        state.visiting.difference_update(entered)


def link(runtime, dependent, inputs, at):
    """Trusted source-owner API, called in the publication transaction."""
    target = key(dependent)
    with runtime.transaction() as connection:
        for reference in inputs:
            validate(runtime, reference, at)
            pending, seen = [key(reference)], set()
            while pending:
                current = pending.pop()
                if current == target:
                    raise TaskError("source_dependency_cycle", "a derived source cannot support itself")
                if current in seen:
                    continue
                seen.add(current)
                pending.extend(tuple(r) for r in runtime.read_all("""SELECT input_id, input_lineage, input_revision
                    FROM source_dependencies WHERE dependent_id = ? AND dependent_lineage = ? AND dependent_revision = ?""", current))
            connection.execute("INSERT OR IGNORE INTO source_dependencies VALUES (?, ?, ?, ?, ?, ?)", (*target, *key(reference)))


def revoke_dependants(runtime, reference, at):
    """Revocation is durable; publishing a new source cannot reactivate descendants."""
    pending, seen = [key(reference)], set()
    with runtime.transaction() as connection:
        while pending:
            current = pending.pop()
            if current in seen:
                continue
            seen.add(current)
            children = runtime.read_all("""SELECT dependent_id, dependent_lineage, dependent_revision
                FROM source_dependencies WHERE input_id = ? AND input_lineage = ? AND input_revision = ?""", current)
            for child in children:
                values = tuple(child)
                connection.execute("""UPDATE sources SET state = 'revoked', governance_revision = governance_revision + 1,
                    updated_at = ? WHERE source_id = ? AND lineage_id = ? AND source_revision = ? AND state = 'active'""",
                    (at.isoformat(), *values))
                pending.append(values)
