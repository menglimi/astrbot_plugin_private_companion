from __future__ import annotations

import asyncio
from copy import deepcopy
import inspect
import math
import time
from dataclasses import dataclass
from typing import Any, Mapping

from .story_authority import (
    STORY_HANDOFF_TARGET_PLUGIN_ID,
    StoryAuthorityError,
    story_authority_controller,
)
from .story_migration_contract import STORY_MIGRATION_OWNER_ID


STORY_MIGRATION_COMMIT_KEY = "story_migration_commit"
STORY_MIGRATION_COMMIT_VERSION = "companion.story-migration-commit.v1"
STORY_SOURCE_PLUGIN_ID = STORY_MIGRATION_OWNER_ID
_MARKER_FIELDS = frozenset(
    {
        "version",
        "snapshot_id",
        "snapshot_sha256",
        "target_plugin_id",
        "owner_id",
        "committed_at",
    }
)
_TARGET_DESCRIPTOR_FIELDS = frozenset(
    {
        "plugin_id",
        "instance_generation",
        "api_family",
        "api_version",
        "supported_task_versions",
        "capabilities",
        "lifecycle_state",
        "degraded_reasons",
    }
)
_TARGET_API_FAMILY = "content.story"
_TARGET_API_VERSION = "content.story-api.v1"
_TARGET_TASK_VERSIONS = (
    "content.story-task.v1",
    "content.story-task.v2",
)
_TARGET_MIGRATION_CAPABILITIES = frozenset(
    {
        "story.migration.abort",
        "story.migration.commit",
        "story.migration.prepare",
        "story.migration.status",
    }
)
_TARGET_LEDGER_VERSION = "content.story-migration-ledger.v1"
_ABSENT = object()


def _fail(code: str) -> None:
    raise StoryAuthorityError(code)


def _exact_dict(value: Any, fields: frozenset[str], code: str) -> dict[str, Any]:
    if (
        type(value) is not dict
        or any(type(key) is not str for key in value)
        or set(value) != fields
    ):
        _fail(code)
    return value


def _digest(value: Any, code: str) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        _fail(code)
    return value


def _generation(value: Any, code: str) -> str:
    if (
        type(value) is not str
        or len(value) != 32
        or any(character not in "0123456789abcdef" for character in value)
    ):
        _fail(code)
    return value


def _timestamp(value: Any, code: str) -> int | float:
    if (
        type(value) not in (int, float)
        or not math.isfinite(float(value))
        or not 0.0 <= float(value) <= 10_000_000_000.0
    ):
        _fail(code)
    return value


def validate_story_migration_commit_marker(value: Any) -> dict[str, Any]:
    """Validate the exact six-field source-owned durable commit marker."""

    marker = _exact_dict(
        value,
        _MARKER_FIELDS,
        "story_handoff_marker_invalid",
    )
    if (
        type(marker["version"]) is not str
        or marker["version"] != STORY_MIGRATION_COMMIT_VERSION
    ):
        _fail("story_handoff_marker_version_unsupported")
    digest = _digest(marker["snapshot_sha256"], "story_handoff_marker_invalid")
    if (
        type(marker["snapshot_id"]) is not str
        or marker["snapshot_id"] != f"storysnap_{digest}"
    ):
        _fail("story_handoff_marker_invalid")
    if (
        type(marker["target_plugin_id"]) is not str
        or marker["target_plugin_id"] != STORY_HANDOFF_TARGET_PLUGIN_ID
    ):
        _fail("story_handoff_marker_target_mismatch")
    if (
        type(marker["owner_id"]) is not str
        or marker["owner_id"] != STORY_MIGRATION_OWNER_ID
    ):
        _fail("story_handoff_marker_owner_mismatch")
    _timestamp(marker["committed_at"], "story_handoff_marker_invalid")
    return deepcopy(marker)


def preflight_story_handoff_sections(
    *sections: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Fence a valid marker, or block malformed/conflicting startup stores."""

    controller = story_authority_controller()
    markers: list[dict[str, Any]] = []
    try:
        for section_map in sections:
            if not isinstance(section_map, Mapping):
                continue
            if STORY_MIGRATION_COMMIT_KEY not in section_map:
                continue
            markers.append(
                validate_story_migration_commit_marker(
                    section_map[STORY_MIGRATION_COMMIT_KEY]
                )
            )
        if not markers:
            controller.assert_marker_absent()
            return None
        marker = markers[0]
        if any(candidate != marker for candidate in markers[1:]):
            _fail("story_handoff_marker_conflict")
        controller.recover_committed_marker(marker, source_verified=True)
        return deepcopy(marker)
    except StoryAuthorityError as exc:
        controller.block(exc.code)
        raise
    except Exception:
        controller.block("story_handoff_marker_invalid")
        raise StoryAuthorityError("story_handoff_marker_invalid") from None


@dataclass(frozen=True)
class _Target:
    api: Any
    generation: str
    descriptor: dict[str, Any]


@dataclass(frozen=True)
class EnforcedStoryTarget:
    """Exact Content generation authorized by both durable handoff ledgers."""

    api: Any
    generation: str
    descriptor: dict[str, Any]
    marker: dict[str, Any]


class _TargetChanged(StoryAuthorityError):
    def __init__(self) -> None:
        super().__init__("story_handoff_target_generation_changed")


def _validate_target_descriptor(api: Any, value: Any) -> _Target:
    descriptor = _exact_dict(
        value,
        _TARGET_DESCRIPTOR_FIELDS,
        "story_handoff_target_descriptor_invalid",
    )
    if (
        type(descriptor["plugin_id"]) is not str
        or descriptor["plugin_id"] != STORY_HANDOFF_TARGET_PLUGIN_ID
    ):
        _fail("story_handoff_target_identity_mismatch")
    generation = _generation(
        descriptor["instance_generation"],
        "story_handoff_target_descriptor_invalid",
    )
    if (
        type(descriptor["api_family"]) is not str
        or type(descriptor["api_version"]) is not str
        or descriptor["api_family"] != _TARGET_API_FAMILY
        or descriptor["api_version"] != _TARGET_API_VERSION
    ):
        _fail("story_handoff_target_version_unsupported")
    versions = descriptor["supported_task_versions"]
    if (
        type(versions) is not list
        or tuple(versions) != _TARGET_TASK_VERSIONS
        or any(type(item) is not str for item in versions)
    ):
        _fail("story_handoff_target_version_unsupported")
    capabilities = descriptor["capabilities"]
    if (
        type(capabilities) is not list
        or any(type(item) is not str or not item for item in capabilities)
        or len(capabilities) != len(set(capabilities))
        or not _TARGET_MIGRATION_CAPABILITIES.issubset(capabilities)
    ):
        _fail("story_handoff_target_capability_missing")
    degraded = descriptor["degraded_reasons"]
    if (
        type(descriptor["lifecycle_state"]) is not str
        or descriptor["lifecycle_state"] != "ready"
        or type(degraded) is not list
        or degraded
    ):
        _fail("story_handoff_target_not_ready")
    for method_name in (
        "story_migration_status",
        "prepare_story_migration",
        "commit_story_migration",
        "abort_story_migration",
    ):
        if not callable(getattr(api, method_name, None)):
            _fail("story_handoff_target_capability_missing")
    return _Target(api=api, generation=generation, descriptor=deepcopy(descriptor))


def _fresh_target(plugin: Any, expected: _Target | None = None) -> _Target:
    resolver = getattr(plugin, "_content_companion_api_fresh", None)
    if not callable(resolver):
        _fail("story_handoff_target_unavailable")
    try:
        api = resolver()
    except Exception:
        _fail("story_handoff_target_unavailable")
    if api is None:
        if expected is not None:
            raise _TargetChanged()
        _fail("story_handoff_target_unavailable")
    capabilities = getattr(api, "capabilities", None)
    if not callable(capabilities):
        _fail("story_handoff_target_capability_missing")
    try:
        target = _validate_target_descriptor(api, capabilities())
    except StoryAuthorityError:
        raise
    except Exception:
        _fail("story_handoff_target_descriptor_invalid")
    if expected is not None and (
        target.api is not expected.api
        or target.generation != expected.generation
    ):
        raise _TargetChanged()
    return target


def _validate_backup(value: Any) -> None:
    backup = _exact_dict(
        value,
        frozenset({"sha256", "size", "existed"}),
        "story_handoff_target_status_invalid",
    )
    _digest(backup["sha256"], "story_handoff_target_status_invalid")
    if (
        type(backup["size"]) is not int
        or not 0 <= backup["size"] <= 32 * 1024 * 1024
        or type(backup["existed"]) is not bool
    ):
        _fail("story_handoff_target_status_invalid")


def _validate_snapshot_identity(snapshot_id: Any, snapshot_sha256: Any) -> None:
    digest = _digest(snapshot_sha256, "story_handoff_target_status_invalid")
    if type(snapshot_id) is not str or snapshot_id != f"storysnap_{digest}":
        _fail("story_handoff_target_status_invalid")


def _validate_target_status(value: Any) -> dict[str, Any]:
    if type(value) is not dict or any(type(key) is not str for key in value):
        _fail("story_handoff_target_status_invalid")
    status = value.get("status")
    if type(status) is not str:
        _fail("story_handoff_target_status_invalid")
    if status == "absent":
        result = _exact_dict(
            value,
            frozenset({"version", "status", "target_plugin_id", "owner_id"}),
            "story_handoff_target_status_invalid",
        )
    elif status == "prepared":
        result = _exact_dict(
            value,
            frozenset(
                {
                    "version",
                    "status",
                    "source_plugin_id",
                    "source_instance_generation",
                    "target_plugin_id",
                    "owner_id",
                    "snapshot_id",
                    "snapshot_sha256",
                    "prepared_at",
                    "baseline_sha256",
                    "backup",
                }
            ),
            "story_handoff_target_status_invalid",
        )
        if (
            type(result["source_plugin_id"]) is not str
            or result["source_plugin_id"] != STORY_SOURCE_PLUGIN_ID
        ):
            _fail("story_handoff_target_status_invalid")
        _generation(
            result["source_instance_generation"],
            "story_handoff_target_status_invalid",
        )
        _validate_snapshot_identity(result["snapshot_id"], result["snapshot_sha256"])
        _timestamp(result["prepared_at"], "story_handoff_target_status_invalid")
        _digest(result["baseline_sha256"], "story_handoff_target_status_invalid")
        _validate_backup(result["backup"])
    elif status == "committed":
        result = _exact_dict(
            value,
            frozenset(
                {
                    "version",
                    "status",
                    "source_plugin_id",
                    "source_instance_generation",
                    "marker",
                    "backup",
                }
            ),
            "story_handoff_target_status_invalid",
        )
        if (
            type(result["source_plugin_id"]) is not str
            or result["source_plugin_id"] != STORY_SOURCE_PLUGIN_ID
        ):
            _fail("story_handoff_target_status_invalid")
        _generation(
            result["source_instance_generation"],
            "story_handoff_target_status_invalid",
        )
        validate_story_migration_commit_marker(result["marker"])
        _validate_backup(result["backup"])
    elif status == "aborted":
        result = _exact_dict(
            value,
            frozenset(
                {
                    "version",
                    "status",
                    "source_plugin_id",
                    "source_instance_generation",
                    "target_plugin_id",
                    "owner_id",
                    "snapshot_id",
                    "snapshot_sha256",
                    "aborted_at",
                    "backup",
                }
            ),
            "story_handoff_target_status_invalid",
        )
        if (
            type(result["source_plugin_id"]) is not str
            or result["source_plugin_id"] != STORY_SOURCE_PLUGIN_ID
        ):
            _fail("story_handoff_target_status_invalid")
        _generation(
            result["source_instance_generation"],
            "story_handoff_target_status_invalid",
        )
        _validate_snapshot_identity(result["snapshot_id"], result["snapshot_sha256"])
        _timestamp(result["aborted_at"], "story_handoff_target_status_invalid")
        _validate_backup(result["backup"])
    else:
        _fail("story_handoff_target_status_invalid")
    if (
        type(result["version"]) is not str
        or result["version"] != _TARGET_LEDGER_VERSION
        or type(result.get("target_plugin_id", STORY_HANDOFF_TARGET_PLUGIN_ID))
        is not str
        or result.get("target_plugin_id", STORY_HANDOFF_TARGET_PLUGIN_ID)
        != STORY_HANDOFF_TARGET_PLUGIN_ID
        or type(result.get("owner_id", STORY_MIGRATION_OWNER_ID)) is not str
        or result.get("owner_id", STORY_MIGRATION_OWNER_ID)
        != STORY_MIGRATION_OWNER_ID
    ):
        _fail("story_handoff_target_status_invalid")
    return deepcopy(result)


def _target_status(target: _Target) -> dict[str, Any]:
    try:
        value = target.api.story_migration_status()
    except Exception:
        _fail("story_handoff_target_call_failed")
    return _validate_target_status(value)


def _matches_snapshot(status: Mapping[str, Any], snapshot: Mapping[str, Any]) -> bool:
    return bool(
        status.get("snapshot_id") == snapshot.get("snapshot_id")
        and status.get("snapshot_sha256") == snapshot.get("snapshot_sha256")
    )


def _matches_marker(status: Mapping[str, Any], marker: Mapping[str, Any]) -> bool:
    return bool(status.get("status") == "committed" and status.get("marker") == marker)


def _source_state(plugin: Any) -> dict[str, Any] | None:
    """Return Companion's primary durable store, never a scoped persona view."""

    data = getattr(plugin, "_data_default", None)
    if type(data) is dict:
        return data
    data = getattr(plugin, "data", None)
    return data if type(data) is dict else None


async def _source_marker(plugin: Any) -> tuple[bool, Any]:
    """Copy the primary marker while holding Companion's owning data lock."""

    lock = getattr(plugin, "_data_lock", None)
    if (
        lock is None
        or not hasattr(lock, "__aenter__")
        or not hasattr(lock, "__aexit__")
    ):
        _fail("story_handoff_source_state_unavailable")
    async with lock:
        data = _source_state(plugin)
        if data is None:
            _fail("story_handoff_source_state_unavailable")
        if STORY_MIGRATION_COMMIT_KEY not in data:
            return False, None
        return True, deepcopy(data[STORY_MIGRATION_COMMIT_KEY])


def _confirm_persisted_source_marker(
    plugin: Any,
    marker: Mapping[str, Any],
) -> None:
    """Require the primary durable store to contain the exact memory marker."""

    reader = getattr(plugin, "_read_story_migration_commit_persisted_sync", None)
    if not callable(reader):
        _fail("story_handoff_marker_persistence_unavailable")
    try:
        persisted = reader()
    except BaseException:
        _fail("story_handoff_marker_persistence_unconfirmed")
    if (
        type(persisted) is not tuple
        or len(persisted) != 2
        or persisted[0] is not True
        or persisted[1] != marker
    ):
        _fail("story_handoff_marker_persistence_unconfirmed")


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
    story_authority_controller().block(code)
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
    controller = story_authority_controller()
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
        story_authority_controller().abort_before_marker(
            generation=generation,
            lease_token=lease_token,
        )
    except BaseException:
        story_authority_controller().block(
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

    controller = story_authority_controller()
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
    if controller.committed_marker_source_verified(pinned):
        controller.recover_committed_marker(pinned)
        return pinned
    marker_present, marker_value = await _source_marker(plugin)
    if not marker_present:
        controller.block("story_handoff_marker_persistence_unconfirmed")
        _fail("story_handoff_marker_persistence_unconfirmed")
    try:
        source_marker = validate_story_migration_commit_marker(marker_value)
        _confirm_persisted_source_marker(plugin, source_marker)
        controller.recover_committed_marker(
            source_marker,
            source_verified=True,
        )
    except StoryAuthorityError as exc:
        controller.block(exc.code)
        raise
    return source_marker


async def commit_story_handoff(
    plugin: Any,
    *,
    generation: str,
    lease_token: str = "",
) -> dict[str, Any]:
    """Coordinate target prepare, source durability, and target commit."""

    controller = story_authority_controller()
    marker = controller.committed_marker()
    if marker is not None:
        marker = await _verify_controller_marker(plugin, controller, marker)
        return await _replay_committed_marker(plugin, marker)
    marker_present, marker_value = await _source_marker(plugin)
    if marker_present:
        try:
            marker = validate_story_migration_commit_marker(marker_value)
            _confirm_persisted_source_marker(plugin, marker)
            controller.recover_committed_marker(marker, source_verified=True)
        except StoryAuthorityError as exc:
            controller.block(exc.code)
            raise
        return await _replay_committed_marker(plugin, marker)
    if not str(lease_token or ""):
        _fail("story_handoff_lease_invalid")

    target = _fresh_target(plugin)
    initial_status = _target_status(target)
    if initial_status["status"] == "committed":
        _block("story_handoff_split_brain")
    snapshot = controller.export_lease(
        generation=generation,
        lease_token=lease_token,
    )
    snapshot = controller.begin_commit(
        generation=generation,
        lease_token=lease_token,
        snapshot_id=snapshot["snapshot_id"],
        snapshot_sha256=snapshot["snapshot_sha256"],
        target_plugin_id=STORY_HANDOFF_TARGET_PLUGIN_ID,
        owner_id=STORY_MIGRATION_OWNER_ID,
    )
    target_abort_snapshot: Mapping[str, Any] = snapshot
    marker_persisted = False
    try:
        if initial_status["status"] == "prepared":
            if not _matches_snapshot(initial_status, snapshot):
                target_abort_snapshot = {
                    "snapshot_id": initial_status["snapshot_id"],
                    "snapshot_sha256": initial_status["snapshot_sha256"],
                }
                raise StoryAuthorityError("story_handoff_target_conflict")
            # A crash before the source marker intentionally leaves Content's
            # prepared ledger durable. A new source generation may reuse it
            # only when the complete canonical snapshot identity is identical;
            # the older generation remains immutable provenance in the ledger.
        elif initial_status["status"] not in {"absent", "aborted"}:
            raise StoryAuthorityError("story_handoff_target_conflict")
        else:
            result, target = await _await_target_call(
                plugin,
                target,
                "prepare_story_migration",
                deepcopy(snapshot),
                source_plugin_id=STORY_SOURCE_PLUGIN_ID,
                source_instance_generation=generation,
            )
            prepared = _validate_target_status(result)
            if prepared["status"] == "committed":
                _block("story_handoff_split_brain")
            if prepared["status"] != "prepared" or not _matches_snapshot(
                prepared, snapshot
            ):
                raise StoryAuthorityError("story_handoff_target_prepare_unconfirmed")

        marker = await _persist_source_marker(
            plugin,
            generation=generation,
            lease_token=lease_token,
            snapshot=snapshot,
        )
        marker_persisted = True
    except BaseException as original:
        if (
            marker_persisted
            or controller.committed_marker() is not None
            or controller.authority_state() == "blocked"
        ):
            raise
        was_cancelled = isinstance(original, asyncio.CancelledError)
        try:
            await _cleanup_before_marker(
                plugin,
                generation=generation,
                lease_token=lease_token,
                snapshot=target_abort_snapshot,
            )
        except asyncio.CancelledError:
            was_cancelled = True
        except BaseException:
            if was_cancelled:
                raise asyncio.CancelledError
            raise
        if was_cancelled:
            raise asyncio.CancelledError
        raise

    try:
        return await _replay_committed_marker(plugin, marker)
    except asyncio.CancelledError:
        # The durable marker and controller fence stay committed. The target
        # call was harvested; a later startup/API retry replays the same marker.
        raise


async def resume_story_handoff(plugin: Any) -> dict[str, Any] | None:
    controller = story_authority_controller()
    marker = controller.committed_marker()
    if marker is None:
        return None
    marker = await _verify_controller_marker(plugin, controller, marker)
    return await _replay_committed_marker(plugin, marker)


__all__ = [
    "EnforcedStoryTarget",
    "STORY_MIGRATION_COMMIT_KEY",
    "STORY_MIGRATION_COMMIT_VERSION",
    "call_enforced_story_target",
    "commit_story_handoff",
    "preflight_story_handoff_sections",
    "resolve_enforced_story_target",
    "resume_story_handoff",
    "validate_story_migration_commit_marker",
]
