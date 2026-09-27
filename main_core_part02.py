# -*- coding: utf-8 -*-
"""PrivateCompanionExtensionAPIPart02Mixin。

由 tools/split_mixin_domain.py 从 main.py 机械抽取（29 个方法 + 0 个模块级名字 + 0 个类级赋值 / 295 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionExtensionAPI）。
"""
from __future__ import annotations

from .helpers import _flat_get, _safe_int, _single_line
from copy import deepcopy
from typing import Any



class PrivateCompanionExtensionAPIPart02Mixin:
    """PrivateCompanionExtensionAPIPart02Mixin（从 PrivateCompanionExtensionAPI 拆出）。"""


    def export_reality_touch_legacy_state(self) -> dict[str, Any]:
        """Return a detached one-time migration payload for Reality Companion."""
        plugin = self._plugin
        source_config = getattr(plugin, "config", {})

        def legacy_bool(key: str, default: bool = False) -> bool:
            value = _flat_get(source_config, key, default)
            if isinstance(value, str):
                normalized = value.strip().lower()
                if normalized in {"true", "1", "yes", "y", "on", "enable", "enabled", "启用", "开启", "开", "是"}:
                    return True
                if normalized in {"false", "0", "no", "n", "off", "disable", "disabled", "停用", "关闭", "关", "否", ""}:
                    return False
            return bool(value)

        def legacy_int(key: str, default: int, minimum: int, maximum: int) -> int:
            return _safe_int(_flat_get(source_config, key, default), default, minimum, maximum)

        source_users = plugin.data.get("users") if isinstance(plugin.data, dict) else None
        allowed_keys = {
            "user_id",
            "umo",
            "nickname",
            "last_display_name",
            "display_name",
            "reality_touch_consent",
            "reality_touch_pending_consent",
            "reality_touch_policy",
            "reality_touch_camera_consent",
            "reality_touch_camera_policy",
            "wakeup_alarm",
            "reality_touch_reminders",
        }
        users: dict[str, dict[str, Any]] = {}
        if isinstance(source_users, dict):
            for user_id, user in source_users.items():
                if not isinstance(user, dict):
                    continue
                selected = {
                    key: deepcopy(value)
                    for key, value in user.items()
                    if key in allowed_keys
                }
                if any(key.startswith("reality_touch") or key == "wakeup_alarm" for key in selected):
                    selected.setdefault("user_id", _single_line(user_id, 120))
                    users[_single_line(user_id, 120)] = selected
        store = plugin.data.get("reality_touch") if isinstance(plugin.data, dict) else None
        config = {
            "enabled": legacy_bool("enable_experimental_bluetooth_wakeup"),
            "camera_enabled": legacy_bool("enable_reality_touch_camera"),
            "camera_index": legacy_int("reality_touch_camera_index", 0, 0, 100000),
            "camera_min_interval_seconds": legacy_int("reality_touch_camera_min_interval_seconds", 60, 10, 3600),
            "camera_capture_timeout_seconds": legacy_int("reality_touch_camera_capture_timeout_seconds", 5, 2, 20),
            "camera_analysis_timeout_seconds": legacy_int("reality_touch_camera_analysis_timeout_seconds", 25, 5, 90),
            "camera_proactive_curiosity_enabled": legacy_bool("enable_reality_touch_camera_proactive_curiosity"),
            "camera_proactive_min_tier": legacy_int("reality_touch_camera_proactive_min_tier", 4, 1, 5),
            "camera_proactive_max_daily": legacy_int("reality_touch_camera_proactive_max_daily", 1, 0, 10),
            "camera_proactive_cooldown_minutes": legacy_int("reality_touch_camera_proactive_cooldown_minutes", 240, 10, 1440),
            "audio_default_playback_volume": legacy_int("tts_local_playback_volume", 35, 0, 100),
        }
        return {
            "version": 1,
            "users": users,
            "reality_touch": deepcopy(store) if isinstance(store, dict) else {},
            "config": config,
        }

    async def generate_reality_touch_text(self, prompt: str, **kwargs: Any) -> str:
        """Generate bounded device-facing wording through the host model stack."""
        caller = getattr(self._plugin, "_llm_call", None)
        if not callable(caller):
            return ""
        return str(await caller(prompt, **kwargs) or "")

    async def send_reality_touch_chat(self, umo: str, text: str) -> bool:
        return await self._content_family.send_reality_touch_chat(
            umo,
            text,
        )

    async def record_reality_touch_output(
        self,
        user_id: str,
        text: str,
        *,
        source: str = "reality_touch_audio",
        delivered_at: float | None = None,
    ) -> dict[str, Any]:
        """Record speech delivered outside chat so the next reply can continue it."""
        return await self._content_family.record_reality_touch_output(
            user_id,
            text,
            source=source,
            delivered_at=delivered_at,
        )

    def get_reality_touch_cron_manager(self) -> Any | None:
        return self._scheduler_family.get_reality_touch_cron_manager()

    async def delete_reality_touch_cron_job(self, job_id: str) -> tuple[bool, str]:
        return await self._scheduler_family.delete_reality_touch_cron_job(
            job_id,
        )

    def get_bot_identity(self) -> dict[str, Any]:
        """Return a stable Bot identity without guessing between multiple accounts."""
        return self._identity_family.get_bot_identity()

    def get_unified_person_contract(self) -> dict[str, Any]:
        return self._identity_family.get_unified_person_contract()

    def resolve_unified_person(self, identity: dict[str, Any]) -> dict[str, Any]:
        return self._identity_family.resolve_unified_person(
            identity,
        )

    def create_unified_person(
        self,
        identity: dict[str, Any],
        *,
        profile: dict[str, Any] | None = None,
        operation_id: str = "",
    ) -> dict[str, Any]:
        return self._identity_family.create_unified_person(
            identity,
            profile=profile,
            operation_id=operation_id,
        )

    def get_unified_person_projection(self, person_id: str) -> dict[str, Any] | None:
        return self._identity_family.get_unified_person_projection(
            person_id,
        )

    def get_p6_readonly_status(self) -> dict[str, Any]:
        """Expose bounded Unified Person counts without an authority surface."""
        return self._diagnostics_family.get_p6_readonly_status()

    def get_unified_person_context(self, event: Any | None = None) -> dict[str, Any]:
        return self._identity_family.get_unified_person_context(
            event,
        )

    def get_scene_context(self, user_id: str = "") -> dict[str, Any]:
        """Return the current structured Bot-life context for plugin integrations."""
        return self._diagnostics_family.get_scene_context(
            user_id,
        )

    def get_realtime_context(self, user_id: str = "", purpose: str = "together") -> dict[str, Any]:
        """Return the full structured scene and its canonical prompt representation."""
        return self._diagnostics_family.get_realtime_context(
            user_id,
            purpose,
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
        return self._memory_family.record_external_realtime_continuity(
            user_id,
            summary=summary,
            public_summary=public_summary,
            facts=facts,
            ttl_seconds=ttl_seconds,
            activity_id=activity_id,
        )

    def get_external_realtime_continuity(self, *, user_id: str = "", public: bool = False) -> dict[str, Any]:
        return self._memory_family.get_external_realtime_continuity(
            user_id=user_id,
            public=public,
        )

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
        return self._scheduler_family.notify_external_activity_started(
            activity_id,
            user_id=user_id,
            kind=kind,
            label=label,
            source_plugin=source_plugin,
            ttl_seconds=ttl_seconds,
            metadata=metadata,
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
        return self._scheduler_family.notify_external_activity_updated(
            activity_id,
            user_id=user_id,
            kind=kind,
            label=label,
            source_plugin=source_plugin,
            ttl_seconds=ttl_seconds,
            metadata=metadata,
        )

    def notify_external_activity_ended(self, activity_id: str) -> bool:
        return self._scheduler_family.notify_external_activity_ended(
            activity_id,
        )

    def get_external_activity(self, *, user_id: str = "", activity_id: str = "") -> dict[str, Any]:
        return self._scheduler_family.get_external_activity(
            user_id=user_id,
            activity_id=activity_id,
        )

    async def prepare_proactive_chat(
        self,
        session_id: str,
        *,
        unanswered_count: int = 0,
    ) -> dict[str, Any]:
        return await self._plugin._prepare_proactive_chat_bridge(
            session_id,
            unanswered_count=unanswered_count,
        )

    async def review_proactive_chat_message(
        self,
        session_id: str,
        text: str,
        *,
        token: str = "",
    ) -> dict[str, Any]:
        return await self._plugin._review_proactive_chat_bridge_message(
            session_id,
            text,
            token=token,
        )

    async def notify_proactive_chat_sent(
        self,
        session_id: str,
        text: str,
        *,
        token: str = "",
    ) -> dict[str, Any]:
        return await self._plugin._record_proactive_chat_bridge_sent(
            session_id,
            text,
            token=token,
        )

    async def cancel_proactive_chat(
        self,
        session_id: str,
        *,
        token: str = "",
    ) -> bool:
        return await self._plugin._cancel_proactive_chat_bridge(
            session_id,
            token=token,
        )

    def resolve_historical_chat_identities(self, speakers: list[str]) -> dict[str, Any]:
        return self._identity_family.resolve_historical_chat_identities(
            speakers,
        )

    async def stage_historical_relationship_observations(
        self,
        *,
        user_id: str,
        user_name: str,
        batch_id: str,
        observations: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return await self._relationship_family.stage_historical_relationship_observations(
            user_id=user_id,
            user_name=user_name,
            batch_id=batch_id,
            observations=observations,
        )

    async def rebind_historical_relationship_observations(
        self,
        *,
        batch_id: str,
        old_user_id: str,
        user_id: str,
        user_name: str = "",
    ) -> dict[str, Any]:
        """Move one imported batch of traceable pending and confirmed relationship observations."""
        return await self._relationship_family.rebind_historical_relationship_observations(
            batch_id=batch_id,
            old_user_id=old_user_id,
            user_id=user_id,
            user_name=user_name,
        )

    async def rollback_historical_relationship_observations(self, batch_id: str) -> dict[str, Any]:
        return await self._relationship_family.rollback_historical_relationship_observations(
            batch_id,
        )
