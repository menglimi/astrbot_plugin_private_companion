# -*- coding: utf-8 -*-
"""Bridge for the optional creative/content companion plugin."""
from __future__ import annotations

import asyncio
import math
import random
import time
from copy import deepcopy
from typing import Any

from .creative import _persona_provider_id
from .conversation_prompt_section import (
    PromptRenderMode,
    exact_text,
    prompt_section,
    render_prompt_sections,
)
from .helpers import _safe_float, _single_line
from .external_bridge_resolver import (
    invalidate_external_bridge_cache,
    resolve_external_bridge,
)
from .persona_config import runtime_persona_setting
from .story_authority import StoryAuthorityError, story_authority_controller
from .story_handoff import (
    call_enforced_story_target,
    resolve_enforced_story_target,
)
from .logging_util import get_module_logger

logger = get_module_logger(__name__)

_CONTENT_PLUGIN_ID = "astrbot_plugin_content_companion"
_CONTENT_STORY_OWNER_ID = "astrbot_plugin_private_companion"
_CONTENT_API_FAMILY = "content.story"
_CONTENT_API_VERSION = "content.story-api.v1"
_CONTENT_TASK_VERSION = "content.story-task.v1"
_CONTENT_SERVICES_VERSION = "content.story-services.v1"
_CONTENT_DESCRIPTOR_FIELDS = frozenset(
    {
        "plugin_id",
        "instance_generation",
        "api_family",
        "api_version",
        "supported_task_versions",
        "capabilities",
        "lifecycle_state",
        "degraded_reasons",
    }
)
_CONTENT_VERSION_FIELDS = frozenset(
    {
        "plugin_id",
        "instance_generation",
        "api_family",
        "api_version",
        "task_version",
        "supported_task_versions",
        "services_version",
    }
)
_CONTENT_REQUIRED_CAPABILITIES = frozenset(
    {
        "story.build-task",
        "story.callback.offer-share",
        "story.callback.record-progress",
        "story.execute-task",
        "story.owner-scoped-projects",
        "story.operation.advance",
        "story.operation.generate-chunk",
        "story.operation.generate-project",
        "story.operation.list",
        "story.operation.manual-edit",
        "story.operation.rebuild-memory",
        "story.operation.review-chunk",
        "story.operation.start",
        "story.validate-task",
    }
)
_CONTENT_HANDOFF_CAPABILITY = "story.handoff.enforced"
_CONTENT_API_UNSET = object()
_CONTENT_MODEL_ROLE_OUTPUT_LIMITS = {
    "creative_project": 500,
    "creative_outline": 200,
    "creative_review": 220,
    "creative_extract": 300,
    "creative_writing": 1360,
}
_CONTENT_OPERATION_MODEL_CALL_LIMITS = {
    "advance": 8,
    "advance_now": 8,
    "start": 1,
    "generate_project": 1,
    "generate_chunk": 7,
    "review_chunk": 1,
    "manual_edit": 0,
    "rebuild_memory": 0,
    "list": 0,
    "get": 0,
}
_CONTENT_MODEL_PROMPT_CHAR_LIMIT = 16_000
_CONTENT_MODEL_REQUEST_TOKEN_LIMIT = 8_000
_CONTENT_MODEL_EXECUTION_TOKEN_LIMIT = 32_000
_CONTENT_PROGRESS_PROJECT_LIMITS = {
    "id": 80,
    "owner_id": 120,
    "title": 80,
    "work_type": 40,
    "premise": 500,
    "tone": 80,
    "status": 24,
    "next_hint": 240,
}
_CONTENT_PROGRESS_PROJECT_FIELDS = frozenset(
    {*_CONTENT_PROGRESS_PROJECT_LIMITS, "current_chars", "target_chars"}
)
_CONTENT_EXTRACT_FIELDS = frozenset(
    {"next_direction", "important_facts", "new_threads"}
)
_CONTENT_SHARE_FIELDS = frozenset(
    {
        "key",
        "milestone",
        "disclosure_kind",
        "project_id",
        "work_type",
        "title",
        "premise",
        "tone",
        "source",
        "snippet",
        "current_chars",
        "target_chars",
        "chunk_count",
        "maturity_score",
        "completion_ratio",
        "status",
        "created_ts",
    }
)


def _raise_story_write_fence(state: str) -> None:
    code = {
        "draining": "story_legacy_write_draining",
        "leased": "story_legacy_write_leased",
        "committing": "story_legacy_write_committing",
        "blocked": "story_legacy_write_blocked",
    }.get(state, "story_legacy_write_blocked")
    raise StoryAuthorityError(code)


class _ContentStoryModelBudget:
    """One execution's strict Companion-owned model callback budget."""

    __slots__ = ("_owner", "_call_limit", "_used_calls", "_used_tokens")

    def __init__(self, owner: Any, *, call_limit: int) -> None:
        self._owner = owner
        self._call_limit = max(0, min(int(call_limit), 8))
        self._used_calls = 0
        self._used_tokens = 0

    def _provider(self, role: str) -> str:
        owner = self._owner
        if role == "creative_outline":
            values = (
                _persona_provider_id(
                    owner,
                    "CREATIVE_OUTLINE_PROVIDER_ID",
                    "creative_outline_provider_id",
                    "creative",
                ),
                _persona_provider_id(
                    owner,
                    "CREATIVE_PROVIDER_ID",
                    "creative_provider_id",
                    "creative",
                ),
                _persona_provider_id(
                    owner,
                    "MAI_STYLE_PROVIDER_ID",
                    "mai_style_provider_id",
                    "fast",
                ),
            )
        elif role in {"creative_review", "creative_extract"}:
            values = (
                _persona_provider_id(
                    owner,
                    "CREATIVE_REVIEW_PROVIDER_ID",
                    "creative_review_provider_id",
                    "creative",
                ),
                _persona_provider_id(
                    owner,
                    "CREATIVE_PROVIDER_ID",
                    "creative_provider_id",
                    "creative",
                ),
                _persona_provider_id(
                    owner,
                    "MAI_STYLE_PROVIDER_ID",
                    "mai_style_provider_id",
                    "fast",
                ),
            )
        else:
            values = (
                _persona_provider_id(
                    owner,
                    "CREATIVE_PROVIDER_ID",
                    "creative_provider_id",
                    "creative",
                ),
                _persona_provider_id(
                    owner,
                    "MAI_STYLE_PROVIDER_ID",
                    "mai_style_provider_id",
                    "fast",
                ),
            )
        selector = getattr(owner, "_task_provider", None)
        if not callable(selector):
            return next((value for value in values if value), "")
        try:
            return str(selector(*values, allow_replacement=False) or "").strip()
        except Exception:
            return ""

    async def __call__(
        self,
        *,
        provider_role: Any,
        prompt: Any,
        max_tokens: Any,
    ) -> str | None:
        if type(provider_role) is not str or provider_role not in _CONTENT_MODEL_ROLE_OUTPUT_LIMITS:
            return None
        if type(prompt) is not str or not prompt.strip() or len(prompt) > _CONTENT_MODEL_PROMPT_CHAR_LIMIT:
            return None
        if "\x00" in prompt or type(max_tokens) is not int:
            return None
        output_limit = _CONTENT_MODEL_ROLE_OUTPUT_LIMITS[provider_role]
        if max_tokens <= 0 or max_tokens > output_limit:
            return None
        estimator = getattr(self._owner, "_estimate_model_request_tokens", None)
        if not callable(estimator):
            return None
        try:
            estimated = int(estimator(prompt, max_tokens=max_tokens))
        except Exception:
            return None
        if (
            estimated <= 0
            or estimated > _CONTENT_MODEL_REQUEST_TOKEN_LIMIT
            or self._used_calls >= self._call_limit
            or self._used_tokens + estimated > _CONTENT_MODEL_EXECUTION_TOKEN_LIMIT
        ):
            return None

        # Reserve before provider selection/call. Missing providers and failed
        # requests deliberately consume this execution's bounded quota.
        self._used_calls += 1
        self._used_tokens += estimated
        provider_id = self._provider(provider_role)
        caller = getattr(self._owner, "_llm_call", None)
        if not provider_id or not callable(caller):
            return None
        external_prompt = prompt_section(
            key=f"external.content_companion.{provider_role}",
            title="Content Companion 外部任务",
            source="content_companion",
            content=exact_text(prompt),
        )
        return await caller(
            render_prompt_sections(
                [external_prompt],
                mode=PromptRenderMode.EXACT,
            ),
            max_tokens=max_tokens,
            provider_id=provider_id,
            task=provider_role,
            timeout_key=provider_role,
            token_limit=_CONTENT_MODEL_REQUEST_TOKEN_LIMIT,
            strict_provider=True,
        )



class _content_companion_bridgeHostRef:
    """延迟引用宿主 content_companion_bridge 模块，保证 patch 宿主全局名对全部域生效。"""

    def __getattr__(self, name: str):
        from . import content_companion_bridge as _host_module

        return getattr(_host_module, name)


_content_companion_bridge_host = _content_companion_bridgeHostRef()
