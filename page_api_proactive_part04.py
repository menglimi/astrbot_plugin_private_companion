# -*- coding: utf-8 -*-
"""PrivateCompanionPageApiProactivePart04Mixin。

由 tools/split_mixin_domain.py 从 page_api_proactive.py 机械抽取（1 个方法 + 0 个模块级名字 + 0 个类级赋值 / 554 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApiProactiveMixin）。
"""
from __future__ import annotations
from .page_api_proactive_shared import Any
from .page_api_proactive_shared import _path_text
from .page_api_proactive_shared import time



class PrivateCompanionPageApiProactivePart04Mixin:
    """PrivateCompanionPageApiProactivePart04Mixin（从 PrivateCompanionPageApiProactiveMixin 拆出）。"""


    def _proactive_task_summary(self, data: dict[str, Any]) -> dict[str, Any]:
        users = data.get("users") if isinstance(data.get("users"), dict) else {}
        now = time.time()
        items: list[dict[str, Any]] = []
        source_counts: dict[str, int] = {}
        status_counts: dict[str, int] = {}
        audit_items: list[dict[str, Any]] = []
        audit_status_counts: dict[str, int] = {}
        user_states: list[dict[str, Any]] = []

        def should_show_user_state(
            user_id: str,
            user: dict[str, Any],
            summary: dict[str, Any],
            *,
            next_ts: float,
            effective_limit: Any,
        ) -> bool:
            """Keep empty transient sessions out of the proactive dashboard."""
            display_name = self._single_line(summary.get("display_name"), 80)
            if not display_name.startswith("临时会话"):
                return True
            proactive_gate = getattr(self.plugin, "_user_enabled_for_proactive", None)
            if callable(proactive_gate):
                try:
                    if self._int(effective_limit) > 0 and proactive_gate(user_id, user):
                        return True
                except Exception:
                    pass
            timer_event = user.get("llm_timer_event") if isinstance(user.get("llm_timer_event"), dict) else {}
            timer_ts = self._float(timer_event.get("scheduled_ts"))
            if next_ts > now or timer_ts > now:
                return True
            recent_cutoff = now - 7 * 24 * 3600
            # Group traffic can refresh a temporary identity. Only private
            # activity should keep it visible in the proactive dashboard.
            recent_private_activity = max(
                self._float(user.get("last_private_seen")),
                self._float(user.get("last_private_activity_at")),
                self._float(user.get("last_private_reply_at")),
            )
            if recent_private_activity >= recent_cutoff:
                return True
            recent_proactive_activity = max(
                self._float(user.get("last_proactive_sent_at")),
                self._float(user.get("last_proactive_skip_at")),
                self._float(user.get("last_proactive_message_at")),
            )
            if recent_proactive_activity >= recent_cutoff:
                return True
            afterglow = user.get("proactive_afterglow")
            return isinstance(afterglow, dict) and self._float(afterglow.get("ts")) >= recent_cutoff

        def readiness_snapshot(user: dict[str, Any]) -> dict[str, Any]:
            getter = getattr(self.plugin, "_proactive_inner_readiness", None)
            if not callable(getter):
                return {}
            try:
                raw = getter(user, now=now)
            except Exception:
                return {}
            if not isinstance(raw, dict):
                return {}
            drive = raw.get("drive") if isinstance(raw.get("drive"), dict) else {}
            temperature = raw.get("temperature") if isinstance(raw.get("temperature"), dict) else {}
            return {
                "score": round(self._float(raw.get("score")), 3),
                "label": self._single_line(raw.get("label"), 80),
                "detail": self._single_line(raw.get("detail"), 220),
                "drive_score": round(self._float(drive.get("score")), 3) if drive else 0,
                "drive_label": self._single_line(drive.get("label"), 40) if drive else "",
                "drive_detail": self._single_line(drive.get("detail"), 140) if drive else "",
                "temperature_score": round(self._float(temperature.get("score")), 3) if temperature else 0,
                "temperature_label": self._single_line(temperature.get("label"), 40) if temperature else "",
                "temperature_detail": self._single_line(temperature.get("detail"), 140) if temperature else "",
                "motivation": self._proactive_motivation_runtime_summary(raw.get("motivation")),
            }

        def planned_semantic_snapshot(user: dict[str, Any]) -> dict[str, Any]:
            semantic_kind = self._single_line(user.get("planned_proactive_semantic_kind"), 40)
            anchor_type = self._single_line(user.get("planned_proactive_anchor_type"), 40)
            semantic_score = self._int(user.get("planned_proactive_semantic_score"))
            semantic_note = self._single_line(user.get("planned_proactive_semantic_note"), 180)
            need_layer = self._single_line(user.get("planned_proactive_need_layer"), 40)
            need_drive = self._single_line(user.get("planned_proactive_need_drive"), 80)
            need_note = self._single_line(user.get("planned_proactive_need_note"), 120)
            need_score_bias = None
            need_pressure_bias = None
            pressure = 0
            risk = 0
            getter = getattr(self.plugin, "_planned_proactive_semantics", None)
            if callable(getter):
                try:
                    semantics = getter(user)
                except Exception:
                    semantics = {}
                if isinstance(semantics, dict):
                    semantic_kind = semantic_kind or self._single_line(semantics.get("kind"), 40)
                    anchor_type = anchor_type or self._single_line(semantics.get("anchor_type"), 40)
                    if semantic_score <= 0:
                        semantic_score = int(max(0.0, min(1.0, self._float(semantics.get("score")))) * 100)
                    pressure = int(max(0.0, min(1.0, self._float(semantics.get("pressure")))) * 100)
                    risk = int(max(0.0, min(1.0, self._float(semantics.get("risk")))) * 100)
                    semantic_note = semantic_note or self._single_line(semantics.get("note"), 180)
                    need_layer = need_layer or self._single_line(semantics.get("need_layer"), 40)
                    need_drive = need_drive or self._single_line(semantics.get("need_drive"), 80)
                    need_note = need_note or self._single_line(semantics.get("need_note"), 120)
                    need_score_bias = semantics.get("need_score_bias")
                    need_pressure_bias = semantics.get("need_pressure_bias")
            return {
                "semantic_kind": semantic_kind,
                "semantic_anchor_type": anchor_type,
                "semantic_score": semantic_score,
                "semantic_pressure": pressure,
                "semantic_risk": risk,
                "semantic_note": semantic_note,
                "need_layer": need_layer,
                "need_level": need_layer,
                "need_drive": need_drive,
                "need_note": need_note,
                "need_score_bias": need_score_bias,
                "need_pressure_bias": need_pressure_bias,
            }

        def planned_window_snapshot(user: dict[str, Any]) -> dict[str, Any]:
            start_ts = self._float(user.get("planned_proactive_window_start_at"))
            best_until_ts = self._float(user.get("planned_proactive_best_until_at"))
            expire_ts = self._float(user.get("planned_proactive_expire_at"))
            phase = ""
            detail = ""
            phase_getter = getattr(self.plugin, "_planned_impulse_window_phase", None)
            if callable(phase_getter):
                try:
                    phase, detail = phase_getter(user, now=now)
                except Exception:
                    phase, detail = "", ""
            impulse_value = 0
            value_getter = getattr(self.plugin, "_planned_impulse_value", None)
            if callable(value_getter):
                try:
                    impulse_value = int(max(0.0, min(1.0, self._float(value_getter(user, now=now)))) * 100)
                except Exception:
                    impulse_value = 0
            return {
                "window_start_ts": start_ts,
                "window_start": self.plugin._format_timestamp_elapsed(start_ts),
                "best_until_ts": best_until_ts,
                "best_until": self.plugin._format_timestamp_elapsed(best_until_ts),
                "expire_ts": expire_ts,
                "expire": self.plugin._format_timestamp_elapsed(expire_ts),
                "window_phase": self._single_line(phase, 24),
                "window_detail": self._single_line(detail, 160),
                "impulse_value": impulse_value,
            }

        def afterglow_snapshot(user: dict[str, Any]) -> dict[str, Any]:
            raw = user.get("proactive_afterglow")
            if not isinstance(raw, dict):
                return {}
            ts = self._float(raw.get("ts"))
            return {
                "ts": ts,
                "time": self.plugin._format_timestamp_elapsed(ts),
                "status": self._single_line(raw.get("status"), 32),
                "label": self._single_line(raw.get("label"), 180),
                "next_tendency": self._single_line(raw.get("next_tendency"), 180),
                "reason": self._single_line(raw.get("reason"), 50),
                "action": self._single_line(raw.get("action"), 50),
                "semantic_kind": self._single_line(raw.get("semantic_kind"), 40),
                "anchor_type": self._single_line(raw.get("anchor_type"), 40),
                "semantic_score": self._int(raw.get("semantic_score")),
            }

        def hesitation_snapshot(user: dict[str, Any]) -> dict[str, Any]:
            raw = user.get("recent_proactive_hesitations")
            latest = raw[-1] if isinstance(raw, list) and raw and isinstance(raw[-1], dict) else {}
            ts = self._float(latest.get("ts") if isinstance(latest, dict) else 0) or self._float(user.get("last_proactive_hesitation_at"))
            note = self._single_line(latest.get("note") if isinstance(latest, dict) else "", 160) or self._single_line(user.get("last_proactive_hesitation_note"), 160)
            if not ts and not note:
                return {}
            return {
                "ts": ts,
                "time": self.plugin._format_timestamp_elapsed(ts),
                "note": note,
                "count": self._int(latest.get("count") if isinstance(latest, dict) else 0),
                "topic": self._single_line(latest.get("topic") if isinstance(latest, dict) else "", 80),
                "motive": self._single_line(latest.get("motive") if isinstance(latest, dict) else "", 140),
            }

        for user_id, user in users.items():
            if not isinstance(user, dict):
                continue
            readiness = readiness_snapshot(user)
            afterglow = afterglow_snapshot(user)
            hesitation = hesitation_snapshot(user)
            user_summary_for_state = self._user_summary(str(user_id), user)
            effective_limit = (
                self.plugin._effective_user_daily_limit(user)
                if hasattr(self.plugin, "_effective_user_daily_limit")
                else getattr(self.plugin, "max_daily_messages", 0)
            )
            next_for_state = self._float(user.get("next_proactive_at"))
            proactive_gate = getattr(self.plugin, "_user_enabled_for_proactive", None)
            if callable(proactive_gate):
                try:
                    proactive_enabled = bool(proactive_gate(str(user_id), user))
                except Exception:
                    proactive_enabled = False
            else:
                capabilities = user.get("unified_profile_capabilities")
                proactive_enabled = bool(
                    user.get("proactive_private_enabled") is True
                    or (
                        isinstance(capabilities, dict)
                        and capabilities.get("proactive_private_enabled") is True
                    )
                )
            if bool(user.get("enabled", True)) and proactive_enabled:
                if should_show_user_state(
                    str(user_id),
                    user,
                    user_summary_for_state,
                    next_ts=next_for_state,
                    effective_limit=effective_limit,
                ):
                    user_states.append(
                        {
                            "user_id": str(user_id),
                            "user_label": user_summary_for_state.get("display_name") or str(user_id),
                            "user_role": user_summary_for_state.get("relationship_role") or "",
                            "user_role_label": user_summary_for_state.get("relationship_role_label") or "",
                            "sent_today": self._int(user.get("sent_today")),
                            "effective_daily_limit": effective_limit,
                            "effective_daily_limit_text": (
                                self.plugin._format_proactive_daily_limit(effective_limit)
                                if hasattr(self.plugin, "_format_proactive_daily_limit")
                                else str(effective_limit)
                            ),
                            "effective_daily_limit_unlimited": (
                                self.plugin._proactive_daily_limit_is_unlimited(effective_limit)
                                if hasattr(self.plugin, "_proactive_daily_limit_is_unlimited")
                                else False
                            ),
                            "next_proactive_ts": next_for_state,
                            "next_proactive": self.plugin._format_timestamp_elapsed(next_for_state),
                            "proactive_sending": bool(user.get("proactive_sending")),
                            "last_skip_ts": self._float(user.get("last_proactive_skip_at")),
                            "last_skip": self.plugin._format_timestamp_elapsed(user.get("last_proactive_skip_at", 0)),
                            "last_skip_reason": self._single_line(user.get("last_proactive_skip_reason"), 160),
                            "last_skip_prefix": self._single_line(user.get("last_proactive_skip_prefix"), 20),
                            "last_sent_ts": self._float(user.get("last_sent")),
                            "last_sent": self.plugin._format_timestamp_elapsed(user.get("last_sent", 0)),
                            "inner_readiness": readiness,
                            "afterglow": afterglow,
                            "hesitation": hesitation,
                        }
                    )
            scheduled_ts = self._float(user.get("next_proactive_at"))
            timer_event = user.get("llm_timer_event") if isinstance(user.get("llm_timer_event"), dict) else {}
            if scheduled_ts <= 0 and timer_event:
                scheduled_ts = self._float(timer_event.get("scheduled_ts"))
            if scheduled_ts <= 0:
                continue
            source = self._single_line(user.get("planned_proactive_source"), 40)
            if not source and timer_event:
                source = "timer"
            source = source or "proactive"
            status = "due" if scheduled_ts <= now else "scheduled"
            if scheduled_ts < now - 15 * 60:
                status = "overdue"
            if timer_event and self._single_line(timer_event.get("backend"), 40) == "astrbot_cron" and scheduled_ts <= now:
                status = "handed_off"
            timer_status = self._single_line(timer_event.get("status"), 40)
            if timer_status in {"failed", "cancelled", "cancel_failed", "cancel_skipped"}:
                status = timer_status
            user_summary = self._user_summary(str(user_id), user)
            source_counts[source] = source_counts.get(source, 0) + 1
            status_counts[status] = status_counts.get(status, 0) + 1
            action = (
                self._single_line(user.get("planned_proactive_action"), 40)
                or self._single_line(timer_event.get("action"), 40)
                or "message"
            )
            reason = (
                self._single_line(user.get("planned_proactive_reason"), 40)
                or self._single_line(timer_event.get("reason"), 40)
            )
            topic = (
                self._single_line(user.get("planned_proactive_topic"), 80)
                or self._single_line(timer_event.get("topic"), 80)
            )
            motive = (
                self._single_line(user.get("planned_proactive_motive"), 180)
                or self._single_line(timer_event.get("motive"), 180)
            )
            sanitizer = getattr(self.plugin, "_sanitize_friend_proactive_plan_fields", None)
            if callable(sanitizer):
                sanitized = sanitizer(
                    user,
                    reason=reason,
                    action=action,
                    topic=topic,
                    motive=motive,
                )
                reason = self._single_line(sanitized.get("reason"), 40) or reason
                action = self._single_line(sanitized.get("action"), 40) or action
                topic = self._single_line(sanitized.get("topic"), 80)
                motive = self._single_line(sanitized.get("motive"), 180)
            semantic = planned_semantic_snapshot(user)
            window = planned_window_snapshot(user)
            model_result = user.get("planned_proactive_model_judge_result")
            if not isinstance(model_result, dict):
                model_result = {}
            judged_at = self._float(user.get("planned_proactive_model_judge_at"))
            items.append(
                {
                    "user_id": str(user_id),
                    "user_label": user_summary.get("display_name") or str(user_id),
                    "user_role": user_summary.get("relationship_role") or "",
                    "user_role_label": user_summary.get("relationship_role_label") or "",
                    "source": source,
                    "source_label": self._proactive_source_label(source),
                    "source_note": self._proactive_source_note(source),
                    "status": status,
                    "action": action,
                    "reason": reason,
                    "reason_label": self._proactive_reason_label(reason, target_name=user_summary.get("display_name") or str(user_id)),
                    "reason_detail": self._proactive_reason_detail(
                        reason=reason,
                        source=source,
                        topic=topic,
                        motive=motive,
                        note=user.get("last_proactive_skip_reason"),
                        target_name=user_summary.get("display_name") or str(user_id),
                    ),
                    "topic": topic,
                    "motive": motive,
                    "planned_impulse_id": self._single_line(user.get("planned_proactive_impulse_id"), 40),
                    "planned_candidate_id": self._single_line(user.get("planned_candidate_id"), 40),
                    **semantic,
                    **window,
                    "inner_readiness": readiness,
                    "afterglow": afterglow,
                    "hesitation": hesitation,
                    "model_judge": {
                        "decision": self._single_line(model_result.get("decision"), 24),
                        "score": self._int(model_result.get("score")),
                        "reason": self._single_line(model_result.get("reason"), 140),
                        "cached": bool(model_result.get("cached")),
                        "judged_ts": judged_at,
                        "judged": self.plugin._format_timestamp_elapsed(judged_at),
                    } if model_result or judged_at > 0 else {},
                    "scheduled_ts": scheduled_ts,
                    "scheduled": self.plugin._format_timestamp_elapsed(scheduled_ts),
                    "last_skip": self.plugin._format_timestamp_elapsed(user.get("last_proactive_skip_at", 0)),
                    "last_skip_ts": self._float(user.get("last_proactive_skip_at")),
                    "last_skip_reason": self._single_line(user.get("last_proactive_skip_reason"), 120),
                    "last_skip_prefix": self._single_line(user.get("last_proactive_skip_prefix"), 20),
                    "created_ts": self._float(timer_event.get("created_at")),
                    "created": self.plugin._format_timestamp_elapsed(timer_event.get("created_at", 0)),
                    "origin": self._single_line(timer_event.get("origin"), 40),
                    "backend": self._single_line(timer_event.get("backend"), 40),
                    "job_id": self._single_line(timer_event.get("job_id"), 80),
                    "timer_status": timer_status,
                    "timer_error": self._single_line(timer_event.get("error") or timer_event.get("replace_error"), 180),
                    "activity": self._single_line(timer_event.get("activity"), 60),
                    "estimated_minutes": self._int(timer_event.get("estimated_minutes")),
                    "followup_intensity": self._int(timer_event.get("followup_intensity")),
                    "replaced_job_id": self._single_line(timer_event.get("replaced_job_id"), 80),
                    "cancelled_job_id": self._single_line(timer_event.get("cancelled_job_id"), 80),
                    "raw_time": self._single_line(timer_event.get("raw_time"), 40),
                    "trigger_message_id": self._single_line(timer_event.get("trigger_message_id"), 120),
                    "trigger_umo": self._single_line(timer_event.get("trigger_umo"), 160),
                    "has_timer_event": bool(timer_event),
                    "silence_until_due": bool(timer_event.get("silence_until_due")) if timer_event else False,
                }
            )

        items.sort(key=lambda item: self._float(item.get("scheduled_ts")))
        raw_audit = data.get("proactive_audit_log") if isinstance(data.get("proactive_audit_log"), list) else []
        seen_audit_signatures: dict[str, dict[str, Any]] = {}
        for raw in raw_audit:
            if not isinstance(raw, dict):
                continue
            meta_leak_checker = getattr(self.plugin, "_framework_agent_meta_summary_leak", None)
            if callable(meta_leak_checker) and (
                meta_leak_checker(str(raw.get("text_preview") or ""))
                or meta_leak_checker(str(raw.get("original_text_preview") or ""))
                or meta_leak_checker(str(raw.get("final_text_preview") or ""))
                or meta_leak_checker(str(raw.get("text") or ""))
                or meta_leak_checker(str(raw.get("note") or ""))
                or meta_leak_checker(str(raw.get("diagnostic_detail") or ""))
            ):
                continue
            user_id = str(raw.get("user_id") or "")
            user = users.get(user_id) if isinstance(users.get(user_id), dict) else {}
            user_summary = self._user_summary(user_id, user) if user_id else {}
            status = self._single_line(raw.get("status"), 32) or "unknown"
            note = self._single_line(raw.get("note"), 180)
            diagnostic_detail = self._single_line(raw.get("diagnostic_detail"), 2400)
            obsolete_checker = getattr(self.plugin, "_proactive_audit_note_is_obsolete_fixed_error", None)
            if callable(obsolete_checker) and obsolete_checker(note):
                status = "obsolete"
                note = "旧版本主动发送变量错误，当前版本已修复"
            if status == "obsolete":
                continue
            audit_reason = self._single_line(raw.get("reason"), 40)
            audit_action = self._single_line(raw.get("action"), 60)
            audit_topic = self._single_line(raw.get("topic"), 100)
            audit_motive = self._single_line(raw.get("motive"), 180)
            sanitizer = getattr(self.plugin, "_sanitize_friend_proactive_plan_fields", None)
            if isinstance(user, dict) and callable(sanitizer):
                sanitized = sanitizer(
                    user,
                    reason=audit_reason,
                    action=audit_action,
                    topic=audit_topic,
                    motive=audit_motive,
                )
                audit_reason = self._single_line(sanitized.get("reason"), 40) or audit_reason
                audit_action = self._single_line(sanitized.get("action"), 60) or audit_action
                audit_topic = self._single_line(sanitized.get("topic"), 100)
                audit_motive = self._single_line(sanitized.get("motive"), 180)
            updated_ts = self._float(raw.get("updated_ts"))
            bucket = int(updated_ts // 300) if updated_ts > 0 else 0
            signature = "|".join(
                self._single_line(value, 120)
                for value in (
                    user_id,
                    status,
                    self._single_line(raw.get("source"), 40),
                    audit_reason,
                    audit_action,
                    audit_topic,
                    audit_motive,
                    note,
                    bucket,
                )
            )
            text_preview = self._display_message_text(raw.get("text_preview"), 180)
            original_text_preview = self._display_message_text(raw.get("original_text_preview"), 180)
            final_text_preview = self._display_message_text(raw.get("final_text_preview"), 180)
            existing = seen_audit_signatures.get(signature)
            if existing is not None:
                previous_updated = self._float(existing.get("updated_ts"))
                existing["updated_ts"] = max(previous_updated, updated_ts)
                existing["updated"] = self.plugin._format_timestamp_elapsed(existing["updated_ts"])
                existing["duplicate_count"] = max(1, self._int(existing.get("duplicate_count"))) + max(1, self._int(raw.get("duplicate_count")))
                if updated_ts >= previous_updated:
                    if text_preview:
                        existing["text_preview"] = text_preview
                    if original_text_preview:
                        existing["original_text_preview"] = original_text_preview
                    if final_text_preview:
                        existing["final_text_preview"] = final_text_preview
                    if diagnostic_detail:
                        existing["diagnostic_detail"] = diagnostic_detail
                continue
            audit_status_counts[status] = audit_status_counts.get(status, 0) + 1
            item = {
                "id": self._single_line(raw.get("id"), 40),
                "user_id": user_id,
                "user_label": user_summary.get("display_name") or user_id,
                "user_role": user_summary.get("relationship_role") or "",
                "user_role_label": user_summary.get("relationship_role_label") or "",
                "status": status,
                "source": self._single_line(raw.get("source"), 40),
                "source_label": self._proactive_source_label(raw.get("source")),
                "source_note": self._proactive_source_note(raw.get("source")),
                "reason": audit_reason,
                "reason_label": self._proactive_reason_label(audit_reason, target_name=user_summary.get("display_name") or user_id),
                "reason_detail": self._proactive_reason_detail(
                    reason=audit_reason,
                    source=raw.get("source"),
                    topic=audit_topic,
                    motive=audit_motive,
                    note="",
                    target_name=user_summary.get("display_name") or user_id,
                ),
                "action": audit_action,
                "topic": audit_topic,
                "motive": audit_motive,
                "semantic_kind": self._single_line(raw.get("semantic_kind"), 40),
                "semantic_anchor_type": self._single_line(raw.get("semantic_anchor_type"), 40),
                "semantic_score": self._int(raw.get("semantic_score")),
                "semantic_pressure": self._int(raw.get("semantic_pressure")),
                "semantic_risk": self._int(raw.get("semantic_risk")),
                "semantic_note": self._single_line(raw.get("semantic_note"), 180),
                "need_layer": self._single_line(raw.get("need_layer") or raw.get("semantic_need_layer"), 40),
                "need_level": self._single_line(raw.get("need_layer") or raw.get("semantic_need_layer"), 40),
                "need_drive": self._single_line(raw.get("need_drive") or raw.get("semantic_need_drive"), 80),
                "need_note": self._single_line(raw.get("need_note") or raw.get("semantic_need_note"), 120),
                "need_score_bias": raw.get("need_score_bias", raw.get("semantic_need_score_bias")),
                "need_pressure_bias": raw.get("need_pressure_bias", raw.get("semantic_need_pressure_bias")),
                "note": note,
                "diagnostic_detail": diagnostic_detail,
                "text_preview": text_preview,
                "original_text_preview": original_text_preview,
                "final_text_preview": final_text_preview,
                "scheduled_ts": self._float(raw.get("scheduled_ts")),
                "scheduled": self.plugin._format_timestamp_elapsed(raw.get("scheduled_ts", 0)),
                "created_ts": self._float(raw.get("created_ts")),
                "created": self.plugin._format_timestamp_elapsed(raw.get("created_ts", 0)),
                "updated_ts": updated_ts,
                "updated": self.plugin._format_timestamp_elapsed(raw.get("updated_ts", 0)),
                "expects_reply": bool(raw.get("expects_reply")),
                "outcome": self._single_line(raw.get("outcome"), 32),
                "outcome_at": self._float(raw.get("outcome_at")),
                "outcome_latency_seconds": self._int(raw.get("outcome_latency_seconds")),
                "candidate_id": self._single_line(raw.get("candidate_id"), 40),
                "has_image": bool(_path_text(raw.get("image_path"), 1000)),
                "extra_count": self._int(raw.get("extra_count")),
                "duplicate_count": max(1, self._int(raw.get("duplicate_count"))),
            }
            seen_audit_signatures[signature] = item
            audit_items.append(item)
        audit_items.sort(key=lambda item: self._float(item.get("updated_ts") or item.get("created_ts")), reverse=True)
        runtime = data.get("proactive_runtime") if isinstance(data.get("proactive_runtime"), dict) else {}
        last_tick_started = self._float(runtime.get("last_tick_started_at")) if runtime else 0
        last_tick_finished = self._float(runtime.get("last_tick_finished_at")) if runtime else 0
        tick_age = now - max(last_tick_started, last_tick_finished) if max(last_tick_started, last_tick_finished) > 0 else -1
        expected_interval = max(30, self._int(getattr(self.plugin, "check_interval_seconds", 60)))
        review_summary_getter = getattr(self.plugin, "_proactive_review_audit_summary", None)
        review_summary = review_summary_getter(now=now, window_days=7) if callable(review_summary_getter) else {}
        if not isinstance(review_summary, dict):
            review_summary = {}
        return {
            "total": len(items),
            "source_counts": source_counts,
            "status_counts": status_counts,
            "items": items[:30],
            "audit_total": len(audit_items),
            "audit_status_counts": audit_status_counts,
            "audit_items": audit_items[:40],
            "review_summary": review_summary,
            "user_states": sorted(
                user_states,
                key=lambda item: (
                    0 if item.get("user_role") == "owner" else 1,
                    self._float(item.get("next_proactive_ts")) if self._float(item.get("next_proactive_ts")) > 0 else 9999999999,
                    self._single_line(item.get("user_label"), 40),
                ),
            )[:40],
            "runtime": {
                "last_tick_started_ts": last_tick_started,
                "last_tick_started": self.plugin._format_timestamp_elapsed(last_tick_started),
                "last_tick_finished_ts": last_tick_finished,
                "last_tick_finished": self.plugin._format_timestamp_elapsed(last_tick_finished),
                "tick_age_seconds": round(tick_age, 1) if tick_age >= 0 else -1,
                "expected_interval_seconds": expected_interval,
                "healthy": bool(tick_age >= 0 and tick_age <= max(180, expected_interval * 4)),
                "last_tick_error": self._single_line(runtime.get("last_tick_error"), 180) if runtime else "",
            },
        }
