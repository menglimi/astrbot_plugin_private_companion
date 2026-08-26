# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import re
from typing import Any

from astrbot.api import logger

from .conversation_injection_plan import (
    PLACEMENT_STABLE_SYSTEM,
    get_conversation_injection_plan,
)
from .conversation_prompt_section import prompt_section, render_prompt_sections
from .helpers import _now_ts, _safe_float, _single_line
from .persona_config import runtime_persona_setting
from .prompt_surface import PromptSurface


GROUP_CONTEXT_FINAL_PRIORITY = 10_000


async def inject_humanized_state(
    self: Any,
    event: Any,
    req: Any,
    *args: Any,
    **kwargs: Any,
) -> Any:
    """LLM 请求前注入陪伴状态、群聊上下文、工具边界和合并消息阅读上下文。"""
    if self is None:
        return

    def log_bookshelf_secret_skip(reason: str, user: dict[str, Any] | None = None, text: str = "") -> None:
        logger_func = getattr(self, "_log_bookshelf_secret_skip", None)
        if not callable(logger_func):
            return
        source_text = text
        if not source_text:
            source_text = (
                getattr(event, "private_companion_group_text", "")
                or getattr(event, "message_str", "")
                or ""
            )
        logger_func(reason, source_text, user if isinstance(user, dict) else None)

    if not self.enabled:
        return
    feedback_recorder = getattr(self, "_record_photo_reference_feedback_from_event", None)
    if callable(feedback_recorder):
        try:
            feedback_recorder(event)
        except Exception as exc:
            logger.debug(
                "[PrivateCompanion] 记录参考图效果反馈失败: %s",
                _single_line(exc, 120),
            )
    if self._stop_group_llm_reply_if_blocked(event, source="llm_request"):
        return
    if not hasattr(req, "system_prompt"):
        log_bookshelf_secret_skip("llm_request_no_system_prompt")
        return
    self._sanitize_request_context_new_conversation_boundary(event, req)
    self._repair_incomplete_tool_context_groups(event, req)
    self._sanitize_private_companion_prompt_artifacts_in_request(event, req)
    self._append_deepseek_tool_protocol_guard(event, req)
    self._append_passive_reply_tool_boundary(event, req)
    self._remember_external_llm_request_for_token_stats(event, req)
    proactive_only_limited = self._proactive_only_limited_passive_event(event)
    if self._proactive_only_blocks_passive_event(event, "llm_request"):
        log_bookshelf_secret_skip("proactive_only_mode")
        return
    if proactive_only_limited and not self._proactive_only_llm_request_needs_full_path():
        await self._append_proactive_only_unlocked_llm_request_fragments(event, req)
        log_bookshelf_secret_skip("proactive_only_limited_light_path")
        return
    is_private_chat = bool(getattr(event, "is_private_chat", lambda: False)())
    private_user_active = False
    if is_private_chat:
        try:
            resolver = getattr(self, "_private_user_id_for_event", None)
            private_user_id = (
                resolver(event)
                if callable(resolver)
                else self._canonical_private_user_id(str(event.get_sender_id()))
            )
        except Exception:
            private_user_id = ""
        raw_users = self.data.get("users", {})
        private_user = raw_users.get(private_user_id) if private_user_id and isinstance(raw_users, dict) else None
        private_user_active = (
            isinstance(private_user, dict)
            and self._private_passive_profile_available(private_user_id, private_user)
        )
        if private_user_active:
            relationship_getter = getattr(self, "_req041_relationship_read_view", None)
            if callable(relationship_getter):
                private_user = relationship_getter(event, private_user, kind="private")
            scoped_getter = getattr(self, "_req041_scoped_private_read_view", None)
            if callable(scoped_getter):
                private_user = scoped_getter(event, private_user)
            preferred_address = _single_line(
                private_user.get("nickname") or runtime_persona_setting(self, "default_nickname", "你"),
                24,
            )
            if preferred_address:
                # MemoryCompanion consumes these request-scoped fields after this hook.
                setattr(req, "_private_companion_preferred_address", preferred_address)
                setattr(req, "_private_companion_preferred_address_locked", True)
        if not private_user_active:
            reason = "private_user_missing" if not isinstance(private_user, dict) else "private_user_disabled"
            log_bookshelf_secret_skip(reason, private_user if isinstance(private_user, dict) else None)
            logger.info(
                "[PrivateCompanion] 非目标/未启用私聊跳过陪伴被动增强: user=%s reason=%s",
                _single_line(private_user_id, 40) or "unknown",
                reason,
            )
    rest_allowed, rest_reason = await self._should_reply_during_rest(event, is_private_chat=is_private_chat)
    if not rest_allowed:
        log_bookshelf_secret_skip(f"rest_reply_gate:{_single_line(rest_reason, 60)}")
        self._stop_reply_for_rest_gate(event, rest_reason)
        return
    if rest_reason not in {"disabled", "not_sleeping"}:
        try:
            setattr(event, "private_companion_rest_reply_gate_reason", rest_reason)
        except Exception:
            pass
        logger.info(
            "[PrivateCompanion] 睡眠/休息回复闸门放行本轮被动回复: session=%s reason=%s",
            _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            _single_line(rest_reason, 120),
        )
    _busy_delay, busy_delay_reason = await self._apply_busy_reply_gate_delay(
        event,
        is_private_chat=is_private_chat,
    )
    if busy_delay_reason == "superseded_by_newer_private_message":
        return
    pending_marker = getattr(self, "_message_debounce_mark_llm_pending", None)
    if callable(pending_marker):
        # Only mark requests that belong to this companion's inbound path.
        # Other plugins can share the same AstrBot session and must not cause
        # their responses to be discarded when a user sends a follow-up.
        mark_pending = bool(is_private_chat and private_user_active)
        if not is_private_chat:
            group_id_for_pending = self._extract_group_id_from_event(event)
            group_scene = getattr(event, "private_companion_group_scene", None)
            high_intensity = getattr(event, "private_companion_group_high_intensity", None)
            mark_pending = bool(
                isinstance(group_scene, dict)
                and str(group_scene.get("talking_to") or "") == "bot"
                and not (isinstance(high_intensity, dict) and high_intensity.get("merge_active"))
                and group_id_for_pending
                and self._feature_enabled_or_temp_unlocked("enable_group_companion")
                and self._group_enabled_for_event(group_id_for_pending)
            )
        if mark_pending:
            pending_marker(event)
    self._trim_passive_request_context_if_needed(event, req, is_private_chat=is_private_chat)
    await self._enrich_request_context_image_placeholders(event, req)
    if not is_private_chat:
        await self._append_group_image_understanding_to_request(event, req)
    if (
        bool(getattr(event, "private_companion_deferred_private_image_only", False))
        and not bool(getattr(event, "private_companion_deferred_private_image_only_ready", False))
    ):
        for attr in ("image_urls", "images"):
            existing = getattr(req, attr, None)
            if existing:
                try:
                    setattr(req, attr, [])
                except Exception:
                    pass
    await self.apply_tts_enhancement_request(event, req)
    await self._append_forward_message_context_to_request(event, req)
    if not is_private_chat and runtime_persona_setting(self, "enable_group_reality_promise_guard", True):
        await self._append_capability_boundary_to_request(event, req)
    if not is_private_chat:
        await self._mark_group_conversation_from_llm_request(event)
        await self._append_group_injection_guard_to_request(event, req)
        await self._append_group_persona_denoise_to_request(event, req)
        await self._append_group_high_intensity_reply_guard_to_request(event, req)
        await self._append_group_member_safety_hidden_marker_to_request(event, req)
    else:
        await self._append_non_target_private_identity_guard_to_request(event, req)
    await self._append_daily_review_guidance_to_request(event, req)
    weather_query_allowed = is_private_chat and private_user_active
    weather_query_user = private_user if weather_query_allowed and isinstance(private_user, dict) else None
    if not is_private_chat:
        weather_group_id = self._extract_group_id_from_event(event)
        weather_query_allowed = bool(
            weather_group_id
            and self._feature_enabled_or_temp_unlocked("enable_group_companion")
            and self._group_enabled_for_event(weather_group_id)
        )
    if weather_query_allowed:
        await self._append_weather_query_context_to_request(
            event,
            req,
            current_user=weather_query_user,
        )
    passive_states_enabled = self._feature_enabled_or_temp_unlocked("inject_passive_states")
    if not passive_states_enabled and is_private_chat:
        if private_user_active:
            await self._append_reply_style_to_request(event, req, mode="private")
        try:
            resolver = getattr(self, "_private_user_id_for_event", None)
            backlog_user_id = (
                resolver(event)
                if callable(resolver)
                else self._canonical_private_user_id(str(event.get_sender_id()))
            )
        except Exception:
            backlog_user_id = ""
        backlog_user = self.data.get("users", {}).get(backlog_user_id) if backlog_user_id else None
        if isinstance(backlog_user, dict):
            await self._append_rest_reply_backlog_to_request(event, req, backlog_user)
        await self._append_worldbook_mentions_to_request(event, req, mode="light")
        await self._append_conditional_tool_instructions_to_request(event, req)
        await self._append_environment_perception_to_request(event, req)
        log_bookshelf_secret_skip(
            "passive_injection_disabled",
            backlog_user if isinstance(backlog_user, dict) else None,
        )
        return

    if not is_private_chat:
        group_id = self._extract_group_id_from_event(event) if self._feature_enabled_or_temp_unlocked("enable_group_companion") else ""
        group: dict[str, Any] | None = None
        sender_id = ""
        if group_id and self._group_enabled_for_event(group_id):
            try:
                sender_id = str(event.get_sender_id())
            except Exception:
                sender_id = ""
            group = self._get_group(group_id)
        if group_id and isinstance(group, dict):
            existing_scoped_group = getattr(event, "req041_scoped_group_read_view", None)
            if isinstance(existing_scoped_group, dict):
                group = existing_scoped_group
            else:
                scoped_group_getter = getattr(self, "_req041_scoped_group_read_view", None)
                if callable(scoped_group_getter):
                    try:
                        sender_id = str(event.get_sender_id())
                    except Exception:
                        sender_id = ""
                    private_users = self.data.get("users") if isinstance(self.data.get("users"), dict) else {}
                    canonicalizer = getattr(self, "_canonical_private_user_id", None)
                    canonical_sender = (
                        canonicalizer(sender_id) if callable(canonicalizer) else sender_id
                    )
                    relationship_user = (
                        private_users.get(canonical_sender) if isinstance(private_users, dict) else None
                    )
                    group = scoped_group_getter(
                        event, group_id=group_id, group=group, sender_id=sender_id,
                        relationship_user=(
                            relationship_user if isinstance(relationship_user, dict) else None
                        ),
                    )
            expression_marker = "<!-- private_companion_expression_voice_group_v1 -->"
            current_prompt = req.system_prompt or ""
            current_turn_prompt = str(getattr(req, "prompt", "") or "")
            if expression_marker not in current_prompt and expression_marker not in current_turn_prompt:
                group_expression_selection = self._expression_voice_selection(
                    scope="group",
                    target_id=group_id,
                    inbound_text=_single_line(
                        getattr(event, "private_companion_group_text", "")
                        or getattr(event, "message_str", "")
                        or getattr(req, "prompt", ""),
                        300,
                    ),
                    context_owner=group,
                    include_heading=False,
                )
                expression_voice = str(group_expression_selection.get("prompt") or "")
                semantic_expression_rules = group_expression_selection.get("rules")
                if isinstance(semantic_expression_rules, list) and semantic_expression_rules:
                    try:
                        setattr(event, "private_companion_semantic_expression_rules", semantic_expression_rules)
                        setattr(
                            event,
                            "private_companion_semantic_expression_context",
                            dict(group_expression_selection.get("context") or {}),
                        )
                        setattr(event, "private_companion_semantic_expression_group_id", group_id)
                    except Exception:
                        pass
                if expression_voice:
                    placement = "prompt" if self._append_turn_prompt_fragment_by_position(
                        req,
                        expression_marker,
                        expression_voice,
                        title="已审核的表达学习规则",
                        priority=58,
                        source="expression",
                    ) else "system_prompt"
                    if placement == "system_prompt":
                        req.system_prompt = (
                            f"{current_prompt}\n\n{expression_marker}\n{expression_voice}"
                        ).strip()
                    await self._record_request_prompt_fragment(
                        event,
                        title="群聊表达底色注入",
                        key="expression.voice",
                        text=expression_voice,
                        source="expression",
                        mode="group",
                        metadata={"注入位置": placement, "范围": "全局抽象表达底色"},
                    )
        if runtime_persona_setting(self, "enable_group_context_injection", True) and self._feature_enabled_or_temp_unlocked("enable_group_companion"):
            if group_id and self._group_enabled_for_event(group_id):
                if not isinstance(group, dict):
                    group = self._get_group(group_id)
                text_for_mark = _single_line(
                    getattr(event, "private_companion_group_text", "") or getattr(event, "message_str", ""),
                    260,
                )
                marker = "<!-- private_companion_group_context_v1 -->"
                current_prompt = req.system_prompt or ""
                current_turn_prompt = str(getattr(req, "prompt", "") or "")
                if marker not in current_prompt and marker not in current_turn_prompt:
                    combined_text = await self._consume_semantic_message_buffer_for_event(event, private_chat=False)
                    extra_sections: list[dict[str, Any]] = []
                    if combined_text:
                        high_intensity = getattr(event, "private_companion_group_high_intensity", None)
                        if isinstance(high_intensity, dict) and high_intensity.get("active"):
                            combined_text = self._compact_high_intensity_prompt_lines(
                                combined_text,
                                max_chars=700,
                                max_lines=8,
                            )
                            extra_sections.append(
                                prompt_section(
                                    "本轮高强度合并消息",
                                    "群里刚刚短时间内多次叫到你，下面这些消息已压缩为同一轮理解背景；"
                                    "只挑最相关的一点短答，不要逐条回应：\n"
                                    f"{combined_text}",
                                )
                            )
                        else:
                            extra_sections.append(
                                prompt_section(
                                    "本轮用户连续补充",
                                    "用户刚刚在短时间内连续补充了几句,请把它们当作同一轮完整发言理解,"
                                    "不要逐条回复：\n"
                                    f"{combined_text}",
                                )
                            )
                    wakeup_effect = getattr(event, "private_companion_group_wakeup_state_effect", None)
                    wakeup_state_text = ""
                    if passive_states_enabled and isinstance(wakeup_effect, dict) and wakeup_effect:
                        try:
                            state = await self._ensure_daily_state()
                        except Exception:
                            state = self.data.get("daily_state", {})
                        wakeup_state_text = self._format_group_wakeup_humanized_prompt(
                            wakeup_effect,
                            state,
                            include_heading=False,
                        )
                    if self._user_asks_recalled_messages(text_for_mark):
                        recall_context = self._format_recalled_messages_for_natural_query(
                            event,
                            limit=5,
                            include_heading=False,
                        )
                        if recall_context:
                            extra_sections.append(prompt_section("撤回消息查询", recall_context))
                    passive_group_formatter = getattr(self, "_format_group_passive_reply_context_for_prompt", None)
                    if callable(passive_group_formatter):
                        group_context = passive_group_formatter(group, sender_id, str(event.message_str or ""))
                    else:
                        group_context = prompt_section(
                            "群聊上下文",
                            self._format_group_context_for_prompt(group, sender_id, str(event.message_str or "")),
                        )
                    group_context_section: dict[str, Any] | None = None
                    if isinstance(group_context, dict) and set(group_context) == {"title", "content"}:
                        group_context_section = group_context
                    elif group_context:
                        group_context_section = prompt_section("群聊上下文", str(group_context))
                    group_sections: list[dict[str, Any]] = []
                    slang_embedding_builder = getattr(self, "_group_slang_embedding_context", None)
                    if callable(slang_embedding_builder):
                        try:
                            slang_embedding_text = await slang_embedding_builder(
                                group,
                                str(event.message_str or ""),
                                include_heading=False,
                            )
                        except Exception as exc:
                            logger.debug(
                                "[PrivateCompanion] 群黑话嵌入上下文生成失败: %s",
                                _single_line(exc, 120),
                            )
                            slang_embedding_text = ""
                        if slang_embedding_text:
                            group_sections.append(
                                prompt_section(
                                    "群内黑话语义近似（仅作软参考）",
                                    slang_embedding_text,
                                )
                            )
                    high_intensity_for_context = getattr(event, "private_companion_group_high_intensity", None)
                    recent_atrelay_context = self._format_recent_atrelay_context_for_prompt(
                        kind="group",
                        target=group_id,
                        sender_id=sender_id,
                        current_text=str(event.message_str or ""),
                        limit=2,
                        include_heading=False,
                    )
                    if recent_atrelay_context:
                        group_sections.append(prompt_section("刚刚的转述动作", recent_atrelay_context))
                    if wakeup_state_text:
                        group_sections.append(prompt_section("群聊唤醒与当前状态", wakeup_state_text))
                    group_sections.extend(extra_sections)
                    realtime_formatter = getattr(self, "_format_external_realtime_prompt_section", None)
                    if callable(realtime_formatter):
                        realtime_section = realtime_formatter({}, public=True)
                        realtime_context = str(realtime_section.get("content") or "")
                        if realtime_context:
                            group_sections.insert(0, realtime_section)
                    if group_context_section is not None:
                        group_sections.append(group_context_section)
                    group_context_text = render_prompt_sections(group_sections)
                    placement = "prompt" if self._append_turn_prompt_fragment_by_position(
                        req,
                        marker,
                        group_context_text,
                        title="群聊上下文",
                        priority=GROUP_CONTEXT_FINAL_PRIORITY,
                        source="group",
                        structured=True,
                    ) else "system_prompt"
                    if placement == "system_prompt":
                        req.system_prompt = (
                            f"{current_prompt}\n\n{marker}\n{group_context_text}"
                        ).strip()
                    await self._record_request_prompt_fragment(
                        event,
                        title="群聊上下文注入",
                        key="group.context",
                        text=group_context_text,
                        source="group",
                        mode="group",
                        metadata={"注入位置": placement},
                    )
        if passive_states_enabled:
            await self._append_group_active_period_boundary_to_request(
                event,
                req,
                group_id if isinstance(group, dict) else "",
            )
        group_recall_text = _single_line(
            getattr(event, "private_companion_group_text", "") or getattr(event, "message_str", ""),
            260,
        )
        recall_marker = "<!-- private_companion_recall_query_v1 -->"
        if (
            self._user_asks_recalled_messages(group_recall_text)
            and recall_marker not in (req.system_prompt or "")
            and recall_marker not in str(getattr(req, "prompt", "") or "")
        ):
            recall_context = self._format_recalled_messages_for_natural_query(
                event,
                limit=5,
                include_heading=False,
            )
            if recall_context:
                placement = "prompt" if self._append_turn_prompt_fragment_by_position(
                    req,
                    recall_marker,
                    recall_context,
                    title="撤回消息查询",
                    priority=66,
                    source="recall",
                ) else "system_prompt"
                if placement == "system_prompt":
                    req.system_prompt = f"{req.system_prompt or ''}\n\n{recall_marker}\n{recall_context}".strip()
                await self._record_request_prompt_fragment(
                    event,
                    title="历史召回查询注入",
                    key="recall.query",
                    text=recall_context,
                    source="recall",
                    mode="group",
                    metadata={"注入位置": placement},
                )
        timeline_marker = "<!-- private_companion_self_timeline_v1 -->"
        if timeline_marker not in (req.system_prompt or "") and timeline_marker not in str(getattr(req, "prompt", "") or ""):
            high_intensity_for_timeline = getattr(event, "private_companion_group_high_intensity", None)
            timeline_limit = 3 if isinstance(high_intensity_for_timeline, dict) and high_intensity_for_timeline.get("active") else 8
            self_timeline_context = (
                ""
                if self._memory_companion_should_defer_prompt_section("self_timeline", event, req)
                else self._format_self_timeline_context_for_reply(
                    group_recall_text,
                    limit=timeline_limit,
                    include_heading=False,
                )
            )
            if self_timeline_context:
                placement = "prompt" if self._append_turn_prompt_fragment_by_position(
                    req,
                    timeline_marker,
                    self_timeline_context,
                    title="自我时间线检索",
                    priority=67,
                    source="self_timeline",
                ) else "system_prompt"
                if placement == "system_prompt":
                    req.system_prompt = f"{req.system_prompt or ''}\n\n{timeline_marker}\n{self_timeline_context}".strip()
                await self._record_request_prompt_fragment(
                    event,
                    title="自我时间线检索",
                    key="self.timeline",
                    text=self_timeline_context,
                    source="self_timeline",
                    mode="group",
                    metadata={"注入位置": placement},
                )
        await self._append_reply_style_to_request(event, req, mode="group")
        await self._append_conditional_tool_instructions_to_request(event, req)
        await self._append_environment_perception_to_request(event, req)
        log_bookshelf_secret_skip("group_chat")
        return
    try:
        resolver = getattr(self, "_private_user_id_for_event", None)
        user_id = (
            resolver(event)
            if callable(resolver)
            else self._canonical_private_user_id(str(event.get_sender_id()))
        )
    except Exception:
        log_bookshelf_secret_skip("private_sender_missing")
        return
    raw_users = self.data.get("users", {})
    current_user = raw_users.get(user_id) if isinstance(raw_users, dict) else None
    if not isinstance(current_user, dict):
        log_bookshelf_secret_skip("private_user_missing")
        return
    if not self._private_passive_profile_available(user_id, current_user):
        log_bookshelf_secret_skip("private_user_disabled", current_user)
        return
    # Keep the extension context keyed to the canonical user even when the
    # persisted profile predates the user_id field.
    current_user = dict(current_user)
    current_user.setdefault("user_id", user_id)
    if not bool(getattr(event, "private_companion_skip_passive_input_status", False)):
        self._start_passive_input_status_loop(event, user_id)

    state = await self._ensure_daily_state(skip_conversation_summary=True, passive_fast=True)
    await self._append_private_active_period_boundary_to_request(event, req, state)
    inbound_text = _single_line(getattr(event, "message_str", "") or current_user.get("last_user_message"), 260)
    lightweight_passive = self._is_lightweight_private_passive_inbound(inbound_text)
    memo_query = self._memo_management_instruction_matches(inbound_text)
    if memo_query:
        lightweight_passive = False
    bookshelf_signal_getter = getattr(self, "_bookshelf_secret_signal_info", None)
    bookshelf_signal = bookshelf_signal_getter(inbound_text) if callable(bookshelf_signal_getter) else {}
    if lightweight_passive and isinstance(bookshelf_signal, dict) and bookshelf_signal.get("likely"):
        lightweight_passive = False
        logger.info(
            "[PrivateCompanion] 夹层密码请求退出轻量被动链路: user=%s direct=%s context=%s access=%s text=%s",
            user_id,
            ",".join(bookshelf_signal.get("direct_matches") or []) or "-",
            ",".join(bookshelf_signal.get("context_matches") or []) or "-",
            ",".join(bookshelf_signal.get("access_matches") or []) or "-",
            inbound_text,
        )
    prompt_surface = PromptSurface()
    reply_style_section = self._format_reply_style_prompt_section()
    reply_style_prompt = str(reply_style_section.get("content") or "")
    if reply_style_prompt:
        prompt_surface.add(
            "reply.style",
            reply_style_prompt,
            title=str(reply_style_section.get("title") or "回复风格约束"),
            priority=12,
            source="reply_style",
        )
    dialogue_outfit_continuity = self._format_dialogue_outfit_continuity_for_prompt(
        current_user,
        include_heading=False,
    )
    if dialogue_outfit_continuity:
        prompt_surface.add(
            "dialogue.outfit_continuity",
            dialogue_outfit_continuity,
            title="当前会话服装连续性",
            priority=13,
            source="daily_state",
        )
    routine_check_section = self._format_private_routine_check_boundary_section(inbound_text)
    routine_check_boundary = str(routine_check_section.get("content") or "")
    if routine_check_boundary:
        prompt_surface.add(
            "turn.routine_check_boundary",
            routine_check_boundary,
            title=str(routine_check_section.get("title") or "轻量例行检查边界"),
            priority=14,
            source="conversation",
        )
    if self._record_recent_private_fact_correction(current_user, inbound_text):
        self._schedule_data_save(sections={"users"})
    fact_attribution_guard = self._format_private_fact_attribution_guard(
        current_user,
        inbound_text,
        include_heading=False,
    )
    if fact_attribution_guard:
        prompt_surface.add(
            "identity.fact_attribution",
            fact_attribution_guard,
            title="事实主语与归属边界",
            priority=11,
            source="identity",
        )
    emotion_inertia_getter = getattr(self, "_format_emotion_inertia_prompt", None)
    if callable(emotion_inertia_getter):
        emotion_inertia = emotion_inertia_getter(current_user, include_heading=False)
        if emotion_inertia:
            prompt_surface.add(
                "state.emotion_inertia",
                emotion_inertia,
                title="情绪惯性",
                priority=29,
                source="emotion_ledger",
            )
    reunion_getter = getattr(self, "_format_private_reunion_prompt", None)
    if callable(reunion_getter):
        reunion_prompt = reunion_getter(
            current_user,
            inbound_text,
            include_heading=False,
        )
        if reunion_prompt:
            prompt_surface.add(
                "conversation.reunion",
                reunion_prompt,
                title="久别重逢的时间感",
                priority=27,
                source="conversation",
            )
            try:
                setattr(
                    event,
                    "_private_companion_reunion_observed_at",
                    _safe_float(current_user.get("last_inbound_gap_observed_at"), 0),
                )
            except Exception:
                pass
    preference_getter = getattr(self, "_format_bot_self_preference_consistency", None)
    if callable(preference_getter):
        preference_prompt = preference_getter(
            current_user,
            inbound_text,
            include_heading=False,
        )
        if preference_prompt:
            prompt_surface.add(
                "persona.preference_continuity",
                preference_prompt,
                title="Bot 自身偏好连续性",
                priority=16,
                source="bot_self_history",
            )
    state_changed = False
    state_update_reason = "legacy"
    if bool(runtime_persona_setting(self, "enable_passive_state_delta_injection", True)):
        session_key = _single_line(getattr(event, "unified_msg_origin", ""), 160) or f"private:{user_id}"
        state_update_sections, state_changed, state_update_reason = self._private_passive_state_update_for_prompt(
            session=session_key,
            state=state,
            current_user=current_user,
            inbound_text=inbound_text,
            lightweight=lightweight_passive,
            as_sections=True,
        )
        for index, state_update_section in enumerate(state_update_sections or []):
            prompt_surface.add(
                "state.session_update" if index == 0 else "state.reply_policy",
                state_update_section,
                title=str(state_update_section.get("title") or "Bot 自身模拟状态更新"),
                priority=30 + index,
                source="daily_state",
            )
    elif lightweight_passive:
        lightweight_injection = self._prepared_lightweight_state_injection(
            state,
            include_heading=False,
        )
        lightweight_injection = self._sanitize_schedule_context_for_private_user(lightweight_injection, current_user)
        prompt_surface.add(
            "state.lightweight",
            lightweight_injection,
            title="Bot 自身模拟状态",
            priority=30,
            source="daily_state",
        )
    else:
        state_injection = self._format_state_injection(state, include_heading=False)
        state_injection = self._sanitize_schedule_context_for_private_user(state_injection, current_user)
        prompt_surface.add(
            "state.full",
            state_injection,
            title="Bot 自身模拟状态",
            priority=30,
            source="daily_state",
        )
        life_context = self._format_life_context_injection(include_heading=False)
        life_context = self._sanitize_schedule_context_for_private_user(life_context, current_user)
        if life_context:
            prompt_surface.add(
                "life.context", life_context, title="Bot 模拟生活背景", priority=35, source="daily_state"
            )
        important_dates = self._format_important_dates_injection(include_heading=False)
        if important_dates:
            prompt_surface.add(
                "important.dates", important_dates, title="近期重要日期", priority=36, source="daily_state"
            )
        memo_section = prompt_section("备忘便签", "")
        if self._private_user_role(current_user, user_id) == "owner":
            memo_section = (
                self._format_memo_notes_prompt_section(
                    days=3650,
                    include_pinned=True,
                    limit=12,
                )
                if memo_query
                else self._format_memo_notes_prompt_section(
                    days=2,
                    include_pinned=False,
                    limit=4,
                )
            )
            memo_notes = str(memo_section.get("content") or "")
            if memo_query and not memo_notes:
                memo_notes = "当前没有进行中的便签。不要编造便签内容。"
        else:
            memo_notes = ""
        if memo_notes:
            prompt_surface.add(
                "memo.notes",
                memo_notes,
                title=str(memo_section.get("title") or "备忘便签"),
                priority=37,
                source="daily_state",
            )
        worldview_section = (
            self._format_worldview_adaptation_prompt_section()
            if self._feature_enabled_or_temp_unlocked("enable_environment_perception")
            and runtime_persona_setting(self, "enable_worldview_perception", True)
            else prompt_section("世界观适配", "")
        )
        worldview_context = str(worldview_section.get("content") or "")
        worldview_context = self._sanitize_owner_environment_context_for_private_user(worldview_context, current_user)
        if worldview_context:
            prompt_surface.add(
                "worldview.adaptation",
                worldview_context,
                title=str(worldview_section.get("title") or "世界观适配"),
                priority=37,
                source="worldview",
            )
    realtime_formatter = getattr(self, "_format_external_realtime_prompt_section", None)
    if callable(realtime_formatter):
        realtime_section = realtime_formatter(current_user, public=False)
        realtime_context = str(realtime_section.get("content") or "")
        if realtime_context:
            prompt_surface.add(
                "realtime.activity_continuity",
                realtime_context,
                title=str(realtime_section.get("title") or "实时共同活动与短期连续性"),
                # Render after daily schedule and recall fragments so the
                # current realtime fact is the last authoritative context.
                priority=76,
                source="external_realtime",
            )
    departure_getter = getattr(self, "_format_conversation_departure_prompt", None)
    if callable(departure_getter):
        departure_prompt = departure_getter(
            current_user,
            inbound_text,
            state,
            include_heading=False,
        )
        if departure_prompt:
            prompt_surface.add(
                "conversation.departure",
                departure_prompt,
                title="自然退场候选",
                priority=28,
                source="conversation",
            )
            self._schedule_data_save(sections={"users"})
    identity_anchor = self._format_private_identity_anchor_for_prompt(
        user_id,
        current_user,
        event,
        include_heading=False,
    )
    if identity_anchor:
        prompt_surface.add(
            "identity.anchor", identity_anchor, title="私聊身份锚点", priority=10, source="identity"
        )
    recent_atrelay_context = self._format_recent_atrelay_context_for_prompt(
        kind="private",
        target=user_id,
        sender_id=user_id,
        current_text=inbound_text,
        limit=2,
        include_heading=False,
    )
    if recent_atrelay_context:
        prompt_surface.add(
            "atrelay.recent",
            recent_atrelay_context,
            title="刚刚的转述动作",
            priority=26,
            source="tools",
        )
    if not self._format_atrelay_target_summary_for_prompt(inbound_text):
        mentioned_worldbook = self._format_worldbook_private_mentions_for_prompt(
            inbound_text,
            limit=4,
            include_heading=False,
        )
        if mentioned_worldbook:
            prompt_surface.add(
                "worldbook.mentions",
                mentioned_worldbook,
                title="本轮提到的关系网对象",
                priority=55,
                source="worldbook",
            )
    environment_section = await self._format_passive_environment_prompt_section(
        event,
        lightweight=lightweight_passive,
    )
    environment_fragment = str(environment_section.get("content") or "")
    environment_fragment = self._sanitize_owner_environment_context_for_private_user(environment_fragment, current_user)
    if environment_fragment:
        prompt_surface.add(
            "environment.lightweight" if lightweight_passive else "environment.perception",
            environment_fragment,
            title=str(environment_section.get("title") or "环境感知"),
            priority=20,
            source="environment",
        )
    rest_backlog_prompt = self._take_rest_reply_backlog_prompt(current_user)
    if rest_backlog_prompt:
        prompt_surface.add(
            "rest.backlog",
            rest_backlog_prompt,
            title="休息期间消息承接",
            priority=25,
            source="daily_state",
        )
    busy_delay = _safe_float(getattr(event, "private_companion_busy_reply_delay_seconds", 0.0), 0.0)
    if busy_delay > 0:
        busy_schedule = _single_line(
            getattr(event, "private_companion_busy_reply_schedule", ""),
            180,
        )
        busy_reply_boundary = (
            "当前日程正在专注处理事情，这轮消息已经自然晚了一点才看到。回复可以比平时更短、更聚焦，但必须完整回答用户真正问的内容。\n"
            "不要汇报延迟秒数，不要说系统排队、闸门、后台或提示词，也不要每次都机械道歉；除非用户追问，通常不必主动解释为什么晚回。"
            + (f"\n当前忙碌片段：{busy_schedule}" if busy_schedule else "")
        )
        prompt_surface.add(
            "busy.reply_boundary",
            busy_reply_boundary,
            title="忙碌中的回复节奏",
            priority=31,
            source="daily_state",
        )
    try:
        sleeping, _sleep_runtime, _sleep_item, sleep_schedule_text = self._rest_reply_sleep_context()
    except Exception:
        sleeping, sleep_schedule_text = False, ""
    if sleeping:
        sleep_reply_boundary = (
            "当前处于睡眠/休息延续。用户如果只是短句叫醒、查岗、例行检查、确认在不在，回复要短、迷糊、低打扰，通常 1 句即可。\n"
            "不要因为记忆里有相似旧话题就展开长段回忆、梦境、解释或连续追问；旧记忆只做语气底色，不要新编具体梦境内容。\n"
            "如果用户没有明确提出新请求，回复后应自然收住，表现为可以睡回去。"
            + (f"\n当前休息片段：{_single_line(sleep_schedule_text, 160)}" if sleep_schedule_text else "")
        )
        prompt_surface.add(
            "rest.sleep_reply_boundary",
            sleep_reply_boundary,
            title="休息中被叫醒回复边界",
            priority=32,
            source="daily_state",
        )
    is_wake_event = bool(getattr(event, "is_wake", False)) or bool(
        getattr(event, "is_at_or_wake_command", False)
    )
    group_share_reply_context_getter = getattr(self, "_format_recent_group_share_snapshot_for_reply", None)
    if callable(group_share_reply_context_getter):
        group_share_reply_context = group_share_reply_context_getter(
            current_user,
            inbound_text,
            event_umo=_single_line(getattr(event, "unified_msg_origin", ""), 180),
            as_section=True,
        )
        if group_share_reply_context:
            prompt_surface.add(
                "group_share.reply_source",
                group_share_reply_context,
                priority=44,
                source="group_observation",
            )
    if not is_wake_event:
        proactive_sections = await self._format_proactive_reply_context(
            event,
            as_sections=True,
        )
        for index, section in enumerate(proactive_sections or []):
            prompt_surface.add(
                f"proactive.reply_context.{index}",
                section,
                priority=45 + index,
                source="proactive",
            )
    short_reaction_context = self._format_short_reaction_context_for_prompt(
        current_user,
        inbound_text,
        include_heading=False,
    )
    if short_reaction_context:
        prompt_surface.add(
            "turn.short_reaction",
            short_reaction_context,
            title="本轮短反应锚点",
            priority=48,
            source="conversation",
        )
    if re.search(r"(说过|讲过|提过|聊过|发过|说了|讲了|提了).{0,4}(啦|了|呀|啊)?$", inbound_text):
        prompt_surface.add(
            "turn.repeat_correction_boundary",
            (
                "用户是在提醒你刚才/前面已经说过。回复只需要短短认一下，不要编造“几小时前/几分钟前”等具体时间差，"
                "也不要把回复写成“你希望我换个话题还是继续聊”的选项题。更自然的做法是：承认自己刚才没接稳，然后收住或自己轻轻换一个具体小切口。"
            ),
            title="用户纠正重复话题",
            priority=49,
            source="conversation",
        )
    reply_chain_context_getter = getattr(self, "_format_reply_chain_context_for_prompt", None)
    if callable(reply_chain_context_getter) and not bool(getattr(event, "private_companion_reply_chain_context_injected", False)):
        try:
            reply_chain_context = await reply_chain_context_getter(
                event,
                include_heading=False,
            )
        except Exception as exc:
            logger.debug("[PrivateCompanion] 引用链上下文读取失败: %s", _single_line(exc, 120))
            reply_chain_context = ""
        if reply_chain_context:
            prompt_surface.add(
                "reply.chain",
                reply_chain_context,
                title="引用链上下文",
                priority=54,
                source="forward_message",
            )
    private_image_enhancement_enabled_for_request = self._feature_enabled_or_temp_unlocked(
        "enable_private_image_self_recognition"
    )
    buffered_image_context = (
        self._take_buffered_private_image_context_for_event(event)
        if private_image_enhancement_enabled_for_request
        else {}
    )
    buffered_image_from_handoff = bool(
        isinstance(buffered_image_context, dict)
        and buffered_image_context.get("from_handoff")
    )
    buffered_images = (
        [str(item) for item in buffered_image_context.get("images", []) if str(item or "").strip()]
        if isinstance(buffered_image_context, dict)
        else []
    )
    buffered_image_vision = ""
    delayed_image_sources = getattr(event, "private_companion_delayed_image_sources", [])
    if not buffered_images and isinstance(delayed_image_sources, list):
        buffered_images = [str(item) for item in delayed_image_sources[:5] if str(item or "").strip()]
    buffered_image_vision_limit = self._private_image_vision_text_limit(len(buffered_images))
    if isinstance(buffered_image_context, dict):
        buffered_image_vision = _single_line(buffered_image_context.get("vision_text"), buffered_image_vision_limit)
    delayed_image_vision = _single_line(
        getattr(event, "private_companion_delayed_image_vision_text", ""),
        buffered_image_vision_limit,
    )
    if delayed_image_vision and not buffered_image_vision:
        buffered_image_vision = delayed_image_vision
    buffered_image_mode = _single_line(buffered_image_context.get("image_mode"), 20) if isinstance(buffered_image_context, dict) else ""
    delayed_image_mode = _single_line(getattr(event, "private_companion_delayed_image_mode", ""), 20)
    if not buffered_image_mode and delayed_image_mode:
        buffered_image_mode = delayed_image_mode
    vision_task = buffered_image_context.get("vision_task") if isinstance(buffered_image_context, dict) else None
    if not buffered_image_vision and isinstance(vision_task, asyncio.Task):
        vision_wait_timeout = self._private_image_vision_wait_budget_seconds()
        try:
            if vision_wait_timeout > 0:
                buffered_image_vision = _single_line(await asyncio.wait_for(asyncio.shield(vision_task), timeout=vision_wait_timeout), buffered_image_vision_limit)
        except asyncio.TimeoutError:
            logger.info("[PrivateCompanion] 私聊图片视觉转述仍在进行,本轮先注入路径兜底: timeout=%.1fs", vision_wait_timeout)
        except Exception as exc:
            logger.info("[PrivateCompanion] 私聊图片视觉转述获取失败: %s", _single_line(exc, 120))
    buffered_images_include_gif = (
        bool(runtime_persona_setting(self, "enable_private_image_gif_enhancement", True))
        and self._private_image_sources_include_gif(buffered_images)
        if buffered_images
        else False
    )
    if (
        buffered_images
        and not buffered_image_vision
        and buffered_image_mode != "no_vision"
        and (
            buffered_images_include_gif
            or (
                buffered_image_mode == "direct"
                and (
                    buffered_image_from_handoff
                    or not self._event_main_provider_supports_image(event)
                )
            )
        )
    ):
        buffered_image_vision = _single_line(
            await self._transcribe_private_inbound_images(
                buffered_images,
                umo=str(getattr(event, "unified_msg_origin", "") or ""),
            ),
            buffered_image_vision_limit,
        )
    combined_text = ""
    private_buffer_key = self._semantic_buffer_key(f"private:{user_id}", user_id)
    private_buffer_snapshot = self._semantic_buffer_active_snapshot(private_buffer_key, force=True)
    if (
        not private_image_enhancement_enabled_for_request
        and isinstance(private_buffer_snapshot, dict)
        and _single_line(private_buffer_snapshot.get("kind"), 40) == "image"
    ):
        buffers = getattr(self, "_semantic_message_buffers", None)
        if isinstance(buffers, dict):
            buffers.pop(private_buffer_key, None)
        private_buffer_snapshot = {}
    private_buffer_active = bool(private_buffer_snapshot)
    if not lightweight_passive or buffered_images or private_buffer_active:
        combined_text = await self._consume_semantic_message_buffer_for_event(event, private_chat=True)
    if combined_text:
        prompt_surface.add(
            "turn.continuation",
            "用户刚刚在短时间内连续补充了几句,请把它们当作同一轮完整发言理解,不要逐条回复,也不要表现得像用户重复催促：\n"
            f"{combined_text}",
            title="本轮用户连续补充",
            priority=50,
            source="message_debounce",
        )
        inbound_text = _single_line(combined_text.replace("\n", " "), 260)
    if self._user_asks_recalled_messages(inbound_text):
        prompt_surface.add(
            "recall.query",
            self._format_recalled_messages_for_natural_query(
                event,
                limit=5,
                include_heading=False,
            ),
            title="撤回消息查询",
            priority=52,
            source="recall",
        )
    food_menu_context = (
        self._format_food_menu_for_reply(
            inbound_text,
            limit=3,
            user=current_user,
            include_heading=False,
        )
        if self._feature_enabled_or_temp_unlocked("enable_food_menu_recommendation")
        else ""
    )
    meal_care_reply_context = self._format_meal_care_reply_context(
        current_user,
        inbound_text,
        include_heading=False,
    )
    if meal_care_reply_context:
        prompt_surface.add(
            "food.meal_care",
            meal_care_reply_context,
            title="吃饭关心承接",
            priority=55,
            source="food",
        )
    if food_menu_context:
        prompt_surface.add(
            "food.menu",
            food_menu_context,
            title="吃饭候选",
            priority=53,
            source="food",
        )
    if (
        buffered_images
        and buffered_image_vision
        and buffered_image_mode != "no_vision"
        and self._private_image_user_has_specific_vision_request(inbound_text)
    ):
        contextual_vision = _single_line(
            await self._transcribe_private_inbound_images(
                buffered_images,
                umo=str(getattr(event, "unified_msg_origin", "") or ""),
                user_text=inbound_text,
                force_contextual=True,
            ),
            buffered_image_vision_limit,
        )
        if contextual_vision:
            buffered_image_vision = contextual_vision
    if buffered_images:
        direct_image_mounted = False
        if (
            not buffered_image_from_handoff
            and buffered_image_mode == "direct"
            and self._event_main_provider_supports_image(event)
            and not buffered_images_include_gif
        ):
            image_refs: list[str] = []
            for image_ref in buffered_images[:5]:
                for request_ref in self._private_image_sources_for_astrbot_request([image_ref]):
                    if request_ref not in image_refs:
                        image_refs.append(request_ref)
            if not image_refs:
                logger.info(
                    "[PrivateCompanion] 私聊延迟图片无模型可读源,跳过直接挂图: user=%s images=%s",
                    user_id,
                    len(buffered_images),
                )
                buffered_image_mode = (
                    "caption"
                    if self._has_private_image_visual_provider(str(getattr(event, "unified_msg_origin", "") or ""))
                    else "no_vision"
                )
            else:
                existing = getattr(req, "image_urls", None)
                if not isinstance(existing, list):
                    existing = []
                for image_ref in image_refs:
                    if image_ref not in existing:
                        existing.append(image_ref)
                req.image_urls = existing
                logger.info(
                    "[PrivateCompanion] 私聊延迟图片已挂回视觉主模型: user=%s images=%s mounted=%s",
                    user_id,
                    len(buffered_images),
                    len(image_refs),
                )
                try:
                    await self._refresh_default_persona_prompt(str(getattr(event, "unified_msg_origin", "") or ""))
                except Exception as exc:
                    logger.debug("[PrivateCompanion] 图片直挂刷新人格缓存失败: %s", exc)
                direct_role_hint = self._private_image_direct_role_appearance_prompt(
                    include_heading=False,
                )
                if direct_role_hint:
                    prompt_surface.add(
                        "image.direct",
                        direct_role_hint,
                        title="当前角色外貌",
                        priority=55,
                        source="private_image",
                    )
                direct_image_mounted = True
        if not direct_image_mounted and buffered_image_vision:
            intent_line = self._private_image_intent_line(buffered_image_vision)
            ownership_line = self._private_image_ownership_line(buffered_image_vision)
            reply_objective = self._private_image_reply_objective(ownership_line, vision_text=buffered_image_vision, user_text=inbound_text)
            logger.info(
                "[PrivateCompanion] 私聊延迟图片已注入视觉摘要: user=%s chars=%s intent=%s ownership=%s objective=%s preview=%s",
                user_id,
                len(buffered_image_vision),
                intent_line or "无",
                ownership_line or "无",
                _single_line(reply_objective, 120),
                _single_line(buffered_image_vision, 220),
            )
            image_context_intro = (
                "用户刚刚只发了一张图片,没有继续补充文字。"
                if bool(getattr(event, "private_companion_deferred_private_image_only_ready", False))
                else "用户刚刚先单独发了一张图片,随后补充了文字。"
            )
            prompt_surface.add(
                "image.vision",
                f"{image_context_intro}下面是这张图的视觉摘要；请按摘要理解当前图片，不要说没看到图。"
                "只回应本轮图片和用户文字，不要提模型、插件或路径。"
                "如果最近对话里用户明确规定了这张/下一张图片的回复方式（例如只回复某句话、不要回复其他内容）,必须优先照做。\n"
                f"{self._private_image_identity_disambiguation_instruction()}\n"
                f"{reply_objective}\n"
                f"{buffered_image_vision}",
                title="本轮延迟图片",
                priority=55,
                source="private_image",
            )
            await self._memory_companion_record_image_observation(
                event,
                content=buffered_image_vision,
                image_count=len(buffered_images),
                source="current_private_image",
                user_id=user_id,
                user_name=_single_line(current_user.get("nickname") or current_user.get("display_name") or user_id, 80),
            )
        else:
            image_context_intro = (
                "用户刚刚只发了一张图片,没有继续补充文字。"
                if bool(getattr(event, "private_companion_deferred_private_image_only_ready", False))
                else "用户刚刚先单独发了一张图片,随后补充了文字。"
            )
            prompt_surface.add(
                "image.fallback",
                f"{image_context_intro}图片已暂存，但暂无可靠视觉摘要；"
                "如果用户问图片内容，请自然说暂时没看清，不要编造画面。\n"
                + "\n".join(f"- {path}" for path in buffered_images),
                title="本轮延迟图片",
                priority=55,
                source="private_image",
            )
    elif bool(getattr(event, "private_companion_deferred_private_image_only_ready", False)):
        if buffered_image_vision:
            prompt_surface.add(
                "image.only.vision",
                "用户只发了一张图片。下面是这张图的视觉摘要；请自然接住图片内容或表达意图，不要提处理过程。"
                "如果最近对话里用户明确规定了这张/下一张图片的回复方式（例如只回复某句话、不要回复其他内容）,必须优先照做。\n"
                f"{buffered_image_vision}",
                title="本轮延迟图片",
                priority=55,
                source="private_image",
            )
            await self._memory_companion_record_image_observation(
                event,
                content=buffered_image_vision,
                image_count=max(1, len(buffered_images)),
                source="current_private_image",
                user_id=user_id,
                user_name=_single_line(current_user.get("nickname") or current_user.get("display_name") or user_id, 80),
            )
        else:
            prompt_surface.add(
                "image.only.fallback",
                "用户只发了一张图片，但当前没有可靠图片内容；请自然表示暂时没看清，可以请用户补一句，不要编造画面。",
                title="本轮延迟图片",
                priority=55,
                source="private_image",
            )
    reply_image_sources: list[str] = []
    reply_image_prompt_anchor = ""
    skip_reply_image_for_forward_context = bool(getattr(event, "private_companion_forward_context_injected", False))
    if skip_reply_image_for_forward_context:
        logger.info("[PrivateCompanion] 本轮已注入合并消息上下文,跳过引用图片重复视觉: user=%s", user_id)
    if (
        not skip_reply_image_for_forward_context
        and not buffered_images
        and not bool(getattr(event, "private_companion_deferred_private_image_only_ready", False))
    ):
        reply_image_sources = await self._find_reply_image_sources_for_event(event)
        if reply_image_sources:
            reply_image_limit = self._private_image_vision_text_limit(len(reply_image_sources))
            reply_image_vision = _single_line(
                getattr(event, "private_companion_reply_image_vision_text", ""),
                reply_image_limit,
            )
            if not reply_image_vision:
                reply_image_vision = _single_line(
                    await self._transcribe_private_inbound_images(
                        reply_image_sources,
                        umo=str(getattr(event, "unified_msg_origin", "") or ""),
                        user_text=inbound_text,
                        force_contextual=self._private_image_user_has_specific_vision_request(inbound_text),
                    ),
                    reply_image_limit,
                )
            if reply_image_vision:
                intent_line = self._private_image_intent_line(reply_image_vision)
                ownership_line = self._private_image_ownership_line(reply_image_vision)
                reply_objective = self._private_image_reply_objective(ownership_line, vision_text=reply_image_vision, user_text=inbound_text)
                logger.info(
                    "[PrivateCompanion] 私聊引用图片已注入视觉摘要: user=%s images=%s intent=%s ownership=%s objective=%s preview=%s",
                    user_id,
                    len(reply_image_sources),
                    intent_line or "无",
                    ownership_line or "无",
                    _single_line(reply_objective, 120),
                    _single_line(reply_image_vision, 220),
                )
                reply_image_prompt_anchor = (
                    f"用户本轮是在问被引用图片：{inbound_text or '（空）'}。\n"
                    "下面摘要只属于这一次被引用的图片；请把它作为当前问题的主要依据。\n"
                    f"{reply_objective}\n"
                    f"{reply_image_vision}"
                )
                prompt_surface.add(
                    "image.reply.vision",
                    f"用户这轮引用/回复了一张图片,并发送文字：{inbound_text or '（空）'}。\n"
                    "下面是被引用图片的视觉摘要；请优先回答用户当前文字针对这张图提出的问题。\n"
                    f"{self._private_image_identity_disambiguation_instruction()}\n"
                    f"{reply_objective}\n"
                    f"{reply_image_vision}",
                    title="本轮引用图片",
                    priority=55,
                    source="private_image",
                )
                await self._memory_companion_record_image_observation(
                    event,
                    content=reply_image_vision,
                    image_count=len(reply_image_sources),
                    source="reply_image",
                    user_id=user_id,
                    user_name=_single_line(current_user.get("nickname") or current_user.get("display_name") or user_id, 80),
                )
                image_keys = self._private_image_cache_image_keys(reply_image_sources)
                if image_keys:
                    try:
                        async with self._data_lock:
                            user = self._get_user(user_id)
                            user["last_private_image_vision_feedback_target"] = {
                                "ts": _now_ts(),
                                "image_keys": image_keys,
                                "vision_text": _single_line(reply_image_vision, reply_image_limit),
                                "reply": "",
                                "ownership": ownership_line,
                                "intent": intent_line,
                                "source": "reply_image",
                            }
                            self._save_data_sync(sections={"users"})
                    except Exception as exc:
                        logger.debug("[PrivateCompanion] 私聊引用图片视觉反馈目标记录失败: %s", exc)
                try:
                    setattr(event, "private_companion_reply_image_vision_text", _single_line(reply_image_vision, reply_image_limit))
                    setattr(event, "private_companion_reply_image_count", len(reply_image_sources))
                    setattr(event, "private_companion_reply_image_user_text", inbound_text)
                    setattr(
                        event,
                        "private_companion_reply_image_content_question",
                        self._private_image_user_asks_content(inbound_text),
                    )
                except Exception:
                    pass
            else:
                prompt_surface.add(
                    "image.reply.fallback",
                    f"用户这轮引用/回复了一张图片,并发送文字：{inbound_text or '（空）'}。"
                    "当前未能拿到可用视觉摘要；如果用户问图片内容，请自然说明暂时没看清，不要编造。",
                    title="本轮引用图片",
                    priority=55,
                    source="private_image",
                )
    if reply_image_prompt_anchor:
        prompt_surface.add(
            "image.reply.anchor",
            reply_image_prompt_anchor,
            title="当前引用图片锚点",
            priority=4,
            source="private_image",
        )
    if lightweight_passive:
        log_bookshelf_secret_skip("lightweight_passive", current_user, inbound_text)
    if not lightweight_passive:
        collected_contexts = await self._collect_private_passive_prompt_contexts(
            event,
            req,
            inbound_text=inbound_text,
            current_user=current_user,
            is_private_chat=is_private_chat,
        )
        creative_reply_context = next(
            (
                str(item.get("content") or "").strip()
                for item in collected_contexts
                if isinstance(item, dict) and _single_line(item.get("key"), 80) == "creative.hidden"
            ),
            "",
        )
        if creative_reply_context:
            setattr(event, "private_companion_creative_reply_context", creative_reply_context)
        self._add_collected_prompt_contexts(prompt_surface, collected_contexts)
    static_fragment_keys = {"reply.style"}
    (
        static_injection,
        dynamic_injection,
        static_prompt_modules,
        dynamic_prompt_modules,
    ) = prompt_surface.render_partition_with_fragments(
        lambda fragment: fragment.normalized_key() in static_fragment_keys
    )
    injection = "\n\n".join(part for part in (static_injection, dynamic_injection) if part)
    static_marker = "<!-- private_companion_static_v1 -->"
    marker = "<!-- private_companion_state_v1 -->"
    current_prompt = req.system_prompt or ""
    if self._request_has_managed_prompt_marker(req, marker):
        log_bookshelf_secret_skip("state_marker_already_present", current_user, inbound_text)
        await self._append_conditional_tool_instructions_to_request(event, req)
        return
    if not injection:
        logger.debug("[PrivateCompanion] 被动状态提示词片段为空,跳过状态 marker 注入")
        log_bookshelf_secret_skip("empty_passive_injection", current_user, inbound_text)
        await self._append_conditional_tool_instructions_to_request(event, req)
        return
    static_placement = ""
    dynamic_placement = ""
    conversation_plan = get_conversation_injection_plan(req)
    if static_injection and not self._request_has_managed_prompt_marker(req, static_marker):
        current_prompt = req.system_prompt or ""
        req.system_prompt = f"{current_prompt}\n\n{static_marker}\n{static_injection}".strip()
        static_placement = "system_prompt"
        if conversation_plan is not None:
            conversation_plan.add(
                key="passive.static",
                marker=static_marker,
                content=static_injection,
                title="稳定回复约束",
                priority=12,
                source="passive_state",
                placement=PLACEMENT_STABLE_SYSTEM,
                temporary=False,
                materialized=True,
                structured=True,
                metadata={"batch": True},
                children=static_prompt_modules,
            )
    if dynamic_injection:
        if not self._append_turn_prompt_fragment_by_position(
            req,
            marker,
            dynamic_injection,
            title="本轮回复上下文",
            priority=40,
            source="passive_state",
            structured=True,
        ):
            dynamic_placement = "system_prompt"
            current_prompt = req.system_prompt or ""
            req.system_prompt = f"{current_prompt}\n\n{marker}\n{dynamic_injection}".strip()
        else:
            dynamic_placement = _single_line(
                getattr(req, "_private_companion_turn_prompt_placement", "prompt"),
                40,
            ) or "prompt"
        if conversation_plan is not None:
            conversation_plan.annotate_marker(
                marker,
                metadata={"batch": True},
                children=dynamic_prompt_modules,
            )
    injection_placement = "+".join(part for part in (static_placement, dynamic_placement) if part) or "none"
    await self._append_conditional_tool_instructions_to_request(event, req)
    state_log_parts = [
        f"心理能量={state.get('energy', 70)}/100",
        f"情绪底色={state.get('mood_bias', '平稳')}",
    ]
    weather = _single_line(state.get("weather"), 80)
    if self._private_user_role(current_user or {}) == "friend":
        weather = ""
    if weather and weather != "暂无天气信息":
        state_log_parts.append(f"天气={weather}")
    current_item = self._get_current_plan_item(self.data.get("daily_plan", {}))
    current_schedule = self._sanitize_schedule_context_for_private_user(
        self._format_plan_item_for_prompt(current_item),
        current_user,
    ) or "无当前日程"
    recorder = getattr(self, "_record_prompt_injection_snapshot", None)
    if callable(recorder):
        await recorder(
            kind="passive",
            session=_single_line(getattr(event, "unified_msg_origin", ""), 160) or self._event_scope_key(event),
            title="被动回复注入",
            text=injection,
            mode="light" if lightweight_passive else "full",
            trace_id=self._prompt_injection_trace_id_for_event(event),
            message_preview=self._prompt_injection_message_preview_for_event(event),
            sender_label=self._prompt_injection_sender_label_for_event(event),
            modules=prompt_surface.rendered_fragments(),
            metadata={
                "状态": "｜".join(state_log_parts),
                "当前日程": current_schedule,
                "注入位置": injection_placement,
                "状态注入模式": "增量"
                if bool(runtime_persona_setting(self, "enable_passive_state_delta_injection", True))
                else "完整",
                "状态变化": "是" if state_changed else "否",
                "状态触发": state_update_reason,
                "会话": _single_line(getattr(event, "unified_msg_origin", ""), 160) or "unknown",
                "发送者": _single_line(self._event_sender_id(event), 80),
            },
        )
    logger.info(
        "[PrivateCompanion] 已注入被动状态提示词到 %s: mode=%s state_mode=%s reason=%s placement=%s chars=%s 状态=%s；当前日程=%s",
        _single_line(getattr(event, "unified_msg_origin", ""), 80) or "unknown_session",
        "light" if lightweight_passive else "full",
        "delta" if bool(runtime_persona_setting(self, "enable_passive_state_delta_injection", True)) else "legacy",
        state_update_reason,
        injection_placement,
        len(injection),
        "｜".join(state_log_parts),
        current_schedule,
    )
