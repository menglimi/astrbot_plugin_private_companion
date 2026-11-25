"""Generic relay adapters (Sub2API, OpenAI-compatible billing) and graceful unsupported-official handler."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import time

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


class Sub2ApiAdapter(QuotaAdapter):
    @property
    def descriptor(self) -> AdapterDescriptor:
        return AdapterDescriptor(
            id="sub2api",
            name="Sub2API / 用量直出中转站",
            short_name="Sub2API",
            category="relay",
            summary="通过 GET /v1/usage 直接用 API Key 查询剩余额度与套餐名，支持一键复用 AstrBot 供应商 Key。",
            auth_guide="导入 AstrBot 供应商后可直接复用其 API Key；手动模式填写站点的 API Key 即可。",
            capabilities=AdapterCapabilities(
                supports_provider_reuse=True,
                supports_logo=True,
                supports_group_plan=True,
            ),
            fields=(
                FieldSpec(
                    name="auth_token",
                    label="API Key",
                    kind="password",
                    required=False,
                    group="primary",
                    placeholder="复用 AstrBot 供应商时可留空",
                    hint="通过 Authorization: Bearer 发送到 /v1/usage。",
                ),
            ),
            default_display_type="USD",
            order=40,
        )

    async def probe(self, spec: QuotaStationSpec, ctx: AdapterProbeContext) -> QuotaSnapshot:
        started = time.monotonic()
        checked_at = ctx.now_iso()
        root = normalize_station_root(ctx.base_url)
        token = ctx.effective_token()
        logo_ver, logo_err = "", ""
        if ctx.logo_cache is not None and root:
            logo_ver, logo_err = await ctx.logo_cache.refresh(
                spec.id, root, "", allow_http=ctx.allow_http, proxy_url=ctx.proxy_url, session_factory=ctx.session_factory
            )
        if not root:
            return QuotaSnapshot(
                station_id=spec.id,
                ok=False,
                error="未配置站点地址",
                error_code="invalid_url",
                checked_at=checked_at,
                latency_ms=ctx.elapsed_ms(started),
            )
        if not token:
            return QuotaSnapshot(
                station_id=spec.id,
                ok=False,
                error="未读取到可用 API Key",
                error_code="missing_credentials",
                repair_hint="请复用 AstrBot 供应商或在上方填写 API Key。",
                display_type=ctx.display_type or "USD",
                checked_at=checked_at,
                latency_ms=ctx.elapsed_ms(started),
                logo_version=logo_ver,
                logo_error=logo_err,
            )

        try:
            status, _h, payload, preview = await http_request_json(
                "GET",
                f"{root}/v1/usage",
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
                display_type=ctx.display_type or "USD",
                checked_at=checked_at,
                latency_ms=ctx.elapsed_ms(started),
                logo_version=logo_ver,
                logo_error=logo_err,
            )

        if status in (401, 403):
            return QuotaSnapshot(
                station_id=spec.id,
                ok=False,
                error=f"API Key 鉴权失败 (HTTP {status})",
                error_code="auth_failed",
                repair_hint="请核对 API Key 或确认该站是否支持 /v1/usage 接口。",
                display_type=ctx.display_type or "USD",
                checked_at=checked_at,
                latency_ms=ctx.elapsed_ms(started),
                logo_version=logo_ver,
                logo_error=logo_err,
            )
        if status < 200 or status >= 300 or not isinstance(payload, dict):
            return QuotaSnapshot(
                station_id=spec.id,
                ok=False,
                error=f"/v1/usage 返回异常 (HTTP {status})：{preview[:80]}",
                error_code="http_error" if status >= 400 else "invalid_response",
                display_type=ctx.display_type or "USD",
                checked_at=checked_at,
                latency_ms=ctx.elapsed_ms(started),
                logo_version=logo_ver,
                logo_error=logo_err,
            )

        if payload.get("isValid") is False:
            reason = str(payload.get("invalidMessage") or payload.get("status") or "账户或密钥无效").strip()
            return QuotaSnapshot(
                station_id=spec.id,
                ok=False,
                error=reason,
                error_code="auth_failed",
                display_type=ctx.display_type or "USD",
                checked_at=checked_at,
                latency_ms=ctx.elapsed_ms(started),
                logo_version=logo_ver,
                logo_error=logo_err,
            )

        quota_obj = payload.get("quota") if isinstance(payload.get("quota"), dict) else {}
        rem = safe_number(payload.get("remaining"))
        total = safe_number(quota_obj.get("limit"))
        if total is None:
            total = safe_number(payload.get("total"))
        used = safe_number(quota_obj.get("used"))
        if used is None:
            used = safe_number(payload.get("used"))
        if rem is None and total is not None and used is not None:
            rem = max(0.0, total - used)
        if rem is None:
            return QuotaSnapshot(
                station_id=spec.id,
                ok=False,
                error="/v1/usage 响应缺少 remaining 或 quota.limit/used 字段",
                error_code="invalid_response",
                display_type=ctx.display_type or "USD",
                checked_at=checked_at,
                latency_ms=ctx.elapsed_ms(started),
                logo_version=logo_ver,
                logo_error=logo_err,
            )

        raw_unit = str(ctx.display_type or payload.get("unit") or quota_obj.get("unit") or "USD").strip().upper()
        display_type = raw_unit if raw_unit in ("CNY", "USD", "CREDIT", "TOKENS") else "CREDIT"
        plan_name = str(payload.get("planName") or payload.get("plan_name") or "").strip()

        return QuotaSnapshot(
            station_id=spec.id,
            ok=True,
            remaining=round(max(0.0, rem), 6),
            used=round(max(0.0, used), 6) if used is not None else None,
            total=round(max(0.0, total), 6) if total is not None else None,
            display_type=display_type,
            group_name=plan_name,
            checked_at=checked_at,
            latency_ms=ctx.elapsed_ms(started),
            logo_version=logo_ver,
            logo_error=logo_err,
        )


class OpenAiBillingAdapter(QuotaAdapter):
    @property
    def descriptor(self) -> AdapterDescriptor:
        return AdapterDescriptor(
            id="openai_billing",
            name="OpenAI 兼容单 Key 限额站",
            short_name="单 Key 限额",
            category="relay",
            summary="直接用 sk- 模型 Key 查询 /v1/dashboard/billing/subscription 与 usage，适合旧版 OneAPI 或仅开放单 Key 查额的中转站。",
            auth_guide="可直接复用 AstrBot 供应商的 sk- 密钥！注意：显示的是该 Key 的消费限额与已用量，不一定是整个账号的总余额。",
            capabilities=AdapterCapabilities(
                supports_provider_reuse=True,
                supports_logo=True,
                is_key_scoped=True,
            ),
            fields=(
                FieldSpec(
                    name="auth_token",
                    label="渠道 API Key（sk-...）",
                    kind="password",
                    required=False,
                    group="primary",
                    placeholder="复用 AstrBot 供应商时可留空",
                    hint="直接通过 Bearer 鉴权请求 OpenAI 兼容账单接口。",
                ),
                FieldSpec(
                    name="display_type",
                    label="显示币种",
                    kind="select",
                    group="advanced",
                    default="USD",
                    options=(("USD", "美元 (USD)"), ("CNY", "人民币 (CNY)"), ("CREDIT", "积分 (CREDIT)")),
                ),
            ),
            default_display_type="USD",
            order=45,
        )

    async def probe(self, spec: QuotaStationSpec, ctx: AdapterProbeContext) -> QuotaSnapshot:
        started = time.monotonic()
        checked_at = ctx.now_iso()
        root = normalize_station_root(ctx.base_url)
        token = ctx.effective_token()
        display_type = ctx.display_type or "USD"
        logo_ver, logo_err = "", ""
        if ctx.logo_cache is not None and root:
            logo_ver, logo_err = await ctx.logo_cache.refresh(
                spec.id, root, "", allow_http=ctx.allow_http, proxy_url=ctx.proxy_url, session_factory=ctx.session_factory
            )
        if not root or not token:
            return QuotaSnapshot(
                station_id=spec.id,
                ok=False,
                error="缺少站点地址或 API Key",
                error_code="missing_credentials" if root else "invalid_url",
                display_type=display_type,
                checked_at=checked_at,
                latency_ms=ctx.elapsed_ms(started),
                logo_version=logo_ver,
                logo_error=logo_err,
            )

        headers = {"Authorization": f"Bearer {token}"}

        # 1) Try /v1/dashboard/billing/subscription + /usage
        sub_data = None
        last_err = ""
        last_status = 0
        for sub_path in ("/v1/dashboard/billing/subscription", "/dashboard/billing/subscription"):
            try:
                status, _h, payload, preview = await http_request_json(
                    "GET",
                    f"{root}{sub_path}",
                    headers=headers,
                    allow_http=ctx.allow_http,
                    proxy_url=ctx.proxy_url,
                    timeout_seconds=ctx.timeout_seconds,
                    secrets=(token,),
                    session_factory=ctx.session_factory,
                )
            except QuotaHttpError as exc:
                last_err = str(exc)
                continue
            last_status = status
            if status in (401, 403):
                return QuotaSnapshot(
                    station_id=spec.id,
                    ok=False,
                    error=f"单 Key 账单接口鉴权失败 (HTTP {status})",
                    error_code="auth_failed",
                    repair_hint="该站点可能关闭了单 Key 查额接口，建议改用「NewAPI / OneAPI」并填系统访问令牌。",
                    display_type=display_type,
                    checked_at=checked_at,
                    latency_ms=ctx.elapsed_ms(started),
                    logo_version=logo_ver,
                    logo_error=logo_err,
                )
            if status == 200 and isinstance(payload, dict):
                hard_limit = safe_number(payload.get("hard_limit_usd"))
                if hard_limit is None:
                    hard_limit = safe_number(payload.get("system_hard_limit_usd"))
                if hard_limit is not None:
                    sub_data = (hard_limit, payload, sub_path.startswith("/v1/"))
                    break
            last_err = f"HTTP {status}: {preview[:80]}"

        if sub_data is not None:
            hard_limit, _sub_payload, has_v1 = sub_data
            now_dt = ctx.now()
            if now_dt.tzinfo is None:
                now_dt = now_dt.replace(tzinfo=timezone.utc)
            start_str = (now_dt - timedelta(days=99)).strftime("%Y-%m-%d")
            end_str = (now_dt + timedelta(days=1)).strftime("%Y-%m-%d")
            usage_prefix = "/v1" if has_v1 else ""
            used_usd = 0.0
            try:
                u_status, _uh, u_payload, _uprev = await http_request_json(
                    "GET",
                    f"{root}{usage_prefix}/dashboard/billing/usage?start_date={start_str}&end_date={end_str}",
                    headers=headers,
                    allow_http=ctx.allow_http,
                    proxy_url=ctx.proxy_url,
                    timeout_seconds=ctx.timeout_seconds,
                    secrets=(token,),
                    session_factory=ctx.session_factory,
                )
                if u_status == 200 and isinstance(u_payload, dict):
                    raw_usage = safe_number(u_payload.get("total_usage"))
                    if raw_usage is not None:
                        # OpenAI billing usage is reported in cents (1/100 USD).
                        used_usd = max(0.0, raw_usage / 100.0)
            except QuotaHttpError:
                pass
            remaining = max(0.0, hard_limit - used_usd)
            return QuotaSnapshot(
                station_id=spec.id,
                ok=True,
                remaining=round(remaining, 6),
                used=round(used_usd, 6),
                total=round(hard_limit, 6),
                display_type=display_type,
                group_name="单 Key 限额",
                extra="按该 sk- 密钥限额统计（非账户总余额）",
                is_key_scoped=True,
                checked_at=checked_at,
                latency_ms=ctx.elapsed_ms(started),
                logo_version=logo_ver,
                logo_error=logo_err,
            )

        # 2) Fallback: /dashboard/billing/credit_grants
        try:
            g_status, _gh, g_payload, g_prev = await http_request_json(
                "GET",
                f"{root}/dashboard/billing/credit_grants",
                headers=headers,
                allow_http=ctx.allow_http,
                proxy_url=ctx.proxy_url,
                timeout_seconds=ctx.timeout_seconds,
                secrets=(token,),
                session_factory=ctx.session_factory,
            )
            if g_status == 200 and isinstance(g_payload, dict):
                avail = safe_number(g_payload.get("total_available"))
                granted = safe_number(g_payload.get("total_granted"))
                used = safe_number(g_payload.get("total_used"))
                if avail is None and granted is not None and used is not None:
                    avail = max(0.0, granted - used)
                if avail is not None:
                    return QuotaSnapshot(
                        station_id=spec.id,
                        ok=True,
                        remaining=round(max(0.0, avail), 6),
                        used=round(max(0.0, used), 6) if used is not None else None,
                        total=round(max(0.0, granted), 6) if granted is not None else None,
                        display_type=display_type,
                        group_name="单 Key 限额",
                        is_key_scoped=True,
                        checked_at=checked_at,
                        latency_ms=ctx.elapsed_ms(started),
                        logo_version=logo_ver,
                        logo_error=logo_err,
                    )
            last_err = f"HTTP {g_status}: {g_prev[:80]}"
        except QuotaHttpError as exc:
            last_err = str(exc)

        return QuotaSnapshot(
            station_id=spec.id,
            ok=False,
            error=last_err or f"单 Key 账单接口不可用 (HTTP {last_status})",
            error_code="http_error" if last_status >= 400 else "invalid_response",
            repair_hint="该站点未开放 OpenAI 兼容账单接口；如果是 NewAPI/OneAPI 站，请改用系统访问令牌模式。",
            display_type=display_type,
            checked_at=checked_at,
            latency_ms=ctx.elapsed_ms(started),
            logo_version=logo_ver,
            logo_error=logo_err,
        )


class UnsupportedOfficialAdapter(QuotaAdapter):
    @property
    def descriptor(self) -> AdapterDescriptor:
        return AdapterDescriptor(
            id="unsupported_official",
            name="官方推理 Key（无查额接口）",
            short_name="仅记账观测",
            category="unsupported",
            summary="OpenAI、Anthropic、Google Gemini、火山方舟、阿里云百炼、智谱等官方平台不开放普通推理 API Key 查询账户余额。",
            auth_guide="此类官方站点无法通过推理 Key 直接读取账户余额；可保留用于查看本插件「计价与汇率」统计的近期预估消耗，或改用自定义脚本。",
            capabilities=AdapterCapabilities(
                supports_provider_reuse=True,
                is_unsupported_official=True,
            ),
            fields=(),
            default_display_type="USD",
            order=95,
        )

    async def probe(self, spec: QuotaStationSpec, ctx: AdapterProbeContext) -> QuotaSnapshot:
        return QuotaSnapshot(
            station_id=spec.id,
            ok=False,
            error="该官方平台不支持通过推理 API Key 查询账户余额",
            error_code="unsupported_official",
            repair_hint="可在「计价与汇率」查看本插件统计的预估消耗，或如有自建账单中转接口，改选「自定义脚本」模式。",
            display_type=ctx.display_type or "USD",
            checked_at=ctx.now_iso(),
            latency_ms=0,
        )
