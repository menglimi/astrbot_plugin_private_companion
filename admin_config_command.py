# -*- coding: utf-8 -*-
"""Parser, policy, action and formatter for explicit admin config commands."""
from __future__ import annotations

import re
from typing import Any, Callable

from astrbot.api import logger

from .helpers import _now_ts, _safe_float, _set_into_config, _single_line
from .persona_config import runtime_persona_setting
from .runtime_config_dispatcher import dispatch_runtime_config_effects

RUNTIME_SETTINGS_SECTIONS = {"runtime_settings", "manual_diagnosis_pending_config"}
PENDING_CONFIG_SECTION = {"manual_diagnosis_pending_config"}


def parse_setting_text(text: str, aliases: dict[str, str], resolve_alias: Callable[[str], str]) -> tuple[str, str]:
    """Parse a config key/value while preserving all legacy separators and aliases."""
    raw = re.sub(r"^(?:把|将)\s*", "", str(text or "").strip())
    if not raw:
        return "", ""
    match = re.search(r"([A-Za-z_][A-Za-z0-9_]*)\s*(?:=|:|：|设为|设置为|改成|调到)\s*(.+)", raw)
    if match:
        return resolve_alias(match.group(1).strip()), match.group(2).strip()
    for alias, key in sorted(aliases.items(), key=lambda item: len(str(item[0])), reverse=True):
        alias_text = str(alias or "").strip()
        if not alias_text:
            continue
        delimiter_pattern = rf"^{re.escape(alias_text)}\s*(?:=|:|：|设为|设置为|改成|调到|调整为|调为)\s*(.+)$"
        delimiter_match = re.match(delimiter_pattern, raw, flags=re.I | re.S)
        if delimiter_match:
            return key, delimiter_match.group(1).strip()
        if raw.lower().startswith(alias_text.lower()):
            tail = raw[len(alias_text):].strip()
            tail = re.sub(r"^(?:=|:|：|设为|设置为|改成|调到|调整为|调为)\s*", "", tail).strip()
            if tail:
                return key, tail
    parts = raw.split(maxsplit=1)
    return (resolve_alias(parts[0].strip()), parts[1].strip()) if len(parts) >= 2 else ("", "")


def can_apply_config(host: Any, event: Any) -> bool:
    """Keep private/group management policy at the command boundary."""
    is_private = bool(getattr(event, "is_private_chat", lambda: False)())
    checker = host._can_manage_private_companion if is_private else host._can_manage_group_companion
    return bool(checker(event))


def get_pending_config(host: Any, event: Any) -> dict[str, Any] | None:
    store = host._companion_manual_pending_store()
    key = host._companion_manual_pending_key(event)
    pending = store.get(key)
    if not isinstance(pending, dict):
        return None
    if _now_ts() - _safe_float(pending.get("ts"), 0.0, 0.0) > 1800:
        store.pop(key, None)
        host._save_data_sync(sections=PENDING_CONFIG_SECTION)
        return None
    return pending


async def apply_config_value(host: Any, key: str, value: Any) -> tuple[bool, str, Any, Any]:
    """Normalize, persist and hot-apply one setting, rolling back atomically on failure."""
    ok, normalized, error = host._companion_manual_normalize_config_value(key, value)
    if not ok:
        return False, error, None, None
    old = host._companion_manual_current_config_value(key)
    old_semantic_debounce = runtime_persona_setting(host, "enable_semantic_message_debounce", None)
    old_semantic_seconds = runtime_persona_setting(host, "semantic_message_debounce_seconds", None)
    extra_updates: dict[str, Any] = {}
    if key == "enable_message_debounce":
        extra_updates["enable_semantic_message_debounce"] = bool(normalized)
    if key == "text_message_debounce_seconds":
        extra_updates["semantic_message_debounce_seconds"] = normalized
    config_value = normalized
    if key == "rest_reply_probability":
        config_value = max(0, min(100, int(round(_safe_float(normalized, 0.0, 0.0) * 100))))
    saved = False
    config = getattr(host, "config", None)
    if config is not None:
        try:
            saved = _set_into_config(config, key, config_value, allow_flat_fallback=False)
        except TypeError:
            saved = _set_into_config(config, key, config_value)
        if not saved:
            saved = _set_into_config(config, key, config_value)
        for extra_key, extra_value in extra_updates.items():
            try:
                _set_into_config(config, extra_key, extra_value, allow_flat_fallback=False)
            except TypeError:
                _set_into_config(config, extra_key, extra_value)
        if saved and not await host._save_config_if_possible():
            old_config_value = old
            if key == "rest_reply_probability":
                old_config_value = max(0, min(100, int(round(_safe_float(old, 0.0, 0.0) * 100))))
            _set_into_config(config, key, old_config_value)
            if key == "enable_message_debounce":
                _set_into_config(config, "enable_semantic_message_debounce", old_semantic_debounce)
            if key == "text_message_debounce_seconds":
                _set_into_config(config, "semantic_message_debounce_seconds", old_semantic_seconds)
            await host._save_config_if_possible()
            return False, "配置保存失败，已恢复修改前的运行配置。", old, old
    if not saved:
        logger.debug("答疑设置只更新运行态,未找到可写配置项: key=%s", key)
        return False, "配置项无法写入，已恢复修改前的运行配置。", old, old
    try:
        dispatch_runtime_config_effects(
            host, {key: normalized}, source="manual_command",
            adapter=getattr(host, "page_api", None), apply_plain_values=True,
        )
    except Exception as exc:
        old_config_value = old
        if key == "rest_reply_probability":
            old_config_value = max(0, min(100, int(round(_safe_float(old, 0.0, 0.0) * 100))))
        _set_into_config(config, key, old_config_value)
        if key == "enable_message_debounce":
            _set_into_config(config, "enable_semantic_message_debounce", old_semantic_debounce)
        if key == "text_message_debounce_seconds":
            _set_into_config(config, "semantic_message_debounce_seconds", old_semantic_seconds)
        await host._save_config_if_possible()
        dispatch_runtime_config_effects(
            host, {key: old}, source="manual_command_rollback",
            adapter=getattr(host, "page_api", None), apply_plain_values=True,
        )
        logger.warning("答疑设置运行态应用失败: key=%s error_type=%s", key, type(exc).__name__)
        return False, "配置运行态应用失败，已恢复修改前的配置。", old, old
    return True, "", old, normalized


def format_setting_usage(allowed_keys: list[str]) -> str:
    allowed = "、".join(sorted(allowed_keys)[:12])
    return (
        "请这样写：陪伴 答疑设置 <配置项> <值>\n"
        "例如：陪伴 答疑设置 group_high_intensity_wakeup_threshold 5\n"
        "也可以：陪伴 答疑设置 高强度阈值 5\n"
        f"可改配置很多，前几个是：{allowed} ..."
    )


def format_setting_result(host: Any, key: str, old: Any, new: Any) -> str:
    label = host._companion_manual_config_label(key)
    old_text = host._companion_manual_format_config_item_value(key, old)
    new_text = host._companion_manual_format_config_item_value(key, new)
    if host._companion_manual_values_equal(old, new):
        return f"配置没有变化：\n{key}（{label}）本来就是 {new_text}"
    return f"已修改并保存配置：\n{key}（{label}）：由 {old_text} 改为 {new_text}"


async def apply_setting_command(host: Any, event: Any, text: str) -> str:
    if not can_apply_config(host, event):
        return host._management_denied_text()
    key, value = parse_setting_text(
        text, host._companion_manual_config_aliases(), host._companion_manual_config_key_from_alias,
    )
    if not key or not value:
        return format_setting_usage(list(host._companion_manual_config_specs().keys()))
    ok, error, old, new = await apply_config_value(host, key, value)
    if not ok:
        return error
    host._companion_manual_pending_store().pop(host._companion_manual_pending_key(event), None)
    host._save_data_sync(sections=RUNTIME_SETTINGS_SECTIONS)
    return format_setting_result(host, key, old, new)


async def apply_pending_config(host: Any, event: Any) -> str:
    if not can_apply_config(host, event):
        return host._management_denied_text()
    pending = get_pending_config(host, event)
    if not pending:
        return "没有待确认的答疑配置建议。先用：陪伴 答疑 <问题>"
    changes = pending.get("changes") if isinstance(pending.get("changes"), list) else []
    if not changes:
        return "这次答疑没有可执行配置建议。"
    lines = ["已按刚才的答疑建议修改配置："]
    applied = 0
    for item in changes:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        ok, error, old, new = await apply_config_value(host, key, item.get("value"))
        if not ok:
            lines.append(f"- {key}：跳过，{error}")
            continue
        applied += 1
        lines.append(
            f"- {key}（{host._companion_manual_config_label(key)}）："
            f"由 {host._companion_manual_format_config_item_value(key, old)} 改为 {host._companion_manual_format_config_item_value(key, new)}"
            f"；{_single_line(item.get('reason'), 120) or '按答疑建议调整'}"
        )
    host._companion_manual_pending_store().pop(host._companion_manual_pending_key(event), None)
    host._save_data_sync(sections=RUNTIME_SETTINGS_SECTIONS)
    if applied <= 0:
        return "没有成功应用的配置项。"
    lines.append("已保存到插件配置；如果 AstrBot 配置对象不支持同步保存，日志里会提示。")
    return "\n".join(lines)


def cancel_pending_config(host: Any, event: Any) -> str:
    store = host._companion_manual_pending_store()
    key = host._companion_manual_pending_key(event)
    existed = key in store
    store.pop(key, None)
    host._save_data_sync(sections=PENDING_CONFIG_SECTION)
    return "已取消刚才的答疑配置建议。" if existed else "当前没有待确认的答疑配置建议。"
