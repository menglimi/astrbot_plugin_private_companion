# -*- coding: utf-8 -*-
"""ProactiveEngineCandidateOfferPlannedStatusMixin。

由 tools/split_mixin_domain.py 从 proactive_engine_candidate.py 机械抽取（8 个方法 + 0 个模块级名字 + 0 个类级赋值 / 586 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveEngineCandidateMixin）。
"""
from __future__ import annotations

from .proactive_engine_candidate_shared import logger
from .proactive_engine_candidate_shared import Any
from .proactive_engine_candidate_shared import _engine_host
from .proactive_engine_candidate_shared import _engine_proactive_window_timezone
from .proactive_engine_candidate_shared import _safe_float
from .proactive_engine_candidate_shared import _safe_int
from .proactive_engine_candidate_shared import _single_line
from .proactive_engine_candidate_shared import re
from .proactive_engine_candidate_shared import runtime_persona_setting



class ProactiveEngineCandidateOfferPlannedStatusMixin:
    """ProactiveEngineCandidateOfferPlannedStatusMixin（从 ProactiveEngineCandidateMixin 拆出）。"""


    def _offer_proactive_candidate(self, user_id: str, user: dict[str, Any], candidate: dict[str, Any]) -> bool:
        user["user_id"] = str(user.get("user_id") or user_id)
        now = _engine_host._now_ts()
        source = _single_line(candidate.get("source"), 40) or "unknown"
        scheduled = _safe_float(candidate.get("scheduled_ts"), now)
        prepared, invalid_window_reason = self._prepare_proactive_candidate_window(
            candidate,
            reason=_single_line(candidate.get("reason"), 40) or "check_in",
            source=source,
            now=now,
        )
        if not isinstance(prepared, dict):
            if invalid_window_reason == "免打扰覆盖整个有效窗口":
                self._remember_weather_proactive_block(
                    user,
                    candidate,
                    now=now,
                    reason=invalid_window_reason,
                )
            logger.info(
                "主动来源在入队前终止: user=%s source=%s reason=%s note=%s",
                _single_line(user_id, 40),
                source,
                _single_line(candidate.get("reason"), 40),
                _single_line(invalid_window_reason, 120),
            )
            return False
        candidate = prepared
        scheduled = _safe_float(candidate.get("scheduled_ts"), now)
        incoming_timeliness = self._proactive_timeliness_level(
            reason=candidate.get("reason"),
            source=source,
        )
        social_relay_note = self._unverified_social_relay_plan_reason(
            candidate,
            source=source,
            has_trigger=bool(self._candidate_trigger_message_id(candidate)),
        )
        if social_relay_note:
            self._record_proactive_candidate(user_id, candidate, status="blocked", note=social_relay_note, user=user)
            return False
        rest_until = self._proactive_rest_block_until(
            user,
            now=now,
            reason=candidate.get("reason"),
            source=source,
        )
        if rest_until > now and scheduled < rest_until:
            self._record_proactive_candidate(user_id, candidate, status="blocked", note="用户明确休息中", user=user)
            return False
        busy_until = 0.0
        busy_block_kind = ""
        busy_context_getter = getattr(self, "_busy_reply_proactive_block_context", None)
        busy_gate = getattr(self, "_busy_reply_proactive_block_until", None)
        if callable(busy_context_getter):
            try:
                busy_context = busy_context_getter(
                    user,
                    now=now,
                    reason=candidate.get("reason"),
                    source=source,
                )
                if isinstance(busy_context, dict):
                    busy_until = _safe_float(busy_context.get("until"), 0.0)
                    busy_block_kind = _single_line(busy_context.get("kind"), 40)
            except Exception:
                busy_until = 0.0
        elif callable(busy_gate):
            try:
                busy_until = _safe_float(
                    busy_gate(
                        user,
                        now=now,
                        reason=candidate.get("reason"),
                        source=source,
                    ),
                    0.0,
                )
            except Exception:
                busy_until = 0.0
        if busy_until > now and scheduled < busy_until and (
            incoming_timeliness == "routine" or busy_block_kind == "external_realtime"
        ):
            expire_at = _safe_float(candidate.get("expire_at"), 0)
            preserve_event_expiry = incoming_timeliness != "routine"
            if preserve_event_expiry and expire_at > 0 and busy_until >= expire_at:
                self._record_proactive_candidate(user_id, candidate, status="blocked", note="实时共处覆盖事件有效期", user=user)
                return False
            shift = busy_until - scheduled
            candidate = dict(candidate)
            shift_keys = (
                ("scheduled_ts", "window_start_at", "preferred_ts", "best_until_at")
                if preserve_event_expiry
                else ("scheduled_ts", "window_start_at", "preferred_ts", "best_until_at", "expire_at")
            )
            for key in shift_keys:
                value = _safe_float(candidate.get(key), 0.0)
                if value > 0:
                    candidate[key] = value + shift
            if preserve_event_expiry:
                candidate["best_until_at"] = min(_safe_float(candidate.get("best_until_at"), 0), expire_at)
            scheduled = _safe_float(candidate.get("scheduled_ts"), busy_until)
        candidate = self._defer_candidate_after_conversation_closing(
            user,
            candidate,
            now=now,
            timeliness=incoming_timeliness,
        )
        scheduled = _safe_float(candidate.get("scheduled_ts"), scheduled)
        if not self._user_enabled_for_proactive(str(user_id), user):
            self._clear_pending_proactive_plan(user)
            return False
        weather_block = self._weather_proactive_block_reason(user, candidate, now=now)
        if weather_block:
            self._record_proactive_candidate(user_id, candidate, status="blocked", note=weather_block, user=user)
            return False
        planned_source = self._normalize_legacy_proactive_text(
            user.get("planned_proactive_source"), limit=40
        ).lower()
        planned_timezone = _single_line(user.get("planned_proactive_window_timezone"), 64)
        candidate_timezone = _single_line(candidate.get("window_timezone"), 64)
        if (
            candidate_timezone
            and planned_timezone
            and planned_timezone != candidate_timezone
            and source in {"weather_alert", "environment_change", "weather_context"}
            and planned_source in {"weather_alert", "environment_change", "weather_context"}
        ):
            # The candidate was rebased to a new effective timezone; discard
            # the old planned timestamp before comparing scheduling priority.
            self._clear_pending_proactive_plan(user)
        silence_reason_getter = getattr(self, "_friend_unanswered_silence_reason", None)
        silence_reason = silence_reason_getter(user, now=now) if callable(silence_reason_getter) else ""
        if silence_reason and source not in {"timer", "troubleshooting", "simulation"}:
            self._record_proactive_candidate(user_id, candidate, status="blocked", note=silence_reason, user=user)
            return False
        if not self._friend_can_receive_proactive_reason(user, candidate.get("reason"), candidate.get("action")):
            return False
        timer_event = self._get_active_llm_timer(user)
        timer_scheduled = _safe_float(timer_event.get("scheduled_ts"), 0) if isinstance(timer_event, dict) else 0.0
        if timer_scheduled > now and scheduled < timer_scheduled and self._in_llm_timer_silence_window(user, now=now):
            self._remember_silenced_candidate_for_timer(user, candidate, now=now)
            self._record_proactive_candidate(user_id, candidate, status="blocked", note="已有聊天临时预约临近", user=user)
            return False
        if _safe_float(user.get("next_proactive_at"), 0) > 0 and self._normalize_legacy_proactive_text(user.get("planned_proactive_source"), limit=40) == "timer":
            current_timer = self._get_active_llm_timer(user)
            if self._llm_timer_can_use_internal_scheduler(current_timer if isinstance(current_timer, dict) else None):
                self._record_proactive_candidate(user_id, candidate, status="blocked", note="已有用户预约/定时主动", user=user)
                return False
            self._clear_llm_timer_internal_plan_fields(user)
        current_next = _safe_float(user.get("next_proactive_at"), 0)
        preempted_for_timeliness = False
        if current_next > 0 and current_next <= scheduled:
            current_timeliness = self._planned_proactive_timeliness_level(user)
            if self._proactive_timeliness_rank(incoming_timeliness) <= self._proactive_timeliness_rank(current_timeliness):
                # 挤占策略：默认直接丢弃（blocked，保持原行为）；
                # 开启 proactive_preempt_queue_enabled 后改为入池排队（deferred），
                # 当前计划发送完成后由 _schedule_next_proactive 自动晋升为下一条。
                if bool(runtime_persona_setting(self, "proactive_preempt_queue_enabled", False)):
                    queued_impulse = self._candidate_to_impulse(user, candidate, source=source, now=now)
                    if isinstance(queued_impulse, dict):
                        queued_impulse["state"] = "deferred"
                        queued_impulse["last_note"] = "已有更早主动候选，挤占入池排队"
                        queued_impulse["updated_ts"] = now
                        # 入池时把窗口整体后移：保证 expire_at 至少到入池时刻 +
                        # 配置小时数（proactive_preempt_queue_expire_hours，默认 2h），
                        # 避免排队等待期间窗口过期被 _cleanup_proactive_impulses 清掉。
                        preempt_expire_hours = _safe_int(
                            runtime_persona_setting(self, "proactive_preempt_queue_expire_hours", 2),
                            2,
                            1,
                            24,
                        )
                        min_expire = now + preempt_expire_hours * 3600
                        shift = max(0.0, min_expire - _safe_float(queued_impulse.get("expire_at"), 0))
                        if shift > 0:
                            for key in ("window_start_at", "preferred_ts", "best_until_at", "expire_at"):
                                value = _safe_float(queued_impulse.get(key), 0)
                                if value > 0:
                                    queued_impulse[key] = value + shift
                        self._queue_proactive_impulse(user, queued_impulse)
                        self._record_proactive_candidate(
                            user_id, candidate, status="deferred", note="已有更早主动候选，已入池排队等待", user=user
                        )
                    else:
                        self._record_proactive_candidate(user_id, candidate, status="blocked", note="已有更早主动候选", user=user)
                else:
                    self._record_proactive_candidate(user_id, candidate, status="blocked", note="已有更早主动候选", user=user)
                return False
            preempted_for_timeliness = True
        action = _single_line(candidate.get("action"), 40) or "message"
        if self._private_user_role(user, str(user_id)) == "friend" and self._action_has_photo_text(action):
            action = self._fallback_action_for_unavailable(action, user)
        if self._private_user_role(user, str(user_id)) == "friend":
            sanitized = self._sanitize_friend_proactive_plan_fields(
                user,
                reason=_single_line(candidate.get("reason"), 40) or "check_in",
                action=action,
                topic=_single_line(candidate.get("topic"), 80),
                motive=_single_line(candidate.get("motive"), 180),
            )
            action = sanitized["action"]
            candidate = dict(candidate)
            candidate["reason"] = sanitized["reason"]
            candidate["topic"] = sanitized["topic"]
            candidate["motive"] = sanitized["motive"]
            if self._friend_proactive_candidate_leaks_owner_environment(user, candidate):
                self._record_proactive_candidate(user_id, candidate, status="blocked", note="次要用户不接收主要用户环境/天气分享", user=user)
                return False
        if not self._action_is_available(action, user):
            self._record_proactive_candidate(user_id, candidate, status="blocked", note="动作不可用或媒体额度不足", user=user)
            return False
        if incoming_timeliness == "routine" and self._proactive_candidate_repeated(user, candidate):
            self._record_proactive_candidate(user_id, candidate, status="blocked", note="近期主题过于相似", user=user)
            return False
        impulse = self._candidate_to_impulse(user, candidate, source=source, now=now)
        if not isinstance(impulse, dict):
            return False
        queued_impulse = self._queue_proactive_impulse(user, impulse)
        if not isinstance(queued_impulse, dict) or not queued_impulse:
            return False
        impulse = queued_impulse
        if preempted_for_timeliness:
            self._mark_planned_candidate_status(user, "deferred", "更高时效主动已优先进入当前发送窗口")
        item = self._record_proactive_candidate(user_id, candidate, status="accepted", note="进入主动计划", user=user)
        self._remember_weather_proactive_accept(user, candidate, now=now)
        self._reset_planned_proactive_delivery_state(user)
        user["next_proactive_at"] = scheduled
        user["planned_proactive_reason"] = self._normalize_legacy_proactive_text(candidate.get("reason"), limit=40) or "check_in"
        user["planned_proactive_action"] = self._normalize_legacy_proactive_text(action, limit=40) or "message"
        user["planned_proactive_source"] = self._normalize_legacy_proactive_text(source, limit=40) or "proactive"
        user["planned_proactive_conversation_posture"] = _single_line(
            impulse.get("conversation_posture") if isinstance(impulse, dict) else candidate.get("conversation_posture"),
            24,
        ).lower()
        user["planned_proactive_conversation_closing_deferred"] = bool(
            candidate.get("conversation_closing_deferred")
        )
        user["planned_proactive_window_timezone"] = _single_line(candidate.get("window_timezone"), 64)
        user["planned_proactive_kind"] = _single_line(impulse.get("kind"), 40) or self._proactive_message_kind(
            reason=candidate.get("reason"),
            source=source,
            semantic_kind=impulse.get("semantic_kind"),
        )
        self._store_planned_proactive_route_fields(user, impulse)
        user["planned_proactive_motive"] = self._normalize_internal_motive_text(
            _single_line(candidate.get("motive"), 180)
        )
        user["planned_proactive_topic"] = _single_line(candidate.get("topic"), 80)
        user["planned_mobile_location_transition_key"] = _single_line(
            candidate.get("_mobile_location_transition_key"), 80
        )
        user["planned_mobile_location_event_type"] = _single_line(
            candidate.get("mobile_location_event_type"), 32
        )
        user["planned_proactive_impulse_id"] = _single_line(impulse.get("id"), 20) if isinstance(impulse, dict) else ""
        user["planned_proactive_window_start_at"] = _safe_float(
            impulse.get("window_start_at"),
            scheduled,
        ) if isinstance(impulse, dict) else scheduled
        user["planned_proactive_window_timezone"] = (
            _single_line(impulse.get("window_timezone"), 64)
            if isinstance(impulse, dict)
            else ""
        ) or _engine_proactive_window_timezone(self)
        user["planned_proactive_best_until_at"] = _safe_float(
            impulse.get("best_until_at"),
            scheduled,
        ) if isinstance(impulse, dict) else scheduled
        user["planned_proactive_expire_at"] = _safe_float(
            impulse.get("expire_at"),
            scheduled,
        ) if isinstance(impulse, dict) else scheduled
        if isinstance(impulse, dict):
            user["planned_proactive_semantic_kind"] = _single_line(impulse.get("semantic_kind"), 40)
            user["planned_proactive_anchor_type"] = _single_line(impulse.get("semantic_anchor_type"), 40)
            user["planned_proactive_semantic_score"] = int(max(0.0, min(1.0, _safe_float(impulse.get("semantic_score"), 0.5))) * 100)
            user["planned_proactive_semantic_note"] = _single_line(impulse.get("semantic_note"), 180)
            user["planned_proactive_need_layer"] = _single_line(impulse.get("semantic_need_layer"), 40)
            user["planned_proactive_need_drive"] = _single_line(impulse.get("semantic_need_drive"), 80)
            user["planned_proactive_need_note"] = _single_line(impulse.get("semantic_need_note"), 120)
        else:
            semantics = self._planned_proactive_semantics(user)
            user["planned_proactive_semantic_kind"] = _single_line(semantics.get("kind"), 40)
            user["planned_proactive_anchor_type"] = _single_line(semantics.get("anchor_type"), 40)
            user["planned_proactive_semantic_score"] = int(max(0.0, min(1.0, _safe_float(semantics.get("score"), 0.5))) * 100)
            user["planned_proactive_semantic_note"] = _single_line(semantics.get("note"), 180)
            user["planned_proactive_need_layer"] = _single_line(semantics.get("need_layer"), 40)
            user["planned_proactive_need_drive"] = _single_line(semantics.get("need_drive"), 80)
            user["planned_proactive_need_note"] = _single_line(semantics.get("need_note"), 120)
        user["planned_event_chain"] = [] if self._private_user_role(user) == "friend" else (
            [dict(step) for step in impulse.get("chain", []) if isinstance(step, dict)]
            if isinstance(impulse, dict)
            else []
        )
        user["planned_opener_mode"] = _single_line(impulse.get("opener_mode"), 24) if isinstance(impulse, dict) else ""
        user["planned_followup_kind"] = _single_line(impulse.get("followup_kind"), 32) if isinstance(impulse, dict) else ""
        self._clear_planned_proactive_trigger(user)
        user["planned_proactive_quota_exempt"] = False
        user["planned_candidate_id"] = item.get("id", "")
        self._set_planned_proactive_trigger(
            user,
            message_id=self._candidate_trigger_message_id(candidate),
            umo=_single_line(candidate.get("trigger_umo") or candidate.get("umo"), 160),
            created_at=_safe_float(candidate.get("trigger_ts") or candidate.get("created_ts"), 0),
        )
        context_key = _single_line(candidate.get("context_key"), 60)
        context = candidate.get("context")
        if context_key and isinstance(context, dict):
            user[context_key] = context
        return True

    def _planned_proactive_signature(self, user: dict[str, Any]) -> str:
        # motive 不参与计划重复判定（模板化动机文本）；source/reason 保留以区分主动类型。
        return self._proactive_topic_signature(
            user.get("planned_proactive_topic"),
            self._normalize_legacy_proactive_text(user.get("planned_proactive_source"), limit=40),
            self._normalize_legacy_proactive_text(user.get("planned_proactive_reason"), limit=40),
        )

    def _planned_proactive_recently_repeated(self, user: dict[str, Any]) -> bool:
        signature = self._planned_proactive_signature(user)
        if not signature:
            return False
        return self._recent_proactive_topic_repeated(user, signature)

    def _unverified_social_relay_plan_reason(
        self,
        item: dict[str, Any],
        *,
        source: str = "",
        has_trigger: bool = False,
    ) -> str:
        if not isinstance(item, dict):
            return ""
        normalized_source = self._normalize_legacy_proactive_text(source or item.get("source") or item.get("planned_proactive_source"), limit=40)
        if normalized_source in {"timer", "troubleshooting", "simulation", "group_share"}:
            return ""
        if has_trigger:
            return ""
        reason = self._normalize_legacy_proactive_text(item.get("reason") or item.get("planned_proactive_reason"), limit=40)
        if reason in {"group_share", "news_share", "bili_video_share", "web_exploration_share"}:
            return ""
        if normalized_source not in {"event", "random", "unknown", ""}:
            return ""
        text = " ".join(
            _single_line(item.get(key), 180)
            for key in (
                "topic",
                "planned_proactive_topic",
                "motive",
                "planned_proactive_motive",
                "why",
                "scene",
                "impulse",
            )
            if _single_line(item.get(key), 180)
        )
        if not text:
            return ""
        relay_markers = ("转达", "转述", "转告", "带话", "捎话")
        if any(token in text for token in relay_markers):
            return "疑似第三方转述/带话内容,缺少真实触发来源"
        invite_markers = ("约", "邀请", "要不要去", "去不去", "一起", "夜宵", "吃饭", "见面", "碰头")
        soft_message_markers = ("留言", "说一声", "说一下", "告诉你一声", "通知你一声")
        third_party_patterns = (
            r"[\u4e00-\u9fffA-Za-z0-9_]{1,12}(?:说|问|发(?:来|了|的)?(?:消息)?|留言|约|邀请)",
            r"(?:他|她|TA|ta)(?:说|问|发(?:来|了|的)?|留言|约|邀请)",
            r"(?:他的|她的|TA的|ta的).{0,8}(?:消息|留言|邀约|邀请)",
        )
        has_third_party_signal = any(re.search(pattern, text) for pattern in third_party_patterns)
        if has_third_party_signal and any(token in text for token in soft_message_markers):
            return "疑似第三方留言/带话内容,缺少真实触发来源"
        if any(token in text for token in invite_markers) and has_third_party_signal:
            return "疑似第三方邀约内容,缺少真实触发来源"
        return ""

    def _planned_proactive_status_snapshot(self, user: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(user, dict):
            return {}
        keys = (
            "planned_candidate_id",
            "planned_proactive_impulse_id",
            "planned_proactive_reason",
            "planned_proactive_action",
            "planned_proactive_source",
            "planned_proactive_conversation_posture",
            "planned_proactive_conversation_closing_deferred",
            "planned_proactive_kind",
            "planned_proactive_motive",
            "planned_proactive_topic",
            "planned_proactive_semantic_kind",
            "planned_proactive_anchor_type",
            "planned_proactive_semantic_score",
            "planned_proactive_semantic_note",
            "planned_proactive_need_layer",
            "planned_proactive_need_drive",
            "planned_proactive_need_note",
            "planned_mobile_location_transition_key",
            "planned_mobile_location_event_type",
        )
        return {key: user.get(key) for key in keys}

    def _mark_planned_candidate_status(
        self,
        user: dict[str, Any],
        status: str,
        note: str = "",
        *,
        planned_snapshot: dict[str, Any] | None = None,
    ) -> None:
        restored_values: dict[str, Any] = {}
        if isinstance(planned_snapshot, dict) and planned_snapshot:
            for key, value in planned_snapshot.items():
                if value in (None, "", {}, []):
                    continue
                restored_values[key] = user.get(key)
                user[key] = value
        try:
            outcome_recorder = getattr(self, "_note_proactive_afterglow_outcome", None)
            if callable(outcome_recorder):
                try:
                    outcome_recorder(user, status=status, note=note)
                except Exception as exc:
                    logger.debug("主动结果余韵记录失败: %s", _single_line(exc, 120))
            candidate_id = str(user.get("planned_candidate_id") or "")
            user_id = str(user.get("user_id") or user.get("id") or "")
            if candidate_id:
                for item in self._cleanup_proactive_candidate_pool():
                    if str(item.get("id") or "") == candidate_id:
                        item["status"] = status
                        item["note"] = _single_line(note, 160)
                        item["updated_ts"] = _engine_host._now_ts()
                        break
            impulse_id = _single_line(user.get("planned_proactive_impulse_id"), 20)
            if not impulse_id:
                return
            for impulse in self._cleanup_proactive_impulses(user):
                if _single_line(impulse.get("id"), 20) != impulse_id:
                    continue
                impulse["updated_ts"] = _engine_host._now_ts()
                impulse["last_status"] = _single_line(status, 24)
                impulse["last_note"] = _single_line(note, 160)
                if status in {"sent"}:
                    impulse["state"] = "sent"
                elif status in {"blocked", "cancelled", "dropped"}:
                    impulse["state"] = "blocked"
                elif status == "deferred":
                    impulse["state"] = "deferred"
                    next_at = _safe_float(user.get("next_proactive_at"), 0)
                    if next_at > 0:
                        impulse["window_start_at"] = next_at
                        impulse["preferred_ts"] = max(_safe_float(impulse.get("preferred_ts"), 0), next_at)
                        if _single_line(impulse.get("source"), 40) == "body_monitor":
                            hard_expire_at = _safe_float(user.get("planned_proactive_expire_at"), 0)
                            impulse["best_until_at"] = min(
                                max(_safe_float(impulse.get("best_until_at"), 0), next_at),
                                hard_expire_at,
                            )
                            impulse["expire_at"] = hard_expire_at
                        else:
                            impulse["best_until_at"] = max(_safe_float(impulse.get("best_until_at"), 0), next_at + 20 * 60)
                            impulse["expire_at"] = max(_safe_float(impulse.get("expire_at"), 0), impulse["best_until_at"] + 40 * 60)
                else:
                    impulse["state"] = "queued"
                break
            is_send_retry_deferred = status == "deferred" and (
                "已保留待重发内容" in str(note or "") or "平台发送" in str(note or "")
            )
            if user_id and status in {"blocked", "cancelled", "dropped", "failed", "deferred"} and not is_send_retry_deferred:
                self._shrink_user_proactive_candidates(user_id, note=note)
        finally:
            for key, value in restored_values.items():
                user[key] = value

    def _maybe_upgrade_planned_message_action(
        self,
        action: str,
        *,
        reason: str,
        user: dict[str, Any],
        motive: str = "",
        planned_event: dict[str, Any] | None = None,
    ) -> str:
        normalized = str(action or "message").strip() or "message"
        if normalized != "message":
            return self._fallback_action_for_unavailable(normalized, user)
        if isinstance(planned_event, dict) and (planned_event.get("_daily_greeting") or planned_event.get("_daily_meal_care")):
            return "message"
        candidates: list[tuple[str, float]] = []
        event_text = ""
        if isinstance(planned_event, dict):
            event_text = " ".join(
                _single_line(planned_event.get(key), 80)
                for key in ("topic", "why", "scene", "motive", "impulse")
            )
        combined_hint = f"{event_text} {motive}"
        if self._screen_glance_available(user) and reason in {"check_in", "quiet_care", "background_schedule"}:
            candidates.append(("screen_peek", 1.15))
        if (
            self._photo_text_available(user)
            and reason in {"activity_share", "diary_share", "background_schedule", "noon_greeting", "evening_greeting"}
            and self._strong_photo_share_intent(event_text, motive, user.get("planned_proactive_topic"))
        ):
            return "photo_text"
        photo_probability = self._proactive_photo_text_trigger_probability(
            reason,
            event_text,
            motive,
            user.get("planned_proactive_topic"),
            user=user,
        )
        if self._photo_text_available(user) and photo_probability > 0 and _engine_host.random.random() < photo_probability:
            return "photo_text"
        if self._photo_text_available(user) and (
            reason in {"activity_share", "diary_share", "background_schedule", "noon_greeting", "evening_greeting"}
            or any(token in combined_hint for token in self._visual_share_tokens())
        ):
            candidates.append(("photo_text", 1.05))
        if self._voice_available(user) and reason in {"quiet_care", "diary_share", "insomnia_night", "evening_greeting"}:
            candidates.append(("voice", 0.82))
        if self._poke_available() and self._effective_user_poke_daily_limit(user) > 0 and self._poke_action_cooldown_remaining(user) <= 0 and reason in {"check_in", "quiet_care", "morning_greeting", "evening_greeting"}:
            candidates.append(("poke", 0.62))
        if not candidates:
            return "message"
        candidates.append(("message", 0.38))
        return self._fallback_action_for_unavailable(self._weighted_choice(candidates), user)

    def _pick_best_planned_event(
        self, user: dict[str, Any], now: float | None = None
    ) -> dict[str, Any] | None:
        now = now or _engine_host._now_ts()
        candidates = []
        for event in (
            self._pick_pending_followup_event(user, now),
            self._pick_meal_care_event(user, now=now),
            self._pick_daily_greeting_event(user, now),
            self._pick_mobile_location_arrival_event(user, now=now),
            self._habit_proactive_event_for_user(user, now=now),
            self._pick_mood_checkin_event(user, now=now),
            self._pick_memory_echo_event(user, now=now),
            self._pick_absence_miss_event(user, now=now),
            self._pick_game_invite_event(user, now=now),
            self._pick_state_need_event(user, now=now),
            self._pick_story_plan_event(now, user=user),
        ):
            if not isinstance(event, dict):
                continue
            if self._unverified_social_relay_plan_reason(
                event,
                source="event",
                has_trigger=bool(_single_line(event.get("trigger_message_id"), 120)),
            ):
                continue
            reason = str(event.get("reason") or "check_in")
            event_ts = self._timestamp_from_story_event(event, reason)
            if self._friend_proactive_scheduled_too_early(user, event_ts):
                continue
            if event_ts > now or (
                event_ts > 0
                and now - event_ts
                <= runtime_persona_setting(self, "max_proactive_plan_lag_minutes", 180) * 60
            ):
                candidates.append((event_ts, event))
        if not candidates:
            return None
        near_sticky = [
            (event_ts, event)
            for event_ts, event in candidates
            if self._is_sticky_greeting_event(event) and 0 < event_ts - now <= 90 * 60
        ]
        if near_sticky:
            near_sticky.sort(key=lambda item: (self._event_priority(item[1]), item[0]))
            return near_sticky[0][1]
        non_sticky = [
            (event_ts, event)
            for event_ts, event in candidates
            if not self._is_sticky_greeting_event(event)
        ]
        if non_sticky:
            non_sticky.sort(key=lambda item: item[0])
            weighted = []
            for index, (_, event) in enumerate(non_sticky[:3]):
                priority_tuple = self._event_priority(event)
                priority_score = float(-priority_tuple[0])
                weighted.append((event, 1.0 + priority_score * 0.05 + max(0.0, 0.35 - index * 0.1)))
            return self._weighted_choice(weighted)
        ranked = sorted(
            candidates,
            key=lambda item: (self._event_priority(item[1]), item[0]),
        )
        top = ranked[:3]
        return _engine_host.random.choice(top)[1]
