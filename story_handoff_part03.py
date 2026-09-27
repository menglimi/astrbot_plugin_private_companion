# -*- coding: utf-8 -*-
"""story_handoff 拆分件 part03（机械搬移，行为不变）。

函数 / 常量 / 类逐字搬自 story_handoff.py，仅调整模块级依赖的导入来源。
"""
import asyncio
from copy import deepcopy
from typing import Any, Mapping
from .story_authority import (
    STORY_HANDOFF_TARGET_PLUGIN_ID,
    StoryAuthorityError,
)
from .story_handoff_shared import _story_handoff_host
from .story_migration_contract import STORY_MIGRATION_OWNER_ID

from .story_handoff_part01 import (
    STORY_SOURCE_PLUGIN_ID,
    _confirm_persisted_source_marker,
    _fail,
    _fresh_target,
    _matches_snapshot,
    _source_marker,
    _target_status,
    _validate_target_status,
    validate_story_migration_commit_marker,
)
from .story_handoff_part02 import (
    _await_target_call,
    _block,
    _cleanup_before_marker,
    _persist_source_marker,
    _replay_committed_marker,
    _verify_controller_marker,
)


async def commit_story_handoff(
    plugin: Any,
    *,
    generation: str,
    lease_token: str = "",
) -> dict[str, Any]:
    """Coordinate target prepare, source durability, and target commit."""

    controller = _story_handoff_host.story_authority_controller()
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
    controller = _story_handoff_host.story_authority_controller()
    marker = controller.committed_marker()
    if marker is None:
        return None
    marker = await _verify_controller_marker(plugin, controller, marker)
    return await _replay_committed_marker(plugin, marker)
