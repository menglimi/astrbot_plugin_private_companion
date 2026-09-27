# -*- coding: utf-8 -*-
"""配置/设置域。

由 tools/split_mixin_domain.py 从 page_api.py 机械抽取（8 个方法 + 2 个模块级名字 + 0 个类级赋值 / 2871 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApi）。
"""
from __future__ import annotations

import asyncio
import re
import time
from .companion_interaction_expression import current_interaction_projection, normalize_normal_interaction_band_cap
from .constants import PAGE_FONT_NAMES, PAGE_THEME_NAMES
from .helpers import _normalize_timezone_name, _normalize_timezone_setting
from .model_routing import build_rules, normalize_scope
from .page_backend import build_route_bindings
from .persona_config import runtime_persona_setting
from .photo_reference_catalog import CATALOG_VERSION, CatalogValidationError, load_catalog, validate_and_serialize
from .relationship_ledger import migrate_relationship_positive_stage_cap, normalize_relationship_positive_stage_cap_key
from .relationship_policy import (
    normalize_relationship_stage_policy,
    normalize_relationship_stage_provider_routes,
    relationship_stage_policy_json,
)
from .runtime_config_dispatcher import TTS_RUNTIME_KEYS
from .story_authority import StoryAuthorityError, story_authority_controller
from copy import deepcopy
from typing import Any

from .logging_util import get_module_logger
from .page_api_shared import _page_api_host, _page_api_host_request as request

logger = get_module_logger(__name__)



PAGE_PRIVATE_CONFIG_KEYS = frozenset({"standalone_webui_access_token"})

CYCLE_SETTING_KEYS = (
    "enable_cycle_state",
    "enable_advanced_cycle_strategy",
    "advanced_cycle_link_intensity",
    "advanced_cycle_start_offset",
    "advanced_cycle_menstrual_days",
    "advanced_cycle_menstrual_prompt",
    "advanced_cycle_menstrual_mood",
    "advanced_cycle_menstrual_energy",
    "advanced_cycle_follicular_days",
    "advanced_cycle_follicular_prompt",
    "advanced_cycle_follicular_mood",
    "advanced_cycle_follicular_energy",
    "advanced_cycle_pre_ovulation_days",
    "advanced_cycle_pre_ovulation_prompt",
    "advanced_cycle_pre_ovulation_mood",
    "advanced_cycle_pre_ovulation_energy",
    "advanced_cycle_ovulation_days",
    "advanced_cycle_ovulation_prompt",
    "advanced_cycle_ovulation_mood",
    "advanced_cycle_ovulation_energy",
    "advanced_cycle_luteal_days",
    "advanced_cycle_luteal_prompt",
    "advanced_cycle_luteal_mood",
    "advanced_cycle_luteal_energy",
    "advanced_cycle_pms_days",
    "advanced_cycle_pms_prompt",
    "advanced_cycle_pms_mood",
    "advanced_cycle_pms_energy",
    "advanced_cycle_discomfort_simulation",
    "advanced_cycle_discomfort_chance",
    "advanced_cycle_discomfort_types",
)



class _page_api_configHostRef:
    """延迟引用宿主 page_api_config 模块，保证 patch 宿主全局名对全部域生效。"""

    def __getattr__(self, name: str):
        from . import page_api_config as _host_module

        return getattr(_host_module, name)


_page_api_config_host = _page_api_configHostRef()
