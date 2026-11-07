"""The dashboard edits the same independent V2 settings that execute images."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from urllib.parse import urlsplit

from .types import MAX_REFERENCE_IMAGES, NativeImageError, fingerprint

BACKENDS = {"auto", "online_api", "sd_webui", "comfyui", "nai"}
# xAI's Grok Imagine is its own request contract: OpenAI's ``size`` is rejected
# with 400 ("Argument not supported: size"), output dimensions come from
# ``aspect_ratio``/``resolution``, and editing takes JSON ``image``/``images``
# objects (a URL or a Files API ``file_id``) instead of a multipart upload.
XAI_PLATFORM = "xai"
XAI_QUALITY_MODEL = "grok-imagine-image-2.0"
XAI_QUALITIES = frozenset({"auto", "low", "medium"})
# Keep the complete V1 provider vocabulary in the persisted contract.  A saved
# provider can be shown and edited even when its protocol adapter is not loaded;
# routing reports it as unsupported instead of silently changing it to OpenAI.
PLATFORMS = {
    "auto", "openai", "openrouter", "agnes", "sensenova", "minimax",
    "bailian", "modelscope", "doubao", "gemini", "xai",
}
# ``quality`` and ``response_format`` are optional request fields: the empty
# string means "omit the field and use the service default".  The previous
# contract forced ``quality=standard`` and ``response_format=b64_json`` on every
# OpenAI-compatible request, which the official GPT Image protocol rejects.
IMAGE_QUALITIES = {"", "auto", "low", "medium", "high", "standard", "hd"}
RESPONSE_FORMATS = {"", "b64_json"}
# The official GPT Image models are a different request contract from the
# legacy dall-e family: they have no ``response_format`` field at all and accept
# ``quality`` only from {auto, low, medium, high}.  Versions up to 7.0.7 wrote
# the dall-e default ``standard`` into every saved endpoint (and forced
# ``b64_json`` on the wire), so a stored ``standard`` on a GPT Image endpoint is
# a legacy default, not a user decision.  A dall-e endpoint keeps an explicit
# ``standard``/``hd``; a model this release cannot classify keeps its saved
# values as well, because a compatible gateway may legitimately accept them.
GPT_IMAGE_MODEL_PREFIXES = ("gpt-image-", "chatgpt-image-")
GPT_IMAGE_QUALITIES = frozenset({"auto", "low", "medium", "high"})
DALL_E_MODEL_PREFIXES = ("dall-e", "dalle")
IMAGE_OPERATIONS = ("generate", "edit")
LOCAL_ROUTES = {"comfyui", "sd_webui", "nai"}
# One image operation is bounded by the same budget everywhere: the tool task
# deadline, the HTTP call and the polling wait.  300s is the historical total
# budget and 1800s is the documented maximum an endpoint may configure.
DEFAULT_TASK_TIMEOUT_SECONDS = 300
MAX_TASK_TIMEOUT_SECONDS = 1800


def task_timeout_seconds(config) -> int:
    """The single time budget shared by the image tool task and its wait.

    The manifest used to declare a fixed 300s deadline while endpoints accepted
    up to 1800s, so the supervisor cancelled work the dashboard explicitly
    allowed to run longer.  The budget now follows the longest timeout an
    administrator configured, never dropping below the previous default and
    never exceeding the documented limit.
    """
    if not isinstance(config, dict):
        return DEFAULT_TASK_TIMEOUT_SECONDS
    budget = DEFAULT_TASK_TIMEOUT_SECONDS
    for endpoint in config.get("endpoints") or ():
        if isinstance(endpoint, dict) and endpoint.get("enabled") is True:
            budget = max(budget, _configured_timeout(endpoint.get("timeout_seconds")))
    for name in ("sd_webui", "comfyui"):
        connection = config.get(name)
        if isinstance(connection, dict) and connection.get("base_url"):
            budget = max(budget, _configured_timeout(connection.get("timeout_seconds")))
    return min(MAX_TASK_TIMEOUT_SECONDS, budget)


def _configured_timeout(value) -> int:
    if type(value) is int and 1 <= value <= MAX_TASK_TIMEOUT_SECONDS:
        return value
    return 0


def image_model_family(model) -> str:
    """Classify a saved model ID into the request contract it belongs to.

    Returns ``"gpt-image"`` for the official GPT Image family, ``"dall-e"`` for
    the legacy OpenAI models or ``""`` for anything this release cannot prove.
    Provider decorations such as ``openai/``, ``azure:`` or a ``:free`` suffix
    are ignored, so a gateway keeps the same model contract.
    """
    name = model.strip().lower() if isinstance(model, str) else ""
    segments = [part for part in name.replace(":", "/").split("/") if part]
    if any(part.startswith(GPT_IMAGE_MODEL_PREFIXES) for part in segments):
        return "gpt-image"
    if any(part.startswith(DALL_E_MODEL_PREFIXES) for part in segments):
        return "dall-e"
    return ""


def is_xai_image_host(base) -> bool:
    """Whether an OpenAI-compatible base URL targets xAI's Grok Imagine API.

    Only the host is inspected: a gateway on another domain keeps the OpenAI
    contract unless the administrator selects the ``xai`` protocol explicitly.
    """
    try:
        host = (urlsplit(base).hostname or "").lower()
    except (TypeError, ValueError):
        return False
    return host == "x.ai" or host.endswith(".x.ai")


def xai_quality_capable(model) -> bool:
    """Grok Imagine accepts ``quality`` only on the 2.0 family (auto/low/medium)."""
    name = model.strip().lower() if isinstance(model, str) else ""
    segments = [part for part in name.replace(":", "/").split("/") if part]
    return any(part == XAI_QUALITY_MODEL for part in segments)


def accepted_request_fields(platform, model, *, quality="", response_format=""):
    """The optional request fields this exact backend/model can accept.

    An empty value means "omit the field and use the service default".  This is
    the single capability decision shared by the wire request, the dashboard
    projection and the save normalization, so the page and the request can
    never disagree about a saved value.
    """
    if not isinstance(quality, str) or quality not in IMAGE_QUALITIES:
        quality = ""
    if not isinstance(response_format, str) or response_format not in RESPONSE_FORMATS:
        response_format = ""
    if platform == "minimax":
        # MiniMax has its own fixed protocol; its request builder never
        # forwards the OpenAI quality/response_format vocabulary.
        return {"quality": "", "response_format": ""}
    if platform == XAI_PLATFORM:
        # Grok Imagine 1.0 rejects ``quality`` outright and 2.0 accepts only
        # auto/low/medium, so the page and the wire share one decision here.
        allowed = quality if (xai_quality_capable(model) and quality in XAI_QUALITIES) else ""
        return {"quality": allowed, "response_format": response_format}
    if image_model_family(model) == "gpt-image":
        return {"quality": quality if quality in GPT_IMAGE_QUALITIES else "",
                "response_format": ""}
    return {"quality": quality, "response_format": response_format}


def project_endpoint_fields(endpoint) -> tuple[str, str]:
    """The effective ``(quality, response_format)`` for one saved endpoint.

    Only the GPT Image family is normalized: 7.0.7's ``standard`` default and
    its forced ``b64_json`` are rejected by that protocol.  A dall-e endpoint
    (or any model this release cannot classify) keeps the saved choice exactly,
    so a deliberate legacy ``standard`` is never rewritten for the wrong model.
    """
    if not isinstance(endpoint, dict):
        return "", ""
    quality = endpoint.get("quality", "")
    response_format = endpoint.get("response_format", "")
    quality = quality if isinstance(quality, str) and quality in IMAGE_QUALITIES else ""
    response_format = (response_format if isinstance(response_format, str)
                       and response_format in RESPONSE_FORMATS else "")
    if image_model_family(endpoint.get("model")) == "gpt-image":
        accepted = accepted_request_fields(endpoint.get("platform"), endpoint.get("model"),
                                           quality=quality, response_format=response_format)
        return accepted["quality"], accepted["response_format"]
    return quality, response_format


def has_legacy_protocol_defaults(endpoint) -> bool:
    """Whether the saved values are the old dall-e defaults that GPT Image rejects.

    The page uses this read-only projection flag to explain why a saved
    ``standard``/``b64_json`` is not sent; it is never persisted (the next save
    stores the normalized values).
    """
    if not isinstance(endpoint, dict) or image_model_family(endpoint.get("model")) != "gpt-image":
        return False
    quality, response_format = project_endpoint_fields(endpoint)
    return (quality != endpoint.get("quality", "")
            or response_format != endpoint.get("response_format", ""))


def _default_operation_routes(value):
    selected = value.get("backend", "auto")
    endpoints = value.get("endpoints", [])
    if not isinstance(selected, str) or selected not in BACKENDS:
        selected = "auto"
    if not isinstance(endpoints, list):
        endpoints = []
    online = ["online:" + item["id"] for item in endpoints
              if isinstance(item, dict) and isinstance(item.get("id"), str)]
    routes = []
    if selected in {"auto", "online_api"}:
        routes.extend(online)
    if selected in {"auto", "comfyui"}:
        routes.append("comfyui")
    if selected in {"auto", "sd_webui"}:
        routes.append("sd_webui")
    if selected == "nai":
        routes.append("nai")
    return {operation: list(routes) for operation in IMAGE_OPERATIONS}


def _stored_operation_routes(value):
    defaults = _default_operation_routes(value)
    raw = value.get("operation_routes")
    if not isinstance(raw, dict) or raw.keys() - set(IMAGE_OPERATIONS):
        return defaults
    endpoints = value.get("endpoints", [])
    endpoint_ids = {item["id"] for item in endpoints
                    if isinstance(item, dict) and isinstance(item.get("id"), str)}
    result = {}
    for operation in IMAGE_OPERATIONS:
        routes = raw.get(operation)
        if not isinstance(routes, list):
            result[operation] = defaults[operation]
            continue
        seen = set()
        normalized = []
        for route in routes:
            if not isinstance(route, str) or route in seen:
                continue
            if route in LOCAL_ROUTES or (route.startswith("online:") and route[7:] in endpoint_ids):
                normalized.append(route)
                seen.add(route)
        result[operation] = normalized
    return result


def _validate_operation_routes(value, endpoints):
    raw = value.get("operation_routes")
    if raw is None:
        return _default_operation_routes(value)
    if not isinstance(raw, dict) or raw.keys() - set(IMAGE_OPERATIONS):
        raise NativeImageError("invalid_configuration")
    endpoint_ids = {item["id"] for item in endpoints}
    result = {}
    for operation in IMAGE_OPERATIONS:
        routes = raw.get(operation)
        if not isinstance(routes, list) or len(routes) > 24 + len(LOCAL_ROUTES):
            raise NativeImageError("invalid_configuration")
        seen = set()
        normalized = []
        for route in routes:
            if not isinstance(route, str) or route in seen:
                raise NativeImageError("invalid_configuration")
            if route in LOCAL_ROUTES:
                normalized.append(route)
            elif route.startswith("online:") and route[7:] in endpoint_ids:
                normalized.append(route)
            else:
                raise NativeImageError("invalid_configuration")
            seen.add(route)
        result[operation] = normalized
    return result


def settings(plugin):
    native = plugin.config.get("native_v2", {})
    saved = native.get("output")
    if isinstance(saved, dict):
        result = copy.deepcopy(saved)
        result.setdefault("legacy_backend", "")
        result.setdefault("legacy_options", {})
        result.setdefault("network", {})
        for endpoint in result.get("endpoints", []):
            if isinstance(endpoint, dict):
                # 7.1.x saved xAI as an ordinary OpenAI-compatible endpoint.
                # Project the dedicated protocol so the page offers xAI's own
                # fields; the value is persisted on the next successful save.
                if endpoint.get("platform") == "openai" and is_xai_image_host(endpoint.get("base_url", "")):
                    endpoint["platform"] = XAI_PLATFORM
                # Project the effective protocol values.  A 7.0.7 ``standard``
                # on a GPT Image endpoint is its old default and is not sent;
                # dall-e/unknown models keep the administrator's exact choice.
                # The read-only flag lets the page explain the migration; it is
                # dropped again by save_configuration and never persisted.
                legacy = has_legacy_protocol_defaults(endpoint)
                endpoint["quality"], endpoint["response_format"] = project_endpoint_fields(endpoint)
                endpoint["legacy_protocol_defaults"] = legacy
                endpoint.setdefault("max_reference_images", MAX_REFERENCE_IMAGES)
                endpoint.setdefault("size_label", f"{endpoint.get('width', 1024)}x{endpoint.get('height', 1024)}")
        result["operation_routes"] = _stored_operation_routes(result)
        return result
    connection = copy.deepcopy(native.get("connection", {}))
    result = {"enabled": native.get("enabled") is True,
              "backend": native.get("backend", "sd_webui"), "endpoints": [],
              "legacy_backend": "",
              "legacy_options": {}, "network": {},
              "sd_webui": connection if native.get("backend") != "comfyui" else {},
              "comfyui": connection if native.get("backend") == "comfyui" else {}}
    result["operation_routes"] = _default_operation_routes(result)
    return result


def _revision_source(value):
    """Return persisted inputs only for optimistic concurrency checks."""
    source = copy.deepcopy(value)
    source.pop("operation_routes", None)
    return source


def public_configuration(plugin):
    source = settings(plugin)
    result = copy.deepcopy(source)
    for item in result.get("endpoints", []):
        headers = item.pop("custom_headers", None)
        item["custom_headers_set"] = bool(headers)
        item["custom_header_names"] = sorted(headers) if isinstance(headers, dict) else []
        item["api_key_set"] = bool(item.pop("api_key", ""))
    # Local backends do not have endpoint-level custom headers.  Projecting
    # those display-only endpoint fields here makes an unmodified public
    # configuration fail its own save validation when the page sends it back.
    for item in (result.get("sd_webui", {}), result.get("comfyui", {})):
        item["api_key_set"] = bool(item.pop("api_key", ""))
    network = result.setdefault("network", {})
    network["download_proxy_set"] = bool(network.pop("download_proxy", ""))
    provider = getattr(plugin, "_native_v2_provider", None)
    from .nai_backend import NAIImageBackend
    nai = NAIImageBackend(getattr(plugin, "context", None), Path(plugin.data_dir) / "nai").status()
    return {"revision": fingerprint(_revision_source(source)), "settings": result, "nai": nai,
            "live": bool(provider and not provider._closed),
            "limits": {"max_endpoints": 24, "timeout_min": 1, "timeout_max": MAX_TASK_TIMEOUT_SECONDS},
            "supported_backends": sorted(BACKENDS), "supported_platforms": sorted(PLATFORMS)}


def _network(value, previous):
    if not isinstance(value, dict) or value.keys() - {"download_proxy", "download_proxy_set",
                                                       "use_environment_proxy", "clear_download_proxy"}:
        raise NativeImageError("invalid_configuration")
    result = copy.deepcopy(value)
    proxy = result.get("download_proxy", previous.get("download_proxy", ""))
    use_environment_proxy = result.get("use_environment_proxy", previous.get("use_environment_proxy", False))
    result.pop("download_proxy_set", None)
    clear = result.pop("clear_download_proxy", False)
    if (not isinstance(proxy, str) or len(proxy) > 2048 or type(use_environment_proxy) is not bool
            or type(clear) is not bool):
        raise NativeImageError("invalid_configuration")
    if clear:
        proxy = ""
    elif not proxy:
        proxy = previous.get("download_proxy", "")
    if proxy:
        try:
            parsed = urlsplit(proxy)
        except ValueError as exc:
            raise NativeImageError("invalid_configuration") from exc
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password \
        or parsed.fragment:
            raise NativeImageError("invalid_configuration")
    return {"download_proxy": proxy.rstrip("/"), "use_environment_proxy": use_environment_proxy}


def _legacy_options(value, previous):
    if value is None:
        value = previous or {}
    if not isinstance(value, dict) or len(json.dumps(value, ensure_ascii=False)) > 20000:
        raise NativeImageError("invalid_configuration")
    try:
        encoded = json.loads(json.dumps(value, ensure_ascii=False))
    except (TypeError, ValueError) as exc:
        raise NativeImageError("invalid_configuration") from exc
    return encoded


def _connection(value, previous):
    if not isinstance(value, dict) or value.keys() - {"base_url", "api_key", "api_key_set", "clear_api_key",
            "timeout_seconds", "parameters", "workflows", "custom_headers_set", "custom_header_names"}:
        raise NativeImageError("invalid_configuration")
    result = copy.deepcopy(value)
    result.pop("api_key_set", None)
    # Accept the endpoint projection fields for compatibility with older page
    # builds, but never persist them on a local backend.
    result.pop("custom_headers_set", None)
    result.pop("custom_header_names", None)
    clear = result.pop("clear_api_key", False)
    key = result.get("api_key", "")
    if not isinstance(key, str) or type(clear) is not bool:
        raise NativeImageError("invalid_configuration")
    result["api_key"] = "" if clear else key or previous.get("api_key", "")
    raw = result.get("base_url", "")
    if not isinstance(raw, str):
        raise NativeImageError("invalid_configuration")
    if raw:
        try:
            url = urlsplit(raw)
        except ValueError as exc:
            raise NativeImageError("invalid_configuration") from exc
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or url.fragment:
            raise NativeImageError("invalid_configuration")
    timeout = result.get("timeout_seconds", 300)
    if type(timeout) is not int or not 1 <= timeout <= MAX_TASK_TIMEOUT_SECONDS:
        raise NativeImageError("invalid_configuration")
    result["timeout_seconds"] = timeout
    for key in ("parameters", "workflows"):
        if key in result:
            raw_value = result[key]
            if isinstance(raw_value, str):
                try:
                    raw_value = json.loads(raw_value)
                except ValueError as exc:
                    raise NativeImageError("invalid_configuration") from exc
            if not isinstance(raw_value, dict):
                raise NativeImageError("invalid_configuration")
            result[key] = raw_value
    return result


def save_configuration(plugin, revision, changes):
    before = settings(plugin)
    if revision not in {fingerprint(_revision_source(before)), fingerprint(before)}:
        raise NativeImageError("configuration_conflict")
    if not isinstance(changes, dict) or changes.keys() - {
            "enabled", "backend", "legacy_backend", "legacy_options", "network", "endpoints", "sd_webui",
            "comfyui", "operation_routes"}:
        raise NativeImageError("invalid_configuration")
    candidate = {**before, **copy.deepcopy(changes)}
    if (type(candidate.get("enabled")) is not bool or candidate.get("backend") not in BACKENDS
            or not isinstance(candidate.get("legacy_backend", ""), str)
            or len(candidate.get("legacy_backend", "")) > 64):
        raise NativeImageError("invalid_configuration")
    candidate["legacy_options"] = _legacy_options(candidate.get("legacy_options"), before.get("legacy_options", {}))
    candidate["network"] = _network(candidate.get("network", {}), before.get("network", {}))
    for name in ("sd_webui", "comfyui"):
        candidate[name] = _connection(candidate.get(name, {}), before.get(name, {}))
    endpoints = candidate.get("endpoints")
    if not isinstance(endpoints, list) or len(endpoints) > 24:
        raise NativeImageError("invalid_configuration")
    ids = set()
    previous = {item["id"]: item for item in before.get("endpoints", [])}
    normalized = []
    for item in endpoints:
        if not isinstance(item, dict) or item.keys() - {"id", "name", "enabled", "platform", "model",
                "width", "height", "size_label", "quality", "response_format", "max_reference_images",
                "base_url", "api_key", "api_key_set", "legacy_protocol_defaults",
                "clear_api_key", "clear_custom_headers", "timeout_seconds", "custom_headers", "custom_headers_set",
                "custom_header_names", "ratio"}:
            raise NativeImageError("invalid_configuration")
        identifier = item.get("id")
        if not isinstance(identifier, str) or not identifier or len(identifier) > 128 or identifier in ids:
            raise NativeImageError("invalid_configuration")
        ids.add(identifier)
        quality = item.get("quality", "")
        response_format = item.get("response_format", "")
        max_reference_images = item.get("max_reference_images", MAX_REFERENCE_IMAGES)
        if (type(item.get("enabled")) is not bool or item.get("platform") not in PLATFORMS
                or any(type(item.get(key)) is not str or len(item[key]) > 240 for key in ("name", "model"))
                or any(type(item.get(key)) is not int or not 64 <= item[key] <= 8192 for key in ("width", "height"))
                or ("size_label" in item and (not isinstance(item.get("size_label"), str)
                                               or len(item.get("size_label", "")) > 64))
                or quality not in IMAGE_QUALITIES or response_format not in RESPONSE_FORMATS
                or type(max_reference_images) is not int or not 0 <= max_reference_images <= MAX_REFERENCE_IMAGES):
            raise NativeImageError("invalid_configuration")
        clear_headers = item.get("clear_custom_headers", False)
        if type(clear_headers) is not bool:
            raise NativeImageError("invalid_configuration")
        # An old page can still post the 7.0.7 default ``standard`` for a GPT
        # Image endpoint.  It is not a protocol value there, so it is migrated to
        # "server default" instead of rejecting the save; dall-e keeps it.
        quality, response_format = project_endpoint_fields(item)
        headers = {} if clear_headers else item.get("custom_headers", previous.get(identifier, {}).get("custom_headers", {}))
        if headers in (None, ""):
            headers = {}
        if isinstance(headers, str):
            try:
                headers = json.loads(headers)
            except ValueError as exc:
                raise NativeImageError("invalid_configuration") from exc
        if (type(headers) is not dict or len(headers) > 32
                or any(not isinstance(key, str) or not key or len(key) > 80
                       or not isinstance(value, str) or len(value) > 2000
                       or "\r" in value or "\n" in value for key, value in headers.items())):
            raise NativeImageError("invalid_configuration")
        ratio = item.get("ratio", "")
        if not isinstance(ratio, str) or len(ratio) > 20:
            raise NativeImageError("invalid_configuration")
        connection = _connection({key:value for key,value in item.items() if key in
            {"base_url", "api_key", "api_key_set", "clear_api_key", "timeout_seconds"}}, previous.get(identifier, {}))
        normalized.append({**item, "name": item["name"].strip(),
                           "size_label": item.get("size_label", f"{item.get('width')}x{item.get('height')}"),
                           **connection, "custom_headers": headers, "ratio": ratio, "quality": quality,
                           "response_format": response_format,
                           "max_reference_images": max_reference_images})
        normalized[-1].pop("api_key_set", None)
        normalized[-1].pop("legacy_protocol_defaults", None)
        normalized[-1].pop("clear_api_key", None)
        normalized[-1].pop("clear_custom_headers", None)
        normalized[-1].pop("custom_headers_set", None)
        normalized[-1].pop("custom_header_names", None)
    candidate["endpoints"] = normalized
    if "operation_routes" not in changes:
        if "backend" in changes and changes["backend"] != before.get("backend"):
            candidate["operation_routes"] = _default_operation_routes(candidate)
        else:
            valid_refs = {"online:" + item["id"] for item in normalized} | LOCAL_ROUTES
            routes = before.get("operation_routes") or _default_operation_routes(before)
            candidate["operation_routes"] = {
                operation: [route for route in routes.get(operation, []) if route in valid_refs]
                for operation in IMAGE_OPERATIONS}
            previous_ids = {item["id"] for item in before.get("endpoints", []) if isinstance(item, dict)}
            new_refs = ["online:" + item["id"] for item in normalized if item["id"] not in previous_ids]
            for operation in IMAGE_OPERATIONS:
                candidate["operation_routes"][operation].extend(new_refs)
    candidate["operation_routes"] = _validate_operation_routes(candidate, normalized)
    if candidate["enabled"] and candidate["backend"] == "online_api" and not any(
            endpoint["enabled"] and endpoint["base_url"] and endpoint["model"]
            for endpoint in normalized):
        raise NativeImageError("invalid_configuration")
    for name in ("sd_webui", "comfyui"):
        connection = candidate[name]
        if connection.get("base_url"):
            from .backends import NativeTransportBackend
            NativeTransportBackend(name, connection, Path(plugin.data_dir) / "native_v2" / "validate")
    provider = getattr(plugin, "_native_v2_provider", None)
    if provider and provider.owner._tasks:
        raise NativeImageError("configuration_busy")
    from .output_routing import OutputRoutingBackend
    backend = OutputRoutingBackend(candidate, Path(plugin.data_dir) / "native_v2" / "transport",
                                   context=getattr(plugin, "context", None))
    if candidate["enabled"] and not backend.operations:
        raise NativeImageError("native_endpoint_unconfigured")
    original = copy.deepcopy(plugin.config.get("native_v2", {}))
    saver = getattr(plugin.config, "save_config", None)
    if not callable(saver):
        raise NativeImageError("configuration_store_unavailable")
    try:
        # ``native_v2.enabled`` is the lifecycle/config-availability flag.  Do
        # not overload it with the output feature switch; a disabled output
        # must still leave the extension and its editor loaded.
        plugin.config["native_v2"] = {**original, "enabled": True,
                                      "backend": candidate["backend"], "output": candidate}
        saver()
    except Exception as exc:
        plugin.config["native_v2"] = original
        raise NativeImageError("configuration_save_failed") from exc
    if provider:
        provider.owner.backend = backend
        # The tool declaration keeps the documented ceiling (an immutable
        # catalog identity); the effective per-task budget is derived live from
        # this saved configuration, so the next task uses it without a
        # registry/lease refresh.  Tasks that already started keep the snapshot
        # they captured at dispatch.
        provider.wait_budget_seconds = provider.task_timeout_seconds()
    else:
        from .bootstrap import build_provider
        from ... import main
        provider = build_provider(plugin, version=main.PLUGIN_VERSION)
        plugin._native_v2_provider = provider
        if provider is None:
            raise NativeImageError("image_provider_unavailable")
    plugin._native_v2_mode = candidate["enabled"]
    return public_configuration(plugin)
