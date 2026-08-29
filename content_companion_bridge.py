# -*- coding: utf-8 -*-
"""Bridge for the optional creative/content companion plugin."""
from __future__ import annotations

import asyncio
import math
import random
import time
from copy import deepcopy
from typing import Any

from astrbot.api import logger

from .creative import _persona_provider_id
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
        return await caller(
            prompt,
            max_tokens=max_tokens,
            provider_id=provider_id,
            task=provider_role,
            timeout_key=provider_role,
            token_limit=_CONTENT_MODEL_REQUEST_TOKEN_LIMIT,
            strict_provider=True,
        )


class ContentCompanionBridgeMixin:
    def _content_companion_api(self) -> Any | None:
        return resolve_external_bridge(
            self,
            cache_key="content_companion",
            module_names=(
                "data.plugins.astrbot_plugin_content_companion.main",
                "astrbot_plugin_content_companion.main",
            ),
            getter_name="get_content_companion_api",
            star_name="astrbot_plugin_content_companion",
        )

    def _content_companion_api_fresh(self) -> Any | None:
        """Bypass the positive cache at every durable handoff boundary."""

        invalidate_external_bridge_cache(self, "content_companion")
        return self._content_companion_api()

    @staticmethod
    def _content_story_sequence(value: Any) -> tuple[str, ...] | None:
        if type(value) is not list or any(type(item) is not str for item in value):
            return None
        if len(value) != len(set(value)):
            return None
        return tuple(value)

    def _content_story_contract(
        self,
        *,
        api: Any = _CONTENT_API_UNSET,
        expected_generation: str = "",
        require_enforced: bool = False,
        _refreshed: bool = False,
    ) -> tuple[str, Any | None, str]:
        """Negotiate current Story API or an explicit descriptor-less legacy API."""

        pinned_api = api is not _CONTENT_API_UNSET
        candidate = api if pinned_api else self._content_companion_api()
        if candidate is None:
            return "missing", None, "content_companion_unavailable"
        missing = object()
        try:
            descriptor_getter = getattr(candidate, "capabilities", missing)
        except Exception:
            return "incompatible", candidate, "descriptor_method_unreadable"
        if not callable(descriptor_getter):
            declares_current = descriptor_getter is not missing
            for method in ("versions", "build_task", "validate_task", "execute_task"):
                try:
                    if getattr(candidate, method, missing) is not missing:
                        declares_current = True
                except Exception:
                    declares_current = True
            if declares_current:
                return "incompatible", candidate, "descriptor_method_missing"
            if not pinned_api and not _refreshed:
                invalidate_external_bridge_cache(self, "content_companion")
                replacement = self._content_companion_api()
                if replacement is not candidate:
                    return self._content_story_contract(_refreshed=True)
            return "legacy", candidate, "descriptor_unavailable"
        try:
            descriptor = descriptor_getter()
        except Exception:
            return "incompatible", candidate, "descriptor_query_failed"
        if type(descriptor) is not dict or set(descriptor) != _CONTENT_DESCRIPTOR_FIELDS:
            return "incompatible", candidate, "descriptor_malformed"
        versions = self._content_story_sequence(descriptor.get("supported_task_versions"))
        capabilities = self._content_story_sequence(descriptor.get("capabilities"))
        degraded = self._content_story_sequence(descriptor.get("degraded_reasons"))
        generation = descriptor.get("instance_generation")
        if (
            descriptor.get("plugin_id") != _CONTENT_PLUGIN_ID
            or descriptor.get("api_family") != _CONTENT_API_FAMILY
            or descriptor.get("api_version") != _CONTENT_API_VERSION
            or type(generation) is not str
            or len(generation) != 32
            or any(character not in "0123456789abcdef" for character in generation)
            or (expected_generation and generation != expected_generation)
            or versions is None
            or _CONTENT_TASK_VERSION not in versions
            or capabilities is None
            or not _CONTENT_REQUIRED_CAPABILITIES.issubset(capabilities)
            or (require_enforced and _CONTENT_HANDOFF_CAPABILITY not in capabilities)
            or degraded is None
        ):
            return "incompatible", candidate, "descriptor_incompatible"
        if (
            not pinned_api
            and not _refreshed
            and descriptor.get("lifecycle_state") in {"closed", "superseded"}
        ):
            invalidate_external_bridge_cache(self, "content_companion")
            replacement = self._content_companion_api()
            if replacement is not candidate:
                return self._content_story_contract(_refreshed=True)
        if descriptor.get("lifecycle_state") != "ready" or degraded:
            return "incompatible", candidate, "service_not_ready"

        versions_getter = getattr(candidate, "versions", None)
        builder = getattr(candidate, "build_task", None)
        validator = getattr(candidate, "validate_task", None)
        executor = getattr(candidate, "execute_task", None)
        if not all(
            callable(item)
            for item in (versions_getter, builder, validator, executor)
        ):
            return "incompatible", candidate, "required_method_missing"
        try:
            version_info = versions_getter()
        except Exception:
            return "incompatible", candidate, "version_query_failed"
        if type(version_info) is not dict or set(version_info) != _CONTENT_VERSION_FIELDS:
            return "incompatible", candidate, "version_descriptor_malformed"
        version_supported = self._content_story_sequence(
            version_info.get("supported_task_versions")
        )
        if (
            version_info.get("plugin_id") != descriptor["plugin_id"]
            or version_info.get("instance_generation") != generation
            or version_info.get("api_family") != _CONTENT_API_FAMILY
            or version_info.get("api_version") != _CONTENT_API_VERSION
            or version_info.get("task_version") != _CONTENT_TASK_VERSION
            or version_supported != versions
            or version_info.get("services_version") != _CONTENT_SERVICES_VERSION
        ):
            return "incompatible", candidate, "version_descriptor_incompatible"
        return "current", candidate, ""

    @staticmethod
    def _content_story_bounded_text(value: Any, limit: int) -> str | None:
        if (
            type(value) is not str
            or len(value) > limit
            or "\x00" in value
        ):
            return None
        return value

    def _content_story_progress_payload(
        self,
        *,
        event: Any,
        project: Any,
        chunk: Any,
        extract: Any,
    ) -> tuple[str, dict[str, Any], str, dict[str, Any]] | None:
        if type(event) is not str or event not in {"project-created", "project-advanced"}:
            return None
        if type(project) is not dict or set(project) != _CONTENT_PROGRESS_PROJECT_FIELDS:
            return None
        normalized_project: dict[str, Any] = {}
        for field, limit in _CONTENT_PROGRESS_PROJECT_LIMITS.items():
            value = self._content_story_bounded_text(project.get(field), limit)
            if value is None:
                return None
            normalized_project[field] = value
        if normalized_project["owner_id"] != _CONTENT_STORY_OWNER_ID:
            return None
        for field in ("current_chars", "target_chars"):
            value = project.get(field)
            if type(value) is not int or value < 0 or value > 2_000_000:
                return None
            normalized_project[field] = value
        normalized_chunk = self._content_story_bounded_text(chunk, 1200)
        if normalized_chunk is None:
            return None
        if type(extract) is not dict or set(extract) != _CONTENT_EXTRACT_FIELDS:
            return None
        next_direction = self._content_story_bounded_text(
            extract.get("next_direction"),
            160,
        )
        if next_direction is None:
            return None
        normalized_extract: dict[str, Any] = {
            "next_direction": next_direction,
        }
        for field in ("important_facts", "new_threads"):
            values = extract.get(field)
            if type(values) is not list or len(values) > 3:
                return None
            normalized_values: list[str] = []
            for item in values:
                normalized = self._content_story_bounded_text(item, 80)
                if normalized is None:
                    return None
                normalized_values.append(normalized)
            normalized_extract[field] = normalized_values
        return str(event), normalized_project, normalized_chunk, normalized_extract

    async def _content_story_record_progress(
        self,
        *,
        event: Any,
        project: Any,
        chunk: Any,
        extract: Any,
    ) -> None:
        payload = self._content_story_progress_payload(
            event=event,
            project=project,
            chunk=chunk,
            extract=extract,
        )
        if payload is None:
            return
        _event, normalized_project, normalized_chunk, normalized_extract = payload
        recorder = getattr(self, "_memory_companion_record_creative_progress", None)
        if callable(recorder):
            await recorder(
                project=normalized_project,
                chunk=normalized_chunk,
                extract=normalized_extract,
            )

    def _content_story_share_payload(self, candidate: Any) -> dict[str, Any] | None:
        if type(candidate) is not dict or set(candidate) != _CONTENT_SHARE_FIELDS:
            return None
        text_limits = {
            "key": 128,
            "milestone": 40,
            "disclosure_kind": 24,
            "project_id": 80,
            "work_type": 40,
            "title": 80,
            "premise": 180,
            "tone": 80,
            "source": 180,
            "snippet": 260,
            "status": 24,
        }
        normalized: dict[str, Any] = {}
        for field, limit in text_limits.items():
            value = self._content_story_bounded_text(candidate.get(field), limit)
            if value is None:
                return None
            normalized[field] = value
        if (
            not normalized["project_id"]
            or not normalized["snippet"]
            or normalized["milestone"]
            not in {"opening", "midpoint", "finished", "impression_question"}
            or normalized["disclosure_kind"] not in {"milestone", "ask_impression"}
            or (
                normalized["disclosure_kind"] == "ask_impression"
                and normalized["milestone"] != "impression_question"
            )
            or (
                normalized["milestone"] == "impression_question"
                and normalized["disclosure_kind"] != "ask_impression"
            )
            or normalized["status"] not in {"drafting", "finished", "paused"}
            or normalized["key"]
            != f"{normalized['project_id']}:{normalized['milestone']}"
        ):
            return None
        for field, maximum in (
            ("current_chars", 2_000_000),
            ("target_chars", 2_000_000),
            ("chunk_count", 40),
        ):
            value = candidate.get(field)
            if type(value) is not int or value < 0 or value > maximum:
                return None
            normalized[field] = value
        for field, minimum, maximum in (
            ("maturity_score", 0.0, 100.0),
            ("completion_ratio", 0.0, 1.0),
            ("created_ts", 0.0, 32_503_680_000.0),
        ):
            value = candidate.get(field)
            if type(value) not in (int, float):
                return None
            numeric = float(value)
            if not math.isfinite(numeric) or numeric < minimum or numeric > maximum:
                return None
            normalized[field] = numeric
        return normalized

    async def _content_story_offer_share(self, *, candidate: Any) -> bool:
        normalized = self._content_story_share_payload(candidate)
        if normalized is None or not runtime_persona_setting(
            self,
            "enable_creative_writing",
            True,
        ):
            return False
        scheduler = getattr(self, "_schedule_creative_share_candidate", None)
        lock = getattr(self, "_data_lock", None)
        saver = getattr(self, "_save_data_sync", None)
        if not callable(scheduler) or not isinstance(lock, asyncio.Lock) or not callable(saver):
            return False
        async with lock:
            data = getattr(self, "data", None)
            users = data.get("users") if isinstance(data, dict) else None
            if not isinstance(users, dict):
                return False
            key = normalized["key"]
            if any(
                isinstance(user, dict)
                and user.get("last_creative_share_key") == key
                for user in users.values()
            ):
                return True
            try:
                users_before = deepcopy(users)
            except Exception:
                return False
            changed = bool(scheduler(normalized, mark_disclosed=False))
            if not changed:
                data["users"] = users_before
                return False
            try:
                saver(sections={"users"})
            except Exception:
                data["users"] = users_before
                return False
            return True

    async def _content_story_execute(
        self,
        operation: str,
        **fields: Any,
    ) -> tuple[bool, dict[str, Any] | None]:
        """Run only the exact Content generation authorized after S3 commit."""

        state = story_authority_controller().authority_state()
        if state in {"created", "open"}:
            # Before the durable marker, Companion remains the only writer and
            # every declared current Content contract is strictly standby.
            return False, None
        if state != "committed":
            _raise_story_write_fence(state)
        call_limit = _CONTENT_OPERATION_MODEL_CALL_LIMITS.get(operation)
        if call_limit is None:
            return True, None
        try:
            target = await resolve_enforced_story_target(self)
        except asyncio.CancelledError:
            raise
        except StoryAuthorityError as exc:
            logger.warning(
                "[PrivateCompanion] Story handoff 未能证明唯一写者: code=%s",
                exc.code,
            )
            return True, None
        mode, api, reason = self._content_story_contract(
            api=target.api,
            expected_generation=target.generation,
            require_enforced=True,
        )
        if mode != "current" or api is None:
            logger.warning(
                "[PrivateCompanion] 独立创作合同不可用，拒绝降级 owner 注入: reason=%s",
                reason,
            )
            return True, None
        raw_task: dict[str, Any] = {
            "version": _CONTENT_TASK_VERSION,
            "operation": operation,
            "owner_id": _CONTENT_STORY_OWNER_ID,
            "max_model_calls": call_limit,
        }
        raw_task.update(fields)
        budget = _ContentStoryModelBudget(self, call_limit=call_limit)
        services = {
            "version": _CONTENT_SERVICES_VERSION,
            "call_model": budget,
            "record_progress": self._content_story_record_progress,
            "offer_share": self._content_story_offer_share,
        }
        try:
            task = api.build_task(raw_task)
            if type(task) is not dict:
                raise TypeError("story_task_not_mapping")
            result, after = await call_enforced_story_target(
                self,
                target,
                "execute_task",
                task,
                services,
            )
        except asyncio.CancelledError:
            raise
        except StoryAuthorityError as exc:
            logger.warning(
                "[PrivateCompanion] 独立创作实例在执行边界失效: operation=%s code=%s",
                operation,
                exc.code,
            )
            return True, None
        except Exception as exc:
            logger.warning(
                "[PrivateCompanion] 独立创作任务拒绝: operation=%s error_type=%s",
                operation,
                type(exc).__name__,
            )
            return True, None
        post_mode, post_api, post_reason = self._content_story_contract(
            api=after.api,
            expected_generation=after.generation,
            require_enforced=True,
        )
        if post_mode != "current" or post_api is not api:
            logger.warning(
                "[PrivateCompanion] 独立创作实例执行后合同失效: operation=%s reason=%s",
                operation,
                post_reason,
            )
            return True, None
        if type(result) is not dict:
            logger.warning(
                "[PrivateCompanion] 独立创作任务返回畸形: operation=%s",
                operation,
            )
            return True, None
        return True, dict(result)

    def _content_companion_status(self) -> dict[str, Any]:
        api = self._content_companion_api()
        getter = getattr(api, "status", None) if api is not None else None
        if not callable(getter):
            return {"installed": False, "enabled": False, "available": False, "reason": "content_companion_unavailable"}
        try:
            value = getter()
        except Exception as exc:
            logger.warning("[PrivateCompanion] 独立创作能力查询失败: %s", _single_line(exc, 160))
            return {"installed": True, "enabled": False, "available": False, "reason": "status_query_failed"}
        return dict(value) if isinstance(value, dict) else {"installed": True, "enabled": False, "available": False}

    def _content_companion_available(self) -> bool:
        mode, _api, _reason = self._content_story_contract()
        if mode != "legacy":
            return False
        return bool(self._content_companion_status().get("available"))

    def _content_companion_qzone_available(self) -> bool:
        status = self._content_companion_status()
        return bool(isinstance(status.get("qzone"), dict) and status["qzone"].get("enabled"))

    async def _content_companion_call(self, operation: str, *args: Any, **kwargs: Any) -> Any:
        if story_authority_controller().authority_state() not in {"created", "open"}:
            return None
        mode, api, _reason = self._content_story_contract()
        if mode != "legacy":
            return None
        handler = getattr(api, operation, None) if api is not None else None
        if not callable(handler):
            return None
        try:
            self._content_companion_delegating = True
            return await handler(self, *args, **kwargs)
        except Exception as exc:
            logger.warning("[PrivateCompanion] 独立创作操作失败: operation=%s error=%s", operation, _single_line(exc, 160))
            return None
        finally:
            self._content_companion_delegating = False

    async def _content_story_maybe_start_current(
        self,
        *,
        idle_checked: bool,
    ) -> tuple[bool, bool]:
        state = story_authority_controller().authority_state()
        if state in {"created", "open"}:
            return False, False
        if state != "committed":
            _raise_story_write_fence(state)
        if not runtime_persona_setting(self, "enable_creative_writing", True):
            return True, False
        idle_checker = getattr(self, "_bot_currently_idle_for_creative_writing", None)
        if not idle_checked and callable(idle_checker) and not idle_checker():
            return True, False

        lock = getattr(self, "_content_story_start_lock", None)
        if not isinstance(lock, asyncio.Lock):
            lock = asyncio.Lock()
            self._content_story_start_lock = lock
        async with lock:
            claimed, listing = await self._content_story_execute("list")
            if not claimed or listing is None:
                return True, False
            projects = listing.get("projects")
            if type(projects) is not list:
                return True, False
            active = [
                item
                for item in projects
                if isinstance(item, dict) and item.get("status") == "drafting"
            ]
            try:
                maximum_active = int(
                    runtime_persona_setting(self, "creative_max_active_projects", 2)
                )
            except (TypeError, ValueError):
                maximum_active = 2
            if len(active) >= max(1, min(maximum_active, 20)):
                return True, False
            last_created = max(
                (
                    _safe_float(item.get("created_at"), 0)
                    for item in projects
                    if isinstance(item, dict)
                ),
                default=0,
            )
            if time.time() - last_created < 10 * 3600:
                return True, False
            try:
                probability = float(
                    runtime_persona_setting(
                        self,
                        "creative_inspiration_probability",
                        0.2,
                    )
                )
            except (TypeError, ValueError):
                probability = 0.2
            if random.random() > max(0.0, min(probability, 1.0)):
                return True, False
            source_getter = getattr(self, "_creative_inspiration_source", None)
            source = source_getter() if callable(source_getter) else None
            if not isinstance(source, dict):
                return True, False
            prompt = _single_line(source.get("text"), 220)
            if not prompt:
                return True, False
            style = _single_line(
                runtime_persona_setting(self, "default_style", ""),
                80,
            )
            claimed, result = await self._content_story_execute(
                "start",
                author_prompt=prompt,
                style=style,
            )
            return claimed, bool(
                isinstance(result, dict) and isinstance(result.get("project"), dict)
            )

    async def _maybe_advance_creative_projects(self) -> None:
        if not getattr(self, "_content_companion_delegating", False):
            state = story_authority_controller().authority_state()
            if state not in {"created", "open"}:
                if state == "committed":
                    if not runtime_persona_setting(
                        self,
                        "enable_creative_writing",
                        True,
                    ):
                        return
                    pending_checker = getattr(
                        self,
                        "_creative_has_pending_proactive_plan",
                        None,
                    )
                    if callable(pending_checker) and pending_checker():
                        return
                    idle_checker = getattr(
                        self,
                        "_bot_currently_idle_for_creative_writing",
                        None,
                    )
                    if callable(idle_checker) and not idle_checker():
                        return
                    await self._content_story_maybe_start_current(idle_checked=True)
                    try:
                        base_budget = int(
                            runtime_persona_setting(
                                self,
                                "creative_chars_per_session",
                                220,
                            )
                        )
                    except (TypeError, ValueError):
                        base_budget = 220
                    output_limit = max(
                        60,
                        min(1200, int(base_budget * random.uniform(0.72, 1.18))),
                    )
                    await self._content_story_execute(
                        "advance",
                        output_char_limit=output_limit,
                    )
                    return
                _raise_story_write_fence(state)
        if not getattr(self, "_content_companion_delegating", False) and self._content_companion_available():
            result = await self._content_companion_call("advance_creative_projects")
            if result is not None:
                return
        return await super()._maybe_advance_creative_projects()

    async def _maybe_start_creative_project(self, *, idle_checked: bool = False) -> bool:
        if not getattr(self, "_content_companion_delegating", False):
            claimed, started = await self._content_story_maybe_start_current(
                idle_checked=idle_checked,
            )
            if claimed:
                return started
        if not getattr(self, "_content_companion_delegating", False) and self._content_companion_available():
            result = await self._content_companion_call("maybe_start_creative_project", idle_checked=idle_checked)
            if result is not None:
                return bool(result)
        return bool(await super()._maybe_start_creative_project(idle_checked=idle_checked))

    async def _generate_creative_project(self, source: dict[str, str]) -> Any:
        if not getattr(self, "_content_companion_delegating", False):
            claimed, result = await self._content_story_execute(
                "generate_project",
                author_prompt=_single_line(source.get("text"), 220),
                style=_single_line(
                    runtime_persona_setting(self, "default_style", ""),
                    80,
                ),
            )
            if claimed:
                project_result = result.get("project") if isinstance(result, dict) else None
                return dict(project_result) if isinstance(project_result, dict) else None
        if not getattr(self, "_content_companion_delegating", False) and self._content_companion_available():
            result = await self._content_companion_call("generate_creative_project", source)
            if result is not None:
                return result
        return await super()._generate_creative_project(source)

    async def _generate_creative_chunk(self, project: dict[str, Any], budget: int) -> str:
        if not getattr(self, "_content_companion_delegating", False):
            try:
                output_limit = max(60, min(1200, int(budget)))
            except (TypeError, ValueError):
                output_limit = 60
            claimed, result = await self._content_story_execute(
                "generate_chunk",
                work_id=_single_line(project.get("id"), 80),
                output_char_limit=output_limit,
            )
            if claimed:
                chunk_result = result.get("chunk") if isinstance(result, dict) else None
                return chunk_result if type(chunk_result) is str else ""
        if not getattr(self, "_content_companion_delegating", False) and self._content_companion_available():
            result = await self._content_companion_call("generate_creative_chunk", project, budget)
            if result is not None:
                return str(result)
        return await super()._generate_creative_chunk(project, budget)

    async def _review_creative_chunk(self, *args: Any, **kwargs: Any) -> Any:
        if not getattr(self, "_content_companion_delegating", False):
            project = args[0] if args and isinstance(args[0], dict) else {}
            outline = str(args[2] if len(args) > 2 else kwargs.get("outline", ""))[:300]
            excerpt = str(args[3] if len(args) > 3 else kwargs.get("chunk_text", ""))[:600]
            claimed, result = await self._content_story_execute(
                "review_chunk",
                work_id=_single_line(project.get("id"), 80),
                outline=outline,
                recent_excerpt=excerpt,
                context_char_limit=900,
            )
            if claimed:
                review_result = result.get("review") if isinstance(result, dict) else None
                return dict(review_result) if isinstance(review_result, dict) else {}
        if not getattr(self, "_content_companion_delegating", False) and self._content_companion_available():
            result = await self._content_companion_call("review_creative_chunk", *args, **kwargs)
            if result is not None:
                return result
        return await super()._review_creative_chunk(*args, **kwargs)

    async def _apply_creative_manual_edit(self, *args: Any, **kwargs: Any) -> Any:
        if not getattr(self, "_content_companion_delegating", False):
            project_id = args[0] if args else kwargs.get("project_id", "")
            edit_type = args[1] if len(args) > 1 else kwargs.get("edit_type", "")
            edit_content = args[2] if len(args) > 2 else kwargs.get("edit_content", "")
            edit_title = args[3] if len(args) > 3 else kwargs.get("edit_title", "")
            part_index = args[4] if len(args) > 4 else kwargs.get("part_index", -1)
            if part_index is None:
                part_index = -1
            edit_text = str(edit_content or "")
            if len(edit_text) > 900:
                return {
                    "success": False,
                    "error": "story_edit_content_too_large",
                }
            claimed, result = await self._content_story_execute(
                "manual_edit",
                work_id=_single_line(project_id, 80),
                edit_type=_single_line(edit_type, 24),
                edit_title=_single_line(edit_title, 60),
                recent_excerpt=edit_text,
                context_char_limit=900,
                part_index=part_index,
            )
            if claimed:
                edit_result = result.get("result") if isinstance(result, dict) else None
                return dict(edit_result) if isinstance(edit_result, dict) else {}
        if not getattr(self, "_content_companion_delegating", False) and self._content_companion_available():
            result = await self._content_companion_call("apply_creative_manual_edit", *args, **kwargs)
            if result is not None:
                return result
        return await super()._apply_creative_manual_edit(*args, **kwargs)

    async def _rebuild_creative_memory_from_project(self, project_id: str) -> Any:
        if not getattr(self, "_content_companion_delegating", False):
            claimed, result = await self._content_story_execute(
                "rebuild_memory",
                work_id=_single_line(project_id, 80),
            )
            if claimed:
                rebuild_result = result.get("result") if isinstance(result, dict) else None
                return dict(rebuild_result) if isinstance(rebuild_result, dict) else {}
        if not getattr(self, "_content_companion_delegating", False) and self._content_companion_available():
            result = await self._content_companion_call("rebuild_creative_memory", project_id)
            if result is not None:
                return result
        return await super()._rebuild_creative_memory_from_project(project_id)

    async def _maybe_generate_creative_cover(self, project_id: str, *, force: bool = False) -> Any:
        state = story_authority_controller().authority_state()
        if state == "committed":
            # No managed current-contract cover operation exists yet.  After
            # handoff, absence is safer than re-entering the former owner.
            return None
        if state not in {"created", "open"}:
            _raise_story_write_fence(state)
        if self._content_companion_available():
            result = await self._content_companion_call("maybe_generate_creative_cover", project_id, force=force)
            if result is not None:
                return result
        return await super()._maybe_generate_creative_cover(project_id, force=force)
