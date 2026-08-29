from __future__ import annotations

import time
from typing import Any

from .helpers import _safe_float, _safe_int, _single_line


class _MemoryCapabilityFamily:
    """Private capability family backed only by its owning façade."""

    __slots__ = ("_owner",)

    def __init__(self, owner: Any) -> None:
        self._owner = owner

    async def record_game_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Apply one idempotent, per-user game event to companion afterglow."""
        return await self._owner._plugin._record_external_game_event(payload)

    def memory_page_capabilities(self) -> dict[str, Any]:
        """Describe the versioned, read-only Memory Page producer API."""
        return self._owner._memory_page_service.capabilities()

    async def export_memory_page_snapshot(
        self,
        *,
        target_plugin_id: str,
        selected_date: str = "",
    ) -> dict[str, Any]:
        """Export a bounded, path-free Memory Page snapshot."""
        return await self._owner._memory_page_service.export_snapshot(
            target_plugin_id=target_plugin_id,
            selected_date=selected_date,
        )

    async def read_memory_page_photo(
        self,
        *,
        target_plugin_id: str,
        photo_ref: str,
    ) -> dict[str, Any]:
        """Read one generation-bound photo reference after strict revalidation."""
        return await self._owner._memory_page_service.read_photo(
            target_plugin_id=target_plugin_id,
            photo_ref=photo_ref,
        )

    def record_external_realtime_continuity(
        self,
        user_id: str,
        *,
        summary: str,
        public_summary: str = "",
        facts: list[str] | None = None,
        ttl_seconds: int = 21600,
        activity_id: str = "",
    ) -> dict[str, Any]:
        """Store bounded post-call continuity without writing long-term memory."""
        key = _single_line(user_id, 80)
        text = _single_line(summary, 2200)
        if not key or not text:
            return {}
        registry = getattr(self._owner._plugin, "_external_realtime_continuity", None)
        if not isinstance(registry, dict):
            registry = {}
            self._owner._plugin._external_realtime_continuity = registry
        now = time.time()
        ttl = _safe_int(ttl_seconds, 21600, 300, 86400)
        bounded_facts = [
            _single_line(item, 240)
            for item in (facts or [])
            if _single_line(item, 240)
        ][:8]
        item = {
            "user_id": key,
            "summary": text,
            "public_summary": _single_line(public_summary, 360),
            "facts": bounded_facts,
            "activity_id": _single_line(activity_id, 120),
            "updated_at": now,
            "expires_at": now + ttl,
        }
        registry[key] = item
        return dict(item)

    def get_external_realtime_continuity(self, *, user_id: str = "", public: bool = False) -> dict[str, Any]:
        registry = getattr(self._owner._plugin, "_external_realtime_continuity", None)
        if not isinstance(registry, dict):
            return {}
        now = time.time()
        for key, item in list(registry.items()):
            if not isinstance(item, dict) or _safe_float(item.get("expires_at"), 0.0) <= now:
                registry.pop(key, None)
        key = _single_line(user_id, 80)
        item = registry.get(key) if key else None
        if not isinstance(item, dict):
            return {}
        result = dict(item)
        if public:
            result["summary"] = _single_line(result.get("public_summary"), 360)
            result["facts"] = []
        return result if _single_line(result.get("summary"), 2200) else {}
