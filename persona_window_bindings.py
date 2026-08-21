"""Versioned storage and transaction helpers for multi-persona window bindings.

The plugin has two sources for window bindings: the AstrBot configuration and a
small reload-safe JSON file.  This module deliberately keeps those sources
separate.  Callers can use :class:`BindingMutationPlan` to update both sources
and their in-memory claims/conflicts/cache in one higher-level transaction.

No platform-specific UMO parser is used here.  A UMO is an opaque, non-empty,
single-line window key; this is important for adapters such as QQ Official,
whose group UMO can look like ``QBot...:GroupMessage:...``.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import time
import uuid
from typing import Any, Callable, Mapping


STORE_VERSION = 2
DEFAULT_WINDOW_MAX_LENGTH = 240
DEFAULT_PERSONA_MAX_LENGTH = 96


class BindingStoreError(RuntimeError):
    """Base error for invalid or unreadable binding persistence."""


class BindingStoreValidationError(ValueError, BindingStoreError):
    """Raised when a binding key/value cannot be represented safely."""


class BindingRevisionConflict(BindingStoreError):
    """Raised when a mutation was based on an older store revision."""

    def __init__(self, expected: int, actual: int) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"persona window binding revision conflict: expected={expected} actual={actual}"
        )


def _nonnegative_int(value: Any, default: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return max(0, parsed)


def _timestamp(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return parsed if parsed >= 0 else default


def normalize_window(value: Any, *, max_length: int = DEFAULT_WINDOW_MAX_LENGTH) -> str:
    """Normalize an opaque UMO/window key.

    Newlines are rejected rather than collapsed: accepting a pasted multi-line
    value would make one UI row silently represent several unrelated windows.
    Other whitespace is retained, except for surrounding whitespace.  Colons,
    Unicode, and adapter-specific punctuation are all legal.
    """

    text = str(value if value is not None else "").strip()
    if not text:
        return ""
    if "\r" in text or "\n" in text:
        return ""
    if max_length <= 0 or len(text) > max_length:
        return ""
    return text


def normalize_persona_id(
    value: Any, *, max_length: int = DEFAULT_PERSONA_MAX_LENGTH
) -> str:
    """Normalize a persona ID without imposing platform-specific syntax."""

    text = str(value if value is not None else "").strip()
    if not text or "\r" in text or "\n" in text:
        return ""
    if max_length <= 0 or len(text) > max_length:
        return ""
    return text


def normalize_bindings(
    value: Any,
    *,
    window_max_length: int = DEFAULT_WINDOW_MAX_LENGTH,
    persona_max_length: int = DEFAULT_PERSONA_MAX_LENGTH,
    persona_normalizer: Callable[[Any], str] | None = None,
) -> dict[str, str]:
    """Return a sanitized mapping, dropping malformed entries.

    Config and persisted maps are normalized independently.  This function does
    not merge them, so callers can retain source precedence and perform a
    rollback without losing which source contained a binding.
    """

    if not isinstance(value, Mapping):
        return {}
    result: dict[str, str] = {}
    normalize_pid = persona_normalizer or (
        lambda item: normalize_persona_id(item, max_length=persona_max_length)
    )
    for raw_window, raw_persona in value.items():
        window = normalize_window(raw_window, max_length=window_max_length)
        try:
            persona = str(normalize_pid(raw_persona) or "").strip()
        except Exception:
            persona = ""
        if persona and len(persona) > persona_max_length:
            persona = persona[:persona_max_length]
        if window and persona:
            result[window] = persona
    return result


def merge_bindings(
    config_bindings: Mapping[str, Any] | None,
    persisted_bindings: Mapping[str, Any] | None,
    *,
    window_max_length: int = DEFAULT_WINDOW_MAX_LENGTH,
    persona_max_length: int = DEFAULT_PERSONA_MAX_LENGTH,
    persona_normalizer: Callable[[Any], str] | None = None,
) -> dict[str, str]:
    """Merge config then persisted bindings (persisted values take precedence)."""

    merged = normalize_bindings(
        config_bindings,
        window_max_length=window_max_length,
        persona_max_length=persona_max_length,
        persona_normalizer=persona_normalizer,
    )
    merged.update(
        normalize_bindings(
            persisted_bindings,
            window_max_length=window_max_length,
            persona_max_length=persona_max_length,
            persona_normalizer=persona_normalizer,
        )
    )
    return merged


@dataclass
class BindingStoreState:
    """Normalized contents of ``persona_window_bindings.json``."""

    version: int = STORE_VERSION
    revision: int = 0
    updated_at: float = 0.0
    bindings: dict[str, str] = field(default_factory=dict)
    source_format: str = "missing"
    needs_migration: bool = False

    def __post_init__(self) -> None:
        self.version = STORE_VERSION
        self.revision = _nonnegative_int(self.revision)
        self.updated_at = _timestamp(self.updated_at)
        self.bindings = normalize_bindings(self.bindings)

    def clone(self) -> "BindingStoreState":
        return deepcopy(self)

    def to_payload(self) -> dict[str, Any]:
        return {
            "version": STORE_VERSION,
            "revision": self.revision,
            "updated_at": self.updated_at,
            "bindings": deepcopy(self.bindings),
        }


@dataclass
class BindingRuntimeSnapshot:
    """Rollbackable runtime state for one window-binding transaction.

    ``config_bindings`` and ``persisted_bindings`` intentionally remain two
    fields.  The former is the AstrBot config mapping and the latter is the
    reload-safe file mapping; combining them here would make partial writes
    impossible to recover correctly.
    """

    config_bindings: dict[str, str] = field(default_factory=dict)
    persisted_bindings: dict[str, str] = field(default_factory=dict)
    claims: dict[str, Any] = field(default_factory=dict)
    conflicts: dict[str, Any] = field(default_factory=dict)
    passive_cache: dict[str, Any] = field(default_factory=dict)
    revision: int = 0
    updated_at: float = 0.0

    @classmethod
    def capture(
        cls,
        config_bindings: Mapping[str, Any] | None,
        persisted_bindings: Mapping[str, Any] | None,
        *,
        claims: Mapping[str, Any] | None = None,
        conflicts: Mapping[str, Any] | None = None,
        passive_cache: Mapping[str, Any] | None = None,
        revision: Any = 0,
        updated_at: Any = 0.0,
        window_max_length: int = DEFAULT_WINDOW_MAX_LENGTH,
        persona_max_length: int = DEFAULT_PERSONA_MAX_LENGTH,
        persona_normalizer: Callable[[Any], str] | None = None,
    ) -> "BindingRuntimeSnapshot":
        def clone_map(value: Mapping[str, Any] | None) -> dict[str, Any]:
            return deepcopy(dict(value)) if isinstance(value, Mapping) else {}

        return cls(
            config_bindings=normalize_bindings(
                config_bindings,
                window_max_length=window_max_length,
                persona_max_length=persona_max_length,
                persona_normalizer=persona_normalizer,
            ),
            persisted_bindings=normalize_bindings(
                persisted_bindings,
                window_max_length=window_max_length,
                persona_max_length=persona_max_length,
                persona_normalizer=persona_normalizer,
            ),
            claims=clone_map(claims),
            conflicts=clone_map(conflicts),
            passive_cache=clone_map(passive_cache),
            revision=_nonnegative_int(revision),
            updated_at=_timestamp(updated_at),
        )

    def clone(self) -> "BindingRuntimeSnapshot":
        return deepcopy(self)

    @property
    def effective_bindings(self) -> dict[str, str]:
        return merge_bindings(self.config_bindings, self.persisted_bindings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "config_bindings": deepcopy(self.config_bindings),
            "persisted_bindings": deepcopy(self.persisted_bindings),
            "claims": deepcopy(self.claims),
            "conflicts": deepcopy(self.conflicts),
            "passive_cache": deepcopy(self.passive_cache),
            "revision": self.revision,
            "updated_at": self.updated_at,
        }


@dataclass
class BindingMutationPlan:
    """Pure before/after plan; applying it is owned by the plugin transaction."""

    operation: str
    window_key: str
    persona_id: str = ""
    before: BindingRuntimeSnapshot = field(default_factory=BindingRuntimeSnapshot)
    after: BindingRuntimeSnapshot = field(default_factory=BindingRuntimeSnapshot)
    changed: bool = False

    @property
    def effective_bindings(self) -> dict[str, str]:
        return self.after.effective_bindings

    @property
    def persisted_state(self) -> BindingStoreState:
        return BindingStoreState(
            revision=self.after.revision,
            updated_at=self.after.updated_at,
            bindings=self.after.persisted_bindings,
        )

    @property
    def rollback_persisted_state(self) -> BindingStoreState:
        """Exact persisted state to restore if a surrounding transaction fails."""

        return BindingStoreState(
            revision=self.before.revision,
            updated_at=self.before.updated_at,
            bindings=self.before.persisted_bindings,
        )

    def rollback_snapshot(self) -> BindingRuntimeSnapshot:
        return self.before.clone()


class PersonaWindowBindingStore:
    """Atomic v2 JSON store and revision-aware mutation planner."""

    def __init__(
        self,
        path: str | Path,
        *,
        window_max_length: int = DEFAULT_WINDOW_MAX_LENGTH,
        persona_max_length: int = DEFAULT_PERSONA_MAX_LENGTH,
        persona_normalizer: Callable[[Any], str] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.path = Path(path)
        self.window_max_length = window_max_length
        self.persona_max_length = persona_max_length
        self.persona_normalizer = persona_normalizer
        self.clock = clock
        self._last_state = BindingStoreState()

    def _normalize_bindings(self, value: Any) -> dict[str, str]:
        return normalize_bindings(
            value,
            window_max_length=self.window_max_length,
            persona_max_length=self.persona_max_length,
            persona_normalizer=self.persona_normalizer,
        )

    def load(self, *, migrate: bool = False) -> BindingStoreState:
        """Load and normalize the store.

        Corrupt JSON and a non-object root raise :class:`BindingStoreError`;
        callers must not silently replace a user's binding file with ``{}``.
        Set ``migrate=True`` to atomically rewrite v1/plain input as v2.
        """

        if not self.path.exists():
            state = BindingStoreState(source_format="missing")
            self._last_state = state
            return state.clone()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise BindingStoreError(f"unable to read binding store: {self.path}") from exc
        if not isinstance(payload, dict):
            raise BindingStoreError("persona window binding store root must be an object")

        has_envelope = "bindings" in payload
        if has_envelope:
            raw_bindings = payload.get("bindings")
            version = _nonnegative_int(payload.get("version"), 1)
            if version > STORE_VERSION:
                raise BindingStoreError(
                    f"unsupported persona window binding store version: {version}"
                )
            source_format = f"v{version}"
            needs_migration = version != STORE_VERSION
        else:
            raw_bindings = payload
            version = 0
            source_format = "plain"
            needs_migration = True
        if not isinstance(raw_bindings, dict):
            raise BindingStoreError("persona window binding store bindings must be an object")
        normalized_bindings = self._normalize_bindings(raw_bindings)
        state = BindingStoreState(
            version=STORE_VERSION,
            revision=_nonnegative_int(payload.get("revision"), 0),
            updated_at=_timestamp(payload.get("updated_at"), 0.0),
            bindings=normalized_bindings,
            source_format=source_format,
            needs_migration=needs_migration or normalized_bindings != raw_bindings,
        )
        if migrate and state.needs_migration:
            state = self._write_state(
                state,
                preserve_revision=True,
                source_format="v2",
                needs_migration=False,
            )
        self._last_state = state.clone()
        return state.clone()

    # Read-only naming aliases make integration from old loader call sites easy.
    read = load

    def _write_state(
        self,
        state: BindingStoreState,
        *,
        preserve_revision: bool = True,
        source_format: str = "v2",
        needs_migration: bool = False,
    ) -> BindingStoreState:
        normalized = self._normalize_bindings(state.bindings)
        revision = _nonnegative_int(state.revision)
        updated_at = _timestamp(state.updated_at)
        payload = {
            "version": STORE_VERSION,
            "revision": revision,
            "updated_at": updated_at,
            "bindings": normalized,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(
            f"{self.path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        )
        try:
            with temporary.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.flush()
                try:
                    os.fsync(handle.fileno())
                except OSError:
                    pass
            os.replace(temporary, self.path)
        except Exception as exc:
            raise BindingStoreError(f"unable to save binding store: {self.path}") from exc
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except Exception:
                pass
        result = BindingStoreState(
            revision=revision,
            updated_at=updated_at,
            bindings=normalized,
            source_format=source_format,
            needs_migration=needs_migration,
        )
        self._last_state = result.clone()
        return result

    def save(
        self,
        bindings: Mapping[str, Any],
        *,
        expected_revision: int | None = None,
        revision: int | None = None,
    ) -> BindingStoreState:
        """Atomically persist bindings and advance the store revision."""

        current = self.load()
        if expected_revision is not None and _nonnegative_int(expected_revision) != current.revision:
            raise BindingRevisionConflict(_nonnegative_int(expected_revision), current.revision)
        normalized = self._normalize_bindings(bindings)
        next_revision = (
            _nonnegative_int(revision)
            if revision is not None
            else current.revision + (1 if normalized != current.bindings else 0)
        )
        return self._write_state(
            BindingStoreState(
                revision=next_revision,
                updated_at=self.clock(),
                bindings=normalized,
            ),
            preserve_revision=True,
        )

    def save_snapshot(self, snapshot: BindingStoreState) -> BindingStoreState:
        """Write an exact state for rollback or migration recovery."""

        if not isinstance(snapshot, BindingStoreState):
            raise TypeError("snapshot must be BindingStoreState")
        return self._write_state(snapshot.clone(), preserve_revision=True)

    restore = save_snapshot

    def capture_runtime(
        self,
        config_bindings: Mapping[str, Any] | None,
        *,
        claims: Mapping[str, Any] | None = None,
        conflicts: Mapping[str, Any] | None = None,
        passive_cache: Mapping[str, Any] | None = None,
        state: BindingStoreState | None = None,
    ) -> BindingRuntimeSnapshot:
        persisted = state or self.load()
        return BindingRuntimeSnapshot.capture(
            config_bindings,
            persisted.bindings,
            claims=claims,
            conflicts=conflicts,
            passive_cache=passive_cache,
            revision=persisted.revision,
            updated_at=persisted.updated_at,
            window_max_length=self.window_max_length,
            persona_max_length=self.persona_max_length,
            persona_normalizer=self.persona_normalizer,
        )

    def _check_plan_revision(
        self, snapshot: BindingRuntimeSnapshot, expected_revision: int | None
    ) -> None:
        if expected_revision is not None:
            expected = _nonnegative_int(expected_revision)
            if expected != snapshot.revision:
                raise BindingRevisionConflict(expected, snapshot.revision)

    def _plan(
        self,
        operation: str,
        snapshot: BindingRuntimeSnapshot,
        window_key: str,
        persona_id: str = "",
        *,
        update_config: bool = True,
        update_persisted: bool = True,
        expected_revision: int | None = None,
    ) -> BindingMutationPlan:
        self._check_plan_revision(snapshot, expected_revision)
        before = snapshot.clone()
        after = snapshot.clone()
        changed = False
        if operation == "upsert":
            if update_config and after.config_bindings.get(window_key) != persona_id:
                after.config_bindings[window_key] = persona_id
                changed = True
            if update_persisted and after.persisted_bindings.get(window_key) != persona_id:
                after.persisted_bindings[window_key] = persona_id
                changed = True
            if after.claims.get(window_key) != persona_id:
                after.claims[window_key] = persona_id
                changed = True
            if window_key in after.conflicts:
                after.conflicts.pop(window_key, None)
                changed = True
            if window_key in after.passive_cache:
                after.passive_cache.pop(window_key, None)
                changed = True
        elif operation == "delete":
            # Deletion is intentionally a true removal.  There is no tombstone
            # or exclusion marker, so automatic recognition may bind it again.
            if update_config and window_key in after.config_bindings:
                after.config_bindings.pop(window_key, None)
                changed = True
            if update_persisted and window_key in after.persisted_bindings:
                after.persisted_bindings.pop(window_key, None)
                changed = True
            for mapping in (after.claims, after.conflicts, after.passive_cache):
                if window_key in mapping:
                    mapping.pop(window_key, None)
                    changed = True
        else:
            raise ValueError(f"unknown binding mutation operation: {operation}")
        if changed:
            after.revision += 1
            after.updated_at = self.clock()
        return BindingMutationPlan(
            operation=operation,
            window_key=window_key,
            persona_id=persona_id,
            before=before,
            after=after,
            changed=changed,
        )

    def plan_upsert(
        self,
        snapshot: BindingRuntimeSnapshot,
        window_key: Any,
        persona_id: Any,
        *,
        expected_revision: int | None = None,
        update_config: bool = True,
        update_persisted: bool = True,
    ) -> BindingMutationPlan:
        window = normalize_window(window_key, max_length=self.window_max_length)
        persona = normalize_persona_id(persona_id, max_length=self.persona_max_length)
        if not window:
            raise BindingStoreValidationError("window key must be non-empty, single-line, and within the length limit")
        if not persona:
            raise BindingStoreValidationError("persona ID must be non-empty, single-line, and within the length limit")
        return self._plan(
            "upsert",
            snapshot,
            window,
            persona,
            update_config=update_config,
            update_persisted=update_persisted,
            expected_revision=expected_revision,
        )

    def plan_delete(
        self,
        snapshot: BindingRuntimeSnapshot,
        window_key: Any,
        *,
        expected_revision: int | None = None,
        update_config: bool = True,
        update_persisted: bool = True,
    ) -> BindingMutationPlan:
        window = normalize_window(window_key, max_length=self.window_max_length)
        if not window:
            raise BindingStoreValidationError("window key must be non-empty, single-line, and within the length limit")
        return self._plan(
            "delete",
            snapshot,
            window,
            update_config=update_config,
            update_persisted=update_persisted,
            expected_revision=expected_revision,
        )

    def persist_plan(self, plan: BindingMutationPlan) -> BindingStoreState:
        """Persist the plan's file mapping with its before revision.

        The caller must update AstrBot config and runtime maps separately.  If
        one of those writes fails, call :meth:`save_snapshot` with
        ``plan.rollback_persisted_state`` and restore ``plan.rollback_snapshot``
        into the in-memory config/claims/conflicts/cache maps.
        """

        if not isinstance(plan, BindingMutationPlan):
            raise TypeError("plan must be BindingMutationPlan")
        return self.save(
            plan.after.persisted_bindings,
            expected_revision=plan.before.revision,
            revision=plan.after.revision,
        )


# Short aliases for integrations that prefer the generic names.
WindowBindingStore = PersonaWindowBindingStore
WindowBindingSnapshot = BindingRuntimeSnapshot
WindowBindingMutationPlan = BindingMutationPlan


__all__ = [
    "STORE_VERSION",
    "DEFAULT_WINDOW_MAX_LENGTH",
    "BindingStoreError",
    "BindingStoreValidationError",
    "BindingRevisionConflict",
    "BindingStoreState",
    "BindingRuntimeSnapshot",
    "BindingMutationPlan",
    "PersonaWindowBindingStore",
    "WindowBindingStore",
    "WindowBindingSnapshot",
    "WindowBindingMutationPlan",
    "normalize_window",
    "normalize_persona_id",
    "normalize_bindings",
    "merge_bindings",
]
