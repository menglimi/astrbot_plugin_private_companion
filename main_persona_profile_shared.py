# -*- coding: utf-8 -*-
"""人格档案域。

由 tools/split_main_domain.py 从 main.py 机械抽取（33 个方法 / 923 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPlugin）。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import time
import uuid
from .helpers import _set_into_config, _single_line
from .main_shared import _ACTIVE_PERSONA_ID
from .persona_config import (
    PERSONA_SETTINGS_KEY,
    PERSONA_SETTINGS_REVISION_KEY,
    PERSONA_SETTINGS_SCHEMA_VERSION,
    PERSONA_SETTINGS_VERSION_KEY,
    PersonaConfigError,
    PersonaSettingsTypeError,
    copy_from_primary_config,
    create_persona_settings,
    detach_persona_settings,
    migrate_persona_profile,
    normalize_persona_settings,
    normalize_setting_value,
    resolve_effective_settings,
    resolve_persona_setting,
    runtime_persona_setting,
)
from .persona_sqlite_store import load_persona_sqlite_store
from .runtime_config_dispatcher import dispatch_runtime_config_effects
from .story_authority import story_legacy_operation, story_legacy_sync_operation
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from .logging_util import get_module_logger

logger = get_module_logger(__name__)



class _main_persona_profileHostRef:
    """延迟引用宿主 main_persona_profile 模块，保证 patch 宿主全局名对全部域生效。"""

    def __getattr__(self, name: str):
        from . import main_persona_profile as _host_module

        return getattr(_host_module, name)


_main_persona_profile_host = _main_persona_profileHostRef()
