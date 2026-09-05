from __future__ import annotations

import json

import pytest

from companion.injection import (
    ActionRequest,
    ActionResult,
    CapabilityConflictError,
    CapabilityDescriptor,
    CapabilityRegistry,
    CompanionEvent,
    ContextContribution,
    Evidence,
    ExtensionManifest,
    ExtensionRegistry,
    ExtensionStatus,
    InjectionProtocolError,
    Observation,
    RuntimeScope,
    Scope,
)


def _scope() -> Scope:
    return Scope(platform="aiocqhttp", user_id="u-1", persona_id="default")


def _descriptor(version: str = "1.0", provider: str = "example.health") -> CapabilityDescriptor:
    return CapabilityDescriptor(
        id="health.activity.read",
        kind="observe",
        version=version,
        provider=provider,
        scopes=("user",),
    )


def test_dtos_are_json_safe_and_nested_values_are_immutable() -> None:
    observation = Observation(
        type="health.activity",
        scope=_scope(),
        value={"steps": [1, 2], "meta": {"source": "watch"}},
        evidence=Evidence(source="health", evidence_kind="device_observed"),
    )

    assert json.loads(json.dumps(observation.to_dict()))["value"]["steps"] == [1, 2]
    with pytest.raises(TypeError):
        observation.value["steps"] = ()  # type: ignore[index]


def test_registry_resolves_highest_compatible_version() -> None:
    registry = CapabilityRegistry()
    registry.register(_descriptor("1.0"))
    registry.register(_descriptor("1.2", provider="example.health.v12"))
    registry.register(_descriptor("2.0", provider="example.health.v2"))

    assert registry.resolve("health.activity.read@1").version == "1.2"
    assert registry.resolve("health.activity.read@1.1").version == "1.2"
    assert registry.resolve("health.activity.read").version == "2.0"
    assert registry.resolve("health.activity.read@3") is None


def test_registry_rejects_same_key_from_different_provider() -> None:
    registry = CapabilityRegistry()
    registry.register(_descriptor())
    with pytest.raises(CapabilityConflictError):
        registry.register(_descriptor(provider="another.health"))


def test_dtos_validate_scope_and_action_status() -> None:
    with pytest.raises(InjectionProtocolError):
        Scope()
    with pytest.raises(InjectionProtocolError):
        ActionResult(status="made_up", action="device.light.set")

    request = ActionRequest(
        action="device.light.set",
        scope=_scope(),
        arguments={"power": "on"},
        idempotency_key="light:u-1:on",
    )
    event = CompanionEvent(
        id="evt-1",
        type="device.light.changed",
        scope=_scope(),
        occurred_at="2026-09-05T21:00:00+08:00",
        payload={"power": "on"},
    )
    contribution = ContextContribution(
        lane="scene",
        key="light_state",
        content="卧室灯已打开。",
        evidence="device_observed",
    )
    assert request.to_dict()["arguments"]["power"] == "on"
    assert event.to_dict()["type"] == "device.light.changed"
    assert contribution.to_dict()["lane"] == "scene"


def test_extension_registry_exposes_control_plane_metadata() -> None:
    descriptor = CapabilityDescriptor(
        id="device.light.set",
        kind="execute",
        version="1.0",
        provider="example.smart_home",
        permissions=("device.light.write",),
        side_effect="external_device",
    )
    registry = ExtensionRegistry()
    registry.register(
        ExtensionManifest(
            id="example.smart_home",
            version="2.1",
            sdk_version="0.1",
            display_name="Smart Home",
            capabilities=(descriptor,),
            permissions=("device.light.write",),
            ui_modules=("devices",),
        )
    )
    registry.set_status(
        ExtensionStatus(
            id="example.smart_home",
            state="degraded",
            reason="device bridge offline",
            capability_states={"device.light.set": "unavailable"},
            task_count=1,
        )
    )

    snapshot = registry.snapshot()
    assert snapshot[0]["manifest"]["id"] == "example.smart_home"
    assert snapshot[0]["status"]["state"] == "degraded"
    assert registry.capabilities.resolve("device.light.set@1").provider == "example.smart_home"


def test_runtime_scope_is_complete_and_stable() -> None:
    scope = RuntimeScope.from_dict(
        {
            "installation_id": "install-1",
            "bot_id": "bot-a",
            "platform": "aiocqhttp",
            "account_id": "account-a",
            "persona_id": "persona-a",
            "conversation_id": "conversation-1",
            "session_id": "session-1",
            "user_id": "user-1",
            "persona_binding_revision": 3,
        }
    )
    assert scope.scope_key == RuntimeScope.from_dict(scope.to_dict()).scope_key
    assert scope.to_scope().scope_key == scope.scope_key
    with pytest.raises(InjectionProtocolError):
        RuntimeScope.from_dict({"installation_id": "install-1"})


def test_extension_registry_replaces_capabilities_atomically() -> None:
    registry = ExtensionRegistry()
    first = ExtensionManifest(
        id="example.extension",
        version="1.0",
        sdk_version="0.1",
        capabilities=(
            CapabilityDescriptor(
                id="example.read",
                kind="observe",
                version="1.0",
                provider="example.extension",
            ),
        ),
    )
    registry.register(first)
    replacement = ExtensionManifest(
        id="example.extension",
        version="1.0",
        sdk_version="0.1",
        capabilities=(
            CapabilityDescriptor(
                id="example.write",
                kind="execute",
                version="1.0",
                provider="example.extension",
            ),
        ),
    )
    registry.register(replacement)
    assert registry.capabilities.resolve("example.read@1") is None
    assert registry.capabilities.resolve("example.write@1") is not None
    assert registry.self_check() == ()


def test_manifest_rejects_capability_owned_by_another_extension() -> None:
    with pytest.raises(InjectionProtocolError):
        ExtensionRegistry().register(
            ExtensionManifest(
                id="example.extension",
                version="1.0",
                sdk_version="0.1",
                capabilities=(
                    CapabilityDescriptor(
                        id="example.read",
                        kind="observe",
                        version="1.0",
                        provider="another.extension",
                    ),
                ),
            )
        )


def test_wire_decoding_reports_protocol_errors_for_malformed_payloads() -> None:
    with pytest.raises(InjectionProtocolError):
        CapabilityDescriptor.from_dict({"id": "health.read"})
    with pytest.raises(InjectionProtocolError):
        ExtensionManifest.from_dict({"id": "example.extension", "version": "1.0", "sdk_version": "0.1", "capabilities": 1})
    with pytest.raises(InjectionProtocolError):
        ExtensionStatus.from_dict({"id": "example.extension", "task_count": "many"})


def test_context_contribution_supports_scope_provenance_and_wire_roundtrip() -> None:
    runtime_scope = RuntimeScope(
        installation_id="install-1",
        bot_id="bot-a",
        platform="aiocqhttp",
        account_id="account-a",
        persona_id="persona-a",
        conversation_id="conversation-1",
        user_id="user-1",
    )
    contribution = ContextContribution.from_dict(
        {
            "lane": "affect",
            "key": "temporary_emotion",
            "content": "当前情绪有余波。",
            "evidence": "derived",
            "scope": runtime_scope.to_dict(),
            "source": "temp_emotion",
            "source_refs": ["emotion:user-1"],
            "revision": "42",
            "trace_id": "trace-1",
        }
    )

    assert contribution.scope is not None
    assert contribution.scope.scope_key == runtime_scope.scope_key
    wire = contribution.to_dict()
    assert wire["source_refs"] == ["emotion:user-1"]
    assert wire["trace_id"] == "trace-1"


def test_context_contribution_rejects_malformed_scope() -> None:
    with pytest.raises(InjectionProtocolError):
        ContextContribution.from_dict(
            {
                "lane": "scene",
                "key": "bad_scope",
                "content": "x",
                "evidence": "derived",
                "scope": {"installation_id": "only-one-field"},
            }
        )
