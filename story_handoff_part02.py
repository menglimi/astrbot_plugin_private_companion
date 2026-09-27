# -*- coding: utf-8 -*-
"""story_handoff 拆分件 part02（机械搬移，行为不变）。

函数 / 常量 / 类逐字搬自 story_handoff.py，仅调整模块级依赖的导入来源。
"""
import asyncio
from copy import deepcopy
import inspect
import time
from typing import Any, Mapping
from .story_authority import (
    STORY_HANDOFF_TARGET_PLUGIN_ID,
    StoryAuthorityError,
)
from .story_handoff_shared import _story_handoff_host
from .story_migration_contract import STORY_MIGRATION_OWNER_ID

from .story_handoff_part01 import (
    EnforcedStoryTarget,
    STORY_MIGRATION_COMMIT_KEY,
    STORY_MIGRATION_COMMIT_VERSION,
    _ABSENT,
    _Target,
    _TargetChanged,
    _confirm_persisted_source_marker,
    _fail,
    _fresh_target,
    _matches_marker,
    _matches_snapshot,
    _source_marker,
    _source_state,
    _target_status,
    _validate_target_status,
    validate_story_migration_commit_marker,
)


async def _await_target_call(
    plugin: Any,
    target: _Target,
    method_name: str,
    *args: Any,
    **kwargs: Any,
) -> tuple[Any, _Target]:
    """Harvest one target await and then re-resolve its exact generation."""

    before = _fresh_target(plugin, target)
    handler = getattr(before.api, method_name, None)
    if not callable(handler):
        _fail("story_handoff_target_capability_missing")
    try:
        operation = handler(*args, **kwargs)
    except Exception:
        _fail("story_handoff_target_call_failed")
    if not inspect.isawaitable(operation):
        _fail("story_handoff_target_descriptor_invalid")
    task = asyncio.ensure_future(operation)
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
        except BaseException:
            # Harvest and translate target failures only after revalidating the
            # exact Content generation below.
            pass
    error: BaseException | None = None
    result: Any = None
    try:
        result = task.result()
    except BaseException as exc:
        error = exc
    try:
        after = _fresh_target(plugin, before)
    except StoryAuthorityError:
        if cancelled:
            raise asyncio.CancelledError
        raise
    if cancelled:
        raise asyncio.CancelledError
    if error is not None:
        if isinstance(error, asyncio.CancelledError):
            raise error
        raise StoryAuthorityError("story_handoff_target_call_failed") from None
    return result, after


def _block(code: str) -> None:
    _story_handoff_host.story_authority_controller().block(code)
    raise StoryAuthorityError(code)


async def _persist_source_marker(
    plugin: Any,
    *,
    generation: str,
    lease_token: str,
    snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    lock = getattr(plugin, "_data_lock", None)
    if (
        lock is None
        or not hasattr(lock, "__aenter__")
        or not hasattr(lock, "__aexit__")
    ):
        _fail("story_handoff_source_state_unavailable")
    controller = _story_handoff_host.story_authority_controller()
    async with lock:
        data = _source_state(plugin)
        if data is None:
            _fail("story_handoff_source_state_unavailable")
        baseline = deepcopy(data)
        existing = data.get(STORY_MIGRATION_COMMIT_KEY, _ABSENT)
        existing_present = existing is not _ABSENT
        marker_durable = False
        marker: dict[str, Any] | None = None
        try:
            if existing is _ABSENT:
                marker = {
                    "version": STORY_MIGRATION_COMMIT_VERSION,
                    "snapshot_id": snapshot["snapshot_id"],
                    "snapshot_sha256": snapshot["snapshot_sha256"],
                    "target_plugin_id": STORY_HANDOFF_TARGET_PLUGIN_ID,
                    "owner_id": STORY_MIGRATION_OWNER_ID,
                    "committed_at": time.time(),
                }
                marker = validate_story_migration_commit_marker(marker)
                data[STORY_MIGRATION_COMMIT_KEY] = deepcopy(marker)
            else:
                marker = validate_story_migration_commit_marker(existing)
                if (
                    marker["snapshot_id"] != snapshot["snapshot_id"]
                    or marker["snapshot_sha256"] != snapshot["snapshot_sha256"]
                ):
                    _confirm_persisted_source_marker(plugin, marker)
                    marker_durable = True
                    _fail("story_handoff_marker_conflict")

            if not marker_durable:
                saver = getattr(
                    plugin,
                    "_save_story_migration_commit_confirmed_sync",
                    None,
                )
                if not callable(saver):
                    _fail("story_handoff_marker_persistence_unavailable")
                save_error: BaseException | None = None
                try:
                    saver(marker)
                except BaseException as exc:
                    save_error = exc
                try:
                    _confirm_persisted_source_marker(plugin, marker)
                except BaseException:
                    if save_error is not None:
                        raise save_error
                    raise
                # No await is permitted between this confirmation and the
                # in-process committed fence below.
                marker_durable = True
            try:
                controller.finish_commit(
                    generation=generation,
                    lease_token=lease_token,
                    marker=marker,
                )
            except BaseException:
                if not marker_durable:
                    raise
                # Persistence is the point of no return. Even an unexpected
                # controller receipt failure must recover the same marker and
                # can never restore the pre-marker baseline.
                data[STORY_MIGRATION_COMMIT_KEY] = deepcopy(marker)
                controller.recover_committed_marker(marker, source_verified=True)
            return deepcopy(marker)
        except BaseException:
            if marker_durable and marker is not None:
                data[STORY_MIGRATION_COMMIT_KEY] = deepcopy(marker)
                controller.recover_committed_marker(marker, source_verified=True)
                raise
            if existing_present:
                controller.block("story_handoff_marker_persistence_unconfirmed")
                raise
            data.clear()
            data.update(baseline)
            raise


async def _abort_target_exact(plugin: Any, snapshot: Mapping[str, Any]) -> None:
    for _attempt in range(4):
        target = _fresh_target(plugin)
        status = _target_status(target)
        if status["status"] == "absent":
            return
        if status["status"] == "committed":
            _block("story_handoff_split_brain")
        if status["status"] == "aborted":
            # An aborted ledger has no live target transaction to release.
            # Its snapshot may predate the current source lease.
            return
        if status["status"] != "prepared" or not _matches_snapshot(status, snapshot):
            _block("story_handoff_target_conflict")
        try:
            result, target = await _await_target_call(
                plugin,
                target,
                "abort_story_migration",
                snapshot_id=snapshot["snapshot_id"],
                snapshot_sha256=snapshot["snapshot_sha256"],
            )
            confirmed = _validate_target_status(result)
            if confirmed["status"] == "aborted" and _matches_snapshot(
                confirmed, snapshot
            ):
                return
            _block("story_handoff_target_abort_unconfirmed")
        except _TargetChanged:
            continue
    _block("story_handoff_target_abort_unconfirmed")


async def _cleanup_before_marker(
    plugin: Any,
    *,
    generation: str,
    lease_token: str,
    snapshot: Mapping[str, Any],
) -> None:
    cleanup = asyncio.create_task(_abort_target_exact(plugin, snapshot))
    cancelled = False
    while not cleanup.done():
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            cancelled = True
    try:
        cleanup.result()
        _story_handoff_host.story_authority_controller().abort_before_marker(
            generation=generation,
            lease_token=lease_token,
        )
    except BaseException:
        _story_handoff_host.story_authority_controller().block(
            "story_handoff_target_abort_unconfirmed"
        )
        raise
    if cancelled:
        raise asyncio.CancelledError


async def _replay_committed_marker(
    plugin: Any,
    marker: Mapping[str, Any],
) -> dict[str, Any]:
    pinned = validate_story_migration_commit_marker(marker)
    for _attempt in range(8):
        target = _fresh_target(plugin)
        try:
            status = _target_status(target)
        except StoryAuthorityError as exc:
            if exc.code == "story_handoff_target_status_invalid":
                _block(exc.code)
            raise
        if _matches_marker(status, pinned):
            return {
                "status": "committed",
                "marker": deepcopy(pinned),
                "target": status,
                "replayed": False,
            }
        if status["status"] != "prepared" or not _matches_snapshot(status, pinned):
            _block("story_handoff_committed_target_conflict")
        try:
            result, _target = await _await_target_call(
                plugin,
                target,
                "commit_story_migration",
                deepcopy(pinned),
            )
        except _TargetChanged:
            continue
        try:
            confirmed = _validate_target_status(result)
        except StoryAuthorityError as exc:
            _block(exc.code)
        if not _matches_marker(confirmed, pinned):
            _block("story_handoff_target_commit_unconfirmed")
        return {
            "status": "committed",
            "marker": deepcopy(pinned),
            "target": confirmed,
            "replayed": True,
        }
    raise StoryAuthorityError("story_handoff_target_generation_unstable")


def _private_target(expected: EnforcedStoryTarget | None) -> _Target | None:
    if expected is None:
        return None
    return _Target(
        api=expected.api,
        generation=expected.generation,
        descriptor=deepcopy(expected.descriptor),
    )


async def resolve_enforced_story_target(
    plugin: Any,
    *,
    expected: EnforcedStoryTarget | None = None,
) -> EnforcedStoryTarget:
    """Resolve the sole post-marker Story writer and reprove both ledgers.

    This boundary deliberately performs a fresh Content resolution, checks the
    live primary marker, and requires the controller's exact durable-readback
    proof on every use.  A current Content API remains standby until
    Companion's controller is committed, and no legacy fallback is possible
    once that durable marker exists.
    """

    controller = _story_handoff_host.story_authority_controller()
    if controller.authority_state() != "committed":
        _fail("story_handoff_not_committed")
    marker = controller.committed_marker()
    if marker is None:
        _block("story_handoff_marker_missing")
    pinned = validate_story_migration_commit_marker(marker)
    if not controller.committed_marker_source_verified(pinned):
        _fail("story_handoff_marker_persistence_unconfirmed")
    marker_present, source_value = await _source_marker(plugin)
    if not marker_present:
        _block("story_handoff_marker_missing")
    try:
        source_marker = validate_story_migration_commit_marker(source_value)
    except StoryAuthorityError as exc:
        _block(exc.code)
    if source_marker != pinned:
        _block("story_handoff_marker_conflict")

    await _replay_committed_marker(plugin, pinned)
    target = _fresh_target(plugin, _private_target(expected))
    capabilities = target.descriptor["capabilities"]
    if "story.handoff.enforced" not in capabilities:
        _fail("story_handoff_enforcement_unavailable")
    status = _target_status(target)
    if not _matches_marker(status, pinned):
        _block("story_handoff_committed_target_conflict")
    return EnforcedStoryTarget(
        api=target.api,
        generation=target.generation,
        descriptor=deepcopy(target.descriptor),
        marker=deepcopy(pinned),
    )


async def call_enforced_story_target(
    plugin: Any,
    target: EnforcedStoryTarget,
    method_name: str,
    *args: Any,
    **kwargs: Any,
) -> tuple[Any, EnforcedStoryTarget]:
    """Harvest one target await and reprove the same enforced generation."""

    before = await resolve_enforced_story_target(plugin, expected=target)
    handler = getattr(before.api, method_name, None)
    if not callable(handler):
        _fail("story_handoff_target_capability_missing")
    try:
        operation = handler(*args, **kwargs)
    except Exception:
        _fail("story_handoff_target_call_failed")
    if not inspect.isawaitable(operation):
        _fail("story_handoff_target_descriptor_invalid")
    task = asyncio.ensure_future(operation)
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    error: BaseException | None = None
    result: Any = None
    try:
        result = task.result()
    except BaseException as exc:
        error = exc
    try:
        after = await resolve_enforced_story_target(plugin, expected=before)
    except StoryAuthorityError:
        if cancelled:
            raise asyncio.CancelledError
        raise
    if cancelled:
        raise asyncio.CancelledError
    if error is not None:
        if isinstance(error, asyncio.CancelledError):
            raise error
        raise StoryAuthorityError("story_handoff_target_call_failed") from None
    return result, after


async def _verify_controller_marker(
    plugin: Any,
    controller: Any,
    marker: Mapping[str, Any],
) -> dict[str, Any]:
    """Upgrade an in-memory marker to an exact persisted source proof."""

    pinned = validate_story_migration_commit_marker(marker)
    if controller.authority_state() == "blocked":
        _fail("story_handoff_blocked")
    marker_present, marker_value = await _source_marker(plugin)
    if not marker_present:
        controller.block("story_handoff_marker_missing")
        _fail("story_handoff_marker_missing")
    try:
        source_marker = validate_story_migration_commit_marker(marker_value)
        if source_marker != pinned:
            _fail("story_handoff_marker_conflict")
        _confirm_persisted_source_marker(plugin, source_marker)
        controller.recover_committed_marker(
            source_marker,
            source_verified=True,
        )
    except StoryAuthorityError as exc:
        controller.block(exc.code)
        raise
    return source_marker
