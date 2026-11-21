"""Installation-scoped automatic snapshots using the manual backup engine."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import logging

from companion_v2.storage.backup import InstallationBackup
from companion_v2.storage.management import (auto_backup_policy, latest_installation_snapshot,
                                             retention_policy)

from .backup_api import backup_operation_lock
from .storage_api import storage_request_lock

logger = logging.getLogger(__name__)
CHECK_SECONDS = 30
FAILURE_RETRY_SECONDS = 300


def _run_due(lock, runtime, directory, at, interval, policy, cached_latest):
    # Do not queue behind a manual backup/restore. Recheck on the next poll.
    if not lock.acquire(blocking=False):
        return cached_latest, None
    try:
        backup = InstallationBackup(runtime, directory=directory, now=lambda: at, **policy)
        if backup.pending_path.exists():
            return None, None
        if cached_latest is not None:
            snapshot_id, created_at = cached_latest
            if timedelta(0) <= at - created_at < interval and backup.has_published_bundle(snapshot_id):
                return cached_latest, None
        # A deleted cache witness or a backwards clock step invalidates the
        # cached schedule. Re-scan under the same operation lock as deletion.
        latest = latest_installation_snapshot(runtime.data_root, backup_directory=backup.directory,
                                              installation_id=runtime.installation_id, now=at)
        if latest is not None and at - latest[1] < interval:
            return latest, None
        result = backup.create(note="自动滚动备份")
        return ((result.unwrap()["snapshot_id"], at) if result.is_success else None), result
    finally:
        lock.release()


class AutomaticBackup:
    def __init__(self, controller):
        self.controller = controller
        self._stop = asyncio.Event()
        self._task = None
        self._key = self._cached_latest = None
        self._retry_at = 0.0

    def start(self):
        if self._task is None and not self._stop.is_set():
            self._task = asyncio.create_task(self._run(), name="companion:auto-backup")
        return self

    async def close(self):
        self._stop.set()
        if self._task is not None:
            # Cancelling to_thread does not stop the writer. Drain it before the
            # caller closes the live runtime or retires its imports.
            await asyncio.shield(self._task)

    async def _run(self):
        while not self._stop.is_set():
            await self._tick()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=CHECK_SECONDS)
            except asyncio.TimeoutError:
                pass

    async def _tick(self):
        controller = self.controller
        storage_lock = storage_request_lock(controller)
        if self._stop.is_set() or storage_lock.locked():
            return
        # Keep the selected backup directory stable through publication and rotation.
        async with storage_lock:
            async with controller._control_lock:
                runtime = controller.runtime
                schedule = auto_backup_policy(controller.config)
                if (self._stop.is_set() or controller._terminated or runtime is None
                        or not schedule["auto_backup_enabled"]):
                    self._key = self._cached_latest = None
                    return
                directory = getattr(runtime, "backup_directory", None)
                interval = timedelta(minutes=schedule["auto_backup_interval_minutes"])
                policy = retention_policy(controller.config)
                lock = backup_operation_lock(controller)
            at = controller.now()
            loop = asyncio.get_running_loop()
            key = (runtime, directory, interval, policy)
            if key != self._key:
                self._key, self._cached_latest, self._retry_at = key, None, 0.0
            if loop.time() < self._retry_at:
                return
            try:
                self._cached_latest, result = await asyncio.to_thread(_run_due, lock, runtime, directory,
                                                                      at, interval, policy, self._cached_latest)
            except Exception as error:
                logger.warning("[CompanionV2] 自动滚动备份失败 error_type=%s", type(error).__name__)
                self._retry_at = loop.time() + FAILURE_RETRY_SECONDS
                return
            if result is None:
                return
            if not result.is_success:
                logger.warning("[CompanionV2] 自动滚动备份失败 code=%s", result.failure.code)
                self._retry_at = loop.time() + FAILURE_RETRY_SECONDS
                return
            document = result.unwrap()
            retention = document["retention"]
            logger.info("[CompanionV2] 自动滚动备份完成 snapshot_id=%s removed_count=%s",
                        document["snapshot_id"], retention["removed_count"])
            if retention.get("failed"):
                logger.warning("[CompanionV2] 自动滚动备份成功，但旧快照清理未完成 failed_count=%s",
                               retention.get("failed_count", "unknown"))
