from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import sys
import threading
from types import ModuleType
from typing import Any

from astrbot.api.message_components import Plain

from .helpers import _single_line
from .story_migration_contract import (
    STORY_MIGRATION_API_FAMILY,
    STORY_MIGRATION_API_VERSION,
    STORY_MIGRATION_OWNER_ID,
    STORY_MIGRATION_SNAPSHOT_VERSION,
    StoryMigrationSnapshotError,
    build_story_migration_snapshot,
)
from .story_authority import (
    StoryAuthorityError,
    assert_single_persona_story_shelf,
    story_authority_controller,
    story_profile_inspection_context,
)
from .story_handoff import commit_story_handoff


def _new_story_snapshot_runtime() -> ModuleType:
    """Create process-bounded state that survives plugin module aliases/reloads."""

    runtime = ModuleType("_astrbot_private_companion_story_snapshot_runtime_v1")
    runtime.admission = threading.BoundedSemaphore(1)
    runtime.executor = ThreadPoolExecutor(
        max_workers=1,
        thread_name_prefix="companion-story-snapshot",
    )
    return runtime


_STORY_SNAPSHOT_RUNTIME_KEY = (
    "_astrbot_private_companion_story_snapshot_runtime_v1"
)


def _install_story_snapshot_runtime() -> ModuleType:
    """Atomically share one runtime and retire any losing race candidate."""

    candidate = _new_story_snapshot_runtime()
    runtime = sys.modules.setdefault(_STORY_SNAPSHOT_RUNTIME_KEY, candidate)
    if runtime is not candidate:
        candidate.executor.shutdown(wait=False, cancel_futures=True)
    return runtime


_STORY_SNAPSHOT_RUNTIME = _install_story_snapshot_runtime()
_STORY_SNAPSHOT_ADMISSION = _STORY_SNAPSHOT_RUNTIME.admission
_STORY_SNAPSHOT_EXECUTOR = _STORY_SNAPSHOT_RUNTIME.executor


class _ContentCapabilityFamily:
    """Private capability family backed only by its owning façade."""

    __slots__ = ("_owner",)

    def __init__(self, owner: Any) -> None:
        self._owner = owner

    def get_realtime_voice_config(self) -> dict[str, Any]:
        """Expose the active companion voice language to realtime plugins."""
        return self._owner._plugin._realtime_voice_config()

    def story_migration_capabilities(self) -> dict[str, Any]:
        """Describe the read-only Story snapshot surface for exact negotiation."""
        state = self._owner._story_migration_lifecycle_state()
        degraded = [] if state == "ready" else ["story_snapshot_service_not_ready"]
        if story_authority_controller().authority_state() == "blocked":
            degraded.append("story_handoff_blocked")
        return {
            "plugin_id": STORY_MIGRATION_OWNER_ID,
            "instance_generation": self._owner._story_migration_instance_generation(),
            "api_family": STORY_MIGRATION_API_FAMILY,
            "api_version": STORY_MIGRATION_API_VERSION,
            "supported_task_versions": [STORY_MIGRATION_SNAPSHOT_VERSION],
            "capabilities": [
                "story.snapshot.export",
                "story.snapshot.path-free",
                "story.snapshot.read-only",
                "story.handoff.prepare",
                "story.handoff.export-lease",
                "story.handoff.abort",
                "story.handoff.commit",
            ],
            "lifecycle_state": state,
            "degraded_reasons": degraded,
        }

    @staticmethod
    async def _finish_snapshot_build(
        worker: asyncio.Future[dict[str, Any]],
    ) -> dict[str, Any]:
        """Harvest a worker before its protected source lock can be released."""
        cancelled = False
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                cancelled = True
        try:
            result = worker.result()
        except BaseException:
            if cancelled:
                raise asyncio.CancelledError
            raise
        if cancelled:
            raise asyncio.CancelledError
        return result

    async def _build_story_migration_snapshot(
        self,
        *,
        require_single_persona: bool = False,
    ) -> dict[str, Any]:
        """Build one bounded snapshot while its source is protected by the data lock."""
        owner = self._owner
        generation = owner._story_migration_instance_generation()
        if owner._story_migration_lifecycle_state() != "ready":
            raise StoryMigrationSnapshotError("story_snapshot_service_closed")
        if not _STORY_SNAPSHOT_ADMISSION.acquire(blocking=False):
            raise StoryMigrationSnapshotError("story_snapshot_busy")
        try:
            plugin = owner._plugin
            lock = getattr(plugin, "_data_lock", None)
            if (
                lock is None
                or not hasattr(lock, "__aenter__")
                or not hasattr(lock, "__aexit__")
            ):
                raise StoryMigrationSnapshotError("story_snapshot_state_unavailable")
            async with lock:
                if (
                    owner._story_migration_lifecycle_state() != "ready"
                    or owner._story_migration_instance_generation() != generation
                ):
                    raise StoryMigrationSnapshotError("story_snapshot_service_closed")
                if require_single_persona:
                    with story_profile_inspection_context():
                        assert_single_persona_story_shelf(plugin)
                        data = getattr(plugin, "_data_default", None)
                else:
                    data = getattr(plugin, "data", None)
                if type(data) is not dict:
                    raise StoryMigrationSnapshotError("story_snapshot_state_unavailable")
                projects = data.get("creative_projects", [])
                try:
                    worker = asyncio.ensure_future(
                        asyncio.get_running_loop().run_in_executor(
                            _STORY_SNAPSHOT_EXECUTOR,
                            build_story_migration_snapshot,
                            projects,
                        )
                    )
                    snapshot = await self._finish_snapshot_build(worker)
                except asyncio.CancelledError:
                    raise
                except StoryAuthorityError:
                    raise
                except StoryMigrationSnapshotError:
                    raise
                except Exception:
                    raise StoryMigrationSnapshotError(
                        "story_snapshot_build_failed"
                    ) from None
                if (
                    owner._story_migration_lifecycle_state() != "ready"
                    or owner._story_migration_instance_generation() != generation
                ):
                    raise StoryMigrationSnapshotError("story_snapshot_service_closed")
                return snapshot
        except asyncio.CancelledError:
            raise
        except StoryAuthorityError:
            raise
        except StoryMigrationSnapshotError:
            raise
        except Exception:
            raise StoryMigrationSnapshotError(
                "story_snapshot_state_unavailable"
            ) from None
        finally:
            _STORY_SNAPSHOT_ADMISSION.release()

    async def export_story_migration_snapshot(
        self,
        *,
        lease_token: str = "",
    ) -> dict[str, Any]:
        """Export the S1 live view or the immutable snapshot pinned by a lease."""
        owner = self._owner
        if lease_token:
            return story_authority_controller().export_lease(
                generation=owner._story_migration_instance_generation(),
                lease_token=lease_token,
            )
        return await self._build_story_migration_snapshot()

    async def prepare_story_handoff(
        self,
        *,
        target_plugin_id: str,
        owner_id: str,
    ) -> dict[str, Any]:
        """Drain legacy writers and mint one process-wide, memory-only lease."""
        owner = self._owner
        if owner._story_migration_lifecycle_state() != "ready":
            raise StoryAuthorityError("story_handoff_service_closed")
        generation = owner._story_migration_instance_generation()
        return await story_authority_controller().prepare(
            generation=generation,
            target_plugin_id=target_plugin_id,
            owner_id=owner_id,
            snapshot_factory=lambda: self._build_story_migration_snapshot(
                require_single_persona=True,
            ),
        )

    async def abort_story_handoff(
        self,
        *,
        lease_token: str,
    ) -> dict[str, Any]:
        """Release the exact live handoff lease without persisting its token."""
        owner = self._owner
        return story_authority_controller().abort(
            generation=owner._story_migration_instance_generation(),
            lease_token=lease_token,
        )

    async def commit_story_handoff(
        self,
        *,
        lease_token: str = "",
    ) -> dict[str, Any]:
        """Commit or replay the exact source-owned Story handoff."""

        owner = self._owner
        if owner._story_migration_lifecycle_state() != "ready":
            raise StoryAuthorityError("story_handoff_service_closed")
        return await commit_story_handoff(
            owner._plugin,
            generation=owner._story_migration_instance_generation(),
            lease_token=lease_token,
        )

    async def send_reality_touch_chat(self, umo: str, text: str) -> bool:
        sender = getattr(self._owner._plugin, "_send_chain_components", None)
        if not callable(sender) or not _single_line(umo, 180) or not _single_line(text, 1000):
            return False
        return bool(await sender(umo, [Plain(str(text))]))

    async def record_reality_touch_output(
        self,
        user_id: str,
        text: str,
        *,
        source: str = "reality_touch_audio",
        delivered_at: float | None = None,
    ) -> dict[str, Any]:
        """Record speech delivered outside chat so the next reply can continue it."""
        recorder = getattr(self._owner._plugin, "_record_reality_touch_output", None)
        if not callable(recorder):
            return {"recorded": False, "reason": "recorder_unavailable"}
        return await recorder(
            user_id,
            text,
            source=source,
            delivered_at=delivered_at,
        )
