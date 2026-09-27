# -*- coding: utf-8 -*-
"""配置迁移 / 导入导出 / 备份 域页面 API。

由 tools/split_mixin_domain.py 从 page_api.py 机械抽取（46 个方法 / 956 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApi）。

"""
from __future__ import annotations

import asyncio
import json
import time
import re
import hashlib
import secrets
from copy import copy, deepcopy
from pathlib import Path
from typing import Any, Mapping
from quart import send_file
from .page_api_shared import _page_api_host, _page_api_host_request as request
from .config_migration import _config_root_mapping, _ensure_config_parent_dir
from .helpers import _MISSING, _flat_get, _normalize_timezone_name, _normalize_timezone_setting, _path_text, _redact_outbound_secrets, _safe_int, _set_into_config, _strip_internal_message_blocks, _text_looks_garbled, _text_similarity, _today_key, normalize_bot_relationship_cards
from .story_authority import (
    StoryAuthorityError,
    story_authority_controller,
    story_legacy_operation,
    story_legacy_operation_if,
)
from .logging_util import get_module_logger
from .page_backend import MigrationBackupService, build_route_bindings, generation_log_candidates

logger = get_module_logger(__name__)

# 本域方法在宿主模块中用到的模块级常量。
# 拆分后方法体留在本模块，名字必须在本模块可解析，否则运行期 NameError。
PLUGIN_NAME = "astrbot_plugin_private_companion"
_MIGRATION_UNKNOWN_CONFIG_KEY = "_migration_unknown_config_fields_v1"
_MIGRATION_UNKNOWN_NAMESPACES = ("settings", "features", "providers")
_MIGRATION_UNKNOWN_MAX_BYTES = 256 * 1024
_MIGRATION_UNKNOWN_MAX_FIELDS = 128
_MIGRATION_UNKNOWN_SENSITIVE_NAME = re.compile(
    r"(?:access[_-]?token|password|secret|cookie|api[_-]?key|storage[_-])",
    flags=re.I,
)
EXTENSION_MIGRATION_NOTICE_VERSION = "6.2.2"



class _page_api_migrationHostRef:
    """延迟引用宿主 page_api_migration 模块，保证 patch 宿主全局名对全部域生效。"""

    def __getattr__(self, name: str):
        from . import page_api_migration as _host_module

        return getattr(_host_module, name)


_page_api_migration_host = _page_api_migrationHostRef()
