# -*- coding: utf-8 -*-
"""ContentCompanionBridgePart01Mixin。

由 tools/split_mixin_domain.py 从 content_companion_bridge.py 机械抽取（14 个方法 + 0 个模块级名字 + 0 个类级赋值 / 446 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ContentCompanionBridgeMixin）。
"""
from __future__ import annotations

from .content_companion_bridge_shared import (
    _CONTENT_API_FAMILY,
    _CONTENT_API_UNSET,
    _CONTENT_API_VERSION,
    _CONTENT_DESCRIPTOR_FIELDS,
    _CONTENT_EXTRACT_FIELDS,
    _CONTENT_HANDOFF_CAPABILITY,
    _CONTENT_OPERATION_MODEL_CALL_LIMITS,
    _CONTENT_PLUGIN_ID,
    _CONTENT_PROGRESS_PROJECT_FIELDS,
    _CONTENT_PROGRESS_PROJECT_LIMITS,
    _CONTENT_REQUIRED_CAPABILITIES,
    _CONTENT_SERVICES_VERSION,
    _CONTENT_SHARE_FIELDS,
    _CONTENT_STORY_OWNER_ID,
    _CONTENT_TASK_VERSION,
    _CONTENT_VERSION_FIELDS,
    _raise_story_write_fence,
)
from .content_companion_bridge_shared import Any
from .content_companion_bridge_shared import StoryAuthorityError
from .content_companion_bridge_shared import _ContentStoryModelBudget
from .content_companion_bridge_shared import _single_line
from .content_companion_bridge_shared import asyncio
from .content_companion_bridge_shared import call_enforced_story_target
from .content_companion_bridge_shared import deepcopy
from .content_companion_bridge_shared import invalidate_external_bridge_cache
from .content_companion_bridge_shared import logger
from .content_companion_bridge_shared import math
from .content_companion_bridge_shared import resolve_enforced_story_target
from .content_companion_bridge_shared import resolve_external_bridge
from .content_companion_bridge_shared import runtime_persona_setting
from .content_companion_bridge_shared import _content_companion_bridge_host



class ContentCompanionBridgePart01Mixin:
    """ContentCompanionBridgePart01Mixin（从 ContentCompanionBridgeMixin 拆出）。"""


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
            prefer_module_getter=True,
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
            False,
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

        state = _content_companion_bridge_host.story_authority_controller().authority_state()
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
                "Story handoff 未能证明唯一写者: code=%s",
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
                "独立创作合同不可用，拒绝降级 owner 注入: reason=%s",
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
                "独立创作实例在执行边界失效: operation=%s code=%s",
                operation,
                exc.code,
            )
            return True, None
        except Exception as exc:
            logger.warning(
                "独立创作任务拒绝: operation=%s error_type=%s",
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
                "独立创作实例执行后合同失效: operation=%s reason=%s",
                operation,
                post_reason,
            )
            return True, None
        if type(result) is not dict:
            logger.warning(
                "独立创作任务返回畸形: operation=%s",
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
            logger.warning("独立创作能力查询失败: %s", _single_line(exc, 160))
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
        if _content_companion_bridge_host.story_authority_controller().authority_state() not in {"created", "open"}:
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
            logger.warning("独立创作操作失败: operation=%s error=%s", operation, _single_line(exc, 160))
            return None
        finally:
            self._content_companion_delegating = False
