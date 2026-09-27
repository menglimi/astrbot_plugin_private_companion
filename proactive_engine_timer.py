# -*- coding: utf-8 -*-
"""LLM 定时器/静默窗口域。

由 tools/split_mixin_domain.py 从 proactive_engine.py 机械抽取（9 个方法 + 0 个模块级名字 + 0 个类级赋值 / 194 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveEngineMixin）。
"""
from __future__ import annotations
from .proactive_engine_shared import _engine_host

from .helpers import _now_ts, _safe_float, _single_line
from .persona_config import runtime_persona_setting
from .proactive_engine_shared import _engine_proactive_window_timezone
from typing import Any



class ProactiveEngineTimerMixin:
    """LLM 定时器/静默窗口域（从 ProactiveEngineMixin 拆出）。"""


    def _llm_timer_pre_silence_seconds(self) -> float:
        return max(
            0.0,
            float(runtime_persona_setting(self, "timer_pre_silence_minutes", 20) or 0) * 60.0,
        )

    def _upcoming_llm_timer_ts(self, user: dict[str, Any], *, now: float | None = None) -> float:
        event = self._get_active_llm_timer(user)
        if not isinstance(event, dict):
            return 0.0
        scheduled_ts = _safe_float(event.get("scheduled_ts"), 0)
        check_now = _engine_host._now_ts() if now is None else now
        return scheduled_ts if scheduled_ts > check_now else 0.0

    def _in_llm_timer_pre_silence_window(self, user: dict[str, Any], *, now: float | None = None) -> bool:
        lead = self._llm_timer_pre_silence_seconds()
        if lead <= 0:
            return False
        check_now = _engine_host._now_ts() if now is None else now
        timer_ts = self._upcoming_llm_timer_ts(user, now=check_now)
        return timer_ts > 0 and 0 < timer_ts - check_now <= lead

    def _in_llm_timer_silence_window(self, user: dict[str, Any], *, now: float | None = None) -> bool:
        event = self._get_active_llm_timer(user)
        if not isinstance(event, dict):
            return False
        check_now = _engine_host._now_ts() if now is None else now
        scheduled_ts = _safe_float(event.get("scheduled_ts"), 0)
        if scheduled_ts <= check_now:
            return False
        if bool(event.get("silence_until_due")):
            return True
        return self._in_llm_timer_pre_silence_window(user, now=check_now)

    def _remember_silenced_plan_for_timer(self, user: dict[str, Any], *, now: float | None = None) -> None:
        event = self._get_active_llm_timer(user)
        if not isinstance(event, dict):
            return
        planned_source = self._normalize_legacy_proactive_text(user.get("planned_proactive_source"), limit=40)
        if planned_source == "timer":
            return
        topic = _single_line(user.get("planned_proactive_topic"), 80)
        motive = _single_line(user.get("planned_proactive_motive"), 160)
        reason = self._normalize_legacy_proactive_text(user.get("planned_proactive_reason"), limit=40)
        action = self._normalize_legacy_proactive_text(user.get("planned_proactive_action"), limit=32)
        if not any((topic, motive, reason)):
            return
        existing = event.get("deferred_context")
        if isinstance(existing, dict) and existing:
            return
        event["deferred_context"] = {
            "created_at": now or _engine_host._now_ts(),
            "reason": reason,
            "action": action,
            "topic": topic,
            "motive": self._normalize_internal_motive_text(motive),
            "source": planned_source,
        }

    def _remember_silenced_candidate_for_timer(
        self,
        user: dict[str, Any],
        candidate: dict[str, Any],
        *,
        now: float | None = None,
    ) -> None:
        event = self._get_active_llm_timer(user)
        if not isinstance(event, dict):
            return
        existing = event.get("deferred_context")
        if isinstance(existing, dict) and existing:
            return
        topic = _single_line(candidate.get("topic"), 80)
        motive = _single_line(candidate.get("motive"), 160)
        reason = _single_line(candidate.get("reason"), 40)
        action = _single_line(candidate.get("action"), 32)
        if not any((topic, motive, reason)):
            return
        event["deferred_context"] = {
            "created_at": now or _engine_host._now_ts(),
            "reason": reason,
            "action": action,
            "topic": topic,
            "motive": self._normalize_internal_motive_text(motive),
            "source": _single_line(candidate.get("source"), 40),
        }

    def _promote_due_llm_timer_plan(self, user: dict[str, Any], *, now: float | None = None) -> bool:
        event = self._get_active_llm_timer(user)
        if not isinstance(event, dict):
            return False
        if not self._llm_timer_can_use_internal_scheduler(event):
            return False
        check_now = _engine_host._now_ts() if now is None else now
        scheduled_ts = _safe_float(event.get("scheduled_ts"), 0)
        if scheduled_ts <= 0 or scheduled_ts > check_now:
            return False
        if self._normalize_legacy_proactive_text(user.get("planned_proactive_source"), limit=40) != "timer":
            self._reset_planned_proactive_delivery_state(user)
        user["next_proactive_at"] = scheduled_ts
        user["planned_proactive_reason"] = self._normalize_legacy_proactive_text(event.get("reason"), limit=40) or "check_in"
        user["planned_proactive_action"] = self._normalize_legacy_proactive_text(event.get("action"), limit=24) or "message"
        user["planned_proactive_source"] = "timer"
        user["planned_proactive_motive"] = self._normalize_internal_motive_text(_single_line(event.get("motive"), 140))
        user["planned_proactive_topic"] = _single_line(event.get("topic"), 60)
        user["planned_proactive_impulse_id"] = ""
        user["planned_mobile_location_transition_key"] = ""
        user["planned_mobile_location_event_type"] = ""
        user["planned_proactive_window_start_at"] = scheduled_ts
        user["planned_proactive_window_timezone"] = _engine_proactive_window_timezone(self)
        active_span, grace_span = self._proactive_impulse_default_window_seconds(
            user["planned_proactive_reason"],
            source="timer",
        )
        user["planned_proactive_best_until_at"] = scheduled_ts + active_span
        user["planned_proactive_expire_at"] = scheduled_ts + active_span + grace_span
        semantics = self._planned_proactive_semantics(user)
        user["planned_proactive_semantic_kind"] = _single_line(semantics.get("kind"), 40)
        user["planned_proactive_anchor_type"] = _single_line(semantics.get("anchor_type"), 40)
        user["planned_proactive_semantic_score"] = int(max(0.0, min(1.0, _safe_float(semantics.get("score"), 0.5))) * 100)
        user["planned_proactive_semantic_note"] = _single_line(semantics.get("note"), 180)
        user["planned_proactive_need_layer"] = _single_line(semantics.get("need_layer"), 40)
        user["planned_proactive_need_drive"] = _single_line(semantics.get("need_drive"), 80)
        user["planned_proactive_need_note"] = _single_line(semantics.get("need_note"), 120)
        user["planned_event_chain"] = [] if self._private_user_role(user) == "friend" else (
            list(event.get("chain") or []) if isinstance(event.get("chain"), list) else []
        )
        user["planned_opener_mode"] = ""
        user["planned_followup_kind"] = ""
        user["planned_proactive_quota_exempt"] = False
        self._set_planned_proactive_trigger(
            user,
            message_id=_single_line(event.get("trigger_message_id"), 120),
            umo=_single_line(event.get("trigger_umo"), 160),
            created_at=_safe_float(event.get("trigger_ts"), 0),
        )
        self._store_planned_proactive_route_fields(user, {**event, "source": "timer"})
        return True

    def _commit_mobile_location_arrival_after_send(self, user: dict[str, Any]) -> None:
        """Consume a location transition only after a real delivery has started."""
        transition_key = _single_line(user.get("planned_mobile_location_transition_key"), 80)
        if not transition_key:
            return
        user["last_mobile_location_arrival_key"] = transition_key
        if _single_line(user.get("mobile_location_priority_key"), 80) == transition_key:
            user["mobile_location_priority_key"] = ""
            user["mobile_location_priority_until"] = 0
        user["planned_mobile_location_transition_key"] = ""
        user["planned_mobile_location_event_type"] = ""

    def _promote_upcoming_llm_timer_plan(self, user: dict[str, Any], *, now: float | None = None) -> bool:
        event = self._get_active_llm_timer(user)
        if not isinstance(event, dict):
            return False
        if not self._llm_timer_can_use_internal_scheduler(event):
            return False
        check_now = _engine_host._now_ts() if now is None else now
        scheduled_ts = _safe_float(event.get("scheduled_ts"), 0)
        if scheduled_ts <= check_now:
            return False
        if self._normalize_legacy_proactive_text(user.get("planned_proactive_source"), limit=40) != "timer":
            self._reset_planned_proactive_delivery_state(user)
        user["next_proactive_at"] = scheduled_ts
        user["planned_proactive_reason"] = self._normalize_legacy_proactive_text(event.get("reason"), limit=40) or "check_in"
        user["planned_proactive_action"] = self._normalize_legacy_proactive_text(event.get("action"), limit=24) or "message"
        user["planned_proactive_source"] = "timer"
        user["planned_proactive_motive"] = self._normalize_internal_motive_text(_single_line(event.get("motive"), 140))
        user["planned_proactive_topic"] = _single_line(event.get("topic"), 60)
        user["planned_proactive_impulse_id"] = ""
        user["planned_mobile_location_transition_key"] = ""
        user["planned_mobile_location_event_type"] = ""
        user["planned_proactive_window_start_at"] = scheduled_ts
        user["planned_proactive_window_timezone"] = _engine_proactive_window_timezone(self)
        active_span, grace_span = self._proactive_impulse_default_window_seconds(
            user["planned_proactive_reason"],
            source="timer",
        )
        user["planned_proactive_best_until_at"] = scheduled_ts + active_span
        user["planned_proactive_expire_at"] = scheduled_ts + active_span + grace_span
        semantics = self._planned_proactive_semantics(user)
        user["planned_proactive_semantic_kind"] = _single_line(semantics.get("kind"), 40)
        user["planned_proactive_anchor_type"] = _single_line(semantics.get("anchor_type"), 40)
        user["planned_proactive_semantic_score"] = int(max(0.0, min(1.0, _safe_float(semantics.get("score"), 0.5))) * 100)
        user["planned_proactive_semantic_note"] = _single_line(semantics.get("note"), 180)
        user["planned_proactive_need_layer"] = _single_line(semantics.get("need_layer"), 40)
        user["planned_proactive_need_drive"] = _single_line(semantics.get("need_drive"), 80)
        user["planned_proactive_need_note"] = _single_line(semantics.get("need_note"), 120)
        user["planned_event_chain"] = [] if self._private_user_role(user) == "friend" else (
            list(event.get("chain") or []) if isinstance(event.get("chain"), list) else []
        )
        user["planned_opener_mode"] = ""
        user["planned_followup_kind"] = ""
        user["planned_proactive_quota_exempt"] = False
        self._set_planned_proactive_trigger(
            user,
            message_id=_single_line(event.get("trigger_message_id"), 120),
            umo=_single_line(event.get("trigger_umo"), 160),
            created_at=_safe_float(event.get("trigger_ts"), 0),
        )
        self._store_planned_proactive_route_fields(user, {**event, "source": "timer"})
        return True
