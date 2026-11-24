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


def _format_newapi_custom_extra(params: NewApiDisplayParams, quota_raw: float) -> str:
    """Build subtle conversion reference metadata for non-fiat CUSTOM currency stations."""
    if params.display_type != "CREDIT":
        return ""
    if params.site_raw_display_type not in ("CUSTOM", "POINTS", "POINT", "CREDIT"):
        return ""
    parts: list[str] = []
    unit_label = params.symbol or "积分"
    if params.custom_rate is not None and params.custom_rate > 0 and params.custom_rate != 1.0:
        rate_txt = str(int(params.custom_rate)) if params.custom_rate.is_integer() else f"{params.custom_rate:g}"
        parts.append(f"站内换算 {rate_txt} {unit_label} = 1 基准单位")
    if params.site_price is not None and params.site_price > 0 and params.per_unit > 0:
        ref_recharge = max(0.0, (float(quota_raw) / float(params.per_unit)) * params.site_price)
        parts.append(f"参考充值约 ¥{ref_recharge:,.2f}")
    return " · ".join(parts)


def _safe_user_id_header(header_name: object) -> str:
    candidate = str(header_name or "").strip()
    if candidate and _HEADER_NAME_RE.match(candidate):
        return candidate
    return "New-Api-User"


def _known_alias_roots(root: str) -> tuple[str, ...]:
    """Preserve existing syuan.org host-alias compatibility from service_balance.py."""
    try:
        parsed = urlsplit(root)
    except ValueError:
        return (root,)
    hostname = (parsed.hostname or "").lower()
    if hostname not in ("api.syuan.org", "ai.syuan.org"):
        return (root,)
    alt_host = "ai.syuan.org" if hostname == "api.syuan.org" else "api.syuan.org"
    netloc = f"{alt_host}:{parsed.port}" if parsed.port is not None else alt_host
    alt_root = parsed._replace(netloc=netloc).geturl().rstrip("/")
    return (root, alt_root)


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
) -> tuple[str, str, float | None, str]:
    """Read newest consumption log (type=2); independent of balance probe with 1x retry."""
    headers = {"Authorization": f"Bearer {token}"}
    if user_id:
        headers[user_id_header or "New-Api-User"] = user_id
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
        return "", "", None, str(last_exc)
    if status < 200 or status >= 300:
        return "", "", None, f"HTTP {status}"
    if not isinstance(payload, dict):
        return "", "", None, f"日志返回非 JSON：{preview[:60]}"
    if payload.get("success") is not True:
        msg = str(payload.get("message") or "").strip()
        return "", "", None, msg or "日志接口返回失败"

    data = payload.get("data")
    items: list[Any] = []
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        items = data["items"]
    elif isinstance(data, list):
        items = data
    if not items or not isinstance(items[0], dict):
        return "", "", None, ""

    item = items[0]
    ts = safe_number(item.get("created_at"))
    if ts is None or ts <= 0:
        return "", "", None, ""
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
    return used_at, model_name, cost, ""


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
        (user_data, bal_err), (last_at, last_model, last_cost, last_err) = await asyncio.gather(
            balance_task,
            log_task,
        )

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
        account_name = str(user_data.get("display_name") or user_data.get("username") or "").strip()
        group_name = str(user_data.get("group") or "").strip()
        extra = _format_newapi_custom_extra(params, quota_raw)

        return QuotaSnapshot(
            station_id=spec.id,
            ok=True,
            remaining=round(remaining, 6),
            used=round(used, 6) if used is not None else None,
            total=round(total, 6) if total is not None else None,
            display_type=display_type,
            symbol=symbol,
            quota_per_unit=per_unit,
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
