# -*- coding: utf-8 -*-
"""Public, persistable diagnostic result DTOs.

This module deliberately has no AstrBot or plugin imports. Diagnostic results
cross the page API and the on-disk troubleshooting history, so untrusted error
text must never define the public payload.
"""
from __future__ import annotations

import math
import re
import secrets
from typing import Any
from urllib.parse import urlparse


DIAGNOSTIC_ENVELOPE_VERSION = "private_companion.diagnostic.v1"
DIAGNOSTIC_PUBLIC_FIELDS = (
    "diagnostic_version",
    "test_id",
    "duration_ms",
    "phase",
    "error_category",
    "retryable",
    "next_step",
)

_VALID_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9_-]{0,79}$")
_VALID_TEST_ID = re.compile(r"^diag_[a-z0-9_]{1,40}_(?:[a-f0-9]{12,32}|legacy)$")
_VALID_PHASES = frozenset({"completed", "failed", "scheduled", "running"})
_VALID_STEP_STATUSES = frozenset({"ok", "error", "warn", "info", "pending"})
_ISSUED_TEST_KINDS = frozenset({
    "provider",
    "image_api_endpoint",
    "image_generation",
    "image_generation_text2img",
    "image_generation_selfie",
    "tts_generation",
    "screen_peek",
    "qzone_integration",
    "proactive_message",
    "model_diagnostics",
    "skill_similarity",
    "weather_api",
    "balance_api",
    "web_search",
})
_PUBLIC_TITLES = {
    "provider": "Provider diagnostic",
    "image_api_endpoint": "Image API endpoint diagnostic",
    "image_generation": "Image generation diagnostic",
    "image_generation_text2img": "Text-to-image diagnostic",
    "image_generation_selfie": "Reference image diagnostic",
    "tts_generation": "TTS generation diagnostic",
    "screen_peek": "Screen observation diagnostic",
    "qzone_integration": "QZone diagnostic",
    "proactive_message": "Proactive message diagnostic",
    "model_diagnostics": "Model data diagnostic",
    "skill_similarity": "Skill similarity diagnostic",
    "weather_api": "Weather API diagnostic",
    "balance_api": "Balance API diagnostic",
    "web_search": "Web search diagnostic",
    "check": "Diagnostic test",
}
_PUBLIC_ERROR_LABELS = {
    "configuration": "Configuration is incomplete or incompatible.",
    "authorization": "Authentication or permission was not accepted.",
    "timeout": "The diagnostic call timed out.",
    "unavailable": "The required service is unavailable.",
    "validation": "The diagnostic target or parameters are invalid.",
    "provider": "The provider did not return a usable result.",
    "unknown": "The diagnostic did not complete.",
    "none": "",
}
_NEXT_STEPS = {
    "configuration": "Check the required model or service configuration, then test again.",
    "authorization": "Check the account authorization or credential configuration, then test again.",
    "timeout": "Check service load and network state, then retry the diagnostic.",
    "unavailable": "Confirm the related plugin, service, or provider is running, then test again.",
    "validation": "Refresh the operations page and check the selected test target.",
    "provider": "Check provider status and model configuration; use a fallback provider if needed.",
    "unknown": "Use the test id to inspect plugin logs, then retry after confirming configuration.",
    "none": "No action is needed.",
}


def diagnostic_test_id(test_kind: Any, *, token: str | None = None) -> str:
    """Create an opaque id with no user, endpoint, path, or prompt input."""
    normalized = str(test_kind or "").strip().lower()
    normalized = normalized if normalized in _ISSUED_TEST_KINDS else "check"
    suffix = str(token or secrets.token_hex(6)).lower()
    if not re.fullmatch(r"[a-f0-9]{12,32}", suffix):
        suffix = secrets.token_hex(6)
    return f"diag_{normalized}_{suffix}"


def classify_diagnostic_error(value: Any) -> str:
    text = str(value or "").lower()
    if not text:
        return "unknown"
    if any(token in text for token in ("timeout", "timed out", "超时", "等待其他", "排队")):
        return "timeout"
    if any(token in text for token in ("401", "403", "authorization", "unauthorized", "forbidden", "认证", "权限", "api key", "密钥")):
        return "authorization"
    if any(token in text for token in ("未知排障", "unknown test", "参数", "无效", "invalid", "校验")):
        return "validation"
    if any(token in text for token in ("missing", "configuration", "未配置", "缺少", "配置", "not configured")):
        return "configuration"
    if any(token in text for token in ("不可用", "未检测到", "未安装", "not available", "connection refused", "服务未启动")):
        return "unavailable"
    if any(token in text for token in ("provider", "模型", "tts", "调用失败", "generation", "rate limit", "429")):
        return "provider"
    return "unknown"


def _as_int(value: Any, default: int = 0, *, ceiling: int = 86_400_000) -> int:
    try:
        parsed = int(float(value))
    except (TypeError, ValueError):
        return default
    return min(max(0, parsed), ceiling)


def _as_number(value: Any, *, ceiling: float = 1_000_000_000_000_000.0) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed):
        return None
    return min(max(-ceiling, parsed), ceiling)


def _safe_identifier(value: Any, limit: int = 80) -> str:
    text = str(value or "").strip().lower()
    if not _VALID_IDENTIFIER.fullmatch(text):
        return ""
    return text[:limit]


def _safe_label(value: Any, limit: int = 100) -> str:
    text = " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split())
    if not text:
        return ""
    if "traceback" in text.lower() or "authorization:" in text.lower() or "cookie:" in text.lower():
        return "Internal detail is hidden."
    text = re.sub(r"(?i)(bearer\s+)[^\s,;]+", r"\1[hidden]", text)
    text = re.sub(r"(?i)(api[_ -]?key|token|secret|password)\s*[=:]\s*[^\s,;]+", r"\1=[hidden]", text)
    return text[:limit]


def _safe_url(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlparse(raw)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return ""
        port = f":{parsed.port}" if parsed.port else ""
        return _safe_label(f"{parsed.scheme}://{parsed.hostname}{port}{parsed.path or ''}", 180)
    except ValueError:
        return ""


def _safe_path(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlparse(raw)
        path = parsed.path if parsed.scheme or parsed.netloc else raw.split("?", 1)[0].split("#", 1)[0]
    except ValueError:
        return ""
    if not path.startswith("/"):
        path = f"/{path.lstrip('/')}"
    return _safe_label(path, 180)


def _safe_result_preview(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    preview: list[dict[str, str]] = []
    for raw in value[:3]:
        if not isinstance(raw, dict):
            continue
        title = _safe_label(raw.get("title"), 140)
        snippet = _safe_label(raw.get("snippet") or raw.get("summary"), 240)
        if title or snippet:
            preview.append({"title": title, "snippet": snippet})
    return preview


def _safe_steps(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    items: list[dict[str, str]] = []
    for raw in value[:12]:
        if not isinstance(raw, dict):
            continue
        status = _safe_identifier(raw.get("status"), 16)
        if status not in _VALID_STEP_STATUSES:
            status = "info"
        name = _safe_label(raw.get("name"), 40) or "Diagnostic step"
        detail = {
            "ok": "This stage completed.",
            "error": "This stage did not pass; see the next step below.",
            "warn": "This stage needs attention.",
            "pending": "This stage is still waiting.",
            "info": "This stage was recorded.",
        }[status]
        items.append({"name": name, "status": status, "detail": detail})
    return items


def _safe_test_id(value: Any, test_type: str) -> str:
    candidate = str(value or "").strip().lower()
    if _VALID_TEST_ID.fullmatch(candidate):
        return candidate
    return f"diag_{test_type or 'check'}_legacy"


def _safe_test_kind(value: Any) -> str:
    candidate = _safe_identifier(value, 40)
    return candidate if candidate in _ISSUED_TEST_KINDS else "check"


def normalize_diagnostic_result(
    result: dict[str, Any] | None,
    *,
    test_type: Any = "",
    duration_ms: Any = 0,
    test_id: Any = "",
    contract_version: Any = DIAGNOSTIC_ENVELOPE_VERSION,
) -> dict[str, Any]:
    """Project an internal result into the public, default-redacted DTO."""
    source = dict(result) if isinstance(result, dict) else {}
    kind = _safe_test_kind(test_type or source.get("type"))
    ok = bool(source.get("ok"))
    pending = bool(source.get("pending")) and not ok
    raw_error = source.get("error") or source.get("detail")
    category = "none" if (ok or pending) else classify_diagnostic_error(raw_error)
    phase = "completed" if ok else ("scheduled" if pending else "failed")
    supplied_phase = _safe_identifier(source.get("phase"), 20)
    if supplied_phase in _VALID_PHASES and ((supplied_phase == "running" and pending) or supplied_phase == phase):
        phase = supplied_phase
    retryable = category in {"timeout", "unavailable"}
    if category == "provider":
        retryable = any(token in str(raw_error or "").lower() for token in ("429", "rate limit", "临时", "network", "connection"))
    supplied_id = test_id or source.get("test_id")
    public_id = _safe_test_id(supplied_id, kind)
    if not supplied_id:
        public_id = f"diag_{kind}_legacy"
    duration = _as_int(duration_ms) or _as_int(source.get("duration_ms")) or _as_int(source.get("elapsed_ms"))
    title = _PUBLIC_TITLES[kind]
    detail = "The test completed." if ok else ("The test is scheduled or running." if pending else _PUBLIC_ERROR_LABELS[category])

    return {
        "diagnostic_version": _safe_label(contract_version, 60) or DIAGNOSTIC_ENVELOPE_VERSION,
        "test_id": public_id,
        "duration_ms": duration,
        "phase": phase,
        "error_category": category,
        "retryable": retryable,
        "next_step": _NEXT_STEPS[category],
        "type": kind,
        "test_key": _safe_identifier(source.get("test_key"), 80),
        "ok": ok,
        "pending": pending,
        "outcome_type": _safe_identifier(source.get("outcome_type"), 40),
        "title": title,
        "backend": _safe_label(source.get("backend"), 80),
        "image_model": _safe_label(source.get("image_model"), 80),
        "image_size": _safe_label(source.get("image_size"), 40),
        "endpoint_index": _as_int(source.get("endpoint_index"), -1, ceiling=10_000),
        "endpoint_name": _safe_label(source.get("endpoint_name"), 80),
        "endpoint_platform": _safe_label(source.get("endpoint_platform"), 60),
        "endpoint_url": _safe_url(source.get("endpoint_url")),
        "endpoint_status": _safe_identifier(source.get("endpoint_status"), 20),
        "endpoint_timeout_seconds": _as_int(source.get("endpoint_timeout_seconds")),
        "queue_wait_ms": _as_int(source.get("queue_wait_ms")),
        "workflow_kind": _safe_identifier(source.get("workflow_kind"), 20),
        "used_reference": bool(source.get("used_reference")),
        "reference_id": _safe_identifier(source.get("reference_id"), 60),
        "reference_kind": _safe_identifier(source.get("reference_kind"), 40),
        "reference_roles": [_safe_identifier(item, 40) for item in (source.get("reference_roles") if isinstance(source.get("reference_roles"), list) else [])[:8] if _safe_identifier(item, 40)],
        "wardrobe_mode": _safe_identifier(source.get("wardrobe_mode"), 40),
        "wardrobe_category": _safe_identifier(source.get("wardrobe_category"), 40),
        "outfit_locked": bool(source.get("outfit_locked")),
        "daily_outfit_removed": bool(source.get("daily_outfit_removed")),
        "final_presets": [_safe_identifier(item, 60) for item in (source.get("final_presets") if isinstance(source.get("final_presets"), list) else [])[:6] if _safe_identifier(item, 60)],
        "prompt_hash": _safe_identifier(source.get("prompt_hash"), 80),
        "provider": _safe_label(source.get("provider"), 100),
        "source": _safe_label(source.get("source"), 100),
        "location_label": _safe_label(source.get("location_label"), 120),
        "query_mode": _safe_identifier(source.get("query_mode"), 20),
        "source_id": _safe_label(source.get("source_id"), 100),
        "endpoint_path": _safe_path(source.get("endpoint_path")),
        "amount": _as_number(source.get("amount")),
        "total": _as_number(source.get("total")),
        "used": _as_number(source.get("used")),
        "remaining_percent": _as_number(source.get("remaining_percent"), ceiling=1_000_000.0),
        "currency_label": _safe_label(source.get("currency_label"), 20),
        "result_count": _as_int(source.get("result_count"), ceiling=10_000),
        "result_preview": _safe_result_preview(source.get("result_preview")),
        "file_size": _as_int(source.get("file_size")),
        "detail": detail,
        "error": "" if (ok or pending) else _PUBLIC_ERROR_LABELS[category],
        "timeout_seconds": _as_int(source.get("timeout_seconds")),
        "test_timeout_seconds": _as_int(source.get("test_timeout_seconds")),
        "estimated_timeout_seconds": _as_int(source.get("estimated_timeout_seconds")),
        "timeout_budget": _safe_label(source.get("timeout_budget"), 120),
        "backend_preference": _safe_identifier(source.get("backend_preference"), 30),
        "external_timeout_seconds": _as_int(source.get("external_timeout_seconds")),
        "backup_external_timeout_seconds": _as_int(source.get("backup_external_timeout_seconds")),
        "comfyui_wait_seconds": _as_int(source.get("comfyui_wait_seconds")),
        "backup_external": bool(source.get("backup_external")),
        "external_queue_lock": bool(source.get("external_queue_lock")),
        "tool_call_timeout_seconds": _as_int(source.get("tool_call_timeout_seconds")),
        "context_chars": _as_int(source.get("context_chars")),
        "action": _safe_identifier(source.get("action"), 60),
        "reason": _safe_identifier(source.get("reason"), 40),
        "extra_count": _as_int(source.get("extra_count")),
        "local_count": _as_int(source.get("local_count")),
        "model_count": _as_int(source.get("model_count")),
        "suggestion_count": _as_int(source.get("suggestion_count")),
        "steps": _safe_steps(source.get("steps")),
        "elapsed_ms": duration,
        "ran_at": float(source.get("ran_at") or 0) if str(source.get("ran_at") or "").replace(".", "", 1).isdigit() else 0.0,
        "ran_at_text": _safe_label(source.get("ran_at_text"), 40),
        # Compatibility keys are deliberately blank: they can carry paths,
        # prompts, samples, or conversation text in historical result shapes.
        "path": "",
        "prompt_path": "",
        "reference_image": "",
        "umo": "",
        "prompt": "",
        "text": "",
        "sample": "",
        "text_preview": "",
        "original_text_preview": "",
        "final_text_preview": "",
        "diagnostic_detail": "",
        "warnings": [],
        "suggestions": [],
        "sections": [],
    }
