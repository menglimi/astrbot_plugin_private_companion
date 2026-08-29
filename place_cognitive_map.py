# -*- coding: utf-8 -*-
"""User-confirmed place knowledge derived from the optional mobile bridge."""
from __future__ import annotations

import asyncio
import hashlib
import time
from datetime import datetime, timezone
from typing import Any


from .helpers import _safe_float, _single_line
from .scoped_domain_contract import build_scoped_domain_payload
from .logging_util import get_module_logger

logger = get_module_logger(__name__)


_MAP_STORE_KEY = "place_cognitive_maps"
_MAP_VERSION = 1
_MAX_PLACES_PER_USER = 24
_MAX_ROUTES_PER_USER = 32
_PLACE_TRANSITION_MAX_SECONDS = 8 * 60 * 60
_PLACE_KINDS = frozenset({"home", "work", "custom"})
_PLACE_KIND_LABELS = {"home": "家", "work": "工作地点", "custom": "自定义地点"}


class PlaceCognitiveMapMixin:
    """Keep a small, consent-based semantic map without retaining coordinates."""

    @staticmethod
    def _place_cognitive_map_now_ts() -> float:
        return time.time()

    @staticmethod
    def _place_cognitive_map_key(name: str, kind: str) -> str:
        return f"{kind}:{''.join(name.casefold().split())}"

    @staticmethod
    def _place_cognitive_map_iso(timestamp: float) -> str:
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat(timespec="seconds")

    @staticmethod
    def _place_cognitive_map_place(mobile_location: Any) -> dict[str, Any]:
        location = mobile_location if isinstance(mobile_location, dict) else {}
        raw = location.get("place") if isinstance(location.get("place"), dict) else {}
        if (
            not bool(location.get("available"))
            or not bool(raw.get("matched"))
            or _single_line(raw.get("confidence"), 32) not in {"", "confirmed"}
        ):
            return {}
        name = _single_line(raw.get("name"), 48)
        kind = _single_line(raw.get("kind"), 24).casefold()
        if not name:
            return {}
        if kind not in _PLACE_KINDS:
            kind = "custom"
        return {
            "key": PlaceCognitiveMapMixin._place_cognitive_map_key(name, kind),
            "name": name,
            "kind": kind,
            "radius_m": round(_safe_float(raw.get("radius_m"), 0.0, 0.0, 10000.0), 1),
            "aliases": list(dict.fromkeys(
                _single_line(item, 40)
                for item in list(raw.get("aliases") or [])[:8]
                if _single_line(item, 40) and _single_line(item, 40) != name
            )),
            "parent_name": _single_line(raw.get("parent_name"), 48),
        }

    def _place_cognitive_map_root(self) -> dict[str, Any]:
        data = getattr(self, "data", None)
        if not isinstance(data, dict):
            return {}
        root = data.get(_MAP_STORE_KEY)
        if not isinstance(root, dict):
            root = {}
            data[_MAP_STORE_KEY] = root
        return root

    def _place_cognitive_map_user_state(self, store_key: str) -> dict[str, Any]:
        root = self._place_cognitive_map_root()
        if not isinstance(root, dict) or not store_key:
            return {}
        state = root.get(store_key)
        if not isinstance(state, dict):
            state = {
                "version": _MAP_VERSION,
                "places": {},
                "routes": {},
                "current_place_key": "",
                "last_departed_place_key": "",
                "last_departed_at_ts": 0.0,
                "last_transition": {},
                "updated_at": "",
            }
            root[store_key] = state
        state["version"] = _MAP_VERSION
        if not isinstance(state.get("places"), dict):
            state["places"] = {}
        if not isinstance(state.get("routes"), dict):
            state["routes"] = {}
        return state

    @staticmethod
    def _place_cognitive_map_event(
        *,
        event: str,
        subject_ref: str,
        place: dict[str, Any],
        timestamp: float,
        previous_place: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        name = _single_line(place.get("name"), 48)
        key = _single_line(place.get("key"), 96)
        event_id = f"place-{event}:{subject_ref}:{key}:{int(timestamp)}"
        if event == "arrival":
            title = f"用户到达已确认地点：{name}"
        else:
            title = f"用户离开已确认地点：{name}"
        if event == "arrival" and isinstance(previous_place, dict):
            previous_name = _single_line(previous_place.get("name"), 48)
            if previous_name:
                title += f"（从{previous_name}前往）"
        return {
            "visibility": "private",
            "activity_id": event_id,
            "title": title,
            "kind": f"confirmed_place_{event}",
            "start_at": PlaceCognitiveMapMixin._place_cognitive_map_iso(timestamp),
            "source_refs": [f"reality:mobile_place:{subject_ref}:{key}"],
        }

    def _place_cognitive_map_emit_memory_event(
        self,
        event: dict[str, Any],
        namespace: Any | None = None,
    ) -> None:
        if namespace is not None:
            bridge_getter = getattr(self, "_memory_companion_bridge", None)
            upsert = getattr(self, "_memory_companion_upsert_scoped_record", None)
            bridge = bridge_getter() if callable(bridge_getter) else None
            if bridge is None or not callable(upsert):
                return
            event_id = _single_line(event.get("activity_id"), 240)
            if not event_id:
                return
            record_id = "place-event:" + hashlib.sha256(event_id.encode("utf-8")).hexdigest()
            payload = build_scoped_domain_payload(
                domain="memory",
                source_kind="private",
                source_revision=1,
                content={
                    "memory_type": "confirmed_place_event",
                    "title": _single_line(event.get("title"), 300),
                    "kind": _single_line(event.get("kind"), 80),
                    "start_at": _single_line(event.get("start_at"), 80),
                    "source_refs": list(event.get("source_refs") or [])[:4],
                },
            )
            result = upsert(
                bridge, namespace, record_kind="memory", record_id=record_id,
                revision=1, payload=payload, event_id=event_id,
            )
            if not isinstance(result, dict) or result.get("ok") is not True:
                logger.debug(
                    "地点认知 scoped memory 写入被拒绝: code=%s",
                    _single_line(result.get("code"), 120) if isinstance(result, dict) else "invalid_result",
                )
            return
        recorder = getattr(self, "_memory_companion_record_observed_activity", None)
        if not callable(recorder):
            return

        async def _record() -> None:
            try:
                await recorder(event)
            except Exception as exc:
                logger.debug("地点认知活动归档失败: %s", _single_line(exc, 160))

        try:
            task = asyncio.create_task(_record())
        except RuntimeError:
            return
        tasks = getattr(self, "_place_cognitive_map_memory_tasks", None)
        if not isinstance(tasks, set):
            tasks = set()
            self._place_cognitive_map_memory_tasks = tasks
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    @staticmethod
    def _place_cognitive_map_summary(
        state: dict[str, Any],
        *,
        include_transition: bool = False,
    ) -> dict[str, Any]:
        places = state.get("places") if isinstance(state.get("places"), dict) else {}
        routes = state.get("routes") if isinstance(state.get("routes"), dict) else {}
        current_key = _single_line(state.get("current_place_key"), 96)
        current = places.get(current_key) if isinstance(places.get(current_key), dict) else {}
        known = sorted(
            (item for item in places.values() if isinstance(item, dict) and _single_line(item.get("name"), 48)),
            key=lambda item: float(item.get("last_seen_ts") or 0),
            reverse=True,
        )[:6]
        route_items = sorted(
            (item for item in routes.values() if isinstance(item, dict)),
            key=lambda item: float(item.get("last_seen_ts") or 0),
            reverse=True,
        )[:4]
        current_place = {
            "name": _single_line(current.get("name"), 48),
            "kind": _single_line(current.get("kind"), 24),
        } if current else {}
        transition = state.get("last_transition") if isinstance(state.get("last_transition"), dict) else {}
        if include_transition and current_place and _single_line(transition.get("kind"), 24) == "arrival":
            current_place["transition_at"] = _single_line(transition.get("at"), 40)
            current_place["previous_place_name"] = _single_line(transition.get("from_name"), 48)
        result = {
            "available": bool(known),
            "current_place": current_place,
            "known_places": [PlaceCognitiveMapMixin._place_cognitive_map_known_place(item) for item in known],
            "recent_routes": [
                {
                    "from_name": _single_line(item.get("from_name"), 48),
                    "to_name": _single_line(item.get("to_name"), 48),
                    "count": max(1, int(item.get("count") or 1)),
                }
                for item in route_items
                if _single_line(item.get("from_name"), 48) and _single_line(item.get("to_name"), 48)
            ],
        }
        if include_transition and transition:
            result["last_transition"] = {
                "kind": _single_line(transition.get("kind"), 24),
                "from_name": _single_line(transition.get("from_name"), 48),
                "from_kind": _single_line(transition.get("from_kind"), 24),
                "to_name": _single_line(transition.get("to_name"), 48),
                "to_kind": _single_line(transition.get("to_kind"), 24),
                "at": _single_line(transition.get("at"), 40),
            }
        return result

    @staticmethod
    def _place_cognitive_map_known_place(item: Any) -> dict[str, Any]:
        place = item if isinstance(item, dict) else {}
        result = {
            "name": _single_line(place.get("name"), 48),
            "kind": _single_line(place.get("kind"), 24),
        }
        aliases = list(place.get("aliases") or [])[:8]
        parent_name = _single_line(place.get("parent_name"), 48)
        if aliases:
            result["aliases"] = aliases
        if parent_name:
            result["parent_name"] = parent_name
        return result

    def _observe_mobile_place_context(
        self,
        user_id: Any,
        mobile_location: Any,
        *,
        observed_at: float | None = None,
        include_transition: bool = False,
    ) -> dict[str, Any]:
        """Observe one authorized location snapshot and return a bounded map view.

        Only an explicitly named place that the mobile app reports as *matched*
        can create a durable map fact. Raw coordinates and unmatched labels are
        intentionally never placed in this store.
        """
        normalized_user_id = _single_line(user_id, 120)
        if not normalized_user_id:
            return {"available": False, "current_place": {}, "known_places": [], "recent_routes": []}
        binder = getattr(self, "_req041_reality_private_binding", None)
        binding = binder(normalized_user_id, purpose="memory_write") if callable(binder) else None
        if callable(binder) and (not isinstance(binding, dict) or binding.get("ok") is not True):
            return {"available": False, "current_place": {}, "known_places": [], "recent_routes": []}
        store_key = _single_line(binding.get("store_key"), 160) if isinstance(binding, dict) else normalized_user_id
        subject_ref = _single_line(binding.get("subject_ref"), 160) if isinstance(binding, dict) else normalized_user_id
        namespace = binding.get("context") if isinstance(binding, dict) else None
        if not store_key or not subject_ref:
            return {"available": False, "current_place": {}, "known_places": [], "recent_routes": []}
        location = mobile_location if isinstance(mobile_location, dict) else {}
        data = getattr(self, "data", None)
        root = data.get(_MAP_STORE_KEY) if isinstance(data, dict) and isinstance(data.get(_MAP_STORE_KEY), dict) else {}
        promoted_legacy = False
        if isinstance(binding, dict) and store_key not in root and isinstance(root.get(normalized_user_id), dict):
            root[store_key] = root.pop(normalized_user_id)
            promoted_legacy = True
            saver = getattr(self, "_schedule_data_save", None)
            if callable(saver):
                saver(sections={"place_cognitive_maps"}, delay=0.5)
        if not bool(location.get("available")):
            existing = root.get(store_key)
            return self._place_cognitive_map_summary(existing, include_transition=include_transition) if isinstance(existing, dict) else {
                "available": False, "current_place": {}, "known_places": [], "recent_routes": [],
            }
        place = self._place_cognitive_map_place(location)
        existing = root.get(store_key)
        if not place and not isinstance(existing, dict):
            return {"available": False, "current_place": {}, "known_places": [], "recent_routes": []}
        state = self._place_cognitive_map_user_state(store_key)
        if not state:
            return {"available": False, "current_place": {}, "known_places": [], "recent_routes": []}
        raw_place = location.get("place") if isinstance(location.get("place"), dict) else {}
        confidence = _single_line(raw_place.get("confidence"), 32)
        if confidence in {"boundary_uncertain", "uncertain", "confirming", "departure_confirming"}:
            # A noisy GPS fix near a boundary must not manufacture a departure
            # event. Wait for an explicit outside/confirmed sample instead.
            return self._place_cognitive_map_summary(state, include_transition=include_transition)
        timestamp = float(observed_at) if isinstance(observed_at, (int, float)) else self._place_cognitive_map_now_ts()
        timestamp = max(0.0, timestamp)
        places = state["places"]
        previous_key = _single_line(state.get("current_place_key"), 96)
        previous = places.get(previous_key) if isinstance(places.get(previous_key), dict) else {}
        changed = False
        events: list[dict[str, Any]] = []

        if not place:
            if previous:
                previous["last_left_at"] = self._place_cognitive_map_iso(timestamp)
                state["current_place_key"] = ""
                state["last_departed_place_key"] = previous_key
                state["last_departed_at_ts"] = timestamp
                state["last_transition"] = {
                    "kind": "departure",
                    "from_name": _single_line(previous.get("name"), 48),
                    "from_kind": _single_line(previous.get("kind"), 24),
                    "to_name": "",
                    "to_kind": "",
                    "at": self._place_cognitive_map_iso(timestamp),
                }
                state["updated_at"] = self._place_cognitive_map_iso(timestamp)
                changed = True
                events.append(self._place_cognitive_map_event(
                    event="departure", subject_ref=subject_ref, place=previous, timestamp=timestamp,
                ))
        else:
            key = place["key"]
            stored = places.get(key)
            if not isinstance(stored, dict):
                stored = {"key": key, "name": place["name"], "kind": place["kind"], "first_seen_at": self._place_cognitive_map_iso(timestamp)}
                places[key] = stored
                changed = True
            stored["name"] = place["name"]
            stored["kind"] = place["kind"]
            stored["aliases"] = place["aliases"]
            stored["parent_name"] = place["parent_name"]
            if place["radius_m"] > 0:
                stored["radius_m"] = place["radius_m"]
            previous_seen_ts = _safe_float(stored.get("last_seen_ts"), 0)
            if previous_seen_ts <= 0 or timestamp - previous_seen_ts >= 60:
                stored["last_seen_ts"] = timestamp
                stored["last_seen_at"] = self._place_cognitive_map_iso(timestamp)
                changed = True

            route_from_key = previous_key
            route_from = previous
            if not route_from:
                departed_key = _single_line(state.get("last_departed_place_key"), 96)
                departed_at = _safe_float(state.get("last_departed_at_ts"), 0)
                departed = places.get(departed_key) if isinstance(places.get(departed_key), dict) else {}
                if departed and 0 <= timestamp - departed_at <= _PLACE_TRANSITION_MAX_SECONDS:
                    route_from_key = departed_key
                    route_from = departed

            if previous_key != key:
                if previous:
                    previous["last_left_at"] = self._place_cognitive_map_iso(timestamp)
                    events.append(self._place_cognitive_map_event(
                        event="departure", subject_ref=subject_ref, place=previous, timestamp=timestamp,
                    ))
                if route_from and route_from_key != key:
                    route_key = f"{route_from_key}>{key}"
                    route = state["routes"].get(route_key)
                    if not isinstance(route, dict):
                        route = {"from_key": route_from_key, "to_key": key, "from_name": route_from.get("name", ""), "to_name": stored["name"], "count": 0}
                        state["routes"][route_key] = route
                    route["from_name"] = _single_line(route_from.get("name"), 48)
                    route["to_name"] = stored["name"]
                    route["count"] = max(0, int(route.get("count") or 0)) + 1
                    route["last_seen_ts"] = timestamp
                    route["last_seen_at"] = self._place_cognitive_map_iso(timestamp)
                state["current_place_key"] = key
                state["last_departed_place_key"] = ""
                state["last_departed_at_ts"] = 0.0
                state["last_transition"] = {
                    "kind": "arrival",
                    "from_name": (
                        _single_line(route_from.get("name"), 48)
                        if route_from_key != key
                        else "外出"
                    ),
                    "from_kind": (
                        _single_line(route_from.get("kind"), 24)
                        if route_from_key != key
                        else "outside"
                    ),
                    "to_name": stored["name"],
                    "to_kind": _single_line(stored.get("kind"), 24),
                    "at": self._place_cognitive_map_iso(timestamp),
                }
                state["updated_at"] = self._place_cognitive_map_iso(timestamp)
                changed = True
                events.append(self._place_cognitive_map_event(
                    event="arrival", subject_ref=subject_ref, place=stored, timestamp=timestamp,
                    previous_place=route_from if route_from_key != key else None,
                ))

        if len(places) > _MAX_PLACES_PER_USER:
            removable = sorted(
                (item for item in places.items() if item[0] != state.get("current_place_key")),
                key=lambda item: float(item[1].get("last_seen_ts") or 0),
            )
            for stale_key, _item in removable[: max(0, len(places) - _MAX_PLACES_PER_USER)]:
                places.pop(stale_key, None)
        routes = state["routes"]
        if len(routes) > _MAX_ROUTES_PER_USER:
            removable = sorted(routes.items(), key=lambda item: float(item[1].get("last_seen_ts") or 0))
            for stale_key, _item in removable[: max(0, len(routes) - _MAX_ROUTES_PER_USER)]:
                routes.pop(stale_key, None)

        if changed:
            saver = getattr(self, "_schedule_data_save", None)
            if callable(saver) and not promoted_legacy:
                saver(sections={"place_cognitive_maps"}, delay=0.5)
            for event in events:
                try:
                    self._place_cognitive_map_emit_memory_event(event, namespace)
                except TypeError:
                    # Preserve small third-party/test subclasses implementing the old hook.
                    self._place_cognitive_map_emit_memory_event(event)
        return self._place_cognitive_map_summary(state, include_transition=include_transition)

    @staticmethod
    def _format_place_cognitive_map_context(cognitive_map: Any) -> str:
        state = cognitive_map if isinstance(cognitive_map, dict) else {}
        known = state.get("known_places") if isinstance(state.get("known_places"), list) else []
        if not known:
            return ""
        place_text = "、".join(
            PlaceCognitiveMapMixin._format_place_cognitive_map_place_item(item)
            for item in known
            if isinstance(item, dict) and _single_line(item.get("name"), 48)
        )
        routes = state.get("recent_routes") if isinstance(state.get("recent_routes"), list) else []
        route_text = "；".join(
            f"{_single_line(item.get('from_name'), 48)}→{_single_line(item.get('to_name'), 48)}（{max(1, int(item.get('count') or 1))}次）"
            for item in routes
            if isinstance(item, dict) and _single_line(item.get("from_name"), 48) and _single_line(item.get("to_name"), 48)
        )
        result = f"已确认地点：{place_text}"
        if route_text:
            result += f"；近期地点间移动：{route_text}"
        return result

    @staticmethod
    def _format_place_cognitive_map_place_item(item: Any) -> str:
        place = item if isinstance(item, dict) else {}
        name = _single_line(place.get("name"), 48)
        kind = _PLACE_KIND_LABELS.get(_single_line(place.get("kind"), 24), "已标记地点")
        aliases = "、".join(
            _single_line(alias, 40) for alias in list(place.get("aliases") or [])[:8] if _single_line(alias, 40)
        )
        parent_name = _single_line(place.get("parent_name"), 48)
        details = [kind]
        if aliases:
            details.append(f"也叫{aliases}")
        if parent_name:
            details.append(f"位于{parent_name}")
        return f"{name}（{'，'.join(details)}）"
