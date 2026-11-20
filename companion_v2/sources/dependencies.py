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

_SOURCE_META_COLUMNS = (
    "source_id, lineage_id, source_revision, owner, state, issued_at, expires_at"
)
_SOURCE_META_SQL = f"SELECT {_SOURCE_META_COLUMNS} FROM sources WHERE source_id = ? AND lineage_id = ? AND source_revision = ?"
_FULL_SOURCE_SQL = "SELECT * FROM sources WHERE source_id = ? AND lineage_id = ? AND source_revision = ?"
_EDGE_SQL = """SELECT input_id, input_lineage, input_revision FROM source_dependencies
    WHERE dependent_id = ? AND dependent_lineage = ? AND dependent_revision = ?"""

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
    guarded: set = field(default_factory=set)
    visiting: set = field(default_factory=set)
    active: bool = True


@dataclass
class _Batch:
    runtime: object
    at: object
    thread: int
    task: object
    connection: object
    parent: object = None
    guards: tuple = ()
    rollback_epoch: int = 0
    rows: dict = field(default_factory=dict)
    full_records: dict = field(default_factory=dict)
    edges: dict = field(default_factory=dict)
    owner_required: dict = field(default_factory=dict)
    checked_unguarded: set = field(default_factory=set)
    active: bool = True


def _clear_batch(batch):
    batch.rows.clear()
    batch.full_records.clear()
    batch.edges.clear()
    batch.owner_required.clear()
    batch.checked_unguarded.clear()


def _sync_batch(batch, runtime, guards=None):
    rollback_epoch = getattr(runtime, "_transaction_rollback_epoch", 0)
    if rollback_epoch != batch.rollback_epoch:
        _clear_batch(batch)
        batch.rollback_epoch = rollback_epoch
        state = _CURRENT.get()
        if state is not None and state.active and state.runtime is runtime and state.thread == batch.thread:
            state.checked.clear()
            state.guarded.clear()
    if guards is not None and guards != batch.guards:
        batch.checked_unguarded.clear()
        batch.guards = guards


def _invalidate_single_batch(batch, *, source_key=None, edge_target=None, owner=None, clear_all=False):
    if clear_all:
        _clear_batch(batch)
        return
    if source_key is not None:
        had_key = (
            source_key in batch.rows
            or source_key in batch.full_records
            or source_key in batch.edges
            or source_key in batch.checked_unguarded
        )
        batch.rows.pop(source_key, None)
        batch.full_records.pop(source_key, None)
        batch.edges.pop(source_key, None)
        if had_key:
            batch.checked_unguarded.clear()
    if edge_target is not None:
        if edge_target in batch.rows or edge_target in batch.edges or edge_target in batch.checked_unguarded:
            batch.rows.pop(edge_target, None)
            batch.edges.pop(edge_target, None)
            batch.checked_unguarded.clear()
    if owner is not None:
        batch.owner_required.pop(owner, None)
        batch.checked_unguarded.clear()


def invalidate_batch(runtime, *, source_key=None, edge_target=None, owner=None, clear_all=False):
    """Keep active transaction-scoped batches consistent with source mutations."""
    thread, task = get_ident(), _task()
    state = _CURRENT.get()
    if state is not None and state.active and state.runtime is runtime and state.thread == thread:
        state.checked.clear()
        state.guarded.clear()
    batch = _BATCH.get()
    while batch is not None:
        if batch.active and batch.runtime is runtime and batch.thread == thread and batch.task is task:
            _invalidate_single_batch(
                batch,
                source_key=source_key,
                edge_target=edge_target,
                owner=owner,
                clear_all=clear_all,
            )
        batch = batch.parent


@contextmanager
def validation_batch(runtime, at):
    """Reuse database rows while one read snapshot or write transaction pins them."""
    read_snapshot = getattr(runtime, "read_snapshot", None)
    if not callable(read_snapshot):
        yield
        return
    with read_snapshot() as connection:
        if not callable(getattr(connection, "execute", None)):
            yield
            return
        guards = _guards(runtime)
        rollback_epoch = getattr(runtime, "_transaction_rollback_epoch", 0)
        outer = _BATCH.get()
        if (outer is not None and outer.active and outer.runtime is runtime and outer.at == at
                and outer.thread == get_ident() and outer.task is _task()):
            _sync_batch(outer, runtime, guards)
            yield
            return
        batch = _Batch(
            runtime,
            at,
            get_ident(),
            _task(),
            connection,
            parent=outer,
            guards=guards,
            rollback_epoch=rollback_epoch,
        )
        token = _BATCH.set(batch)
        try:
            yield
        finally:
            batch.active = False
            _clear_batch(batch)
            _BATCH.reset(token)


def _batch_for(state):
    batch = _BATCH.get()
    if (batch is not None and batch.active and batch.runtime is state.runtime and batch.at == state.at
            and batch.thread == state.thread and batch.task is state.task):
        _sync_batch(batch, state.runtime, state.guards)
        return batch
    return None


def is_unguarded_checked(runtime, reference, at):
    """Return True if this reference already completed unguarded in the active batch."""
    batch = _BATCH.get()
    if (batch is None or not batch.active or batch.runtime is not runtime or batch.at != at
            or batch.thread != get_ident() or batch.task is not _task()):
        return False
    _sync_batch(batch, runtime, _guards(runtime))
    return key(reference) in batch.checked_unguarded


def _prefetch(batch, root):
    rows = batch.connection.execute(_REACHABLE + """SELECT r.id AS reach_id,
        r.lineage AS reach_lineage, r.revision AS reach_revision,
        s.source_id, s.lineage_id, s.source_revision, s.owner, s.state, s.issued_at, s.expires_at
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


def _load_batch_node(batch, current):
    """Avoid recursive CTE overhead for leaf sources without outgoing edges."""
    if current in batch.rows and current in batch.edges:
        return batch.rows[current]
    inputs = batch.connection.execute(_EDGE_SQL, current).fetchall()
    if not inputs:
        row = batch.connection.execute(_SOURCE_META_SQL, current).fetchone()
        batch.rows[current] = row
        batch.edges[current] = []
        return row
    _prefetch(batch, current)
    return batch.rows[current]


def _load_record(runtime, batch, current):
    if batch is not None and current in batch.full_records:
        return batch.full_records[current]
    row = batch.connection.execute(_FULL_SOURCE_SQL, current).fetchone() if batch is not None else runtime.read_one(_FULL_SOURCE_SQL, current)
    if row is None:
        raise TaskError("source_dependency_unavailable", "an original source dependency is unavailable")
    from .repositories import _source_from_row
    record = _source_from_row(row)
    if batch is not None:
        batch.full_records[current] = record
    return record


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
            state.guarded.clear()
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
        state.guarded.clear()
        state.visiting.clear()
        _CURRENT.reset(token)


def _walk(state, reference_key):
    runtime, at = state.runtime, state.at
    batch = _batch_for(state)
    if reference_key in state.checked or (batch is not None and reference_key in batch.checked_unguarded):
        return
    from .governance import validate_owner
    pending, entered, meta = [(reference_key, False)], set(), {}
    try:
        while pending:
            current, exiting = pending.pop()
            if exiting:
                row = meta[current]
                # Postorder keeps owner reads of declared inputs out of recursion.
                guarded = validate_owner(
                    runtime,
                    row["owner"],
                    at,
                    lambda k=current: _load_record(runtime, batch, k),
                    required_cache=batch.owner_required if batch is not None else None,
                    connection=batch.connection if batch is not None else None,
                )
                if batch is not None and not guarded and any(child in state.guarded for child in batch.edges.get(current, ())):
                    guarded = True
                if guarded:
                    state.guarded.add(current)
                elif batch is not None and batch.active and current in batch.edges:
                    batch.checked_unguarded.add(current)
                state.checked.add(current)
                state.visiting.remove(current)
                entered.remove(current)
                continue
            if current in state.checked or (batch is not None and current in batch.checked_unguarded):
                continue
            if current in state.visiting:
                raise TaskError("source_dependency_cycle", "a derived source cannot support itself")
            if batch is not None:
                row = _load_batch_node(batch, current)
            else:
                # Independent calls must observe current revocation and expiry.
                row = runtime.read_one(_SOURCE_META_SQL, current)
            if row is None or row["state"] != "active":
                raise TaskError("source_dependency_unavailable", "an original source dependency is unavailable")
            if at < decode_timestamp(row["issued_at"]) or (row["expires_at"] and at >= decode_timestamp(row["expires_at"])):
                raise TaskError("source_dependency_expired", "an original source dependency is outside its validity")
            meta[current] = row
            state.visiting.add(current)
            entered.add(current)
            pending.append((current, True))
            inputs = batch.edges[current] if batch is not None else runtime.read_all(_EDGE_SQL, current)
            pending.extend((tuple(r), False) for r in inputs)
    finally:
        # An owner can catch a rejected nested read. Its unfinished nodes must not
        # become cached successes or remain marked as a recursive visit.
        state.visiting.difference_update(entered)


def _ensure_acyclic_in_batch(batch, target, root):
    pending, seen = [root], set()
    while pending:
        current = pending.pop()
        if current == target:
            raise TaskError("source_dependency_cycle", "a derived source cannot support itself")
        if current in seen:
            continue
        seen.add(current)
        if current not in batch.edges:
            _load_batch_node(batch, current)
        pending.extend(batch.edges[current])


def _ensure_acyclic_unbatched(runtime, target, root):
    pending, seen = [root], set()
    while pending:
        current = pending.pop()
        if current == target:
            raise TaskError("source_dependency_cycle", "a derived source cannot support itself")
        if current in seen:
            continue
        seen.add(current)
        pending.extend(tuple(r) for r in runtime.read_all(_EDGE_SQL, current))


def link(runtime, dependent, inputs, at):
    """Trusted source-owner API, called in the publication transaction."""
    target = key(dependent)
    with runtime.transaction() as connection, validation_batch(runtime, at):
        batch = _BATCH.get()
        for reference in inputs:
            validate(runtime, reference, at)
            input_key = key(reference)
            if batch is not None and batch.active:
                _sync_batch(batch, runtime)
                _ensure_acyclic_in_batch(batch, target, input_key)
            else:
                _ensure_acyclic_unbatched(runtime, target, input_key)
            connection.execute("INSERT OR IGNORE INTO source_dependencies VALUES (?, ?, ?, ?, ?, ?)", (*target, *input_key))
            invalidate_batch(runtime, edge_target=target)


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
        invalidate_batch(runtime, clear_all=True)
