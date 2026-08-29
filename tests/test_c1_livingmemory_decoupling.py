from __future__ import annotations

import asyncio
import importlib.util
import sys
import time
import types
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]

try:
    import astrbot.api  # noqa: F401
except ImportError:
    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")
    api.logger = types.SimpleNamespace(warning=lambda *args, **kwargs: None, debug=lambda *args, **kwargs: None)
    sys.modules["astrbot"] = astrbot
    sys.modules["astrbot.api"] = api


def _load_adapter():
    package_name = "c1_private_companion"
    package = types.ModuleType(package_name)
    package.__path__ = [str(ROOT)]
    sys.modules[package_name] = package
    spec = importlib.util.spec_from_file_location(
        f"{package_name}.memory_companion_adapter",
        ROOT / "memory_companion_adapter.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.MemoryCompanionAdapterMixin


MemoryCompanionAdapterMixin = _load_adapter()


def _load_contract():
    spec = importlib.util.spec_from_file_location("c1_companion_contract", ROOT / "bot_personal_contract.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CONTRACT = _load_contract()


class _ProbeBridge:
    def probe_bot_personal_memory_capabilities(self):
        result = CONTRACT.capability_descriptor()
        result.update({"available": True, "state": "ready", "degraded": False})
        return result


class _Plugin(MemoryCompanionAdapterMixin):
    def __init__(self, bridge=True, livingmemory=True, bridge_object=None):
        self.enable_memory_companion_bridge = bridge
        self.enable_livingmemory_integration = livingmemory
        self.bridge_object = _ProbeBridge() if bridge_object is True else bridge_object

    def _memory_companion_bridge_uncached(self):
        return self.bridge_object


def test_bridge_and_livingmemory_switches_are_orthogonal():
    bridge = _ProbeBridge()
    assert _Plugin(True, True, bridge)._memory_companion_bridge() is bridge
    assert _Plugin(True, False, bridge)._memory_companion_bridge() is bridge
    assert _Plugin(False, True, bridge)._memory_companion_bridge() is None
    assert _Plugin(False, False, bridge)._memory_companion_bridge() is None


def test_missing_bridge_is_observable_local_only():
    plugin = _Plugin(True, False, None)
    status = plugin._memory_companion_coordination_status()
    assert status["available"] is False
    assert status["state"] == "degraded"
    assert status["reason"] == "bridge_missing"


def test_missing_or_mismatched_capability_probe_is_degraded_without_remote_use():
    missing = _Plugin(True, False, object())
    assert missing._memory_companion_bridge() is None
    assert missing._memory_companion_coordination_status()["reason"] == "capability_probe_missing"

    class _MismatchedBridge(_ProbeBridge):
        def probe_bot_personal_memory_capabilities(self):
            result = super().probe_bot_personal_memory_capabilities()
            result["contract_fingerprint"] = "wrong"
            return result

    mismatched = _Plugin(True, False, _MismatchedBridge())
    assert mismatched._memory_companion_bridge() is None
    status = mismatched._memory_companion_coordination_status()
    assert status["reason"] == "capability_contract_mismatch"
    assert "contract_fingerprint" in status["mismatches"]


def test_capability_probe_allows_backward_compatible_superset():
    class _SupersetBridge(_ProbeBridge):
        def probe_bot_personal_memory_capabilities(self):
            result = super().probe_bot_personal_memory_capabilities()
            result["windows"] = [*result["windows"], "midday_extended"]
            result["memory_types"] = [*result["memory_types"], "bot_new_optional_type"]
            result["contract_fingerprint"] = "future-compatible-superset"
            return result

    plugin = _Plugin(True, False, _SupersetBridge())
    bridge = plugin._memory_companion_bridge()
    assert bridge is not None
    status = plugin._bridge_last_status
    assert status["state"] == "ready_compatible"
    assert status["contract_compatibility"] == "superset"


def test_capability_probe_allows_known_memory_v2_contract():
    class _LegacyV2Bridge(_ProbeBridge):
        def probe_bot_personal_memory_capabilities(self):
            result = super().probe_bot_personal_memory_capabilities()
            result.update(
                {
                    "contract_fingerprint": "0ffe3a1ab69b659c",
                    "contract_revision": 2,
                    "capability_schema_version": "1.2",
                    "canonical_schema_version": 2,
                    "payload_schema_version": "1.0",
                }
            )
            return result

    plugin = _Plugin(True, False, _LegacyV2Bridge())
    assert plugin._memory_companion_bridge() is not None
    status = plugin._bridge_last_status
    assert status["state"] == "ready_compatible"
    assert status["contract_compatibility"] == "legacy_v2"
    assert status["negotiated_canonical_schema_version"] == 2


def test_bot_personal_outbox_uses_negotiated_schema_and_namespace():
    capability = object()

    class _CaptureBridge(_ProbeBridge):
        def __init__(self):
            self.envelope = None

        @staticmethod
        def register_emotion_producer(_producer):
            return capability

        async def record_bot_personal_archive(self, envelope, *, producer_capability=None):
            assert producer_capability is capability
            self.envelope = envelope
            return {
                "ok": True,
                "state": "sent",
                "record_id": envelope["record_id"],
                "version": envelope["version"],
            }

    bridge = _CaptureBridge()
    plugin = _Plugin(True, False, bridge)
    plugin.data = {}
    plugin.bot_self_id = "bot-1"
    plugin.plugin_specific_persona_id = "persona-main"
    plugin._schedule_data_save = lambda **_kwargs: None

    result = asyncio.run(
        plugin._memory_companion_record_bot_personal(
            memory_type="bot_daily_diary",
            payload={"date": "2026-08-17", "summary": "v3 bridge"},
            idempotency_key="diary:2026-08-17",
            occurred_at="2026-08-17T21:00:00+08:00",
        )
    )

    assert result["ok"] is True
    assert bridge.envelope["canonical_schema_version"] == 3
    assert bridge.envelope["owner_bot_id"] == "bot-1"
    assert bridge.envelope["persona_id"] == "persona-main"


def test_bot_personal_sender_passes_registered_producer_capability():
    capability = object()

    class _CapabilityBridge(_ProbeBridge):
        def __init__(self):
            self.received_capability = None

        def register_emotion_producer(self, _producer):
            return capability

        async def record_bot_personal_archive(self, envelope, *, producer_capability=None):
            self.received_capability = producer_capability
            return {"ok": True, "state": "stored", "record_id": envelope["idempotency_key"]}

    bridge = _CapabilityBridge()
    sender = _Plugin(True, False, bridge)._memory_companion_bot_personal_sender()

    assert callable(sender)
    result = asyncio.run(sender({"idempotency_key": "c1:sender-capability"}))
    assert result["ok"] is True
    assert bridge.received_capability is capability


def test_bot_personal_sender_is_unavailable_without_producer_capability():
    class _NoCapabilityBridge(_ProbeBridge):
        async def record_bot_personal_archive(self, _envelope, *, producer_capability=None):
            raise AssertionError("recorder must not be used without a producer capability")

    sender = _Plugin(True, False, _NoCapabilityBridge())._memory_companion_bot_personal_sender()

    assert sender is None


def test_prefixed_or_livingmemory_modules_do_not_drive_bridge(monkeypatch):
    module = types.ModuleType("third_party_livingmemory_prefix")
    module.PLUGIN_NAME = "astrbot_plugin_memory_companion_extra"
    module.get_active_bridge = lambda: object()
    monkeypatch.setitem(sys.modules, module.__name__, module)
    plugin = _Plugin(True, False, None)
    assert MemoryCompanionAdapterMixin._memory_companion_bridge_uncached(plugin) is None


def test_non_module_registry_proxy_is_ignored_without_dynamic_attribute_lookup():
    class _TorchClassProxy:
        def __getattr__(self, name):
            raise RuntimeError(f"Tried to instantiate class 'PLUGIN_NAME.{name}'")

    assert MemoryCompanionAdapterMixin._memory_companion_module_matches(_TorchClassProxy()) is False


def test_bridge_discovery_uses_astrbot_registered_plugin_instance():
    bridge = _ProbeBridge()
    module = types.ModuleType("data.plugins.custom_memory_folder.main")
    metadata = SimpleNamespace(
        name="astrbot_plugin_memory_companion",
        display_name="我会牢牢记住你",
        root_dir_name="custom_memory_folder",
        module_path=module.__name__,
        module=module,
        activated=True,
        star_cls=SimpleNamespace(memory_companion=bridge),
        version="1.7.1",
    )
    plugin = _Plugin(True, False, None)
    plugin.context = SimpleNamespace(get_all_stars=lambda: [metadata])

    assert MemoryCompanionAdapterMixin._memory_companion_bridge_uncached(plugin) is bridge
    presence = plugin._memory_companion_presence()
    assert presence["detected"] is True
    assert presence["loaded"] is True
    assert presence["display_name"] == "我会牢牢记住你"


def test_registered_plugin_wins_over_stale_module_alias(monkeypatch):
    active_bridge = _ProbeBridge()
    stale_bridge = object()
    stale_module = types.ModuleType("data.plugins.astrbot_plugin_memory_companion.main")
    stale_module.PLUGIN_NAME = "astrbot_plugin_memory_companion"
    stale_module.get_active_bridge = lambda: stale_bridge
    monkeypatch.setitem(sys.modules, stale_module.__name__, stale_module)

    metadata = SimpleNamespace(
        name="astrbot_plugin_memory_companion",
        display_name="我会牢牢记住你",
        root_dir_name="astrbot_plugin_remember_you",
        module_path=stale_module.__name__,
        module=stale_module,
        activated=True,
        star_cls=SimpleNamespace(memory_companion=active_bridge),
        version="1.7.3",
    )
    plugin = _Plugin(True, False, None)
    plugin.context = SimpleNamespace(get_all_stars=lambda: [metadata])

    assert MemoryCompanionAdapterMixin._memory_companion_bridge_uncached(plugin) is active_bridge


def test_registered_absence_never_resurrects_stale_memory_module(monkeypatch):
    stale_bridge = _ProbeBridge()
    module_name = "data.plugins.astrbot_plugin_memory_companion.main"
    stale_module = types.ModuleType(module_name)
    stale_module.PLUGIN_NAME = "astrbot_plugin_memory_companion"
    stale_module.get_active_bridge = lambda: stale_bridge
    monkeypatch.setitem(sys.modules, module_name, stale_module)
    plugin = _Plugin(True, False, None)
    plugin.context = SimpleNamespace(
        get_all_stars=lambda: [],
        get_registered_star=lambda _name: None,
    )

    assert MemoryCompanionAdapterMixin._memory_companion_bridge_uncached(plugin) is None


def test_legacy_public_bridge_getter_is_supported(monkeypatch):
    bridge = _ProbeBridge()
    module_name = "data.plugins.astrbot_plugin_memory_companion.main"
    module = types.ModuleType(module_name)
    module.PLUGIN_NAME = "astrbot_plugin_memory_companion"
    module.get_memory_companion_bridge = lambda: bridge
    monkeypatch.setitem(sys.modules, module_name, module)
    plugin = _Plugin(True, False, None)

    assert MemoryCompanionAdapterMixin._memory_companion_bridge_uncached(plugin) is bridge


def test_missing_bridge_negative_cache_retries_quickly():
    bridge = _ProbeBridge()

    class _LatePlugin(_Plugin):
        def __init__(self):
            super().__init__(True, False, None)
            self.calls = 0

        def _memory_companion_bridge_uncached(self):
            self.calls += 1
            return bridge if self.calls >= 2 else None

    plugin = _LatePlugin()
    assert plugin._memory_companion_bridge() is None
    assert plugin._memory_companion_bridge() is None
    assert plugin.calls == 1

    plugin._bridge_cache_ts = time.monotonic() - plugin._BRIDGE_MISSING_CACHE_TTL - 0.1
    assert plugin._memory_companion_bridge() is bridge
    assert plugin.calls == 2


def test_inactive_cached_bridge_is_replaced_immediately():
    class _LifecycleBridge(_ProbeBridge):
        def __init__(self, active=True):
            self.active = active

        def bridge_lifecycle_status(self):
            return {"active": self.active}

    old_bridge = _LifecycleBridge(active=False)
    new_bridge = _LifecycleBridge(active=True)
    plugin = _Plugin(True, False, new_bridge)
    plugin._bridge_cache = old_bridge
    plugin._bridge_cache_ts = time.monotonic()
    plugin._memory_companion_emotion_capability_bridge = old_bridge
    plugin._memory_companion_emotion_producer_capability_cache = object()

    assert plugin._memory_companion_bridge() is new_bridge
    assert plugin._memory_companion_emotion_capability_bridge is None
    assert plugin._memory_companion_emotion_producer_capability_cache is None


def test_inactive_discovered_bridge_fails_closed():
    class _InactiveBridge(_ProbeBridge):
        @staticmethod
        def bridge_lifecycle_status():
            return {"active": False}

    plugin = _Plugin(True, False, _InactiveBridge())
    assert plugin._memory_companion_bridge() is None
    assert plugin._memory_companion_coordination_status()["reason"] == "bridge_inactive"


def test_legacy_livingmemory_migration_entrypoint_remains():
    assert (ROOT / "integration_status.py").exists()
