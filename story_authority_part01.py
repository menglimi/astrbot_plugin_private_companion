# -*- coding: utf-8 -*-
"""_StoryAuthorityControllerPart01Mixin。

由 tools/split_mixin_domain.py 从 story_authority.py 机械抽取（23 个方法 + 0 个模块级名字 + 0 个类级赋值 / 447 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 _StoryAuthorityController）。
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import secrets
import threading
import time
from .story_authority_shared import (
    STORY_HANDOFF_LEASE_VERSION,
    STORY_HANDOFF_TARGET_PLUGIN_ID,
    StoryAuthorityError,
    _resolve_future,
    _validate_pinned_snapshot_identity,
)
from .story_migration_contract import STORY_MIGRATION_OWNER_ID
from contextlib import contextmanager
from copy import deepcopy
from typing import Any, Awaitable, Callable, Iterator



class _StoryAuthorityControllerPart01Mixin:
    """_StoryAuthorityControllerPart01Mixin（从 _StoryAuthorityController 拆出）。"""


    @staticmethod
    def _operation_identity() -> Any:
        try:
            task = asyncio.current_task()
        except RuntimeError:
            task = None
        if task is not None:
            return task
        return ("thread", threading.get_ident())

    @staticmethod
    def _token_digest(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8", errors="strict")).hexdigest()

    @staticmethod
    def _same_digest(left: str, right: str) -> bool:
        return bool(left and right and hmac.compare_digest(left, right))

    def _remember_lease_locked(self, reason: str) -> None:
        lease = self._lease
        if lease is None:
            return
        self._last_token_digest = self._token_digest(str(lease["token"]))
        self._last_token_generation = str(lease["generation"])
        self._last_token_reason = str(reason)

    def _clear_lease_locked(self, *, reason: str = "") -> None:
        if reason:
            self._remember_lease_locked(reason)
        self._lease = None

    def _expire_locked(self) -> bool:
        lease = self._lease
        if lease is None or self._state != "leased":
            return False
        if time.monotonic() < float(lease["expires_monotonic"]):
            return False
        self._clear_lease_locked(reason="expired")
        self._state = "open" if self._active_generation else "created"
        return True

    def _wake_waiters_locked(self) -> None:
        waiters = list(self._waiters.values())
        self._waiters.clear()
        for loop, future in waiters:
            try:
                loop.call_soon_threadsafe(_resolve_future, future)
            except RuntimeError:
                pass

    def stage_generation(self, generation: str) -> None:
        generation = str(generation or "")
        if not generation:
            raise StoryAuthorityError("story_handoff_generation_invalid")
        with self._lock:
            self._expire_locked()
            # A staged hot reload never inherits or revokes the live generation's
            # authority.  In particular, an old lease keeps bootstrap writers
            # blocked until its exact abort or TTL expiry.
            if not self._active_generation:
                self._active_generation = generation
                if self._state not in {"committing", "committed", "blocked"}:
                    self._state = "created"
                self._drain_id = ""
                self._prepare_bindings.clear()
                self._inspection_bindings.clear()
                if self._state == "created":
                    self._clear_lease_locked()
                    self._last_token_digest = ""
                    self._last_token_generation = ""
                    self._last_token_reason = ""

    def activate_generation(self, generation: str) -> None:
        generation = str(generation or "")
        if not generation:
            raise StoryAuthorityError("story_handoff_generation_invalid")
        with self._lock:
            self._expire_locked()
            if self._state in {"committed", "blocked"}:
                self._active_generation = generation
                self._wake_waiters_locked()
                return
            if self._state == "committing":
                if self._active_generation != generation:
                    raise StoryAuthorityError("story_handoff_commit_in_progress")
                return
            self._clear_lease_locked(reason="stale")
            self._active_generation = generation
            self._last_token_digest = ""
            self._last_token_generation = ""
            self._last_token_reason = ""
            self._state = "open"
            self._drain_id = ""
            self._prepare_bindings.clear()
            self._inspection_bindings.clear()
            self._wake_waiters_locked()

    def supersede_generation(self, generation: str) -> None:
        self._close_matching_generation(generation)

    def close_generation(self, generation: str) -> None:
        self._close_matching_generation(generation)

    def _close_matching_generation(self, generation: str) -> None:
        with self._lock:
            self._expire_locked()
            if self._active_generation != str(generation or ""):
                return
            if self._state in {"committing", "committed", "blocked"}:
                # A lifecycle close cannot undo a committing or durable fence.
                # A later generation is allowed to stage on the same object.
                if self._state != "committing":
                    self._active_generation = ""
                self._wake_waiters_locked()
                return
            self._clear_lease_locked(reason="stale")
            self._state = "created"
            self._active_generation = ""
            self._drain_id = ""
            self._prepare_bindings.clear()
            self._inspection_bindings.clear()
            self._wake_waiters_locked()

    def enter_legacy_operation(self, operation: str) -> Any:
        del operation
        identity = self._operation_identity()
        with self._lock:
            self._expire_locked()
            inspection = self._inspection_bindings.get(identity)
            if (
                inspection is not None
                and inspection[:2]
                == (self._active_generation, self._drain_id)
                and self._state == "draining"
            ):
                return ("authority-inspection", identity)
            depth = self._depths.get(identity, 0)
            if depth:
                self._depths[identity] = depth + 1
                return identity
            if self._state in {"created", "open"}:
                self._state = "open"
                self._depths[identity] = 1
                return identity
            if self._state == "draining":
                raise StoryAuthorityError("story_legacy_write_draining")
            if self._state == "leased":
                raise StoryAuthorityError("story_legacy_write_leased")
            if self._state == "committing":
                raise StoryAuthorityError("story_legacy_write_committing")
            if self._state == "committed":
                raise StoryAuthorityError("story_legacy_write_committed")
            raise StoryAuthorityError("story_legacy_write_blocked")

    def enter_startup_operation(self, operation: str) -> Any:
        """Permit read/bootstrap work after a valid durable marker is fenced.

        The returned identity is deliberately not re-entrant legacy authority:
        nested Story writer decorators still see ``committed`` and reject.
        """

        del operation
        identity = self._operation_identity()
        with self._lock:
            self._expire_locked()
            if self._state == "committing":
                raise StoryAuthorityError("story_handoff_commit_in_progress")
            if self._state == "draining":
                raise StoryAuthorityError("story_legacy_write_draining")
            if self._state == "leased":
                raise StoryAuthorityError("story_legacy_write_leased")
            if self._state == "blocked" and self._commit_marker is None:
                raise StoryAuthorityError("story_handoff_blocked")
            if self._state in {"created", "open"}:
                self._state = "open"
            depth = self._startup_depths.get(identity, 0)
            self._startup_depths[identity] = depth + 1
            return ("authority-startup", identity)

    def exit_startup_operation(self, identity: Any) -> None:
        if (
            isinstance(identity, tuple)
            and len(identity) == 2
            and identity[0] == "authority-startup"
        ):
            root_identity = identity[1]
            with self._lock:
                depth = self._startup_depths.get(root_identity, 0)
                if depth <= 1:
                    self._startup_depths.pop(root_identity, None)
                else:
                    self._startup_depths[root_identity] = depth - 1
                if not self._depths and not self._startup_depths:
                    self._wake_waiters_locked()
            return
        self.exit_legacy_operation(identity)

    def exit_legacy_operation(self, identity: Any) -> None:
        if (
            isinstance(identity, tuple)
            and len(identity) == 2
            and identity[0] == "authority-inspection"
        ):
            return
        with self._lock:
            depth = self._depths.get(identity, 0)
            if depth <= 1:
                self._depths.pop(identity, None)
            else:
                self._depths[identity] = depth - 1
            if not self._depths and not self._startup_depths:
                self._wake_waiters_locked()

    @contextmanager
    def strict_profile_inspection(self) -> Iterator[None]:
        """Permit only the prepare Task's strict persisted-profile read path."""
        identity = self._operation_identity()
        with self._lock:
            binding = self._prepare_bindings.get(identity)
            if (
                binding is None
                or binding != (self._active_generation, self._drain_id)
                or self._state != "draining"
            ):
                raise StoryAuthorityError(
                    "story_handoff_profile_inspection_unavailable"
                )
            existing = self._inspection_bindings.get(identity)
            depth = existing[2] + 1 if existing is not None else 1
            self._inspection_bindings[identity] = (*binding, depth)
        try:
            yield
        finally:
            with self._lock:
                existing = self._inspection_bindings.get(identity)
                if existing is not None and existing[:2] == binding:
                    if existing[2] <= 1:
                        self._inspection_bindings.pop(identity, None)
                    else:
                        self._inspection_bindings[identity] = (
                            existing[0],
                            existing[1],
                            existing[2] - 1,
                        )

    def _abort_drain(self, generation: str, drain_id: str) -> None:
        with self._lock:
            self._waiters.pop(drain_id, None)
            if (
                self._active_generation == generation
                and self._state == "draining"
                and self._drain_id == drain_id
            ):
                self._state = "open"
                self._drain_id = ""

    async def prepare(
        self,
        *,
        generation: str,
        target_plugin_id: str,
        owner_id: str,
        snapshot_factory: Callable[[], Awaitable[dict[str, Any]]],
    ) -> dict[str, Any]:
        if target_plugin_id != STORY_HANDOFF_TARGET_PLUGIN_ID:
            raise StoryAuthorityError("story_handoff_target_unsupported")
        if owner_id != STORY_MIGRATION_OWNER_ID:
            raise StoryAuthorityError("story_handoff_owner_mismatch")
        generation = str(generation or "")
        loop = asyncio.get_running_loop()
        drain_id = secrets.token_hex(16)
        waiter: asyncio.Future[None] | None = None
        with self._lock:
            self._expire_locked()
            if self._active_generation != generation:
                raise StoryAuthorityError("story_handoff_generation_stale")
            if self._state == "draining":
                raise StoryAuthorityError("story_handoff_prepare_busy")
            if self._state == "leased":
                raise StoryAuthorityError("story_handoff_already_leased")
            if self._state == "committing":
                raise StoryAuthorityError("story_handoff_commit_in_progress")
            if self._state == "committed":
                raise StoryAuthorityError("story_handoff_already_committed")
            if self._state == "blocked":
                raise StoryAuthorityError("story_handoff_blocked")
            self._state = "draining"
            self._drain_id = drain_id
            if self._depths or self._startup_depths:
                waiter = loop.create_future()
                self._waiters[drain_id] = (loop, waiter)
        if waiter is not None:
            try:
                await asyncio.wait_for(
                    asyncio.shield(waiter),
                    timeout=self._drain_timeout_seconds,
                )
            except asyncio.TimeoutError:
                self._abort_drain(generation, drain_id)
                raise StoryAuthorityError("story_handoff_drain_timeout") from None
            except asyncio.CancelledError:
                self._abort_drain(generation, drain_id)
                raise
            finally:
                with self._lock:
                    self._waiters.pop(drain_id, None)
        with self._lock:
            if self._active_generation != generation:
                self._abort_drain(generation, drain_id)
                raise StoryAuthorityError("story_handoff_generation_stale")
            if self._state != "draining" or self._drain_id != drain_id:
                raise StoryAuthorityError("story_handoff_prepare_interrupted")
            if self._depths or self._startup_depths:
                self._abort_drain(generation, drain_id)
                raise StoryAuthorityError("story_handoff_drain_timeout")
        prepare_identity = self._operation_identity()
        prepare_binding = (generation, drain_id)
        with self._lock:
            self._prepare_bindings[prepare_identity] = prepare_binding
        try:
            try:
                snapshot = await snapshot_factory()
                if type(snapshot) is not dict:
                    raise StoryAuthorityError("story_handoff_snapshot_invalid")
                pinned_snapshot = deepcopy(snapshot)
                _validate_pinned_snapshot_identity(pinned_snapshot)
            finally:
                with self._lock:
                    if (
                        self._prepare_bindings.get(prepare_identity)
                        == prepare_binding
                    ):
                        self._prepare_bindings.pop(prepare_identity, None)
                    inspection = self._inspection_bindings.get(prepare_identity)
                    if (
                        inspection is not None
                        and inspection[:2] == prepare_binding
                    ):
                        self._inspection_bindings.pop(prepare_identity, None)
        except asyncio.CancelledError:
            self._abort_drain(generation, drain_id)
            raise
        except BaseException:
            self._abort_drain(generation, drain_id)
            raise
        token = secrets.token_urlsafe(32)
        expires_monotonic = time.monotonic() + self._lease_ttl_seconds
        expires_at = time.time() + self._lease_ttl_seconds
        with self._lock:
            if self._active_generation != generation:
                self._abort_drain(generation, drain_id)
                raise StoryAuthorityError("story_handoff_generation_stale")
            if self._state != "draining" or self._drain_id != drain_id:
                raise StoryAuthorityError("story_handoff_prepare_interrupted")
            self._lease = {
                "generation": generation,
                "token": token,
                "snapshot": pinned_snapshot,
                "expires_monotonic": expires_monotonic,
            }
            self._last_token_digest = ""
            self._last_token_generation = ""
            self._last_token_reason = ""
            self._state = "leased"
            self._drain_id = ""
        return {
            "version": STORY_HANDOFF_LEASE_VERSION,
            "instance_generation": generation,
            "target_plugin_id": STORY_HANDOFF_TARGET_PLUGIN_ID,
            "owner_id": STORY_MIGRATION_OWNER_ID,
            "lease_token": token,
            "snapshot_id": str(pinned_snapshot.get("snapshot_id") or ""),
            "snapshot_sha256": str(pinned_snapshot.get("snapshot_sha256") or ""),
            "ttl_seconds": self._lease_ttl_seconds,
            "expires_at": expires_at,
        }

    def _receipt_error_locked(self, generation: str, token: str) -> None:
        digest = self._token_digest(token)
        if not self._same_digest(digest, self._last_token_digest):
            raise StoryAuthorityError("story_handoff_lease_invalid")
        if generation != self._last_token_generation:
            raise StoryAuthorityError("story_handoff_generation_stale")
        if self._last_token_reason == "expired":
            raise StoryAuthorityError("story_handoff_lease_expired")
        raise StoryAuthorityError("story_handoff_lease_invalid")

    def export_lease(self, *, generation: str, lease_token: str) -> dict[str, Any]:
        token = str(lease_token or "")
        if not token:
            raise StoryAuthorityError("story_handoff_lease_invalid")
        with self._lock:
            self._expire_locked()
            if self._active_generation != generation:
                raise StoryAuthorityError("story_handoff_generation_stale")
            lease = self._lease
            if self._state != "leased" or lease is None:
                self._receipt_error_locked(generation, token)
            if lease["generation"] != generation:
                raise StoryAuthorityError("story_handoff_generation_stale")
            if not hmac.compare_digest(token, str(lease["token"])):
                raise StoryAuthorityError("story_handoff_lease_invalid")
            return deepcopy(lease["snapshot"])

    def abort(self, *, generation: str, lease_token: str) -> dict[str, Any]:
        token = str(lease_token or "")
        if not token:
            raise StoryAuthorityError("story_handoff_lease_invalid")
        with self._lock:
            self._expire_locked()
            if self._active_generation != generation:
                raise StoryAuthorityError("story_handoff_generation_stale")
            lease = self._lease
            if self._state == "leased" and lease is not None:
                if lease["generation"] != generation:
                    raise StoryAuthorityError("story_handoff_generation_stale")
                if not hmac.compare_digest(token, str(lease["token"])):
                    raise StoryAuthorityError("story_handoff_lease_invalid")
                self._clear_lease_locked(reason="aborted")
                self._state = "open"
                return {"aborted": True, "already_released": False}
            digest = self._token_digest(token)
            if (
                self._last_token_reason == "aborted"
                and self._last_token_generation == generation
                and self._same_digest(digest, self._last_token_digest)
            ):
                return {"aborted": False, "already_released": True}
            self._receipt_error_locked(generation, token)
            raise AssertionError("unreachable")

    def begin_commit(
        self,
        *,
        generation: str,
        lease_token: str,
        snapshot_id: str,
        snapshot_sha256: str,
        target_plugin_id: str,
        owner_id: str,
    ) -> dict[str, Any]:
        """Pin an unexpiring commit window for the exact live lease."""

        if target_plugin_id != STORY_HANDOFF_TARGET_PLUGIN_ID:
            raise StoryAuthorityError("story_handoff_target_unsupported")
        if owner_id != STORY_MIGRATION_OWNER_ID:
            raise StoryAuthorityError("story_handoff_owner_mismatch")
        token = str(lease_token or "")
        if not token:
            raise StoryAuthorityError("story_handoff_lease_invalid")
        with self._lock:
            self._expire_locked()
            if self._active_generation != generation:
                raise StoryAuthorityError("story_handoff_generation_stale")
            lease = self._lease
            if self._state != "leased" or lease is None:
                self._receipt_error_locked(generation, token)
            if lease["generation"] != generation:
                raise StoryAuthorityError("story_handoff_generation_stale")
            if not hmac.compare_digest(token, str(lease["token"])):
                raise StoryAuthorityError("story_handoff_lease_invalid")
            snapshot = lease["snapshot"]
            if (
                snapshot.get("snapshot_id") != snapshot_id
                or snapshot.get("snapshot_sha256") != snapshot_sha256
            ):
                raise StoryAuthorityError("story_handoff_snapshot_identity_invalid")
            self._state = "committing"
            return deepcopy(snapshot)
