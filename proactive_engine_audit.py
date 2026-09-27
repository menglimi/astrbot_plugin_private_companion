# -*- coding: utf-8 -*-
"""审计/解释域。

由 tools/split_mixin_domain.py 从 proactive_engine.py 机械抽取（14 个方法 + 0 个模块级名字 + 0 个类级赋值 / 461 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveEngineMixin）。
"""
from __future__ import annotations

import uuid
from .helpers import (
    _now_ts,
    _path_text,
    _redact_outbound_secrets,
    _safe_float,
    _safe_int,
    _single_line,
    _strip_internal_message_blocks,
)
from .persona_config import runtime_persona_setting
from .proactive_routes import PROACTIVE_ROUTE_REGISTRY
from typing import Any

from .logging_util import get_module_logger
from .proactive_engine_shared import _engine_host

logger = get_module_logger(__name__)



class ProactiveEngineAuditMixin:
    """审计/解释域（从 ProactiveEngineMixin 拆出）。"""


    def _proactive_decision_snapshot(self, user: dict[str, Any], *, now: float | None = None) -> dict[str, Any]:
        now = _engine_host._now_ts() if now is None else now
        factors = self._proactive_decision_factors(user, now=now)
        blocker_labels = [item.get("label") for item in factors if item.get("blocker")]
        total_score = 0
        for item in factors:
            if item.get("key") == "total":
                total_score = _safe_int(item.get("score"), 0, 0, 100)
                break
        return {
            "score": total_score,
            "blockers": [str(item) for item in blocker_labels if str(item or "").strip()],
            "factors": factors,
            "generated_ts": now,
        }

    def _proactive_audit_log(self) -> list[dict[str, Any]]:
        raw = self.data.setdefault("proactive_audit_log", [])
        if not isinstance(raw, list):
            raw = []
            self.data["proactive_audit_log"] = raw
        return raw

    def _proactive_review_audit_summary(
        self,
        *,
        now: float | None = None,
        window_days: int = 7,
    ) -> dict[str, Any]:
        """Aggregate recent review outcomes so tuning is evidence-based."""
        check_now = _engine_host._now_ts() if now is None else float(now)
        cutoff = check_now - max(1, min(30, int(window_days or 7))) * 86400
        decision_counts: dict[str, int] = {}
        reason_counts: dict[str, int] = {}
        outcome_counts = {"replied_24h": 0, "no_reply_24h": 0, "pending": 0}
        total = 0
        for item in self._proactive_audit_log():
            if not isinstance(item, dict):
                continue
            updated_at = _safe_float(item.get("updated_ts"), _safe_float(item.get("created_ts"), 0))
            if updated_at < cutoff:
                continue
            status = _single_line(item.get("status"), 32).lower() or "unknown"
            if status in {"running", "unknown"}:
                continue
            reason = _single_line(item.get("reason") or item.get("note"), 48) or "未标注"
            decision_counts[status] = decision_counts.get(status, 0) + 1
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
            if status == "sent" and bool(item.get("expects_reply")):
                outcome = _single_line(item.get("outcome"), 32).lower()
                sent_at = _safe_float(item.get("sent_ts"), updated_at)
                if outcome == "replied_24h":
                    outcome_counts["replied_24h"] += 1
                elif sent_at > 0 and check_now - sent_at >= 24 * 3600:
                    outcome_counts["no_reply_24h"] += 1
                else:
                    outcome_counts["pending"] += 1
            total += 1
        settled_outcomes = outcome_counts["replied_24h"] + outcome_counts["no_reply_24h"]
        runtime = self.data.get("proactive_review_runtime")
        runtime = runtime if isinstance(runtime, dict) else {}
        return {
            "window_days": max(1, min(30, int(window_days or 7))),
            "total": total,
            "decision_counts": decision_counts,
            "decision_percentages": {
                key: round(value / total * 100, 1) for key, value in decision_counts.items()
            } if total else {},
            "top_reasons": [
                {"reason": key, "count": value}
                for key, value in sorted(reason_counts.items(), key=lambda pair: (-pair[1], pair[0]))[:10]
            ],
            "reply_outcomes": outcome_counts,
            "reply_rate_24h": (
                round(outcome_counts["replied_24h"] / settled_outcomes * 100, 1)
                if settled_outcomes else None
            ),
            "consecutive_fallback_releases": _safe_int(runtime.get("consecutive_fallback_releases"), 0, 0),
            "last_fallback_release_at": _safe_float(runtime.get("last_fallback_release_at"), 0),
            "last_fallback_reason": _single_line(runtime.get("last_fallback_reason"), 180),
            "alert": _safe_int(runtime.get("consecutive_fallback_releases"), 0, 0) >= 10,
        }

    def _mark_proactive_audit_reply_outcome(
        self,
        user: dict[str, Any],
        *,
        received_at: float | None = None,
        message_id: str = "",
    ) -> bool:
        """Associate the first timely inbound reply with its reply-seeking proactive send."""
        if not isinstance(user, dict):
            return False
        audit_id = _single_line(user.get("last_proactive_reply_audit_id"), 40)
        sent_at = _safe_float(user.get("last_proactive_reply_audit_sent_at"), 0)
        check_at = _engine_host._now_ts() if received_at is None else float(received_at)
        if not audit_id or sent_at <= 0 or check_at < sent_at or check_at - sent_at > 24 * 3600:
            return False
        for item in reversed(self._proactive_audit_log()):
            if not isinstance(item, dict) or _single_line(item.get("id"), 40) != audit_id:
                continue
            if _single_line(item.get("status"), 32).lower() != "sent" or not bool(item.get("expects_reply")):
                return False
            if _single_line(item.get("outcome"), 32):
                return False
            item["outcome"] = "replied_24h"
            item["outcome_at"] = check_at
            item["outcome_latency_seconds"] = max(0, int(check_at - sent_at))
            if message_id:
                item["outcome_message_id"] = _single_line(message_id, 160)
            item["updated_ts"] = check_at
            user["last_proactive_reply_audit_outcome"] = "replied_24h"
            user["last_proactive_reply_audit_outcome_at"] = check_at
            return True
        return False

    def _proactive_visible_text_preview(self, text: str, *, limit: int = 180) -> str:
        meta_checker = getattr(self, "_framework_agent_meta_summary_leak", None)
        if callable(meta_checker):
            try:
                if meta_checker(str(text or "")):
                    return ""
            except Exception:
                pass
        cleaner = getattr(self, "_visible_text_without_tts_reading", None)
        if callable(cleaner):
            try:
                return _single_line(cleaner(text, limit=limit), limit)
            except Exception:
                pass
        return _single_line(_strip_internal_message_blocks(text, enabled=bool(runtime_persona_setting(self, "enable_framework_error_leak_guard", True))), limit)

    def _proactive_audit_safe_note(self, note: Any, *, limit: int = 180) -> str:
        limit = max(1, int(limit or 1))
        text = _single_line(_redact_outbound_secrets(note, self), max(4096, limit + 1))
        if not text:
            return ""
        meta_checker = getattr(self, "_framework_agent_meta_summary_leak", None)
        if callable(meta_checker):
            try:
                if meta_checker(text):
                    return "模型/供应商返回内部错误，原文已隐藏"
            except Exception:
                pass
        if len(text) > limit:
            return text[: max(1, limit - 1)].rstrip() + "…"
        return text

    def _proactive_audit_signature(self, item: dict[str, Any], *, bucket_seconds: int = 300) -> str:
        updated = _safe_float(item.get("updated_ts") or item.get("created_ts"), 0)
        bucket = int(updated // max(1, bucket_seconds)) if updated > 0 else 0
        parts = [
            item.get("user_id"),
            item.get("status"),
            item.get("source"),
            item.get("reason"),
            item.get("action"),
            item.get("topic"),
            item.get("motive"),
            item.get("note"),
            bucket,
        ]
        return "|".join(_single_line(part, 120) for part in parts)

    @staticmethod
    def _proactive_audit_note_is_obsolete_fixed_error(note: Any) -> bool:
        text = str(note or "")
        if "NameError" not in text:
            return False
        return any(
            token in text
            for token in (
                "name 'topic' is not defined",
                "name 'name' is not defined",
            )
        )

    def _compact_proactive_audit_log(self) -> None:
        log = self._proactive_audit_log()
        compacted: list[dict[str, Any]] = []
        seen: dict[str, dict[str, Any]] = {}
        for item in log:
            if not isinstance(item, dict):
                continue
            if self._proactive_audit_note_is_obsolete_fixed_error(item.get("note")):
                item["status"] = "obsolete"
                item["note"] = "旧版本主动发送变量错误，当前版本已修复"
                item.pop("diagnostic_detail", None)
            signature = self._proactive_audit_signature(item)
            previous = seen.get(signature)
            if previous is None:
                seen[signature] = item
                compacted.append(item)
                continue
            previous["updated_ts"] = max(
                _safe_float(previous.get("updated_ts"), 0),
                _safe_float(item.get("updated_ts"), 0),
            )
            previous["duplicate_count"] = _safe_int(previous.get("duplicate_count"), 1, 1) + 1
            for key in ("text_preview", "original_text_preview", "final_text_preview", "image_path", "diagnostic_detail"):
                if item.get(key):
                    previous[key] = item.get(key)
            if item.get("extra_count") is not None:
                previous["extra_count"] = max(
                    _safe_int(previous.get("extra_count"), 0, 0),
                    _safe_int(item.get("extra_count"), 0, 0),
                )
        if len(compacted) != len(log):
            log[:] = compacted[-160:]

    def _append_proactive_audit(
        self,
        user_id: str,
        user: dict[str, Any],
        *,
        status: str,
        note: str = "",
        reason: str = "",
        action: str = "",
        text: str = "",
        original_text: str = "",
        final_text: str = "",
        diagnostic_detail: str = "",
    ) -> str:
        now = _engine_host._now_ts()
        audit_id = uuid.uuid4().hex[:12]
        semantics = self._planned_proactive_semantics(user)
        item = {
            "id": audit_id,
            "created_ts": now,
            "updated_ts": now,
            "user_id": str(user_id or user.get("user_id") or user.get("id") or ""),
            "status": _single_line(status, 32) or "unknown",
            "note": self._proactive_audit_safe_note(note, limit=180),
            "source": self._normalize_legacy_proactive_text(user.get("planned_proactive_source"), limit=40) or "proactive",
            "route_kind": _single_line(user.get("planned_proactive_kind"), 40) or (
                self._proactive_message_kind(
                    reason=reason or user.get("planned_proactive_reason"),
                    source=user.get("planned_proactive_source"),
                    semantic_kind=user.get("planned_proactive_semantic_kind"),
                )
                if callable(getattr(self, "_proactive_message_kind", None))
                else PROACTIVE_ROUTE_REGISTRY.route_for(
                    reason=reason or user.get("planned_proactive_reason"),
                    source=user.get("planned_proactive_source"),
                    semantic_kind=user.get("planned_proactive_semantic_kind"),
                ).key
            ),
            "route_version": _safe_int(user.get("planned_proactive_route_version"), 0, 0),
            "route_dedupe_key": _single_line(user.get("planned_proactive_route_dedupe_key"), 180),
            "route_review_profile": _single_line(user.get("planned_proactive_route_review_profile"), 40),
            "route_retry_profile": _single_line(user.get("planned_proactive_route_retry_profile"), 40),
            "reason": self._normalize_legacy_proactive_text(reason or user.get("planned_proactive_reason"), limit=40),
            "action": _single_line(action or user.get("planned_proactive_action"), 60) or "message",
            "topic": _single_line(user.get("planned_proactive_topic"), 100),
            "motive": _single_line(user.get("planned_proactive_motive"), 180),
            "semantic_kind": _single_line(user.get("planned_proactive_semantic_kind"), 40) or _single_line(semantics.get("kind"), 40),
            "semantic_anchor_type": _single_line(user.get("planned_proactive_anchor_type"), 40) or _single_line(semantics.get("anchor_type"), 40),
            "semantic_score": _safe_int(user.get("planned_proactive_semantic_score"), 0, 0, 100) or int(max(0.0, min(1.0, _safe_float(semantics.get("score"), 0.0))) * 100),
            "semantic_pressure": int(max(0.0, min(1.0, _safe_float(semantics.get("pressure"), 0.0))) * 100),
            "semantic_risk": int(max(0.0, min(1.0, _safe_float(semantics.get("risk"), 0.0))) * 100),
            "semantic_note": _single_line(user.get("planned_proactive_semantic_note"), 180) or _single_line(semantics.get("note"), 180),
            "need_layer": _single_line(user.get("planned_proactive_need_layer"), 40) or _single_line(semantics.get("need_layer"), 40),
            "need_drive": _single_line(user.get("planned_proactive_need_drive"), 80) or _single_line(semantics.get("need_drive"), 80),
            "need_note": _single_line(user.get("planned_proactive_need_note"), 120) or _single_line(semantics.get("need_note"), 120),
            "scheduled_ts": _safe_float(user.get("next_proactive_at"), 0),
            "candidate_id": _single_line(user.get("planned_candidate_id"), 40),
            "umo": _single_line(user.get("umo"), 180),
            "text_preview": self._proactive_visible_text_preview(text) if text else "",
            "original_text_preview": self._proactive_visible_text_preview(original_text) if original_text else "",
            "final_text_preview": self._proactive_visible_text_preview(final_text) if final_text else "",
            "diagnostic_detail": self._proactive_audit_safe_note(diagnostic_detail, limit=2400) if diagnostic_detail else "",
        }
        log = self._proactive_audit_log()
        signature = self._proactive_audit_signature(item)
        for existing in reversed(log[-30:]):
            if not isinstance(existing, dict):
                continue
            if self._proactive_audit_signature(existing) != signature:
                continue
            existing["updated_ts"] = now
            existing["duplicate_count"] = _safe_int(existing.get("duplicate_count"), 1, 1) + 1
            for key in ("text_preview", "original_text_preview", "final_text_preview", "diagnostic_detail"):
                if item.get(key):
                    existing[key] = item.get(key)
            return _single_line(existing.get("id"), 40) or audit_id
        log.append(item)
        self._compact_proactive_audit_log()
        del log[:-160]
        return audit_id

    def _update_proactive_audit(
        self,
        audit_id: str,
        *,
        status: str,
        note: str = "",
        text: str = "",
        image_path: str = "",
        extra_count: int | None = None,
        action: str = "",
        reason: str = "",
        original_text: str = "",
        final_text: str = "",
        diagnostic_detail: str = "",
        sent_at: float | None = None,
        expects_reply: bool | None = None,
    ) -> None:
        if not audit_id:
            return
        for item in reversed(self._proactive_audit_log()):
            if str(item.get("id") or "") != str(audit_id):
                continue
            previous_status = _single_line(item.get("status"), 32)
            item["status"] = _single_line(status, 32) or item.get("status") or "unknown"
            item["updated_ts"] = _engine_host._now_ts()
            if note:
                item["note"] = self._proactive_audit_safe_note(note, limit=180)
            if text:
                item["text_preview"] = self._proactive_visible_text_preview(text)
            if original_text:
                item["original_text_preview"] = self._proactive_visible_text_preview(original_text)
            if final_text:
                item["final_text_preview"] = self._proactive_visible_text_preview(final_text)
            if diagnostic_detail:
                item["diagnostic_detail"] = self._proactive_audit_safe_note(diagnostic_detail, limit=2400)
            if image_path:
                item["image_path"] = _path_text(image_path, 1000)
            if extra_count is not None:
                item["extra_count"] = max(0, int(extra_count))
            if action:
                item["action"] = _single_line(action, 60)
            if reason:
                item["reason"] = _single_line(reason, 40)
            if sent_at is not None:
                item["sent_ts"] = max(0.0, float(sent_at))
            if expects_reply is not None:
                item["expects_reply"] = bool(expects_reply)
            if item.get("status") in {"cancelled", "dropped"} and item.get("status") != previous_status:
                notifier = getattr(self, "_schedule_reply_interception_forward", None)
                if callable(notifier):
                    notifier(
                        "proactive_block",
                        source=_single_line(item.get("source"), 60) or "主动消息",
                        reason=_single_line(item.get("note"), 300) or "主动候选被拦截",
                        source_session=_single_line(item.get("umo"), 180),
                        before=_single_line(
                            item.get("final_text_preview") or item.get("text_preview") or item.get("original_text_preview"),
                            500,
                        ),
                        detail="；".join(
                            part for part in (
                                f"状态={item.get('status')}",
                                f"动作={_single_line(item.get('action'), 60)}" if item.get("action") else "",
                                f"话题={_single_line(item.get('topic'), 120)}" if item.get("topic") else "",
                            ) if part
                        ),
                    )
            self._compact_proactive_audit_log()
            break

    def _recover_stale_proactive_sending(self, user: dict[str, Any], *, now: float | None = None) -> bool:
        if not user.get("proactive_sending"):
            return False
        now = now or _engine_host._now_ts()
        started_at = _safe_float(user.get("proactive_sending_started_at"), 0)
        if started_at > 0 and now - started_at < 8 * 60:
            return False
        user["proactive_sending"] = False
        user["proactive_sending_started_at"] = 0
        logger.warning(
            "检测到残留的主动发送标记,已自动清理: user=%s started_at=%s",
            user.get("user_id") or user.get("id") or "unknown",
            self._environment_fromtimestamp(started_at).strftime("%m-%d %H:%M:%S") if started_at > 0 else "unknown",
        )
        return True

    def _is_recent_poke_echo(self, user: dict[str, Any], text: str, *, now: float | None = None) -> bool:
        now = now or _engine_host._now_ts()
        suppress_until = _safe_float(user.get("poke_echo_suppress_until"), 0)
        if suppress_until <= 0 or now > suppress_until:
            return False
        return not bool(_single_line(text, 120))

    def _explain_proactive_decision(self, user: dict[str, Any]) -> str:
        probe = dict(user)
        decision, reason = self._should_send(probe)
        now = _engine_host._now_ts()
        snapshot = self._proactive_decision_snapshot(probe, now=now)
        planned_reason = self._normalize_legacy_proactive_text(probe.get("planned_proactive_reason"), limit=40)
        planned_action = str(probe.get("planned_proactive_action") or "message")
        planned_source = self._normalize_legacy_proactive_text(probe.get("planned_proactive_source"), limit=40)
        planned_motive = _single_line(probe.get("planned_proactive_motive"), 48)
        next_at = _safe_float(probe.get("next_proactive_at"), 0)
        planned_impulse_id = _single_line(probe.get("planned_proactive_impulse_id"), 20)
        planned_best_until = _safe_float(probe.get("planned_proactive_best_until_at"), 0)
        timer_event = self._get_active_llm_timer(probe)
        active_impulses = [
            item for item in self._cleanup_proactive_impulses(probe, now=now)
            if isinstance(item, dict) and str(item.get("state") or "queued") in {"queued", "deferred"}
        ]
        next_at_text = (
            self._environment_fromtimestamp(next_at).strftime("%m-%d %H:%M:%S")
            if next_at > 0
            else "未安排"
        )
        sent_today = _safe_int(probe.get("sent_today"), 0)
        sent_greetings = probe.get("greetings_sent")
        if not isinstance(sent_greetings, list):
            sent_greetings = []
        suppressed_greetings = probe.get("greetings_suppressed_by_inbound")
        if not isinstance(suppressed_greetings, list):
            suppressed_greetings = []
        last_activity_at = self._latest_private_user_activity_ts(probe)
        last_sent_at = _safe_float(probe.get("last_sent"), 0)
        last_seen_gap = now - last_activity_at if last_activity_at > 0 else -1
        last_sent_gap = now - last_sent_at if last_sent_at > 0 else -1
        idle_limit = (
            self._effective_user_greeting_idle_minutes(probe) * 60
            if self._is_greeting_reason(planned_reason)
            else self._effective_user_idle_minutes(probe) * 60
        )
        min_interval = self._effective_min_interval_seconds(probe)
        if self._is_greeting_reason(planned_reason) and self._private_user_role(probe) != "friend":
            min_interval = min(min_interval, self._greeting_min_interval_seconds(planned_reason))
        effective_daily_limit = self._effective_user_daily_limit(probe)
        daily_limit_text = self._format_proactive_daily_limit(effective_daily_limit)
        soft_target_text = "不限" if self._proactive_daily_limit_is_unlimited(effective_daily_limit) else f"{self._soft_daily_target(probe):.1f}"
        reason_allowed = self._is_reason_allowed_now(planned_reason, probe)
        moment_ok = True
        if reason == "未到候选主动时间":
            reason_allowed_text = "到点后再检查"
            moment_ok_text = "到点后再检查"
        else:
            reason_allowed_text = "通过" if reason_allowed else "不适合"
            moment_ok_text = "候选到点即发送"
        lines = [
            f"主动判定：{'会发送' if decision else '这次不发'}",
            f"原因：{reason}",
            f"综合评分：{_safe_int(snapshot.get('score'), 0, 0, 100)}/100",
            f"下次候选：{next_at_text}",
            f"计划：{planned_reason or '未记录'}｜{planned_action}"
            + (f"｜计划源：{planned_source}" if planned_source else "")
            + (f"｜念头ID：{planned_impulse_id}" if planned_impulse_id else "")
            + (f"｜话题：{_single_line(probe.get('planned_proactive_topic'), 24)}" if _single_line(probe.get("planned_proactive_topic"), 24) else "")
            + (f"｜动机：{planned_motive}" if planned_motive else "")
            + (f"｜最佳窗口到 {self._environment_fromtimestamp(planned_best_until).strftime('%H:%M:%S')}" if planned_best_until > 0 else "")
            + (f"｜来源：模型预约" if isinstance(timer_event, dict) and _safe_float(timer_event.get("scheduled_ts"), 0) == next_at else ""),
            f"潜在念头：{len(active_impulses)} 个待选",
            f"今日已发：{sent_today}/{daily_limit_text}｜软目标约 {soft_target_text}",
            f"今日问候：已发 {', '.join(str(item) for item in sent_greetings) or '无'}｜被用户消息跳过 {', '.join(str(item) for item in suppressed_greetings) or '无'}",
            f"免打扰：{'是' if self._is_quiet_time() else '否'}｜失眠特例：{'可用' if self._can_send_insomnia_night_message(probe) else '不可用'}",
            f"距用户上次活跃：{self._format_elapsed(max(0, last_seen_gap)) if last_seen_gap >= 0 else '从未'}｜要求至少 {self._format_elapsed(idle_limit)}",
            f"距上次主动：{self._format_elapsed(max(0, last_sent_gap)) if last_sent_gap >= 0 else '从未'}｜要求至少 {self._format_elapsed(min_interval)}",
            f"时间窗适配：{reason_allowed_text}｜自然动机：{moment_ok_text}",
        ]
        blockers = snapshot.get("blockers") if isinstance(snapshot.get("blockers"), list) else []
        if blockers:
            lines.append("阻塞项：" + " / ".join(_single_line(item, 32) for item in blockers[:6] if _single_line(item, 32)))
        factor_lines = []
        factors = snapshot.get("factors") if isinstance(snapshot.get("factors"), list) else []
        for item in factors:
            if not isinstance(item, dict) or item.get("key") in {"total"}:
                continue
            label = _single_line(item.get("label"), 24)
            detail = _single_line(item.get("detail"), 90)
            state_text = "通过" if item.get("passed") else "未通过"
            score_text = f"{_safe_int(item.get('score'), 0, -100, 100):+d}"
            if label:
                factor_lines.append(f"- {label}：{state_text}（{score_text}）" + (f"｜{detail}" if detail else ""))
        if factor_lines:
            lines.append("判定分解：")
            lines.extend(factor_lines[:12])
        return "\n".join(lines)
