# -*- coding: utf-8 -*-

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
from typing import Any

from astrbot.api import logger

LEGACY_PROACTIVE_ACTIONS_KEY = "enabled_proactive_actions"

LEGACY_KEY_ALIASES: dict[str, tuple[str, ...]] = {
    "target_group_ids": ("group_whitelist_ids",),
    "timezone": ("environment_perception_timezone",),
    "enable_maintenance_token_saver": ("enable_daily_token_soft_limit",),
    "maintenance_token_soft_limit": ("daily_token_soft_limit",),
    "DIARY_PROVIDER_ID": ("DREAM_DIARY_PROVIDER_ID",),
    "DREAM_PROVIDER_ID": ("DREAM_DIARY_PROVIDER_ID",),
    "COMFYUI_PHOTO_WORKFLOW_NAME": ("COMFYUI_TEXT2IMG_WORKFLOW_NAME", "COMFYUI_SELFIE_WORKFLOW_NAME"),
    "allow_photo_text_action": ("enable_photo_text_action",),
    "allow_screen_peek_action": ("enable_screen_glance_action",),
    "allow_poke_action": ("enable_poke_action",),
    "allow_voice_action": ("enable_voice_action",),
    "creative_base_chars_per_hour": ("creative_chars_per_session",),
    "enable_hot_trend_sources": ("enable_news_daily_hot_read",),
    "hot_trend_sources": ("news_hot_sources",),
    "hot_trend_max_items": ("news_hot_max_items",),
    "enable_jm_cosmos_integration": ("enable_private_reading_integration",),
    "enable_jm_cosmos_boredom_read": ("enable_private_reading_boredom_read",),
    "jm_cosmos_min_interval_hours": ("private_reading_min_interval_hours",),
    "jm_cosmos_max_photo_count": ("private_reading_max_photo_count",),
    "jm_cosmos_share_probability": ("private_reading_share_probability",),
    "jm_cosmos_default_keywords": ("private_reading_default_keywords",),
    "jm_cosmos_blocked_tags": ("private_reading_blocked_tags",),
    "JM_COSMOS_VISION_PROVIDER_ID": ("PRIVATE_READING_VISION_PROVIDER_ID",),
}

LEGACY_PROACTIVE_ACTION_FLAG_KEYS: dict[str, str] = {
    "photo_text": "enable_photo_text_action",
    "screen_peek": "enable_screen_glance_action",
    "screen_glance": "enable_screen_glance_action",
    "poke": "enable_poke_action",
    "voice": "enable_voice_action",
}

PRECISION_PROVIDER_MODE_KEYS: tuple[str, ...] = (
    "MAI_STYLE_PROVIDER_ID",
    "DAILY_PLAN_PROVIDER_ID",
    "DETAIL_ENHANCEMENT_PROVIDER_ID",
    "DREAM_DIARY_PROVIDER_ID",
    "CREATIVE_PROVIDER_ID",
    "CREATIVE_OUTLINE_PROVIDER_ID",
    "CREATIVE_REVIEW_PROVIDER_ID",
    "VOICE_PROMPT_PROVIDER_ID",
    "tts_conversion_provider_id",
    "PHOTO_PROMPT_PROVIDER_ID",
    "NARRATION_PROVIDER_ID",
    "HISTORY_SUMMARY_PROVIDER_ID",
    "RESPONSE_REVIEW_PROVIDER_ID",
    "SMART_SILENCE_PROVIDER_ID",
    "PROACTIVE_PERSONA_JUDGE_PROVIDER_ID",
    "TROUBLESHOOTING_PROVIDER_ID",
    "SMART_MESSAGE_DEBOUNCE_PROVIDER_ID",
    "REST_WAKEUP_PROVIDER_ID",
    "RELATIONSHIP_ANALYSIS_PROVIDER_ID",
    "EMOTION_JUDGEMENT_PROVIDER_ID",
    "COMPANION_MEMORY_PROVIDER_ID",
    "DIALOGUE_EPISODE_PROVIDER_ID",
    "GROUP_INTERJECT_PROVIDER_ID",
    "GROUP_EPISODE_PROVIDER_ID",
    "GROUP_SLANG_PROVIDER_ID",
    "GROUP_FOLLOWUP_JUDGE_PROVIDER_ID",
    "FORWARD_MESSAGE_PROVIDER_ID",
    "PRIVATE_READING_VISION_PROVIDER_ID",
    "NEWS_PROVIDER_ID",
    "WEB_EXPLORATION_PROVIDER_ID",
)


def migrate_flat_config_into_schema_groups(
    config: Any,
    *,
    schema_path: Path,
    logger: Any | None = None,
    save: bool = True,
) -> int:
    """Copy legacy flat config values into the new AstrBot schema groups."""
    try:
        return _migrate_flat_config_into_schema_groups(config, schema_path=schema_path, logger=logger, save=save)
    except Exception as exc:
        if logger is not None:
            logger.warning("[PrivateCompanion] 配置分组迁移失败，已跳过且不影响插件加载: %s", _single_line(exc, 160))
        return 0


def _migrate_flat_config_into_schema_groups(
    config: Any,
    *,
    schema_path: Path,
    logger: Any | None = None,
    save: bool = True,
) -> int:
    root = _config_root_mapping(config)
    if not isinstance(root, dict):
        return 0
    schema_map = _schema_group_items(schema_path, logger=logger)
    if not schema_map:
        return 0

    changed: list[str] = []
    for key, item in schema_map.items():
        if key == "provider_config_mode":
            continue
        if key not in root:
            continue
        old_value = root.get(key)
        if old_value == item.get("default"):
            continue
        if _copy_into_schema_group(root, schema_map, key, old_value):
            changed.append(key)

    legacy_group = root.get("legacy_compat_config")
    legacy_sources = [root]
    if isinstance(legacy_group, dict):
        legacy_sources.append(legacy_group)

    if _migrate_legacy_group_access_mode(root, schema_map):
        changed.append("require_target_group->group_access_mode")
    action_changes = _migrate_legacy_proactive_actions(root, schema_map, legacy_sources)
    changed.extend(action_changes)

    for old_key, new_keys in LEGACY_KEY_ALIASES.items():
        for source in legacy_sources:
            if old_key not in source:
                continue
            old_value = source.get(old_key)
            if _is_empty(old_value):
                continue
            for new_key in new_keys:
                if _copy_into_schema_group(root, schema_map, new_key, old_value):
                    changed.append(f"{old_key}->{new_key}")

    if _ensure_provider_config_mode(root, schema_map):
        changed.append("provider_config_mode~mode-infer")
    added_compat_defaults = _ensure_flat_schema_compat_defaults(root, schema_map)
    if added_compat_defaults:
        changed.extend(f"{key}~compat-default" for key in added_compat_defaults)
    removed_section_keys = _cleanup_legacy_section_markers(root)
    if removed_section_keys:
        changed.extend(f"{key}~section-cleanup" for key in removed_section_keys)
    roleplay_hint_changes = _migrate_legacy_roleplay_image_hint(root, schema_map)
    changed.extend(roleplay_hint_changes)

    # 旧别名键只负责迁移；仍在 schema 中登记的 flat 兼容键重置为默认值，
    # 避免 AstrBot 每次启动都反复补齐并刷屏。
    removed_legacy_keys: list[str] = []
    cleanup_keys = set(LEGACY_KEY_ALIASES) | {LEGACY_PROACTIVE_ACTIONS_KEY, "require_target_group"}
    for old_key in cleanup_keys:
        item = schema_map.get(old_key)
        if old_key in root:
            if item:
                default_value = _coerce_schema_value(item.get("default"), item)
                if root.get(old_key) != default_value:
                    root[old_key] = default_value
                    removed_legacy_keys.append(old_key)
            else:
                root.pop(old_key, None)
                removed_legacy_keys.append(old_key)
        if isinstance(legacy_group, dict) and old_key in legacy_group:
            if item and str(item.get("group") or "") == "legacy_compat_config":
                default_value = _coerce_schema_value(item.get("default"), item)
                if legacy_group.get(old_key) != default_value:
                    legacy_group[old_key] = default_value
                    if old_key not in removed_legacy_keys:
                        removed_legacy_keys.append(old_key)
            else:
                legacy_group.pop(old_key, None)
                if old_key not in removed_legacy_keys:
                    removed_legacy_keys.append(old_key)
    if removed_legacy_keys:
        changed.extend(f"{key}~cleanup" for key in removed_legacy_keys)

    if not changed:
        return 0
    if logger is not None:
        logger.info("[PrivateCompanion] 已将旧版扁平配置迁移到新版分组配置: %s 项", len(changed))
    if save:
        _save_config_after_schema_migration(config, logger=logger)
    return len(changed)


def _ensure_provider_config_mode(root: dict[str, Any], schema_map: dict[str, dict[str, Any]]) -> bool:
    item = schema_map.get("provider_config_mode")
    if not item:
        return False
    group_key = str(item.get("group") or "")
    group = root.get(group_key)
    if not isinstance(group, dict):
        group = {}
        root[group_key] = group

    root_mode = _normalize_provider_config_mode_value(root.get("provider_config_mode"))
    group_mode = _normalize_provider_config_mode_value(group.get("provider_config_mode"))
    quick_keys = (
        "FAST_RESPONSE_PROVIDER_ID",
        "COMPLEX_REASONING_PROVIDER_ID",
        "CREATIVE_MODEL_PROVIDER_ID",
        "PLUGIN_VISION_PROVIDER_ID",
    )
    has_quick_provider = _has_any_configured_provider(root, group, quick_keys)
    has_precision_provider = _has_any_configured_provider(root, group, PRECISION_PROVIDER_MODE_KEYS)
    explicit = ""
    if group_mode and root_mode and group_mode != root_mode:
        # Official AstrBot config pages save the visible schema group first.
        # When it disagrees with the hidden flat compatibility key, prefer what
        # the user can actually see and just sync the hidden copy afterward.
        explicit = group_mode
    elif group_mode:
        if not root_mode and group_mode == "quick" and has_precision_provider and not has_quick_provider:
            explicit = ""
        else:
            explicit = group_mode
    elif root_mode:
        explicit = root_mode
    if explicit:
        changed = False
        if group.get("provider_config_mode") != explicit:
            group["provider_config_mode"] = explicit
            changed = True
        if root.get("provider_config_mode") != explicit:
            root["provider_config_mode"] = explicit
            changed = True
        return changed

    inferred = "precision" if has_precision_provider else "quick"
    group["provider_config_mode"] = inferred
    root["provider_config_mode"] = inferred
    return True


def _normalize_provider_config_mode_value(value: Any) -> str:
    text = str(value or "").strip().lower()
    aliases = {
        "quick": "quick",
        "fast": "quick",
        "simple": "quick",
        "快速": "quick",
        "快速配置": "quick",
        "precision": "precision",
        "precise": "precision",
        "advanced": "precision",
        "detail": "precision",
        "detailed": "precision",
        "精准": "precision",
        "精准配置": "precision",
        "分流": "precision",
        "分流模型": "precision",
    }
    return aliases.get(text, "")


def _has_any_configured_provider(
    root: dict[str, Any],
    mode_group: dict[str, Any],
    keys: tuple[str, ...],
) -> bool:
    for key in keys:
        if str(mode_group.get(key) or "").strip():
            return True
        if str(root.get(key) or "").strip():
            return True
        for value in root.values():
            if isinstance(value, dict) and str(value.get(key) or "").strip():
                return True
    return False


def _cleanup_flat_schema_item_keys(root: dict[str, Any], schema_map: dict[str, dict[str, Any]]) -> list[str]:
    removed: list[str] = []
    for key in list(root.keys()):
        if key not in schema_map:
            continue
        item = schema_map.get(key) or {}
        group_key = str(item.get("group") or "")
        if not group_key:
            continue
        group = root.get(group_key)
        if not isinstance(group, dict):
            group = {}
            root[group_key] = group
        if key not in group and root.get(key) != item.get("default"):
            group[key] = _coerce_schema_value(root.get(key), item)
        root.pop(key, None)
        removed.append(key)
    return removed


def _ensure_flat_schema_compat_defaults(root: dict[str, Any], schema_map: dict[str, dict[str, Any]]) -> list[str]:
    added: list[str] = []
    for key, item in schema_map.items():
        if key in root:
            continue
        group_key = str(item.get("group") or "")
        if not group_key:
            continue
        root[key] = _coerce_schema_value(item.get("default"), item)
        added.append(key)
    return added


def _cleanup_legacy_section_markers(root: dict[str, Any]) -> list[str]:
    removed: list[str] = []
    for key in list(root.keys()):
        if str(key).startswith("_section_"):
            root.pop(key, None)
            removed.append(str(key))
    return removed


def _migrate_legacy_roleplay_image_hint(root: dict[str, Any], schema_map: dict[str, dict[str, Any]]) -> list[str]:
    image_item = schema_map.get("private_image_self_recognition_hint") or {}
    image_group_key = str(image_item.get("group") or "")
    profile_item = schema_map.get("roleplay_user_profile_prompt") or {}
    profile_group_key = str(profile_item.get("group") or "")
    if not image_group_key or not profile_group_key:
        return []
    image_group = root.get(image_group_key)
    if not isinstance(image_group, dict):
        return []
    raw_hint = str(image_group.get("private_image_self_recognition_hint") or "").strip()
    if not raw_hint:
        return []
    split = _split_legacy_roleplay_image_hint(raw_hint)
    user_text = "\n".join(split["user"]).strip()
    image_text = "\n".join(split["image"]).strip()
    if not user_text:
        return []
    profile_group = root.get(profile_group_key)
    if not isinstance(profile_group, dict):
        profile_group = {}
        root[profile_group_key] = profile_group
    old_profile = str(profile_group.get("roleplay_user_profile_prompt") or "").strip()
    new_profile = _append_unique_text(old_profile, user_text)
    changed: list[str] = []
    if new_profile != old_profile:
        profile_group["roleplay_user_profile_prompt"] = new_profile[:2000]
        changed.append("private_image_self_recognition_hint->roleplay_user_profile_prompt")
    if image_text != raw_hint:
        image_group["private_image_self_recognition_hint"] = image_text[:1200]
        changed.append("private_image_self_recognition_hint~user-profile-cleanup")
    return changed


def _split_legacy_roleplay_image_hint(text: str) -> dict[str, list[str]]:
    user_lines: list[str] = []
    image_lines: list[str] = []
    for raw_line in str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        if _looks_like_user_profile_line(line):
            user_lines.append(raw_line.strip())
        else:
            image_lines.append(raw_line.strip())
    return {"user": user_lines, "image": image_lines}


def _looks_like_user_profile_line(line: str) -> bool:
    text = str(line or "").strip()
    if not text:
        return False
    lower = text.lower()
    if any(token in lower for token in ("user profile", "user_profile", "master profile")):
        return True
    if re.search(r"(对用户的称呼|用户[：:的]|主人[：:的]|主人的|用户性别|用户生日|用户年龄|用户职业|用户身份|用户资料|用户设定|用户画像|用户偏好|用户边界|用户关系|用户称呼|称呼用户|如何称呼用户|与用户关系|和用户关系|彼此关系|相处方式|关系补充|是角色的XX|与角色的相处方式)", text):
        return True
    label = re.split(r"[：:]", text, maxsplit=1)[0].strip()
    if label in {"称呼", "昵称", "性别", "生日", "年龄", "职业", "专业", "身份", "关系", "边界", "偏好", "是角色的XX", "与角色的相处方式", "其他补充信息"}:
        return True
    if re.search(r"(专业是|职业是|生日是|性别是|称呼.*主人|叫.*主人|把用户|用户是|主人是)", text):
        return True
    return False


def _append_unique_text(existing: str, addition: str) -> str:
    existing = str(existing or "").strip()
    addition_lines = [line.strip() for line in str(addition or "").splitlines() if line.strip()]
    if not addition_lines:
        return existing
    if not existing:
        return "\n".join(addition_lines)
    existing_lines = [line.strip() for line in existing.splitlines() if line.strip()]
    existing_set = set(existing_lines)
    merged = list(existing_lines)
    for line in addition_lines:
        if line not in existing_set:
            merged.append(line)
            existing_set.add(line)
    return "\n".join(merged)


def _migrate_legacy_group_access_mode(root: dict[str, Any], schema_map: dict[str, dict[str, Any]]) -> bool:
    raw = root.get("require_target_group")
    legacy_group = root.get("legacy_compat_config")
    if raw is None and isinstance(legacy_group, dict):
        raw = legacy_group.get("require_target_group")
    if raw is None:
        return False
    require_target_group = _coerce_bool(raw)
    mode = "whitelist" if require_target_group else "blacklist"
    return _copy_into_schema_group(root, schema_map, "group_access_mode", mode)


def _migrate_legacy_proactive_actions(
    root: dict[str, Any],
    schema_map: dict[str, dict[str, Any]],
    legacy_sources: list[dict[str, Any]],
) -> list[str]:
    raw = _first_present_value(legacy_sources, LEGACY_PROACTIVE_ACTIONS_KEY)
    actions = _parse_legacy_action_list(raw)
    if not actions:
        return []
    changed: list[str] = []
    for action, new_key in LEGACY_PROACTIVE_ACTION_FLAG_KEYS.items():
        enabled = action in actions
        if _copy_into_schema_group(root, schema_map, new_key, enabled):
            changed.append(f"{LEGACY_PROACTIVE_ACTIONS_KEY}->{new_key}")
    return changed


def _first_present_value(sources: list[dict[str, Any]], key: str) -> Any:
    for source in sources:
        if key in source:
            return source.get(key)
    return None


def _parse_legacy_action_list(raw: Any) -> set[str]:
    if raw is None:
        return set()
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return set()
        try:
            parsed = json.loads(text)
        except Exception:
            parsed = None
        if isinstance(parsed, list):
            raw_parts = parsed
        else:
            raw_parts = re.split(r"[,\s、;；|]+", text)
    elif isinstance(raw, list):
        raw_parts = raw
    elif isinstance(raw, tuple | set):
        raw_parts = list(raw)
    else:
        raw_parts = []
    actions: set[str] = set()
    for part in raw_parts:
        text = str(part or "").strip()
        if not text:
            continue
        for action in text.split("+"):
            action = action.strip()
            if action:
                actions.add(action)
    return actions


def _config_root_mapping(config: Any) -> dict[str, Any] | None:
    if isinstance(config, dict):
        return config
    for attr in ("data", "config"):
        target = getattr(config, attr, None)
        if isinstance(target, dict):
            return target
    return None


def _copy_into_schema_group(root: dict[str, Any], schema_map: dict[str, dict[str, Any]], key: str, value: Any) -> bool:
    item = schema_map.get(key)
    if not item:
        return False
    default = item.get("default")
    value = _coerce_schema_value(value, item)
    if value == default:
        return False
    group_key = str(item.get("group") or "")
    group = root.get(group_key)
    if not isinstance(group, dict):
        group = {}
        root[group_key] = group
    group_value = group.get(key)
    should_copy = key not in group or group_value == default
    if not should_copy and _is_empty(group_value) and not _is_empty(value):
        should_copy = True
    if not should_copy:
        return False
    group[key] = value
    return True


def _schema_group_items(schema_path: Path, *, logger: Any | None = None) -> dict[str, dict[str, Any]]:
    mapping: dict[str, dict[str, Any]] = {}
    try:
        raw = json.loads(schema_path.read_text(encoding="utf-8"))
    except Exception as exc:
        if logger is not None:
            logger.debug("[PrivateCompanion] 读取配置 schema 用于分组迁移失败: %s", exc)
        return mapping
    if not isinstance(raw, dict):
        return mapping
    for group_key, group in raw.items():
        if not isinstance(group, dict) or group.get("type") != "object":
            continue
        items = group.get("items")
        if not isinstance(items, dict):
            continue
        for key, item in items.items():
            if isinstance(item, dict):
                copied = dict(item)
                copied["group"] = str(group_key)
                mapping[str(key)] = copied
    return mapping


def _coerce_schema_value(value: Any, item: dict[str, Any]) -> Any:
    item_type = str(item.get("type") or "")
    if item_type == "bool":
        return _coerce_bool(value)
    if item_type == "int":
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return item.get("default")
    if item_type == "float":
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return item.get("default")
        slider = item.get("slider")
        if (
            isinstance(slider, dict)
            and float(slider.get("max", 0) or 0) <= 1.0
            and parsed > 1.0
            and ("probability" in str(item.get("description") or "").lower() or "概率" in str(item.get("description") or ""))
        ):
            parsed /= 100.0
        return parsed
    if item_type == "list":
        if isinstance(value, list):
            return value
        text = str(value or "").strip()
        if not text:
            return []
        text = text.replace("\r\n", "\n").replace("\r", "\n").replace("，", ",").replace("\n", ",")
        return [part.strip() for part in text.split(",") if part.strip()]
    if item_type in {"string", "text"}:
        return str(value or "")
    return value


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "1", "yes", "y", "on", "enable", "enabled", "启用", "开启", "开", "是"}:
            return True
        if text in {"false", "0", "no", "n", "off", "disable", "disabled", "停用", "关闭", "关", "否", ""}:
            return False
    return bool(value)


def _save_config_after_schema_migration(config: Any, *, logger: Any | None = None) -> None:
    for method_name in ("save_config", "save", "save_conf"):
        save = getattr(config, method_name, None)
        if not callable(save):
            continue
        try:
            _ensure_config_parent_dir(config, logger=logger)
            result = save()
            if asyncio.iscoroutine(result) or hasattr(result, "__await__"):
                try:
                    asyncio.get_running_loop().create_task(result)
                except RuntimeError:
                    close = getattr(result, "close", None)
                    if callable(close):
                        close()
                    if logger is not None:
                        logger.debug("[PrivateCompanion] 配置分组迁移已写入运行态，当前无事件循环可异步保存")
            return
        except TypeError:
            continue
        except FileNotFoundError as exc:
            if _ensure_config_parent_dir(config, error=exc, logger=logger):
                try:
                    result = save()
                    if asyncio.iscoroutine(result) or hasattr(result, "__await__"):
                        try:
                            asyncio.get_running_loop().create_task(result)
                        except RuntimeError:
                            close = getattr(result, "close", None)
                            if callable(close):
                                close()
                    return
                except Exception as retry_exc:
                    if logger is not None:
                        logger.warning("[PrivateCompanion] 重试保存配置分组迁移结果失败: %s", _single_line(retry_exc, 160))
                    return
            if logger is not None:
                logger.warning("[PrivateCompanion] 保存配置分组迁移结果失败: %s", _single_line(exc, 160))
            return
        except Exception as exc:
            if logger is not None:
                logger.warning("[PrivateCompanion] 保存配置分组迁移结果失败: %s", _single_line(exc, 160))
            return


def _ensure_config_parent_dir(
    config: Any,
    *,
    error: BaseException | None = None,
    logger: Any | None = None,
) -> bool:
    paths: list[str] = []
    for attr in (
        "path",
        "file",
        "filepath",
        "file_path",
        "config_path",
        "_path",
        "_file",
        "_filepath",
        "_file_path",
        "_config_path",
    ):
        try:
            value = getattr(config, attr, None)
        except Exception:
            value = None
        if value:
            paths.append(str(value))
    if isinstance(config, dict):
        for key in ("path", "file", "filepath", "file_path", "config_path"):
            value = config.get(key)
            if value:
                paths.append(str(value))
    if error is not None:
        match = re.search(r"['\"]([^'\"]+?\.tmp)['\"]", str(error))
        if match:
            paths.append(match.group(1))
    changed = False
    for raw in paths:
        text = str(raw or "").strip()
        if not text:
            continue
        if text.endswith(".tmp"):
            parent = Path(text).expanduser().parent
        else:
            candidate = Path(text).expanduser()
            parent = candidate if text.endswith(("/", "\\")) else candidate.parent
        if not str(parent):
            continue
        try:
            parent.mkdir(parents=True, exist_ok=True)
            changed = True
        except Exception as exc:
            if logger is not None:
                logger.debug("[PrivateCompanion] 创建配置目录失败: %s", _single_line(exc, 160))
    return changed


def _is_empty(value: Any) -> bool:
    return value in (None, "", [], {})


def _single_line(text: Any, limit: int = 80) -> str:
    return " ".join(str(text or "").split())[:limit]
