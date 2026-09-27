# -*- coding: utf-8 -*-
"""TTS/语音域。

由 tools/split_mixin_domain.py 从 page_api.py 机械抽取（25 个方法 + 3 个模块级名字 + 0 个类级赋值 / 974 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApi）。
"""
from __future__ import annotations

import asyncio
import json
import re
import secrets
import time
from .helpers import _redact_outbound_secrets
from astrbot.api.event import MessageChain
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from .logging_util import get_module_logger
from .page_api_shared import _page_api_host, _page_api_host_request as request

logger = get_module_logger(__name__)



TTS_PROVIDER_SYSTEM_KEYS = {"id", "provider", "type", "provider_type", "enable", "hint", "provider_source_id"}

TTS_PROVIDER_SECRET_KEYS = {
    "api_key",
    "azure_tts_subscription_key",
    "gemini_tts_api_key",
    "proxy",
}

FISH_AUDIO_MODEL_OPTIONS = [
    {"value": "s2.1-pro-free", "label": "S2.1 Pro Free"},
    {"value": "s2.1-pro", "label": "S2.1 Pro"},
    {"value": "s2-pro", "label": "S2 Pro"},
    {"value": "s1", "label": "S1 旧版"},
]



class _page_api_ttsHostRef:
    """延迟引用宿主 page_api_tts 模块，保证 patch 宿主全局名对全部域生效。"""

    def __getattr__(self, name: str):
        from . import page_api_tts as _host_module

        return getattr(_host_module, name)


_page_api_tts_host = _page_api_ttsHostRef()
