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

from .memory_companion_adapter_shared import (
    _LEGACY_V2_CONTRACT,
    _memory_companion_safe_float,
    logger,
)
from .memory_companion_adapter_shared import logger
from .memory_companion_adapter_record_observations import MemoryCompanionAdapterRecordObservationsMixin
from .memory_companion_adapter_record_agenda_attach import MemoryCompanionAdapterRecordAgendaAttachMixin
from .memory_companion_adapter_probe_namespace_scoped import MemoryCompanionAdapterProbeNamespaceScopedMixin
from .memory_companion_adapter_match_presence_outbox_archive import MemoryCompanionAdapterMatchPresenceOutboxArchiveMixin
from .memory_companion_adapter_emotion_relationship import MemoryCompanionAdapterEmotionRelationshipMixin
from .memory_companion_adapter_coordination_context import MemoryCompanionAdapterCoordinationContextMixin
from .memory_companion_adapter_compose_context import MemoryCompanionAdapterComposeContextMixin
from .memory_companion_adapter_bridge_lifecycle import MemoryCompanionAdapterBridgeLifecycleMixin
class MemoryCompanionAdapterMixin(MemoryCompanionAdapterBridgeLifecycleMixin, MemoryCompanionAdapterComposeContextMixin, MemoryCompanionAdapterCoordinationContextMixin, MemoryCompanionAdapterEmotionRelationshipMixin, MemoryCompanionAdapterMatchPresenceOutboxArchiveMixin, MemoryCompanionAdapterProbeNamespaceScopedMixin, MemoryCompanionAdapterRecordAgendaAttachMixin, MemoryCompanionAdapterRecordObservationsMixin):
    """Optional bridge helpers for astrbot_plugin_memory_companion."""

    _bridge_cache: Any | None = None
    _bridge_cache_ts: float = 0.0
    _BRIDGE_CACHE_TTL: float = 30.0
    _BRIDGE_MISSING_CACHE_TTL: float = 2.0
    _bridge_dependency_failure_until: float = 0.0
    _bridge_dependency_failure_module: str = ""
    _bridge_last_status: dict[str, Any] = {}

    _MEMORY_COMPANION_PLUGIN_ALIASES = frozenset(
        {
            "astrbot_plugin_memory_companion",
            "astrbot_plugin_remember_you",
            "memorycompanion",
            "memory_companion",
            "rememberyou",
            "remember_you",
            "我会牢牢记住你",
        }
    )
