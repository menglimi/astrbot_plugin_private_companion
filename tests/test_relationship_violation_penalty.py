from __future__ import annotations

import ast
import copy
import math
import unittest
from pathlib import Path
from typing import Any

from helpers import _now_ts, _safe_float, _safe_int, _single_line
from persona_config import runtime_persona_setting
from relationship_ledger import apply_relationship_event


ROOT = Path(__file__).resolve().parents[1]


def _load_methods(*names: str) -> dict[str, Any]:
    tree = ast.parse((ROOT / "user_memory.py").read_text(encoding="utf-8"))
    owner = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "UserMemoryMixin")
    namespace: dict[str, Any] = {
        "Any": Any,
        "deepcopy": copy.deepcopy,
        "math": math,
        "_now_ts": _now_ts,
        "_safe_float": _safe_float,
        "_safe_int": _safe_int,
        "_single_line": _single_line,
        "runtime_persona_setting": runtime_persona_setting,
    }
    for name in names:
        method = next(node for node in owner.body if isinstance(node, ast.FunctionDef) and node.name == name)
        module = ast.Module(body=[copy.deepcopy(method)], type_ignores=[])
        ast.fix_missing_locations(module)
        exec(compile(module, str(ROOT / "user_memory.py"), "exec"), namespace)
    return namespace


METHODS = _load_methods(
    "_relationship_violation_state",
    "_settle_relationship_violation_recovery",
    "_apply_relationship_violation_policy",
)


class _Host:
    enable_relationship_violation_penalties = True
    relationship_violation_recovery_minutes_per_point = 180

    _relationship_violation_state = METHODS["_relationship_violation_state"]
    _settle_relationship_violation_recovery = METHODS["_settle_relationship_violation_recovery"]
    _apply_relationship_violation_policy = METHODS["_apply_relationship_violation_policy"]

    @staticmethod
    def _private_user_role(user: dict[str, Any], _user_id: str = "") -> str:
        return str(user.get("relationship_role") or "friend")

    @staticmethod
    def _schedule_data_save(*_args, **_kwargs) -> None:
        return None

    @staticmethod
    def _apply_relationship_event(user: dict[str, Any], delta: int, **kwargs: Any) -> dict[str, Any]:
        return apply_relationship_event(
            user,
            delta,
            positive_daily_cap=12,
            event_window_seconds=1800,
            positive_event_cap=4,
            negative_event_cap=12,
            **kwargs,
        )


class RelationshipViolationPenaltyTests(unittest.TestCase):
    def test_violation_penalizes_and_apology_recovers_only_part(self) -> None:
        host = _Host()
        user = {
            "user_id": "secondary-1",
            "relationship_role": "friend",
            "relationship_mode": "normal",
            "relationship_score": 10,
        }
        violation = host._apply_relationship_violation_policy(
            user,
            {
                "emotion_event": "boundary_violation",
                "emotion_target": "bot",
                "emotion_intensity": 100,
                "violation_severity": 3,
                "emotion_reason": "明确越过角色底线",
            },
            event_id="message-1",
            now=1_700_000_000,
        )
        self.assertEqual(3, violation["severity"])
        self.assertEqual(-2, user["relationship_score"])
        self.assertEqual(3, user["relationship_violation"]["unrecovered_points"])

        apology = host._apply_relationship_violation_policy(
            user,
            {"emotion_event": "apology", "emotion_target": "bot"},
            event_id="message-2",
            now=1_700_000_001,
        )
        self.assertEqual(2, apology["recovered"])
        self.assertEqual(1, user["relationship_violation"]["unrecovered_points"])
        self.assertEqual(2, user["relationship_violation"]["apology_recovered_points"])

    def test_repeat_violation_claws_back_apology_credit(self) -> None:
        host = _Host()
        user = {
            "user_id": "secondary-2",
            "relationship_role": "friend",
            "relationship_mode": "normal",
            "relationship_score": 30,
            "relationship_violation": {
                "unrecovered_points": 1,
                "apology_recovered_points": 2,
                "last_recovery_at": 1_700_000_000,
            },
        }
        result = host._apply_relationship_violation_policy(
            user,
            {
                "emotion_event": "boundary_violation",
                "emotion_target": "bot",
                "violation_severity": 1,
                "emotion_reason": "重复越界",
            },
            event_id="message-3",
            now=1_700_000_001,
        )
        self.assertEqual(2, result["clawback"])
        self.assertEqual(1, user["relationship_violation"]["repeat_count"])
        self.assertEqual(0, user["relationship_violation"]["apology_recovered_points"])


if __name__ == "__main__":
    unittest.main()
