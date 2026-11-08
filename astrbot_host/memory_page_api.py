"""Formal, path-free Memory Page API published by the V2 companion facade.

The memory companion already knows how to consume this contract.  V2 does not
have the V1 plugin internals its one-release legacy adapter requires, so this
adapter provides a small, truthful snapshot surface instead: the bridge is
available, while empty plans/photos/diaries are represented as empty.  The
snapshot contains no filesystem paths, provider objects or host sessions.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo


COMPANION_PLUGIN_ID = "astrbot_plugin_private_companion"
MEMORY_PLUGIN_ID = "astrbot_plugin_memory_companion"
MEMORY_PAGE_API_FAMILY = "companion.memory-page"
MEMORY_PAGE_API_VERSION = "companion.memory-page-api.v1"
MEMORY_PAGE_SNAPSHOT_VERSION = "companion.memory-page-snapshot.v1"
MEMORY_PAGE_PHOTO_VERSION = "companion.memory-page-photo.v1"
MEMORY_PAGE_CAPABILITIES = (
    "memory.page.snapshot.export",
    "memory.page.snapshot.path-free",
    "memory.page.snapshot.read-only",
    "memory.page.photo.read",
)
_SNAPSHOT_HASHED_FIELDS = (
    "version",
    "source_plugin_id",
    "instance_generation",
    "selected_date",
    "available_dates",
    "features",
    "coordination",
    "day",
)


class MemoryPageUnavailable(ValueError):
    """Stable error code for the memory companion's formal bridge."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _local_today() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()


def _date(value: Any, *, allow_empty: bool = True) -> str:
    if value is None:
        value = ""
    if type(value) is not str or len(value) > 10:
        raise MemoryPageUnavailable("memory_page_snapshot_invalid_date")
    if not value and allow_empty:
        return ""
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise MemoryPageUnavailable("memory_page_snapshot_invalid_date") from error
    if parsed.isoformat() != value:
        raise MemoryPageUnavailable("memory_page_snapshot_invalid_date")
    return value


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8", "strict")


def _seal(payload: dict[str, Any]) -> dict[str, Any]:
    hashed = {field: payload[field] for field in _SNAPSHOT_HASHED_FIELDS}
    digest = hashlib.sha256(_canonical_json(hashed)).hexdigest()
    detached = dict(payload)
    detached["snapshot_id"] = f"memorypagesnap_{digest}"
    detached["snapshot_sha256"] = digest
    return detached


def _empty_snapshot(*, generation: str, selected_date: str) -> dict[str, Any]:
    payload = {
        "version": MEMORY_PAGE_SNAPSHOT_VERSION,
        "source_plugin_id": COMPANION_PLUGIN_ID,
        "instance_generation": generation,
        "selected_date": selected_date,
        "available_dates": [selected_date] if selected_date else [],
        "features": {
            "daily_plan_enabled": False,
            "detail_enhancement_enabled": False,
        },
        "coordination": {
            "available": True,
            "state": "ready",
            "reason_code": "",
        },
        "day": {
            "date": selected_date,
            "bot_name": "",
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
        },
    }
    return _seal(payload)


def _short(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit]


def _record_date(record: dict[str, Any]) -> str:
    slot = str((record.get("planning") or {}).get("slot") or "")
    for token in slot.split(":"):
        try:
            return date.fromisoformat(token).isoformat()
        except ValueError:
            continue
    anchor = str((record.get("schedule") or {}).get("anchor") or "")
    try:
        return datetime.fromisoformat(anchor).date().isoformat()
    except ValueError:
        return ""


def _calendar_plans(records: Any) -> tuple[dict[str, list[dict[str, Any]]], set[str]]:
    plans: dict[str, list[dict[str, Any]]] = {}
    dates: set[str] = set()
    for row in records:
        try:
            record = json.loads(row["record_json"])
        except (KeyError, TypeError, ValueError):
            continue
        if not isinstance(record, dict) or record.get("kind") != "role_plan":
            continue
        if str(record.get("state") or "") not in ("accepted", "planned"):
            continue
        day = _record_date(record)
        if not day:
            continue
        anchor = str((record.get("schedule") or {}).get("anchor") or "")
        zone = str((record.get("schedule") or {}).get("timezone_name") or "Asia/Shanghai")
        try:
            moment = datetime.fromisoformat(anchor).astimezone(ZoneInfo(zone))
            clock = moment.strftime("%H:%M")
        except (ValueError, TypeError):
            clock = ""
        plans.setdefault(day, []).append({
            "time": clock,
            "activity": _short(record.get("title"), 180),
            "mood": "",
            "message_seed": _short(record.get("detail"), 220),
        })
        dates.add(day)
    for items in plans.values():
        items.sort(key=lambda item: (item["time"], item["activity"]))
        for index, item in enumerate(items):
            item["index"] = index
        del items[18:]
    return plans, dates


def _daily_state(states: Any, selected_date: str) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for row in states:
        try:
            record = json.loads(row["record_json"])
        except (KeyError, TypeError, ValueError):
            continue
        if isinstance(record, dict) and isinstance(record.get("values"), dict):
            values = record["values"]
            break
    energy = values.get("energy")
    energy = energy if type(energy) is int and 0 <= energy <= 100 else None
    mood = values.get("mood")
    mood_bias = ""
    if isinstance(mood, dict):
        baseline = mood.get("baseline")
        if type(baseline) is int:
            mood_bias = str(baseline)
    return {
        "date": selected_date,
        "energy": energy,
        "mood_bias": _short(mood_bias, 80),
        "sleep": _short(values.get("sleep"), 80),
        "weather": _short(values.get("weather"), 80),
        "note": _short(values.get("presence") or values.get("health"), 180),
    }


def _diaries(rows: Any) -> list[dict[str, Any]]:
    diaries: list[dict[str, Any]] = []
    for row in rows:
        try:
            record = json.loads(row["content_json"])
        except (KeyError, TypeError, ValueError):
            continue
        if not isinstance(record, dict):
            continue
        body = _short(record.get("body"), 8000)
        summary = _short(record.get("summary"), 180) or body[:80]
        kind = str(row["kind"] if "kind" in row.keys() else "")
        diaries.append({
            "date": _short(row["local_date"], 10),
            "summary": summary,
            "body": body,
            "share_seed": _short(record.get("afterglow"), 400),
            "tags": [kind] if kind in ("diary", "dream") else [],
            "today_events": [],
            "proactive_events": [],
            "long_term_events": [],
        })
    return diaries[:8]


class MemoryPageAPI:
    """The exact formal methods consumed by the memory companion."""

    def __init__(self, controller: Any) -> None:
        self.controller = controller
        self.instance_generation = uuid.uuid4().hex

    def capabilities(self) -> dict[str, Any]:
        return {
            "plugin_id": COMPANION_PLUGIN_ID,
            "instance_generation": self.instance_generation,
            "api_family": MEMORY_PAGE_API_FAMILY,
            "api_version": MEMORY_PAGE_API_VERSION,
            "supported_task_versions": [
                MEMORY_PAGE_SNAPSHOT_VERSION,
                MEMORY_PAGE_PHOTO_VERSION,
            ],
            "capabilities": list(MEMORY_PAGE_CAPABILITIES),
            "lifecycle_state": "ready",
            "degraded_reasons": [],
        }

    async def export_snapshot(
        self,
        *,
        target_plugin_id: str = MEMORY_PLUGIN_ID,
        selected_date: str = "",
    ) -> dict[str, Any]:
        if target_plugin_id != MEMORY_PLUGIN_ID:
            raise MemoryPageUnavailable("memory_page_snapshot_state_unavailable")
        selected = _date(selected_date) or _local_today()
        runtime = getattr(self.controller, "runtime", None)
        if runtime is None:
            return _empty_snapshot(generation=self.instance_generation, selected_date=selected)
        try:
            records = runtime.read_all("SELECT record_json FROM calendar_records", ())
            states = runtime.read_all(
                "SELECT record_json FROM role_state_records ORDER BY updated_at DESC LIMIT 1", ())
            journals = runtime.read_all(
                "SELECT content_json, kind, local_date FROM role_journals "
                "WHERE local_date = ? AND state = 'active'", (selected,))
            journal_dates = runtime.read_all(
                "SELECT DISTINCT local_date FROM role_journals WHERE state = 'active'", ())
        except Exception as error:
            raise MemoryPageUnavailable("memory_page_snapshot_state_unavailable") from error
        plans, dates = _calendar_plans(records)
        for row in journal_dates:
            text = str(row["local_date"] or "")
            if len(text) == 10:
                dates.add(text)
        items = plans.get(selected, [])
        state = _daily_state(states, selected)
        diaries = _diaries(journals)
        payload = {
            "version": MEMORY_PAGE_SNAPSHOT_VERSION,
            "source_plugin_id": COMPANION_PLUGIN_ID,
            "instance_generation": self.instance_generation,
            "selected_date": selected,
            "available_dates": sorted(dates, reverse=True),
            "features": {
                "daily_plan_enabled": True,
                "detail_enhancement_enabled": False,
            },
            "coordination": {
                "available": True,
                "state": "ready",
                "reason_code": "",
            },
            "day": {
                "date": selected,
                "bot_name": "",
                "plan": {
                    "date": selected if items else "",
                    "source": "history" if items else "none",
                    "items": items,
                },
                "current_item": {
                    "index": None,
                    "time": "",
                    "activity": "",
                    "mood": "",
                    "message_seed": "",
                },
                "daily_state": state,
                "details": [],
                "photos": [],
                "diaries": diaries,
            },
        }
        return _seal(payload)

    async def read_photo(
        self,
        *,
        target_plugin_id: str = MEMORY_PLUGIN_ID,
        photo_ref: str = "",
    ) -> dict[str, Any]:
        if target_plugin_id != MEMORY_PLUGIN_ID:
            raise MemoryPageUnavailable("memory_page_photo_unavailable")
        raise MemoryPageUnavailable("memory_page_photo_unavailable")


__all__ = [
    "MEMORY_PAGE_API_FAMILY",
    "MEMORY_PAGE_API_VERSION",
    "MEMORY_PAGE_CAPABILITIES",
    "MEMORY_PAGE_SNAPSHOT_VERSION",
    "MemoryPageAPI",
    "MemoryPageUnavailable",
]
