# -*- coding: utf-8 -*-
"""Single runtime side-effect dispatcher for persisted configuration changes."""
from __future__ import annotations

from typing import Any, Mapping

from astrbot.api import logger

from .helpers import _set_today_key_timezone
from .persona_config import runtime_persona_setting


TTS_RUNTIME_KEYS = frozenset(
    {
        "tts_synthesis_backend",
        "tts_provider_id_zh",
        "tts_provider_id_ja",
        "tts_provider_id_en",
        "tts_mimo_tool_name",
        "tts_mimo_voice_name",
        "tts_mimo_style_prompt",
        "tts_generation_mode",
        "tts_voice_language",
        "tts_fishaudio_model",
        "tts_fishaudio_emotion_mode",
        "tts_delivery_mode",
        "tts_foreign_text_mode",
        "tts_message_scope",
        "tts_conversion_scope",
        "tts_conversion_provider_id",
        "tts_extra_prompt",
        "tts_frequency_control_mode",
        "tts_constraint_mode",
        "tts_session_min_interval_seconds",
        "tts_private_min_interval_seconds",
        "tts_group_min_interval_seconds",
        "tts_trigger_probability",
        "tts_private_trigger_probability",
        "tts_group_trigger_probability",
        "enable_tts_local_playback",
        "enable_tts_local_playback_live_only",
        "enable_tts_live_subtitle_sync",
        "tts_live_subtitle_url",
        "tts_local_playback_volume",
        "tts_local_playback_min_interval_seconds",
        "auto_voice_enabled",
        "auto_voice_full_conversion_enabled",
        "auto_voice_probability",
        "auto_voice_max_chars",
        "auto_voice_cooldown_seconds",
        "main_user_voice_probability",
        "main_user_mention_voice_keywords",
        "main_user_mention_voice_probability",
        "main_user_mention_voice_prompt",
    }
)


def _close_unused_awaitable(value: Any) -> None:
    closer = getattr(value, "close", None)
    if callable(closer):
        closer()


def _schedule_proactive_kick(
    plugin: Any,
    *,
    adapter: Any,
    label: str,
) -> None:
    kicker = getattr(plugin, "_kick_proactive_loop_once", None)
    if not callable(kicker):
        return
    try:
        awaitable = kicker()
    except Exception as exc:
        logger.warning(
            "[PrivateCompanion] 配置热更新唤醒失败: error_type=%s",
            type(exc).__name__,
        )
        return
    creator = getattr(adapter, "_create_page_background_task", None)
    if callable(creator):
        try:
            creator(awaitable, label=label)
            return
        except RuntimeError:
            _close_unused_awaitable(awaitable)
            return
    creator = getattr(plugin, "_create_lifecycle_background_task", None)
    if callable(creator):
        task = creator(awaitable, label=label)
        if task is not None:
            return
    _close_unused_awaitable(awaitable)


def _dispatch_persona_effects(
    plugin: Any,
    *,
    persona_id: str,
    changed_keys: set[str],
) -> None:
    sanitizer = getattr(plugin, "_sanitize_persona_id", None)
    pid = sanitizer(persona_id) if callable(sanitizer) else str(persona_id or "")
    if not pid:
        return
    reset = getattr(plugin, "_reset_persona_prompt_caches", None)
    if callable(reset):
        reset(pid)
    activate = getattr(plugin, "_activate_persona_id", None)
    deactivate = getattr(plugin, "_deactivate_persona_for_event", None)
    token = activate(pid, allow_inactive=True) if callable(activate) else None
    try:
        expression_changed = any(
            key.startswith("expression_")
            or key in {"bot_name", "default_style", "reply_style_prompt"}
            for key in changed_keys
        )
        refresher = getattr(plugin, "_refresh_expression_voice_profile", None)
        if expression_changed and callable(refresher):
            refresher()
        worldbook_changed = bool(
            {"worldbook_config_paths", "roleplay_knowledge_source_ids"}
            & changed_keys
        )
        importer = getattr(plugin, "_import_worldbook_entries_from_sources", None)
        if (
            worldbook_changed
            and bool(runtime_persona_setting(plugin, "worldbook_auto_import", True))
            and callable(importer)
            and importer()
        ):
            scheduler = getattr(plugin, "_schedule_data_save", None)
            if callable(scheduler):
                scheduler(
                    sections={
                        "worldbook_entries",
                        "worldbook_member_profiles",
                        "worldbook_group_profiles",
                        "worldbook_import_state",
                        "worldbook_deleted_member_ids",
                        "worldbook_deleted_group_ids",
                    },
                    delay=0.1,
                )
    finally:
        if callable(deactivate):
            deactivate(token)

    proactive_changed = any(
        key.startswith("proactive_")
        or key.startswith("enable_proactive")
        or key
        in {
            "max_daily_messages",
            "idle_minutes",
            "min_interval_minutes",
            "quiet_hours",
            "enable_daily_review",
            "daily_review_time",
            "daily_review_provider_id",
        }
        for key in changed_keys
    )
    if not proactive_changed:
        return
    kicker = getattr(plugin, "_kick_proactive_loop_once", None)
    creator = getattr(plugin, "_create_lifecycle_background_task", None)
    if not callable(kicker) or not callable(creator):
        return

    async def kick_target_persona() -> None:
        active_token = activate(pid, allow_inactive=True) if callable(activate) else None
        try:
            await kicker()
        finally:
            if callable(deactivate):
                deactivate(active_token)

    awaitable = kick_target_persona()
    try:
        task = creator(
            awaitable,
            label=f"persona_setting_hot_apply:{pid}",
        )
    except Exception as exc:
        _close_unused_awaitable(awaitable)
        logger.warning(
            "[PrivateCompanion] 人格配置唤醒调度失败: persona=%s error_type=%s",
            pid,
            type(exc).__name__,
        )
        return
    if task is None:
        _close_unused_awaitable(awaitable)
        logger.warning(
            "[PrivateCompanion] 人格配置已保存，但主动调度即时唤醒未启动: persona=%s",
            pid,
        )


def dispatch_runtime_config_effects(
    plugin: Any,
    changes: Mapping[str, Any],
    *,
    scope: str = "global",
    persona_id: str = "",
    source: str = "unknown",
    adapter: Any = None,
    overrides: Mapping[str, Any] | None = None,
    apply_plain_values: bool = False,
) -> None:
    """Apply all runtime-only effects through one auditable entry point."""

    values = dict(changes or {})
    changed_keys = {str(key) for key in values}
    if scope == "persona":
        _dispatch_persona_effects(
            plugin,
            persona_id=persona_id,
            changed_keys=changed_keys,
        )
        return
    adapter = adapter or getattr(plugin, "page_api", None)
    runtime_overrides = dict(overrides or {})
    previous_effective_timezone = str(
        runtime_overrides.get("__previous_environment_perception_timezone")
        or getattr(plugin, "environment_perception_timezone", "")
        or ""
    )
    if apply_plain_values:
        for key, value in values.items():
            if key != "enable_multi_persona_mode":
                setattr(plugin, key, value)
        if "enable_message_debounce" in values:
            plugin.enable_semantic_message_debounce = bool(
                values["enable_message_debounce"]
            )
        if "text_message_debounce_seconds" in values:
            plugin.semantic_message_debounce_seconds = values[
                "text_message_debounce_seconds"
            ]

    if "enable_body_monitor_integration" in values:
        enabled = bool(values["enable_body_monitor_integration"])
        plugin.enable_body_monitor_integration = enabled
        scheduler = getattr(adapter, "_schedule_body_monitor_integration_toggle", None)
        if callable(scheduler):
            scheduler(enabled)

    if "enable_multi_persona_mode" in values:
        enabled = bool(values["enable_multi_persona_mode"])
        transition = getattr(plugin, "_prepare_multi_persona_transition", None)
        if callable(transition):
            transition(enabled)
        plugin.enable_multi_persona_mode = enabled

    if "environment_perception_timezone" in values:
        timezone_name = str(
            getattr(plugin, "environment_perception_timezone", "") or ""
        )
        _set_today_key_timezone(timezone_name)
        invalidator = getattr(plugin, "_invalidate_timezone_derived_state", None)
        if callable(invalidator):
            invalidator(previous_effective_timezone, timezone_name)
        _schedule_proactive_kick(
            plugin,
            adapter=adapter,
            label=f"runtime_config:{source}:environment_perception_timezone",
        )

    if "max_daily_messages" in values:
        _schedule_proactive_kick(
            plugin,
            adapter=adapter,
            label=f"runtime_config:{source}:max_daily_messages",
        )

    if changed_keys & {"storage_backend", "storage_sqlite_path"} and not bool(
        runtime_overrides.get("__defer_storage_rebuild")
    ):
        rebuild = getattr(plugin, "_rebuild_store_manager", None)
        if callable(rebuild):
            rebuild(reload_data=True)

    if "enable_tts_enhancement" in changed_keys or changed_keys & TTS_RUNTIME_KEYS:
        loader = getattr(plugin, "_load_tts_enhancement_config", None)
        if callable(loader):
            overlay = getattr(adapter, "_config_overlay", None)
            loader(
                overlay(runtime_overrides or values)
                if callable(overlay)
                else getattr(plugin, "config", {})
            )
        else:
            for key in changed_keys:
                if key == "enable_tts_enhancement" or key in TTS_RUNTIME_KEYS:
                    setattr(plugin, key, values[key])

    if values.get("enable_daily_case_review_experiment") is False:
        data = getattr(plugin, "data", None)
        if isinstance(data, dict):
            data["daily_review_case_audit"] = []
            scheduler = getattr(plugin, "_schedule_data_save", None)
            if callable(scheduler):
                scheduler(sections={"daily_review_case_audit"})


__all__ = ["TTS_RUNTIME_KEYS", "dispatch_runtime_config_effects"]
