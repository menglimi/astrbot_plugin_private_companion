# -*- coding: utf-8 -*-
"""互动查询与转发发送域。

由 tools/split_mixin_domain.py 从 llm_tool_actions.py 机械抽取（28 个方法 + 0 个模块级名字 + 0 个类级赋值 / 1126 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 LlmToolActionsMixin）。
"""
from __future__ import annotations

import json
import re
import uuid
from .conversation_prompt_section import PromptSection, prompt_section
from .helpers import _now_ts, _safe_float, _safe_int, _single_line
from .interaction_query_orchestrator import execute_interaction_query
from .interaction_tool_contract import InteractionQuery
from .llm_tool_actions_shared import _render_tool_prompt_section_labeled, logger
from .persona_config import runtime_persona_setting
from astrbot.api.event import AstrMessageEvent, MessageChain
from astrbot.core.platform.message_session import MessageSession
from typing import Any
from .llm_tool_actions_interaction_relay_part04 import LlmToolActionsInteractionRelayPart04Mixin
from .llm_tool_actions_interaction_relay_part03 import LlmToolActionsInteractionRelayPart03Mixin
from .llm_tool_actions_interaction_relay_part02 import LlmToolActionsInteractionRelayPart02Mixin
from .llm_tool_actions_interaction_relay_part01 import LlmToolActionsInteractionRelayPart01Mixin

try:
    from astrbot.api.message_components import At, Plain
except ImportError:
    from astrbot.api.message_components import At, Plain



class LlmToolActionsInteractionRelayMixin(LlmToolActionsInteractionRelayPart01Mixin, LlmToolActionsInteractionRelayPart02Mixin, LlmToolActionsInteractionRelayPart03Mixin, LlmToolActionsInteractionRelayPart04Mixin):
    """互动查询与转发发送域（从 LlmToolActionsMixin 拆出）。"""
