from __future__ import annotations

import asyncio
import importlib
from pathlib import Path

import pytest
import sys
import tempfile
import types
import unittest
from types import SimpleNamespace


COMPANION_ROOT = Path(__file__).resolve().parents[1]


from external_memory_dependency import resolve_memory_plugin_root


_memory_resolution = resolve_memory_plugin_root(COMPANION_ROOT)
if _memory_resolution.root is None:
    pytest.skip(_memory_resolution.detail, allow_module_level=True)
MEMORY_ROOT = _memory_resolution.root


def _load_modules():
    try:
        import astrbot.api  # noqa: F401
    except ImportError:
        astrbot = types.ModuleType("astrbot")
        api = types.ModuleType("astrbot.api")
        api.logger = types.SimpleNamespace(
            warning=lambda *args, **kwargs: None,
            debug=lambda *args, **kwargs: None,
        )
        sys.modules["astrbot"] = astrbot
        sys.modules["astrbot.api"] = api

    companion_name = "c4_dual_companion"
    companion = types.ModuleType(companion_name)
    companion.__path__ = [str(COMPANION_ROOT)]
    sys.modules[companion_name] = companion
    adapter = importlib.import_module(f"{companion_name}.memory_companion_adapter")

    memory_name = "c4_dual_memory"
    memory = types.ModuleType(memory_name)
    memory.__path__ = [str(MEMORY_ROOT)]
    sys.modules[memory_name] = memory
    core = types.ModuleType(f"{memory_name}.core")
    core.__path__ = [str(MEMORY_ROOT / "core")]
    sys.modules[core.__name__] = core
    bridge = importlib.import_module(f"{memory_name}.core.bridge")
    service = importlib.import_module(f"{memory_name}.core.service")
    store = importlib.import_module(f"{memory_name}.core.store")
    return adapter.MemoryCompanionAdapterMixin, bridge.MemoryCompanionBridge, service.MemoryCompanionService, store.MemoryStore


def test_companion_profile_facade_reads_memory_profile_without_raw_payload_leak():
    mixin, bridge_type, service_type, store_type = _load_modules()

    class Companion(mixin):
        def __init__(self, bridge):
            self.enable_memory_companion_bridge = True
            self.bridge = bridge

        def _memory_companion_bridge_uncached(self):
            return self.bridge

        @staticmethod
        def _memory_companion_bridge_bot_id():
            return "bot-c4"

        @staticmethod
        def _memory_companion_archive_persona_id():
            return "persona-c4"

    with tempfile.TemporaryDirectory() as temporary:
        service = object.__new__(service_type)
        service.store = store_type(Path(temporary) / "memory.db")
        service.store.initialize()
        service._schedule_memory_embedding = lambda *args, **kwargs: None
        companion = Companion(None)
        service.context = SimpleNamespace(get_all_stars=lambda: [SimpleNamespace(
            star_cls=companion, star_cls_type=type(companion), activated=True,
            root_dir_name="astrbot_plugin_private_companion", name="陪伴插件",
        )])
        bridge = bridge_type(service)
        companion.bridge = bridge
        capability = bridge.register_private_companion(companion)

        async def run():
            archived = await bridge.record_bot_personal_archive({
                "canonical_schema_version": 3,
                "owner_bot_id": "bot-c4",
                "persona_id": "persona-c4",
                "memory_type": "bot_creative_work",
                "date": "2026-07-30",
                "window": "afternoon",
                "occurred_at": "2026-07-30T15:00:00+08:00",
                "source_refs": ["companion:c4:creative"],
                "idempotency_key": "c4:dual:creative",
                "payload": {"summary": "raw creative detail must remain internal"},
            }, producer_capability=capability)
            result = await companion._memory_companion_read_profile(
                "bot_creative",
                query="creative",
                current_date="2026-07-30",
                current_window="afternoon",
            )
            locked = await companion._memory_companion_read_profile("locked_frame_personal")
            return archived, result, locked

        try:
            archived, result, locked = asyncio.run(run())
            assert archived["ok"] is True
            assert result["ok"] is True and result["read_only"] is True
            assert len(result["items"]) == 1
            assert all("payload" not in item and "content" not in item and "evidence" not in item for item in result["items"])
            assert "raw creative detail" not in str(result)
            assert locked["ok"] is True and locked["read_only"] is True
            assert locked["state"] == "ready"
        finally:
            service.store.close()
