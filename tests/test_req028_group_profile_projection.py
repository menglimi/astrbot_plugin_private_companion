from __future__ import annotations

import ast
import copy
from pathlib import Path
import re
import time
from typing import Any
import unittest


ROOT = Path(__file__).resolve().parents[1]


def _single_line(value: Any, limit: int = 1000) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _safe_int(value: Any, default: int = 0, minimum: int | None = None) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        result = default
    return max(minimum, result) if minimum is not None else result


class _Logger:
    def info(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def _load_methods(filename: str, class_name: str, names: set[str]) -> dict[str, Any]:
    source = (ROOT / filename).read_text(encoding="utf-8")
    tree = ast.parse(source)
    owner = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    namespace: dict[str, Any] = {
        "Any": Any,
        "_single_line": _single_line,
        "_safe_float": lambda value, default=0.0: float(value) if isinstance(value, (int, float)) else default,
        "_safe_int": _safe_int,
        "_now_ts": lambda: 123.0,
        "logger": _Logger(),
        "time": time,
        "deepcopy": copy.deepcopy,
        "_persona_value": lambda host, key, default=None: getattr(host, key, default),
    }
    for node in owner.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            module = ast.Module(body=[copy.deepcopy(node)], type_ignores=[])
            ast.fix_missing_locations(module)
            exec(compile(module, str(ROOT / filename), "exec"), namespace)
    return {name: namespace[name] for name in names}


GROUP_METHODS = _load_methods(
    "group_observation.py",
    "GroupObservationMixin",
    {
        "_update_group_observation",
        "_capture_group_observation_once",
        "_group_observation_marker_matches",
        "_effective_group_history_limit",
        "_trim_group_history_lists",
    },
)


class _GroupHost:
    max_group_recent_messages = 12
    enable_group_slang_learning = False
    enable_group_topic_threads = False
    enable_group_relationship_graph = False
    enable_group_interjection_feedback = False
    _GROUP_ROLE_LABELS = {"unknown": "未知"}

    def __init__(self) -> None:
        self.data: dict[str, Any] = {}

    def _analyze_group_injection_guard(self, _text: str, *, sender_id: str = "") -> dict[str, Any]:
        return {"blocked": False, "score": 0, "reasons": []}

    def _group_name_from_event(self, _event: Any) -> str:
        return ""

    def _sender_qq_nickname(self, _event: Any) -> str:
        return "QQ昵称"

    @staticmethod
    def _group_member_identity_name(_user_id: str, fallback: str = "", *, limit: int = 30) -> str:
        return _single_line(fallback, limit)

    def _record_user_recent_group_message_from_observation(self, **_kwargs: Any) -> None:
        return None

    @staticmethod
    def _expression_group_learning_source_enabled(_group_id: str) -> bool:
        return False

    def _update_group_atmosphere(self, _group: dict[str, Any]) -> None:
        return None


for _name, _method in GROUP_METHODS.items():
    setattr(_GroupHost, _name, _method)


class GroupProfileProjectionTests(unittest.TestCase):
    def test_group_observation_stays_inside_each_group_and_creates_no_legacy_personal_profile(self) -> None:
        host = _GroupHost()
        group_a: dict[str, Any] = {}
        group_b: dict[str, Any] = {}

        host._update_group_observation(group_a, sender_id="10001", sender_name="甲群名片", text="你好", group_id="group-a")
        host._update_group_observation(group_b, sender_id="10001", sender_name="乙群名片", text="又见面了", group_id="group-b")

        self.assertNotIn("users", host.data)
        self.assertNotIn("worldbook_member_profiles", host.data)
        self.assertEqual("甲群名片", group_a["recent_messages"][0]["name"])
        self.assertEqual("乙群名片", group_b["recent_messages"][0]["name"])
        self.assertNotIn("又见面了", str(group_a))
        self.assertNotIn("你好", str(group_b))

    def test_empty_ingress_does_not_create_group_observation_records(self) -> None:
        host = _GroupHost()
        group: dict[str, Any] = {}
        self.assertFalse(host._capture_group_observation_once({}, sender_id="10001", sender_name="名片", text="", group_id="group-a"))
        self.assertEqual({}, group)

    def test_group_capture_is_behind_the_whitelisted_group_pipeline_gate(self) -> None:
        source = (ROOT / "message_pipeline.py").read_text(encoding="utf-8")
        gate = "if not group_id or not self._group_enabled_for_event(group_id):"
        capture = "await self._capture_group_observation_event("
        self.assertIn(gate, source)
        self.assertIn(capture, source)
        self.assertLess(source.index(gate), source.index(capture))


if __name__ == "__main__":
    unittest.main()
