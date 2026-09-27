# -*- coding: utf-8 -*-
"""表达声线与规则场景装配。

由 tools/split_mixin_domain.py 从 user_memory.py 机械抽取（38 个方法 + 0 个模块级名字 + 0 个类级赋值 / 1089 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 UserMemoryMixin）。
"""
from __future__ import annotations

import hashlib
import re
from .conversation_prompt_section import PromptSection, prompt_section
from .expression_scope_ownership import bind_expression_item, bind_expression_profile
from .helpers import _now_ts, _safe_float, _safe_int, _single_line, _strip_internal_message_blocks
from .persona_config import runtime_persona_setting
from .scoped_runtime_view import scoped_approved_expression_rules
from .user_memory_render_shared import _render_conversation_section_labeled
from copy import deepcopy
from datetime import datetime
from typing import Any
from .user_memory_expression_voice_part03 import UserMemoryExpressionVoicePart03Mixin
from .user_memory_expression_voice_part02 import UserMemoryExpressionVoicePart02Mixin
from .user_memory_expression_voice_part01 import UserMemoryExpressionVoicePart01Mixin



class UserMemoryExpressionVoiceMixin(UserMemoryExpressionVoicePart01Mixin, UserMemoryExpressionVoicePart02Mixin, UserMemoryExpressionVoicePart03Mixin):
    """表达声线与规则场景装配（从 UserMemoryMixin 拆出）。"""
