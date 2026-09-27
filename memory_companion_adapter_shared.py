# -*- coding: utf-8 -*-

from __future__ import annotations

import sys
import uuid
import asyncio
import hashlib
import json
import re
import time
import types
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


from .bot_personal_contract import (
    BOT_PERSONAL_CAPABILITY_SCHEMA_VERSION,
    BOT_PERSONAL_CANONICAL_SCHEMA_VERSION,
    BOT_PERSONAL_MEMORY_DOMAIN,
    BOT_PERSONAL_MEMORY_TYPES,
    BOT_PERSONAL_PAYLOAD_SCHEMA_VERSION,
    CONTRACT_FINGERPRINT,
    CONTRACT_REVISION,
    WINDOW_SLUGS,
    window_for_minutes,
)
from .bot_personal_outbox import BotPersonalOutbox
from .helpers import _missing_optional_model_dependency, _now_ts, _path_text, _safe_float, _safe_int, _single_address, _single_line
from .companion_interaction_expression import current_interaction_projection
from .relationship_ledger import normalize_relationship_mode
from .relationship_policy import relationship_projection_for_bridge
from .namespace_capability import negotiate_namespace_capability
from .identity_namespace import validate_namespace_context
from .persona_config import runtime_persona_setting
from .conversation_prompt_section import (
    PromptSection,
    prompt_section,
)
from .logging_util import get_module_logger

logger = get_module_logger(__name__)


# The v2 contract was published by the previous Memory Companion release.
# Keep this tuple narrow: only this known, fully-compatible legacy descriptor
# may negotiate down; arbitrary mismatches remain degraded.
_LEGACY_V2_CONTRACT = {
    "contract_fingerprint": "0ffe3a1ab69b659c",
    "contract_revision": 2,
    "capability_schema_version": "1.2",
    "canonical_schema_version": 2,
    "payload_schema_version": "1.0",
}


def _memory_companion_safe_float(value: Any, default: float, minimum: float = 0.0) -> float:
    helper = globals().get("_safe_float")
    if callable(helper):
        return helper(value, default, minimum)
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, parsed)



class _memory_companion_adapterHostRef:
    """延迟引用宿主 memory_companion_adapter 模块，保证 patch 宿主全局名对全部域生效。"""

    def __getattr__(self, name: str):
        from . import memory_companion_adapter as _host_module

        return getattr(_host_module, name)


_memory_companion_adapter_host = _memory_companion_adapterHostRef()
