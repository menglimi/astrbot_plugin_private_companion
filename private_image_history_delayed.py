# -*- coding: utf-8 -*-
"""PrivateImageHistoryDelayedMixin。

由 tools/split_mixin_domain.py 从 private_image.py 机械抽取（17 个方法 + 0 个模块级名字 + 0 个类级赋值 / 1146 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateImageMixin）。
"""
from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from .conversation_injection_plan import get_conversation_injection_plan
from .conversation_prompt_section import PromptSection, prompt_section
from .helpers import _safe_float, _single_line, _strip_outbound_control_blocks
from .persona_config import runtime_persona_setting
from .private_image_shared import _private_image_host, logger
from .segmented_message import sanitize_llm_segment_control_tokens
from astrbot.api.event import AstrMessageEvent
from astrbot.api.provider import ProviderRequest
from astrbot.core.agent.message import AssistantMessageSegment, TextPart, UserMessageSegment
from astrbot.core.astr_main_agent import MainAgentBuildConfig, build_main_agent
from typing import Any
from .private_image_history_delayed_part04 import PrivateImageHistoryDelayedPart04Mixin
from .private_image_history_delayed_part03 import PrivateImageHistoryDelayedPart03Mixin
from .private_image_history_delayed_part02 import PrivateImageHistoryDelayedPart02Mixin
from .private_image_history_delayed_part01 import PrivateImageHistoryDelayedPart01Mixin



class PrivateImageHistoryDelayedMixin(PrivateImageHistoryDelayedPart01Mixin, PrivateImageHistoryDelayedPart02Mixin, PrivateImageHistoryDelayedPart03Mixin, PrivateImageHistoryDelayedPart04Mixin):
    """PrivateImageHistoryDelayedMixin（从 PrivateImageMixin 拆出）。"""
