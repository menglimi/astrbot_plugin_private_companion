# -*- coding: utf-8 -*-
"""_StoryAuthorityControllerPart02Mixin。

由 tools/split_mixin_domain.py 从 story_authority.py 机械抽取（9 个方法 + 0 个模块级名字 + 0 个类级赋值 / 172 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 _StoryAuthorityController）。
"""
from __future__ import annotations

import hmac
from .story_authority_shared import STORY_HANDOFF_TARGET_PLUGIN_ID, StoryAuthorityError
from .story_migration_contract import STORY_MIGRATION_OWNER_ID
from copy import deepcopy
from typing import Any, Mapping



class _StoryAuthorityControllerPart02Mixin:
    """_StoryAuthorityControllerPart02Mixin（从 _StoryAuthorityController 拆出）。"""


    def abort_before_marker(
        self,
        *,
        generation: str,
        lease_token: str,
    ) -> dict[str, Any]:
        """Release an exact leased/committing transaction before durability."""

        token = str(lease_token or "")
        if not token:
            raise StoryAuthorityError("story_handoff_lease_invalid")
        with self._lock:
            if self._active_generation != generation:
                raise StoryAuthorityError("story_handoff_generation_stale")
            lease = self._lease
            if self._state not in {"leased", "committing"} or lease is None:
                digest = self._token_digest(token)
                if (
                    self._last_token_reason == "aborted"
                    and self._last_token_generation == generation
                    and self._same_digest(digest, self._last_token_digest)
                ):
                    return {"aborted": False, "already_released": True}
                self._receipt_error_locked(generation, token)
            if lease["generation"] != generation:
                raise StoryAuthorityError("story_handoff_generation_stale")
            if not hmac.compare_digest(token, str(lease["token"])):
                raise StoryAuthorityError("story_handoff_lease_invalid")
            self._clear_lease_locked(reason="aborted")
            self._state = "open"
            return {"aborted": True, "already_released": False}

    def finish_commit(
        self,
        *,
        generation: str,
        lease_token: str,
        marker: dict[str, Any],
    ) -> None:
        """Make the in-process fence irreversible after the marker is durable."""

        token = str(lease_token or "")
        with self._lock:
            lease = self._lease
            if (
                self._active_generation != generation
                or self._state != "committing"
                or lease is None
                or lease.get("generation") != generation
            ):
                raise StoryAuthorityError("story_handoff_commit_interrupted")
            if not token or not hmac.compare_digest(token, str(lease["token"])):
                raise StoryAuthorityError("story_handoff_lease_invalid")
            snapshot = lease["snapshot"]
            if (
                marker.get("snapshot_id") != snapshot.get("snapshot_id")
                or marker.get("snapshot_sha256") != snapshot.get("snapshot_sha256")
                or marker.get("target_plugin_id") != STORY_HANDOFF_TARGET_PLUGIN_ID
                or marker.get("owner_id") != STORY_MIGRATION_OWNER_ID
            ):
                raise StoryAuthorityError("story_handoff_marker_conflict")
            self._commit_marker = deepcopy(marker)
            self._commit_marker_source_verified = True
            self._blocked_reason = ""
            self._clear_lease_locked(reason="committed")
            self._state = "committed"
            self._drain_id = ""
            self._prepare_bindings.clear()
            self._inspection_bindings.clear()
            self._wake_waiters_locked()

    def recover_committed_marker(
        self,
        marker: dict[str, Any],
        *,
        source_verified: bool = False,
    ) -> None:
        """Recover a previously validated marker without replacing this object."""

        pinned = deepcopy(marker)
        with self._lock:
            if self._state == "blocked" and self._commit_marker != pinned:
                raise StoryAuthorityError("story_handoff_blocked")
            if (
                self._state == "blocked"
                and not source_verified
                and self._blocked_reason not in self._replay_recoverable_blocks
            ):
                raise StoryAuthorityError("story_handoff_blocked")
            if self._commit_marker is not None and self._commit_marker != pinned:
                self._state = "blocked"
                self._blocked_reason = "story_handoff_marker_conflict"
                self._clear_lease_locked(reason="blocked")
                self._wake_waiters_locked()
                raise StoryAuthorityError("story_handoff_marker_conflict")
            lease = self._lease
            if lease is not None and (
                lease.get("snapshot", {}).get("snapshot_id")
                != pinned.get("snapshot_id")
                or lease.get("snapshot", {}).get("snapshot_sha256")
                != pinned.get("snapshot_sha256")
            ):
                self._state = "blocked"
                self._blocked_reason = "story_handoff_marker_conflict"
                self._clear_lease_locked(reason="blocked")
                self._wake_waiters_locked()
                raise StoryAuthorityError("story_handoff_marker_conflict")
            already_verified = bool(
                self._commit_marker == pinned
                and self._commit_marker_source_verified
            )
            self._commit_marker = pinned
            self._commit_marker_source_verified = bool(
                source_verified or already_verified
            )
            self._blocked_reason = ""
            self._clear_lease_locked(reason="committed")
            self._state = "committed"
            self._drain_id = ""
            self._prepare_bindings.clear()
            self._inspection_bindings.clear()
            self._wake_waiters_locked()

    def assert_marker_absent(self) -> None:
        """Reject disappearance of a marker after this process observed commit."""

        with self._lock:
            if self._state == "committed" or self._commit_marker is not None:
                self._state = "blocked"
                self._blocked_reason = "story_handoff_marker_missing"
                self._clear_lease_locked(reason="blocked")
                self._wake_waiters_locked()
                raise StoryAuthorityError("story_handoff_marker_missing")
            if self._state == "blocked":
                raise StoryAuthorityError("story_handoff_blocked")

    def block(self, reason: str) -> None:
        with self._lock:
            self._blocked_reason = str(reason or "story_handoff_blocked")
            self._clear_lease_locked(reason="blocked")
            self._state = "blocked"
            self._drain_id = ""
            self._prepare_bindings.clear()
            self._inspection_bindings.clear()
            self._wake_waiters_locked()

    def committed_marker(self) -> dict[str, Any] | None:
        with self._lock:
            return deepcopy(self._commit_marker)

    def committed_marker_source_verified(
        self,
        marker: Mapping[str, Any],
    ) -> bool:
        """Return whether this exact marker crossed a confirmed durable read."""

        with self._lock:
            return bool(
                self._commit_marker_source_verified
                and self._commit_marker == marker
            )

    def authority_state(self) -> str:
        with self._lock:
            self._expire_locked()
            return self._state

    def debug_state(self) -> dict[str, Any]:
        """Return token-free state for diagnostics and focused tests."""
        with self._lock:
            self._expire_locked()
            return {
                "state": self._state,
                "active_generation": self._active_generation,
                "active_roots": len(self._depths) + len(self._startup_depths),
                "waiters": len(self._waiters),
                "preparers": len(self._prepare_bindings),
                "inspectors": len(self._inspection_bindings),
                "has_lease": self._lease is not None,
            }
