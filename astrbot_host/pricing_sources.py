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
import random
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
    PUBLIC_SOURCE_SPECS,
    CatalogPrice,
    PriceCatalogStore,
    SourceStatus,
    adapt_litellm,
    adapt_modelsdev,
    adapt_newapi,
    adapt_openrouter,
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


def same_origin(url_a: object, url_b: object) -> bool:
    oa = normalized_origin(url_a)
    ob = normalized_origin(url_b)
    return oa is not None and ob is not None and oa == ob


def find_newapi_source_by_url(settings: dict[str, Any], target_url: str) -> str | None:
    """Return existing ``newapi:*`` source_id matching ``target_url`` for URL deduplication."""
    norm_target = target_url.rstrip("/").lower()
    sources = settings.get("price_sources") or {}
    for sid, cfg in sources.items():
        if type(sid) is not str or not sid.startswith("newapi:") or type(cfg) is not dict:
            continue
        existing_url = str(cfg.get("url") or "").strip().rstrip("/").lower()
        if existing_url and existing_url == norm_target:
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
        provider_proxy: object = None,
        allow_redirects: bool = True,
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
            effective_redirects = False

        proxy = self._resolve_proxy(provider_proxy)
        req_kwargs: dict[str, Any] = {}
        if headers:
            req_kwargs["headers"] = headers
        if proxy is not None:
            req_kwargs["proxy"] = proxy
        if not effective_redirects:
            req_kwargs["allow_redirects"] = False

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
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
                if attempt < self.max_retries and _is_transient_error(error):
                    delay = self.retry_base_seconds * (2 ** attempt) + random.uniform(0.0, 0.25)
                    if delay > 0:
                        await self._sleep(delay)
                    continue
                raise
        assert last_error is not None
        raise last_error

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
            if kind == "public" and source_id in _PUBLIC_SPECS_BY_ID:
                spec = _PUBLIC_SPECS_BY_ID[source_id]
                url = spec["url"]
                label = spec["label"]

            prev = existing_status if isinstance(existing_status, dict) else {}
            prev_count = int(prev.get("model_count") or 0)
            prev_last_ok = prev.get("last_ok_at") if type(prev.get("last_ok_at")) is str else None
            prev_etag = prev.get("etag") if (type(prev.get("etag")) is str and prev_count > 0) else None

            if not url:
                return (
                    SourceStatus(
                        source_id=source_id, kind=kind, label=label, url=url,
                        enabled=True, status="error", model_count=prev_count,
                        last_sync_at=now_iso, last_ok_at=prev_last_ok, etag=prev_etag,
                        error="价格源地址无效", provider_id=provider_id,
                    ),
                    None,
                    True,
                )

            conn = connections.get(provider_id or "", {}) if kind == "newapi" else {}
            provider_proxy = conn.get("proxy")
            api_key = str(conn.get("api_key") or "").strip() if kind == "newapi" else ""
            api_base = str(conn.get("api_base") or "").strip() if kind == "newapi" else ""

            try:
                if kind == "newapi":
                    status_code, payload, new_etag = await self._fetch_json_with_retry(
                        session,
                        url,
                        etag=prev_etag,
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
                    ),
                    None,
                    True,
                )

            if status_code == 304:
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
                        ),
                        None,
                        True,
                    )
                prices = adapt_newapi(
                    payload, source_id=source_id, source_label=label, updated_at=now_iso
                )
            else:
                prices = []

            needs_key = (kind == "newapi" and len(prices) == 0)
            return (
                SourceStatus(
                    source_id=source_id, kind=kind, label=label, url=url,
                    enabled=True, status="ok", model_count=len(prices),
                    last_sync_at=now_iso, last_ok_at=now_iso, etag=new_etag,
                    error=None, provider_id=provider_id, needs_key=needs_key,
                ),
                prices,
                False,
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
        enable: bool = True,
    ) -> dict[str, Any]:
        """Probe ``<root>/api/pricing`` for New API compatibility, URL-dedup, enable & sync."""
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
            existing_sid = find_newapi_source_by_url(settings, target_url)
            deduplicated = existing_sid is not None
            source_id = existing_sid or f"newapi:{provider_id}"
            label = (supplier_label.strip() or provider_id)[:80]
            now_iso = self._current_time().isoformat()
            clean_key = str(api_key or "").strip()

            status_code = 0
            payload: Any = None
            new_etag: str | None = None
            needs_key = False

            try:
                async with self._session_factory(self.timeout_seconds) as session:
                    try:
                        status_code, payload, new_etag = await self._fetch_json_with_retry(
                            session,
                            target_url,
                            bearer_key=None,
                            provider_proxy=proxy,
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
                                    provider_proxy=proxy,
                                    allow_redirects=False,
                                )
                            else:
                                return {
                                    "ok": False,
                                    "source_id": source_id if deduplicated else None,
                                    "deduplicated": deduplicated,
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
                                provider_proxy=proxy,
                                allow_redirects=False,
                            )
                        except Exception:
                            pass
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.warning(
                    "[CompanionV2] New API 探测失败 provider=%s origin=%s error_type=%s",
                    provider_id, safe_origin_label(target_url), type(error).__name__,
                )
                return {
                    "ok": False,
                    "source_id": source_id if deduplicated else None,
                    "deduplicated": deduplicated,
                    "models": 0,
                    "needs_key": isinstance(error, _HttpStatusError) and error.status in (401, 403),
                    "url": target_url,
                    "error": _safe_sync_error(error),
                }

            if not (isinstance(payload, dict) and payload.get("success") is True and isinstance(payload.get("data"), list)):
                return {
                    "ok": False,
                    "source_id": source_id if deduplicated else None,
                    "deduplicated": deduplicated,
                    "models": 0,
                    "needs_key": False,
                    "url": target_url,
                    "error": "目标接口不是兼容的 New API /api/pricing 端点",
                }

            existing_cfg = settings.get("price_sources", {}).get(source_id, {}) if deduplicated else {}
            effective_provider_id = (
                str(existing_cfg.get("provider_id") or provider_id) if deduplicated else provider_id
            )
            effective_label = (
                str(existing_cfg.get("label") or label) if deduplicated else label
            )

            prices = adapt_newapi(
                payload, source_id=source_id, source_label=effective_label, updated_at=now_iso
            )
            needs_key = len(prices) == 0

            if enable:
                self.settings_store.upsert_newapi_source(
                    source_id,
                    url=target_url,
                    provider_id=effective_provider_id,
                    label=effective_label,
                    enabled=True,
                )
            source_status = SourceStatus(
                source_id=source_id,
                kind="newapi",
                label=effective_label,
                url=target_url,
                enabled=enable,
                status="ok",
                model_count=len(prices),
                last_sync_at=now_iso,
                last_ok_at=now_iso,
                etag=new_etag,
                error=None,
                provider_id=effective_provider_id,
                needs_key=needs_key,
            )
            self.catalog_store.save_source_result(
                source_status, prices, updated_at=now_iso, preserve_existing_prices=False
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
            }

    def maybe_schedule_daily_sync(self) -> bool:
        """Non-blocking daily auto-sync trigger when ``price_sync.auto_daily`` is True and due."""
        settings = self.settings_store.read_safe()
        if not is_catalog_sync_due(
            settings, now=self._current_time(), ttl_seconds=CATALOG_DAILY_SYNC_SECONDS
        ):
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
            if not is_catalog_sync_due(
                settings, now=self._current_time(), ttl_seconds=CATALOG_DAILY_SYNC_SECONDS
            ):
                return
            await self.sync_sources(record_daily_timestamp=True)
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
