# -*- coding: utf-8 -*-
"""outbound_delivery 域。

由 tools/split_mixin_domain.py 从 proactive_message.py 机械抽取（58 个方法 + 1 个模块级名字 + 0 个类级赋值 / 2228 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveMessageMixin）。
"""
from __future__ import annotations

import asyncio
import inspect
import json
import os
import re
import time
import uuid
from .final_response_persistence import collect_proactive_delivery
from .helpers import (
    _format_history_media_marker,
    _normalize_photo_subject_owner,
    _now_ts,
    _path_text,
    _photo_subject_owner_prompt_label,
    _redact_outbound_secrets,
    _safe_int,
    _single_line,
    _strip_outbound_control_blocks,
    _today_key,
)
from .persona_config import runtime_persona_setting
from .segmented_message import (
    component_kind,
    component_order_from_owner,
    component_strategies_from_owner,
    plan_component_chunks,
    sanitize_llm_segment_control_tokens,
)
from astrbot.api.event import AstrMessageEvent, MessageChain
from astrbot.core.agent.message import AssistantMessageSegment, UserMessageSegment
from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
from astrbot.core.platform.message_session import MessageSession
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.platform import PlatformStatus
from astrbot.core.star.star_handler import EventType, star_handlers_registry
from dataclasses import dataclass
from types import SimpleNamespace
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
def _host_Image():
    """按宿主当前的 ``Image`` 取值，保住 ``patch("<宿主>.Image")`` 的能力。

    ``Image`` 在本模块有**两种互斥**用法：``isinstance(x, Image)`` 需要真实
    class 对象（转发函数会 ``TypeError: isinstance() arg 2 must be a type``），
    而 ``Image.xxx(...)`` 构造调用需要可被替换的入口（测试会 patch 宿主）。
    折中：模块级 ``Image`` 保持真实 class 供 isinstance，构造调用改走本函数 ——
    优先取宿主属性（被 patch 时即替身），否则退回模块级真实 class。
    """
    from . import proactive_message as _host

    candidate = getattr(_host, "Image", None)
    if candidate is None or candidate is Image:
        return Image
    return candidate
# ---- 宿主全局取值助手结束 ----

@dataclass(frozen=True, slots=True)
class _ProactiveSendOutcome:
    delivered: bool
    complete: bool
    delivered_text: str = ""
    image_delivered: bool = False
    extra_components_delivered: int = 0
    note: str = ""
    primary_complete: bool = False
    delivery_umo: str = ""
    delivered_chain: tuple[Any, ...] = ()

    def __bool__(self) -> bool:
        return self.delivered



class _proactive_message_outbound_deliveryHostRef:
    """延迟引用宿主 proactive_message_outbound_delivery 模块，保证 patch 宿主全局名对全部域生效。"""

    def __getattr__(self, name: str):
        from . import proactive_message_outbound_delivery as _host_module

        return getattr(_host_module, name)


_proactive_message_outbound_delivery_host = _proactive_message_outbound_deliveryHostRef()
