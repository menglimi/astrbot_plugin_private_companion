from __future__ import annotations

import ast
import asyncio
import copy
from pathlib import Path
import sys
import time
from typing import Any
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from companion_interaction_expression import allowed_expression_bands, current_interaction_projection  # noqa: E402
from interaction_dynamics import settle_interaction_dynamics  # noqa: E402
from persona_config import runtime_persona_setting  # noqa: E402
from relationship_ledger import (  # noqa: E402
    is_owner_exclusive,
    normalize_relationship_mode,
    record_manual_relationship_change,
    relationship_positive_score_cap,
)


class _Request:
    payload: dict[str, Any] = {}

    async def get_json(self, silent: bool = True) -> dict[str, Any]:
        del silent
        return copy.deepcopy(self.payload)


class _Logger:
    def error(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    info = error
    debug = error
    warning = error


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


def _safe_float(value: Any, default: float = 0.0, minimum: float = 0.0, maximum: float | None = None) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        result = default
    result = max(minimum, result)
    if maximum is not None:
        result = min(maximum, result)
    return result


def _single_line(value: Any, limit: int = 80) -> str:
    return " ".join(str(value or "").split())[:limit]


def _load_update_user() -> Any:
    path = ROOT / "page_api_users_groups.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    owner = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "PrivateCompanionPageApiUsersGroupsMixin")
    method = next(node for node in owner.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "update_user")
    namespace: dict[str, Any] = {
        "Any": Any,
        "allowed_expression_bands": allowed_expression_bands,
        "current_interaction_projection": current_interaction_projection,
        "deepcopy": copy.deepcopy,
        "logger": _Logger(),
        "is_owner_exclusive": is_owner_exclusive,
        "normalize_relationship_mode": normalize_relationship_mode,
        "record_manual_relationship_change": record_manual_relationship_change,
        "relationship_positive_score_cap": relationship_positive_score_cap,
        "request": _Request(),
        "time": time,
        "_safe_int": _safe_int,
    }
    module = ast.Module(body=[copy.deepcopy(method)], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(path), "exec"), namespace)
    return namespace["update_user"], namespace["request"]


UPDATE_USER, REQUEST = _load_update_user()


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
        "asyncio": asyncio,
        "current_interaction_projection": current_interaction_projection,
        "deepcopy": copy.deepcopy,
        "logger": _Logger(),
        "settle_interaction_dynamics": settle_interaction_dynamics,
        "_now_ts": time.time,
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


class _AsyncLock:
    async def __aenter__(self) -> "_AsyncLock":
        return self

    async def __aexit__(self, _exc_type: Any, _exc: Any, _tb: Any) -> None:
        return None


class _Plugin:
    def __init__(self, user: dict[str, Any]) -> None:
        self.data = {"users": {"10001": user}}
        self._data_lock = _AsyncLock()
        self.saved = 0

    def _get_user(self, user_id: str) -> dict[str, Any]:
        return self.data["users"][user_id]

    @staticmethod
    def _private_user_role(user: dict[str, Any], _user_id: str) -> str:
        return str(user.get("relationship_role") or "friend")

    @staticmethod
    def _normalize_private_user_role(value: Any) -> str:
        role = str(value or "").strip().lower()
        return role if role in {"owner", "friend"} else ""

    @staticmethod
    def _normalize_owner_exclusive_relationship_prompt(value: Any) -> str:
        return str(value or "").strip()[:2400]

    def _set_owner_exclusive_relationship_prompt(
        self,
        user: dict[str, Any],
        *,
        stable_user_id: str,
        text: Any,
    ) -> dict[str, Any]:
        if str(user.get("user_id") or "") != stable_user_id:
            return {"ok": False, "message": "稳定用户身份不匹配"}
        normalized = self._normalize_owner_exclusive_relationship_prompt(text)
        if normalized:
            user["persona_relationship_prompts"] = {
                "persona-main": {
                    "persona_id": "persona-main",
                    "stable_user_id": stable_user_id,
                    "relationship_mode": "owner_exclusive",
                    "text": normalized,
                }
            }
        else:
            user.pop("persona_relationship_prompts", None)
        return {"ok": True}

    def _save_data_sync(self, **_kwargs) -> None:
        self.saved += 1


class _Host:
    def __init__(self, user: dict[str, Any]) -> None:
        self.plugin = _Plugin(user)

    update_user = UPDATE_USER

    @staticmethod
    def _single_line(value: Any, limit: int) -> str:
        return " ".join(str(value or "").split())[:limit]

    @staticmethod
    def _relationship_score_input(value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or not -1200 <= value <= 1200:
            raise ValueError("invalid relationship score")
        return value

    @staticmethod
    def _observation_profile_by_user_id_locked(_data: dict[str, Any], _user_id: str) -> None:
        return None

    @staticmethod
    def _user_summary(user_id: str, user: dict[str, Any]) -> dict[str, Any]:
        return {"user_id": user_id, **copy.deepcopy(user)}

    @staticmethod
    def _expression_profile_summary(_user: dict[str, Any]) -> dict[str, Any]:
        return {}

    @staticmethod
    def _ok(value: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True, "data": value}

    @staticmethod
    def _error(message: str) -> dict[str, Any]:
        return {"ok": False, "error": message}


class Req028UserUpdateContractTests(unittest.TestCase):
    def _run(self, user: dict[str, Any], payload: dict[str, Any]) -> tuple[dict[str, Any], _Host]:
        host = _Host(copy.deepcopy(user))
        REQUEST.payload = {"user_id": "10001", **payload}
        return asyncio.run(host.update_user()), host

    def test_owner_exclusive_score_is_frozen_until_normal_stage_is_explicit(self) -> None:
        base = {"relationship_role": "owner", "relationship_mode": "owner_exclusive", "relationship_score": 600}
        rejected, rejected_host = self._run(base, {"companion_intimacy": 500})
        self.assertFalse(rejected["ok"])
        self.assertEqual(base, rejected_host.plugin.data["users"]["10001"])

        accepted, accepted_host = self._run(base, {"relationship_mode": "normal", "companion_intimacy": 500})
        self.assertTrue(accepted["ok"])
        user = accepted_host.plugin.data["users"]["10001"]
        self.assertEqual("normal", user["relationship_mode"])
        self.assertEqual(500, user["relationship_score"])
        self.assertEqual("administrator", user["relationship_ledger"][-1]["source"])
        self.assertEqual(500, user["relationship_ledger"][-1]["score_after"])

    def test_owner_only_interaction_bands_are_rejected_server_side(self) -> None:
        friend = {"relationship_role": "friend", "relationship_mode": "normal", "relationship_score": 0}
        rejected, _ = self._run(friend, {"current_interaction_band": "affectionate"})
        self.assertFalse(rejected["ok"])

        owner = {"relationship_role": "owner", "relationship_mode": "owner_exclusive", "relationship_score": 600}
        accepted, host = self._run(owner, {"current_interaction_band": "affectionate"})
        self.assertTrue(accepted["ok"])
        interaction = host.plugin.data["users"]["10001"]["current_interaction"]
        self.assertEqual("affectionate", interaction["expression_band"])
        self.assertTrue(interaction["manual_override"])
        self.assertEqual("page_administrator", interaction["operator"])

    def test_owner_exclusive_relationship_prompt_can_be_saved_and_cleared(self) -> None:
        owner = {
            "user_id": "10001",
            "relationship_role": "owner",
            "relationship_mode": "owner_exclusive",
            "relationship_score": 600,
        }
        accepted, host = self._run(
            owner,
            {"owner_exclusive_relationship_prompt": "一起长大的青梅竹马，平时可以自然互损。"},
        )
        self.assertTrue(accepted["ok"])
        entry = host.plugin.data["users"]["10001"]["persona_relationship_prompts"]["persona-main"]
        self.assertEqual("10001", entry["stable_user_id"])
        self.assertEqual("owner_exclusive", entry["relationship_mode"])

        cleared, cleared_host = self._run(
            host.plugin.data["users"]["10001"],
            {"owner_exclusive_relationship_prompt": ""},
        )
        self.assertTrue(cleared["ok"])
        self.assertNotIn("persona_relationship_prompts", cleared_host.plugin.data["users"]["10001"])

    def test_owner_exclusive_relationship_prompt_rejects_non_owner_and_wrong_identity(self) -> None:
        friend = {
            "user_id": "10001",
            "relationship_role": "friend",
            "relationship_mode": "normal",
            "relationship_score": 0,
        }
        rejected, _ = self._run(
            friend,
            {"owner_exclusive_relationship_prompt": "不应保存"},
        )
        self.assertFalse(rejected["ok"])

        wrong_identity = {
            "user_id": "another-user",
            "relationship_role": "owner",
            "relationship_mode": "owner_exclusive",
            "relationship_score": 600,
        }
        rejected, _ = self._run(
            wrong_identity,
            {"owner_exclusive_relationship_prompt": "也不应保存"},
        )
        self.assertFalse(rejected["ok"])

    def test_manual_interaction_correction_clears_stale_automatic_contact_boundary(self) -> None:
        contact_values = (
            {
                "mode": "no_contact",
                "active": True,
                "no_contact": True,
                "source": "automatic",
                "reason_code": "explicit_user_boundary",
            },
            "no_contact",
        )
        for contact_preference in contact_values:
            with self.subTest(contact_preference=contact_preference):
                user = {
                    "relationship_role": "owner",
                    "relationship_mode": "owner_exclusive",
                    "relationship_score": 600,
                    "contact_preference": contact_preference,
                }

                accepted, host = self._run(user, {"current_interaction_band": "warm"})

                self.assertTrue(accepted["ok"])
                saved = host.plugin.data["users"]["10001"]
                self.assertEqual("warm", saved["current_interaction"]["expression_band"])
                self.assertTrue(saved["current_interaction"]["manual_override"])
                self.assertFalse(saved["contact_preference"]["active"])
                self.assertEqual(
                    "administrator_manual_interaction_correction",
                    saved["contact_preference"]["reason_code"],
                )

    def test_owner_downgrade_requires_explicit_normal_relationship_selection(self) -> None:
        owner = {"relationship_role": "owner", "relationship_mode": "owner_exclusive", "relationship_score": 600}
        rejected, _ = self._run(owner, {"relationship_role": "friend"})
        self.assertFalse(rejected["ok"])

        accepted, host = self._run(
            owner,
            {"relationship_role": "friend", "relationship_mode": "normal", "companion_intimacy": 100},
        )
        self.assertTrue(accepted["ok"])
        user = host.plugin.data["users"]["10001"]
        self.assertEqual("friend", user["relationship_role"])
        self.assertEqual("normal", user["relationship_mode"])
        self.assertEqual("administrator", user["relationship_ledger"][-1]["source"])

    def test_automatic_settlement_writes_only_current_interaction_and_contact_boundary(self) -> None:
        host = _Host(
            {
                "relationship_role": "friend",
                "relationship_mode": "normal",
                "relationship_state": {"mode": "backoff", "mood_score": -99},
            }
        )
        host._settle_current_interaction_from_intent = SETTLE_INTERACTION.__get__(host, _Host)
        host._settle_current_interaction_from_intent(
            host.plugin.data["users"]["10001"],
            {"intent": "boundary", "confidence": 0.95, "boundary_durable": True},
        )
        user = host.plugin.data["users"]["10001"]
        self.assertEqual("avoidant", user["current_interaction"]["expression_band"])
        self.assertTrue(user["contact_preference"]["active"])
        self.assertEqual({"mode": "backoff", "mood_score": -99}, user["relationship_state"])

    def test_explicit_reengagement_clears_boundary_and_recovers_gradually(self) -> None:
        user = {
            "relationship_role": "owner",
            "relationship_mode": "owner_exclusive",
            "contact_preference": {"mode": "no_contact", "active": True, "no_contact": True},
        }
        host = _Host(user)
        host._settle_current_interaction_from_intent = SETTLE_INTERACTION.__get__(host, _Host)
        host._settle_current_interaction_from_intent(
            host.plugin.data["users"]["10001"],
            {"intent": "intimacy", "confidence": 0.9, "emotion_event": "neutral"},
        )
        settled = host.plugin.data["users"]["10001"]
        self.assertFalse(settled["contact_preference"]["active"])
        self.assertEqual("lively", settled["current_interaction"]["expression_band"])

    def test_manual_interaction_override_is_not_replaced_by_automatic_event(self) -> None:
        user = {
            "relationship_role": "owner",
            "relationship_mode": "owner_exclusive",
            "current_interaction": {
                "expression_band": "affectionate",
                "source": "manual",
                "manual_override": True,
                "updated_at": time.time(),
                "expires_at": time.time() + 3600,
            },
        }
        host = _Host(user)
        host._settle_current_interaction_from_intent = SETTLE_INTERACTION.__get__(host, _Host)
        host._settle_current_interaction_from_intent(
            host.plugin.data["users"]["10001"],
            {"intent": "chat", "confidence": 0.9, "emotion_event": "hurt", "emotion_confidence": 0.9, "emotion_intensity": 95, "emotion_target": "bot"},
        )
        self.assertEqual("affectionate", host.plugin.data["users"]["10001"]["current_interaction"]["expression_band"])

    def test_manual_interaction_override_clears_stale_boundary_during_settlement(self) -> None:
        user = {
            "relationship_role": "owner",
            "relationship_mode": "owner_exclusive",
            "contact_preference": {
                "mode": "no_contact",
                "active": True,
                "no_contact": True,
                "source": "automatic",
            },
            "current_interaction": {
                "expression_band": "warm",
                "source": "manual",
                "manual_override": True,
                "updated_at": time.time(),
                "expires_at": 0,
            },
        }
        host = _Host(user)
        host._settle_current_interaction_from_intent = SETTLE_INTERACTION.__get__(host, _Host)

        host._settle_current_interaction_from_intent(
            host.plugin.data["users"]["10001"],
            {"intent": "chat", "confidence": 0.9, "emotion_event": "neutral"},
        )

        settled = host.plugin.data["users"]["10001"]
        self.assertEqual("warm", settled["current_interaction"]["expression_band"])
        self.assertFalse(settled["contact_preference"]["active"])
        self.assertEqual(
            "manual_interaction_override_retained",
            settled["contact_preference"]["reason_code"],
        )

    def test_new_explicit_boundary_can_replace_manual_interaction_override(self) -> None:
        user = {
            "relationship_role": "owner",
            "relationship_mode": "owner_exclusive",
            "current_interaction": {
                "expression_band": "warm",
                "source": "manual",
                "manual_override": True,
                "updated_at": time.time(),
                "expires_at": 0,
            },
        }
        host = _Host(user)
        host._settle_current_interaction_from_intent = SETTLE_INTERACTION.__get__(host, _Host)

        host._settle_current_interaction_from_intent(
            host.plugin.data["users"]["10001"],
            {
                "intent": "boundary",
                "confidence": 0.95,
                "boundary_durable": True,
                "emotion_event": "neutral",
            },
        )

        settled = host.plugin.data["users"]["10001"]
        self.assertEqual("avoidant", settled["current_interaction"]["expression_band"])
        self.assertTrue(settled["contact_preference"]["active"])

    def test_hurt_thresholds_and_recovery_window_drive_the_unified_interaction(self) -> None:
        host = _Host({"relationship_role": "friend", "relationship_mode": "normal"})
        host._settle_current_interaction_from_intent = SETTLE_INTERACTION.__get__(host, _Host)
        host.emotional_gate_hurt_threshold = 70
        host.emotional_gate_refuse_threshold = 90
        host.emotional_gate_recovery_per_hour = 30
        host.emotional_gate_max_hurt_minutes = 120

        host._settle_current_interaction_from_intent(
            host.plugin.data["users"]["10001"],
            {"intent": "chat", "confidence": 0.9, "emotion_event": "hurt", "emotion_confidence": 0.9, "emotion_intensity": 65, "emotion_target": "bot"},
        )
        self.assertEqual("relaxed", host.plugin.data["users"]["10001"]["current_interaction"]["expression_band"])

        before = time.time()
        host._settle_current_interaction_from_intent(
            host.plugin.data["users"]["10001"],
            {"intent": "chat", "confidence": 0.9, "emotion_event": "hurt", "emotion_confidence": 0.9, "emotion_intensity": 95, "emotion_target": "bot"},
        )
        interaction = host.plugin.data["users"]["10001"]["current_interaction"]
        self.assertEqual("avoidant", interaction["expression_band"])
        self.assertEqual("severe_hurt_event", interaction["reason"])
        self.assertLessEqual(interaction["expires_at"] - before, 120 * 60 + 2)


if __name__ == "__main__":
    unittest.main()
