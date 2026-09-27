# -*- coding: utf-8 -*-
"""面板摘要域。

由 tools/split_mixin_domain.py 从 page_api.py 机械抽取（30 个方法 + 0 个模块级名字 + 0 个类级赋值 / 2195 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApi）。
"""
from __future__ import annotations

import re
import sqlite3
import time
from .companion_interaction_expression import current_interaction_projection
from .helpers import _safe_int
from .relationship_ledger import normalize_relationship_mode, relationship_ledger_summary
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .logging_util import get_module_logger
from .page_api_summary_panel_content import PrivateCompanionPageApiSummaryPanelContentMixin
from .page_api_summary_panel_daily import PrivateCompanionPageApiSummaryPanelDailyMixin
from .page_api_summary_panel_runtime import PrivateCompanionPageApiSummaryPanelRuntimeMixin
from .page_api_summary_panel_profile import PrivateCompanionPageApiSummaryPanelProfileMixin

logger = get_module_logger(__name__)



class PrivateCompanionPageApiSummaryPanelMixin(PrivateCompanionPageApiSummaryPanelProfileMixin, PrivateCompanionPageApiSummaryPanelRuntimeMixin, PrivateCompanionPageApiSummaryPanelDailyMixin, PrivateCompanionPageApiSummaryPanelContentMixin):
    """面板摘要域（从 PrivateCompanionPageApi 拆出）。"""
