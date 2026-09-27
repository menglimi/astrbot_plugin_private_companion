# -*- coding: utf-8 -*-
"""参考图与素材域。

由 tools/split_mixin_domain.py 从 page_api_media.py 机械抽取（51 个方法 + 11 个模块级名字 + 0 个类级赋值 / 1804 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApiMediaMixin）。
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import io
import json
import math
import mimetypes
import os
import re
import time
import uuid
from .conversation_prompt_section import (
    PromptRenderMode,
    prompt_document,
    prompt_section,
    render_prompt_document,
    render_prompt_sections,
)
from .helpers import _path_text, _safe_int
from .page_api_shared import _page_api_host_request as request
from .photo_reference_catalog import (
    CATALOG_VERSION,
    CatalogValidationError,
    MAX_LIBRARY_REFERENCES,
    PhotoReference,
    load_catalog,
    project_reference_candidate,
)
from .photo_reference_metadata import (
    build_reference_metadata_review_prompt,
    compile_reference_metadata,
    merge_reference_questionnaire_evidence,
    normalize_reviewed_reference_intent,
)
from .photo_reference_selection import SelectionResult, run_photo_selection_trial
from .reference_asset_gate import ReferenceAssetGate
try:
    from PIL import Image as PILImage
    from PIL import ImageOps as PILImageOps
except Exception:  # pragma: no cover - Pillow 缺失时回退到原图预览
    PILImage = None
    PILImageOps = None
from .reference_assets import (
    REFERENCE_ASSET_MAX_BYTES,
    REFERENCE_ASSET_MAX_PER_OWNER,
    REFERENCE_ASSET_MAX_TOTAL,
    REFERENCE_ASSET_ROLES,
    normalize_reference_asset,
    normalize_reference_asset_scope,
    normalize_reference_owner_id,
)
from contextlib import asynccontextmanager
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote, urlparse

from .logging_util import get_module_logger

logger = get_module_logger(__name__)



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

PHOTO_REFERENCE_PREVIEW_MAX_BYTES = 20 * 1024 * 1024

PHOTO_REFERENCE_METADATA_REVIEW_TIMEOUT_SECONDS = 60.0

PHOTO_REFERENCE_ASSET_MAX_BYTES = 12 * 1024 * 1024

PHOTO_REFERENCE_ASSET_MAX_COUNT = 256

PHOTO_REFERENCE_ASSET_MAX_PER_OWNER = 32

PHOTO_REFERENCE_UPLOAD_MAX_COUNT = 256

PHOTO_REFERENCE_UPLOAD_MAX_TOTAL_BYTES = 1024 * 1024 * 1024

PHOTO_REFERENCE_UPLOAD_MAX_REQUEST_BYTES = 20 * 1024 * 1024

PHOTO_REFERENCE_ASSET_SCOPES = {"relation_user", "group", "knowledge"}

PHOTO_REFERENCE_ASSET_MIMES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}



class _page_api_media_referenceHostRef:
    """延迟引用宿主 page_api_media_reference 模块，保证 patch 宿主全局名对全部域生效。"""

    def __getattr__(self, name: str):
        from . import page_api_media_reference as _host_module

        return getattr(_host_module, name)


_page_api_media_reference_host = _page_api_media_referenceHostRef()
