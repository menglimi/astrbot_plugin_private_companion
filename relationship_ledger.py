"""Deterministic long-term companion relationship settlement.

This module owns the bounded relationship score.  It deliberately does not
grant identity, owner, memory, proactive, tool, or platform authority.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
from math import isfinite
from typing import Any
from zoneinfo import ZoneInfo

try:
    from .relationship_policy import (
        RELATIONSHIP_SCORE_MAX,
        RELATIONSHIP_SCORE_MIN,
        normalize_relationship_stage_policy,
        relationship_stage_for_score,
    )
except ImportError:  # pragma: no cover - direct-module test compatibility
    from relationship_policy import (
        RELATIONSHIP_SCORE_MAX,
        RELATIONSHIP_SCORE_MIN,
        normalize_relationship_stage_policy,
        relationship_stage_for_score,
    )


OWNER_EXCLUSIVE_MODE = "owner_exclusive"
NORMAL_RELATIONSHIP_MODE = "normal"
DEFAULT_POSITIVE_DAILY_CAP = 12
DEFAULT_EVENT_WINDOW_SECONDS = 30 * 60
_EVENT_REASON_ALIASES = {
    "fast_inbound": "inbound",
    "fast_proactive_reply": "proactive_reply",
    "fast_interaction_warmth": "interaction_warmth",
}
DEFAULT_DECAY_GRACE_DAYS = 3
RELATIONSHIP_SCORE_SCHEMA_VERSION = 2
_LEGACY_SCORE_ANCHORS: tuple[tuple[int, int], ...] = (
    (-120, -1200),
    (-80, -800),
    (-40, -400),
    (0, 0),
    (3, 200),
    (16, 600),
    (55, 900),
    (120, 1200),
)
RELATIONSHIP_POSITIVE_STAGE_CAP_KEYS = (
    "familiar",
    "close",
    "intimate",
    "deeply_bonded",
)
DEFAULT_RELATIONSHIP_POSITIVE_STAGE_CAP_KEY = "deeply_bonded"
_POSITIVE_STAGE_SCORE_MAX = {
    "familiar": 599,
    "close": 899,
    "intimate": 1199,
    "deeply_bonded": RELATIONSHIP_SCORE_MAX,
}


def normalize_relationship_mode(value: Any, role: Any) -> str:
    mode = str(value or "").strip().lower()
    normalized_role = str(role or "").strip().lower()
    if mode == OWNER_EXCLUSIVE_MODE and normalized_role == "owner":
        return OWNER_EXCLUSIVE_MODE
    return NORMAL_RELATIONSHIP_MODE


def is_owner_exclusive(user: Any) -> bool:
    if not isinstance(user, dict):
        return False
    return normalize_relationship_mode(user.get("relationship_mode"), user.get("relationship_role")) == OWNER_EXCLUSIVE_MODE


def is_owner_role(user: Any) -> bool:
    """Whether the record belongs to the configured primary user."""
    if not isinstance(user, dict):
        return False
    return str(user.get("relationship_role") or "").strip().lower() == "owner"


def normalize_relationship_positive_stage_cap_key(value: Any) -> str:
    """Return the configured ordinary-user positive relationship ceiling."""
    key = str(value or "").strip().lower()
    return key if key in RELATIONSHIP_POSITIVE_STAGE_CAP_KEYS else DEFAULT_RELATIONSHIP_POSITIVE_STAGE_CAP_KEY


def relationship_positive_score_cap(value: Any) -> int:
    return _POSITIVE_STAGE_SCORE_MAX[normalize_relationship_positive_stage_cap_key(value)]


def legacy_relationship_score_to_v2(value: Any) -> int:
    """Translate a legacy score while preserving the old asymmetric stages."""
    parsed = _integer(value)
    score = parsed if parsed is not None else 0
    if score <= _LEGACY_SCORE_ANCHORS[0][0]:
        return _LEGACY_SCORE_ANCHORS[0][1]
    if score >= _LEGACY_SCORE_ANCHORS[-1][0]:
        return _LEGACY_SCORE_ANCHORS[-1][1]
    for (left_old, left_new), (right_old, right_new) in zip(
        _LEGACY_SCORE_ANCHORS,
        _LEGACY_SCORE_ANCHORS[1:],
    ):
        if left_old <= score <= right_old:
            progress = (score - left_old) / max(1, right_old - left_old)
            return int(round(left_new + progress * (right_new - left_new)))
    return 0


def _repair_relationship_score_migration_audits(user: dict[str, Any]) -> bool:
    """Keep earlier v2 migration audits from being counted as relationship drift."""
    ledger = user.get("relationship_ledger")
    if not isinstance(ledger, list):
        return False
    changed = False
    for entry in ledger:
        if not isinstance(entry, dict) or entry.get("reason_code") != "relationship_score_schema_migration":
            continue
        if _integer(entry.get("delta")) != 0:
            entry["delta"] = 0
            changed = True
    return changed


def migrate_relationship_score_schema(
    user: Any,
    *,
    created: bool = False,
    now: Any = None,
    record_id: Any = None,
) -> dict[str, Any]:
    """Idempotently initialize new records or translate one persisted v1 score."""
    if not isinstance(user, dict):
        return _result(False, "invalid_user")
    version = _integer(user.get("relationship_score_schema_version")) or 0
    repaired = _repair_relationship_score_migration_audits(user)
    if version >= RELATIONSHIP_SCORE_SCHEMA_VERSION:
        code = "relationship_score_migration_audit_repaired" if repaired else "relationship_score_schema_current"
        return _result(repaired, code, score=_score(user.get("relationship_score")))

    if created:
        user["relationship_score_schema_version"] = RELATIONSHIP_SCORE_SCHEMA_VERSION
        return _result(True, "relationship_score_schema_initialized", score=_score(user.get("relationship_score")))

    ts = _timestamp(now)
    parsed_before = _integer(user.get("relationship_score"))
    before = parsed_before if parsed_before is not None else 0
    after = legacy_relationship_score_to_v2(before)
    clean_record_id = str(record_id or user.get("user_id") or "").strip()[:160]
    metadata = {
        "source_schema_version": version or 1,
        "target_schema_version": RELATIONSHIP_SCORE_SCHEMA_VERSION,
        "mapping": "legacy_piecewise_linear_v1",
        "score_before": before,
        "score_after": after,
        "migrated_at": ts,
    }
    if clean_record_id:
        metadata["record_id"] = clean_record_id

    user["relationship_score"] = after
    user["relationship_score_schema_version"] = RELATIONSHIP_SCORE_SCHEMA_VERSION
    user["relationship_score_migration"] = deepcopy(metadata)
    history = user.setdefault("relationship_score_migration_history", [])
    if not isinstance(history, list):
        history = []
        user["relationship_score_migration_history"] = history
    history.append(deepcopy(metadata))
    if len(history) > 20:
        del history[:-20]

    last_effective = _finite_number(user.get("relationship_last_effective_at")) or 0.0
    user["relationship_last_effective_at"] = max(last_effective, ts)
    user["relationship_decay_settled_day"] = ""
    ledger = user.setdefault("relationship_ledger", [])
    if not isinstance(ledger, list):
        ledger = []
        user["relationship_ledger"] = ledger
    event_source = (
        f"relationship_score_schema:{version or 1}:{RELATIONSHIP_SCORE_SCHEMA_VERSION}:"
        f"{clean_record_id}:{before}:{after}"
    )
    entry = {
        "event_key": sha256(event_source.encode("utf-8")).hexdigest()[:24],
        "reason_code": "relationship_score_schema_migration",
        # Scale translation is an audit event, not a relationship change.
        "delta": 0,
        "score_before": before,
        "score_after": after,
        "created_at": ts,
        "source": "schema_migration",
        **deepcopy(metadata),
    }
    ledger.append(entry)
    if len(ledger) > 200:
        del ledger[:-200]
    result = _result(True, "relationship_score_schema_migrated", score=after, entry=deepcopy(entry))
    result["migration"] = deepcopy(metadata)
    return result


def migrate_legacy_relationship_score(
    user: Any,
    *,
    created: bool = False,
    now: Any = None,
    record_id: Any = None,
) -> dict[str, Any]:
    """Backward-compatible name for the relationship score schema migration."""
    return migrate_relationship_score_schema(user, created=created, now=now, record_id=record_id)


def _effective_positive_stage_cap_key(user: dict[str, Any], configured: Any = None) -> str:
    return normalize_relationship_positive_stage_cap_key(
        configured if configured is not None else user.get("relationship_positive_stage_cap_key")
    )


def apply_relationship_event(
    user: Any,
    delta: Any,
    *,
    reason_code: Any,
    now: Any = None,
    event_id: Any = None,
    positive_daily_cap: int = DEFAULT_POSITIVE_DAILY_CAP,
    event_window_seconds: int = DEFAULT_EVENT_WINDOW_SECONDS,
    positive_event_cap: int = 4,
    negative_event_cap: int = 12,
    positive_stage_cap_key: Any = None,
    timezone_name: Any = None,
) -> dict[str, Any]:
    """Settle one auditable relationship event into the bounded score."""
    if not isinstance(user, dict):
        return _result(False, "invalid_user")
    numeric_delta = _integer(delta)
    if numeric_delta is None or numeric_delta == 0:
        return _result(False, "invalid_delta")
    reason = _reason(reason_code)
    if not reason:
        return _result(False, "missing_reason")
    ts = _timestamp(now)
    role = str(user.get("relationship_role") or "").strip().lower()
    mode = normalize_relationship_mode(user.get("relationship_mode"), role)
    user["relationship_mode"] = mode
    if mode == OWNER_EXCLUSIVE_MODE:
        return _result(False, "owner_exclusive_frozen", score=_score(user.get("relationship_score")))

    ledger = user.setdefault("relationship_ledger", [])
    if not isinstance(ledger, list):
        ledger = []
        user["relationship_ledger"] = ledger
    window_seconds = max(60, int(event_window_seconds or DEFAULT_EVENT_WINDOW_SECONDS))
    dedupe_key = _event_key(reason, ts, event_id, window_seconds)
    explicit_event_id = str(event_id or "").strip()
    canonical_reason = _EVENT_REASON_ALIASES.get(reason, reason)
    duplicate = any(
        isinstance(item, dict) and item.get("event_key") == dedupe_key
        for item in ledger[-200:]
    )
    if not explicit_event_id and not duplicate:
        for item in reversed(ledger[-200:]):
            if not isinstance(item, dict):
                continue
            item_reason = _EVENT_REASON_ALIASES.get(_reason(item.get("reason_code")), _reason(item.get("reason_code")))
            item_ts = _finite_number(item.get("created_at"))
            if item_reason == canonical_reason and item_ts is not None and abs(ts - item_ts) < window_seconds:
                duplicate = True
                break
    if duplicate:
        return _result(False, "duplicate_event", score=_score(user.get("relationship_score")))

    positive_cap = max(1, min(30, int(positive_event_cap or 4)))
    negative_cap = max(1, min(60, int(negative_event_cap or 12)))
    requested = max(-negative_cap, min(positive_cap, numeric_delta))
    before = _score(user.get("relationship_score"))
    if requested > 0 and before >= 900:
        requested = max(1, (requested + 1) // 2)
    elif requested > 0 and before >= 600:
        requested = max(1, (requested * 3 + 3) // 4)
    applied = requested
    day_key = _calendar_date(ts, timezone_name).isoformat()
    totals = user.setdefault("relationship_daily_totals", {})
    if not isinstance(totals, dict) or totals.get("day") != day_key:
        totals = {"day": day_key, "positive": 0, "negative": 0}
        user["relationship_daily_totals"] = totals
    if applied > 0:
        raw_daily_cap = DEFAULT_POSITIVE_DAILY_CAP if positive_daily_cap is None else positive_daily_cap
        cap = max(0, min(120, int(raw_daily_cap)))
        remaining = max(0, cap - _bounded_int(totals.get("positive"), 0, 0, cap))
        applied = min(applied, remaining)
        if applied <= 0:
            return _result(False, "positive_daily_cap", score=_score(user.get("relationship_score")))

    positive_score_cap = relationship_positive_score_cap(_effective_positive_stage_cap_key(user, positive_stage_cap_key))
    after = max(RELATIONSHIP_SCORE_MIN, min(RELATIONSHIP_SCORE_MAX, before + applied))
    # The stage ceiling is an ordinary-user limit; the primary user (owner)
    # is exempt unless the frozen owner-exclusive mode is active.
    if applied > 0 and not is_owner_exclusive(user) and not is_owner_role(user):
        after = min(after, positive_score_cap)
    applied = after - before
    if applied == 0:
        code = "positive_stage_cap" if requested > 0 and before >= positive_score_cap and not is_owner_role(user) else "score_bound"
        return _result(False, code, score=before)
    user["relationship_score"] = after
    if applied > 0:
        totals["positive"] = _bounded_int(totals.get("positive"), 0, 0, 120) + applied
        user["relationship_last_effective_at"] = ts
    else:
        totals["negative"] = _bounded_int(totals.get("negative"), 0, -120, 0) + applied
    entry = {
        "event_key": dedupe_key,
        "reason_code": reason,
        "delta": applied,
        "score_before": before,
        "score_after": after,
        "created_at": ts,
    }
    ledger.append(entry)
    if len(ledger) > 200:
        del ledger[:-200]
    return _result(True, "applied", score=after, delta=applied, entry=deepcopy(entry))


def clamp_relationship_positive_stage_cap(
    user: Any,
    *,
    cap_key: Any = None,
    now: Any = None,
    reason_code: Any = "relationship_positive_stage_cap_runtime_clamp",
) -> dict[str, Any]:
    """Clamp a normal positive score and append one auditable runtime entry."""
    if not isinstance(user, dict):
        return _result(False, "invalid_user")
    key = _effective_positive_stage_cap_key(user, cap_key)
    user["relationship_positive_stage_cap_key"] = key
    if is_owner_exclusive(user):
        return _result(False, "owner_exclusive_exempt", score=_score(user.get("relationship_score")))
    if is_owner_role(user):
        return _result(False, "owner_role_exempt", score=_score(user.get("relationship_score")))
    before = _score(user.get("relationship_score"))
    if before <= 0:
        return _result(False, "non_positive_score_exempt", score=before)
    maximum = relationship_positive_score_cap(key)
    if before <= maximum:
        return _result(False, "within_positive_stage_cap", score=before)
    after = maximum
    user["relationship_score"] = after
    return _append_cap_audit_entry(
        user,
        before=before,
        after=after,
        now=now,
        reason_code=reason_code,
        metadata={"cap_key": key, "cap_max": maximum},
    )


def migrate_relationship_positive_stage_cap(
    user: Any,
    *,
    old_cap_key: Any,
    new_cap_key: Any,
    now: Any = None,
) -> dict[str, Any]:
    """Lower an existing ordinary score proportionally exactly once per transition."""
    if not isinstance(user, dict):
        return _result(False, "invalid_user")
    old_key = normalize_relationship_positive_stage_cap_key(old_cap_key)
    new_key = normalize_relationship_positive_stage_cap_key(new_cap_key)
    user["relationship_positive_stage_cap_key"] = new_key
    old_max = relationship_positive_score_cap(old_key)
    new_max = relationship_positive_score_cap(new_key)
    # Configuration elevation begins a new future lowering generation even for
    # a currently non-positive record.  Otherwise its old marker would
    # incorrectly suppress a later real lowering once the record turns
    # positive again.
    if new_max > old_max:
        user.pop("relationship_positive_stage_cap_migration", None)
        return _result(False, "positive_stage_cap_not_lowered", score=_score(user.get("relationship_score")))
    if is_owner_exclusive(user):
        return _result(False, "owner_exclusive_exempt", score=_score(user.get("relationship_score")))
    # The primary user is exempt from the ordinary-user ceiling, so its score
    # is never lowered here; skipping without a marker keeps a later real
    # lowering (after the owner role is dropped) eligible.
    if is_owner_role(user):
        return _result(False, "owner_role_exempt", score=_score(user.get("relationship_score")))
    before = _score(user.get("relationship_score"))
    if before <= 0:
        return _result(False, "non_positive_score_exempt", score=before)
    if new_max >= old_max:
        return _result(False, "positive_stage_cap_not_lowered", score=before)
    transition = f"{old_key}->{new_key}"
    marker = user.get("relationship_positive_stage_cap_migration")
    if isinstance(marker, dict) and marker.get("transition") == transition:
        return _result(False, "positive_stage_cap_already_migrated", score=before)
    after = min(new_max, int(round(before / old_max * new_max)))
    user["relationship_positive_stage_cap_migration"] = {
        "transition": transition,
        "old_cap_key": old_key,
        "new_cap_key": new_key,
        "old_cap_max": old_max,
        "new_cap_max": new_max,
        "migrated_at": _timestamp(now),
    }
    if after >= before:
        return _result(False, "positive_stage_cap_no_score_drop", score=before)
    user["relationship_score"] = after
    result = _append_cap_audit_entry(
        user,
        before=before,
        after=after,
        now=now,
        reason_code="relationship_positive_stage_cap_migration",
        metadata={
            "old_cap_key": old_key,
            "new_cap_key": new_key,
            "old_cap_max": old_max,
            "new_cap_max": new_max,
            "transition": transition,
        },
    )
    result["code"] = "positive_stage_cap_migrated"
    return result


def apply_natural_relationship_decay(
    user: Any,
    *,
    now: Any = None,
    policy: Any = None,
    grace_days: int = DEFAULT_DECAY_GRACE_DAYS,
    early_rate: int = 2,
    middle_rate: int = 5,
    late_rate: int = 8,
    timezone_name: Any = None,
) -> dict[str, Any]:
    """Idempotently decay positive scores toward zero by inactive calendar day."""
    if not isinstance(user, dict):
        return _result(False, "invalid_user")
    ts = _timestamp(now)
    if is_owner_exclusive(user):
        return _result(False, "owner_exclusive_frozen", score=_score(user.get("relationship_score")))
    score = _score(user.get("relationship_score"))
    if score <= 0:
        return _result(False, "non_positive_score", score=score)
    last_effective = _finite_number(user.get("relationship_last_effective_at"))
    if not last_effective:
        last_effective = _finite_number(user.get("last_activity_at")) or _finite_number(user.get("last_seen"))
    if not last_effective or ts <= last_effective:
        return _result(False, "no_inactive_window", score=score)

    today = _calendar_date(ts, timezone_name)
    last_day = _calendar_date(last_effective, timezone_name)
    inactive_days = max(0, (today - last_day).days)
    grace = max(0, min(30, int(grace_days or 0)))
    if inactive_days <= grace:
        return _result(False, "within_grace", score=score)
    settled_text = str(user.get("relationship_decay_settled_day") or "").strip()
    try:
        settled_day = datetime.fromisoformat(settled_text).date() if settled_text else last_day
    except ValueError:
        settled_day = last_day
    start_offset = max(grace + 1, (settled_day - last_day).days + 1)
    if start_offset > inactive_days:
        return _result(False, "already_settled", score=score)

    rates = (
        max(0, min(30, int(early_rate or 0))),
        max(0, min(30, int(middle_rate or 0))),
        max(0, min(30, int(late_rate or 0))),
    )
    requested = sum(_decay_for_inactive_day(day, rates=rates) for day in range(start_offset, inactive_days + 1))
    if requested <= 0:
        user["relationship_decay_settled_day"] = today.isoformat()
        return _result(False, "no_decay", score=score)
    before_projection = relationship_stage_for_score(score, policy)
    candidate = max(0, score - requested)
    candidate = _apply_stage_hysteresis(user, score, candidate, ts, policy, before_projection)
    applied = score - candidate
    user["relationship_decay_settled_day"] = today.isoformat()
    if applied <= 0:
        return _result(False, "stage_hysteresis", score=score)
    user["relationship_score"] = candidate
    ledger = user.setdefault("relationship_ledger", [])
    if not isinstance(ledger, list):
        ledger = []
        user["relationship_ledger"] = ledger
    entry = {
        "event_key": f"natural_decay:{today.isoformat()}",
        "reason_code": "natural_decay",
        "delta": -applied,
        "score_before": score,
        "score_after": candidate,
        "created_at": ts,
        "inactive_days": inactive_days,
    }
    ledger.append(entry)
    if len(ledger) > 200:
        del ledger[:-200]
    return _result(True, "applied", score=candidate, delta=-applied, entry=deepcopy(entry))


def relationship_ledger_summary(user: Any, *, limit: int = 12) -> dict[str, Any]:
    if not isinstance(user, dict):
        return {"items": [], "trend": "unknown"}
    ledger = user.get("relationship_ledger")
    items = [deepcopy(item) for item in ledger if isinstance(item, dict)] if isinstance(ledger, list) else []
    recent = items[-max(1, min(50, int(limit or 12))):]
    total = sum(_integer(item.get("delta")) or 0 for item in recent)
    trend = "rising" if total > 0 else "cooling" if total < 0 else "steady"
    return {"items": recent, "trend": trend, "recent_delta": total}


def record_manual_relationship_change(
    user: Any,
    before: Any,
    after: Any,
    *,
    now: Any = None,
    reason_code: Any = "administrator_manual",
) -> dict[str, Any]:
    """Record an already-authorized exact admin adjustment without re-settling it."""
    if not isinstance(user, dict):
        return _result(False, "invalid_user")
    before_score = _score(before)
    after_score = _score(after)
    if before_score == after_score:
        return _result(False, "unchanged", score=after_score)
    ts = _timestamp(now)
    reason = _reason(reason_code) or "administrator_manual"
    ledger = user.setdefault("relationship_ledger", [])
    if not isinstance(ledger, list):
        ledger = []
        user["relationship_ledger"] = ledger
    entry = {
        "event_key": _event_key(reason, ts, f"manual:{ts}:{before_score}:{after_score}", 60),
        "reason_code": reason,
        "delta": after_score - before_score,
        "score_before": before_score,
        "score_after": after_score,
        "created_at": ts,
        "source": "administrator",
    }
    ledger.append(entry)
    if len(ledger) > 200:
        del ledger[:-200]
    user["relationship_last_effective_at"] = ts
    user["relationship_decay_settled_day"] = ""
    return _result(True, "recorded", score=after_score, delta=after_score - before_score, entry=deepcopy(entry))


def _append_cap_audit_entry(
    user: dict[str, Any],
    *,
    before: int,
    after: int,
    now: Any,
    reason_code: Any,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    ts = _timestamp(now)
    reason = _reason(reason_code) or "relationship_positive_stage_cap_runtime_clamp"
    ledger = user.setdefault("relationship_ledger", [])
    if not isinstance(ledger, list):
        ledger = []
        user["relationship_ledger"] = ledger
    entry = {
        "event_key": _event_key(reason, ts, f"{reason}:{before}:{after}:{metadata}", 60),
        "reason_code": reason,
        "delta": after - before,
        "score_before": before,
        "score_after": after,
        "created_at": ts,
        "source": "relationship_cap",
        **metadata,
    }
    ledger.append(entry)
    if len(ledger) > 200:
        del ledger[:-200]
    return _result(True, reason, score=after, delta=after - before, entry=deepcopy(entry))


def _apply_stage_hysteresis(user: dict[str, Any], before: int, candidate: int, ts: float, policy: Any, before_projection: dict[str, Any]) -> int:
    after_projection = relationship_stage_for_score(candidate, policy)
    before_index = int(before_projection.get("stage_index") or 0)
    after_index = int(after_projection.get("stage_index") or 0)
    if after_index >= before_index:
        return candidate
    stages = normalize_relationship_stage_policy(policy)
    last_drop = _finite_number(user.get("relationship_last_decay_stage_drop_at")) or 0.0
    if last_drop and ts - last_drop < 7 * 86400:
        current_min = int(stages[before_index]["min"])
        return max(candidate, current_min)
    immediate_index = max(0, before_index - 1)
    limited = max(candidate, int(stages[immediate_index]["min"]))
    if relationship_stage_for_score(limited, policy).get("stage_index") < before_index:
        user["relationship_last_decay_stage_drop_at"] = ts
    return limited


def _decay_for_inactive_day(day: int, *, rates: tuple[int, int, int] = (2, 5, 8)) -> int:
    configured = ((7, rates[0]), (14, rates[1]), (10**9, rates[2]))
    for maximum, amount in configured:
        if day <= maximum:
            return amount
    return rates[2]


def _event_key(reason: str, ts: float, event_id: Any, window_seconds: int) -> str:
    explicit = str(event_id or "").strip()
    canonical_reason = _EVENT_REASON_ALIASES.get(reason, reason)
    source = (
        f"{explicit[:160]}:{canonical_reason}"
        if explicit
        else f"{canonical_reason}:{ts:.6f}"
    )
    return sha256(source.encode("utf-8", errors="ignore")).hexdigest()[:24]


def _calendar_date(ts: float, timezone_name: Any = None):
    name = str(timezone_name or "").strip()
    try:
        tz = ZoneInfo(name) if name else timezone.utc
    except (KeyError, ValueError):
        tz = timezone.utc
    return datetime.fromtimestamp(ts, tz=tz).date()


def _reason(value: Any) -> str:
    text = "_".join(str(value or "").strip().lower().split())
    return "".join(ch for ch in text if ch.isalnum() or ch in {"_", "-"})[:48]


def _score(value: Any) -> int:
    parsed = _integer(value)
    return max(RELATIONSHIP_SCORE_MIN, min(RELATIONSHIP_SCORE_MAX, parsed if parsed is not None else 0))


def _integer(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and isfinite(value):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if isfinite(number) else None


def _timestamp(value: Any) -> float:
    number = _finite_number(value)
    return number if number is not None and number > 0 else datetime.now(tz=timezone.utc).timestamp()


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    parsed = _integer(value)
    return max(minimum, min(maximum, parsed if parsed is not None else default))


def _result(changed: bool, code: str, *, score: int | None = None, delta: int = 0, entry: dict[str, Any] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"changed": bool(changed), "code": code, "delta": int(delta)}
    if score is not None:
        result["score"] = int(score)
    if entry is not None:
        result["entry"] = entry
    return result


__all__ = [
    "OWNER_EXCLUSIVE_MODE",
    "NORMAL_RELATIONSHIP_MODE",
    "RELATIONSHIP_SCORE_SCHEMA_VERSION",
    "apply_natural_relationship_decay",
    "apply_relationship_event",
    "clamp_relationship_positive_stage_cap",
    "is_owner_exclusive",
    "legacy_relationship_score_to_v2",
    "migrate_legacy_relationship_score",
    "migrate_relationship_score_schema",
    "migrate_relationship_positive_stage_cap",
    "normalize_relationship_mode",
    "normalize_relationship_positive_stage_cap_key",
    "record_manual_relationship_change",
    "relationship_positive_score_cap",
    "relationship_ledger_summary",
]
