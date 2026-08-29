from __future__ import annotations

import time
from typing import Any

from .helpers import _safe_float, _safe_int, _single_line


class _SchedulerCapabilityFamily:
    """Private capability family backed only by its owning façade."""

    __slots__ = ("_owner",)

    def __init__(self, owner: Any) -> None:
        self._owner = owner

    def register_proactive_ability(self, spec: dict[str, Any]) -> bool:
        return self._owner._plugin.register_external_proactive_ability(spec)

    def unregister_proactive_ability(self, name: str) -> bool:
        return self._owner._plugin.unregister_external_proactive_ability(name)

    def list_proactive_abilities(self) -> list[dict[str, Any]]:
        return self._owner._plugin.external_proactive_abilities()

    async def notify_mobile_location_update(self, user_id: str) -> dict[str, Any]:
        """Let the mobile gateway wake location-aware proactive planning promptly."""
        return await self._owner._plugin._handle_mobile_location_update(user_id)

    def get_reality_touch_cron_manager(self) -> Any | None:
        getter = getattr(self._owner._plugin, "_official_cron_manager", None)
        return getter() if callable(getter) else None

    async def delete_reality_touch_cron_job(self, job_id: str) -> tuple[bool, str]:
        deleter = getattr(self._owner._plugin, "_delete_official_llm_timer_job", None)
        if not callable(deleter):
            return False, "AstrBot 官方 Cron 不可用"
        return await deleter(job_id)

    def notify_external_activity_started(
        self,
        activity_id: str,
        *,
        user_id: str = "",
        kind: str = "external",
        label: str = "",
        source_plugin: str = "external",
        ttl_seconds: int = 240,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._upsert_external_activity(
            activity_id,
            user_id=user_id,
            kind=kind,
            label=label,
            source_plugin=source_plugin,
            ttl_seconds=ttl_seconds,
            metadata=metadata,
            preserve_started_at=False,
        )

    def notify_external_activity_updated(
        self,
        activity_id: str,
        *,
        user_id: str = "",
        kind: str = "",
        label: str = "",
        source_plugin: str = "",
        ttl_seconds: int = 240,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._upsert_external_activity(
            activity_id,
            user_id=user_id,
            kind=kind,
            label=label,
            source_plugin=source_plugin,
            ttl_seconds=ttl_seconds,
            metadata=metadata,
            preserve_started_at=True,
        )

    def notify_external_activity_ended(self, activity_id: str) -> bool:
        activity_key = _single_line(activity_id, 120)
        registry = getattr(self._owner._plugin, "_external_realtime_activities", None)
        return bool(activity_key and isinstance(registry, dict) and registry.pop(activity_key, None))

    def get_external_activity(self, *, user_id: str = "", activity_id: str = "") -> dict[str, Any]:
        registry = getattr(self._owner._plugin, "_external_realtime_activities", None)
        if not isinstance(registry, dict):
            return {}
        now = time.time()
        expired = [
            key
            for key, item in registry.items()
            if not isinstance(item, dict) or _safe_float(item.get("expires_at"), 0.0) <= now
        ]
        for key in expired:
            registry.pop(key, None)
        activity_key = _single_line(activity_id, 120)
        if activity_key:
            item = registry.get(activity_key)
            return dict(item) if isinstance(item, dict) else {}
        normalized_user_id = _single_line(user_id, 80)
        matches = [
            item
            for item in registry.values()
            if isinstance(item, dict)
            and (not normalized_user_id or not item.get("user_id") or item.get("user_id") == normalized_user_id)
        ]
        if not matches:
            return {}
        return dict(max(matches, key=lambda item: _safe_float(item.get("updated_at"), 0.0)))

    def _upsert_external_activity(
        self,
        activity_id: str,
        *,
        user_id: str,
        kind: str,
        label: str,
        source_plugin: str,
        ttl_seconds: int,
        metadata: dict[str, Any] | None,
        preserve_started_at: bool,
    ) -> dict[str, Any]:
        activity_key = _single_line(activity_id, 120)
        if not activity_key:
            return {}
        registry = getattr(self._owner._plugin, "_external_realtime_activities", None)
        if not isinstance(registry, dict):
            registry = {}
            self._owner._plugin._external_realtime_activities = registry
        existing = registry.get(activity_key) if preserve_started_at else None
        existing = existing if isinstance(existing, dict) else {}
        now = time.time()
        ttl = _safe_int(ttl_seconds, 240, 30, 3600)
        item = {
            "activity_id": activity_key,
            "user_id": _single_line(user_id, 80) or _single_line(existing.get("user_id"), 80),
            "kind": _single_line(kind, 40) or _single_line(existing.get("kind"), 40) or "external",
            "label": _single_line(label, 100) or _single_line(existing.get("label"), 100),
            "source_plugin": _single_line(source_plugin, 100)
            or _single_line(existing.get("source_plugin"), 100)
            or "external",
            "started_at": _safe_float(existing.get("started_at"), now) if existing else now,
            "updated_at": now,
            "expires_at": now + ttl,
            "metadata": dict(metadata) if isinstance(metadata, dict) else dict(existing.get("metadata") or {}),
        }
        registry[activity_key] = item
        return dict(item)
