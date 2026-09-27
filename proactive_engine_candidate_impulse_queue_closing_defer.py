# -*- coding: utf-8 -*-
"""ProactiveEngineCandidateImpulseQueueClosingDeferMixin。

由 tools/split_mixin_domain.py 从 proactive_engine_candidate.py 机械抽取（10 个方法 + 0 个模块级名字 + 0 个类级赋值 / 514 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveEngineCandidateMixin）。
"""
from __future__ import annotations
from .proactive_engine_candidate_shared import Any
from .proactive_engine_candidate_shared import _engine_host
from .proactive_engine_candidate_shared import _safe_float
from .proactive_engine_candidate_shared import _safe_int
from .proactive_engine_candidate_shared import _single_line
from .proactive_engine_candidate_shared import runtime_persona_setting
from .proactive_engine_candidate_shared import uuid



class ProactiveEngineCandidateImpulseQueueClosingDeferMixin:
    """ProactiveEngineCandidateImpulseQueueClosingDeferMixin（从 ProactiveEngineCandidateMixin 拆出）。"""


    def _proactive_impulse_orchestration_priority(self, impulse: dict[str, Any]) -> int:
        source = _single_line(impulse.get("source"), 40).lower()
        reason = self._normalize_legacy_proactive_text(impulse.get("reason"), limit=40)
        priorities = {
            "timer": 100,
            "weather_alert": 98,
            "body_monitor": 96,
            "memo_note": 94,
            "environment_change": 90,
            "pending_followup": 92,
            "followup": 88,
            "mobile_location": 84,
            "birthday_celebration": 86,
            "special_day_ritual": 89,
            "night_care": 87,
            "daily_greeting": 72,
            "meal_care": 78,
            "balance": 82,
            "birthday_curiosity": 68,
            "habit": 64,
            "state": 60,
            "story": 58,
            "event": 56,
            "creative": 54,
            "random": 20,
        }
        priority = priorities.get(source, 48)
        if reason in {
            "birthday_celebration",
            "birthday_eve_hint",
            "birthday_makeup",
            "important_date_share",
            "special_day_greeting",
            "insomnia_night",
        }:
            priority = max(priority, 86)
        elif reason == "morning_greeting":
            priority = max(priority, 82)
        elif reason in {"noon_greeting", "evening_greeting"}:
            priority = max(priority, 72)
        elif reason == "quiet_care":
            priority = max(priority, 74)
        # 外部分享类（content_share 路线）优先级可配：默认 48 与硬编码一致，
        # 调高后（如 72~78 与饭点关心同档）这类内容在活跃时段更容易被选中发出，
        # 但仍受免打扰/冷却/日上限等闸门约束。
        if reason in {
            "news_share",
            "bili_video_share",
            "web_exploration_share",
            "creative_share",
            "group_share",
            "reading_archive_recommendation_request",
            "game_invite",
        }:
            priority = _safe_int(
                runtime_persona_setting(self, "proactive_share_priority", 48),
                48,
                0,
                100,
            )
        return priority

    def _proactive_impulse_content_signature(self, impulse: dict[str, Any]) -> str:
        # 只按内容（topic）比对；motive 是模板化动机文本，计入会把不同内容误判为相似而合并。
        return self._proactive_topic_signature(
            impulse.get("topic"),
        )

    def _merge_proactive_impulse_timing(self, target: dict[str, Any], incoming: dict[str, Any]) -> None:
        target_start = _safe_float(target.get("window_start_at"), 0)
        incoming_start = _safe_float(incoming.get("window_start_at"), 0)
        target_preferred = _safe_float(target.get("preferred_ts"), 0)
        incoming_preferred = _safe_float(incoming.get("preferred_ts"), 0)
        if target_start <= 0 or (incoming_start > 0 and incoming_start < target_start):
            target["window_start_at"] = incoming_start
        if target_preferred <= 0 or (incoming_preferred > 0 and incoming_preferred < target_preferred):
            target["preferred_ts"] = incoming_preferred
        target["best_until_at"] = max(
            _safe_float(target.get("best_until_at"), 0),
            _safe_float(incoming.get("best_until_at"), 0),
        )
        target["expire_at"] = max(
            _safe_float(target.get("expire_at"), 0),
            _safe_float(incoming.get("expire_at"), 0),
        )

    def _replace_proactive_impulse_with_higher_priority(
        self,
        existing: dict[str, Any],
        incoming: dict[str, Any],
    ) -> dict[str, Any]:
        existing_id = _single_line(existing.get("id"), 20) or uuid.uuid4().hex[:12]
        existing_created = _safe_float(existing.get("created_ts"), _engine_host._now_ts())
        existing_state = _single_line(existing.get("state"), 24) or "queued"
        replacement = dict(incoming)
        replacement["id"] = existing_id
        replacement["created_ts"] = existing_created
        replacement["updated_ts"] = _engine_host._now_ts()
        replacement["state"] = existing_state
        self._merge_proactive_impulse_timing(replacement, existing)
        replacement["signature"] = self._proactive_impulse_signature(replacement)
        replacement["salience"] = max(
            _safe_float(existing.get("salience"), 0.0),
            _safe_float(incoming.get("salience"), 0.0),
        )
        replacement["urgency"] = max(
            _safe_float(existing.get("urgency"), 0.0),
            _safe_float(incoming.get("urgency"), 0.0),
        )
        existing.clear()
        existing.update(replacement)
        return existing

    def _queue_proactive_impulse(
        self,
        user: dict[str, Any],
        impulse: dict[str, Any],
    ) -> dict[str, Any]:
        disabled = getattr(self, "_proactive_generation_disabled", None)
        if callable(disabled) and disabled(user):
            return {}
        check_now = _engine_host._now_ts()
        prepared, invalid_reason = self._prepare_proactive_candidate_window(
            impulse,
            reason=_single_line(impulse.get("reason"), 40) or "check_in",
            source=_single_line(impulse.get("source"), 40) or "random",
            now=check_now,
        )
        if not isinstance(prepared, dict):
            impulse["state"] = "blocked"
            impulse["last_status"] = "blocked"
            impulse["last_note"] = invalid_reason
            impulse["updated_ts"] = check_now
            return {}
        impulse = prepared
        pool = self._cleanup_proactive_impulses(user)
        origin_event_id = _single_line(impulse.get("origin_event_id"), 80)
        if origin_event_id:
            for existing in reversed(pool):
                if _single_line(existing.get("origin_event_id"), 80) != origin_event_id:
                    continue
                existing_state = str(existing.get("state") or "queued")
                # 位置转场可能在发送前被用户活跃、休息或复核闸门拦下；
                # 只有真实发送成功才消耗这次转场，其他来源沿用原有去重契约。
                terminal_states = {"sent"} if _single_line(impulse.get("source"), 40) == "mobile_location" else {
                    "sent", "blocked", "cancelled", "dropped"
                }
                if existing_state in terminal_states:
                    return {}
                if _single_line(impulse.get("source"), 40) == "mobile_location":
                    for key in ("_mobile_location_transition_key", "_mobile_location_priority", "mobile_location_event_type"):
                        if key in impulse:
                            existing[key] = impulse.get(key)
        signature = self._proactive_impulse_signature(impulse)
        reason = _single_line(impulse.get("reason"), 40)
        source = _single_line(impulse.get("source"), 40)
        for existing in reversed(pool):
            if not isinstance(existing, dict):
                continue
            if str(existing.get("state") or "queued") not in {"queued", "deferred"}:
                continue
            if str(existing.get("reason") or "") != reason:
                continue
            if str(existing.get("source") or "") != source:
                continue
            if not self._topic_signature_similar(signature, str(existing.get("signature") or "")):
                continue
            existing_start = _safe_float(existing.get("window_start_at"), 0)
            incoming_start = _safe_float(impulse.get("window_start_at"), 0)
            existing_preferred = _safe_float(existing.get("preferred_ts"), 0)
            incoming_preferred = _safe_float(impulse.get("preferred_ts"), 0)
            closing_deferred = bool(
                existing.get("conversation_closing_deferred") or impulse.get("conversation_closing_deferred")
            )
            existing["updated_ts"] = _engine_host._now_ts()
            if existing_start <= 0:
                existing["window_start_at"] = incoming_start
            elif incoming_start > 0:
                existing["window_start_at"] = (
                    max(existing_start, incoming_start)
                    if closing_deferred
                    else min(existing_start, incoming_start)
                )
            if existing_preferred <= 0:
                existing["preferred_ts"] = incoming_preferred
            elif incoming_preferred > 0:
                existing["preferred_ts"] = (
                    max(existing_preferred, incoming_preferred)
                    if closing_deferred
                    else min(existing_preferred, incoming_preferred)
                )
            if closing_deferred:
                existing["conversation_closing_deferred"] = True
                existing["conversation_closing_deferred_until"] = max(
                    _safe_float(existing.get("conversation_closing_deferred_until"), 0),
                    _safe_float(impulse.get("conversation_closing_deferred_until"), 0),
                )
            existing["best_until_at"] = max(
                _safe_float(existing.get("best_until_at"), 0),
                _safe_float(impulse.get("best_until_at"), 0),
            )
            existing["expire_at"] = max(
                _safe_float(existing.get("expire_at"), 0),
                _safe_float(impulse.get("expire_at"), 0),
            )
            existing["salience"] = max(_safe_float(existing.get("salience"), 0.0), _safe_float(impulse.get("salience"), 0.0))
            existing["warmth"] = max(_safe_float(existing.get("warmth"), 0.0), _safe_float(impulse.get("warmth"), 0.0))
            existing["urgency"] = max(_safe_float(existing.get("urgency"), 0.0), _safe_float(impulse.get("urgency"), 0.0))
            existing_fit = _safe_float(existing.get("persona_fit"), 0.0)
            incoming_fit = _safe_float(impulse.get("persona_fit"), 0.0)
            if incoming_fit > 0:
                existing["persona_fit"] = max(existing_fit, incoming_fit)
            if incoming_fit >= existing_fit:
                existing["persona_fit_blocker"] = bool(impulse.get("persona_fit_blocker"))
            elif impulse.get("persona_fit_blocker"):
                existing["persona_fit_blocker"] = True
            if _single_line(impulse.get("persona_fit_note"), 160):
                existing["persona_fit_note"] = _single_line(impulse.get("persona_fit_note"), 160)
            existing_semantic = _safe_float(existing.get("semantic_score"), 0.0)
            incoming_semantic = _safe_float(impulse.get("semantic_score"), 0.0)
            if incoming_semantic >= existing_semantic:
                for key in (
                    "semantic_kind",
                    "semantic_anchor_type",
                    "semantic_score",
                    "semantic_anchor_score",
                    "semantic_pressure",
                    "semantic_risk",
                    "semantic_note",
                    "semantic_need_layer",
                    "semantic_need_drive",
                    "semantic_need_note",
                    "semantic_need_score_bias",
                    "semantic_need_pressure_bias",
                    "semantic_blocker",
                ):
                    if key in impulse:
                        existing[key] = impulse.get(key)
            elif impulse.get("semantic_blocker"):
                existing["semantic_blocker"] = True
                existing["semantic_risk"] = max(_safe_float(existing.get("semantic_risk"), 0.0), _safe_float(impulse.get("semantic_risk"), 0.0))
            if _single_line(impulse.get("topic"), 80):
                existing["topic"] = _single_line(impulse.get("topic"), 80)
            if _single_line(impulse.get("motive"), 180):
                existing["motive"] = self._normalize_internal_motive_text(_single_line(impulse.get("motive"), 180))
            if impulse.get("chain"):
                existing["chain"] = [dict(item) for item in impulse.get("chain", []) if isinstance(item, dict)]
            if _single_line(impulse.get("trigger_message_id"), 120):
                existing["trigger_message_id"] = _single_line(impulse.get("trigger_message_id"), 120)
            if _single_line(impulse.get("trigger_umo"), 160):
                existing["trigger_umo"] = _single_line(impulse.get("trigger_umo"), 160)
            if _safe_float(impulse.get("trigger_ts"), 0) > 0:
                existing["trigger_ts"] = _safe_float(impulse.get("trigger_ts"), 0)
            if impulse.get("quota_exempt"):
                existing["quota_exempt"] = True
            if _single_line(impulse.get("context_key"), 60) and isinstance(impulse.get("context"), dict):
                existing["context_key"] = _single_line(impulse.get("context_key"), 60)
                existing["context"] = dict(impulse.get("context"))
            return existing
        content_signature = self._proactive_impulse_content_signature(impulse)
        if content_signature:
            incoming_priority = self._proactive_impulse_orchestration_priority(impulse)
            for existing in reversed(pool):
                if not isinstance(existing, dict):
                    continue
                if str(existing.get("state") or "queued") not in {"queued", "deferred"}:
                    continue
                existing_signature = self._proactive_impulse_content_signature(existing)
                if not self._topic_signature_similar(content_signature, existing_signature):
                    continue
                existing_priority = self._proactive_impulse_orchestration_priority(existing)
                if incoming_priority > existing_priority:
                    return self._replace_proactive_impulse_with_higher_priority(existing, impulse)
                if incoming_priority == existing_priority:
                    self._merge_proactive_impulse_timing(existing, impulse)
                existing["updated_ts"] = _engine_host._now_ts()
                existing["salience"] = max(
                    _safe_float(existing.get("salience"), 0.0),
                    _safe_float(impulse.get("salience"), 0.0),
                )
                existing["urgency"] = max(
                    _safe_float(existing.get("urgency"), 0.0),
                    _safe_float(impulse.get("urgency"), 0.0),
                )
                return existing
        item = dict(impulse)
        item["id"] = _single_line(item.get("id"), 20) or uuid.uuid4().hex[:12]
        item["signature"] = signature
        item["state"] = str(item.get("state") or "queued")
        pool.append(item)
        del pool[:-16]
        return item

    def _proactive_conversation_closing_until(
        self,
        user: dict[str, Any],
        *,
        now: float | None = None,
    ) -> float:
        """Return the short-lived conversation closing boundary, if still valid.

        This is deliberately separate from the user's rest gate.  A bot-initiated
        closing is a conversational posture with a TTL; a later user activity
        supersedes it without requiring text matching.
        """
        if not isinstance(user, dict):
            return 0.0
        continuity = user.get("state_continuity")
        marker = continuity.get("conversation_closing") if isinstance(continuity, dict) else None
        if not isinstance(marker, dict):
            departure = (
                continuity.get("conversation_departure")
                if isinstance(continuity, dict) and isinstance(continuity.get("conversation_departure"), dict)
                else user.get("conversation_departure")
            )
            if isinstance(departure, dict):
                marker = {
                    "at": departure.get("at"),
                    "posture": "closing",
                    "source": "confirmed_visible_reply",
                }
        if not isinstance(marker, dict):
            return 0.0
        posture = _single_line(marker.get("posture"), 24).lower()
        if posture and posture != "closing":
            return 0.0
        check_now = _engine_host._now_ts() if now is None else now
        at = _safe_float(marker.get("at"), 0.0)
        if at <= 0:
            return 0.0
        latest_activity = 0.0
        private_activity_getter = getattr(self, "_latest_private_user_activity_ts", None)
        if callable(private_activity_getter):
            try:
                latest_activity = _safe_float(private_activity_getter(user), 0.0)
            except Exception:
                latest_activity = 0.0
        if latest_activity <= 0:
            latest_activity = max(
                _safe_float(user.get("last_private_activity_at"), 0.0),
                _safe_float(user.get("last_user_message_at"), 0.0),
            )
        if latest_activity > at + 0.001:
            self._release_proactive_closing_deferred_plan(user, now=check_now)
            return 0.0
        until = _safe_float(marker.get("until"), 0.0)
        if until <= at:
            grace_minutes = _safe_float(
                runtime_persona_setting(self, "proactive_closing_grace_minutes", 45),
                45.0,
            )
            until = at + max(0.0, min(240.0, grace_minutes)) * 60.0
        return until if until > check_now else 0.0

    def _release_proactive_closing_deferred_plan(
        self,
        user: dict[str, Any],
        *,
        now: float,
    ) -> None:
        """Make a deferred candidate re-evaluable after fresh private activity."""
        if not isinstance(user, dict):
            return
        if bool(user.get("planned_proactive_conversation_closing_deferred")):
            next_at = _safe_float(user.get("next_proactive_at"), 0.0)
            if next_at > now:
                user["next_proactive_at"] = now
            window_start = _safe_float(user.get("planned_proactive_window_start_at"), 0.0)
            if window_start > now:
                user["planned_proactive_window_start_at"] = now
            user["planned_proactive_conversation_closing_deferred"] = False
        impulses = user.get("proactive_impulses")
        if not isinstance(impulses, list):
            return
        for impulse in impulses:
            if not isinstance(impulse, dict) or not impulse.get("conversation_closing_deferred"):
                continue
            for key in ("window_start_at", "preferred_ts"):
                value = _safe_float(impulse.get(key), 0.0)
                if value > now:
                    impulse[key] = now
            impulse["conversation_closing_deferred"] = False
            impulse["conversation_closing_released_at"] = now
            impulse["updated_ts"] = now

    def _defer_candidate_after_conversation_closing(
        self,
        user: dict[str, Any],
        candidate: dict[str, Any],
        *,
        now: float,
        timeliness: str = "routine",
    ) -> dict[str, Any]:
        """Softly move ordinary candidates past a recent bot closing.

        The candidate remains inspectable and keeps its source/trigger.  Explicit
        triggers and time-sensitive routes are allowed through; only a routine
        untriggered candidate is shifted.
        """
        if not isinstance(candidate, dict):
            return candidate
        closing_until = self._proactive_conversation_closing_until(user, now=now)
        if closing_until <= now:
            return candidate
        posture = _single_line(candidate.get("conversation_posture"), 24).lower()
        source = _single_line(candidate.get("source"), 40).lower()
        has_trigger = bool(self._candidate_trigger_message_id(candidate))
        if posture == "closing" or has_trigger or timeliness in {"urgent", "timely"}:
            return candidate
        if source in {"timer", "pending_followup", "followup", "troubleshooting", "simulation"}:
            return candidate
        scheduled = _safe_float(candidate.get("scheduled_ts"), now)
        if scheduled >= closing_until:
            return candidate
        shift = closing_until - scheduled
        shifted = dict(candidate)
        for key in ("scheduled_ts", "window_start_at", "preferred_ts", "best_until_at", "expire_at"):
            value = _safe_float(shifted.get(key), 0.0)
            if value > 0:
                shifted[key] = value + shift
        shifted["conversation_closing_deferred"] = True
        shifted["conversation_closing_deferred_until"] = closing_until
        return shifted

    def _candidate_to_impulse(
        self,
        user: dict[str, Any],
        candidate: dict[str, Any],
        *,
        source: str,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        if not isinstance(candidate, dict):
            return None
        disabled = getattr(self, "_proactive_generation_disabled", None)
        if callable(disabled) and disabled(user):
            return None
        check_now = _engine_host._now_ts() if now is None else now
        candidate = self._prepare_proactive_route_candidate(
            user,
            candidate,
            source=source,
            now=check_now,
        )
        reason = _single_line(candidate.get("reason"), 40) or "check_in"
        action = _single_line(candidate.get("action"), 40) or "message"
        motive = _single_line(candidate.get("motive"), 180)
        topic = _single_line(candidate.get("topic"), 80)
        prepared, _invalid_reason = self._prepare_proactive_candidate_window(
            candidate,
            reason=reason,
            source=source,
            now=check_now,
        )
        if not isinstance(prepared, dict):
            return None
        window_start_at = _safe_float(prepared.get("window_start_at"), 0)
        preferred_ts = _safe_float(prepared.get("preferred_ts"), 0)
        best_until_at = _safe_float(prepared.get("best_until_at"), 0)
        expire_at = _safe_float(prepared.get("expire_at"), 0)
        impulse = self._build_proactive_impulse(
            user,
            reason=reason,
            action=action,
            motive=motive,
            topic=topic,
            source=source,
            window_start_at=window_start_at,
            preferred_ts=preferred_ts,
            best_until_at=best_until_at,
            expire_at=expire_at,
            window_timezone=_single_line(prepared.get("window_timezone"), 64),
            chain=prepared.get("chain") if isinstance(prepared.get("chain"), list) else [],
            trigger_message_id=self._candidate_trigger_message_id(prepared),
            trigger_umo=_single_line(prepared.get("trigger_umo") or prepared.get("umo"), 160),
            trigger_ts=_safe_float(prepared.get("trigger_ts") or prepared.get("created_ts"), 0),
            quota_exempt=bool(prepared.get("_free_screen_peek")),
            context_key=_single_line(prepared.get("context_key"), 60),
            context=prepared.get("context"),
            opener_mode="name_only" if candidate.get("_name_only_opener") else "",
            followup_kind=(
                "suspended_opener"
                if candidate.get("_opener_followup")
                else "chain_followup"
                if candidate.get("_chain_followup")
                else ""
            ),
            origin_event_id=_single_line(prepared.get("origin_event_id"), 80),
            conversation_posture=_single_line(prepared.get("conversation_posture"), 24),
        )
        for key in ("_mobile_location_transition_key", "_mobile_location_priority", "mobile_location_event_type"):
            if key in prepared:
                impulse[key] = prepared.get(key)
        for key in (
            "kind",
            "kind_label",
            "route_version",
            "route_dedupe_key",
            "route_review_profile",
            "route_retry_profile",
            "route_cancel_if_new_inbound",
            "route_recent_chat_policy",
            "route_allow_automatic_followup",
            "route_disable_segmenting",
            "response_expectation",
            "quota_tier",
        ):
            if key in prepared:
                impulse[key] = prepared[key]
        impulse["conversation_closing_deferred"] = bool(prepared.get("conversation_closing_deferred"))
        if prepared.get("conversation_closing_deferred_until"):
            impulse["conversation_closing_deferred_until"] = _safe_float(
                prepared.get("conversation_closing_deferred_until"),
            )
        return impulse

    def _impulse_ready_now(self, impulse: dict[str, Any], *, now: float | None = None) -> bool:
        check_now = _engine_host._now_ts() if now is None else now
        return (
            str(impulse.get("state") or "queued") in {"queued", "deferred"}
            and check_now >= _safe_float(impulse.get("window_start_at"), 0)
            and check_now <= _safe_float(impulse.get("expire_at"), 0)
        )
