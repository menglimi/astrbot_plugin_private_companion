# -*- coding: utf-8 -*-
"""UserMemoryRelationshipBoundaryPart03Mixin。

由 tools/split_mixin_domain.py 从 user_memory_relationship_boundary.py 机械抽取（6 个方法 + 0 个模块级名字 + 0 个类级赋值 / 423 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 UserMemoryRelationshipBoundaryMixin）。
"""
from __future__ import annotations

import math
import random
from .helpers import _now_ts, _safe_float, _safe_int, _single_line
from .persona_config import runtime_persona_setting
from .relationship_policy import relationship_stage_for_score
from copy import deepcopy
from typing import Any



class UserMemoryRelationshipBoundaryPart03Mixin:
    """UserMemoryRelationshipBoundaryPart03Mixin（从 UserMemoryRelationshipBoundaryMixin 拆出）。"""


    def _apply_relationship_violation_policy(
        self,
        user: dict[str, Any],
        intent: dict[str, Any] | None,
        *,
        event_id: str = "",
        now: float | None = None,
    ) -> dict[str, Any]:
        """Apply bounded penalties/recovery for secondary-user boundary events."""
        if not isinstance(user, dict) or not isinstance(intent, dict):
            return {"changed": False, "reason": "invalid_input"}
        if not bool(runtime_persona_setting(self, "enable_relationship_violation_penalties", True)):
            return {"changed": False, "reason": "disabled"}
        if not bool(runtime_persona_setting(self, "enable_custom_relationship_stage_policy", True)):
            return {"changed": False, "reason": "relationship_system_disabled"}
        try:
            role = self._private_user_role(user, str(user.get("user_id") or ""))
        except Exception:
            role = str(user.get("relationship_role") or "friend")
        if str(role).strip().lower() == "owner":
            return {"changed": False, "reason": "owner_exempt"}
        ts = _now_ts() if now is None else _safe_float(now, _now_ts(), 0)
        state = self._relationship_violation_state(user)
        self._settle_relationship_violation_recovery(user, now=ts)
        event = str(intent.get("emotion_event") or "neutral").strip().lower()
        feedback_kind = str(intent.get("boundary_feedback_kind") or intent.get("violation_kind") or "").strip().lower()
        explicit_id = _single_line(event_id, 96)
        if explicit_id and explicit_id == _single_line(state.get("last_event_id"), 96) and (
            event in {"boundary_violation", "hurt", "apology"} or feedback_kind == "confession"
        ):
            return {"changed": False, "reason": "duplicate_event", "state": deepcopy(state)}
        if feedback_kind == "confession":
            state["confession_count"] = _safe_int(state.get("confession_count"), 0, 0) + 1
            state["confession_until"] = ts + 30 * 60
            state["last_reason"] = _single_line(intent.get("boundary_feedback_reason") or "表达喜欢或想念", 120)
            state["last_event_id"] = explicit_id
            self._schedule_data_save(sections={"users"})
            boundary_logger = getattr(self, "_log_relationship_boundary_event", None)
            if callable(boundary_logger):
                boundary_logger(
                    user,
                    "confession",
                    penalty=0,
                    current_tier=_single_line(intent.get("boundary_current_tier"), 32) or "unknown",
                )
            return {"changed": True, "reason": "confession_feedback", "state": deepcopy(state)}
        if event == "apology":
            if not bool(runtime_persona_setting(self, "enable_relationship_boundary_apology", True)):
                return {"changed": False, "reason": "apology_recovery_disabled", "state": deepcopy(state)}
            outstanding = _safe_int(state.get("unrecovered_points"), 0, 0, 60)
            if outstanding <= 0:
                return {"changed": False, "reason": "nothing_to_recover", "state": deepcopy(state)}
            last_kind = _single_line(state.get("last_kind"), 40) or "general"
            apology_by_kind = state.get("apology_by_kind") if isinstance(state.get("apology_by_kind"), dict) else {}
            apology_limit = _safe_int(
                runtime_persona_setting(self, "relationship_boundary_apology_duplicate_limit", 3),
                3,
                1,
                20,
            )
            apology_count = _safe_int(apology_by_kind.get(last_kind), 0, 0)
            if apology_count >= apology_limit:
                state["last_event_id"] = explicit_id
                return {"changed": False, "reason": "apology_trust_exhausted", "state": deepcopy(state)}
            apology_ratio = _safe_float(
                runtime_persona_setting(self, "relationship_boundary_apology_restore_ratio", 0.6),
                0.6,
                0.0,
                1.0,
            )
            recover = min(6, max(1, int(math.ceil(outstanding * apology_ratio))))
            recoverable_score = _safe_int(state.get("recoverable_score"), outstanding, 0, 60)
            recover = min(recover, recoverable_score if "recoverable_score" in state else outstanding)
            if recover <= 0:
                state["last_event_id"] = explicit_id
                return {"changed": False, "reason": "apology_recovery_quota_exhausted", "state": deepcopy(state)}
            result = self._apply_relationship_event(
                user,
                recover,
                reason_code="relationship_violation_recovery",
                event_id=explicit_id,
                now=ts,
            )
            applied = _safe_int(result.get("delta"), 0, 0, recover)
            if applied:
                state["unrecovered_points"] = max(0, outstanding - applied)
                state["stage_load"] = max(0, _safe_int(state.get("stage_load"), outstanding, 0, 120) - applied)
                state["recoverable_score"] = max(0, recoverable_score - applied)
                state["apology_recovered_points"] = min(6, _safe_int(state.get("apology_recovered_points"), 0, 0, 6) + applied)
                state["apology_recovered_kind"] = last_kind
                apology_by_kind[last_kind] = apology_count + 1
                state["apology_by_kind"] = apology_by_kind
                state["last_recovery_at"] = ts
                state["apology_speedup_until"] = ts + max(
                    3600,
                    outstanding
                    * 60
                    * _safe_int(
                        runtime_persona_setting(
                            self,
                            "relationship_violation_recovery_minutes_per_point",
                            180,
                        ),
                        180,
                        15,
                        10080,
                    ),
                )
                state["last_event_id"] = explicit_id
                stage_refresher = getattr(self, "_refresh_relationship_violation_stage", None)
                if callable(stage_refresher):
                    stage_refresher(state, now=ts)
                self._schedule_data_save(sections={"users"})
                boundary_logger = getattr(self, "_log_relationship_boundary_event", None)
                if callable(boundary_logger):
                    boundary_logger(
                        user,
                        "apology",
                        recovered=applied,
                        remaining=state.get("unrecovered_points"),
                        stage=state.get("stage"),
                    )
            return {"changed": bool(applied), "reason": "apology_recovery", "recovered": applied, "state": deepcopy(state)}
        emotion_confidence = _safe_float(intent.get("emotion_confidence"), 1.0, 0.0, 1.0)
        is_severe_hurt_violation = (
            event == "hurt"
            and emotion_confidence >= 0.8
            and str(intent.get("emotion_target") or "").lower() == "bot"
            and _safe_int(intent.get("emotion_intensity"), 0, 0, 100) >= 76
            and str(intent.get("emotion_rule") or "") in {"severe_hurt", "identity_hurt"}
        )
        if (
            (event != "boundary_violation" and not is_severe_hurt_violation)
            or (event == "boundary_violation" and emotion_confidence < 0.8)
            or (
                str(intent.get("emotion_target") or "").lower() != "bot"
                if event == "boundary_violation"
                else str(intent.get("emotion_target") or "").lower() not in {"bot", "ambiguous"}
            )
        ):
            return {"changed": False, "reason": "no_violation", "state": deepcopy(state)}
        severity = _safe_int(intent.get("violation_severity"), 2 if is_severe_hurt_violation else 1, 1, 3)
        outstanding = _safe_int(state.get("unrecovered_points"), 0, 0, 60)
        prior_apology = _safe_int(state.get("apology_recovered_points"), 0, 0, 6)
        clawed_back = 0
        violation_kind = feedback_kind or ("hurt" if is_severe_hurt_violation else "boundary")
        if violation_kind == "bottom_line" and not bool(
            runtime_persona_setting(self, "enable_relationship_boundary_bottom_line", True)
        ):
            violation_kind = "harassment"
        apology_kind = _single_line(state.get("apology_recovered_kind"), 40)
        if prior_apology and (not apology_kind or apology_kind == violation_kind):
            clawback = self._apply_relationship_event(
                user,
                -prior_apology,
                reason_code="relationship_violation_clawback",
                event_id=explicit_id,
                now=ts,
            )
            clawed_back = max(0, -_safe_int(clawback.get("delta"), 0, -6, 0))
            state["apology_recovered_points"] = 0
            state["apology_recovered_kind"] = ""
        penalty_defaults = {
            1: _safe_int(runtime_persona_setting(self, "relationship_boundary_penalty_light", 4), 4, 1, 60),
            2: _safe_int(runtime_persona_setting(self, "relationship_boundary_penalty_mid", 7), 7, 1, 60),
            3: _safe_int(runtime_persona_setting(self, "relationship_boundary_penalty_severe", 12), 12, 1, 60),
        }
        penalty = (
                _safe_int(runtime_persona_setting(self, "relationship_boundary_penalty_bottom_line", 14), 14, 1, 60)
            if violation_kind == "bottom_line"
            else penalty_defaults[severity]
        )
        deduct_factor_getter = getattr(self, "_boundary_feedback_tier_deduct_factor", None)
        try:
            deduct_factor = float(deduct_factor_getter(user)) if callable(deduct_factor_getter) else 1.0
        except Exception:
            deduct_factor = 1.0
        penalty = max(1, int(math.ceil(penalty * max(0.3, min(1.0, deduct_factor)))))
        result = self._apply_relationship_event(
            user,
            -penalty,
            reason_code="relationship_violation",
            event_id=explicit_id,
            now=ts,
        )
        applied_penalty = max(0, -_safe_int(result.get("delta"), 0, -60, 0))
        previous_recoverable = _safe_int(state.get("recoverable_score"), 0, 0, 60)
        if outstanding > 0 and previous_recoverable > 0:
            state["forfeited_recovery_score"] = min(
                120,
                _safe_int(state.get("forfeited_recovery_score"), 0, 0, 120) + previous_recoverable,
            )
            previous_recoverable = 0
        recover_ratio = {
            1: _safe_float(runtime_persona_setting(self, "relationship_boundary_recover_ratio_light", 0.5), 0.5, 0.0, 1.0),
            2: _safe_float(runtime_persona_setting(self, "relationship_boundary_recover_ratio_mid", 0.33), 0.33, 0.0, 1.0),
            3: _safe_float(runtime_persona_setting(self, "relationship_boundary_recover_ratio_severe", 0.25), 0.25, 0.0, 1.0),
        }[severity]
        if violation_kind == "bottom_line":
            recover_ratio *= 0.5
        new_recoverable = int(applied_penalty * recover_ratio)
        state["recoverable_score"] = min(60, previous_recoverable + new_recoverable)
        state["unrecovered_points"] = min(60, outstanding + max(severity, new_recoverable))
        state["stage_load"] = min(120, _safe_int(state.get("stage_load"), 0, 0, 120) + applied_penalty + clawed_back)
        state["incident_count"] = _safe_int(state.get("incident_count"), 0, 0) + 1
        state["repeat_count"] = _safe_int(state.get("repeat_count"), 0, 0) + (1 if outstanding > 0 else 0)
        state["level"] = min(6, max(_safe_int(state.get("level"), 0, 0, 6), severity + state["repeat_count"] // 2))
        state["last_violation_at"] = ts
        state["last_recovery_at"] = ts
        state["last_severity"] = severity
        state["last_kind"] = violation_kind
        state["last_reason"] = _single_line(intent.get("emotion_reason") or intent.get("emotion_rule"), 120)
        state["cooldown_until"] = ts + {1: 20, 2: 45, 3: 90}[severity] * 60
        state["last_event_id"] = explicit_id
        violations = state.get("violations") if isinstance(state.get("violations"), list) else []
        violations.append(
            {
                "ts": ts,
                "event_id": explicit_id,
                "kind": violation_kind,
                "severity": severity,
                "penalty": applied_penalty,
                "text": _single_line(intent.get("text"), 120),
                "scope": _single_line(intent.get("boundary_scope"), 20) or "private",
            }
        )
        state["violations"] = violations[-50:]
        demoted = 0
        if violation_kind == "bottom_line":
            state["bottom_line_count"] = _safe_int(state.get("bottom_line_count"), 0, 0) + 1
            bottom_count = _safe_int(state.get("bottom_line_count"), 0, 0)
            if bottom_count == 1:
                state["stage_load"] = max(
                    state["stage_load"],
                    _safe_int(
                        runtime_persona_setting(self, "relationship_boundary_stage_forbid_points", 12),
                        12,
                        1,
                        120,
                    ),
                )
            elif bottom_count >= 2:
                state["stage_load"] = max(
                    state["stage_load"],
                    _safe_int(
                        runtime_persona_setting(self, "relationship_boundary_stage_reflect_points", 20),
                        20,
                        1,
                        120,
                    ),
                )
            if bottom_count >= 3 and _safe_int(state.get("last_bottom_line_demoted_count"), 0, 0) < bottom_count:
                demoter = getattr(self, "_demote_relationship_after_repeated_bottom_line", None)
                if callable(demoter):
                    demoted = max(0, _safe_int(demoter(user, event_id=explicit_id, now=ts), 0, 0, 1200))
                state["last_bottom_line_demoted_count"] = bottom_count
        stage_refresher = getattr(self, "_refresh_relationship_violation_stage", None)
        if callable(stage_refresher):
            stage_refresher(state, now=ts)
        side_effects = getattr(self, "_record_relationship_boundary_side_effects", None)
        if callable(side_effects) and (applied_penalty or clawed_back):
            side_effects(user, intent, state, now=ts)
        self._schedule_data_save(sections={"users", "boundary_feedback_reports"})
        boundary_logger = getattr(self, "_log_relationship_boundary_event", None)
        if callable(boundary_logger):
            boundary_logger(
                user,
                "violation",
                kind=violation_kind,
                severity=severity,
                penalty=applied_penalty,
                clawback=clawed_back,
                stage=state.get("stage"),
                demoted=demoted,
                current_tier=_single_line(intent.get("boundary_current_tier"), 32) or "unknown",
                suitable_tier=_single_line(intent.get("boundary_suitable_tier"), 32) or "unknown",
            )
        return {
            "changed": bool(applied_penalty or clawed_back),
            "reason": "boundary_violation",
            "severity": severity,
            "penalty": applied_penalty,
            "clawback": clawed_back,
            "demoted": demoted,
            "state": deepcopy(state),
        }

    def _relationship_violation_prompt_hint(self, user: dict[str, Any], *, now: float | None = None) -> str:
        if not isinstance(user, dict):
            return ""
        state = self._relationship_violation_state(user)
        ts = _now_ts() if now is None else now
        self._settle_relationship_violation_recovery(user, now=ts)
        points = _safe_int(state.get("unrecovered_points"), 0, 0, 60)
        if points <= 0 and _safe_float(state.get("confession_until"), 0) > ts:
            tone = _single_line(
                runtime_persona_setting(
                    self,
                    "relationship_boundary_tone_confession",
                    "把这次表达当作心意，不当作冒犯；结合当前关系自然害羞、迟疑或温和说明节奏，不必机械拒绝。",
                ),
                240,
            )
            return f"刚收到对方的喜欢或想念表达：{tone}"
        if points <= 0:
            return ""
        stage = str(state.get("stage") or "normal")
        kind = str(state.get("last_kind") or "boundary")
        if kind == "bottom_line":
            default_tone = "明确表达这触碰了重要底线，受伤和距离感可以真实存在；不要功能化播报惩罚，也不要立即恢复亲密。"
            tone = _single_line(
                runtime_persona_setting(self, "relationship_boundary_tone_bottom_line", default_tone),
                240,
            ) or default_tone
        elif _safe_int(state.get("last_severity"), 1, 1, 3) >= 3:
            default_tone = "明显收住亲密表达，直接说明不舒服并拒绝继续；保持角色口吻，不使用系统式警告。"
            tone = _single_line(
                runtime_persona_setting(self, "relationship_boundary_tone_severe", default_tone),
                240,
            ) or default_tone
        elif stage in {"forbid", "reflect"}:
            default_tone = "平静而明确地划清界限，减少主动贴近和暧昧回应；可以说明原因，但不要反复说教。"
            tone = _single_line(
                runtime_persona_setting(self, "relationship_boundary_tone_mid", default_tone),
                240,
            ) or default_tone
        else:
            default_tone = "轻微降低亲密度，带一点迟疑或回避并自然说明节奏；不要把普通互动渲染成严重冒犯。"
            tone = _single_line(
                runtime_persona_setting(self, "relationship_boundary_tone_light", default_tone),
                240,
            ) or default_tone
        relationship_stage = relationship_stage_for_score(
            user.get("relationship_score", 0),
            runtime_persona_setting(self, "relationship_stage_policy", None),
        ).get("phase", {})
        relationship_stage_key = str(relationship_stage.get("key") or "acquaintance")
        if relationship_stage_key in {"deeply_distant", "strongly_distant", "distant", "acquaintance"}:
            tier_tone = _single_line(
                runtime_persona_setting(
                    self,
                    "relationship_boundary_tone_silent",
                    "关系尚浅时不必长篇袒露脆弱，可以安静收住互动并记住这次不舒服。",
                ),
                240,
            )
        elif relationship_stage_key in {"intimate", "deeply_bonded"}:
            tier_tone = _single_line(
                runtime_persona_setting(
                    self,
                    "relationship_boundary_tone_communicate",
                    "关系很深时可以因为信任而说清为什么难过或生气，但亲密关系不等于放弃边界。",
                ),
                240,
            )
        else:
            tier_tone = ""
        if stage == "reflect":
            stage_hint = "当前处于反思/冷静阶段，回复可以更短、更克制，不主动开启新亲密话题。"
        elif stage == "forbid":
            stage_hint = "当前需要明确边界，避免用撒娇或玩笑把拒绝冲淡。"
        elif stage == "avoid":
            stage_hint = "当前略有回避，仍需先正常回应对方这一轮的实际内容。"
        else:
            stage_hint = "余波尚未完全恢复，先自然回应，不要突然恢复到高亲密度。"
        apology_hint = (
            "若对方真诚道歉，可以承认这份修复意愿并逐步缓和；不要一条道歉就抹去全部余波。"
            if _safe_int(state.get("apology_recovered_points"), 0, 0, 6) <= 0
            else "已经接受过一次修复；同类行为再次发生时应表现出信任受损，而不是重复无条件原谅。"
        )
        return f"关系边界余波：{tone} {tier_tone} {stage_hint} {apology_hint}"

    @staticmethod
    def _boundary_feedback_level_key(state: dict[str, Any]) -> str:
        if str(state.get("last_kind") or "") == "bottom_line":
            return "bottom_line"
        severity = _safe_int(state.get("last_severity"), 1, 1, 3)
        return {1: "light", 2: "mid", 3: "severe"}[severity]

    def _boundary_feedback_probability(self, prefix: str, level: str, default: float) -> float:
        return _safe_float(
            runtime_persona_setting(self, f"relationship_boundary_{prefix}_probability_{level}", default),
            default,
            0.0,
            1.0,
        )

    def _record_relationship_boundary_side_effects(
        self,
        user: dict[str, Any],
        intent: dict[str, Any],
        state: dict[str, Any],
        *,
        now: float,
    ) -> None:
        event_id = _single_line(state.get("last_event_id"), 96)
        if event_id and event_id == _single_line(state.get("last_side_effect_event_id"), 96):
            return
        state["last_side_effect_event_id"] = event_id
        level = self._boundary_feedback_level_key(state)
        vent_defaults = {"light": 0.15, "mid": 0.35, "severe": 0.6, "bottom_line": 0.9}
        if bool(runtime_persona_setting(self, "enable_relationship_boundary_vent", True)) and random.random() <= self._boundary_feedback_probability(
            "vent", level, vent_defaults[level]
        ):
            self._append_relationship_boundary_vent(user, intent, state, now=now)

        if not bool(runtime_persona_setting(self, "enable_relationship_boundary_owner_report", True)):
            return
        report = self._queue_relationship_boundary_owner_report(user, intent, state, now=now)
        if not report:
            return
        report_defaults = {"light": 0.12, "mid": 0.3, "severe": 0.55, "bottom_line": 0.85}
        if random.random() <= self._boundary_feedback_probability("owner_report", level, report_defaults[level]):
            task_creator = getattr(self, "_create_lifecycle_background_task", None)
            operation = self._send_relationship_boundary_owner_report(report)
            if callable(task_creator):
                task_creator(operation, label="relationship_boundary_owner_report")
            else:
                closer = getattr(operation, "close", None)
                if callable(closer):
                    closer()

    def _boundary_feedback_display_name(self, user: dict[str, Any]) -> str:
        nickname = _single_line(user.get("nickname") or user.get("name"), 32)
        if nickname and nickname != "你":
            return nickname
        user_id = _single_line(user.get("user_id") or user.get("id"), 80)
        return f"{user_id[-4:]}号" if user_id else "那个人"
