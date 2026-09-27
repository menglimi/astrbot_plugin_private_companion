# -*- coding: utf-8 -*-
"""人格路由 / 统一身份域。

由 tools/split_main_domain.py 从 main.py 机械抽取（44 个方法 / 1055 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPlugin）。
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import time
import unicodedata
import uuid
from .helpers import _now_ts, _single_line
from .main_shared import (
    _ACTIVE_PERSONA_ID,
    _PERSONA_PROFILE_FORBIDDEN_FILENAME_CHARS,
    _PERSONA_SETTING_MANIFEST,
    _WINDOWS_RESERVED_FILENAME_STEMS,
)
from .migration_scoped_projection import scoped_persona_ref
from .person_context_contract import (
    CONTRACT_NAME as PERSON_CONTRACT_NAME,
    CONTRACT_VERSION as PERSON_CONTRACT_VERSION,
    P3_CONTRACT_NAME,
    P3_CONTRACT_VERSION,
    contract_self_check as person_contract_self_check,
)
from .persona_config import (
    PERSONA_SETTINGS_KEY,
    PERSONA_SETTINGS_REVISION_KEY,
    PERSONA_SETTINGS_SCHEMA_VERSION,
    PERSONA_SETTINGS_VERSION_KEY,
    PersonaConfigError,
    PersonaSettingsTypeError,
    detach_persona_settings,
    migrate_persona_profile,
    runtime_persona_setting,
)
from .persona_sqlite_store import PersonaSqliteStoreRegistry, read_persona_store_snapshot_read_only
from .plugin_identity import PLUGIN_ID
from .story_authority import story_legacy_operation
from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from .logging_util import get_module_logger

logger = get_module_logger(__name__)



class _main_persona_routingHostRef:
    """延迟引用宿主 main_persona_routing 模块，保证 patch 宿主全局名对全部域生效。"""

    def __getattr__(self, name: str):
        from . import main_persona_routing as _host_module

        return getattr(_host_module, name)


_main_persona_routing_host = _main_persona_routingHostRef()
