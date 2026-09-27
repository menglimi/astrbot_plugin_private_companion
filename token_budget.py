# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import re
import time
from datetime import datetime
from typing import Any


from .constants import (
    MODEL_PROVIDER_KEYS,
    MODEL_QUICK_TIMEOUT_KEYS,
    MODEL_TASK_PROVIDER_KEYS,
    MODEL_TASK_PROVIDER_PREFIXES,
)
from .helpers import _flat_get, _now_ts, _safe_float, _safe_int, _single_line, _today_key
from .model_routing import contains_sensitive_refusal, scope_allows
from .persona_config import runtime_persona_setting
from .logging_util import get_module_logger
from .task_prompt_registry import (
    TASK_PROMPT_CONFIG_KEY,
    apply_task_prompt_override,
    normalize_task_prompt_overrides,
)

from .token_budget_shared import (
    _looks_like_upstream_llm_error_response,
    logger,
)
from .token_budget_shared import logger
from .token_budget_usage_record import TokenBudgetUsageRecordMixin
from .token_budget_task_prompt_usage import TokenBudgetTaskPromptUsageMixin
from .token_budget_model_override_route import TokenBudgetModelOverrideRouteMixin
from .token_budget_llm_tool import TokenBudgetLlmToolMixin
from .token_budget_llm_call import TokenBudgetLlmCallMixin
from .token_budget_budget_provider import TokenBudgetBudgetProviderMixin
class TokenBudgetMixin(TokenBudgetBudgetProviderMixin, TokenBudgetLlmCallMixin, TokenBudgetLlmToolMixin, TokenBudgetModelOverrideRouteMixin, TokenBudgetTaskPromptUsageMixin, TokenBudgetUsageRecordMixin):
    """Methods split from main.PrivateCompanionPlugin."""

    # A card limit is an optional, per-request estimate used only to decide
    # whether the configured fallback should take the request.  It is not a
    # daily quota and it does not truncate a request when no fallback exists.
    MODEL_TOKEN_LIMIT_MIN = 256
    MODEL_TOKEN_LIMIT_MAX = 2_000_000
    MODEL_IMAGE_TOKEN_ESTIMATE = 256
