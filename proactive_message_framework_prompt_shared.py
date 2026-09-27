# -*- coding: utf-8 -*-
"""framework_prompt 域。

由 tools/split_mixin_domain.py 从 proactive_message.py 机械抽取（33 个方法 + 3 个模块级名字 + 0 个类级赋值 / 2301 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveMessageMixin）。
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import re
import time
import uuid
from .constants import _ACTION_TEXT, _REASON_TEXT
from .conversation_prompt_section import (
    PromptDocument,
    PromptDocumentPart,
    PromptLabelStyle,
    PromptRenderMode,
    PromptSection,
    prompt_document,
    prompt_section,
    render_prompt_document,
    render_prompt_sections,
)
from .helpers import (
    _now_ts,
    _path_text,
    _safe_float,
    _safe_int,
    _single_line,
    _strip_internal_message_blocks,
)
from .memory_context_policy import core_memory_usage_contract_section
from .persona_config import runtime_persona_setting
from .planning import _external_schedule_material_context
from .proactive_message_shared import _PROACTIVE_DOCUMENT_RENDER, _persona_provider_id, _proactive_prompt_part
from .token_budget import _looks_like_upstream_llm_error_response
from astrbot.api.event import AstrMessageEvent, MessageChain
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context
from astrbot.core.astr_main_agent import MainAgentBuildConfig, build_main_agent
from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
from astrbot.core.platform.message_session import MessageSession
from astrbot.core.platform.platform_metadata import PlatformMetadata
from astrbot.core.provider.entities import LLMResponse
from copy import deepcopy
from datetime import datetime
from typing import Any

from .logging_util import get_module_logger

logger = get_module_logger(__name__)



# ---- 宿主全局转发层（由 tmp/refactor/autofix_domain_globals.py 生成）----
# ==== 需真实对象（isinstance/下标/继承）：复制宿主的 import 语句 ====
try:
    from astrbot.api.message_components import At, Image, Plain, Record, Reply
except ImportError:
    from astrbot.api.message_components import At, Image, Plain
    from astrbot.core.message.components import Record
    try:
        from astrbot.api.message_components import Reply
    except ImportError:
        try:
            from astrbot.core.message.components import Reply
        except ImportError:
            Reply = None
# ==== 需实时转发（可被 patch）：同名函数转发宿主 ====
def _now_ts(*args, **kwargs):
    from . import proactive_message as _host
    return getattr(_host, "_now_ts")(*args, **kwargs)
# ---- 宿主全局转发层结束 ----

# ---- 宿主全局取值助手（由 tmp/refactor/autofix_domain_globals.py 生成）----
def _host_build_main_agent():
    """按宿主当前的 ``build_main_agent`` 取值，保住 ``patch("<宿主>.build_main_agent")`` 的能力。

    本模块那份 import 是**真实对象**，测试 patch 宿主模块属性时触达不到它，
    导致绕过替身直接调用真实实现。所有调用点改走本函数即可恢复可替换性。
    """
    from . import proactive_message as _host

    candidate = getattr(_host, "build_main_agent", None)
    if candidate is None or candidate is build_main_agent:
        return build_main_agent
    return candidate
# ---- 宿主全局取值助手结束 ----

class SyntheticPrivateWakeEvent(AstrMessageEvent):
    def __init__(
        self,
        *,
        context: Context,
        session: MessageSession,
        message: str,
        sender_name: str = "PrivateCompanion",
    ) -> None:
        platform_meta = PlatformMetadata(
            name=session.platform_id,
            description="SyntheticPrivateWake",
            id=session.platform_id,
        )

        msg_obj = AstrBotMessage()
        msg_obj.type = session.message_type
        msg_obj.self_id = session.session_id
        msg_obj.session_id = session.session_id
        msg_obj.message_id = f"private_companion_{uuid.uuid4().hex}"
        msg_obj.sender = MessageMember(user_id=session.session_id, nickname=sender_name)
        msg_obj.message = [Plain(message)]
        msg_obj.message_str = message
        msg_obj.raw_message = message
        msg_obj.timestamp = int(time.time())

        super().__init__(message, msg_obj, platform_meta, session.session_id)
        self.session = session
        self.context_obj = context
        self.is_at_or_wake_command = True
        self.is_wake = True

    async def send(self, message: MessageChain) -> None:
        if message is None:
            return
        await self.context_obj.send_message(self.session, message)
        await super().send(message)

class _CapturedSendMessageCall:
    def __init__(self, session: str, messages: list[dict[str, Any]]) -> None:
        self.session = str(session or "")
        self.messages = [dict(item) for item in messages if isinstance(item, dict)]

class _CapturedFrameworkSendMessage(Exception):
    """Stop the framework agent once its send_message_to_user payload is captured."""



class _proactive_message_framework_promptHostRef:
    """延迟引用宿主 proactive_message_framework_prompt 模块，保证 patch 宿主全局名对全部域生效。"""

    def __getattr__(self, name: str):
        from . import proactive_message_framework_prompt as _host_module

        return getattr(_host_module, name)


_proactive_message_framework_prompt_host = _proactive_message_framework_promptHostRef()
