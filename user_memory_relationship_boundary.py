# -*- coding: utf-8 -*-
"""关系情绪判定与边界管教。

由 tools/split_mixin_domain.py 从 user_memory.py 机械抽取（29 个方法 + 0 个模块级名字 + 0 个类级赋值 / 1442 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 UserMemoryMixin）。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import random
import re
import uuid
from .conversation_prompt_section import prompt_section
from .domains.affect.emotion_event_ledger import record_recent_emotion_event
from .domains.affect.emotion_targeting import classify_emotion_target
from .helpers import _now_ts, _safe_float, _safe_int, _single_line, _today_key
from .persona_config import runtime_persona_setting
from .relationship_policy import relationship_stage_for_score
from .user_memory_render_shared import logger, _render_user_memory_background_prompt
from astrbot.api.event import MessageChain
try:
    from astrbot.api.message_components import Plain
except ImportError:  # pragma: no cover
    from astrbot.core.message.components import Plain
from copy import deepcopy
from datetime import datetime
from typing import Any
from .user_memory_relationship_boundary_part04 import UserMemoryRelationshipBoundaryPart04Mixin
from .user_memory_relationship_boundary_part03 import UserMemoryRelationshipBoundaryPart03Mixin
from .user_memory_relationship_boundary_part02 import UserMemoryRelationshipBoundaryPart02Mixin
from .user_memory_relationship_boundary_part01 import UserMemoryRelationshipBoundaryPart01Mixin





class UserMemoryRelationshipBoundaryMixin(UserMemoryRelationshipBoundaryPart01Mixin, UserMemoryRelationshipBoundaryPart02Mixin, UserMemoryRelationshipBoundaryPart03Mixin, UserMemoryRelationshipBoundaryPart04Mixin):
    """关系情绪判定与边界管教（从 UserMemoryMixin 拆出）。"""
