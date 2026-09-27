"""Companion-owned Unified Person registry for the chat-side plugin.

This module is deliberately a small boundary around ``person_context_contract``:
the companion may create and link identities, while consumers only read the
contract projection.  Group overlays are scoped records and never become
profile facts.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import threading
import re
from typing import Any

try:
    from .person_context_contract import (
        build_identity_key,
        build_person_projection,
        ensure_person_store,
        person_id_for_identity,
        resolve_identity,
        validate_projection,
    )
    from .p4_affinity_confinement import validate_runtime_state
    from .identity_namespace import AssurancePolicy, NamespaceContext
except ImportError:
    from person_context_contract import (
        build_identity_key,
        build_person_projection,
        ensure_person_store,
        person_id_for_identity,
        resolve_identity,
        validate_projection,
    )
    from p4_affinity_confinement import validate_runtime_state
    from identity_namespace import AssurancePolicy, NamespaceContext


_LOCK = threading.RLock()
_FORBIDDEN = {
    "raw_prompt", "prompt", "private_object", "private_object_ref", "object",
    "chat_text", "chat_history", "conversation", "conversation_text", "content",
    "evidence_body", "evidence_text", "message_history", "message_text", "messages",
    "raw_content", "transcript", "database",
}
_CONTROL_CHARACTER_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_KEY_SEPARATOR_RE = re.compile(r"[\s\-./:]+")
_IDENTITY_FIELDS = (
    "companion_instance_id", "bot_account_id", "adapter_instance_id",
    "subject_namespace", "platform_subject_id",
)
_IDENTITY_ASSURANCE_RANK = {
    "unverified": 0,
    "observed": 1,
    "verified": 2,
    "explicit_linked": 3,
}
_P4_EFFECT_VERSION = 1
_P4_EFFECT_ALLOWED_FIELDS = frozenset({
    "event_id", "occurred_at", "kind", "source_kind", "target_kind", "authority",
    "reason_code", "safe_reference", "safe_hash", "status", "shadow_only",
})
_P4_EFFECT_FORBIDDEN_FIELDS = frozenset({
    "raw_prompt", "prompt", "text", "content", "chat_text", "messages", "transcript",
    "private_object", "private_object_ref", "database", "db", "score", "penalty",
    "confinement_state", "confinement_until", "authorized", "owner",
})
_P4_EFFECT_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,79}\Z")
_P4_EFFECT_TIMESTAMP_RE = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-9]{2})\Z"
)
PERSON_PURGE_RETENTION_SECONDS = 7 * 24 * 60 * 60
_PROFILE_FACT_FIELDS = frozenset({
    "display_name", "preferred_address", "style", "profile_origin", "auto_profile_created",
})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe(value: Any, depth: int = 0) -> Any:
    """Copy only bounded JSON-like values and drop context-bearing fields."""
    if depth > 2:
        return None
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return " ".join(_CONTROL_CHARACTER_RE.sub(" ", value).split())[:240]
    if isinstance(value, (list, tuple)):
        result = []
        for item in list(value)[:16]:
            safe = _safe(item, depth + 1)
            if safe not in (None, "", [], {}):
                result.append(safe)
        return result
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in list(value.items())[:32]:
            if not isinstance(key, str) or _CONTROL_CHARACTER_RE.search(key):
                continue
            name = _KEY_SEPARATOR_RE.sub("_", key.strip().lower()).strip("_")
            if not name or name in _FORBIDDEN:
                continue
            safe = _safe(item, depth + 1)
            if safe is not None:
                result[name[:80]] = safe
        return result
    return None


def _text(value: Any, field: str, limit: int = 200) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field}_invalid")
    value = value.strip()
    if not value or _CONTROL_CHARACTER_RE.search(value) or len(value) > limit:
        raise ValueError(f"{field}_invalid")
    return value


def _operation_id(value: Any) -> str:
    if value in (None, ""):
        return ""
    return _text(value, "operation_id", 120)


def _safe_affinity_score(value: Any) -> int:
    """Normalize optional profile affinity without letting malformed input abort creation."""
    if isinstance(value, bool):
        return 0
    try:
        score = int(float(value)) if value not in (None, "") else 0
    except (TypeError, ValueError, OverflowError):
        return 0
    return max(-1200, min(1200, score))


def _identity(identity: Any) -> dict[str, str]:
    if not isinstance(identity, dict):
        raise ValueError("identity_invalid")
    # build_identity_key is the contract authority; this explicit check also
    # prevents accidental partial identity records from being persisted.
    normalized = {field: _text(identity.get(field), field) for field in _IDENTITY_FIELDS}
    normalized["subject_namespace"] = normalized["subject_namespace"].lower()
    build_identity_key(normalized)
    return normalized


def _root(store: dict[str, Any]) -> dict[str, Any]:
    ensure_person_store(store)
    root = store["unified_person"]
    if not isinstance(root.get("profiles"), dict):
        root["profiles"] = {}
    if not isinstance(root.get("identity_links"), dict):
        root["identity_links"] = {}
    if not isinstance(root.get("group_overlays"), dict):
        root["group_overlays"] = {}
    if not isinstance(root.get("audit_events"), list):
        root["audit_events"] = []
    if not isinstance(root.get("operations"), dict):
        root["operations"] = {}
    if not isinstance(root.get("binding_checkpoints"), dict):
        root["binding_checkpoints"] = {}
    if not isinstance(root.get("detached_identity_links"), dict):
        root["detached_identity_links"] = {}
    if not isinstance(root.get("person_tombstones"), dict):
        root["person_tombstones"] = {}
    if not isinstance(root.get("identity_tombstones"), dict):
        root["identity_tombstones"] = {}
    return root


def _fingerprint(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _timestamp(value: Any) -> float:
    try:
        text = _text(value, "timestamp", 80).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except (TypeError, ValueError, OverflowError):
        return -1.0


def _contains_exact_value(value: Any, expected: str, *, depth: int = 0) -> bool:
    if depth > 6:
        return False
    if isinstance(value, str):
        return value == expected
    if isinstance(value, dict):
        return any(_contains_exact_value(item, expected, depth=depth + 1) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_exact_value(item, expected, depth=depth + 1) for item in value)
    return False


def _person_identity_assurance(root: dict[str, Any], person_id: str) -> str:
    """Derive profile assurance from the person's remaining active links."""
    assurances: list[str] = []
    for link in root["identity_links"].values():
        if not isinstance(link, dict) or link.get("person_id") != person_id or link.get("status") != "active":
            continue
        assurance = link.get("identity_assurance")
        assurances.append(
            assurance
            if isinstance(assurance, str) and assurance in _IDENTITY_ASSURANCE_RANK
            else "observed"
        )
    return max(assurances, key=_IDENTITY_ASSURANCE_RANK.__getitem__) if assurances else "unverified"


def _contains_forbidden_key(value: Any) -> bool:
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str or key.strip().lower() in _P4_EFFECT_FORBIDDEN_FIELDS:
                return True
            if _contains_forbidden_key(item):
                return True
        return False
    if type(value) in (list, tuple):
        return any(_contains_forbidden_key(item) for item in value)
    return False


def _p4_effect_container(root: dict[str, Any]) -> dict[str, Any] | None:
    """Return the separate preparation ledger, without repairing corruption."""
    existing = root.get("p4_effect")
    if existing is None:
        existing = {"version": _P4_EFFECT_VERSION, "people": {}, "operations": {}}
        root["p4_effect"] = existing
    if not isinstance(existing, dict):
        return None
    if existing.get("version") != _P4_EFFECT_VERSION:
        return None
    if not isinstance(existing.get("people"), dict) or not isinstance(existing.get("operations"), dict):
        return None
    return existing


def _normalize_p4_effect_event(event: Any) -> tuple[dict[str, Any] | None, str]:
    if type(event) is not dict or _contains_forbidden_key(event):
        return None, "invalid_p4_effect_event"
    if any(type(key) is not str or key not in _P4_EFFECT_ALLOWED_FIELDS for key in event):
        return None, "invalid_p4_effect_event"
    if type(event.get("event_id")) is not str or _P4_EFFECT_TOKEN_RE.fullmatch(event["event_id"]) is None:
        return None, "invalid_p4_effect_event"
    occurred_at = event.get("occurred_at")
    if type(occurred_at) is not str or _P4_EFFECT_TIMESTAMP_RE.fullmatch(occurred_at) is None:
        return None, "invalid_p4_effect_event"
    try:
        parsed_occurred_at = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
    except (TypeError, ValueError, OverflowError):
        return None, "invalid_p4_effect_event"
    if parsed_occurred_at.tzinfo is None or parsed_occurred_at.utcoffset() is None:
        return None, "invalid_p4_effect_event"
    if type(event.get("kind")) is not str or _P4_EFFECT_TOKEN_RE.fullmatch(event["kind"]) is None:
        return None, "invalid_p4_effect_event"
    normalized: dict[str, Any] = {
        "event_id": event["event_id"],
        "occurred_at": occurred_at,
        "kind": event["kind"],
    }
    for field in ("source_kind", "target_kind", "authority", "reason_code", "safe_reference"):
        if field not in event:
            continue
        value = event[field]
        if type(value) is type(None):
            continue
        if type(value) is not str:
            return None, "invalid_p4_effect_event"
        if value == "":
            continue
        if _P4_EFFECT_TOKEN_RE.fullmatch(value) is None:
            return None, "invalid_p4_effect_event"
        normalized[field] = value
    if "safe_hash" in event:
        safe_hash = event["safe_hash"]
        if type(safe_hash) is not type(None):
            if type(safe_hash) is not str:
                return None, "invalid_p4_effect_event"
            if safe_hash and re.fullmatch(r"sha256:[0-9a-f]{64}", safe_hash) is None:
                return None, "invalid_p4_effect_event"
            if safe_hash:
                normalized["safe_hash"] = safe_hash
    if "status" in event:
        status = event.get("status")
        if type(status) is not str or status not in {"shadow", "invalid", "degraded"}:
            return None, "invalid_p4_effect_event"
        normalized["status"] = status
    if "shadow_only" in event:
        if event.get("shadow_only") is not True:
            return None, "invalid_p4_effect_event"
        normalized["shadow_only"] = True
    return normalized, ""


def _p4_effect_fingerprint(person_id: str, event: dict[str, Any]) -> str:
    return _fingerprint({"person_id": person_id, "event": event})


def _p4_effect_state(events: list[dict[str, Any]]) -> dict[str, Any]:
    last = events[-1] if events else {}
    return {
        "mode": "effect_preparation",
        "event_count": len(events),
        "last_event_id": str(last.get("event_id") or ""),
        "last_kind": str(last.get("kind") or ""),
    }


def _p4_effect_summary(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "mode": "effect_preparation",
        "event_count": max(0, min(512, int(state.get("event_count") or 0))),
        "last_kind": str(state.get("last_kind") or "")[:80],
    }


def _replay_p4_effect_entry(entry: Any, person_id: str) -> tuple[dict[str, Any] | None, dict[str, dict[str, Any]] | None, str]:
    if type(entry) is not dict or entry.get("person_id") != person_id:
        return None, None, "p4_effect_person_conflict"
    events = entry.get("events")
    if type(events) is not list or len(events) > 512:
        return None, None, "p4_effect_corrupt"
    normalized_events: list[dict[str, Any]] = []
    event_index: dict[str, dict[str, Any]] = {}
    for envelope in events:
        if type(envelope) is not dict or set(envelope) != {"event_id", "person_id", "origin_person_id", "event", "event_fingerprint", "recorded_at", "operation_id"}:
            return None, None, "p4_effect_corrupt"
        if envelope.get("person_id") != person_id or type(envelope.get("origin_person_id")) is not str or not envelope["origin_person_id"]:
            return None, None, "p4_effect_corrupt"
        event, error = _normalize_p4_effect_event(envelope.get("event"))
        if event is None or envelope.get("event_id") != event.get("event_id"):
            return None, None, error or "p4_effect_corrupt"
        event_id = event["event_id"]
        if event_id in event_index or envelope.get("event_fingerprint") != _p4_effect_fingerprint(envelope["origin_person_id"], event):
            return None, None, "p4_effect_corrupt"
        event_index[event_id] = deepcopy(envelope)
        normalized_events.append(event)
    state = _p4_effect_state(normalized_events)
    if entry.get("state") != state:
        return None, None, "p4_effect_state_mismatch"
    return state, event_index, ""



class _unified_person_registryHostRef:
    """延迟引用宿主 unified_person_registry 模块，保证 patch 宿主全局名对全部域生效。"""

    def __getattr__(self, name: str):
        from . import unified_person_registry as _host_module

        return getattr(_host_module, name)


_unified_person_registry_host = _unified_person_registryHostRef()
