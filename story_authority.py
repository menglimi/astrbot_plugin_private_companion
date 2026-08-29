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


STORY_HANDOFF_TARGET_PLUGIN_ID = "astrbot_plugin_content_companion"
STORY_HANDOFF_LEASE_VERSION = "companion.story-handoff-lease.v1"
STORY_HANDOFF_DRAIN_TIMEOUT_SECONDS = 5.0
STORY_HANDOFF_LEASE_TTL_SECONDS = 60.0
_STORY_SNAPSHOT_ENVELOPE_KEYS = frozenset(
    {
        "version",
        "owner_id",
        "projects",
        "snapshot_id",
        "snapshot_sha256",
    }
)


class StoryAuthorityError(RuntimeError):
    """Stable, body-free error raised at the legacy Story authority boundary."""

    def __init__(self, code: str) -> None:
        self.code = str(code)
        super().__init__(self.code)


def _resolve_future(future: asyncio.Future[None]) -> None:
    if not future.done():
        future.set_result(None)


def _validate_pinned_snapshot_identity(snapshot: dict[str, Any]) -> None:
    """Bind the S1 wire identity to the exact bounded envelope being leased."""
    version = snapshot.get("version")
    owner_id = snapshot.get("owner_id")
    snapshot_id = snapshot.get("snapshot_id")
    snapshot_sha256 = snapshot.get("snapshot_sha256")
    if (
        set(snapshot) != _STORY_SNAPSHOT_ENVELOPE_KEYS
        or type(version) is not str
        or version != STORY_MIGRATION_SNAPSHOT_VERSION
        or type(owner_id) is not str
        or owner_id != STORY_MIGRATION_OWNER_ID
        or type(snapshot_id) is not str
        or type(snapshot_sha256) is not str
        or len(snapshot_sha256) != 64
        or any(
            character not in "0123456789abcdef"
            for character in snapshot_sha256
        )
        or snapshot_id != f"storysnap_{snapshot_sha256}"
    ):
        raise StoryAuthorityError("story_handoff_snapshot_identity_invalid")
    try:
        rebuilt = build_story_migration_snapshot(
            snapshot.get("projects"),
            owner_id=STORY_MIGRATION_OWNER_ID,
        )
        if rebuilt != snapshot:
            raise ValueError("snapshot is not the canonical S1 projection")
        canonical = canonical_story_snapshot_payload(snapshot)
        encoded = json.dumps(
            snapshot,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except StoryMigrationSnapshotError as exc:
        if exc.code == "story_snapshot_too_large":
            raise StoryAuthorityError("story_handoff_snapshot_too_large") from None
        raise StoryAuthorityError(
            "story_handoff_snapshot_identity_invalid"
        ) from None
    except Exception:
        raise StoryAuthorityError(
            "story_handoff_snapshot_identity_invalid"
        ) from None
    if len(canonical) > MAX_SNAPSHOT_BYTES or len(encoded) > MAX_SNAPSHOT_BYTES:
        raise StoryAuthorityError("story_handoff_snapshot_too_large")
    calculated = hashlib.sha256(canonical).hexdigest()
    if not hmac.compare_digest(calculated, snapshot_sha256):
        raise StoryAuthorityError("story_handoff_snapshot_identity_invalid")


class _StoryAuthorityController:
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
