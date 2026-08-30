# -*- coding: utf-8 -*-
"""Shared discovery and lifecycle checks for optional companion bridges."""
from __future__ import annotations

import sys
import time
from typing import Any


_POSITIVE_TTL = 15.0
# 未安装外部插件时负向结果可缓存更久，避免频繁全量扫描 sys.modules。
_NEGATIVE_TTL = 10.0
_MISSING = object()


def _lifecycle_active(api: Any) -> bool:
    """Return whether an API explicitly reports that its instance has stopped."""
    if api is None:
        return False
    lifecycle = getattr(api, "bridge_lifecycle_status", None)
    if callable(lifecycle):
        try:
            status = lifecycle()
        except Exception:
            return False
        if isinstance(status, dict) and status.get("active") is False:
            return False
        if isinstance(status, dict) and status.get("active") is True:
            return True

    # Feature status and lifecycle are different signals. A disabled plugin is
    # still installed and must remain discoverable so the panel can report and
    # configure it. Only an explicit active=false marks an old API as stale.
    status_getter = getattr(api, "status", None)
    if callable(status_getter):
        try:
            status = status_getter()
        except TypeError:
            # Some legacy status methods require the host owner. Preserve the
            # pre-resolver compatibility behavior for those APIs.
            return True
        except Exception:
            # A broken status payload is reported by the bridge-specific
            # status call. It must not erase the fact that the API was found.
            return True
        if isinstance(status, dict) and status.get("active") is False:
            return False
    return True


def _safe_identity_text(value: Any) -> str:
    """Convert an optional plugin identity to text without trusting third-party objects."""
    if value is None:
        return ""
    try:
        return str(value)
    except Exception:
        # Some extension namespaces (notably torch custom-class proxies) can
        # raise from __str__. A malformed identity must not break discovery.
        return ""


def _static_module_field(module: Any, name: str, default: Any = None) -> Any:
    """Read an already-registered module field without invoking lazy imports."""
    if module is None:
        return default
    try:
        namespace = object.__getattribute__(module, "__dict__")
    except Exception:
        return default
    if not isinstance(namespace, dict):
        return default
    value = namespace.get(name, _MISSING)
    return default if value is _MISSING else value


def _identity_segments(value: Any) -> set[str]:
    text = _safe_identity_text(value).strip().casefold().replace("\\", "/")
    if not text:
        return set()
    for separator in ("/", ":"):
        text = text.replace(separator, ".")
    return {part.strip().replace("-", "_") for part in text.split(".") if part.strip()}


def _metadata_matches_star(metadata: Any, star_name: str) -> bool:
    expected = _safe_identity_text(star_name).strip().casefold().replace("-", "_")
    if not expected:
        return False
    values = (
        getattr(metadata, "name", ""),
        getattr(metadata, "root_dir_name", ""),
        getattr(metadata, "module_path", ""),
        _static_module_field(getattr(metadata, "module", None), "__name__", ""),
        _static_module_field(getattr(metadata, "module", None), "PLUGIN_NAME", ""),
        getattr(getattr(metadata, "star_cls", None), "plugin_id", ""),
        getattr(type(getattr(metadata, "star_cls", None)), "__module__", ""),
    )
    return any(expected in _identity_segments(value) for value in values)


def _api_from_module(module: Any, getter_name: str) -> Any | None:
    getter = _static_module_field(module, getter_name)
    try:
        return getter() if callable(getter) else None
    except Exception:
        return None


def _module_candidates(
    module_names: tuple[str, ...],
    *,
    getter_name: str,
    star_name: str,
) -> list[Any]:
    suffixes = tuple(name.removeprefix("data.plugins.") for name in module_names)
    candidates: list[Any] = []
    seen: set[int] = set()
    for name in module_names:
        module = sys.modules.get(name)
        if module is not None and id(module) not in seen:
            candidates.append(module)
            seen.add(id(module))
    for name, module in list(sys.modules.items()):
        if module is None or id(module) in seen:
            continue
        module_identity = _static_module_field(module, "PLUGIN_NAME", "")
        if (
            any(name.endswith(suffix) for suffix in suffixes)
            or _safe_identity_text(star_name).casefold().replace("-", "_")
            in _identity_segments(module_identity)
            or callable(_static_module_field(module, getter_name))
        ):
            candidates.append(module)
            seen.add(id(module))
    return candidates


def _registered_star_candidates(owner: Any, star_name: str) -> list[tuple[Any, bool]]:
    context = getattr(owner, "context", None)
    candidates: list[tuple[Any, bool]] = []

    # Ask for the exact registry entry first. Custom AstrBot-compatible
    # contexts may expose historical entries through get_all_stars() while
    # get_registered_star() still identifies the instance currently in use.
    get_one = getattr(context, "get_registered_star", None)
    if callable(get_one):
        try:
            metadata = get_one(star_name)
        except Exception:
            metadata = None
        if metadata is not None:
            candidates.append((metadata, True))

    seen = {id(metadata) for metadata, _exact in candidates}

    get_all = getattr(context, "get_all_stars", None)
    if callable(get_all):
        try:
            stars = list(get_all() or [])
        except Exception:
            stars = []
        for metadata in stars:
            if metadata is not None and id(metadata) not in seen:
                candidates.append((metadata, False))
                seen.add(id(metadata))
    return candidates


def _uncached_resolve(
    owner: Any,
    *,
    module_names: tuple[str, ...],
    getter_name: str,
    star_name: str,
    prefer_module_getter: bool = False,
) -> Any | None:
    # Prefer AstrBot's current registry. A stale module alias may survive a hot
    # reload, while get_all_stars() points at the active instance and module.
    for metadata, exact_registry_match in _registered_star_candidates(owner, star_name):
        if not bool(getattr(metadata, "activated", True)):
            continue
        if not exact_registry_match and not _metadata_matches_star(metadata, star_name):
            continue
        module = getattr(metadata, "module", None)
        instance = getattr(metadata, "star_cls", None)

        # A few extensions keep their active API in a module-level singleton.
        # During a hot reload AstrBot can briefly expose a newer module getter
        # alongside an older registry instance. Those extensions can opt into
        # the legacy ordering so the live singleton remains usable.
        if prefer_module_getter:
            candidates = (
                _api_from_module(module, getter_name),
                getattr(instance, "extension_api", None) if instance is not None else None,
            )
        else:
            candidates = (
                getattr(instance, "extension_api", None) if instance is not None else None,
                _api_from_module(module, getter_name),
            )
        for api in candidates:
            if api is not None and api is not owner and _lifecycle_active(api):
                return api

    # Fixed names remain the fast compatibility path. The candidate scan also
    # accepts the plugin's canonical PLUGIN_NAME or its unique getter, covering
    # custom directory names and hot-reload module aliases without broad fuzzy
    # matching against unrelated plugin descriptions.
    for module in _module_candidates(
        module_names,
        getter_name=getter_name,
        star_name=star_name,
    ):
        api = _api_from_module(module, getter_name)
        if api is not None and api is not owner and _lifecycle_active(api):
            return api
    return None


def resolve_external_bridge(
    owner: Any,
    *,
    cache_key: str,
    module_names: tuple[str, ...],
    getter_name: str,
    star_name: str,
    prefer_module_getter: bool = False,
) -> Any | None:
    """Resolve an optional plugin API with bounded positive/negative caching."""
    cache = getattr(owner, "_external_bridge_resolver_cache", None)
    if not isinstance(cache, dict):
        cache = {}
        setattr(owner, "_external_bridge_resolver_cache", cache)
    now = time.monotonic()
    entry = cache.get(cache_key)
    if isinstance(entry, dict) and now < float(entry.get("expires_at", 0.0) or 0.0):
        api = entry.get("api")
        if api is None or _lifecycle_active(api):
            return api
        cache.pop(cache_key, None)

    api = _uncached_resolve(
        owner,
        module_names=module_names,
        getter_name=getter_name,
        star_name=star_name,
        prefer_module_getter=prefer_module_getter,
    )
    cache[cache_key] = {
        "api": api,
        "expires_at": now + (_POSITIVE_TTL if api is not None else _NEGATIVE_TTL),
    }
    return api


def invalidate_external_bridge_cache(owner: Any, cache_key: str | None = None) -> None:
    cache = getattr(owner, "_external_bridge_resolver_cache", None)
    if not isinstance(cache, dict):
        return
    if cache_key:
        cache.pop(cache_key, None)
    else:
        cache.clear()
