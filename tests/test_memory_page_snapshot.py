from __future__ import annotations

import asyncio
import base64
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import memory_page_snapshot as memory_page_contract
from memory_page_snapshot import (
    MEMORY_PAGE_API_FAMILY,
    MEMORY_PAGE_API_VERSION,
    MEMORY_PAGE_OWNER_ID,
    MEMORY_PAGE_PHOTO_BASE64_MAX_BYTES,
    MEMORY_PAGE_PHOTO_MAX_BYTES,
    MEMORY_PAGE_PHOTO_REF_MAX_ENTRIES,
    MEMORY_PAGE_PHOTO_REF_TTL_SECONDS,
    MEMORY_PAGE_PHOTO_RESULT_MAX_BYTES,
    MEMORY_PAGE_PHOTO_VERSION,
    MEMORY_PAGE_SNAPSHOT_MAX_BYTES,
    MEMORY_PAGE_SNAPSHOT_VERSION,
    MEMORY_PAGE_TARGET_ID,
    MemoryPageSnapshotError,
    MemoryPageSnapshotService,
)


GENERATION = "0123456789abcdef0123456789abcdef"
PNG = b"\x89PNG\r\n\x1a\n" + b"memory-page-photo"


class _Clock:
    def __init__(self, value: float = 0.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


class _Plugin:
    def __init__(self, root: Path, data: dict[str, Any] | None = None) -> None:
        self.data_dir = root
        self.plugin_data_dir = root
        self.data_file = root / "state.json"
        self.data = data if data is not None else {}
        self._data_lock = asyncio.Lock()
        self.bot_name = "小星"
        self.enable_daily_plan = True
        self.enable_detail_enhancement = True
        self._bridge_last_status = {
            "available": True,
            "state": "ready",
            "reason": "",
            "secret": "must-not-leak",
        }

    @staticmethod
    def _get_current_plan_item(plan: dict[str, Any]) -> dict[str, Any] | None:
        items = plan.get("items")
        return items[0] if isinstance(items, list) and items and isinstance(items[0], dict) else None


class _Owner:
    def __init__(self, plugin: _Plugin) -> None:
        self._plugin = plugin
        self.generation = GENERATION
        self.state = "ready"

    def _extension_instance_generation(self) -> str:
        return self.generation

    def _extension_lifecycle_state(self) -> str:
        return self.state


def _service(
    tmp_path: Path,
    data: dict[str, Any] | None = None,
    *,
    clock: _Clock | None = None,
) -> tuple[MemoryPageSnapshotService, _Owner, _Plugin]:
    plugin = _Plugin(tmp_path, data)
    owner = _Owner(plugin)
    return MemoryPageSnapshotService(owner, clock=clock or _Clock()), owner, plugin


def _sample_data(photo: Path) -> dict[str, Any]:
    return {
        "daily_plan": {
            "date": "2026-08-26",
            "items": [
                {
                    "time": "08:30",
                    "activity": "去图书馆整理笔记",
                    "mood": "安静",
                    "message_seed": "路上看见一朵云",
                    "private_runtime": "must-not-leak",
                }
            ],
            "prompt": "must-not-leak",
        },
        "daily_plan_history": [
            {
                "date": "2026-08-25",
                "items": [{"time": "09:00", "activity": "散步"}],
            }
        ],
        "state_generated_day": "2026-08-26",
        "daily_state": {
            "energy": 72,
            "mood_bias": "平稳",
            "sleep": "睡得不错",
            "weather": "有风",
            "note": "想慢慢做完手边的事",
            "user_id": "must-not-leak",
        },
        "detail_enhanced_day": "2026-08-26",
        "detail_enhanced_segments": {
            "2026-08-26:0:08:30": {
                "status": "done",
                "summary": "在靠窗的位置整理读书笔记",
                "today_events": [{"event": "借到想看的书", "trace": "must-not-leak"}],
                "proactive_events": [{"topic": "晚点聊聊那本书"}],
                "state_variables": [{"name": "专注", "value": "逐渐稳定"}],
                "provider": "must-not-leak",
            }
        },
        "daily_story_plan": {
            "date": "2026-08-26",
            "summary": "一天里值得留下的小片段",
            "today_events": [{"event": "翻到一张旧书签"}],
            "proactive_events": [{"topic": "分享书签上的句子"}],
            "long_term_events": [{"title": "继续读完这本书"}],
        },
        "bot_diaries": [
            {
                "date": "2026-08-26",
                "summary": "把安静的一天收好",
                "body": "在图书馆靠窗坐了一会儿，也记住了那阵风。",
                "share_seed": "今天的云很好看",
                "tags": ["图书馆", "风"],
                "story_plan": {
                    "today_events": [{"event": "整理笔记"}],
                    "proactive_events": [{"topic": "聊聊书"}],
                    "long_term_events": [{"title": "下次再去"}],
                },
                "token": "must-not-leak",
            }
        ],
        "daily_outfit_photo": {
            "date": "2026-08-26",
            "path": str(photo),
            "generated_at": 1_777_000_000,
            "backend": "must-not-leak",
            "prompt": "must-not-leak",
        },
        "daily_outfit_history": [],
        "recent_photo_generations": [],
        "users": {"private-user": {"secret": "must-not-leak"}},
    }


def _export(
    service: MemoryPageSnapshotService,
    *,
    selected_date: str = "",
) -> dict[str, Any]:
    return asyncio.run(
        service.export_snapshot(
            target_plugin_id=MEMORY_PAGE_TARGET_ID,
            selected_date=selected_date,
        )
    )


def _error_code(error: pytest.ExceptionInfo[MemoryPageSnapshotError]) -> str:
    assert str(error.value) == error.value.code
    return error.value.code


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def test_descriptor_is_exact_detached_and_lifecycle_bound(tmp_path: Path) -> None:
    service, owner, _plugin = _service(tmp_path)

    assert service.capabilities() == {
        "plugin_id": MEMORY_PAGE_OWNER_ID,
        "instance_generation": GENERATION,
        "api_family": MEMORY_PAGE_API_FAMILY,
        "api_version": MEMORY_PAGE_API_VERSION,
        "supported_task_versions": [
            MEMORY_PAGE_SNAPSHOT_VERSION,
            MEMORY_PAGE_PHOTO_VERSION,
        ],
        "capabilities": [
            "memory.page.snapshot.export",
            "memory.page.snapshot.path-free",
            "memory.page.snapshot.read-only",
            "memory.page.photo.read",
        ],
        "lifecycle_state": "ready",
        "degraded_reasons": [],
    }

    mutated = service.capabilities()
    mutated["capabilities"].append("caller-mutation")
    assert "caller-mutation" not in service.capabilities()["capabilities"]

    owner.state = "created"
    assert service.capabilities()["degraded_reasons"] == [
        "memory_page_snapshot_service_not_ready"
    ]
    owner.state = "superseded"
    assert service.capabilities()["lifecycle_state"] == "superseded"


def test_snapshot_exact_projection_hash_bounds_and_no_source_mutation(tmp_path: Path) -> None:
    photo = tmp_path / "outfit.png"
    photo.write_bytes(PNG)
    source = _sample_data(photo)
    original = json.loads(json.dumps(source))
    service, _owner, _plugin = _service(tmp_path, source)

    snapshot = _export(service)

    assert set(snapshot) == {
        "version",
        "source_plugin_id",
        "instance_generation",
        "selected_date",
        "available_dates",
        "features",
        "coordination",
        "day",
        "snapshot_id",
        "snapshot_sha256",
    }
    assert snapshot["version"] == MEMORY_PAGE_SNAPSHOT_VERSION
    assert snapshot["source_plugin_id"] == MEMORY_PAGE_OWNER_ID
    assert snapshot["instance_generation"] == GENERATION
    assert snapshot["selected_date"] == "2026-08-26"
    assert snapshot["available_dates"] == ["2026-08-26", "2026-08-25"]
    assert snapshot["features"] == {
        "daily_plan_enabled": True,
        "detail_enhancement_enabled": True,
    }
    assert snapshot["coordination"] == {
        "available": True,
        "state": "ready",
        "reason_code": "",
    }

    day = snapshot["day"]
    assert set(day) == {
        "date",
        "bot_name",
        "plan",
        "current_item",
        "daily_state",
        "details",
        "photos",
        "diaries",
    }
    assert day["date"] == "2026-08-26"
    assert day["bot_name"] == "小星"
    assert day["plan"] == {
        "date": "2026-08-26",
        "source": "live",
        "items": [
            {
                "index": 0,
                "time": "08:30",
                "activity": "去图书馆整理笔记",
                "mood": "安静",
                "message_seed": "路上看见一朵云",
            }
        ],
    }
    assert day["current_item"] == day["plan"]["items"][0]
    assert day["daily_state"] == {
        "date": "2026-08-26",
        "energy": 72,
        "mood_bias": "平稳",
        "sleep": "睡得不错",
        "weather": "有风",
        "note": "想慢慢做完手边的事",
    }
    assert day["details"][0] == {
        "id": day["details"][0]["id"],
        "index": 0,
        "status": "ready",
        "time": "08:30",
        "summary": "在靠窗的位置整理读书笔记",
        "today_events": ["借到想看的书"],
        "proactive_events": ["晚点聊聊那本书"],
        "state_variables": ["专注: 逐渐稳定"],
    }
    assert day["details"][0]["id"].startswith("detail_")
    assert day["details"][1]["status"] == "story_plan"
    assert day["diaries"] == [
        {
            "date": "2026-08-26",
            "summary": "把安静的一天收好",
            "body": "在图书馆靠窗坐了一会儿，也记住了那阵风。",
            "share_seed": "今天的云很好看",
            "tags": ["图书馆", "风"],
            "today_events": ["整理笔记"],
            "proactive_events": ["聊聊书"],
            "long_term_events": ["下次再去"],
        }
    ]
    assert len(day["photos"]) == 1
    assert set(day["photos"][0]) == {
        "id",
        "date",
        "kind",
        "generated_at",
        "available",
        "error_code",
        "photo_ref",
    }
    assert day["photos"][0]["available"] is True
    assert day["photos"][0]["photo_ref"].startswith(f"mphoto_{GENERATION[:12]}_")

    unsigned = {key: value for key, value in snapshot.items() if key not in {"snapshot_id", "snapshot_sha256"}}
    digest = hashlib.sha256(_canonical_bytes(unsigned)).hexdigest()
    assert snapshot["snapshot_id"] == f"memorypagesnap_{digest}"
    assert snapshot["snapshot_sha256"] == digest
    assert len(_canonical_bytes(snapshot)) <= MEMORY_PAGE_SNAPSHOT_MAX_BYTES

    wire = _canonical_bytes(snapshot).decode("utf-8")
    assert str(tmp_path) not in wire
    assert "outfit.png" not in wire
    assert '"path"' not in wire
    assert "must-not-leak" not in wire
    assert source == original
    snapshot["day"]["plan"]["items"][0]["activity"] = "caller-mutation"
    assert source["daily_plan"]["items"][0]["activity"] == "去图书馆整理笔记"


def test_snapshot_selects_history_and_returns_exact_empty_day(tmp_path: Path) -> None:
    photo = tmp_path / "outfit.png"
    photo.write_bytes(PNG)
    service, _owner, _plugin = _service(tmp_path, _sample_data(photo))

    history = _export(service, selected_date="2026-08-25")
    assert history["day"]["plan"] == {
        "date": "2026-08-25",
        "source": "history",
        "items": [
            {
                "index": 0,
                "time": "09:00",
                "activity": "散步",
                "mood": "",
                "message_seed": "",
            }
        ],
    }
    assert history["day"]["current_item"] == {
        "index": None,
        "time": "",
        "activity": "",
        "mood": "",
        "message_seed": "",
    }
    assert history["day"]["daily_state"]["date"] == ""

    empty = _export(service, selected_date="2020-02-29")
    assert empty["selected_date"] == "2020-02-29"
    assert empty["day"] == {
        "date": "2020-02-29",
        "bot_name": "小星",
        "plan": {"date": "", "source": "none", "items": []},
        "current_item": {
            "index": None,
            "time": "",
            "activity": "",
            "mood": "",
            "message_seed": "",
        },
        "daily_state": {
            "date": "",
            "energy": None,
            "mood_bias": "",
            "sleep": "",
            "weather": "",
            "note": "",
        },
        "details": [],
        "photos": [],
        "diaries": [],
    }


def test_snapshot_enforces_type_date_and_collection_bounds(tmp_path: Path) -> None:
    huge = "字" * 10_000
    data = {
        "daily_plan": {
            "date": "2026-08-26",
            "items": [
                {
                    "time": huge,
                    "activity": huge,
                    "mood": huge,
                    "message_seed": huge,
                }
                for _ in range(40)
            ],
        },
        "daily_plan_history": [
            {"date": f"2026-01-{day:02d}", "items": []}
            for day in range(1, 32)
        ],
        "detail_enhanced_day": "2026-08-26",
        "detail_enhanced_segments": {
            f"2026-08-26:{index}:08:30": {
                "status": "done",
                "summary": huge,
                "today_events": [huge] * 20,
                "proactive_events": [huge] * 20,
                "state_variables": [huge] * 20,
            }
            for index in range(40)
        },
        "bot_diaries": [
            {
                "date": "2026-08-26",
                "summary": huge,
                "body": huge,
                "share_seed": huge,
                "tags": [huge] * 20,
                "today_events": [huge] * 20,
            }
            for _ in range(10)
        ],
        "daily_state": {"date": "2026-08-26", "energy": True},
    }
    service, _owner, _plugin = _service(tmp_path, data)

    snapshot = _export(service)

    assert len(snapshot["available_dates"]) <= 180
    assert snapshot["available_dates"] == sorted(set(snapshot["available_dates"]), reverse=True)
    assert len(snapshot["day"]["plan"]["items"]) == 18
    assert len(snapshot["day"]["details"]) == 18
    assert len(snapshot["day"]["diaries"]) == 4
    assert len(snapshot["day"]["details"][0]["today_events"]) <= 5
    assert len(snapshot["day"]["diaries"][0]["tags"]) <= 8
    assert snapshot["day"]["daily_state"]["energy"] is None
    assert len(snapshot["day"]["plan"]["items"][0]["activity"]) == 180
    assert len(snapshot["day"]["diaries"][0]["body"]) == 520
    assert len(_canonical_bytes(snapshot)) <= MEMORY_PAGE_SNAPSHOT_MAX_BYTES


@pytest.mark.parametrize("invalid", [None, 1, " 2026-08-26", "2026-8-26", "2026-02-30"])
def test_snapshot_rejects_invalid_selected_dates(tmp_path: Path, invalid: Any) -> None:
    service, _owner, _plugin = _service(tmp_path)
    with pytest.raises(MemoryPageSnapshotError) as captured:
        asyncio.run(
            service.export_snapshot(
                target_plugin_id=MEMORY_PAGE_TARGET_ID,
                selected_date=invalid,
            )
        )
    assert _error_code(captured) == "memory_page_snapshot_invalid_date"


def test_target_binding_and_non_ready_lifecycle_are_stable_errors(tmp_path: Path) -> None:
    service, owner, _plugin = _service(tmp_path)
    with pytest.raises(MemoryPageSnapshotError) as mismatch:
        asyncio.run(
            service.export_snapshot(
                target_plugin_id="another-plugin",
                selected_date="",
            )
        )
    assert _error_code(mismatch) == "memory_page_target_mismatch"

    owner.state = "created"
    with pytest.raises(MemoryPageSnapshotError) as closed:
        _export(service)
    assert _error_code(closed) == "memory_page_service_closed"


def test_snapshot_rechecks_lifecycle_while_waiting_for_data_lock(tmp_path: Path) -> None:
    service, owner, plugin = _service(tmp_path)

    async def exercise() -> str:
        await plugin._data_lock.acquire()
        task = asyncio.create_task(
            service.export_snapshot(
                target_plugin_id=MEMORY_PAGE_TARGET_ID,
                selected_date="",
            )
        )
        await asyncio.sleep(0)
        owner.state = "superseded"
        plugin._data_lock.release()
        with pytest.raises(MemoryPageSnapshotError) as captured:
            await task
        return captured.value.code

    assert asyncio.run(exercise()) == "memory_page_service_closed"


def test_snapshot_rechecks_lifecycle_after_photo_io(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    photo = tmp_path / "outfit.png"
    photo.write_bytes(PNG)
    service, owner, _plugin = _service(tmp_path, _sample_data(photo))
    original = MemoryPageSnapshotService._prepare_photos_sync

    def close_after_read(self: MemoryPageSnapshotService, *args: Any) -> Any:
        result = original(self, *args)
        owner.state = "superseded"
        return result

    monkeypatch.setattr(MemoryPageSnapshotService, "_prepare_photos_sync", close_after_read)
    with pytest.raises(MemoryPageSnapshotError) as captured:
        _export(service)
    assert _error_code(captured) == "memory_page_service_closed"
    assert len(service._photo_refs) == 0


def test_cancelled_error_propagates_without_publishing_photo_refs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    photo = tmp_path / "outfit.png"
    photo.write_bytes(PNG)
    service, _owner, _plugin = _service(tmp_path, _sample_data(photo))

    async def cancelled(*_args: Any, **_kwargs: Any) -> Any:
        raise asyncio.CancelledError

    monkeypatch.setattr(memory_page_contract.asyncio, "to_thread", cancelled)
    with pytest.raises(asyncio.CancelledError):
        _export(service)
    assert len(service._photo_refs) == 0


def test_oversized_snapshot_does_not_publish_staged_photo_refs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    photo = tmp_path / "outfit.png"
    photo.write_bytes(PNG)
    service, _owner, _plugin = _service(tmp_path, _sample_data(photo))
    monkeypatch.setattr(memory_page_contract, "MEMORY_PAGE_SNAPSHOT_MAX_BYTES", 1)

    with pytest.raises(MemoryPageSnapshotError) as captured:
        _export(service)

    assert _error_code(captured) == "memory_page_snapshot_too_large"
    assert len(service._photo_refs) == 0


def test_photo_ref_commit_rolls_back_when_lifecycle_closes_after_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    photo = tmp_path / "outfit.png"
    photo.write_bytes(PNG)
    service, owner, _plugin = _service(tmp_path, _sample_data(photo))
    original = MemoryPageSnapshotService._require_ready

    def close_after_publish(
        candidate: MemoryPageSnapshotService,
        expected_generation: str = "",
    ) -> str:
        if candidate._photo_refs:
            owner.state = "closed"
        return original(candidate, expected_generation)

    monkeypatch.setattr(MemoryPageSnapshotService, "_require_ready", close_after_publish)
    with pytest.raises(MemoryPageSnapshotError) as captured:
        _export(service)

    assert _error_code(captured) == "memory_page_service_closed"
    assert len(service._photo_refs) == 0


def test_clear_references_revokes_a_published_photo(tmp_path: Path) -> None:
    photo = tmp_path / "outfit.png"
    photo.write_bytes(PNG)
    service, _owner, _plugin = _service(tmp_path, _sample_data(photo))
    photo_ref = _export(service)["day"]["photos"][0]["photo_ref"]

    service.clear_references()

    assert len(service._photo_refs) == 0
    with pytest.raises(MemoryPageSnapshotError) as captured:
        asyncio.run(
            service.read_photo(
                target_plugin_id=MEMORY_PAGE_TARGET_ID,
                photo_ref=photo_ref,
            )
        )
    assert _error_code(captured) == "memory_page_photo_ref_expired"


@pytest.mark.parametrize(
    ("content", "mime_type"),
    [
        (b"\xff\xd8\xffjpeg", "image/jpeg"),
        (b"\x89PNG\r\n\x1a\npng", "image/png"),
        (b"GIF89agif", "image/gif"),
        (b"BMbitmap", "image/bmp"),
        (b"RIFF\x04\x00\x00\x00WEBPwebp", "image/webp"),
        (b"\x00\x00\x00\x18ftypavifavif", "image/avif"),
    ],
)
def test_photo_read_exact_wire_and_magic_based_mime(
    tmp_path: Path,
    content: bytes,
    mime_type: str,
) -> None:
    photo = tmp_path / "photo.bin"
    photo.write_bytes(content)
    data = {
        "daily_outfit_photo": {
            "date": "2026-08-26",
            "path": str(photo),
            "generated_at": 1,
        }
    }
    service, _owner, _plugin = _service(tmp_path, data)
    photo_ref = _export(service)["day"]["photos"][0]["photo_ref"]

    result = asyncio.run(
        service.read_photo(
            target_plugin_id=MEMORY_PAGE_TARGET_ID,
            photo_ref=photo_ref,
        )
    )

    assert result == {
        "version": MEMORY_PAGE_PHOTO_VERSION,
        "source_plugin_id": MEMORY_PAGE_OWNER_ID,
        "instance_generation": GENERATION,
        "photo_ref": photo_ref,
        "mime_type": mime_type,
        "size": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "content_base64": base64.b64encode(content).decode("ascii"),
    }
    assert str(tmp_path) not in json.dumps(result)
    assert len(result["content_base64"]) <= MEMORY_PAGE_PHOTO_BASE64_MAX_BYTES
    assert len(_canonical_bytes(result)) <= MEMORY_PAGE_PHOTO_RESULT_MAX_BYTES


def test_photo_ref_is_generation_bound_expires_and_has_bounded_registry(tmp_path: Path) -> None:
    clock = _Clock(100.0)
    photo = tmp_path / "photo.png"
    photo.write_bytes(PNG)
    data = {
        "daily_outfit_photo": {
            "date": "2026-08-26",
            "path": str(photo),
            "generated_at": 1,
        }
    }
    service, _owner, _plugin = _service(tmp_path, data, clock=clock)
    photo_ref = _export(service)["day"]["photos"][0]["photo_ref"]
    assert MEMORY_PAGE_PHOTO_REF_TTL_SECONDS == 900
    assert MEMORY_PAGE_PHOTO_REF_MAX_ENTRIES == 256
    assert len(service._photo_refs) <= MEMORY_PAGE_PHOTO_REF_MAX_ENTRIES

    token = photo_ref[len(f"mphoto_{GENERATION[:12]}_") :]
    stale = f"mphoto_ffffffffffff_{token}"
    with pytest.raises(MemoryPageSnapshotError) as stale_error:
        asyncio.run(
            service.read_photo(
                target_plugin_id=MEMORY_PAGE_TARGET_ID,
                photo_ref=stale,
            )
        )
    assert _error_code(stale_error) == "memory_page_photo_ref_stale"

    with pytest.raises(MemoryPageSnapshotError) as invalid:
        asyncio.run(
            service.read_photo(
                target_plugin_id=MEMORY_PAGE_TARGET_ID,
                photo_ref="../../photo.png",
            )
        )
    assert _error_code(invalid) == "memory_page_photo_ref_invalid"

    clock.value += 901
    with pytest.raises(MemoryPageSnapshotError) as expired:
        asyncio.run(
            service.read_photo(
                target_plugin_id=MEMORY_PAGE_TARGET_ID,
                photo_ref=photo_ref,
            )
        )
    assert _error_code(expired) == "memory_page_photo_ref_expired"


def test_stale_concurrent_photo_read_does_not_revoke_new_registration(tmp_path: Path) -> None:
    photo = tmp_path / "photo.png"
    photo.write_bytes(PNG)
    data = {
        "daily_outfit_photo": {
            "date": "2026-08-26",
            "path": str(photo),
            "generated_at": 1,
        }
    }
    service, _owner, _plugin = _service(tmp_path, data)
    photo_ref = _export(service)["day"]["photos"][0]["photo_ref"]
    stale_registration = service._lookup_photo_ref(photo_ref, GENERATION)

    assert _export(service)["day"]["photos"][0]["photo_ref"] == photo_ref
    current_registration = service._lookup_photo_ref(photo_ref, GENERATION)
    assert current_registration is not stale_registration

    with pytest.raises(MemoryPageSnapshotError) as stale_read:
        service._recheck_photo_ref(photo_ref, stale_registration, GENERATION)

    assert _error_code(stale_read) == "memory_page_photo_ref_expired"
    assert service._lookup_photo_ref(photo_ref, GENERATION) is current_registration


def test_photo_registry_evicts_oldest_reference_at_256_entries(tmp_path: Path) -> None:
    data: dict[str, Any] = {}
    service, _owner, plugin = _service(tmp_path, data)

    async def populate() -> tuple[str, str]:
        first_ref = ""
        last_ref = ""
        for index in range(MEMORY_PAGE_PHOTO_REF_MAX_ENTRIES + 1):
            photo = tmp_path / f"photo-{index}.png"
            photo.write_bytes(PNG + str(index).encode("ascii"))
            plugin.data["daily_outfit_photo"] = {
                "date": "2026-08-26",
                "path": str(photo),
                "generated_at": index + 1,
            }
            snapshot = await service.export_snapshot(
                target_plugin_id=MEMORY_PAGE_TARGET_ID,
                selected_date="",
            )
            last_ref = snapshot["day"]["photos"][0]["photo_ref"]
            if not first_ref:
                first_ref = last_ref
        return first_ref, last_ref

    first_ref, last_ref = asyncio.run(populate())
    assert len(service._photo_refs) == MEMORY_PAGE_PHOTO_REF_MAX_ENTRIES

    with pytest.raises(MemoryPageSnapshotError) as evicted:
        asyncio.run(
            service.read_photo(
                target_plugin_id=MEMORY_PAGE_TARGET_ID,
                photo_ref=first_ref,
            )
        )
    assert _error_code(evicted) == "memory_page_photo_ref_expired"
    assert asyncio.run(
        service.read_photo(
            target_plugin_id=MEMORY_PAGE_TARGET_ID,
            photo_ref=last_ref,
        )
    )["photo_ref"] == last_ref


def test_photo_read_revalidates_identity_size_mtime_magic_and_sha(tmp_path: Path) -> None:
    photo = tmp_path / "photo.png"
    photo.write_bytes(PNG)
    data = {
        "daily_outfit_photo": {
            "date": "2026-08-26",
            "path": str(photo),
            "generated_at": 1,
        }
    }
    service, _owner, _plugin = _service(tmp_path, data)
    photo_ref = _export(service)["day"]["photos"][0]["photo_ref"]

    photo.write_bytes(b"\x89PNG\r\n\x1a\nchanged-content")
    with pytest.raises(MemoryPageSnapshotError) as changed:
        asyncio.run(
            service.read_photo(
                target_plugin_id=MEMORY_PAGE_TARGET_ID,
                photo_ref=photo_ref,
            )
        )
    assert _error_code(changed) == "memory_page_photo_changed"


def test_photo_snapshot_rejects_outside_symlink_unsupported_and_oversized_files(
    tmp_path: Path,
) -> None:
    outside = tmp_path.parent / f"outside-{os.getpid()}-{tmp_path.name}.png"
    outside.write_bytes(PNG)
    symlink = tmp_path / "linked.png"
    try:
        symlink.symlink_to(outside)
    except OSError:
        outside.unlink(missing_ok=True)
        pytest.skip("symlinks are unavailable")
    svg = tmp_path / "vector.svg"
    svg.write_text("<svg xmlns='http://www.w3.org/2000/svg'/>", encoding="utf-8")
    oversized = tmp_path / "oversized.png"
    with oversized.open("wb") as stream:
        stream.write(b"\x89PNG\r\n\x1a\n")
        stream.truncate(MEMORY_PAGE_PHOTO_MAX_BYTES + 1)
    data = {
        "recent_photo_generations": [
            {"date": "2026-08-26", "path": str(outside), "ts": 4},
            {"date": "2026-08-26", "path": str(symlink), "ts": 3},
            {"date": "2026-08-26", "path": str(svg), "ts": 2},
            {"date": "2026-08-26", "path": str(oversized), "ts": 1},
        ]
    }
    service, _owner, _plugin = _service(tmp_path, data)
    try:
        rows = _export(service)["day"]["photos"]
    finally:
        outside.unlink(missing_ok=True)

    assert [row["error_code"] for row in rows] == [
        "memory_page_photo_unavailable",
        "memory_page_photo_unavailable",
        "memory_page_photo_unsupported",
        "memory_page_photo_too_large",
    ]
    assert all(row["available"] is False and row["photo_ref"] == "" for row in rows)
    assert str(tmp_path) not in json.dumps(rows)


def test_photo_snapshot_rejects_fifo_without_blocking(tmp_path: Path) -> None:
    if not hasattr(os, "mkfifo") or not hasattr(os, "O_NONBLOCK"):
        pytest.skip("non-blocking FIFO inspection is unavailable")
    fifo = tmp_path / "photo.png"
    try:
        os.mkfifo(fifo)
    except OSError:
        pytest.skip("FIFOs are unavailable")
    data = {
        "daily_outfit_photo": {
            "date": "2026-08-26",
            "path": str(fifo),
            "generated_at": 1,
        }
    }
    service, _owner, _plugin = _service(tmp_path, data)

    row = _export(service)["day"]["photos"][0]

    assert row["available"] is False
    assert row["error_code"] == "memory_page_photo_unavailable"
    assert row["photo_ref"] == ""


def test_photo_read_rechecks_target_and_lifecycle(tmp_path: Path) -> None:
    photo = tmp_path / "photo.png"
    photo.write_bytes(PNG)
    data = {
        "daily_outfit_photo": {
            "date": "2026-08-26",
            "path": str(photo),
            "generated_at": 1,
        }
    }
    service, owner, _plugin = _service(tmp_path, data)
    photo_ref = _export(service)["day"]["photos"][0]["photo_ref"]

    with pytest.raises(MemoryPageSnapshotError) as mismatch:
        asyncio.run(
            service.read_photo(
                target_plugin_id="another-plugin",
                photo_ref=photo_ref,
            )
        )
    assert _error_code(mismatch) == "memory_page_target_mismatch"

    owner.state = "closed"
    with pytest.raises(MemoryPageSnapshotError) as closed:
        asyncio.run(
            service.read_photo(
                target_plugin_id=MEMORY_PAGE_TARGET_ID,
                photo_ref=photo_ref,
            )
        )
    assert _error_code(closed) == "memory_page_service_closed"


def test_public_limits_are_frozen() -> None:
    assert MEMORY_PAGE_SNAPSHOT_MAX_BYTES == 256 * 1024
    assert MEMORY_PAGE_PHOTO_MAX_BYTES == 8 * 1024 * 1024
    assert MEMORY_PAGE_PHOTO_BASE64_MAX_BYTES == 11_184_812
    assert MEMORY_PAGE_PHOTO_RESULT_MAX_BYTES == 12 * 1024 * 1024
