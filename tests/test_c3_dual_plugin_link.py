from __future__ import annotations

import asyncio
import importlib
import os
from pathlib import Path
import sys
import tempfile
import types
from types import SimpleNamespace


COMPANION_ROOT = Path(__file__).resolve().parents[1]
_MEMORY_ROOT_CANDIDATES = (
    Path(os.environ["ASTRBOT_MEMORY_PLUGIN_ROOT"])
    if os.environ.get("ASTRBOT_MEMORY_PLUGIN_ROOT")
    else COMPANION_ROOT / ".missing-memory-root",
    COMPANION_ROOT.parent / "memory",
    COMPANION_ROOT.parents[1] / "astrbot_plugin_memory_companion-main",
    COMPANION_ROOT.parent / "astrbot_plugin_remember_you",
)
MEMORY_ROOT = next((path for path in _MEMORY_ROOT_CANDIDATES if path.is_dir()), _MEMORY_ROOT_CANDIDATES[0])


def _load_companion_package():
    name = "c3_dual_companion"
    package = types.ModuleType(name)
    package.__path__ = [str(COMPANION_ROOT)]
    sys.modules[name] = package
    return importlib.import_module(f"{name}.bot_personal_contract"), importlib.import_module(f"{name}.bot_personal_outbox")


def _load_memory_package():
    name = "c3_dual_memory"
    package = types.ModuleType(name)
    package.__path__ = [str(MEMORY_ROOT)]
    sys.modules[name] = package
    core = types.ModuleType(f"{name}.core")
    core.__path__ = [str(MEMORY_ROOT / "core")]
    sys.modules[core.__name__] = core
    bridge = importlib.import_module(f"{name}.core.bridge")
    service = importlib.import_module(f"{name}.core.service")
    store = importlib.import_module(f"{name}.core.store")
    contract = importlib.import_module(f"{name}.core.bot_personal_contract")
    return bridge, service, store, contract


def test_chat_outbox_delivers_structured_archive_to_memory_bridge_without_domain_leak():
    companion_contract, outbox_module = _load_companion_package()
    bridge_module, service_module, store_module, memory_contract = _load_memory_package()
    assert companion_contract.CONTRACT_FINGERPRINT == memory_contract.CONTRACT_FINGERPRINT

    with tempfile.TemporaryDirectory() as temporary:
        service = object.__new__(service_module.MemoryCompanionService)
        service.store = store_module.MemoryStore(Path(temporary) / "memory.db")
        service.store.initialize()
        service._schedule_memory_embedding = lambda *args, **kwargs: None
        producer = type("PrivateCompanionProducer", (), {})()
        producer._memory_companion_bridge_bot_id = lambda: "bot-1"
        producer._memory_companion_archive_persona_id = lambda: "persona-main"
        service.context = SimpleNamespace(get_all_stars=lambda: [SimpleNamespace(
            star_cls=producer, star_cls_type=type(producer), activated=True,
            root_dir_name="astrbot_plugin_private_companion", name="陪伴插件",
        )])
        bridge = bridge_module.MemoryCompanionBridge(service)
        capability = bridge.register_private_companion(producer)
        outbox = outbox_module.BotPersonalOutbox({})

        async def run():
            payload = {
                "date": "2026-07-30",
                "window": "evening",
                "summary": "C3 dual plugin archive",
                "items": ["local first", "bridge second"],
            }
            first = await outbox.enqueue(
                memory_type="bot_schedule_plan",
                payload=payload,
                idempotency_key="daily_plan:2026-07-30",
                occurred_at="2026-07-30T19:00:00+08:00",
                owner_bot_id="bot-1",
                persona_id="persona-main",
                canonical_schema_version=3,
                sender=lambda envelope: bridge.record_bot_personal_archive(
                    envelope, producer_capability=capability,
                ),
            )
            duplicate = await outbox.enqueue(
                memory_type="bot_schedule_plan",
                payload=payload,
                idempotency_key="daily_plan:2026-07-30",
                occurred_at="2026-07-30T19:00:00+08:00",
                owner_bot_id="bot-1",
                persona_id="persona-main",
                canonical_schema_version=3,
                sender=lambda envelope: bridge.record_bot_personal_archive(
                    envelope, producer_capability=capability,
                ),
            )
            revised_payload = {
                **payload,
                "summary": "C3 dual plugin archive revised",
                "items": ["local revised", "bridge revised"],
            }
            revised = await outbox.enqueue(
                memory_type="bot_schedule_plan",
                payload=revised_payload,
                idempotency_key="daily_plan:2026-07-30",
                occurred_at="2026-07-30T19:30:00+08:00",
                owner_bot_id="bot-1",
                persona_id="persona-main",
                canonical_schema_version=3,
                version=2,
                sender=lambda envelope: bridge.record_bot_personal_archive(
                    envelope, producer_capability=capability,
                ),
            )
            stored = await service.store.get_memory(revised["record_id"])
            profile = await service.read_bot_personal_profile(
                limit=10,
                owner_bot_id="bot-1",
                persona_id="persona-main",
            )
            return (
                first,
                duplicate,
                revised,
                revised_payload,
                outbox.entries[0],
                stored,
                profile,
            )

        try:
            first, duplicate, revised, local, queued, stored, profile = asyncio.run(run())
            assert first["ok"] and first["state"] == "sent", first.get("error_code")
            assert duplicate["deduplicated"] is True
            assert revised["ok"] and revised["state"] == "sent", revised.get("error_code")
            assert revised["record_id"] == first["record_id"]
            assert queued["state"] == "sent"
            assert queued["version"] == queued["envelope"]["version"] == 2
            assert queued["envelope"]["payload"]["summary"] == local["summary"]
            assert stored is not None
            assert stored.metadata["version"] == 2
            assert stored.metadata["payload"]["summary"] == local["summary"]
            assert profile["read_only"] is True
            assert len(profile["items"]) == 1
            assert profile["items"][0]["record_id"] == revised["record_id"]
            assert profile["items"][0]["version"] == 2
            assert all("payload" not in item and "content" not in item for item in profile["items"])
        finally:
            service.store.close()
