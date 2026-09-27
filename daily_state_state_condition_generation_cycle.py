# -*- coding: utf-8 -*-
"""DailyStateStateConditionGenerationCycleMixin。

由 tools/split_mixin_domain.py 从 daily_state_state.py 机械抽取（23 个方法 + 0 个模块级名字 + 0 个类级赋值 / 654 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 DailyStateStateMixin）。
"""
from __future__ import annotations

from .daily_state_state_shared import _now_ts, _today_key
from .daily_state_state_shared import Any
from .daily_state_state_shared import _safe_float
from .daily_state_state_shared import _safe_int
from .daily_state_state_shared import _single_line
from .daily_state_state_shared import random
from .daily_state_state_shared import runtime_persona_setting



class DailyStateStateConditionGenerationCycleMixin:
    """DailyStateStateConditionGenerationCycleMixin（从 DailyStateStateMixin 拆出）。"""


    async def _generate_state_conditions(
        self,
        weather: dict[str, Any] | None = None,
        *,
        deferred_state_updates: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        intensity = _safe_float(runtime_persona_setting(self, "humanized_state_intensity", 50), 50, 0, 100) / 100
        persona_profile = self._persona_state_profile()
        now_dt = self._environment_now()
        current_minute = now_dt.hour * 60 + now_dt.minute

        sleep_pool = [
            ("睡得很踏实", "平稳", 0, 8),
            ("昨晚睡得很浅,半夜醒了好几次", "迟钝", -16, 10),
            ("失眠了,翻来覆去很久才睡着", "敏感", -24, 14),
            ("一晚上都在做梦,醒过来却记不清", "恍惚", -18, 12),
            ("赖床赖得有点久,懵懵的", "迷糊", -14, 8),
            ("闹钟没叫醒我,起来还有点懵", "慌乱", -17, 7),
        ]
        dream_pool = [
            ("没有记住梦", "平稳", 0, 2),
            ("梦里一直在找一件放错地方的小东西,醒来还残着一点没找完的感觉", "恍惚", -6, 5),
            ("梦见走过一段很安静的路,路灯和风声都很近", "柔和", 4, 4),
            ("梦里反复听见一句没听清的话,醒来后胸口还有点闷", "低落", -10, 7),
        ]
        hunger_pool = [
            ("无饥饿感", "平稳", 0, 3),
            ("饿,想吃东西", "粘人", -4, 2),
            ("胃口不好", "低落", -8, 3),
            ("想吃甜的", "柔软", 1, 2),
        ]
        cycle_pool = [
            ("不处于生理期", "平稳", 0, 24),
            ("生理期前,身体感受更敏锐,耐受度稍低", "敏感", -18, 24),
            ("处于生理期,身体舒适度与能量偏低", "疲惫", -24, 72),
        ]

        def pick(pool: list[tuple[str, str, int, int]], special_chance: float = 0.35) -> tuple[str, str, int, int]:
            if random.random() > special_chance * max(0.2, intensity):
                return pool[0]
            return random.choice(pool[1:])

        sleep_pick = pick(sleep_pool, 0.42)
        enhanced_dream = None
        if bool(runtime_persona_setting(self, "enable_enhanced_dreams", False)):
            enhanced_dream = await self._generate_enhanced_dream_pick(weather)
        dream_pick = enhanced_dream or pick(dream_pool, 0.55)
        if deferred_state_updates is None:
            self._remember_daily_dream_pick(dream_pick)
        else:
            deferred_state_updates["dream_pick"] = dream_pick
        hunger_pick = pick(hunger_pool, 0.22)
        specs = [
            ("sleep", "睡眠", *sleep_pick),
            ("dream", "梦境", *dream_pick),
        ]
        if persona_profile.get("allow_hunger", True):
            specs.append(("hunger", "饥饿", *hunger_pick))
        if persona_profile.get("allow_cycle", False):
            skip_cycle_spec = False
            if self._advanced_cycle_enabled():
                meta = self.data.get("body_cycle_state", {})
                anchor_ts = _safe_float(meta.get("cycle_anchor_ts"), 0) if isinstance(meta, dict) else 0
                active_advanced_cycle = any(
                    isinstance(cond, dict)
                    and str(cond.get("kind") or "") == "body_cycle"
                    and str(cond.get("phase") or "") in self._ADVANCED_CYCLE_PHASES
                    and _safe_float(cond.get("start_ts"), 0) <= _now_ts() < _safe_float(cond.get("end_ts"), 0)
                    for cond in (self.data.get("state_conditions", []) or [])
                )
                # The anchored continuous timeline owns phase progression once
                # started, so no daily random cycle pick is needed anymore.
                skip_cycle_spec = anchor_ts > 0 or active_advanced_cycle
            if not skip_cycle_spec:
                cycle_spec = (
                    self._pick_advanced_cycle_spec(intensity)
                    if self._advanced_cycle_enabled()
                    else self._pick_body_cycle_spec(cycle_pool, intensity)
                )
                specs.append(("body_cycle", "周期", *cycle_spec))
        else:
            specs.append(("body_cycle", "周期", *cycle_pool[0]))

        diary_tags = self._recent_diary_tags()
        weather_text = self._weather_summary_text(weather)
        if persona_profile.get("allow_health", True):
            health_causes = self._build_health_causes(
                sleep_label=sleep_pick[0],
                weather_text=weather_text,
                diary_tags=diary_tags,
            )
            health_spec = self._pick_health_spec(health_causes, intensity, weather_text)
            if health_spec is not None:
                specs.append(("health", "健康", *health_spec))
        if "失眠" in diary_tags and random.random() < 0.35:
            specs.append(("sleep", "睡眠延续", "昨晚的失眠感还没完全散掉", "迟钝", -12, 8))
        if persona_profile.get("allow_health", True) and "生病" in diary_tags and random.random() < 0.4:
            specs.append(("health", "健康延续", "身体像还在恢复,反应慢半拍", "疲惫", -14, 18, "前两天的不舒服还没完全退掉"))
        if "低能量" in diary_tags and random.random() < 0.35:
            specs.append(("sleep", "能量延续", "昨天的低电量拖到今天早上", "安静", -10, 6))
        if "好梦" in diary_tags and random.random() < 0.3:
            specs.append(("dream", "梦境余温", "梦里留下了一点柔和的亮色", "柔和", 4, 5))
        screen_diary_spec = self._screen_diary_state_condition_spec()
        if screen_diary_spec is not None:
            specs.append(screen_diary_spec)

        conditions = []
        for spec in specs:
            extras: dict[str, Any] = {}
            if len(spec) >= 7:
                kind, title, label, mood, energy_delta, duration_hours, cause = spec[:7]
                extras["cause"] = cause
            else:
                kind, title, label, mood, energy_delta, duration_hours = spec[:6]
            cycle_phase = self._infer_body_cycle_phase(label) if kind == "body_cycle" else ""
            advanced_cycle_phase = self._advanced_cycle_enabled() and cycle_phase in self._ADVANCED_CYCLE_PHASES
            if energy_delta == 0 and kind not in {"sleep", "dream"} and not advanced_cycle_phase:
                continue
            if kind == "health" and energy_delta < 0:
                extras["on_end_transition"] = "health_relief"
                extras["phase"] = "mild_discomfort"
            if kind == "sleep" and energy_delta <= -16:
                extras["on_end_transition"] = "sleep_rebound"
                extras["phase"] = "sleep_debt"
            if kind == "body_cycle" and cycle_phase != "cycle":
                extras["phase"] = cycle_phase
                extras["episode_key"] = f"body-cycle-{_today_key()}"
                if cycle_phase in self._ADVANCED_CYCLE_PHASES:
                    extras["transition_options"] = self._advanced_cycle_transition_options(cycle_phase)
                elif extras["phase"] == "pre":
                    extras["transition_options"] = [{"to": "body_period", "base_weight": 0.72}, {"to": "stable", "base_weight": 0.28}]
                elif extras["phase"] == "period":
                    extras["transition_options"] = [{"to": "body_recovery", "base_weight": 0.65}, {"to": "stable", "base_weight": 0.35}]
            effective_energy_delta = (
                int(energy_delta)
                if advanced_cycle_phase
                else int(energy_delta * max(0.4, intensity))
            )
            extras["transition_options"] = self._build_transition_options(
                kind=kind,
                energy_delta=effective_energy_delta,
                cause=str(extras.get("cause") or ""),
                on_end_transition=str(extras.get("on_end_transition") or ""),
            ) or extras.get("transition_options", [])
            condition = self._make_condition(
                kind=kind,
                title=title,
                label=label,
                mood=mood,
                energy_delta=effective_energy_delta,
                duration_hours=duration_hours,
                intensity=random.randint(35, 90),
                **extras,
            )
            if kind == "body_cycle" and cycle_phase != "cycle":
                if deferred_state_updates is None:
                    self._record_body_cycle_episode(condition)
                else:
                    deferred_state_updates.setdefault("body_cycle_conditions", []).append(condition)
            conditions.append(condition)
        dream_aftertaste = self._build_dream_aftertaste_condition(dream_pick)
        if dream_aftertaste is not None:
            conditions.append(dream_aftertaste)
        discomfort_condition = self._maybe_pick_cycle_discomfort(deferred_state_updates)
        if discomfort_condition is not None:
            conditions.append(discomfort_condition)
        if 0 <= current_minute < 5 * 60:
            late_night_pool = [
                ("夜里还没完全安静下来,眼睛和脑子都慢半拍", "困倦", -14, 4),
                ("这个点还醒着,困意和清醒混在一起", "恍惚", -12, 3),
                ("已经很晚了,精神有点发飘,只想把声音放轻", "疲惫", -10, 5),
            ]
            label, mood, energy_delta, duration_hours = random.choice(late_night_pool)
            conditions.append(
                self._make_condition(
                    kind="sleep",
                    title="夜深未眠",
                    label=label,
                    mood=mood,
                    energy_delta=int(energy_delta * max(0.55, intensity)),
                    duration_hours=duration_hours,
                    intensity=random.randint(45, 88),
                    phase="late_night_awake",
                    transition_options=[
                        {"to": "sleep_afterglow", "base_weight": 0.35},
                        {"to": "sleep_tail", "base_weight": 0.2},
                        {"to": "stable", "base_weight": 0.45},
                    ],
                )
            )
        return conditions

    def _ensure_time_based_hunger_condition(self) -> None:
        profile = self._persona_state_profile()
        if not profile.get("allow_hunger", True):
            return
        if any(str(cond.get("kind") or "") == "hunger" for cond in self._get_active_conditions()):
            return
        if _safe_float(self.data.get("last_food_state_feedback_at"), 0) + 90 * 60 > _now_ts():
            return
        now_dt = self._environment_now()
        minute = now_dt.hour * 60 + now_dt.minute
        windows = [
            ("breakfast", 7 * 60, 9 * 60 + 30, "饿,想吃热的", "柔软", -4, 2),
            ("lunch", 11 * 60, 13 * 60 + 40, "饿,想吃东西", "走神", -6, 2),
            ("afternoon", 15 * 60, 17 * 60, "想吃甜的", "柔软", 2, 2),
            ("dinner", 17 * 60 + 30, 20 * 60, "饿,想吃热的", "粘人", -5, 3),
            ("late_snack", 21 * 60 + 30, 23 * 60 + 30, "有点想吃东西", "松散", -3, 2),
        ]
        matched = next((item for item in windows if item[1] <= minute <= item[2]), None)
        if not matched:
            return
        window_id, _start, _end, label, mood, energy_delta, duration_hours = matched
        attempts = self.data.get("hunger_window_attempts")
        if not isinstance(attempts, dict):
            attempts = {}
        today = _today_key()
        generated = attempts.get("generated")
        if not isinstance(generated, list):
            generated = []
        generated = [
            item for item in generated
            if isinstance(item, dict) and str(item.get("date") or "") == today
        ][-5:]
        if len(generated) >= 2:
            attempts["generated"] = generated
            self.data["hunger_window_attempts"] = attempts
            return
        last_generated_ts = max((_safe_float(item.get("ts"), 0) for item in generated), default=0.0)
        if last_generated_ts and _now_ts() - last_generated_ts < 4 * 3600:
            attempts["generated"] = generated
            self.data["hunger_window_attempts"] = attempts
            return
        attempt_key = f"{today}:{window_id}"
        if attempts.get("last_key") == attempt_key:
            return
        attempts["last_key"] = attempt_key
        attempts["last_attempt_ts"] = _now_ts()
        self.data["hunger_window_attempts"] = attempts
        intensity = max(0.0, min(1.0, _safe_float(runtime_persona_setting(self, "humanized_state_intensity", 50), 50, 0, 100) / 100))
        chance = 0.25 + 0.30 * intensity
        if window_id in {"afternoon", "late_snack"}:
            chance *= 0.65
        if random.random() > chance:
            return
        self.data.setdefault("state_conditions", []).append(
            self._make_condition(
                kind="hunger",
                title="饭点",
                label=label,
                mood=mood,
                energy_delta=int(energy_delta * max(0.55, intensity)),
                duration_hours=duration_hours,
                intensity=random.randint(45, 82),
                phase=window_id,
                cause="饭点自然波动",
            )
        )
        generated.append({"date": today, "window": window_id, "ts": _now_ts()})
        attempts["generated"] = generated[-5:]
        attempts["last_generated_ts"] = _now_ts()
        self.data["hunger_window_attempts"] = attempts

    def _advanced_cycle_enabled(self) -> bool:
        return bool(runtime_persona_setting(self, "enable_advanced_cycle_strategy", False))

    def _infer_body_cycle_phase(self, label: str) -> str:
        text = str(label or "")
        upper_text = text.upper()
        if "PMS" in upper_text or "经前综合征" in text:
            return "pms"
        if "排卵前期" in text:
            return "pre_ovulation"
        if "月经期" in text:
            return "menstrual"
        if "卵泡期" in text:
            return "follicular"
        if "排卵期" in text:
            return "ovulation"
        if "黄体期" in text:
            return "luteal"
        if "生理期后" in text or "恢复" in text:
            return "recovery"
        if "前" in text:
            return "pre"
        if "生理期" in text:
            return "period"
        return "cycle"

    def _body_cycle_max_hours(self, phase: str, label: str = "") -> int:
        phase = str(phase or self._infer_body_cycle_phase(label))
        advanced_hours = self._advanced_cycle_phase_hours(phase)
        if advanced_hours is not None:
            return advanced_hours
        if phase == "period":
            return 72
        if phase in {"pre", "recovery"}:
            return 24
        return 48

    def _body_cycle_interval_seconds(self) -> int:
        if self._advanced_cycle_enabled():
            return self._advanced_cycle_total_days() * 86400
        return random.randint(25, 34) * 86400

    def _advanced_cycle_phase_days(self, phase: str) -> int:
        defaults = {
            "menstrual": 5,
            "follicular": 5,
            "pre_ovulation": 3,
            "ovulation": 1,
            "luteal": 8,
            "pms": 6,
        }
        attributes = {
            "menstrual": "advanced_cycle_menstrual_days",
            "follicular": "advanced_cycle_follicular_days",
            "pre_ovulation": "advanced_cycle_pre_ovulation_days",
            "ovulation": "advanced_cycle_ovulation_days",
            "luteal": "advanced_cycle_luteal_days",
            "pms": "advanced_cycle_pms_days",
        }
        default = defaults.get(phase, 1)
        attribute = attributes.get(phase, "")
        return _safe_int(runtime_persona_setting(self, attribute, default), default, 1, 30) if attribute else default

    def _advanced_cycle_phase_hours(self, phase: str) -> int | None:
        if phase not in self._ADVANCED_CYCLE_PHASES:
            return None
        return self._advanced_cycle_phase_days(phase) * 24

    def _advanced_cycle_total_days(self) -> int:
        return sum(self._advanced_cycle_phase_days(phase) for phase in self._ADVANCED_CYCLE_PHASES)

    def _advanced_cycle_offset_signature(self, offset: int) -> str:
        durations = ",".join(str(self._advanced_cycle_phase_days(phase)) for phase in self._ADVANCED_CYCLE_PHASES)
        return f"{max(0, int(offset))}:{durations}"

    def _advanced_cycle_position_from_offset(self, offset: int) -> tuple[str, int]:
        total_days = max(1, self._advanced_cycle_total_days())
        cycle_day = ((max(1, int(offset)) - 1) % total_days) + 1
        cursor = 0
        for phase in self._ADVANCED_CYCLE_PHASES:
            phase_days = self._advanced_cycle_phase_days(phase)
            if cycle_day <= cursor + phase_days:
                return phase, cycle_day - cursor
            cursor += phase_days
        return "pms", self._advanced_cycle_phase_days("pms")

    def _advanced_cycle_day_of_phase(self, phase: str, day_in_phase: int) -> int:
        """Map a phase plus its day index to the absolute cycle day."""
        cursor = 0
        for candidate in self._ADVANCED_CYCLE_PHASES:
            if candidate == phase:
                return cursor + max(1, int(day_in_phase))
            cursor += self._advanced_cycle_phase_days(candidate)
        return 1

    def _advanced_cycle_runtime(self) -> dict[str, Any]:
        """Derive the current six-phase position for display and continuity.

        The stored cycle anchor timestamp is the authoritative continuous
        timeline: it always yields the current phase and day, even when the
        bot was offline or no body_cycle condition is currently active. Active
        conditions are only used as a fallback for old data without an anchor.

        Returns:
            Phase position details, or an empty dict when the strategy is off
            or the cycle has not started yet.
        """
        if not self._advanced_cycle_enabled():
            return {}
        now = _now_ts()
        meta = self.data.get("body_cycle_state")
        anchor_ts = _safe_float(meta.get("cycle_anchor_ts"), 0) if isinstance(meta, dict) else 0
        phase = ""
        day_in_phase = 0
        if anchor_ts > 0:
            cycle_day = int((now - anchor_ts) // 86400) + 1
            phase, day_in_phase = self._advanced_cycle_position_from_offset(cycle_day)
        else:
            # Legacy fallback for historical data created before the anchor
            # existed. The anchor is always seeded on first enable now, so this
            # branch only matters while migrating old conditions.
            conditions = self.data.get("state_conditions", [])
            if isinstance(conditions, list):
                for cond in conditions:
                    if not isinstance(cond, dict) or str(cond.get("kind") or "") != "body_cycle":
                        continue
                    cond_phase = str(cond.get("phase") or "")
                    if cond_phase not in self._ADVANCED_CYCLE_PHASES:
                        continue
                    start_ts = _safe_float(cond.get("start_ts"), 0)
                    end_ts = _safe_float(cond.get("end_ts"), 0)
                    if start_ts <= now < end_ts:
                        phase = cond_phase
                        day_in_phase = int((now - start_ts) // 86400) + 1
                        break
            if not phase:
                return {}
        phase_days = self._advanced_cycle_phase_days(phase)
        day_in_phase = max(1, min(phase_days, int(day_in_phase)))
        label, mood, energy_delta, _ = self._advanced_cycle_phase_spec(phase)
        next_phase = self._ADVANCED_CYCLE_TRANSITIONS.get(phase, "")
        next_phase = self._ADVANCED_CYCLE_TRANSITIONS.get(phase, "").removeprefix("body_")
        return {
            "phase": phase,
            "phase_name": self._ADVANCED_CYCLE_PHASE_NAMES.get(phase, phase),
            "day_in_phase": day_in_phase,
            "phase_days": phase_days,
            "cycle_day": self._advanced_cycle_day_of_phase(phase, day_in_phase),
            "cycle_days": self._advanced_cycle_total_days(),
            "mood": _single_line(mood, 20),
            "energy_delta": int(energy_delta),
            "label": _single_line(label, 160),
            "next_phase": next_phase,
            "next_phase_name": self._ADVANCED_CYCLE_PHASE_NAMES.get(next_phase, ""),
        }

    def _active_cycle_discomfort_conditions(self) -> list[dict[str, Any]]:
        now = _now_ts()
        items: list[dict[str, Any]] = []
        conditions = self.data.get("state_conditions", [])
        if not isinstance(conditions, list):
            return items
        for cond in conditions:
            if not isinstance(cond, dict) or str(cond.get("kind") or "") != "cycle_discomfort":
                continue
            if _safe_float(cond.get("start_ts"), 0) <= now < _safe_float(cond.get("end_ts"), 0):
                items.append(
                    {
                        "type": _single_line(cond.get("phase"), 12) or "经期不适",
                        "label": _single_line(cond.get("label"), 80),
                        "mood": _single_line(cond.get("mood"), 12),
                    }
                )
        return items

    def _maybe_pick_cycle_discomfort(self, deferred_state_updates: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """Roll once per day for a menstrual discomfort episode on the current phase.

        Only runs when the discomfort simulation, the advanced six-phase
        strategy and the persona cycle allowance are all enabled, and only
        during phases allowed per discomfort type. Rolls at most once per
        calendar day and skips the roll while another discomfort condition is
        still active.

        Returns:
            A cycle_discomfort condition dict, or None when skipped.
        """
        if not bool(runtime_persona_setting(self, "advanced_cycle_discomfort_simulation", False)):
            return None
        if not self._persona_state_profile().get("allow_cycle", False):
            return None
        intensity = _safe_int(runtime_persona_setting(self, "humanized_state_intensity", 50), 50, 0, 100)
        if intensity <= 0:
            return None
        meta = self.data.get("body_cycle_state")
        meta = dict(meta) if isinstance(meta, dict) else {}
        if meta.get("last_discomfort_roll_date") == _today_key():
            return None
        runtime = self._advanced_cycle_runtime()
        phase = runtime.get("phase") if runtime else ""
        if phase not in self._ADVANCED_CYCLE_PHASES:
            return None
        now = _now_ts()
        conditions = self.data.get("state_conditions", [])
        if isinstance(conditions, list):
            for cond in conditions:
                if (
                    isinstance(cond, dict)
                    and str(cond.get("kind") or "") == "cycle_discomfort"
                    and _safe_float(cond.get("end_ts"), 0) > now
                ):
                    return None
        # One roll attempt per day regardless of the outcome, so a failed roll
        # does not give the phase extra chances later the same day.
        if deferred_state_updates is None:
            meta["last_discomfort_roll_date"] = _today_key()
            self.data["body_cycle_state"] = meta
        else:
            deferred_state_updates["cycle_discomfort_roll_date"] = _today_key()
        chance = _safe_int(runtime_persona_setting(self, "advanced_cycle_discomfort_chance", 55), 55, 0, 100)
        if chance <= 0 or random.random() > chance / 100.0:
            return None
        raw_types = str(runtime_persona_setting(self, "advanced_cycle_discomfort_types", "痛经,头痛,腰酸,乏力") or "痛经,头痛,腰酸,乏力")
        requested = {token.strip() for token in raw_types.replace("，", ",").split(",") if token.strip()}
        candidates = [
            (name, spec)
            for name, spec in self._ADVANCED_CYCLE_DISCOMFORT_SPECS.items()
            if name in requested and phase in spec.get("phases", set())
        ]
        if not candidates:
            return None
        name, spec = random.choices(
            candidates,
            weights=[int(spec.get("weight") or 1) for _, spec in candidates],
            k=1,
        )[0]
        energy_delta = int((spec.get("energy_delta") or 0) * max(0.5, intensity / 50.0))
        return self._make_condition(
            kind="cycle_discomfort",
            title="经期不适",
            label=_single_line(spec.get("label"), 80),
            mood=_single_line(spec.get("mood"), 12) or "疲惫",
            energy_delta=energy_delta,
            duration_hours=_safe_int(spec.get("duration_hours"), 6, 1, 24),
            intensity=random.randint(45, max(46, min(92, 40 + intensity))),
            cause="生理周期阶段伴随不适",
            phase=name,
            episode_key=f"cycle-discomfort-{_today_key()}",
        )

    def _advanced_cycle_linked_energy(self, phase: str) -> int:
        median = self._ADVANCED_CYCLE_INTENSITY_MEDIANS.get(phase, 0.0)
        intensity = _safe_int(runtime_persona_setting(self, "humanized_state_intensity", 50), 50, 0, 100)
        return int(round(median * (intensity / 50.0)))

    def _advanced_cycle_phase_spec(self, phase: str) -> tuple[str, str, int, int]:
        defaults = {
            "menstrual": ("处于月经期，身体更容易疲倦，情绪感受稍敏锐", "疲惫", -12),
            "follicular": ("处于卵泡期，精力平稳回升，心情逐渐轻快", "轻快", 0),
            "pre_ovulation": ("处于排卵前期，身体逐渐轻盈，精力有所上升", "期待", 8),
            "ovulation": ("处于排卵期，精力较充足，社交意愿稍有增强", "明朗", 9),
            "luteal": ("处于黄体期，精力尚可，情绪整体平稳", "平稳", 5),
            "pms": ("处于 PMS 期，精力有所下降，情绪波动稍明显", "敏感", -8),
        }
        attributes = {
            "menstrual": ("advanced_cycle_menstrual_prompt", "advanced_cycle_menstrual_mood", "advanced_cycle_menstrual_energy"),
            "follicular": ("advanced_cycle_follicular_prompt", "advanced_cycle_follicular_mood", "advanced_cycle_follicular_energy"),
            "pre_ovulation": ("advanced_cycle_pre_ovulation_prompt", "advanced_cycle_pre_ovulation_mood", "advanced_cycle_pre_ovulation_energy"),
            "ovulation": ("advanced_cycle_ovulation_prompt", "advanced_cycle_ovulation_mood", "advanced_cycle_ovulation_energy"),
            "luteal": ("advanced_cycle_luteal_prompt", "advanced_cycle_luteal_mood", "advanced_cycle_luteal_energy"),
            "pms": ("advanced_cycle_pms_prompt", "advanced_cycle_pms_mood", "advanced_cycle_pms_energy"),
        }
        selected_phase = phase if phase in defaults else "menstrual"
        default_prompt, default_mood, default_energy = defaults[selected_phase]
        prompt_attr, mood_attr, energy_attr = attributes[selected_phase]
        label = _single_line(runtime_persona_setting(self, prompt_attr, default_prompt), 160) or default_prompt
        mood = _single_line(runtime_persona_setting(self, mood_attr, default_mood), 20) or default_mood
        energy_delta = (
            self._advanced_cycle_linked_energy(selected_phase)
        if bool(runtime_persona_setting(self, "advanced_cycle_link_intensity", False))
            else _safe_int(runtime_persona_setting(self, energy_attr, default_energy), default_energy, -50, 30)
        )
        return label, mood, energy_delta, self._advanced_cycle_phase_days(selected_phase) * 24

    def _advanced_cycle_transition_options(self, phase: str) -> list[dict[str, Any]]:
        target = self._ADVANCED_CYCLE_TRANSITIONS.get(phase, "")
        return [{"to": target, "base_weight": 1.0}] if target else []

    def _advanced_cycle_condition(
        self,
        phase: str,
        *,
        episode_key: str = "",
        cause: str = "周期阶段自然推进",
        duration_hours: int | None = None,
    ) -> dict[str, Any]:
        label, mood, energy_delta, configured_hours = self._advanced_cycle_phase_spec(phase)
        return self._make_condition(
            kind="body_cycle",
            title="周期",
            label=label,
            mood=mood,
            energy_delta=energy_delta,
            duration_hours=max(1, int(duration_hours or configured_hours)),
            intensity=max(35, _safe_int(runtime_persona_setting(self, "humanized_state_intensity", 50), 50, 0, 100)),
            cause=cause,
            phase=phase,
            episode_key=episode_key or f"body-cycle-{_today_key()}",
            transition_options=self._advanced_cycle_transition_options(phase),
        )

    def _pick_advanced_cycle_spec(self, intensity: float) -> tuple[str, str, int, int]:
        neutral = ("不处于生理期", "平稳", 0, 24)
        if self._body_cycle_generation_blocked():
            return neutral
        meta = self.data.get("body_cycle_state", {})
        anchor_ts = _safe_float(meta.get("cycle_anchor_ts"), 0) if isinstance(meta, dict) else 0
        if anchor_ts > 0:
            # Once the continuous timeline is anchored, phase progression is
            # deterministic; a random new-cycle pick would shift it backwards.
            return neutral
        now = _now_ts()
        expected_ts = _safe_float(meta.get("next_expected_start_ts"), 0) if isinstance(meta, dict) else 0
        if expected_ts > 0:
            days_late = max(0.0, (now - expected_ts) / 86400)
            chance = min(0.75, 0.22 + days_late * 0.14) * max(0.35, min(1.15, intensity))
        else:
            chance = 0.10 * max(0.35, min(1.2, intensity))
        if random.random() > chance:
            return neutral
        return self._advanced_cycle_phase_spec("menstrual")

    def _body_cycle_generation_blocked(self, now: float | None = None) -> bool:
        now = _now_ts() if now is None else now
        meta = self.data.get("body_cycle_state", {})
        if isinstance(meta, dict):
            expected_ts = _safe_float(meta.get("next_expected_start_ts"), 0)
            if expected_ts > 0 and now < expected_ts - 2 * 86400:
                return True
            if expected_ts <= 0 and _safe_float(meta.get("last_end_ts"), 0) + 18 * 86400 > now:
                return True
        conditions = self.data.get("state_conditions", [])
        if not isinstance(conditions, list):
            return False
        recent_floor = now - 14 * 86400
        for cond in conditions:
            if not isinstance(cond, dict) or str(cond.get("kind") or "") != "body_cycle":
                continue
            start_ts = _safe_float(cond.get("start_ts"), 0)
            end_ts = _safe_float(cond.get("end_ts"), 0)
            if end_ts > now or max(start_ts, end_ts) >= recent_floor:
                return True
        return False

    def _pick_body_cycle_spec(
        self,
        cycle_pool: list[tuple[str, str, int, int]],
        intensity: float,
    ) -> tuple[str, str, int, int]:
        neutral = cycle_pool[0]
        if self._body_cycle_generation_blocked():
            return neutral
        now = _now_ts()
        meta = self.data.get("body_cycle_state", {})
        expected_ts = _safe_float(meta.get("next_expected_start_ts"), 0) if isinstance(meta, dict) else 0
        if expected_ts > 0:
            days_late = max(0.0, (now - expected_ts) / 86400)
            chance = min(0.65, 0.18 + days_late * 0.12) * max(0.35, min(1.15, intensity))
        else:
            chance = 0.085 * max(0.35, min(1.2, intensity))
        if random.random() > chance:
            return neutral
        return random.choices(cycle_pool[1:], weights=[0.45, 0.55], k=1)[0]

    def _record_body_cycle_episode(self, cond: dict[str, Any]) -> None:
        start_ts = _safe_float(cond.get("start_ts"), _now_ts())
        end_ts = _safe_float(cond.get("end_ts"), start_ts)
        phase = str(cond.get("phase") or self._infer_body_cycle_phase(str(cond.get("label") or "")))
        previous = self.data.get("body_cycle_state")
        meta = dict(previous) if isinstance(previous, dict) else {}
        payload = {
            "last_start_ts": start_ts,
            "last_end_ts": end_ts,
            "next_expected_start_ts": start_ts + self._body_cycle_interval_seconds(),
            "last_phase": phase,
            "last_label": _single_line(cond.get("label"), 80),
        }
        # Episode reconciliation rewrites this record whenever the bot
        # catches up after downtime. Keep the daily discomfort dedup marker
        # across those rewrites so one calendar day still gets one roll.
        if meta.get("last_discomfort_roll_date"):
            payload["last_discomfort_roll_date"] = _single_line(
                meta.get("last_discomfort_roll_date"), 16
            )
        if phase in self._ADVANCED_CYCLE_PHASES:
            payload["strategy"] = "advanced"
            previous_anchor = _safe_float(meta.get("cycle_anchor_ts"), 0)
            if phase == "menstrual" and previous_anchor <= 0:
                payload["cycle_anchor_ts"] = start_ts
            elif previous_anchor > 0:
                payload["cycle_anchor_ts"] = previous_anchor
            if phase != "menstrual" and _safe_float(meta.get("last_start_ts"), 0) > 0:
                payload["last_start_ts"] = _safe_float(meta.get("last_start_ts"), start_ts)
                payload["next_expected_start_ts"] = _safe_float(
                    meta.get("next_expected_start_ts"),
                    payload["last_start_ts"] + self._advanced_cycle_total_days() * 86400,
                )
            for key in ("manual_offset", "manual_offset_signature", "manual_offset_phase", "manual_offset_day_in_phase"):
                if key in meta:
                    payload[key] = meta[key]
        else:
            payload["strategy"] = "legacy"
        self.data["body_cycle_state"] = payload
