"""Zero-extra-config official API adapters (DeepSeek, OpenRouter, SiliconFlow, Moonshot).

All four natively reuse the API Key & proxy already configured on an AstrBot provider,
requiring zero duplicate credentials when imported from AstrBot.
"""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlsplit

from companion_v2.model_gateway.quota import QuotaSnapshot, QuotaStationSpec

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


def _api_key_field(placeholder: str = "留空则直接复用绑定 AstrBot 供应商的 API Key") -> FieldSpec:
    return FieldSpec(
        name="auth_token",
        label="API Key",
        kind="password",
        required=False,
        group="primary",
        placeholder=placeholder,
        hint="若选择「复用 AstrBot 供应商」，无需再次填写密钥；手动模式请填写官方 API Key。",
    )


def _missing_key_snapshot(spec: QuotaStationSpec, ctx: AdapterProbeContext, started: float, display_type: str) -> QuotaSnapshot:
    return QuotaSnapshot(
        station_id=spec.id,
        ok=False,
        error="未读取到可用 API Key",
        error_code="missing_credentials",
        repair_hint="请绑定一个已启用的 AstrBot 供应商，或在手动模式下填写官方 API Key。",
        display_type=display_type,
        checked_at=ctx.now_iso(),
        latency_ms=ctx.elapsed_ms(started),
    )


_OFFICIAL_LOGO_HINTS: dict[str, tuple[tuple[str, ...], str, tuple[str, ...]]] = {
    "deepseek": (
        ("deepseek.com",),
        "https://fe-static.deepseek.com/chat/icon-180.png",
        ("https://www.deepseek.com",),
    ),
    "openrouter": (
        ("openrouter.ai",),
        "",
        ("https://openrouter.ai",),
    ),
    "siliconflow": (
        ("siliconflow.cn", "siliconflow.com"),
        "",
        ("https://www.siliconflow.cn", "https://siliconflow.cn"),
    ),
    "moonshot": (
        ("moonshot.cn", "moonshot.ai"),
        "",
        ("https://www.moonshot.cn", "https://platform.moonshot.cn"),
    ),
}


def _official_logo_hints(adapter_id: str, root: str) -> tuple[str, tuple[str, ...]]:
    entry = _OFFICIAL_LOGO_HINTS.get(adapter_id)
    if entry is None:
        return "", ()
    domains, published_logo, extra_roots = entry
    hostname = (urlsplit(root).hostname or "").strip().lower()
    if any(hostname == dom or hostname.endswith("." + dom) for dom in domains):
        return published_logo, extra_roots
    return "", ()


async def _fetch_logo_if_enabled(
    spec: QuotaStationSpec,
    root: str,
    ctx: AdapterProbeContext,
    *,
    adapter_id: str = "",
) -> tuple[str, str]:
    if ctx.logo_cache is None or not root:
        return "", ""
    published_logo, extra_roots = _official_logo_hints(adapter_id or spec.adapter_id, root)
    return await ctx.logo_cache.refresh(
        spec.id,
        root,
        published_logo,
        extra_roots=extra_roots,
        allow_http=ctx.allow_http,
        proxy_url=ctx.proxy_url,
        session_factory=ctx.session_factory,
    )


class DeepSeekAdapter(QuotaAdapter):
    @property
    def descriptor(self) -> AdapterDescriptor:
        return AdapterDescriptor(
            id="deepseek",
            name="DeepSeek 官方",
            short_name="DeepSeek",
            category="official",
            summary="直连 DeepSeek 官方余额接口（GET /user/balance），自动区分充值与赠送余额，支持一键复用 AstrBot 供应商 Key。",
            auth_guide="从 AstrBot 导入 DeepSeek 供应商后无需额外填密钥，点击测试即可直接出数。",
            capabilities=AdapterCapabilities(
                supports_provider_reuse=True,
                supports_logo=True,
            ),
            fields=(_api_key_field(),),
            default_display_type="CNY",
            order=20,
        )

    async def probe(self, spec: QuotaStationSpec, ctx: AdapterProbeContext) -> QuotaSnapshot:
        started = time.monotonic()
        checked_at = ctx.now_iso()
        root = normalize_station_root(ctx.base_url or "https://api.deepseek.com")
        token = ctx.effective_token()
        logo_ver, logo_err = await _fetch_logo_if_enabled(spec, root, ctx)
        if not token:
            snap = _missing_key_snapshot(spec, ctx, started, "CNY")
            return QuotaSnapshot(**{**snap.to_dict(), "logo_version": logo_ver, "logo_error": logo_err})

        try:
            status, _hdrs, payload, preview = await http_request_json(
                "GET",
                f"{root}/user/balance",
                headers={"Authorization": f"Bearer {token}"},
                allow_http=ctx.allow_http,
                proxy_url=ctx.proxy_url,
                timeout_seconds=ctx.timeout_seconds,
                secrets=(token,),
                session_factory=ctx.session_factory,
            )
        except QuotaHttpError as exc:
            return QuotaSnapshot(
                station_id=spec.id,
                ok=False,
                error=str(exc),
                error_code=exc.code,
                repair_hint="请检查 DeepSeek 网络连通性或代理配置。",
                display_type="CNY",
                checked_at=checked_at,
                latency_ms=ctx.elapsed_ms(started),
                logo_version=logo_ver,
                logo_error=logo_err,
            )

        if status in (401, 403):
            return QuotaSnapshot(
                station_id=spec.id,
                ok=False,
                error=f"DeepSeek API Key 鉴权失败 (HTTP {status})",
                error_code="auth_failed",
                repair_hint="请核对 DeepSeek 官方 API Key 是否有效。",
                display_type="CNY",
                checked_at=checked_at,
                latency_ms=ctx.elapsed_ms(started),
                logo_version=logo_ver,
                logo_error=logo_err,
            )
        if status < 200 or status >= 300 or not isinstance(payload, dict):
            return QuotaSnapshot(
                station_id=spec.id,
                ok=False,
                error=f"DeepSeek 接口异常 (HTTP {status})：{preview[:80]}",
                error_code="http_error" if status >= 400 else "invalid_response",
                display_type="CNY",
                checked_at=checked_at,
                latency_ms=ctx.elapsed_ms(started),
                logo_version=logo_ver,
                logo_error=logo_err,
            )

        infos = payload.get("balance_infos")
        if not isinstance(infos, list) or not infos:
            return QuotaSnapshot(
                station_id=spec.id,
                ok=False,
                error="DeepSeek 响应缺少 balance_infos 字段",
                error_code="invalid_response",
                display_type="CNY",
                checked_at=checked_at,
                latency_ms=ctx.elapsed_ms(started),
                logo_version=logo_ver,
                logo_error=logo_err,
            )

        # Prefer CNY entry when both CNY and USD exist, unless user explicitly forced display_type.
        want_cur = ctx.display_type if ctx.display_type in ("CNY", "USD") else "CNY"
        chosen = next(
            (item for item in infos if isinstance(item, dict) and str(item.get("currency") or "").upper() == want_cur),
            None,
        )
        if chosen is None:
            chosen = next((item for item in infos if isinstance(item, dict)), None)
        if chosen is None:
            return QuotaSnapshot(
                station_id=spec.id,
                ok=False,
                error="DeepSeek 余额条目无效",
                error_code="invalid_response",
                display_type="CNY",
                checked_at=checked_at,
                latency_ms=ctx.elapsed_ms(started),
                logo_version=logo_ver,
                logo_error=logo_err,
            )

        currency = str(chosen.get("currency") or "CNY").upper()
        if currency not in ("CNY", "USD"):
            currency = "CNY"
        total_bal = safe_number(chosen.get("total_balance"))
        granted = safe_number(chosen.get("granted_balance"))
        topped = safe_number(chosen.get("topped_up_balance"))
        if total_bal is None:
            return QuotaSnapshot(
                station_id=spec.id,
                ok=False,
                error="DeepSeek 余额数值无效",
                error_code="invalid_response",
                display_type=currency,
                checked_at=checked_at,
                latency_ms=ctx.elapsed_ms(started),
                logo_version=logo_ver,
                logo_error=logo_err,
            )

        sym = "¥" if currency == "CNY" else "$"
        parts: list[str] = []
        if topped is not None:
            parts.append(f"充值 {sym}{topped:.2f}")
        if granted is not None and granted > 0:
            parts.append(f"赠送 {sym}{granted:.2f}")
        if payload.get("is_available") is False:
            parts.append("账户已欠费暂停")

        return QuotaSnapshot(
            station_id=spec.id,
            ok=True,
            remaining=round(max(0.0, total_bal), 6),
            display_type=currency,
            group_name="官方直连",
            extra=" · ".join(parts),
            checked_at=checked_at,
            latency_ms=ctx.elapsed_ms(started),
            logo_version=logo_ver,
            logo_error=logo_err,
        )


class OpenRouterAdapter(QuotaAdapter):
    @property
    def descriptor(self) -> AdapterDescriptor:
        return AdapterDescriptor(
            id="openrouter",
            name="OpenRouter 官方",
            short_name="OpenRouter",
            category="official",
            summary="优先查询账号总余额（/api/v1/credits），若权限不足自动降级查询当前 Key 限额（/api/v1/auth/key）。",
            auth_guide="从 AstrBot 导入 OpenRouter 供应商后无需额外填密钥；如使用受限 Key，会自动降级显示单 Key 用量与限额。",
            capabilities=AdapterCapabilities(
                supports_provider_reuse=True,
                supports_logo=True,
                supports_group_plan=True,
            ),
            fields=(_api_key_field(),),
            default_display_type="USD",
            order=25,
        )

    async def probe(self, spec: QuotaStationSpec, ctx: AdapterProbeContext) -> QuotaSnapshot:
        started = time.monotonic()
        checked_at = ctx.now_iso()
        root = normalize_station_root(ctx.base_url or "https://openrouter.ai")
        token = ctx.effective_token()
        logo_ver, logo_err = await _fetch_logo_if_enabled(spec, root, ctx)
        if not token:
            snap = _missing_key_snapshot(spec, ctx, started, "USD")
            return QuotaSnapshot(**{**snap.to_dict(), "logo_version": logo_ver, "logo_error": logo_err})

        headers = {"Authorization": f"Bearer {token}"}

        # 1) Try account credits endpoint first.
        try:
            c_status, _h, c_payload, _prev = await http_request_json(
                "GET",
                f"{root}/api/v1/credits",
                headers=headers,
                allow_http=ctx.allow_http,
                proxy_url=ctx.proxy_url,
                timeout_seconds=ctx.timeout_seconds,
                secrets=(token,),
                session_factory=ctx.session_factory,
            )
        except QuotaHttpError as exc:
            return QuotaSnapshot(
                station_id=spec.id,
                ok=False,
                error=str(exc),
                error_code=exc.code,
                repair_hint="请检查 OpenRouter 网络连通性或代理配置。",
                display_type="USD",
                checked_at=checked_at,
                latency_ms=ctx.elapsed_ms(started),
                logo_version=logo_ver,
                logo_error=logo_err,
            )

        if c_status == 200 and isinstance(c_payload, dict) and isinstance(c_payload.get("data"), dict):
            data = c_payload["data"]
            total_credits = safe_number(data.get("total_credits"))
            total_usage = safe_number(data.get("total_usage"))
            if total_credits is not None and total_usage is not None:
                rem = max(0.0, total_credits - total_usage)
                return QuotaSnapshot(
                    station_id=spec.id,
                    ok=True,
                    remaining=round(rem, 6),
                    used=round(max(0.0, total_usage), 6),
                    total=round(max(0.0, total_credits), 6),
                    display_type="USD",
                    group_name="账号余额",
                    extra=f"累计充值 ${total_credits:.2f} · 已用 ${total_usage:.4f}",
                    checked_at=checked_at,
                    latency_ms=ctx.elapsed_ms(started),
                    logo_version=logo_ver,
                    logo_error=logo_err,
                )

        # 2) Fallback to /api/v1/auth/key (works for any standard inference key).
        try:
            k_status, _h, k_payload, k_prev = await http_request_json(
                "GET",
                f"{root}/api/v1/auth/key",
                headers=headers,
                allow_http=ctx.allow_http,
                proxy_url=ctx.proxy_url,
                timeout_seconds=ctx.timeout_seconds,
                secrets=(token,),
                session_factory=ctx.session_factory,
            )
        except QuotaHttpError as exc:
            return QuotaSnapshot(
                station_id=spec.id,
                ok=False,
                error=str(exc),
                error_code=exc.code,
                display_type="USD",
                checked_at=checked_at,
                latency_ms=ctx.elapsed_ms(started),
                logo_version=logo_ver,
                logo_error=logo_err,
            )

        if k_status in (401, 403):
            return QuotaSnapshot(
                station_id=spec.id,
                ok=False,
                error=f"OpenRouter API Key 鉴权失败 (HTTP {k_status})",
                error_code="auth_failed",
                repair_hint="请核对 OpenRouter API Key 是否有效。",
                display_type="USD",
                checked_at=checked_at,
                latency_ms=ctx.elapsed_ms(started),
                logo_version=logo_ver,
                logo_error=logo_err,
            )
        if k_status < 200 or k_status >= 300 or not isinstance(k_payload, dict) or not isinstance(k_payload.get("data"), dict):
            return QuotaSnapshot(
                station_id=spec.id,
                ok=False,
                error=f"OpenRouter 接口返回异常 (HTTP {k_status})：{k_prev[:80]}",
                error_code="http_error" if k_status >= 400 else "invalid_response",
                display_type="USD",
                checked_at=checked_at,
                latency_ms=ctx.elapsed_ms(started),
                logo_version=logo_ver,
                logo_error=logo_err,
            )

        kd = k_payload["data"]
        limit = safe_number(kd.get("limit"))
        limit_rem = safe_number(kd.get("limit_remaining"))
        usage = safe_number(kd.get("usage"))
        if limit_rem is None and limit is not None and usage is not None:
            limit_rem = max(0.0, limit - usage)
        is_free = kd.get("is_free_tier") is True
        label = str(kd.get("label") or "").strip()
        extra_parts: list[str] = []
        if limit is None:
            extra_parts.append("当前 Key 未设消费上限")
        if usage is not None:
            extra_parts.append(f"Key 已用 ${usage:.4f}")
        if is_free:
            extra_parts.append("Free Tier")

        return QuotaSnapshot(
            station_id=spec.id,
            ok=True,
            remaining=round(max(0.0, limit_rem), 6) if limit_rem is not None else None,
            used=round(max(0.0, usage), 6) if usage is not None else None,
            total=round(max(0.0, limit), 6) if limit is not None else None,
            display_type="USD",
            account_name=label,
            group_name="Free Tier" if is_free else "单 Key 限额",
            extra=" · ".join(extra_parts),
            is_key_scoped=True,
            checked_at=checked_at,
            latency_ms=ctx.elapsed_ms(started),
            logo_version=logo_ver,
            logo_error=logo_err,
        )


class SiliconFlowAdapter(QuotaAdapter):
    @property
    def descriptor(self) -> AdapterDescriptor:
        return AdapterDescriptor(
            id="siliconflow",
            name="硅基流动 SiliconFlow",
            short_name="硅基流动",
            category="official",
            summary="直连硅基流动用户信息接口（GET /v1/user/info），同时展示总余额、充值余额与赠送余额。",
            auth_guide="从 AstrBot 导入硅基流动供应商后无需额外填密钥，测试即可出数。",
            capabilities=AdapterCapabilities(
                supports_provider_reuse=True,
                supports_logo=True,
            ),
            fields=(_api_key_field(),),
            default_display_type="CNY",
            order=30,
        )

    async def probe(self, spec: QuotaStationSpec, ctx: AdapterProbeContext) -> QuotaSnapshot:
        started = time.monotonic()
        checked_at = ctx.now_iso()
        root = normalize_station_root(ctx.base_url or "https://api.siliconflow.cn")
        token = ctx.effective_token()
        logo_ver, logo_err = await _fetch_logo_if_enabled(spec, root, ctx)
        if not token:
            snap = _missing_key_snapshot(spec, ctx, started, "CNY")
            return QuotaSnapshot(**{**snap.to_dict(), "logo_version": logo_ver, "logo_error": logo_err})

        try:
            status, _h, payload, preview = await http_request_json(
                "GET",
                f"{root}/v1/user/info",
                headers={"Authorization": f"Bearer {token}"},
                allow_http=ctx.allow_http,
                proxy_url=ctx.proxy_url,
                timeout_seconds=ctx.timeout_seconds,
                secrets=(token,),
                session_factory=ctx.session_factory,
            )
        except QuotaHttpError as exc:
            return QuotaSnapshot(
                station_id=spec.id,
                ok=False,
                error=str(exc),
                error_code=exc.code,
                display_type="CNY",
                checked_at=checked_at,
                latency_ms=ctx.elapsed_ms(started),
                logo_version=logo_ver,
                logo_error=logo_err,
            )

        if status in (401, 403):
            return QuotaSnapshot(
                station_id=spec.id,
                ok=False,
                error=f"硅基流动 API Key 鉴权失败 (HTTP {status})",
                error_code="auth_failed",
                repair_hint="请检查硅基流动 API Key 是否正确。",
                display_type="CNY",
                checked_at=checked_at,
                latency_ms=ctx.elapsed_ms(started),
                logo_version=logo_ver,
                logo_error=logo_err,
            )
        if status < 200 or status >= 300 or not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
            return QuotaSnapshot(
                station_id=spec.id,
                ok=False,
                error=f"硅基流动接口异常 (HTTP {status})：{preview[:80]}",
                error_code="http_error" if status >= 400 else "invalid_response",
                display_type="CNY",
                checked_at=checked_at,
                latency_ms=ctx.elapsed_ms(started),
                logo_version=logo_ver,
                logo_error=logo_err,
            )

        data = payload["data"]
        total_bal = safe_number(data.get("totalBalance"))
        charge_bal = safe_number(data.get("chargeBalance"))
        gift_bal = safe_number(data.get("balance"))
        if total_bal is None:
            if charge_bal is not None or gift_bal is not None:
                total_bal = (charge_bal or 0.0) + (gift_bal or 0.0)
            else:
                return QuotaSnapshot(
                    station_id=spec.id,
                    ok=False,
                    error="硅基流动响应缺少余额字段",
                    error_code="invalid_response",
                    display_type="CNY",
                    checked_at=checked_at,
                    latency_ms=ctx.elapsed_ms(started),
                    logo_version=logo_ver,
                    logo_error=logo_err,
                )

        parts: list[str] = []
        if charge_bal is not None:
            parts.append(f"充值 ¥{charge_bal:.2f}")
        if gift_bal is not None and gift_bal > 0:
            parts.append(f"赠送 ¥{gift_bal:.2f}")
        account_name = str(data.get("name") or data.get("email") or "").strip()

        return QuotaSnapshot(
            station_id=spec.id,
            ok=True,
            remaining=round(max(0.0, total_bal), 6),
            display_type="CNY",
            account_name=account_name,
            group_name="官方直连",
            extra=" · ".join(parts),
            checked_at=checked_at,
            latency_ms=ctx.elapsed_ms(started),
            logo_version=logo_ver,
            logo_error=logo_err,
        )


class MoonshotAdapter(QuotaAdapter):
    @property
    def descriptor(self) -> AdapterDescriptor:
        return AdapterDescriptor(
            id="moonshot",
            name="Moonshot / Kimi 官方",
            short_name="Moonshot",
            category="official",
            summary="直连 Moonshot 官方余额接口（GET /v1/users/me/balance），展示可用余额、现金余额与代金券。",
            auth_guide="从 AstrBot 导入 Moonshot 供应商后无需额外填密钥，测试即可出数。",
            capabilities=AdapterCapabilities(
                supports_provider_reuse=True,
                supports_logo=True,
            ),
            fields=(_api_key_field(),),
            default_display_type="CNY",
            order=35,
        )

    async def probe(self, spec: QuotaStationSpec, ctx: AdapterProbeContext) -> QuotaSnapshot:
        started = time.monotonic()
        checked_at = ctx.now_iso()
        root = normalize_station_root(ctx.base_url or "https://api.moonshot.cn")
        token = ctx.effective_token()
        logo_ver, logo_err = await _fetch_logo_if_enabled(spec, root, ctx)
        if not token:
            snap = _missing_key_snapshot(spec, ctx, started, "CNY")
            return QuotaSnapshot(**{**snap.to_dict(), "logo_version": logo_ver, "logo_error": logo_err})

        try:
            status, _h, payload, preview = await http_request_json(
                "GET",
                f"{root}/v1/users/me/balance",
                headers={"Authorization": f"Bearer {token}"},
                allow_http=ctx.allow_http,
                proxy_url=ctx.proxy_url,
                timeout_seconds=ctx.timeout_seconds,
                secrets=(token,),
                session_factory=ctx.session_factory,
            )
        except QuotaHttpError as exc:
            return QuotaSnapshot(
                station_id=spec.id,
                ok=False,
                error=str(exc),
                error_code=exc.code,
                display_type="CNY",
                checked_at=checked_at,
                latency_ms=ctx.elapsed_ms(started),
                logo_version=logo_ver,
                logo_error=logo_err,
            )

        if status in (401, 403):
            return QuotaSnapshot(
                station_id=spec.id,
                ok=False,
                error=f"Moonshot API Key 鉴权失败 (HTTP {status})",
                error_code="auth_failed",
                repair_hint="请检查 Moonshot API Key 是否正确。",
                display_type="CNY",
                checked_at=checked_at,
                latency_ms=ctx.elapsed_ms(started),
                logo_version=logo_ver,
                logo_error=logo_err,
            )
        if status < 200 or status >= 300 or not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
            return QuotaSnapshot(
                station_id=spec.id,
                ok=False,
                error=f"Moonshot 接口异常 (HTTP {status})：{preview[:80]}",
                error_code="http_error" if status >= 400 else "invalid_response",
                display_type="CNY",
                checked_at=checked_at,
                latency_ms=ctx.elapsed_ms(started),
                logo_version=logo_ver,
                logo_error=logo_err,
            )

        data = payload["data"]
        avail = safe_number(data.get("available_balance"))
        cash = safe_number(data.get("cash_balance"))
        voucher = safe_number(data.get("voucher_balance"))
        if avail is None:
            return QuotaSnapshot(
                station_id=spec.id,
                ok=False,
                error="Moonshot 响应缺少 available_balance 字段",
                error_code="invalid_response",
                display_type="CNY",
                checked_at=checked_at,
                latency_ms=ctx.elapsed_ms(started),
                logo_version=logo_ver,
                logo_error=logo_err,
            )

        parts: list[str] = []
        if cash is not None:
            parts.append(f"现金 ¥{cash:.2f}")
        if voucher is not None and voucher > 0:
            parts.append(f"代金券 ¥{voucher:.2f}")

        return QuotaSnapshot(
            station_id=spec.id,
            ok=True,
            remaining=round(max(0.0, avail), 6),
            display_type="CNY",
            group_name="官方直连",
            extra=" · ".join(parts),
            checked_at=checked_at,
            latency_ms=ctx.elapsed_ms(started),
            logo_version=logo_ver,
            logo_error=logo_err,
        )
