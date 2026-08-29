from __future__ import annotations

import asyncio
import base64
from collections import OrderedDict
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import secrets
import stat
import threading
import time
from typing import Any, Callable


MEMORY_PAGE_OWNER_ID = "astrbot_plugin_private_companion"
MEMORY_PAGE_TARGET_ID = "astrbot_plugin_memory_companion"
MEMORY_PAGE_API_FAMILY = "companion.memory-page"
MEMORY_PAGE_API_VERSION = "companion.memory-page-api.v1"
MEMORY_PAGE_SNAPSHOT_VERSION = "companion.memory-page-snapshot.v1"
MEMORY_PAGE_PHOTO_VERSION = "companion.memory-page-photo.v1"

MEMORY_PAGE_SNAPSHOT_MAX_BYTES = 256 * 1024
MEMORY_PAGE_PHOTO_MAX_BYTES = 8 * 1024 * 1024
MEMORY_PAGE_PHOTO_BASE64_MAX_BYTES = 11_184_812
MEMORY_PAGE_PHOTO_RESULT_MAX_BYTES = 12 * 1024 * 1024
MEMORY_PAGE_PHOTO_REF_TTL_SECONDS = 900
MEMORY_PAGE_PHOTO_REF_MAX_ENTRIES = 256

_GENERATION_RE = re.compile(r"^[0-9a-f]{32}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_PHOTO_REF_RE = re.compile(r"^mphoto_([0-9a-f]{12})_([A-Za-z0-9_-]{22})$")
_REASON_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_COORDINATION_STATES = {
    "ready",
    "degraded",
    "local_only",
    "disabled",
    "inactive",
    "unavailable",
}
_DETAIL_STATUSES = {
    "planned",
    "ready",
    "observed",
    "degraded",
    "story_plan",
    "unknown",
}


class MemoryPageSnapshotError(RuntimeError):
    """Stable, body-free error raised by the Memory Page producer."""

    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class _PhotoBlob:
    root: Path
    parts: tuple[str, ...]
    device: int
    inode: int
    size: int
    mtime_ns: int
    mime_type: str
    sha256: str
    content: bytes


@dataclass(frozen=True, slots=True)
class _PhotoRegistration:
    root: Path
    parts: tuple[str, ...]
    device: int
    inode: int
    size: int
    mtime_ns: int
    mime_type: str
    sha256: str
    expires_at: float


def _text(value: Any, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    value = value.encode("utf-8", errors="ignore").decode("utf-8")
    value = _CONTROL_RE.sub(" ", value)
    return " ".join(value.split())[:limit]


def _date_text(value: Any) -> str:
    value = _text(value, 10)
    if not _DATE_RE.fullmatch(value):
        return ""
    try:
        return value if date.fromisoformat(value).isoformat() == value else ""
    except ValueError:
        return ""


def _timestamp(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    number = float(value)
    if not math.isfinite(number) or number < 0 or number > 10_000_000_000:
        return 0
    return int(number)


def _energy(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 0 <= value <= 100 else None


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _event_text(value: Any, limit: int = 180) -> str:
    if isinstance(value, str):
        return _text(value, limit)
    if not isinstance(value, dict):
        return ""
    for key in (
        "event",
        "summary",
        "topic",
        "title",
        "text",
        "status",
        "scene",
        "why",
        "motive",
        "impulse",
        "next_hint",
    ):
        result = _text(value.get(key), limit)
        if result:
            return result
    name = _text(value.get("name") or value.get("key"), 60)
    raw_scalar = value.get("value")
    scalar = ""
    if isinstance(raw_scalar, str):
        scalar = _text(raw_scalar, max(0, limit - len(name) - 2))
    elif isinstance(raw_scalar, bool):
        scalar = "true" if raw_scalar else "false"
    elif isinstance(raw_scalar, int):
        scalar = str(raw_scalar)
    if name and scalar:
        return _text(f"{name}: {scalar}", limit)
    return name


def _event_list(value: Any, *, limit: int = 5, item_limit: int = 180) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value[:64]:
        text = _event_text(item, item_limit)
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _valid_generation(value: Any) -> str:
    return value if isinstance(value, str) and _GENERATION_RE.fullmatch(value) else ""


class MemoryPageSnapshotService:
    """Generation-bound, read-only Memory Page snapshot and photo producer."""

    __slots__ = (
        "_clock",
        "_owner",
        "_photo_refs",
        "_photo_refs_lock",
        "_secret",
    )

    def __init__(
        self,
        owner: Any,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._owner = owner
        self._clock = clock
        self._secret = secrets.token_bytes(32)
        self._photo_refs: OrderedDict[str, _PhotoRegistration] = OrderedDict()
        self._photo_refs_lock = threading.RLock()

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
        if len(encoded) > MEMORY_PAGE_SNAPSHOT_MAX_BYTES:
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
                derived = _date_text(item.get("date")) or MemoryPageSnapshotService._date_from_timestamp(
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

    def _project_details(
        self,
        data: dict[str, Any],
        selected_date: str,
        generation: str,
    ) -> list[dict[str, Any]]:
        segments: dict[Any, Any] = {}
        if _date_text(data.get("detail_enhanced_day")) == selected_date:
            raw = data.get("detail_enhanced_segments")
            if isinstance(raw, dict):
                segments = raw
        else:
            history = data.get("detail_enhanced_history")
            if isinstance(history, list):
                for item in reversed(history[-512:]):
                    if not isinstance(item, dict) or _date_text(item.get("date")) != selected_date:
                        continue
                    raw = item.get("segments")
                    if isinstance(raw, dict):
                        segments = raw
                    break

        result: list[dict[str, Any]] = []
        for position, (raw_key, snapshot) in enumerate(segments.items()):
            if position >= 64:
                break
            if not isinstance(snapshot, dict):
                continue
            key = raw_key if isinstance(raw_key, str) else ""
            match = re.fullmatch(r"\d{4}-\d{2}-\d{2}:(\d{1,3}):(\d{1,2}:\d{2})", key)
            raw_index = snapshot.get("index")
            index = (
                raw_index
                if isinstance(raw_index, int)
                and not isinstance(raw_index, bool)
                and 0 <= raw_index < 18
                else None
            )
            if index is None and match:
                parsed_index = int(match.group(1))
                index = parsed_index if parsed_index < 18 else None
            raw_status = _text(snapshot.get("status"), 24).lower()
            status_map = {
                "done": "ready",
                "complete": "ready",
                "completed": "ready",
                "ready": "ready",
                "planned": "planned",
                "pending": "planned",
                "generating": "planned",
                "observed": "observed",
                "failed": "degraded",
                "degraded": "degraded",
            }
            status = status_map.get(raw_status, raw_status if raw_status in _DETAIL_STATUSES else "unknown")
            time_text = _text(snapshot.get("time") or snapshot.get("start_time"), 20)
            if not time_text and match:
                time_text = match.group(2)
            identity = f"detail|{generation}|{selected_date}|{key}|{position}"
            result.append(
                {
                    "id": f"detail_{self._opaque(identity)}",
                    "index": index,
                    "status": status,
                    "time": time_text,
                    "summary": _text(snapshot.get("summary"), 180),
                    "today_events": _event_list(snapshot.get("today_events")),
                    "proactive_events": _event_list(snapshot.get("proactive_events")),
                    "state_variables": _event_list(snapshot.get("state_variables")),
                }
            )
            if len(result) >= 18:
                return result

        story = self._story_plan_for_date(data, selected_date)
        if story and len(result) < 18:
            today_events = _event_list(story.get("today_events"))
            proactive_events = _event_list(story.get("proactive_events"))
            summary = _text(story.get("summary"), 180)
            if summary or today_events or proactive_events:
                result.append(
                    {
                        "id": f"detail_{self._opaque(f'story|{generation}|{selected_date}')}",
                        "index": None,
                        "status": "story_plan",
                        "time": "",
                        "summary": summary,
                        "today_events": today_events,
                        "proactive_events": proactive_events,
                        "state_variables": [],
                    }
                )
        return result

    @staticmethod
    def _story_plan_for_date(data: dict[str, Any], selected_date: str) -> dict[str, Any]:
        current = data.get("daily_story_plan")
        if isinstance(current, dict) and _date_text(current.get("date")) == selected_date:
            return current
        history = data.get("daily_story_plan_history")
        if isinstance(history, list):
            for item in reversed(history[-512:]):
                if isinstance(item, dict) and _date_text(item.get("date")) == selected_date:
                    return item
        return {}

    def _project_diaries(self, data: dict[str, Any], selected_date: str) -> list[dict[str, Any]]:
        raw = data.get("bot_diaries")
        if not isinstance(raw, list):
            return []
        result: list[dict[str, Any]] = []
        for item in reversed(raw[-512:]):
            if not isinstance(item, dict) or _date_text(item.get("date")) != selected_date:
                continue
            story = item.get("story_plan") if isinstance(item.get("story_plan"), dict) else {}
            tags = item.get("tags")
            safe_tags: list[str] = []
            if isinstance(tags, list):
                for tag in tags[:32]:
                    value = _text(tag, 40)
                    if value and value not in safe_tags:
                        safe_tags.append(value)
                    if len(safe_tags) >= 8:
                        break
            result.append(
                {
                    "date": selected_date,
                    "summary": _text(item.get("summary"), 220),
                    "body": _text(item.get("body"), 520),
                    "share_seed": _text(item.get("share_seed"), 180),
                    "tags": safe_tags,
                    "today_events": _event_list(item.get("today_events") or story.get("today_events")),
                    "proactive_events": _event_list(
                        item.get("proactive_events") or story.get("proactive_events")
                    ),
                    "long_term_events": _event_list(
                        item.get("long_term_events") or story.get("long_term_events")
                    ),
                }
            )
            if len(result) >= 4:
                break
        return result

    def _photo_candidates(
        self,
        data: dict[str, Any],
        selected_date: str,
        generation: str,
    ) -> list[dict[str, Any]]:
        candidates: list[tuple[str, dict[str, Any]]] = []
        current = data.get("daily_outfit_photo")
        if isinstance(current, dict):
            candidates.append(("daily_outfit", current))
        history = data.get("daily_outfit_history")
        if isinstance(history, list):
            candidates.extend(
                ("daily_outfit", item)
                for item in reversed(history[-128:])
                if isinstance(item, dict)
            )
        recent = data.get("recent_photo_generations")
        if isinstance(recent, list):
            candidates.extend(
                ("recent_photo", item)
                for item in recent[:256]
                if isinstance(item, dict)
            )

        result: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()
        for position, (default_kind, item) in enumerate(candidates):
            generated_at = _timestamp(item.get("generated_at") or item.get("ts"))
            date_key = _date_text(item.get("date")) or self._date_from_timestamp(generated_at)
            if date_key != selected_date:
                continue
            raw_path = item.get("path")
            path_identity = os.fspath(raw_path) if isinstance(raw_path, (str, os.PathLike)) else ""
            raw_kind = _text(item.get("kind"), 40).lower()
            kind = default_kind
            if default_kind == "recent_photo" and "life" in raw_kind:
                kind = "life_photo"
            identity_key = (kind, date_key, path_identity)
            if identity_key in seen:
                continue
            seen.add(identity_key)
            opaque_identity = f"photo|{generation}|{date_key}|{kind}|{position}|{path_identity}"
            result.append(
                {
                    "id": f"photo_{self._opaque(opaque_identity)}",
                    "date": date_key,
                    "kind": kind,
                    "generated_at": generated_at,
                    "_raw_path": raw_path,
                }
            )
        result.sort(key=lambda item: item["generated_at"], reverse=True)
        return result[:8]

    def _prepare_photos_sync(
        self,
        plugin: Any,
        candidates: list[dict[str, Any]],
        generation: str,
    ) -> tuple[list[dict[str, Any]], list[_PhotoBlob | None]]:
        roots = self._trusted_roots(plugin)
        rows: list[dict[str, Any]] = []
        registrations: list[_PhotoBlob | None] = []
        for candidate in candidates[:8]:
            raw_path = candidate.get("_raw_path")
            row = {
                "id": candidate["id"],
                "date": candidate["date"],
                "kind": candidate["kind"],
                "generated_at": candidate["generated_at"],
                "available": False,
                "error_code": "memory_page_photo_unavailable",
                "photo_ref": "",
            }
            blob: _PhotoBlob | None = None
            try:
                root, parts = self._authorize_photo_path(raw_path, roots)
                blob = self._read_authorized_photo(root, parts)
                row["available"] = True
                row["error_code"] = ""
            except MemoryPageSnapshotError as error:
                row["error_code"] = error.code
            rows.append(row)
            registrations.append(blob)
        return rows, registrations

    def _stage_photo_refs(
        self,
        rows: list[dict[str, Any]],
        blobs: list[_PhotoBlob | None],
        generation: str,
    ) -> tuple[list[dict[str, Any]], list[tuple[str, _PhotoBlob]]]:
        self._require_ready(generation)
        staged: list[tuple[str, _PhotoBlob]] = []
        for row, blob in zip(rows, blobs):
            if blob is None:
                continue
            identity = "|".join(
                (
                    generation,
                    str(blob.device),
                    str(blob.inode),
                    str(blob.size),
                    str(blob.mtime_ns),
                    blob.sha256,
                )
            )
            photo_ref = f"mphoto_{generation[:12]}_{self._opaque(identity)}"
            row["photo_ref"] = photo_ref
            staged.append((photo_ref, blob))
        self._require_ready(generation)
        return rows, staged

    def _commit_photo_refs(
        self,
        staged: list[tuple[str, _PhotoBlob]],
        generation: str,
    ) -> None:
        self._require_ready(generation)
        now = self._clock()
        with self._photo_refs_lock:
            previous = self._photo_refs.copy()
            try:
                self._require_ready(generation)
                self._prune_refs_locked(now)
                for photo_ref, blob in staged:
                    self._photo_refs[photo_ref] = _PhotoRegistration(
                        root=blob.root,
                        parts=blob.parts,
                        device=blob.device,
                        inode=blob.inode,
                        size=blob.size,
                        mtime_ns=blob.mtime_ns,
                        mime_type=blob.mime_type,
                        sha256=blob.sha256,
                        expires_at=now + MEMORY_PAGE_PHOTO_REF_TTL_SECONDS,
                    )
                    self._photo_refs.move_to_end(photo_ref)
                while len(self._photo_refs) > MEMORY_PAGE_PHOTO_REF_MAX_ENTRIES:
                    self._photo_refs.popitem(last=False)
                self._require_ready(generation)
            except BaseException:
                self._photo_refs.clear()
                self._photo_refs.update(previous)
                raise

    def _lookup_photo_ref(self, photo_ref: str, generation: str) -> _PhotoRegistration:
        now = self._clock()
        with self._photo_refs_lock:
            self._require_ready(generation)
            registration = self._photo_refs.get(photo_ref)
            if registration is None:
                self._prune_refs_locked(now)
                raise MemoryPageSnapshotError("memory_page_photo_ref_expired")
            if registration.expires_at <= now:
                self._photo_refs.pop(photo_ref, None)
                self._prune_refs_locked(now)
                raise MemoryPageSnapshotError("memory_page_photo_ref_expired")
            self._prune_refs_locked(now, preserve=photo_ref)
            self._photo_refs.move_to_end(photo_ref)
            return registration

    def _recheck_photo_ref(
        self,
        photo_ref: str,
        registration: _PhotoRegistration,
        generation: str,
    ) -> None:
        now = self._clock()
        with self._photo_refs_lock:
            self._require_ready(generation)
            current = self._photo_refs.get(photo_ref)
            if current is not registration:
                self._prune_refs_locked(now)
                raise MemoryPageSnapshotError("memory_page_photo_ref_expired")
            if registration.expires_at <= now:
                self._photo_refs.pop(photo_ref, None)
                self._prune_refs_locked(now)
                raise MemoryPageSnapshotError("memory_page_photo_ref_expired")

    def _prune_refs_locked(self, now: float, *, preserve: str = "") -> None:
        for key, item in list(self._photo_refs.items()):
            if key != preserve and item.expires_at <= now:
                self._photo_refs.pop(key, None)

    @staticmethod
    def _validate_photo_ref(photo_ref: Any, generation: str) -> str:
        if not isinstance(photo_ref, str):
            raise MemoryPageSnapshotError("memory_page_photo_ref_invalid")
        match = _PHOTO_REF_RE.fullmatch(photo_ref)
        if not match:
            raise MemoryPageSnapshotError("memory_page_photo_ref_invalid")
        if match.group(1) != generation[:12]:
            raise MemoryPageSnapshotError("memory_page_photo_ref_stale")
        return photo_ref

    def _opaque(self, identity: str) -> str:
        digest = hmac.new(self._secret, identity.encode("utf-8"), hashlib.sha256).digest()[:16]
        return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")

    @staticmethod
    def _trusted_roots(plugin: Any) -> tuple[Path, ...]:
        candidates: list[Any] = [
            getattr(plugin, "data_dir", None),
            getattr(plugin, "plugin_data_dir", None),
        ]
        data_file = getattr(plugin, "data_file", None)
        if isinstance(data_file, (str, os.PathLike)):
            candidates.append(Path(data_file).parent)
        roots: list[Path] = []
        for raw in candidates:
            if not isinstance(raw, (str, os.PathLike)):
                continue
            try:
                root = Path(raw).resolve(strict=True)
                if not root.is_dir() or root in roots:
                    continue
            except (OSError, RuntimeError, ValueError):
                continue
            roots.append(root)
        return tuple(roots)

    @staticmethod
    def _authorize_photo_path(raw_path: Any, roots: tuple[Path, ...]) -> tuple[Path, tuple[str, ...]]:
        if not isinstance(raw_path, (str, os.PathLike)):
            raise MemoryPageSnapshotError("memory_page_photo_unavailable")
        try:
            value = os.fspath(raw_path)
        except TypeError:
            raise MemoryPageSnapshotError("memory_page_photo_unavailable") from None
        if not isinstance(value, str) or not value or "\x00" in value:
            raise MemoryPageSnapshotError("memory_page_photo_unavailable")
        separators = [os.sep]
        if os.altsep:
            separators.append(os.altsep)
        raw_parts = [value]
        for separator in separators:
            raw_parts = [piece for part in raw_parts for piece in part.split(separator)]
        if any(part in {".", ".."} for part in raw_parts):
            raise MemoryPageSnapshotError("memory_page_photo_unavailable")
        candidate = Path(value)
        for root in roots:
            try:
                lexical = Path(os.path.abspath(candidate if candidate.is_absolute() else root / candidate))
                relative = lexical.relative_to(root)
            except (OSError, ValueError):
                continue
            parts = relative.parts
            if not parts or len(parts) > 32 or any(
                not part or part in {".", ".."} or len(part.encode("utf-8")) > 255
                for part in parts
            ):
                continue
            return root, tuple(parts)
        raise MemoryPageSnapshotError("memory_page_photo_unavailable")

    @classmethod
    def _read_authorized_photo(
        cls,
        root: Path,
        parts: tuple[str, ...],
        *,
        expected: _PhotoRegistration | None = None,
    ) -> _PhotoBlob:
        fd = cls._open_nofollow(root, parts)
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode):
                code = "memory_page_photo_changed" if expected is not None else "memory_page_photo_unavailable"
                raise MemoryPageSnapshotError(code)
            if expected is not None and (
                before.st_dev != expected.device
                or before.st_ino != expected.inode
                or before.st_size != expected.size
                or before.st_mtime_ns != expected.mtime_ns
            ):
                raise MemoryPageSnapshotError("memory_page_photo_changed")
            if before.st_size > MEMORY_PAGE_PHOTO_MAX_BYTES:
                raise MemoryPageSnapshotError("memory_page_photo_too_large")
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = os.read(fd, min(1024 * 1024, MEMORY_PAGE_PHOTO_MAX_BYTES + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > MEMORY_PAGE_PHOTO_MAX_BYTES:
                    raise MemoryPageSnapshotError("memory_page_photo_too_large")
            after = os.fstat(fd)
        except MemoryPageSnapshotError:
            raise
        except OSError:
            code = "memory_page_photo_changed" if expected is not None else "memory_page_photo_read_failed"
            raise MemoryPageSnapshotError(code) from None
        finally:
            try:
                os.close(fd)
            except OSError:
                pass
        if (
            before.st_dev != after.st_dev
            or before.st_ino != after.st_ino
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or total != after.st_size
        ):
            raise MemoryPageSnapshotError("memory_page_photo_changed")
        content = b"".join(chunks)
        mime_type = cls._detect_image_mime(content)
        if not mime_type:
            code = "memory_page_photo_changed" if expected is not None else "memory_page_photo_unsupported"
            raise MemoryPageSnapshotError(code)
        digest = hashlib.sha256(content).hexdigest()
        if expected is not None and (mime_type != expected.mime_type or digest != expected.sha256):
            raise MemoryPageSnapshotError("memory_page_photo_changed")
        return _PhotoBlob(
            root=root,
            parts=parts,
            device=after.st_dev,
            inode=after.st_ino,
            size=total,
            mtime_ns=after.st_mtime_ns,
            mime_type=mime_type,
            sha256=digest,
            content=content,
        )

    @staticmethod
    def _open_nofollow(root: Path, parts: tuple[str, ...]) -> int:
        nofollow = getattr(os, "O_NOFOLLOW", None)
        directory = getattr(os, "O_DIRECTORY", None)
        nonblock = getattr(os, "O_NONBLOCK", None)
        if nofollow is None or directory is None or nonblock is None or not parts:
            raise MemoryPageSnapshotError("memory_page_photo_unavailable")
        cloexec = getattr(os, "O_CLOEXEC", 0)
        directory_flags = os.O_RDONLY | directory | nofollow | cloexec
        file_flags = os.O_RDONLY | nofollow | nonblock | cloexec
        opened_directory = -1
        try:
            opened_directory = os.open(os.fspath(root), directory_flags)
            for part in parts[:-1]:
                next_directory = os.open(part, directory_flags, dir_fd=opened_directory)
                os.close(opened_directory)
                opened_directory = next_directory
            result = os.open(parts[-1], file_flags, dir_fd=opened_directory)
        except (OSError, TypeError, NotImplementedError):
            raise MemoryPageSnapshotError("memory_page_photo_unavailable") from None
        finally:
            if opened_directory >= 0:
                try:
                    os.close(opened_directory)
                except OSError:
                    pass
        return result

    @staticmethod
    def _detect_image_mime(content: bytes) -> str:
        if content.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if content.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if content.startswith((b"GIF87a", b"GIF89a")):
            return "image/gif"
        if content.startswith(b"BM"):
            return "image/bmp"
        if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
            return "image/webp"
        if (
            len(content) >= 12
            and content[4:8] == b"ftyp"
            and content[8:12] in {b"avif", b"avis"}
        ):
            return "image/avif"
        return ""

    @classmethod
    def _read_photo_registration_sync(cls, registration: _PhotoRegistration) -> _PhotoBlob:
        try:
            return cls._read_authorized_photo(
                registration.root,
                registration.parts,
                expected=registration,
            )
        except MemoryPageSnapshotError as error:
            if error.code == "memory_page_photo_unavailable":
                raise MemoryPageSnapshotError("memory_page_photo_changed") from None
            raise
