# -*- coding: utf-8 -*-
"""生图诊断域。

由 tools/split_mixin_domain.py 从 page_api_media.py 机械抽取（27 个方法 + 0 个模块级名字 + 0 个类级赋值 / 1621 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApiMediaMixin）。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import secrets
import time
from .diagnostic_envelope import diagnostic_test_id
from .helpers import _path_text, _redact_outbound_secrets
from .page_api_shared import _page_api_host_request as request
from .page_backend import generation_log_candidates
from .photo_reference_catalog import CATALOG_VERSION, CatalogValidationError, PhotoReference, load_catalog
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

from .logging_util import get_module_logger

logger = get_module_logger(__name__)



class _page_api_media_diagnosticsHostRef:
    """延迟引用宿主 page_api_media_diagnostics 模块，保证 patch 宿主全局名对全部域生效。"""

    def __getattr__(self, name: str):
        from . import page_api_media_diagnostics as _host_module

        return getattr(_host_module, name)


_page_api_media_diagnostics_host = _page_api_media_diagnosticsHostRef()
