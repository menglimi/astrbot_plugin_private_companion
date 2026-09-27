# -*- coding: utf-8 -*-
"""UserMemoryRelationshipBoundaryPart02Mixin。

由 tools/split_mixin_domain.py 从 user_memory_relationship_boundary.py 机械抽取（8 个方法 + 0 个模块级名字 + 0 个类级赋值 / 294 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 UserMemoryRelationshipBoundaryMixin）。
"""
from __future__ import annotations

import asyncio
import hashlib
import math
from .domains.affect.emotion_event_ledger import record_recent_emotion_event
from .helpers import _safe_float, _safe_int, _single_line
from .persona_config import runtime_persona_setting
from .relationship_policy import relationship_stage_for_score
from .user_memory_render_shared import logger
from datetime import datetime
from typing import Any



class UserMemoryRelationshipBoundaryPart02Mixin:
    """UserMemoryRelationshipBoundaryPart02Mixin（从 UserMemoryRelationshipBoundaryMixin 拆出）。"""


    def _record_interaction_emotion_event(
        self,
        user: dict[str, Any],
        intent: dict[str, Any],
        *,
        band: str,
        reason_code: str,
        status: str = "applied",
        expires_at: float = 0.0,
    ) -> dict[str, Any] | None:
        emotion_event = str(intent.get("emotion_event") or "neutral").strip().lower()
        inbound_intent = str(intent.get("intent") or "chat").strip().lower()
        attribution = intent.get("emotion_attribution") if isinstance(intent.get("emotion_attribution"), dict) else {}
        needs_review = bool(
            emotion_event == "neutral"
            and attribution.get("target") == "ambiguous"
            and attribution.get("auto_settle") is False
        )
        event_type = "neutral" if needs_review else emotion_event if emotion_event != "neutral" else inbound_intent
        if event_type not in {
            "neutral", "hurt", "boundary_violation", "apology", "comfort", "praise", "comfort_need", "external_negative",
            "play", "intimacy", "boundary",
        }:
            return None
        user_id = _single_line(user.get("user_id") or user.get("id"), 120)
        session_id = _single_line(user.get("umo"), 220)
        platform = session_id.split(":", 1)[0] if ":" in session_id else ""
        target = _single_line(intent.get("emotion_target"), 24).lower() or "none"
        target_ref = (
            {"kind": "bot", "id": "self", "role": "bot_self"}
            if target == "bot"
            else {
                "kind": "user" if target == "self" else "unknown" if target == "ambiguous" else "other",
                "id": user_id if target == "self" else "",
                "role": target,
            }
        )
        message_fingerprint = hashlib.sha256(
            _single_line(intent.get("text"), 500).encode("utf-8", errors="ignore")
        ).hexdigest()
        event, created = record_recent_emotion_event(
            user,
            {
                "event_id": _single_line((intent.get("_emotion_revision_of") or {}).get("event_id"), 96) if isinstance(intent.get("_emotion_revision_of"), dict) else "",
                "trace_id": _single_line((intent.get("_emotion_revision_of") or {}).get("trace_id"), 96) if isinstance(intent.get("_emotion_revision_of"), dict) else "",
                "revision": _safe_int((intent.get("_emotion_revision_of") or {}).get("revision"), 1, 1) if isinstance(intent.get("_emotion_revision_of"), dict) else 1,
                "producer_plugin": "private_companion",
                "origin_kind": "interaction",
                "platform": platform,
                "bot_id": self._memory_companion_bridge_bot_id(),
                "scope": "private",
                "session_id": session_id,
                "actor_ref": {"kind": "user", "id": user_id, "role": "speaker"},
                "target_ref": target_ref,
                "quoted_target_ref": {
                    "kind": "quoted",
                    "id": "",
                    "role": _single_line(attribution.get("quoted_target"), 40),
                } if attribution.get("quoted_target") not in {None, "", "none"} else {},
                "event_type": event_type,
                "intensity": _safe_int(intent.get("emotion_intensity"), 0, 0, 100),
                "confidence": _safe_float(intent.get("emotion_confidence"), intent.get("confidence") or 0.0, 0.0),
                "source_rule": _single_line(intent.get("emotion_rule") or intent.get("source"), 80),
                "occurred_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "expires_at": datetime.fromtimestamp(expires_at).astimezone().isoformat(timespec="seconds") if expires_at else "",
                "dedupe_key": f"{session_id}|{message_fingerprint}|{event_type}",
                "message_fingerprint": message_fingerprint,
                "applied_interaction": band,
                "correction_of": _single_line((intent.get("_emotion_revision_of") or {}).get("event_id"), 96) if isinstance(intent.get("_emotion_revision_of"), dict) else "",
                "status": "observed" if needs_review else status,
                "reason_codes": [reason_code, _single_line(intent.get("emotion_rule"), 64)],
            },
        )
        if not created:
            return event
        mirror = getattr(self, "_memory_companion_record_emotion_event", None)
        if callable(mirror):
            operation = mirror(event)
            try:
                creator = getattr(self, "_create_lifecycle_background_task", None)
                task = creator(operation, label="emotion_event_mirror") if callable(creator) else asyncio.create_task(operation)
                if task is None:
                    raise RuntimeError("background task unavailable")
            except Exception:
                close = getattr(operation, "close", None)
                if callable(close):
                    close()
        return event

    def _boundary_feedback_tier_deduct_factor(self, user: dict[str, Any]) -> float:
        if not bool(runtime_persona_setting(self, "relationship_boundary_tier_adaptive", True)):
            return 1.0
        stage = relationship_stage_for_score(
            user.get("relationship_score", 0),
            runtime_persona_setting(self, "relationship_stage_policy", None),
        ).get("phase", {})
        return {
            "deeply_distant": 1.0,
            "strongly_distant": 1.0,
            "distant": 0.95,
            "acquaintance": 0.9,
            "familiar": 0.85,
            "close": 0.7,
            "intimate": 0.6,
            "deeply_bonded": 0.5,
        }.get(str(stage.get("key") or "acquaintance"), 1.0)

    def _boundary_feedback_tier_recovery_factor(self, user: dict[str, Any]) -> float:
        if not bool(runtime_persona_setting(self, "relationship_boundary_tier_adaptive", True)):
            return 1.0
        stage = relationship_stage_for_score(
            user.get("relationship_score", 0),
            runtime_persona_setting(self, "relationship_stage_policy", None),
        ).get("phase", {})
        return {
            "deeply_distant": 0.5,
            "strongly_distant": 0.6,
            "distant": 0.7,
            "acquaintance": 0.8,
            "familiar": 0.9,
            "close": 1.0,
            "intimate": 1.25,
            "deeply_bonded": 1.5,
        }.get(str(stage.get("key") or "acquaintance"), 1.0)

    def _refresh_relationship_violation_stage(self, state: dict[str, Any], *, now: float) -> str:
        if not bool(runtime_persona_setting(self, "enable_relationship_boundary_stage", True)):
            state["stage"] = "normal"
            return "normal"
        load = _safe_int(state.get("stage_load"), 0, 0, 120)
        avoid_at = _safe_int(runtime_persona_setting(self, "relationship_boundary_stage_avoid_points", 6), 6, 1, 120)
        forbid_at = _safe_int(runtime_persona_setting(self, "relationship_boundary_stage_forbid_points", 12), 12, avoid_at, 120)
        reflect_at = _safe_int(runtime_persona_setting(self, "relationship_boundary_stage_reflect_points", 20), 20, forbid_at, 120)
        if load >= reflect_at:
            stage = "reflect"
            state["cold_until"] = max(
                _safe_float(state.get("cold_until"), 0),
                now
                + _safe_int(
                    runtime_persona_setting(self, "relationship_boundary_cold_minutes", 180),
                    180,
                    10,
                    1440,
                )
                * 60,
            )
        elif load >= forbid_at:
            stage = "forbid"
        elif load >= avoid_at:
            stage = "avoid"
        else:
            stage = "normal"
        state["stage"] = stage
        return stage

    def _demote_relationship_after_repeated_bottom_line(
        self,
        user: dict[str, Any],
        *,
        event_id: str,
        now: float,
    ) -> int:
        """Demote one configured tier while preserving the unified ledger audit."""
        projection = relationship_stage_for_score(
            user.get("relationship_score", 0),
            runtime_persona_setting(self, "relationship_stage_policy", None),
        )
        stages = projection.get("stages") if isinstance(projection.get("stages"), list) else []
        index = _safe_int(projection.get("stage_index"), 0, 0)
        if not stages or index <= 0:
            return 0
        target = stages[index - 1] if isinstance(stages[index - 1], dict) else {}
        before = _safe_int(user.get("relationship_score"), 0, -1200, 1200)
        after = min(before, _safe_int(target.get("max"), before, -1200, 1200))
        if after >= before:
            return 0
        user["relationship_score"] = after
        ledger = user.setdefault("relationship_ledger", [])
        if not isinstance(ledger, list):
            ledger = []
            user["relationship_ledger"] = ledger
        ledger.append(
            {
                "event_key": f"relationship_bottom_line_demote:{_single_line(event_id, 96) or int(now)}",
                "reason_code": "relationship_bottom_line_demote",
                "delta": after - before,
                "score_before": before,
                "score_after": after,
                "created_at": now,
            }
        )
        if len(ledger) > 200:
            del ledger[:-200]
        return before - after

    def _log_relationship_boundary_event(self, user: dict[str, Any], decision: str, **fields: Any) -> None:
        details = " ".join(
            f"{_single_line(key, 32)}={_single_line(value, 80)}"
            for key, value in fields.items()
            if value not in (None, "")
        )
        logger.info(
            "[BoundaryFeedback] user=%s decision=%s%s",
            _single_line(user.get("user_id"), 80),
            _single_line(decision, 32),
            f" {details}" if details else "",
        )

    def _relationship_violation_state(self, user: dict[str, Any]) -> dict[str, Any]:
        state = user.get("relationship_violation")
        if not isinstance(state, dict):
            state = {}
            user["relationship_violation"] = state
        legacy_recoverable = _safe_int(state.get("unrecovered_points"), 0, 0, 60) if "recoverable_score" not in state else 0
        defaults = {
            "unrecovered_points": 0,
            "recoverable_score": legacy_recoverable,
            "forfeited_recovery_score": 0,
            "stage_load": 0,
            "apology_recovered_points": 0,
            "apology_recovered_kind": "",
            "apology_by_kind": {},
            "apology_speedup_until": 0.0,
            "incident_count": 0,
            "repeat_count": 0,
            "bottom_line_count": 0,
            "last_bottom_line_demoted_count": 0,
            "confession_count": 0,
            "confession_until": 0.0,
            "last_violation_at": 0.0,
            "last_recovery_at": 0.0,
            "cooldown_until": 0.0,
            "level": 0,
            "last_severity": 0,
            "last_kind": "",
            "last_reason": "",
            "stage": "normal",
            "cold_until": 0.0,
            "violations": [],
            "last_event_id": "",
        }
        for key, value in defaults.items():
            if key not in state:
                state[key] = value
        return state

    def _settle_relationship_violation_recovery(self, user: dict[str, Any], *, now: float) -> int:
        state = self._relationship_violation_state(user)
        outstanding = _safe_int(state.get("unrecovered_points"), 0, 0, 60)
        if outstanding <= 0:
            state["unrecovered_points"] = 0
            state["stage_load"] = 0
            state["stage"] = "normal"
            return 0
        last = _safe_float(state.get("last_recovery_at") or state.get("last_violation_at"), now, 0)
        minutes_per_point = _safe_int(
                runtime_persona_setting(self, "relationship_violation_recovery_minutes_per_point", 180),
            180,
            15,
            10080,
        )
        recovery_factor_getter = getattr(self, "_boundary_feedback_tier_recovery_factor", None)
        try:
            recovery_factor = float(recovery_factor_getter(user)) if callable(recovery_factor_getter) else 1.0
        except Exception:
            recovery_factor = 1.0
        effective_seconds = max(60.0, minutes_per_point * 60 / max(0.3, min(2.0, recovery_factor)))
        if _safe_float(state.get("apology_speedup_until"), 0) > now:
            speedup = _safe_float(
                runtime_persona_setting(self, "relationship_boundary_apology_speedup_multiplier", 3.0),
                3.0,
                1.0,
                10.0,
            )
            effective_seconds = max(60.0, effective_seconds / speedup)
        recovered = min(outstanding, max(0, int(max(0.0, now - last) // effective_seconds)))
        if recovered:
            state["unrecovered_points"] = outstanding - recovered
            prior_stage_load = _safe_int(state.get("stage_load"), outstanding, 0, 120)
            stage_reduction = min(prior_stage_load, max(recovered, int(math.ceil(prior_stage_load * recovered / outstanding))))
            state["stage_load"] = max(0, prior_stage_load - stage_reduction)
            state["last_recovery_at"] = min(now, last + recovered * effective_seconds)
            recoverable_score = _safe_int(state.get("recoverable_score"), 0, 0, 60)
            score_restore = min(recovered, recoverable_score)
            if score_restore:
                result = self._apply_relationship_event(
                    user,
                    score_restore,
                    reason_code="relationship_violation_recovery",
                    event_id=f"boundary-natural-recovery:{int(state['last_recovery_at'])}",
                    now=now,
                )
                applied = _safe_int(result.get("delta"), 0, 0, score_restore)
                state["recoverable_score"] = max(0, recoverable_score - applied)
            state["level"] = max(0, _safe_int(state.get("level"), 0, 0, 6) - (1 if state["unrecovered_points"] == 0 else 0))
            stage_refresher = getattr(self, "_refresh_relationship_violation_stage", None)
            if callable(stage_refresher):
                stage_refresher(state, now=now)
            if state["unrecovered_points"] <= 0:
                state["apology_speedup_until"] = 0.0
        return recovered
