"""Plugin-style adapter registry, SSRF-hardened HTTP helpers, and dynamic form schema contracts."""

from __future__ import annotations

from abc import ABC, abstractmethod
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
import ipaddress
import json
import math
import time
from typing import Any, Callable
from urllib.parse import urljoin, urlsplit

import aiohttp

from companion_v2.model_gateway.quota import (
    QuotaSnapshot,
    QuotaStationSpec,
    redact_quota_text,
)

from ..exchange_rate import read_bounded_stream

DEFAULT_TIMEOUT_SECONDS = 10.0
MAX_JSON_RESPONSE_BYTES = 131_072  # 128 KiB
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# Cloud metadata IPs that must NEVER be probed even if LAN HTTP is opted in.
_BLOCKED_METADATA_HOSTS = frozenset({
    "169.254.169.254",
    "169.254.170.2",
    "100.100.100.200",
    "metadata.google.internal",
    "metadata.goog",
    "fd00:ec2::254",
})


class QuotaHttpError(RuntimeError):
    """Structured outbound HTTP failure carrying status code and credential-safe message."""

    def __init__(self, code: str, message: str, *, status: int = 0, body_preview: str = ""):
        super().__init__(message)
        self.code = code
        self.status = status
        self.body_preview = body_preview


def _parse_ip_address(hostname: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    clean = hostname.strip().lower().rstrip(".")
    if clean.startswith("[") and clean.endswith("]"):
        clean = clean[1:-1]
    if "%" in clean:
        clean = clean.split("%", 1)[0]
    if not clean:
        return None
    try:
        ip = ipaddress.ip_address(clean)
    except ValueError:
        return None
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


def _is_metadata_or_link_local_host(hostname: str) -> bool:
    lower = hostname.strip().lower().rstrip(".")
    if lower.startswith("[") and lower.endswith("]"):
        lower = lower[1:-1]
    if "%" in lower:
        lower = lower.split("%", 1)[0]
    if lower in _BLOCKED_METADATA_HOSTS:
        return True
    ip = _parse_ip_address(lower)
    if ip is None:
        return False
    return str(ip) in _BLOCKED_METADATA_HOSTS or bool(ip.is_link_local)


def _is_private_or_local_host(hostname: str) -> bool:
    lower = hostname.strip().lower().rstrip(".")
    if not lower:
        return True
    if lower in ("localhost", "ip6-localhost", "ip6-loopback") or lower.endswith(".local"):
        return True
    ip = _parse_ip_address(lower)
    if ip is None:
        return False
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def validate_proxy_url(proxy_url: str) -> str:
    """Validate optional per-station proxy URL and block cloud metadata / link-local proxy hosts."""
    cleaned = str(proxy_url or "").strip()
    if not cleaned:
        return ""
    if len(cleaned) > 2048 or any(ord(ch) < 33 for ch in cleaned):
        raise QuotaHttpError("invalid_proxy", "代理地址格式无效")
    parsed = urlsplit(cleaned)
    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https", "socks5"):
        raise QuotaHttpError("invalid_proxy", "代理地址仅支持 http://、https:// 或 socks5://")
    hostname = (parsed.hostname or "").strip().lower()
    if not hostname or parsed.fragment:
        raise QuotaHttpError("invalid_proxy", "代理地址缺少有效主机名")
    if _is_metadata_or_link_local_host(hostname):
        raise QuotaHttpError("ssrf_blocked", "安全策略禁止将云元数据或链路本地地址作为代理")
    return cleaned


def validate_target_url(url: str, *, allow_http: bool = False) -> str:
    """Validate URL scheme, host, and SSRF boundaries before any outbound request."""
    if type(url) is not str or not url.strip() or len(url) > 2048:
        raise QuotaHttpError("invalid_url", "目标地址无效")
    cleaned = url.strip()
    if any(ord(ch) < 33 for ch in cleaned):
        raise QuotaHttpError("invalid_url", "目标地址包含非法空白或控制字符")
    parsed = urlsplit(cleaned)
    scheme = parsed.scheme.lower()
    if scheme not in ("https", "http"):
        raise QuotaHttpError("invalid_url", "仅支持 HTTP(S) 站点地址")
    hostname = (parsed.hostname or "").strip().lower()
    if not hostname or parsed.username is not None or parsed.password is not None or parsed.fragment:
        raise QuotaHttpError("invalid_url", "目标地址不能内嵌账号密码或片段")
    if _is_metadata_or_link_local_host(hostname):
        raise QuotaHttpError("ssrf_blocked", "安全策略禁止访问云元数据或链路本地地址")
    if scheme == "http" and not allow_http:
        raise QuotaHttpError(
            "http_not_allowed",
            "默认仅允许 HTTPS 站点；如为内网自建中转站，请在高级选项勾选「允许局域网 HTTP」",
        )
    if not allow_http and _is_private_or_local_host(hostname):
        raise QuotaHttpError(
            "ssrf_blocked",
            "安全策略默认禁止访问本机/内网地址；如确需监测内网站点，请勾选「允许局域网 HTTP / 内网地址」",
        )
    return cleaned


def normalize_station_root(url: str) -> str:
    """Strip common API suffixes (/v1, /v1beta, /api/user/self, /api/status) to obtain station root."""
    cleaned = str(url or "").strip()
    if not cleaned:
        return ""
    parsed = urlsplit(cleaned)
    if not parsed.scheme or not parsed.netloc:
        return cleaned.rstrip("/")
    path = (parsed.path or "").rstrip("/")
    lower = path.lower()
    for suffix in (
        "/api/user/self",
        "/api/status",
        "/api/pricing",
        "/api/v1/credits",
        "/api/v1/auth/key",
        "/api/v1",
        "/v1/chat/completions",
        "/v1/usage",
        "/v1/user/info",
        "/v1/users/me/balance",
        "/v1beta",
        "/v1",
    ):
        if lower.endswith(suffix):
            path = path[: len(path) - len(suffix)].rstrip("/")
            lower = path.lower()
            break
    return f"{parsed.scheme.lower()}://{parsed.netloc}{path}"


def same_origin(url_a: str, url_b: str) -> bool:
    try:
        pa = urlsplit(url_a)
        pb = urlsplit(url_b)
    except ValueError:
        return False
    sa, sb = pa.scheme.lower(), pb.scheme.lower()
    ha, hb = (pa.hostname or "").lower(), (pb.hostname or "").lower()
    if not sa or sa != sb or not ha or ha != hb:
        return False
    port_a = pa.port or (443 if sa == "https" else 80)
    port_b = pb.port or (443 if sb == "https" else 80)
    return port_a == port_b


def _build_session(
    *,
    timeout_seconds: float,
    proxy_url: str = "",
    cookie_jar: aiohttp.AbstractCookieJar | None = None,
    session_factory: Callable[..., Any] | None = None,
) -> tuple[Any, str | None]:
    """Create a bounded aiohttp session respecting explicit per-station proxy without silent fallback."""
    safe_proxy = validate_proxy_url(proxy_url) if proxy_url else ""
    if callable(session_factory):
        return session_factory(timeout_seconds=timeout_seconds, proxy_url=safe_proxy, cookie_jar=cookie_jar), None

    timeout = aiohttp.ClientTimeout(total=timeout_seconds, connect=min(5.0, timeout_seconds))
    connector = None
    request_proxy: str | None = None
    if safe_proxy:
        parsed = urlsplit(safe_proxy)
        scheme = parsed.scheme.lower()
        if scheme == "socks5":
            try:
                from aiohttp_socks import ProxyConnector
            except ImportError as exc:
                raise QuotaHttpError("proxy_dependency_missing", "SOCKS5 代理需要安装 aiohttp-socks 依赖") from exc
            connector = ProxyConnector.from_url(safe_proxy, rdns=True)
        elif scheme in ("http", "https"):
            request_proxy = safe_proxy
        else:
            raise QuotaHttpError("invalid_proxy", "代理地址仅支持 http://、https:// 或 socks5://")

    kwargs: dict[str, Any] = {
        "timeout": timeout,
        "trust_env": False,
    }
    if connector is not None:
        kwargs["connector"] = connector
    if cookie_jar is not None:
        kwargs["cookie_jar"] = cookie_jar
    return aiohttp.ClientSession(**kwargs), request_proxy


async def _read_stream_prefix(response: Any, *, max_bytes: int, chunk_size: int = 16384) -> bytes:
    """Read at most ``max_bytes`` from a streaming HTTP response without failing on larger bodies."""
    content = getattr(response, "content", None)
    if content is not None and hasattr(content, "iter_chunked"):
        buf = bytearray()
        async for chunk in content.iter_chunked(chunk_size):
            if not chunk:
                continue
            if not isinstance(chunk, (bytes, bytearray)):
                raise ValueError("response stream yielded non-bytes chunk")
            remaining = max_bytes - len(buf)
            if remaining <= 0:
                break
            buf.extend(chunk[:remaining])
            if len(buf) >= max_bytes:
                break
        return bytes(buf)
    if content is not None and hasattr(content, "read"):
        raw = await content.read(max_bytes)
    else:
        raw = await response.read()
    if not isinstance(raw, (bytes, bytearray)):
        raise ValueError("response body is not bytes")
    return bytes(raw[:max_bytes])


async def http_request_bytes(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    json_body: dict[str, Any] | None = None,
    allow_http: bool = False,
    proxy_url: str = "",
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    max_bytes: int = MAX_JSON_RESPONSE_BYTES,
    truncate_at_max_bytes: bool = False,
    secrets: tuple[str, ...] = (),
    session_factory: Callable[..., Any] | None = None,
    cookie_jar: aiohttp.AbstractCookieJar | None = None,
) -> tuple[int, dict[str, str], bytes]:
    """Perform one bounded, non-redirecting HTTP request with SSRF and secret redaction guards."""
    safe_url = validate_target_url(url, allow_http=allow_http)
    req_headers = {
        "Accept": "application/json",
        "User-Agent": BROWSER_USER_AGENT,
        **(headers or {}),
    }
    redact_pool = tuple(s for s in (*secrets, proxy_url) if s)
    session, request_proxy = _build_session(
        timeout_seconds=timeout_seconds,
        proxy_url=proxy_url,
        cookie_jar=cookie_jar,
        session_factory=session_factory,
    )
    req_kwargs: dict[str, Any] = {
        "headers": req_headers,
        "allow_redirects": False,
    }
    if request_proxy is not None:
        req_kwargs["proxy"] = request_proxy
    if json_body is not None:
        req_kwargs["json"] = json_body

    try:
        async with session:
            async with session.request(method, safe_url, **req_kwargs) as response:
                status = int(getattr(response, "status", 0))
                resp_headers = {str(k).lower(): str(v) for k, v in getattr(response, "headers", {}).items()}
                if truncate_at_max_bytes:
                    raw = await _read_stream_prefix(response, max_bytes=max_bytes)
                else:
                    content_length = getattr(response, "content_length", None)
                    if content_length is not None and content_length > max_bytes:
                        raise QuotaHttpError("response_too_large", "上游响应体积超过安全上限", status=status)
                    raw = await read_bounded_stream(response, max_bytes=max_bytes)
                return status, resp_headers, raw
    except QuotaHttpError:
        raise
    except asyncio.TimeoutError as exc:
        raise QuotaHttpError("timeout", "请求站点超时，请检查站点状态或代理配置") from exc
    except aiohttp.ClientError as exc:
        detail = redact_quota_text(str(exc), redact_pool, max_len=120)
        raise QuotaHttpError("network_error", f"网络连接失败：{detail or exc.__class__.__name__}") from exc
    except ValueError as exc:
        msg = str(exc)
        if "exceeded" in msg:
            raise QuotaHttpError("response_too_large", "上游响应体积超过安全上限") from exc
        raise QuotaHttpError("invalid_response", redact_quota_text(msg, redact_pool)) from exc
    except OSError as exc:
        detail = redact_quota_text(str(exc), redact_pool, max_len=120)
        raise QuotaHttpError("network_error", f"网络异常：{detail or exc.__class__.__name__}") from exc


_SENSITIVE_HEADER_NAMES = frozenset({
    "authorization",
    "proxy-authorization",
    "cookie",
    "x-api-key",
    "api-key",
    "new-api-user",
    "rix-api-user",
    "veloera-user",
})


async def http_request_public_bytes(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    allow_http: bool = False,
    origin_url: str = "",
    proxy_url: str = "",
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    max_bytes: int = MAX_JSON_RESPONSE_BYTES,
    truncate_at_max_bytes: bool = False,
    max_redirects: int = 2,
    session_factory: Callable[..., Any] | None = None,
) -> tuple[int, dict[str, str], bytes, str]:
    """Credential-free GET with bounded, SSRF-revalidated redirect following.

    Returns ``(status, resp_headers, body_bytes, final_url)``.
    Every redirect hop is re-validated via ``validate_target_url`` and NEVER inherits
    ``allow_http=True`` across origins or carries auth/cookie headers.
    """
    clean_headers: dict[str, str] = {}
    for key, value in (headers or {}).items():
        if str(key).strip().lower() in _SENSITIVE_HEADER_NAMES:
            continue
        clean_headers[str(key)] = str(value)

    base_origin = origin_url or url
    current_url = url
    hops = max(0, min(int(max_redirects), 3))

    for hop in range(hops + 1):
        hop_allow_http = bool(allow_http) and same_origin(base_origin, current_url)
        safe_url = validate_target_url(current_url, allow_http=hop_allow_http)
        status, resp_headers, raw = await http_request_bytes(
            "GET",
            safe_url,
            headers=clean_headers,
            allow_http=hop_allow_http,
            proxy_url=proxy_url,
            timeout_seconds=timeout_seconds,
            max_bytes=max_bytes,
            truncate_at_max_bytes=truncate_at_max_bytes,
            session_factory=session_factory,
        )
        if status in (301, 302, 303, 307, 308):
            location = (resp_headers.get("location") or "").strip()
            if not location:
                return status, resp_headers, raw, safe_url
            if hop >= hops:
                raise QuotaHttpError("too_many_redirects", "重定向次数超过安全上限", status=status)
            next_url = urljoin(safe_url, location)
            next_allow_http = bool(allow_http) and same_origin(base_origin, next_url)
            validate_target_url(next_url, allow_http=next_allow_http)
            current_url = next_url
            continue
        return status, resp_headers, raw, safe_url

    raise QuotaHttpError("too_many_redirects", "重定向次数超过安全上限")


async def http_request_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    json_body: dict[str, Any] | None = None,
    allow_http: bool = False,
    proxy_url: str = "",
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    max_bytes: int = MAX_JSON_RESPONSE_BYTES,
    secrets: tuple[str, ...] = (),
    session_factory: Callable[..., Any] | None = None,
    cookie_jar: aiohttp.AbstractCookieJar | None = None,
) -> tuple[int, dict[str, str], Any, str]:
    """Return ``(status, headers, parsed_json_or_None, redacted_text_preview)``."""
    status, resp_headers, raw = await http_request_bytes(
        method,
        url,
        headers=headers,
        json_body=json_body,
        allow_http=allow_http,
        proxy_url=proxy_url,
        timeout_seconds=timeout_seconds,
        max_bytes=max_bytes,
        secrets=secrets,
        session_factory=session_factory,
        cookie_jar=cookie_jar,
    )
    text = raw.decode("utf-8", errors="replace")
    preview = redact_quota_text(text, (*secrets, proxy_url), max_len=200)
    if 300 <= status < 400:
        location = redact_quota_text(resp_headers.get("location", ""), (*secrets, proxy_url), max_len=120)
        raise QuotaHttpError(
            "redirect_blocked",
            f"站点返回重定向 (HTTP {status})，已出于凭据安全拦截，请填写最终真实域名"
            + (f"（目标：{location}）" if location else ""),
            status=status,
            body_preview=preview,
        )
    parsed: Any = None
    try:
        parsed = json.loads(text)
    except ValueError:
        parsed = None
    return status, resp_headers, parsed, preview


def safe_number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        num = float(value)
    elif isinstance(value, str):
        cleaned = value.strip().replace(",", "")
        if not cleaned:
            return None
        try:
            num = float(cleaned)
        except ValueError:
            return None
    else:
        return None
    return num if math.isfinite(num) else None


@dataclass(frozen=True)
class FieldSpec:
    """Declarative form field specification driven by backend adapters."""

    name: str
    label: str
    kind: str = "text"  # text | password | number | select | textarea | checkbox
    required: bool = False
    group: str = "primary"  # primary | advanced
    placeholder: str = ""
    hint: str = ""
    default: Any = ""
    options: tuple[tuple[str, str], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "kind": self.kind,
            "required": self.required,
            "group": self.group,
            "placeholder": self.placeholder,
            "hint": self.hint,
            "default": self.default,
            "options": [{"value": v, "label": lbl} for v, lbl in self.options],
        }


@dataclass(frozen=True)
class AdapterCapabilities:
    supports_provider_reuse: bool = False
    supports_last_usage: bool = False
    supports_logo: bool = False
    supports_group_plan: bool = False
    supports_one_time_login: bool = False
    supports_custom_divisor: bool = False
    is_key_scoped: bool = False
    is_unsupported_official: bool = False

    def to_dict(self) -> dict[str, bool]:
        return {
            "supports_provider_reuse": self.supports_provider_reuse,
            "supports_last_usage": self.supports_last_usage,
            "supports_logo": self.supports_logo,
            "supports_group_plan": self.supports_group_plan,
            "supports_one_time_login": self.supports_one_time_login,
            "supports_custom_divisor": self.supports_custom_divisor,
            "is_key_scoped": self.is_key_scoped,
            "is_unsupported_official": self.is_unsupported_official,
        }


@dataclass(frozen=True)
class AdapterDescriptor:
    id: str
    name: str
    short_name: str
    category: str  # relay | official | script | unsupported
    summary: str
    auth_guide: str
    capabilities: AdapterCapabilities = field(default_factory=AdapterCapabilities)
    fields: tuple[FieldSpec, ...] = ()
    default_user_id_header: str = "New-Api-User"
    default_display_type: str = ""
    default_quota_per_unit: float = 0.0
    order: int = 100

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "short_name": self.short_name,
            "category": self.category,
            "summary": self.summary,
            "auth_guide": self.auth_guide,
            "capabilities": self.capabilities.to_dict(),
            "fields": [f.to_dict() for f in self.fields],
            "default_user_id_header": self.default_user_id_header,
            "default_display_type": self.default_display_type,
            "default_quota_per_unit": self.default_quota_per_unit,
            "order": self.order,
        }


@dataclass
class AdapterProbeContext:
    """Resolved per-request execution context supplied by QuotaWatchService."""

    base_url: str
    auth_token: str = ""
    user_id: str = ""
    user_id_header: str = "New-Api-User"
    proxy_url: str = ""
    custom_script: str = ""
    display_type: str = ""
    quota_per_unit: float = 0.0
    fiat_currency: str = ""
    fiat_per_base_unit: float | None = None
    allow_http: bool = False
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    session_factory: Callable[..., Any] | None = None
    now: Callable[[], datetime] = field(default_factory=lambda: (lambda: datetime.now(timezone.utc)))
    logo_cache: Any = None
    astrbot_api_key: str = ""

    def effective_token(self) -> str:
        return (self.auth_token or self.astrbot_api_key or "").strip()

    def now_iso(self) -> str:
        current = self.now()
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        return current.isoformat()

    def elapsed_ms(self, started: float) -> int:
        return max(0, int((time.monotonic() - started) * 1000))


class QuotaAdapter(ABC):
    """Base interface implemented by every station quota protocol adapter."""

    @property
    @abstractmethod
    def descriptor(self) -> AdapterDescriptor:
        raise NotImplementedError

    @abstractmethod
    async def probe(self, spec: QuotaStationSpec, ctx: AdapterProbeContext) -> QuotaSnapshot:
        raise NotImplementedError


class AdapterRegistry:
    """Thread-safe registry of quota protocol adapters."""

    def __init__(self) -> None:
        self._adapters: dict[str, QuotaAdapter] = {}

    def register(self, adapter: QuotaAdapter) -> QuotaAdapter:
        desc = adapter.descriptor
        self._adapters[desc.id] = adapter
        return adapter

    def get(self, adapter_id: str) -> QuotaAdapter | None:
        return self._adapters.get(str(adapter_id or "").strip())

    def all(self) -> list[QuotaAdapter]:
        return sorted(self._adapters.values(), key=lambda a: (a.descriptor.order, a.descriptor.id))

    def list_descriptors(self) -> list[dict[str, Any]]:
        return [adapter.descriptor.to_dict() for adapter in self.all()]
