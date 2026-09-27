# -*- coding: utf-8 -*-
"""Regression: an oversized single line must not blank out the whole tail (P1).

``_read_debug_lines`` used to walk back from EOF in 64 KiB..1 MiB windows until
it landed on a newline boundary.  When the window stayed *inside* one line that
is larger than the 1 MiB hard cap, the "drop the partial record" branch ran
``content.find(b"\\n")``, got ``-1`` and replaced the whole buffer with ``b""`` —
so every complete, valid record read in that window was thrown away and the
collapsed debug panel rendered empty.  One oversized traceback / LLM response
appended to the log was enough to trigger it.
"""
from __future__ import annotations

import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


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


OVERSIZE = 2 * 1024 * 1024  # 2 MiB, well past the 1 MiB tail cap


def _record(tag: str, filler: int = 0) -> str:
    return json.dumps({"tag": tag, "filler": "x" * filler}, ensure_ascii=False)


class _ReadDebugLinesOversizeTests(unittest.TestCase):
    def test_oversized_line_between_normal_lines_keeps_tail(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "generation.jsonl"
            normal = [_record(f"line-{index}") for index in range(20)]
            path.write_text(
                "\n".join([*normal[:10], _record("oversize", OVERSIZE), *normal[10:]]) + "\n",
                encoding="utf-8",
            )

            lines = PrivateCompanionPageApi._read_debug_lines(path, tail_lines=20)

            self.assertTrue(lines, "oversized line must not blank out the whole tail")
            tags = [json.loads(line)["tag"] for line in lines]
            self.assertIn("line-19", tags, "trailing records after the oversized line must survive")
            self.assertIn("line-10", tags, "records right after the oversized line must survive")

    def test_oversized_line_at_eof_keeps_preceding_lines(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "generation.jsonl"
            normal = [_record(f"line-{index}") for index in range(20)]
            path.write_text(
                "\n".join([*normal, _record("oversize", OVERSIZE)]) + "\n",
                encoding="utf-8",
            )

            lines = PrivateCompanionPageApi._read_debug_lines(path, tail_lines=8)

            self.assertTrue(lines, "oversized trailing line must not blank out the whole tail")
            tags = [json.loads(line)["tag"] for line in lines]
            self.assertIn("line-19", tags, "the line preceding the oversized record must survive")

    def test_file_with_only_one_oversized_line(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "generation.jsonl"
            path.write_text(_record("only", OVERSIZE) + "\n", encoding="utf-8")

            lines = PrivateCompanionPageApi._read_debug_lines(path, tail_lines=4)

            self.assertEqual(len(lines), 1, "a lone oversized line is still one readable record")
            self.assertEqual(json.loads(lines[0])["tag"], "only")

    def test_small_tail_budget_still_returns_the_oversized_record(self) -> None:
        """tail_lines=1 with a >1 MiB line must not silently degrade to empty."""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "generation.jsonl"
            path.write_text(_record("only", OVERSIZE) + "\n", encoding="utf-8")

            lines = PrivateCompanionPageApi._read_debug_lines(path, tail_lines=1)

            self.assertEqual(len(lines), 1)
            self.assertEqual(json.loads(lines[0])["tag"], "only")

    def test_normal_file_is_unaffected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "generation.jsonl"
            path.write_text(
                "\n".join(_record(f"line-{index}") for index in range(200)) + "\n",
                encoding="utf-8",
            )

            lines = PrivateCompanionPageApi._read_debug_lines(path, tail_lines=5)

            self.assertEqual(len(lines), 5)
            self.assertEqual(json.loads(lines[-1])["tag"], "line-199")

    def test_empty_file_still_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "generation.jsonl"
            path.write_bytes(b"")

            self.assertEqual(PrivateCompanionPageApi._read_debug_lines(path, tail_lines=8), [])

    def test_unbounded_read_returns_every_line(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "generation.jsonl"
            path.write_text("\n".join(_record(f"line-{index}") for index in range(7)) + "\n", encoding="utf-8")

            lines = PrivateCompanionPageApi._read_debug_lines(path)

            self.assertEqual(len(lines), 7)

    def test_oversized_line_does_not_force_a_full_file_read(self) -> None:
        """The fix must stay tail-based: a huge file must not be read in full."""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "generation.jsonl"
            pad = "\n".join(_record(f"pad-{index}") for index in range(4000))
            path.write_text(
                pad + "\n" + _record("oversize", OVERSIZE) + "\n" + _record("tail") + "\n",
                encoding="utf-8",
            )
            total = path.stat().st_size

            decoder = PrivateCompanionPageApi._read_debug_lines  # noqa: F841
            with mock.patch.object(
                type(path), "read_text", side_effect=AssertionError("full-file read is forbidden")
            ):
                lines = PrivateCompanionPageApi._read_debug_lines(path, tail_lines=4)

            self.assertLess(total, 4 * 1024 * 1024)
            self.assertTrue(lines)
            self.assertEqual(json.loads(lines[-1])["tag"], "tail")


if __name__ == "__main__":
    unittest.main()
