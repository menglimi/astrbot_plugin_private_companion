# -*- coding: utf-8 -*-
"""入站意图习惯与沉默决策。

由 tools/split_mixin_domain.py 从 user_memory.py 机械抽取（35 个方法 + 0 个模块级名字 + 0 个类级赋值 / 1466 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 UserMemoryMixin）。
"""
from __future__ import annotations

import asyncio
import hashlib
import random
import re
import time
from .companion_interaction_expression import current_interaction_projection
from .conversation_prompt_section import prompt_section
from .domains.affect.interaction_dynamics import settle_interaction_dynamics
from .helpers import _now_ts, _safe_float, _safe_int, _single_line, _today_key
from .persona_config import runtime_persona_setting
from .relationship_policy import relationship_stage_for_score
from .user_memory_render_shared import logger, _render_user_memory_background_prompt, _render_user_memory_labeled_section
from datetime import datetime
from typing import Any
from .user_memory_inbound_intent_part04 import UserMemoryInboundIntentPart04Mixin
from .user_memory_inbound_intent_part03 import UserMemoryInboundIntentPart03Mixin
from .user_memory_inbound_intent_part02 import UserMemoryInboundIntentPart02Mixin
from .user_memory_inbound_intent_part01 import UserMemoryInboundIntentPart01Mixin





class UserMemoryInboundIntentMixin(UserMemoryInboundIntentPart01Mixin, UserMemoryInboundIntentPart02Mixin, UserMemoryInboundIntentPart03Mixin, UserMemoryInboundIntentPart04Mixin):
    """入站意图习惯与沉默决策（从 UserMemoryMixin 拆出）。"""
