# -*- coding: utf-8 -*-
"""Process-stable generation fencing for replace-based persistence paths."""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any


_RUNTIME_KEY = "_astrbot_private_companion_persistence_generation_v1"


def _new_runtime() -> ModuleType:
    runtime = ModuleType(_RUNTIME_KEY)
    runtime.guard = threading.RLock()
    runtime.paths = {}
    return runtime


# A normal module global is replaced by Python hot reload.  The sys.modules
# anchor deliberately survives loading the next plugin module generation.
_runtime = sys.modules.setdefault(_RUNTIME_KEY, _new_runtime())


def canonical_persistence_path(path: str | Path) -> str:
    try:
        resolved = str(Path(path).resolve())
    except Exception:
        resolved = os.path.abspath(os.fspath(path))
    return os.path.normcase(resolved)


def _path_state(path: str | Path) -> Any:
    marker = canonical_persistence_path(path)
    with _runtime.guard:
        state = _runtime.paths.get(marker)
        if state is None:
            state = SimpleNamespace(
                marker=marker,
                owner="",
                generation=0,
                next_sequence=0,
                committed_sequence=0,
                prepare_lock=threading.RLock(),
                replace_lock=threading.RLock(),
            )
            _runtime.paths[marker] = state
        return state


def shared_prepare_lock(path: str | Path) -> threading.RLock:
    """Return the reload-stable lock for read/merge preparation only."""

    return _path_state(path).prepare_lock


def activate_persistence_owner(owner_token: str, paths: list[str | Path]) -> dict[str, int]:
    """Publish ``owner_token`` as the only writer generation for ``paths``."""

    owner = str(owner_token or "").strip()
    if not owner:
        raise ValueError("persistence owner token is required")
    activated: dict[str, int] = {}
    for path in paths:
        state = _path_state(path)
        with state.replace_lock:
            if state.owner != owner:
                state.owner = owner
                state.generation = max(0, int(state.generation or 0)) + 1
                state.next_sequence = 0
                state.committed_sequence = 0
            activated[state.marker] = int(state.generation)
    return activated


def capture_write_ticket(path: str | Path, owner_token: str = "") -> dict[str, Any]:
    """Capture owner generation and an intra-generation write sequence."""

    state = _path_state(path)
    owner = str(owner_token or "").strip()
    with state.replace_lock:
        # The first live instance may persist during startup before publication.
        # A later staged instance never steals the existing owner; publication
        # is the sole operation allowed to advance an occupied generation.
        if owner and not state.owner:
            state.owner = owner
            state.generation = max(0, int(state.generation or 0)) + 1
            state.next_sequence = 0
            state.committed_sequence = 0
        state.next_sequence = max(0, int(state.next_sequence or 0)) + 1
        return {
            "marker": state.marker,
            "owner": owner,
            "generation": int(state.generation or 0),
            "sequence": int(state.next_sequence),
        }


def replace_if_ticket_current(
    temporary_path: str | Path,
    target_path: str | Path,
    ticket: dict[str, Any],
    *,
    attempts: int = 6,
) -> bool:
    """Validate immediately before, and under the same lock as, ``replace``."""

    state = _path_state(target_path)
    owner = str(ticket.get("owner") or "")
    generation = int(ticket.get("generation") or 0)
    sequence = int(ticket.get("sequence") or 0)
    with state.replace_lock:
        if ticket.get("marker") != state.marker:
            return False
        if state.owner and (
            not owner
            or state.owner != owner
            or int(state.generation or 0) != generation
        ):
            return False
        if sequence <= int(state.committed_sequence or 0):
            return False
        last_error: Exception | None = None
        for attempt in range(max(1, int(attempts or 1))):
            try:
                os.replace(os.fspath(temporary_path), os.fspath(target_path))
                state.committed_sequence = max(
                    int(state.committed_sequence or 0), sequence
                )
                return True
            except PermissionError as exc:
                last_error = exc
                time.sleep(0.05 * (attempt + 1))
            except OSError as exc:
                last_error = exc
                if getattr(exc, "winerror", 0) not in {32, 33, 5}:
                    raise
                time.sleep(0.05 * (attempt + 1))
        if last_error is not None:
            raise last_error
    return False


__all__ = [
    "activate_persistence_owner",
    "canonical_persistence_path",
    "capture_write_ticket",
    "replace_if_ticket_current",
    "shared_prepare_lock",
]
