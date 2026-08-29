"""LAB-only, process-local fixture gate for acceptance scenarios.

The module deliberately has no Test Lab SDK dependency.  Registration is
attempted only when the isolated Lab injects ``astrbot_test_lab_fixture``;
production installs otherwise keep a ``None`` adapter and execute no fixture
logic.
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import importlib
import json
import math
import re
from threading import RLock
from typing import Any

from .plugin_identity import PLUGIN_ID
from .relationship_policy import relationship_stage_for_score


SCHEMA = "companion.acceptance_state.v1"
MAX_ACTIVE_RUNS = 32
MAX_PAYLOAD_BYTES = 8 * 1024
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_LAB_WORD = re.compile(r"^LAB[_:-][A-Za-z0-9_:\-\u4e00-\u9fff]{1,55}$", re.IGNORECASE)
_STAGE_KEYS = frozenset(
    {
        "deeply_distant",
        "strongly_distant",
        "distant",
        "acquaintance",
        "familiar",
        "close",
        "intimate",
        "deeply_bonded",
    }
)
_INTERACTION_BANDS = frozenset(
    {"avoidant", "hurt", "relaxed", "lively", "warm", "close", "affectionate"}
)
_ALLOWED_PAYLOAD_KEYS = frozenset(
    {
        "relationship_score",
        "relationship_role",
        "relationship_mode",
        "positive_stage_cap_key",
        "previous_stage_key",
        "interaction_band",
        "group_interest_words",
        "group_interest_min_probability",
    }
)
_SERIALIZABLE_CAPABILITY_TYPES = (
    str,
    bytes,
    bytearray,
    bool,
    int,
    float,
    list,
    tuple,
    dict,
    set,
    frozenset,
)


def _text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split())[:limit]


def _digest(value: Any) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8", errors="ignore")).hexdigest()[:16]


def _require_process_capability(capability: object) -> None:
    # The Lab registry performs the identity check.  This local guard ensures
    # a directly invoked adapter still rejects JSON/OneBot-shaped substitutes.
    if capability is None or isinstance(capability, _SERIALIZABLE_CAPABILITY_TYPES):
        raise PermissionError("process-local fixture capability required")


def _event_umo(event: Any) -> str:
    return _text(getattr(event, "unified_msg_origin", ""), 240) if event is not None else ""


def _event_actor_id(event: Any) -> str:
    if event is None:
        return ""
    getter = getattr(event, "get_sender_id", None)
    if callable(getter):
        try:
            actor_id = _text(getter(), 160)
            if actor_id:
                return actor_id
        except Exception:
            pass
    for name in ("sender_id", "user_id"):
        actor_id = _text(getattr(event, name, ""), 160)
        if actor_id:
            return actor_id
    message = getattr(event, "message_obj", None)
    sender = getattr(message, "sender", None) if message is not None else None
    return _text(getattr(sender, "user_id", ""), 160) if sender is not None else ""


def _normalized_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    unknown = set(payload) - _ALLOWED_PAYLOAD_KEYS
    if unknown:
        raise ValueError("unsupported Companion fixture field")
    try:
        encoded = json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":")).encode()
    except (TypeError, ValueError) as exc:
        raise ValueError("fixture payload must be JSON-compatible") from exc
    if len(encoded) > MAX_PAYLOAD_BYTES:
        raise ValueError("fixture payload exceeds 8 KiB")

    score = payload.get("relationship_score")
    if type(score) is not int or not -1200 <= score <= 1200:
        raise ValueError("relationship_score must be an integer in -1200..1200")
    raw_role = payload.get("relationship_role", "friend")
    if not isinstance(raw_role, str):
        raise ValueError("relationship_role must be a string")
    role = _text(raw_role, 16).lower()
    if role not in {"friend", "owner"}:
        raise ValueError("relationship_role is invalid")
    raw_mode = payload.get("relationship_mode", "normal")
    if not isinstance(raw_mode, str):
        raise ValueError("relationship_mode must be a string")
    mode = _text(raw_mode, 24).lower()
    if mode not in {"normal", "owner_exclusive"}:
        raise ValueError("relationship_mode is invalid")
    if mode == "owner_exclusive" and role != "owner":
        raise ValueError("owner_exclusive requires the owner role")
    raw_positive_cap = payload.get("positive_stage_cap_key", "deeply_bonded")
    if not isinstance(raw_positive_cap, str):
        raise ValueError("positive_stage_cap_key must be a string")
    positive_cap = _text(raw_positive_cap, 32).lower()
    if positive_cap not in _STAGE_KEYS:
        raise ValueError("positive_stage_cap_key is invalid")
    raw_previous_stage = payload.get("previous_stage_key", "")
    if not isinstance(raw_previous_stage, str):
        raise ValueError("previous_stage_key must be a string")
    previous_stage = _text(raw_previous_stage, 32).lower()
    if previous_stage and previous_stage not in _STAGE_KEYS:
        raise ValueError("previous_stage_key is invalid")
    raw_interaction_band = payload.get("interaction_band", "relaxed")
    if not isinstance(raw_interaction_band, str):
        raise ValueError("interaction_band must be a string")
    interaction_band = _text(raw_interaction_band, 24).lower()
    if interaction_band not in _INTERACTION_BANDS:
        raise ValueError("interaction_band is invalid")

    raw_words = payload.get("group_interest_words", [])
    if not isinstance(raw_words, list) or len(raw_words) > 8:
        raise ValueError("group_interest_words must be an array with at most 8 items")
    words: list[str] = []
    for raw_word in raw_words:
        if not isinstance(raw_word, str):
            raise ValueError("group interest words must be strings")
        word = _text(raw_word, 60)
        if not _LAB_WORD.fullmatch(word):
            raise ValueError("group interest words must use the LAB_ synthetic namespace")
        if word.casefold() not in {item.casefold() for item in words}:
            words.append(word)

    minimum_probability = payload.get("group_interest_min_probability", 0.0)
    if type(minimum_probability) not in {int, float}:
        raise ValueError("group_interest_min_probability is invalid")
    try:
        minimum_probability = float(minimum_probability)
    except (TypeError, ValueError) as exc:
        raise ValueError("group_interest_min_probability is invalid") from exc
    if not math.isfinite(minimum_probability) or not 0.0 <= minimum_probability <= 1.0:
        raise ValueError("group_interest_min_probability must be in 0..1")
    if minimum_probability > 0 and not words:
        raise ValueError("a group interest probability requires at least one synthetic word")

    return {
        "relationship_score": score,
        "relationship_role": role,
        "relationship_mode": mode,
        "positive_stage_cap_key": positive_cap,
        "previous_stage_key": previous_stage,
        "interaction_band": interaction_band,
        "group_interest_words": tuple(words),
        "group_interest_min_probability": minimum_probability,
    }


class CompanionLabFixtureAdapter:
    """Bounded run overlays consumed only from Companion's normal read paths."""

    fixture_schemas = (SCHEMA,)
    fixture_capabilities = ("final_projection", "residual_projection")

    def __init__(self) -> None:
        self._overlays: dict[str, dict[str, Any]] = {}
        self._closed = False
        self._lock = RLock()

    def prepare_fixture(
        self,
        run_id: str,
        schema: str,
        scope: Mapping[str, Any],
        payload: Mapping[str, Any],
        capability: object,
    ) -> None:
        _require_process_capability(capability)
        if not isinstance(run_id, str) or not isinstance(schema, str):
            raise ValueError("run_id and schema must be strings")
        clean_run_id = _text(run_id, 128)
        if not _IDENTIFIER.fullmatch(clean_run_id):
            raise ValueError("run_id is invalid")
        if schema != SCHEMA:
            raise ValueError("unsupported Companion fixture schema")
        if not isinstance(scope, Mapping) or not isinstance(payload, Mapping):
            raise ValueError("fixture scope and payload must be objects")
        raw_umo = scope.get("effective_umo")
        raw_actor_id = scope.get("effective_actor_id")
        if not isinstance(raw_umo, str) or not isinstance(raw_actor_id, str):
            raise ValueError("effective_umo and effective_actor_id must be strings")
        effective_umo = _text(raw_umo, 240)
        effective_actor_id = _text(raw_actor_id, 160)
        if not effective_umo or not effective_actor_id:
            raise ValueError("effective_umo and effective_actor_id are required")
        normalized = _normalized_payload(payload)

        with self._lock:
            if self._closed:
                raise RuntimeError("Companion fixture adapter is closed")
            if clean_run_id in self._overlays:
                raise ValueError("one Companion fixture is allowed per run")
            if len(self._overlays) >= MAX_ACTIVE_RUNS:
                raise RuntimeError("too many active Companion fixtures")
            if any(
                item["effective_umo"] == effective_umo
                and item["effective_actor_id"] == effective_actor_id
                for item in self._overlays.values()
            ):
                raise RuntimeError("Companion fixture scope is already active")

            overlay = {
                "schema": SCHEMA,
                "effective_umo": effective_umo,
                "effective_actor_id": effective_actor_id,
                "payload": normalized,
                "relationship_view_count": 0,
                "group_setting_read_count": 0,
            }
            self._overlays = {**self._overlays, clean_run_id: overlay}

    def describe_applied_fixture(self, run_id: str) -> Mapping[str, Any]:
        clean_run_id = _text(run_id, 128)
        with self._lock:
            overlay = self._overlays.get(clean_run_id)
            overlay = dict(overlay) if overlay is not None else None
        if overlay is None:
            raise KeyError("Companion fixture is not active")
        payload = overlay["payload"]
        phase = relationship_stage_for_score(
            payload["relationship_score"],
            previous_stage_key=payload["previous_stage_key"],
        )["phase"]
        return {
            "active": True,
            "schema": SCHEMA,
            "run_digest": _digest(clean_run_id),
            "scope_digest": _digest(overlay["effective_umo"]),
            "actor_digest": _digest(overlay["effective_actor_id"]),
            "relationship": {
                "score": payload["relationship_score"],
                "role": payload["relationship_role"],
                "mode": payload["relationship_mode"],
                "stage_key": _text(phase.get("key"), 32),
                "interaction_band": payload["interaction_band"],
            },
            "group_wakeup": {
                "interest_word_count": len(payload["group_interest_words"]),
                "minimum_probability": payload["group_interest_min_probability"],
            },
            "observations": {
                "relationship_view_count": int(overlay["relationship_view_count"]),
                "group_setting_read_count": int(overlay["group_setting_read_count"]),
            },
        }

    def release_fixture(self, run_id: str) -> None:
        clean_run_id = _text(run_id, 128)
        with self._lock:
            if clean_run_id not in self._overlays:
                return
            self._overlays = {
                key: value for key, value in self._overlays.items() if key != clean_run_id
            }

    def describe_released_fixture(self, run_id: str) -> Mapping[str, Any]:
        clean_run_id = _text(run_id, 128)
        with self._lock:
            active = clean_run_id in self._overlays
        return {
            "active": active,
            "residual_count": int(active),
            "residual_status": "present" if active else "clear",
        }

    def close(self) -> None:
        with self._lock:
            self._overlays = {}
            self._closed = True

    def overlay_relationship_view(self, event: Any, user: Any) -> Any:
        if not isinstance(user, Mapping):
            return user
        matched = self._matching_overlay(event)
        if matched is None:
            return user
        run_id, overlay = matched
        payload = overlay["payload"]
        view = dict(user)
        view.update(
            {
                "relationship_score": payload["relationship_score"],
                "relationship_role": payload["relationship_role"],
                "relationship_mode": payload["relationship_mode"],
                "relationship_positive_stage_cap_key": payload["positive_stage_cap_key"],
                "relationship_phase_key": payload["previous_stage_key"],
                "current_interaction": {
                    "expression_band": payload["interaction_band"],
                    "source": "lab_fixture",
                    "relationship_score": payload["relationship_score"],
                },
            }
        )
        if not self._increment(run_id, "relationship_view_count"):
            return user
        return view

    def group_wakeup_settings(self, event: Any) -> Mapping[str, Any] | None:
        matched = self._matching_overlay(event)
        if matched is None:
            return None
        run_id, overlay = matched
        payload = overlay["payload"]
        if not self._increment(run_id, "group_setting_read_count"):
            return None
        return {
            "interest_words": payload["group_interest_words"],
            "minimum_probability": payload["group_interest_min_probability"],
        }

    def _matching_overlay(self, event: Any) -> tuple[str, dict[str, Any]] | None:
        umo = _event_umo(event)
        actor_id = _event_actor_id(event)
        if not umo or not actor_id:
            return None
        with self._lock:
            if self._closed:
                return None
            for run_id, overlay in self._overlays.items():
                if (
                    overlay["effective_umo"] == umo
                    and overlay["effective_actor_id"] == actor_id
                ):
                    return run_id, dict(overlay)
        return None

    def _increment(self, run_id: str, key: str) -> bool:
        with self._lock:
            current = self._overlays.get(run_id)
            if current is None:
                return False
            updated = dict(current)
            updated[key] = int(updated.get(key) or 0) + 1
            self._overlays = {**self._overlays, run_id: updated}
            return True


def register_companion_lab_fixture_adapter() -> CompanionLabFixtureAdapter | None:
    """Register only when the isolated Lab's process-local gate is importable."""

    module_name = "astrbot_test_lab_fixture"
    try:
        fixture_module = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name == module_name:
            return None
        raise
    establish = getattr(fixture_module, "establish_fixture_capability", None)
    capability_is_valid = getattr(fixture_module, "fixture_capability_is_valid", None)
    register = getattr(fixture_module, "register_fixture_adapter", None)
    if not all(callable(item) for item in (establish, capability_is_valid, register)):
        raise RuntimeError("Test Lab fixture registration is unavailable")
    capability = establish()
    if not capability_is_valid(capability):
        raise PermissionError("invalid Test Lab fixture capability")
    _require_process_capability(capability)
    adapter = CompanionLabFixtureAdapter()
    try:
        register(PLUGIN_ID, adapter, capability)
    except Exception:
        adapter.close()
        raise
    return adapter


__all__ = [
    "CompanionLabFixtureAdapter",
    "SCHEMA",
    "register_companion_lab_fixture_adapter",
]
