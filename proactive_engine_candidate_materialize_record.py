# -*- coding: utf-8 -*-
"""ProactiveEngineCandidateMaterializeRecordMixin。

由 tools/split_mixin_domain.py 从 proactive_engine_candidate.py 机械抽取（5 个方法 + 0 个模块级名字 + 0 个类级赋值 / 450 行）。
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
from .proactive_engine_candidate_shared import _today_key
from .proactive_engine_candidate_shared import uuid



class ProactiveEngineCandidateMaterializeRecordMixin:
    """ProactiveEngineCandidateMaterializeRecordMixin（从 ProactiveEngineCandidateMixin 拆出）。"""


    def _remember_proactive_hesitation(
        self,
        user: dict[str, Any],
        impulse: dict[str, Any],
        *,
        note: str = "",
        now: float | None = None,
    ) -> None:
        check_now = _engine_host._now_ts() if now is None else now
        raw = user.setdefault("recent_proactive_hesitations", [])
        if not isinstance(raw, list):
            raw = []
            user["recent_proactive_hesitations"] = raw
        item = {
            "ts": check_now,
            "reason": _single_line(impulse.get("reason"), 40),
            "source": _single_line(impulse.get("source"), 40),
            "topic": _single_line(impulse.get("topic"), 80),
            "motive": _single_line(impulse.get("motive"), 140),
            "note": _single_line(note, 140),
            "count": _safe_int(impulse.get("hesitation_count"), 1, 1, 20),
        }
        raw.append(item)
        del raw[:-8]
        user["last_proactive_hesitation_at"] = check_now
        user["last_proactive_hesitation_note"] = item["note"]

    def _motive_with_hesitation_memory(self, impulse: dict[str, Any], motive: str) -> str:
        count = _safe_int(impulse.get("hesitation_count"), 0, 0, 8)
        cleaned = self._normalize_internal_motive_text(motive)
        if count <= 0:
            return cleaned
        source = str(impulse.get("source") or "")
        if source in {"timer", "troubleshooting", "simulation"}:
            return cleaned
        topic = _single_line(impulse.get("topic"), 40)
        if cleaned:
            return cleaned
        if topic:
            return self._normalize_internal_motive_text(f"想到“{topic}”，想短短提一句")
        return ""

    def _materialize_best_proactive_impulse(
        self,
        user: dict[str, Any],
        *,
        now: float | None = None,
    ) -> bool:
        check_now = _engine_host._now_ts() if now is None else now
        user_id = str(user.get("user_id") or user.get("id") or "")
        active = [
            item
            for item in self._cleanup_proactive_impulses(user, now=check_now)
            if isinstance(item, dict) and str(item.get("state") or "queued") in {"queued", "deferred"}
        ]
        if not active:
            return False
        ready = [item for item in active if self._impulse_ready_now(item, now=check_now)]
        selected: dict[str, Any] | None = None
        review_at = 0.0
        if ready:
            ready.sort(
                key=lambda item: (
                    self._score_proactive_impulse(user, item, now=check_now)
                    + self._proactive_impulse_orchestration_priority(item) / 300.0,
                    self._proactive_impulse_orchestration_priority(item),
                ),
                reverse=True,
            )
            selected = ready[0]
            review_at = check_now
        else:
            future = sorted(
                [
                    item for item in active
                    if _safe_float(item.get("expire_at"), 0) > check_now
                    and _safe_float(item.get("window_start_at"), 0) > check_now
                ],
                key=lambda item: (
                    _safe_float(item.get("window_start_at"), check_now + 365 * 24 * 3600),
                    -self._score_proactive_impulse(user, item, now=check_now),
                ),
            )
            if not future:
                return False
            earliest_start = _safe_float(future[0].get("window_start_at"), check_now)
            near_term = [
                item
                for item in future
                if _safe_float(item.get("window_start_at"), earliest_start) <= earliest_start + 30 * 60
            ]
            selected = max(
                near_term,
                key=lambda item: (
                    self._proactive_impulse_orchestration_priority(item),
                    self._score_proactive_impulse(user, item, now=check_now),
                    -_safe_float(item.get("window_start_at"), earliest_start),
                ),
            )
            review_at = _safe_float(selected.get("window_start_at"), check_now)
        last_materialized_at = _safe_float(selected.get("last_materialized_at"), 0)
        materialized_count = _safe_int(selected.get("materialized_count"), 0, 0)
        if materialized_count >= 3 and check_now - last_materialized_at <= 15 * 60:
            selected["state"] = "blocked"
            selected["last_status"] = "blocked"
            selected["last_note"] = "同一来源短时间重复物化已熔断"
            selected["updated_ts"] = check_now
            logger.warning(
                "主动念头重复物化熔断: user=%s origin=%s count=%s",
                _single_line(user_id, 40),
                _single_line(selected.get("origin_event_id"), 80) or _single_line(selected.get("id"), 20),
                materialized_count,
            )
            return self._materialize_best_proactive_impulse(user, now=check_now)
        candidate = {
            "source": self._normalize_legacy_proactive_text(selected.get("source"), limit=40) or "impulse",
            "kind": _single_line(selected.get("kind"), 40) or self._proactive_message_kind(
                reason=selected.get("reason"),
                source=selected.get("source"),
                semantic_kind=selected.get("semantic_kind"),
            ),
            "quota_tier": _safe_int(self._proactive_quota_policy(user).get("tier"), 0, 0, 5),
            "reason": self._normalize_legacy_proactive_text(selected.get("reason"), limit=40) or "check_in",
            "action": self._normalize_legacy_proactive_text(selected.get("action"), limit=40) or "message",
            "scheduled_ts": max(review_at, _safe_float(selected.get("window_start_at"), review_at)),
            "topic": _single_line(selected.get("topic"), 80),
            "motive": self._motive_with_hesitation_memory(selected, _single_line(selected.get("motive"), 180)),
            "conversation_posture": _single_line(selected.get("conversation_posture"), 24).lower(),
            "conversation_closing_deferred": bool(selected.get("conversation_closing_deferred")),
            "score": int(max(0.0, min(1.0, self._score_proactive_impulse(user, selected, now=check_now))) * 100),
            "context_key": _single_line(selected.get("context_key"), 60),
            "context": selected.get("context"),
            "chain": selected.get("chain") if isinstance(selected.get("chain"), list) else [],
            "origin_event_id": _single_line(selected.get("origin_event_id"), 80),
            "window_start_at": _safe_float(selected.get("window_start_at"), 0),
            "preferred_ts": _safe_float(selected.get("preferred_ts"), 0),
            "best_until_at": _safe_float(selected.get("best_until_at"), 0),
            "expire_at": _safe_float(selected.get("expire_at"), 0),
            "window_timezone": _single_line(selected.get("window_timezone"), 64)
            or _engine_proactive_window_timezone(self),
        }
        for key in ("_mobile_location_transition_key", "_mobile_location_priority", "mobile_location_event_type"):
            if key in selected:
                candidate[key] = selected.get(key)
        item = self._record_proactive_candidate(
            user_id,
            candidate,
            status="accepted",
            note="由潜在念头池物化为当前主动计划",
            user=user,
        )
        self._reset_planned_proactive_delivery_state(user)
        user["next_proactive_at"] = candidate["scheduled_ts"]
        user["planned_proactive_reason"] = self._normalize_legacy_proactive_text(candidate["reason"], limit=40) or "check_in"
        user["planned_proactive_action"] = self._normalize_legacy_proactive_text(candidate["action"], limit=40) or "message"
        user["planned_proactive_source"] = self._normalize_legacy_proactive_text(candidate["source"], limit=40) or "impulse"
        user["planned_proactive_conversation_posture"] = _single_line(
            candidate.get("conversation_posture"),
            24,
        ).lower()
        user["planned_proactive_conversation_closing_deferred"] = bool(
            candidate.get("conversation_closing_deferred")
        )
        user["planned_proactive_kind"] = _single_line(candidate.get("kind"), 40)
        self._store_planned_proactive_route_fields(user, selected)
        user["planned_proactive_motive"] = self._normalize_internal_motive_text(candidate["motive"])
        user["planned_proactive_topic"] = candidate["topic"]
        if user["planned_proactive_reason"] == "birthday_curiosity":
            user["birthday_curiosity_asked_at"] = check_now
        user["planned_proactive_impulse_id"] = _single_line(selected.get("id"), 20)
        user["planned_mobile_location_transition_key"] = _single_line(
            selected.get("_mobile_location_transition_key"), 80
        )
        user["planned_mobile_location_event_type"] = _single_line(
            selected.get("mobile_location_event_type"), 32
        )
        user["planned_proactive_window_start_at"] = _safe_float(selected.get("window_start_at"), 0)
        user["planned_proactive_window_timezone"] = _single_line(
            selected.get("window_timezone"),
            64,
        ) or _engine_proactive_window_timezone(self)
        user["planned_proactive_best_until_at"] = _safe_float(selected.get("best_until_at"), 0)
        user["planned_proactive_expire_at"] = _safe_float(selected.get("expire_at"), 0)
        user["planned_proactive_semantic_kind"] = _single_line(selected.get("semantic_kind"), 40)
        user["planned_proactive_anchor_type"] = _single_line(selected.get("semantic_anchor_type"), 40)
        user["planned_proactive_semantic_score"] = int(max(0.0, min(1.0, _safe_float(selected.get("semantic_score"), 0.5))) * 100)
        user["planned_proactive_semantic_note"] = _single_line(selected.get("semantic_note"), 180)
        user["planned_proactive_need_layer"] = _single_line(selected.get("semantic_need_layer"), 40)
        user["planned_proactive_need_drive"] = _single_line(selected.get("semantic_need_drive"), 80)
        user["planned_proactive_need_note"] = _single_line(selected.get("semantic_need_note"), 120)
        user["planned_candidate_id"] = item.get("id", "")
        user["planned_event_chain"] = (
            []
            if self._private_user_role(user) == "friend"
            else [dict(step) for step in selected.get("chain", []) if isinstance(step, dict)]
        )
        user["planned_opener_mode"] = _single_line(selected.get("opener_mode"), 24)
        user["planned_followup_kind"] = _single_line(selected.get("followup_kind"), 32)
        user["planned_proactive_quota_exempt"] = bool(selected.get("quota_exempt"))
        self._set_planned_proactive_trigger(
            user,
            message_id=_single_line(selected.get("trigger_message_id"), 120),
            umo=_single_line(selected.get("trigger_umo"), 160),
            created_at=_safe_float(selected.get("trigger_ts"), 0),
        )
        context_key = _single_line(selected.get("context_key"), 60)
        context = selected.get("context")
        if context_key and isinstance(context, dict):
            user[context_key] = dict(context)
        selected["updated_ts"] = check_now
        selected["state"] = "queued"
        materialized_at = _safe_float(selected.get("last_materialized_at"), 0)
        materialized_count = _safe_int(selected.get("materialized_count"), 0, 0)
        selected["materialized_count"] = materialized_count + 1 if check_now - materialized_at <= 15 * 60 else 1
        selected["last_materialized_at"] = check_now
        return True

    def _record_proactive_candidate(
        self,
        user_id: str,
        candidate: dict[str, Any],
        *,
        status: str,
        note: str = "",
        user: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        disabled = getattr(self, "_proactive_generation_disabled", None)
        target_user = user
        if not isinstance(target_user, dict):
            users = self.data.get("users") if isinstance(self.data.get("users"), dict) else {}
            target_user = users.get(str(user_id)) if isinstance(users.get(str(user_id)), dict) else None
        if callable(disabled) and disabled(target_user):
            return {}
        now = _engine_host._now_ts()
        source_hint = _single_line(candidate.get("source"), 40) or "unknown"
        if isinstance(target_user, dict):
            candidate = self._prepare_proactive_route_candidate(
                target_user,
                candidate,
                source=source_hint,
                now=now,
            )
        topic = _single_line(candidate.get("topic"), 80)
        motive = _single_line(candidate.get("motive"), 160)
        action = _single_line(candidate.get("action"), 40) or "message"
        source = _single_line(candidate.get("source"), 40) or "unknown"
        reason = _single_line(candidate.get("reason"), 40) or "check_in"
        scheduled = _safe_float(candidate.get("scheduled_ts"), now)
        origin_event_id = _single_line(candidate.get("origin_event_id"), 80)
        signature = self._proactive_topic_signature(topic, motive)
        semantics: dict[str, Any] = {}
        if isinstance(user, dict):
            semantics = self._proactive_candidate_semantics(
                user,
                reason=reason,
                action=action,
                motive=motive,
                topic=topic,
                source=source,
                context=candidate.get("context"),
                chain=candidate.get("chain") if isinstance(candidate.get("chain"), list) else [],
                trigger_message_id=self._candidate_trigger_message_id(candidate),
                trigger_ts=_safe_float(candidate.get("trigger_ts") or candidate.get("created_ts"), 0),
            )
        semantic_fields = {
            "semantic_kind": _single_line(semantics.get("kind"), 40),
            "semantic_anchor_type": _single_line(semantics.get("anchor_type"), 40),
            "semantic_score": int(max(0.0, min(1.0, _safe_float(semantics.get("score"), 0.0))) * 100) if semantics else 0,
            "semantic_pressure": int(max(0.0, min(1.0, _safe_float(semantics.get("pressure"), 0.0))) * 100) if semantics else 0,
            "semantic_risk": int(max(0.0, min(1.0, _safe_float(semantics.get("risk"), 0.0))) * 100) if semantics else 0,
            "semantic_note": _single_line(semantics.get("note"), 180),
            "semantic_need_layer": _single_line(semantics.get("need_layer"), 40),
            "semantic_need_drive": _single_line(semantics.get("need_drive"), 80),
            "semantic_need_note": _single_line(semantics.get("need_note"), 120),
            "semantic_need_score_bias": _safe_float(semantics.get("need_score_bias"), 0.0),
            "semantic_need_pressure_bias": _safe_float(semantics.get("need_pressure_bias"), 0.0),
        }
        proactive_kind = _single_line(candidate.get("kind"), 40) or self._proactive_message_kind(
            reason=reason,
            source=source,
            semantic_kind=semantic_fields.get("semantic_kind"),
        )
        quota_policy = self._proactive_quota_policy(target_user if isinstance(target_user, dict) else {})
        pool = self._cleanup_proactive_candidate_pool(now=now)
        if status in {"blocked", "accepted"}:
            for existing in reversed(pool):
                if not isinstance(existing, dict):
                    continue
                if str(existing.get("status") or "") != status:
                    continue
                if str(existing.get("user_id") or "") != str(user_id):
                    continue
                if status == "accepted" and str(existing.get("id") or "") == str(candidate.get("id") or ""):
                    continue
                same_origin = bool(
                    origin_event_id
                    and origin_event_id == _single_line(existing.get("origin_event_id"), 80)
                )
                if not same_origin and not self._topic_signature_similar(signature, str(existing.get("signature") or "")):
                    continue
                existing_short_lived = self._proactive_candidate_is_short_lived(existing)
                incoming_short_lived = reason in {"weather_alert", "environment_change"} or source in {
                    "weather_alert",
                    "environment_change",
                }
                merge_horizon = 2 * 3600 if existing_short_lived or incoming_short_lived else 18 * 3600
                if now - _safe_float(existing.get("last_seen_ts") or existing.get("created_ts"), 0) > merge_horizon:
                    continue
                existing_expire_at = _safe_float(existing.get("expire_at"), 0)
                if (existing_short_lived or incoming_short_lived) and existing_expire_at > 0 and now > existing_expire_at + 2 * 3600:
                    continue
                repeat_limit = self._candidate_repeat_count_limit(status)
                previous_repeat = _safe_int(existing.get("repeat_count"), 1, 1)
                existing["repeat_count"] = min(repeat_limit, previous_repeat + 1)
                existing["merged_trigger_count"] = _safe_int(
                    existing.get("merged_trigger_count"),
                    max(0, previous_repeat - 1),
                    0,
                ) + 1
                merged_by_day = existing.get("merged_by_day")
                if not isinstance(merged_by_day, dict):
                    merged_by_day = {}
                    existing["merged_by_day"] = merged_by_day
                today_key = _today_key()
                merged_by_day[today_key] = _safe_int(merged_by_day.get(today_key), 0, 0) + 1
                if len(merged_by_day) > 8:
                    existing["merged_by_day"] = {
                        key: merged_by_day[key]
                        for key in sorted(merged_by_day)[-8:]
                    }
                if previous_repeat + 1 > repeat_limit:
                    existing["repeat_count_capped"] = True
                existing["last_seen_ts"] = now
                existing["updated_ts"] = now
                existing["scheduled_ts"] = max(_safe_float(existing.get("scheduled_ts"), scheduled), scheduled)
                if origin_event_id:
                    existing["origin_event_id"] = origin_event_id
                for key in ("window_start_at", "preferred_ts", "best_until_at", "expire_at"):
                    incoming_value = _safe_float(candidate.get(key), 0)
                    if incoming_value > 0:
                        existing[key] = incoming_value
                existing["source"] = source or _single_line(existing.get("source"), 40)
                existing["kind"] = proactive_kind
                existing["quota_tier"] = _safe_int(quota_policy.get("tier"), 0, 0, 5)
                for route_key in (
                    "route_version",
                    "route_dedupe_key",
                    "route_review_profile",
                    "route_retry_profile",
                    "route_cancel_if_new_inbound",
                    "route_recent_chat_policy",
                    "route_allow_automatic_followup",
                    "route_disable_segmenting",
                    "response_expectation",
                ):
                    if route_key in candidate:
                        existing[route_key] = candidate[route_key]
                existing["reason"] = reason or _single_line(existing.get("reason"), 40)
                existing["action"] = action or _single_line(existing.get("action"), 40)
                existing["topic"] = topic or _single_line(existing.get("topic"), 80)
                existing["motive"] = motive or _single_line(existing.get("motive"), 160)
                existing_posture = _single_line(candidate.get("conversation_posture"), 24).lower()
                if existing_posture in {"closing", "open", "neutral"}:
                    existing["conversation_posture"] = existing_posture
                if note:
                    existing["note"] = _single_line(note, 160)
                existing["score"] = max(_safe_int(existing.get("score"), 0, 0, 100), _safe_int(candidate.get("score"), 0, 0, 100))
                if semantics:
                    existing.update(semantic_fields)
                return existing
        item = {
            "id": uuid.uuid4().hex[:12],
            "created_ts": now,
            "last_seen_ts": now,
            "scheduled_ts": scheduled,
            "window_start_at": _safe_float(candidate.get("window_start_at"), 0),
            "preferred_ts": _safe_float(candidate.get("preferred_ts"), 0),
            "best_until_at": _safe_float(candidate.get("best_until_at"), 0),
            "expire_at": _safe_float(candidate.get("expire_at"), 0),
            "origin_event_id": origin_event_id,
            "user_id": str(user_id),
            "source": source,
            "kind": proactive_kind,
            "kind_label": _single_line(self._proactive_kind_policy(proactive_kind).get("label"), 40),
            "quota_tier": _safe_int(quota_policy.get("tier"), 0, 0, 5),
            "route_version": _safe_int(candidate.get("route_version"), 0, 0),
            "route_dedupe_key": _single_line(candidate.get("route_dedupe_key"), 180),
            "route_review_profile": _single_line(candidate.get("route_review_profile"), 40),
            "route_retry_profile": _single_line(candidate.get("route_retry_profile"), 40),
            "route_cancel_if_new_inbound": bool(candidate.get("route_cancel_if_new_inbound", True)),
            "route_recent_chat_policy": _single_line(candidate.get("route_recent_chat_policy"), 40),
            "route_allow_automatic_followup": bool(candidate.get("route_allow_automatic_followup", True)),
            "route_disable_segmenting": bool(candidate.get("route_disable_segmenting", False)),
            "response_expectation": _single_line(candidate.get("response_expectation"), 24),
            "reason": reason,
            "action": action,
            "topic": topic,
            "motive": motive,
            "conversation_posture": (
                _single_line(candidate.get("conversation_posture"), 24).lower()
                if _single_line(candidate.get("conversation_posture"), 24).lower() in {"closing", "open", "neutral"}
                else ""
            ),
            "score": _safe_int(candidate.get("score"), 0, 0, 100),
            "signature": signature,
            "status": status,
            "note": _single_line(note, 160),
            "repeat_count": 1,
            "merged_trigger_count": 0,
            "merged_by_day": {},
            **(semantic_fields if semantics else {}),
        }
        pool.append(item)
        self._cleanup_proactive_candidate_pool(now=now)
        return item

    def _proactive_candidate_repeated(self, user: dict[str, Any], candidate: dict[str, Any]) -> bool:
        candidate_kind = _single_line(candidate.get("kind"), 40) or self._proactive_message_kind(
            reason=candidate.get("reason"),
            source=candidate.get("source"),
            semantic_kind=candidate.get("semantic_kind"),
        )
        # Deterministic event routes own their lifecycle and evidence identity;
        # generic topic similarity must not suppress a new reminder or alert.
        if candidate_kind in {"transactional", "safety_event"}:
            return False
        # 只按内容（topic）判定重复；外部分享类的 motive 是统一模板，计入签名
        # 会让不同内容被判"主题过于相似"而误杀。
        signature = self._proactive_topic_signature(
            candidate.get("topic"),
        )
        if not signature:
            return False
        if self._recent_proactive_topic_repeated(user, signature):
            return True
        now = _engine_host._now_ts()
        user_id = str(user.get("user_id") or user.get("id") or "")
        for item in self._cleanup_proactive_candidate_pool(now=now):
            if str(item.get("user_id") or "") != user_id:
                continue
            if str(item.get("status") or "") not in {"accepted", "sent"}:
                continue
            item_kind = _single_line(item.get("kind"), 40) or self._proactive_message_kind(
                reason=item.get("reason"),
                source=item.get("source"),
                semantic_kind=item.get("semantic_kind"),
            )
            if item_kind != candidate_kind:
                continue
            if now - _safe_float(item.get("created_ts"), 0) > 8 * 3600:
                continue
            if self._topic_signature_similar(signature, str(item.get("signature") or "")):
                return True
        return False
