# -*- coding: utf-8 -*-
"""表达 / 表达库 域页面 API。

由 tools/split_mixin_domain.py 从 page_api.py 机械抽取（27 个方法 / 2388 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApi）。

"""
from __future__ import annotations

import json
import time
import re
import hmac
import hashlib
from copy import copy, deepcopy
from datetime import date, datetime, timedelta
from typing import Any, Mapping
from quart import send_file
from .page_api_shared import _page_api_host, _page_api_host_request as request
from .helpers import _MISSING, _flat_get, _normalize_timezone_name, _normalize_timezone_setting, _path_text, _redact_outbound_secrets, _safe_int, _set_into_config, _strip_internal_message_blocks, _text_looks_garbled, _text_similarity, _today_key, normalize_bot_relationship_cards
from .expression_scope_ownership import (
    ExpressionScopeError,
    bind_expression_item,
    bind_expression_profile,
    validate_expression_scope_binding,
)
from .reaction_asset_library import get_reaction_asset_library
from .logging_util import get_module_logger

logger = get_module_logger(__name__)



class _page_api_expressionHostRef:
    """延迟引用宿主 page_api_expression 模块，保证 patch 宿主全局名对全部域生效。"""

    def __getattr__(self, name: str):
        from . import page_api_expression as _host_module

        return getattr(_host_module, name)


_page_api_expression_host = _page_api_expressionHostRef()
