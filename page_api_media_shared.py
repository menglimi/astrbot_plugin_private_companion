# -*- coding: utf-8 -*-
"""图片 / 素材 / 参考图 域页面 API。

由 tools/split_page_api_media.py 从 page_api.py 机械抽取（123 个方法 / 4287 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApi）。
"""
from __future__ import annotations

import asyncio
import io
import json
import math
import os
import time
import re
import base64
import binascii
import hashlib
import mimetypes
import secrets
import uuid
from contextlib import asynccontextmanager
from copy import copy, deepcopy
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote, urlparse
from quart import send_file
from .page_api_shared import _page_api_host
from .conversation_prompt_section import (
    PromptRenderMode,
    prompt_document,
    prompt_heading_ref,
    prompt_section,
    render_prompt_content,
    render_prompt_document,
    render_prompt_sections,
)
from .diagnostic_envelope import DIAGNOSTIC_ENVELOPE_VERSION, diagnostic_test_id, normalize_diagnostic_result
from .helpers import _MISSING, _flat_get, _normalize_timezone_name, _normalize_timezone_setting, _path_text, _redact_outbound_secrets, _safe_int, _set_into_config, _strip_internal_message_blocks, _text_looks_garbled, _text_similarity, _today_key, normalize_bot_relationship_cards
from .wardrobe_assets import asset_abs_path, asset_root, load_asset_index
from .reference_asset_gate import ReferenceAssetGate
from .owned_reaction_asset_catalog import MAX_ASSET_BYTES, OwnedReactionAssetCatalog
from .photo_reference_catalog import (
    CATALOG_VERSION,
    MAX_LIBRARY_REFERENCES,
    CatalogValidationError,
    PhotoReference,
    load_catalog,
    project_reference_candidate,
    validate_and_serialize,
)
from .photo_reference_metadata import (
    build_reference_metadata_review_prompt,
    compile_reference_metadata,
    merge_reference_questionnaire_evidence,
    normalize_reviewed_reference_intent,
)
from .photo_reference_selection import SelectionResult, run_photo_selection_trial
from .reference_assets import (
    REFERENCE_ASSET_MAX_BYTES,
    REFERENCE_ASSET_MAX_PER_OWNER,
    REFERENCE_ASSET_MAX_TOTAL,
    REFERENCE_ASSET_ROLES,
    normalize_reference_asset,
    normalize_reference_asset_scope,
    normalize_reference_owner_id,
)
from .page_backend import MigrationBackupService, build_route_bindings, generation_log_candidates
from .logging_util import get_module_logger
from .page_api_media_reference import PrivateCompanionPageApiMediaReferenceMixin
from .page_api_media_diagnostics import PrivateCompanionPageApiMediaDiagnosticsMixin
try:
    from PIL import Image as PILImage
    from PIL import ImageOps as PILImageOps
except Exception:  # pragma: no cover - Pillow 缺失时回退到原图预览
    PILImage = None
    PILImageOps = None
# ---------------------------------------------------------------------------
# Host-module globals re-materialised for this mixin module.
# These names are referenced by the moved method bodies but are defined at
# module scope in page_api.py (as globals / module-level functions), so the
# mechanical split could not pick them up from import statements alone.
# No behaviours are changed: only definitions are repeated here so that the
# module can resolve its own globals through the normal module namespace.
# ---------------------------------------------------------------------------
logger = get_module_logger(__name__)



PLUGIN_NAME = "astrbot_plugin_private_companion"
PAGE_API_PREFIX = f"/{PLUGIN_NAME}/page"
IMAGE_CACHE_THUMBNAIL_MAX_EDGE = 160
IMAGE_CACHE_THUMBNAIL_QUALITY = 78
# The guided editor must never leave a WebUI request waiting forever when the
# configured main model or its upstream connection stops responding.
# Reference assets are an independent store for member/role/knowledge images. Keep
# the limits generous enough for a small visual knowledge base while preventing
# an accidental page upload from exhausting the plugin data directory.
# WebUI catalog uploads are content-addressed so repeated submissions do not
# create another copy of the same image. These limits cover abandoned uploads
# that are no longer referenced by the saved catalog.
# A 12 MiB image expands to roughly 16 MiB when Base64 encoded. Leave room for
# the data URL and JSON envelope, while rejecting oversized bodies before
# Quart parses them into memory.



class _page_api_mediaHostRef:
    """延迟引用宿主 page_api_media 模块，保证 patch 宿主全局名对全部域生效。"""

    def __getattr__(self, name: str):
        from . import page_api_media as _host_module

        return getattr(_host_module, name)


_page_api_media_host = _page_api_mediaHostRef()


class _PageApiMediaRequestProxy:
    """`request` 的双向可 patch 代理（宿主门面 page_api_media）。

    方法搬到 page_api_media_part*.py 后，域模块的 `request` 若直接绑定
    page_api 的代理，tests 对宿主门面 `page_api_media.request` 的 patch
    （test_reaction_expression_page_api.py）就会静默失效。本代理把取值推迟
    到调用时经宿主门面解析，两种 patch 风格都生效。
    """

    def __getattr__(self, name: str):
        from . import page_api_media as _host_module

        return getattr(getattr(_host_module, "request"), name)


request = _PageApiMediaRequestProxy()
