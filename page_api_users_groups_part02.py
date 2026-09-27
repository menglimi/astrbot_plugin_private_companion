# -*- coding: utf-8 -*-
"""PrivateCompanionPageApiUsersGroupsPart02Mixin。

由 tools/split_mixin_domain.py 从 page_api_users_groups.py 机械抽取（5 个方法 + 0 个模块级名字 + 0 个类级赋值 / 561 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApiUsersGroupsMixin）。
"""
from __future__ import annotations

import hmac
import time
import uuid
from .companion_interaction_expression import allowed_expression_bands, current_interaction_projection
from .helpers import _safe_int
from .page_api_shared import _page_api_host_request as request
from .page_api_users_groups_shared import logger
from .relationship_ledger import (
    normalize_relationship_mode,
    record_manual_relationship_change,
    relationship_positive_score_cap,
)
from copy import deepcopy
from typing import Any



class PrivateCompanionPageApiUsersGroupsPart02Mixin:
    """PrivateCompanionPageApiUsersGroupsPart02Mixin（从 PrivateCompanionPageApiUsersGroupsMixin 拆出）。"""


    async def unlink_unified_identity(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True)
        if not isinstance(payload, dict):
            return self._error("请求体必须是 JSON 对象")
        person_id = self._single_line(payload.get("person_id"), 80)
        user_id = self._single_line(payload.get("user_id"), 160)
        operation_id = self._single_line(payload.get("operation_id"), 120)
        confirmation_token = self._single_line(payload.get("confirmation_token"), 80)
        if "dry_run" in payload and type(payload.get("dry_run")) is not bool:
            return self._error("dry_run 必须是 JSON 布尔值")
        dry_run = payload.get("dry_run", True)
        if not person_id or not user_id or not operation_id:
            return self._error("person_id、user_id 和 operation_id 均为必填项")
        if not dry_run and not confirmation_token:
            return self._error("执行解绑必须提交预览返回的 confirmation_token")
        try:
            async with self.plugin._data_lock:
                registry = self._page_unified_person_registry()
                users = self.plugin.data.get("users")
                user = users.get(user_id) if isinstance(users, dict) else None
                if not isinstance(user, dict):
                    return self._error("用户不存在")
                if self._single_line(user.get("unified_person_id"), 80) != person_id:
                    return self._error("用户与统一人物不匹配")
                subject = self._single_line(
                    user.get("identity_subject_id") or user.get("user_id") or user_id,
                    160,
                )
                resolver = getattr(registry, "identity_for_person_subject", None)
                identity = resolver(person_id, subject) if callable(resolver) else None
                if not isinstance(identity, dict):
                    return self._error("当前用户没有唯一的正式身份链接")
                checkpoint_reader = getattr(registry, "identity_projection_checkpoint", None)
                checkpoint = checkpoint_reader(person_id) if callable(checkpoint_reader) else {}
                if not isinstance(checkpoint, dict) or checkpoint.get("ok") is not True:
                    return self._error("统一身份投影暂不可安全变更")
                expected_confirmation = self._identity_unlink_confirmation(
                    person_id=person_id,
                    operation_id=operation_id,
                    identity=identity,
                    checkpoint=checkpoint,
                )
                if not dry_run and not hmac.compare_digest(
                    confirmation_token, expected_confirmation
                ):
                    return self._error("身份状态已变化，请刷新后重新预览")
                result = registry.unlink_identity(
                    person_id,
                    identity,
                    operation_id=operation_id,
                    actor_id="page_administrator",
                    dry_run=dry_run,
                )
                if result.get("changed"):
                    emitter = getattr(self.plugin, "_req041_emit_identity_dual_write", None)
                    if callable(emitter):
                        emitter(
                            result,
                            action="unlink",
                            operation_id=operation_id,
                            registry=registry,
                        )
                    self.plugin._schedule_data_save(sections={"unified_person"})
            if not result.get("ok") and result.get("code") != "split_manual_review_required":
                return self._error(str(result.get("code") or "统一身份解绑失败"))
            safe_result = self._safe_identity_unlink_result(result)
            if dry_run and result.get("ok"):
                safe_result["confirmation_token"] = expected_confirmation
            return self._ok({"result": safe_result})
        except Exception as exc:
            logger.warning("统一身份解绑失败: %s", exc)
            return self._error("统一身份解绑失败")

    async def archive_unified_person(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True)
        if not isinstance(payload, dict):
            return self._error("请求体必须是 JSON 对象")
        person_id = self._single_line(payload.get("person_id"), 80)
        operation_id = self._single_line(payload.get("operation_id"), 120)
        confirmation_token = self._single_line(payload.get("confirmation_token"), 80)
        if "dry_run" in payload and type(payload.get("dry_run")) is not bool:
            return self._error("dry_run 必须是 JSON 布尔值")
        dry_run = payload.get("dry_run", True)
        if not person_id or not operation_id:
            return self._error("person_id 和 operation_id 均为必填项")
        if not dry_run and not confirmation_token:
            return self._error("执行归档必须提交预览返回的 confirmation_token")
        archive = getattr(self.plugin, "archive_unified_person", None)
        if not callable(archive):
            return self._error("人物归档服务不可用")
        try:
            result = await archive(
                person_id, operation_id=operation_id,
                confirmation_token=confirmation_token, dry_run=dry_run,
                actor_id="page_administrator", reason_code="person_archive",
            )
            if not result.get("ok"):
                code = str(result.get("code") or "人物归档失败")
                if code == "scoped_identity_archive_unavailable":
                    status = self._identity_archive_status()
                    return self._error(status["reason"] + status["recovery"] or "人物归档服务尚未就绪，请刷新身份与隔离页面")
                return self._error(code)
            return self._ok({"result": self._safe_person_lifecycle_result(result, "archive")})
        except Exception as exc:
            logger.warning("人物归档失败: %s", exc)
            return self._error("人物归档失败")

    async def delete_unified_person(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True)
        if not isinstance(payload, dict):
            return self._error("请求体必须是 JSON 对象")
        person_id = self._single_line(payload.get("person_id"), 80)
        operation_id = self._single_line(payload.get("operation_id"), 120)
        confirmation_token = self._single_line(payload.get("confirmation_token"), 80)
        if "dry_run" in payload and type(payload.get("dry_run")) is not bool:
            return self._error("dry_run 必须是 JSON 布尔值")
        dry_run = payload.get("dry_run", True)
        if not person_id or not operation_id:
            return self._error("person_id 和 operation_id 均为必填项")
        if not dry_run and not confirmation_token:
            return self._error("执行删除必须提交预览返回的 confirmation_token")
        purge = getattr(self.plugin, "purge_unified_person", None)
        if not callable(purge):
            return self._error("人物删除服务不可用")
        try:
            result = await purge(
                person_id, operation_id=operation_id,
                confirmation_token=confirmation_token, dry_run=dry_run,
                actor_id="page_administrator", reason_code="person_delete",
            )
            safe_result = self._safe_person_lifecycle_result(result, "purge")
            if not result.get("ok") and result.get("code") != "archive_retention_active":
                return self._error(str(result.get("code") or "人物删除失败"))
            return self._ok({"result": safe_result})
        except Exception as exc:
            logger.warning("人物删除失败: %s", exc)
            return self._error("人物删除失败")

    async def preview_unified_identity_merge(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True)
        if not isinstance(payload, dict):
            return self._error("请求体必须是 JSON 对象")
        source_person_id = self._single_line(payload.get("source_person_id"), 80)
        target_person_id = self._single_line(payload.get("target_person_id"), 80)
        operation_id = self._single_line(payload.get("operation_id"), 120)
        if not source_person_id or not target_person_id or not operation_id:
            return self._error("source_person_id、target_person_id 和 operation_id 均为必填项")
        try:
            async with self.plugin._data_lock:
                result = self._page_unified_person_registry().preview_person_merge(
                    source_person_id,
                    target_person_id,
                    operation_id=operation_id,
                )
            if not result.get("ok") and result.get("code") != "merge_manual_review_required":
                return self._error(str(result.get("code") or "统一人物合并预览失败"))
            return self._ok({"result": result})
        except Exception as exc:
            logger.warning("统一人物合并预览失败: %s", exc)
            return self._error("统一人物合并预览失败")

    async def update_user(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True)
        if not isinstance(payload, dict):
            return self._error("请求体必须是 JSON 对象")
        user_id = str(payload.get("user_id", "")).strip()
        if not user_id:
            return self._error("缺少 user_id")
        if "enabled" in payload or "private_companion_enabled" in payload:
            return self._error("私聊权限已移除，普通私聊始终可用")
        if "proactive_private_enabled" in payload and type(payload.get("proactive_private_enabled")) is not bool:
            return self._error("proactive_private_enabled 必须是 JSON 布尔值")
        if "portrait_mode" in payload:
            portrait_mode = str(payload.get("portrait_mode") or "").strip().lower()
            if portrait_mode not in {"follow_global", "disabled", "use_existing", "learn_and_use"}:
                return self._error("portrait_mode 无效")
            payload["portrait_mode"] = portrait_mode
        relationship_score = None
        intimacy_keys = [
            key
            for key in ("companion_intimacy", "relationship_score")
            if key in payload
        ]
        if len(intimacy_keys) > 1:
            return self._error("陪伴亲密度字段不能重复提交")
        if intimacy_keys:
            try:
                relationship_score = self._relationship_score_input(
                    payload.get(intimacy_keys[0])
                )
            except ValueError as exc:
                return self._error(str(exc))
        requested_mode = str(payload.get("relationship_mode") or "").strip().lower() if "relationship_mode" in payload else None
        if requested_mode is not None and requested_mode not in {"normal", "owner_exclusive"}:
            return self._error("relationship_mode must be normal or owner_exclusive")
        relationship_prompt_requested = "owner_exclusive_relationship_prompt" in payload
        relationship_prompt_value = payload.get("owner_exclusive_relationship_prompt")
        if relationship_prompt_requested and relationship_prompt_value is not None and not isinstance(relationship_prompt_value, str):
            return self._error("owner_exclusive_relationship_prompt 必须是文本")
        requested_interaction_band = (
            str(payload.get("current_interaction_band") or "").strip().lower()
            if "current_interaction_band" in payload
            else None
        )
        interaction_expires_at = 0.0
        if "current_interaction_expires_at" in payload:
            raw_expiry = payload.get("current_interaction_expires_at")
            if isinstance(raw_expiry, bool) or not isinstance(raw_expiry, (int, float)):
                return self._error("current_interaction_expires_at must be a timestamp")
            interaction_expires_at = float(raw_expiry)
            if interaction_expires_at < 0 or interaction_expires_at > time.time() + 366 * 86400:
                return self._error("current_interaction_expires_at is outside the allowed range")
        try:
            action_message = ""
            async with self.plugin._data_lock:
                save_sections = {"users"}
                user = self.plugin._get_user(user_id)
                private_memory_mutation = any(
                    bool(payload.get(key))
                    for key in (
                        "clear_emotion_state",
                        "clear_behavior_habits",
                        "clear_learning",
                        "clear_open_loops",
                    )
                ) or bool(self._single_line(payload.get("remove_open_loop_text"), 120))
                private_memory_revision = None
                memory_managed_getter = getattr(
                    self.plugin, "_req041_private_memory_managed", None
                )
                private_memory_managed = bool(
                    memory_managed_getter() if callable(memory_managed_getter) else False
                )
                if private_memory_mutation and private_memory_managed:
                    preparer = getattr(
                        self.plugin, "_req041_prepare_authoritative_private_memory", None
                    )
                    private_memory_revision = preparer(user) if callable(preparer) else None
                    if private_memory_revision is None:
                        return self._error("权威私聊记忆暂不可写，请稍后重试")
                expression_voice_needs_refresh = False
                previous_role = self.plugin._private_user_role(user, user_id)
                previous_mode = normalize_relationship_mode(user.get("relationship_mode"), previous_role)
                role = previous_role
                if "relationship_role" in payload:
                    normalized_role = self.plugin._normalize_private_user_role(payload.get("relationship_role"))
                    if not normalized_role:
                        return self._error("relationship_role must be owner or friend")
                    role = normalized_role
                next_mode = normalize_relationship_mode(requested_mode if requested_mode is not None else previous_mode, role)
                if relationship_prompt_requested:
                    stable_user_id = self._single_line(user.get("user_id"), 160)
                    if stable_user_id != user_id:
                        return self._error("稳定用户身份不匹配，请刷新用户详情后重试")
                    prompt_normalizer = getattr(
                        self.plugin,
                        "_normalize_owner_exclusive_relationship_prompt",
                        None,
                    )
                    normalized_relationship_prompt = (
                        prompt_normalizer(relationship_prompt_value)
                        if callable(prompt_normalizer)
                        else str(relationship_prompt_value or "").strip()
                    )
                    if normalized_relationship_prompt and role != "owner":
                        return self._error("专属关系文本只允许绑定主要用户")
                if requested_mode == "owner_exclusive" and role != "owner":
                    return self._error("owner_exclusive relationship requires an owner user")
                if previous_mode == "owner_exclusive" and role != "owner" and requested_mode != "normal":
                    return self._error("switch the relationship to a normal stage before changing the owner role")
                if previous_mode == "owner_exclusive" and requested_mode == "normal" and relationship_score is None:
                    return self._error("leaving owner_exclusive requires selecting a normal relationship score")
                if previous_mode == "owner_exclusive" and relationship_score is not None and requested_mode != "normal":
                    return self._error("owner_exclusive relationship score is frozen; select normal mode first")
                if next_mode == "owner_exclusive" and relationship_score is not None:
                    return self._error("owner_exclusive relationship does not accept an exact score")
                if relationship_score is not None and relationship_score > 0 and role != "owner":
                    positive_cap = relationship_positive_score_cap(
                        getattr(self.plugin, "relationship_positive_stage_cap_key", "close")
                    )
                    if relationship_score > positive_cap:
                        return self._error(
                            f"普通用户亲密度上限为 {positive_cap}（当前配置的阶段上限），"
                            "请先调整「普通用户正向亲密度阶段上限」或将该用户设为主要用户"
                        )
                if requested_interaction_band is not None:
                    if not requested_interaction_band:
                        return self._error("current_interaction_band is required")
                    if requested_interaction_band not in allowed_expression_bands(role, next_mode):
                        return self._error("current interaction band is not allowed for this relationship")
                    requested_projection = current_interaction_projection(
                        {"expression_band": requested_interaction_band},
                        relationship_role=role,
                        relationship_mode=next_mode,
                        relationship_score=user.get("relationship_score"),
                        normal_interaction_band_cap=getattr(self.plugin, "normal_interaction_band_cap", "warm"),
                    )
                    if requested_projection.get("expression_band") != requested_interaction_band:
                        return self._error("current interaction band exceeds the configured user cap")
                capability_changes = {}
                for capability_key in (
                    "proactive_private_enabled",
                    "portrait_mode",
                ):
                    if capability_key in payload:
                        capability_changes[capability_key] = payload.get(capability_key)
                if capability_changes:
                    updater = getattr(self.plugin, "_req036_update_capabilities", None)
                    if not callable(updater):
                        return self._error("统一用户权限服务不可用")
                    capability_result = updater(
                        user,
                        capability_changes,
                        actor_id="page_administrator",
                        target_identity=user_id,
                        reason_code="page_administrator_update",
                    )
                    if not bool(capability_result.get("ok")):
                        return self._error(str(capability_result.get("code") or "权限更新失败"))
                    if "proactive_private_enabled" in capability_changes:
                        proactive_enabled = bool(
                            capability_result["capabilities"].get("proactive_private_enabled")
                        )
                        if proactive_enabled:
                            self.plugin._ensure_private_user_umo(user_id, user)
                        else:
                            self.plugin._clear_pending_proactive_plan(user)
                legacy_profile_before = {
                    key: (key in user, user.get(key))
                    for key in ("nickname", "style")
                    if key in payload
                }
                if "nickname" in payload:
                    user["nickname"] = self._single_line(payload.get("nickname"), 24)
                if "style" in payload:
                    user["style"] = self._single_line(payload.get("style"), 24)
                profile_fact_changes = {}
                if "nickname" in payload:
                    profile_fact_changes["preferred_address"] = user["nickname"]
                    if user["nickname"]:
                        profile_fact_changes["display_name"] = user["nickname"]
                if "style" in payload:
                    profile_fact_changes["style"] = user["style"]
                if profile_fact_changes:
                    profile_updater = getattr(
                        self.plugin, "_req041_update_unified_profile_facts", None
                    )
                    if callable(profile_updater):
                        profile_result = profile_updater(
                            user,
                            profile_fact_changes,
                            actor_id="page_administrator",
                            schedule_save=False,
                        )
                        if (
                            profile_result.get("state") != "skipped"
                            and profile_result.get("ok") is not True
                        ):
                            for key, (was_present, previous_value) in legacy_profile_before.items():
                                if was_present:
                                    user[key] = previous_value
                                else:
                                    user.pop(key, None)
                            return self._error(
                                str(profile_result.get("code") or "统一身份档案更新失败")
                            )
                        if profile_result.get("ok") and profile_result.get("changed"):
                            save_sections.add("unified_person")
                if "relationship_role" in payload:
                    user["relationship_role"] = role
                    expression_voice_needs_refresh = role != previous_role
                if requested_mode is not None:
                    user["relationship_mode"] = next_mode
                    expression_voice_needs_refresh = expression_voice_needs_refresh or next_mode != previous_mode
                if relationship_prompt_requested:
                    prompt_setter = getattr(
                        self.plugin,
                        "_set_owner_exclusive_relationship_prompt",
                        None,
                    )
                    if not callable(prompt_setter):
                        return self._error("当前版本不支持按人格保存专属关系文本")
                    prompt_result = prompt_setter(
                        user,
                        stable_user_id=user_id,
                        text=normalized_relationship_prompt,
                    )
                    if not prompt_result.get("ok"):
                        return self._error(prompt_result.get("message") or "专属关系文本保存失败")
                if relationship_score is not None:
                    previous_score = _safe_int(user.get("relationship_score"), 0, -1200, 1200)
                    effective_score = relationship_score
                    user["relationship_score"] = effective_score
                    record_manual_relationship_change(
                        user,
                        previous_score,
                        effective_score,
                        now=time.time(),
                        reason_code="administrator_manual_relationship_adjustment",
                    )
                if requested_interaction_band is not None:
                    changed_at = time.time()
                    user["current_interaction"] = current_interaction_projection(
                        {
                            "expression_band": requested_interaction_band,
                            "source": "manual",
                            "operator": "page_administrator",
                            "reason": self._single_line(payload.get("current_interaction_reason"), 120) or "administrator_manual_override",
                            "updated_at": changed_at,
                            "expires_at": interaction_expires_at,
                            "manual_override": True,
                        },
                        relationship_role=role,
                        relationship_mode=next_mode,
                        relationship_score=user.get("relationship_score"),
                        normal_interaction_band_cap=getattr(self.plugin, "normal_interaction_band_cap", "warm"),
                        now=changed_at,
                    )
                    contact = user.get("contact_preference")
                    contact_active = bool(
                        (
                            isinstance(contact, dict)
                            and (
                                contact.get("active")
                                or contact.get("no_contact")
                                or contact.get("backoff")
                            )
                        )
                        or str(contact or "").strip().lower()
                        in {"no_contact", "backoff", "avoid", "stop"}
                    )
                    if requested_interaction_band != "avoidant" and contact_active:
                        user["contact_preference"] = {
                            "mode": "normal",
                            "active": False,
                            "no_contact": False,
                            "backoff": False,
                            "source": "manual",
                            "operator": "page_administrator",
                            "reason_code": "administrator_manual_interaction_correction",
                            "updated_at": changed_at,
                        }
                    expression_voice_needs_refresh = True
                elif role != previous_role or next_mode != previous_mode:
                    user["current_interaction"] = current_interaction_projection(
                        user.get("current_interaction"),
                        relationship_role=role,
                        relationship_mode=next_mode,
                        relationship_score=user.get("relationship_score"),
                        normal_interaction_band_cap=getattr(self.plugin, "normal_interaction_band_cap", "warm"),
                        now=time.time(),
                    )
                if "proactive_daily_limit" in payload:
                    user["proactive_daily_limit"] = _safe_int(payload.get("proactive_daily_limit"), -1, -1, 30)
                for key in (
                    "proactive_idle_minutes",
                    "proactive_min_interval_minutes",
                    "photo_daily_limit",
                    "screen_peek_daily_limit",
                    "poke_daily_limit",
                ):
                    if key in payload:
                        user[key] = _safe_int(payload.get(key), -1, -1)
                if self.plugin._private_user_role(user, user_id) == "friend":
                    user["photo_daily_limit"] = -1
                    user["photo_sent_today"] = 0
                    user["photo_sent_day"] = ""
                    user["photo_generated_today"] = 0
                    user["photo_generated_day"] = ""
                    user["last_generated_photo_path"] = ""
                    user["last_generated_photo_at"] = 0
                    user["screen_peek_daily_limit"] = -1
                    user["screen_peek_today"] = 0
                    user["screen_peek_day"] = ""
                    user["screen_peek_last_at"] = 0
                if "proactive_boundary_note" in payload:
                    user["proactive_boundary_note"] = self._single_line(payload.get("proactive_boundary_note"), 180)
                if payload.get("reset_daily"):
                    user["sent_today"] = 0
                    user["sent_day"] = ""
                    user["ignored_streak"] = 0
                    user["photo_sent_today"] = 0
                    user["photo_sent_day"] = ""
                    user["photo_generated_today"] = 0
                    user["photo_generated_day"] = ""
                    user["screen_peek_today"] = 0
                if payload.get("clear_schedule"):
                    self.plugin._clear_pending_proactive_plan(user)
                if payload.get("clear_emotion_state"):
                    user["intent_profile"] = {}
                    user.pop("relationship_state", None)
                    user["current_interaction"] = current_interaction_projection(
                        None,
                        relationship_role=user.get("relationship_role"),
                        relationship_mode=user.get("relationship_mode"),
                        relationship_score=user.get("relationship_score"),
                        normal_interaction_band_cap=getattr(self.plugin, "normal_interaction_band_cap", "warm"),
                    )
                if payload.get("clear_behavior_habits"):
                    user["behavior_habits"] = {}
                if payload.get("clear_learning"):
                    for key, empty in (
                        ("companion_memory", {}),
                        ("expression_profile", {}),
                        ("intent_profile", {}),
                        ("recent_reply_topics", []),
                        ("dialogue_episodes", []),
                        ("open_loops", []),
                        ("action_preferences", {}),
                    ):
                        user[key] = empty
                    user["episode_message_count"] = 0
                    user["last_episode_refresh_at"] = 0
                    user["last_memory_refresh_at"] = 0
                    expression_voice_needs_refresh = True
                if payload.get("clear_open_loops"):
                    action_message = self.plugin._remove_open_loop_entry(user, "全部")
                remove_open_loop_text = self._single_line(payload.get("remove_open_loop_text"), 120)
                if remove_open_loop_text:
                    action_message = self.plugin._remove_open_loop_entry(user, remove_open_loop_text)
                expression_action = self._single_line(payload.get("expression_action"), 40)
                if expression_action:
                    action_message = self._apply_expression_profile_action(user, payload)
                    if expression_action in {"approve", "delete_sample"}:
                        expression_voice_needs_refresh = True
                if expression_voice_needs_refresh:
                    voice_refresher = getattr(self.plugin, "_refresh_expression_voice_profile", None)
                    if callable(voice_refresher):
                        voice_refresher()
                        save_sections.add("expression_voice_profile")
                if any(
                    key in payload
                    for key in ("relationship_role", "relationship_mode", "relationship_score", "companion_intimacy")
                ):
                    snapshot_emitter = getattr(self.plugin, "_req041_emit_relationship_snapshot", None)
                    if callable(snapshot_emitter):
                        snapshot_emitter(
                            user,
                            reason_code="administrator_relationship_update",
                        )
                if private_memory_mutation and private_memory_managed:
                    committer = getattr(
                        self.plugin, "_req041_commit_authoritative_private_memory", None
                    )
                    if not callable(committer) or not committer(
                        user,
                        expected_revision=private_memory_revision,
                        operation_id=f"req041-page-memory:{user_id}:{uuid.uuid4().hex}",
                    ):
                        return self._error("权威私聊记忆已发生并发变更，请刷新后重试")
                    save_sections.add("_req041_private_memory")
                self.plugin._save_data_sync(sections=save_sections)
                snapshot = deepcopy(user)
            result = self._user_summary(user_id, snapshot)
            result.update(
                {
                    "expression_profile": self._expression_profile_summary(snapshot),
                }
            )
            if action_message:
                result["message"] = action_message
            return self._ok(result)
        except Exception as exc:
            logger.error(f"更新用户失败: {exc}", exc_info=True)
            return self._error(str(exc))
