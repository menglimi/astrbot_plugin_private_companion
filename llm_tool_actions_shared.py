# -*- coding: utf-8 -*-
"""llm_tool_actions 族的共享件。

被拆分出的域 mixin 与宿主 llm_tool_actions.py 共同依赖的模块级名字放这里，
保证全族共享同一份绑定：

* ``logger`` —— 全族共享同一 logger 实例（宿主 re-export，各域子模块也从
  本模块取），使既有 ``patch("...llm_tool_actions.logger.xxx")`` 对已搬走的
  方法依然生效。
* ``PHOTO_TOOL_SILENT_SENTINEL`` / ``_render_tool_prompt_section_labeled`` /
  ``_render_tool_prompt_section_labeled_inline`` —— 跨桶共用的模块级常量与
  提示词渲染辅助函数。

本模块处于导入链最上游（不 import 任何同族模块），零导入环。
"""
from __future__ import annotations

from .conversation_prompt_section import (
    PromptRenderMode,
    PromptSection,
    render_prompt_sections,
)
from .logging_util import get_module_logger

# 全族共享的 logger 实例: 宿主从本模块 re-export, 各域子模块也从本模块取,
# 保证既有 patch("...llm_tool_actions.logger.xxx") 对已搬走的方法依然生效。
logger = get_module_logger("astrbot_plugin_private_companion.llm_tool_actions")

PHOTO_TOOL_SILENT_SENTINEL = "[[PC_PHOTO_SENT_NO_FOLLOWUP]]"


def _render_tool_prompt_section_labeled(section: PromptSection | None) -> str:
    if section is None:
        return ""
    return render_prompt_sections([section], mode=PromptRenderMode.LABELED_BLOCK)


def _render_tool_prompt_section_labeled_inline(section: PromptSection | None) -> str:
    if section is None:
        return ""
    return render_prompt_sections([section], mode=PromptRenderMode.LABELED_INLINE)
