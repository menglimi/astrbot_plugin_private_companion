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
    normalize_station_root,
    safe_number,
    same_origin,
    validate_target_url,
)
from .detector import DetectionResult, detect_station
from .logo import (
    CachedLogo,
    LOGO_MAX_BYTES,
    StationLogoCache,
    logo_candidate_urls,
    resolve_logo_url,
    sniff_safe_raster_image,
)
from .newapi import (
    DEFAULT_NEWAPI_PER_UNIT,
    NewApiAdapter,
    NewApiUserIdAdapter,
    detect_required_user_header,
    exchange_newapi_login_for_token,
    fetch_newapi_status,
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
    "NewApiUserIdAdapter",
    "OpenAiBillingAdapter",
    "OpenRouterAdapter",
    "QuotaAdapter",
    "QuotaHttpError",
    "SiliconFlowAdapter",
    "StationLogoCache",
    "Sub2ApiAdapter",
    "UnsupportedOfficialAdapter",
    "build_default_registry",
    "default_registry",
    "detect_required_user_header",
    "detect_station",
    "exchange_newapi_login_for_token",
    "fetch_newapi_status",
    "http_request_bytes",
    "http_request_json",
    "logo_candidate_urls",
    "normalize_station_root",
    "resolve_logo_url",
    "safe_number",
    "same_origin",
    "sniff_safe_raster_image",
    "validate_target_url",
]
