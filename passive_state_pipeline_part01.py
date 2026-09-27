# -*- coding: utf-8 -*-
"""passive_state_pipeline 阶段 1（原 inject_humanized_state 行 752-1051）。

由 tmp/refactor/psp_split.py 机械提取，控制流与副作用保持原样。
"""
from __future__ import annotations

from typing import Any

from .passive_state_pipeline_shared import _PASSIVE_STAGE_STOP, _psp_host


async def _passive_state_stage_1(self: Any, ctx: Any):
    combined_text = getattr(ctx, "combined_text", None)
    event = getattr(ctx, "event", None)
    exc = getattr(ctx, "exc", None)
    existing = getattr(ctx, "existing", None)
    index = getattr(ctx, "index", None)
    is_private_chat = getattr(ctx, "is_private_chat", None)
    log_bookshelf_secret_skip = getattr(ctx, "log_bookshelf_secret_skip", None)
    marker = getattr(ctx, "marker", None)
    place_sections = getattr(ctx, "place_sections", None)
    raw_users = getattr(ctx, "raw_users", None)
    realtime_context = getattr(ctx, "realtime_context", None)
    realtime_formatter = getattr(ctx, "realtime_formatter", None)
    realtime_section = getattr(ctx, "realtime_section", None)
    recall_section = getattr(ctx, "recall_section", None)
    recent_atrelay_section = getattr(ctx, "recent_atrelay_section", None)
    req = getattr(ctx, "req", None)
    resolver = getattr(ctx, "resolver", None)
    section = getattr(ctx, "section", None)
    state = getattr(ctx, "state", None)
    user = getattr(ctx, "user", None)
    try:
        resolver = getattr(self, "_private_user_id_for_event", None)
        user_id = (
            resolver(event)
            if callable(resolver)
            else self._canonical_private_user_id(str(event.get_sender_id()))
        )
    except Exception:
        log_bookshelf_secret_skip("private_sender_missing")
        return _PASSIVE_STAGE_STOP
    raw_users = self.data.get("users", {})
    current_user = raw_users.get(user_id) if isinstance(raw_users, dict) else None
    if not isinstance(current_user, dict):
        log_bookshelf_secret_skip("private_user_missing")
        return _PASSIVE_STAGE_STOP
    if not self._private_passive_profile_available(user_id, current_user):
        log_bookshelf_secret_skip("private_user_disabled", current_user)
        return _PASSIVE_STAGE_STOP
    # Keep the extension context keyed to the canonical user even when the
    # persisted profile predates the user_id field.
    current_user = dict(current_user)
    current_user.setdefault("user_id", user_id)
    if not bool(getattr(event, "private_companion_skip_passive_input_status", False)):
        self._start_passive_input_status_loop(event, user_id)

    state = await self._ensure_daily_state(skip_conversation_summary=True, passive_fast=True)
    await self._append_private_active_period_boundary_to_request(event, req, state)
    inbound_text = _psp_host._single_line(getattr(event, "message_str", "") or current_user.get("last_user_message"), 260)
    lightweight_passive = self._is_lightweight_private_passive_inbound(inbound_text)
    memo_query = self._memo_management_instruction_matches(inbound_text)
    if memo_query:
        lightweight_passive = False
    bookshelf_signal_getter = getattr(self, "_bookshelf_secret_signal_info", None)
    bookshelf_signal = bookshelf_signal_getter(inbound_text) if callable(bookshelf_signal_getter) else {}
    if lightweight_passive and isinstance(bookshelf_signal, dict) and bookshelf_signal.get("likely"):
        lightweight_passive = False
        _psp_host.logger.info(
            "夹层密码请求退出轻量被动链路: user=%s direct=%s context=%s access=%s text=%s",
            user_id,
            ",".join(bookshelf_signal.get("direct_matches") or []) or "-",
            ",".join(bookshelf_signal.get("context_matches") or []) or "-",
            ",".join(bookshelf_signal.get("access_matches") or []) or "-",
            inbound_text,
        )
    prompt_surface = _psp_host.PromptSurface()
    prompt_surface.add(
        _psp_host._persona_core_emphasis_prompt_section(),
        priority=8,
    )
    reply_style_section = self._format_reply_style_prompt_section()
    reply_style_prompt = str(reply_style_section.content or "")
    if reply_style_prompt:
        prompt_surface.add(
            reply_style_section,
            priority=12,
        )
    outfit_section = self._format_dialogue_outfit_continuity_prompt_section(current_user)
    if outfit_section.content:
        prompt_surface.add(outfit_section, priority=13)
    wardrobe_builder = getattr(self, "_wardrobe_prompt_section", None)
    if callable(wardrobe_builder):
        # 先按请求挂好衣柜只读工具，再决定提示词里要不要写它：顺序反了就会出现
        # 「提示词说有、工具表里没有」，模型会照着提示词凭空调用一个不存在的工具。
        wardrobe_tool_syncer = getattr(self, "_sync_wardrobe_detail_tool", None)
        wardrobe_detail_tool = False
        if callable(wardrobe_tool_syncer):
            try:
                wardrobe_detail_tool = bool(wardrobe_tool_syncer(req))
            except Exception as exc:
                _psp_host.logger.debug("衣柜细节工具挂载失败: %s", _psp_host._single_line(exc, 160))
        try:
            wardrobe_section = wardrobe_builder(
                current_user, inbound_text, detail_tool=wardrobe_detail_tool
            )
        except Exception as exc:
            wardrobe_section = None
            _psp_host.logger.debug("角色衣柜提示词构建失败: %s", _psp_host._single_line(exc, 160))
        if wardrobe_section is not None and wardrobe_section.content:
            prompt_surface.add(wardrobe_section, priority=13)
    routine_check_section = self._format_private_routine_check_boundary_section(inbound_text)
    routine_check_boundary = str(routine_check_section.content or "")
    if routine_check_boundary:
        prompt_surface.add(
            routine_check_section,
            priority=14,
        )
    if self._record_recent_private_fact_correction(current_user, inbound_text):
        self._schedule_data_save(sections={"users"})
    fact_section = self._format_private_fact_attribution_guard_prompt_section(
        current_user,
        inbound_text,
    )
    if fact_section.content:
        prompt_surface.add(fact_section, priority=11)
    emotion_section = self._format_emotion_inertia_prompt_section(current_user)
    if emotion_section is not None and emotion_section.content:
        prompt_surface.add(emotion_section, priority=29)
    reunion_section = self._format_private_reunion_prompt_section(
        current_user,
        inbound_text,
    )
    if reunion_section is not None and reunion_section.content:
        prompt_surface.add(reunion_section, priority=27)
        try:
            setattr(
                event,
                "_private_companion_reunion_observed_at",
                _psp_host._safe_float(current_user.get("last_inbound_gap_observed_at"), 0),
            )
        except Exception:
            pass
    preference_section = self._format_bot_self_preference_consistency_prompt_section(
        current_user,
        inbound_text,
    )
    if preference_section is not None and preference_section.content:
        prompt_surface.add(preference_section, priority=16)
    state_changed = False
    state_update_reason = "legacy"
    if bool(_psp_host.runtime_persona_setting(self, "enable_passive_state_delta_injection", True)):
        session_key = _psp_host._single_line(getattr(event, "unified_msg_origin", ""), 160) or f"private:{user_id}"
        state_update_sections, state_changed, state_update_reason = (
            self._private_passive_state_update_prompt_sections(
                session=session_key,
                state=state,
                current_user=current_user,
                inbound_text=inbound_text,
                lightweight=lightweight_passive,
            )
        )
        for index, state_update_section in enumerate(state_update_sections or []):
            prompt_surface.add(
                state_update_section,
                priority=30 + index,
            )
    elif lightweight_passive:
        lightweight_section = self._prepared_lightweight_state_prompt_section(state)
        lightweight_injection = self._sanitize_schedule_context_for_private_user(
            str(lightweight_section.content or ""),
            current_user,
        )
        prompt_surface.add(
            _psp_host.prompt_section(
                key=lightweight_section.key or "state.lightweight",
                title=lightweight_section.title,
                source=lightweight_section.source or "daily_state",
                content=lightweight_injection,
                children=lightweight_section.children,
                metadata=lightweight_section.metadata,
            ),
            priority=30,
        )
    else:
        state_section = self._format_state_prompt_section(state)
        state_injection = str(state_section.content or "")
        state_injection = self._sanitize_schedule_context_for_private_user(state_injection, current_user)
        prompt_surface.add(
            _psp_host.prompt_section(
                key=state_section.key or "state.full",
                title=state_section.title,
                source=state_section.source or "daily_state",
                content=state_injection,
                children=state_section.children,
                metadata=state_section.metadata,
            ),
            priority=30,
        )
        life_section = self._format_life_context_prompt_section()
        life_context = str(life_section.content or "")
        life_context = self._sanitize_schedule_context_for_private_user(life_context, current_user)
        if life_context:
            prompt_surface.add(
                _psp_host.prompt_section(
                    key=life_section.key or "life.context",
                    title=life_section.title,
                    source=life_section.source or "daily_state",
                    content=life_context,
                    children=life_section.children,
                    metadata=life_section.metadata,
                ),
                priority=35,
            )
        important_section = self._format_important_dates_prompt_section()
        if important_section.content:
            prompt_surface.add(important_section, priority=36)
        memo_section: _psp_host.PromptSection | None = None
        memo_notes = ""
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
            memo_notes = str(memo_section.content or "")
            if memo_query and not memo_notes:
                memo_notes = "当前没有进行中的便签。不要编造便签内容。"
                memo_section = _psp_host.prompt_section(
                    key=memo_section.key,
                    title=memo_section.title,
                    source=memo_section.source,
                    content=memo_notes,
                )
        if memo_notes and memo_section is not None:
            prompt_surface.add(memo_section, priority=37)
        worldview_sections = (
            self._format_worldview_adaptation_prompt_sections()
            if self._feature_enabled_or_temp_unlocked("enable_environment_perception")
            and _psp_host.runtime_persona_setting(self, "enable_worldview_perception", True)
            else []
        )
        for index, worldview_section in enumerate(worldview_sections):
            worldview_context = str(worldview_section.content or "")
            worldview_context = self._sanitize_owner_environment_context_for_private_user(
                worldview_context,
                current_user,
            )
            if not worldview_context:
                continue
            prompt_surface.add(
                _psp_host.prompt_section(
                    key=worldview_section.key or (
                        "worldview.adaptation" if index == 0 else f"worldview.reference.{index}"
                    ),
                    title=worldview_section.title,
                    source=worldview_section.source or "worldview",
                    content=worldview_context,
                    children=worldview_section.children,
                    metadata=worldview_section.metadata,
                ),
                priority=37 + index,
            )
    realtime_formatter = getattr(self, "_format_external_realtime_prompt_section", None)
    if callable(realtime_formatter):
        realtime_section = realtime_formatter(current_user, public=False)
        realtime_context = str(realtime_section.content or "")
        if realtime_context:
            # Render after daily schedule and recall fragments so the current
            # realtime fact is the last authoritative context.
            prompt_surface.add(realtime_section, priority=76)
    departure_section = self._format_conversation_departure_prompt_section(
        current_user,
        inbound_text,
        state,
    )
    if isinstance(departure_section, _psp_host.PromptSection) and departure_section.content:
        prompt_surface.add(departure_section, priority=28)
        self._schedule_data_save(sections={"users"})
    identity_section = self._format_private_identity_anchor_prompt_section(
        user_id,
        current_user,
        event,
    )
    if identity_section.content:
        prompt_surface.add(identity_section, priority=10)
    recent_atrelay_section = self._format_recent_atrelay_context_prompt_section(
        kind="private",
        target=user_id,
        sender_id=user_id,
        current_text=inbound_text,
        limit=2,
    )
    if recent_atrelay_section.content:
        prompt_surface.add(recent_atrelay_section, priority=26)
    target_summary_section = self._format_atrelay_target_summary_prompt_section(
        inbound_text
    )
    if target_summary_section is None or not target_summary_section.content:
        worldbook_section = self._format_worldbook_private_mentions_prompt_section(
            inbound_text,
            limit=4,
        )
        if worldbook_section.content:
            prompt_surface.add(worldbook_section, priority=55)
    environment_section = await self._format_passive_environment_prompt_section(
        event,
        lightweight=lightweight_passive,
    )
    environment_fragment = str(environment_section.content or "")
    environment_fragment = self._sanitize_owner_environment_context_for_private_user(environment_fragment, current_user)
    if environment_fragment:
        prompt_surface.add(
            _psp_host.prompt_section(
                key=environment_section.key,
                title=environment_section.title,
                source=environment_section.source,
                content=environment_fragment,
                children=environment_section.children,
                metadata=environment_section.metadata,
            ),
            priority=20,
        )
    rest_backlog_prompt = self._take_rest_reply_backlog_prompt(current_user)
    _snapshot = locals()
    for _k in ['current_user', 'exc', 'inbound_text', 'index', 'lightweight_passive', 'prompt_surface', 'rest_backlog_prompt', 'state', 'state_changed', 'state_update_reason', 'user_id']:
        if _k in _snapshot:
            setattr(ctx, _k, _snapshot[_k])
