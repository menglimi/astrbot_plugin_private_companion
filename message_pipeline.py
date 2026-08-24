# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import uuid
from copy import deepcopy
from functools import wraps
from typing import Any

from astrbot.api import logger

from .companion_interaction_expression import current_interaction_projection
from .helpers import (
    _group_link_message_context,
    _missing_optional_model_dependency,
    _now_ts,
    _safe_float,
    _safe_int,
    _single_line,
)


def _coalesce_event_data_saves(handler: Any) -> Any:
    """Submit all durable mutations from one message handler as one request."""

    @wraps(handler)
    async def wrapped(self: Any, event: Any, *args: Any, **kwargs: Any) -> Any:
        starter = getattr(self, "_begin_event_data_save_batch", None)
        finisher = getattr(self, "_finish_event_data_save_batch", None)
        handle = starter(event) if callable(starter) else None
        try:
            return await handler(self, event, *args, **kwargs)
        finally:
            if callable(finisher):
                finisher(handle)

    return wrapped


def event_data_save_boundary(handler: Any = None, *, flush: bool = False) -> Any:
    """Share an event-owned save batch across early and final message hooks."""

    if handler is None:
        return lambda actual: event_data_save_boundary(actual, flush=flush)

    @wraps(handler)
    async def wrapped(self: Any, event: Any, *args: Any, **kwargs: Any) -> Any:
        starter = getattr(self, "_begin_event_data_save_batch", None)
        finisher = getattr(self, "_finish_event_data_save_batch", None)
        suspender = getattr(self, "_suspend_event_data_save_batch", None)
        handle = starter(event) if callable(starter) else None
        completed = False
        try:
            result = await handler(self, event, *args, **kwargs)
            completed = True
            return result
        finally:
            if handle and callable(finisher):
                stopped = False
                is_stopped = getattr(event, "is_stopped", None)
                if callable(is_stopped):
                    try:
                        stopped = bool(is_stopped())
                    except Exception:
                        stopped = False
                if flush or stopped or not completed:
                    finisher(handle)
                elif callable(suspender):
                    suspender(handle)

    return wrapped


def _persona_value(owner: Any, key: str, default: Any = None) -> Any:
    """Read the active persona setting, with a legacy harness fallback."""
    getter = getattr(owner, "persona_setting", None)
    if callable(getter):
        try:
            return getter(key, default)
        except Exception:
            pass
    return getattr(owner, key, default)


def _persona_feature_enabled(owner: Any, key: str, default: bool = False) -> bool:
    """Apply a persona-scoped feature flag and retain proactive-only unlocks."""
    if not hasattr(owner, "enable_multi_persona_mode"):
        checker = getattr(owner, "_feature_enabled_or_temp_unlocked", None)
        if callable(checker):
            try:
                return bool(checker(key, default))
            except Exception:
                pass
    if bool(_persona_value(owner, key, default)):
        return True
    unlocker = getattr(owner, "_proactive_only_temp_unlock_allows", None)
    return bool(
        _persona_value(owner, "enable_proactive_only_mode", False)
        and callable(unlocker)
        and unlocker(key)
    )


@_coalesce_event_data_saves
async def handle_private_message(self: Any, event: Any, *args: Any, **kwargs: Any) -> Any:
    """记录私聊互动、图片防抖、用户画像和主动陪伴反馈。"""
    if self is None:
        return
    inbound_checker = getattr(self, "_event_is_inbound_chat_message", None)
    if callable(inbound_checker) and not inbound_checker(event):
        logger.debug("[PrivateCompanion] 非入站聊天事件跳过私聊陪伴链路")
        return
    received_ts = _now_ts()
    user_id = str(event.get_sender_id())
    self_id = self._event_self_id(event)
    if user_id and self_id and user_id == self_id:
        logger.info("[PrivateCompanion] 忽略 Bot 自己的私聊回流事件: user=%s", user_id)
        return
    sender_display_name = _single_line(self._sender_display_name(event), 40)
    text = _single_line(event.message_str, 120)
    # Keep optional feedback/observation results defined when the message is
    # empty or exits through a lightweight branch.
    expression_feedback: dict[str, Any] = {}
    calendar_observation_result: dict[str, Any] = {}
    async with self._data_lock:
        private_user, auto_profile_created = self._ensure_auto_private_user_profile(
            event,
            user_id=user_id,
            sender_display_name=sender_display_name,
            now=received_ts,
        )
        if isinstance(private_user, dict):
            user_id = _single_line(private_user.get("user_id"), 160) or user_id
        migrator = getattr(self, "_req036_migrate_configured_target_capability", None)
        if callable(migrator):
            migrator(user_id, private_user)
        self._req036_attach_unified_profile_context(
            event,
            user=private_user if isinstance(private_user, dict) else None,
            source="private_auto",
        )
        self._schedule_data_save(sections={"users", "unified_person"})
    if auto_profile_created:
        logger.info(
            "[PrivateCompanion] 已建立最小用户档案: user=%s platform=%s",
            _single_line(self._canonical_private_user_id(user_id), 80),
            _single_line(self._platform_kind_for_event(event), 40),
        )
    if self._is_onebot_poke_notice_event(event):
        # 戳一戳会以私聊空文本事件进入 AstrBot；交给专用插件处理。
        logger.debug("[PrivateCompanion] 私聊戳一戳 notice 交给专用插件")
        return
    self._qzone_note_event_bot(event)
    if text.startswith(("陪伴", "/陪伴", "私聊陪伴", "主动陪伴")):
        return
    if self._message_debounce_command_text(event, text):
        return
    existing_reply_preview = self._event_existing_reply_result_preview(event)
    if existing_reply_preview:
        preview_user_id = self._canonical_private_user_id(user_id)
        preview_users = self.data.get("users", {})
        preview_user = preview_users.get(preview_user_id) if isinstance(preview_users, dict) else None
        if (
            self._private_passive_profile_available(
                preview_user_id,
                preview_user if isinstance(preview_user, dict) else None,
            )
            and not (isinstance(preview_user, dict) and not bool(preview_user.get("enabled", True)))
        ):
            await self._cancel_activity_followup_on_user_return(
                preview_user_id or user_id,
                trigger_message_id=self._event_message_id(event),
                trigger_umo=str(getattr(event, "unified_msg_origin", "") or ""),
                source_text=text,
            )
        logger.info(
            "[PrivateCompanion] 已有其他链路回复,跳过私聊被动接管: user=%s text=%s result=%s",
            user_id,
            _single_line(text, 80),
            _single_line(existing_reply_preview, 120),
        )
        return
    canonical_user_id = self._canonical_private_user_id(user_id)
    raw_users = self.data.get("users", {})
    existing_user = raw_users.get(canonical_user_id) if isinstance(raw_users, dict) else None
    private_profile_available = self._private_passive_profile_available(
        canonical_user_id,
        existing_user if isinstance(existing_user, dict) else None,
    )
    if not private_profile_available:
        logger.info(
            "[PrivateCompanion] 非目标/未启用私聊放行默认主链: user=%s text=%s reason=%s",
            _single_line(canonical_user_id or user_id, 80),
            _single_line(text, 120),
            "not_profile",
        )
        return
    self._record_c3_inbound_activity(
        event,
        text=text,
        received_ts=received_ts,
        user_id=canonical_user_id or user_id,
        sender_id=user_id,
        sender_name=sender_display_name,
    )
    await self._cancel_activity_followup_on_user_return(
        canonical_user_id or user_id,
        trigger_message_id=self._event_message_id(event),
        trigger_umo=str(getattr(event, "unified_msg_origin", "") or ""),
        source_text=text,
    )
    if text and await self._maybe_answer_companion_manual_natural_question(event, text):
        return
    natural_photo_text = _single_line(event.message_str, 800)
    if natural_photo_text:
        try:
            if await self._maybe_handle_natural_language_photo_request(event, user_id, natural_photo_text):
                return
        except Exception as exc:
            missing = _missing_optional_model_dependency(exc)
            if not missing:
                raise
            logger.warning(
                "[PrivateCompanion] 私聊自然语言生图前置处理缺少可选模型依赖，已降级放行普通私聊: user=%s module=%s err=%s",
                user_id,
                missing,
                _single_line(exc, 160),
            )
    if self._proactive_only_blocks_passive_event(event, "private_event_pipeline"):
        await self._record_proactive_only_private_feedback(
            event,
            user_id=user_id,
            sender_display_name=sender_display_name,
            text=text,
            received_ts=received_ts,
        )
        return
    receipt_text = _single_line(event.message_str, 800)
    if receipt_text and await self._maybe_handle_atrelay_private_receipt_reply(event, user_id, sender_display_name, receipt_text):
        return
    forward_only_prompt = ""
    if self._feature_enabled_or_temp_unlocked("enable_forward_message_adaptation") and not text:
        try:
            forward_id, forward_payload = await self._find_forward_descriptor_for_event(event)
        except Exception as exc:
            forward_id, forward_payload = "", {}
            logger.info("[PrivateCompanion] 私聊合并消息预解析失败: user=%s error=%s", user_id, _single_line(exc, 120))
        if forward_id or forward_payload:
            forward_only_prompt = "我转发了一段聊天记录,你看看里面在说什么。"
            text = forward_only_prompt
            try:
                event.message_str = forward_only_prompt
                message_obj = getattr(event, "message_obj", None)
                if message_obj is not None:
                    setattr(message_obj, "message_str", forward_only_prompt)
            except Exception:
                pass
            logger.info(
                "[PrivateCompanion] 私聊纯合并消息已补触发文本: user=%s id=%s inline=%s",
                user_id,
                _single_line(forward_id, 40) or "inline",
                bool(forward_payload),
            )
    if not text and not forward_only_prompt:
        quoted_relation_text = await self._private_reply_only_relation_lookup_text(event)
        if quoted_relation_text:
            text = quoted_relation_text
            try:
                event.message_str = quoted_relation_text
                message_obj = getattr(event, "message_obj", None)
                if message_obj is not None:
                    setattr(message_obj, "message_str", quoted_relation_text)
            except Exception:
                pass
    has_nontext_content = self._private_event_has_nontext_content(event)
    if (
        not text
        and not forward_only_prompt
        and not self._private_event_has_image_safe(event, label="private_empty_guard")
        and not has_nontext_content
    ):
        component_types: list[str] = []
        try:
            for item in self._event_components(event):
                component_types.append(_single_line(self._component_type_name(item), 40))
        except Exception:
            component_types = []
        logger.info(
            "[PrivateCompanion] 忽略空私聊事件,避免阻止默认 LLM 空跑: user=%s components=%s",
            user_id,
            ",".join([item for item in component_types if item]) or "-",
        )
        self._record_passive_no_reply(
            event,
            source="私聊事件",
            reason="空私聊事件被忽略",
            detail=",".join([item for item in component_types if item]) or "-",
            level="info",
        )
        empty_result = self._build_result_from_chain([])
        try:
            empty_result.stop_event()
        except Exception:
            pass
        event.set_result(empty_result)
        event.stop_event()
        return
    reference_media_with_text = False
    if text and not forward_only_prompt:
        try:
            reference_media_with_text = await self._event_references_media_or_forward_with_text(event, text)
        except Exception as exc:
            missing = _missing_optional_model_dependency(exc)
            if not missing:
                raise
            logger.warning(
                "[PrivateCompanion] 私聊引用媒体检测缺少可选模型依赖，已按普通文本继续: user=%s module=%s err=%s",
                user_id,
                missing,
                _single_line(exc, 160),
            )
            reference_media_with_text = False
        if reference_media_with_text:
            logger.info(
                "[PrivateCompanion] 私聊引用媒体/合并消息附带文字,跳过文本收口等待: user=%s text=%s",
                user_id,
                _single_line(text, 80),
            )

    raw_users = self.data.get("users", {})
    fast_user = raw_users.get(user_id) if isinstance(raw_users, dict) else None
    fast_target_user = self._private_passive_profile_available(user_id, fast_user)
    if (
        fast_target_user
        and text
        and not forward_only_prompt
        and not reference_media_with_text
        and await self._maybe_resume_pending_atrelay_request(event, user_id, text)
    ):
        return
    if (
        fast_target_user
        and text
        and not forward_only_prompt
        and not reference_media_with_text
        and await self._maybe_handle_direct_atrelay_request(event, text)
    ):
        return
    if (
        fast_target_user
        and text
        and not forward_only_prompt
        and not bool(_persona_value(self, 'enable_smart_message_debounce', False))
        and self._message_debounce_seconds("text") <= 0
        and self._is_lightweight_private_passive_inbound(text)
        and not self._meal_care_requires_full_reply(fast_user, text)
        and not self._is_private_image_only_message(event, text)
    ):
        if self._is_recent_poke_echo(fast_user, text):
            logger.info("[PrivateCompanion] 忽略 poke 回流事件,不计入用户新消息: %s", user_id)
            return
        if self._is_duplicate_inbound_message(event, scope=f"private:{user_id}", sender_id=user_id, text=text):
            self._record_passive_no_reply(
                event,
                source="私聊去重",
                reason="重复私聊事件被忽略",
                detail=text,
                level="info",
            )
            event.stop_event()
            return
        self._note_private_user_umo(user_id, fast_user, event.unified_msg_origin)
        self._note_private_display_name_observation(fast_user, user_id, sender_display_name, now=received_ts)
        fast_user["last_seen"] = received_ts
        fast_user["last_activity_at"] = received_ts
        self._note_private_inbound_activity(fast_user, received_ts, text=text)
        self._mark_greetings_satisfied_by_recent_activity(fast_user, activity_ts=received_ts)
        self._note_morning_greeting_reply(fast_user, now=received_ts)
        if self._cancel_inbound_conflicting_greeting(
            fast_user,
            now=received_ts,
            user_id=user_id,
            trigger_umo=str(getattr(event, "unified_msg_origin", "") or ""),
        ):
            logger.info("[PrivateCompanion] 用户已在当前问候时段自然来聊,已请求取消冲突问候候选: %s", user_id)
            if not self._simulation_active(fast_user) and _safe_float(fast_user.get("next_proactive_at"), 0) <= 0:
                self._schedule_next_proactive(fast_user, now=received_ts)
        safe_text = self._sanitize_orphan_tts_placeholders(text)
        fast_user["last_user_message"] = safe_text or text
        fast_user["last_user_message_at"] = received_ts
        self._note_user_chronotype_from_inbound(fast_user, safe_text or text, received_ts)
        fast_intent_profile = self._analyze_inbound_intent(text)
        boundary_enricher = getattr(self, "_enrich_boundary_feedback_intent", None)
        if callable(boundary_enricher):
            fast_intent_profile = boundary_enricher(fast_user, fast_intent_profile)
        fast_user["intent_profile"] = fast_intent_profile
        violation_settler = getattr(self, "_apply_relationship_violation_policy", None)
        if callable(violation_settler):
            violation_settler(
                fast_user,
                fast_intent_profile,
                event_id=self._event_message_id(event),
                now=received_ts,
            )
        if self._clear_state_share_proactive_after_user_status_question(fast_user, user_id=user_id, text=safe_text or text, now=received_ts):
            if not self._simulation_active(fast_user) and _safe_float(fast_user.get("next_proactive_at"), 0) <= 0:
                self._schedule_next_proactive(fast_user, now=received_ts)
        try:
            read_view_getter = getattr(self, "_req041_relationship_read_view", None)
            fast_read_user = (
                read_view_getter(event, fast_user, kind="private")
                if callable(read_view_getter) else fast_user
            )
            scoped_read_getter = getattr(self, "_req041_scoped_private_read_view", None)
            if callable(scoped_read_getter):
                fast_read_user = scoped_read_getter(event, fast_read_user)
            await self._memory_companion_apply_emotional_drift(
                event=event,
                user_id=user_id,
                user=fast_user,
            )
            self._memory_companion_attach_private_context(
                event,
                user_id=user_id,
                user=fast_read_user,
                text=safe_text or text,
            )
        except Exception as exc:
            missing = _missing_optional_model_dependency(exc)
            if not missing:
                raise
            logger.warning(
                "[PrivateCompanion] 私聊轻量链路记忆桥缺少可选模型依赖，已跳过记忆增强: user=%s module=%s err=%s",
                user_id,
                missing,
                _single_line(exc, 160),
            )
        rest_silence_applied = self._apply_user_rest_silence_from_message(fast_user, safe_text or text, now=received_ts)
        fast_user["inbound_count"] = _safe_int(fast_user.get("inbound_count"), 0) + 1
        self._apply_relationship_event(
            fast_user,
            1,
            reason_code="fast_inbound",
            event_id=self._event_message_id(event),
            now=received_ts,
        )
        fast_user["episode_message_count"] = _safe_int(fast_user.get("episode_message_count"), 0, 0) + 1
        if _safe_float(fast_user.get("awaiting_reply_since"), 0) > 0:
            audit_outcome_recorder = getattr(self, "_mark_proactive_audit_reply_outcome", None)
            if callable(audit_outcome_recorder):
                audit_outcome_recorder(
                    fast_user,
                    received_at=received_ts,
                    message_id=self._event_message_id(event),
                )
            fast_user["reply_count"] = _safe_int(fast_user.get("reply_count"), 0) + 1
            self._note_action_reply_feedback(
                fast_user,
                str(fast_user.get("last_proactive_action") or "message"),
                text,
            )
            self._apply_relationship_event(
                fast_user,
                2,
                reason_code="fast_proactive_reply",
                event_id=self._event_message_id(event),
                now=received_ts,
            )
            fast_user["awaiting_reply_since"] = 0
            fast_user["last_reply_at"] = received_ts
            fast_user["last_private_reply_at"] = received_ts
            fast_user["pending_followup_event"] = {}
            fast_user["planned_proactive_quota_exempt"] = False
        fast_user["ignored_streak"] = 0
        fast_user["friend_unanswered_silenced_since"] = 0
        fast_user["friend_unanswered_silence_note"] = ""
        fast_user_is_owner = self._private_user_role(fast_user, user_id) == "owner"
        fast_meal_care_result: dict[str, Any] = {}
        if fast_user_is_owner:
            fast_meal_care_result = self._handle_meal_care_inbound(
                fast_user,
                safe_text or text,
                now=received_ts,
            )
        fast_interaction_warmth_applied = (
            bool(_persona_value(self, "enable_custom_relationship_stage_policy", False))
            and fast_user_is_owner
            and self._apply_interaction_warmth_to_state(text, fast_user)
        )
        fast_calendar_observation_result: dict[str, Any] = {}
        observer = getattr(self, "_agenda_observe_calendar_message", None)
        if callable(observer):
            try:
                fast_calendar_observation_result = observer(
                    text=safe_text or text,
                    event_time=received_ts,
                    source_ref=self._event_message_id(event) or f"{str(getattr(event, 'unified_msg_origin', '') or '')}:{received_ts}",
                    conversation_id=str(getattr(event, "unified_msg_origin", "") or ""),
                    source_user_id=user_id,
                    target_user_id=user_id,
                    subject_actor_id=str(getattr(self, "bot_personal_subject", "") or "bot_self"),
                ) or {}
            except Exception as exc:
                logger.warning(
                    "[PrivateCompanion] 轻量私聊日历候选观察失败，已放行当前回复: user=%s error=%s",
                    user_id,
                    _single_line(exc, 160),
                )
        if fast_interaction_warmth_applied:
            self._apply_relationship_event(
                fast_user,
                1,
                reason_code="fast_interaction_warmth",
                event_id=self._event_message_id(event),
                now=received_ts,
            )
        fast_save_sections = {"users"}
        if fast_meal_care_result.get("foods"):
            fast_save_sections.add("food_menu")
        if fast_interaction_warmth_applied:
            fast_save_sections.update({"state_conditions", "daily_state"})
        fast_save_sections.update(fast_calendar_observation_result.get("changed_sections") or ())
        self._schedule_data_save(sections=fast_save_sections)
        if (
            rest_silence_applied
            and _safe_float(fast_user.get("user_rest_until"), 0) > received_ts
            and self._user_rest_signal_should_block_current_reply(safe_text or text)
        ):
            self._stop_private_reply_after_user_rest_signal(event, user_id, safe_text or text)
            return
        return

    rest_silence_early_block = False
    rest_silence_early_text = ""
    async with self._data_lock:
        users = self.data.get("users") if isinstance(self.data.get("users"), dict) else {}
        existing_user = users.get(user_id) if isinstance(users, dict) else None
        is_target_user = self._private_passive_profile_available(
            user_id,
            existing_user if isinstance(existing_user, dict) else None,
        )
        if not is_target_user:
            logger.info(
                "[PrivateCompanion] 非目标/未启用私聊不记录陪伴资料: user=%s text=%s",
                _single_line(user_id, 80),
                _single_line(text, 120),
            )
            return
        user = self._get_user(user_id)
        if self._is_recent_poke_echo(user, text):
            logger.info("[PrivateCompanion] 忽略 poke 回流事件,不计入用户新消息: %s", user_id)
            return
        if self._is_duplicate_inbound_message(event, scope=f"private:{user_id}", sender_id=user_id, text=text):
            self._schedule_data_save(sections={"inbound_debounce_stats"})
            self._record_passive_no_reply(
                event,
                source="私聊去重",
                reason="重复私聊事件被忽略",
                detail=text,
                level="info",
            )
            event.stop_event()
            return
        smart_debounce_state_changed = False
        if is_target_user and text and not forward_only_prompt and not reference_media_with_text:
            smart_debounce_state_changed = bool(self._maybe_record_smart_message_debounce_followup(
                scope=f"private:{user_id}",
                sender_id=user_id,
                text=text,
                now=received_ts,
            ))
        private_image_enhancement_enabled = (
            self._feature_enabled_or_temp_unlocked("enable_private_image_self_recognition")
            and bool(_persona_value(self, 'enable_message_debounce', _persona_value(self, 'enable_semantic_message_debounce', True)))
            and self._message_debounce_seconds("image") > 0
        )
        private_image_only = (
            is_target_user
            and private_image_enhancement_enabled
            and self._is_private_image_only_message(event, text)
        )
        if (
            is_target_user
            and text
            and not forward_only_prompt
            and private_image_enhancement_enabled
            and not private_image_only
            and self._private_event_has_image_safe(event, label="private_text_image")
        ):
            try:
                async with self._temporarily_release_data_lock():
                    persisted_images = await self._persist_private_inbound_images(event, user_id)
                usable_images = [source for source in persisted_images if self._private_image_source_to_model_url(source)]
            except Exception as exc:
                missing = _missing_optional_model_dependency(exc)
                if not missing:
                    raise
                logger.warning(
                    "[PrivateCompanion] 私聊图文图片预处理缺少可选模型依赖，已按纯文本继续: user=%s module=%s err=%s",
                    user_id,
                    missing,
                    _single_line(exc, 160),
                )
                persisted_images = []
                usable_images = []
            if usable_images:
                umo = str(getattr(event, "unified_msg_origin", "") or "")
                try:
                    has_visual_provider = self._has_private_image_visual_provider(umo)
                except Exception as exc:
                    missing = _missing_optional_model_dependency(exc)
                    if not missing:
                        raise
                    logger.warning(
                        "[PrivateCompanion] 私聊图文视觉 provider 检测缺少可选模型依赖，已关闭本轮识图: user=%s module=%s err=%s",
                        user_id,
                        missing,
                        _single_line(exc, 160),
                    )
                    has_visual_provider = False
                setattr(event, "private_companion_delayed_image_sources", usable_images[:5])
                has_dynamic_gif_sources = (
                    bool(_persona_value(self, 'enable_private_image_gif_enhancement', True))
                    and self._private_image_sources_include_gif(usable_images)
                )
                image_mode = self._private_image_delivery_mode(
                    has_visual_provider=has_visual_provider,
                    main_provider_supports_image=self._event_main_provider_supports_image(event),
                    has_dynamic_gif=has_dynamic_gif_sources,
                )
                setattr(event, "private_companion_delayed_image_mode", image_mode)
                if image_mode == "caption":
                    try:
                        async with self._temporarily_release_data_lock():
                            vision_text = _single_line(
                                await self._transcribe_private_inbound_images(
                                    usable_images[:5],
                                    umo=umo,
                                    user_text=text,
                                    force_contextual=self._private_image_user_mentions_combo_result(text) or self._private_image_user_has_specific_vision_request(text),
                                ),
                                self._private_image_vision_text_limit(len(usable_images)),
                            )
                    except Exception as exc:
                        missing = _missing_optional_model_dependency(exc)
                        if not missing:
                            raise
                        logger.warning(
                            "[PrivateCompanion] 私聊图文视觉摘要缺少可选模型依赖，已按无视觉摘要继续: user=%s module=%s err=%s",
                            user_id,
                            missing,
                            _single_line(exc, 160),
                        )
                        vision_text = ""
                    if vision_text:
                        setattr(event, "private_companion_delayed_image_vision_text", vision_text)
                logger.info(
                    "[PrivateCompanion] 私聊文本图片混合消息已接入图片上下文: user=%s images=%s mode=%s gif=%s combo=%s vision=%s text=%s",
                    user_id,
                    len(usable_images),
                    image_mode,
                    has_dynamic_gif_sources,
                    self._private_image_user_mentions_combo_result(text),
                    bool(_single_line(getattr(event, "private_companion_delayed_image_vision_text", ""), 80)),
                    _single_line(text, 80),
                )
            else:
                logger.info(
                    "[PrivateCompanion] 私聊文本图片混合消息未解析到可用图片源: user=%s sources=%s text=%s",
                    user_id,
                    len(persisted_images),
                    _single_line(text, 80),
                )
        if is_target_user and forward_only_prompt:
            key = self._semantic_buffer_key(f"private:{user_id}", user_id)
            if self._note_semantic_message_buffer(
                key,
                text,
                now=received_ts,
                wait_seconds=self._message_debounce_seconds("forward"),
                kind="forward",
            ):
                if smart_debounce_state_changed:
                    self._schedule_data_save(sections={"smart_message_debounce"})
                event.stop_event()
                return
        if private_image_only:
            setattr(event, "private_companion_deferred_private_image_only", True)
            key = self._semantic_buffer_key(f"private:{user_id}", user_id)
            self._note_semantic_message_buffer(
                key,
                "用户刚刚先单独发送了一张图片,可能马上会补充说明。",
                now=received_ts,
                wait_seconds=self._message_debounce_seconds("image"),
                kind="image",
            )
            buffers = getattr(self, "_semantic_message_buffers", None)
            if isinstance(buffers, dict) and isinstance(buffers.get(key), dict):
                async with self._temporarily_release_data_lock():
                    persisted_images = await self._persist_private_inbound_images(event, user_id)
                has_model_usable_image = any(self._private_image_source_to_model_url(source) for source in persisted_images)
                if not persisted_images:
                    buffers.pop(key, None)
                    setattr(event, "private_companion_deferred_private_image_only", False)
                    logger.info(
                        "[PrivateCompanion] 私聊单图未解析到可用图片源,放行原始事件: user=%s sources=%s",
                        user_id,
                        len(persisted_images),
                    )
                    if smart_debounce_state_changed:
                        self._schedule_data_save(sections={"smart_message_debounce"})
                    return
                if not has_model_usable_image:
                    logger.info(
                        "[PrivateCompanion] 私聊单图已保存但不可直供模型,仍进入防抖等待补充: user=%s sources=%s",
                        user_id,
                        len(persisted_images),
                    )
                buffers[key]["images"] = persisted_images
                buffers[key]["original_event"] = event
                has_dynamic_gif_sources = (
                    bool(_persona_value(self, 'enable_private_image_gif_enhancement', True))
                    and self._private_image_sources_include_gif(persisted_images)
                )
                umo = str(getattr(event, "unified_msg_origin", "") or "")
                has_visual_provider = self._has_private_image_visual_provider(umo)
                image_mode = self._private_image_delivery_mode(
                    has_visual_provider=has_visual_provider,
                    main_provider_supports_image=bool(persisted_images) and self._event_main_provider_supports_image(event),
                    has_dynamic_gif=has_dynamic_gif_sources,
                )
                buffers[key]["image_mode"] = image_mode
                if persisted_images and image_mode == "caption":
                    buffers[key]["vision_task"] = self._create_lifecycle_background_task(
                        self._transcribe_private_inbound_images(
                            persisted_images,
                            umo=umo,
                        ),
                        label="private_image_debounce_vision",
                    )
                logger.info(
                    "[PrivateCompanion] 私聊单图已进入防抖缓冲: user=%s images=%s mode=%s vision=%s",
                    user_id,
                    len(persisted_images),
                    image_mode,
                    bool(persisted_images) and image_mode == "caption",
                )
                self._create_lifecycle_background_task(
                    self._finalize_private_image_buffer_after_wait(key, user_id, received_ts),
                    label="private_image_debounce_finalize",
                )
                if smart_debounce_state_changed:
                    self._schedule_data_save(sections={"smart_message_debounce"})
            event.stop_event()
            return
        elif is_target_user and not forward_only_prompt and not reference_media_with_text:
            pending_debounce_merge = False
            pending_absorber = getattr(self, "_message_debounce_absorb_pending_message", None)
            if callable(pending_absorber):
                pending_debounce_merge = bool(pending_absorber(event, text))
            key = self._semantic_buffer_key(f"private:{user_id}", user_id)
            buffers = getattr(self, "_semantic_message_buffers", None)
            existing_buffer = buffers.get(key) if isinstance(buffers, dict) else None
            buffered_images = (
                isinstance(existing_buffer, dict)
                and isinstance(existing_buffer.get("images"), list)
                and bool(existing_buffer.get("images"))
                and _now_ts() - _safe_float(existing_buffer.get("first_ts"), 0) <= max(
                    45.0,
                    self._message_debounce_seconds("image") + 30.0,
                )
            )
            if buffered_images:
                messages = existing_buffer.setdefault("messages", [])
                if not isinstance(messages, list):
                    messages = []
                    existing_buffer["messages"] = messages
                cleaned_text = _single_line(text, 260)
                if cleaned_text and cleaned_text not in [_single_line(item.get("text"), 260) for item in messages if isinstance(item, dict)]:
                    messages.append({"ts": _now_ts(), "text": cleaned_text, "sender_name": ""})
                existing_buffer["updated_ts"] = _now_ts()
                if _safe_float(existing_buffer.get("deadline_ts"), 0.0) <= 0:
                    first_ts = _safe_float(existing_buffer.get("first_ts"), received_ts, received_ts)
                    existing_buffer["deadline_ts"] = first_ts + self._message_debounce_seconds("image")
                logger.info(
                    "[PrivateCompanion] 消息收口合并补话: kind=image mode=fixed scope=private:%s sender=%s wait=%.1fs count=%s text=%s",
                    user_id,
                    user_id,
                    self._message_debounce_seconds("image"),
                    len(messages),
                    _single_line(cleaned_text, 80),
                )
            elif not pending_debounce_merge:
                try:
                    async with self._temporarily_release_data_lock():
                        smart_wait = await self._smart_message_debounce_wait_seconds_for_event(
                            event,
                            key=key,
                            text=text,
                            sender_id=user_id,
                            sender_name=sender_display_name,
                            private_chat=True,
                        )
                except Exception as exc:
                    missing = _missing_optional_model_dependency(exc)
                    if not missing:
                        raise
                    logger.warning(
                        "[PrivateCompanion] 私聊智能收口模型缺少可选依赖，已回退固定等待: user=%s module=%s err=%s",
                        user_id,
                        missing,
                        _single_line(exc, 160),
                    )
                    smart_wait = self._message_debounce_seconds("text")
                    try:
                        setattr(event, "private_companion_smart_message_debounce_result", {"decision": "fixed", "confidence": 0.0, "reason": f"missing {missing}"})
                    except Exception:
                        pass
                smart_result = getattr(event, "private_companion_smart_message_debounce_result", None)
                smart_decision = str(smart_result.get("decision") or "") if isinstance(smart_result, dict) else ""
                smart_handled = smart_decision in {"complete", "incomplete"}
                smart_debounce_state_changed = smart_debounce_state_changed or smart_handled
                wait_seconds = smart_wait if smart_handled else self._message_debounce_seconds("text")
                if self._note_semantic_message_buffer(
                    key,
                    text,
                    wait_seconds=wait_seconds,
                    smart_debounce={"enabled": smart_handled, "decision": smart_decision or "fixed"},
                    kind="text",
                ):
                    if smart_debounce_state_changed:
                        self._schedule_data_save(sections={"smart_message_debounce"})
                    event.stop_event()
                    return
        user["umo"] = event.unified_msg_origin
        self._note_private_display_name_observation(user, user_id, sender_display_name, now=received_ts)
        if not is_target_user:
            user["enabled"] = False
            self._clear_pending_proactive_plan(user)
        user["last_seen"] = _now_ts()
        user["last_activity_at"] = received_ts or _now_ts()
        self._note_private_inbound_activity(user, received_ts or _now_ts(), text=text)
        self._mark_greetings_satisfied_by_recent_activity(user, activity_ts=received_ts or _now_ts())
        self._note_morning_greeting_reply(user, now=received_ts or _now_ts())
        private_memory_managed = False
        private_memory_revision = None
        # Feedback is only evaluated for textual inbound messages, but the
        # final persistence section is shared by all private message types.
        expression_feedback: dict[str, Any] = {}
        if text:
            user["inbound_count"] = _safe_int(user.get("inbound_count"), 0) + 1
        self._apply_relationship_event(
            user,
            1,
            reason_code="inbound",
            event_id=self._event_message_id(event),
            now=received_ts,
        )
        suspended = user.get("suspended_proactive")
        if (
            isinstance(suspended, dict)
            and suspended.get("active")
            and _now_ts() - _safe_float(suspended.get("created_at"), 0) <= _persona_value(self, 'proactive_reply_context_hours', 12) * 3600
        ):
            suspended["resume_ready"] = True
            suspended["complaint_enabled"] = False
            suspended["complaint_sent"] = True
            suspended["second_followup"] = {}
            user["pending_followup_event"] = {}
            user["planned_proactive_quota_exempt"] = False
        if _safe_float(user.get("awaiting_reply_since"), 0) > 0:
            audit_outcome_recorder = getattr(self, "_mark_proactive_audit_reply_outcome", None)
            if callable(audit_outcome_recorder):
                audit_outcome_recorder(
                    user,
                    received_at=received_ts,
                    message_id=self._event_message_id(event),
                )
            user["reply_count"] = _safe_int(user.get("reply_count"), 0) + 1
            self._note_action_reply_feedback(
                user,
                str(user.get("last_proactive_action") or "message"),
                text,
            )
            self._apply_relationship_event(
                user,
                2,
                reason_code="proactive_reply",
                event_id=self._event_message_id(event),
                now=received_ts,
            )
            user["awaiting_reply_since"] = 0
            user["last_reply_at"] = _now_ts()
            user["last_private_reply_at"] = user["last_reply_at"]
            user["pending_followup_event"] = {}
            user["planned_proactive_quota_exempt"] = False
        user["ignored_streak"] = 0
        user["friend_unanswered_silenced_since"] = 0
        user["friend_unanswered_silence_note"] = ""
        if text:
            safe_text = self._sanitize_orphan_tts_placeholders(text)
            user["last_user_message"] = safe_text or text
            user["last_user_message_at"] = received_ts
            self._note_user_chronotype_from_inbound(user, safe_text or text, received_ts)
            if is_target_user and self._clear_state_share_proactive_after_user_status_question(user, user_id=user_id, text=safe_text or text, now=received_ts):
                if not self._simulation_active(user) and _safe_float(user.get("next_proactive_at"), 0) <= 0:
                    self._schedule_next_proactive(user, now=received_ts)
            rest_silence_applied = self._apply_user_rest_silence_from_message(user, safe_text or text, now=received_ts)
            if rest_silence_applied and _safe_float(user.get("user_rest_until"), 0) > received_ts:
                if self._user_rest_signal_should_block_current_reply(safe_text or text):
                    rest_silence_early_block = bool(is_target_user)
                    rest_silence_early_text = safe_text or text
            self._apply_private_image_vision_negative_feedback(user, safe_text or text)
            reaction_expression_feedback = self._apply_reaction_expression_feedback(
                user,
                safe_text or text,
                scope_key=self._reaction_expression_scope_key(event, user_id),
            )
            if reaction_expression_feedback:
                self._log_reaction_expression_event(
                    event,
                    stage="feedback",
                    decision="recorded",
                    reason="feedback_private",
                    scope="private",
                    image_id=reaction_expression_feedback.get("image_id"),
                    feedback_signal=reaction_expression_feedback.get("signal"),
                    feedback_score=reaction_expression_feedback.get("score"),
                )
            expression_feedback = self._apply_expression_rule_feedback(
                user,
                safe_text or text,
                channel="private",
            )
            if expression_feedback:
                logger.info(
                    "[PrivateCompanion] 表达规则收到用户反馈: user=%s signal=%s updated=%s demoted=%s",
                    user_id,
                    _single_line(expression_feedback.get("signal"), 16),
                    _safe_int(expression_feedback.get("updated_rules"), 0, 0),
                    _safe_int(expression_feedback.get("demoted_rules"), 0, 0),
                )
            private_memory_write_allowed = self._req041_private_memory_write_allowed(user)
            private_memory_managed = self._req041_private_memory_managed()
            private_memory_revision = (
                self._req041_prepare_authoritative_private_memory(user)
                if private_memory_write_allowed and private_memory_managed else None
            )
            if private_memory_write_allowed and private_memory_managed and private_memory_revision is None:
                private_memory_write_allowed = False
            if private_memory_write_allowed and is_target_user:
                observer = getattr(self, "_agenda_observe_calendar_message", None)
                if callable(observer):
                    try:
                        calendar_observation_result = observer(
                            text=safe_text or text,
                            event_time=received_ts,
                            source_ref=self._event_message_id(event) or f"{str(getattr(event, 'unified_msg_origin', '') or '')}:{received_ts}",
                            conversation_id=str(getattr(event, "unified_msg_origin", "") or ""),
                            source_user_id=user_id,
                            target_user_id=user_id,
                            subject_actor_id=str(getattr(self, "bot_personal_subject", "") or "bot_self"),
                        ) or {}
                    except Exception as exc:
                        logger.warning(
                            "[PrivateCompanion] 日历候选观察失败，已放行当前回复: user=%s error=%s",
                            user_id,
                            _single_line(exc, 160),
                        )
            if private_memory_write_allowed:
                user["episode_message_count"] = _safe_int(user.get("episode_message_count"), 0, 0) + 1
            if self._expression_private_learning_source_enabled(user, user_id):
                self._update_expression_profile_from_message(user, safe_text or text)
                self._refresh_expression_voice_profile()
            if private_memory_write_allowed:
                self._update_companion_memory_from_message(user, safe_text or text)
                self._update_open_loops_from_message(user, safe_text or text)
                self._update_action_preferences_from_message(user, safe_text or text)
                self._update_user_behavior_habits_from_message(user, safe_text or text)
                if private_memory_managed:
                    self._req041_commit_authoritative_private_memory(
                        user,
                        expected_revision=private_memory_revision,
                        operation_id="req041-private-message:" + (
                            self._event_message_id(event) or uuid.uuid4().hex
                        ),
                    )
            if (
                not rest_silence_early_block
                and (
                    _persona_value(self, 'enable_intent_emotion_analysis', True)
                    or _persona_value(self, 'enable_relationship_state_machine', True)
                    or _persona_value(self, 'enable_emotion_simulation', True)
                )
            ):
                intent_profile = self._analyze_inbound_intent(text)
                boundary_enricher = getattr(self, "_enrich_boundary_feedback_intent", None)
                if callable(boundary_enricher):
                    intent_profile = boundary_enricher(user, intent_profile)
                violation_settler = getattr(self, "_apply_relationship_violation_policy", None)
                if callable(violation_settler):
                    violation_settler(
                        user,
                        intent_profile,
                        event_id=self._event_message_id(event),
                        now=received_ts,
                    )
                if _persona_value(self, 'enable_intent_emotion_analysis', True):
                    user["intent_profile"] = intent_profile
                if self._should_use_llm_emotion_judgement(text, intent_profile):
                    # Model review does not block the current passive reply; it keeps using cached emotion state.
                    observed_event = self._record_interaction_emotion_event(
                        user,
                        intent_profile,
                        band=str(current_interaction_projection(
                            user.get("current_interaction"),
                            relationship_role=self._private_user_role(user, user_id),
                            relationship_mode=user.get("relationship_mode", "normal"),
                            now=_now_ts(),
                        ).get("expression_band") or "relaxed"),
                        reason_code="target_review_pending",
                        status="observed",
                    )
                    emotion_review_id = uuid.uuid4().hex
                    user["pending_emotion_judgement"] = {
                        "review_id": emotion_review_id,
                        "message_event_id": self._event_message_id(event),
                        "text": _single_line(text, 240),
                        "created_at": _now_ts(),
                        "local": deepcopy(intent_profile),
                        "observed_event": observed_event or {},
                    }
                    self._create_lifecycle_background_task(
                        self._refine_inbound_emotion_with_model(
                            user_id,
                            text,
                            deepcopy(intent_profile),
                            review_id=emotion_review_id,
                        ),
                        label="inbound_emotion_refine",
                    )
                else:
                    self._update_relationship_state_from_intent(user, intent_profile)
            if is_target_user and self._cancel_inbound_conflicting_greeting(
                user,
                now=_now_ts(),
                user_id=user_id,
                trigger_umo=str(getattr(event, "unified_msg_origin", "") or ""),
            ):
                logger.info("[PrivateCompanion] 用户已在当前问候时段自然来聊,已请求取消冲突问候候选: %s", user_id)
                if not self._simulation_active(user) and _safe_float(user.get("next_proactive_at"), 0) <= 0:
                    self._schedule_next_proactive(user, now=_now_ts())
        user_is_owner = self._private_user_role(user, user_id) == "owner"
        food_feedback = self._detect_food_feedback(text) if text else {"is_food": False}
        food_feedback_detected = bool(text) and user_is_owner and bool(
            food_feedback.get("is_food") and food_feedback.get("actionable")
        )
        food_feedback_applied = food_feedback_detected and self._apply_food_feedback_to_state(text)
        used_food_items: list[str] = []
        meal_care_result: dict[str, Any] = {}
        food_feedback_actionable = bool(
            food_feedback.get("actionable")
            or food_feedback.get("feeding")
            or food_feedback.get("bot_directed")
        )
        if user_is_owner and food_feedback_actionable:
            user["last_food_feedback_at"] = _now_ts()
            user["last_food_feedback_text"] = _single_line(text, 120)
        active_meal_care = bool(self._meal_care_active_context(user, now=received_ts)) if user_is_owner else False
        if food_feedback.get("is_food") and not active_meal_care:
            used_food_items = self._mark_food_menu_item_used_from_text(text) if user_is_owner else []
            if used_food_items:
                user["last_food_menu_choice"] = {
                    "ts": _now_ts(),
                    "items": used_food_items,
                    "text": _single_line(text, 120),
                }
        if user_is_owner and text:
            meal_care_result = self._handle_meal_care_inbound(user, safe_text or text, now=received_ts)
            if meal_care_result.get("foods"):
                user["last_food_menu_choice"] = {
                    "ts": _now_ts(),
                    "items": list(meal_care_result.get("foods") or []),
                    "text": _single_line(text, 120),
                    "source": "meal_care_reply",
                }
        care_feedback = self._detect_care_feedback(text) if text else {"is_care": False}
        care_feedback_detected = bool(text) and user_is_owner and bool(care_feedback.get("is_care"))
        care_feedback_applied = care_feedback_detected and self._apply_care_feedback_to_state(text)
        if care_feedback_applied:
            self._apply_relationship_event(
                user,
                2,
                reason_code="care_feedback",
                event_id=self._event_message_id(event),
                now=received_ts,
            )
        interaction_warmth_applied = (
            bool(_persona_value(self, 'enable_custom_relationship_stage_policy', False))
            and bool(text)
            and is_target_user
            and user_is_owner
            and self._apply_interaction_warmth_to_state(text, user)
        )
        if interaction_warmth_applied:
            self._apply_relationship_event(
                user,
                1,
                reason_code="interaction_warmth",
                event_id=self._event_message_id(event),
                now=received_ts,
            )
        schedule_adjustment_applied = (
            bool(text)
            and is_target_user
            and user_is_owner
            and self._record_schedule_adjustment_from_interaction(text, user)
        )
        if schedule_adjustment_applied:
            self._apply_relationship_event(
                user,
                1,
                reason_code="schedule_adjustment",
                event_id=self._event_message_id(event),
                now=received_ts,
            )
        if food_feedback_applied:
            self._apply_relationship_event(
                user,
                1,
                reason_code="food_feedback",
                event_id=self._event_message_id(event),
                now=received_ts,
            )

        response = ""
        if is_target_user:
            try:
                read_view_getter = getattr(self, "_req041_relationship_read_view", None)
                relationship_read_user = (
                    read_view_getter(event, user, kind="private")
                    if callable(read_view_getter) else user
                )
                scoped_read_getter = getattr(self, "_req041_scoped_private_read_view", None)
                if callable(scoped_read_getter):
                    relationship_read_user = scoped_read_getter(event, relationship_read_user)
                self._memory_companion_attach_private_context(
                    event,
                    user_id=user_id,
                    user=relationship_read_user,
                    text=(safe_text if text else "") or text,
                )
            except Exception as exc:
                missing = _missing_optional_model_dependency(exc)
                if not missing:
                    raise
                logger.warning(
                    "[PrivateCompanion] 私聊记忆上下文挂载缺少可选模型依赖，已跳过记忆增强: user=%s module=%s err=%s",
                    user_id,
                    missing,
                    _single_line(exc, 160),
                )
        save_sections = {"users"}
        if private_memory_managed and private_memory_revision is not None:
            save_sections.add("_req041_private_memory")
        save_sections.update(calendar_observation_result.get("changed_sections") or ())
        if expression_feedback:
            save_sections.update(expression_feedback.get("updated_sections") or ())
            if _safe_int(expression_feedback.get("updated_rules"), 0, 0) > 0:
                save_sections.add("expression_voice_profile")
        if self._expression_private_learning_source_enabled(user, user_id):
            save_sections.add("expression_voice_profile")
        if smart_debounce_state_changed:
            save_sections.add("smart_message_debounce")
        if food_feedback_detected:
            save_sections.update(
                {
                    "last_food_state_feedback_at",
                    "last_food_state_feedback_text",
                }
            )
        if food_feedback_applied:
            save_sections.update(
                {
                    "state_conditions",
                    "daily_state",
                }
            )
        if care_feedback_detected:
            save_sections.add("state_conditions")
        if interaction_warmth_applied:
            save_sections.update({"state_conditions", "daily_state"})
        if used_food_items:
            save_sections.add("food_menu")
        if meal_care_result.get("foods"):
            save_sections.add("food_menu")
        if schedule_adjustment_applied:
            save_sections.update(
                {
                    "schedule_adjustments",
                    "dialogue_outfit_override",
                    "detail_enhanced_segments",
                    "daily_plan",
                    "daily_story_plan",
                    "daily_state",
                }
            )
            if isinstance(self.data.get("daily_state"), dict) and isinstance(
                self.data["daily_state"].get("sleep_runtime"), dict
            ):
                save_sections.add("daily_state")
        self._schedule_data_save(sections=save_sections)
        user_snapshot = dict(user)

    if not is_target_user:
        logger.info(
            "[PrivateCompanion] 非目标/未启用私聊放行默认主链: user=%s text=%s",
            _single_line(user_id, 80),
            _single_line(text, 120),
        )
        return
    if is_target_user and rest_silence_early_block:
        self._stop_private_reply_after_user_rest_signal(event, user_id, rest_silence_early_text or text)
        return
    if is_target_user and schedule_adjustment_applied:
        self._create_lifecycle_background_task(
            self._kick_proactive_loop_once(),
            label="kick_proactive_loop_inbound",
        )
    if response:
        await self._reply(event, response)
        event.stop_event()
    elif is_target_user:
        pass
    if is_target_user:
        self._create_lifecycle_background_task(
            self._maybe_refresh_companion_memory(user_id, user_snapshot),
            label="refresh_companion_memory_inbound",
        )
        self._create_lifecycle_background_task(
            self._maybe_refresh_dialogue_episode(user_id, user_snapshot),
            label="refresh_dialogue_episode_inbound",
        )

@_coalesce_event_data_saves
async def handle_group_message(self: Any, event: Any, *args: Any, **kwargs: Any) -> Any:
    """观察群聊消息，维护群上下文并判断是否自然唤醒 Bot。"""
    if self is None:
        return
    if self._is_onebot_poke_notice_event(event):
        # 同样避免群聊观察链将戳一戳误作空消息或普通上下文。
        logger.debug("[PrivateCompanion] 群聊戳一戳 notice 已放行给专用插件")
        return
    self._qzone_note_event_bot(event)
    if not _persona_feature_enabled(self, "enable_group_companion"):
        return
    group_id = self._extract_group_id_from_event(event)
    if not group_id or not self._group_enabled_for_event(group_id):
        return
    try:
        sender_id = str(event.get_sender_id())
    except Exception:
        sender_id = ""
    self_id = self._event_self_id(event)
    if sender_id and self_id and sender_id == self_id:
        logger.info(
            "[PrivateCompanion] 已终止 Bot 自己的群聊回流事件: group=%s self=%s text=%s",
            group_id,
            self_id,
            _single_line(getattr(event, "message_str", ""), 80),
        )
        event.stop_event()
        return
    received_ts = _now_ts()
    text = self._group_observation_event_text(event)
    if not text:
        return
    if text.startswith(("陪伴群", "/陪伴群", "群陪伴", "群聊陪伴")):
        return
    if self._message_debounce_command_text(event, text):
        return
    sender_name = self._sender_display_name(event)
    reaction_expression_feedback = {}
    reaction_feedback_lock = getattr(self, "_data_lock", None)
    if sender_id and reaction_feedback_lock is not None:
        # The image tool stores its group target in the group reaction
        # state and scopes it by the exact group UMO. Apply feedback before any
        # early-return branch so existing reply handlers cannot swallow it.
        async with reaction_feedback_lock:
            group_user = self._reaction_expression_feedback_user(
                sender_id,
                text,
                create_for_opt_out=True,
                event=event,
            )
            if isinstance(group_user, dict):
                reaction_expression_feedback = self._apply_reaction_expression_feedback(
                    group_user,
                    text,
                    scope_key=self._reaction_expression_scope_key(event, sender_id),
                )
                if reaction_expression_feedback:
                    self._persist_reaction_expression_state(
                        sections={"reaction_expression_group_states"}
                    )
    if reaction_expression_feedback:
        self._log_reaction_expression_event(
            event,
            stage="feedback",
            decision="recorded",
            reason="feedback_group",
            scope="group",
            image_id=reaction_expression_feedback.get("image_id"),
            feedback_signal=reaction_expression_feedback.get("signal"),
            feedback_score=reaction_expression_feedback.get("score"),
        )
    await self._capture_group_observation_event(
        event,
        group_id=group_id,
        sender_id=sender_id,
        sender_name=sender_name,
        text=text,
    )
    self._start_group_image_understanding(
        event,
        group_id=group_id,
        sender_id=sender_id,
        text=text,
    )
    existing_reply_preview = self._event_existing_reply_result_preview(event)
    if self._proactive_only_blocks_passive_event(event, "group_event_pipeline"):
        logger.debug("[PrivateCompanion] 主动消息专用模式已保留群聊观察,跳过回复增强")
        return
    if existing_reply_preview:
        async with self._data_lock:
            if self._is_duplicate_inbound_message(event, scope=f"group:{group_id}", sender_id=sender_id, text=text):
                self._save_data_sync(sections={"inbound_debounce_stats"})
                return
            group = self._get_group(group_id)
            group["umo"] = _single_line(getattr(event, "unified_msg_origin", ""), 160)
            scene = self._infer_group_scene(event, group, sender_id=sender_id, sender_name=sender_name, text=text)
            self._capture_group_observation_once(
                group,
                sender_id=sender_id,
                sender_name=sender_name,
                text=text,
                group_id=group_id,
                scene=scene,
                message_id=self._event_message_id(event),
                event=event,
            )
            self._save_data_sync(sections={"groups"})
            group_snapshot = deepcopy(group)
        logger.info(
            "[PrivateCompanion] 已有其他链路回复,仅记录群聊观察: group=%s sender=%s text=%s result=%s",
            group_id,
            sender_id,
            _single_line(text, 80),
            _single_line(existing_reply_preview, 120),
        )
        self._create_lifecycle_background_task(
            self._maybe_refresh_group_episode(group_id, group_snapshot),
            label="refresh_group_episode_existing_reply",
        )
        self._create_lifecycle_background_task(
            self._maybe_refresh_group_slang_meanings(group_id, group_snapshot),
            label="refresh_group_slang_existing_reply",
        )
        return
    if self._group_llm_reply_blocked(group_id):
        logger.debug(
            "[PrivateCompanion] 本群 LLM 回复已关闭,已保留观察并跳过回复增强: group=%s text=%s",
            group_id,
            _single_line(text, 80),
        )
        return
    try:
        signals = self._event_scene_signals(event)
    except Exception:
        signals = {}
    at_bot = any(
        isinstance(item, dict) and bool(item.get("is_bot"))
        for item in (signals.get("at_targets") if isinstance(signals, dict) else []) or []
    )
    reply_to_bot = bool(
        isinstance(signals, dict)
        and _single_line(signals.get("self_id"), 80)
        and _single_line(signals.get("reply_to_id"), 80) == _single_line(signals.get("self_id"), 80)
    )
    quoted_link_payload = False
    if not at_bot and not reply_to_bot:
        try:
            quoted_link_payload = await self._event_reply_contains_link_payload(event)
        except Exception as exc:
            logger.debug("[PrivateCompanion] 群聊引用链接守卫读取失败: %s", _single_line(exc, 120))
        if quoted_link_payload:
            setattr(event, "private_companion_group_quoted_link_payload", True)
    current_link_payload = _group_link_message_context(text, limit=260)[1]
    if (current_link_payload or quoted_link_payload) and not at_bot and not reply_to_bot:
        # Some adapters mark any reply/share card as a wake event before
        # plugins inspect its real target.  Clear that provisional state;
        # an actual Bot-name/custom-word/continuation match below can set
        # it again deliberately.
        try:
            setattr(event, "is_at_or_wake_command", False)
            setattr(event, "is_wake", False)
        except Exception:
            pass
    if (at_bot or reply_to_bot) and await self._maybe_handle_natural_language_photo_request(event, sender_id, text, directed=True):
        return
    image_wakeup: dict[str, Any] = {}
    image_wakeup_getter = getattr(self, "_maybe_group_image_wakeup", None)
    if callable(image_wakeup_getter):
        image_wakeup = await image_wakeup_getter(event, sender_id=sender_id)
    registration_payload = None
    continuation: bool | None = False
    resting_mention_notice = ""
    scene: dict[str, Any] = {}
    wakeup_state_effect: dict[str, Any] = {}
    group_for_judge: dict[str, Any] = {}
    active_for_judge: dict[str, Any] = {}
    high_intensity_state: dict[str, Any] = {}
    group_snapshot_high_intensity: dict[str, Any] = {}
    async with self._data_lock:
        if self._is_duplicate_inbound_message(event, scope=f"group:{group_id}", sender_id=sender_id, text=text):
            self._save_data_sync(sections={"inbound_debounce_stats"})
            event.stop_event()
            return
        group = self._get_group(group_id)
        if sender_id:
            users = self.data.get("users", {})
            resolver = getattr(self, "_private_user_id_for_event", None)
            scoped_sender_id = (
                resolver(event, sender_id)
                if callable(resolver)
                else self._canonical_private_user_id(sender_id)
            )
            current_sender = users.get(scoped_sender_id) if isinstance(users, dict) else None
            boundary_profile_known = bool(
                isinstance(current_sender, dict)
                and any(
                    key in current_sender
                    for key in ("relationship_role", "manual_enabled", "enabled", "umo", "relationship_score")
                )
            )
            if (at_bot or reply_to_bot) and boundary_profile_known and bool(
                _persona_value(self, 'enable_relationship_boundary_feedback', True)
            ):
                current_sender.setdefault("user_id", scoped_sender_id)
                group_boundary_intent = self._analyze_inbound_intent(text)
                group_boundary_intent["boundary_scope"] = "group"
                group_boundary_intent["boundary_group_id"] = group_id
                boundary_enricher = getattr(self, "_enrich_boundary_feedback_intent", None)
                if callable(boundary_enricher):
                    group_boundary_intent = boundary_enricher(current_sender, group_boundary_intent)
                violation_settler = getattr(self, "_apply_relationship_violation_policy", None)
                if callable(violation_settler):
                    violation_settler(
                        current_sender,
                        group_boundary_intent,
                        event_id=self._event_message_id(event),
                        now=received_ts,
                    )
                if self._should_use_llm_emotion_judgement(text, group_boundary_intent):
                    review_id = uuid.uuid4().hex
                    current_sender["pending_emotion_judgement"] = {
                        "review_id": review_id,
                        "message_event_id": self._event_message_id(event),
                        "text": _single_line(text, 240),
                        "created_at": _now_ts(),
                        "local": deepcopy(group_boundary_intent),
                        "scope": "group",
                        "group_id": group_id,
                    }
                    self._create_lifecycle_background_task(
                        self._refine_inbound_emotion_with_model(
                            scoped_sender_id,
                            text,
                            deepcopy(group_boundary_intent),
                            review_id=review_id,
                        ),
                        label="group_boundary_emotion_refine",
                    )
            if scoped_sender_id in set(self._configured_target_ids()) or (
                isinstance(current_sender, dict) and bool(current_sender.get("manual_enabled"))
            ):
                target_user = self._get_user(scoped_sender_id)
                target_user["last_activity_at"] = received_ts
                self._mark_greetings_satisfied_by_recent_activity(target_user, activity_ts=received_ts)
                if self._cancel_inbound_conflicting_greeting(
                    target_user,
                    now=received_ts,
                    user_id=scoped_sender_id,
                    trigger_umo=str(getattr(event, "unified_msg_origin", "") or ""),
                ):
                    logger.info("[PrivateCompanion] 目标用户已在群内交流,已请求取消冲突问候候选: group=%s user=%s", group_id, scoped_sender_id)
                    if not self._simulation_active(target_user) and _safe_float(target_user.get("next_proactive_at"), 0) <= 0:
                        self._schedule_next_proactive(target_user, now=received_ts)
                self._maybe_schedule_post_goodnight_group_activity(
                    group_id,
                    group,
                    sender_id=sender_id,
                    sender_name=sender_name,
                    text=text,
                    now=received_ts,
                )
                self._maybe_schedule_group_ignore_complaint(
                    group_id,
                    group,
                    sender_id=sender_id,
                    sender_name=sender_name,
                    text=text,
                    now=received_ts,
                )
        group["umo"] = _single_line(getattr(event, "unified_msg_origin", ""), 160)
        _, resting_mention_notice = self._group_resting_mention_notice(
            event,
            group,
            sender_id=sender_id,
            now=received_ts,
        )
        if resting_mention_notice:
            self._save_data_sync(sections={"groups"})
        scene = self._infer_group_scene(event, group, sender_id=sender_id, sender_name=sender_name, text=text)
        if quoted_link_payload:
            scene["quoted_link_payload"] = True
        if resting_mention_notice:
            continuation = True
            scene.update(
                {
                    "trigger": "group_wakeup_resting_mention",
                    "talking_to": "bot",
                    "talking_to_name": "你",
                    "reason": "mentioned_resting_user",
                    "wakeup_word": "@休息用户",
                    "wakeup_strength": "strong",
                    "wakeup_strength_label": "明确需要你接话",
                    "wakeup_instruction": (
                        f"群友刚刚 @ 了一个已明确在休息的用户（内部提示：{resting_mention_notice}）。请用当前人格自然提醒发起 @ 的群友晚点再叫他；"
                        "语气柔和、像群友接话，不要像系统通知；不要私聊或 @ 休息用户，不要泄露具体休息截止时间或私聊原因。"
                    ),
                }
            )
        high_intensity_state = self._group_high_intensity_state(group)
        if not resting_mention_notice:
            async with self._temporarily_release_data_lock():
                continuation = await self._group_message_is_bot_continuation(
                    group,
                    sender_id,
                    sender_name,
                    scene,
                    text,
                    allow_llm=False,
                )
        if continuation is None:
            if high_intensity_state.get("active"):
                continuation = False
            else:
                group_for_judge = deepcopy(group)
            active_for_judge = deepcopy(self._group_active_conversation(group))

    if continuation is None:
        judged = await self._group_followup_llm_judge(
            group_for_judge,
            sender_id=sender_id,
            sender_name=sender_name,
            text=text,
            active=active_for_judge,
            scene=scene,
        )
        continuation = bool(judged) if judged is not None else False

    async with self._data_lock:
        group = self._get_group(group_id)
        scene = self._infer_group_scene(event, group, sender_id=sender_id, sender_name=sender_name, text=text)
        if quoted_link_payload:
            scene["quoted_link_payload"] = True
        if resting_mention_notice:
            setattr(event, "is_at_or_wake_command", True)
            setattr(event, "is_wake", True)
            scene.update(
                {
                    "trigger": "group_wakeup_resting_mention",
                    "talking_to": "bot",
                    "talking_to_name": "你",
                    "reason": "mentioned_resting_user",
                    "wakeup_word": "@休息用户",
                    "wakeup_strength": "strong",
                    "wakeup_strength_label": "明确需要你接话",
                    "wakeup_fatigue": {},
                    "wakeup_instruction": (
                        f"群友刚刚 @ 了一个已明确在休息的用户（内部提示：{resting_mention_notice}）。请用当前人格自然提醒发起 @ 的群友晚点再叫他；"
                        "语气柔和、像群友接话，不要像系统通知；不要私聊或 @ 休息用户，不要泄露具体休息截止时间或私聊原因。"
                    ),
                }
            )
        elif continuation:
            setattr(event, "is_at_or_wake_command", True)
            setattr(event, "is_wake", True)
            scene.update({"trigger": "bot_conversation_followup", "talking_to": "bot", "talking_to_name": "你", "reason": "contextual_followup_after_bot_wake"})
        elif _persona_value(self, "enable_group_wakeup_enhancement", False) and str(scene.get("trigger") or "") == "mention_bot_name":
            setattr(event, "is_at_or_wake_command", True)
            setattr(event, "is_wake", True)
            strength = self._group_wakeup_strength("direct_word", group, scene)
            fatigue = self._bump_group_wakeup_fatigue(group, "direct_word")
            scene.update(
                {
                    "trigger": "group_wakeup_direct_word",
                    "talking_to": "bot",
                    "talking_to_name": "你",
                    "reason": "direct_wakeup_word",
                    "wakeup_word": _single_line(_persona_value(self, "bot_name", ""), 60),
                    "wakeup_strength": strength,
                    "wakeup_strength_label": self._group_wakeup_strength_label(strength),
                    "wakeup_fatigue": dict(fatigue),
                    "wakeup_note": "群友提到了 Bot 名字。",
                }
            )
            group["last_group_wakeup_at"] = _now_ts()
            group["last_group_wakeup"] = {
                "ts": _now_ts(),
                "type": "direct_word",
                "word": _single_line(_persona_value(self, "bot_name", ""), 60),
                "strength": strength,
                "strength_label": self._group_wakeup_strength_label(strength),
                "reason": "direct_wakeup_word",
                "reason_label": self._group_wakeup_reason_label("direct_word", "direct_wakeup_word"),
                "reason_detail": "提到 Bot 名字或强唤醒词",
                "fatigue": dict(fatigue),
                "sender_id": sender_id,
                "sender_name": _single_line(sender_name, 40),
                "text": _single_line(text, 120),
            }
            wakeup_state_effect = self._apply_group_wakeup_to_humanized_state(scene, text)
            self._record_group_wakeup_log(
                group,
                scene=scene,
                sender_id=sender_id,
                sender_name=sender_name,
                text=text,
                wakeup=group["last_group_wakeup"],
                result="woke",
                strength=strength,
                fatigue=fatigue,
                note=_single_line(scene.get("wakeup_note"), 180),
            )
        elif (
            image_wakeup
            and str(scene.get("trigger") or "") not in {"at_other", "reply_other", "at_all"}
            and not bool(scene.get("quoted_link_payload"))
        ):
            setattr(event, "is_at_or_wake_command", True)
            setattr(event, "is_wake", True)
            strength = self._group_wakeup_strength("direct_word", group, scene)
            fatigue = self._bump_group_wakeup_fatigue(group, "direct_word")
            scene.update(
                {
                    "trigger": "group_wakeup_image_word",
                    "talking_to": "bot",
                    "talking_to_name": "你",
                    "reason": _single_line(image_wakeup.get("reason"), 60) or "image_direct_wakeup_word",
                    "wakeup_word": _single_line(image_wakeup.get("word"), 60),
                    "wakeup_strength": strength,
                    "wakeup_strength_label": self._group_wakeup_strength_label(strength),
                    "wakeup_fatigue": dict(fatigue),
                    "wakeup_note": _single_line(image_wakeup.get("note"), 180),
                }
            )
            group["last_group_wakeup_at"] = _now_ts()
            group["last_group_wakeup"] = {
                "ts": _now_ts(),
                "type": "direct_word",
                "word": _single_line(image_wakeup.get("word"), 60),
                "strength": strength,
                "strength_label": self._group_wakeup_strength_label(strength),
                "reason": _single_line(image_wakeup.get("reason"), 80) or "image_direct_wakeup_word",
                "reason_label": self._group_wakeup_reason_label("direct_word", str(image_wakeup.get("reason") or "")),
                "reason_detail": "图片视觉摘要命中强唤醒词",
                "fatigue": dict(fatigue),
                "sender_id": sender_id,
                "sender_name": _single_line(sender_name, 40),
                "text": _single_line(text, 120),
                "source": "image_vision",
            }
            self._record_group_wakeup_log(
                group,
                scene=scene,
                sender_id=sender_id,
                sender_name=sender_name,
                text=text,
                wakeup=group["last_group_wakeup"],
                result="woke",
                strength=strength,
                fatigue=fatigue,
                note=_single_line(image_wakeup.get("note"), 180),
            )
            logger.info(
                "[PrivateCompanion] 群聊图片内容命中唤醒词: group=%s sender=%s word=%s strength=%s",
                group_id,
                sender_id,
                image_wakeup.get("word"),
                strength,
            )
        else:
            wakeup = self._evaluate_group_wakeup(
                group,
                scene=scene,
                sender_id=sender_id,
                sender_name=sender_name,
                text=text,
                group_id=group_id,
            )
            if wakeup:
                setattr(event, "is_at_or_wake_command", True)
                setattr(event, "is_wake", True)
                strength = _single_line(wakeup.get("strength"), 24) or self._group_wakeup_strength(str(wakeup.get("type") or ""), group, scene)
                fatigue = self._bump_group_wakeup_fatigue(group, str(wakeup.get("type") or ""))
                scene.update(
                    {
                        "trigger": f"group_wakeup_{wakeup.get('type')}",
                        "talking_to": "bot",
                        "talking_to_name": "你",
                        "reason": _single_line(wakeup.get("reason"), 60),
                        "wakeup_word": _single_line(wakeup.get("word"), 60),
                        "wakeup_strength": strength,
                        "wakeup_strength_label": self._group_wakeup_strength_label(strength),
                        "wakeup_fatigue": dict(fatigue),
                        "wakeup_note": _single_line(wakeup.get("note"), 180),
                        "wakeup_topic_weight": wakeup.get("topic_weight") if isinstance(wakeup.get("topic_weight"), dict) else {},
                    }
                )
                group["last_group_wakeup_at"] = _now_ts()
                group["last_group_wakeup"] = {
                    "ts": _now_ts(),
                    "type": _single_line(wakeup.get("type"), 40),
                    "word": _single_line(wakeup.get("word"), 60),
                    "strength": strength,
                    "strength_label": self._group_wakeup_strength_label(strength),
                    "fatigue": dict(fatigue),
                    "probability": wakeup.get("probability"),
                    "score": wakeup.get("score"),
                    "threshold": wakeup.get("threshold"),
                    "intensity": wakeup.get("intensity"),
                    "help_type": wakeup.get("help_type"),
                    "reason": _single_line(wakeup.get("reason"), 80),
                    "reason_label": _single_line(wakeup.get("reason_label"), 80) or self._group_wakeup_reason_label(str(wakeup.get("type") or ""), str(wakeup.get("reason") or "")),
                    "reason_detail": _single_line(wakeup.get("reason_detail"), 180) or self._group_wakeup_reason_detail(wakeup),
                    "topic_weight": wakeup.get("topic_weight") if isinstance(wakeup.get("topic_weight"), dict) else {},
                    "sender_id": sender_id,
                    "sender_name": _single_line(sender_name, 40),
                    "text": _single_line(text, 120),
                }
                wakeup_state_effect = self._apply_group_wakeup_to_humanized_state(scene, text)
                self._record_group_wakeup_log(
                    group,
                    scene=scene,
                    sender_id=sender_id,
                    sender_name=sender_name,
                    text=text,
                    wakeup=group["last_group_wakeup"],
                    result="woke",
                    strength=strength,
                    fatigue=fatigue,
                    note=_single_line(wakeup.get("note"), 180),
                )
                logger.info(
                    "[PrivateCompanion] 群聊增强唤醒命中: group=%s sender=%s type=%s word=%s strength=%s fatigue=%s reason=%s detail=%s",
                    group_id,
                    sender_id,
                    wakeup.get("type"),
                    wakeup.get("word"),
                    strength,
                    fatigue.get("label"),
                    group["last_group_wakeup"].get("reason_label"),
                    group["last_group_wakeup"].get("reason_detail"),
                )
        talking_to_bot = str(scene.get("talking_to") or "") == "bot"
        if (
            not talking_to_bot
            and
            str(scene.get("talking_to") or "") not in {"group", ""}
            and str(self._group_active_conversation(group).get("sender_id") or "") != str(sender_id or "")
        ):
            self._mark_group_bot_conversation(group, sender_id, sender_name, active=False)
        scene_trigger = str(scene.get("trigger") or "")
        if talking_to_bot and scene_trigger in {"at_bot", "reply_bot"}:
            strength = self._group_wakeup_strength("direct_word", group, scene)
            fatigue = self._bump_group_wakeup_fatigue(group, "direct_word")
            scene.setdefault("wakeup_strength", strength)
            scene.setdefault("wakeup_strength_label", self._group_wakeup_strength_label(strength))
            scene["wakeup_fatigue"] = dict(fatigue)
            group["last_group_wakeup_at"] = _now_ts()
            group["last_group_wakeup"] = {
                "ts": _now_ts(),
                "type": "direct_word",
                "word": "@" if scene_trigger == "at_bot" else "reply",
                "strength": strength,
                "strength_label": self._group_wakeup_strength_label(strength),
                "reason": "explicit_at_or_reply",
                "reason_label": "明确 @ 或引用 Bot",
                "reason_detail": "群友明确 @ 或引用了 Bot",
                "fatigue": dict(fatigue),
                "sender_id": sender_id,
                "sender_name": _single_line(sender_name, 40),
                "text": _single_line(text, 120),
            }
            self._record_group_wakeup_log(
                group,
                scene=scene,
                sender_id=sender_id,
                sender_name=sender_name,
                text=text,
                wakeup=group["last_group_wakeup"],
                result="woke",
                strength=strength,
                fatigue=fatigue,
                note="群友明确 @ 或引用了 Bot。",
            )
        high_intensity_state = self._group_high_intensity_state(group)
        if high_intensity_state.get("active"):
            setattr(event, "private_companion_group_high_intensity", dict(high_intensity_state))
        high_intensity_merge_active = bool(high_intensity_state.get("merge_active"))
        setattr(event, "private_companion_group_scene", dict(scene))
        setattr(event, "private_companion_group_sender_name", sender_name)
        setattr(event, "private_companion_group_text", text)
        setattr(event, "private_companion_group_contextual_followup", bool(continuation))
        read_view_getter = getattr(self, "_req041_relationship_read_view", None)
        private_users = self.data.get("users") if isinstance(self.data.get("users"), dict) else {}
        canonical_sender = self._canonical_private_user_id(sender_id)
        relationship_user = private_users.get(canonical_sender) if isinstance(private_users, dict) else None
        if callable(read_view_getter) and isinstance(relationship_user, dict):
            read_view_getter(
                event, relationship_user, kind="group_member", group_id=group_id,
            )
        group_read_view = group
        scoped_group_getter = getattr(self, "_req041_scoped_group_read_view", None)
        if callable(scoped_group_getter):
            group_read_view = scoped_group_getter(
                event, group_id=group_id, group=group, sender_id=sender_id,
                relationship_user=relationship_user if isinstance(relationship_user, dict) else None,
            )
        self._memory_companion_attach_group_context(
            event,
            group_id=group_id,
            group=group_read_view,
            sender_id=sender_id,
            sender_name=sender_name,
            text=text,
        )
        if wakeup_state_effect:
            setattr(event, "private_companion_group_wakeup_state_effect", dict(wakeup_state_effect))
        group_reference_media_with_text = False
        if talking_to_bot and text:
            async with self._temporarily_release_data_lock():
                group_reference_media_with_text = await self._event_references_media_or_forward_with_text(event, text)
            if group_reference_media_with_text:
                logger.info(
                    "[PrivateCompanion] 群聊引用媒体/合并消息附带文字,跳过群聊收口等待: group=%s sender=%s text=%s",
                    group_id,
                    sender_id,
                    _single_line(text, 80),
                )
        if high_intensity_merge_active and talking_to_bot and not group_reference_media_with_text:
            high_key = self._group_high_intensity_buffer_key(group_id, sender_id)
            if self._note_semantic_message_buffer(
                high_key,
                text,
                sender_name=sender_name,
                wait_seconds=self._group_high_intensity_merge_wait_seconds(),
                force=True,
                kind="group_high_intensity",
            ):
                self._capture_group_observation_once(
                    group,
                    sender_id=sender_id,
                    sender_name=sender_name,
                    text=text,
                    group_id=group_id,
                    scene=scene,
                    message_id=self._event_message_id(event),
                    event=event,
                )
                self._save_data_sync(sections={"groups"})
                logger.info(
                    "[PrivateCompanion] 群聊高强度消息已合并等待: group=%s sender=%s scope=%s recent_wakeups=%s floor=%s reason=%s wait=%ss text=%s",
                    group_id,
                    sender_id,
                    _persona_value(self, "group_high_intensity_merge_scope", "group"),
                    high_intensity_state.get("recent_wakeups"),
                    high_intensity_state.get("merge_recent_floor"),
                    high_intensity_state.get("reason"),
                    self._group_high_intensity_merge_wait_seconds(),
                    _single_line(text, 80),
                )
                event.stop_event()
                return
        if talking_to_bot and not high_intensity_merge_active and not group_reference_media_with_text:
            async with self._temporarily_release_data_lock():
                air_guard = await self._group_air_reply_guard_decision(
                    group,
                    sender_id=sender_id,
                    sender_name=sender_name,
                    text=text,
                    scene=scene,
                )
            if isinstance(air_guard, dict) and air_guard.get("block"):
                self._capture_group_observation_once(
                    group,
                    sender_id=sender_id,
                    sender_name=sender_name,
                    text=text,
                    group_id=group_id,
                    scene=scene,
                    message_id=self._event_message_id(event),
                    event=event,
                )
                group["last_air_guard_block"] = {
                    "ts": _now_ts(),
                    "sender_id": sender_id,
                    "sender_name": _single_line(sender_name, 40),
                    "text": _single_line(text, 120),
                    "reason": _single_line(air_guard.get("reason"), 60),
                    "answer": _single_line(air_guard.get("answer"), 60),
                    "recent_bot_replies": _safe_int(air_guard.get("recent_bot_replies"), 0, 0, 99),
                    "recent_polite_replies": _safe_int(air_guard.get("recent_polite_replies"), 0, 0, 99),
                }
                self._save_data_sync(sections={"groups"})
                logger.info(
                    "[PrivateCompanion] 群聊读空气拦截回复: group=%s sender=%s reason=%s text=%s",
                    group_id,
                    sender_id,
                    air_guard.get("reason"),
                    _single_line(text, 80),
                )
                event.stop_event()
                return
        group_smart_wait = 0.0
        group_buffer_key = self._semantic_buffer_key(f"group:{group_id}", sender_id)
        pending_debounce_merge = False
        pending_absorber = getattr(self, "_message_debounce_absorb_pending_message", None)
        if (
            talking_to_bot
            and not high_intensity_merge_active
            and not group_reference_media_with_text
            and callable(pending_absorber)
        ):
            pending_debounce_merge = bool(pending_absorber(event, text))
        if talking_to_bot and not high_intensity_merge_active and not group_reference_media_with_text:
            if not pending_debounce_merge:
                self._maybe_record_smart_message_debounce_followup(
                    scope=f"group:{group_id}",
                    sender_id=sender_id,
                    text=text,
                    now=_now_ts(),
                )
                async with self._temporarily_release_data_lock():
                    group_smart_wait = await self._smart_message_debounce_wait_seconds_for_event(
                        event,
                        key=group_buffer_key,
                        text=text,
                        sender_id=sender_id,
                        sender_name=sender_name,
                        private_chat=False,
                    )
        group_smart_result = getattr(event, "private_companion_smart_message_debounce_result", None)
        group_smart_decision = str(group_smart_result.get("decision") or "") if isinstance(group_smart_result, dict) else ""
        group_smart_handled = group_smart_decision in {"complete", "incomplete"}
        short_wait = 0.0
        if not high_intensity_merge_active:
            short_wait = self._group_short_wakeup_wait_seconds(event, text, smart_result=group_smart_result)
        if short_wait > 0:
            group_smart_wait = max(group_smart_wait, short_wait)
            group_smart_decision = "incomplete"
            group_smart_handled = True
        if (
            talking_to_bot
            and not high_intensity_merge_active
            and not group_reference_media_with_text
            and group_smart_decision == "incomplete"
            and self._note_semantic_message_buffer(
                group_buffer_key,
                text,
                sender_name=sender_name,
                wait_seconds=group_smart_wait,
                smart_debounce={"enabled": group_smart_handled, "decision": group_smart_decision or "fixed"},
                kind="group_short_wakeup" if short_wait > 0 else "group_text",
            )
        ):
            self._save_data_sync(sections={"smart_message_debounce"})
            event.stop_event()
            return
        self._capture_group_observation_once(
            group,
            sender_id=sender_id,
            sender_name=sender_name,
            text=text,
            group_id=group_id,
            scene=scene,
            message_id=self._event_message_id(event),
            event=event,
        )
        registration_payload = self._maybe_worldbook_self_register_from_group_message(
            event,
            group_id=group_id,
            sender_id=sender_id,
            sender_name=sender_name,
            text=text,
            group=group,
        )
        affinity_preparer = getattr(self, "_req041_prepare_group_affinity_candidate", None)
        if (
            callable(affinity_preparer)
            and scene_trigger in {"at_bot", "reply_bot"}
            and not group_reference_media_with_text
            and not (
                isinstance(registration_payload, dict)
                and bool(registration_payload.get("blocked_reply"))
            )
        ):
            affinity_preparer(
                event,
                group_id=group_id,
                relationship_user=(
                    relationship_user if isinstance(relationship_user, dict) else None
                ),
                scene_trigger=scene_trigger,
                forwarded=group_reference_media_with_text,
            )
        share_scheduled = self._maybe_schedule_group_private_share(group_id, group, trigger_sender_id=sender_id)
        save_sections = {"groups", "users", "proactive_candidate_pool"}
        if self._expression_group_learning_source_enabled(group.get("group_id") or group_id):
            save_sections.add("expression_voice_profile")
        if isinstance(registration_payload, dict):
            if registration_payload.get("updated_observation_profile"):
                save_sections.add("worldbook_member_profiles")
            if registration_payload.get("user_id"):
                save_sections.update(
                    {
                        "worldbook_member_profiles",
                        "worldbook_deleted_member_ids",
                    }
                )
        self._save_data_sync(sections=save_sections)
        group_snapshot = deepcopy(group)
        group_snapshot_high_intensity = dict(high_intensity_state)
    await self._dispatch_due_atrelay_tasks(event, group_id, sender_id)
    if isinstance(registration_payload, dict) and registration_payload.get("blocked_reply"):
        await self._reply(event, str(registration_payload.get("blocked_reply") or "这个称呼我不记。"))
        event.stop_event()
        return
    if isinstance(registration_payload, dict) and registration_payload.get("confirm_reply"):
        await self._reply(event, str(registration_payload.get("confirm_reply") or ""))
        if not registration_payload.get("user_id"):
            event.stop_event()
            return
    if registration_payload and registration_payload.get("user_id"):
        self._create_lifecycle_background_task(
            self._refresh_worldbook_self_registration_impression(registration_payload),
            label="refresh_worldbook_self_registration_impression",
        )
    if share_scheduled:
        self._create_lifecycle_background_task(
            self._kick_proactive_loop_once(),
            label="kick_proactive_loop_group",
        )
    if not group_snapshot_high_intensity.get("active"):
        self._create_lifecycle_background_task(
            self._maybe_refresh_group_episode(group_id, group_snapshot),
            label="refresh_group_episode",
        )
        self._create_lifecycle_background_task(
            self._maybe_refresh_group_slang_meanings(group_id, group_snapshot),
            label="refresh_group_slang",
        )
    else:
        logger.info(
            "[PrivateCompanion] 群聊高强度收口生效: group=%s recent_wakeups=%s threshold=%s merge_active=%s floor=%s reason=%s merge_scope=%s merge_wait=%ss skip=followup-refresh/general-interject repeat=enabled",
            group_id,
            group_snapshot_high_intensity.get("recent_wakeups"),
            group_snapshot_high_intensity.get("threshold"),
            group_snapshot_high_intensity.get("merge_active"),
            group_snapshot_high_intensity.get("merge_recent_floor"),
            group_snapshot_high_intensity.get("reason"),
            _persona_value(self, "group_high_intensity_merge_scope", "group"),
            self._group_high_intensity_merge_wait_seconds(),
        )
    await self._maybe_group_interject(
        event,
        group_snapshot,
        text,
        allow_interjection=(
            not bool(group_snapshot_high_intensity.get("active"))
            and self._group_wakeup_allows_general_interjection(scene)
        ),
    )
    original_interject_at = _safe_float(group.get("last_interject_at"), 0) if isinstance(group, dict) else 0
    repeat_state_changed = group_snapshot.get("repeat_follow_state") != (
        group.get("repeat_follow_state") if isinstance(group, dict) else {}
    )
    if _safe_float(group_snapshot.get("last_interject_at"), 0) > original_interject_at or repeat_state_changed:
        async with self._data_lock:
            current = self._get_group(group_id)
            current["last_interject_at"] = group_snapshot.get("last_interject_at", current.get("last_interject_at", 0))
            current["interject_day"] = group_snapshot.get("interject_day", current.get("interject_day", ""))
            current["interject_today"] = group_snapshot.get("interject_today", current.get("interject_today", 0))
            current["last_bot_interjection"] = group_snapshot.get("last_bot_interjection", current.get("last_bot_interjection", {}))
            current["repeat_follow_state"] = group_snapshot.get("repeat_follow_state", current.get("repeat_follow_state", {}))
            current["recent_bot_replies"] = deepcopy(
                group_snapshot.get("recent_bot_replies", current.get("recent_bot_replies", []))
            )
            self._save_data_sync(sections={"groups"})
