"""Safe station logo fetcher and cache with raster + strict static-SVG validation.

Design notes:
- Discovery chain: published /api/status logo -> homepage <link rel="...icon..."> /
  apple-touch-icon -> /logo.png -> /favicon.ico -> api.* parent web root fallback,
  with 24h positive cache and 1h negative cache.
- Credential-free public icon fetches allow up to 2 SSRF-revalidated redirects.
- Raster images (up to 1 MiB) are verified by magic bytes; SVG logos (up to 64 KiB)
  pass a strict XML allowlist sanitizer that strips/rejects scripts, foreignObject,
  animations, event handlers, nested data: URIs, and external references.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import tempfile
import threading
from typing import Any, Callable, Iterable
from urllib.parse import urljoin, urlsplit
import xml.etree.ElementTree as ET

from .base import (
    BROWSER_USER_AGENT,
    QuotaHttpError,
    _is_metadata_or_link_local_host,
    _is_private_or_local_host,
    http_request_public_bytes,
    same_origin,
    validate_target_url,
)

LOGO_MAX_BYTES = 1024 * 1024  # 1 MiB ceiling for crisp high-DPI PNG/ICO/WebP brand marks
SVG_MAX_BYTES = 64 * 1024  # 64 KiB ceiling for static vector brand logos
HOMEPAGE_ICON_PROBE_MAX_BYTES = 64 * 1024  # Bounded <head> prefix read for HTML icon discovery
LOGO_META_MAX_BYTES = 16 * 1024
LOGO_MAX_REDIRECTS = 2
LOGO_MAX_FETCH_ATTEMPTS = 16
LOGO_TTL = timedelta(hours=24)
LOGO_NEGATIVE_TTL = timedelta(hours=1)
LOGO_BACKOFF_SCHEDULE = (
    timedelta(minutes=5),
    timedelta(minutes=15),
    timedelta(hours=1),
)
LOGO_DISK_SCHEMA_VERSION = 1
LOGO_TIMEOUT_SECONDS = 6.0
LOGO_DIRECT_SUFFIXES = (
    "/logo.png",
    "/logo.svg",
    "/favicon.svg",
    "/favicon.png",
    "/favicon.ico",
)
_SAFE_STATION_FILE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

_SVG_NS = "http://www.w3.org/2000/svg"
_XLINK_NS = "http://www.w3.org/1999/xlink"
_XML_NS = "http://www.w3.org/XML/1998/namespace"
_SVG_MAX_DEPTH = 32
_SVG_MAX_NODES = 2048

_SAFE_SVG_TAGS = frozenset({
    "svg",
    "g",
    "defs",
    "title",
    "desc",
    "path",
    "rect",
    "circle",
    "ellipse",
    "line",
    "polyline",
    "polygon",
    "lineargradient",
    "radialgradient",
    "stop",
    "clippath",
    "mask",
    "pattern",
    "symbol",
    "use",
})

_HREF_ALLOWED_TAGS = frozenset({
    "use",
    "lineargradient",
    "radialgradient",
    "pattern",
})

_SAFE_SVG_ATTRS = frozenset({
    "id",
    "class",
    "version",
    "viewbox",
    "width",
    "height",
    "x",
    "y",
    "x1",
    "y1",
    "x2",
    "y2",
    "cx",
    "cy",
    "r",
    "rx",
    "ry",
    "d",
    "points",
    "fill",
    "fill-opacity",
    "fill-rule",
    "stroke",
    "stroke-width",
    "stroke-opacity",
    "stroke-linecap",
    "stroke-linejoin",
    "stroke-miterlimit",
    "stroke-dasharray",
    "stroke-dashoffset",
    "opacity",
    "color",
    "transform",
    "preserveaspectratio",
    "offset",
    "stop-color",
    "stop-opacity",
    "gradientunits",
    "gradienttransform",
    "spreadmethod",
    "fx",
    "fy",
    "fr",
    "clip-path",
    "clip-rule",
    "clippathunits",
    "mask",
    "maskunits",
    "maskcontentunits",
    "patternunits",
    "patterncontentunits",
    "patterntransform",
    "shape-rendering",
    "vector-effect",
    "paint-order",
    "style",
    "href",
    "role",
    "aria-label",
    "aria-hidden",
    "focusable",
})

_FORBIDDEN_SVG_DECLS = ("<!doctype", "<!entity", "<!element", "<!attlist", "<![cdata[")
_FORBIDDEN_VALUE_SUBSTRINGS = (
    "javascript:",
    "vbscript:",
    "data:",
    "expression(",
    "@import",
    "http://",
    "https://",
    "file://",
    "ftp://",
    "//",
)
_CSS_URL_RE = re.compile(r"url\s*\(([^)]*)\)", re.IGNORECASE)
_LOCAL_FRAGMENT_RE = re.compile(r"^#[A-Za-z_][A-Za-z0-9_.:-]*$")


def sniff_safe_raster_image(body: bytes) -> str | None:
    """Strictly validate raster image magic bytes; reject HTML, text, and SVG."""
    if not body or len(body) < 4:
        return None
    head = body[:16]
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


def _split_clark_name(raw_name: str) -> tuple[str, str]:
    if raw_name.startswith("{") and "}" in raw_name:
        end = raw_name.index("}")
        return raw_name[1:end], raw_name[end + 1 :]
    return "", raw_name


def _is_safe_svg_attr_value(attr_lower: str, value: str, tag_lower: str) -> bool:
    if "\x00" in value:
        return False
    compact = re.sub(r"[\s\x00-\x20]+", "", value).lower()
    if attr_lower == "style" and "\\" in value:
        return False
    for needle in _FORBIDDEN_VALUE_SUBSTRINGS:
        if needle in compact:
            return False
    if attr_lower == "href":
        return tag_lower in _HREF_ALLOWED_TAGS and bool(_LOCAL_FRAGMENT_RE.fullmatch(value.strip()))
    if "url(" in compact:
        matches = list(_CSS_URL_RE.finditer(value))
        if not matches or len(matches) != compact.count("url("):
            return False
        for match in matches:
            target = match.group(1).strip()
            if len(target) >= 2 and target[0] == target[-1] and target[0] in ("'", '"'):
                target = target[1:-1].strip()
            if not _LOCAL_FRAGMENT_RE.fullmatch(target):
                return False
    return True


def _sanitize_svg_node(elem: ET.Element, depth: int, counter: list[int]) -> ET.Element | None:
    if depth > _SVG_MAX_DEPTH:
        return None
    counter[0] += 1
    if counter[0] > _SVG_MAX_NODES:
        return None
    if not isinstance(elem.tag, str):
        return None

    ns, local_tag = _split_clark_name(elem.tag)
    tag_lower = local_tag.lower()
    if ns not in ("", _SVG_NS) or tag_lower not in _SAFE_SVG_TAGS:
        return None

    clean = ET.Element(local_tag)
    for raw_key, raw_val in elem.attrib.items():
        if not isinstance(raw_key, str):
            return None
        attr_ns, attr_local = _split_clark_name(raw_key)
        attr_lower = attr_local.lower()
        if not attr_local or attr_lower.startswith("on"):
            return None
        if attr_ns == "":
            if attr_lower not in _SAFE_SVG_ATTRS:
                return None
            out_attr_name = attr_local
        elif attr_ns == _XLINK_NS and attr_lower == "href":
            out_attr_name = "href"
        elif attr_ns == _XML_NS and attr_lower == "space":
            out_attr_name = "xml:space"
        else:
            return None

        val_str = str(raw_val or "")
        if not _is_safe_svg_attr_value(attr_lower, val_str, tag_lower):
            return None
        clean.set(out_attr_name, val_str)

    if tag_lower in ("title", "desc") and elem.text:
        clean.text = elem.text.strip()[:256]

    for child in elem:
        sanitized_child = _sanitize_svg_node(child, depth + 1, counter)
        if sanitized_child is None:
            return None
        clean.append(sanitized_child)

    return clean


def sanitize_safe_svg(body: bytes) -> bytes | None:
    """Strictly validate and re-serialize a static SVG logo (<= 64 KiB) via XML allowlist.

    Rejects scripts, foreignObject, animations, event handlers, DTD/entity declarations,
    nested data: URIs, and any external href/url(...) references.
    """
    if not body or len(body) > SVG_MAX_BYTES:
        return None
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return None

    stripped = text.lstrip("\ufeff \t\r\n")
    if not stripped.startswith("<"):
        return None
    lower = stripped.lower()
    if "<svg" not in lower:
        return None
    for decl in _FORBIDDEN_SVG_DECLS:
        if decl in lower:
            return None

    try:
        parsed_root = ET.fromstring(stripped)
    except ET.ParseError:
        return None

    root_ns, root_local = _split_clark_name(parsed_root.tag) if isinstance(parsed_root.tag, str) else ("", "")
    if root_ns not in ("", _SVG_NS) or root_local.lower() != "svg":
        return None

    clean_root = _sanitize_svg_node(parsed_root, 0, [0])
    if clean_root is None:
        return None
    clean_root.set("xmlns", _SVG_NS)
    serialized = ET.tostring(clean_root, encoding="utf-8", xml_declaration=False)
    if not serialized or len(serialized) > SVG_MAX_BYTES:
        return None
    return serialized


def sniff_safe_logo(body: bytes) -> tuple[str | None, bytes]:
    """Validate logo payload as a safe raster image or sanitized static SVG."""
    raster_mime = sniff_safe_raster_image(body)
    if raster_mime:
        return raster_mime, body
    clean_svg = sanitize_safe_svg(body)
    if clean_svg is not None:
        return "image/svg+xml", clean_svg
    return None, b""


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


class _HeadIconParser(HTMLParser):
    """Lightweight HTML <head> link extractor for favicon / apple-touch-icon discovery."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_body = False
        self.candidates: list[tuple[int, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_lower = tag.lower()
        if tag_lower == "body":
            self.in_body = True
            return
        if self.in_body or tag_lower != "link" or len(self.candidates) >= 24:
            return
        attr_map = {str(k or "").lower(): str(v or "").strip() for k, v in attrs}
        href = attr_map.get("href", "")
        rel = attr_map.get("rel", "").lower()
        if not href or not rel:
            return
        tokens = set(rel.split())
        if "apple-touch-icon" in tokens or "apple-touch-icon-precomposed" in tokens:
            priority = 0
        elif "icon" in tokens:
            priority = 1
        elif "mask-icon" in tokens:
            priority = 2
        else:
            return
        self.candidates.append((priority, href))


def extract_html_icon_links(
    html_bytes: bytes,
    page_url: str,
    *,
    allow_http: bool = False,
) -> list[str]:
    """Extract and SSRF-validate icon URLs from bounded HTML <head> markup."""
    if not html_bytes:
        return []
    text = html_bytes[:HOMEPAGE_ICON_PROBE_MAX_BYTES].decode("utf-8", errors="ignore")
    if "<link" not in text.lower():
        return []
    parser = _HeadIconParser()
    try:
        parser.feed(text)
    except Exception:
        pass
    if not parser.candidates:
        return []
    ordered = sorted(parser.candidates, key=lambda item: item[0])
    urls: list[str] = []
    seen: set[str] = set()
    for _prio, href in ordered:
        resolved = resolve_logo_url(page_url, href, allow_http=allow_http)
        if resolved and resolved not in seen:
            seen.add(resolved)
            urls.append(resolved)
            if len(urls) >= 4:
                break
    return urls


def _origin_of(base_url: str) -> str:
    try:
        parsed = urlsplit(str(base_url or "").strip())
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme.lower()}://{parsed.netloc}"
    except ValueError:
        pass
    return ""


def _api_fallback_roots(base_url: str) -> list[str]:
    """Derive public web root fallbacks for ``api.<domain>`` hosts (e.g. api.deepseek.com -> www.deepseek.com)."""
    try:
        parsed = urlsplit(str(base_url or "").strip())
    except ValueError:
        return []
    hostname = (parsed.hostname or "").strip().lower()
    if not hostname.startswith("api."):
        return []
    parent = hostname[4:]
    if "." not in parent or _is_private_or_local_host(parent) or _is_metadata_or_link_local_host(parent):
        return []
    fallbacks: list[str] = []
    for candidate in (f"https://www.{parent}", f"https://{parent}"):
        try:
            fallbacks.append(validate_target_url(candidate, allow_http=False))
        except QuotaHttpError:
            continue
    return fallbacks


def logo_candidate_urls(
    base_url: str,
    published_logo: str = "",
    *,
    extra_roots: Iterable[str] = (),
    allow_http: bool = False,
) -> list[str]:
    """Ordered direct logo candidates (excluding dynamic homepage HTML discovery)."""
    candidates: list[str] = []
    seen: set[str] = set()

    def _add(root: str, ref: str, *, root_allow_http: bool) -> None:
        resolved = resolve_logo_url(root, ref, allow_http=root_allow_http)
        if resolved and resolved not in seen:
            seen.add(resolved)
            candidates.append(resolved)

    if published_logo:
        _add(base_url, published_logo, root_allow_http=allow_http)

    roots = _ordered_icon_roots(base_url, extra_roots=extra_roots)
    for root in roots:
        root_allow = bool(allow_http) and same_origin(base_url, root)
        for suffix in LOGO_DIRECT_SUFFIXES:
            _add(root, suffix, root_allow_http=root_allow)
    return candidates


def _ordered_icon_roots(base_url: str, *, extra_roots: Iterable[str] = ()) -> list[str]:
    base_origin = _origin_of(base_url)
    extra_origins: list[str] = []
    for item in extra_roots:
        orig = _origin_of(item)
        if orig:
            extra_origins.append(orig)
    hostname = (urlsplit(base_origin).hostname or "").lower() if base_origin else ""
    api_fallbacks = _api_fallback_roots(base_origin) if base_origin else []

    raw_order: list[str] = []
    if hostname.startswith("api.") and extra_origins:
        raw_order.extend(extra_origins)
        if base_origin:
            raw_order.append(base_origin)
        raw_order.extend(api_fallbacks)
    else:
        if base_origin:
            raw_order.append(base_origin)
        raw_order.extend(extra_origins)
        raw_order.extend(api_fallbacks)

    roots: list[str] = []
    seen: set[str] = set()
    for root in raw_order:
        if root and root not in seen:
            seen.add(root)
            roots.append(root)
    return roots


def _short_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:12]


def _backoff_delta(fail_count: int) -> timedelta:
    idx = max(0, min(int(fail_count) - 1, len(LOGO_BACKOFF_SCHEDULE) - 1))
    return LOGO_BACKOFF_SCHEDULE[idx]


def _parse_iso_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


@dataclass(frozen=True)
class CachedLogo:
    source_url: str
    mime_type: str
    data: bytes
    version: str
    fetched_at: datetime
    error: str = ""
    fail_count: int = 0
    next_retry_at: datetime | None = None

    @property
    def usable(self) -> bool:
        return bool(self.data and self.mime_type and self.version)

    def data_url(self) -> str:
        if not self.usable:
            return ""
        encoded = base64.b64encode(self.data).decode("ascii")
        return f"data:{self.mime_type};base64,{encoded}"


class StationLogoCache:
    """Persistent + in-memory Stale-While-Revalidate cache for proxied station logos."""

    def __init__(
        self,
        *,
        now: Callable[[], datetime] | None = None,
        cache_dir: Path | str | None = None,
    ):
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._cache_dir: Path | None = Path(cache_dir).resolve() if cache_dir else None
        self._lock = threading.RLock()
        self._entries: dict[str, CachedLogo] = {}

    def _current_time(self) -> datetime:
        current = self._now()
        return current.replace(tzinfo=timezone.utc) if current.tzinfo is None else current

    @staticmethod
    def _station_stem(station_id: str) -> str:
        sid = str(station_id or "").strip().lower()
        if sid and _SAFE_STATION_FILE_RE.fullmatch(sid):
            return sid
        return "st-" + hashlib.sha256(str(station_id or "").encode("utf-8")).hexdigest()[:16]

    def _disk_paths(self, station_id: str) -> tuple[Path, Path] | None:
        if self._cache_dir is None:
            return None
        stem = self._station_stem(station_id)
        return self._cache_dir / f"{stem}.bin", self._cache_dir / f"{stem}.meta.json"

    def _save_to_disk(self, station_id: str, entry: CachedLogo, *, drop_binary: bool = False) -> None:
        paths = self._disk_paths(station_id)
        if paths is None:
            return
        bin_path, meta_path = paths
        try:
            if entry.usable:
                _atomic_write_bytes(bin_path, entry.data)
            elif drop_binary:
                bin_path.unlink(missing_ok=True)
            meta = {
                "schema_version": LOGO_DISK_SCHEMA_VERSION,
                "station_id": str(station_id or ""),
                "source_url": entry.source_url,
                "mime_type": entry.mime_type,
                "version": entry.version,
                "fetched_at": entry.fetched_at.isoformat(),
                "last_error": entry.error,
                "fail_count": int(entry.fail_count),
                "next_retry_at": entry.next_retry_at.isoformat() if entry.next_retry_at is not None else "",
            }
            meta_bytes = (json.dumps(meta, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            _atomic_write_bytes(meta_path, meta_bytes)
        except OSError:
            return

    def _load_from_disk(self, station_id: str) -> CachedLogo | None:
        paths = self._disk_paths(station_id)
        if paths is None:
            return None
        bin_path, meta_path = paths
        if not meta_path.exists():
            return None
        try:
            raw_meta = meta_path.read_bytes()
            if len(raw_meta) > LOGO_META_MAX_BYTES:
                return None
            meta = json.loads(raw_meta.decode("utf-8"))
            if type(meta) is not dict:
                return None
        except (OSError, UnicodeError, ValueError):
            return None

        source_url = str(meta.get("source_url") or "")
        fetched_at = _parse_iso_datetime(meta.get("fetched_at")) or self._current_time()
        last_error = str(meta.get("last_error") or "")
        raw_fails = meta.get("fail_count", 0)
        fail_count = int(raw_fails) if isinstance(raw_fails, int) and not isinstance(raw_fails, bool) and raw_fails >= 0 else 0
        next_retry_at = _parse_iso_datetime(meta.get("next_retry_at"))

        if bin_path.exists():
            try:
                raw_bin = bin_path.read_bytes()
            except OSError:
                raw_bin = b""
            if raw_bin and len(raw_bin) <= LOGO_MAX_BYTES:
                mime, clean_bytes = sniff_safe_logo(raw_bin)
                if mime and clean_bytes:
                    return CachedLogo(
                        source_url=source_url,
                        mime_type=mime,
                        data=clean_bytes,
                        version=_short_hash(clean_bytes),
                        fetched_at=fetched_at,
                        error=last_error,
                        fail_count=fail_count,
                        next_retry_at=next_retry_at,
                    )

        if last_error:
            return CachedLogo(
                source_url=source_url,
                mime_type="",
                data=b"",
                version="",
                fetched_at=fetched_at,
                error=last_error,
                fail_count=fail_count,
                next_retry_at=next_retry_at,
            )
        return None

    def get(self, station_id: str) -> CachedLogo | None:
        with self._lock:
            entry = self._entries.get(station_id)
            if entry is not None:
                return entry
        loaded = self._load_from_disk(station_id)
        if loaded is not None:
            with self._lock:
                self._entries.setdefault(station_id, loaded)
                return self._entries[station_id]
        return None

    def is_stale(self, entry: CachedLogo | None) -> bool:
        if entry is None or not entry.usable:
            return True
        now = self._current_time()
        if (now - entry.fetched_at) < LOGO_TTL:
            return False
        if entry.next_retry_at is not None and now < entry.next_retry_at:
            return False
        return True

    def forget(self, station_id: str) -> None:
        with self._lock:
            self._entries.pop(station_id, None)
        paths = self._disk_paths(station_id)
        if paths is not None:
            for path in paths:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    async def refresh(
        self,
        station_id: str,
        base_url: str,
        published_logo: str = "",
        *,
        extra_roots: Iterable[str] = (),
        allow_http: bool = False,
        proxy_url: str = "",
        fallback_proxy_url: str = "",
        force: bool = False,
        session_factory: Callable[..., Any] | None = None,
    ) -> tuple[str, str]:
        """Return ``(version, error_text)`` with persistent disk cache, SWR, and exponential backoff."""
        roots = _ordered_icon_roots(base_url, extra_roots=extra_roots)
        published_url = resolve_logo_url(base_url, published_logo, allow_http=allow_http) if published_logo else ""
        if not published_url and not roots:
            self.forget(station_id)
            return "", "站点地址无效，无法解析图标"

        want_origin = _origin_of(base_url)
        want = "|".join([want_origin, published_url, *roots])
        now = self._current_time()
        prev = self.get(station_id)
        prev_origin = prev.source_url.split("|", 1)[0] if (prev is not None and prev.source_url) else ""
        same_station_origin = bool(prev is not None and prev.usable and prev_origin and prev_origin == want_origin)

        if not force and prev is not None and prev.source_url == want:
            age = now - prev.fetched_at
            if prev.usable:
                if age < LOGO_TTL:
                    return prev.version, ""
                if prev.next_retry_at is not None and now < prev.next_retry_at:
                    return prev.version, ""
            else:
                if prev.next_retry_at is not None:
                    if now < prev.next_retry_at:
                        return "", prev.error
                elif prev.error and age < LOGO_NEGATIVE_TTL:
                    return "", prev.error

        referer = want_origin
        img_headers = {
            "Accept": "image/webp,image/png,image/jpeg,image/x-icon,image/gif,image/svg+xml,*/*;q=0.5",
            "User-Agent": BROWSER_USER_AGENT,
        }
        html_headers = {
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
            "User-Agent": BROWSER_USER_AGENT,
        }
        if referer:
            img_headers["Referer"] = referer
            html_headers["Referer"] = referer

        # Public credential-free logo requests can safely fall back between proxy and direct on transport failure.
        alt_proxy = "" if proxy_url else str(fallback_proxy_url or "").strip()
        proxy_order = (proxy_url, alt_proxy) if alt_proxy != proxy_url else (proxy_url,)

        attempts = 0
        tried_urls: set[str] = set()
        last_error = "未能获取有效图标"

        async def _fetch_public(
            target: str,
            *,
            headers: dict[str, str],
            timeout_seconds: float,
            max_bytes: int,
            truncate_at_max_bytes: bool = False,
        ) -> tuple[int, dict[str, str], bytes, str]:
            last_exc: QuotaHttpError | None = None
            for idx_p, candidate_proxy in enumerate(proxy_order):
                try:
                    return await http_request_public_bytes(
                        target,
                        headers=headers,
                        allow_http=allow_http,
                        origin_url=base_url,
                        proxy_url=candidate_proxy,
                        timeout_seconds=timeout_seconds,
                        max_bytes=max_bytes,
                        truncate_at_max_bytes=truncate_at_max_bytes,
                        max_redirects=LOGO_MAX_REDIRECTS,
                        session_factory=session_factory,
                    )
                except QuotaHttpError as exc:
                    last_exc = exc
                    if idx_p + 1 < len(proxy_order) and exc.code in ("network_error", "timeout", "proxy_dependency_missing"):
                        continue
                    raise
            assert last_exc is not None
            raise last_exc

        async def _try_image(target: str) -> tuple[str | None, bytes]:
            nonlocal attempts, last_error
            if not target or target in tried_urls or attempts >= LOGO_MAX_FETCH_ATTEMPTS:
                return None, b""
            tried_urls.add(target)
            attempts += 1
            try:
                status, _resp_headers, body, _final = await _fetch_public(
                    target,
                    headers=img_headers,
                    timeout_seconds=LOGO_TIMEOUT_SECONDS,
                    max_bytes=LOGO_MAX_BYTES,
                )
            except QuotaHttpError as exc:
                last_error = str(exc)
                return None, b""
            if status < 200 or status >= 300:
                last_error = f"HTTP {status}"
                return None, b""
            mime, clean_bytes = sniff_safe_logo(body)
            if not mime:
                last_error = "图标不是受支持的安全图片（已拒绝非图片或含脚本的 SVG）"
                return None, b""
            return mime, clean_bytes

        def _store_success(mime: str, data: bytes) -> tuple[str, str]:
            entry = CachedLogo(
                source_url=want,
                mime_type=mime,
                data=data,
                version=_short_hash(data),
                fetched_at=now,
                error="",
                fail_count=0,
                next_retry_at=None,
            )
            with self._lock:
                self._entries[station_id] = entry
            self._save_to_disk(station_id, entry)
            return entry.version, ""

        # 1. Explicit /api/status or official published logo (fast 1-request path).
        if published_url:
            mime, data = await _try_image(published_url)
            if mime:
                return _store_success(mime, data)

        # 2. Per-root discovery: homepage <link rel="...icon..."> -> direct icon paths.
        for idx, root in enumerate(roots):
            if attempts >= LOGO_MAX_FETCH_ATTEMPTS:
                break
            root_allow_http = bool(allow_http) and same_origin(base_url, root)
            home_url = f"{root}/"
            auth_walled = False
            if home_url not in tried_urls and attempts < LOGO_MAX_FETCH_ATTEMPTS:
                tried_urls.add(home_url)
                attempts += 1
                try:
                    h_status, _h_headers, h_body, final_home = await _fetch_public(
                        home_url,
                        headers=html_headers,
                        timeout_seconds=min(LOGO_TIMEOUT_SECONDS, 4.0),
                        max_bytes=HOMEPAGE_ICON_PROBE_MAX_BYTES,
                        truncate_at_max_bytes=True,
                    )
                    if h_status in (401, 403):
                        auth_walled = True
                        last_error = f"HTTP {h_status}"
                    elif 200 <= h_status < 300 and h_body:
                        html_links = extract_html_icon_links(
                            h_body,
                            final_home,
                            allow_http=bool(allow_http) and same_origin(base_url, final_home),
                        )
                        for icon_url in html_links:
                            mime, data = await _try_image(icon_url)
                            if mime:
                                return _store_success(mime, data)
                except QuotaHttpError as exc:
                    last_error = str(exc)

            # Skip redundant path probes on auth-walled API roots when fallback roots remain.
            if auth_walled and idx + 1 < len(roots):
                continue

            for suffix in LOGO_DIRECT_SUFFIXES:
                candidate = resolve_logo_url(root, suffix, allow_http=root_allow_http)
                mime, data = await _try_image(candidate)
                if mime:
                    return _store_success(mime, data)

        # Stale-While-Revalidate: NEVER erase a previously usable logo on transient revalidation failure!
        if prev is not None and prev.usable and (prev.source_url == want or same_station_origin):
            next_fails = prev.fail_count + 1
            retained = CachedLogo(
                source_url=prev.source_url,
                mime_type=prev.mime_type,
                data=prev.data,
                version=prev.version,
                fetched_at=prev.fetched_at,
                error=last_error,
                fail_count=next_fails,
                next_retry_at=now + _backoff_delta(next_fails),
            )
            with self._lock:
                self._entries[station_id] = retained
            self._save_to_disk(station_id, retained)
            return retained.version, ""

        next_fails = (prev.fail_count + 1) if (prev is not None and not prev.usable and prev.source_url == want) else 1
        failed = CachedLogo(
            source_url=want,
            mime_type="",
            data=b"",
            version="",
            fetched_at=now,
            error=last_error,
            fail_count=next_fails,
            next_retry_at=now + _backoff_delta(next_fails),
        )
        with self._lock:
            self._entries[station_id] = failed
        self._save_to_disk(station_id, failed, drop_binary=True)
        return "", last_error
