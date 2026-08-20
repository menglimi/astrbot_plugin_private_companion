from __future__ import annotations

import asyncio
import json
import math
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


class _Logger:
    def __getattr__(self, _name: str):
        return lambda *_args, **_kwargs: None


def _runtime_stubs() -> dict[str, types.ModuleType]:
    astrbot = types.ModuleType("astrbot")
    astrbot.__path__ = []
    api = types.ModuleType("astrbot.api")
    api.__path__ = []
    api_event = types.ModuleType("astrbot.api.event")
    core = types.ModuleType("astrbot.core")
    core.__path__ = []
    utils = types.ModuleType("astrbot.core.utils")
    utils.__path__ = []
    astrbot_path = types.ModuleType("astrbot.core.utils.astrbot_path")
    quart = types.ModuleType("quart")

    api.logger = _Logger()
    api_event.MessageChain = list
    astrbot_path.get_astrbot_data_path = tempfile.gettempdir
    quart.request = SimpleNamespace(args={})

    async def send_file(*_args, **_kwargs):
        return None

    quart.send_file = send_file
    astrbot.api = api
    astrbot.core = core
    api.event = api_event
    core.utils = utils
    utils.astrbot_path = astrbot_path
    return {
        "astrbot": astrbot,
        "astrbot.api": api,
        "astrbot.api.event": api_event,
        "astrbot.core": core,
        "astrbot.core.utils": utils,
        "astrbot.core.utils.astrbot_path": astrbot_path,
        "quart": quart,
    }


with mock.patch.dict(sys.modules, _runtime_stubs()):
    package_name = "astrbot_plugin_private_companion"
    if package_name not in sys.modules:
        package = types.ModuleType(package_name)
        package.__path__ = [str(Path(__file__).resolve().parents[1])]
        package.__package__ = package_name
        sys.modules[package_name] = package
    from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi
    PAGE_API_MODULE = sys.modules["astrbot_plugin_private_companion.page_api"]


class _ImageApi:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir

    def status(self) -> dict[str, object]:
        return {
            "installed": True,
            "enabled": True,
            "available": True,
            "state": "managed",
            "debug": {"enabled": True, "capture_mode": "redacted", "sensitive": False},
        }

    def debug_data_dirs(self, _owner: object) -> list[str]:
        return [str(self.data_dir)]


class _Plugin:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = str(data_dir)
        self._api = _ImageApi(data_dir)

    def _image_companion_api(self) -> _ImageApi:
        return self._api


def _event(
    *,
    trace: str,
    seq: int,
    ts: float,
    stage: str = "backend_submit",
    status: str = "ok",
    source: str = "legacy",
) -> dict[str, object]:
    event: dict[str, object] = {
        "schema_version": 1,
        "trace": trace,
        "seq": seq,
        "ts": ts,
        "time": "2026-08-20T12:00:00+00:00",
        "elapsed_ms": 12,
        "stage": stage,
        "status": status,
        "context": {"session": "FriendMessage:10001"},
        "data": {"prompt": "a quiet room"},
    }
    if source == "unified":
        event.pop("trace")
        event["trace_id"] = trace
        event["request_id"] = trace
        event["event_id"] = f"event-{trace}-{seq}"
        event["severity"] = "info"
        event.update(
            {
                "operation": "",
                "workflow": "",
                "backend": "",
                "route": "",
                "attempt": None,
                "error_code": "",
                "failure_stage": "",
            }
        )
    return event


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )


class ImageDebugPageApiTests(unittest.TestCase):
    def _api(self, data_dir: Path) -> PrivateCompanionPageApi:
        return PrivateCompanionPageApi(_Plugin(data_dir))

    def test_status_returns_only_latest_summary_without_event_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_jsonl(
                root / "photo_debug" / "generation.jsonl",
                [_event(trace="trace-latest", seq=1, ts=1001.0, source="unified")],
            )
            result = asyncio.run(self._api(root).get_image_extension_status())

        debug = result["data"]["photo_debug"]
        self.assertEqual(debug["events"], [])
        self.assertEqual(debug["latest"]["trace"], "trace-latest")
        self.assertNotIn("data", debug["latest"])
        self.assertNotIn(str(root), repr(debug))

    def test_expanded_trace_returns_full_selected_events_and_all_trace_options(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_jsonl(
                root / "photo_debug" / "generation.jsonl",
                [
                    _event(trace="trace-first", seq=1, ts=1000.0, source="unified"),
                    _event(trace="trace-latest", seq=1, ts=1001.0, source="unified"),
                ],
            )
            api = self._api(root)
            request_stub = SimpleNamespace(args={"trace": "trace-first", "limit": "240"})
            with mock.patch.object(PAGE_API_MODULE, "request", request_stub):
                result = asyncio.run(api.get_image_debug())

        payload = result["data"]
        self.assertEqual(payload["requested_trace"], "trace-first")
        self.assertEqual([item["trace"] for item in payload["events"]], ["trace-first"])
        self.assertEqual({item["trace"] for item in payload["traces"]}, {"trace-first", "trace-latest"})
        self.assertEqual(payload["events"][0]["data"]["prompt"], "a quiet room")

    def test_expanded_trace_includes_safe_payload_sidecar_contents(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sidecar = root / "photo_debug" / "traces" / "trace-payload" / "payloads" / "request_1.json"
            sidecar.parent.mkdir(parents=True, exist_ok=True)
            sidecar.write_text('{"prompt":"full body","api_key":"secret"}', encoding="utf-8")
            row = _event(trace="trace-payload", seq=1, ts=1000.0, source="unified")
            row["data"] = {
                "payloads": {
                    "request": {
                        "captured": True,
                        "path": "traces/trace-payload/payloads/request_1.json",
                        "mime_type": "application/json",
                        "encoding": "utf-8",
                    }
                }
            }
            _write_jsonl(root / "photo_debug" / "generation.jsonl", [row])
            request_stub = SimpleNamespace(args={"trace": "trace-payload", "limit": "240"})
            with mock.patch.object(PAGE_API_MODULE, "request", request_stub):
                result = asyncio.run(self._api(root).get_image_debug())

        metadata = result["data"]["events"][0]["data"]["payloads"]["request"]
        self.assertIn('"prompt":"full body"', metadata["content"])
        self.assertIn('"api_key":"secret"', metadata["content"])

    def test_dual_written_events_are_coalesced_even_with_independent_sequences(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_jsonl(
                root / "photo_generation_trace.txt",
                [_event(trace="trace-a", seq=4, ts=1000.00)],
            )
            _write_jsonl(
                root / "photo_debug" / "generation.jsonl",
                [_event(trace="trace-a", seq=99, ts=1000.35, source="unified")],
            )
            payload = self._api(root)._recent_photo_generation_debug()

        self.assertEqual(len(payload["events"]), 1)
        self.assertEqual(payload["events"][0]["source_file"], "photo_debug/generation.jsonl")

    def test_same_source_repeated_stage_is_retained(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_jsonl(
                root / "photo_generation_trace.txt",
                [
                    _event(trace="trace-a", seq=1, ts=1000.0),
                    _event(trace="trace-a", seq=2, ts=1000.1),
                ],
            )
            payload = self._api(root)._recent_photo_generation_debug()

        self.assertEqual(len(payload["events"]), 2)
        self.assertEqual(payload["traces"][0]["event_count"], 2)

    def test_tail_reader_handles_utf8_long_line_and_empty_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            empty = root / "empty.jsonl"
            empty.write_bytes(b"")
            long_line = root / "long.jsonl"
            long_line.write_text(json.dumps({"text": "生图" * 25000}, ensure_ascii=False) + "\n", encoding="utf-8")

            self.assertEqual(PrivateCompanionPageApi._read_debug_lines(empty, tail_lines=8), [])
            lines = PrivateCompanionPageApi._read_debug_lines(long_line, tail_lines=1)

        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0])["text"][:2], "生图")

    def test_symbolic_link_debug_file_is_not_read(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            outside = root.parent / f"image-debug-outside-{os.getpid()}.jsonl"
            _write_jsonl(outside, [_event(trace="trace-outside", seq=1, ts=1000.0)])
            link = root / "photo_debug" / "generation.jsonl"
            link.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.symlink(outside, link)
            except OSError:
                self.skipTest("当前环境不允许创建符号链接")
            try:
                payload = self._api(root)._recent_photo_generation_debug()
            finally:
                outside.unlink(missing_ok=True)

        self.assertFalse(payload["available"])
        self.assertEqual(payload["events"], [])

    def test_timestamp_rejects_non_finite_values(self) -> None:
        self.assertEqual(PrivateCompanionPageApi._photo_debug_event_timestamp({"ts": "bad"}), 0.0)
        self.assertEqual(PrivateCompanionPageApi._photo_debug_event_timestamp({"ts": math.inf}), 0.0)

    def test_malformed_timestamp_and_sequence_do_not_break_debug_reads(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            malformed = _event(trace="trace-bad", seq=1, ts=1000.0)
            malformed["ts"] = "not-a-number"
            malformed["seq"] = "bad"
            valid = _event(trace="trace-good", seq=2, ts=1001.0)
            _write_jsonl(root / "photo_debug" / "generation.jsonl", [malformed, valid])

            payload = self._api(root)._recent_photo_generation_debug()

        self.assertEqual([item["trace"] for item in payload["events"]], ["trace-bad", "trace-good"])
        self.assertEqual(PrivateCompanionPageApi._photo_debug_event_sequence({"seq": "bad"}), 0)
        self.assertEqual(PrivateCompanionPageApi._photo_debug_event_sequence({"seq": float("nan")}), 0)

    def test_trace_options_keep_latest_trace_after_last_event_sorting(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            rows = [
                _event(trace=f"trace-{index}", seq=1, ts=1000.0 + index)
                for index in range(25)
            ]
            # This Trace first appeared early but finished after all newer
            # Trace IDs, so it must remain in the 24-item chooser.
            rows.append(_event(trace="trace-0", seq=2, ts=2000.0, stage="completed", status="completed"))
            _write_jsonl(root / "photo_debug" / "generation.jsonl", rows)

            payload = self._api(root)._recent_photo_generation_debug()

        trace_ids = [item["trace"] for item in payload["traces"]]
        self.assertEqual(payload["latest"]["trace"], "trace-0")
        self.assertIn("trace-0", trace_ids)
        self.assertEqual(len(trace_ids), 24)


if __name__ == "__main__":
    unittest.main()
