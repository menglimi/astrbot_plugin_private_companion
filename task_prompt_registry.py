# -*- coding: utf-8 -*-
"""Catalog and safe overrides for plugin-owned task-model prompts.

This module deliberately knows nothing about AstrBot's ordinary conversation
request.  Callers opt in with a plugin task key, so main-chat system prompts
cannot be changed through this interface.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Mapping

from .constants import MODEL_TASK_PROVIDER_KEYS, MODEL_TASK_PROVIDER_PREFIXES
from .task_prompt_registry_part01 import (
    TASK_PROMPT_CONFIG_KEY,
    TASK_PROMPT_GROUPS,
    TASK_PROMPT_MAX_CHARS,
    _BUILTIN_GROUP_PROMPT_RULES,
    _BUILTIN_TASK_PROMPT_RULES,
    _INVALID_PROMPT_CONTROL_RE,
    _MAIN_CONVERSATION_TASKS,
    _PROMPT_MARKER_CLOSE,
    _PROMPT_MARKER_OPEN,
    _TASK_GROUP_MEMBERS,
    _TASK_KEY_RE,
    _TASK_NAMES,
    _TaskPromptDefinition,
    _TaskPromptFamily,
    _TaskPromptPattern,
    _prompt_section_label,
)
from .task_prompt_registry_part02 import (
    _BUILTIN_AUTHORED_TASK_RULES,
    _BUILTIN_DYNAMIC_FAMILY_RULES,
    _BUILTIN_GROUP_DYNAMIC_FIELDS,
    _BUILTIN_GROUP_EXECUTION_RULES,
    _BUILTIN_GROUP_OUTPUT_CONTRACTS,
    _BUILTIN_GROUP_PROHIBITIONS,
    _BUILTIN_PROMPT_INTRO,
)
from .task_prompt_registry_part03 import (
    TASK_PROMPT_KEYS,
    TASK_PROMPT_PREFIXES,
    _BUILTIN_TASK_INPUT_HINTS,
    _BUILTIN_TASK_OUTPUT_CONTRACTS,
    _PROVIDER_KEY_OVERRIDES,
    _TASK_BY_KEY,
    _TASK_DEFINITIONS,
    _TASK_FAMILIES,
    _TASK_PATTERNS,
    _TASK_PATTERN_BY_KEY,
    _build_definitions,
    _description,
    _dynamic_fields_for_builtin_prompt,
    _family_for_task,
    _normalize_override_key,
    _normalize_task_key,
    _provider_key_for_task,
    _task_rule_for_builtin_prompt,
    builtin_task_prompt,
    builtin_task_prompt_preview,
    normalize_task_prompt_overrides,
    resolve_task_prompt_override,
    task_prompt_metadata,
    validate_task_prompt_override,
)
from .task_prompt_registry_part04 import (
    apply_task_prompt_override,
    catalog_task_prompts,
)


# Every fixed task has a task-specific contract.  A few high-risk call sites
# above keep their full, line-by-line authored wording; the remaining entries
# use the corresponding task contract from ``_BUILTIN_TASK_PROMPT_RULES``.
# Merging here makes the completeness invariant explicit and prevents a new
# task from silently falling through to a name-only generic sentence.
for _task_key, _task_rule in _BUILTIN_TASK_PROMPT_RULES.items():
    _BUILTIN_AUTHORED_TASK_RULES.setdefault(_task_key, _task_rule)

_BUILTIN_AUTHORED_TASK_RULES.update(
    {
        "persona_style_scenarios_batch_*": (
            "为 {{batch_index}} 对应的人格风格情景批次生成约定 JSON。只依据当前人格资料、场景输入和本批次编号，"
            "保持批次之间的独立性与字段顺序，覆盖可观察行为、语言风格和边界示例；不得把一批结果覆盖到另一批，"
            "不得增加未获证据支持的人生经历或敏感设定。"
        ),
        "persona_style_scenarios_json_repair_*": (
            "修复 {{batch_index}} 对应人格风格情景批次的待修复 JSON。只处理括号、引号、字段类型、缺失空值和枚举格式，"
            "保留原有情景文本、人格语义和批次编号；无法恢复的字段留空，不新增情景、不重排批次、不输出 Markdown 或修复说明。"
        ),
    }
)

__all__ = [
    "TASK_PROMPT_CONFIG_KEY",
    "TASK_PROMPT_GROUPS",
    "TASK_PROMPT_KEYS",
    "TASK_PROMPT_MAX_CHARS",
    "TASK_PROMPT_PREFIXES",
    "apply_task_prompt_override",
    "builtin_task_prompt",
    "builtin_task_prompt_preview",
    "catalog_task_prompts",
    "normalize_task_prompt_overrides",
    "resolve_task_prompt_override",
    "task_prompt_metadata",
    "validate_task_prompt_override",
]
