# -*- coding: utf-8 -*-
"""task_prompt_registry 拆分件 part04（机械搬移，行为不变）。

函数 / 常量 / 类逐字搬自 task_prompt_registry.py，仅调整模块级依赖的导入来源。
"""
from typing import Any, Mapping

from .task_prompt_registry_part01 import (
    TASK_PROMPT_GROUPS,
    _PROMPT_MARKER_CLOSE,
    _PROMPT_MARKER_OPEN,
    _TaskPromptDefinition,
)
from .task_prompt_registry_part03 import (
    _TASK_BY_KEY,
    _TASK_DEFINITIONS,
    _TASK_PATTERN_BY_KEY,
    _normalize_task_key,
    builtin_task_prompt_preview,
    normalize_task_prompt_overrides,
    resolve_task_prompt_override,
    task_prompt_metadata,
)


def catalog_task_prompts(overrides: Any = None) -> list[dict[str, Any]]:
    """Return the complete frontend catalog with effective custom prompts."""
    normalized = normalize_task_prompt_overrides(overrides)
    definitions = list(_TASK_DEFINITIONS)
    known = set(_TASK_BY_KEY)
    for task_key in normalized:
        # Keep configured family-prefix overrides visible as first-class
        # rows.  They are intentionally omitted from the empty catalog (the
        # concrete tasks already advertise that family), but hiding a saved
        # ``qzone_``/``persona_`` override makes it impossible to inspect or
        # edit the generic constraint from the panel.
        if task_key in known:
            continue
        metadata = task_prompt_metadata(task_key)
        if metadata is None:
            continue
        definitions.append(
            _TaskPromptDefinition(
                task_key=task_key,
                name=str(metadata["name"]),
                group=str(metadata["group"]),
                description=str(metadata["description"]),
                provider_key=str(metadata["provider_key"]),
                dynamic=True,
            )
        )

    group_order = {name: index for index, name in enumerate(TASK_PROMPT_GROUPS)}
    definitions.sort(key=lambda item: (group_order.get(item.group, 999), item.task_key))
    rows: list[dict[str, Any]] = []
    for item in definitions:
        if item.task_key in _TASK_PATTERN_BY_KEY:
            custom_prompt = normalized.get(item.task_key, "")
            override_key = item.task_key if custom_prompt else ""
        else:
            custom_prompt, override_key = resolve_task_prompt_override(item.task_key, normalized)
        rows.append(
            {
                "task_key": item.task_key,
                "name": item.name,
                "group": item.group,
                "description": item.description,
                "provider_key": item.provider_key,
                "builtin_prompt": builtin_task_prompt_preview(item.task_key),
                "builtin_prompt_dynamic": True,
                "custom_prompt": custom_prompt,
                "customized": bool(custom_prompt),
                "override_key": override_key,
                "dynamic": item.dynamic,
            }
        )
    return rows


def apply_task_prompt_override(
    task_key: Any,
    prompt: str,
    system_prompt: str | None = None,
    overrides: Any = None,
) -> tuple[str, str | None]:
    """Append a plugin task override to the system prompt, if configured."""
    user_prompt = str(prompt or "")
    custom_prompt, _ = resolve_task_prompt_override(task_key, overrides)
    key = _normalize_task_key(task_key)
    if not custom_prompt or not key:
        return user_prompt, system_prompt

    start_marker = f"{_PROMPT_MARKER_OPEN}插件任务附加指令开始：{key}{_PROMPT_MARKER_CLOSE}"
    end_marker = f"{_PROMPT_MARKER_OPEN}插件任务附加指令结束：{key}{_PROMPT_MARKER_CLOSE}"
    current_system = str(system_prompt or "")
    if start_marker in current_system:
        return user_prompt, current_system
    block = f"{start_marker}\n{custom_prompt}\n{end_marker}"
    combined = f"{current_system.rstrip()}\n\n{block}" if current_system.strip() else block
    return user_prompt, combined
