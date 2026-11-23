"""Lightweight HTTP ETag computation and 304 Not Modified helpers for polling APIs."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
from typing import Any

from astrbot.api.web import request as current_request

try:
    from fastapi.encoders import jsonable_encoder
except Exception:  # pragma: no cover - fallback when FastAPI is absent
    jsonable_encoder = None

try:
    from starlette.responses import JSONResponse, Response
except Exception:  # pragma: no cover - fallback when Starlette is absent
    JSONResponse = None
    Response = None


def _strip_ignored(value: Any, ignored: frozenset[str]) -> Any:
    if not ignored:
        return value
    if isinstance(value, Mapping):
        return {
            key: _strip_ignored(item, ignored)
            for key, item in value.items()
            if key not in ignored
        }
    if isinstance(value, list):
        return [_strip_ignored(item, ignored) for item in value]
    if isinstance(value, tuple):
        return [_strip_ignored(item, ignored) for item in value]
    return value


def compute_etag(value: Any, *, ignore_keys: tuple[str, ...] | frozenset[str] = ()) -> str:
    """Compute a deterministic weak ETag over a JSON-serializable payload."""
    ignored = frozenset(ignore_keys)
    normalized = _strip_ignored(value, ignored)
    encoded = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8", "replace")
    digest = hashlib.sha256(encoded).hexdigest()[:24]
    return f'W/"{digest}"'


def request_if_none_match(req: Any = None) -> str:
    """Safely read the ``If-None-Match`` header from the active or given request."""
    target = current_request if req is None else req
    try:
        headers = getattr(target, "headers", None)
    except (LookupError, RuntimeError, AttributeError):
        return ""
    if headers is None:
        return ""
    if isinstance(headers, Mapping):
        value = headers.get("if-none-match") or headers.get("If-None-Match")
        if isinstance(value, str):
            return value.strip()
        for key, item in headers.items():
            if isinstance(key, str) and key.lower() == "if-none-match" and isinstance(item, str):
                return item.strip()
        return ""
    getter = getattr(headers, "get", None)
    if callable(getter):
        value = getter("if-none-match") or getter("If-None-Match")
        if isinstance(value, str):
            return value.strip()
    return ""


def _normalize_tag(token: str) -> str:
    cleaned = token.strip()
    if cleaned.startswith(("W/", "w/")):
        cleaned = cleaned[2:].strip()
    return cleaned


def etag_matches(header_value: str | None, etag: str) -> bool:
    """Perform RFC 7232 weak ETag comparison against an ``If-None-Match`` header."""
    if not isinstance(header_value, str) or not header_value.strip() or not etag:
        return False
    expected = _normalize_tag(etag)
    for part in header_value.split(","):
        candidate = part.strip()
        if not candidate:
            continue
        if candidate == "*":
            return True
        if _normalize_tag(candidate) == expected:
            return True
    return False


def not_modified_response(etag: str) -> Any:
    """Return an HTTP 304 Not Modified response carrying the current ETag."""
    headers = {"ETag": etag, "Cache-Control": "no-cache"}
    if Response is not None:
        return Response(status_code=304, headers=headers)
    return {"status": "not_modified", "status_code": 304, "etag": etag}


def _is_live_asgi_request(req: Any) -> bool:
    """Whether ``req`` wraps a live Starlette/ASGI request from AstrBot's dashboard server."""
    target = current_request if req is None else req
    try:
        raw = getattr(target, "_request", None) or getattr(target, "_raw", None)
        if raw is None:
            inner = getattr(target, "_get", None)
            if callable(inner):
                resolved = inner()
                raw = getattr(resolved, "_request", None) or getattr(resolved, "_raw", None)
        scope = getattr(raw, "scope", None)
    except (LookupError, RuntimeError, AttributeError):
        return False
    return isinstance(scope, Mapping) and "app" in scope


def conditional_response(
    payload: Any,
    *,
    req: Any = None,
    etag: str | None = None,
    ignore_keys: tuple[str, ...] | frozenset[str] = (),
) -> Any:
    """Apply HTTP ETag / 304 conditional response handling to a successful API payload.

    Preserves plain ``dict`` returns for direct unit-test invocations without
    ``If-None-Match`` while emitting Starlette ``JSONResponse`` (with HTTP
    ``ETag`` header) on live ASGI requests and ``Response(status_code=304)``
    whenever ``If-None-Match`` matches.
    """
    if not isinstance(payload, Mapping) or payload.get("status") != "ok":
        return payload
    tag = etag or compute_etag(payload.get("data", payload), ignore_keys=ignore_keys)
    header_value = request_if_none_match(req)
    if header_value and etag_matches(header_value, tag):
        return not_modified_response(tag)
    envelope = {**payload, "etag": tag}
    if JSONResponse is not None and _is_live_asgi_request(req):
        content = jsonable_encoder(envelope) if jsonable_encoder is not None else envelope
        return JSONResponse(
            content=content,
            headers={"ETag": tag, "Cache-Control": "no-cache"},
        )
    return envelope


__all__ = [
    "compute_etag",
    "conditional_response",
    "etag_matches",
    "not_modified_response",
    "request_if_none_match",
]
