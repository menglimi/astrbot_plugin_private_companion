# -*- coding: utf-8 -*-
"""表达规则归一去重归并。

由 tools/split_mixin_domain.py 从 user_memory.py 机械抽取（36 个方法 + 0 个模块级名字 + 0 个类级赋值 / 1436 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 UserMemoryMixin）。
"""
from __future__ import annotations

import hashlib
import re
from .helpers import _now_ts, _safe_float, _safe_int, _single_line
from .persona_config import runtime_persona_setting
from copy import deepcopy
from datetime import datetime
from typing import Any
from .user_memory_expression_rule_part03 import UserMemoryExpressionRulePart03Mixin
from .user_memory_expression_rule_part02 import UserMemoryExpressionRulePart02Mixin
from .user_memory_expression_rule_part01 import UserMemoryExpressionRulePart01Mixin



class UserMemoryExpressionRuleMixin(UserMemoryExpressionRulePart01Mixin, UserMemoryExpressionRulePart02Mixin, UserMemoryExpressionRulePart03Mixin):
    """表达规则归一去重归并（从 UserMemoryMixin 拆出）。"""
