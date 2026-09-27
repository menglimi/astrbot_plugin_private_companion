from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, contextmanager
from copy import deepcopy
import functools
import hashlib
import hmac
import json
import secrets
import sys
import threading
import time
from types import ModuleType
from typing import Any, AsyncIterator, Awaitable, Callable, Iterator, Mapping

from .story_migration_contract import (
    MAX_SNAPSHOT_BYTES,
    STORY_MIGRATION_OWNER_ID,
    STORY_MIGRATION_SNAPSHOT_VERSION,
    StoryMigrationSnapshotError,
    build_story_migration_snapshot,
    canonical_story_snapshot_payload,
)


from .story_authority_shared import (
    STORY_HANDOFF_DRAIN_TIMEOUT_SECONDS,
    STORY_HANDOFF_LEASE_TTL_SECONDS,
    STORY_HANDOFF_LEASE_VERSION,
    STORY_HANDOFF_TARGET_PLUGIN_ID,
    StoryAuthorityError,
    _STORY_SNAPSHOT_ENVELOPE_KEYS,
    _resolve_future,
    _validate_pinned_snapshot_identity,
)
from .story_authority_shared import STORY_HANDOFF_DRAIN_TIMEOUT_SECONDS
from .story_authority_part02 import _StoryAuthorityControllerPart02Mixin
from .story_authority_part01 import _StoryAuthorityControllerPart01Mixin
class _StoryAuthorityController(_StoryAuthorityControllerPart01Mixin, _StoryAuthorityControllerPart02Mixin):
    """Process-wide legacy Story writer gate and durable handoff fence."""

    _schema_version = 3
    _replay_recoverable_blocks = frozenset(
        {
            "story_handoff_committed_target_conflict",
            "story_handoff_target_commit_unconfirmed",
            "story_handoff_target_status_invalid",
        }
    )

    def __init__(
        self,
        *,
        drain_timeout_seconds: float = STORY_HANDOFF_DRAIN_TIMEOUT_SECONDS,
        lease_ttl_seconds: float = STORY_HANDOFF_LEASE_TTL_SECONDS,
    ) -> None:
        self._lock = threading.RLock()
        self._state = "created"
        self._active_generation = ""
        self._depths: dict[Any, int] = {}
        self._startup_depths: dict[Any, int] = {}
        self._prepare_bindings: dict[Any, tuple[str, str]] = {}
        self._inspection_bindings: dict[Any, tuple[str, str, int]] = {}
        self._waiters: dict[
            str, tuple[asyncio.AbstractEventLoop, asyncio.Future[None]]
        ] = {}
        self._drain_id = ""
        self._lease: dict[str, Any] | None = None
        self._last_token_digest = ""
        self._last_token_generation = ""
        self._last_token_reason = ""
        self._commit_marker: dict[str, Any] | None = None
        self._commit_marker_source_verified = False
        self._blocked_reason = ""
        self._drain_timeout_seconds = float(drain_timeout_seconds)
        self._lease_ttl_seconds = float(lease_ttl_seconds)


_STORY_AUTHORITY_RUNTIME_KEY = "_astrbot_private_companion_story_authority_runtime_v1"


def _install_story_authority_runtime() -> ModuleType:
    candidate = ModuleType(_STORY_AUTHORITY_RUNTIME_KEY)
    candidate.error_type = StoryAuthorityError
    candidate.controller = _StoryAuthorityController()
    return sys.modules.setdefault(_STORY_AUTHORITY_RUNTIME_KEY, candidate)


def _upgrade_story_authority_runtime(runtime: ModuleType) -> ModuleType:
    """Upgrade the S2 singleton in place while its original lock is held."""

    controller = getattr(runtime, "controller", None)
    lock = getattr(controller, "_lock", None)
    if controller is None or lock is None or not hasattr(lock, "__enter__"):
        raise RuntimeError("story_authority_runtime_invalid")
    with lock:
        previous_state = str(getattr(controller, "_state", "blocked") or "blocked")
        if int(getattr(controller.__class__, "_schema_version", 0) or 0) < 3:
            controller.__class__ = _StoryAuthorityController
        defaults = {
            "_startup_depths": {},
            "_commit_marker": None,
            "_commit_marker_source_verified": False,
            "_blocked_reason": "",
        }
        for name, value in defaults.items():
            if not hasattr(controller, name):
                setattr(controller, name, value)
        allowed = {
            "created",
            "open",
            "draining",
            "leased",
            "committing",
            "committed",
            "blocked",
        }
        if previous_state == "closed":
            controller._state = "created"
            controller._active_generation = ""
        elif previous_state not in allowed:
            controller._state = "blocked"
            controller._blocked_reason = "story_authority_state_unknown"
    return runtime


_STORY_AUTHORITY_RUNTIME = _upgrade_story_authority_runtime(
    _install_story_authority_runtime()
)
# Every package alias exposes the exact same exception class and controller.
StoryAuthorityError = _STORY_AUTHORITY_RUNTIME.error_type
_STORY_AUTHORITY = _STORY_AUTHORITY_RUNTIME.controller


def story_authority_controller() -> _StoryAuthorityController:
    return _STORY_AUTHORITY


@contextmanager
def story_profile_inspection_context() -> Iterator[None]:
    with _STORY_AUTHORITY.strict_profile_inspection():
        yield


@contextmanager
def story_legacy_context(operation: str) -> Iterator[None]:
    identity = _STORY_AUTHORITY.enter_legacy_operation(operation)
    try:
        yield
    finally:
        _STORY_AUTHORITY.exit_legacy_operation(identity)


@asynccontextmanager
async def story_legacy_async_context(operation: str) -> AsyncIterator[None]:
    identity = _STORY_AUTHORITY.enter_legacy_operation(operation)
    try:
        yield
    finally:
        _STORY_AUTHORITY.exit_legacy_operation(identity)


def story_legacy_sync_operation(operation: str):
    def decorator(function):
        @functools.wraps(function)
        def wrapper(*args, **kwargs):
            with story_legacy_context(operation):
                return function(*args, **kwargs)

        wrapper.__story_authority_operation__ = operation
        return wrapper

    return decorator


def story_startup_sync_operation(operation: str):
    def decorator(function):
        @functools.wraps(function)
        def wrapper(*args, **kwargs):
            identity = _STORY_AUTHORITY.enter_startup_operation(operation)
            try:
                return function(*args, **kwargs)
            finally:
                _STORY_AUTHORITY.exit_startup_operation(identity)

        wrapper.__story_authority_operation__ = operation
        wrapper.__story_authority_startup__ = True
        return wrapper

    return decorator


def story_legacy_operation(operation: str):
    def decorator(function):
        @functools.wraps(function)
        async def wrapper(*args, **kwargs):
            async with story_legacy_async_context(operation):
                return await function(*args, **kwargs)

        wrapper.__story_authority_operation__ = operation
        return wrapper

    return decorator


def story_legacy_operation_if(
    operation: str,
    predicate: Callable[..., bool],
):
    def decorator(function):
        @functools.wraps(function)
        async def wrapper(*args, **kwargs):
            if not predicate(*args, **kwargs):
                return await function(*args, **kwargs)
            async with story_legacy_async_context(operation):
                return await function(*args, **kwargs)

        wrapper.__story_authority_operation__ = operation
        wrapper.__story_authority_conditional__ = True
        return wrapper

    return decorator


def assert_single_persona_story_shelf(plugin: Any) -> None:
    """Prove every persisted persona shelf is empty before a handoff."""
    if not bool(getattr(plugin, "enable_multi_persona_mode", False)):
        return
    ids_getter = getattr(plugin, "_persona_profile_ids", None)
    snapshot_getter = getattr(plugin, "_persona_profile_snapshot_read_only", None)
    primary_getter = getattr(plugin, "_primary_persona_id", None)
    if not all(
        callable(candidate)
        for candidate in (
            ids_getter,
            snapshot_getter,
            primary_getter,
        )
    ):
        raise StoryAuthorityError("story_handoff_multi_persona_unverifiable")
    try:
        primary = str(primary_getter() or "")
        persona_ids = list(ids_getter(strict=True))
        if not primary or primary not in persona_ids:
            raise ValueError("primary persona is not enumerable")
    except Exception:
        raise StoryAuthorityError(
            "story_handoff_multi_persona_unverifiable"
        ) from None
    primary_store = getattr(plugin, "_data_default", None)
    if type(primary_store) is not dict:
        raise StoryAuthorityError("story_handoff_multi_persona_unverifiable")
    stores: list[dict[str, Any]] = [primary_store]
    profiles = getattr(plugin, "_persona_data_profiles", None)
    if type(profiles) is not dict:
        raise StoryAuthorityError("story_handoff_multi_persona_unverifiable")
    # Cache and disk are independent evidence: neither may hide the other.
    for profile in profiles.values():
        if type(profile) is not dict:
            raise StoryAuthorityError("story_handoff_multi_persona_unverifiable")
        stores.append(profile)
    for raw_persona_id in persona_ids:
        persona_id = str(raw_persona_id or "")
        if not persona_id:
            continue
        try:
            profile = snapshot_getter(persona_id)
        except StoryAuthorityError:
            raise
        except Exception:
            raise StoryAuthorityError(
                "story_handoff_multi_persona_unverifiable"
            ) from None
        if profile is not None and type(profile) is not dict:
            raise StoryAuthorityError(
                "story_handoff_multi_persona_unverifiable"
            )
        if profile is not None:
            stores.append(profile)
    seen: set[int] = set()
    for store in stores:
        if id(store) in seen:
            continue
        seen.add(id(store))
        projects = store.get("creative_projects", [])
        if type(projects) is not list:
            raise StoryAuthorityError("story_handoff_multi_persona_unverifiable")
        if projects:
            raise StoryAuthorityError("story_handoff_multi_persona_unsupported")
