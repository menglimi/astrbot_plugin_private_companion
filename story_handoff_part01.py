# -*- coding: utf-8 -*-
"""story_handoff 拆分件 part01（机械搬移，行为不变）。

函数 / 常量 / 类逐字搬自 story_handoff.py，仅调整模块级依赖的导入来源。
"""
from copy import deepcopy
import math
from dataclasses import dataclass
from typing import Any, Mapping
from .story_authority import (
    STORY_HANDOFF_TARGET_PLUGIN_ID,
    StoryAuthorityError,
)
from .story_handoff_shared import _story_handoff_host
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

    controller = _story_handoff_host.story_authority_controller()
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
