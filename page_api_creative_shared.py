# -*- coding: utf-8 -*-
"""创作域。

由 tools/split_mixin_domain.py 从 page_api.py 机械抽取（32 个方法 + 2 个模块级名字 + 0 个类级赋值 / 1187 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApi）。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import mimetypes
import re
import secrets
import time
from .conversation_prompt_section import (
    PromptRenderMode,
    prompt_document,
    prompt_section,
    render_prompt_document,
    render_prompt_sections,
)
from .diagnostic_envelope import diagnostic_test_id
from .helpers import _path_text, _safe_int
from .story_authority import story_legacy_operation
from .wardrobe import WARDROBE_MAX_DESCRIPTION, WARDROBE_MAX_NAME, WARDROBE_MAX_TAG
from copy import deepcopy
from pathlib import Path
from quart import send_file
from .page_api_shared import _page_api_host, _page_api_host_request as request
from typing import Any
from urllib.parse import quote

from .logging_util import get_module_logger
from .page_api_shared import _page_api_host

logger = get_module_logger(__name__)



def _render_page_background_prompt(
    *,
    key: str,
    title: str,
    content: str,
) -> str:
    return render_prompt_sections(
        [
            prompt_section(
                key=key,
                title=title,
                source="page_api",
                content=content,
            )
        ],
        mode=PromptRenderMode.BODY_ONLY,
    )

def _render_page_background_prompt_pair(
    *,
    key: str,
    system_title: str,
    system_content: str,
    user_title: str,
    user_content: str,
) -> tuple[str, str]:
    rendered = render_prompt_document(
        prompt_document(
            system=(
                prompt_section(
                    key=f"{key}.system",
                    title=system_title,
                    source="page_api",
                    content=system_content,
                ),
            ),
            user=(
                prompt_section(
                    key=f"{key}.request",
                    title=user_title,
                    source="page_api",
                    content=user_content,
                ),
            ),
        ),
        mode=PromptRenderMode.BODY_ONLY,
    )
    return rendered["system"], rendered["user"]



class _page_api_creativeHostRef:
    """延迟引用宿主 page_api_creative 模块，保证 patch 宿主全局名对全部域生效。"""

    def __getattr__(self, name: str):
        from . import page_api_creative as _host_module

        return getattr(_host_module, name)


_page_api_creative_host = _page_api_creativeHostRef()
