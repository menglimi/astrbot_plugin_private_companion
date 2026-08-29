from __future__ import annotations

import asyncio
import importlib
import json
from pathlib import Path
import tempfile
import threading
import time

import pytest

from astrbot_plugin_private_companion.bot_personal_outbox import OUTBOX_STATES
from astrbot_plugin_private_companion.daily_state import DailyStateMixin
from astrbot_plugin_private_companion.helpers import _memory_archive_warning
from astrbot_plugin_private_companion.memory_companion_adapter import (
    MemoryCompanionAdapterMixin,
)
from astrbot_plugin_private_companion.state_views import StateViewsMixin
from astrbot_plugin_private_companion.storage import json_backend as json_backend_module
from astrbot_plugin_private_companion.storage import path_generation
from astrbot_plugin_private_companion.storage.json_backend import JsonStoreBackend


def _run(operation):
    return asyncio.run(operation)


class _AdapterHarness(MemoryCompanionAdapterMixin):
    def __init__(
        self,
        *,
        default_data: dict | None = None,
        profiles: dict[str, dict] | None = None,
        schema: int = 2,
        bridge=None,
    ) -> None:
        self._data_default = default_data if default_data is not None else {}
        self._persona_data_profiles = profiles if profiles is not None else {"alt": {}}
        self._active = "main"
        self.enable_multi_persona_mode = True
        self.plugin_specific_persona_id = "main"
        self._schema = schema
        self._bridge = bridge
        self._bridge_last_status = {}
        self.saved: list[tuple[str, str]] = []

    @property
    def data(self):
        return (
            self._data_default
            if self._active == "main"
            else self._persona_data_profiles[self._active]
        )

    def _effective_plugin_persona_id(self) -> str:
        return self._active

    def _primary_persona_id(self) -> str:
        return "main"

    def _persona_data_for_save(self, persona_id: str) -> dict:
        return (
            self._data_default
            if persona_id == "main"
            else self._persona_data_profiles[persona_id]
        )

    def _schedule_default_data_save(self, **_kwargs) -> None:
        self.saved.append(("default", self._active))

    def _schedule_persona_data_save(self, persona_id: str, **_kwargs) -> None:
        self.saved.append((persona_id, self._active))

    def _memory_companion_bridge(self):
        self._bridge_last_status = {
            "negotiated_canonical_schema_version": self._schema,
        }
        return self._bridge

    def _memory_companion_bot_personal_sender(self):
        return None

    def _memory_companion_bridge_bot_id(self, _event=None) -> str:
        return "bot-a"

    def _memory_companion_now_iso(self) -> str:
        return "2026-08-27T12:00:00+08:00"

    def _environment_now(self):
        from datetime import datetime

        return datetime.fromisoformat("2026-08-27T12:00:00+08:00")


@pytest.mark.parametrize("schema", [2, 3])
def test_persona_outboxes_bind_exact_backing_and_restart_state(schema: int) -> None:
    default: dict = {}
    profiles = {"alt": {}}
    harness = _AdapterHarness(default_data=default, profiles=profiles, schema=schema)

    first = _run(
        harness._memory_companion_record_bot_personal(
            memory_type="bot_daily_diary",
            payload={"date": "2026-08-27", "summary": "main"},
            idempotency_key="diary:2026-08-27",
        )
    )
    harness._active = "alt"
    second = _run(
        harness._memory_companion_record_bot_personal(
            memory_type="bot_daily_diary",
            payload={"date": "2026-08-27", "summary": "alt"},
            idempotency_key="diary:2026-08-27",
        )
    )

    assert first["state"] == second["state"] == "pending"
    assert len(default["bot_personal_outbox"]) == 1
    assert len(profiles["alt"]["bot_personal_outbox"]) == 1
    main_envelope = default["bot_personal_outbox"][0]["envelope"]
    alt_envelope = profiles["alt"]["bot_personal_outbox"][0]["envelope"]
    if schema == 3:
        assert (main_envelope["owner_bot_id"], main_envelope["persona_id"]) == (
            "bot-a",
            "main",
        )
        assert (alt_envelope["owner_bot_id"], alt_envelope["persona_id"]) == (
            "bot-a",
            "alt",
        )
    else:
        assert "owner_bot_id" not in main_envelope
        assert "persona_id" not in alt_envelope

    default["bot_personal_outbox"][0]["state"] = "sent"
    profiles["alt"]["bot_personal_outbox"][0]["state"] = "retry"
    alt_outbox = harness._memory_companion_outbox()
    harness.enable_multi_persona_mode = False
    harness._active = "main"
    alt_outbox._persist()
    assert harness.saved[-1][0] == "alt"

    restarted = _AdapterHarness(
        default_data=default,
        profiles=profiles,
        schema=schema,
    )
    assert restarted._memory_companion_outbox().status()["sent"] == 1
    restarted._active = "alt"
    assert restarted._memory_companion_outbox().status()["retry"] == 1


def _plan(activity: str, *, generated_at: str = "2026-08-27 10:00") -> dict:
    return {
        "date": "2026-08-27",
        "generated_at": generated_at,
        "source": "llm",
        "items": [
            {
                "time": "10:00",
                "end": "11:00",
                "activity": activity,
                "mood": "平静",
                "message_seed": "稍后聊聊",
            }
        ],
    }


def test_plan_and_diary_revisions_are_content_based_and_persistent() -> None:
    harness = _AdapterHarness()
    first = _plan("读书")
    _run(harness._memory_companion_record_daily_plan(first))
    identical = _plan("读书", generated_at="2026-08-27 10:30")
    _run(harness._memory_companion_record_daily_plan(identical))
    changed = _plan("散步")
    _run(harness._memory_companion_record_daily_plan(changed))

    entry = harness.data["bot_personal_outbox"][0]
    assert first["version"] == identical["version"] == 1
    assert changed["version"] == entry["version"] == 2
    assert entry["envelope"]["version"] == 2

    diary = {"date": "2026-08-27", "summary": "完成测试", "tags": ["测试"]}
    _run(harness._memory_companion_record_daily_diary(diary))
    same_diary = {
        **diary,
        "memory_archive": {"ok": False, "state": "retry"},
        "version": 999,
    }
    _run(harness._memory_companion_record_daily_diary(same_diary))
    revised_diary = {"date": "2026-08-27", "summary": "完成全部测试", "tags": ["测试"]}
    _run(harness._memory_companion_record_daily_diary(revised_diary))
    assert diary["version"] == same_diary["version"] == 1
    assert revised_diary["version"] == 2

    restarted = _AdapterHarness(default_data=harness._data_default, profiles={"alt": {}})
    after_restart = _plan("写总结")
    _run(restarted._memory_companion_record_daily_plan(after_restart))
    assert after_restart["version"] == 3


@pytest.mark.parametrize(
    "old_state",
    sorted(OUTBOX_STATES),
)
def test_higher_revision_supersedes_every_declared_outbox_state(old_state: str) -> None:
    harness = _AdapterHarness()
    first = _plan("旧内容")
    _run(harness._memory_companion_record_daily_plan(first))
    entry = harness.data["bot_personal_outbox"][0]
    entry.update(
        {
            "state": old_state,
            "attempts": 5,
            "last_error": "old-error",
            "sent_at": "old",
            "dead_letter_at": "old-dead-letter",
            "remote_record_id": "old-remote",
            "remote_version": 1,
        }
    )
    replacement = _plan("新内容")
    _run(harness._memory_companion_record_daily_plan(replacement))
    assert entry["state"] == "pending"
    assert entry["version"] == replacement["version"] == 2
    assert entry["attempts"] == 0
    assert entry["last_error"] == ""
    assert entry["sent_at"] == ""
    assert entry["dead_letter_at"] == ""
    assert entry["remote_record_id"] == ""
    assert entry["remote_version"] == 0
    assert entry["state"] in OUTBOX_STATES


def test_in_flight_old_revision_response_cannot_mutate_new_revision() -> None:
    async def scenario() -> None:
        harness = _AdapterHarness()
        first = _plan("旧内容")
        await harness._memory_companion_record_daily_plan(first)
        outbox = harness._memory_companion_outbox()
        assert outbox is not None
        started = asyncio.Event()
        release = asyncio.Event()

        async def delayed_sender(_envelope):
            started.set()
            await release.wait()
            return {
                "ok": True,
                "state": "sent",
                "record_id": "remote-old",
                "version": 1,
            }

        draining = asyncio.create_task(outbox.drain(delayed_sender, limit=1))
        await asyncio.wait_for(started.wait(), timeout=1.0)
        replacement = _plan("新内容")
        await harness._memory_companion_record_daily_plan(replacement)
        release.set()
        result = await asyncio.wait_for(draining, timeout=1.0)

        entry = harness.data["bot_personal_outbox"][0]
        assert result[0]["state"] == "superseded"
        assert entry["state"] == "pending"
        assert entry["version"] == replacement["version"] == 2
        assert entry["remote_record_id"] == ""
        assert entry["sent_at"] == ""

    _run(scenario())


def test_archive_failure_is_visible_in_plan_and_diary_command_text() -> None:
    archive = {"ok": False, "state": "retry", "error_code": "bridge_timeout"}
    warning = _memory_archive_warning({"memory_archive": archive})
    assert "Memory 归档尚未完成" in warning
    assert "bridge_timeout" in warning

    class DiaryView(StateViewsMixin):
        @staticmethod
        def _polish_diary_text(value, **_kwargs):
            return str(value or "")

    diary_text = DiaryView()._format_single_diary(
        {"date": "2026-08-27", "body": "正文", "memory_archive": archive}
    )
    assert "Memory 归档尚未完成" in diary_text

    class PlanView:
        data = {}
        bot_name = "小星"

        @staticmethod
        def _plan_item_display_status(_plan, _item, _index):
            return "planned"

    plan_text = DailyStateMixin._format_daily_plan(
        PlanView(),
        {**_plan("读书"), "memory_archive": archive},
    )
    assert "Memory 归档尚未完成" in plan_text


def test_profile_read_passes_real_producer_capability_and_degrades_without_it() -> None:
    token = object()

    class Bridge:
        def __init__(self, *, capability=True):
            self.capability = capability
            self.kwargs = None

        def register_emotion_producer(self, _producer):
            return token if self.capability else None

        def read_bot_profile(self, _profile, **kwargs):
            self.kwargs = kwargs
            return {"ok": True, "state": "ready", "items": []}

    bridge = Bridge()
    harness = _AdapterHarness(bridge=bridge)
    result = _run(
        harness._memory_companion_read_profile(
            "bot_creative",
            authorized=True,
        )
    )
    assert result["ok"] is True
    assert bridge.kwargs["producer_capability"] is token

    unavailable = Bridge(capability=False)
    degraded = _run(
        _AdapterHarness(bridge=unavailable)._memory_companion_read_profile(
            "bot_creative",
            authorized=True,
        )
    )
    assert degraded["ok"] is False
    assert degraded["error_code"] == "producer_capability_unavailable"
    assert unavailable.kwargs is None


def test_reload_stable_generation_fence_rejects_writer_blocked_over_three_seconds(
    monkeypatch,
) -> None:
    with tempfile.TemporaryDirectory() as temporary:
        target = Path(temporary) / "companions.json"
        old = JsonStoreBackend(
            target,
            lambda value: value,
            dict,
            persistence_owner_token="old-owner",
        )
        path_generation.activate_persistence_owner("old-owner", [target])
        started = threading.Event()
        release = threading.Event()
        original_dump = json_backend_module.json.dump

        def blocked_dump(value, stream, *args, **kwargs):
            if value.get("marker") == "old":
                started.set()
                assert release.wait(timeout=10.0)
            return original_dump(value, stream, *args, **kwargs)

        monkeypatch.setattr(json_backend_module.json, "dump", blocked_dump)
        thread = threading.Thread(target=old.save_store, args=({"marker": "old"},))
        thread.start()
        assert started.wait(timeout=2.0)
        time.sleep(3.05)

        reloaded_registry = importlib.reload(path_generation)
        reloaded_registry.activate_persistence_owner("new-owner", [target])
        new = JsonStoreBackend(
            target,
            lambda value: value,
            dict,
            persistence_owner_token="new-owner",
        )
        new.save_store({"marker": "new"})
        release.set()
        thread.join(timeout=5.0)

        assert not thread.is_alive()
        assert json.loads(target.read_text(encoding="utf-8")) == {"marker": "new"}
        assert new.last_write_status["state"] == "saved"
        assert old.last_write_status["state"] == "superseded"


def test_same_generation_sequence_prevents_late_old_snapshot_overwrite(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as temporary:
        target = Path(temporary) / "companions.json"
        backend = JsonStoreBackend(
            target,
            lambda value: value,
            dict,
            persistence_owner_token="one-owner",
        )
        path_generation.activate_persistence_owner("one-owner", [target])
        started = threading.Event()
        release = threading.Event()
        original_dump = json_backend_module.json.dump

        def blocked_dump(value, stream, *args, **kwargs):
            if value.get("marker") == "older":
                started.set()
                assert release.wait(timeout=5.0)
            return original_dump(value, stream, *args, **kwargs)

        monkeypatch.setattr(json_backend_module.json, "dump", blocked_dump)
        thread = threading.Thread(target=backend.save_store, args=({"marker": "older"},))
        thread.start()
        assert started.wait(timeout=2.0)
        backend.save_store({"marker": "newer"})
        release.set()
        thread.join(timeout=5.0)

        assert json.loads(target.read_text(encoding="utf-8")) == {"marker": "newer"}
