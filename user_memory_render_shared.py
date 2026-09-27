# -*- coding: utf-8 -*-
"""user_memory 跨桶共享渲染助手。

由 tools/split_mixin_domain.py 的前置 step0 从 user_memory.py 机械抽取
（3 个模块级函数）。方法体零改动。

这三个助手被 6 个域桶共同引用（expression_voice / relationship_boundary /
inbound_intent / reply_review / companion_record / context_prompt），无法随
任一单桶搬走，故提升到共享模块；宿主保留同名 re-export 以免外部 import 断裂。
"""
from __future__ import annotations

from .logging_util import get_module_logger

# 全族共享的 logger 实例: 宿主从本模块 re-export, 各域子模块也从本模块取,
# 保证既有 patch("...user_memory.logger.xxx") 对已搬走的方法依然生效。
logger = get_module_logger("astrbot_plugin_private_companion.user_memory")

from .conversation_prompt_section import (
    PromptRenderMode,
    PromptSection,
    render_prompt_sections,
)


def _render_conversation_section_labeled(section: PromptSection | None) -> str:
    """Render a canonical section for labeled string-returning call sites."""

    if section is None:
        return ""
    return render_prompt_sections(
        [section],
        mode=PromptRenderMode.LABELED_BLOCK,
    )


def _render_user_memory_background_prompt(section: PromptSection) -> str:
    return render_prompt_sections([section], mode=PromptRenderMode.BODY_ONLY)


def _render_user_memory_labeled_section(section: PromptSection) -> str:
    return render_prompt_sections([section], mode=PromptRenderMode.LABELED_BLOCK)
