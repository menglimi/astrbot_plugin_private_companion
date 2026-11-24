"""Safe station logo fetcher and cache with strict raster magic-byte validation.

Design notes:
- Inspired by quota-watch's logo proxy fallback chain (/api/status data.logo -> /favicon.ico -> initials)
  with 24h positive cache and 1h negative cache.
- Adapted for AstrBot Dashboard's sandboxed iframe bridge: serves verified raster bytes as
  bounded ``data:image/...;base64,...`` URIs via authenticated JSON API.
- Rejects HTML pages disguised as images AND raw SVGs to eliminate XSS risk inside data URIs.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import threading
from typing import Any, Callable
from urllib.parse import urljoin, urlsplit

from .base import (
    BROWSER_USER_AGENT,
    QuotaHttpError,
    http_request_bytes,
    same_origin,
    validate_target_url,
)

LOGO_MAX_BYTES = 256 * 1024  # 256 KiB ceiling for crisp favicon/mark
LOGO_TTL = timedelta(hours=24)
LOGO_NEGATIVE_TTL = timedelta(hours=1)
LOGO_TIMEOUT_SECONDS = 6.0


def sniff_safe_raster_image(body: bytes) -> str | None:
    """Strictly validate raster image magic bytes; reject HTML, text, and executable SVG."""
    if not body or len(body) < 4:
        return None
    head = body[:16]
    # Reject obvious text/markup payloads immediately (including SVG / XML / HTML).
    trimmed = body[:64].lstrip()
    if trimmed.startswith((b"<", b"<!", b"<?xml", b"{", b"[")):
        return None
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head[:4] in (b"\x00\x00\x01\x00", b"\x00\x00\x02\x00"):
        return "image/x-icon"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    return None


def resolve_logo_url(base_url: str, ref: str, *, allow_http: bool = False) -> str:
    """Resolve an absolute, protocol-relative, or relative logo reference against base_url.

    Cross-origin logos never inherit ``allow_http=True``: third-party hosts must use public HTTPS.
    """
    ref_text = str(ref or "").strip()
    if not ref_text:
        return ""
    base_clean = str(base_url or "").strip().rstrip("/") + "/"
    try:
        parsed_base = urlsplit(base_clean)
        if not parsed_base.scheme or not parsed_base.hostname:
            return ""
        resolved = urljoin(base_clean, ref_text)
        effective_allow_http = bool(allow_http) and same_origin(base_clean, resolved)
        return validate_target_url(resolved, allow_http=effective_allow_http)
    except (ValueError, QuotaHttpError):
        return ""


def logo_candidate_urls(base_url: str, published_logo: str = "", *, allow_http: bool = False) -> list[str]:
    """Ordered fallback chain: published /api/status logo -> /favicon.ico."""
    candidates: list[str] = []
    seen: set[str] = set()
    for ref in (published_logo, "/favicon.ico"):
        resolved = resolve_logo_url(base_url, ref, allow_http=allow_http)
        if resolved and resolved not in seen:
            seen.add(resolved)
            candidates.append(resolved)
    return candidates


def _origin_of(base_url: str) -> str:
    try:
        parsed = urlsplit(base_url)
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}"
    except ValueError:
        pass
    return ""


def _short_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:12]


@dataclass(frozen=True)
class CachedLogo:
    source_url: str
    mime_type: str
    data: bytes
    version: str
    fetched_at: datetime
    error: str = ""

    @property
    def usable(self) -> bool:
        return bool(self.data and self.mime_type and self.version)

    def data_url(self) -> str:
        if not self.usable:
            return ""
        encoded = base64.b64encode(self.data).decode("ascii")
        return f"data:{self.mime_type};base64,{encoded}"


class StationLogoCache:
    """In-memory positive/negative cache for proxied station logos."""

    def __init__(self, *, now: Callable[[], datetime] | None = None):
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._lock = threading.RLock()
        self._entries: dict[str, CachedLogo] = {}

    def _current_time(self) -> datetime:
        current = self._now()
        return current.replace(tzinfo=timezone.utc) if current.tzinfo is None else current

    def get(self, station_id: str) -> CachedLogo | None:
        with self._lock:
            return self._entries.get(station_id)

    def forget(self, station_id: str) -> None:
        with self._lock:
            self._entries.pop(station_id, None)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    async def refresh(
        self,
        station_id: str,
        base_url: str,
        published_logo: str = "",
        *,
        allow_http: bool = False,
        proxy_url: str = "",
        session_factory: Callable[..., Any] | None = None,
    ) -> tuple[str, str]:
        """Return ``(version, error_text)`` using positive/negative TTL caching."""
        sources = logo_candidate_urls(base_url, published_logo, allow_http=allow_http)
        if not sources:
            self.forget(station_id)
            return "", "站点地址无效，无法解析图标"

        want = sources[0]
        now = self._current_time()
        with self._lock:
            prev = self._entries.get(station_id)
        if prev is not None and prev.source_url == want:
            age = now - prev.fetched_at
            if prev.usable and age < LOGO_TTL:
                return prev.version, ""
            if not prev.usable and prev.error and age < LOGO_NEGATIVE_TTL:
                return "", prev.error

        referer = _origin_of(base_url)
        last_error = "未能获取有效图标"
        for target in sources:
            headers = {
                "Accept": "image/webp,image/png,image/jpeg,image/x-icon,image/gif,*/*;q=0.5",
                "User-Agent": BROWSER_USER_AGENT,
            }
            if referer:
                headers["Referer"] = referer
            try:
                status, _resp_headers, body = await http_request_bytes(
                    "GET",
                    target,
                    headers=headers,
                    allow_http=bool(allow_http) and same_origin(base_url, target),
                    proxy_url=proxy_url,
                    timeout_seconds=LOGO_TIMEOUT_SECONDS,
                    max_bytes=LOGO_MAX_BYTES,
                    session_factory=session_factory,
                )
            except QuotaHttpError as exc:
                last_error = str(exc)
                continue
            if status < 200 or status >= 300:
                last_error = f"HTTP {status}"
                continue
            mime = sniff_safe_raster_image(body)
            if not mime:
                last_error = "图标不是受支持的栅格图片（已拒绝非图片或 SVG）"
                continue
            entry = CachedLogo(
                source_url=want,
                mime_type=mime,
                data=body,
                version=_short_hash(body),
                fetched_at=now,
                error="",
            )
            with self._lock:
                self._entries[station_id] = entry
            return entry.version, ""

        failed = CachedLogo(
            source_url=want,
            mime_type="",
            data=b"",
            version="",
            fetched_at=now,
            error=last_error,
        )
        with self._lock:
            self._entries[station_id] = failed
        return "", last_error
