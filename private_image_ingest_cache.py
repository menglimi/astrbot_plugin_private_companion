# -*- coding: utf-8 -*-
"""PrivateImageIngestCacheMixin。

由 tools/split_mixin_domain.py 从 private_image.py 机械抽取（37 个方法 + 0 个模块级名字 + 0 个类级赋值 / 1054 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateImageMixin）。
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import html
import io
import os
import re
import shutil
import tempfile
import urllib.request
from .conversation_injection_plan import PLACEMENT_DYNAMIC_SYSTEM, get_conversation_injection_plan
from .conversation_prompt_section import PromptSection
from .helpers import _missing_optional_model_dependency, _safe_float, _safe_int, _single_line
from .persona_config import runtime_persona_setting
from .private_image_shared import PREPARED_IMAGE_MAX_AGE_SECONDS, _private_image_host, logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.provider import ProviderRequest
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlparse, urlsplit, urlunparse, urlunsplit
from .private_image_ingest_cache_part03 import PrivateImageIngestCachePart03Mixin
from .private_image_ingest_cache_part02 import PrivateImageIngestCachePart02Mixin
from .private_image_ingest_cache_part01 import PrivateImageIngestCachePart01Mixin



class PrivateImageIngestCacheMixin(PrivateImageIngestCachePart01Mixin, PrivateImageIngestCachePart02Mixin, PrivateImageIngestCachePart03Mixin):
    """PrivateImageIngestCacheMixin（从 PrivateImageMixin 拆出）。"""
