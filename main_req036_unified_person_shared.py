# -*- coding: utf-8 -*-
"""REQ036 统一人格域。

由 tools/split_main_domain.py 从 main.py 机械抽取（33 个方法 / 1525 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPlugin）。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import time
from .companion_interaction_expression import build_expression_decision, content_intent_from_text, expression_decision_prompt_section
from .context_orchestration import build_context, project_context
from .conversation_injection_plan import PLACEMENT_DYNAMIC_SYSTEM
from .domains.affect.reply_temperature import compose_reply_temperature, reply_temperature_prompt_section
from .helpers import _now_ts, _safe_float, _safe_int, _single_line
from .identity_namespace import NamespaceContext
from .main_shared import _multi_persona_event_context
from .message_pipeline import event_data_save_boundary
from .p4_affinity_confinement import apply_legacy_relationship_delta
from .p4_live_runtime import decide_live_request
from .p4_runtime_gate import SAFE_CONFINEMENT_REPLY
from .p4_shadow import build_p4_shadow
from .person_context_contract import (
    CONTRACT_NAME as PERSON_CONTRACT_NAME,
    CONTRACT_VERSION as PERSON_CONTRACT_VERSION,
    P3_CONTRACT_NAME,
    P3_CONTRACT_VERSION,
    build_identity_key,
)
from .persona_config import runtime_persona_setting
from .plugin_identity import PLUGIN_ID
from .unified_person_registry import UnifiedPersonRegistry
from .unified_profile_contract import (
    build_person_ref as req036_build_person_ref,
    build_portrait_request as req036_build_portrait_request,
    build_profile_dto as req036_build_profile_dto,
    validate_profile_dto as req036_validate_profile_dto,
)
from .unified_profile_service import (
    DEFAULT_UNAUTHORIZED_PRIVATE_REPLY,
    capability_summary as req036_capability_summary,
    private_companion_gate as req036_private_companion_gate,
    proactive_private_gate as req036_proactive_private_gate,
    update_capabilities as req036_update_capabilities,
)
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest
from typing import Any

from .logging_util import get_module_logger

logger = get_module_logger(__name__)



class _main_req036_unified_personHostRef:
    """延迟引用宿主 main_req036_unified_person 模块，保证 patch 宿主全局名对全部域生效。"""

    def __getattr__(self, name: str):
        from . import main_req036_unified_person as _host_module

        return getattr(_host_module, name)


_main_req036_unified_person_host = _main_req036_unified_personHostRef()
