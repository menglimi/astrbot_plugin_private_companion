from __future__ import annotations

import json
import asyncio
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


class _Stub:
    def __init__(self, *_args, **_kwargs):
        pass


def _astrbot_stubs() -> dict[str, types.ModuleType]:
    names = (
        "astrbot", "astrbot.api", "astrbot.api.event", "astrbot.api.message_components",
        "astrbot.api.provider", "astrbot.api.star", "astrbot.core", "astrbot.core.agent",
        "astrbot.core.agent.message", "astrbot.core.astr_main_agent", "astrbot.core.db",
        "astrbot.core.db.po", "astrbot.core.message", "astrbot.core.message.components",
        "astrbot.core.platform", "astrbot.core.platform.astrbot_message",
        "astrbot.core.platform.message_session", "astrbot.core.platform.message_type",
        "astrbot.core.platform.platform", "astrbot.core.platform.platform_metadata",
        "astrbot.core.provider", "astrbot.core.provider.entities", "astrbot.core.star",
        "astrbot.core.star.star_handler", "astrbot.core.utils", "astrbot.core.utils.astrbot_path",
    )
    modules = {name: types.ModuleType(name) for name in names}
    for name, module in modules.items():
        if any(other.startswith(f"{name}.") for other in names):
            module.__path__ = []
        module.__getattr__ = lambda _name: _Stub
    for name, module in modules.items():
        if "." in name:
            parent, child = name.rsplit(".", 1)
            setattr(modules[parent], child, module)
    modules["astrbot.api"].logger = types.SimpleNamespace(
        debug=lambda *_args, **_kwargs: None,
        info=lambda *_args, **_kwargs: None,
        warning=lambda *_args, **_kwargs: None,
        error=lambda *_args, **_kwargs: None,
    )
    modules["astrbot.api"].AstrBotConfig = dict
    modules["astrbot.api.event"].AstrMessageEvent = type("AstrMessageEvent", (), {})
    modules["astrbot.api.event"].MessageChain = _Stub
    modules["astrbot.api.event"].filter = _Stub
    modules["astrbot.core.utils.astrbot_path"].get_astrbot_data_path = lambda: tempfile.gettempdir()
    return modules


with mock.patch.dict(sys.modules, _astrbot_stubs()):
    root = Path(__file__).resolve().parents[1]
    package = types.ModuleType("astrbot_plugin_private_companion")
    package.__path__ = [str(root)]
    package.__package__ = "astrbot_plugin_private_companion"
    sys.modules.setdefault("astrbot_plugin_private_companion", package)
    from astrbot_plugin_private_companion.proactive_message import ProactiveMessageMixin


class _Harness(ProactiveMessageMixin):
    def __init__(self, data_dir: str, *, max_size_kb: int = 2048, backups: int = 2):
        self.data_dir = data_dir
        self.photo_generation_trace_max_size_kb = max_size_kb
        self.photo_generation_trace_backup_count = backups


class _SelectionHarness(_Harness):
    def __init__(self, data_dir: str):
        super().__init__(data_dir)
        self.enable_photo_reference_image = True
        self._candidates = [
            {
                "id": "sleepwear-bedroom",
                "kind": "library",
                "path": "C:/images/sleepwear.png",
                "note": "bedroom sleepwear after shower",
                "reference_roles": ["identity", "outfit", "scene"],
                "outfit_category": "sleepwear",
                "outfit_lock_default": True,
                "scene_categories": ["home", "bedroom"],
                "time_categories": ["night", "bedtime"],
                "metadata_source": "configured",
            },
            {
                "id": "school-uniform",
                "kind": "library",
                "path": "C:/images/school.png",
                "note": "school uniform in classroom",
                "reference_roles": ["identity", "outfit", "scene"],
                "outfit_category": "school_uniform",
                "outfit_lock_default": True,
                "scene_categories": ["school"],
                "time_categories": ["daytime"],
                "metadata_source": "configured",
            },
            {
                "id": "persona",
                "kind": "persona",
                "path": "C:/images/persona.png",
                "note": "identity only",
                "reference_roles": ["identity"],
                "outfit_category": "",
                "outfit_lock_default": False,
                "scene_categories": [],
                "time_categories": [],
                "metadata_source": "configured",
            },
        ]

    async def _photo_reference_candidates_async(self, *, allow_daily_outfit: bool = True):
        return [dict(candidate) for candidate in self._candidates]

    @staticmethod
    def _recent_sent_photo_continuity_candidate(_continuity_key: str):
        return {}

    @staticmethod
    def _task_provider(*_provider_ids: str) -> str:
        return ""

    @staticmethod
    async def _llm_call(_prompt: str, **_kwargs):
        return "1"


class PhotoGenerationTraceTests(unittest.TestCase):
    @staticmethod
    def _read_lines(path: Path) -> list[dict[str, object]]:
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def test_writes_json_events_to_txt_with_shared_trace_sequence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            harness = _Harness(temp_dir)
            harness._append_photo_generation_trace_event(
                "trace-1",
                "request_received",
                context={"session": "session-1", "workflow_kind": "selfie"},
            )
            harness._append_photo_generation_trace_event(
                "trace-1", "completed", data={"image_path": "C:/out.png"}
            )

            path = Path(temp_dir) / "photo_generation_trace.txt"
            events = self._read_lines(path)
            self.assertEqual(path.suffix, ".txt")
            self.assertEqual([event["seq"] for event in events], [1, 2])
            self.assertEqual([event["stage"] for event in events], ["request_received", "completed"])
            self.assertEqual(events[1]["context"]["session"], "session-1")

    def test_rotates_txt_files_at_configured_size(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            harness = _Harness(temp_dir, max_size_kb=1, backups=2)
            for index in range(8):
                harness._append_photo_generation_trace_event(
                    f"trace-{index}",
                    "prompt_composed",
                    data={"prompt": f"{index}-" + ("x" * 700)},
                )

            root = Path(temp_dir)
            self.assertTrue((root / "photo_generation_trace.txt").is_file())
            self.assertTrue((root / "photo_generation_trace.1.txt").is_file())
            self.assertTrue((root / "photo_generation_trace.2.txt").is_file())
            self.assertFalse((root / "photo_generation_trace.3.txt").exists())

    def test_oversized_single_event_is_replaced_with_bounded_record(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            harness = _Harness(temp_dir, max_size_kb=1, backups=2)
            harness._append_photo_generation_trace_event(
                "trace-large",
                "reference_candidates",
                context={f"context-{index}": "x" * 1200 for index in range(48)},
                data={f"candidate-{index}": "x" * 1200 for index in range(48)},
            )

            path = Path(temp_dir) / "photo_generation_trace.txt"
            self.assertLessEqual(path.stat().st_size, 1024)
            event = self._read_lines(path)[0]
            self.assertTrue(event["context"]["truncated"])
            self.assertTrue(event["data"]["truncated"])
            self.assertGreater(event["data"]["original_bytes"], 1024)

    def test_zero_size_disables_file_logging(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            harness = _Harness(temp_dir, max_size_kb=0)
            harness._append_photo_generation_trace_event("trace-1", "request_received")
            self.assertFalse((Path(temp_dir) / "photo_generation_trace.txt").exists())

    def test_zero_backups_discards_rotated_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            harness = _Harness(temp_dir, max_size_kb=1, backups=0)
            for index in range(3):
                harness._append_photo_generation_trace_event(
                    f"trace-{index}",
                    "prompt_composed",
                    data={"prompt": f"{index}-" + ("x" * 700)},
                )

            root = Path(temp_dir)
            self.assertTrue((root / "photo_generation_trace.txt").is_file())
            self.assertFalse((root / "photo_generation_trace.1.txt").exists())

    def test_redacts_sensitive_keys_and_url_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            harness = _Harness(temp_dir)
            harness._append_photo_generation_trace_event(
                "trace-1",
                "backend_selected",
                data={"api_key": "plain-secret", "url": "https://example.test/image?api_key=plain-secret"},
            )
            payload = self._read_lines(Path(temp_dir) / "photo_generation_trace.txt")[0]["data"]
            self.assertEqual(payload["api_key"], "***")
            self.assertNotIn("plain-secret", payload["url"])

    def test_reference_candidates_include_metadata_scores_and_selection_reason(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            harness = _SelectionHarness(temp_dir)
            selected = asyncio.run(
                harness._select_photo_reference_candidate_async(
                    "selfie",
                    request_text="take a selfie after shower in bedroom sleepwear",
                    ambient_context="home bedroom at night after shower",
                    schedule_history_context="08:00-09:00｜已完成｜在学校上课",
                    trace_id="trace-selection",
                )
            )
            events = self._read_lines(Path(temp_dir) / "photo_generation_trace.txt")
            event = events[-1]
            candidates = event["data"]["candidates"]

            self.assertEqual(selected["id"], "sleepwear-bedroom")
            self.assertEqual(event["stage"], "reference_candidates")
            self.assertEqual(event["data"]["selection_source"], "model")
            self.assertEqual(event["data"]["selection_reason"], "valid_candidate_number")
            self.assertTrue(event["data"]["schedule_history_used"])
            self.assertIn("在学校上课", event["data"]["schedule_history_context"])
            self.assertEqual(candidates[0]["outfit_category"], "sleepwear")
            self.assertEqual(candidates[0]["scene_categories"], ["home", "bedroom"])
            self.assertEqual(candidates[0]["time_categories"], ["night", "bedtime"])
            self.assertIsInstance(candidates[0]["score"], float)


if __name__ == "__main__":
    unittest.main()
