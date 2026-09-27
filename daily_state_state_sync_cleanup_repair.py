# -*- coding: utf-8 -*-
"""DailyStateStateSyncCleanupRepairMixin。

由 tools/split_mixin_domain.py 从 daily_state_state.py 机械抽取（6 个方法 + 0 个模块级名字 + 0 个类级赋值 / 305 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 DailyStateStateMixin）。
"""
from __future__ import annotations

from .daily_state_state_shared import _now_ts, logger
from .daily_state_state_shared import Any
from .daily_state_state_shared import _safe_float
from .daily_state_state_shared import _safe_int
from .daily_state_state_shared import _single_line
from .daily_state_state_shared import runtime_persona_setting



class DailyStateStateSyncCleanupRepairMixin:
    """DailyStateStateSyncCleanupRepairMixin（从 DailyStateStateMixin 拆出）。"""


    def _synchronize_body_cycle_strategy(self, conditions: list[Any], now: float) -> list[Any]:
        advanced_enabled = self._advanced_cycle_enabled()
        desired_mode = "advanced" if advanced_enabled else "legacy"
        previous_mode = str(self.data.get("body_cycle_strategy_mode") or "")
        kept: list[Any] = []
        removed = 0
        for cond in conditions:
            if not isinstance(cond, dict) or str(cond.get("kind") or "") != "body_cycle":
                kept.append(cond)
                continue
            phase = str(cond.get("phase") or self._infer_body_cycle_phase(str(cond.get("label") or "")))
            is_advanced = phase in self._ADVANCED_CYCLE_PHASES
            if is_advanced != advanced_enabled:
                removed += 1
                continue
            cond["phase"] = phase
            kept.append(cond)
        if removed:
            existing_meta = self.data.get("body_cycle_state")
            # A legacy condition may still be present while an advanced
            # timeline has already been anchored. Remove only the incompatible
            # condition in that case; resetting the anchor would move the
            # user back to day one after a restart or migration.
            keep_continuous_state = (
                desired_mode == "advanced"
                and isinstance(existing_meta, dict)
                and _safe_float(existing_meta.get("cycle_anchor_ts"), 0) > 0
            )
            if not keep_continuous_state:
                self.data.pop("body_cycle_state", None)
            logger.info(
                "周期策略切换，已清理不兼容旧状态: mode=%s removed=%s",
                desired_mode,
                removed,
            )
        self.data["body_cycle_strategy_mode"] = desired_mode

        if not advanced_enabled:
            return kept

        offset = _safe_int(runtime_persona_setting(self, "advanced_cycle_start_offset", 0), 0, 0, 180)
        meta = self.data.get("body_cycle_state")
        meta = dict(meta) if isinstance(meta, dict) else {}
        if offset <= 0:
            if meta.get("manual_offset_signature"):
                for key in ("manual_offset", "manual_offset_signature", "manual_offset_phase", "manual_offset_day_in_phase"):
                    meta.pop(key, None)
                self.data["body_cycle_state"] = meta
            has_cycle_condition = any(
                isinstance(cond, dict) and str(cond.get("kind") or "") == "body_cycle"
                for cond in kept
            )
            anchor_ts = _safe_float(meta.get("cycle_anchor_ts"), 0)
            if not has_cycle_condition and anchor_ts <= 0:
                condition = self._advanced_cycle_condition(
                    "menstrual",
                    cause="六阶段周期策略首次启用，自然进入第一周期",
                )
                kept.append(condition)
                self._record_body_cycle_episode(condition)
                logger.info("六阶段周期策略首次启用，已从月经期第 1 天开始推进")
            return kept

        signature = self._advanced_cycle_offset_signature(offset)
        if meta.get("manual_offset_signature") == signature:
            return kept

        kept = [
            cond
            for cond in kept
            if not (isinstance(cond, dict) and str(cond.get("kind") or "") == "body_cycle")
        ]
        phase, day_in_phase = self._advanced_cycle_position_from_offset(offset)
        remaining_days = self._advanced_cycle_phase_days(phase) - day_in_phase + 1
        condition = self._advanced_cycle_condition(
            phase,
            cause="管理员设置了周期起始日",
            duration_hours=remaining_days * 24,
        )
        kept.append(condition)
        self._record_body_cycle_episode(condition)
        meta = self.data.get("body_cycle_state")
        meta = dict(meta) if isinstance(meta, dict) else {}
        meta.update(
            {
                "manual_offset": offset,
                "manual_offset_signature": signature,
                "manual_offset_phase": phase,
                "manual_offset_day_in_phase": day_in_phase,
                "cycle_anchor_ts": max(0.0, now - (offset - 1) * 86400),
                "strategy": "advanced",
            }
        )
        self.data["body_cycle_state"] = meta
        logger.info(
            "已应用六阶段周期起始日: offset=%s phase=%s phase_day=%s previous_mode=%s",
            offset,
            phase,
            day_in_phase,
            previous_mode or "unknown",
        )
        return kept

    def _cleanup_expired_conditions(self) -> set[str]:
        now = _now_ts()
        had_body_cycle_state = "body_cycle_state" in self.data
        conditions = self.data.setdefault("state_conditions", [])
        if not isinstance(conditions, list):
            self.data["state_conditions"] = []
            return set()
        profile = self._persona_state_profile()
        if not profile.get("allow_cycle", False):
            before_count = len(conditions)
            conditions = [
                cond for cond in conditions
                if not isinstance(cond, dict) or str(cond.get("kind") or "") not in {"body_cycle", "cycle_discomfort"}
            ]
            removed_count = before_count - len(conditions)
            if removed_count:
                self.data.pop("body_cycle_state", None)
                logger.info("生理期模拟已关闭，清理旧周期状态: removed=%s", removed_count)
        else:
            conditions = self._synchronize_body_cycle_strategy(conditions, now)
            conditions = self._repair_body_cycle_conditions(conditions, now)
            if not self._advanced_cycle_enabled() or not bool(
                runtime_persona_setting(self, "advanced_cycle_discomfort_simulation", False)
            ):
                before_count = len(conditions)
                conditions = [
                    cond
                    for cond in conditions
                    if not isinstance(cond, dict) or str(cond.get("kind") or "") != "cycle_discomfort"
                ]
                if len(conditions) < before_count:
                    logger.info(
                        "不适模拟已关闭，清理残留经期不适状态: removed=%s",
                        before_count - len(conditions),
                    )
        active = []
        expired = []
        for cond in conditions:
            if not isinstance(cond, dict):
                continue
            if _safe_float(cond.get("end_ts"), 0) > now:
                active.append(cond)
            else:
                expired.append(cond)
        for cond in expired:
            active.extend(self._spawn_followup_conditions(cond))
        active = self._reconcile_advanced_cycle_condition(active, now)
        active = self._prune_active_hunger_conditions(active, now)
        self.data["state_conditions"] = active
        return {
            "body_cycle_state"
        } if had_body_cycle_state and "body_cycle_state" not in self.data else set()

    def _prune_active_hunger_conditions(self, conditions: list[dict[str, Any]], now: float) -> list[dict[str, Any]]:
        hunger_items = [
            cond for cond in conditions
            if isinstance(cond, dict)
            and str(cond.get("kind") or "") == "hunger"
            and _safe_float(cond.get("start_ts"), 0) <= now < _safe_float(cond.get("end_ts"), 0)
        ]
        if len(hunger_items) <= 1:
            return conditions
        hunger_items.sort(key=lambda item: (_safe_float(item.get("start_ts"), 0), _safe_float(item.get("end_ts"), 0)), reverse=True)
        keep_id = hunger_items[0].get("id")
        pruned: list[dict[str, Any]] = []
        for cond in conditions:
            if isinstance(cond, dict) and str(cond.get("kind") or "") == "hunger" and cond.get("id") != keep_id:
                continue
            pruned.append(cond)
        logger.info("已清理重复饥饿状态: kept=%s removed=%s", keep_id or "-", len(hunger_items) - 1)
        return pruned

    def _repair_body_cycle_conditions(self, conditions: list[Any], now: float) -> list[dict[str, Any]]:
        repaired: list[dict[str, Any]] = []
        active_cycles: list[dict[str, Any]] = []
        last_cycle_end = 0.0
        for cond in conditions:
            if not isinstance(cond, dict):
                continue
            if str(cond.get("kind") or "") != "body_cycle":
                repaired.append(cond)
                continue
            label = _single_line(cond.get("label"), 80)
            phase = str(cond.get("phase") or self._infer_body_cycle_phase(label))
            cond["phase"] = phase
            start_ts = _safe_float(cond.get("start_ts"), now)
            if start_ts <= 0:
                start_ts = now
                cond["start_ts"] = start_ts
            max_hours = self._body_cycle_max_hours(phase, label)
            max_end_ts = start_ts + max_hours * 3600
            end_ts = _safe_float(cond.get("end_ts"), max_end_ts)
            if end_ts <= 0:
                end_ts = max_end_ts
            if end_ts > max_end_ts:
                end_ts = max_end_ts
                cond["end_ts"] = end_ts
                cond["duration_hours"] = max_hours
            if not cond.get("episode_key"):
                cond["episode_key"] = f"body-cycle-{self._environment_fromtimestamp(start_ts).strftime('%Y-%m-%d')}"
            last_cycle_end = max(last_cycle_end, end_ts)
            if start_ts <= now < end_ts:
                active_cycles.append(cond)
            repaired.append(cond)

        if len(active_cycles) > 1:
            active_cycles.sort(key=lambda item: _safe_float(item.get("start_ts"), 0), reverse=True)
            keep_id = active_cycles[0].get("id")
            filtered: list[dict[str, Any]] = []
            for cond in repaired:
                if str(cond.get("kind") or "") == "body_cycle" and cond.get("id") != keep_id:
                    cond["end_ts"] = min(_safe_float(cond.get("end_ts"), now), now - 1)
                filtered.append(cond)
            repaired = filtered

        if last_cycle_end > 0:
            meta = self.data.get("body_cycle_state")
            if not isinstance(meta, dict):
                meta = {}
            expected_ts = _safe_float(meta.get("next_expected_start_ts"), 0)
            base_start = _safe_float(meta.get("last_start_ts"), 0)
            if base_start <= 0:
                base_start = max(0.0, last_cycle_end - 4 * 86400)
            if self._advanced_cycle_enabled():
                if expected_ts <= 0:
                    expected_ts = base_start + self._advanced_cycle_total_days() * 86400
            else:
                if expected_ts <= 0 or expected_ts <= last_cycle_end:
                    expected_ts = base_start + 28 * 86400
                expected_ts = max(expected_ts, last_cycle_end + 18 * 86400)
            meta.update(
                {
                    "last_end_ts": max(_safe_float(meta.get("last_end_ts"), 0), last_cycle_end),
                    "next_expected_start_ts": expected_ts,
                }
            )
            self.data["body_cycle_state"] = meta
        return repaired

    def _reconcile_advanced_cycle_condition(self, conditions: list[dict[str, Any]], now: float) -> list[dict[str, Any]]:
        """Align the active cycle condition with the anchored continuous timeline.

        The anchor always knows the true current phase and day. When the bot
        was offline or a transition condition was spawned late, this replaces
        the stale condition with one positioned exactly on the timeline so its
        energy and mood effects never lag behind the displayed phase.

        Args:
            conditions: Currently active condition list after follow-up spawns.
            now: Current unix timestamp.

        Returns:
            The adjusted condition list.
        """
        if not self._advanced_cycle_enabled():
            return conditions
        meta = self.data.get("body_cycle_state")
        anchor_ts = _safe_float(meta.get("cycle_anchor_ts"), 0) if isinstance(meta, dict) else 0
        if anchor_ts <= 0:
            return conditions
        expected_phase, day_in_phase = self._advanced_cycle_position_from_offset(
            int((now - anchor_ts) // 86400) + 1
        )
        phase_days = self._advanced_cycle_phase_days(expected_phase)
        phase_start = anchor_ts + (self._advanced_cycle_day_of_phase(expected_phase, 1) - 1) * 86400
        active_cycles = [
            cond
            for cond in conditions
            if isinstance(cond, dict)
            and str(cond.get("kind") or "") == "body_cycle"
            and _safe_float(cond.get("start_ts"), 0) <= now < _safe_float(cond.get("end_ts"), 0)
        ]
        if len(active_cycles) == 1:
            cond = active_cycles[0]
            cond_start = _safe_float(cond.get("start_ts"), 0)
            if str(cond.get("phase") or "") == expected_phase and abs(cond_start - phase_start) < 6 * 3600:
                return conditions
        kept = [
            cond
            for cond in conditions
            if not (isinstance(cond, dict) and str(cond.get("kind") or "") == "body_cycle")
        ]
        condition = self._advanced_cycle_condition(
            expected_phase,
            cause="周期阶段自然推进",
        )
        condition["start_ts"] = phase_start
        condition["duration_hours"] = phase_days * 24
        condition["end_ts"] = phase_start + phase_days * 24 * 3600
        kept.append(condition)
        self._record_body_cycle_episode(condition)
        logger.info(
            "已对齐六阶段周期状态: phase=%s phase_start=%s day_in_phase=%s",
            expected_phase,
            self._environment_fromtimestamp(phase_start).strftime("%Y-%m-%d %H:%M"),
            day_in_phase,
        )
        return kept

    def _spawn_followup_conditions(self, cond: dict[str, Any]) -> list[dict[str, Any]]:
        choice = self._pick_condition_transition(cond)
        if not choice or choice == "stable":
            return []
        followup = self._build_transition_condition(choice, cond)
        if isinstance(followup, dict) and str(followup.get("kind") or "") == "body_cycle":
            self._record_body_cycle_episode(followup)
        return [followup] if followup else []
