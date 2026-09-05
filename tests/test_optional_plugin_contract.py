# -*- coding: utf-8 -*-
"""Characterization/contract tests for the shared optional-plugin boundary."""
from __future__ import annotations

from types import SimpleNamespace

from astrbot_plugin_private_companion.companion.integrations.optional_plugin import (
    CapabilityContract,
    OptionalPluginSpec,
    negotiate_capabilities,
)


SPEC = OptionalPluginSpec(
    cache_key="sample",
    plugin_id="astrbot_plugin_sample",
    module_names=("astrbot_plugin_sample.main",),
    getter_name="get_sample_api",
)
CONTRACT = CapabilityContract(
    plugin_id=SPEC.plugin_id,
    api_family="sample.family",
    api_version="sample.api.v1",
    task_version="sample.task.v1",
    required_capabilities=frozenset({"sample.execute"}),
    descriptor_fields=frozenset(
        {
            "plugin_id", "instance_generation", "api_family", "api_version",
            "supported_task_versions", "capabilities", "lifecycle_state",
            "degraded_reasons",
        }
    ),
    generation_validator=lambda value: type(value) is int and value > 0,
)


def _descriptor(**changes):
    value = {
        "plugin_id": SPEC.plugin_id,
        "instance_generation": 7,
        "api_family": "sample.family",
        "api_version": "sample.api.v1",
        "supported_task_versions": ["sample.task.v1"],
        "capabilities": ["sample.execute"],
        "lifecycle_state": "ready",
        "degraded_reasons": [],
    }
    value.update(changes)
    return value


def test_optional_plugin_spec_preserves_canonical_star_name() -> None:
    assert SPEC.star_name == SPEC.plugin_id


def test_capability_negotiation_accepts_exact_ready_descriptor() -> None:
    api = SimpleNamespace(capabilities=lambda: _descriptor())
    result = negotiate_capabilities(api, CONTRACT)
    assert (result.state, result.api, result.generation, result.reason) == (
        "current", api, 7, ""
    )


def test_capability_negotiation_maps_query_and_shape_errors() -> None:
    broken = SimpleNamespace(capabilities=lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert negotiate_capabilities(broken, CONTRACT).reason == "descriptor_query_failed"
    malformed = SimpleNamespace(capabilities=lambda: {**_descriptor(), "extra": True})
    assert negotiate_capabilities(malformed, CONTRACT).reason == "descriptor_malformed"


def test_capability_negotiation_distinguishes_missing_degraded_and_stale_generation() -> None:
    assert negotiate_capabilities(None, CONTRACT).reason == "sample_unavailable"
    degraded = SimpleNamespace(capabilities=lambda: _descriptor(degraded_reasons=["offline"]))
    assert negotiate_capabilities(degraded, CONTRACT).reason == "service_not_ready"
    stale = SimpleNamespace(capabilities=lambda: _descriptor(instance_generation=8))
    assert negotiate_capabilities(stale, CONTRACT, expected_generation=7).reason == "descriptor_incompatible"


def test_capability_negotiation_rejects_duplicate_sequence_values() -> None:
    api = SimpleNamespace(capabilities=lambda: _descriptor(capabilities=["sample.execute", "sample.execute"]))
    assert negotiate_capabilities(api, CONTRACT).reason == "descriptor_incompatible"
