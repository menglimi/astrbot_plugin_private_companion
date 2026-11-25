"""New API / One API / VoAPI / Veloera / RixAPI / derivative relay station adapters.

Incorporates lessons from quota-watch (by mofei-114):
- Public /api/status metadata (currency display type, quota_per_unit, custom symbol, site logo)
- Independent /api/log/self?p=1&page_size=1&type=2 last-consumption probe with 1x transient retry
- Precise diagnosis of sk- channel keys (ACCESS_TOKEN_SCOPE_DENIED) and missing New-Api-User headers
- Optional one-time login token exchange that NEVER persists user passwords.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
import re
import time
from typing import Any
from urllib.parse import urlsplit

import aiohttp

from companion_v2.model_gateway.quota import (
    QuotaSnapshot,
    QuotaStationSpec,
    normalize_currency_symbol,
    redact_quota_text,
)

from .base import (
    AdapterCapabilities,
    AdapterDescriptor,
    AdapterProbeContext,
    FieldSpec,
    QuotaAdapter,
    QuotaHttpError,
    http_request_json,
    normalize_station_root,
    safe_number,
)

DEFAULT_NEWAPI_PER_UNIT = 500_000.0
_HEADER_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9-]{0,63}$")

_USER_HEADER_HINTS = (
    ("rix-api-user", "Rix-Api-User"),
    ("veloera-user", "Veloera-User"),
    ("new-api-user", "New-Api-User"),
)


def normalize_newapi_display_type(raw_display_type: object) -> str:
    """Normalize NewAPI /api/status quota_display_type without misclassifying CUSTOM points as USD."""
    raw = str(raw_display_type or "").strip().upper()
    if raw in ("CNY", "USD", "CREDIT", "TOKENS"):
        return raw
    if raw in ("CUSTOM", "POINTS", "POINT"):
        return "CREDIT"
    return ""


@dataclass(frozen=True)
class NewApiDisplayParams:
    """Resolved display mode, divisor, multiplier, and symbol for a NewAPI-family station."""

    display_type: str
    per_unit: float
    display_multiplier: float
    symbol: str
    site_raw_display_type: str = ""
    custom_rate: float | None = None
    site_price: float | None = None


def resolve_newapi_display_params(
    site_status: dict[str, Any] | None,
    *,
    override_display_type: str = "",
    override_per_unit: float = 0.0,
) -> NewApiDisplayParams:
    """Resolve NewAPI 4-mode conversion parameters with strict anti-crosstalk rules.

    Truth table on ``base_units = raw_quota / quota_per_unit``:
    - ``USD``: ``base_units * 1.0``
    - ``CNY``: ``base_units * usd_exchange_rate`` (ONLY when site itself reports CNY)
    - ``CUSTOM`` / ``POINTS`` -> ``CREDIT``: ``base_units * custom_currency_exchange_rate``
      (NEVER reads ``usd_exchange_rate``)
    - ``TOKENS`` (or ``display_in_currency=false`` without explicit display type): ``raw_quota``
    """
    status = site_status if isinstance(site_status, dict) else {}
    raw_site_dt = str(status.get("quota_display_type") or "").strip().upper()
    if raw_site_dt:
        site_dt = normalize_newapi_display_type(raw_site_dt)
    elif status.get("display_in_currency") is False:
        site_dt = "TOKENS"
    else:
        site_dt = ""

    override_dt = normalize_newapi_display_type(override_display_type)
    display_type = override_dt or site_dt or "USD"

    per_unit = (
        float(override_per_unit)
        if (isinstance(override_per_unit, (int, float)) and not isinstance(override_per_unit, bool) and math.isfinite(override_per_unit) and override_per_unit > 0)
        else 0.0
    )
    if per_unit <= 0:
        spu = safe_number(status.get("quota_per_unit"))
        per_unit = spu if (spu is not None and spu > 0) else DEFAULT_NEWAPI_PER_UNIT

    usd_rate = safe_number(status.get("usd_exchange_rate"))
    if usd_rate is not None and usd_rate <= 0:
        usd_rate = None
    custom_rate = safe_number(status.get("custom_currency_exchange_rate"))
    if custom_rate is not None and custom_rate <= 0:
        custom_rate = None
    site_price = safe_number(status.get("price"))
    if site_price is not None and site_price <= 0:
        site_price = None

    if display_type in ("USD", "TOKENS"):
        display_multiplier = 1.0
    elif display_type == "CNY":
        display_multiplier = usd_rate if (site_dt == "CNY" and usd_rate is not None) else 1.0
    elif display_type == "CREDIT":
        display_multiplier = custom_rate if (site_dt == "CREDIT" and custom_rate is not None) else 1.0
    else:
        display_multiplier = 1.0

    raw_symbol = str(status.get("custom_currency_symbol") or "") if display_type == "CREDIT" else ""
    symbol = normalize_currency_symbol(display_type, raw_symbol)

    return NewApiDisplayParams(
        display_type=display_type,
        per_unit=per_unit,
        display_multiplier=display_multiplier,
        symbol=symbol,
        site_raw_display_type=raw_site_dt,
        custom_rate=custom_rate,
        site_price=site_price,
    )


def convert_newapi_quota(
    raw_quota: float | None,
    *,
    per_unit: float,
    display_type: str,
    display_multiplier: float = 1.0,
) -> float | None:
    """Convert raw NewAPI integer quota into display units consistently for balance and logs."""
    if raw_quota is None:
        return None
    if display_type == "TOKENS":
        return max(0.0, float(raw_quota))
    if per_unit <= 0:
        return None
    mult = float(display_multiplier) if (math.isfinite(display_multiplier) and display_multiplier > 0) else 1.0
    return max(0.0, (float(raw_quota) / float(per_unit)) * mult)


_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_CNY_PAY_HINTS = ("alipay", "wxpay", "wechat", "epay", "支付宝", "微信", "易支付")
_USD_PAY_HINTS = ("stripe", "creem", "paypal")


def _has_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(str(text or "")))


def _pay_methods_text(topup_info: dict[str, Any] | None) -> str:
    if not isinstance(topup_info, dict):
        return ""
    methods = topup_info.get("pay_methods")
    parts: list[str] = []
    if isinstance(methods, list):
        for item in methods:
            if isinstance(item, dict):
                parts.append(str(item.get("type") or ""))
                parts.append(str(item.get("name") or ""))
            elif isinstance(item, str):
                parts.append(item)
    return " ".join(parts).lower()


def infer_newapi_fiat_conversion(
    params: NewApiDisplayParams,
    site_status: dict[str, Any] | None,
    topup_info: dict[str, Any] | None = None,
    *,
    override_fiat_currency: str = "",
    override_fiat_per_base_unit: float | None = None,
) -> tuple[str, float | None, float | None]:
    """Return ``(inferred_fiat_currency, site_recharge_price, effective_fiat_per_base_unit)``.

    Zero false positives on genuine USD sites: only infers CNY on CUSTOM stations when
    confirmed by /api/user/topup/info CNY rails or unambiguous price + non-USD symbol/CJK signals.
    """
    status = site_status if isinstance(site_status, dict) else {}
    topup = topup_info if isinstance(topup_info, dict) else {}

    topup_price = safe_number(topup.get("price"))
    if topup_price is not None and topup_price <= 0:
        topup_price = None
    site_price = topup_price if topup_price is not None else params.site_price

    override_cur = str(override_fiat_currency or "").strip().upper()
    if override_cur not in ("CNY", "USD"):
        override_cur = ""

    override_pbu = (
        float(override_fiat_per_base_unit)
        if (
            override_fiat_per_base_unit is not None
            and not isinstance(override_fiat_per_base_unit, bool)
            and math.isfinite(override_fiat_per_base_unit)
            and override_fiat_per_base_unit > 0
        )
        else None
    )

    if params.display_type in ("CNY", "USD"):
        return params.display_type, site_price, params.display_multiplier

    if params.display_type != "CREDIT":
        return "", site_price, None

    inferred_cur = override_cur
    if not inferred_cur:
        methods_text = _pay_methods_text(topup)
        has_cny_pay = any(hint in methods_text for hint in _CNY_PAY_HINTS)
        has_usd_pay = (
            topup.get("enable_stripe_topup") is True
            or topup.get("enable_creem_topup") is True
            or any(hint in methods_text for hint in _USD_PAY_HINTS)
        )
        if has_cny_pay and not has_usd_pay:
            inferred_cur = "CNY"
        elif has_usd_pay and not has_cny_pay:
            inferred_cur = "USD"
        elif site_price is not None and 0 < site_price < 4.5:
            sym = params.symbol.strip()
            non_usd_symbol = bool(sym) and sym != "$" and (any(ord(ch) > 127 for ch in sym) or _has_cjk(sym))
            sys_name = str(status.get("system_name") or "")
            if non_usd_symbol or _has_cjk(sys_name):
                inferred_cur = "CNY"

    # NewAPI's status/topup `price` field is historically RMB per base unit (default 7.3).
    # Never apply it automatically as a USD rate on overseas Stripe/Creem CUSTOM stations.
    effective_pbu = override_pbu if override_pbu is not None else (site_price if inferred_cur == "CNY" else None)
    return inferred_cur, site_price, effective_pbu


def _format_newapi_custom_extra(
    params: NewApiDisplayParams,
    quota_raw: float,
    *,
    inferred_fiat_currency: str = "",
    effective_fiat_pbu: float | None = None,
) -> str:
    """Build subtle conversion reference metadata for non-fiat CUSTOM currency stations."""
    if params.display_type != "CREDIT":
        return ""
    if params.site_raw_display_type not in ("CUSTOM", "POINTS", "POINT", "CREDIT"):
        return ""
    parts: list[str] = []
    unit_label = params.symbol or "积分"
    fiat_sym = normalize_currency_symbol(inferred_fiat_currency) if inferred_fiat_currency in ("CNY", "USD") else "¥"
    pbu = (
        effective_fiat_pbu
        if (effective_fiat_pbu is not None and effective_fiat_pbu > 0)
        else (params.site_price if inferred_fiat_currency == "CNY" else None)
    )

    if params.custom_rate is not None and params.custom_rate > 0:
        if pbu is not None and pbu > 0:
            per_fiat = params.custom_rate / pbu
            rate_txt = str(int(per_fiat)) if abs(per_fiat - round(per_fiat)) < 1e-6 else f"{per_fiat:g}"
            parts.append(f"站内换算 {rate_txt} {unit_label} = {fiat_sym}1")
        elif params.custom_rate != 1.0:
            rate_txt = str(int(params.custom_rate)) if params.custom_rate.is_integer() else f"{params.custom_rate:g}"
            parts.append(f"站内换算 {rate_txt} {unit_label} = 1 基准单位")
    if pbu is not None and pbu > 0 and params.per_unit > 0:
        ref_recharge = max(0.0, (float(quota_raw) / float(params.per_unit)) * pbu)
        parts.append(f"参考充值约 {fiat_sym}{ref_recharge:,.2f}")
    return " · ".join(parts)


def _safe_user_id_header(header_name: object) -> str:
    candidate = str(header_name or "").strip()
    if candidate and _HEADER_NAME_RE.match(candidate):
        return candidate
    return "New-Api-User"


def _known_alias_roots(root: str) -> tuple[str, ...]:
    """Preserve syuan.org host-alias compatibility (aiapi/api/ai.syuan.org)."""
    try:
        parsed = urlsplit(root)
    except ValueError:
        return (root,)
    hostname = (parsed.hostname or "").lower()
    syuan_aliases = ("aiapi.syuan.org", "api.syuan.org", "ai.syuan.org")
    if hostname not in syuan_aliases:
        return (root,)
    roots = [root]
    for alt_host in syuan_aliases:
        if alt_host == hostname:
            continue
        netloc = f"{alt_host}:{parsed.port}" if parsed.port is not None else alt_host
        alt_root = parsed._replace(netloc=netloc).geturl().rstrip("/")
        if alt_root not in roots:
            roots.append(alt_root)
    return tuple(roots)


def detect_required_user_header(text: str) -> str:
    lower = str(text or "").lower()
    for needle, header_name in _USER_HEADER_HINTS:
        if needle in lower:
            return header_name
    if "user id" in lower and ("header" in lower or "未提供" in lower or "missing" in lower):
        return "New-Api-User"
    return ""


def _classify_newapi_auth_error(status: int, message: str, body_preview: str) -> tuple[str, str, str, str]:
    """Return ``(error_code, short_error, repair_hint, detected_header)``."""
    combined = f"{message} {body_preview}".strip()
    lower = combined.lower()

    if "access_token_scope_denied" in lower or "sk-" in lower and "token" in lower:
        return (
            "sk_key_rejected",
            "访问令牌权限不足（这是渠道 sk- 密钥，无法查账号余额）",
            "请改填站点「个人设置 → 系统访问令牌」，不要填写模型调用用的 sk- 密钥。",
            "",
        )
    header = detect_required_user_header(combined)
    if header:
        return (
            "requires_user_id",
            f"该站点要求携带请求头 {header}（缺少用户 ID）",
            f"请在「用户 ID」栏填写站点数字 UID（请求头已自动匹配为 {header}）。",
            header,
        )
    if status in (401, 403) or any(
        kw in lower for kw in ("unauthorized", "invalid access token", "not logged in", "auth_unauthorized", "无权")
    ):
        msg = message.strip() or f"令牌校验失败 (HTTP {status or 401})"
        return (
            "auth_failed",
            msg,
            "请核对系统访问令牌是否有效或已过期；如该站是 NewAPI 衍生站，也可检查是否需补充用户 ID。",
            "",
        )
    return (
        "http_error" if status >= 400 else "invalid_response",
        message.strip() or (f"上游返回 HTTP {status}" if status >= 400 else "站点返回 success=false"),
        "请核对站点地址、访问令牌或代理配置后重试。",
        "",
    )


async def fetch_newapi_status(
    root: str,
    *,
    allow_http: bool = False,
    proxy_url: str = "",
    timeout_seconds: float = 6.0,
    session_factory: Any = None,
) -> dict[str, Any] | None:
    """Read public GET /api/status metadata (never sends credentials)."""
    try:
        status, _headers, payload, _preview = await http_request_json(
            "GET",
            f"{root}/api/status",
            allow_http=allow_http,
            proxy_url=proxy_url,
            timeout_seconds=timeout_seconds,
            session_factory=session_factory,
        )
    except QuotaHttpError:
        return None
    if status != 200 or not isinstance(payload, dict) or payload.get("success") is not True:
        return None
    data = payload.get("data")
    return data if isinstance(data, dict) else None


async def _fetch_last_consumption(
    root: str,
    *,
    token: str,
    user_id: str,
    user_id_header: str,
    per_unit: float,
    display_type: str = "USD",
    display_multiplier: float = 1.0,
    allow_http: bool,
    proxy_url: str,
    timeout_seconds: float,
    session_factory: Any,
) -> tuple[str, str, float | None, float | None, str]:
    """Read newest consumption log (type=2); returns ``(used_at, model, cost, raw_quota, error)``."""
    headers = {"Authorization": f"Bearer {token}"}
    if user_id:
        headers[_safe_user_id_header(user_id_header)] = user_id
    url = f"{root}/api/log/self?p=1&page_size=1&type=2"
    secrets = (token,)

    last_exc: QuotaHttpError | None = None
    status, payload, preview = 0, None, ""
    for attempt in range(2):
        try:
            status, _resp_headers, payload, preview = await http_request_json(
                "GET",
                url,
                headers=headers,
                allow_http=allow_http,
                proxy_url=proxy_url,
                timeout_seconds=min(timeout_seconds, 8.0),
                secrets=secrets,
                session_factory=session_factory,
            )
            last_exc = None
            break
        except QuotaHttpError as exc:
            last_exc = exc
            if attempt == 0 and exc.code in ("network_error", "timeout"):
                await asyncio.sleep(0.3)
                continue
            break

    if last_exc is not None:
        return "", "", None, None, str(last_exc)
    if status < 200 or status >= 300:
        return "", "", None, None, f"HTTP {status}"
    if not isinstance(payload, dict):
        return "", "", None, None, f"日志返回非 JSON：{preview[:60]}"
    if payload.get("success") is not True:
        msg = str(payload.get("message") or "").strip()
        return "", "", None, None, msg or "日志接口返回失败"

    data = payload.get("data")
    items: list[Any] = []
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        items = data["items"]
    elif isinstance(data, list):
        items = data
    if not items or not isinstance(items[0], dict):
        return "", "", None, None, ""

    item = items[0]
    ts = safe_number(item.get("created_at"))
    if ts is None or ts <= 0:
        return "", "", None, None, ""
    if ts > 1e12:
        ts = ts / 1000.0
    used_at = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    model_name = str(item.get("model_name") or "").strip()
    raw_quota = safe_number(item.get("quota"))
    cost = convert_newapi_quota(
        raw_quota,
        per_unit=per_unit,
        display_type=display_type,
        display_multiplier=display_multiplier,
    )
    return used_at, model_name, cost, raw_quota, ""


async def fetch_newapi_topup_info(
    root: str,
    *,
    token: str,
    user_id: str = "",
    user_id_header: str = "New-Api-User",
    allow_http: bool = False,
    proxy_url: str = "",
    timeout_seconds: float = 5.0,
    session_factory: Any = None,
) -> dict[str, Any] | None:
    """Read optional authenticated GET /api/user/topup/info to confirm fiat currency and price."""
    if not token:
        return None
    headers = {"Authorization": f"Bearer {token}"}
    if user_id:
        headers[_safe_user_id_header(user_id_header)] = user_id
    try:
        status, _h, payload, _p = await http_request_json(
            "GET",
            f"{root}/api/user/topup/info",
            headers=headers,
            allow_http=allow_http,
            proxy_url=proxy_url,
            timeout_seconds=min(timeout_seconds, 5.0),
            secrets=(token,),
            session_factory=session_factory,
        )
    except QuotaHttpError:
        return None
    if status != 200 or not isinstance(payload, dict) or payload.get("success") is not True:
        return None
    data = payload.get("data")
    return data if isinstance(data, dict) else None


def _parse_timestamp_iso(raw_ts: Any) -> tuple[float | None, str]:
    num = safe_number(raw_ts)
    if num is not None and num > 0:
        if num > 1e12:
            num = num / 1000.0
        try:
            return num, datetime.fromtimestamp(num, tz=timezone.utc).isoformat()
        except (OSError, OverflowError, ValueError):
            return num, ""
    if isinstance(raw_ts, str) and raw_ts.strip():
        text = raw_ts.strip()
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp(), dt.isoformat()
        except ValueError:
            return None, ""
    return None, ""


async def fetch_newapi_subscriptions(
    root: str,
    *,
    token: str,
    user_id: str = "",
    user_id_header: str = "New-Api-User",
    allow_http: bool = False,
    proxy_url: str = "",
    timeout_seconds: float = 5.0,
    session_factory: Any = None,
) -> list[dict[str, Any]]:
    """Read optional GET /api/subscription/self; silently returns [] when unsupported (e.g. 404)."""
    if not token:
        return []
    headers = {"Authorization": f"Bearer {token}"}
    if user_id:
        headers[_safe_user_id_header(user_id_header)] = user_id
    try:
        status, _h, payload, _p = await http_request_json(
            "GET",
            f"{root}/api/subscription/self",
            headers=headers,
            allow_http=allow_http,
            proxy_url=proxy_url,
            timeout_seconds=min(timeout_seconds, 5.0),
            secrets=(token,),
            session_factory=session_factory,
        )
    except QuotaHttpError:
        return []
    if status != 200 or not isinstance(payload, dict) or payload.get("success") is not True:
        return []
    data = payload.get("data")
    raw_items: list[Any] = []
    if isinstance(data, list):
        raw_items = data
    elif isinstance(data, dict):
        for key in ("subscriptions", "items", "list", "active_subscriptions"):
            if isinstance(data.get(key), list):
                raw_items = data[key]
                break
    return [item for item in raw_items if isinstance(item, dict)]


def _normalize_subscriptions(
    raw_items: list[dict[str, Any]],
    *,
    now_ts: float,
    per_unit: float,
    display_type: str,
    display_multiplier: float,
    effective_fiat_pbu: float | None,
) -> tuple[dict[str, Any], ...]:
    normalized: list[dict[str, Any]] = []
    inactive_states = frozenset({"expired", "cancelled", "canceled", "inactive", "disabled", "revoked", "0", "false"})
    for item in raw_items:
        sub_obj = item.get("subscription") if isinstance(item.get("subscription"), dict) else item
        plan_obj = item.get("plan") if isinstance(item.get("plan"), dict) else {}

        status_raw = sub_obj.get("status")
        if status_raw is not None and str(status_raw).strip().lower() in inactive_states:
            continue
        if sub_obj.get("is_active") is False or sub_obj.get("active") is False:
            continue

        end_ts, end_iso = _parse_timestamp_iso(
            sub_obj.get("end_time") or sub_obj.get("expired_time") or sub_obj.get("expires_at")
        )
        if end_ts is not None and end_ts > 0 and end_ts < now_ts:
            continue

        raw_total = None
        for key in ("amount_total", "total_amount", "total_quota", "quota", "limit"):
            raw_total = safe_number(sub_obj.get(key))
            if raw_total is not None:
                break
        raw_used = None
        for key in ("amount_used", "used_amount", "used_quota", "used"):
            raw_used = safe_number(sub_obj.get(key))
            if raw_used is not None:
                break
        raw_rem = None
        for key in ("amount_remaining", "remaining_quota", "remain_quota", "remaining"):
            raw_rem = safe_number(sub_obj.get(key))
            if raw_rem is not None:
                break
        if raw_rem is None and raw_total is not None:
            raw_rem = max(0.0, raw_total - (raw_used or 0.0))
        if raw_rem is None and raw_total is None:
            continue
        if raw_rem is not None and raw_rem <= 0 and (raw_total is None or raw_total > 0):
            continue

        name = str(
            plan_obj.get("title")
            or plan_obj.get("name")
            or sub_obj.get("plan_name")
            or sub_obj.get("title")
            or sub_obj.get("name")
            or "订阅套餐"
        ).strip()[:80]

        rem_disp = convert_newapi_quota(raw_rem, per_unit=per_unit, display_type=display_type, display_multiplier=display_multiplier)
        used_disp = convert_newapi_quota(raw_used, per_unit=per_unit, display_type=display_type, display_multiplier=display_multiplier)
        total_disp = convert_newapi_quota(raw_total, per_unit=per_unit, display_type=display_type, display_multiplier=display_multiplier)

        fiat_rem = None
        fiat_total = None
        if display_type in ("CNY", "USD"):
            fiat_rem = rem_disp
            fiat_total = total_disp
        elif effective_fiat_pbu is not None and effective_fiat_pbu > 0 and per_unit > 0 and display_type != "TOKENS":
            fiat_rem = max(0.0, (raw_rem / per_unit) * effective_fiat_pbu) if raw_rem is not None else None
            fiat_total = max(0.0, (raw_total / per_unit) * effective_fiat_pbu) if raw_total is not None else None

        normalized.append({
            "name": name,
            "remaining": round(rem_disp, 6) if rem_disp is not None else None,
            "used": round(used_disp, 6) if used_disp is not None else None,
            "total": round(total_disp, 6) if total_disp is not None else None,
            "fiat_remaining": round(fiat_rem, 6) if fiat_rem is not None else None,
            "fiat_total": round(fiat_total, 6) if fiat_total is not None else None,
            "expires_at": end_iso,
        })
    return tuple(normalized[:12])


def _parse_log_other(raw_other: Any) -> dict[str, Any]:
    if isinstance(raw_other, dict):
        return raw_other
    if isinstance(raw_other, str) and raw_other.strip():
        try:
            parsed = json.loads(raw_other)
            if isinstance(parsed, dict):
                return parsed
        except ValueError:
            return {}
    return {}


async def fetch_recent_consumption_logs(
    root: str,
    *,
    token: str,
    user_id: str = "",
    user_id_header: str = "New-Api-User",
    per_unit: float = DEFAULT_NEWAPI_PER_UNIT,
    display_type: str = "USD",
    display_multiplier: float = 1.0,
    symbol: str = "",
    inferred_fiat_currency: str = "",
    effective_fiat_pbu: float | None = None,
    max_pages: int = 5,
    page_size: int = 40,
    start_timestamp: int | None = None,
    allow_http: bool = False,
    proxy_url: str = "",
    timeout_seconds: float = 10.0,
    session_factory: Any = None,
) -> dict[str, Any]:
    """Read-only paginated fetch of NewAPI consumption logs (type=2, bounded <=5 pages / <=200 rows)."""
    clean_root = normalize_station_root(root)
    if not clean_root or not token:
        raise QuotaHttpError("missing_credentials", "缺少站点地址或系统访问令牌")

    pages_cap = max(1, min(int(max_pages), 5))
    size_cap = max(1, min(int(page_size), 50))
    total_cap = min(200, pages_cap * size_cap)

    headers = {"Authorization": f"Bearer {token}"}
    if user_id:
        headers[_safe_user_id_header(user_id_header)] = user_id

    rows: list[dict[str, Any]] = []
    pages_fetched = 0
    truncated = False

    for page in range(1, pages_cap + 1):
        query = f"p={page}&page_size={size_cap}&type=2"
        if start_timestamp is not None and start_timestamp > 0:
            query += f"&start_timestamp={int(start_timestamp)}"
        status, _h, payload, preview = await http_request_json(
            "GET",
            f"{clean_root}/api/log/self?{query}",
            headers=headers,
            allow_http=allow_http,
            proxy_url=proxy_url,
            timeout_seconds=min(timeout_seconds, 8.0),
            secrets=(token,),
            session_factory=session_factory,
        )
        if status < 200 or status >= 300 or not isinstance(payload, dict):
            if page == 1:
                raise QuotaHttpError("http_error", f"上游日志接口返回异常 (HTTP {status})：{preview[:80]}", status=status)
            break
        if payload.get("success") is not True:
            if page == 1:
                msg = redact_quota_text(str(payload.get("message") or "").strip(), (token,))
                raise QuotaHttpError("invalid_response", msg or "上游日志接口返回失败")
            break

        pages_fetched += 1
        data = payload.get("data")
        items: list[Any] = []
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            items = data["items"]
        elif isinstance(data, list):
            items = data
        if not items:
            break

        reached_older = False
        for item in items:
            if not isinstance(item, dict):
                continue
            ts_val, used_at = _parse_timestamp_iso(item.get("created_at"))
            if start_timestamp is not None and start_timestamp > 0 and ts_val is not None and ts_val < start_timestamp:
                reached_older = True
                continue

            other = _parse_log_other(item.get("other"))
            raw_quota = safe_number(item.get("quota")) or 0.0
            cost = convert_newapi_quota(
                raw_quota,
                per_unit=per_unit,
                display_type=display_type,
                display_multiplier=display_multiplier,
            ) or 0.0
            fiat_cost: float | None = None
            if display_type in ("CNY", "USD"):
                fiat_cost = cost
            elif effective_fiat_pbu is not None and effective_fiat_pbu > 0 and per_unit > 0 and display_type != "TOKENS":
                fiat_cost = max(0.0, (raw_quota / per_unit) * effective_fiat_pbu)

            wallet_ded = safe_number(other.get("wallet_quota_deducted"))
            sub_ded = safe_number(other.get("subscription_quota_deducted"))
            raw_source = str(other.get("billing_source") or "").strip().lower()
            if raw_source == "subscription" or other.get("use_subscription") is True or (sub_ded is not None and sub_ded > 0):
                billing_source = "subscription"
            elif wallet_ded == 0 and raw_quota > 0 and "subscription" in str(other).lower():
                billing_source = "subscription"
            else:
                billing_source = "wallet"

            prompt_tokens = int(max(0.0, safe_number(item.get("prompt_tokens")) or 0.0))
            completion_tokens = int(max(0.0, safe_number(item.get("completion_tokens")) or 0.0))
            cache_tokens = int(
                max(
                    0.0,
                    safe_number(other.get("cache_tokens"))
                    or safe_number(other.get("cached_tokens"))
                    or safe_number(other.get("cache_read_tokens"))
                    or 0.0,
                )
            )
            group_name = str(item.get("group") or other.get("group") or "").strip()[:64]
            user_group_ratio = safe_number(other.get("user_group_ratio"))
            base_group_ratio = safe_number(other.get("group_ratio"))
            effective_group_ratio = (
                user_group_ratio
                if (user_group_ratio is not None and user_group_ratio > 0)
                else (base_group_ratio if (base_group_ratio is not None and base_group_ratio > 0) else None)
            )

            rows.append({
                "created_at": used_at,
                "timestamp": int(ts_val) if ts_val is not None else 0,
                "model_name": str(item.get("model_name") or "").strip()[:160],
                "token_name": str(item.get("token_name") or "").strip()[:120],
                "group": group_name,
                "group_ratio": round(effective_group_ratio, 6) if effective_group_ratio is not None else None,
                "billing_source": billing_source,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "cache_tokens": cache_tokens,
                "cost": round(cost, 6),
                "fiat_cost": round(fiat_cost, 6) if fiat_cost is not None else None,
            })
            if len(rows) >= total_cap:
                truncated = True
                break

        if len(rows) >= total_cap or reached_older or len(items) < size_cap:
            if len(items) >= size_cap and page == pages_cap:
                truncated = True
            break

    return {
        "display_type": display_type,
        "symbol": symbol,
        "inferred_fiat_currency": inferred_fiat_currency,
        "fiat_per_base_unit": effective_fiat_pbu,
        "pages_fetched": pages_fetched,
        "truncated": truncated,
        "rows": rows,
        "counted_in_private_companion_budget": False,
    }


async def exchange_newapi_login_for_token(
    base_url: str,
    username: str,
    password: str,
    *,
    user_id_header: str = "New-Api-User",
    allow_http: bool = False,
    proxy_url: str = "",
    timeout_seconds: float = 10.0,
    session_factory: Any = None,
) -> dict[str, Any]:
    """One-time password login -> session cookie -> read/generate access token + numeric UID.

    Passwords are used strictly in memory for this single call and MUST NEVER be persisted.
    """
    root = normalize_station_root(base_url)
    if not root:
        raise QuotaHttpError("invalid_url", "请先填写站点地址")
    if not str(username or "").strip() or not str(password or ""):
        raise QuotaHttpError("missing_credentials", "请填写站点用户名与密码")

    jar = aiohttp.CookieJar(unsafe=True)
    secrets = (password, username)
    status, _headers, login_data, preview = await http_request_json(
        "POST",
        f"{root}/api/user/login",
        json_body={"username": username.strip(), "password": password},
        allow_http=allow_http,
        proxy_url=proxy_url,
        timeout_seconds=timeout_seconds,
        secrets=secrets,
        session_factory=session_factory,
        cookie_jar=jar,
    )
    if status < 200 or status >= 300 or not isinstance(login_data, dict):
        raise QuotaHttpError("login_failed", f"登录请求失败 (HTTP {status})：{preview[:100]}", status=status)
    data_obj = login_data.get("data") if isinstance(login_data.get("data"), dict) else {}
    if data_obj.get("require_2fa") is True:
        raise QuotaHttpError("login_2fa_required", "该站点开启了两步验证 (2FA)，请在浏览器登录后手动复制系统访问令牌")
    if login_data.get("success") is not True:
        msg = redact_quota_text(str(login_data.get("message") or "").strip(), secrets) or "用户名或密码错误"
        raise QuotaHttpError("login_failed", msg)

    uid_num = data_obj.get("id")
    uid_str = str(int(uid_num)) if isinstance(uid_num, (int, float)) and not isinstance(uid_num, bool) and uid_num > 0 else ""

    safe_uid_header = _safe_user_id_header(user_id_header)
    tok_headers: dict[str, str] = {}
    if uid_str:
        tok_headers[safe_uid_header] = uid_str

    # Prefer reading existing token from /api/user/self if exposed; otherwise call /api/user/token.
    token = ""
    regenerated = False
    self_status, _h, self_data, _p = await http_request_json(
        "GET",
        f"{root}/api/user/self",
        headers=tok_headers,
        allow_http=allow_http,
        proxy_url=proxy_url,
        timeout_seconds=timeout_seconds,
        secrets=secrets,
        session_factory=session_factory,
        cookie_jar=jar,
    )
    if self_status == 200 and isinstance(self_data, dict) and self_data.get("success") is True:
        sd = self_data.get("data")
        if isinstance(sd, dict):
            if not uid_str and isinstance(sd.get("id"), int) and sd["id"] > 0:
                uid_str = str(sd["id"])
                tok_headers[safe_uid_header] = uid_str
            existing_tok = str(sd.get("access_token") or "").strip()
            if existing_tok:
                token = existing_tok

    if not token:
        t_status, _th, tok_data, t_preview = await http_request_json(
            "GET",
            f"{root}/api/user/token",
            headers=tok_headers,
            allow_http=allow_http,
            proxy_url=proxy_url,
            timeout_seconds=timeout_seconds,
            secrets=secrets,
            session_factory=session_factory,
            cookie_jar=jar,
        )
        if t_status < 200 or t_status >= 300 or not isinstance(tok_data, dict):
            raise QuotaHttpError("token_exchange_failed", f"换取访问令牌失败 (HTTP {t_status})：{t_preview[:100]}")
        if tok_data.get("success") is not True or not str(tok_data.get("data") or "").strip():
            msg = redact_quota_text(str(tok_data.get("message") or "").strip(), secrets) or "站点未返回访问令牌"
            raise QuotaHttpError("token_exchange_failed", msg)
        token = str(tok_data["data"]).strip()
        regenerated = True

    return {
        "access_token": token,
        "user_id": uid_str,
        "regenerated": regenerated,
    }


def _common_newapi_advanced_fields() -> tuple[FieldSpec, ...]:
    return (
        FieldSpec(
            name="display_type",
            label="显示币种（可选覆盖）",
            kind="select",
            group="advanced",
            default="",
            options=(("", "自动从站点读取"), ("CNY", "人民币 (CNY)"), ("USD", "美元 (USD)"), ("CREDIT", "积分 (CREDIT)"), ("TOKENS", "原始 Token")),
            hint="留空自动读取 /api/status 返回的币种单位。",
        ),
        FieldSpec(
            name="quota_per_unit",
            label="每单位额度换算（可选）",
            kind="number",
            group="advanced",
            placeholder="留空自动读取（常见 500000）",
            hint="为 0 或留空时自动取站点 quota_per_unit，取不到时默认 500000。",
        ),
    )


class _BaseNewApiAdapter(QuotaAdapter):
    """Shared implementation for standard NewAPI/OneAPI and derivative User-ID stations."""

    requires_user_id: bool = False

    async def probe(self, spec: QuotaStationSpec, ctx: AdapterProbeContext) -> QuotaSnapshot:
        started = time.monotonic()
        checked_at = ctx.now_iso()
        root = normalize_station_root(ctx.base_url)
        if not root:
            return QuotaSnapshot(
                station_id=spec.id,
                ok=False,
                error="未配置站点地址",
                error_code="invalid_url",
                repair_hint="请填写站点根地址（例如 https://api.example.com）。",
                checked_at=checked_at,
                latency_ms=ctx.elapsed_ms(started),
            )

        # Step 1: Public /api/status (populates currency, divisor, multiplier, logo even before auth!)
        site_status_found: dict[str, Any] | None = None
        published_logo = ""
        active_root = root

        for candidate_root in _known_alias_roots(root):
            site_status = await fetch_newapi_status(
                candidate_root,
                allow_http=ctx.allow_http,
                proxy_url=ctx.proxy_url,
                timeout_seconds=min(ctx.timeout_seconds, 6.0),
                session_factory=ctx.session_factory,
            )
            if site_status is not None:
                active_root = candidate_root
                site_status_found = site_status
                published_logo = str(site_status.get("logo") or "")
                break

        params = resolve_newapi_display_params(
            site_status_found,
            override_display_type=ctx.display_type,
            override_per_unit=ctx.quota_per_unit,
        )
        display_type = params.display_type
        per_unit = params.per_unit
        display_multiplier = params.display_multiplier
        symbol = params.symbol

        logo_version, logo_error = "", ""
        if ctx.logo_cache is not None:
            logo_version, logo_error = await ctx.logo_cache.refresh(
                spec.id,
                active_root,
                published_logo,
                allow_http=ctx.allow_http,
                proxy_url=ctx.proxy_url,
                session_factory=ctx.session_factory,
            )

        token = ctx.auth_token.strip()
        user_id = ctx.user_id.strip()
        user_id_header = ctx.user_id_header.strip() or "New-Api-User"

        if not token:
            return QuotaSnapshot(
                station_id=spec.id,
                ok=False,
                error="尚未填写用户系统访问令牌",
                error_code="missing_credentials",
                repair_hint="请在站点「个人设置 → 系统访问令牌」复制令牌（不是 sk- 渠道密钥）。",
                display_type=display_type,
                symbol=symbol,
                quota_per_unit=per_unit,
                checked_at=checked_at,
                latency_ms=ctx.elapsed_ms(started),
                logo_version=logo_version,
                logo_error=logo_error,
            )
        if self.requires_user_id and not user_id:
            return QuotaSnapshot(
                station_id=spec.id,
                ok=False,
                error=f"该站类型需要填写用户 ID（{user_id_header}）",
                error_code="requires_user_id",
                repair_hint=f"请在站点个人中心查看数字 UID 并填入「用户 ID」栏。",
                detected_header=user_id_header,
                display_type=display_type,
                symbol=symbol,
                quota_per_unit=per_unit,
                checked_at=checked_at,
                latency_ms=ctx.elapsed_ms(started),
                logo_version=logo_version,
                logo_error=logo_error,
            )

        # Step 2: Run /api/user/self and /api/log/self concurrently & independently.
        req_headers = {"Authorization": f"Bearer {token}"}
        if user_id:
            req_headers[user_id_header] = user_id

        async def _probe_balance():
            last_error: tuple[str, str, str, str] | None = None
            for candidate_root in _known_alias_roots(active_root):
                try:
                    status, _hdrs, payload, preview = await http_request_json(
                        "GET",
                        f"{candidate_root}/api/user/self",
                        headers=req_headers,
                        allow_http=ctx.allow_http,
                        proxy_url=ctx.proxy_url,
                        timeout_seconds=ctx.timeout_seconds,
                        secrets=(token,),
                        session_factory=ctx.session_factory,
                    )
                except QuotaHttpError as exc:
                    last_error = (exc.code, str(exc), "请检查站点连通性、HTTPS 证书或代理配置。", "")
                    continue

                if status < 200 or status >= 300:
                    msg = ""
                    if isinstance(payload, dict):
                        msg = str(payload.get("message") or payload.get("error") or "")
                    last_error = _classify_newapi_auth_error(status, msg, preview)
                    if last_error[0] in ("requires_user_id", "sk_key_rejected", "auth_failed"):
                        return None, last_error
                    continue

                if not isinstance(payload, dict):
                    last_error = ("invalid_response", "余额接口未返回合法 JSON", "请确认站点地址是否为中转站根地址。", "")
                    continue

                if payload.get("success") is not True:
                    msg = str(payload.get("message") or "")
                    last_error = _classify_newapi_auth_error(status, msg, preview)
                    return None, last_error

                data = payload.get("data")
                if not isinstance(data, dict):
                    last_error = ("invalid_response", "余额响应缺少 data 字段", "站点返回格式异常，可尝试切换为自定义脚本。", "")
                    continue
                return data, None

            return None, last_error or ("network_error", "无法连接中转站余额接口", "请检查网络或代理设置。", "")

        balance_task = asyncio.create_task(_probe_balance())
        log_task = asyncio.create_task(
            _fetch_last_consumption(
                active_root,
                token=token,
                user_id=user_id,
                user_id_header=user_id_header,
                per_unit=per_unit,
                display_type=display_type,
                display_multiplier=display_multiplier,
                allow_http=ctx.allow_http,
                proxy_url=ctx.proxy_url,
                timeout_seconds=ctx.timeout_seconds,
                session_factory=ctx.session_factory,
            )
        )
        sub_task = asyncio.create_task(
            fetch_newapi_subscriptions(
                active_root,
                token=token,
                user_id=user_id,
                user_id_header=user_id_header,
                allow_http=ctx.allow_http,
                proxy_url=ctx.proxy_url,
                timeout_seconds=min(ctx.timeout_seconds, 5.0),
                session_factory=ctx.session_factory,
            )
        )

        async def _noop_topup() -> dict[str, Any] | None:
            return None

        topup_task = asyncio.create_task(
            fetch_newapi_topup_info(
                active_root,
                token=token,
                user_id=user_id,
                user_id_header=user_id_header,
                allow_http=ctx.allow_http,
                proxy_url=ctx.proxy_url,
                timeout_seconds=min(ctx.timeout_seconds, 5.0),
                session_factory=ctx.session_factory,
            )
            if display_type == "CREDIT"
            else _noop_topup()
        )
        (
            (user_data, bal_err),
            (last_at, last_model, last_cost, last_raw_quota, last_err),
            raw_subs,
            topup_info,
        ) = await asyncio.gather(
            balance_task,
            log_task,
            sub_task,
            topup_task,
        )

        server_address = str((site_status_found or {}).get("server_address") or "").strip()

        if bal_err is not None or user_data is None:
            err_code, err_msg, repair_hint, detected_header = bal_err or (
                "invalid_response",
                "余额读取失败",
                "",
                "",
            )
            return QuotaSnapshot(
                station_id=spec.id,
                ok=False,
                error=err_msg,
                error_code=err_code,
                repair_hint=repair_hint,
                detected_header=detected_header,
                display_type=display_type,
                symbol=symbol,
                quota_per_unit=per_unit,
                server_address=server_address,
                checked_at=checked_at,
                latency_ms=ctx.elapsed_ms(started),
                last_used_at=last_at,
                last_used_model=last_model,
                last_used_cost=last_cost,
                last_used_error=last_err,
                logo_version=logo_version,
                logo_error=logo_error,
            )

        quota_raw = safe_number(user_data.get("quota"))
        used_raw = safe_number(user_data.get("used_quota"))
        if quota_raw is None:
            return QuotaSnapshot(
                station_id=spec.id,
                ok=False,
                error="站点返回缺少 quota 数值",
                error_code="invalid_response",
                repair_hint="该站点响应字段与标准 NewAPI 不同，可切换「自定义脚本」模式提取。",
                display_type=display_type,
                symbol=symbol,
                quota_per_unit=per_unit,
                server_address=server_address,
                checked_at=checked_at,
                latency_ms=ctx.elapsed_ms(started),
                last_used_at=last_at,
                last_used_model=last_model,
                last_used_cost=last_cost,
                last_used_error=last_err,
                logo_version=logo_version,
                logo_error=logo_error,
            )

        remaining = convert_newapi_quota(
            quota_raw,
            per_unit=per_unit,
            display_type=display_type,
            display_multiplier=display_multiplier,
        )
        assert remaining is not None
        used = convert_newapi_quota(
            used_raw,
            per_unit=per_unit,
            display_type=display_type,
            display_multiplier=display_multiplier,
        )
        total = (remaining + used) if used is not None else None

        inferred_fiat_cur, site_recharge_price, effective_fiat_pbu = infer_newapi_fiat_conversion(
            params,
            site_status_found,
            topup_info,
            override_fiat_currency=ctx.fiat_currency or spec.fiat_currency,
            override_fiat_per_base_unit=(
                ctx.fiat_per_base_unit if ctx.fiat_per_base_unit is not None else spec.fiat_per_base_unit
            ),
        )

        fiat_remaining: float | None = None
        fiat_used: float | None = None
        fiat_total: float | None = None
        fiat_last_cost: float | None = None
        if display_type in ("CNY", "USD"):
            fiat_remaining = remaining
            fiat_used = used
            fiat_total = total
            fiat_last_cost = last_cost
        elif (
            display_type == "CREDIT"
            and inferred_fiat_cur in ("CNY", "USD")
            and effective_fiat_pbu is not None
            and effective_fiat_pbu > 0
            and per_unit > 0
        ):
            fiat_remaining = max(0.0, (quota_raw / per_unit) * effective_fiat_pbu)
            fiat_used = max(0.0, (used_raw / per_unit) * effective_fiat_pbu) if used_raw is not None else None
            fiat_total = (fiat_remaining + fiat_used) if fiat_used is not None else None
            fiat_last_cost = (
                max(0.0, (last_raw_quota / per_unit) * effective_fiat_pbu) if last_raw_quota is not None else None
            )

        now_dt = ctx.now()
        if now_dt.tzinfo is None:
            now_dt = now_dt.replace(tzinfo=timezone.utc)
        subscriptions = _normalize_subscriptions(
            raw_subs,
            now_ts=now_dt.timestamp(),
            per_unit=per_unit,
            display_type=display_type,
            display_multiplier=display_multiplier,
            effective_fiat_pbu=effective_fiat_pbu,
        )

        account_name = str(user_data.get("display_name") or user_data.get("username") or "").strip()
        group_name = str(user_data.get("group") or "").strip()
        extra = _format_newapi_custom_extra(
            params,
            quota_raw,
            inferred_fiat_currency=inferred_fiat_cur,
            effective_fiat_pbu=effective_fiat_pbu,
        )

        return QuotaSnapshot(
            station_id=spec.id,
            ok=True,
            remaining=round(remaining, 6),
            used=round(used, 6) if used is not None else None,
            total=round(total, 6) if total is not None else None,
            display_type=display_type,
            symbol=symbol,
            quota_per_unit=per_unit,
            custom_exchange_rate=params.custom_rate if display_type == "CREDIT" else None,
            site_recharge_price=site_recharge_price,
            inferred_fiat_currency=inferred_fiat_cur,
            fiat_remaining=round(fiat_remaining, 6) if fiat_remaining is not None else None,
            fiat_used=round(fiat_used, 6) if fiat_used is not None else None,
            fiat_total=round(fiat_total, 6) if fiat_total is not None else None,
            fiat_last_used_cost=round(fiat_last_cost, 6) if fiat_last_cost is not None else None,
            subscriptions=subscriptions,
            server_address=server_address,
            account_name=account_name,
            group_name=group_name,
            extra=extra,
            checked_at=checked_at,
            latency_ms=ctx.elapsed_ms(started),
            last_used_at=last_at,
            last_used_model=last_model,
            last_used_cost=round(last_cost, 6) if last_cost is not None else None,
            last_used_error=last_err,
            logo_version=logo_version,
            logo_error=logo_error,
        )


class NewApiAdapter(_BaseNewApiAdapter):
    requires_user_id = False

    @property
    def descriptor(self) -> AdapterDescriptor:
        return AdapterDescriptor(
            id="newapi",
            name="New API / One API 标准中转站",
            short_name="NewAPI / OneAPI",
            category="relay",
            summary="支持 One API、New API、One Hub、Done Hub、VoAPI 等标准中转站，自动读取币种、换算比例、头像与最近消费。",
            auth_guide="请登录站点 → 个人设置 → 复制「系统访问令牌（Access Token）」。注意：不要填模型调用用的 sk- 密钥，也不要频繁访问 /api/user/token 以免重置旧令牌。",
            capabilities=AdapterCapabilities(
                supports_provider_reuse=False,
                supports_last_usage=True,
                supports_logo=True,
                supports_group_plan=True,
                supports_one_time_login=True,
                supports_custom_divisor=True,
            ),
            fields=(
                FieldSpec(
                    name="auth_token",
                    label="系统访问令牌（Access Token）",
                    kind="password",
                    required=True,
                    group="primary",
                    placeholder="填写个人设置里的系统访问令牌（不是 sk- 密钥）",
                    hint="保存后留空表示保持原有令牌；也可点下方「账号密码一次性获取」自动换取（不保存密码）。",
                ),
                FieldSpec(
                    name="user_id",
                    label="用户 ID（可选）",
                    kind="text",
                    required=False,
                    group="advanced",
                    placeholder="如站点报错缺少 New-Api-User 再填写",
                    hint="纯数字 UID；标准站通常可留空。",
                ),
                *_common_newapi_advanced_fields(),
            ),
            default_user_id_header="New-Api-User",
            default_quota_per_unit=DEFAULT_NEWAPI_PER_UNIT,
            order=10,
        )


class NewApiUserIdAdapter(_BaseNewApiAdapter):
    requires_user_id = True

    @property
    def descriptor(self) -> AdapterDescriptor:
        return AdapterDescriptor(
            id="newapi_user_id",
            name="New API 衍生站（需用户 ID / 神人站 / Veloera / RixAPI）",
            short_name="NewAPI 衍生站",
            category="relay",
            summary="适配要求携带 New-Api-User / Rix-Api-User / Veloera-User 请求头的中转站（俗称神人站或新版衍生站）。",
            auth_guide="登录站点个人中心查看纯数字「用户 ID」，并复制「系统访问令牌（Access Token / nap_）」。如未开启 2FA，也可用一次性登录自动获取令牌和用户 ID。",
            capabilities=AdapterCapabilities(
                supports_provider_reuse=False,
                supports_last_usage=True,
                supports_logo=True,
                supports_group_plan=True,
                supports_one_time_login=True,
                supports_custom_divisor=True,
            ),
            fields=(
                FieldSpec(
                    name="auth_token",
                    label="系统访问令牌（Access Token）",
                    kind="password",
                    required=True,
                    group="primary",
                    placeholder="填写系统访问令牌（如 nap_ 开头，非 sk- 密钥）",
                    hint="留空保留已保存令牌；支持下方一次性登录换取（密码绝不落盘）。",
                ),
                FieldSpec(
                    name="user_id",
                    label="站点用户 ID（必填）",
                    kind="text",
                    required=True,
                    group="primary",
                    placeholder="例如 12345（站点个人中心数字 UID）",
                    hint="随令牌一起通过下方请求头发送，缺失会报 New-Api-User header not provided。",
                ),
                FieldSpec(
                    name="user_id_header",
                    label="用户 ID 请求头名称",
                    kind="select",
                    required=False,
                    group="advanced",
                    default="New-Api-User",
                    options=(
                        ("New-Api-User", "New-Api-User（新版 NewAPI / 神人站）"),
                        ("Rix-Api-User", "Rix-Api-User（RixAPI）"),
                        ("Veloera-User", "Veloera-User（Veloera）"),
                    ),
                    hint="通常自动识别，绝大多数衍生站为 New-Api-User。",
                ),
                *_common_newapi_advanced_fields(),
            ),
            default_user_id_header="New-Api-User",
            default_quota_per_unit=DEFAULT_NEWAPI_PER_UNIT,
            order=15,
        )
