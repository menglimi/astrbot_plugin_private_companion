# -*- coding: utf-8 -*-
"""DailyStateStateTransitionComposeNoteMixin。

由 tools/split_mixin_domain.py 从 daily_state_state.py 机械抽取（7 个方法 + 0 个模块级名字 + 0 个类级赋值 / 305 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 DailyStateStateMixin）。
"""
from __future__ import annotations

from .daily_state_state_shared import _now_ts, _today_key
from .daily_state_state_shared import Any
from .daily_state_state_shared import _safe_float
from .daily_state_state_shared import _safe_int
from .daily_state_state_shared import _single_line
from .daily_state_state_shared import compose_affect_modulation
from .daily_state_state_shared import random



class DailyStateStateTransitionComposeNoteMixin:
    """DailyStateStateTransitionComposeNoteMixin（从 DailyStateStateMixin 拆出）。"""


    def _pick_condition_transition(self, cond: dict[str, Any]) -> str:
        options = cond.get("transition_options", [])
        if not isinstance(options, list) or not options:
            return ""
        weighted: list[tuple[str, float]] = []
        cause = _single_line(cond.get("cause"), 120)
        intensity = _safe_int(cond.get("intensity"), 50, 0, 100)
        weather_text = self._weather_summary_text(self.data.get("daily_weather", {}))
        care_notes = cond.get("care_notes", [])
        care_count = len(care_notes) if isinstance(care_notes, list) else 0
        for option in options:
            if not isinstance(option, dict):
                continue
            target = str(option.get("to") or "").strip()
            weight = float(option.get("base_weight") or 0)
            if not target or weight <= 0:
                continue
            if target == "recovery_afterglow":
                weight += min(0.22, care_count * 0.08)
                if "提醒" in cause or "用户" in cause:
                    weight += 0.06
            elif target == "health_tail":
                if intensity >= 75:
                    weight += 0.1
                if any(token in cause for token in ("透支", "失眠")):
                    weight += 0.08
                if any(token in weather_text for token in ("降雨", "小雨", "中雨", "大雨", "冷", "风")):
                    weight += 0.05
                weight -= min(0.12, care_count * 0.05)
            elif target == "sleep_afterglow":
                weight += min(0.16, care_count * 0.05)
            elif target == "sleep_tail":
                if intensity >= 80:
                    weight += 0.08
                if any(token in cause for token in ("失眠", "睡")):
                    weight += 0.04
            weighted.append((target, max(0.0, weight)))
        total = sum(weight for _, weight in weighted)
        if total <= 0:
            return ""
        pick = random.random() * total
        cursor = 0.0
        for target, weight in weighted:
            cursor += weight
            if pick <= cursor:
                return target
        return weighted[-1][0]

    def _build_transition_condition(self, target: str, cond: dict[str, Any]) -> dict[str, Any] | None:
        cause = _single_line(cond.get("cause"), 120)
        if target == "recovery_afterglow":
            label = "不适缓解后的轻度回升"
            if cause:
                label = "不适正在缓解,状态明显回升"
            return self._make_condition(
                kind="recovery_afterglow",
                title="恢复后的回弹",
                label=label,
                mood="轻快",
                energy_delta=10,
                duration_hours=12,
                intensity=68,
                cause="前序不适开始缓解",
                phase="afterglow",
            )
        if target == "health_tail":
            return self._make_condition(
                kind="health_tail",
                title="恢复尾声",
                label="整体好转,但仍有轻微虚弱残留",
                mood="平缓",
                energy_delta=-4,
                duration_hours=10,
                intensity=48,
                cause="恢复中,体力尚未完全回满",
                phase="tail",
            )
        if target == "sleep_afterglow":
            return self._make_condition(
                kind="sleep_afterglow",
                title="补回来一点精神",
                label="睡意缓解后的轻度回升",
                mood="轻松",
                energy_delta=8,
                duration_hours=8,
                intensity=60,
                cause="前序失眠或浅睡影响减弱",
                phase="afterglow",
            )
        if target == "sleep_tail":
            return self._make_condition(
                kind="sleep_tail",
                title="迟钝尾声",
                label="睡眠影响减弱,但反应仍略慢",
                mood="安静",
                energy_delta=-3,
                duration_hours=6,
                intensity=42,
                cause="睡眠债仍有轻微残留",
                phase="tail",
            )
        if target == "soft_afterglow":
            return self._make_condition(
                kind="soft_afterglow",
                title="被关心后的余温",
                label="收到关心反馈后的柔和余波",
                mood="柔和",
                energy_delta=4,
                duration_hours=4,
                intensity=48,
                cause="用户关心反馈仍有轻度影响",
                phase="afterglow",
            )
        if target == "body_period":
            return self._make_condition(
                kind="body_cycle",
                title="周期",
                label="处于生理期,身体舒适度与能量偏低",
                mood="疲惫",
                energy_delta=-18,
                duration_hours=72,
                intensity=64,
                cause="周期阶段自然推进",
                phase="period",
                episode_key=_single_line(cond.get("episode_key"), 40),
                transition_options=[
                    {"to": "body_recovery", "base_weight": 0.65},
                    {"to": "stable", "base_weight": 0.35},
                ],
            )
        if target == "body_recovery":
            return self._make_condition(
                kind="body_cycle",
                title="周期",
                label="生理期后,慢慢回到稳定状态",
                mood="松弛",
                energy_delta=-5,
                duration_hours=24,
                intensity=48,
                cause="周期阶段自然推进",
                phase="recovery",
                episode_key=_single_line(cond.get("episode_key"), 40),
                transition_options=[{"to": "stable", "base_weight": 1.0}],
            )
        advanced_targets = {
            "body_menstrual": "menstrual",
            "body_follicular": "follicular",
            "body_pre_ovulation": "pre_ovulation",
            "body_ovulation": "ovulation",
            "body_luteal": "luteal",
            "body_pms": "pms",
        }
        if target in advanced_targets and self._advanced_cycle_enabled():
            return self._advanced_cycle_condition(
                advanced_targets[target],
                episode_key=_single_line(cond.get("episode_key"), 40),
            )
        return None

    def _get_active_conditions(self) -> list[dict[str, Any]]:
        now = _now_ts()
        conditions = self.data.get("state_conditions", [])
        if not isinstance(conditions, list):
            return []
        active = []
        for cond in conditions:
            if not isinstance(cond, dict):
                continue
            start_ts = _safe_float(cond.get("start_ts"), 0)
            end_ts = _safe_float(cond.get("end_ts"), 0)
            if start_ts <= now < end_ts:
                active.append(cond)
        return active

    def _compose_state_from_conditions(self, weather: dict[str, Any] | None = None) -> dict[str, Any]:
        profile = self._persona_state_profile()
        active = [
            cond for cond in self._get_active_conditions()
            if self._state_condition_allowed(str(cond.get("kind") or ""), profile)
        ]
        values = self._base_state_values(profile)
        weather_text = self._weather_summary_text(weather)
        energy = 75
        composed_at = _now_ts()
        mood_candidates = []
        health_cause = ""
        for cond in active:
            kind = str(cond.get("kind") or "")
            if kind in values:
                values[kind] = _single_line(cond.get("label"), 80)
            energy += self._condition_effective_energy_delta(cond, now=composed_at)
            mood = _single_line(cond.get("mood"), 20)
            if mood and mood != "平稳":
                intensity = _safe_int(cond.get("intensity"), 50, 0, 100)
                if kind == "memory_afterglow":
                    intensity = max(0, round(intensity * self._memory_afterglow_decay(cond, now=composed_at)))
                mood_candidates.append((mood, intensity))
            if kind == "health" and not health_cause:
                health_cause = _single_line(cond.get("cause"), 120)
        remembered_dream = self._remembered_daily_dream_label()
        if values.get("dream") == "没有记住梦" and remembered_dream:
            values["dream"] = remembered_dream
        existing_state = self.data.get("daily_state")
        existing_override_ts = 0.0
        if isinstance(existing_state, dict) and existing_state.get("date") == _today_key():
            existing_override_ts = _safe_float(existing_state.get("location_override_ts"), 0)
        override_active = existing_override_ts > 0 and _now_ts() - existing_override_ts < 4 * 3600
        if override_active:
            inferred_location = self._current_location_state_text(existing_state)
        else:
            inferred_location = self._current_location_state_text({"location": values.get("location", "")})
        if inferred_location:
            values["location"] = inferred_location
        energy = max(10, min(100, energy))
        mood_bias = (
            sorted(mood_candidates, key=lambda item: item[1], reverse=True)[0][0]
            if mood_candidates else "平稳"
        )
        cycle_runtime: dict[str, Any] = {}
        if self._advanced_cycle_enabled() and profile.get("allow_cycle", False):
            cycle_runtime = self._advanced_cycle_runtime()
            if cycle_runtime:
                values["body_cycle"] = (
                    f"{cycle_runtime.get('phase_name', '周期')} 第{cycle_runtime.get('day_in_phase', 1)}天"
                )
                discomfort = self._active_cycle_discomfort_conditions()
                if discomfort:
                    cycle_runtime["discomfort"] = discomfort
        note = self._build_state_note(
            values["sleep"],
            values["dream"],
            values["health"],
            values["hunger"],
            values["body_cycle"],
            weather_text,
            mood_bias,
            energy,
            health_cause,
        )
        result = {
            "date": _today_key(),
            **values,
            "weather": weather_text,
            "mood_bias": mood_bias,
            "energy": energy,
            "note": note,
            "cycle_runtime": cycle_runtime,
            "conditions": active,
            "affect_modulation": compose_affect_modulation(active, now=composed_at),
        }
        if override_active:
            result["location_override_ts"] = existing_override_ts
            result["location_source"] = "dialogue_override"
        return result

    @staticmethod
    def _memory_afterglow_decay(cond: dict[str, Any], *, now: float) -> float:
        if str(cond.get("kind") or "") != "memory_afterglow":
            return 1.0
        start_ts = _safe_float(cond.get("start_ts"), now)
        half_life = max(60.0, min(86400.0, _safe_float(cond.get("half_life_seconds"), 1800.0)))
        age = max(0.0, now - start_ts)
        return max(0.0, min(1.0, 0.5 ** (age / half_life)))

    def _condition_effective_energy_delta(self, cond: dict[str, Any], *, now: float) -> int:
        base = _safe_int(cond.get("energy_delta"), 0, -100, 100)
        if str(cond.get("kind") or "") != "memory_afterglow":
            return base
        return int(round(base * self._memory_afterglow_decay(cond, now=now)))

    def _build_state_note(
        self,
        sleep: str,
        dream: str,
        health: str,
        hunger: str,
        body_cycle: str,
        weather: str,
        mood_bias: str,
        energy: int,
        health_cause: str = "",
    ) -> str:
        if energy < 35:
            pace = "今天能量很低,日程应更轻、更慢,主动消息也要更短。"
        elif energy < 55:
            pace = "今天能量偏低,适合少量任务和更多停顿。"
        elif energy > 80:
            pace = "今天能量不错,可以安排一些需要专注的事情。"
        else:
            pace = "今天能量中等,适合保持温和节奏。"
        weather_text = str(weather or "").strip()
        weather_text = weather_text.rstrip("。！？!?,,；; ")
        weather_part = f"天气：{weather_text}。" if weather_text and weather_text != "暂无天气信息" else ""
        cause_part = f" 身体不太舒服更像是因为{health_cause}。" if health_cause else ""
        detail_parts = []
        if sleep and sleep not in {"睡眠平稳", "睡得很踏实"}:
            detail_parts.append(f"睡眠：{sleep}")
        if dream and dream != "没有记住梦":
            detail_parts.append(f"梦境：{dream}")
        if health and health != "状态正常" and not self._is_inapplicable_state_text(health):
            detail_parts.append(f"健康：{health}")
        if hunger and hunger not in {"饥饿感平稳", "无饥饿感"} and not self._is_inapplicable_state_text(hunger):
            detail_parts.append(f"饥饿：{hunger}")
        if body_cycle and body_cycle not in {"无明显周期影响", "不处于生理期"} and not self._is_inapplicable_state_text(body_cycle):
            detail_parts.append(f"周期：{body_cycle}")
        detail_text = (" " + "；".join(detail_parts) + "。") if detail_parts else ""
        return (
            f"{pace} 情绪底色偏{mood_bias}。"
            f"{weather_part}{cause_part}"
            f"{detail_text}"
        )
