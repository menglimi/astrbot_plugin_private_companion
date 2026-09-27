# -*- coding: utf-8 -*-
from __future__ import annotations

import re
import hashlib
import hmac
import json
import time
import uuid
from copy import deepcopy
from datetime import datetime
from typing import Any

from .page_api_shared import _page_api_host, _page_api_host_request as request

from .helpers import _safe_int
from .companion_interaction_expression import allowed_expression_bands, current_interaction_projection
from .emotion_diagnostics import build_emotion_trace_projection, emotion_trace_summary
from .relationship_ledger import (
    normalize_relationship_mode,
    record_manual_relationship_change,
    relationship_positive_score_cap,
)
from .migration_backfill import legacy_pending_reference
from .logging_util import get_module_logger

logger = get_module_logger(__name__)



class _page_api_users_groupsHostRef:
    """延迟引用宿主 page_api_users_groups 模块，保证 patch 宿主全局名对全部域生效。"""

    def __getattr__(self, name: str):
        from . import page_api_users_groups as _host_module

        return getattr(_host_module, name)


_page_api_users_groups_host = _page_api_users_groupsHostRef()
