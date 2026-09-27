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

logger = get_module_logger(__name__)


def _looks_like_upstream_llm_error_response(text: Any) -> bool:
    """Match high-confidence error envelopes returned as successful LLM text."""
    cleaned = _single_line(text, 2000)
    if not cleaned:
        return False
    lowered = cleaned.lower()
    normalized = re.sub(r"[^a-z0-9\u4e00-\u9fff_]+", " ", lowered).strip()
    compact = re.sub(r"[^a-z0-9\u4e00-\u9fff_]+", "", lowered)

    direct_markers = (
        "allchatmodelsfailed",
        "allllmprovidersfailed",
        "allavailablechatmodelsareunavailable",
        "promptcouldnotbesubmitted",
        "promptwasnotsubmitted",
        "promptcontainssensitivewords",
        "unabletosubmitrequest",
        "providerapierrorupstreamreturnedaninternalfailuremessage",
    )
    if any(marker in compact for marker in direct_markers):
        return True

    google_policy_features = (
        "generativeaiprohibitedusepolicy",
        "tryrephrasingtheprompt",
        "sensitivewords",
    )
    if sum(feature in compact for feature in google_policy_features) >= 2:
        return True

    if "functiondeclaration" in compact and any(
        marker in compact
        for marker in (
            "schemadidntspecify",
            "invalidrequest",
            "badrequest",
            "errorcode400",
        )
    ):
        return True

    if lowered.lstrip().startswith("traceback (most recent call last):"):
        return True

    if re.match(
        r"^(?:模型|api|provider|函数工具|工具)\s*调用失败\s*[:：]",
        cleaned,
        flags=re.IGNORECASE,
    ):
        return True

    if re.match(
        r"^(?:api connection error|api status error|authentication error|"
        r"permission denied error|rate limit error|internal server error)\s*(?:[:：-]|$)",
        lowered,
    ):
        return True

    error_classes = (
        "badrequesterror",
        "apiconnectionerror",
        "apistatuserror",
        "authenticationerror",
        "permissiondeniederror",
        "ratelimiterror",
        "notfounderror",
        "internalservererror",
    )
    error_class = next((name for name in error_classes if name in compact), "")
    if error_class:
        structured_signal = any(
            marker in compact
            for marker in (
                "errorcode",
                "statuscode",
                "httpstatus",
                "requestid",
                "invalidrequest",
            )
        )
        leading_error_class = bool(
            re.match(
                rf"^(?:(?:llm|provider|api)\s+(?:response\s+)?error\s+|error\s+)?{error_class}\b",
                normalized,
            )
        )
        if structured_signal or (
            leading_error_class
            and (":" in cleaned[:100] or "：" in cleaned[:100] or len(cleaned) <= 48)
        ):
            return True

    if lowered.lstrip().startswith(("{", "[")) and any(
        marker in lowered for marker in ('"error"', "'error'")
    ) and any(
        marker in compact
        for marker in ("invalid_request", "badrequest", "permissiondenied", "ratelimit")
    ):
        return True

    return False



class _token_budgetHostRef:
    """延迟引用宿主 token_budget 模块，保证 patch 宿主全局名对全部域生效。"""

    def __getattr__(self, name: str):
        from . import token_budget as _host_module

        return getattr(_host_module, name)


_token_budget_host = _token_budgetHostRef()
