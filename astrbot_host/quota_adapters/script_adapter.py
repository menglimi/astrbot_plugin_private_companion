"""Custom QuickJS script adapter reusing the audited service_balance_script sandbox."""

from __future__ import annotations

import time

from companion_v2.model_gateway.quota import (
    QuotaSnapshot,
    QuotaStationSpec,
    redact_quota_text,
)

from ..service_balance_script import BalanceQueryError, ScriptServiceBalancePort
from .base import (
    AdapterCapabilities,
    AdapterDescriptor,
    AdapterProbeContext,
    FieldSpec,
    QuotaAdapter,
)

DEFAULT_SCRIPT_TEMPLATE = (
    "({\n"
    "  request: {\n"
    '    url: "{{baseUrl}}/user/balance",\n'
    '    method: "GET",\n'
    "    headers: {\n"
    '      "Authorization": "Bearer {{apiKey}}",\n'
    '      "User-Agent": "astrbot_plugin_private_companion"\n'
    "    }\n"
    "  },\n"
    "  extractor: function(response) {\n"
    "    return {\n"
    "      isValid: response.is_active !== false,\n"
    "      remaining: Number(response.balance),\n"
    '      unit: "USD"\n'
    "    };\n"
    "  }\n"
    "})"
)


class CustomScriptAdapter(QuotaAdapter):
    @property
    def descriptor(self) -> AdapterDescriptor:
        return AdapterDescriptor(
            id="custom_script",
            name="自定义脚本（QuickJS 沙箱）",
            short_name="自定义脚本",
            category="script",
            summary="复用插件内置 QuickJS 安全沙箱（ES2020，无联网权限，16 MiB / 500ms 上限），可适配任意非标准或魔改中转站。",
            auth_guide="使用 ({ request: {...}, extractor: function(response) {...} }) 表达式；支持 {{baseUrl}}、{{apiKey}}、{{accessToken}}、{{userId}} 变量安全注入。",
            capabilities=AdapterCapabilities(
                supports_provider_reuse=True,
                supports_group_plan=True,
            ),
            fields=(
                FieldSpec(
                    name="auth_token",
                    label="密钥 / 访问令牌（{{apiKey}} / {{accessToken}}）",
                    kind="password",
                    required=False,
                    group="primary",
                    placeholder="复用 AstrBot 供应商时 {{apiKey}} 自动取自供应商",
                    hint="不要把密钥硬编码写进脚本里，请使用模板变量。",
                ),
                FieldSpec(
                    name="user_id",
                    label="用户 ID（可选，{{userId}}）",
                    kind="text",
                    required=False,
                    group="advanced",
                    placeholder="按脚本需要填写数字 UID",
                ),
                FieldSpec(
                    name="custom_script",
                    label="请求与提取脚本（ES2020）",
                    kind="textarea",
                    required=True,
                    group="primary",
                    default=DEFAULT_SCRIPT_TEMPLATE,
                    placeholder=DEFAULT_SCRIPT_TEMPLATE,
                    hint="返回字段：isValid、invalidMessage、remaining、total、used、unit、planName、extra。",
                ),
            ),
            default_display_type="USD",
            order=80,
        )

    async def probe(self, spec: QuotaStationSpec, ctx: AdapterProbeContext) -> QuotaSnapshot:
        started = time.monotonic()
        checked_at = ctx.now_iso()
        script = ctx.custom_script.strip()
        if not script:
            return QuotaSnapshot(
                station_id=spec.id,
                ok=False,
                error="未填写自定义查询脚本",
                error_code="script_error",
                repair_hint="请填写 request + extractor 对象表达式，或切换为内置站点协议。",
                display_type=ctx.display_type or "CREDIT",
                checked_at=checked_at,
                latency_ms=ctx.elapsed_ms(started),
            )

        settings = {
            "advanced_script": script,
            "advanced_base_url": ctx.base_url.strip(),
            "advanced_api_key": ctx.astrbot_api_key.strip() or ctx.auth_token.strip(),
            "advanced_access_token": ctx.auth_token.strip(),
            "advanced_user_id": ctx.user_id.strip(),
            "advanced_proxy_url": ctx.proxy_url.strip(),
        }
        secrets = (ctx.auth_token, ctx.astrbot_api_key, ctx.proxy_url)

        try:
            port = ScriptServiceBalancePort(settings, now=ctx.now)
            observation = await port.fetch()
        except BalanceQueryError as exc:
            return QuotaSnapshot(
                station_id=spec.id,
                ok=False,
                error=redact_quota_text(str(exc), secrets),
                error_code=exc.state or "script_error",
                repair_hint="请检查脚本语法、目标 HTTPS 地址、返回数值类型及代理配置。",
                display_type=ctx.display_type or "CREDIT",
                checked_at=checked_at,
                latency_ms=ctx.elapsed_ms(started),
            )
        except ValueError as exc:
            msg = redact_quota_text(str(exc), secrets)
            hint = (
                "请在 AstrBot Python 环境中安装 quickjs-ng（及使用代理时安装 aiohttp-socks）。"
                if ("quickjs" in msg.lower() or "aiohttp-socks" in msg.lower())
                else "请核对脚本限制（≤16 KiB）、HTTPS 地址与代理格式。"
            )
            return QuotaSnapshot(
                station_id=spec.id,
                ok=False,
                error=msg,
                error_code="script_error",
                repair_hint=hint,
                display_type=ctx.display_type or "CREDIT",
                checked_at=checked_at,
                latency_ms=ctx.elapsed_ms(started),
            )

        unit_raw = str(ctx.display_type or observation.unit or "CREDIT").strip().upper()
        display_type = unit_raw if unit_raw in ("CNY", "USD", "CREDIT", "TOKENS") else "CREDIT"
        symbol = "" if unit_raw in ("CNY", "USD", "CREDIT", "TOKENS") else str(observation.unit or "")[:12]

        return QuotaSnapshot(
            station_id=spec.id,
            ok=True,
            remaining=round(float(observation.balance), 6),
            used=round(float(observation.used), 6) if observation.used is not None else None,
            total=round(float(observation.total), 6) if observation.total is not None else None,
            display_type=display_type,
            symbol=symbol,
            group_name=observation.plan_name or "",
            extra=observation.extra or "",
            checked_at=observation.observed_at.isoformat(),
            latency_ms=ctx.elapsed_ms(started),
        )
