# -*- coding: utf-8 -*-
"""PrivateImageTranscribeGroupMixin。

由 tools/split_mixin_domain.py 从 private_image.py 机械抽取（13 个方法 + 0 个模块级名字 + 0 个类级赋值 / 1024 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateImageMixin）。
"""
from __future__ import annotations

import asyncio
import hashlib
import time
from .conversation_injection_plan import get_conversation_injection_plan
from .conversation_prompt_section import PromptRenderMode, prompt_section, render_prompt_sections
from .helpers import (
    _missing_optional_model_dependency,
    _safe_float,
    _safe_int,
    _single_line,
    _strip_internal_message_blocks,
)
from .persona_config import runtime_persona_setting
from .private_image_shared import _private_image_host, logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.provider import ProviderRequest
from typing import Any
from .private_image_transcribe_group_part03 import PrivateImageTranscribeGroupPart03Mixin
from .private_image_transcribe_group_part02 import PrivateImageTranscribeGroupPart02Mixin
from .private_image_transcribe_group_part01 import PrivateImageTranscribeGroupPart01Mixin



class PrivateImageTranscribeGroupMixin(PrivateImageTranscribeGroupPart01Mixin, PrivateImageTranscribeGroupPart02Mixin, PrivateImageTranscribeGroupPart03Mixin):
    """PrivateImageTranscribeGroupMixin（从 PrivateImageMixin 拆出）。"""
