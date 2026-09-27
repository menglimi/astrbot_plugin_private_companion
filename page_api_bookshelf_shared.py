# -*- coding: utf-8 -*-
"""书架域。

由 tools/split_mixin_domain.py 从 page_api.py 机械抽取（37 个方法 + 2 个模块级名字 + 0 个类级赋值 / 1404 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApi）。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import shutil
import time
from .helpers import _path_text, _strip_internal_message_blocks, _text_similarity
from .story_authority import story_authority_controller
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from .logging_util import get_module_logger
from .page_api_shared import _page_api_host, _page_api_host_request as request

logger = get_module_logger(__name__)



BOOKSHELF_ACCESS_TOKEN_TTL_SECONDS = 24 * 60 * 60

BOOKSHELF_ACCESS_TOKEN_MAX_PERSISTED = 8



class _page_api_bookshelfHostRef:
    """延迟引用宿主 page_api_bookshelf 模块，保证 patch 宿主全局名对全部域生效。"""

    def __getattr__(self, name: str):
        from . import page_api_bookshelf as _host_module

        return getattr(_host_module, name)


_page_api_bookshelf_host = _page_api_bookshelfHostRef()
