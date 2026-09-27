# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import re  # noqa: F401 (阶段模块经 _psp_host 代理使用)
from typing import Any, Iterable


from .conversation_injection_plan import (
    DELIVERY_GROUP_MARKER_METADATA_KEY,
    PLACEMENT_DYNAMIC_SYSTEM,
    PLACEMENT_STABLE_SYSTEM,  # noqa: F401 (阶段模块经 _psp_host 代理使用)
    PLACEMENT_TURN_TAIL,
    get_conversation_injection_plan,
)
from .conversation_prompt_section import PromptSection, prompt_section, render_prompt_sections
from .group_prompt_context import (
    GROUP_HISTORY_INJECTED_ATTR,
    group_prompt_context_history_count,
)
# noqa: F401 (_now_ts / _safe_float 由阶段模块经 _psp_host 代理使用)
from .helpers import _now_ts, _safe_float, _single_address, _single_line
from .persona_config import runtime_persona_setting
from .prompt_surface import PromptSurface  # noqa: F401 (阶段模块经 _psp_host 代理使用)
from .logging_util import get_module_logger
from .passive_state_pipeline_shared import _PASSIVE_STAGE_STOP, _PassiveStageContext
from .passive_state_pipeline_part01 import _passive_state_stage_1
from .passive_state_pipeline_part02 import _passive_state_stage_2
from .passive_state_pipeline_part03 import _passive_state_stage_3
from .passive_state_pipeline_part04 import _passive_state_stage_4
from .passive_state_pipeline_part05 import (  # noqa: F401 (兼容 passive_state_pipeline.X 旧命名空间)
    _turn_continuation_prompt_section,
    _deferred_private_image_prompt_section,
    _reply_private_image_prompt_section,
    _persona_core_emphasis_prompt_section,
    _neutralize_stale_reaction_feedback_compat,
)

logger = get_module_logger(__name__)


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

    def place_sections(
        marker: str,
        sections: Iterable[PromptSection],
        *,
        priority: int,
        force_dynamic: bool = False,
    ) -> str:
        authored = tuple(
            section
            for section in sections
            if isinstance(section, PromptSection)
            and render_prompt_sections([section], mode="body_only").strip()
        )
        if not authored:
            return "none"
        position = str(
            runtime_persona_setting(self, "passive_injection_position", "prompt")
            or "prompt"
        ).strip().lower()
        normalizer = getattr(self, "_normalize_passive_injection_position", None)
        if callable(normalizer):
            position = str(normalizer(position) or "prompt")
        use_system_prompt = position == "system_prompt" and not force_dynamic
        plan = get_conversation_injection_plan(req)
        if plan is None:
            raise RuntimeError("conversation injection plan is unavailable")
        if not plan.contains_marker(marker):
            for index, section in enumerate(authored):
                plan.add(
                    section=section,
                    marker=marker if index == 0 else "",
                    priority=priority,
                    placement=(
                        PLACEMENT_DYNAMIC_SYSTEM
                        if use_system_prompt
                        else PLACEMENT_TURN_TAIL
                    ),
                    materialized=False,
                    metadata={DELIVERY_GROUP_MARKER_METADATA_KEY: marker},
                )
        plan.render_into(req, prefer_extra_user_content=True)
        if use_system_prompt:
            return "system_prompt"
        return str(
            getattr(req, "_private_companion_turn_prompt_placement", "prompt")
            or "prompt"
        )

    def place_section(
        marker: str,
        section: PromptSection,
        *,
        priority: int,
        force_dynamic: bool = False,
    ) -> str:
        return place_sections(
            marker,
            (section,),
            priority=priority,
            force_dynamic=force_dynamic,
        )

    if not self.enabled:
        return
    feedback_recorder = getattr(self, "_record_photo_reference_feedback_from_event", None)
    if callable(feedback_recorder):
        try:
            feedback_recorder(event)
        except Exception as exc:
            logger.debug(
                "记录参考图效果反馈失败: %s",
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
    reaction_history_neutralizer = getattr(
        self, "_neutralize_stale_reaction_feedback_in_history", None
    )
    if callable(reaction_history_neutralizer):
        try:
            reaction_history_neutralizer(event, req)
        except Exception as exc:
            # Historical cleanup must never prevent the provider request itself.
            logger.debug(
                "清理历史反应标签失败: %s",
                _single_line(exc, 120),
            )
    else:
        # Older hot-loaded plugin objects may not carry the method even though
        # this pipeline module has been updated. Keep the request alive and
        # retain the same historical-tag cleanup semantics.
        try:
            _neutralize_stale_reaction_feedback_compat(req)
        except Exception as exc:
            logger.debug(
                "兼容清理历史反应标签失败: %s",
                _single_line(exc, 120),
            )
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
            portrait_preferred_address = ""
            portrait_address_reader = getattr(
                self, "_req036_preferred_address_from_portrait", None
            )
            if callable(portrait_address_reader):
                try:
                    portrait_preferred_address = _single_line(
                        await portrait_address_reader(private_user), 24
                    )
                except Exception as exc:
                    logger.debug(
                        "当前对象画像称呼读取失败，保留既有称呼: %s",
                        _single_line(exc, 120),
                    )
            preferred_address = portrait_preferred_address or _single_address(
                private_user.get("nickname")
                or runtime_persona_setting(self, "default_nickname", "你"),
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
                "非目标/未启用私聊跳过陪伴被动增强: user=%s reason=%s",
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
            "睡眠/休息回复闸门放行本轮被动回复: session=%s reason=%s",
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
        wardrobe_appender = getattr(self, "_append_group_wardrobe_to_request", None)
        if callable(wardrobe_appender):
            await wardrobe_appender(event, req)
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
                )
                expression_section = group_expression_selection.get("section")
                expression_voice = (
                    str(expression_section.content or "")
                    if isinstance(expression_section, PromptSection)
                    else ""
                )
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
                    placement = place_section(
                        expression_marker,
                        expression_section,
                        priority=58,
                    )
                    await self._record_request_prompt_fragment(
                        event,
                        title="群聊表达底色注入",
                        key="expression.voice",
                        text=expression_voice,
                        source="expression",
                        mode="group",
                        metadata={"注入位置": placement, "范围": "全局抽象表达底色"},
                    )
        try:
            setattr(event, GROUP_HISTORY_INJECTED_ATTR, False)
        except Exception:
            pass
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
                    extra_sections: list[PromptSection] = []
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
                                    key="turn.high_intensity_continuation",
                                    title="本轮高强度合并消息",
                                    source="message_debounce",
                                    template=(
                                        "群里刚刚短时间内多次叫到你，下面这些消息已压缩为同一轮理解背景；"
                                        "只挑最相关的一点短答，不要逐条回应：\n{messages}"
                                    ),
                                    variables={"messages": combined_text},
                                )
                            )
                        else:
                            extra_sections.append(
                                _turn_continuation_prompt_section(
                                    combined_text,
                                    private_chat=False,
                                )
                            )
                    wakeup_effect = getattr(event, "private_companion_group_wakeup_state_effect", None)
                    wakeup_state_section: PromptSection | None = None
                    needs_daily_state = bool(
                        passive_states_enabled
                        and isinstance(wakeup_effect, dict)
                        and wakeup_effect
                    )
                    async def _load_group_daily_state() -> dict[str, Any] | None:
                        if not needs_daily_state:
                            return None
                        try:
                            return await self._ensure_daily_state()
                        except Exception:
                            return self.data.get("daily_state", {})

                    async def _load_group_slang_embedding() -> PromptSection | None:
                        try:
                            return await self._group_slang_embedding_prompt_section(
                                group,
                                str(event.message_str or ""),
                            )
                        except Exception as exc:
                            logger.debug(
                                "[PrivateCompanion] 群黑话嵌入上下文生成失败: %s",
                                _single_line(exc, 120),
                            )
                            return None

                    # 将可能触网的 daily_state 与黑话 embedding 并行拉取，避免串行等待拉长回复
                    state, slang_embedding_section = await asyncio.gather(
                        _load_group_daily_state(),
                        _load_group_slang_embedding(),
                    )
                    if needs_daily_state and isinstance(state, dict):
                        wakeup_state_section = self._format_group_wakeup_humanized_prompt_section(
                            wakeup_effect,
                            state,
                        )
                    if self._user_asks_recalled_messages(text_for_mark):
                        recall_section = self._format_recalled_messages_for_natural_query_prompt_section(
                            event,
                            limit=5,
                        )
                        if recall_section.content:
                            extra_sections.append(recall_section)
                    passive_group_formatter = getattr(self, "_format_group_passive_reply_context_for_prompt", None)
                    if not callable(passive_group_formatter):
                        raise TypeError("group prompt context producer must return PromptSection")
                    group_context_section = passive_group_formatter(
                        group,
                        sender_id,
                        str(event.message_str or ""),
                    )
                    if not isinstance(group_context_section, PromptSection):
                        raise TypeError("group prompt context producer must return PromptSection")
                    group_sections: list[PromptSection] = []
                    if slang_embedding_section is not None and slang_embedding_section.content:
                        group_sections.append(slang_embedding_section)
                    high_intensity_for_context = getattr(event, "private_companion_group_high_intensity", None)
                    recent_atrelay_section = self._format_recent_atrelay_context_prompt_section(
                        kind="group",
                        target=group_id,
                        sender_id=sender_id,
                        current_text=str(event.message_str or ""),
                        limit=2,
                    )
                    if recent_atrelay_section.content:
                        group_sections.append(recent_atrelay_section)
                    if wakeup_state_section is not None and wakeup_state_section.content:
                        group_sections.append(wakeup_state_section)
                    if bool(runtime_persona_setting(self, "enable_group_social_context", False)):
                        try:
                            self._append_group_social_context_sections(
                                group,
                                group_sections,
                                sender_id=sender_id,
                            )
                        except Exception as exc:
                            logger.debug(
                                "[PrivateCompanion] 群聊社交上下文注入失败: %s",
                                _single_line(exc, 120),
                            )
                    group_sections.extend(extra_sections)
                    realtime_formatter = getattr(self, "_format_external_realtime_prompt_section", None)
                    if callable(realtime_formatter):
                        realtime_section = realtime_formatter({}, public=True)
                        realtime_context = str(realtime_section.content or "")
                        if realtime_context:
                            group_sections.insert(0, realtime_section)
                    if group_context_section is not None:
                        group_sections.append(group_context_section)
                    group_context_text = render_prompt_sections(group_sections)
                    placement = place_sections(
                        marker,
                        group_sections,
                        priority=GROUP_CONTEXT_FINAL_PRIORITY,
                    )
                    if group_prompt_context_history_count(group_context_section) > 0:
                        try:
                            setattr(event, GROUP_HISTORY_INJECTED_ATTR, True)
                        except Exception:
                            pass
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
            recall_section = self._format_recalled_messages_for_natural_query_prompt_section(
                event,
                limit=5,
            )
            if recall_section.content:
                recall_context = str(recall_section.content)
                placement = place_section(
                    recall_marker,
                    recall_section,
                    priority=66,
                )
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
            timeline_section: PromptSection | None = None
            if not self._memory_companion_should_defer_prompt_section("self_timeline", event, req):
                timeline_section = self._format_self_timeline_context_for_reply_section(
                    group_recall_text,
                    limit=timeline_limit,
                )
            if timeline_section is not None and timeline_section.content:
                self_timeline_context = str(timeline_section.content)
                placement = place_section(
                    timeline_marker,
                    timeline_section,
                    priority=67,
                )
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

    _stage_ctx = _PassiveStageContext(**{
        _k: _v
        for _k, _v in locals().items()
        if _k in ['combined_text', 'event', 'exc', 'existing', 'index', 'is_private_chat', 'log_bookshelf_secret_skip', 'marker', 'place_sections', 'raw_users', 'realtime_context', 'realtime_formatter', 'realtime_section', 'recall_section', 'recent_atrelay_section', 'req', 'resolver', 'section', 'state', 'user']
    })
    if await _passive_state_stage_1(self, _stage_ctx) is _PASSIVE_STAGE_STOP:
        return
    if await _passive_state_stage_2(self, _stage_ctx) is _PASSIVE_STAGE_STOP:
        return
    if await _passive_state_stage_3(self, _stage_ctx) is _PASSIVE_STAGE_STOP:
        return
    if await _passive_state_stage_4(self, _stage_ctx) is _PASSIVE_STAGE_STOP:
        return
