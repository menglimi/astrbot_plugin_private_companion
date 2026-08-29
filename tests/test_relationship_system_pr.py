from __future__ import annotations

import ast
import asyncio
import copy
import json
from pathlib import Path
import time
from typing import Any

from companion_interaction_expression import (
    OWNER_EXPRESSION_BANDS,
    allowed_expression_bands,
    build_expression_decision,
    current_interaction_projection,
    content_intent_from_text,
    expression_decision_prompt,
    normalize_normal_interaction_band_cap,
)
from interaction_dynamics import settle_interaction_dynamics
from relationship_ledger import (
    apply_natural_relationship_decay,
    apply_relationship_event,
    clamp_relationship_positive_stage_cap,
    is_owner_exclusive,
    migrate_legacy_relationship_score,
    migrate_relationship_positive_stage_cap,
    normalize_relationship_mode,
    normalize_relationship_positive_stage_cap_key,
    record_manual_relationship_change,
    relationship_positive_score_cap,
)
from relationship_policy import relationship_stage_for_score
from persona_config import runtime_persona_setting
from astrbot_plugin_private_companion.story_handoff import (
    StoryAuthorityError,
    story_authority_controller,
)
from astrbot_plugin_private_companion.runtime_config_dispatcher import TTS_RUNTIME_KEYS


ROOT = Path(__file__).resolve().parents[1]


class _Logger:
    def _noop(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    debug = info = warning = error = _noop


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


def _class_method(filename: str, class_name: str, method_name: str, namespace: dict[str, Any]) -> Any:
    path = ROOT / filename
    tree = ast.parse(path.read_text(encoding="utf-8"))
    owner = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    method = next(
        node
        for node in owner.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == method_name
    )
    module = ast.Module(body=[copy.deepcopy(method)], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(path), "exec"), namespace)
    return namespace[method_name]


NOW = 1_700_000_000.0
SETTLE_INTERACTION = _class_method(
    "user_memory.py",
    "UserMemoryMixin",
    "_settle_current_interaction_from_intent",
    {
        "Any": Any,
        "current_interaction_projection": current_interaction_projection,
        "logger": _Logger(),
        "settle_interaction_dynamics": settle_interaction_dynamics,
        "_now_ts": lambda: NOW,
        "_safe_float": _safe_float,
        "_safe_int": _safe_int,
        "_single_line": _single_line,
        "runtime_persona_setting": runtime_persona_setting,
    },
)
BUILD_EXPRESSION_FOR_USER = _class_method(
    "user_memory.py",
    "UserMemoryMixin",
    "_build_expression_decision_for_user",
    {
        "Any": Any,
        "build_expression_decision": build_expression_decision,
        "current_interaction_projection": current_interaction_projection,
        "relationship_stage_for_score": relationship_stage_for_score,
        "_now_ts": lambda: NOW,
        "_safe_float": _safe_float,
        "_safe_int": _safe_int,
        "_single_line": _single_line,
        "runtime_persona_setting": runtime_persona_setting,
    },
)
APPLY_RELATIONSHIP_EVENT = _class_method(
    "core_store.py",
    "CoreStoreMixin",
    "_apply_relationship_event",
    {
        "Any": Any,
        "apply_relationship_event": apply_relationship_event,
        "logger": _Logger(),
        "migrate_legacy_relationship_score": migrate_legacy_relationship_score,
        "_single_line": _single_line,
    },
)


class _InteractionHost:
    enable_emotion_simulation = True
    enable_relationship_state_machine = True
    normal_interaction_band_cap = "warm"
    emotional_gate_hurt_threshold = 70
    emotional_gate_refuse_threshold = 90
    emotional_gate_recovery_per_hour = 30
    emotional_gate_max_hurt_minutes = 120

    _settle_current_interaction_from_intent = SETTLE_INTERACTION

    @staticmethod
    def _private_user_role(user: dict[str, Any], _user_id: str) -> str:
        return str(user.get("relationship_role") or "friend")


class _ExpressionHost:
    enable_custom_relationship_stage_policy = True
    normal_interaction_band_cap = "warm"
    owner_exclusive_tone = "温暖、亲近、稳定"
    owner_exclusive_address_style = "优先使用已确认的专属称呼"
    owner_exclusive_proactive_limit = 6

    _build_expression_decision_for_user = BUILD_EXPRESSION_FOR_USER

    @staticmethod
    def _private_user_role(user: dict[str, Any], _user_id: str = "") -> str:
        return str(user.get("relationship_role") or "friend")


class _Request:
    payload: dict[str, Any] = {}

    async def get_json(self, silent: bool = True) -> dict[str, Any]:
        del silent
        return copy.deepcopy(self.payload)


REQUEST = _Request()
UPDATE_USER = _class_method(
    "page_api_users_groups.py",
    "PrivateCompanionPageApiUsersGroupsMixin",
    "update_user",
    {
        "Any": Any,
        "allowed_expression_bands": allowed_expression_bands,
        "current_interaction_projection": current_interaction_projection,
        "deepcopy": copy.deepcopy,
        "is_owner_exclusive": is_owner_exclusive,
        "logger": _Logger(),
        "normalize_relationship_mode": normalize_relationship_mode,
        "record_manual_relationship_change": record_manual_relationship_change,
        "relationship_positive_score_cap": relationship_positive_score_cap,
        "request": REQUEST,
        "time": time,
        "_safe_int": _safe_int,
    },
)
APPLY_CONFIG_VALUE = _class_method(
    "page_api.py",
    "PrivateCompanionPageApi",
    "_apply_config_value",
    {
        "Any": Any,
        "TTS_RUNTIME_KEYS": TTS_RUNTIME_KEYS,
        "current_interaction_projection": current_interaction_projection,
        "deepcopy": copy.deepcopy,
        "migrate_relationship_positive_stage_cap": migrate_relationship_positive_stage_cap,
        "normalize_normal_interaction_band_cap": normalize_normal_interaction_band_cap,
        "normalize_relationship_positive_stage_cap_key": normalize_relationship_positive_stage_cap_key,
        "time": time,
    },
)
RELATIONSHIP_PROFILE_TARGETS = _class_method(
    "page_api.py",
    "PrivateCompanionPageApi",
    "_relationship_profile_targets",
    {"Any": Any},
)
SAVE_RELATIONSHIP_PROFILE_TARGET = _class_method(
    "page_api.py",
    "PrivateCompanionPageApi",
    "_save_relationship_profile_target",
    {"Any": Any, "deepcopy": copy.deepcopy},
)
APPLY_RELATIONSHIP_PROFILE_BATCH = _class_method(
    "page_api.py",
    "PrivateCompanionPageApi",
    "_apply_relationship_profile_config_batch",
    {
        "Any": Any,
        "current_interaction_projection": current_interaction_projection,
        "deepcopy": copy.deepcopy,
        "logger": _Logger(),
        "migrate_legacy_relationship_score": migrate_legacy_relationship_score,
        "migrate_relationship_positive_stage_cap": migrate_relationship_positive_stage_cap,
        "normalize_normal_interaction_band_cap": normalize_normal_interaction_band_cap,
        "time": time,
    },
)
RESTORE_RELATIONSHIP_CONFIG_VALUES = _class_method(
    "page_api.py",
    "PrivateCompanionPageApi",
    "_restore_relationship_config_values",
    {
        "Any": Any,
        "normalize_normal_interaction_band_cap": normalize_normal_interaction_band_cap,
        "normalize_relationship_positive_stage_cap_key": normalize_relationship_positive_stage_cap_key,
    },
)
ROLLBACK_RELATIONSHIP_CONFIG_TRANSACTION = _class_method(
    "page_api.py",
    "PrivateCompanionPageApi",
    "_rollback_relationship_config_transaction",
    {
        "Any": Any,
        "deepcopy": copy.deepcopy,
        "logger": _Logger(),
    },
)
UPDATE_SETTINGS = _class_method(
    "page_api.py",
    "PrivateCompanionPageApi",
    "update_settings",
    {
        "Any": Any,
        "CatalogValidationError": ValueError,
        "StoryAuthorityError": StoryAuthorityError,
        "asyncio": asyncio,
        "logger": _Logger(),
        "request": REQUEST,
        "story_authority_controller": story_authority_controller,
    },
)
REQ041_CONFIG_RUNTIME_SNAPSHOT = _class_method(
    "page_api.py",
    "PrivateCompanionPageApi",
    "_req041_config_runtime_snapshot",
    {"Any": Any, "deepcopy": copy.deepcopy},
)
ROLLBACK_REQ041_CONFIG_RUNTIME = _class_method(
    "page_api.py",
    "PrivateCompanionPageApi",
    "_rollback_req041_config_runtime",
    {"Any": Any, "deepcopy": copy.deepcopy, "logger": _Logger()},
)
ENSURE_RELATIONSHIP_STATE = _class_method(
    "core_store.py",
    "CoreStoreMixin",
    "_ensure_relationship_user_state",
    {
        "Any": Any,
        "apply_natural_relationship_decay": apply_natural_relationship_decay,
        "clamp_relationship_positive_stage_cap": clamp_relationship_positive_stage_cap,
        "current_interaction_projection": current_interaction_projection,
        "deepcopy": copy.deepcopy,
        "migrate_legacy_relationship_score": migrate_legacy_relationship_score,
        "normalize_normal_interaction_band_cap": normalize_normal_interaction_band_cap,
        "normalize_relationship_mode": normalize_relationship_mode,
        "normalize_relationship_positive_stage_cap_key": normalize_relationship_positive_stage_cap_key,
        "_now_ts": time.time,
    },
)


class _AsyncLock:
    async def __aenter__(self) -> "_AsyncLock":
        return self

    async def __aexit__(self, _exc_type: Any, _exc: Any, _tb: Any) -> None:
        return None


class _Plugin:
    normal_interaction_band_cap = "warm"
    relationship_positive_stage_cap_key = "deeply_bonded"

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

    def _save_data_sync(self, **_kwargs) -> None:
        self.saved += 1


class _ApiHost:
    update_user = UPDATE_USER

    def __init__(self, user: dict[str, Any]) -> None:
        self.plugin = _Plugin(user)

    @staticmethod
    def _single_line(value: Any, limit: int) -> str:
        return _single_line(value, limit)

    @staticmethod
    def _relationship_score_input(value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or not -1200 <= value <= 1200:
            raise ValueError("invalid relationship score")
        return value

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


class _SettingsPlugin:
    relationship_positive_stage_cap_key = "deeply_bonded"
    normal_interaction_band_cap = "warm"

    def __init__(self) -> None:
        self.data = {
            "users": {
                "10001": {
                    "relationship_role": "friend",
                    "relationship_mode": "normal",
                    "relationship_score": 1000,
                    "relationship_positive_stage_cap_key": "deeply_bonded",
                    "normal_interaction_band_cap": "warm",
                    "current_interaction": {"expression_band": "warm"},
                }
            }
        }
        self._data_lock = _AsyncLock()
        self._data_default = self.data
        self.enable_multi_persona_mode = False
        self.saved = 0
        self.persisted_default = copy.deepcopy(self.data)

    def _save_data_sync(self, **_kwargs) -> None:
        self.saved += 1

    async def _flush_scheduled_data_save(self) -> None:
        return None

    def _write_data_snapshot_sync(self, snapshot: dict[str, Any]) -> None:
        self.saved += 1
        self.persisted_default = copy.deepcopy(snapshot)


class _SettingsApiHost:
    IMAGE_API_RUNTIME_SETTING_KEYS: set[str] = set()
    _apply_config_value = APPLY_CONFIG_VALUE
    _relationship_profile_targets = RELATIONSHIP_PROFILE_TARGETS
    _save_relationship_profile_target = SAVE_RELATIONSHIP_PROFILE_TARGET
    _apply_relationship_profile_config_batch = APPLY_RELATIONSHIP_PROFILE_BATCH
    _restore_relationship_config_values = RESTORE_RELATIONSHIP_CONFIG_VALUES
    _rollback_relationship_config_transaction = ROLLBACK_RELATIONSHIP_CONFIG_TRANSACTION
    _req041_config_runtime_snapshot = REQ041_CONFIG_RUNTIME_SNAPSHOT
    _rollback_req041_config_runtime = ROLLBACK_REQ041_CONFIG_RUNTIME
    update_settings = UPDATE_SETTINGS

    def __init__(self) -> None:
        self.plugin = _SettingsPlugin()
        self.config_values: dict[str, Any] = {}

    def _set_config_value(self, key: str, value: Any) -> None:
        self.config_values[key] = value

    @staticmethod
    def _forward_runtime_config_effects(
        _key: str,
        _value: Any,
        _overrides: dict[str, Any] | None = None,
    ) -> None:
        return None

    @staticmethod
    def _single_line(value: Any, limit: int) -> str:
        return _single_line(value, limit)

    @staticmethod
    def _allowed_setting_keys() -> set[str]:
        return {"relationship_positive_stage_cap_key", "normal_interaction_band_cap"}

    @staticmethod
    def _allowed_feature_keys() -> set[str]:
        return set()

    @staticmethod
    def _allowed_provider_keys() -> set[str]:
        return set()

    @staticmethod
    def _schema_bool_keys() -> set[str]:
        return set()

    @staticmethod
    def _normalize_setting_value(_key: str, value: Any) -> Any:
        return value

    @staticmethod
    def _normalize_bool_value(value: Any) -> bool:
        return bool(value)

    @staticmethod
    async def _record_personality_auto_tune_manual_values(_changed: dict[str, Any]) -> None:
        return None

    @staticmethod
    async def _save_config_if_possible() -> bool:
        return True

    @staticmethod
    async def get_overview() -> dict[str, Any]:
        return {"success": True, "data": {"features": {}, "settings": {}}}

    @staticmethod
    def _error(message: str) -> dict[str, Any]:
        return {"success": False, "error": message}

    @staticmethod
    def _is_http_error_response(_value: Any) -> bool:
        return False

    @staticmethod
    def _exception_error(message: str) -> dict[str, Any]:
        return {"success": False, "error": message}


class _RelationshipStateHost:
    relationship_positive_stage_cap_key = "deeply_bonded"
    normal_interaction_band_cap = "warm"
    default_interaction_band = "relaxed"
    relationship_decay_grace_days = 0
    relationship_decay_early_per_day = 0
    relationship_decay_middle_per_day = 0
    relationship_decay_late_per_day = 0
    enable_custom_relationship_stage_policy = True
    relationship_stage_policy = None

    _ensure_relationship_user_state = ENSURE_RELATIONSHIP_STATE


class _RelationshipLedgerHost:
    enable_custom_relationship_stage_policy = False
    enable_p4_b_legacy_score_isolation = False

    _apply_relationship_event = APPLY_RELATIONSHIP_EVENT

    @staticmethod
    def _schedule_data_save(*_args, **_kwargs) -> None:
        return None


def _update_user(
    user: dict[str, Any],
    payload: dict[str, Any],
    *,
    interaction_cap: str = "warm",
    positive_cap: str | None = None,
) -> tuple[dict[str, Any], _ApiHost]:
    host = _ApiHost(copy.deepcopy(user))
    host.plugin.normal_interaction_band_cap = interaction_cap
    if positive_cap is not None:
        host.plugin.relationship_positive_stage_cap_key = positive_cap
    REQUEST.payload = {"user_id": "10001", **payload}
    return asyncio.run(host.update_user()), host


def test_relationship_stage_boundaries_are_stable() -> None:
    expected = {
        -1200: "deeply_distant",
        -801: "deeply_distant",
        -800: "strongly_distant",
        -401: "strongly_distant",
        -400: "distant",
        -1: "distant",
        0: "acquaintance",
        199: "acquaintance",
        200: "familiar",
        599: "familiar",
        600: "close",
        899: "close",
        900: "intimate",
        1199: "intimate",
        1200: "deeply_bonded",
    }
    assert {score: relationship_stage_for_score(score)["phase"]["key"] for score in expected} == expected


def test_ordinary_user_relationship_cap_defaults_to_close() -> None:
    assert normalize_relationship_positive_stage_cap_key(None) == "close"
    assert normalize_relationship_positive_stage_cap_key("unknown") == "close"
    assert relationship_positive_score_cap(None) == 899


def test_ledger_deduplicates_caps_and_decays_toward_zero() -> None:
    user = {"relationship_score": 598, "relationship_role": "friend"}
    first = apply_relationship_event(user, 9, reason_code="helpful_reply", event_id="message-1", now=1_700_000_000)
    duplicate = apply_relationship_event(user, 9, reason_code="helpful_reply", event_id="message-1", now=1_700_000_001)
    assert first["changed"] is True
    assert first["delta"] == 4
    assert duplicate["code"] == "duplicate_event"

    user["relationship_last_effective_at"] = 1_700_000_000 - 6 * 86400
    before = user["relationship_score"]
    decay = apply_natural_relationship_decay(user, now=1_700_000_000)
    assert decay["changed"] is True
    assert 0 <= user["relationship_score"] < before


def test_legacy_relationship_scores_migrate_once_with_stage_anchors() -> None:
    for legacy, expected in ((0, 0), (3, 200), (16, 600), (55, 900), (120, 1200)):
        user = {"relationship_score": legacy}
        first = migrate_legacy_relationship_score(user, now=1_700_000_000)
        second = migrate_legacy_relationship_score(user, now=1_700_000_100)
        assert first["changed"] is True
        assert user["relationship_score"] == expected
        assert user["relationship_score_schema_version"] == 2
        assert second["changed"] is False

    created = {"relationship_score": 0}
    migrate_legacy_relationship_score(created, created=True, now=1_700_000_000)
    assert created["relationship_score"] == 0
    assert relationship_stage_for_score(0)["phase"]["proactive_care_limit"] == 0


def test_relationship_ledger_uses_sliding_dedupe_and_configured_timezone() -> None:
    boundary = 1_800_000.0
    user = {"relationship_role": "friend", "relationship_score": 0}
    first = apply_relationship_event(user, 1, reason_code="inbound", now=boundary - 1, event_window_seconds=1800)
    duplicate = apply_relationship_event(user, 1, reason_code="fast_inbound", now=boundary + 1, event_window_seconds=1800)
    later = apply_relationship_event(user, 1, reason_code="inbound", now=boundary + 1799, event_window_seconds=1800)
    assert first["changed"] is True
    assert duplicate["code"] == "duplicate_event"
    assert later["changed"] is True

    from datetime import datetime
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("Asia/Shanghai")
    before_midnight = datetime(2026, 8, 3, 23, 59, tzinfo=tz).timestamp()
    after_midnight = datetime(2026, 8, 4, 0, 1, tzinfo=tz).timestamp()
    daily = {"relationship_role": "friend", "relationship_score": 0}
    assert apply_relationship_event(
        daily, 2, reason_code="support", event_id="before", now=before_midnight,
        positive_daily_cap=2, timezone_name="Asia/Shanghai",
    )["changed"] is True
    assert apply_relationship_event(
        daily, 2, reason_code="support", event_id="after", now=after_midnight,
        positive_daily_cap=2, timezone_name="Asia/Shanghai",
    )["changed"] is True
    assert daily["relationship_daily_totals"]["day"] == "2026-08-04"


def test_content_intent_requires_explicit_mode_request_and_disabled_policy_is_neutral() -> None:
    for text in ("咖啡再甜一点", "这段剧情有点暧昧", "做爱会怀孕吗？我想要了解避孕风险"):
        assert content_intent_from_text(text) == {"requested_content_tier": "normal", "turn_consent": False}
    explicit = content_intent_from_text("请进入亲密模式，说得暧昧一点")
    assert explicit == {"requested_content_tier": "flirt", "turn_consent": False}

    decision = build_expression_decision(
        {"message_intent": explicit, "content_policy": {"enabled": False}}
    )
    assert decision.content_provider_policy == "unmanaged"
    prompt = expression_decision_prompt(decision)
    assert "内容尺度" not in prompt
    assert "成人内容" not in prompt


def test_ledger_scopes_dedupe_by_event_type_and_coalesces_fast_path_aliases() -> None:
    user = {"relationship_score": 0, "relationship_role": "friend"}
    inbound = apply_relationship_event(user, 1, reason_code="inbound", event_id="message-1", now=1_700_000_000)
    feedback = apply_relationship_event(user, 2, reason_code="care_feedback", event_id="message-1", now=1_700_000_000)
    fast_duplicate = apply_relationship_event(user, 1, reason_code="fast_inbound", event_id="message-1", now=1_700_000_001)
    assert inbound["changed"] is True
    assert feedback["changed"] is True
    assert fast_duplicate["code"] == "duplicate_event"
    assert user["relationship_score"] == 3


def test_owner_exclusive_relationship_is_frozen() -> None:
    owner = {
        "relationship_score": 600,
        "relationship_role": "owner",
        "relationship_mode": "owner_exclusive",
    }
    assert apply_relationship_event(owner, 4, reason_code="support", now=1_700_000_000)["code"] == "owner_exclusive_frozen"
    assert apply_natural_relationship_decay(owner, now=1_800_000_000)["code"] == "owner_exclusive_frozen"
    assert owner["relationship_score"] == 600


def test_lowering_relationship_cap_migrates_once_proportionally() -> None:
    user = {"relationship_score": 1000, "relationship_role": "friend"}
    first = migrate_relationship_positive_stage_cap(
        user,
        old_cap_key="deeply_bonded",
        new_cap_key="close",
        now=1_700_000_000,
    )
    after = user["relationship_score"]
    second = migrate_relationship_positive_stage_cap(
        user,
        old_cap_key="deeply_bonded",
        new_cap_key="close",
        now=1_700_000_001,
    )
    assert first["changed"] is True
    assert 0 < after <= 899
    assert second["code"] == "positive_stage_cap_already_migrated"
    assert user["relationship_score"] == after


def test_owner_normal_mode_ignores_positive_stage_cap() -> None:
    owner = {
        "relationship_role": "owner",
        "relationship_mode": "normal",
        "relationship_score": 899,
        "relationship_positive_stage_cap_key": "close",
    }
    result = apply_relationship_event(owner, 10, reason_code="inbound", event_id="owner-1", now=1_700_000_000)
    assert result["changed"] is True
    assert owner["relationship_score"] == 902

    friend = {
        "relationship_role": "friend",
        "relationship_mode": "normal",
        "relationship_score": 899,
        "relationship_positive_stage_cap_key": "close",
    }
    blocked = apply_relationship_event(friend, 10, reason_code="inbound", event_id="friend-1", now=1_700_000_000)
    assert blocked["changed"] is False
    assert blocked["code"] == "positive_stage_cap"
    assert friend["relationship_score"] == 899


def test_owner_normal_mode_not_clamped_or_migrated_down() -> None:
    owner = {
        "relationship_role": "owner",
        "relationship_mode": "normal",
        "relationship_score": 1200,
        "relationship_positive_stage_cap_key": "deeply_bonded",
    }
    clamped = clamp_relationship_positive_stage_cap(owner, cap_key="close", now=1_700_000_000)
    assert clamped["changed"] is False
    assert clamped["code"] == "owner_role_exempt"
    assert owner["relationship_score"] == 1200

    migrated = migrate_relationship_positive_stage_cap(
        owner,
        old_cap_key="deeply_bonded",
        new_cap_key="close",
        now=1_700_000_001,
    )
    assert migrated["changed"] is False
    assert migrated["code"] == "owner_role_exempt"
    assert owner["relationship_score"] == 1200
    assert owner["relationship_positive_stage_cap_key"] == "close"
    assert "relationship_positive_stage_cap_migration" not in owner


def test_update_user_owner_is_uncapped_and_friend_over_cap_is_rejected() -> None:
    owner = {"relationship_role": "owner", "relationship_mode": "normal", "relationship_score": 899}
    owner_result, owner_host = _update_user(owner, {"relationship_score": 1200}, positive_cap="close")
    assert owner_result["ok"] is True
    assert owner_host.plugin.data["users"]["10001"]["relationship_score"] == 1200

    friend = {"relationship_role": "friend", "relationship_mode": "normal", "relationship_score": 400}
    friend_result, friend_host = _update_user(
        friend,
        {"relationship_score": 1200, "nickname": "不应落地"},
        positive_cap="close",
    )
    assert friend_result["ok"] is False
    assert "普通用户亲密度上限" in friend_result["error"]
    assert friend_host.plugin.data["users"]["10001"]["relationship_score"] == 400
    assert "nickname" not in friend_host.plugin.data["users"]["10001"]


def test_settings_batch_persists_both_cap_migrations_once() -> None:
    host = _SettingsApiHost()
    REQUEST.payload = {
        "settings": {
            "relationship_positive_stage_cap_key": "close",
            "normal_interaction_band_cap": "lively",
        }
    }
    result = asyncio.run(host.update_settings())
    user = host.plugin.data["users"]["10001"]
    assert result["success"] is True
    assert host.plugin.saved == 1
    assert 0 < user["relationship_score"] <= 899
    assert user["relationship_positive_stage_cap_key"] == "close"
    assert user["normal_interaction_band_cap"] == "lively"
    assert user["current_interaction"]["expression_band"] == "lively"


def test_settings_batch_rolls_back_profiles_and_runtime_when_config_save_returns_false() -> None:
    host = _SettingsApiHost()
    before = copy.deepcopy(host.plugin.data)
    save_calls: list[dict[str, Any]] = []

    async def save_config() -> bool:
        save_calls.append(copy.deepcopy(host.config_values))
        return len(save_calls) > 1

    host._save_config_if_possible = save_config
    REQUEST.payload = {
        "settings": {
            "relationship_positive_stage_cap_key": "close",
            "normal_interaction_band_cap": "lively",
        }
    }
    result = asyncio.run(host.update_settings())

    assert result["success"] is False
    assert "已回滚" in result["error"]
    assert host.plugin.data == before
    assert host.plugin.persisted_default == before
    assert host.plugin.relationship_positive_stage_cap_key == "deeply_bonded"
    assert host.plugin.normal_interaction_band_cap == "warm"
    assert host.config_values["relationship_positive_stage_cap_key"] == "deeply_bonded"
    assert host.config_values["normal_interaction_band_cap"] == "warm"
    assert save_calls == [
        {
            "relationship_positive_stage_cap_key": "close",
            "normal_interaction_band_cap": "lively",
        },
        {
            "relationship_positive_stage_cap_key": "deeply_bonded",
            "normal_interaction_band_cap": "warm",
        },
    ]


def test_req041_settings_roll_back_runtime_and_config_when_save_returns_false() -> None:
    host = _SettingsApiHost()
    host.plugin.enable_auto_user_profile_creation = False
    host.plugin.enable_group_relationship_affinity = False
    host.plugin.group_relationship_affinity_allowlist = []
    host._allowed_feature_keys = lambda: {
        "enable_auto_user_profile_creation", "enable_group_relationship_affinity",
    }
    host._allowed_setting_keys = lambda: {"group_relationship_affinity_allowlist"}
    save_calls: list[dict[str, Any]] = []

    async def save_config() -> bool:
        save_calls.append(copy.deepcopy(host.config_values))
        return len(save_calls) > 1

    host._save_config_if_possible = save_config
    REQUEST.payload = {
        "features": {
            "enable_auto_user_profile_creation": True,
            "enable_group_relationship_affinity": True,
        },
        "settings": {"group_relationship_affinity_allowlist": ["group-a"]},
    }
    result = asyncio.run(host.update_settings())

    assert result["success"] is False
    assert "REQ-041 关键运行值已回滚" in result["error"]
    assert host.plugin.enable_auto_user_profile_creation is False
    assert host.plugin.enable_group_relationship_affinity is False
    assert host.plugin.group_relationship_affinity_allowlist == []
    assert host.config_values == {
        "enable_auto_user_profile_creation": False,
        "enable_group_relationship_affinity": False,
        "group_relationship_affinity_allowlist": [],
    }
    assert save_calls[0] == {
        "enable_auto_user_profile_creation": True,
        "enable_group_relationship_affinity": True,
        "group_relationship_affinity_allowlist": ["group-a"],
    }
    assert save_calls[1] == host.config_values


def test_req041_settings_report_inconsistent_when_rollback_save_also_fails() -> None:
    host = _SettingsApiHost()
    host.plugin.enable_auto_user_profile_creation = False
    host._allowed_feature_keys = lambda: {"enable_auto_user_profile_creation"}
    host._allowed_setting_keys = lambda: set()

    async def save_config() -> bool:
        return False

    host._save_config_if_possible = save_config
    REQUEST.payload = {"features": {"enable_auto_user_profile_creation": True}}
    result = asyncio.run(host.update_settings())

    assert result["success"] is False
    assert "旧配置重新持久化失败" in result["error"]
    assert host.plugin.enable_auto_user_profile_creation is False
    assert host.config_values["enable_auto_user_profile_creation"] is False


def test_req041_settings_roll_back_when_config_save_raises() -> None:
    host = _SettingsApiHost()
    host.plugin.enable_group_relationship_affinity = False
    host._allowed_feature_keys = lambda: {"enable_group_relationship_affinity"}
    host._allowed_setting_keys = lambda: set()
    calls = 0

    async def save_config() -> bool:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("simulated req041 write failure")
        return True

    host._save_config_if_possible = save_config
    REQUEST.payload = {"features": {"enable_group_relationship_affinity": True}}
    result = asyncio.run(host.update_settings())

    assert result["success"] is False
    assert "simulated req041 write failure" in result["error"]
    assert calls == 2
    assert host.plugin.enable_group_relationship_affinity is False
    assert host.config_values["enable_group_relationship_affinity"] is False


def test_mixed_relationship_and_req041_settings_roll_back_together() -> None:
    host = _SettingsApiHost()
    before = copy.deepcopy(host.plugin.data)
    host.plugin.enable_group_relationship_affinity = False
    host._allowed_feature_keys = lambda: {"enable_group_relationship_affinity"}
    host._allowed_setting_keys = lambda: {
        "relationship_positive_stage_cap_key", "normal_interaction_band_cap",
    }
    save_calls = 0

    async def save_config() -> bool:
        nonlocal save_calls
        save_calls += 1
        return save_calls > 1

    host._save_config_if_possible = save_config
    REQUEST.payload = {
        "features": {"enable_group_relationship_affinity": True},
        "settings": {"relationship_positive_stage_cap_key": "close"},
    }
    result = asyncio.run(host.update_settings())

    assert result["success"] is False
    assert "关系配置、人格资料及 REQ-041 关键运行值已回滚" in result["error"]
    assert host.plugin.data == before
    assert host.plugin.enable_group_relationship_affinity is False
    assert host.plugin.relationship_positive_stage_cap_key == "deeply_bonded"
    assert host.config_values["enable_group_relationship_affinity"] is False
    assert host.config_values["relationship_positive_stage_cap_key"] == "deeply_bonded"
    assert save_calls == 3


def test_settings_batch_rolls_back_when_config_save_raises() -> None:
    host = _SettingsApiHost()
    before = copy.deepcopy(host.plugin.data)
    save_calls = 0

    async def save_config() -> bool:
        nonlocal save_calls
        save_calls += 1
        if save_calls == 1:
            raise OSError("simulated config write failure")
        return True

    host._save_config_if_possible = save_config
    REQUEST.payload = {
        "settings": {
            "relationship_positive_stage_cap_key": "close",
            "normal_interaction_band_cap": "lively",
        }
    }
    result = asyncio.run(host.update_settings())

    assert result["success"] is False
    assert "simulated config write failure" in result["error"]
    assert save_calls == 2
    assert host.plugin.data == before
    assert host.plugin.persisted_default == before
    assert host.plugin.relationship_positive_stage_cap_key == "deeply_bonded"
    assert host.plugin.normal_interaction_band_cap == "warm"


def test_settings_batch_rolls_back_all_personas_after_mid_save_failure() -> None:
    host = _SettingsApiHost()
    default_profile = copy.deepcopy(host.plugin.data)
    persona_profiles = {
        "main": copy.deepcopy(host.plugin.data),
        "alt": copy.deepcopy(host.plugin.data),
    }
    host.plugin.enable_multi_persona_mode = True
    host.plugin._data_default = default_profile
    host.plugin.data = persona_profiles["main"]
    host.plugin.persona_profiles = persona_profiles
    host.plugin.persisted_profiles = {
        "": copy.deepcopy(default_profile),
        **{profile_id: copy.deepcopy(profile) for profile_id, profile in persona_profiles.items()},
    }
    host.plugin.profile_save_calls = 0
    host.plugin.fail_profile_save_once = True

    def persona_profile_ids() -> list[str]:
        return ["main", "alt"]

    def ensure_persona_profile(profile_id: str) -> dict[str, Any]:
        return host.plugin.persona_profiles[profile_id]

    def write_default(snapshot: dict[str, Any]) -> None:
        host.plugin.profile_save_calls += 1
        host.plugin.persisted_profiles[""] = copy.deepcopy(snapshot)

    def save_persona(profile_id: str, snapshot: dict[str, Any]) -> None:
        host.plugin.profile_save_calls += 1
        if host.plugin.fail_profile_save_once:
            host.plugin.fail_profile_save_once = False
            raise OSError("simulated persona write failure")
        host.plugin.persisted_profiles[profile_id] = copy.deepcopy(snapshot)

    host.plugin._persona_profile_ids = persona_profile_ids
    host.plugin._ensure_persona_profile = ensure_persona_profile
    host.plugin._write_data_snapshot_sync = write_default
    host.plugin._save_persona_profile_sync = save_persona
    before_runtime = {
        "": copy.deepcopy(default_profile),
        **{profile_id: copy.deepcopy(profile) for profile_id, profile in persona_profiles.items()},
    }
    REQUEST.payload = {
        "settings": {
            "relationship_positive_stage_cap_key": "close",
            "normal_interaction_band_cap": "lively",
        }
    }
    result = asyncio.run(host.update_settings())

    assert result["success"] is False
    assert "simulated persona write failure" in result["error"]
    assert default_profile == before_runtime[""]
    assert persona_profiles["main"] == before_runtime["main"]
    assert persona_profiles["alt"] == before_runtime["alt"]
    assert host.plugin.persisted_profiles == before_runtime
    assert host.plugin.relationship_positive_stage_cap_key == "deeply_bonded"
    assert host.plugin.normal_interaction_band_cap == "warm"


def test_zero_rate_decay_settlement_is_reported_for_persistence() -> None:
    host = _RelationshipStateHost()
    user = {
        "relationship_role": "friend",
        "relationship_mode": "normal",
        "relationship_score": 400,
        "relationship_score_schema_version": 2,
        "relationship_positive_stage_cap_key": "deeply_bonded",
        "normal_interaction_band_cap": "warm",
        "current_interaction": current_interaction_projection(
            {"expression_band": "relaxed"},
            relationship_role="friend",
            relationship_mode="normal",
            relationship_score=400,
            normal_interaction_band_cap="warm",
            now=time.time(),
        ),
        "relationship_last_effective_at": time.time() - 10 * 86400,
    }
    assert host._ensure_relationship_user_state(user) is True
    assert user.get("relationship_decay_settled_day")
    assert host._ensure_relationship_user_state(user) is False


def test_disabled_affinity_master_switch_does_not_settle_ledger_or_expression() -> None:
    host = _RelationshipLedgerHost()
    user = {
        "user_id": "u-disabled",
        "relationship_score": 4,
        "relationship_score_schema_version": 2,
        "relationship_ledger": [{"event_id": "existing", "delta": 4}],
    }
    before = copy.deepcopy(user)

    result = host._apply_relationship_event(user, 2, reason_code="inbound", event_id="new")

    assert result["code"] == "relationship_system_disabled"
    assert user == before
    expression_host = _ExpressionHost()
    expression_host.enable_custom_relationship_stage_policy = False
    decision = expression_host._build_expression_decision_for_user(user, channel_scope="group", now=NOW)
    assert decision.expression_band == "relaxed"


def test_all_seven_interaction_bands_are_owner_only_where_required() -> None:
    assert len(OWNER_EXPRESSION_BANDS) == 7
    for band in OWNER_EXPRESSION_BANDS:
        owner = current_interaction_projection(
            {"expression_band": band, "manual_override": True},
            relationship_role="owner",
            relationship_mode="owner_exclusive",
            relationship_score=1200,
        )
        assert owner["expression_band"] == band

    ordinary = current_interaction_projection(
        {"expression_band": "affectionate", "manual_override": True},
        relationship_role="friend",
        relationship_score=1200,
    )
    assert ordinary["expression_band"] == "warm"
    assert "owner_role_required" in ordinary["reason_codes"]


def _flirt_input() -> dict:
    return {
        "relationship_score": 1200,
        "relationship_stage": "deeply_bonded",
        "relationship_role": "friend",
        "relationship_mode": "normal",
        "current_interaction": {"expression_band": "warm"},
        "message_intent": {"requested_content_tier": "flirt", "turn_consent": False},
        "content_policy": {
            "enabled": True,
            "flirt_enabled": True,
            "private_chat": True,
        },
    }


def test_flirt_tier_is_fail_closed_for_every_required_condition() -> None:
    baseline = _flirt_input()
    assert build_expression_decision(baseline).content_tier == "flirt"

    mutations = (
        ("master", lambda value: value["content_policy"].update(enabled=False)),
        ("switch", lambda value: value["content_policy"].update(flirt_enabled=False)),
        ("stage", lambda value: value.update(relationship_stage="familiar")),
        ("interaction", lambda value: value.update(current_interaction={"expression_band": "avoidant"})),
        ("group", lambda value: value["content_policy"].update(private_chat=False)),
    )
    for label, mutate in mutations:
        value = _flirt_input()
        mutate(value)
        assert build_expression_decision(value).content_tier == "normal", label


def test_proactive_path_never_infers_a_flirt_tier() -> None:
    value = _flirt_input()
    value["message_intent"]["requested_content_tier"] = "normal"
    value["proactive_candidate"] = {"eligible": True, "budget": 3}
    decision = build_expression_decision(value)
    assert decision.content_tier == "normal"
    assert decision.proactive_budget >= 0


def test_unified_decision_owns_proactive_readiness_and_cooldown() -> None:
    base = {
        "relationship_score": 0,
        "relationship_role": "friend",
        "relationship_mode": "normal",
        "current_interaction": {"expression_band": "relaxed"},
    }
    cooled = build_expression_decision(
        {
            **base,
            "proactive_candidate": {
                "eligible": True,
                "budget": 6,
                "readiness_score": 80,
                "current_ts": 100.0,
                "cooldown_until": 200.0,
            },
        }
    )
    assert cooled.proactive_budget == 0
    assert cooled.initiative == "passive_only"
    assert cooled.proactive_cooldown_until == 200.0
    assert "proactive_cooldown_active" in cooled.reason_codes

    capped = build_expression_decision(
        {
            **base,
            "proactive_candidate": {"eligible": True, "budget": 6, "readiness_score": 35},
        }
    )
    assert capped.proactive_budget == 1
    assert "proactive_readiness_capped" in capped.reason_codes

    suppressed = build_expression_decision(
        {
            **base,
            "proactive_candidate": {"eligible": True, "budget": 6, "readiness_score": 10},
        }
    )
    assert suppressed.proactive_budget == 0
    assert "proactive_readiness_suppressed" in suppressed.reason_codes


def test_legacy_relationship_state_cannot_override_unified_expression() -> None:
    host = _ExpressionHost()
    decision = host._build_expression_decision_for_user(
        {
            "user_id": "friend",
            "relationship_role": "friend",
            "relationship_mode": "normal",
            "relationship_score": 300,
            "relationship_state": {"mode": "hurt", "hurt_until": NOW + 600},
        },
        proactive_candidate={"eligible": True, "budget": 5},
        now=NOW,
    )
    assert decision.expression_band != "hurt"
    assert decision.proactive_cooldown_until != NOW + 600
    assert "legacy_relationship_state_projection" not in decision.reason_codes


def test_private_intent_settles_boundary_reengagement_and_hurt_without_legacy_authority() -> None:
    host = _InteractionHost()
    user = {"user_id": "friend", "relationship_role": "friend", "relationship_mode": "normal"}
    host._settle_current_interaction_from_intent(
        user,
        {"intent": "boundary", "confidence": 0.95, "boundary_durable": True},
    )
    assert user["current_interaction"]["expression_band"] == "avoidant"
    assert user["contact_preference"]["active"] is True
    assert user["current_interaction"]["reason"] == "contact_boundary_active"
    assert "relationship_state" not in user

    owner = {
        "user_id": "owner",
        "relationship_role": "owner",
        "relationship_mode": "owner_exclusive",
        "contact_preference": {"mode": "no_contact", "active": True, "no_contact": True},
    }
    host._settle_current_interaction_from_intent(
        owner,
        {"intent": "intimacy", "confidence": 0.9, "emotion_event": "neutral"},
    )
    assert owner["contact_preference"]["active"] is False
    assert owner["current_interaction"]["expression_band"] == "lively"
    assert owner["current_interaction"]["reason"] == "intimate_interaction"

    hurt = {"user_id": "friend", "relationship_role": "friend", "relationship_mode": "normal"}
    host._settle_current_interaction_from_intent(
        hurt,
        {
            "intent": "chat",
            "confidence": 0.9,
            "emotion_event": "hurt",
            "emotion_confidence": 0.9,
            "emotion_intensity": 95,
            "emotion_target": "bot",
        },
    )
    assert hurt["current_interaction"]["expression_band"] == "avoidant"
    assert hurt["current_interaction"]["reason"] == "severe_hurt_event"
    assert NOW < hurt["current_interaction"]["expires_at"] <= NOW + 120 * 60


def test_private_intent_preserves_unexpired_manual_interaction_override() -> None:
    host = _InteractionHost()
    user = {
        "user_id": "owner",
        "relationship_role": "owner",
        "relationship_mode": "owner_exclusive",
        "current_interaction": {
            "expression_band": "affectionate",
            "source": "manual",
            "manual_override": True,
            "updated_at": NOW - 10,
            "expires_at": NOW + 3600,
        },
    }
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
    assert user["current_interaction"]["expression_band"] == "affectionate"
    assert user["current_interaction"]["manual_override"] is True


def test_user_update_enforces_owner_freeze_role_and_interaction_caps() -> None:
    exclusive = {"relationship_role": "owner", "relationship_mode": "owner_exclusive", "relationship_score": 600}
    frozen, _ = _update_user(exclusive, {"relationship_score": 500})
    assert frozen["ok"] is False

    left, left_host = _update_user(exclusive, {"relationship_mode": "normal", "relationship_score": 500})
    assert left["ok"] is True
    assert left_host.plugin.data["users"]["10001"]["relationship_mode"] == "normal"
    assert left_host.plugin.data["users"]["10001"]["relationship_score"] == 500

    friend = {"relationship_role": "friend", "relationship_mode": "normal", "relationship_score": 400}
    owner_only, _ = _update_user(friend, {"current_interaction_band": "affectionate"})
    assert owner_only["ok"] is False

    over_cap, _ = _update_user(friend, {"current_interaction_band": "warm"}, interaction_cap="lively")
    assert over_cap["ok"] is False
    assert "configured user cap" in over_cap["error"]

    owner_ok, owner_host = _update_user(exclusive, {"current_interaction_band": "affectionate"})
    assert owner_ok["ok"] is True
    interaction = owner_host.plugin.data["users"]["10001"]["current_interaction"]
    assert interaction["expression_band"] == "affectionate"
    assert interaction["manual_override"] is True


def test_retired_adult_provider_route_cannot_be_resurrected() -> None:
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "def _adult_content_provider_matches" not in source
    assert "ADULT_CONTENT_PROVIDER_ID" not in source


def test_strict_llm_provider_skips_peak_replacement_and_fallback() -> None:
    llm_call = _class_method(
        "token_budget.py",
        "TokenBudgetMixin",
        "_llm_call",
        {
            "Any": Any,
            "asyncio": asyncio,
            "logger": _Logger(),
            "time": time,
            "_single_line": _single_line,
            "_looks_like_upstream_llm_error_response": lambda _value: False,
        },
    )

    class Host:
        llm_provider_id = "cloud-default"
        model_timeout_overrides: dict[str, Any] = {}

        def __init__(self) -> None:
            self.called_provider = ""
            self.peak_calls = 0
            self.fallback_calls = 0
            self.context = type("Context", (), {"llm_generate": self._generate})()

        async def _generate(self, **kwargs: Any) -> Any:
            self.called_provider = kwargs["chat_provider_id"]
            return type("Response", (), {"completion_text": "ok", "role": "assistant"})()

        def _resolve_chat_provider_id(self, provider_id: str | None) -> str:
            return str(provider_id or self.llm_provider_id)

        def _apply_deepseek_peak_replacement(self, _provider_id: str) -> str:
            self.peak_calls += 1
            return "cloud-replacement"

        @staticmethod
        def _classify_llm_prompt(_prompt: str) -> str:
            return "response_review"

        @staticmethod
        def _is_llm_budget_exempt_task(_task: str) -> bool:
            return True

        @staticmethod
        def _daily_token_soft_limit_should_defer(_task: str) -> bool:
            return False

        @staticmethod
        def _llm_daily_budget_remaining() -> int:
            return 1

        def _model_fallback_provider_for_call(self, **_kwargs: Any) -> tuple[str, str]:
            self.fallback_calls += 1
            return "response_review", "cloud-fallback"

        @staticmethod
        def _model_token_limit_route_for_call(**_kwargs: Any) -> tuple[bool, int | None, int]:
            return False, None, 0

        @staticmethod
        def _model_timeout_seconds_for_call(**_kwargs: Any) -> None:
            return None

        @staticmethod
        def _record_llm_usage(**_kwargs: Any) -> None:
            return None

        @staticmethod
        def _sensitive_model_replacement_provider(_provider_id: str = "") -> str:
            return ""

        @staticmethod
        def _sensitive_model_replacement_keyword(_completion: str) -> str:
            return ""

    Host._llm_call = llm_call
    host = Host()
    result = asyncio.run(
        host._llm_call(
            "review",
            provider_id="pinned-review",
            task="response_review",
            strict_provider=True,
        )
    )
    assert result == "ok"
    assert host.called_provider == "pinned-review"
    assert host.peak_calls == 0
    assert host.fallback_calls == 0


def test_memory_presence_does_not_change_companion_expression_authority() -> None:
    payload = {
        "relationship_score": 600,
        "relationship_role": "friend",
        "relationship_mode": "normal",
        "current_interaction": {"expression_band": "warm"},
    }
    without_memory = build_expression_decision(payload)
    with_read_only_memory = build_expression_decision(
        {
            **payload,
            "memory_context": {
                "installed": True,
                "relationship_phase": "unrelated_memory_internal_value",
            },
        }
    )
    assert with_read_only_memory == without_memory


def test_non_adult_output_guard_and_shared_consumers_are_wired() -> None:
    detector = _class_method(
        "user_memory.py",
        "UserMemoryMixin",
        "_response_contains_content_tier_review_candidate",
        {"Any": Any, "re": __import__("re"), "_single_line": _single_line},
    )
    assert detector("普通问候和日常聊天") is False
    assert detector("这段包含露骨性行为描述") is True
    for normal_context in (
        "乳房疼痛可能与激素变化有关，建议就医。",
        "裸体艺术在美术史中有长期传统。",
        "这部小说的高潮部分处理得很好。",
        "阴道炎需要接受规范检查和治疗。",
        "这段话没有露骨描写。",
        "色情诈骗链接不要点击。",
    ):
        assert detector(normal_context) is False

    main_tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
    plugin = next(node for node in main_tree.body if isinstance(node, ast.ClassDef) and node.name == "PrivateCompanionPlugin")
    hook = next(node for node in plugin.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "inject_unified_relationship_expression")
    hook_names = {node.id for node in ast.walk(hook) if isinstance(node, ast.Name)}
    assert {"content_intent_from_text", "expression_decision_prompt"} <= hook_names
    assert "_private_companion_expression_decision" in ast.unparse(hook)

    for filename in ("proactive.py", "proactive_message.py"):
        source = (ROOT / filename).read_text(encoding="utf-8")
        assert "_build_expression_decision_for_user" in source, filename
        assert '"requested_content_tier": "normal"' in source, filename
    tts_source = (ROOT / "tts_enhancement.py").read_text(encoding="utf-8")
    assert '_private_companion_expression_decision' in tts_source
    assert 'expression.get("tts_style")' in tts_source
    assert "语音只能收敛语气，不能扩大文字内容尺度" in tts_source


def test_legacy_relationship_state_has_no_parallel_expression_consumers() -> None:
    def method_source(filename: str, class_name: str, method_name: str) -> str:
        tree = ast.parse((ROOT / filename).read_text(encoding="utf-8"))
        owner = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
        method = next(
            node
            for node in owner.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == method_name
        )
        return ast.unparse(method)

    user_source = (ROOT / "user_memory.py").read_text(encoding="utf-8")
    assert "def _relationship_approach_hint" not in user_source
    assert "expression_decision_prompt(" not in user_source

    intent_source = method_source("user_memory.py", "UserMemoryMixin", "_format_intent_relationship_injection")
    assert "relationship_state" not in intent_source
    assert "_format_emotion_residue_hint" not in intent_source

    planner_source = method_source("user_memory.py", "UserMemoryMixin", "_format_companion_planner_injection")
    assert "expression_decision_prompt" not in planner_source

    expression_context_source = method_source("user_memory.py", "UserMemoryMixin", "_expression_companion_context")
    assert "relationship_state" not in expression_context_source
    assert "_build_expression_decision_for_user" in expression_context_source

    temperature_source = method_source("proactive.py", "ProactiveMixin", "_relationship_proactive_temperature")
    assert "_build_expression_decision_for_user" in temperature_source
    assert 'user.get("relationship_score")' not in temperature_source
    assert "current_interaction_projection" not in temperature_source

    goodnight_source = method_source(
        "proactive_message.py",
        "ProactiveMessageMixin",
        "_goodnight_screen_check_block_reason",
    )
    assert "relationship_state" not in goodnight_source
    assert "_build_expression_decision_for_user" in goodnight_source

    tool_source = method_source(
        "llm_tool_actions.py",
        "LlmToolActionsMixin",
        "_reaction_expression_lookup_context",
    )
    assert "relationship_state" not in tool_source
    assert "_build_expression_decision_for_user" in tool_source

    main_source = (ROOT / "main.py").read_text(encoding="utf-8")
    assert main_source.count("expression_decision_prompt(projection)") == 1


def test_owner_group_projection_is_defaulted_and_never_mutates_real_relationship() -> None:
    host = _ExpressionHost()
    user = {
        "user_id": "owner-1",
        "relationship_role": "owner",
        "relationship_mode": "owner_exclusive",
        "relationship_score": 999,
        "current_interaction": {"expression_band": "affectionate", "source": "manual"},
        "relationship_ledger": [{"event_type": "manual", "delta": 99}],
    }
    before = copy.deepcopy(user)

    group = host._build_expression_decision_for_user(user, channel_scope="group", now=NOW)

    assert group.expression_band in {"relaxed", "lively", "warm"}
    assert group.content_tier == "normal"
    assert user["relationship_role"] == before["relationship_role"]
    assert user["relationship_mode"] == before["relationship_mode"]
    assert user["relationship_score"] == before["relationship_score"]
    assert user["current_interaction"] == before["current_interaction"]
    assert user["relationship_ledger"] == before["relationship_ledger"]


def test_owner_group_projection_switches_can_be_independently_opted_out() -> None:
    host = _ExpressionHost()
    host.owner_group_relationship_projection = False
    host.owner_group_interaction_projection = False
    user = {
        "user_id": "owner-1",
        "relationship_role": "owner",
        "relationship_mode": "owner_exclusive",
        "relationship_score": 999,
        "current_interaction": {"expression_band": "affectionate", "source": "manual"},
    }

    group = host._build_expression_decision_for_user(user, channel_scope="group", now=NOW)

    assert group.expression_band == "affectionate"


def test_active_panel_exposes_relationship_cards() -> None:
    source = (ROOT / "pages" / "陪伴面板" / "app.js").read_text(encoding="utf-8")
    assert 'enable_custom_relationship_stage_policy: ["启用好感度系统"' in source
    assert 'enable_relationship_content_tiers: ["关系内容尺度"' in source
    assert 'data-feature-param="relationship_stage_policy"' in source
    assert 'id="relationshipStageForm"' in source
    assert 'id="currentInteractionForm"' in source
    assert "专属联结只允许主要用户使用" in source
    assert "当轮明确请求 + 私聊 + 长期亲密及以上 + 当前互动非回避/受伤" in source

    schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8-sig"))
    items = schema["basic_config"]["items"]
    assert items["enable_relationship_content_tiers"]["default"] is False
    assert items["enable_flirt_content_tier"]["default"] is True
    assert "enable_adult_content_tier" not in items
    assert "ADULT_CONTENT_PROVIDER_ID" not in items
