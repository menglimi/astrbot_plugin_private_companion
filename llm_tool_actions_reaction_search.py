# -*- coding: utf-8 -*-
"""表情检索与向量域。

由 tools/split_mixin_domain.py 从 llm_tool_actions.py 机械抽取（19 个方法 + 0 个模块级名字 + 0 个类级赋值 / 1096 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 LlmToolActionsMixin）。
"""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
import re
import time
from .helpers import _now_ts, _path_text, _safe_float, _safe_int, _single_line
from .llm_tool_actions_shared import PHOTO_TOOL_SILENT_SENTINEL, logger
from .persona_config import runtime_persona_setting
from .reaction_asset_library import ReactionAssetLibrary
from .reaction_expression import (
    classify_reaction_expression_feedback,
    ensure_reaction_expression_state,
    record_reaction_expression_feedback,
    sync_reaction_expression_auto_preference,
)
from astrbot.api.event import AstrMessageEvent
from typing import Any
from .llm_tool_actions_reaction_search_part03 import LlmToolActionsReactionSearchPart03Mixin
from .llm_tool_actions_reaction_search_part02 import LlmToolActionsReactionSearchPart02Mixin
from .llm_tool_actions_reaction_search_part01 import LlmToolActionsReactionSearchPart01Mixin



class LlmToolActionsReactionSearchMixin(LlmToolActionsReactionSearchPart01Mixin, LlmToolActionsReactionSearchPart02Mixin, LlmToolActionsReactionSearchPart03Mixin):
    """表情检索与向量域（从 LlmToolActionsMixin 拆出）。"""
