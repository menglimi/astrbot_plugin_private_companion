"""Deterministic group-moment (名场面) candidate extraction and storage.

The persona occasionally recalls memorable group moments.  To avoid trusting a
black-box model on every message, this contract first extracts candidates with
cheap, deterministic rules (replies, @-bursts, keyword sparks), then lets the
runtime adapter optionally refine the shortlist with an LLM before persisting.
The contract owns no persistence, clock, network, or platform access; adapters
pass sanitised message snapshots and consume structured, bounded moment facts.
"""

from __future__ import annotations

import hashlib
import math
import re
import time
from typing import Any, Iterable, Mapping

GROUP_MOMENTS_VERSION = "group_moments.v1"

# Keyword sparks that make a recent message a plausible 名场面.
_SPARK_PATTERNS = (
    r"修罗场",
    r"血流成河",
    r"没绷住",
    r"笑死",
    r"经典",
    r"名场面",
    r"这波",
    r"绷不住了",
    r"这谁顶得住",
    r"救命",
    r"好活",
    r"逆天",
    r"公开处刑",
    r"社死",
)

_DEFAULT_MAX_CANDIDATES = 12
_DEFAULT_MAX_STORED = 20
_DEFAULT_WINDOW_SECONDS = 30.0
_DEFAULT_MOMENT_TTL = 7 * 24 * 3600.0  # one week


def _finite(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return default
    return numeric if math.isfinite(numeric) else default


def _text(message: Any) -> str:
    if isinstance(message, str):
        return message
    if isinstance(message, Mapping):
        text = message.get("text") or message.get("content") or message.get("message")
        if text is not None:
            return str(text)
    return ""


def _sender(message: Any) -> str:
    if isinstance(message, Mapping):
        sender_id = message.get("sender_id") or message.get("user_id") or ""
        name = message.get("name") or message.get("sender_name") or ""
        return str(sender_id or name or "")
    return ""


def _stable_text_key(text: str) -> str:
    """Deterministic hash key so dedup survives process restarts."""
    return hashlib.md5(text.strip().encode("utf-8")).hexdigest()[:16]


def _ts(message: Any, default: float = 0.0) -> float:
    if isinstance(message, Mapping):
        value = message.get("ts") or message.get("timestamp")
        if value is not None:
            return _finite(value, default)
    return default


def _is_reply(message: Any) -> bool:
    if isinstance(message, Mapping):
        if message.get("reply_to_id") or message.get("reply_id"):
            return True
        text = _text(message)
        return bool(re.search(r"^@\S+[\s:：]", text))
    return False


def _has_spark(text: str) -> bool:
    lowered = text.lower()
    return any(re.search(pattern, lowered) for pattern in _SPARK_PATTERNS)


def extract_group_moment_candidates(
    messages: Iterable[Any],
    *,
    now: Any = None,
    window_seconds: float = _DEFAULT_WINDOW_SECONDS,
    max_candidates: int = _DEFAULT_MAX_CANDIDATES,
) -> list[dict[str, Any]]:
    """Rule-based coarse filter of candidate moments from a message batch.

    A candidate requires at least one of: a quoted reply, a spark keyword, or
    a same-window multi-sender burst.  The list is ordered by a heuristic
    "signal score" so the LLM refiner (or caller) can take the top few.
    """
    current_ts = max(0.0, _finite(now, time.time()))
    items = [item for item in messages if _text(item)]
    if not items:
        return []
    window = max(1.0, _finite(window_seconds, _DEFAULT_WINDOW_SECONDS))
    candidates: list[dict[str, Any]] = []
    index = 0
    while index < len(items):
        item = items[index]
        text = _text(item)
        score = 0.0
        reasons: list[str] = []
        if _is_reply(item):
            score += 1.0
            reasons.append("reply")
        if _has_spark(text):
            score += 2.0
            reasons.append("spark")
        # Burst: look ahead for a tight cluster of different senders.
        cluster = [item]
        if score > 0:
            j = index + 1
            seen = {_sender(item)}
            cluster_start = _ts(item, current_ts)
            while j < len(items) and len(cluster) < 8:
                nxt = items[j]
                if current_ts and cluster_start and (_ts(nxt, current_ts) - cluster_start) <= window:
                    cluster.append(nxt)
                    if _sender(nxt):
                        seen.add(_sender(nxt))
                    j += 1
                else:
                    break
            if len(seen) >= 3:
                score += 1.0
                reasons.append("burst")
        if score >= 1.0:
            candidates.append({
                "index": index,
                "sender": _sender(item),
                "text": _text(item)[:160],
                "ts": _ts(item, current_ts),
                "score": round(score, 2),
                "reasons": reasons,
                "cluster_size": len(cluster),
            })
        index += 1
    candidates.sort(key=lambda entry: _finite(entry.get("score")), reverse=True)
    return candidates[:max(1, min(50, max_candidates))]


def refine_group_moments_candidates(
    candidates: Iterable[Mapping[str, Any]],
    *,
    llm_refiner: Any = None,
    max_refined: int = 5,
) -> list[dict[str, Any]]:
    """Optional LLM refinement over rule-filtered candidates.

    When ``llm_refiner`` is callable, it is invoked once with the candidate
    list and must return a list of selected candidate dicts (or a JSON list of
    dicts).  Otherwise the top ``max_refined`` candidates are returned
    unchanged, keeping the contract testable without a model.
    """
    pool = [dict(item) for item in candidates if isinstance(item, Mapping) and _text(item)]
    pool.sort(key=lambda entry: _finite(entry.get("score")), reverse=True)
    if llm_refiner is None or not callable(llm_refiner):
        return pool[:max(1, min(50, max_refined))]
    try:
        result = llm_refiner(pool[:max(1, min(50, max_refined))])
        if isinstance(result, str):
            import json
            result = json.loads(result)
        if isinstance(result, list):
            return [dict(item) for item in result if isinstance(item, Mapping)][:max(1, min(50, max_refined))]
    except Exception:
        pass
    return pool[:max(1, min(50, max_refined))]


def settle_group_moments(
    existing: Any,
    *,
    candidates: Iterable[Mapping[str, Any]],
    now: Any = None,
    max_stored: int = _DEFAULT_MAX_STORED,
    ttl: float = _DEFAULT_MOMENT_TTL,
) -> dict[str, Any]:
    """Merge refined candidates into the stored moment list.

    Each candidate is normalised (deduped by text hash), stamped, and given an
    ``expires_at``.  Expired and overflowing entries are dropped.
    """
    current_ts = max(0.0, _finite(now, time.time()))
    ttl = max(60.0, _finite(ttl, _DEFAULT_MOMENT_TTL))
    stored: dict[str, dict[str, Any]] = {}
    old = existing.get("moments") if isinstance(existing, Mapping) else None
    if isinstance(old, list):
        for entry in old:
            if not isinstance(entry, Mapping):
                continue
            key = str(entry.get("hash") or "")
            if not key:
                continue
            expires = _finite(entry.get("expires_at"))
            if expires and expires < current_ts:
                continue
            stored[key] = dict(entry)
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            continue
        text = _text(candidate)
        if not text:
            continue
        key = _stable_text_key(text)
        normalized = {
            "hash": key,
            "text": text[:200],
            "sender": _sender(candidate) or str(candidate.get("sender") or ""),
            "ts": _ts(candidate, current_ts),
            "created_at": current_ts,
            "expires_at": current_ts + ttl,
            "score": _finite(candidate.get("score"), 0.0),
            "reasons": candidate.get("reasons") if isinstance(candidate.get("reasons"), list) else [],
        }
        previous = stored.get(key)
        if previous is not None:
            # Re-scanning the live window must not refresh TTL indefinitely.
            normalized["created_at"] = previous.get("created_at", normalized["created_at"])
            normalized["expires_at"] = previous.get("expires_at", normalized["expires_at"])
            normalized["score"] = max(
                _finite(previous.get("score"), 0.0),
                normalized["score"],
            )
        stored[key] = normalized
    moments = sorted(stored.values(), key=lambda entry: _finite(entry.get("ts")), reverse=True)
    moments = moments[:max(1, min(200, max_stored))]
    return {
        "version": GROUP_MOMENTS_VERSION,
        "moments": moments,
        "updated_at": current_ts,
    }


def select_group_moments_for_prompt(
    value: Any,
    *,
    now: Any = None,
    limit: int = 3,
) -> list[dict[str, Any]]:
    """Return active, bounded moment facts for the group prompt adapter."""
    current_ts = max(0.0, _finite(now, time.time()))
    moments = value.get("moments") if isinstance(value, Mapping) else None
    if not isinstance(moments, list):
        return []
    active = [
        entry for entry in moments
        if isinstance(entry, Mapping)
        and _text(entry)
        and (not _finite(entry.get("expires_at")) or _finite(entry.get("expires_at")) >= current_ts)
    ]
    active.sort(key=lambda entry: _finite(entry.get("ts")), reverse=True)
    active = active[:max(1, min(10, limit))]
    if not active:
        return []
    return [
        {
            "sender": str(entry.get("sender") or "群友"),
            "text": _text(entry)[:90],
            "ts": _finite(entry.get("ts")),
        }
        for entry in active
    ]


# These dimensions describe provisional source-group evidence, not a profile.
_MOMENT_PORTRAIT_SPARK_DIMENSION = "communication_preference"
GROUP_MOMENTS_PORTRAIT_DIMENSIONS = ("communication_preference", "boundary")
_MOMENT_PORTRAIT_BOUNDARY_MARKERS = (
    "别拿我开玩笑",
    "不许乱说",
    "别造谣",
    "过分了",
    "别太过分",
    "生气了",
    "真敢说",
)


def extract_moment_portrait_candidates(
    value: Any,
    *,
    now: Any = None,
    min_score: float = 1.0,
    max_candidates: int = 8,
) -> list[dict[str, Any]]:
    """Extract provisional speaker evidence from unexpired group moments."""
    current_ts = max(0.0, _finite(now, time.time()))
    moments = value.get("moments") if isinstance(value, Mapping) else None
    if not isinstance(moments, list):
        return []
    active = [
        entry for entry in moments
        if isinstance(entry, Mapping)
        and _text(entry)
        and _finite(entry.get("score"), 0.0) >= min_score
        and (not _finite(entry.get("expires_at")) or _finite(entry.get("expires_at")) >= current_ts)
    ]
    if not active:
        return []
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for entry in active:
        sender = str(entry.get("sender") or "").strip()
        text = _text(entry)
        if not sender or not text:
            continue
        dimension = ""
        claim = ""
        lower = text.lower()
        spark_hits = [pattern for pattern in _SPARK_PATTERNS if pattern and pattern in text]
        boundary_hits = [marker for marker in _MOMENT_PORTRAIT_BOUNDARY_MARKERS if marker in lower]
        if boundary_hits:
            dimension = "boundary"
            claim = f"本群互动中出现过可能的不适表达：{text[:120]}。请结合原话判断是否在认真表达边界。"
        elif spark_hits:
            dimension = _MOMENT_PORTRAIT_SPARK_DIMENSION
            claim = f"本群互动中曾使用“{'/'.join(spark_hits[:3])}”这类表达；单次出现不代表长期偏好。"
        if not dimension or not claim:
            continue
        key = (sender, dimension)
        if key in seen:
            continue
        seen.add(key)
        candidates.append({
            "sender": sender,
            "dimension": dimension,
            "claim": claim,
            "evidence_text": text[:200],
            "ts": _finite(entry.get("ts"), current_ts),
            "score": round(_finite(entry.get("score"), 0.0), 2),
            "reasons": [str(item) for item in (entry.get("reasons") or []) if str(item)][:6],
        })
    candidates.sort(key=lambda item: _finite(item.get("score"), 0.0), reverse=True)
    return candidates[:max(1, min(50, max_candidates))]


__all__ = [
    "GROUP_MOMENTS_VERSION",
    "GROUP_MOMENTS_PORTRAIT_DIMENSIONS",
    "extract_group_moment_candidates",
    "refine_group_moments_candidates",
    "settle_group_moments",
    "select_group_moments_for_prompt",
    "extract_moment_portrait_candidates",
]
