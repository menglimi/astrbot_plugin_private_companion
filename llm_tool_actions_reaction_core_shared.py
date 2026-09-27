# -*- coding: utf-8 -*-
"""表情表达核心域。

由 tools/split_mixin_domain.py 从 llm_tool_actions.py 机械抽取（26 个方法 + 7 个模块级名字 + 0 个类级赋值 / 1351 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 LlmToolActionsMixin）。
"""
from __future__ import annotations

import hashlib
import html
import json
import random
import re
import uuid
from .helpers import _now_ts, _safe_float, _safe_int, _single_line, _strip_internal_message_blocks
from .llm_tool_actions_shared import logger
from .owned_reaction_asset_catalog import OwnedReactionAssetCatalog
from .persona_config import runtime_persona_setting
from .reaction_expression import (
    ensure_reaction_expression_state,
    evaluate_reaction_expression_gate,
    normalize_reaction_expression_intent,
    reaction_expression_auto_disabled,
    reaction_expression_effective_probability,
    reaction_expression_explicit_opt_out,
    reaction_expression_explicit_request,
    reaction_expression_high_frequency,
    reaction_expression_normalize_probability,
    reaction_expression_scope_state,
)
from typing import Any



_REACTION_LOG_STAGES = frozenset(
    {
        "gate",
        "authorization",
        "decision",
        "lookup",
        "reservation",
        "attachment",
        "delivery",
        "intent",
        "feedback",
        "degrade",
    }
)

_REACTION_LOG_DECISIONS = frozenset(
    {
        "allow",
        "deny",
        "skip",
        "hit",
        "miss",
        "prepared",
        "sent",
        "failed",
        "uncertain",
        "accepted",
        "discarded",
        "recorded",
        "scoped",
        "omit",
    }
)

_REACTION_LOG_REASONS = frozenset(
    {
        "allowed",
        "vision_rejected",
        "experiment_disabled",
        "provider_unavailable",
        "private_disabled",
        "group_disabled",
        "unknown_disabled",
        "missing_user",
        "probability",
        "cooldown",
        "in_progress",
        "repeated_intent",
        "not_preauthorized",
        "not_authorized",
        "authorization_consumed",
        "authorization_expired",
        "authorization_user_mismatch",
        "authorization_scope_mismatch",
        "send_disabled",
        "missing_visible_text",
        "missing_visible_caption",
        "existing_image",
        "proactive_only",
        "gate",
        "not_found",
        "unavailable",
        "error",
        "lookup_error",
        "missing_query",
        "library_unavailable",
        "missing_file",
        "matched",
        "reservation_lost",
        "duplicate_image",
        "attachment_state_failed",
        "attachment_appended",
        "attachment_prepared",
        "delivered_before_primary",
        "attachment_file_missing",
        "attachment_component_failed",
        "attachment_removed",
        "platform_not_sent",
        "primary_not_delivered",
        "delivery_not_started",
        "awaiting_platform_send",
        "delivered",
        "delivery_uncertain",
        "delivery_failed",
        "append_failed",
        "not_sent",
        "scene_snapshot_failed",
        "usage_mark_failed",
        "intent_extracted",
        "intent_discarded",
        "model_omitted_intent",
        "local_fallback_intent",
        "media_tools_scoped",
        "feedback_private",
        "feedback_group",
        "explicit_opt_out",
        "semantic_cooldown",
    }
)

_REACTION_LOG_STATUSES = frozenset(
    {
        "success",
        "prepared",
        "need_query",
        "unavailable",
        "not_found",
        "error",
        "missing_file",
        "delivery_failed",
        "delivery_uncertain",
        "disabled",
        "missing_user",
        "not_sent",
    }
)

_REACTION_LOG_DELIVERY_CODES = frozenset(
    {
        "current",
        "group",
        "private",
        "blocked",
        "error",
        "platform_sent",
        "delivered",
        "append_failed",
        "attachment_removed",
        "platform_not_sent",
        "attachment_file_missing",
        "attachment_component_failed",
    }
)

_REACTION_LOG_MATCH_BASES = frozenset(
    {"tags_emotions_intents", "provider_score"}
)

_REACTION_LOG_TRIGGER_MODES = frozenset(
    {
        "probability",
        "feedback_bias",
        "semantic_rule",
        "strong_emotion",
        "explicit_opt_out",
    }
)



class _llm_tool_actions_reaction_coreHostRef:
    """延迟引用宿主 llm_tool_actions_reaction_core 模块，保证 patch 宿主全局名对全部域生效。"""

    def __getattr__(self, name: str):
        from . import llm_tool_actions_reaction_core as _host_module

        return getattr(_host_module, name)


_llm_tool_actions_reaction_core_host = _llm_tool_actions_reaction_coreHostRef()
