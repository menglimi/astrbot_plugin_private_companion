"""Read-only conversion of portable settings from the former standalone extensions.

Only preferences with the same meaning in the bundled service are copied.
Routes, identities, credentials, device bindings and actions that can start
external communication require a fresh V2 decision.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from typing import Any


SOURCE_PLUGINS = {
    "game": "astrbot_plugin_game_companion",
    "together": "astrbot_plugin_together_companion",
    "live": "astrbot_plugin_live_stream_companion",
}
REGISTRY_PLUGINS = {
    "content": "astrbot_plugin_content_companion",
    "memory": "astrbot_plugin_memory_companion",
}
WRITABLE_PLUGINS = SOURCE_PLUGINS | REGISTRY_PLUGINS
REVIEW_ONLY_PLUGINS = {
    "screen": "astrbot_plugin_screen_companion",
}

PORTABLE_FIELDS = {
    "game": frozenset((
        "native_v2.tictactoe_enabled", "native_v2.gomoku_enabled",
        "native_v2.pig_dice_enabled", "native_v2.pig_dice_target_score",
        "native_v2.blackjack_enabled",
        "native_v2.xiangqi_enabled", "native_v2.turtle_soup_enabled",
        "native_v2.turtle_soup_content_level", "native_v2.turtle_soup_max_hints",
        "native_v2.draw_guess_enabled", "native_v2.draw_guess_max_guesses",
        "native_v2.draw_guess_duration_seconds",
        "context.enable_private_qq_game_context",
        "context.recent_game_result_ttl_minutes",
    )),
    "together": frozenset((
        "conversation.chat_provider_id",
        "conversation.vision_provider_id", "conversation.system_prompt",
        "conversation.enable_memory_context", "conversation.record_visible_turns",
        "conversation.history_turns", "conversation.model_hangup_enabled",
        "conversation.call_idle_seconds",
        "speech.stt_mode", "speech.stt_provider_id", "speech.stt_correction_enabled",
        "speech.tts_provider_id",
        "speech.browser_language", "speech.browser_tts_fallback",
        "speech.direct_multilingual_tts", "speech.tts_timeout_seconds",
        "speech.tts_volume_percent",
        "watch.comment_interval_seconds", "watch.scene_min_interval_seconds",
        "watch.memory_refresh_seconds", "watch.duck_video_volume",
        "watch.duck_volume_percent",
        "library.max_upload_mb",
        "novel.retrieval_top_k", "novel.spoiler_policy",
        "novel.answer_with_sources", "novel.auto_save_progress",
    )),
    "live": frozenset((
        "bilibili_room_id", "bilibili_web_backend",
        "l2d_max_tags_per_reply", "soullink_mode",
        "soullink_motion_style", "soullink_fps",
        "soullink_parameter_gain", "soullink_body_motion_gain",
        "soullink_vad_decay_rate", "bili_live_inject_max_events",
        "bili_live_inject_event_types", "bili_live_cache_size",
        "live_relationship_context_max_age_seconds",
        "live_relationship_context_recent_limit",
        "live_relationship_context_max_users",
        "live_auto_register_min_events",
        "live_afterglow_cooldown_seconds",
        "live_afterglow_duration_hours",
        "live_memory_context_max_lines", "live_memory_max_items",
        "live_memory_max_topics", "live_memory_max_open_threads",
        "live_memory_max_highlights",
        "live_memory_highlight_event_types",
        "bili_live_history_poll_interval",
        "bili_live_auto_reply_cooldown_seconds",
        "bili_live_auto_reply_max_per_minute",
        "bili_live_auto_reply_max_length",
        "twitch_live_cache_size",
        "twitch_auto_reply_cooldown_seconds",
        "twitch_auto_reply_max_per_minute",
        "twitch_auto_reply_max_length",
        "mouth_sync_fps", "mouth_sync_gain",
        "mouth_sync_smoothing", "mouth_sync_noise_gate",
        "mouth_sync_form_strength",
        "subtitle_typing_speed_ms", "subtitle_hold_seconds",
        "subtitle_max_length", "subtitle_font_size",
        "subtitle_font_weight", "subtitle_text_color",
        "subtitle_stroke_color", "subtitle_stroke_size",
        "subtitle_cursor_color", "subtitle_show_cursor",
        "subtitle_fade_out", "subtitle_position",
        "subtitle_padding", "subtitle_max_width",
        "subtitle_strip_l2d_tags", "subtitle_strip_tts_blocks",
        "subtitle_voice_use_following_plain",
        "subtitle_prefer_chinese_text",
        "subtitle_use_tts_spoken_text",
        "subtitle_strip_html_tags",
        "bili_live_auto_reply_system_prompt",
        "twitch_auto_reply_system_prompt",
    )),
}

HOST_VERIFIED_FIELDS = {"together.conversation.chat_provider_id",
                        "together.conversation.vision_provider_id",
                        "together.speech.stt_provider_id",
                        "together.speech.tts_provider_id"}

# V1 Live used the old plugin name for these preferences; V2 renamed their
# storage keys without changing the corresponding limits.
LIVE_RENAMES = {
    "private_companion_live_context_enabled": "live_relationship_context_enabled",
    "private_companion_live_context_max_age_seconds": "live_relationship_context_max_age_seconds",
    "private_companion_live_context_recent_limit": "live_relationship_context_recent_limit",
    "private_companion_live_context_max_users": "live_relationship_context_max_users",
    "private_companion_live_context_include_identity_note": "live_relationship_context_include_identity_note",
    "private_companion_relationship_style_context_enabled": "live_relationship_style_context_enabled",
    "private_companion_relationship_style_include_profile": "live_relationship_style_include_profile",
    "private_companion_relationship_style_include_memories": "live_relationship_style_include_memories",
    "private_companion_writeback_enabled": "live_state_writeback_enabled",
    "private_companion_viewer_activity_enabled": "live_viewer_activity_enabled",
    "private_companion_viewer_activity_context_enabled": "live_viewer_activity_context_enabled",
    "private_companion_writeback_memory_event_types": "live_writeback_event_types",
    "private_companion_auto_register_viewers": "live_auto_register_viewers",
    "private_companion_auto_register_min_events": "live_auto_register_min_events",
    "private_companion_live_state_cooldown_seconds": "live_afterglow_cooldown_seconds",
    "private_companion_live_state_duration_hours": "live_afterglow_duration_hours",
    "private_companion_live_state_enabled": "live_afterglow_enabled",
    "private_companion_live_summary_enabled": "live_summary_enabled",
    "bili_live_streamer_identity_from_companion_enabled": "bili_live_streamer_identity_from_v2_enabled",
}


GAME_RENAMES = {
    "pig_dice.target_score": "native_v2.pig_dice_target_score",
    "turtle_soup.max_hints": "native_v2.turtle_soup_max_hints",
    "turtle_soup.content_level": "native_v2.turtle_soup_content_level",
    "draw_guess.max_guesses": "native_v2.draw_guess_max_guesses",
    "draw_guess.duration_seconds": "native_v2.draw_guess_duration_seconds",
}


def preview_value(name, field, value):
    if (field.rsplit(".", 1)[-1].endswith("prompt")
            and isinstance(value, str)):
        return {"configured": bool(value), "characters": len(value)}
    return value


@dataclass(frozen=True)
class NativeExtensionConfigurationMigration:
    changes: dict[str, Any]
    differences: tuple[dict[str, str], ...]
    warnings: tuple[str, ...]


def setting_value(document: dict, path: str, default=None):
    value = document
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return default
        value = value[part]
    return value


def settings_state_token(document: dict, fields) -> str:
    values = {field: setting_value(document, field) for field in sorted(fields)}
    encoded = json.dumps(values, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _leaves(document: dict, prefix=""):
    for key, value in document.items():
        path = prefix + key
        if isinstance(value, dict):
            yield from _leaves(value, path + ".")
        else:
            yield path, value


def _schema_field(schema: dict, path: str):
    field = None
    items = schema
    for part in path.split("."):
        field = items.get(part) if isinstance(items, dict) else None
        items = field.get("items") if isinstance(field, dict) else None
    return field


def _valid(value, field):
    if not isinstance(field, dict):
        return False
    kind = field.get("type")
    types = {"bool": bool, "int": int, "float": (int, float),
             "string": str, "text": str, "list": list}
    expected = types.get(kind)
    if expected is None or type(value) not in (expected if isinstance(expected, tuple) else (expected,)):
        return False
    if "options" in field and value not in field["options"]:
        return False
    if kind == "list" and any(type(item) is not str for item in value):
        return False
    if kind in {"int", "float"}:
        try:
            if not math.isfinite(value):
                return False
        except OverflowError:
            return False
        bounds = field.get("slider", {})
        minimum = bounds.get("min", field.get("minimum"))
        maximum = bounds.get("max", field.get("maximum"))
        if minimum is not None and value < minimum or maximum is not None and value > maximum:
            return False
    return True


def convert_native_extension_configuration(name, source, defaults, schema):
    """Return a bounded, auditable setting patch; never copy unknown keys."""
    if name not in SOURCE_PLUGINS or not all(isinstance(item, dict) for item in (source, defaults, schema)):
        return NativeExtensionConfigurationMigration({}, (), ("旧拓展配置或目标 schema 不可用。",))
    changes = {}
    differences = []
    renamed = LIVE_RENAMES if name == "live" else GAME_RENAMES if name == "game" else {}
    for source_path, value in _leaves(source):
        target_path = renamed.get(source_path, source_path)
        if name == "together" and source_path == "conversation.record_shared_experiences":
            differences.append({"field": f"{name}.{source_path}", "kind": "changed_semantics",
                                "explain": "旧共享经历整理已由新版观察和记忆链承接，不等同于记录可见对话，未替换成该开关。"})
            continue
        default = setting_value(defaults, target_path, _MISSING)
        if default is _MISSING:
            differences.append({"field": f"{name}.{source_path}", "kind": "unmapped",
                                "explain": "旧字段在当前拓展中没有可核实的对应项，保持在旧配置。"})
            continue
        if target_path != source_path and setting_value(source, target_path, _MISSING) is not _MISSING:
            differences.append({"field": f"{name}.{source_path}", "kind": "alias_shadowed",
                                "explain": "旧键与新键同时存在，以已明确配置的新键为准。"})
            continue
        if type(value) is type(default) and value == default:
            continue
        if target_path not in PORTABLE_FIELDS[name]:
            differences.append({"field": f"{name}.{source_path}", "kind": "manual_review_required",
                                "explain": "涉及身份、权限、外部连接或运行行为，未自动启用；请在新版对应页面核对。"})
            continue
        if not _valid(value, _schema_field(schema, target_path)):
            differences.append({"field": f"{name}.{source_path}", "kind": "invalid_value",
                                "explain": "旧值不符合新版字段类型或范围，未迁移。"})
            continue
        changes[target_path] = value
    return NativeExtensionConfigurationMigration(changes, tuple(differences),
        ("仅迁移同义且通过新版 schema 校验的偏好；身份、授权、端口及外部自动操作需重新核对。",))


def review_configuration_differences(name, source):
    """Account for every private-device setting without exposing its saved value."""
    if name not in REVIEW_ONLY_PLUGINS or not isinstance(source, dict):
        return ()
    return tuple({
        "field": f"{name}.{path}",
        "kind": "changed_semantics" if path.rsplit(".", 1)[-1] in {
            "enable_diary", "screen_recognition_mode"} else "manual_review_required",
        "explain": ("旧屏幕日记和识别方式与新版已授权画面解读、日记材料设置不同，不能按名称替换。"
                    if path.rsplit(".", 1)[-1] in {"enable_diary", "screen_recognition_mode"}
                    else "屏幕、音频、使用记录和设备连接按当前管理员授权重新核对，未继承采集或共享权限。"),
    } for path, _ in _leaves(source))


_MISSING = object()
