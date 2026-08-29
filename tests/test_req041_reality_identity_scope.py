# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

from astrbot_plugin_private_companion.place_cognitive_map import PlaceCognitiveMapMixin
from astrbot_plugin_private_companion.reality_companion_bridge import RealityCompanionBridgeMixin


PERSON_KEY = "person:" + "a" * 64


def _location(name: str, kind: str = "custom") -> dict:
    return {
        "available": True,
        "place": {"matched": True, "name": name, "kind": kind, "radius_m": 120},
    }


class _ScopedPlaceHost(PlaceCognitiveMapMixin):
    def __init__(self, *, ready: bool = True) -> None:
        self.ready = ready
        self.data: dict = {"place_cognitive_maps": {}}
        self.scoped_writes: list[dict] = []
        self.legacy_writes = 0
        self.saved = 0
        self.namespace = SimpleNamespace(kind="private")

    def _req041_reality_private_binding(self, _user_id: str, **_kwargs):
        if not self.ready:
            return {"ok": False, "code": "scoped_projection_not_reconciled"}
        return {
            "ok": True,
            "store_key": PERSON_KEY,
            "subject_ref": PERSON_KEY,
            "context": self.namespace,
        }

    def _schedule_data_save(self, **_kwargs) -> None:
        self.saved += 1

    @staticmethod
    def _memory_companion_bridge():
        return object()

    def _memory_companion_upsert_scoped_record(self, _bridge, namespace, **kwargs):
        self.scoped_writes.append({"namespace": namespace, **kwargs})
        return {"ok": True, "code": "created"}

    async def _memory_companion_record_observed_activity(self, _event):
        self.legacy_writes += 1


def test_linked_device_ids_share_one_opaque_place_map_and_scoped_memory() -> None:
    host = _ScopedPlaceHost()

    host._observe_mobile_place_context("device-a", _location("家", "home"), observed_at=100)
    result = host._observe_mobile_place_context("device-b", _location("公司", "work"), observed_at=200)

    assert set(host.data["place_cognitive_maps"]) == {PERSON_KEY}
    assert result["recent_routes"] == [{"from_name": "家", "to_name": "公司", "count": 1}]
    assert len(host.scoped_writes) == 3
    assert all(item["namespace"] is host.namespace for item in host.scoped_writes)
    assert all(item["record_kind"] == "memory" for item in host.scoped_writes)
    assert all(item["payload"]["source_kind"] == "private" for item in host.scoped_writes)
    serialized = repr(host.data) + repr(host.scoped_writes)
    assert "device-a" not in serialized
    assert "device-b" not in serialized
    assert host.legacy_writes == 0


def test_unreconciled_identity_cannot_create_or_read_place_state() -> None:
    host = _ScopedPlaceHost(ready=False)
    result = host._observe_mobile_place_context("pending-device", _location("家", "home"), observed_at=100)

    assert result["available"] is False
    assert host.data["place_cognitive_maps"] == {}
    assert host.scoped_writes == []
    assert host.saved == 0


def test_exact_legacy_place_map_is_promoted_only_when_canonical_is_empty() -> None:
    host = _ScopedPlaceHost()
    host.data["place_cognitive_maps"]["device-a"] = {
        "version": 1, "places": {}, "routes": {}, "current_place_key": "", "updated_at": "",
    }

    host._observe_mobile_place_context("device-a", _location("家", "home"), observed_at=100)

    assert "device-a" not in host.data["place_cognitive_maps"]
    assert PERSON_KEY in host.data["place_cognitive_maps"]


class _ScopedRealityHost(RealityCompanionBridgeMixin):
    def __init__(self, *, ready: bool = True) -> None:
        self.ready = ready
        self.data = {
            "users": {
                "device-a": {"user_id": "device-a"},
                "device-b": {"user_id": "device-b"},
                "other": {"user_id": "other"},
            },
            "reality_touch_outputs": {},
        }
        self._data_lock = asyncio.Lock()
        self.saved = 0

    def _req041_reality_private_binding(self, user_id: str, **_kwargs):
        if not self.ready:
            return {"ok": False, "code": "scoped_projection_not_reconciled"}
        store_key = PERSON_KEY if user_id in {"device-a", "device-b"} else "person:" + "b" * 64
        return {
            "ok": True,
            "store_key": store_key,
            "subject_ref": store_key,
            "context": SimpleNamespace(kind="private"),
            "user": self.data["users"][user_id],
        }

    def _get_user(self, user_id: str) -> dict:
        return self.data["users"][user_id]

    def _save_data_sync(self, **_kwargs) -> None:
        self.saved += 1


def test_cross_device_continuity_is_shared_only_inside_one_formal_person() -> None:
    host = _ScopedRealityHost()
    class Api:
        outputs = {}

        @staticmethod
        def subject(user_id):
            return PERSON_KEY if user_id in {"device-a", "device-b"} else "other"

        async def record_reality_touch_output(self, user_id, text, **kwargs):
            self.outputs[self.subject(user_id)] = {"text": text, "source": kwargs.get("source"), "delivered_at": kwargs.get("delivered_at")}
            return {"recorded": True}

        def recent_output(self, user_id):
            return dict(self.outputs.get(self.subject(user_id), {}))

    api = Api()
    host._reality_companion_api = lambda: api
    delivered_at = time.time() - 3
    result = asyncio.run(host._record_reality_touch_output(
        "device-a", "早呀。", delivered_at=delivered_at,
    ))
    host.data["users"]["device-b"].update({
        "last_user_message": "早", "last_user_message_at": delivered_at + 1,
    })

    assert result["recorded"] is True
    assert host.data["reality_touch_outputs"] == {}
    assert "last_reality_touch_output" not in host.data["users"]["device-a"]
    assert "早呀" in host._format_reality_touch_continuity_context(host.data["users"]["device-b"])
    assert host._format_reality_touch_continuity_context(host.data["users"]["other"]) == ""


def test_unreconciled_reality_output_is_rejected_without_mutation() -> None:
    host = _ScopedRealityHost(ready=False)
    result = asyncio.run(host._record_reality_touch_output("device-a", "不可写入"))

    assert result == {"recorded": False, "reason": "reality_companion_unavailable"}
    assert host.data["reality_touch_outputs"] == {}
    assert host.saved == 0
