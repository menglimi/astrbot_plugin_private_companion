# -*- coding: utf-8 -*-
"""生图执行域。

由 tools/split_mixin_domain.py 从 llm_tool_actions.py 机械抽取（2 个方法 + 0 个模块级名字 + 0 个类级赋值 / 1254 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 LlmToolActionsMixin）。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from .helpers import (
    _missing_optional_model_dependency,
    _now_ts,
    _path_text,
    _photo_group_request_matches,
    _safe_float,
    _single_line,
)
from .llm_tool_actions_shared import PHOTO_TOOL_SILENT_SENTINEL, logger
from .persona_config import runtime_persona_setting
from .photo_nai_params import merge_user_photo_nai_params, recent_cached_photo_nai_params
from astrbot.api.event import AstrMessageEvent
from typing import Any
from .llm_tool_actions_photo_generate_part02 import LlmToolActionsPhotoGeneratePart02Mixin
from .llm_tool_actions_photo_generate_part01 import LlmToolActionsPhotoGeneratePart01Mixin



class LlmToolActionsPhotoGenerateMixin(LlmToolActionsPhotoGeneratePart01Mixin, LlmToolActionsPhotoGeneratePart02Mixin):
    """生图执行域（从 LlmToolActionsMixin 拆出）。"""
