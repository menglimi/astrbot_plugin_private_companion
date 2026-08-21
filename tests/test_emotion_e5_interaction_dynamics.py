from __future__ import annotations

import ast
import copy
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import Any
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from companion_interaction_expression import current_interaction_projection
from interaction_dynamics import settle_interaction_dynamics
from persona_config import runtime_persona_setting
from relationship_policy import relationship_projection_for_bridge, relationship_stage_for_score


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


def _single_line(value: Any, limit: int = 80) -> str:
    return " ".join(str(value or "").split())[:limit]


_CLOCK = [1_000.0]


def _load_interaction_settler() -> Any:
    path = ROOT / "user_memory.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    owner = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "UserMemoryMixin")
    method = next(
        node
        for node in owner.body
        if isinstance(node, ast.FunctionDef) and node.name == "_settle_current_interaction_from_intent"
    )
    namespace: dict[str, Any] = {
        "Any": Any,
        "current_interaction_projection": current_interaction_projection,
        "logger": SimpleNamespace(info=lambda *_args, **_kwargs: None),
        "settle_interaction_dynamics": settle_interaction_dynamics,
        "_now_ts": lambda: _CLOCK[0],
        "_safe_float": _safe_float,
        "_safe_int": _safe_int,
        "_single_line": _single_line,
        "runtime_persona_setting": runtime_persona_setting,
    }
    module = ast.Module(body=[copy.deepcopy(method)], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(path), "exec"), namespace)
    return namespace["_settle_current_interaction_from_intent"]


SETTLE_INTERACTION = _load_interaction_settler()


class _InteractionHost:
    enable_emotion_simulation = True
    enable_relationship_state_machine = True
    normal_interaction_band_cap = "warm"
    emotional_gate_hurt_threshold = 70
    emotional_gate_refuse_threshold = 90
    emotional_gate_recovery_per_hour = 24
    emotional_gate_max_hurt_minutes = 90

    _settle_current_interaction_from_intent = SETTLE_INTERACTION

    @staticmethod
    def _private_user_role(user: dict[str, Any], _user_id: str) -> str:
        return str(user.get("relationship_role") or "friend")


class EmotionE5InteractionDynamicsTests(unittest.TestCase):
    def setUp(self) -> None:
        _CLOCK[0] = 1_000.0

    def test_ninety_minute_boundary_has_no_ttl_cliff(self) -> None:
        state = settle_interaction_dynamics(
            {}, requested_band="hurt", event_kind="hurt", intensity=60, now=1000.0
        )
        at_89 = current_interaction_projection(state, now=1000.0 + 89 * 60)
        at_90 = current_interaction_projection(state, now=1000.0 + 90 * 60)
        self.assertEqual("hurt", at_89["expression_band"])
        self.assertEqual("hurt", at_90["expression_band"])
        self.assertLess(at_90["load"], at_89["load"])
        self.assertLess(at_89["load"] - at_90["load"], 1.0)

    def test_repeated_hurt_is_bounded_and_apology_recovers_gradually(self) -> None:
        state: dict = {}
        for offset in range(6):
            state = settle_interaction_dynamics(
                state, requested_band="avoidant", event_kind="hurt", intensity=95, now=1000.0 + offset
            )
        self.assertLessEqual(state["load"], 100.0)
        recovered = settle_interaction_dynamics(
            state, requested_band="warm", event_kind="apology", intensity=80, now=1010.0
        )
        self.assertLess(recovered["load"], state["load"])
        self.assertGreater(recovered["load"], 0.0)
        self.assertIn(recovered["expression_band"], {"hurt", "avoidant"})
        self.assertEqual("recovering", recovered["recovery_band"])

    def test_positive_expression_advances_one_band_per_event(self) -> None:
        state: dict = {}
        observed = []
        for offset in range(4):
            state = settle_interaction_dynamics(
                state,
                requested_band="affectionate",
                event_kind="intimacy",
                intensity=95,
                now=1000.0 + offset,
            )
            observed.append(state["expression_band"])
        self.assertEqual(["lively", "warm", "close", "affectionate"], observed)

    def test_relationship_stage_uses_enter_and_exit_margins(self) -> None:
        self.assertEqual(
            "acquaintance",
            relationship_stage_for_score(205, previous_stage_key="acquaintance")["phase"]["key"],
        )
        self.assertEqual(
            "familiar",
            relationship_stage_for_score(220, previous_stage_key="acquaintance")["phase"]["key"],
        )
        self.assertEqual(
            "familiar",
            relationship_stage_for_score(195, previous_stage_key="familiar")["phase"]["key"],
        )
        self.assertEqual(
            "acquaintance",
            relationship_stage_for_score(179, previous_stage_key="familiar")["phase"]["key"],
        )

    def test_negative_dynamics_never_extend_classifier_hard_expiry(self) -> None:
        host = _InteractionHost()
        user = {"relationship_role": "friend", "relationship_mode": "normal"}
        host._settle_current_interaction_from_intent(
            user,
            {
                "intent": "chat",
                "confidence": 0.9,
                "emotion_event": "hurt",
                "emotion_confidence": 0.9,
                "emotion_intensity": 95,
                "emotion_target": "bot",
            },
        )

        interaction = user["current_interaction"]
        hard_expiry = interaction["expires_at"]
        self.assertEqual("avoidant", interaction["expression_band"])
        self.assertLessEqual(hard_expiry, _CLOCK[0] + 90 * 60)
        self.assertEqual(
            "relaxed",
            current_interaction_projection(interaction, now=hard_expiry)["expression_band"],
        )

    def test_apology_recovers_load_without_extending_negative_hard_expiry(self) -> None:
        host = _InteractionHost()
        user = {"relationship_role": "friend", "relationship_mode": "normal"}
        host._settle_current_interaction_from_intent(
            user,
            {
                "intent": "chat",
                "confidence": 0.9,
                "emotion_event": "hurt",
                "emotion_confidence": 0.9,
                "emotion_intensity": 95,
                "emotion_target": "bot",
            },
        )
        hard_expiry = user["current_interaction"]["expires_at"]
        prior_load = user["current_interaction"]["load"]
        _CLOCK[0] += 60

        host._settle_current_interaction_from_intent(
            user,
            {
                "intent": "chat",
                "confidence": 0.9,
                "emotion_event": "apology",
                "emotion_confidence": 0.9,
                "emotion_intensity": 90,
                "emotion_target": "bot",
            },
        )

        interaction = user["current_interaction"]
        self.assertEqual(hard_expiry, interaction["expires_at"])
        self.assertLess(interaction["load"], prior_load)
        self.assertIn(interaction["expression_band"], {"hurt", "avoidant"})

    def test_contact_boundary_bypasses_dynamics(self) -> None:
        host = _InteractionHost()
        user = {
            "relationship_role": "friend",
            "relationship_mode": "normal",
            "current_interaction": settle_interaction_dynamics(
                {}, requested_band="avoidant", event_kind="hurt", intensity=95, now=_CLOCK[0]
            ),
        }
        host._settle_current_interaction_from_intent(
            user,
            {"intent": "boundary", "confidence": 0.95, "boundary_durable": True},
        )

        interaction = user["current_interaction"]
        self.assertTrue(user["contact_preference"]["active"])
        self.assertEqual("avoidant", interaction["expression_band"])
        self.assertNotIn("dynamics_version", interaction)

    def test_public_stage_dtos_do_not_expose_hysteresis_internals(self) -> None:
        stage = relationship_stage_for_score(220, previous_stage_key="acquaintance")
        bridge = relationship_projection_for_bridge(220, previous_stage_key="acquaintance")
        for projection in (stage, bridge):
            self.assertNotIn("previous_phase_key", projection)
            self.assertNotIn("raw_stage_index", projection)
            self.assertNotIn("hysteresis_applied", projection)


if __name__ == "__main__":
    unittest.main()
