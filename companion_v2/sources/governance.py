"""Runtime owner attestations whose absence cannot reactivate durable sources."""

import inspect

from companion_v2.execution.models import TaskError


_PREFIX = "source-owner-guard:"
_VERSION = "companion.source-owner-guard.v1"
_MISSING = object()


class OwnerGuard:
    def __init__(self, runtime, owner, check):
        self.runtime, self.owner, self.check = runtime, owner, check
        self.closed = False

    def close(self):
        self.closed = True
        guards = getattr(self.runtime, "_source_owner_guards", {})
        if guards.get(self.owner) is self:
            guards.pop(self.owner)


def bind(runtime, owner, check):
    """Attach only in memory; read-only host assembly does not write metadata."""
    if not isinstance(owner, str) or not owner.strip() or not callable(check) or inspect.iscoroutinefunction(check):
        raise ValueError("source owners require a synchronous local governance check")
    if not hasattr(runtime, "_source_owner_guards"):
        runtime._source_owner_guards = {}
    guard = OwnerGuard(runtime, owner, check)
    old = runtime._source_owner_guards.get(owner)
    if old is not None:
        old.close()
    runtime._source_owner_guards[owner] = guard
    return guard


def mark_publication(runtime, owner):
    """Persist the requirement in the same transaction as its first source."""
    if owner in getattr(runtime, "_source_owner_guards", {}):
        with runtime.transaction() as connection:
            connection.execute("INSERT OR IGNORE INTO runtime_metadata(key, value) VALUES (?, ?)",
                               (_PREFIX + owner, _VERSION))
        from .dependencies import invalidate_batch
        invalidate_batch(runtime, owner=owner)


def owner_required(runtime, owner, *, cache=None, connection=None):
    """Look up persisted owner-lease requirement with optional snapshot cache."""
    if cache is not None:
        cached = cache.get(owner, _MISSING)
        if cached is not _MISSING:
            return cached
    if connection is not None:
        row = connection.execute("SELECT value FROM runtime_metadata WHERE key = ?", (_PREFIX + owner,)).fetchone()
    else:
        row = runtime.read_one("SELECT value FROM runtime_metadata WHERE key = ?", (_PREFIX + owner,))
    value = row["value"] if row is not None else None
    if cache is not None:
        cache[owner] = value
    return value


def _check_guard(guard, record, at):
    try:
        result = guard.check(record, at)
        if result is not None:
            if inspect.iscoroutine(result):
                result.close()
            raise TaskError("source_owner_invalid", "source governance must finish synchronously")
    except TaskError:
        raise
    except Exception as error:
        raise TaskError("source_owner_unavailable", "the owner cannot attest this original source") from error


def validate_owner(runtime, owner, at, record_loader, *, required_cache=None, connection=None):
    """Validate owner governance without materializing unguarded source payloads."""
    guard = getattr(runtime, "_source_owner_guards", {}).get(owner)
    required = owner_required(runtime, owner, cache=required_cache, connection=connection)
    if guard is None:
        if required is not None:
            raise TaskError("source_owner_unavailable", "the original source owner is not currently attached")
        return False
    if guard.closed or required is not None and required != _VERSION:
        raise TaskError("source_owner_unavailable", "the original source governance lease is unavailable")
    _check_guard(guard, record_loader(), at)
    return True


def validate(runtime, record, at):
    validate_owner(runtime, record.reference.owner, at, lambda: record)
