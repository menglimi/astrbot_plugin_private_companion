# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any

from .helpers import _now_ts, _safe_float, _single_line, _today_key
from .persona_config import runtime_persona_setting


class StateViewsMixin:
    """Formatting helpers for companion state, dreams, and diaries."""

    def _format_plan_status_summary(self, plan: dict[str, Any]) -> str:
        if not isinstance(plan, dict) or not plan.get("date"):
            return "未生成"
        plan_date = str(plan.get("date") or "").strip()
        today = _today_key()
        if plan_date == today:
            return plan_date
        if self._is_plan_date_active(plan_date):
            due_minutes = self._daily_plan_due_minutes()
            return f"{plan_date}（跨零点沿用,待 {self._minutes_to_hhmm(due_minutes)} 后生成今日日程）"
        return plan_date

    def _format_diaries(self) -> str:
        diaries = self.data.get("bot_diaries", [])
        if not isinstance(diaries, list) or not diaries:
            return "还没有 Bot 日记。"
        return "\n\n".join(self._format_single_diary(diary) for diary in diaries[-3:])

    def _format_single_diary(self, diary: dict[str, Any]) -> str:
        if not isinstance(diary, dict) or not diary:
            return "日记生成失败。"
        tags = diary.get("tags", [])
        tag_text = "、".join(str(tag) for tag in tags) if isinstance(tags, list) else ""
        body = self._polish_diary_text(diary.get("body") or diary.get("summary", ""), field="body")
        summary = self._polish_diary_text(diary.get("summary"), field="summary")
        title = summary if summary and summary != body else ""
        parts = [f"{diary.get('date', _today_key())} 的 Bot 日记：", body]
        if title:
            parts.append(f"题眼：{title}")
        if tag_text:
            parts.append(f"标签：{tag_text}")
        return "\n".join(parts)

    def _format_diary_story_plan(self, diary: dict[str, Any]) -> str:
        plan = diary.get("story_plan")
        if not isinstance(plan, dict):
            return "今日预设：无"
        lines = ["今日预设："]
        events = plan.get("today_events", [])
        if isinstance(events, list) and events:
            for item in events[:4]:
                if isinstance(item, dict):
                    lines.append(f"- {item.get('window', '')} {item.get('event', '')}")
        proactive = plan.get("proactive_events", [])
        if isinstance(proactive, list) and proactive:
            lines.append("主动计划：")
            for item in proactive[:4]:
                if isinstance(item, dict):
                    lines.append(
                        f"- {item.get('window', '')} {item.get('reason', '')}/{item.get('action', 'message')}："
                        f"{item.get('why', '')}"
                        + (f"｜动机：{item.get('motive', '')}" if item.get("motive") else "")
                    )
        long_term = plan.get("long_term_events", [])
        if isinstance(long_term, list) and long_term:
            lines.append("长线事件：")
            for item in long_term[:3]:
                if isinstance(item, dict):
                    lines.append(
                        f"- {item.get('title', '')}：{item.get('status', '')}｜"
                        f"{item.get('tendency', '') or item.get('next_hint', '')}"
                    )
        return "\n".join(lines)

    def _format_state_detail(self, state: dict[str, Any]) -> str:
        if not isinstance(state, dict) or not state:
            return "今天还没有拟人状态。"
        condition_lines = []
        conditions = state.get("conditions", [])
        if isinstance(conditions, list):
            for cond in conditions:
                if not isinstance(cond, dict):
                    continue
                if not self._should_show_condition(cond):
                    continue
                condition_lines.append(
                    f"- {cond.get('title', cond.get('kind', '状态'))}："
                    f"{cond.get('label', '')}｜情绪 {cond.get('mood', '平稳')}｜"
                    f"{('阶段 ' + str(cond.get('phase')) + '｜') if cond.get('phase') else ''}"
                    f"开始 {self._format_condition_started(cond.get('start_ts'))}｜"
                    f"{('原因 ' + str(cond.get('cause')) + '｜') if cond.get('cause') else ''}"
                    f"能量 {cond.get('energy_delta', 0)}｜"
                    f"{self._format_transition_hint(cond)}"
                    f"剩余 {self._format_remaining(cond.get('end_ts'))}"
                )
        condition_text = "\n".join(condition_lines) if condition_lines else "- 当前没有明显叠加状态"
        location_line = ""
        current_location = self._current_location_state_text(state)
        if current_location:
            location_line = f"地点感：{current_location}\n"
        return (
            f"{runtime_persona_setting(self, 'bot_name', '小星')} 今天的拟人状态：\n"
            f"能量：{state.get('energy', 70)}/100\n"
            f"情绪底色：{state.get('mood_bias', '平稳')}\n"
            f"{location_line}"
            f"睡眠：{state.get('sleep', '睡眠平稳')}\n"
            f"梦境：{state.get('dream', '没有记住梦')}\n"
            f"健康：{'不适用' if self._is_inapplicable_state_text(state.get('health', '')) else state.get('health', '状态正常')}\n"
            f"饥饿：{'不适用' if self._is_inapplicable_state_text(state.get('hunger', '')) else state.get('hunger', '无饥饿感')}\n"
            f"周期：{'不适用' if self._is_inapplicable_state_text(state.get('body_cycle', '')) else state.get('body_cycle', '不处于生理期')}\n"
            f"天气：{state.get('weather', '暂无天气信息')}\n"
            f"影响：{state.get('note', '今天状态比较平稳,适合按原计划行动。')}\n"
            f"状态走向：{self._format_state_transition_overview(state)}\n"
            f"当前叠加：\n{condition_text}"
        )

    def _format_dream_view(self, state: dict[str, Any]) -> str:
        if not isinstance(state, dict) or not state:
            return "今天还没有梦境状态。"
        remembered = self.data.get("daily_dream")
        if not isinstance(remembered, dict) or remembered.get("date") != _today_key():
            remembered = {}
        dream_text = _single_line(remembered.get("content"), 1200) or _single_line(state.get("dream"), 220) or "没有记住梦"
        dream_type = _single_line(remembered.get("dream_type"), 40) or "碎片梦"
        factors = remembered.get("factors", [])
        if isinstance(factors, list):
            factor_text = "、".join(_single_line(item, 30) for item in factors[:8] if _single_line(item, 30))
        else:
            factor_text = ""
        aftertaste_lines: list[str] = []
        for cond in state.get("conditions", []) or []:
            if not isinstance(cond, dict):
                continue
            if str(cond.get("kind") or "") != "dream_aftertaste" and str(cond.get("phase") or "") != "dream_aftertaste":
                continue
            label = _single_line(cond.get("label"), 80)
            mood = _single_line(cond.get("mood"), 20) or "平稳"
            cause = _single_line(cond.get("cause"), 80)
            remain = self._format_remaining(cond.get("end_ts"))
            pieces = [label] if label else []
            if mood:
                pieces.append(f"情绪 {mood}")
            if cause:
                pieces.append(cause)
            pieces.append(f"剩余 {remain}")
            aftertaste_lines.append("- " + "｜".join(pieces))
        remembered_afterglow = _single_line(remembered.get("afterglow"), 260)
        fragment_pool = self._normalize_dream_fragment_pool(self.data.get("dream_fragments", []))
        sampled = self._weighted_unique_fragment_sample(fragment_pool, count=6) if fragment_pool else []
        fragment_text = factor_text or ("、".join(sampled) if sampled else "（当前没有明显残留碎片）")
        afterglow_text = remembered_afterglow
        if aftertaste_lines:
            afterglow_text = (afterglow_text + "\n" if afterglow_text else "") + "\n".join(aftertaste_lines)
        if not afterglow_text:
            afterglow_text = "今天没有明显的梦后余韵。"
        return (
            f"{runtime_persona_setting(self, 'bot_name', '小星')} 最近一次梦境：\n"
            f"梦境类型：{dream_type}\n"
            f"梦境因子/碎片：{fragment_text}\n"
            f"梦境内容：{dream_text}\n"
            f"梦境余韵：\n{afterglow_text}"
        )

    def _format_dream_fragment_pool_view(self) -> str:
        pool = self._normalize_dream_fragment_pool(self.data.get("dream_fragments", []))
        if not pool:
            return "现在还没有可用的梦境碎片。先生成日记或状态，通常就会慢慢积起来。"
        now_ts = _now_ts()
        ranked = sorted(
            pool,
            key=lambda item: (
                -self._dream_fragment_effective_weight(item, now_ts=now_ts),
                -_safe_float(item.get("created_ts"), 0),
            ),
        )
        lines = ["当前梦境碎片池："]
        for item in ranked[:12]:
            text = _single_line(item.get("text"), 40)
            if not text:
                continue
            effective = self._dream_fragment_effective_weight(item, now_ts=now_ts)
            raw_weight = _safe_float(item.get("weight"), 1.0)
            source = _single_line(item.get("source"), 20) or "unknown"
            age = self._format_timestamp_elapsed(item.get("created_ts"))
            lines.append(
                f"- {text}｜当前权重 {effective:.2f}（原始 {raw_weight:.2f}）｜来源 {source}｜进入于 {age}"
            )
        if len(lines) == 1:
            return "现在还没有可用的梦境碎片。"
        return "\n".join(lines)
