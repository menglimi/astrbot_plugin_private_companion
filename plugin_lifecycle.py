"""Lifecycle support for the AstrBot plugin entrypoint.

This module deliberately knows only the small host protocol used by lifecycle
management.  Domain mixins remain consumers of the stable methods exposed by
``PrivateCompanionPlugin``; they do not need to participate in task ownership.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from .helpers import _single_line
from .logging_util import get_module_logger

logger = get_module_logger(__name__)


class BackgroundTaskManager:
    """Own startup and short-lived lifecycle tasks for one plugin instance."""

    def __init__(self, host: Any) -> None:
        self.host = host

    def _startup_registry(self) -> dict[str, asyncio.Task[Any]]:
        registry = getattr(self.host, "_startup_background_tasks", None)
        if not isinstance(registry, dict):
            registry = {}
            self.host._startup_background_tasks = registry
        return registry

    def _lifecycle_registry(self) -> dict[asyncio.Task[Any], str]:
        registry = getattr(self.host, "_lifecycle_background_tasks", None)
        if not isinstance(registry, dict):
            registry = {}
            self.host._lifecycle_background_tasks = registry
        return registry

    def create_startup(
        self, label: str, operation: Callable[[], Awaitable[Any]]
    ) -> asyncio.Task[Any]:
        registry = self._startup_registry()
        previous = registry.get(label)
        if isinstance(previous, asyncio.Task) and not previous.done():
            return previous
        task = asyncio.create_task(operation())
        registry[label] = task

        def discard(finished: asyncio.Task[Any]) -> None:
            if registry.get(label) is finished:
                registry.pop(label, None)
            self._observe_failure(finished, label, "startup background task failed")

        task.add_done_callback(discard)
        return task

    def create_lifecycle(
        self, operation: Awaitable[Any], *, label: str
    ) -> asyncio.Task[Any] | None:
        stop_event = getattr(self.host, "_stop_event", None)
        if isinstance(stop_event, asyncio.Event) and stop_event.is_set():
            self._close_awaitable(operation)
            logger.debug(
                "插件已进入终止流程，跳过创建后台任务: task=%s",
                _single_line(label, 100) or "background",
            )
            return None
        try:
            task = asyncio.create_task(operation)
        except RuntimeError:
            self._close_awaitable(operation)
            logger.warning(
                "后台任务无法启动：当前没有运行中的事件循环 task=%s",
                _single_line(label, 100) or "background",
            )
            return None
        registry = self._lifecycle_registry()
        task_label = _single_line(label, 100) or "background"
        registry[task] = task_label

        def discard(finished: asyncio.Task[Any]) -> None:
            effective_label = registry.pop(finished, task_label)
            self._observe_failure(finished, effective_label, "后台任务异常结束")

        task.add_done_callback(discard)
        tracker = getattr(self.host, "_track_final_response_background_task", None)
        if callable(tracker):
            tracker(task, label)
        return task

    async def cancel_lifecycle(self, timeout: float = 3.0) -> None:
        registry = self._lifecycle_registry()
        if not registry:
            return
        current = asyncio.current_task()
        pending_tasks = {
            task
            for task in tuple(registry)
            if isinstance(task, asyncio.Task) and task is not current and not task.done()
        }
        for task in pending_tasks:
            task.cancel()
        if pending_tasks:
            done, pending = await asyncio.wait(
                pending_tasks, timeout=max(0.0, float(timeout))
            )
            for task in done:
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    # Completion callbacks preserve the original diagnostics.
                    pass
            if pending:
                labels = sorted(
                    {_single_line(registry.get(task), 100) or "background" for task in pending}
                )
                logger.warning("终止后台任务超时,继续卸载: tasks=%s", "，".join(labels))
        registry.clear()

    @staticmethod
    async def cancel_task(
        task: Any, label: str, timeout: float = 3.0
    ) -> None:
        if not isinstance(task, asyncio.Task) or task.done():
            return
        task.cancel()
        done, pending = await asyncio.wait({task}, timeout=timeout)
        if pending:
            logger.warning("终止后台任务超时,继续卸载: task=%s", label)
            return
        for finished in done:
            try:
                await finished
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.debug(
                    "终止后台任务时收到异常: task=%s error=%s",
                    label,
                    _single_line(exc, 160),
                )

    @staticmethod
    def _close_awaitable(operation: Awaitable[Any]) -> None:
        closer = getattr(operation, "close", None)
        if callable(closer):
            closer()

    @staticmethod
    def _observe_failure(
        task: asyncio.Task[Any], label: str, message: str
    ) -> None:
        if task.cancelled():
            return
        try:
            error = task.exception()
        except asyncio.CancelledError:
            return
        if error is not None:
            logger.warning(
                "%s: task=%s error=%s",
                message,
                _single_line(label, 100) or "background",
                _single_line(error, 180),
                exc_info=(type(error), error, error.__traceback__),
            )


def task_manager(host: Any) -> BackgroundTaskManager:
    """Return the per-instance manager while supporting minimal test harnesses."""
    manager = getattr(host, "_background_task_manager", None)
    if not isinstance(manager, BackgroundTaskManager):
        manager = BackgroundTaskManager(host)
        host._background_task_manager = manager
    return manager


def assemble_plugin_dependencies(
    host: Any,
    *,
    observability_factory: Callable[[], Any],
) -> None:
    """Assemble lifecycle-owned collaborators after bootstrap state exists."""
    initializer = getattr(host, "_initialize_lab_fixture_adapter", None)
    if callable(initializer):
        initializer()
    host.req041_observability = observability_factory()
    host._req041_runtime_boot_ref = f"boot-{id(host)}"
    task_manager(host)


async def close_early_resources(host: Any) -> None:
    """Close publication-facing resources before background task teardown."""
    lab_fixture_adapter = getattr(host, "_lab_fixture_adapter", None)
    close_lab_fixture = getattr(lab_fixture_adapter, "close", None)
    if callable(close_lab_fixture):
        try:
            close_lab_fixture()
        except Exception as exc:
            logger.warning(
                "LAB fixture 门控清理失败，继续关闭插件: %s",
                type(exc).__name__,
            )
    host._lab_fixture_adapter = None

    close_extension = getattr(
        getattr(host, "extension_api", None), "_close_story_migration_api", None
    )
    if callable(close_extension):
        close_extension()
    host._stop_event.set()

    standalone_webui = getattr(host, "standalone_webui", None)
    if standalone_webui is not None:
        try:
            await standalone_webui.stop()
        except Exception as exc:
            logger.warning(
                "独立陪伴 WebUI 停止失败: %s", _single_line(exc, 160)
            )
    cleanup_delivery_caches = getattr(host, "_cleanup_framework_delivery_caches", None)
    if callable(cleanup_delivery_caches):
        cleanup_delivery_caches(force=True)


async def cancel_registered_host_tasks(host: Any) -> None:
    """Cancel the legacy named registries in their established shutdown order."""
    cancel_task = task_manager(host).cancel_task
    await cancel_task(getattr(host, "_task", None), "proactive_scheduler")

    for task in list(getattr(host, "_passive_input_status_tasks", {}).values()):
        if isinstance(task, asyncio.Task) and not task.done():
            task.cancel()
    getattr(host, "_passive_input_status_tasks", {}).clear()

    await cancel_task(
        getattr(host, "_startup_maintenance_task", None), "startup_maintenance"
    )
    host._req041_replay_requested = False
    await cancel_task(
        getattr(host, "_req041_replay_task", None), "req041_shadow_replay"
    )
    host._req041_scoped_sync_requested = False
    await cancel_task(
        getattr(host, "_req041_scoped_sync_task", None),
        "req041_scoped_projection_sync",
    )

    startup_tasks = getattr(host, "_startup_background_tasks", {})
    for label, task in list(startup_tasks.items()):
        await cancel_task(task, f"startup_{label}")
    startup_tasks.clear()

    group_image_tasks = getattr(host, "_group_image_understanding_tasks", {})
    for task_key, entry in list(group_image_tasks.items()):
        task = entry.get("task") if isinstance(entry, dict) else None
        await cancel_task(task, f"group_image_{_single_line(task_key, 80)}")
    group_image_tasks.clear()

    wakeup_tasks = getattr(host, "_troubleshooting_proactive_wakeup_tasks", {})
    for user_id, task in list(wakeup_tasks.items()):
        await cancel_task(
            task, f"troubleshooting_proactive_{_single_line(user_id, 40)}"
        )
    host._troubleshooting_proactive_wakeup_tasks = {}
