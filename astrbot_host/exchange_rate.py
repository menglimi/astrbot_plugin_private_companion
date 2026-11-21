"""Host-side asynchronous USD/CNY exchange-rate fetcher with stale-while-revalidate.

All outbound HTTP traffic for exchange rates lives strictly in ``astrbot_host/``
so ``companion_v2/`` remains pure domain with zero network dependencies.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import logging
import math
from time import monotonic
from typing import Any, Callable
from urllib.parse import urlsplit

import aiohttp

from companion_v2.model_gateway.pricing import (
    EXCHANGE_RATE_TTL_SECONDS,
    MAX_USD_CNY,
    MIN_USD_CNY,
    PricingSettingsStore,
    effective_rate_source,
    effective_usd_cny_rate,
    is_rate_stale,
)

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 10.0
CONNECT_TIMEOUT_SECONDS = 5.0
STREAM_CHUNK_BYTES = 65536
MAX_RESPONSE_BYTES = 65536
FAILURE_BACKOFF_SECONDS = 300.0
DEFAULT_DEBOUNCE_SECONDS = 0.05

EXCHANGE_RATE_ENDPOINTS: tuple[tuple[str, str], ...] = (
    ("open.er-api.com", "https://open.er-api.com/v6/latest/USD"),
    ("frankfurter.app", "https://api.frankfurter.app/latest?from=USD&to=CNY"),
)


def _reject_nonfinite(_value: str) -> None:
    raise ValueError("nonfinite exchange rate payload")


def normalize_http_proxy(raw: object) -> str | None:
    """Accept only http/https proxy URLs supported natively by aiohttp; never log credentials."""
    if type(raw) is not str:
        return None
    text = raw.strip()
    if not text:
        return None
    parsed = urlsplit(text)
    if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname:
        return None
    return text


def read_astrbot_http_proxy(controller: Any) -> str | None:
    """Read optional AstrBot ``http_proxy`` setting without ever exposing credentials."""
    context = getattr(controller, "context", None)
    getter = getattr(context, "get_config", None) if context is not None else None
    if not callable(getter):
        return None
    try:
        cfg = getter()
    except Exception:
        return None
    if not isinstance(cfg, dict):
        return None
    return normalize_http_proxy(cfg.get("http_proxy"))


async def read_bounded_stream(
    response: Any,
    *,
    max_bytes: int,
    chunk_size: int = STREAM_CHUNK_BYTES,
) -> bytes:
    """Stream response chunks via ``iter_chunked`` enforcing a strict cumulative byte cap.

    Avoids ``aiohttp.StreamReader.read(n)`` single-chunk truncation on multi-chunk bodies.
    """
    content = getattr(response, "content", None)
    if content is not None and hasattr(content, "iter_chunked"):
        buf = bytearray()
        async for chunk in content.iter_chunked(chunk_size):
            if not chunk:
                continue
            if not isinstance(chunk, (bytes, bytearray)):
                raise ValueError("response stream yielded non-bytes chunk")
            buf.extend(chunk)
            if len(buf) > max_bytes:
                raise ValueError(f"response body exceeded {max_bytes} bytes")
        return bytes(buf)
    if content is not None and hasattr(content, "read"):
        raw = await content.read(max_bytes + 1)
    else:
        raw = await response.read()
    if not isinstance(raw, (bytes, bytearray)):
        raise ValueError("response body is not bytes")
    if len(raw) > max_bytes:
        raise ValueError(f"response body exceeded {max_bytes} bytes")
    return bytes(raw)


def parse_usd_cny_payload(raw: bytes, *, source: str) -> float:
    """Parse and strictly bound-check a USD->CNY rate from bounded UTF-8 JSON."""
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("exchange rate response exceeded size limit")
    data = json.loads(raw.decode("utf-8"), parse_constant=_reject_nonfinite)
    if type(data) is not dict:
        raise ValueError("exchange rate payload is not a JSON object")
    if source == "open.er-api.com":
        result = data.get("result")
        if result is not None and result != "success":
            raise ValueError("primary exchange rate endpoint reported non-success")
    rates = data.get("rates")
    if type(rates) is not dict:
        raise ValueError("exchange rate payload missing rates map")
    cny = rates.get("CNY")
    if isinstance(cny, bool) or type(cny) not in (int, float):
        raise ValueError("exchange rate payload missing numeric CNY rate")
    rate = float(cny)
    if not math.isfinite(rate) or not (MIN_USD_CNY <= rate <= MAX_USD_CNY):
        raise ValueError("CNY exchange rate outside valid bounds")
    return round(rate, 6)


def _default_session_factory(timeout_seconds: float) -> aiohttp.ClientSession:
    return aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(
            total=timeout_seconds,
            connect=min(CONNECT_TIMEOUT_SECONDS, timeout_seconds),
        ),
        trust_env=True,
        headers={
            "Accept": "application/json",
            "Accept-Encoding": "gzip, deflate",
            "User-Agent": "astrbot-private-companion/pricing",
        },
    )


def _safe_error_label(error: Exception) -> str:
    if isinstance(error, asyncio.TimeoutError):
        return "汇率服务请求超时"
    if isinstance(error, aiohttp.ClientError):
        return "无法连接汇率服务"
    if isinstance(error, ValueError):
        return "汇率接口返回格式异常"
    return "汇率刷新失败"


class ExchangeRateService:
    """12-hour TTL stale-while-revalidate USD/CNY rate service."""

    def __init__(
        self,
        store: PricingSettingsStore,
        *,
        now: Callable[[], datetime] | None = None,
        session_factory: Callable[[float], Any] | None = None,
        proxy_resolver: Callable[[], str | None] | None = None,
        timeout_seconds: float = REQUEST_TIMEOUT_SECONDS,
        ttl_seconds: int = EXCHANGE_RATE_TTL_SECONDS,
        debounce_seconds: float = DEFAULT_DEBOUNCE_SECONDS,
        failure_backoff_seconds: float = FAILURE_BACKOFF_SECONDS,
        endpoints: tuple[tuple[str, str], ...] = EXCHANGE_RATE_ENDPOINTS,
    ):
        self.store = store
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._session_factory = session_factory or _default_session_factory
        self._proxy_resolver = proxy_resolver
        self.timeout_seconds = float(timeout_seconds)
        self.ttl_seconds = int(ttl_seconds)
        self.debounce_seconds = max(0.0, float(debounce_seconds))
        self.failure_backoff_seconds = max(0.0, float(failure_backoff_seconds))
        self.endpoints = tuple(endpoints)
        self._lock_loop: asyncio.AbstractEventLoop | None = None
        self._sync_lock: asyncio.Lock | None = None
        self._bg_task: asyncio.Task | None = None
        self._last_failure_monotonic: float | None = None

    def _get_lock(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._sync_lock is None or self._lock_loop is not loop:
            self._sync_lock = asyncio.Lock()
            self._lock_loop = loop
        return self._sync_lock

    def _current_time(self) -> datetime:
        current = self._now()
        return current.replace(tzinfo=timezone.utc) if current.tzinfo is None else current

    def _resolve_proxy(self) -> str | None:
        if not callable(self._proxy_resolver):
            return None
        try:
            return normalize_http_proxy(self._proxy_resolver())
        except Exception:
            return None

    async def _fetch_endpoint(self, session: Any, source: str, url: str) -> float:
        proxy = self._resolve_proxy()
        kwargs: dict[str, Any] = {}
        if proxy is not None:
            kwargs["proxy"] = proxy
        async with session.get(url, **kwargs) as response:
            status = getattr(response, "status", 0)
            if status != 200:
                raise ValueError(f"exchange rate HTTP {status}")
            raw = await read_bounded_stream(response, max_bytes=MAX_RESPONSE_BYTES)
            return parse_usd_cny_payload(raw, source=source)

    async def sync_now(self) -> dict[str, Any]:
        """Fetch live USD/CNY rate (primary -> fallback) and persist atomically."""
        async with self._get_lock():
            last_error: Exception | None = None
            try:
                async with self._session_factory(self.timeout_seconds) as session:
                    for index, (source, url) in enumerate(self.endpoints):
                        try:
                            rate = await asyncio.wait_for(
                                self._fetch_endpoint(session, source, url),
                                timeout=self.timeout_seconds,
                            )
                        except asyncio.CancelledError:
                            raise
                        except Exception as error:
                            last_error = error
                            logger.warning(
                                "[CompanionV2] 汇率源请求失败 source=%s error_type=%s",
                                source, type(error).__name__,
                            )
                            continue
                        fetched_at = self._current_time().isoformat()
                        self.store.record_fetched_rate(rate, source=source, fetched_at=fetched_at)
                        self._last_failure_monotonic = None
                        return {
                            "synced": True,
                            "usd_cny_rate": rate,
                            "source": source,
                            "fetched_at": fetched_at,
                            "fallback_used": index > 0,
                            "error": None,
                        }
            except asyncio.CancelledError:
                raise
            except Exception as error:
                last_error = error
                logger.warning("[CompanionV2] 汇率同步会话异常 error_type=%s", type(error).__name__)

            self._last_failure_monotonic = monotonic()
            current = self.store.read_safe()
            return {
                "synced": False,
                "usd_cny_rate": effective_usd_cny_rate(current),
                "source": effective_rate_source(current),
                "fetched_at": current["exchange_rate"]["fetched_at"],
                "fallback_used": False,
                "error": _safe_error_label(last_error) if last_error is not None else "汇率刷新失败",
            }

    def maybe_schedule_background_refresh(self) -> bool:
        """Non-blocking stale-while-revalidate trigger when mode=='auto' and cache > TTL."""
        settings = self.store.read_safe()
        if not is_rate_stale(settings, now=self._current_time(), ttl_seconds=self.ttl_seconds):
            return False
        if self._bg_task is not None and not self._bg_task.done():
            return False
        if (
            self._last_failure_monotonic is not None
            and (monotonic() - self._last_failure_monotonic) < self.failure_backoff_seconds
        ):
            return False
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return False
        task = loop.create_task(self._background_refresh(), name="companion-v2-exchange-rate-refresh")
        self._bg_task = task
        task.add_done_callback(self._on_background_done)
        return True

    async def _background_refresh(self) -> None:
        try:
            if self.debounce_seconds > 0:
                await asyncio.sleep(self.debounce_seconds)
            settings = self.store.read_safe()
            if not is_rate_stale(settings, now=self._current_time(), ttl_seconds=self.ttl_seconds):
                return
            await self.sync_now()
        except asyncio.CancelledError:
            return
        except Exception as error:
            logger.warning("[CompanionV2] 后台汇率刷新失败 error_type=%s", type(error).__name__)

    def _on_background_done(self, task: asyncio.Task) -> None:
        if self._bg_task is task:
            self._bg_task = None
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.warning("[CompanionV2] 后台汇率任务异常 error_type=%s", type(exc).__name__)

    async def close(self) -> None:
        task = self._bg_task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


async def close_exchange_rate_services(owner: Any) -> None:
    """Cancel and clear any ExchangeRateService instances attached to a host controller."""
    services = getattr(owner, "_exchange_rate_services", None)
    if not isinstance(services, dict):
        return
    for service in list(services.values()):
        close_fn = getattr(service, "close", None)
        if callable(close_fn):
            try:
                await close_fn()
            except Exception:
                logger.debug("[CompanionV2] 关闭汇率刷新服务失败", exc_info=True)
    services.clear()
