from __future__ import annotations

import asyncio
import unittest
from typing import Any

from astrbot_plugin_private_companion.main import PrivateCompanionPlugin


class _TaskHarness:
    _create_startup_background_task = PrivateCompanionPlugin._create_startup_background_task
    _create_lifecycle_background_task = PrivateCompanionPlugin._create_lifecycle_background_task
    _cancel_lifecycle_background_tasks = PrivateCompanionPlugin._cancel_lifecycle_background_tasks
    _extension_task_count = PrivateCompanionPlugin._extension_task_count

    def __init__(self) -> None:
        self._stop_event = asyncio.Event()
        self._startup_background_tasks: dict[str, asyncio.Task[Any]] = {}
        self._lifecycle_background_tasks: dict[asyncio.Task[Any], str] = {}
        self.tracked: list[tuple[asyncio.Task[Any], str]] = []

    def _track_final_response_background_task(
        self, task: asyncio.Task[Any], label: str
    ) -> None:
        self.tracked.append((task, label))


class PluginLifecycleCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_startup_registry_deduplicates_live_label_and_replaces_finished_task(
        self,
    ) -> None:
        harness = _TaskHarness()
        release = asyncio.Event()
        starts = 0

        async def operation() -> None:
            nonlocal starts
            starts += 1
            await release.wait()

        first = harness._create_startup_background_task("warmup", operation)
        duplicate = harness._create_startup_background_task("warmup", operation)
        self.assertIs(first, duplicate)
        self.assertEqual(1, harness._extension_task_count())
        await asyncio.sleep(0)
        self.assertEqual(1, starts)

        release.set()
        await first
        await asyncio.sleep(0)
        self.assertNotIn("warmup", harness._startup_background_tasks)
        self.assertEqual(0, harness._extension_task_count())

        replacement = harness._create_startup_background_task("warmup", operation)
        self.assertIsNot(first, replacement)
        await replacement
        self.assertEqual(2, starts)

    async def test_lifecycle_registry_tracks_then_cancels_all_live_tasks(self) -> None:
        harness = _TaskHarness()
        started = asyncio.Event()
        cancelled: list[str] = []

        async def operation(label: str) -> None:
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.append(label)
                raise

        first = harness._create_lifecycle_background_task(
            operation("first"), label="first"
        )
        second = harness._create_lifecycle_background_task(
            operation("second"), label="second"
        )
        await started.wait()

        self.assertEqual([(first, "first"), (second, "second")], harness.tracked)
        self.assertEqual(2, harness._extension_task_count())
        harness._task = first
        harness._group_image_understanding_tasks = {"image": {"task": second}}
        self.assertEqual(2, harness._extension_task_count())
        await harness._cancel_lifecycle_background_tasks(timeout=1.0)

        self.assertEqual({"first", "second"}, set(cancelled))
        self.assertTrue(first.cancelled())
        self.assertTrue(second.cancelled())
        self.assertEqual({}, harness._lifecycle_background_tasks)
        self.assertEqual(0, harness._extension_task_count())

    async def test_finished_lifecycle_task_unregisters_itself(self) -> None:
        harness = _TaskHarness()

        async def operation() -> int:
            return 7

        task = harness._create_lifecycle_background_task(operation(), label="short")
        self.assertEqual(7, await task)
        await asyncio.sleep(0)

        self.assertEqual({}, harness._lifecycle_background_tasks)
        self.assertEqual([(task, "short")], harness.tracked)


if __name__ == "__main__":
    unittest.main()
