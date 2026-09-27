# -*- coding: utf-8 -*-
"""人格 / 人设 / 角色扮演草稿 域页面 API。

由 tools/split_page_api_persona.py 从 page_api.py 机械抽取（58 个方法 / 2946 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApi）。

注：本文件自带 _render_page_background_prompt_pair 的副本，与 page_api.py / page_api_media.py 保持一致——
该函数同时被非 persona 域使用且被测试直接引用，故宿主的定义保留不动。
"""
from __future__ import annotations

import asyncio
import functools
import json
import os
import time
import re
import uuid
from copy import copy, deepcopy
from pathlib import Path
from typing import Any, Mapping
from quart import send_file
from .page_api_shared import _page_api_host, _page_api_host_request as request
from .page_api_persona_config import PrivateCompanionPageApiPersonaConfigMixin
from .page_api_persona_runtime import PrivateCompanionPageApiPersonaRuntimeMixin
from .conversation_prompt_section import (
    PromptRenderMode,
    prompt_document,
    prompt_heading_ref,
    prompt_section,
    render_prompt_content,
    render_prompt_document,
    render_prompt_sections,
)
from .story_authority import (
    StoryAuthorityError,
    story_authority_controller,
    story_legacy_operation,
    story_legacy_operation_if,
)
from .logging_util import get_module_logger

logger = get_module_logger(__name__)



class PrivateCompanionPageApiPersonaMixin(PrivateCompanionPageApiPersonaRuntimeMixin, PrivateCompanionPageApiPersonaConfigMixin):
    """人格 / 人设 / 角色扮演草稿 域（从 PrivateCompanionPageApi 拆出）。"""
