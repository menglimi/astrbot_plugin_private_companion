"""Host-side asynchronous multi-source price catalog sync and New API auto-probe.

All outbound HTTP traffic for ``models.dev``, ``LiteLLM``, ``OpenRouter``, and
``New API /api/pricing`` lives strictly in ``astrbot_host/`` so ``companion_v2/``
remains 100% pure stdlib with zero network dependencies.

Security & resilience guarantees:
- Streaming ``iter_chunked(65536)`` with strict 16 MB decoded cap (never truncates
  multi-MB payloads and never buffers unbounded responses).
- ``trust_env=True`` + AstrBot ``http_proxy`` / per-provider ``proxy`` fallback.
- ``Accept-Encoding: gzip, deflate``, ``ClientTimeout(total=30, connect=10)``,
  2x transient retry with jitter, ``ETag`` / ``If-None-Match`` 304 handling.
- Same-origin ``(scheme, host, port)`` verification AND ``allow_redirects=False``
  whenever sending ``Authorization: Bearer`` to New API ``/api/pricing``.
- Never logs or serializes API keys or proxy credentials.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import logging
import math
import random
import re
from time import monotonic
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit

import aiohttp

from companion_v2.model_gateway.pricing import (
    CATALOG_DAILY_SYNC_SECONDS,
    PricingSettingsStore,
    is_catalog_sync_due,
)
from companion_v2.model_gateway.pricing_catalog import (
    NEWAPI_SITE_META_SCHEMA_VERSION,
    PUBLIC_SOURCE_SPECS,
    CatalogPrice,
    PriceCatalogStore,
    SourceStatus,
    adapt_litellm,
    adapt_modelsdev,
    adapt_newapi,
    adapt_openrouter,
    extract_newapi_pricing_meta,
    infer_newapi_cluster_group,
    parse_newapi_status,
    parse_newapi_topup_info,
    resolve_newapi_currency_params,
)

from .exchange_rate import normalize_http_proxy, read_bounded_stream

logger = logging.getLogger(__name__)

MAX_CATALOG_RESPONSE_BYTES = 16 * 1024 * 1024  # 16 MB decoded cap
CATALOG_TIMEOUT_TOTAL_SECONDS = 30.0
CATALOG_TIMEOUT_CONNECT_SECONDS = 10.0
MAX_TRANSIENT_RETRIES = 2
DEFAULT_RETRY_BASE_SECONDS = 0.4
SYNC_CONCURRENCY = 8
DAILY_SYNC_FAILURE_BACKOFF_SECONDS = 600.0
DEFAULT_DEBOUNCE_SECONDS = 0.05

_TRANSIENT_HTTP_STATUSES = frozenset({429, 500, 502, 503, 504})
_PUBLIC_SPECS_BY_ID = {spec["source_id"]: spec for spec in PUBLIC_SOURCE_SPECS}


def normalized_origin(url: object) -> tuple[str, str, int] | None:
    """Return canonical ``(scheme, host, port)`` or ``None`` for invalid/non-HTTP URLs."""
    if type(url) is not str or not url.strip():
        return None
    try:
        parsed = urlsplit(url.strip())
    except ValueError:
        return None
    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        return None
    host = (parsed.hostname or "").strip().lower()
    if not host:
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    if port is None:
        port = 443 if scheme == "https" else 80
    return scheme, host, port


def safe_origin_label(url: object) -> str:
    """Format only ``scheme://host:port`` for logs (never leaks paths, query, or userinfo)."""
    origin = normalized_origin(url)
    if origin is None:
        return "<invalid-origin>"
    scheme, host, port = origin
    if ":" in host:
        host = f"[{host}]"
    return f"{scheme}://{host}:{port}"


def derive_newapi_root(api_base: object) -> str | None:
    """Strip trailing ``/`` and ``/v1``, plus any query/fragment/userinfo, returning root URL."""
    if type(api_base) is not str or not api_base.strip():
        return None
    try:
        parsed = urlsplit(api_base.strip())
    except ValueError:
        return None
    origin = normalized_origin(api_base)
    if origin is None:
        return None
    scheme, host, port = origin
    default_port = 443 if scheme == "https" else 80
    host_display = f"[{host}]" if ":" in host else host
    netloc = host_display if port == default_port else f"{host_display}:{port}"

    path = (parsed.path or "").rstrip("/")
    lower_path = path.lower()
    if lower_path.endswith("/v1"):
        path = path[:-3].rstrip("/")
    elif lower_path.endswith("/v1beta"):
        path = path[:-7].rstrip("/")
    return f"{scheme}://{netloc}{path}"


def newapi_pricing_url(api_base: object) -> str | None:
    root = derive_newapi_root(api_base)
    if not root:
        return None
    return f"{root}/api/pricing"


def newapi_status_url(api_base: object) -> str | None:
    root = derive_newapi_root(api_base)
    if not root:
        return None
    return f"{root}/api/status"


def newapi_models_url(api_base: object) -> str | None:
    root = derive_newapi_root(api_base)
    if not root:
        return None
    return f"{root}/v1/models"


_HEADER_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9-]{0,63}$")


def _status_url_from_pricing_url(pricing_url: str) -> str | None:
    clean = str(pricing_url or "").strip().rstrip("/")
    if not clean:
        return None
    if clean.lower().endswith("/api/pricing"):
        return clean[:-12] + "/api/status"
    return newapi_status_url(clean)


def _topup_info_url_from_pricing_url(pricing_url: str) -> str | None:
    clean = str(pricing_url or "").strip().rstrip("/")
    if not clean:
        return None
    if clean.lower().endswith("/api/pricing"):
        return clean[:-12] + "/api/user/topup/info"
    root = derive_newapi_root(clean)
    return f"{root}/api/user/topup/info" if root else None


def same_origin(url_a: object, url_b: object) -> bool:
    oa = normalized_origin(url_a)
    ob = normalized_origin(url_b)
    return oa is not None and ob is not None and oa == ob


def _same_origin_for_station(target_url: object, station: dict[str, Any] | None) -> str | None:
    """Return a canonical origin guard URL if ``target_url`` is authorized for ``station`` credentials."""
    if not isinstance(station, dict):
        return None
    target_origin = normalized_origin(target_url)
    if target_origin is None:
        return None
    t_scheme, _t_host, _t_port = target_origin
    if t_scheme == "http" and station.get("allow_http") is not True:
        return None
    for field in ("base_url", "server_address"):
        candidate = station.get(field)
        cand_origin = normalized_origin(candidate)
        if cand_origin is not None and cand_origin == target_origin:
            return str(candidate).strip()
    return None


def _station_auth_params(
    target_url: str,
    station: dict[str, Any] | None,
) -> tuple[str | None, str | None, dict[str, str] | None]:
    """Return ``(bearer_token, origin_guard_base, extra_headers)`` strictly when same-origin passes."""
    if not isinstance(station, dict):
        return None, None, None
    token = str(station.get("auth_token") or "").strip()
    if not token:
        return None, None, None
    guard_base = _same_origin_for_station(target_url, station)
    if not guard_base:
        return None, None, None
    extra_headers: dict[str, str] = {}
    uid = str(station.get("user_id") or "").strip()
    if uid:
        raw_hdr = str(station.get("user_id_header") or "New-Api-User").strip()
        hdr_name = raw_hdr if _HEADER_NAME_RE.fullmatch(raw_hdr) else "New-Api-User"
        extra_headers[hdr_name] = uid
    return token, guard_base, extra_headers or None


def _positive_finite(value: object, *, max_val: float = 100_000.0) -> float | None:
    if isinstance(value, bool) or type(value) not in (int, float):
        return None
    num = float(value)
    if not math.isfinite(num) or num <= 0.0 or num > max_val:
        return None
    return num


_QUOTA_SITE_META_KEYS: tuple[str, ...] = (
    "quota_station_id",
    "quota_station_name",
    "quota_user_group",
    "quota_fiat_currency",
    "quota_fiat_per_base_unit",
    "observed_group_ratios",
)


def _prune_quota_detected_groups(merged_site_meta: dict[str, Any]) -> None:
    raw_detected = merged_site_meta.get("detected_groups")
    if not isinstance(raw_detected, dict):
        return
    cleaned = {
        k: v
        for k, v in raw_detected.items()
        if k != "__station__"
        and not (isinstance(v, dict) and v.get("source") == "quota_watch")
    }
    if cleaned:
        merged_site_meta["detected_groups"] = cleaned
    else:
        merged_site_meta.pop("detected_groups", None)


def _apply_topup_enrichment(
    merged_site_meta: dict[str, Any],
    topup_info: dict[str, Any] | None,
    *,
    has_fresh_status: bool = False,
) -> None:
    """Apply or cleanly clear authenticated topup metadata so unbound stations leave zero residue."""
    if topup_info is not None:
        merged_site_meta["topup_rmb_confirmed"] = bool(topup_info.get("rmb_topup_confirmed"))
        if topup_info.get("pay_method_types"):
            merged_site_meta["topup_pay_methods"] = list(topup_info["pay_method_types"])
        else:
            merged_site_meta.pop("topup_pay_methods", None)
        if topup_info.get("discount_tiers"):
            merged_site_meta["topup_discount_tiers"] = dict(topup_info["discount_tiers"])
        else:
            merged_site_meta.pop("topup_discount_tiers", None)
    else:
        merged_site_meta["topup_rmb_confirmed"] = False
        merged_site_meta.pop("topup_pay_methods", None)
        merged_site_meta.pop("topup_discount_tiers", None)
        if not has_fresh_status and merged_site_meta.get("currency_source") == "topup_custom_cny":
            merged_site_meta["currency_source"] = "status_custom"


def _apply_station_enrichment(
    merged_site_meta: dict[str, Any],
    pricing_meta: dict[str, Any],
    station: dict[str, Any] | None,
    *,
    cluster_id: str | None = None,
) -> None:
    """Absorb strictly non-secret metadata & group hints from a linked Quota Watch station, or clear on unbind."""
    _prune_quota_detected_groups(merged_site_meta)
    if not isinstance(station, dict):
        for key in _QUOTA_SITE_META_KEYS:
            merged_site_meta.pop(key, None)
        return
    station_id = str(station.get("station_id") or "").strip()[:64]
    station_name = str(station.get("station_name") or "").strip()[:64]
    if station_id:
        merged_site_meta["quota_station_id"] = station_id
    else:
        merged_site_meta.pop("quota_station_id", None)
    if station_name:
        merged_site_meta["quota_station_name"] = station_name
    else:
        merged_site_meta.pop("quota_station_name", None)

    group_name = str(station.get("group_name") or "").strip()[:64]
    if group_name:
        merged_site_meta["quota_user_group"] = group_name
    else:
        merged_site_meta.pop("quota_user_group", None)

    fiat_cur = str(station.get("fiat_currency") or "").strip().upper()
    if fiat_cur in ("CNY", "USD"):
        merged_site_meta["quota_fiat_currency"] = fiat_cur
    else:
        merged_site_meta.pop("quota_fiat_currency", None)

    fiat_rate = _positive_finite(station.get("fiat_per_base_unit"))
    if fiat_rate is not None:
        merged_site_meta["quota_fiat_per_base_unit"] = round(fiat_rate, 6)
    else:
        merged_site_meta.pop("quota_fiat_per_base_unit", None)

    if not merged_site_meta.get("custom_currency_symbol"):
        sym = str(station.get("custom_currency_symbol") or "").strip()[:12]
        if sym and sym != "¤":
            merged_site_meta["custom_currency_symbol"] = sym
    if merged_site_meta.get("custom_currency_exchange_rate") is None:
        crate = _positive_finite(station.get("custom_currency_exchange_rate"))
        if crate is not None:
            merged_site_meta["custom_currency_exchange_rate"] = round(crate, 6)

    group_ratio = pricing_meta.get("group_ratio") if isinstance(pricing_meta.get("group_ratio"), dict) else {}
    raw_observed = station.get("observed_group_ratios")
    clean_observed: dict[str, float] = {}
    if isinstance(raw_observed, dict):
        for raw_k, raw_v in list(raw_observed.items())[:32]:
            if type(raw_k) is not str or not raw_k.strip():
                continue
            gkey = raw_k.strip()[:64]
            if isinstance(raw_v, dict):
                gname = str(raw_v.get("group") or gkey).strip()[:64]
                ratio = _positive_finite(raw_v.get("ratio"), max_val=1000.0)
                if gname and ratio is not None:
                    clean_observed[gname] = round(ratio, 6)
            else:
                ratio = _positive_finite(raw_v, max_val=1000.0)
                if ratio is not None:
                    clean_observed[gkey] = round(ratio, 6)
    if clean_observed:
        merged_site_meta["observed_group_ratios"] = clean_observed
    else:
        merged_site_meta.pop("observed_group_ratios", None)

    detected_map = dict(merged_site_meta.get("detected_groups") or {})
    target_cluster = str(cluster_id or "").strip()

    # Record station-level hint without overwriting a higher-confidence per-cluster /v1/models exact match.
    station_hint: dict[str, Any] | None = None
    if len(clean_observed) == 1:
        obs_group, obs_ratio = next(iter(clean_observed.items()))
        station_hint = {
            "group": obs_group,
            "ratio": obs_ratio,
            "confidence": "quota_logs",
            "source": "quota_watch",
        }
    elif group_name and group_name in group_ratio:
        ratio_val = group_ratio[group_name]
        if type(ratio_val) in (int, float) and math.isfinite(float(ratio_val)):
            station_hint = {
                "group": group_name,
                "ratio": round(float(ratio_val), 6),
                "confidence": "quota_user_group",
                "source": "quota_watch",
            }
    elif group_name and group_name in clean_observed:
        station_hint = {
            "group": group_name,
            "ratio": clean_observed[group_name],
            "confidence": "quota_logs",
            "source": "quota_watch",
        }

    if station_hint is not None:
        detected_map["__station__"] = station_hint
        if target_cluster:
            existing = detected_map.get(target_cluster)
            if not isinstance(existing, dict) or existing.get("confidence") != "exact":
                detected_map[target_cluster] = dict(station_hint)
        merged_site_meta["detected_groups"] = detected_map


def find_newapi_sources_needing_backfill(
    settings: dict[str, Any],
    catalog: dict[str, Any],
) -> list[str]:
    """Return enabled ``newapi:*`` source IDs missing ``site_meta`` or on schema < v2."""
    cfg_sources = settings.get("price_sources") or {}
    cat_sources = catalog.get("sources") or {}
    result: list[str] = []
    for sid, scfg in cfg_sources.items():
        if type(sid) is not str or not sid.startswith("newapi:") or type(scfg) is not dict:
            continue
        if scfg.get("enabled", True) is not True:
            continue
        scat = cat_sources.get(sid)
        smeta = scat.get("site_meta") if isinstance(scat, dict) else None
        if not isinstance(smeta, dict):
            result.append(sid)
            continue
        raw_ver = smeta.get("schema_version")
        ver = int(raw_ver) if type(raw_ver) is int else 0
        if ver < NEWAPI_SITE_META_SCHEMA_VERSION:
            result.append(sid)
    return result


def find_newapi_source_by_url(
    settings: dict[str, Any],
    target_url: str,
    *,
    cluster_id: str | None = None,
    auth_mode: str | None = None,
    has_key: bool = False,
) -> str | None:
    """Return existing ``newapi:*`` source_id matching ``target_url`` with auth-gated cluster isolation.

    Public anonymous gateways deduplicate by URL across clusters on the same domain;
    auth-gated gateways requiring Bearer keys isolate distinct clusters that supply
    their own keys so two private-key groups never clobber one another.
    """
    norm_target = target_url.rstrip("/").lower()
    sources = settings.get("price_sources") or {}
    matches: list[tuple[str, dict[str, Any]]] = []
    for sid, cfg in sources.items():
        if type(sid) is not str or not sid.startswith("newapi:") or type(cfg) is not dict:
            continue
        existing_url = str(cfg.get("url") or "").strip().rstrip("/").lower()
        if existing_url and existing_url == norm_target:
            matches.append((sid, cfg))
    if not matches:
        return None

    clean_cluster = str(cluster_id or "").strip()
    if clean_cluster:
        for sid, cfg in matches:
            existing_cluster = str(cfg.get("cluster_id") or "").strip()
            existing_pid = str(cfg.get("provider_id") or "").strip()
            if existing_cluster == clean_cluster or existing_pid == clean_cluster:
                return sid

    if auth_mode == "bearer":
        # Auth-gated Bearer sources are strictly scoped by cluster_id (already checked above);
        # never reuse another cluster's Bearer source or a legacy source with unknown auth_mode.
        return None

    for sid, cfg in matches:
        if cfg.get("auth_mode") == "anonymous":
            return sid
    for sid, cfg in matches:
        if cfg.get("auth_mode") != "bearer":
            return sid
    return None


def _default_catalog_session_factory(timeout_seconds: float) -> aiohttp.ClientSession:
    return aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(
            total=timeout_seconds,
            connect=min(CATALOG_TIMEOUT_CONNECT_SECONDS, timeout_seconds),
        ),
        trust_env=True,
        headers={
            "Accept": "application/json",
            "Accept-Encoding": "gzip, deflate",
            "User-Agent": "astrbot-private-companion/pricing",
        },
    )


def _is_transient_error(error: Exception) -> bool:
    if isinstance(error, (
        asyncio.TimeoutError,
        aiohttp.ClientConnectionError,
        aiohttp.ClientOSError,
        aiohttp.ServerDisconnectedError,
        ConnectionError,
        OSError,
    )):
        return True
    if isinstance(error, _TransientHttpStatusError):
        return True
    return False


class _TransientHttpStatusError(RuntimeError):
    def __init__(self, status: int):
        super().__init__(f"HTTP {status}")
        self.status = status


class _HttpStatusError(RuntimeError):
    def __init__(self, status: int):
        super().__init__(f"HTTP {status}")
        self.status = status


def _safe_sync_error(error: Exception) -> str:
    if isinstance(error, asyncio.TimeoutError):
        return "请求超时"
    if isinstance(error, _TransientHttpStatusError):
        return f"上游返回 HTTP {error.status}"
    if isinstance(error, _HttpStatusError):
        return f"上游返回 HTTP {error.status}"
    if isinstance(error, aiohttp.ClientError):
        return "网络连接失败"
    if isinstance(error, ValueError):
        msg = str(error)
        if "exceeded" in msg:
            return "响应体超过 16 MB 安全上限"
        return "返回数据格式无效"
    return "同步失败"


class PricingCatalogService:
    """Async proxy-safe multi-source price catalog fetcher & New API probe service."""

    def __init__(
        self,
        settings_store: PricingSettingsStore,
        catalog_store: PriceCatalogStore,
        *,
        now: Callable[[], datetime] | None = None,
        session_factory: Callable[[float], Any] | None = None,
        proxy_resolver: Callable[[], str | None] | None = None,
        connections_resolver: Callable[[], dict[str, dict[str, Any]]] | None = None,
        quota_station_resolver: Callable[[str], dict[str, Any] | None] | None = None,
        timeout_seconds: float = CATALOG_TIMEOUT_TOTAL_SECONDS,
        max_retries: int = MAX_TRANSIENT_RETRIES,
        retry_base_seconds: float = DEFAULT_RETRY_BASE_SECONDS,
        debounce_seconds: float = DEFAULT_DEBOUNCE_SECONDS,
        failure_backoff_seconds: float = DAILY_SYNC_FAILURE_BACKOFF_SECONDS,
        sleep_fn: Callable[[float], Awaitable[None]] | None = None,
    ):
        self.settings_store = settings_store
        self.catalog_store = catalog_store
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._session_factory = session_factory or _default_catalog_session_factory
        self._proxy_resolver = proxy_resolver
        self._connections_resolver = connections_resolver
        self._quota_station_resolver = quota_station_resolver
        self.timeout_seconds = float(timeout_seconds)
        self.max_retries = max(0, int(max_retries))
        self.retry_base_seconds = max(0.0, float(retry_base_seconds))
        self.debounce_seconds = max(0.0, float(debounce_seconds))
        self.failure_backoff_seconds = max(0.0, float(failure_backoff_seconds))
        self._sleep = sleep_fn or asyncio.sleep
        self._lock_loop: asyncio.AbstractEventLoop | None = None
        self._sync_lock: asyncio.Lock | None = None
        self._bg_task: asyncio.Task | None = None
        self._last_daily_failure_monotonic: float | None = None
        self._attempted_backfill_ids: set[str] = set()

    def set_quota_station_resolver(
        self,
        resolver: Callable[[str], dict[str, Any] | None] | None,
    ) -> None:
        """Attach an optional read-only Quota Watch resolver (in-memory only, never persisted)."""
        self._quota_station_resolver = resolver

    def _resolve_quota_station(self, origin_or_url: str) -> dict[str, Any] | None:
        resolver = self._quota_station_resolver
        if not callable(resolver) or not origin_or_url:
            return None
        try:
            station = resolver(origin_or_url)
            return dict(station) if isinstance(station, dict) else None
        except Exception:
            return None

    def _get_lock(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._sync_lock is None or self._lock_loop is not loop:
            self._sync_lock = asyncio.Lock()
            self._lock_loop = loop
        return self._sync_lock

    def _current_time(self) -> datetime:
        current = self._now()
        return current.replace(tzinfo=timezone.utc) if current.tzinfo is None else current

    def _resolve_proxy(self, provider_proxy: object = None) -> str | None:
        direct = normalize_http_proxy(provider_proxy)
        if direct is not None:
            return direct
        if callable(self._proxy_resolver):
            try:
                return normalize_http_proxy(self._proxy_resolver())
            except Exception:
                return None
        return None

    def _resolve_connections(self) -> dict[str, dict[str, Any]]:
        if not callable(self._connections_resolver):
            return {}
        try:
            conns = self._connections_resolver()
            return conns if isinstance(conns, dict) else {}
        except Exception:
            return {}

    async def _fetch_json_with_retry(
        self,
        session: Any,
        url: str,
        *,
        etag: str | None = None,
        bearer_key: str | None = None,
        origin_guard_base: str | None = None,
        extra_headers: dict[str, str] | None = None,
        provider_proxy: object = None,
        allow_redirects: bool = True,
        max_retries_override: int | None = None,
    ) -> tuple[int, Any, str | None]:
        """Fetch bounded JSON with ETag 304, same-origin Bearer guard, and 2x jitter retry."""
        headers: dict[str, str] = {}
        if etag:
            headers["If-None-Match"] = etag

        effective_redirects = allow_redirects
        if bearer_key and type(bearer_key) is str and bearer_key.strip():
            # Enforce strict same-origin (scheme, host, port) AND disallow redirects when sending Bearer!
            if not origin_guard_base or not same_origin(url, origin_guard_base):
                raise ValueError("refusing to send Bearer token across origins")
            headers["Authorization"] = f"Bearer {bearer_key.strip()}"
            if isinstance(extra_headers, dict):
                for hk, hv in extra_headers.items():
                    if (
                        type(hk) is str
                        and type(hv) is str
                        and _HEADER_NAME_RE.fullmatch(hk.strip())
                        and hk.strip().lower() != "authorization"
                        and hv.strip()
                    ):
                        headers[hk.strip()] = hv.strip()
            effective_redirects = False

        proxy = self._resolve_proxy(provider_proxy)
        req_kwargs: dict[str, Any] = {}
        if headers:
            req_kwargs["headers"] = headers
        if proxy is not None:
            req_kwargs["proxy"] = proxy
        if not effective_redirects:
            req_kwargs["allow_redirects"] = False

        retries = self.max_retries if max_retries_override is None else max(0, int(max_retries_override))
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            try:
                async with session.get(url, **req_kwargs) as response:
                    status = int(getattr(response, "status", 0))
                    resp_headers = getattr(response, "headers", None) or {}
                    resp_etag = resp_headers.get("ETag") or resp_headers.get("etag")
                    if status == 304:
                        return 304, None, str(resp_etag) if resp_etag else etag
                    if status in _TRANSIENT_HTTP_STATUSES:
                        raise _TransientHttpStatusError(status)
                    if status != 200:
                        raise _HttpStatusError(status)
                    raw = await read_bounded_stream(
                        response, max_bytes=MAX_CATALOG_RESPONSE_BYTES
                    )
                    payload = json.loads(raw.decode("utf-8"))
                    return 200, payload, str(resp_etag) if resp_etag else None
            except asyncio.CancelledError:
                raise
            except Exception as error:
                last_error = error
                if attempt < retries and _is_transient_error(error):
                    delay = self.retry_base_seconds * (2 ** attempt) + random.uniform(0.0, 0.25)
                    if delay > 0:
                        await self._sleep(delay)
                    continue
                raise
        assert last_error is not None
        raise last_error

    async def _fetch_newapi_status_raw(
        self,
        session: Any,
        pricing_url: str,
        *,
        provider_proxy: object = None,
    ) -> dict[str, Any] | None:
        """Best-effort anonymous GET ``<root>/api/status`` returning raw decoded JSON dict."""
        status_url = _status_url_from_pricing_url(pricing_url)
        if not status_url:
            return None
        try:
            code, payload, _etag = await self._fetch_json_with_retry(
                session,
                status_url,
                bearer_key=None,
                provider_proxy=provider_proxy,
                allow_redirects=False,
                max_retries_override=0,
            )
            if code != 200 or not isinstance(payload, dict):
                return None
            if payload.get("success") is False:
                return None
            data = payload.get("data")
            if isinstance(data, dict):
                return payload
            if data is None and any(
                k in payload for k in ("quota_display_type", "system_name", "quota_per_unit", "usd_exchange_rate")
            ):
                return payload
            return None
        except asyncio.CancelledError:
            raise
        except Exception:
            return None

    async def _fetch_newapi_status(
        self,
        session: Any,
        pricing_url: str,
        *,
        provider_proxy: object = None,
        topup_info: dict[str, Any] | None = None,
        pricing_meta: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Best-effort anonymous GET ``<root>/api/status`` for currency & site metadata."""
        raw = await self._fetch_newapi_status_raw(
            session, pricing_url, provider_proxy=provider_proxy
        )
        if raw is None:
            return None
        return parse_newapi_status(raw, topup_info=topup_info, pricing_meta=pricing_meta)

    async def _fetch_newapi_topup_info(
        self,
        session: Any,
        pricing_url: str,
        station: dict[str, Any] | None,
        *,
        provider_proxy: object = None,
    ) -> dict[str, Any] | None:
        """Best-effort authenticated GET ``<root>/api/user/topup/info`` using linked Quota Watch token."""
        if not isinstance(station, dict):
            return None
        preparsed = station.get("topup_info")
        if isinstance(preparsed, dict):
            parsed = parse_newapi_topup_info(preparsed)
            if parsed is not None:
                return parsed
        topup_url = _topup_info_url_from_pricing_url(pricing_url)
        if not topup_url:
            return None
        st_token, guard_base, st_headers = _station_auth_params(topup_url, station)
        if not st_token or not guard_base:
            return None
        try:
            code, payload, _etag = await self._fetch_json_with_retry(
                session,
                topup_url,
                bearer_key=st_token,
                origin_guard_base=guard_base,
                extra_headers=st_headers,
                provider_proxy=provider_proxy or station.get("proxy_url"),
                allow_redirects=False,
                max_retries_override=0,
            )
            if code != 200 or not isinstance(payload, dict):
                return None
            return parse_newapi_topup_info(payload)
        except asyncio.CancelledError:
            raise
        except Exception:
            return None

    async def _fetch_newapi_models_for_group(
        self,
        session: Any,
        api_base: str,
        api_key: str,
        *,
        provider_proxy: object = None,
    ) -> set[str] | None:
        """Best-effort same-origin GET ``<root>/v1/models`` to infer a key's New API group."""
        clean_key = str(api_key or "").strip()
        models_url = newapi_models_url(api_base)
        if not clean_key or not models_url or not same_origin(models_url, api_base):
            return None
        try:
            code, payload, _etag = await self._fetch_json_with_retry(
                session,
                models_url,
                bearer_key=clean_key,
                origin_guard_base=api_base,
                provider_proxy=provider_proxy,
                allow_redirects=False,
                max_retries_override=0,
            )
            if code != 200 or not isinstance(payload, dict):
                return None
            items = payload.get("data")
            if not isinstance(items, list) or not items:
                return None
            models = {
                str(item.get("id")).strip()
                for item in items
                if isinstance(item, dict) and type(item.get("id")) is str and item["id"].strip()
            }
            return models or None
        except asyncio.CancelledError:
            raise
        except Exception:
            return None

    @staticmethod
    def _find_connection_for_newapi_source(
        connections: dict[str, dict[str, Any]],
        *,
        provider_id: str | None,
        cluster_id: str | None,
        pricing_url: str,
        auth_mode: str | None = None,
    ) -> dict[str, Any]:
        if provider_id and provider_id in connections:
            return connections[provider_id]
        norm_url = pricing_url.rstrip("/").lower()
        strict_cluster = (auth_mode == "bearer")
        fallback: dict[str, Any] = {}
        for conn in connections.values():
            if not isinstance(conn, dict):
                continue
            cand_url = newapi_pricing_url(conn.get("api_base"))
            if not cand_url or cand_url.rstrip("/").lower() != norm_url:
                continue
            if cluster_id and str(conn.get("cluster_id") or "").strip() == cluster_id:
                return conn
            if not strict_cluster and not fallback:
                fallback = conn
        return {} if strict_cluster else fallback

    async def _sync_one_source(
        self,
        session: Any,
        semaphore: asyncio.Semaphore,
        source_id: str,
        source_cfg: dict[str, Any],
        existing_status: dict[str, Any] | None,
        connections: dict[str, dict[str, Any]],
        now_iso: str,
    ) -> tuple[SourceStatus, list[CatalogPrice] | None, bool]:
        """Sync one price source; returns ``(SourceStatus, prices_or_None, preserve_existing)``."""
        async with semaphore:
            kind = str(source_cfg.get("kind") or ("newapi" if source_id.startswith("newapi:") else "public"))
            label = str(source_cfg.get("label") or source_id)
            url = str(source_cfg.get("url") or "").strip()
            provider_id = str(source_cfg.get("provider_id") or "").strip() or None
            source_cluster_id = str(source_cfg.get("cluster_id") or "").strip() or None
            currency_override = source_cfg.get("currency_override") if kind == "newapi" else None
            fiat_override = _positive_finite(source_cfg.get("fiat_per_base_unit")) if kind == "newapi" else None
            if kind == "public" and source_id in _PUBLIC_SPECS_BY_ID:
                spec = _PUBLIC_SPECS_BY_ID[source_id]
                url = spec["url"]
                label = spec["label"]

            prev = existing_status if isinstance(existing_status, dict) else {}
            prev_count = int(prev.get("model_count") or 0)
            prev_last_ok = prev.get("last_ok_at") if type(prev.get("last_ok_at")) is str else None
            prev_etag = prev.get("etag") if (type(prev.get("etag")) is str and prev_count > 0) else None
            prev_site_meta = dict(prev.get("site_meta")) if isinstance(prev.get("site_meta"), dict) else {}
            prev_currency = prev.get("currency") if type(prev.get("currency")) is str else None
            prev_override = prev.get("currency_override") if type(prev.get("currency_override")) is str else None
            prev_fiat_override = _positive_finite(prev_site_meta.get("fiat_per_base_unit_override"))
            prev_schema_ver = int(prev_site_meta.get("schema_version") or 0) if type(prev_site_meta.get("schema_version")) is int else 0
            prev_auth_mode = source_cfg.get("auth_mode") or (prev.get("auth_mode") if type(prev.get("auth_mode")) is str else None)

            if not url:
                return (
                    SourceStatus(
                        source_id=source_id, kind=kind, label=label, url=url,
                        enabled=True, status="error", model_count=prev_count,
                        last_sync_at=now_iso, last_ok_at=prev_last_ok, etag=prev_etag,
                        error="价格源地址无效", provider_id=provider_id,
                        currency=prev_currency, currency_override=currency_override,
                        auth_mode=prev_auth_mode, site_meta=prev_site_meta or None,
                    ),
                    None,
                    True,
                )

            conn = (
                self._find_connection_for_newapi_source(
                    connections,
                    provider_id=provider_id,
                    cluster_id=source_cluster_id,
                    pricing_url=url,
                    auth_mode=prev_auth_mode,
                )
                if kind == "newapi"
                else {}
            )
            quota_station = self._resolve_quota_station(url) if kind == "newapi" else None
            station_token, station_guard, station_headers = (
                _station_auth_params(url, quota_station) if kind == "newapi" else (None, None, None)
            )
            provider_proxy = conn.get("proxy") or (
                quota_station.get("proxy_url") if isinstance(quota_station, dict) else None
            )
            api_key = str(conn.get("api_key") or "").strip() if kind == "newapi" else ""
            api_base = str(conn.get("api_base") or "").strip() if kind == "newapi" else ""

            needs_schema_upgrade = (kind == "newapi" and prev_schema_ver < NEWAPI_SITE_META_SCHEMA_VERSION)
            effective_etag = (
                None
                if (
                    kind == "newapi"
                    and (
                        currency_override != prev_override
                        or fiat_override != prev_fiat_override
                        or needs_schema_upgrade
                        or quota_station is not None
                        or bool(prev_site_meta.get("quota_station_id"))
                    )
                )
                else prev_etag
            )
            used_bearer = False
            used_station_auth = False
            status_code = 0
            payload: Any = None
            new_etag: str | None = None

            try:
                if kind == "newapi":
                    if station_token and station_guard:
                        try:
                            st_code, st_payload, st_etag = await self._fetch_json_with_retry(
                                session,
                                url,
                                etag=None,
                                bearer_key=station_token,
                                origin_guard_base=station_guard,
                                extra_headers=station_headers,
                                provider_proxy=provider_proxy,
                                allow_redirects=False,
                            )
                            if (
                                st_code == 200
                                and isinstance(st_payload, dict)
                                and st_payload.get("success") is True
                                and isinstance(st_payload.get("data"), list)
                                and len(st_payload["data"]) > 0
                            ):
                                status_code, payload, new_etag = st_code, st_payload, st_etag
                                used_station_auth = True
                        except asyncio.CancelledError:
                            raise
                        except Exception:
                            # Graceful fallback to anonymous / cluster Bearer flow on station auth failure.
                            pass

                    if not used_station_auth:
                        status_code, payload, new_etag = await self._fetch_json_with_retry(
                            session,
                            url,
                            etag=(None if prev_auth_mode == "bearer" else effective_etag),
                            bearer_key=None,
                            provider_proxy=provider_proxy,
                            allow_redirects=False,
                        )
                        if status_code == 200 and isinstance(payload, dict) and payload.get("success") is True:
                            raw_list = payload.get("data")
                            if isinstance(raw_list, list) and len(raw_list) == 0 and api_key and same_origin(url, api_base):
                                status_code, payload, new_etag = await self._fetch_json_with_retry(
                                    session,
                                    url,
                                    etag=None,
                                    bearer_key=api_key,
                                    origin_guard_base=api_base,
                                    provider_proxy=provider_proxy,
                                    allow_redirects=False,
                                )
                                used_bearer = True
                else:
                    status_code, payload, new_etag = await self._fetch_json_with_retry(
                        session,
                        url,
                        etag=prev_etag,
                        provider_proxy=None,
                        allow_redirects=True,
                    )
            except _HttpStatusError as http_err:
                # If anonymous New API returned 401/403 and we have a same-origin key, retry with Bearer.
                if (
                    kind == "newapi"
                    and http_err.status in (401, 403)
                    and api_key
                    and same_origin(url, api_base)
                ):
                    try:
                        status_code, payload, new_etag = await self._fetch_json_with_retry(
                            session,
                            url,
                            etag=None,
                            bearer_key=api_key,
                            origin_guard_base=api_base,
                            provider_proxy=provider_proxy,
                            allow_redirects=False,
                        )
                        used_bearer = True
                    except asyncio.CancelledError:
                        raise
                    except Exception as retry_err:
                        logger.warning(
                            "[CompanionV2] 价格源同步失败 source=%s origin=%s error_type=%s",
                            source_id, safe_origin_label(url), type(retry_err).__name__,
                        )
                        return (
                            SourceStatus(
                                source_id=source_id, kind=kind, label=label, url=url,
                                enabled=True, status="error", model_count=prev_count,
                                last_sync_at=now_iso, last_ok_at=prev_last_ok, etag=prev_etag,
                                error=_safe_sync_error(retry_err), provider_id=provider_id,
                                needs_key=isinstance(retry_err, _HttpStatusError) and retry_err.status in (401, 403),
                                currency=prev_currency, currency_override=currency_override,
                                auth_mode=prev_auth_mode, site_meta=prev_site_meta or None,
                            ),
                            None,
                            True,
                        )
                else:
                    logger.warning(
                        "[CompanionV2] 价格源同步失败 source=%s origin=%s status=%s",
                        source_id, safe_origin_label(url), http_err.status,
                    )
                    return (
                        SourceStatus(
                            source_id=source_id, kind=kind, label=label, url=url,
                            enabled=True, status="error", model_count=prev_count,
                            last_sync_at=now_iso, last_ok_at=prev_last_ok, etag=prev_etag,
                            error=_safe_sync_error(http_err), provider_id=provider_id,
                            needs_key=(kind == "newapi" and http_err.status in (401, 403)),
                            currency=prev_currency, currency_override=currency_override,
                            auth_mode=prev_auth_mode, site_meta=prev_site_meta or None,
                        ),
                        None,
                        True,
                    )
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.warning(
                    "[CompanionV2] 价格源同步失败 source=%s origin=%s error_type=%s",
                    source_id, safe_origin_label(url), type(error).__name__,
                )
                return (
                    SourceStatus(
                        source_id=source_id, kind=kind, label=label, url=url,
                        enabled=True, status="error", model_count=prev_count,
                        last_sync_at=now_iso, last_ok_at=prev_last_ok, etag=prev_etag,
                        error=_safe_sync_error(error), provider_id=provider_id,
                        currency=prev_currency, currency_override=currency_override,
                        auth_mode=prev_auth_mode, site_meta=prev_site_meta or None,
                    ),
                    None,
                    True,
                )

            if status_code == 304:
                if kind == "newapi":
                    raw_status = await self._fetch_newapi_status_raw(session, url, provider_proxy=provider_proxy)
                    status_meta = parse_newapi_status(raw_status, pricing_meta=prev_site_meta) if raw_status is not None else None
                    merged_meta = {**prev_site_meta, **(status_meta or {})}
                    eff_cur, eff_scale, eff_qpu = resolve_newapi_currency_params(
                        merged_meta,
                        currency_override=currency_override,
                        fiat_per_base_unit_override=fiat_override,
                    )
                    prev_cur, prev_scale, prev_qpu = resolve_newapi_currency_params(
                        prev_site_meta,
                        currency_override=prev_override,
                        fiat_per_base_unit_override=prev_fiat_override,
                    )
                    if (eff_cur, eff_scale, eff_qpu) == (prev_cur, prev_scale, prev_qpu):
                        merged_meta["effective_unit_scale"] = eff_scale
                        if fiat_override is not None:
                            merged_meta["fiat_per_base_unit_override"] = fiat_override
                        else:
                            merged_meta.pop("fiat_per_base_unit_override", None)
                        eff_label = str(merged_meta.get("system_name") or "").strip() or label
                        return (
                            SourceStatus(
                                source_id=source_id, kind=kind, label=eff_label, url=url,
                                enabled=True, status="ok", model_count=prev_count,
                                last_sync_at=now_iso, last_ok_at=now_iso, etag=new_etag,
                                error=None, provider_id=provider_id,
                                currency=eff_cur, currency_override=currency_override,
                                auth_mode=prev_auth_mode, site_meta=merged_meta or None,
                            ),
                            None,
                            True,
                        )
                    # Currency or scale changed upstream while ETag stayed identical -> refetch without ETag!
                    try:
                        status_code, payload, new_etag = await self._fetch_json_with_retry(
                            session,
                            url,
                            etag=None,
                            bearer_key=(api_key if (prev_auth_mode == "bearer" and api_key and same_origin(url, api_base)) else None),
                            origin_guard_base=(api_base if prev_auth_mode == "bearer" else None),
                            provider_proxy=provider_proxy,
                            allow_redirects=False,
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        return (
                            SourceStatus(
                                source_id=source_id, kind=kind, label=label, url=url,
                                enabled=True, status="ok", model_count=prev_count,
                                last_sync_at=now_iso, last_ok_at=now_iso, etag=new_etag,
                                error=None, provider_id=provider_id,
                                currency=prev_currency, currency_override=currency_override,
                                auth_mode=prev_auth_mode, site_meta=prev_site_meta or None,
                            ),
                            None,
                            True,
                        )
                else:
                    return (
                        SourceStatus(
                            source_id=source_id, kind=kind, label=label, url=url,
                            enabled=True, status="ok", model_count=prev_count,
                            last_sync_at=now_iso, last_ok_at=now_iso, etag=new_etag,
                            error=None, provider_id=provider_id,
                        ),
                        None,
                        True,
                    )

            resolved_currency: str | None = None
            resolved_auth_mode: str | None = prev_auth_mode
            merged_site_meta: dict[str, Any] | None = None
            eff_label = label

            if source_id == "modelsdev":
                prices = adapt_modelsdev(payload, updated_at=now_iso)
            elif source_id == "litellm":
                prices = adapt_litellm(payload, updated_at=now_iso)
            elif source_id == "openrouter":
                prices = adapt_openrouter(payload, updated_at=now_iso)
            elif kind == "newapi":
                if not (isinstance(payload, dict) and payload.get("success") is True and isinstance(payload.get("data"), list)):
                    return (
                        SourceStatus(
                            source_id=source_id, kind=kind, label=label, url=url,
                            enabled=True, status="error", model_count=prev_count,
                            last_sync_at=now_iso, last_ok_at=prev_last_ok, etag=prev_etag,
                            error="响应不符合 New API /api/pricing 格式", provider_id=provider_id,
                            currency=prev_currency, currency_override=currency_override,
                            auth_mode=prev_auth_mode, site_meta=prev_site_meta or None,
                        ),
                        None,
                        True,
                    )
                pricing_meta = extract_newapi_pricing_meta(payload)
                raw_status = await self._fetch_newapi_status_raw(session, url, provider_proxy=provider_proxy)
                topup_info = await self._fetch_newapi_topup_info(
                    session, url, quota_station, provider_proxy=provider_proxy
                )
                status_meta = (
                    parse_newapi_status(raw_status, topup_info=topup_info, pricing_meta=pricing_meta)
                    if raw_status is not None
                    else None
                )
                merged_site_meta = {
                    **prev_site_meta,
                    **(status_meta or {}),
                }
                _apply_topup_enrichment(
                    merged_site_meta,
                    topup_info,
                    has_fresh_status=(raw_status is not None),
                )
                if pricing_meta["group_ratio"] or not prev_site_meta.get("group_ratio"):
                    merged_site_meta["group_ratio"] = pricing_meta["group_ratio"]
                if pricing_meta["usable_group"] or not prev_site_meta.get("usable_group"):
                    merged_site_meta["usable_group"] = pricing_meta["usable_group"]
                if pricing_meta["auto_groups"] or not prev_site_meta.get("auto_groups"):
                    merged_site_meta["auto_groups"] = pricing_meta["auto_groups"]
                _apply_station_enrichment(
                    merged_site_meta,
                    pricing_meta,
                    quota_station,
                    cluster_id=source_cluster_id,
                )
                if raw_status is not None or topup_info is not None or prev_site_meta.get("quota_display_type"):
                    merged_site_meta["schema_version"] = NEWAPI_SITE_META_SCHEMA_VERSION
                if status_meta and status_meta.get("system_name"):
                    eff_label = status_meta["system_name"]
                resolved_currency, unit_scale, quota_per_unit = resolve_newapi_currency_params(
                    merged_site_meta,
                    currency_override=currency_override,
                    fiat_per_base_unit_override=fiat_override,
                )
                merged_site_meta["effective_unit_scale"] = unit_scale
                if fiat_override is not None:
                    merged_site_meta["fiat_per_base_unit_override"] = fiat_override
                else:
                    merged_site_meta.pop("fiat_per_base_unit_override", None)
                diag: dict[str, int] = {}
                prices = adapt_newapi(
                    payload,
                    source_id=source_id,
                    source_label=eff_label,
                    updated_at=now_iso,
                    currency=resolved_currency,
                    unit_scale=unit_scale,
                    quota_per_unit=quota_per_unit,
                    diagnostics=diag,
                )
                if not (len(prices) == 0 and prev_count > 0):
                    merged_site_meta["skipped_expr_count"] = diag.get("skipped_unsupported_expr", 0)
                resolved_auth_mode = "bearer" if used_bearer else ("anonymous" if prices else prev_auth_mode)
            else:
                prices = []

            needs_key = (kind == "newapi" and len(prices) == 0)
            preserve_existing = bool(needs_key and prev_count > 0)
            return (
                SourceStatus(
                    source_id=source_id, kind=kind, label=eff_label, url=url,
                    enabled=True, status="ok",
                    model_count=(prev_count if preserve_existing else len(prices)),
                    last_sync_at=now_iso,
                    last_ok_at=(prev_last_ok if preserve_existing else now_iso),
                    etag=(prev_etag if preserve_existing else new_etag),
                    error=None, provider_id=provider_id, needs_key=needs_key,
                    currency=(prev_currency or resolved_currency) if preserve_existing else resolved_currency,
                    currency_override=currency_override,
                    auth_mode=(prev_auth_mode if preserve_existing else resolved_auth_mode),
                    site_meta=merged_site_meta,
                ),
                None if preserve_existing else prices,
                preserve_existing,
            )

    async def sync_sources(
        self,
        *,
        source_ids: list[str] | None = None,
        connections: dict[str, dict[str, Any]] | None = None,
        record_daily_timestamp: bool = False,
    ) -> dict[str, Any]:
        """Synchronize enabled (or specifically requested) price catalog sources."""
        async with self._get_lock():
            settings = self.settings_store.read_safe()
            catalog = self.catalog_store.read_safe()
            configured_sources = settings.get("price_sources") or {}
            conn_map = connections if isinstance(connections, dict) else self._resolve_connections()

            targets: list[tuple[str, dict[str, Any]]] = []
            if source_ids:
                for sid in source_ids:
                    if sid in configured_sources:
                        targets.append((sid, configured_sources[sid]))
            else:
                for sid, cfg in configured_sources.items():
                    if isinstance(cfg, dict) and cfg.get("enabled", True) is True:
                        targets.append((sid, cfg))

            now_iso = self._current_time().isoformat()
            if not targets:
                return {
                    "synced_at": now_iso,
                    "ok_count": 0,
                    "error_count": 0,
                    "results": [],
                }

            semaphore = asyncio.Semaphore(SYNC_CONCURRENCY)
            existing_sources = catalog.get("sources") or {}
            updates: list[tuple[SourceStatus, list[CatalogPrice] | None, bool]] = []
            async with self._session_factory(self.timeout_seconds) as session:
                tasks = [
                    self._sync_one_source(
                        session,
                        semaphore,
                        sid,
                        cfg,
                        existing_sources.get(sid),
                        conn_map,
                        now_iso,
                    )
                    for sid, cfg in targets
                ]
                updates = list(await asyncio.gather(*tasks))

            self.catalog_store.save_batch_results(updates, updated_at=now_iso)
            ok_count = sum(1 for st, _, _ in updates if st.status == "ok")
            error_count = sum(1 for st, _, _ in updates if st.status != "ok")
            if ok_count > 0:
                self._last_daily_failure_monotonic = None
                if record_daily_timestamp:
                    try:
                        self.settings_store.record_auto_sync_at(now_iso)
                    except Exception:
                        logger.debug("[CompanionV2] 记录价格源自动同步时间失败", exc_info=True)
            else:
                self._last_daily_failure_monotonic = monotonic()

            return {
                "synced_at": now_iso,
                "ok_count": ok_count,
                "error_count": error_count,
                "results": [st.to_dict() for st, _, _ in updates],
            }

    async def detect_newapi_source(
        self,
        *,
        provider_id: str,
        api_base: str,
        api_key: str = "",
        proxy: str = "",
        supplier_label: str = "",
        cluster_id: str = "",
        enable: bool = True,
    ) -> dict[str, Any]:
        """Probe ``<root>/api/pricing`` + ``/api/status`` (+ optional ``/v1/models`` group hint)."""
        target_url = newapi_pricing_url(api_base)
        if not target_url:
            return {
                "ok": False,
                "source_id": None,
                "deduplicated": False,
                "models": 0,
                "needs_key": False,
                "url": None,
                "error": "该供应商未配置有效的 HTTP(S) 接口地址",
            }

        async with self._get_lock():
            settings = self.settings_store.read_safe()
            eff_cluster_id = str(cluster_id or "").strip() or provider_id
            fallback_existing_sid = find_newapi_source_by_url(settings, target_url, cluster_id=eff_cluster_id)
            label = (supplier_label.strip() or provider_id)[:80]
            now_iso = self._current_time().isoformat()
            clean_key = str(api_key or "").strip()

            status_code = 0
            payload: Any = None
            new_etag: str | None = None
            used_bearer = False
            used_station_auth = False
            raw_status: dict[str, Any] | None = None
            topup_info: dict[str, Any] | None = None
            status_meta: dict[str, Any] | None = None
            models_set: set[str] | None = None
            quota_station = self._resolve_quota_station(target_url)
            station_token, station_guard, station_headers = _station_auth_params(target_url, quota_station)
            effective_proxy = proxy or (
                quota_station.get("proxy_url") if isinstance(quota_station, dict) else ""
            )

            try:
                async with self._session_factory(self.timeout_seconds) as session:
                    if station_token and station_guard:
                        try:
                            st_code, st_payload, st_etag = await self._fetch_json_with_retry(
                                session,
                                target_url,
                                etag=None,
                                bearer_key=station_token,
                                origin_guard_base=station_guard,
                                extra_headers=station_headers,
                                provider_proxy=effective_proxy,
                                allow_redirects=False,
                            )
                            if (
                                st_code == 200
                                and isinstance(st_payload, dict)
                                and st_payload.get("success") is True
                                and isinstance(st_payload.get("data"), list)
                                and len(st_payload["data"]) > 0
                            ):
                                status_code, payload, new_etag = st_code, st_payload, st_etag
                                used_station_auth = True
                        except asyncio.CancelledError:
                            raise
                        except Exception:
                            pass

                    if not used_station_auth:
                        try:
                            status_code, payload, new_etag = await self._fetch_json_with_retry(
                                session,
                                target_url,
                                bearer_key=None,
                                provider_proxy=effective_proxy,
                                allow_redirects=False,
                            )
                        except _HttpStatusError as http_err:
                            if http_err.status in (401, 403):
                                if clean_key and same_origin(target_url, api_base):
                                    status_code, payload, new_etag = await self._fetch_json_with_retry(
                                        session,
                                        target_url,
                                        bearer_key=clean_key,
                                        origin_guard_base=api_base,
                                        provider_proxy=effective_proxy,
                                        allow_redirects=False,
                                    )
                                    used_bearer = True
                                else:
                                    return {
                                        "ok": False,
                                        "source_id": fallback_existing_sid,
                                        "deduplicated": fallback_existing_sid is not None,
                                        "models": 0,
                                        "needs_key": True,
                                        "url": target_url,
                                        "error": "目标网关要求鉴权才能读取 /api/pricing",
                                    }
                            else:
                                raise

                        # Anonymous succeeded with 0 models -> retry with same-origin key if present.
                        if (
                            status_code == 200
                            and isinstance(payload, dict)
                            and payload.get("success") is True
                            and isinstance(payload.get("data"), list)
                            and len(payload["data"]) == 0
                            and clean_key
                            and same_origin(target_url, api_base)
                        ):
                            try:
                                status_code, payload, new_etag = await self._fetch_json_with_retry(
                                    session,
                                    target_url,
                                    bearer_key=clean_key,
                                    origin_guard_base=api_base,
                                    provider_proxy=effective_proxy,
                                    allow_redirects=False,
                                )
                                used_bearer = True
                            except Exception:
                                pass

                    valid_pricing = (
                        isinstance(payload, dict)
                        and payload.get("success") is True
                        and isinstance(payload.get("data"), list)
                    )
                    if valid_pricing:
                        pricing_meta_preview = extract_newapi_pricing_meta(payload)
                        raw_status = await self._fetch_newapi_status_raw(
                            session, target_url, provider_proxy=effective_proxy
                        )
                        topup_info = await self._fetch_newapi_topup_info(
                            session, target_url, quota_station, provider_proxy=effective_proxy
                        )
                        status_meta = (
                            parse_newapi_status(
                                raw_status,
                                topup_info=topup_info,
                                pricing_meta=pricing_meta_preview,
                            )
                            if raw_status is not None
                            else None
                        )
                        distinct_ratios = {
                            round(r, 6) for r in (pricing_meta_preview.get("group_ratio") or {}).values()
                        }
                        if clean_key and len(distinct_ratios) > 1 and same_origin(target_url, api_base):
                            models_set = await self._fetch_newapi_models_for_group(
                                session, api_base, clean_key, provider_proxy=effective_proxy
                            )
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.warning(
                    "[CompanionV2] New API 探测失败 provider=%s origin=%s error_type=%s",
                    provider_id, safe_origin_label(target_url), type(error).__name__,
                )
                return {
                    "ok": False,
                    "source_id": fallback_existing_sid,
                    "deduplicated": fallback_existing_sid is not None,
                    "models": 0,
                    "needs_key": isinstance(error, _HttpStatusError) and error.status in (401, 403),
                    "url": target_url,
                    "error": _safe_sync_error(error),
                }

            if not (isinstance(payload, dict) and payload.get("success") is True and isinstance(payload.get("data"), list)):
                return {
                    "ok": False,
                    "source_id": fallback_existing_sid,
                    "deduplicated": fallback_existing_sid is not None,
                    "models": 0,
                    "needs_key": False,
                    "url": target_url,
                    "error": "目标接口不是兼容的 New API /api/pricing 端点",
                }

            auth_mode = "bearer" if (used_bearer or len(payload["data"]) == 0) else "anonymous"
            existing_sid = find_newapi_source_by_url(
                settings,
                target_url,
                cluster_id=eff_cluster_id,
                auth_mode=auth_mode,
                has_key=bool(clean_key),
            )
            deduplicated = existing_sid is not None
            source_id = existing_sid or f"newapi:{provider_id}"

            existing_cfg = settings.get("price_sources", {}).get(source_id, {}) if deduplicated else {}
            existing_cat_source = (
                (self.catalog_store.read_safe().get("sources") or {}).get(source_id) or {}
            )
            prev_site_meta = (
                dict(existing_cat_source.get("site_meta"))
                if isinstance(existing_cat_source.get("site_meta"), dict)
                else {}
            )

            effective_provider_id = (
                str(existing_cfg.get("provider_id") or provider_id) if deduplicated else provider_id
            )
            effective_cluster_id = (
                str(existing_cfg.get("cluster_id") or eff_cluster_id) if deduplicated else eff_cluster_id
            )
            site_name = str((status_meta or {}).get("system_name") or "").strip()
            if site_name:
                effective_label = site_name
            elif deduplicated:
                effective_label = str(existing_cfg.get("label") or label)
            else:
                effective_label = label

            pricing_meta = extract_newapi_pricing_meta(payload)
            merged_site_meta: dict[str, Any] = {
                **prev_site_meta,
                **(status_meta or {}),
            }
            _apply_topup_enrichment(
                merged_site_meta,
                topup_info,
                has_fresh_status=(raw_status is not None),
            )
            if pricing_meta["group_ratio"] or not prev_site_meta.get("group_ratio"):
                merged_site_meta["group_ratio"] = pricing_meta["group_ratio"]
            if pricing_meta["usable_group"] or not prev_site_meta.get("usable_group"):
                merged_site_meta["usable_group"] = pricing_meta["usable_group"]
            if pricing_meta["auto_groups"] or not prev_site_meta.get("auto_groups"):
                merged_site_meta["auto_groups"] = pricing_meta["auto_groups"]
            detected_group_info = (
                infer_newapi_cluster_group(payload, models_set)
                if models_set
                else None
            )
            if detected_group_info is not None:
                detected_map = dict(merged_site_meta.get("detected_groups") or {})
                detected_map[eff_cluster_id] = detected_group_info
                merged_site_meta["detected_groups"] = detected_map

            _apply_station_enrichment(
                merged_site_meta,
                pricing_meta,
                quota_station,
                cluster_id=eff_cluster_id,
            )
            if detected_group_info is None and isinstance(merged_site_meta.get("detected_groups"), dict):
                detected_group_info = (
                    merged_site_meta["detected_groups"].get(eff_cluster_id)
                    or merged_site_meta["detected_groups"].get("__station__")
                )
            if raw_status is not None or topup_info is not None or prev_site_meta.get("quota_display_type"):
                merged_site_meta["schema_version"] = NEWAPI_SITE_META_SCHEMA_VERSION

            currency_override = existing_cfg.get("currency_override")
            fiat_override = _positive_finite(existing_cfg.get("fiat_per_base_unit"))
            resolved_currency, unit_scale, quota_per_unit = resolve_newapi_currency_params(
                merged_site_meta,
                currency_override=currency_override,
                fiat_per_base_unit_override=fiat_override,
            )
            merged_site_meta["effective_unit_scale"] = unit_scale
            if fiat_override is not None:
                merged_site_meta["fiat_per_base_unit_override"] = fiat_override
            else:
                merged_site_meta.pop("fiat_per_base_unit_override", None)

            diag: dict[str, int] = {}
            prices = adapt_newapi(
                payload,
                source_id=source_id,
                source_label=effective_label,
                updated_at=now_iso,
                currency=resolved_currency,
                unit_scale=unit_scale,
                quota_per_unit=quota_per_unit,
                diagnostics=diag,
            )
            needs_key = len(prices) == 0
            preserve_existing = bool(
                deduplicated and needs_key and int(existing_cat_source.get("model_count") or 0) > 0
            )
            if not preserve_existing:
                merged_site_meta["skipped_expr_count"] = diag.get("skipped_unsupported_expr", 0)
            stored_count = (
                int(existing_cat_source.get("model_count") or 0)
                if preserve_existing
                else len(prices)
            )
            stored_auth_mode = (
                str(existing_cfg.get("auth_mode") or auth_mode)
                if preserve_existing
                else auth_mode
            )
            stored_etag = (
                existing_cat_source.get("etag")
                if preserve_existing and type(existing_cat_source.get("etag")) is str
                else new_etag
            )

            if enable:
                self.settings_store.upsert_newapi_source(
                    source_id,
                    url=target_url,
                    provider_id=effective_provider_id,
                    label=effective_label,
                    enabled=True,
                    auth_mode=stored_auth_mode,
                    cluster_id=effective_cluster_id,
                )
            source_status = SourceStatus(
                source_id=source_id,
                kind="newapi",
                label=effective_label,
                url=target_url,
                enabled=enable,
                status="ok",
                model_count=stored_count,
                last_sync_at=now_iso,
                last_ok_at=now_iso,
                etag=stored_etag,
                error=None,
                provider_id=effective_provider_id,
                needs_key=needs_key,
                currency=resolved_currency,
                currency_override=currency_override,
                auth_mode=stored_auth_mode,
                site_meta=merged_site_meta,
            )
            self.catalog_store.save_source_result(
                source_status,
                None if preserve_existing else prices,
                updated_at=now_iso,
                preserve_existing_prices=preserve_existing,
            )
            return {
                "ok": True,
                "source_id": source_id,
                "deduplicated": deduplicated,
                "models": len(prices),
                "needs_key": needs_key,
                "url": target_url,
                "error": None,
                "status": source_status.to_dict(),
                "site_name": site_name or None,
                "currency": resolved_currency,
                "currency_source": merged_site_meta.get("currency_source"),
                "quota_display_type": merged_site_meta.get("quota_display_type") or None,
                "custom_currency_symbol": merged_site_meta.get("custom_currency_symbol") or None,
                "custom_currency_exchange_rate": merged_site_meta.get("custom_currency_exchange_rate"),
                "recharge_price": merged_site_meta.get("recharge_price"),
                "effective_unit_scale": unit_scale,
                "detected_group": (detected_group_info or {}).get("group"),
                "detected_group_ratio": (detected_group_info or {}).get("ratio"),
                "detected_group_confidence": (detected_group_info or {}).get("confidence"),
                "available_groups": pricing_meta.get("group_ratio") or {},
                "skipped_expr_count": diag.get("skipped_unsupported_expr", 0),
                "quota_station_linked": bool(merged_site_meta.get("quota_station_id")),
            }

    def _pending_backfill_ids(
        self,
        settings: dict[str, Any],
        catalog: dict[str, Any],
    ) -> list[str]:
        return [
            sid
            for sid in find_newapi_sources_needing_backfill(settings, catalog)
            if sid not in self._attempted_backfill_ids
        ]

    def maybe_schedule_daily_sync(self) -> bool:
        """Non-blocking daily auto-sync & legacy New API metadata schema backfill trigger."""
        settings = self.settings_store.read_safe()
        auto_daily_enabled = (settings.get("price_sync") or {}).get("auto_daily", True) is not False
        daily_due = is_catalog_sync_due(
            settings, now=self._current_time(), ttl_seconds=CATALOG_DAILY_SYNC_SECONDS
        )
        backfill_ids = (
            []
            if (daily_due or not auto_daily_enabled)
            else self._pending_backfill_ids(settings, self.catalog_store.read_safe())
        )
        if not daily_due and not backfill_ids:
            return False
        if self._bg_task is not None and not self._bg_task.done():
            return False
        if (
            self._last_daily_failure_monotonic is not None
            and (monotonic() - self._last_daily_failure_monotonic) < self.failure_backoff_seconds
        ):
            return False
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return False
        task = loop.create_task(self._background_daily_sync(), name="companion-v2-price-catalog-sync")
        self._bg_task = task
        task.add_done_callback(self._on_background_done)
        return True

    async def _background_daily_sync(self) -> None:
        try:
            if self.debounce_seconds > 0:
                await self._sleep(self.debounce_seconds)
            settings = self.settings_store.read_safe()
            if is_catalog_sync_due(
                settings, now=self._current_time(), ttl_seconds=CATALOG_DAILY_SYNC_SECONDS
            ):
                await self.sync_sources(record_daily_timestamp=True)
                return
            if (settings.get("price_sync") or {}).get("auto_daily", True) is False:
                return
            backfill_ids = self._pending_backfill_ids(settings, self.catalog_store.read_safe())
            if not backfill_ids:
                return
            self._attempted_backfill_ids.update(backfill_ids)
            await self.sync_sources(source_ids=backfill_ids, record_daily_timestamp=False)
        except asyncio.CancelledError:
            return
        except Exception as error:
            logger.warning("[CompanionV2] 后台价格源每日同步失败 error_type=%s", type(error).__name__)

    def _on_background_done(self, task: asyncio.Task) -> None:
        if self._bg_task is task:
            self._bg_task = None
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.warning("[CompanionV2] 后台价格源同步任务异常 error_type=%s", type(exc).__name__)

    async def close(self) -> None:
        task = self._bg_task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


async def close_pricing_catalog_services(owner: Any) -> None:
    """Cancel and clear any PricingCatalogService instances attached to a host controller."""
    services = getattr(owner, "_pricing_catalog_services", None)
    if not isinstance(services, dict):
        return
    for service in list(services.values()):
        close_fn = getattr(service, "close", None)
        if callable(close_fn):
            try:
                await close_fn()
            except Exception:
                logger.debug("[CompanionV2] 关闭价格源同步服务失败", exc_info=True)
    services.clear()
