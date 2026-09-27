# -*- coding: utf-8 -*-
"""作品与备忘工具域。

由 tools/split_mixin_domain.py 从 llm_tool_actions.py 机械抽取（33 个方法 + 0 个模块级名字 + 0 个类级赋值 / 1427 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 LlmToolActionsMixin）。
"""
from __future__ import annotations

import json
import re
import time
import uuid
from .conversation_prompt_section import PromptSection, prompt_section
from .helpers import _safe_float, _safe_int, _single_line
from .llm_tool_actions_shared import _render_tool_prompt_section_labeled, logger
from .memo_notes import apply_memo_note_action, memo_note_sort_key, normalize_memo_note
from .persona_config import runtime_persona_setting
from astrbot.api.event import AstrMessageEvent
from datetime import datetime, timedelta
from typing import Any
from .llm_tool_actions_creative_memo_part04 import LlmToolActionsCreativeMemoPart04Mixin
from .llm_tool_actions_creative_memo_part03 import LlmToolActionsCreativeMemoPart03Mixin
from .llm_tool_actions_creative_memo_part02 import LlmToolActionsCreativeMemoPart02Mixin
from .llm_tool_actions_creative_memo_part01 import LlmToolActionsCreativeMemoPart01Mixin



class LlmToolActionsCreativeMemoMixin(LlmToolActionsCreativeMemoPart01Mixin, LlmToolActionsCreativeMemoPart02Mixin, LlmToolActionsCreativeMemoPart03Mixin, LlmToolActionsCreativeMemoPart04Mixin):
    """作品与备忘工具域（从 LlmToolActionsMixin 拆出）。"""
