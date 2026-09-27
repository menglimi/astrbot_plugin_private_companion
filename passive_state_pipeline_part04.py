# -*- coding: utf-8 -*-
"""passive_state_pipeline 阶段 4（原 inject_humanized_state 行 1577-1666）。

由 tmp/refactor/psp_split.py 机械提取，控制流与副作用保持原样。
"""
from __future__ import annotations

from typing import Any

from .passive_state_pipeline_shared import _psp_host


async def _passive_state_stage_4(self: Any, ctx: Any):
    conversation_plan = getattr(ctx, "conversation_plan", None)
    current_user = getattr(ctx, "current_user", None)
    dynamic_placement = getattr(ctx, "dynamic_placement", None)
    dynamic_sections = getattr(ctx, "dynamic_sections", None)
    event = getattr(ctx, "event", None)
    index = getattr(ctx, "index", None)
    injection = getattr(ctx, "injection", None)
    injection_sections = getattr(ctx, "injection_sections", None)
    lightweight_passive = getattr(ctx, "lightweight_passive", None)
    marker = getattr(ctx, "marker", None)
    place_sections = getattr(ctx, "place_sections", None)
    prompt_surface = getattr(ctx, "prompt_surface", None)
    req = getattr(ctx, "req", None)
    section = getattr(ctx, "section", None)
    state = getattr(ctx, "state", None)
    state_changed = getattr(ctx, "state_changed", None)
    state_update_reason = getattr(ctx, "state_update_reason", None)
    static_marker = getattr(ctx, "static_marker", None)
    static_placement = getattr(ctx, "static_placement", None)
    static_sections = getattr(ctx, "static_sections", None)
    if static_sections and not self._request_has_managed_prompt_marker(req, static_marker):
        static_placement = "system_prompt"
        for index, section in enumerate(static_sections):
            conversation_plan.add(
                section=section,
                marker=static_marker if index == 0 else "",
                priority=12,
                placement=_psp_host.PLACEMENT_STABLE_SYSTEM,
                materialized=False,
                metadata={_psp_host.DELIVERY_GROUP_MARKER_METADATA_KEY: static_marker},
            )
    if dynamic_sections:
        dynamic_placement = place_sections(
            marker,
            dynamic_sections,
            priority=40,
        )
    elif static_sections:
        conversation_plan.render_into(req, prefer_extra_user_content=True)
    injection_placement = "+".join(part for part in (static_placement, dynamic_placement) if part) or "none"
    await self._append_conditional_tool_instructions_to_request(event, req)
    state_log_parts = [
        f"心理能量={state.get('energy', 70)}/100",
        f"情绪底色={state.get('mood_bias', '平稳')}",
    ]
    weather = _psp_host._single_line(state.get("weather"), 80)
    if self._private_user_role(current_user or {}) == "friend":
        weather = ""
    if weather and weather != "暂无天气信息":
        state_log_parts.append(f"天气={weather}")
    schedule_material_getter = getattr(self, "_private_passive_schedule_material", None)
    if callable(schedule_material_getter):
        verified_schedule, planned_schedule = schedule_material_getter(current_user)
    else:
        current_item = self._get_current_plan_item(self.data.get("daily_plan", {}))
        verified_schedule = (
            self._sanitize_schedule_context_for_private_user(
                self._format_plan_item_for_prompt(current_item),
                current_user,
            )
            if isinstance(current_item, dict)
            else ""
        )
        planned_schedule = ""
    verified_schedule_log = verified_schedule or "（暂无）"
    planned_schedule_log = planned_schedule or "（暂无）"
    recorder = getattr(self, "_record_prompt_injection_snapshot", None)
    if callable(recorder):
        await recorder(
            kind="passive",
            session=_psp_host._single_line(getattr(event, "unified_msg_origin", ""), 160) or self._event_scope_key(event),
            title="被动回复注入",
            text=injection,
            mode="light" if lightweight_passive else "full",
            trace_id=self._prompt_injection_trace_id_for_event(event),
            message_preview=self._prompt_injection_message_preview_for_event(event),
            sender_label=self._prompt_injection_sender_label_for_event(event),
            section_manifest=injection_sections,
            metadata={
                "状态": "｜".join(state_log_parts),
                # Keep the legacy key for consumers that already read it, but
                # make its evidence-backed meaning explicit alongside the
                # clock-only projection.
                "当前日程": verified_schedule_log,
                "已核实当前活动": verified_schedule_log,
                "当前计划时段": planned_schedule_log,
                "注入位置": injection_placement,
                "状态注入模式": "增量"
                if bool(_psp_host.runtime_persona_setting(self, "enable_passive_state_delta_injection", True))
                else "完整",
                "状态变化": "是" if state_changed else "否",
                "状态触发": state_update_reason,
                "会话": _psp_host._single_line(getattr(event, "unified_msg_origin", ""), 160) or "unknown",
                "发送者": _psp_host._single_line(self._event_sender_id(event), 80),
                "key冲突": len(prompt_surface.conflicts()),
            },
        )
    _psp_host.logger.info(
        "已注入被动状态提示词到 %s: mode=%s state_mode=%s reason=%s placement=%s chars=%s 状态=%s；当前日程=%s；已核实当前活动=%s；当前计划时段=%s",
        _psp_host._single_line(getattr(event, "unified_msg_origin", ""), 80) or "unknown_session",
        "light" if lightweight_passive else "full",
        "delta" if bool(_psp_host.runtime_persona_setting(self, "enable_passive_state_delta_injection", True)) else "legacy",
        state_update_reason,
        injection_placement,
        len(injection),
        "｜".join(state_log_parts),
        verified_schedule_log,
        verified_schedule_log,
        planned_schedule_log,
    )
