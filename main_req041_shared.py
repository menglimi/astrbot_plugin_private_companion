# -*- coding: utf-8 -*-
"""REQ041 迁移 / 作用域重绑域。

由 tools/split_main_domain.py 从 main.py 机械抽取（48 个方法 / 1911 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPlugin）。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import uuid
from .helpers import _now_ts, _set_into_config, _single_line
from .identity_namespace import AssurancePolicy, NamespaceContext
from .migration_backfill import MigrationBackfill, legacy_pending_reference
from .migration_dual_write import MigrationDualWriteProducer
from .migration_read_router import MigrationRelationshipReadRouter
from .migration_replay import MigrationReplayWorker
from .migration_scoped_projection import ScopedProjectionSynchronizer, scoped_group_ref, scoped_persona_ref
from .migration_source_inspector import inspect_migration_sources
from .migration_stability import advance_migration_stability
from .persona_config import runtime_persona_setting
from .relationship_account_store import RelationshipAccountStore
from .relationship_affinity_runtime import (
    admit_confirmed_group_affinity,
    normalize_group_allowlist,
    prepare_group_affinity_candidate,
)
from .relationship_ledger import normalize_relationship_positive_stage_cap_key
from .req041_observability import Req041Observability
from .scoped_runtime_view import overlay_group_runtime_view, overlay_private_runtime_view
from .unified_person_registry import UnifiedPersonRegistry
from astrbot.api.event import AstrMessageEvent
from collections.abc import Collection
from copy import deepcopy
from pathlib import Path
from typing import Any

from .logging_util import get_module_logger

logger = get_module_logger(__name__)



class _main_req041HostRef:
    """延迟引用宿主 main_req041 模块，保证 patch 宿主全局名对全部域生效。"""

    def __getattr__(self, name: str):
        from . import main_req041 as _host_module

        return getattr(_host_module, name)


_main_req041_host = _main_req041HostRef()
