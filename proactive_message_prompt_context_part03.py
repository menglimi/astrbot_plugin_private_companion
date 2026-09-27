# -*- coding: utf-8 -*-
"""ProactiveMessagePromptContextPart03Mixin。

由 tools/split_mixin_domain.py 从 proactive_message_prompt_context.py 机械抽取（12 个方法 + 0 个模块级名字 + 0 个类级赋值 / 295 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveMessagePromptContextMixin）。
"""
from __future__ import annotations
from .proactive_message_prompt_context_shared import Any
from .proactive_message_prompt_context_shared import PromptRenderMode
from .proactive_message_prompt_context_shared import PromptSection
from .proactive_message_prompt_context_shared import _safe_int
from .proactive_message_prompt_context_shared import _single_line
from .proactive_message_prompt_context_shared import normalize_reaction_expression_intent
from .proactive_message_prompt_context_shared import prompt_section
from .proactive_message_prompt_context_shared import reaction_expression_high_frequency
from .proactive_message_prompt_context_shared import render_prompt_sections
from .proactive_message_prompt_context_shared import runtime_persona_setting



class ProactiveMessagePromptContextPart03Mixin:
    """ProactiveMessagePromptContextPart03Mixin（从 ProactiveMessagePromptContextMixin 拆出）。"""


    def _proactive_reaction_expression_fallback_intent(
        self,
        visible_text: Any,
        *,
        action: str,
    ) -> dict[str, Any]:
        """Keep high-frequency proactive delivery from depending on tag recall."""
        if not self._proactive_reaction_expression_enabled(action):
            return {}
        if not reaction_expression_high_frequency(
            runtime_persona_setting(self, "reaction_expression_trigger_probability", 0.2)
        ):
            return {}
        text = _single_line(visible_text, 700)
        if not self._proactive_reaction_text_is_compact(text):
            return {}
        return normalize_reaction_expression_intent(
            query="开心回应",
            context=text,
            purpose="日常分享",
            emotion="开心",
            intensity=2,
            candidate_queries=["开心回应", "轻松互动", "日常分享"],
            candidate_limit=_safe_int(
                runtime_persona_setting(self, "reaction_expression_candidate_limit", 6),
                6,
                1,
                16,
            ),
        )

    def _proactive_natural_delivery_prompt_section(self) -> PromptSection:
        return prompt_section(
            key="proactive.delivery",
            title="自然交付提醒",
            source="proactive_message",
            content=(
                "这一轮的最终文本会成为对话里的下一句。"
                "请把注意力放在这句聊天内容本身，像平时主动开口那样自然收住；"
                "过程中的执行状态只供系统判断，不需要写进正文。"
            ),
        )

    def _proactive_natural_delivery_hint(self) -> str:
        return render_prompt_sections(
            [self._proactive_natural_delivery_prompt_section()],
            mode=PromptRenderMode.LABELED_BLOCK,
        )

    def _format_proactive_future_schedule_hint_section(
        self,
        *,
        reason: str,
    ) -> PromptSection | None:
        """Expose a small, policy-filtered future schedule hint to select routes."""

        if reason not in {
            "background_schedule",
            "activity_share",
            "diary_share",
            "state_share",
            "check_in",
            "quiet_care",
        }:
            return None
        disclosure = getattr(self, "_agenda_disclosure_view", None)
        if not callable(disclosure):
            return None
        try:
            view = disclosure("future_schedule", max_entries=4)
        except Exception:
            return None
        entries = view.get("entries", []) if isinstance(view, dict) else getattr(view, "entries", [])
        if not isinstance(entries, list):
            return None
        lines: list[str] = []
        for item in entries:
            if not isinstance(item, dict):
                continue
            phase = str(item.get("temporal_phase") or "").lower()
            if phase and phase != "future":
                continue
            title = _single_line(item.get("title") or item.get("activity"), 80)
            if not title:
                continue
            start = _single_line(item.get("time") or item.get("start_at"), 16)
            end = _single_line(item.get("end") or item.get("end_at"), 16)
            if "T" in start:
                start = start.split("T", 1)[1][:5]
            if "T" in end:
                end = end.split("T", 1)[1][:5]
            clock = f"{start}-{end}" if start and end else start
            lines.append(f"- {clock + ' ' if clock else ''}{title}")
            if len(lines) >= 2:
                break
        if not lines:
            return None
        return prompt_section(
            key="proactive.future_schedule",
            title="接下来可参考的日程",
            source="proactive_message",
            content=(
                "以下内容已通过日程披露层筛选，只是未来安排，不是已经发生的事实。"
                "如果和本轮动机自然贴合，可以像顺口提到明天或等会儿一样带一句；不贴合就忽略。\n"
                + "\n".join(lines)
            ),
        )

    def _format_proactive_future_schedule_hint(self, *, reason: str) -> str:
        section = self._format_proactive_future_schedule_hint_section(reason=reason)
        return (
            render_prompt_sections([section], mode=PromptRenderMode.LABELED_BLOCK)
            if section is not None
            else ""
        )

    def _format_proactive_calendar_constraint_hint_section(self) -> PromptSection | None:
        """Expose longitudinal calendar context without turning it into a gate."""

        timeline_getter = getattr(self, "_agenda_calendar_timeline", None)
        timeline: dict[str, Any] = {}
        if callable(timeline_getter):
            try:
                candidate = timeline_getter(history_days=2, horizon_days=7)
                if isinstance(candidate, dict):
                    timeline = candidate
            except Exception:
                timeline = {}
        if not timeline:
            snapshot_getter = getattr(self, "_agenda_calendar_snapshot", None)
            if not callable(snapshot_getter):
                return None
            try:
                snapshot = snapshot_getter()
            except Exception:
                return None
            if not isinstance(snapshot, dict):
                return None
            timeline = {
                "date": snapshot.get("date"),
                "today": snapshot.get("events", []),
                "current_phase": [item for item in snapshot.get("events", []) if isinstance(item, dict) and item.get("kind") == "period"],
                "rhythms": [item for item in snapshot.get("events", []) if isinstance(item, dict) and item.get("kind") == "recurrence"],
                "upcoming": [],
                "uncertainties": [],
                "transitions": [],
                "conflicts": snapshot.get("conflicts", []),
            }

        candidates_getter = getattr(self, "_agenda_calendar_candidates_store", None)
        pending_candidates: list[dict[str, Any]] = []
        if callable(candidates_getter):
            try:
                raw_candidates = candidates_getter()
                if isinstance(raw_candidates, list):
                    pending_candidates = [
                        item for item in raw_candidates
                        if isinstance(item, dict)
                        and str(item.get("lifecycle_state") or item.get("lifecycle") or "candidate") not in {"confirmed", "active", "completed", "cancelled", "expired"}
                    ][:5]
            except Exception:
                pending_candidates = []

        def line(item: Any, *, include_date: bool = True) -> str:
            if not isinstance(item, dict):
                return ""
            title = _single_line(item.get("title") or item.get("name"), 72)
            if not title:
                return ""
            date_text = _single_line(item.get("occurrence_date") or item.get("date") or item.get("start_date"), 20)
            end_date = _single_line(item.get("end_date"), 20)
            if end_date and end_date != date_text:
                date_text = f"{date_text}至{end_date}"
            status = "已确认" if str(item.get("status") or "confirmed") in {"confirmed", "active"} and str(item.get("commitment_level") or "confirmed") != "tentative" else "待确认"
            return f"{title}（{date_text or '今天'}，{status}）" if include_date else f"{title}（{status}）"

        sections: list[str] = []
        phases = [line(item) for item in timeline.get("current_phase", [])[:4]] if isinstance(timeline.get("current_phase"), list) else []
        phases = [item for item in phases if item]
        if phases:
            sections.append("当前生活阶段：" + "、".join(phases))
        rhythms = [line(item, include_date=False) for item in timeline.get("rhythms", [])[:4]] if isinstance(timeline.get("rhythms"), list) else []
        rhythms = [item for item in rhythms if item]
        if rhythms:
            sections.append("稳定节律：" + "、".join(rhythms))
        upcoming = [line(item) for item in timeline.get("upcoming", [])[:5]] if isinstance(timeline.get("upcoming"), list) else []
        upcoming = [item for item in upcoming if item]
        if upcoming:
            sections.append("接下来可参考：" + "、".join(upcoming))
        transitions = []
        for item in timeline.get("transitions", [])[:4] if isinstance(timeline.get("transitions"), list) else []:
            if isinstance(item, dict) and _single_line(item.get("title"), 60):
                transitions.append(f"{_single_line(item.get('date'), 16)} {_single_line(item.get('title'), 60)}")
        if transitions:
            sections.append("近期可能变化：" + "、".join(transitions))
        uncertainties = [line(item) for item in timeline.get("uncertainties", [])[:3] if isinstance(item, dict)]
        uncertainties = [item for item in uncertainties if item]
        if uncertainties:
            sections.append("待确认：" + "、".join(uncertainties))
        candidate_lines = []
        for item in pending_candidates:
            candidate = line(item)
            if candidate:
                candidate_lines.append(candidate)
        if candidate_lines:
            sections.append("对话待确认候选：" + "、".join(candidate_lines))
        if not sections:
            return None
        return prompt_section(
            key="proactive.calendar_context",
            title="生活时间线参考",
            source="proactive_message",
            content=(
                "\n".join(f"- {item}" for item in sections)
                + "\n这些是跨日背景和可能的生活节奏，不代表事情已经执行。保持同一生活阶段的连续感，不要因为一次旧日程或单个标题就擅自改写阶段；用户当前明确说法优先，待确认变化只用轻量、可回退的语气。"
                + "待确认候选只能用于自然询问，不能据此断言用户已经安排、正在执行或已经完成。"
            ),
        )

    def _format_proactive_calendar_constraint_hint(self) -> str:
        section = self._format_proactive_calendar_constraint_hint_section()
        return (
            render_prompt_sections([section], mode=PromptRenderMode.LABELED_BLOCK)
            if section is not None
            else ""
        )

    def _proactive_troubleshooting_request_prompt_section(
        self,
        user: dict[str, Any] | None,
    ) -> PromptSection | None:
        if not isinstance(user, dict) or _single_line(user.get("planned_proactive_source"), 40).lower() != "troubleshooting":
            return None
        return prompt_section(
            key="proactive.troubleshooting_origin",
            title="本轮真实开口由头",
            source="proactive_message",
            content=(
                "用户刚刚在控制面板明确发起了一次主动消息链路测试，这个请求本身就是当前、可核验的开口由头。"
                "请仍像角色平时私聊那样自然来找对方一次，不要提测试、控制面板、系统、调度或链路。"
                "不需要另编“刚刷到、刚看到、翻书、收到消息”等生活小剧场；如果当前较晚或普通主动间隔较近，"
                "只把语气收轻、句子缩短，不追问、不催回复。"
            ),
        )

    def _proactive_troubleshooting_request_hint(self, user: dict[str, Any] | None) -> str:
        section = self._proactive_troubleshooting_request_prompt_section(user)
        return (
            render_prompt_sections([section], mode=PromptRenderMode.LABELED_BLOCK)
            if section is not None
            else ""
        )

    def _deferred_immediate_share_tense_prompt_section(
        self,
        user: dict[str, Any],
        action: str,
    ) -> PromptSection | None:
        del action
        freshness_getter = getattr(self, "_planned_proactive_freshness_class", None)
        if not callable(freshness_getter):
            return None
        try:
            if freshness_getter(user) != "immediate":
                return None
        except Exception:
            return None
        if _single_line(user.get("planned_proactive_delivery_state"), 24) != "deferred":
            return None
        return prompt_section(
            key="proactive.deferred_tense",
            title="延后分享的时态",
            source="proactive_message",
            content=(
                "这段生活分享发生在稍早一些的时候，但仍在自然分享窗口内。正文要用已经发生的说法，"
                "不要暗示拍摄或事件与发送处于同一时刻，也不要提延后、等待、系统或调度。"
            ),
        )

    def _deferred_immediate_share_tense_hint(self, user: dict[str, Any], action: str) -> str:
        section = self._deferred_immediate_share_tense_prompt_section(user, action)
        return (
            render_prompt_sections([section], mode=PromptRenderMode.LABELED_BLOCK)
            if section is not None
            else ""
        )

    def _proactive_current_plan_item(self, plan: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """Prefer the agenda disclosure boundary over raw daily-plan prose."""

        getter = getattr(self, "_agenda_current_context_item", None)
        if callable(getter):
            try:
                value = getter()
            except Exception:
                value = None
            if isinstance(value, dict):
                return value
        legacy = getattr(self, "_get_current_plan_item", None)
        if callable(legacy):
            try:
                value = legacy(plan if isinstance(plan, dict) else self.data.get("daily_plan", {}))
            except Exception:
                value = None
            return value if isinstance(value, dict) else None
        return None
