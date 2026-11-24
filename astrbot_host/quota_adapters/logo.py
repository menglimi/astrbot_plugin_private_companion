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
import re
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
LOGO_MAX_REDIRECTS = 2
LOGO_MAX_FETCH_ATTEMPTS = 10
LOGO_TTL = timedelta(hours=24)
LOGO_NEGATIVE_TTL = timedelta(hours=1)
LOGO_TIMEOUT_SECONDS = 6.0

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
        _add(root, "/logo.png", root_allow_http=root_allow)
        _add(root, "/favicon.ico", root_allow_http=root_allow)
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
        extra_roots: Iterable[str] = (),
        allow_http: bool = False,
        proxy_url: str = "",
        session_factory: Callable[..., Any] | None = None,
    ) -> tuple[str, str]:
        """Return ``(version, error_text)`` using positive/negative TTL caching."""
        roots = _ordered_icon_roots(base_url, extra_roots=extra_roots)
        published_url = resolve_logo_url(base_url, published_logo, allow_http=allow_http) if published_logo else ""
        if not published_url and not roots:
            self.forget(station_id)
            return "", "站点地址无效，无法解析图标"

        want = "|".join([_origin_of(base_url), published_url, *roots])
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

        attempts = 0
        tried_urls: set[str] = set()
        last_error = "未能获取有效图标"

        async def _try_image(target: str) -> tuple[str | None, bytes]:
            nonlocal attempts, last_error
            if not target or target in tried_urls or attempts >= LOGO_MAX_FETCH_ATTEMPTS:
                return None, b""
            tried_urls.add(target)
            attempts += 1
            try:
                status, _resp_headers, body, _final = await http_request_public_bytes(
                    target,
                    headers=img_headers,
                    allow_http=allow_http,
                    origin_url=base_url,
                    proxy_url=proxy_url,
                    timeout_seconds=LOGO_TIMEOUT_SECONDS,
                    max_bytes=LOGO_MAX_BYTES,
                    max_redirects=LOGO_MAX_REDIRECTS,
                    session_factory=session_factory,
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
            )
            with self._lock:
                self._entries[station_id] = entry
            return entry.version, ""

        # 1. Explicit /api/status or official published logo (fast 1-request path).
        if published_url:
            mime, data = await _try_image(published_url)
            if mime:
                return _store_success(mime, data)

        # 2. Per-root discovery: homepage <link rel="...icon..."> -> /logo.png -> /favicon.ico.
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
                    h_status, _h_headers, h_body, final_home = await http_request_public_bytes(
                        home_url,
                        headers=html_headers,
                        allow_http=allow_http,
                        origin_url=base_url,
                        proxy_url=proxy_url,
                        timeout_seconds=min(LOGO_TIMEOUT_SECONDS, 4.0),
                        max_bytes=HOMEPAGE_ICON_PROBE_MAX_BYTES,
                        truncate_at_max_bytes=True,
                        max_redirects=LOGO_MAX_REDIRECTS,
                        session_factory=session_factory,
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

            for suffix in ("/logo.png", "/favicon.ico"):
                candidate = resolve_logo_url(root, suffix, allow_http=root_allow_http)
                mime, data = await _try_image(candidate)
                if mime:
                    return _store_success(mime, data)

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
