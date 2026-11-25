"""Built-in quota adapter registry and public exports."""

from __future__ import annotations

from .base import (
    AdapterCapabilities,
    AdapterDescriptor,
    AdapterProbeContext,
    AdapterRegistry,
    FieldSpec,
    QuotaAdapter,
    QuotaHttpError,
    http_request_bytes,
    http_request_json,
    http_request_public_bytes,
    normalize_station_root,
    safe_number,
    same_origin,
    validate_target_url,
)
from .detector import DetectionResult, detect_station
from .logo import (
    CachedLogo,
    LOGO_MAX_BYTES,
    SVG_MAX_BYTES,
    StationLogoCache,
    extract_html_icon_links,
    logo_candidate_urls,
    resolve_logo_url,
    sanitize_safe_svg,
    sniff_safe_logo,
    sniff_safe_raster_image,
)
from .newapi import (
    DEFAULT_NEWAPI_PER_UNIT,
    NewApiAdapter,
    NewApiDisplayParams,
    NewApiUserIdAdapter,
    convert_newapi_quota,
    detect_required_user_header,
    exchange_newapi_login_for_token,
    fetch_newapi_status,
    fetch_newapi_subscriptions,
    fetch_newapi_topup_info,
    fetch_recent_consumption_logs,
    infer_newapi_fiat_conversion,
    resolve_newapi_display_params,
)
from .official import (
    DeepSeekAdapter,
    MoonshotAdapter,
    OpenRouterAdapter,
    SiliconFlowAdapter,
)
from .relay_generic import (
    OpenAiBillingAdapter,
    Sub2ApiAdapter,
    UnsupportedOfficialAdapter,
)
from .script_adapter import CustomScriptAdapter, DEFAULT_SCRIPT_TEMPLATE

_DEFAULT_REGISTRY: AdapterRegistry | None = None


def build_default_registry() -> AdapterRegistry:
    registry = AdapterRegistry()
    for adapter in (
        NewApiAdapter(),
        NewApiUserIdAdapter(),
        DeepSeekAdapter(),
        OpenRouterAdapter(),
        SiliconFlowAdapter(),
        MoonshotAdapter(),
        Sub2ApiAdapter(),
        OpenAiBillingAdapter(),
        CustomScriptAdapter(),
        UnsupportedOfficialAdapter(),
    ):
        registry.register(adapter)
    return registry


def default_registry() -> AdapterRegistry:
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        _DEFAULT_REGISTRY = build_default_registry()
    return _DEFAULT_REGISTRY


__all__ = [
    "AdapterCapabilities",
    "AdapterDescriptor",
    "AdapterProbeContext",
    "AdapterRegistry",
    "CachedLogo",
    "CustomScriptAdapter",
    "DEFAULT_NEWAPI_PER_UNIT",
    "DEFAULT_SCRIPT_TEMPLATE",
    "DeepSeekAdapter",
    "DetectionResult",
    "FieldSpec",
    "LOGO_MAX_BYTES",
    "MoonshotAdapter",
    "NewApiAdapter",
    "NewApiDisplayParams",
    "NewApiUserIdAdapter",
    "OpenAiBillingAdapter",
    "OpenRouterAdapter",
    "QuotaAdapter",
    "QuotaHttpError",
    "SVG_MAX_BYTES",
    "SiliconFlowAdapter",
    "StationLogoCache",
    "Sub2ApiAdapter",
    "UnsupportedOfficialAdapter",
    "build_default_registry",
    "convert_newapi_quota",
    "default_registry",
    "detect_required_user_header",
    "detect_station",
    "exchange_newapi_login_for_token",
    "extract_html_icon_links",
    "fetch_newapi_status",
    "fetch_newapi_subscriptions",
    "fetch_newapi_topup_info",
    "fetch_recent_consumption_logs",
    "http_request_bytes",
    "http_request_json",
    "http_request_public_bytes",
    "infer_newapi_fiat_conversion",
    "logo_candidate_urls",
    "normalize_station_root",
    "resolve_logo_url",
    "resolve_newapi_display_params",
    "safe_number",
    "same_origin",
    "sanitize_safe_svg",
    "sniff_safe_logo",
    "sniff_safe_raster_image",
    "validate_target_url",
]
