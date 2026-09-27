# -*- coding: utf-8 -*-
"""passive_state_pipeline 的提示词片段构造函数（从宿主模块级搬出，宿主 re-export 保持旧命名空间）。

由 tmp/refactor/psp_split2.py 机械搬运，行为不变。
"""
from __future__ import annotations

from typing import Any

from .passive_state_pipeline_shared import _psp_host
from .conversation_prompt_section import PromptSection


def _turn_continuation_prompt_section(
    messages: str,
    *,
    private_chat: bool,
) -> PromptSection:
    ending = (
        "不要逐条回复,也不要表现得像用户重复催促：\n{messages}"
        if private_chat
        else "不要逐条回复：\n{messages}"
    )
    return _psp_host.prompt_section(
        key="turn.continuation",
        title="本轮用户连续补充",
        source="message_debounce",
        template=(
            "用户刚刚在短时间内连续补充了几句,请把它们当作同一轮完整发言理解,"
            + ending
        ),
        variables={"messages": messages},
    )


def _deferred_private_image_prompt_section(*, key: str, content: str) -> PromptSection:
    return _psp_host.prompt_section(
        key=key,
        title="本轮延迟图片",
        source="private_image",
        content=content,
    )


def _reply_private_image_prompt_section(*, key: str, content: str) -> PromptSection:
    return _psp_host.prompt_section(
        key=key,
        title="本轮引用图片",
        source="private_image",
        content=content,
    )


def _persona_core_emphasis_prompt_section() -> PromptSection:
    return _psp_host.prompt_section(
        key="persona.core_emphasis",
        title="人格核心强调",
        source="persona_core",
        content=(
            "你当前的人格核心设定（system prompt 中的人格定义）是最高优先级，"
            "决定你的说话风格、关系框架和行为底线。\n"
            "以下所有上下文注入（记忆、状态、关系、生活习惯、环境、日程、陪伴线索等）"
            "均为辅助参考，权重低于人格核心：\n"
            "- 如果上下文信息与人格核心存在冲突，以人格核心为准\n"
            "- 上下文仅提供本轮补充信息，不改变人格的基本设定和扮演方向\n"
            "- 保持人格扮演的稳定性，不因上下文信息而偏离人格核心\n"
            "- 语言风格、口癖、称呼、语气和表达方式始终贴合人格核心设定；"
            "记忆、表达学习或上下文里的个别措辞只能作参考，不得把与人格设定不符的称呼、口癖或腔调带进回复"
        ),
    )


def _neutralize_stale_reaction_feedback_compat(req: Any) -> None:
    """Best-effort cleanup for plugin instances missing the newer hook."""
    contexts = getattr(req, "contexts", None)
    if not isinstance(contexts, list) or not contexts:
        return
    tag_pattern = _psp_host.re.compile(
        r"(?:<|&lt;|\\<)\s*/?\s*pc[_-]?reaction[_-]?expression\b[^>]*?(?:>|&gt;|\\>)"
        r".*?"
        r"(?:<|&lt;|\\<)\s*/\s*pc[_-]?reaction[_-]?expression\s*(?:>|&gt;|\\>)",
        flags=_psp_host.re.IGNORECASE | _psp_host.re.DOTALL,
    )

    def clean(value: Any) -> tuple[Any, bool]:
        if isinstance(value, str):
            updated = tag_pattern.sub("", value)
            updated = _psp_host.re.sub(r"\n{3,}", "\n\n", updated).strip()
            return updated, updated != value
        if isinstance(value, dict):
            updated = dict(value)
            changed = False
            for key in ("content", "text", "value"):
                if key in updated:
                    updated[key], item_changed = clean(updated[key])
                    changed = changed or item_changed
            return updated, changed
        if isinstance(value, list):
            items = []
            changed = False
            for item in value:
                cleaned, item_changed = clean(item)
                items.append(cleaned)
                changed = changed or item_changed
            return items, changed
        return value, False

    sanitized = []
    changed = False
    for item in contexts:
        cleaned, item_changed = clean(item)
        sanitized.append(cleaned)
        changed = changed or item_changed
    if changed:
        try:
            req.contexts = sanitized
        except Exception:
            pass
