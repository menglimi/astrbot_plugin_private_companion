# -*- coding: utf-8 -*-
"""MemoryPageSnapshotServicePart01Mixin。

由 tools/split_mixin_domain.py 从 memory_page_snapshot.py 机械抽取（20 个方法 + 0 个模块级名字 + 0 个类级赋值 / 391 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 MemoryPageSnapshotService）。
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
try:  # package import
    from .memory_page_snapshot_shared import (
        MEMORY_PAGE_API_FAMILY,
        MEMORY_PAGE_API_VERSION,
        MEMORY_PAGE_OWNER_ID,
        MEMORY_PAGE_PHOTO_BASE64_MAX_BYTES,
        MEMORY_PAGE_PHOTO_RESULT_MAX_BYTES,
        MEMORY_PAGE_PHOTO_VERSION,
        MEMORY_PAGE_SNAPSHOT_MAX_BYTES,
        MEMORY_PAGE_SNAPSHOT_VERSION,
        MEMORY_PAGE_TARGET_ID,
        _COORDINATION_STATES,
        _DATE_RE,
        _REASON_RE,
        _canonical_bytes,
        _date_text,
        _energy,
        _text,
        _timestamp,
        _valid_generation,
    )
except ImportError:  # direct test/import from the plugin directory
    from memory_page_snapshot_shared import (
        MEMORY_PAGE_API_FAMILY,
        MEMORY_PAGE_API_VERSION,
        MEMORY_PAGE_OWNER_ID,
        MEMORY_PAGE_PHOTO_BASE64_MAX_BYTES,
        MEMORY_PAGE_PHOTO_RESULT_MAX_BYTES,
        MEMORY_PAGE_PHOTO_VERSION,
        MEMORY_PAGE_SNAPSHOT_MAX_BYTES,
        MEMORY_PAGE_SNAPSHOT_VERSION,
        MEMORY_PAGE_TARGET_ID,
        _COORDINATION_STATES,
        _DATE_RE,
        _REASON_RE,
        _canonical_bytes,
        _date_text,
        _energy,
        _text,
        _timestamp,
        _valid_generation,
    )
from datetime import date, datetime
from typing import Any
try:  # package import
    from .memory_page_snapshot_shared import MemoryPageSnapshotError
except ImportError:  # direct test/import from the plugin directory
    from memory_page_snapshot_shared import MemoryPageSnapshotError
try:  # package import
    from .memory_page_snapshot_shared import _memory_page_snapshot_host
except ImportError:  # direct test/import from the plugin directory
    from memory_page_snapshot_shared import _memory_page_snapshot_host



class MemoryPageSnapshotServicePart01Mixin:
    """MemoryPageSnapshotServicePart01Mixin（从 MemoryPageSnapshotService 拆出）。"""


    def capabilities(self) -> dict[str, Any]:
        generation = self._current_generation()
        lifecycle = self._current_lifecycle()
        return {
            "plugin_id": MEMORY_PAGE_OWNER_ID,
            "instance_generation": generation,
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
            "lifecycle_state": lifecycle,
            "degraded_reasons": (
                [] if lifecycle == "ready" and generation else ["memory_page_snapshot_service_not_ready"]
            ),
        }

    def clear_references(self) -> None:
        """Revoke every photo reference owned by this façade generation."""
        with self._photo_refs_lock:
            self._photo_refs.clear()

    async def export_snapshot(
        self,
        *,
        target_plugin_id: str,
        selected_date: str = "",
    ) -> dict[str, Any]:
        self._require_target(target_plugin_id)
        requested_date = self._validate_selected_date(selected_date)
        generation = self._require_ready()
        plugin = self._plugin()
        data = getattr(plugin, "data", None)
        data_lock = getattr(plugin, "_data_lock", None)
        if not isinstance(data, dict) or data_lock is None or not hasattr(data_lock, "__aenter__"):
            raise MemoryPageSnapshotError("memory_page_snapshot_state_unavailable")

        coordination = self._coordination(plugin)
        features = {
            "daily_plan_enabled": getattr(plugin, "enable_daily_plan", None) is True,
            "detail_enhancement_enabled": getattr(plugin, "enable_detail_enhancement", None) is True,
        }
        self._require_ready(generation)

        try:
            async with data_lock:
                self._require_ready(generation)
                seed = self._project_locked(plugin, data, requested_date, generation)
                self._require_ready(generation)
        except asyncio.CancelledError:
            raise
        except MemoryPageSnapshotError:
            raise
        except Exception:
            raise MemoryPageSnapshotError("memory_page_snapshot_build_failed") from None

        self._require_ready(generation)
        try:
            photo_rows, registrations = await asyncio.to_thread(
                self._prepare_photos_sync,
                plugin,
                seed.pop("_photo_candidates"),
                generation,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            raise MemoryPageSnapshotError("memory_page_snapshot_build_failed") from None
        self._require_ready(generation)
        photo_rows, staged_refs = self._stage_photo_refs(
            photo_rows,
            registrations,
            generation,
        )
        self._require_ready(generation)
        seed["day"]["photos"] = photo_rows

        unsigned = {
            "version": MEMORY_PAGE_SNAPSHOT_VERSION,
            "source_plugin_id": MEMORY_PAGE_OWNER_ID,
            "instance_generation": generation,
            "selected_date": seed["selected_date"],
            "available_dates": seed["available_dates"],
            "features": features,
            "coordination": coordination,
            "day": seed["day"],
        }
        digest = hashlib.sha256(_canonical_bytes(unsigned)).hexdigest()
        result = {
            **unsigned,
            "snapshot_id": f"memorypagesnap_{digest}",
            "snapshot_sha256": digest,
        }
        try:
            encoded = _canonical_bytes(result)
        except (TypeError, ValueError, UnicodeError):
            raise MemoryPageSnapshotError("memory_page_snapshot_build_failed") from None
        # 尺寸上限经宿主门面延迟解析：tests 会 patch memory_page_snapshot. 上的常量。
        if len(encoded) > _memory_page_snapshot_host.MEMORY_PAGE_SNAPSHOT_MAX_BYTES:
            raise MemoryPageSnapshotError("memory_page_snapshot_too_large")
        self._require_ready(generation)
        detached_result = json.loads(encoded.decode("utf-8"))
        self._commit_photo_refs(staged_refs, generation)
        return detached_result

    async def read_photo(
        self,
        *,
        target_plugin_id: str,
        photo_ref: str,
    ) -> dict[str, Any]:
        self._require_target(target_plugin_id)
        generation = self._require_ready()
        reference = self._validate_photo_ref(photo_ref, generation)
        registration = self._lookup_photo_ref(reference, generation)
        self._require_ready(generation)
        try:
            blob = await asyncio.to_thread(
                self._read_photo_registration_sync,
                registration,
            )
        except asyncio.CancelledError:
            raise
        except MemoryPageSnapshotError:
            raise
        except Exception:
            raise MemoryPageSnapshotError("memory_page_photo_read_failed") from None
        self._require_ready(generation)
        self._recheck_photo_ref(reference, registration, generation)

        content = base64.b64encode(blob.content).decode("ascii")
        if len(content) > MEMORY_PAGE_PHOTO_BASE64_MAX_BYTES:
            raise MemoryPageSnapshotError("memory_page_photo_too_large")
        result = {
            "version": MEMORY_PAGE_PHOTO_VERSION,
            "source_plugin_id": MEMORY_PAGE_OWNER_ID,
            "instance_generation": generation,
            "photo_ref": reference,
            "mime_type": blob.mime_type,
            "size": blob.size,
            "sha256": blob.sha256,
            "content_base64": content,
        }
        if len(_canonical_bytes(result)) > MEMORY_PAGE_PHOTO_RESULT_MAX_BYTES:
            raise MemoryPageSnapshotError("memory_page_photo_too_large")
        self._require_ready(generation)
        return result

    def _plugin(self) -> Any:
        try:
            return self._owner._plugin
        except Exception:
            raise MemoryPageSnapshotError("memory_page_snapshot_state_unavailable") from None

    def _current_generation(self) -> str:
        getter = getattr(self._owner, "_extension_instance_generation", None)
        if not callable(getter):
            getter = getattr(self._owner, "_story_migration_instance_generation", None)
        try:
            return _valid_generation(getter()) if callable(getter) else ""
        except Exception:
            return ""

    def _current_lifecycle(self) -> str:
        getter = getattr(self._owner, "_extension_lifecycle_state", None)
        if not callable(getter):
            getter = getattr(self._owner, "_story_migration_lifecycle_state", None)
        try:
            state = getter() if callable(getter) else "closed"
        except Exception:
            state = "closed"
        return state if state in {"created", "ready", "superseded", "closed"} else "closed"

    def _require_ready(self, expected_generation: str = "") -> str:
        generation = self._current_generation()
        if (
            not generation
            or self._current_lifecycle() != "ready"
            or (expected_generation and generation != expected_generation)
        ):
            raise MemoryPageSnapshotError("memory_page_service_closed")
        return generation

    @staticmethod
    def _require_target(target_plugin_id: Any) -> None:
        if target_plugin_id != MEMORY_PAGE_TARGET_ID:
            raise MemoryPageSnapshotError("memory_page_target_mismatch")

    @staticmethod
    def _validate_selected_date(selected_date: Any) -> str:
        if selected_date == "":
            return ""
        if not isinstance(selected_date, str) or not _DATE_RE.fullmatch(selected_date):
            raise MemoryPageSnapshotError("memory_page_snapshot_invalid_date")
        try:
            if date.fromisoformat(selected_date).isoformat() != selected_date:
                raise ValueError
        except ValueError:
            raise MemoryPageSnapshotError("memory_page_snapshot_invalid_date") from None
        return selected_date

    @staticmethod
    def _coordination(plugin: Any) -> dict[str, Any]:
        raw = getattr(plugin, "_bridge_last_status", None)
        if not isinstance(raw, dict):
            return {
                "available": False,
                "state": "unavailable",
                "reason_code": "coordination_status_unavailable",
            }
        available = raw.get("available") is True
        state = _text(raw.get("state"), 20)
        if state not in _COORDINATION_STATES:
            if raw.get("degraded") is True:
                state = "degraded"
            elif available:
                state = "ready"
            else:
                state = "unavailable"
        reason = _text(raw.get("reason_code") or raw.get("reason"), 64)
        if not _REASON_RE.fullmatch(reason):
            reason = "" if state == "ready" else "coordination_status_unavailable"
        return {"available": available, "state": state, "reason_code": reason}

    def _project_locked(
        self,
        plugin: Any,
        data: dict[str, Any],
        requested_date: str,
        generation: str,
    ) -> dict[str, Any]:
        available_dates = self._available_dates(data)
        selected_date = requested_date or (available_dates[0] if available_dates else "")
        plan, raw_live_plan = self._project_plan(data, selected_date)
        current_item = self._project_current_item(plugin, raw_live_plan, selected_date)
        daily_state = self._project_daily_state(data, selected_date)
        details = self._project_details(data, selected_date, generation)
        diaries = self._project_diaries(data, selected_date)
        photo_candidates = self._photo_candidates(data, selected_date, generation)
        return {
            "selected_date": selected_date,
            "available_dates": available_dates,
            "day": {
                "date": selected_date,
                "bot_name": _text(getattr(plugin, "bot_name", ""), 80),
                "plan": plan,
                "current_item": current_item,
                "daily_state": daily_state,
                "details": details,
                "photos": [],
                "diaries": diaries,
            },
            "_photo_candidates": photo_candidates,
        }

    @staticmethod
    def _available_dates(data: dict[str, Any]) -> list[str]:
        dates: set[str] = set()

        def add(value: Any) -> None:
            if result := _date_text(value):
                dates.add(result)

        for key in ("daily_plan", "daily_story_plan", "daily_outfit_photo"):
            item = data.get(key)
            if isinstance(item, dict):
                add(item.get("date"))
        add(data.get("detail_enhanced_day"))
        add(data.get("state_generated_day"))
        state = data.get("daily_state")
        if isinstance(state, dict):
            add(state.get("date"))
        for key in (
            "daily_plan_history",
            "detail_enhanced_history",
            "daily_story_plan_history",
            "bot_diaries",
            "daily_outfit_history",
        ):
            values = data.get(key)
            if not isinstance(values, list):
                continue
            for item in values[-512:]:
                if isinstance(item, dict):
                    add(item.get("date"))
        recent = data.get("recent_photo_generations")
        if isinstance(recent, list):
            for item in recent[:256]:
                if not isinstance(item, dict):
                    continue
                derived = _date_text(item.get("date")) or MemoryPageSnapshotServicePart01Mixin._date_from_timestamp(
                    item.get("ts")
                )
                add(derived)
        return sorted(dates, reverse=True)[:180]

    @staticmethod
    def _date_from_timestamp(value: Any) -> str:
        stamp = _timestamp(value)
        if not stamp:
            return ""
        try:
            return datetime.fromtimestamp(stamp).date().isoformat()
        except (OSError, OverflowError, ValueError):
            return ""

    @staticmethod
    def _empty_plan() -> dict[str, Any]:
        return {"date": "", "source": "none", "items": []}

    def _project_plan(
        self,
        data: dict[str, Any],
        selected_date: str,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        live = data.get("daily_plan")
        raw_live = live if isinstance(live, dict) else None
        source: dict[str, Any] | None = None
        source_name = "none"
        if raw_live is not None and _date_text(raw_live.get("date")) == selected_date:
            source = raw_live
            source_name = "live"
        else:
            history = data.get("daily_plan_history")
            if isinstance(history, list):
                for item in reversed(history[-512:]):
                    if isinstance(item, dict) and _date_text(item.get("date")) == selected_date:
                        source = item
                        source_name = "history"
                        break
        if source is None:
            return self._empty_plan(), raw_live
        rows: list[dict[str, Any]] = []
        items = source.get("items")
        if isinstance(items, list):
            for index, item in enumerate(items[:18]):
                if not isinstance(item, dict):
                    continue
                rows.append(self._plan_item(item, index))
        return {"date": selected_date, "source": source_name, "items": rows}, raw_live

    @staticmethod
    def _plan_item(item: dict[str, Any], index: int | None) -> dict[str, Any]:
        return {
            "index": index,
            "time": _text(item.get("time"), 20),
            "activity": _text(item.get("activity") or item.get("title"), 180),
            "mood": _text(item.get("mood"), 80),
            "message_seed": _text(item.get("message_seed"), 220),
        }

    def _project_current_item(
        self,
        plugin: Any,
        raw_live_plan: dict[str, Any] | None,
        selected_date: str,
    ) -> dict[str, Any]:
        empty = self._plan_item({}, None)
        if raw_live_plan is None or _date_text(raw_live_plan.get("date")) != selected_date:
            return empty
        getter = getattr(plugin, "_get_current_plan_item", None)
        if not callable(getter):
            return empty
        try:
            current = getter(raw_live_plan)
        except Exception:
            return empty
        if not isinstance(current, dict):
            return empty
        index: int | None = None
        items = raw_live_plan.get("items")
        if isinstance(items, list):
            for candidate_index, candidate in enumerate(items[:18]):
                if candidate is current or candidate == current:
                    index = candidate_index
                    break
        return self._plan_item(current, index)

    @staticmethod
    def _empty_daily_state() -> dict[str, Any]:
        return {
            "date": "",
            "energy": None,
            "mood_bias": "",
            "sleep": "",
            "weather": "",
            "note": "",
        }

    def _project_daily_state(self, data: dict[str, Any], selected_date: str) -> dict[str, Any]:
        raw = data.get("daily_state")
        if not isinstance(raw, dict):
            return self._empty_daily_state()
        state_date = _date_text(raw.get("date")) or _date_text(data.get("state_generated_day"))
        if not selected_date or state_date != selected_date:
            return self._empty_daily_state()
        return {
            "date": selected_date,
            "energy": _energy(raw.get("energy")),
            "mood_bias": _text(raw.get("mood_bias") or raw.get("mood"), 80),
            "sleep": _text(raw.get("sleep"), 80),
            "weather": _text(raw.get("weather"), 80),
            "note": _text(raw.get("note") or raw.get("summary"), 180),
        }
