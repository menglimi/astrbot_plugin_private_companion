"""Pure domain models, currency aggregation, and atomic JSON stores for multi-station quota tracking.

Strictly pure Python stdlib — zero network I/O and zero host/AstrBot imports.
All outbound HTTP probing and adapter protocols live under ``astrbot_host/quota_adapters/``.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
import threading
from typing import Any, Iterable
from urllib.parse import unquote, urlsplit
from uuid import uuid4

from .pricing import DEFAULT_FALLBACK_USD_CNY, MAX_USD_CNY, MIN_USD_CNY

QUOTA_SCHEMA_VERSION = 1
MAX_QUOTA_STATIONS = 32
MAX_STORE_BYTES = 524_288
MAX_SNAPSHOTS_BYTES = 524_288
MIN_INTERVAL_SEC = 60
MAX_INTERVAL_SEC = 86_400
DEFAULT_INTERVAL_SEC = 300
MAX_NAME_CHARS = 64
MAX_URL_CHARS = 2048
MAX_SECRET_CHARS = 4096
MAX_SCRIPT_BYTES = 16_384
MAX_EXTRA_CHARS = 256
MAX_ERROR_CHARS = 256
DEFAULT_LOW_PERCENT = 10.0

DISPLAY_TYPES = ("", "CNY", "USD", "CREDIT", "TOKENS")
SNAPSHOT_DISPLAY_TYPES = ("CNY", "USD", "CREDIT", "TOKENS")
FIAT_CURRENCIES = ("", "CNY", "USD")
DISPLAY_UNIT_MODES = ("native", "fiat")
SOURCE_MODES = ("manual", "astrbot_provider")
AWARENESS_MODES = ("primary", "aggregate_sum", "any_low")
VALID_HEALTH_STATES = ("normal", "low", "critical", "error", "disabled", "pending")
MAX_SUBSCRIPTIONS_PER_STATION = 12

_STATION_ID_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")
_ADAPTER_ID_RE = re.compile(r"[a-z][a-z0-9_]{0,47}\Z")
_HEADER_NAME_RE = re.compile(r"[A-Za-z0-9!#$%&'*+.^_`|~-]{1,64}\Z")
_USER_ID_RE = re.compile(r"[0-9]{1,19}\Z")
_STORE_LOCK = threading.RLock()


class QuotaSettingsError(ValueError):
    """Domain validation or storage error carrying a stable machine error code."""

    def __init__(self, code: str, reason: str):
        super().__init__(reason)
        self.code = code
        self.reason = reason


def _reject_nonfinite(_value: str) -> None:
    raise ValueError("nonfinite JSON value")


def _clean_line(value: object, *, max_len: int = 256) -> str:
    if type(value) is not str:
        return ""
    cleaned = "".join(ch for ch in value if ord(ch) >= 32).strip()
    return cleaned[:max_len]


def _validate_finite_nonneg(value: object, *, field: str, max_value: float = 1e15) -> float:
    if isinstance(value, bool) or type(value) not in (int, float):
        raise QuotaSettingsError("quota_config_invalid", f"{field}必须是有效数字")
    num = float(value)
    if not math.isfinite(num) or num < 0 or num > max_value:
        raise QuotaSettingsError("quota_config_invalid", f"{field}超出有效范围")
    return num


def _optional_nonneg(value: object, *, field: str, max_value: float = 1e15) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            parsed = float(text)
        except ValueError as exc:
            raise QuotaSettingsError("quota_config_invalid", f"{field}必须是有效数字") from exc
        return _validate_finite_nonneg(parsed, field=field, max_value=max_value)
    return _validate_finite_nonneg(value, field=field, max_value=max_value)


def _validate_url(url: object, *, allow_http: bool = False, required: bool = False) -> str:
    if type(url) is not str:
        raise QuotaSettingsError("quota_config_invalid", "站点地址必须是字符串")
    text = url.strip()
    if not text:
        if required:
            raise QuotaSettingsError("quota_config_invalid", "请填写站点地址")
        return ""
    if len(text) > MAX_URL_CHARS or any(ord(ch) < 33 for ch in text):
        raise QuotaSettingsError("quota_config_invalid", "站点地址格式无效")
    parsed = urlsplit(text)
    allowed_schemes = ("https", "http") if allow_http else ("https",)
    if (
        parsed.scheme.lower() not in allowed_schemes
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        if parsed.scheme.lower() == "http" and not allow_http:
            raise QuotaSettingsError(
                "quota_http_not_allowed",
                "默认仅允许 HTTPS 站点；如为内网自建中转站，请勾选「允许局域网 HTTP」",
            )
        raise QuotaSettingsError("quota_config_invalid", "站点地址必须是合法的 HTTP(S) URL 且不能内嵌凭据")
    return text.rstrip("/")


def _validate_proxy(proxy_url: object) -> str:
    if proxy_url is None or proxy_url == "":
        return ""
    if type(proxy_url) is not str or len(proxy_url) > MAX_URL_CHARS or any(ord(ch) < 33 for ch in proxy_url):
        raise QuotaSettingsError("quota_config_invalid", "代理地址格式无效")
    text = proxy_url.strip()
    if not text:
        return ""
    try:
        parsed = urlsplit(text)
        if (
            parsed.scheme.lower() not in ("http", "https", "socks5")
            or not parsed.hostname
            or not parsed.port
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("invalid proxy")
    except ValueError as exc:
        raise QuotaSettingsError(
            "quota_config_invalid",
            "代理地址必须是带端口的 http://、https:// 或 socks5:// 地址",
        ) from exc
    return text


def _validate_secret(value: object, *, field: str, limit: int = MAX_SECRET_CHARS) -> str:
    if value is None or value == "":
        return ""
    if type(value) is not str or len(value) > limit or any(ord(ch) < 32 for ch in value):
        raise QuotaSettingsError("quota_config_invalid", f"{field}格式无效")
    return value.strip()


def slugify_station_id(name: str, existing_ids: Iterable[str] = ()) -> str:
    """Generate a deterministic, URL-safe ASCII station ID avoiding collisions."""
    existing = set(existing_ids)
    raw = str(name or "").strip().lower()
    parts: list[str] = []
    last_dash = False
    for ch in raw:
        if ("a" <= ch <= "z") or ("0" <= ch <= "9"):
            parts.append(ch)
            last_dash = False
        elif ch in ("-", "_", " ", ".", "/"):
            if parts and not last_dash:
                parts.append("-")
                last_dash = True
    base = "".join(parts).strip("-")[:48]
    if not base or not _STATION_ID_RE.fullmatch(base):
        digest_suffix = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8] if raw else uuid4().hex[:8]
        base = f"station-{digest_suffix}"
    if base not in existing:
        return base
    for idx in range(2, 1000):
        candidate = f"{base[:58]}-{idx}"
        if candidate not in existing:
            return candidate
    return f"station-{uuid4().hex[:10]}"


def normalize_currency_symbol(display_type: str, site_symbol: str = "") -> str:
    """Normalize currency symbol, discarding the NewAPI placeholder '¤' (U+00A4)."""
    sym = _clean_line(site_symbol, max_len=12)
    dtype = str(display_type or "").strip().upper()
    if sym and sym not in ("¤", "¥", "￥", "$"):
        return sym
    if dtype == "USD":
        return "$"
    if dtype == "CNY":
        return "¥"
    if sym == "¤":
        return ""
    return sym


def normalize_usd_cny_rate(rate: object) -> float:
    if isinstance(rate, bool) or type(rate) not in (int, float):
        return DEFAULT_FALLBACK_USD_CNY
    value = float(rate)
    if not math.isfinite(value) or not (MIN_USD_CNY <= value <= MAX_USD_CNY):
        return DEFAULT_FALLBACK_USD_CNY
    return value


def convert_currency_amount(
    amount: float | None,
    from_type: str,
    to_currency: str,
    usd_cny_rate: float = DEFAULT_FALLBACK_USD_CNY,
) -> float | None:
    """Convert fiat CNY/USD amount using live FX; return None for non-fiat units."""
    if amount is None or not math.isfinite(amount):
        return None
    src = str(from_type or "").strip().upper()
    dst = str(to_currency or "CNY").strip().upper()
    if src not in ("CNY", "USD") or dst not in ("CNY", "USD"):
        return None
    rate = normalize_usd_cny_rate(usd_cny_rate)
    if src == dst:
        return round(amount, 6)
    if src == "USD" and dst == "CNY":
        return round(amount * rate, 6)
    return round(amount / rate, 6)


def redact_quota_text(text: object, secrets: Iterable[str] = (), *, max_len: int = MAX_ERROR_CHARS) -> str:
    """Redact credentials, proxy passwords, and control chars before returning/logging text."""
    raw = str(text or "")
    expanded: set[str] = set()
    for secret in secrets:
        if not secret or type(secret) is not str:
            continue
        trimmed = secret.strip()
        if not trimmed:
            continue
        expanded.add(trimmed)
        decoded = unquote(trimmed)
        if decoded:
            expanded.add(decoded)
        # Also extract userinfo password from proxy URLs if present.
        if "://" in trimmed:
            try:
                parsed = urlsplit(trimmed)
                if parsed.password:
                    expanded.add(parsed.password)
                    expanded.add(unquote(parsed.password))
                if parsed.username:
                    expanded.add(parsed.username)
            except ValueError:
                pass
    for item in sorted(expanded, key=len, reverse=True):
        if len(item) >= 3:
            raw = raw.replace(item, "[已脱敏]")
    cleaned = "".join(ch if ord(ch) >= 32 else " " for ch in raw)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:max_len]


@dataclass(frozen=True)
class QuotaStationSpec:
    """Validated, immutable configuration for one monitored quota station."""

    id: str
    name: str
    adapter_id: str
    enabled: bool = True
    source_mode: str = "manual"
    astrbot_provider_id: str = ""
    base_url: str = ""
    allow_http: bool = False
    auth_token: str = ""
    user_id: str = ""
    user_id_header: str = "New-Api-User"
    custom_script: str = ""
    proxy_url: str = ""
    display_type: str = ""
    quota_per_unit: float = 0.0
    fiat_currency: str = ""
    fiat_per_base_unit: float | None = None
    display_unit_mode: str = "native"
    interval_sec: int = DEFAULT_INTERVAL_SEC
    low_threshold: float | None = None
    low_percent: float = DEFAULT_LOW_PERCENT
    critical_threshold: float | None = None
    participate_in_awareness: bool = False
    sort_order: int = 0

    def __post_init__(self) -> None:
        if type(self.id) is not str or not _STATION_ID_RE.fullmatch(self.id):
            raise QuotaSettingsError("quota_config_invalid", "站点标识格式无效")
        name = _clean_line(self.name, max_len=MAX_NAME_CHARS)
        if not name:
            raise QuotaSettingsError("quota_config_invalid", "请填写站点名称")
        object.__setattr__(self, "name", name)

        if type(self.adapter_id) is not str or not _ADAPTER_ID_RE.fullmatch(self.adapter_id):
            raise QuotaSettingsError("quota_config_invalid", "站点协议类型无效")
        if type(self.enabled) is not bool:
            raise QuotaSettingsError("quota_config_invalid", "启用开关必须是布尔值")
        if self.source_mode not in SOURCE_MODES:
            raise QuotaSettingsError("quota_config_invalid", "连接来源类型无效")
        if type(self.allow_http) is not bool:
            raise QuotaSettingsError("quota_config_invalid", "允许 HTTP 开关必须是布尔值")
        if type(self.participate_in_awareness) is not bool:
            raise QuotaSettingsError("quota_config_invalid", "参与余额感知开关必须是布尔值")

        provider_id = _clean_line(self.astrbot_provider_id, max_len=256)
        object.__setattr__(self, "astrbot_provider_id", provider_id)
        if self.source_mode == "astrbot_provider" and not provider_id:
            raise QuotaSettingsError("quota_config_invalid", "复用 AstrBot 供应商时必须选择供应商")

        url_required = self.source_mode == "manual" and self.adapter_id != "custom_script"
        base_url = _validate_url(self.base_url, allow_http=self.allow_http, required=url_required)
        object.__setattr__(self, "base_url", base_url)

        auth_token = _validate_secret(self.auth_token, field="访问令牌/API Key")
        object.__setattr__(self, "auth_token", auth_token)

        user_id = _clean_line(self.user_id, max_len=19)
        if user_id and not _USER_ID_RE.fullmatch(user_id):
            raise QuotaSettingsError("quota_config_invalid", "站点用户 ID 必须为数字")
        object.__setattr__(self, "user_id", user_id)

        uid_header = _clean_line(self.user_id_header or "New-Api-User", max_len=64) or "New-Api-User"
        if not _HEADER_NAME_RE.fullmatch(uid_header):
            raise QuotaSettingsError("quota_config_invalid", "用户 ID 请求头名称无效")
        object.__setattr__(self, "user_id_header", uid_header)

        if type(self.custom_script) is not str or len(self.custom_script.encode("utf-8")) > MAX_SCRIPT_BYTES:
            raise QuotaSettingsError("quota_config_invalid", "自定义脚本不能超过 16 KiB")
        object.__setattr__(self, "custom_script", self.custom_script.strip())

        proxy_url = _validate_proxy(self.proxy_url)
        object.__setattr__(self, "proxy_url", proxy_url)

        dtype = str(self.display_type or "").strip().upper()
        if dtype not in DISPLAY_TYPES:
            raise QuotaSettingsError("quota_config_invalid", "显示币种类型无效")
        object.__setattr__(self, "display_type", dtype)

        per_unit = _validate_finite_nonneg(self.quota_per_unit, field="每单位额度换算", max_value=1e12)
        object.__setattr__(self, "quota_per_unit", per_unit)

        fiat_cur = str(self.fiat_currency or "").strip().upper()
        if fiat_cur not in FIAT_CURRENCIES:
            raise QuotaSettingsError("quota_config_invalid", "折算法币币种必须为空、CNY 或 USD")
        object.__setattr__(self, "fiat_currency", fiat_cur)

        fiat_pbu = _optional_nonneg(self.fiat_per_base_unit, field="基准单位法币折算", max_value=1e6)
        if fiat_pbu is not None and fiat_pbu <= 0:
            fiat_pbu = None
        object.__setattr__(self, "fiat_per_base_unit", fiat_pbu)

        unit_mode = str(self.display_unit_mode or "native").strip().lower()
        if unit_mode not in DISPLAY_UNIT_MODES:
            raise QuotaSettingsError("quota_config_invalid", "显示单位模式必须是 native 或 fiat")
        object.__setattr__(self, "display_unit_mode", unit_mode)

        if isinstance(self.interval_sec, bool) or type(self.interval_sec) is not int:
            raise QuotaSettingsError("quota_config_invalid", "刷新间隔必须是整数秒")
        if not (MIN_INTERVAL_SEC <= self.interval_sec <= MAX_INTERVAL_SEC):
            raise QuotaSettingsError(
                "quota_config_invalid",
                f"刷新间隔必须在 {MIN_INTERVAL_SEC}–{MAX_INTERVAL_SEC} 秒之间",
            )

        low_t = _optional_nonneg(self.low_threshold, field="偏低提醒阈值")
        crit_t = _optional_nonneg(self.critical_threshold, field="告急提醒阈值")
        if low_t is not None and crit_t is not None and crit_t > low_t:
            raise QuotaSettingsError("quota_config_invalid", "告急提醒阈值不能高于偏低提醒阈值")
        object.__setattr__(self, "low_threshold", low_t)
        object.__setattr__(self, "critical_threshold", crit_t)

        low_pct = _validate_finite_nonneg(self.low_percent, field="偏低比例阈值", max_value=100.0)
        object.__setattr__(self, "low_percent", low_pct)

        if isinstance(self.sort_order, bool) or type(self.sort_order) is not int:
            raise QuotaSettingsError("quota_config_invalid", "排序值必须是整数")

    def to_storage_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "adapter_id": self.adapter_id,
            "enabled": self.enabled,
            "source_mode": self.source_mode,
            "astrbot_provider_id": self.astrbot_provider_id,
            "base_url": self.base_url,
            "allow_http": self.allow_http,
            "auth_token": self.auth_token,
            "user_id": self.user_id,
            "user_id_header": self.user_id_header,
            "custom_script": self.custom_script,
            "proxy_url": self.proxy_url,
            "display_type": self.display_type,
            "quota_per_unit": self.quota_per_unit,
            "fiat_currency": self.fiat_currency,
            "fiat_per_base_unit": self.fiat_per_base_unit,
            "display_unit_mode": self.display_unit_mode,
            "interval_sec": self.interval_sec,
            "low_threshold": self.low_threshold,
            "low_percent": self.low_percent,
            "critical_threshold": self.critical_threshold,
            "participate_in_awareness": self.participate_in_awareness,
            "sort_order": self.sort_order,
        }

    def to_public_dict(self) -> dict[str, Any]:
        """Serialize strictly without secret values; exposes only presence flags."""
        return {
            "id": self.id,
            "name": self.name,
            "adapter_id": self.adapter_id,
            "enabled": self.enabled,
            "source_mode": self.source_mode,
            "astrbot_provider_id": self.astrbot_provider_id,
            "base_url": self.base_url,
            "allow_http": self.allow_http,
            "has_token": bool(self.auth_token),
            "user_id": self.user_id,
            "user_id_header": self.user_id_header,
            "custom_script": self.custom_script,
            "has_proxy": bool(self.proxy_url),
            "display_type": self.display_type,
            "quota_per_unit": self.quota_per_unit,
            "fiat_currency": self.fiat_currency,
            "fiat_per_base_unit": self.fiat_per_base_unit,
            "display_unit_mode": self.display_unit_mode,
            "interval_sec": self.interval_sec,
            "low_threshold": self.low_threshold,
            "low_percent": self.low_percent,
            "critical_threshold": self.critical_threshold,
            "participate_in_awareness": self.participate_in_awareness,
            "sort_order": self.sort_order,
        }

    def to_export_dict(self) -> dict[str, Any]:
        """Secret-stripped export payload safe for backup/migration archives."""
        data = self.to_storage_dict()
        data["auth_token"] = ""
        data["proxy_url"] = ""
        return data

    def secret_free_fingerprint(self) -> str:
        payload = json.dumps(
            {
                "id": self.id,
                "adapter_id": self.adapter_id,
                "enabled": self.enabled,
                "source_mode": self.source_mode,
                "astrbot_provider_id": self.astrbot_provider_id,
                "base_url": self.base_url,
                "allow_http": self.allow_http,
                "token_hash": hashlib.sha256(self.auth_token.encode("utf-8")).hexdigest()[:16] if self.auth_token else "",
                "user_id": self.user_id,
                "user_id_header": self.user_id_header,
                "script_hash": hashlib.sha256(self.custom_script.encode("utf-8")).hexdigest()[:16] if self.custom_script else "",
                "proxy_hash": hashlib.sha256(self.proxy_url.encode("utf-8")).hexdigest()[:16] if self.proxy_url else "",
                "display_type": self.display_type,
                "quota_per_unit": self.quota_per_unit,
                "fiat_currency": self.fiat_currency,
                "fiat_per_base_unit": self.fiat_per_base_unit,
                "display_unit_mode": self.display_unit_mode,
                "low_threshold": self.low_threshold,
                "low_percent": self.low_percent,
                "critical_threshold": self.critical_threshold,
                "participate_in_awareness": self.participate_in_awareness,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]

    @classmethod
    def from_dict(cls, raw: object) -> "QuotaStationSpec":
        if type(raw) is not dict:
            raise QuotaSettingsError("quota_config_invalid", "站点配置项必须是对象")
        raw_interval = raw.get("interval_sec", DEFAULT_INTERVAL_SEC)
        if isinstance(raw_interval, str) and raw_interval.strip().isdigit():
            raw_interval = int(raw_interval.strip())
        elif isinstance(raw_interval, float) and raw_interval.is_integer():
            raw_interval = int(raw_interval)
        raw_per_unit = raw.get("quota_per_unit", 0.0)
        if raw_per_unit in (None, ""):
            raw_per_unit = 0.0
        elif isinstance(raw_per_unit, str):
            try:
                raw_per_unit = float(raw_per_unit.strip())
            except ValueError as exc:
                raise QuotaSettingsError("quota_config_invalid", "每单位额度换算必须是数字") from exc
        raw_low_pct = raw.get("low_percent", DEFAULT_LOW_PERCENT)
        if raw_low_pct in (None, ""):
            raw_low_pct = DEFAULT_LOW_PERCENT
        elif isinstance(raw_low_pct, str):
            try:
                raw_low_pct = float(raw_low_pct.strip())
            except ValueError as exc:
                raise QuotaSettingsError("quota_config_invalid", "偏低比例阈值必须是数字") from exc
        raw_sort = raw.get("sort_order", 0)
        if isinstance(raw_sort, str) and raw_sort.strip().lstrip("-").isdigit():
            raw_sort = int(raw_sort.strip())

        return cls(
            id=str(raw.get("id") or "").strip(),
            name=str(raw.get("name") or ""),
            adapter_id=str(raw.get("adapter_id") or "").strip(),
            enabled=raw.get("enabled", True),
            source_mode=str(raw.get("source_mode") or "manual").strip(),
            astrbot_provider_id=str(raw.get("astrbot_provider_id") or "").strip(),
            base_url=str(raw.get("base_url") or ""),
            allow_http=raw.get("allow_http", False),
            auth_token=str(raw.get("auth_token") or ""),
            user_id=str(raw.get("user_id") or ""),
            user_id_header=str(raw.get("user_id_header") or "New-Api-User"),
            custom_script=str(raw.get("custom_script") or ""),
            proxy_url=str(raw.get("proxy_url") or ""),
            display_type=str(raw.get("display_type") or ""),
            quota_per_unit=raw_per_unit,
            fiat_currency=str(raw.get("fiat_currency") or ""),
            fiat_per_base_unit=raw.get("fiat_per_base_unit"),
            display_unit_mode=str(raw.get("display_unit_mode") or "native"),
            interval_sec=raw_interval,
            low_threshold=raw.get("low_threshold"),
            low_percent=raw_low_pct,
            critical_threshold=raw.get("critical_threshold"),
            participate_in_awareness=raw.get("participate_in_awareness", False),
            sort_order=raw_sort,
        )


def _sanitize_subscription_items(raw: object) -> tuple[dict[str, Any], ...]:
    if not isinstance(raw, (list, tuple)):
        return ()
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        def _num(key: str) -> float | None:
            val = item.get(key)
            if val is None or isinstance(val, bool):
                return None
            try:
                num = float(val)
            except (TypeError, ValueError):
                return None
            return round(num, 6) if math.isfinite(num) and num >= 0 else None

        entry = {
            "name": _clean_line(str(item.get("name") or ""), max_len=80),
            "remaining": _num("remaining"),
            "used": _num("used"),
            "total": _num("total"),
            "fiat_remaining": _num("fiat_remaining"),
            "fiat_total": _num("fiat_total"),
            "expires_at": _clean_line(str(item.get("expires_at") or ""), max_len=64),
        }
        if entry["remaining"] is None and entry["total"] is None:
            continue
        out.append(entry)
        if len(out) >= MAX_SUBSCRIPTIONS_PER_STATION:
            break
    return tuple(out)


@dataclass(frozen=True)
class QuotaSnapshot:
    """Latest non-secret probe result for one station."""

    station_id: str
    ok: bool
    error: str = ""
    error_code: str = ""
    repair_hint: str = ""
    detected_header: str = ""
    remaining: float | None = None
    used: float | None = None
    total: float | None = None
    remaining_percent: float | None = None
    display_type: str = "CREDIT"
    symbol: str = ""
    quota_per_unit: float = 0.0
    custom_exchange_rate: float | None = None
    site_recharge_price: float | None = None
    inferred_fiat_currency: str = ""
    fiat_remaining: float | None = None
    fiat_used: float | None = None
    fiat_total: float | None = None
    fiat_last_used_cost: float | None = None
    subscriptions: tuple[dict[str, Any], ...] = ()
    server_address: str = ""
    account_name: str = ""
    group_name: str = ""
    extra: str = ""
    is_key_scoped: bool = False
    checked_at: str = ""
    latency_ms: int = 0
    last_used_at: str = ""
    last_used_model: str = ""
    last_used_cost: float | None = None
    last_used_error: str = ""
    logo_version: str = ""
    logo_error: str = ""

    def __post_init__(self) -> None:
        dtype = str(self.display_type or "CREDIT").strip().upper()
        if dtype not in SNAPSHOT_DISPLAY_TYPES:
            dtype = "CREDIT"
        object.__setattr__(self, "display_type", dtype)
        object.__setattr__(self, "symbol", normalize_currency_symbol(dtype, self.symbol))
        fiat_cur = str(self.inferred_fiat_currency or "").strip().upper()
        if fiat_cur not in FIAT_CURRENCIES:
            fiat_cur = ""
        object.__setattr__(self, "inferred_fiat_currency", fiat_cur)
        object.__setattr__(self, "subscriptions", _sanitize_subscription_items(self.subscriptions))
        object.__setattr__(self, "server_address", _clean_line(self.server_address, max_len=256))
        object.__setattr__(self, "error", _clean_line(self.error, max_len=MAX_ERROR_CHARS))
        object.__setattr__(self, "error_code", _clean_line(self.error_code, max_len=64))
        object.__setattr__(self, "repair_hint", _clean_line(self.repair_hint, max_len=MAX_ERROR_CHARS))
        object.__setattr__(self, "detected_header", _clean_line(self.detected_header, max_len=64))
        object.__setattr__(self, "account_name", _clean_line(self.account_name, max_len=128))
        object.__setattr__(self, "group_name", _clean_line(self.group_name, max_len=128))
        object.__setattr__(self, "extra", _clean_line(self.extra, max_len=MAX_EXTRA_CHARS))
        object.__setattr__(self, "last_used_model", _clean_line(self.last_used_model, max_len=160))
        object.__setattr__(self, "last_used_error", _clean_line(self.last_used_error, max_len=180))
        object.__setattr__(self, "logo_version", _clean_line(self.logo_version, max_len=32))
        object.__setattr__(self, "logo_error", _clean_line(self.logo_error, max_len=128))
        object.__setattr__(self, "latency_ms", max(0, int(self.latency_ms or 0)))

        # Compute remaining_percent when missing and total > 0.
        if self.remaining_percent is None and self.remaining is not None and self.total is not None and self.total > 0:
            pct = max(0.0, min(100.0, (self.remaining / self.total) * 100.0))
            object.__setattr__(self, "remaining_percent", round(pct, 2))

    def to_dict(self) -> dict[str, Any]:
        return {
            "station_id": self.station_id,
            "ok": self.ok,
            "error": self.error,
            "error_code": self.error_code,
            "repair_hint": self.repair_hint,
            "detected_header": self.detected_header,
            "remaining": self.remaining,
            "used": self.used,
            "total": self.total,
            "remaining_percent": self.remaining_percent,
            "display_type": self.display_type,
            "symbol": self.symbol,
            "quota_per_unit": self.quota_per_unit,
            "custom_exchange_rate": self.custom_exchange_rate,
            "site_recharge_price": self.site_recharge_price,
            "inferred_fiat_currency": self.inferred_fiat_currency,
            "fiat_remaining": self.fiat_remaining,
            "fiat_used": self.fiat_used,
            "fiat_total": self.fiat_total,
            "fiat_last_used_cost": self.fiat_last_used_cost,
            "subscriptions": [dict(item) for item in self.subscriptions],
            "server_address": self.server_address,
            "account_name": self.account_name,
            "group_name": self.group_name,
            "extra": self.extra,
            "is_key_scoped": self.is_key_scoped,
            "checked_at": self.checked_at,
            "latency_ms": self.latency_ms,
            "last_used_at": self.last_used_at,
            "last_used_model": self.last_used_model,
            "last_used_cost": self.last_used_cost,
            "last_used_error": self.last_used_error,
            "logo_version": self.logo_version,
            "logo_error": self.logo_error,
        }

    @classmethod
    def from_dict(cls, raw: object) -> "QuotaSnapshot":
        if type(raw) is not dict:
            raise QuotaSettingsError("quota_snapshot_invalid", "站点快照格式无效")
        def _opt_float(key: str) -> float | None:
            val = raw.get(key)
            if val is None or isinstance(val, bool):
                return None
            try:
                num = float(val)
            except (TypeError, ValueError):
                return None
            return num if math.isfinite(num) and num >= 0 else None

        return cls(
            station_id=str(raw.get("station_id") or "").strip(),
            ok=bool(raw.get("ok", False)),
            error=str(raw.get("error") or ""),
            error_code=str(raw.get("error_code") or ""),
            repair_hint=str(raw.get("repair_hint") or ""),
            detected_header=str(raw.get("detected_header") or ""),
            remaining=_opt_float("remaining"),
            used=_opt_float("used"),
            total=_opt_float("total"),
            remaining_percent=_opt_float("remaining_percent"),
            display_type=str(raw.get("display_type") or "CREDIT"),
            symbol=str(raw.get("symbol") or ""),
            quota_per_unit=_opt_float("quota_per_unit") or 0.0,
            custom_exchange_rate=_opt_float("custom_exchange_rate"),
            site_recharge_price=_opt_float("site_recharge_price"),
            inferred_fiat_currency=str(raw.get("inferred_fiat_currency") or ""),
            fiat_remaining=_opt_float("fiat_remaining"),
            fiat_used=_opt_float("fiat_used"),
            fiat_total=_opt_float("fiat_total"),
            fiat_last_used_cost=_opt_float("fiat_last_used_cost"),
            subscriptions=_sanitize_subscription_items(raw.get("subscriptions")),
            server_address=str(raw.get("server_address") or ""),
            account_name=str(raw.get("account_name") or ""),
            group_name=str(raw.get("group_name") or ""),
            extra=str(raw.get("extra") or ""),
            is_key_scoped=bool(raw.get("is_key_scoped", False)),
            checked_at=str(raw.get("checked_at") or ""),
            latency_ms=int(raw.get("latency_ms") or 0) if not isinstance(raw.get("latency_ms"), bool) else 0,
            last_used_at=str(raw.get("last_used_at") or ""),
            last_used_model=str(raw.get("last_used_model") or ""),
            last_used_cost=_opt_float("last_used_cost"),
            last_used_error=str(raw.get("last_used_error") or ""),
            logo_version=str(raw.get("logo_version") or ""),
            logo_error=str(raw.get("logo_error") or ""),
        )


@dataclass(frozen=True)
class QuotaAwarenessPolicy:
    """How multi-station quota feeds companion balance awareness."""

    mode: str = "primary"
    primary_station_id: str = ""
    target_currency: str = ""

    def __post_init__(self) -> None:
        if self.mode not in AWARENESS_MODES:
            raise QuotaSettingsError("quota_config_invalid", "余额感知聚合模式无效")
        primary = _clean_line(self.primary_station_id, max_len=64)
        if primary and not _STATION_ID_RE.fullmatch(primary):
            raise QuotaSettingsError("quota_config_invalid", "指定主站点标识无效")
        object.__setattr__(self, "primary_station_id", primary)
        target = str(self.target_currency or "").strip().upper()
        if target not in ("", "CNY", "USD"):
            raise QuotaSettingsError("quota_config_invalid", "汇总折算币种必须为空、CNY 或 USD")
        object.__setattr__(self, "target_currency", target)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "primary_station_id": self.primary_station_id,
            "target_currency": self.target_currency,
        }

    @classmethod
    def from_dict(cls, raw: object) -> "QuotaAwarenessPolicy":
        if type(raw) is not dict:
            return cls()
        return cls(
            mode=str(raw.get("mode") or "primary").strip(),
            primary_station_id=str(raw.get("primary_station_id") or "").strip(),
            target_currency=str(raw.get("target_currency") or "").strip().upper(),
        )


def evaluate_station_health(spec: QuotaStationSpec, snapshot: QuotaSnapshot | None) -> str:
    """Classify one station into normal/low/critical/error/disabled/pending."""
    if not spec.enabled:
        return "disabled"
    if snapshot is None or not snapshot.checked_at:
        return "pending"
    if not snapshot.ok:
        return "error"
    if snapshot.remaining is None:
        return "normal"

    remaining = snapshot.remaining
    pct = snapshot.remaining_percent
    if pct is None and snapshot.total is not None and snapshot.total > 0:
        pct = (remaining / snapshot.total) * 100.0
    elif pct is None and snapshot.used is not None and (remaining + snapshot.used) > 0:
        pct = (remaining / (remaining + snapshot.used)) * 100.0

    # Critical check
    if spec.critical_threshold is not None and spec.critical_threshold > 0 and remaining <= spec.critical_threshold:
        return "critical"
    if (
        snapshot.display_type in ("CNY", "USD")
        and pct is not None
        and spec.low_percent > 0
        and pct <= min(spec.low_percent / 2.0, 5.0)
    ):
        return "critical"

    # Low check
    if spec.low_threshold is not None and spec.low_threshold > 0 and remaining <= spec.low_threshold:
        return "low"
    if (
        snapshot.display_type in ("CNY", "USD")
        and pct is not None
        and spec.low_percent > 0
        and pct <= spec.low_percent
    ):
        return "low"
    return "normal"


def summarize_quota_portfolio(
    stations: Iterable[QuotaStationSpec],
    snapshots: dict[str, QuotaSnapshot],
    *,
    display_currency: str = "CNY",
    usd_cny_rate: float = DEFAULT_FALLBACK_USD_CNY,
) -> dict[str, Any]:
    """Compute exact FX-aware multi-station KPI totals without mixing CNY and USD raw values."""
    target = "USD" if str(display_currency or "CNY").upper() == "USD" else "CNY"
    rate = normalize_usd_cny_rate(usd_cny_rate)

    station_list = list(stations)
    counts = {state: 0 for state in VALID_HEALTH_STATES}
    fiat_remaining_converted = 0.0
    fiat_used_converted = 0.0
    custom_converted_remaining = 0.0
    custom_converted_count = 0
    cny_remaining = 0.0
    usd_remaining = 0.0
    cny_count = 0
    usd_count = 0
    non_fiat_count = 0
    per_station_health: dict[str, str] = {}
    per_station_converted: dict[str, float | None] = {}
    latest_checked_at = ""

    for spec in station_list:
        snap = snapshots.get(spec.id)
        health = evaluate_station_health(spec, snap)
        counts[health] += 1
        per_station_health[spec.id] = health
        per_station_converted[spec.id] = None

        if snap and snap.checked_at and snap.checked_at > latest_checked_at:
            latest_checked_at = snap.checked_at
        if not spec.enabled or snap is None or not snap.ok or snap.remaining is None:
            continue

        if snap.display_type == "CNY":
            cny_count += 1
            cny_remaining += snap.remaining
        elif snap.display_type == "USD":
            usd_count += 1
            usd_remaining += snap.remaining
        else:
            non_fiat_count += 1
            custom_cur = spec.fiat_currency or snap.inferred_fiat_currency
            if snap.fiat_remaining is not None and custom_cur in ("CNY", "USD"):
                conv_custom = convert_currency_amount(snap.fiat_remaining, custom_cur, target, rate)
                if conv_custom is not None:
                    custom_converted_remaining += conv_custom
                    custom_converted_count += 1
            continue

        conv_rem = convert_currency_amount(snap.remaining, snap.display_type, target, rate)
        if conv_rem is not None:
            fiat_remaining_converted += conv_rem
            per_station_converted[spec.id] = round(conv_rem, 4)
        if snap.used is not None:
            conv_used = convert_currency_amount(snap.used, snap.display_type, target, rate)
            if conv_used is not None:
                fiat_used_converted += conv_used

    return {
        "display_currency": target,
        "display_symbol": normalize_currency_symbol(target),
        "usd_cny_rate": rate,
        "total_stations": len(station_list),
        "enabled_stations": sum(1 for st in station_list if st.enabled),
        "ok_count": counts["normal"] + counts["low"] + counts["critical"],
        "normal_count": counts["normal"],
        "low_count": counts["low"],
        "critical_count": counts["critical"],
        "error_count": counts["error"],
        "disabled_count": counts["disabled"],
        "pending_count": counts["pending"],
        "fiat_remaining": round(fiat_remaining_converted, 4),
        "fiat_used": round(fiat_used_converted, 4),
        "fiat_remaining_including_custom": round(fiat_remaining_converted + custom_converted_remaining, 4),
        "custom_converted_remaining": round(custom_converted_remaining, 4),
        "custom_converted_count": custom_converted_count,
        "cny_remaining": round(cny_remaining, 4),
        "usd_remaining": round(usd_remaining, 4),
        "cny_station_count": cny_count,
        "usd_station_count": usd_count,
        "non_fiat_station_count": non_fiat_count,
        "latest_checked_at": latest_checked_at,
        "health_by_id": per_station_health,
        "converted_remaining_by_id": per_station_converted,
    }


def _compute_edit_token(doc: dict[str, Any]) -> str:
    canonical = json.dumps(
        {
            "version": doc.get("version", QUOTA_SCHEMA_VERSION),
            "stations": doc.get("stations", []),
            "awareness": doc.get("awareness", {}),
        },
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _atomic_write_json(path: Path, payload: dict[str, Any], *, private_mode: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        if private_mode:
            try:
                os.chmod(tmp_path, 0o600)
            except OSError:
                pass
        os.replace(tmp_path, path)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def default_quota_document() -> dict[str, Any]:
    doc = {
        "version": QUOTA_SCHEMA_VERSION,
        "stations": [],
        "awareness": QuotaAwarenessPolicy().to_dict(),
    }
    doc["edit_token"] = _compute_edit_token(doc)
    return doc


def _validate_document(raw: object) -> tuple[list[QuotaStationSpec], QuotaAwarenessPolicy]:
    if type(raw) is not dict:
        raise QuotaSettingsError("quota_config_unreadable", "额度看板配置文件格式无效")
    raw_stations = raw.get("stations", [])
    if type(raw_stations) is not list:
        raise QuotaSettingsError("quota_config_unreadable", "站点列表格式无效")
    if len(raw_stations) > MAX_QUOTA_STATIONS:
        raise QuotaSettingsError("quota_config_invalid", f"站点数量不能超过 {MAX_QUOTA_STATIONS} 个")
    seen: set[str] = set()
    stations: list[QuotaStationSpec] = []
    for item in raw_stations:
        spec = QuotaStationSpec.from_dict(item)
        if spec.id in seen:
            raise QuotaSettingsError("quota_config_invalid", f"存在重复的站点标识：{spec.id}")
        seen.add(spec.id)
        stations.append(spec)
    stations.sort(key=lambda s: (s.sort_order, s.name.lower(), s.id))
    awareness = QuotaAwarenessPolicy.from_dict(raw.get("awareness"))
    return stations, awareness


class QuotaStationStore:
    """Host-wide atomic JSON store at ``<runtime_root>/quota/stations.json``."""

    def __init__(self, runtime_root: Path):
        self.root = Path(runtime_root).resolve()
        self.directory = self.root / "quota"
        self.path = self.directory / "stations.json"
        self._cache_stat: tuple[int, int, int] | None = None
        self._cache_doc: tuple[list[QuotaStationSpec], QuotaAwarenessPolicy, str] | None = None

    def _read_unlocked(self) -> tuple[list[QuotaStationSpec], QuotaAwarenessPolicy, str]:
        if not self.path.exists():
            default_doc = default_quota_document()
            return [], QuotaAwarenessPolicy(), default_doc["edit_token"]
        try:
            raw = self.path.read_bytes()
            if len(raw) > MAX_STORE_BYTES:
                raise ValueError("quota stations file is too large")
            data = json.loads(raw.decode("utf-8"), parse_constant=_reject_nonfinite)
        except (OSError, UnicodeError, ValueError) as exc:
            raise QuotaSettingsError("quota_config_unreadable", "额度看板配置文件无法读取") from exc
        stations, awareness = _validate_document(data)
        doc = {
            "version": QUOTA_SCHEMA_VERSION,
            "stations": [st.to_storage_dict() for st in stations],
            "awareness": awareness.to_dict(),
        }
        return stations, awareness, _compute_edit_token(doc)

    def _write_unlocked(
        self,
        stations: list[QuotaStationSpec],
        awareness: QuotaAwarenessPolicy,
    ) -> tuple[list[QuotaStationSpec], QuotaAwarenessPolicy, str]:
        ordered = sorted(stations, key=lambda s: (s.sort_order, s.name.lower(), s.id))
        if len(ordered) > MAX_QUOTA_STATIONS:
            raise QuotaSettingsError("quota_config_invalid", f"站点数量不能超过 {MAX_QUOTA_STATIONS} 个")
        doc = {
            "version": QUOTA_SCHEMA_VERSION,
            "stations": [st.to_storage_dict() for st in ordered],
            "awareness": awareness.to_dict(),
        }
        token = _compute_edit_token(doc)
        _atomic_write_json(self.path, doc, private_mode=True)
        try:
            st = self.path.stat()
            self._cache_stat = (st.st_mtime_ns, st.st_size, st.st_ino)
            self._cache_doc = (list(ordered), awareness, token)
        except OSError:
            self._cache_stat = None
            self._cache_doc = None
        return ordered, awareness, token

    def exists(self) -> bool:
        return self.path.exists()

    def read(self) -> tuple[list[QuotaStationSpec], QuotaAwarenessPolicy, str]:
        with _STORE_LOCK:
            return self._read_unlocked()

    def read_safe(self) -> tuple[list[QuotaStationSpec], QuotaAwarenessPolicy, str]:
        with _STORE_LOCK:
            try:
                st = self.path.stat()
            except OSError:
                self._cache_stat = None
                self._cache_doc = None
                default_doc = default_quota_document()
                return [], QuotaAwarenessPolicy(), default_doc["edit_token"]
            sig = (st.st_mtime_ns, st.st_size, st.st_ino)
            if self._cache_doc is not None and self._cache_stat == sig:
                stations, awareness, token = self._cache_doc
                return list(stations), awareness, token
            try:
                result = self._read_unlocked()
            except QuotaSettingsError:
                default_doc = default_quota_document()
                return [], QuotaAwarenessPolicy(), default_doc["edit_token"]
            self._cache_stat = sig
            self._cache_doc = (list(result[0]), result[1], result[2])
            return result

    def upsert_station(
        self,
        edit_token: object,
        payload: object,
        *,
        seed_stations: tuple[QuotaStationSpec, ...] | list[QuotaStationSpec] = (),
    ) -> tuple[QuotaStationSpec, list[QuotaStationSpec], QuotaAwarenessPolicy, str]:
        """Create or update one station with optimistic concurrency and secret preservation."""
        with _STORE_LOCK:
            stations, awareness, current_token = self._read_unlocked()
            if type(edit_token) is not str or edit_token != current_token:
                raise QuotaSettingsError("quota_config_conflict", "站点配置已在别处更新，请刷新后再保存")
            if type(payload) is not dict:
                raise QuotaSettingsError("quota_config_invalid", "站点参数必须是 JSON 对象")
            if not self.exists() and seed_stations:
                stations = list(seed_stations)

            raw = deepcopy(payload)
            clear_token = bool(raw.pop("clear_auth_token", False))
            clear_proxy = bool(raw.pop("clear_proxy_url", False))

            station_id = str(raw.get("id") or "").strip()
            existing_map = {st.id: st for st in stations}
            existing = existing_map.get(station_id) if station_id else None

            if not station_id:
                station_id = slugify_station_id(str(raw.get("name") or ""), existing_map.keys())
                raw["id"] = station_id
                existing = existing_map.get(station_id)

            if existing is not None:
                incoming_token = str(raw.get("auth_token") or "").strip()
                incoming_proxy = str(raw.get("proxy_url") or "").strip()
                if clear_token and incoming_token:
                    raise QuotaSettingsError("quota_config_invalid", "请不要同时填写新令牌并勾选清除令牌")
                if clear_proxy and incoming_proxy:
                    raise QuotaSettingsError("quota_config_invalid", "请不要同时填写新代理并勾选清除代理")
                if clear_token:
                    raw["auth_token"] = ""
                elif not incoming_token:
                    raw["auth_token"] = existing.auth_token
                if clear_proxy:
                    raw["proxy_url"] = ""
                elif not incoming_proxy:
                    raw["proxy_url"] = existing.proxy_url
                if "sort_order" not in raw:
                    raw["sort_order"] = existing.sort_order
                if "fiat_currency" not in raw:
                    raw["fiat_currency"] = existing.fiat_currency
                if "fiat_per_base_unit" not in raw:
                    raw["fiat_per_base_unit"] = existing.fiat_per_base_unit
                if "display_unit_mode" not in raw:
                    raw["display_unit_mode"] = existing.display_unit_mode
            else:
                if "sort_order" not in raw:
                    raw["sort_order"] = len(stations)

            spec = QuotaStationSpec.from_dict(raw)
            next_stations = [st for st in stations if st.id != spec.id]
            next_stations.append(spec)
            ordered, next_awareness, next_token = self._write_unlocked(next_stations, awareness)
            return spec, ordered, next_awareness, next_token

    def delete_station(
        self,
        edit_token: object,
        station_id: object,
    ) -> tuple[list[QuotaStationSpec], QuotaAwarenessPolicy, str]:
        with _STORE_LOCK:
            stations, awareness, current_token = self._read_unlocked()
            if type(edit_token) is not str or edit_token != current_token:
                raise QuotaSettingsError("quota_config_conflict", "站点配置已在别处更新，请刷新后再删除")
            sid = str(station_id or "").strip()
            if not sid or not any(st.id == sid for st in stations):
                raise QuotaSettingsError("quota_station_not_found", "未找到要删除的站点")
            next_stations = [st for st in stations if st.id != sid]
            next_awareness = awareness
            if awareness.primary_station_id == sid:
                next_awareness = QuotaAwarenessPolicy(
                    mode=awareness.mode,
                    primary_station_id="",
                    target_currency=awareness.target_currency,
                )
            return self._write_unlocked(next_stations, next_awareness)

    def update_awareness(
        self,
        edit_token: object,
        patch: object,
        *,
        seed_stations: tuple[QuotaStationSpec, ...] | list[QuotaStationSpec] = (),
    ) -> tuple[list[QuotaStationSpec], QuotaAwarenessPolicy, str]:
        with _STORE_LOCK:
            stations, awareness, current_token = self._read_unlocked()
            if type(edit_token) is not str or edit_token != current_token:
                raise QuotaSettingsError("quota_config_conflict", "余额感知策略已在别处更新，请刷新后再保存")
            if type(patch) is not dict:
                raise QuotaSettingsError("quota_config_invalid", "余额感知策略参数无效")
            if not self.exists() and seed_stations:
                stations = list(seed_stations)

            allowed = {"mode", "primary_station_id", "target_currency", "participating_station_ids"}
            if set(patch) - allowed:
                raise QuotaSettingsError("quota_config_invalid", "余额感知策略包含不支持的字段")

            merged_policy = {
                **awareness.to_dict(),
                **{k: patch[k] for k in ("mode", "primary_station_id", "target_currency") if k in patch},
            }
            next_awareness = QuotaAwarenessPolicy.from_dict(merged_policy)

            station_ids = {st.id for st in stations}
            if next_awareness.primary_station_id and next_awareness.primary_station_id not in station_ids:
                raise QuotaSettingsError("quota_station_not_found", "指定的主站点不存在")

            next_stations = list(stations)
            if "participating_station_ids" in patch:
                raw_ids = patch["participating_station_ids"]
                if type(raw_ids) is not list or any(type(x) is not str for x in raw_ids):
                    raise QuotaSettingsError("quota_config_invalid", "参与感知的站点列表格式无效")
                selected = set(raw_ids)
                next_stations = [
                    QuotaStationSpec.from_dict({**st.to_storage_dict(), "participate_in_awareness": st.id in selected})
                    for st in stations
                ]

            return self._write_unlocked(next_stations, next_awareness)

    def export_redacted_document(self) -> dict[str, Any]:
        """Return a backup-safe document with all tokens and proxy URLs stripped."""
        stations, awareness, _ = self.read_safe()
        return {
            "version": QUOTA_SCHEMA_VERSION,
            "stations": [st.to_export_dict() for st in stations],
            "awareness": awareness.to_dict(),
        }


class QuotaSnapshotCache:
    """In-memory + lightweight atomic disk cache for non-secret station snapshots."""

    def __init__(self, runtime_root: Path):
        self.root = Path(runtime_root).resolve()
        self.path = self.root / "quota" / "snapshots.json"
        self._lock = threading.RLock()
        self._snapshots: dict[str, QuotaSnapshot] = {}
        self._updated_at: str = ""
        self._loaded = False

    def _ensure_loaded_unlocked(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not self.path.exists():
            return
        try:
            raw = self.path.read_bytes()
            if len(raw) > MAX_SNAPSHOTS_BYTES:
                return
            data = json.loads(raw.decode("utf-8"), parse_constant=_reject_nonfinite)
            if type(data) is not dict:
                return
            self._updated_at = str(data.get("updated_at") or "")
            items = data.get("snapshots", {})
            if isinstance(items, dict):
                for sid, item in items.items():
                    if isinstance(sid, str) and isinstance(item, dict):
                        try:
                            snap = QuotaSnapshot.from_dict({**item, "station_id": sid})
                            self._snapshots[sid] = snap
                        except QuotaSettingsError:
                            continue
        except (OSError, UnicodeError, ValueError):
            return

    def _persist_unlocked(self) -> None:
        payload = {
            "version": QUOTA_SCHEMA_VERSION,
            "updated_at": self._updated_at,
            "snapshots": {sid: snap.to_dict() for sid, snap in self._snapshots.items()},
        }
        try:
            _atomic_write_json(self.path, payload, private_mode=False)
        except OSError:
            pass

    def all(self) -> tuple[dict[str, QuotaSnapshot], str]:
        with self._lock:
            self._ensure_loaded_unlocked()
            return dict(self._snapshots), self._updated_at

    def get(self, station_id: str) -> QuotaSnapshot | None:
        with self._lock:
            self._ensure_loaded_unlocked()
            return self._snapshots.get(station_id)

    def put(self, snapshot: QuotaSnapshot, *, updated_at: str | None = None) -> None:
        with self._lock:
            self._ensure_loaded_unlocked()
            self._snapshots[snapshot.station_id] = snapshot
            self._updated_at = updated_at or snapshot.checked_at or datetime.now(timezone.utc).isoformat()
            self._persist_unlocked()

    def put_many(self, snapshots: Iterable[QuotaSnapshot], *, updated_at: str | None = None) -> None:
        with self._lock:
            self._ensure_loaded_unlocked()
            changed = False
            for snap in snapshots:
                self._snapshots[snap.station_id] = snap
                changed = True
            if changed:
                self._updated_at = updated_at or datetime.now(timezone.utc).isoformat()
                self._persist_unlocked()

    def delete(self, station_id: str) -> None:
        with self._lock:
            self._ensure_loaded_unlocked()
            if station_id in self._snapshots:
                del self._snapshots[station_id]
                self._persist_unlocked()

    def prune(self, valid_ids: Iterable[str]) -> None:
        keep = set(valid_ids)
        with self._lock:
            self._ensure_loaded_unlocked()
            stale = [sid for sid in self._snapshots if sid not in keep]
            if stale:
                for sid in stale:
                    del self._snapshots[sid]
                self._persist_unlocked()
