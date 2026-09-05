from __future__ import annotations

import ast
import copy
from datetime import datetime
import hashlib
from pathlib import Path
import re
import sys
from typing import Any
import unittest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from emotion_event_ledger import record_recent_emotion_event  # noqa: E402
from emotion_targeting import classify_emotion_target
from tests.emotion_eval_cases import (
    EMOTION_EVAL_SCHEMA_VERSION,
    build_emotion_eval_cases,
    emotion_eval_fingerprint,
)


def _single_line(value: Any, limit: int = 80) -> str:
    return " ".join(str(value or "").split())[:limit]


def _safe_float(value: Any, default: float = 0.0, minimum: float = 0.0, maximum: float | None = None) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        result = default
    result = max(minimum, result)
    return min(maximum, result) if maximum is not None else result


def _safe_int(value: Any, default: int = 0, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        result = default
    if minimum is not None:
        result = max(minimum, result)
    if maximum is not None:
        result = min(maximum, result)
    return result


def _load_user_memory_method(name: str, namespace: dict[str, Any]) -> Any:
    path = ROOT / "user_memory.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    owner = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "UserMemoryMixin")
    method = next(node for node in owner.body if isinstance(node, ast.FunctionDef) and node.name == name)
    module = ast.Module(body=[copy.deepcopy(method)], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(path), "exec"), namespace)
    return namespace[name]


CLASSIFY_EVENT = _load_user_memory_method(
    "_classify_relationship_emotion_event",
    {
        "Any": Any,
        "classify_emotion_target": classify_emotion_target,
        "re": re,
        "_safe_float": _safe_float,
        "_single_line": _single_line,
    },
)
RECORD_EVENT = _load_user_memory_method(
    "_record_interaction_emotion_event",
    {
        "Any": Any,
        "datetime": datetime,
        "hashlib": hashlib,
        "record_recent_emotion_event": record_recent_emotion_event,
        "_safe_float": _safe_float,
        "_safe_int": _safe_int,
        "_single_line": _single_line,
    },
)


class _EmotionHost:
    _classify_relationship_emotion_event = CLASSIFY_EVENT
    _record_interaction_emotion_event = RECORD_EVENT

    @staticmethod
    def _is_structured_or_diagnostic_text(_text: str) -> bool:
        return False

    @staticmethod
    def _intent_target_hint(_text: str) -> tuple[bool, bool]:
        return False, False

    @staticmethod
    def _memory_companion_bridge_bot_id() -> str:
        return "bot-1"


class EmotionE7TargetAttributionTests(unittest.TestCase):
    def test_direct_bot_hurt_is_high_confidence_and_settleable(self) -> None:
        result = classify_emotion_target("你真是个没用的垃圾，闭嘴")
        self.assertEqual("bot", result["target"])
        self.assertGreaterEqual(result["confidence"], 0.9)
        self.assertTrue(result["auto_settle"])

    def test_self_third_party_quote_and_logs_never_target_bot(self) -> None:
        samples = {
            "我真是个没用的废物": "self",
            "我同事真是个废物，烦死了": "other",
            "他说“你就是个垃圾”，我该怎么办": "other",
            "ERROR: user said 你是垃圾\nTraceback: line 1": "none",
            "```json\n{\"message\": \"你是垃圾\"}\n```": "none",
        }
        for text, expected in samples.items():
            with self.subTest(text=text):
                result = classify_emotion_target(text)
                self.assertEqual(expected, result["target"])
                self.assertFalse(result["auto_settle"])

    def test_ambiguous_negative_fails_neutral(self) -> None:
        result = classify_emotion_target("真垃圾，烦死了")
        self.assertEqual("ambiguous", result["target"])
        self.assertLess(result["confidence"], 0.65)
        self.assertFalse(result["auto_settle"])

    def test_inbound_classifier_does_not_settle_an_ambiguous_negative_as_bot_hurt(self) -> None:
        host = _EmotionHost()
        ambiguous = host._classify_relationship_emotion_event("真垃圾，烦死了")
        direct = host._classify_relationship_emotion_event("你真是个没用的垃圾，闭嘴")

        self.assertEqual("neutral", ambiguous["event"])
        self.assertEqual("ambiguous", ambiguous["target"])
        self.assertEqual("hurt", direct["event"])
        self.assertEqual("bot", direct["target"])

    def test_ambiguous_observation_is_unknown_and_model_revision_keeps_trace_identity(self) -> None:
        host = _EmotionHost()
        user = {"user_id": "user-1", "umo": "qq:FriendMessage:user-1"}
        observed = host._record_interaction_emotion_event(
            user,
            {
                "intent": "chat",
                "emotion_event": "neutral",
                "emotion_target": "ambiguous",
                "emotion_intensity": 0,
                "emotion_confidence": 0.35,
                "emotion_attribution": {"target": "ambiguous", "auto_settle": False},
                "text": "真垃圾，烦死了",
            },
            band="relaxed",
            reason_code="target_review_pending",
            status="observed",
        )
        assert observed is not None
        self.assertEqual("neutral", observed["event_type"])
        self.assertEqual("observed", observed["status"])
        self.assertEqual("unknown", observed["target_ref"]["kind"])

        revised = host._record_interaction_emotion_event(
            user,
            {
                "intent": "chat",
                "emotion_event": "hurt",
                "emotion_target": "bot",
                "emotion_intensity": 92,
                "emotion_confidence": 0.94,
                "text": "真垃圾，烦死了",
                "_emotion_revision_of": {
                    "event_id": observed["event_id"],
                    "trace_id": observed["trace_id"],
                    "revision": observed["revision"] + 1,
                },
            },
            band="avoidant",
            reason_code="model_target_review",
        )
        assert revised is not None
        self.assertEqual(observed["event_id"], revised["event_id"])
        self.assertEqual(observed["trace_id"], revised["trace_id"])
        self.assertEqual(2, revised["revision"])
        self.assertEqual(observed["event_id"], revised["correction_of"])
        self.assertEqual("bot", revised["target_ref"]["kind"])

    def test_shared_evaluation_fixture_has_stable_semantics(self) -> None:
        cases = build_emotion_eval_cases()
        self.assertEqual(EMOTION_EVAL_SCHEMA_VERSION, "emotion_eval_case.v1")
        self.assertEqual(120, len(cases))
        self.assertEqual(120, len({case["case_id"] for case in cases}))
        self.assertEqual(
            "d74d47eebfd26ecbaef3d013d56c1add1602401104a3d991f66f30921225ba99",
            emotion_eval_fingerprint(),
        )
