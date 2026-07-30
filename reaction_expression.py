# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import json
import os
import re
from copy import deepcopy
from typing import Any

from .helpers import _safe_float, _safe_int, _single_line


REACTION_EXPRESSION_STATE_TEMPLATE: dict[str, Any] = {
    "last_sent_at": 0.0,
    "last_intent_signature": "",
    "last_image_id": "",
    "recent_images": [],
    "recent_outcomes": [],
    "preference": {
        "score": 0,
        "positive_count": 0,
        "negative_count": 0,
        "last_feedback_at": 0.0,
    },
    "feedback_events": [],
    "feedback_target": {},
    "feedback_targets": {},
    "reservation": {},
    "pending_images": {},
    "scopes": {},
}

REACTION_EXPRESSION_RESERVATION_SECONDS = 600.0
# Keep enough sent-image history to cover the duplicate window independently
# from the number of lookup phrases configured for a single expression.
REACTION_EXPRESSION_RECENT_IMAGES_MAX = 256


def ensure_reaction_expression_state(user: dict[str, Any]) -> dict[str, Any]:
    raw = user.get("reaction_expression")
    state = raw if isinstance(raw, dict) else {}
    if state is not raw:
        user["reaction_expression"] = state
    for key, default in REACTION_EXPRESSION_STATE_TEMPLATE.items():
        invalid_container = isinstance(default, (dict, list)) and not isinstance(
            state.get(key), type(default)
        )
        if key not in state or invalid_container:
            state[key] = deepcopy(default)
    preference = state["preference"]
    for key, default in REACTION_EXPRESSION_STATE_TEMPLATE["preference"].items():
        preference.setdefault(key, default)
    return state


def reaction_expression_scope_state(
    state: dict[str, Any], scope_key: Any
) -> dict[str, Any]:
    scopes = state.get("scopes")
    if not isinstance(scopes, dict):
        scopes = {}
        state["scopes"] = scopes
    key = _single_line(scope_key, 240) or "unknown"
    raw = scopes.get(key)
    scoped = raw if isinstance(raw, dict) else {}
    if scoped is not raw:
        scopes[key] = scoped
    scoped.setdefault("last_sent_at", 0.0)
    scoped.setdefault("last_intent_signature", "")
    if not isinstance(scoped.get("reservation"), dict):
        scoped["reservation"] = {}
    return scoped


def reaction_expression_effective_probability(
    state: dict[str, Any], configured_probability: Any
) -> float:
    """Apply learned preference as a gentle bias without overriding the configured rate."""
    base = _safe_float(configured_probability, 0.2, 0.0, 1.0)
    preference = state.get("preference")
    score = (
        _safe_int(preference.get("score"), 0, -20, 20)
        if isinstance(preference, dict)
        else 0
    )
    factor = max(0.35, min(1.35, 1.0 + score * 0.06))
    return max(0.0, min(1.0, base * factor))


def _candidate_query_list(value: Any, *, limit: int) -> list[str]:
    raw_items: list[Any]
    if isinstance(value, (list, tuple, set)):
        raw_items = list(value)
    else:
        text = str(value or "").strip()
        parsed: Any = None
        if text.startswith("["):
            try:
                parsed = json.loads(text)
            except (TypeError, ValueError, json.JSONDecodeError):
                parsed = None
        raw_items = list(parsed) if isinstance(parsed, list) else re.split(r"[\n;；|]+", text)

    result: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        query = _single_line(item, 160)
        key = query.casefold()
        if not query or key in seen:
            continue
        seen.add(key)
        result.append(query)
        if len(result) >= limit:
            break
    return result


def normalize_reaction_expression_intent(
    *,
    query: Any = "",
    context: Any = "",
    purpose: Any = "",
    emotion: Any = "",
    intensity: Any = 0,
    candidate_queries: Any = "",
    candidate_limit: int = 6,
) -> dict[str, Any]:
    limit = _safe_int(candidate_limit, 6, 1, 16)
    query_text = _single_line(query, 500)
    purpose_text = _single_line(purpose, 120)
    emotion_text = _single_line(emotion, 80)
    context_text = _single_line(context, 1000)
    candidates = _candidate_query_list(candidate_queries, limit=limit)
    if query_text and query_text.casefold() not in {item.casefold() for item in candidates}:
        candidates.insert(0, query_text)
        candidates = candidates[:limit]

    provider_query = query_text or (candidates[0] if candidates else "")
    if not provider_query:
        provider_query = " ".join(part for part in (purpose_text, emotion_text, "表情反应") if part)
    provider_query = _single_line(provider_query or "适合当前语境的表情反应", 500)
    normalized_intensity = _safe_int(intensity, 0, 0, 5)
    signature_source = json.dumps(
        {
            "purpose": purpose_text.casefold(),
            "emotion": emotion_text.casefold(),
            "intensity": normalized_intensity,
            "queries": [item.casefold() for item in candidates] or [provider_query.casefold()],
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    signature = hashlib.sha256(signature_source.encode("utf-8")).hexdigest()[:24]
    return {
        "purpose": purpose_text,
        "emotion": emotion_text,
        "intensity": normalized_intensity,
        "context": context_text,
        "candidate_queries": candidates,
        "provider_query": provider_query,
        "signature": signature,
    }


def evaluate_reaction_expression_gate(
    state: dict[str, Any],
    intent: dict[str, Any],
    *,
    now: float,
    probability: float,
    cooldown_seconds: float,
    random_value: float,
) -> dict[str, Any]:
    probability = _safe_float(probability, 0.2, 0.0, 1.0)
    cooldown_seconds = _safe_float(cooldown_seconds, 180.0, 0.0, 86400.0)
    signature = _single_line(intent.get("signature"), 40)

    reservation = state.get("reservation")
    if isinstance(reservation, dict):
        reserved_at = _safe_float(reservation.get("at"), 0.0)
        if (
            reserved_at > 0
            and now - reserved_at < REACTION_EXPRESSION_RESERVATION_SECONDS
        ):
            return {"allowed": False, "reason": "in_progress", "probability": probability}

    last_sent_at = _safe_float(state.get("last_sent_at"), 0.0)
    if last_sent_at > 0 and cooldown_seconds > 0 and now - last_sent_at < cooldown_seconds:
        return {"allowed": False, "reason": "cooldown", "probability": probability}

    if (
        signature
        and signature == _single_line(state.get("last_intent_signature"), 40)
        and last_sent_at > 0
        and now - last_sent_at < max(60.0, cooldown_seconds)
    ):
        return {"allowed": False, "reason": "repeated_intent", "probability": probability}

    if probability <= 0 or (
        probability < 1.0
        and _safe_float(random_value, 1.0, 0.0, 1.0) >= probability
    ):
        return {"allowed": False, "reason": "probability", "probability": probability}
    return {"allowed": True, "reason": "allowed", "probability": probability}


def reserve_reaction_expression_intent(
    state: dict[str, Any],
    intent: dict[str, Any],
    *,
    now: float,
    reservation_token: Any = "",
) -> str:
    signature = _single_line(intent.get("signature"), 40)
    token = _single_line(reservation_token, 80)
    if not token:
        token = hashlib.sha256(
            f"{signature}:{float(now)}:{id(state)}".encode("utf-8")
        ).hexdigest()[:32]
    state["reservation"] = {
        "token": token,
        "signature": signature,
        "at": float(now),
    }
    return token


def reaction_expression_reservation_owned(
    state: dict[str, Any], reservation_token: Any
) -> bool:
    reservation = state.get("reservation")
    if not isinstance(reservation, dict):
        return False
    expected = _single_line(reservation_token, 80)
    current = _single_line(reservation.get("token"), 80)
    return bool(expected and current and expected == current)


def release_reaction_expression_reservation(
    state: dict[str, Any],
    *,
    intent_signature: str = "",
    reservation_token: str = "",
) -> None:
    reservation = state.get("reservation")
    if not isinstance(reservation, dict):
        state["reservation"] = {}
        return
    expected_token = _single_line(reservation_token, 80)
    current_token = _single_line(reservation.get("token"), 80)
    if expected_token:
        if current_token and expected_token == current_token:
            state["reservation"] = {}
        return
    expected_signature = _single_line(intent_signature, 40)
    current_signature = _single_line(reservation.get("signature"), 40)
    if (
        not expected_signature
        or not current_signature
        or expected_signature == current_signature
    ):
        state["reservation"] = {}


def reaction_expression_image_keys(image_id: Any, image_path: Any) -> list[str]:
    keys: list[str] = []
    normalized_id = _single_line(image_id, 160)
    if normalized_id:
        keys.append(f"id:{normalized_id}")
    raw_path = str(image_path or "").strip()
    if raw_path:
        normalized_path = os.path.normcase(os.path.abspath(os.path.normpath(raw_path)))
        normalized_path_key = normalized_path.replace("\\", "/").casefold()
        keys.append(f"path:{normalized_path_key}")
    return keys


def reaction_expression_image_key(image_id: Any, image_path: Any) -> str:
    keys = reaction_expression_image_keys(image_id, image_path)
    return keys[0] if keys else ""


def _normalize_image_keys(value: Any) -> list[str]:
    raw_items = value if isinstance(value, (list, tuple, set)) else [value]
    result: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        key = _single_line(item, 1000)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(key)
    return result


def reserve_reaction_expression_image(
    state: dict[str, Any],
    *,
    image_key: str = "",
    image_keys: Any = None,
    now: float,
    duplicate_window_seconds: float,
    reservation_token: Any = "",
) -> bool:
    keys = _normalize_image_keys(image_keys)
    for key in _normalize_image_keys(image_key):
        if key not in keys:
            keys.append(key)
    if not keys:
        return False
    key_set = set(keys)
    window = _safe_float(duplicate_window_seconds, 600.0, 60.0, 86400.0 * 7)
    recent_images = state.get("recent_images")
    if not isinstance(recent_images, list):
        recent_images = []
        state["recent_images"] = recent_images
    cutoff = float(now) - window
    recent_images[:] = [
        item
        for item in recent_images
        if not isinstance(item, dict)
        or _safe_float(item.get("sent_at"), 0.0) <= 0
        or _safe_float(item.get("sent_at"), 0.0) >= cutoff
    ]
    if len(recent_images) > REACTION_EXPRESSION_RECENT_IMAGES_MAX:
        recent_images[:] = recent_images[-REACTION_EXPRESSION_RECENT_IMAGES_MAX:]
    for item in recent_images:
        if not isinstance(item, dict):
            continue
        recent_keys = _normalize_image_keys(item.get("keys"))
        for recent_key in _normalize_image_keys(item.get("key")):
            if recent_key not in recent_keys:
                recent_keys.append(recent_key)
        for recent_key in reaction_expression_image_keys(
            item.get("image_id"), item.get("path")
        ):
            if recent_key not in recent_keys:
                recent_keys.append(recent_key)
        if key_set.isdisjoint(recent_keys):
            continue
        sent_at = _safe_float(item.get("sent_at"), 0.0)
        if sent_at <= 0 or now - sent_at < window:
            return False

    pending = state.get("pending_images")
    if not isinstance(pending, dict):
        pending = {}
        state["pending_images"] = pending
    for pending_key, pending_value in list(pending.items()):
        pending_at = (
            pending_value.get("at")
            if isinstance(pending_value, dict)
            else pending_value
        )
        if (
            now - _safe_float(pending_at, 0.0)
            >= REACTION_EXPRESSION_RESERVATION_SECONDS
        ):
            pending.pop(pending_key, None)
    if any(key in pending for key in keys):
        return False
    token = _single_line(reservation_token, 80)
    for key in keys:
        pending[key] = {"at": float(now), "token": token}
    return True


def release_reaction_expression_image(
    state: dict[str, Any],
    image_key: str = "",
    *,
    image_keys: Any = None,
    reservation_token: Any = "",
) -> None:
    pending = state.get("pending_images")
    if isinstance(pending, dict):
        keys = _normalize_image_keys(image_keys)
        for key in _normalize_image_keys(image_key):
            if key not in keys:
                keys.append(key)
        token = _single_line(reservation_token, 80)
        for key in keys:
            if not token:
                pending.pop(key, None)
                continue
            current = pending.get(key)
            if isinstance(current, dict) and _single_line(current.get("token"), 80):
                if _single_line(current.get("token"), 80) == token:
                    pending.pop(key, None)
            elif current is not None:
                # Older state used numeric timestamps and has no ownership token.
                pending.pop(key, None)


def append_reaction_expression_outcome(
    state: dict[str, Any],
    *,
    status: str,
    reason: str,
    intent_signature: str,
    now: float,
    candidate_limit: int,
    image_key: str = "",
    cache_hit: bool | None = None,
    latency_ms: float | None = None,
) -> None:
    outcomes = state.get("recent_outcomes")
    if not isinstance(outcomes, list):
        outcomes = []
        state["recent_outcomes"] = outcomes
    outcome = {
        "at": float(now),
        "status": _single_line(status, 32),
        "reason": _single_line(reason, 120),
        "intent_signature": _single_line(intent_signature, 40),
        "image_key": _single_line(image_key, 1000),
    }
    if cache_hit is not None:
        outcome["cache_hit"] = bool(cache_hit)
    if latency_ms is not None:
        outcome["latency_ms"] = round(
            _safe_float(latency_ms, 0.0, 0.0, 3_600_000.0), 2
        )
    outcomes.append(outcome)
    keep = max(4, _safe_int(candidate_limit, 6, 1, 16) * 2)
    del outcomes[:-keep]


def record_reaction_expression_sent(
    state: dict[str, Any],
    intent: dict[str, Any],
    *,
    image_id: Any,
    image_path: Any,
    image_key: str,
    image_keys: Any = None,
    now: float,
    candidate_limit: int,
    duplicate_window_seconds: float = 600.0,
    scope_key: str = "",
    reservation_token: str = "",
    cache_hit: bool | None = None,
    latency_ms: float | None = None,
) -> None:
    signature = _single_line(intent.get("signature"), 40)
    state["last_sent_at"] = float(now)
    state["last_intent_signature"] = signature
    state["last_image_id"] = _single_line(image_id, 160)
    normalized_image_keys = _normalize_image_keys(image_keys)
    for key in reaction_expression_image_keys(image_id, image_path):
        if key not in normalized_image_keys:
            normalized_image_keys.append(key)
    if image_key and image_key not in normalized_image_keys:
        normalized_image_keys.insert(0, image_key)
    release_reaction_expression_reservation(
        state,
        intent_signature=signature,
        reservation_token=reservation_token,
    )
    release_reaction_expression_image(
        state,
        image_key,
        image_keys=normalized_image_keys,
        reservation_token=reservation_token,
    )
    if scope_key:
        scoped = reaction_expression_scope_state(state, scope_key)
        scoped["last_sent_at"] = float(now)
        scoped["last_intent_signature"] = signature
        release_reaction_expression_reservation(
            scoped,
            intent_signature=signature,
            reservation_token=reservation_token,
        )

    recent_images = state.get("recent_images")
    if not isinstance(recent_images, list):
        recent_images = []
        state["recent_images"] = recent_images
    recent_images.append(
        {
            "key": _single_line(image_key, 1000),
            "keys": normalized_image_keys,
            "image_id": _single_line(image_id, 160),
            "path": _single_line(image_path, 1000),
            "sent_at": float(now),
            "intent_signature": signature,
        }
    )
    # Candidate-limit controls lookup breadth, not how long duplicate
    # protection remembers delivered images. Prune by the actual duplicate
    # window and use a fixed defensive cap for malformed/high-volume state.
    window = _safe_float(
        duplicate_window_seconds,
        600.0,
        60.0,
        86400.0 * 7,
    )
    cutoff = float(now) - window
    recent_images[:] = [
        item
        for item in recent_images
        if not isinstance(item, dict)
        or _safe_float(item.get("sent_at"), 0.0) <= 0
        or _safe_float(item.get("sent_at"), 0.0) >= cutoff
    ]
    if len(recent_images) > REACTION_EXPRESSION_RECENT_IMAGES_MAX:
        recent_images[:] = recent_images[-REACTION_EXPRESSION_RECENT_IMAGES_MAX:]
    feedback_target = {
        "image_key": _single_line(image_key, 1000),
        "image_id": _single_line(image_id, 160),
        "path": _single_line(image_path, 1000),
        "intent_signature": signature,
        "sent_at": float(now),
        "expires_at": float(now) + 6 * 3600,
    }
    state["feedback_target"] = feedback_target
    normalized_scope_key = _single_line(scope_key, 240)
    if normalized_scope_key:
        feedback_targets = state.get("feedback_targets")
        if not isinstance(feedback_targets, dict):
            feedback_targets = {}
            state["feedback_targets"] = feedback_targets
        feedback_targets[normalized_scope_key] = dict(feedback_target)
    append_reaction_expression_outcome(
        state,
        status="sent",
        reason="delivered",
        intent_signature=signature,
        now=now,
        candidate_limit=candidate_limit,
        image_key=image_key,
        cache_hit=cache_hit,
        latency_ms=latency_ms,
    )


_NEGATIVE_FEEDBACK_PATTERNS = (
    r"(?:别|不要|别再|不用再).{0,8}(?:发|用).{0,6}(?:表情包|反应图|这种图)",
    r"(?:刚才|上一张|那张|这个).{0,10}(?:表情包|反应图|图)?.{0,8}(?:不合适|不贴切|不喜欢|很尴尬|太尴尬|难看)",
    r"(?:表情包|反应图|这张图).{0,8}(?:不合适|不贴切|不喜欢|很尴尬|太尴尬|难看)",
)
_POSITIVE_FEEDBACK_PATTERNS = (
    r"(?:刚才|上一张|那张|这个|这张).{0,10}(?:表情包|反应图|图)?.{0,8}(?:不错|合适|贴切|喜欢|好笑|好用)",
    r"(?:表情包|反应图|这张图).{0,8}(?:不错|合适|贴切|喜欢|好笑|好用)",
)


def _reaction_expression_feedback_target(
    state: dict[str, Any], scope_key: Any = ""
) -> dict[str, Any]:
    normalized_scope_key = _single_line(scope_key, 240)
    feedback_targets = state.get("feedback_targets")
    if normalized_scope_key and isinstance(feedback_targets, dict):
        if normalized_scope_key in feedback_targets:
            target = feedback_targets.get(normalized_scope_key)
            return target if isinstance(target, dict) else {}
        # Once scoped targets exist, a missing scope must not fall back to the
        # legacy global target from another conversation. An empty mapping is
        # retained for old state files that only have feedback_target.
        if feedback_targets:
            return {}
    target = state.get("feedback_target")
    return target if isinstance(target, dict) else {}


def classify_reaction_expression_feedback(
    state: dict[str, Any], text: Any, *, now: float, scope_key: str = ""
) -> str:
    target = _reaction_expression_feedback_target(state, scope_key)
    if not isinstance(target, dict) or not target:
        return ""
    expires_at = _safe_float(target.get("expires_at"), 0.0)
    if expires_at > 0 and now > expires_at:
        return ""
    compact = re.sub(r"\s+", "", str(text or "")).casefold()
    if not compact:
        return ""
    if any(re.search(pattern, compact, flags=re.I) for pattern in _NEGATIVE_FEEDBACK_PATTERNS):
        return "negative"
    if any(re.search(pattern, compact, flags=re.I) for pattern in _POSITIVE_FEEDBACK_PATTERNS):
        return "positive"
    return ""


def record_reaction_expression_feedback(
    state: dict[str, Any],
    signal: Any,
    text: Any,
    *,
    now: float,
    event_limit: int = 12,
    scope_key: str = "",
) -> dict[str, Any]:
    normalized_signal = _single_line(signal, 16).casefold()
    if normalized_signal not in {"positive", "negative"}:
        return {}
    target = _reaction_expression_feedback_target(state, scope_key)
    if not isinstance(target, dict) or not target:
        return {}

    preference = state.get("preference")
    if not isinstance(preference, dict):
        preference = deepcopy(REACTION_EXPRESSION_STATE_TEMPLATE["preference"])
        state["preference"] = preference
    delta = 1 if normalized_signal == "positive" else -1
    preference["score"] = _safe_int(preference.get("score"), 0, -20, 20) + delta
    preference["score"] = max(-20, min(20, preference["score"]))
    count_key = "positive_count" if normalized_signal == "positive" else "negative_count"
    preference[count_key] = _safe_int(preference.get(count_key), 0, 0) + 1
    preference["last_feedback_at"] = float(now)

    events = state.get("feedback_events")
    if not isinstance(events, list):
        events = []
        state["feedback_events"] = events
    event = {
        "at": float(now),
        "signal": normalized_signal,
        "text": _single_line(text, 180),
        "image_key": _single_line(target.get("image_key"), 1000),
        "image_id": _single_line(target.get("image_id"), 160),
        "intent_signature": _single_line(target.get("intent_signature"), 40),
    }
    events.append(event)
    keep = _safe_int(event_limit, 12, 4, 32)
    del events[:-keep]
    normalized_scope_key = _single_line(scope_key, 240)
    feedback_targets = state.get("feedback_targets")
    if normalized_scope_key and isinstance(feedback_targets, dict):
        feedback_targets.pop(normalized_scope_key, None)
    legacy_target = state.get("feedback_target")
    if not normalized_scope_key or legacy_target is target or (
        isinstance(legacy_target, dict)
        and _single_line(legacy_target.get("image_key"), 1000) == event["image_key"]
        and _safe_float(legacy_target.get("sent_at"), 0.0)
        == _safe_float(target.get("sent_at"), 0.0)
    ):
        state["feedback_target"] = {}
    return {
        "signal": normalized_signal,
        "score": preference["score"],
        "image_id": event["image_id"],
        "image_key": event["image_key"],
    }
