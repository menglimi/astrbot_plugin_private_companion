# -*- coding: utf-8 -*-
"""Typed optional-plugin discovery and capability negotiation primitives.

This module is intentionally framework-light: bridge facades can share one
contract without importing optional plugins or depending on their concrete API
classes.  Existing extension APIs remain duck-typed and unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .external_bridge_resolver import (
    invalidate_external_bridge_cache,
    resolve_external_bridge,
)


@dataclass(frozen=True, slots=True)
class OptionalPluginSpec:
    cache_key: str
    plugin_id: str
    module_names: tuple[str, ...]
    getter_name: str
    prefer_module_getter: bool = True

    @property
    def star_name(self) -> str:
        return self.plugin_id


@dataclass(frozen=True, slots=True)
class CapabilityContract:
    plugin_id: str
    api_family: str
    api_version: str
    task_version: str
    required_capabilities: frozenset[str]
    descriptor_fields: frozenset[str]
    generation_validator: Callable[[Any], bool]
    unavailable_reason: str = ""
    ready_state: str = "ready"


@dataclass(frozen=True, slots=True)
class NegotiationResult:
    state: str
    api: Any | None
    generation: Any
    reason: str


def discover_optional_plugin(owner: Any, spec: OptionalPluginSpec) -> Any | None:
    """Discover a plugin through AstrBot's registry with legacy fallback."""
    return resolve_external_bridge(
        owner,
        cache_key=spec.cache_key,
        module_names=spec.module_names,
        getter_name=spec.getter_name,
        star_name=spec.star_name,
        prefer_module_getter=spec.prefer_module_getter,
    )


def refresh_optional_plugin(owner: Any, spec: OptionalPluginSpec) -> Any | None:
    """Invalidate one lifecycle generation and discover its replacement."""
    invalidate_external_bridge_cache(owner, spec.cache_key)
    return discover_optional_plugin(owner, spec)


def _string_sequence(value: Any) -> tuple[str, ...] | None:
    if type(value) is not list or any(type(item) is not str for item in value):
        return None
    if len(value) != len(set(value)):
        return None
    return tuple(value)


def negotiate_capabilities(
    api: Any | None,
    contract: CapabilityContract,
    *,
    expected_generation: Any = None,
) -> NegotiationResult:
    """Validate the common descriptor envelope and map failures uniformly."""
    empty_generation = "" if isinstance(expected_generation, str) else 0
    if api is None:
        reason = contract.unavailable_reason or contract.plugin_id.removeprefix(
            "astrbot_plugin_"
        ) + "_unavailable"
        return NegotiationResult("missing", None, empty_generation, reason)

    try:
        descriptor_getter = getattr(api, "capabilities")
    except (AttributeError, TypeError):
        return NegotiationResult("incompatible", api, empty_generation, "descriptor_method_missing")
    if not callable(descriptor_getter):
        return NegotiationResult("incompatible", api, empty_generation, "descriptor_method_missing")
    try:
        descriptor = descriptor_getter()
    except Exception:  # third-party boundary: mapped to the stable public reason
        return NegotiationResult("incompatible", api, empty_generation, "descriptor_query_failed")
    if type(descriptor) is not dict or set(descriptor) != contract.descriptor_fields:
        return NegotiationResult("incompatible", api, empty_generation, "descriptor_malformed")

    generation = descriptor.get("instance_generation")
    versions = _string_sequence(descriptor.get("supported_task_versions"))
    capabilities = _string_sequence(descriptor.get("capabilities"))
    degraded = _string_sequence(descriptor.get("degraded_reasons"))
    incompatible = (
        descriptor.get("plugin_id") != contract.plugin_id
        or descriptor.get("api_family") != contract.api_family
        or descriptor.get("api_version") != contract.api_version
        or not contract.generation_validator(generation)
        or (expected_generation is not None and generation != expected_generation)
        or versions is None
        or contract.task_version not in versions
        or capabilities is None
        or not contract.required_capabilities.issubset(capabilities)
        or degraded is None
    )
    if incompatible:
        return NegotiationResult("incompatible", api, empty_generation, "descriptor_incompatible")
    if descriptor.get("lifecycle_state") != contract.ready_state or degraded:
        return NegotiationResult("incompatible", api, generation, "service_not_ready")
    return NegotiationResult("current", api, generation, "")
