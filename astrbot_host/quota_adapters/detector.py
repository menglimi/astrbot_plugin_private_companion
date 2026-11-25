"""Smart read-only station protocol detector (<= 4s total budget, SSRF & redirect safe)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from companion_v2.model_gateway.quota import normalize_currency_symbol

from .base import (
    QuotaHttpError,
    http_request_json,
    normalize_station_root,
    safe_number,
    same_origin,
    validate_target_url,
)
from .newapi import (
    detect_required_user_header,
    fetch_newapi_status,
    resolve_newapi_display_params,
)

DETECT_TOTAL_BUDGET_SECONDS = 4.0

_OFFICIAL_HOST_RULES: tuple[tuple[tuple[str, ...], str, str, str, str], ...] = (
    (("api.deepseek.com",), "deepseek", "DeepSeek 官方", "CNY", "直接复用 AstrBot DeepSeek 供应商 API Key，无需额外填写凭据。"),
    (("openrouter.ai", "api.openrouter.ai"), "openrouter", "OpenRouter", "USD", "直接复用 AstrBot OpenRouter 供应商 API Key，无需额外填写凭据。"),
    (("api.siliconflow.cn", "api.siliconflow.com"), "siliconflow", "硅基流动 SiliconFlow", "CNY", "直接复用 AstrBot 硅基流动供应商 API Key，无需额外填写凭据。"),
    (("api.moonshot.cn", "api.moonshot.ai"), "moonshot", "Moonshot / Kimi", "CNY", "直接复用 AstrBot Moonshot 供应商 API Key，无需额外填写凭据。"),
)

_UNSUPPORTED_OFFICIAL_HOSTS: tuple[tuple[str, str], ...] = (
    ("api.openai.com", "OpenAI 官方"),
    ("api.anthropic.com", "Anthropic 官方"),
    ("generativelanguage.googleapis.com", "Google Gemini (AI Studio)"),
    ("volces.com", "火山方舟（豆包）"),
    ("dashscope.aliyuncs.com", "阿里云百炼"),
    ("open.bigmodel.cn", "智谱开放平台"),
    ("api.x.ai", "xAI (Grok)"),
    ("api.mistral.ai", "Mistral 官方"),
    ("api.groq.com", "Groq 官方"),
)


@dataclass(frozen=True)
class DetectionResult:
    ok: bool
    adapter_id: str
    confidence: str  # high | medium | low
    normalized_url: str
    suggested_name: str = ""
    display_type: str = ""
    symbol: str = ""
    quota_per_unit: float = 0.0
    requires_user_id: bool = False
    user_id_header: str = "New-Api-User"
    turnstile_enabled: bool = False
    logo_available: bool = False
    auth_hint: str = ""
    reason: str = ""
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "adapter_id": self.adapter_id,
            "confidence": self.confidence,
            "normalized_url": self.normalized_url,
            "suggested_name": self.suggested_name,
            "display_type": self.display_type,
            "symbol": self.symbol,
            "quota_per_unit": self.quota_per_unit,
            "requires_user_id": self.requires_user_id,
            "user_id_header": self.user_id_header,
            "turnstile_enabled": self.turnstile_enabled,
            "logo_available": self.logo_available,
            "auth_hint": self.auth_hint,
            "reason": self.reason,
            "warnings": list(self.warnings),
        }


def _match_hostname(hostname: str, pattern: str) -> bool:
    return hostname == pattern or hostname.endswith("." + pattern)


async def detect_station(
    base_url: str,
    *,
    allow_http: bool = False,
    proxy_url: str = "",
    api_key: str = "",
    timeout_seconds: float = DETECT_TOTAL_BUDGET_SECONDS,
    session_factory: Any = None,
) -> DetectionResult:
    """Identify station protocol & required form fields within a bounded read-only budget."""
    root = normalize_station_root(base_url)
    try:
        validate_target_url(root, allow_http=allow_http)
    except QuotaHttpError as exc:
        return DetectionResult(
            ok=False,
            adapter_id="newapi",
            confidence="low",
            normalized_url=root,
            reason=str(exc),
        )

    parsed = urlsplit(root)
    hostname = (parsed.hostname or "").lower()

    # 1. Instant hostname classification for known official providers (0 outbound calls).
    for hosts, adapter_id, label, currency, hint in _OFFICIAL_HOST_RULES:
        if any(_match_hostname(hostname, h) for h in hosts):
            return DetectionResult(
                ok=True,
                adapter_id=adapter_id,
                confidence="high",
                normalized_url=root,
                suggested_name=label,
                display_type=currency,
                symbol=normalize_currency_symbol(currency),
                auth_hint=hint,
                reason=f"已根据官方域名自动识别为 {label}",
            )

    for suffix, label in _UNSUPPORTED_OFFICIAL_HOSTS:
        if _match_hostname(hostname, suffix):
            return DetectionResult(
                ok=True,
                adapter_id="unsupported_official",
                confidence="high",
                normalized_url=root,
                suggested_name=label,
                display_type="USD",
                symbol="$",
                auth_hint=f"{label} 不支持通过普通推理 API Key 查询账户余额；建议在「计价与汇率」查看本插件记录的预估消耗，或改选「自定义脚本」。",
                reason=f"已识别为 {label}（无推理 Key 查额接口）",
                warnings=("该官方平台不开放推理 Key 余额接口，已跳过盲目探测。",),
            )

    # 2. Active public probe with strict total budget (<= 4s).
    budget = max(1.0, min(float(timeout_seconds), DETECT_TOTAL_BUDGET_SECONDS))

    async def _probe_flow() -> DetectionResult:
        status_data = await fetch_newapi_status(
            root,
            allow_http=allow_http,
            proxy_url=proxy_url,
            timeout_seconds=min(budget * 0.65, 2.8),
            session_factory=session_factory,
        )
        if status_data is not None:
            fingerprint_keys = ("quota_per_unit", "quota_display_type", "system_name", "version", "logo", "server_address")
            if any(k in status_data for k in fingerprint_keys):
                site_name = str(status_data.get("system_name") or hostname).strip()[:64]
                params = resolve_newapi_display_params(status_data)
                display_type = params.display_type
                per_unit = params.per_unit
                symbol = params.symbol
                turnstile = bool(status_data.get("turnstile_check"))
                logo_available = bool(str(status_data.get("logo") or "").strip())

                # Detect NewAPI derivative header requirements cleanly without credentials.
                meta_text = f"{site_name} {status_data.get('version', '')}".lower()
                uid_header = ""
                if "veloera" in meta_text:
                    uid_header = "Veloera-User"
                elif "rix" in meta_text:
                    uid_header = "Rix-Api-User"

                # Anonymous probe to /api/user/self: many NewAPI forks immediately report missing New-Api-User!
                if not uid_header:
                    try:
                        _st, _h, self_payload, self_prev = await http_request_json(
                            "GET",
                            f"{root}/api/user/self",
                            allow_http=allow_http,
                            proxy_url=proxy_url,
                            timeout_seconds=min(budget * 0.35, 1.5),
                            session_factory=session_factory,
                        )
                        msg = str(self_payload.get("message") or "") if isinstance(self_payload, dict) else ""
                        uid_header = detect_required_user_header(f"{msg} {self_prev}")
                    except QuotaHttpError:
                        pass

                requires_uid = bool(uid_header)
                adapter_id = "newapi_user_id" if requires_uid else "newapi"
                warnings: list[str] = []
                if turnstile:
                    warnings.append("检测到站点开启了 Cloudflare Turnstile 校验，建议直接手动粘贴系统访问令牌。")
                auth_hint = (
                    f"已识别为需携带 {uid_header} 请求头的 NewAPI 衍生站，请填写系统访问令牌与数字用户 ID。"
                    if requires_uid
                    else "已识别为 NewAPI / OneAPI 系中转站，请在个人设置中复制系统访问令牌（非 sk- 密钥）。"
                )
                return DetectionResult(
                    ok=True,
                    adapter_id=adapter_id,
                    confidence="high",
                    normalized_url=root,
                    suggested_name=site_name,
                    display_type=display_type,
                    symbol=symbol,
                    quota_per_unit=per_unit,
                    requires_user_id=requires_uid,
                    user_id_header=uid_header or "New-Api-User",
                    turnstile_enabled=turnstile,
                    logo_available=logo_available,
                    auth_hint=auth_hint,
                    reason=f"通过 /api/status 识别为 {site_name}",
                    warnings=tuple(warnings),
                )

        # 3. If caller supplied an API key (e.g. from AstrBot provider import), safely probe same-origin relay APIs.
        clean_key = str(api_key or "").strip()
        if clean_key:
            headers = {"Authorization": f"Bearer {clean_key}"}
            for candidate_path, adapter_id, label in (
                ("/v1/usage", "sub2api", "Sub2API 用量接口"),
                ("/v1/dashboard/billing/subscription", "openai_billing", "OpenAI 兼容单 Key 限额站"),
            ):
                target = f"{root}{candidate_path}"
                if not same_origin(root, target):
                    continue
                try:
                    st, _h, payload, _prev = await http_request_json(
                        "GET",
                        target,
                        headers=headers,
                        allow_http=allow_http,
                        proxy_url=proxy_url,
                        timeout_seconds=min(budget * 0.4, 1.8),
                        secrets=(clean_key,),
                        session_factory=session_factory,
                    )
                except QuotaHttpError:
                    continue
                if st == 200 and isinstance(payload, dict):
                    if adapter_id == "sub2api" and ("remaining" in payload or "quota" in payload):
                        return DetectionResult(
                            ok=True,
                            adapter_id="sub2api",
                            confidence="high",
                            normalized_url=root,
                            suggested_name=hostname,
                            display_type=str(payload.get("unit") or "USD").upper(),
                            auth_hint="已验证支持 /v1/usage，可直接复用 AstrBot 供应商 API Key。",
                            reason=f"自动探测命中 {label}",
                        )
                    if adapter_id == "openai_billing" and (
                        "hard_limit_usd" in payload or "system_hard_limit_usd" in payload
                    ):
                        return DetectionResult(
                            ok=True,
                            adapter_id="openai_billing",
                            confidence="high",
                            normalized_url=root,
                            suggested_name=hostname,
                            display_type="USD",
                            symbol="$",
                            auth_hint="已验证支持单 Key 账单限额查询，可直接复用 AstrBot 供应商 sk- 密钥。",
                            reason=f"自动探测命中 {label}",
                        )

        return DetectionResult(
            ok=True,
            adapter_id="newapi",
            confidence="low",
            normalized_url=root,
            suggested_name=hostname,
            display_type="USD",
            symbol="$",
            auth_hint="未能自动识别该站点公开指纹：若是中转站请选「NewAPI」或「NewAPI 衍生站（神人站）」，非标准接口可选「自定义脚本」。",
            reason="公开探测未命中已知指纹，请从下方预设中选择站点类型",
        )

    try:
        return await asyncio.wait_for(_probe_flow(), timeout=budget)
    except asyncio.TimeoutError:
        return DetectionResult(
            ok=True,
            adapter_id="newapi",
            confidence="low",
            normalized_url=root,
            suggested_name=hostname,
            display_type="USD",
            symbol="$",
            auth_hint="自动识别超时，请直接手动选择站点类型预设；若该站需走代理，请在高级设置中配置专用代理。",
            reason="站点自动识别超时（已跳过阻塞）",
        )
