from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
import sys
import types
import unittest

from tests.module_source_index import user_memory_source_text, file_family_source_text
ROOT = Path(__file__).resolve().parents[1]


def _page_api_family_source() -> str:
    """page_api.py 的方法已随域拆分分散到 page_api_*.py，跨族聚合拼接源码。"""
    return "\n".join(
        (ROOT / name).read_text(encoding="utf-8")
        for name in ["page_api.py", *sorted(p.name for p in ROOT.glob("page_api_*.py"))]
    )

PACKAGE_NAME = "c7_companion_test_package"
package = types.ModuleType(PACKAGE_NAME)
package.__path__ = [str(ROOT)]
sys.modules.setdefault(PACKAGE_NAME, package)

from c7_companion_test_package.bot_personal_outbox import BotPersonalOutbox


class OutboxLifecycleTests(unittest.TestCase):
    def test_async_save_uses_lifecycle_callback_when_provided(self):
        labels: list[str] = []
        operations = []

        def create_task(operation, label):
            labels.append(label)
            operations.append(operation)
            return None

        async def save():
            return None

        outbox = BotPersonalOutbox({}, save=save, background_task=create_task)
        asyncio.run(asyncio.to_thread(outbox._persist))
        self.assertEqual(["bot_personal_outbox_save"], labels)
        self.assertEqual(1, len(operations))
        self.assertEqual(inspect.CORO_CLOSED, inspect.getcoroutinestate(operations[0]))


class CompanionConcurrencyStaticTests(unittest.TestCase):
    def test_data_lock_external_awaits_use_temporary_release_context(self):
        # _temporarily_release_data_lock 已随 main.py 拆分迁至 main_lifecycle.py，
        # 跨宿主族聚合源码断言（86ebc90 范本）。
        main_source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in [ROOT / "main.py", *sorted(ROOT.glob("main_*.py"))]
        )
        pipeline_source = file_family_source_text(ROOT, "message_pipeline.py")
        self.assertIn("async def _temporarily_release_data_lock", main_source)
        # Official v6.0.4b moves passive handlers into message_pipeline.py.
        self.assertGreaterEqual(pipeline_source.count("async with self._temporarily_release_data_lock()"), 7)
        self.assertIn('label="refresh_group_episode"', pipeline_source)
        self.assertNotIn('label="refresh_persona_relationship_inbound"', pipeline_source)

    def test_untracked_main_background_tasks_are_not_left_in_message_paths(self):
        source = "\n".join(
            [(ROOT / name).read_text(encoding="utf-8")
             for name in ("main.py", "message_pipeline.py", "daily_state_tick.py")]
            + [user_memory_source_text(ROOT)]
        )
        for marker in (
            "asyncio.create_task(self._refine_inbound_emotion_with_model",
            "asyncio.create_task(self._maybe_refresh_companion_memory",
            "asyncio.create_task(self._maybe_refresh_group_episode",
            "asyncio.create_task(self._kick_proactive_loop_once",
        ):
            self.assertNotIn(marker, source)

    def test_chat_side_background_boundaries_consume_failures_and_keep_http_errors(self):
        sources = {
            name: file_family_source_text(ROOT, name)
            for name in (
                "config_migration.py",
                "news_exploration.py",
                "page_api.py",
                "private_image.py",
                "tts_enhancement.py",
                "user_memory.py",
            )
        }
        config_source = sources["config_migration.py"]
        self.assertIn("_private_companion_config_save_tasks", config_source)
        self.assertIn("done_task.result()", config_source)
        self.assertIn("except asyncio.CancelledError", config_source)
        for name in ("news_exploration.py", "private_image.py", "tts_enhancement.py", "user_memory.py"):
            self.assertIn("_create_lifecycle_background_task", sources[name])

        # page_api.py 的方法已随域拆分分散到 page_api_*.py，
        # 单文件扫描会漏（86ebc90 范本：跨宿主族聚合）。
        page_source = _page_api_family_source()
        self.assertIn("def _exception_error", page_source)
        self.assertIn("status_code=500", page_source)

    def test_page_error_response_is_non_success_status_without_changing_success_payload_shape(self):
        source = _page_api_family_source()
        self.assertIn('return {"success": True, "data": data, "ts": int(time.time())}', source)
        self.assertIn("def _safe_error_message", source)
        self.assertIn('"success": False', source)
        self.assertIn("status_code=500", source)


if __name__ == "__main__":
    unittest.main()
