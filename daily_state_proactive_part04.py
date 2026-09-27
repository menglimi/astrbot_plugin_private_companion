# -*- coding: utf-8 -*-
"""DailyStateProactivePart04Mixin。

由 tools/split_mixin_domain.py 从 daily_state_proactive.py 机械抽取（10 个方法 + 0 个模块级名字 + 0 个类级赋值 / 337 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 DailyStateProactiveMixin）。
"""
from __future__ import annotations

from .daily_state_proactive_shared import _now_ts, logger
from .daily_state_proactive_shared import Any
from .daily_state_proactive_shared import _safe_float
from .daily_state_proactive_shared import _single_line
from .daily_state_proactive_shared import deepcopy
from .daily_state_proactive_shared import math
from .daily_state_proactive_shared import normalize_legacy_tag_text
from .daily_state_proactive_shared import random



class DailyStateProactivePart04Mixin:
    """DailyStateProactivePart04Mixin（从 DailyStateProactiveMixin 拆出）。"""


    def _sync_live_user_proactive_schedule(self, user_id: str, source: dict[str, Any]) -> bool:
        """Mirror proactive-plan mutations from a tick snapshot back to the live user record."""
        if not isinstance(source, dict):
            return False
        raw_user_id = str(user_id or source.get("user_id") or source.get("id") or "").strip()
        if not raw_user_id:
            return False
        try:
            current = self._get_user(raw_user_id)
        except Exception:
            return False
        if not isinstance(current, dict):
            return False
        keys = (
            "next_proactive_at",
            "planned_proactive_reason",
            "planned_proactive_action",
            "planned_proactive_source",
            "planned_proactive_conversation_posture",
            "planned_proactive_conversation_closing_deferred",
            "planned_proactive_kind",
            "planned_proactive_route_version",
            "planned_proactive_route_dedupe_key",
            "planned_proactive_route_review_profile",
            "planned_proactive_route_retry_profile",
            "planned_proactive_route_cancel_if_new_inbound",
            "planned_proactive_route_recent_chat_policy",
            "planned_proactive_route_allow_automatic_followup",
            "planned_proactive_route_disable_segmenting",
            "planned_proactive_response_expectation",
            "planned_proactive_origin_event_id",
            "planned_proactive_route_preflight_action",
            "planned_proactive_route_preflight_note",
            "planned_proactive_motive",
            "planned_proactive_topic",
            "planned_proactive_impulse_id",
            "planned_proactive_window_start_at",
            "planned_proactive_best_until_at",
            "planned_proactive_expire_at",
            "planned_proactive_origin_at",
            "planned_proactive_origin_key",
            "planned_proactive_freshness",
            "planned_proactive_delivery_state",
            "planned_proactive_semantic_kind",
            "planned_proactive_anchor_type",
            "planned_proactive_semantic_score",
            "planned_proactive_semantic_note",
            "planned_proactive_model_judge_signature",
            "planned_proactive_model_judge_result",
            "planned_proactive_model_judge_at",
            "planned_event_chain",
            "planned_opener_mode",
            "planned_followup_kind",
            "planned_proactive_quota_exempt",
            "planned_proactive_window_timezone",
            "planned_birthday_event_context",
            "planned_special_day_context",
            "insomnia_night_context",
            "planned_candidate_id",
            "planned_proactive_trigger_message_id",
            "planned_proactive_trigger_umo",
            "planned_proactive_trigger_ts",
            "planned_proactive_trigger_inbound_count",
            "planned_proactive_trigger_created_at",
            "proactive_impulses",
            "recent_proactive_hesitations",
            "last_proactive_hesitation_at",
            "last_proactive_hesitation_note",
        )
        changed = False
        for key_name in keys:
            if key_name not in source:
                continue
            value = deepcopy(source.get(key_name))
            if current.get(key_name) != value:
                current[key_name] = value
                changed = True
        return changed

    def _recent_chat_proactive_guard_reason(
        self,
        user: dict[str, Any],
        *,
        now: float | None = None,
        planned_reason: str = "",
        planned_source: str = "",
        due_timer_active: bool = False,
        is_troubleshooting: bool = False,
    ) -> str:
        """Block ordinary proactive messages when the private chat has just moved."""
        if not isinstance(user, dict):
            return ""
        source = normalize_legacy_tag_text(planned_source or user.get("planned_proactive_source"))
        if is_troubleshooting or due_timer_active or source == "timer":
            return ""
        check_now = _now_ts() if now is None else now
        reason = normalize_legacy_tag_text(planned_reason or user.get("planned_proactive_reason"))
        idle_minutes = (
            self._effective_user_greeting_idle_minutes(user)
            if self._is_greeting_reason(reason)
            else self._effective_user_idle_minutes(user)
        )
        idle_seconds = max(0, idle_minutes) * 60
        if idle_seconds <= 0:
            return ""
        recent_at = self._latest_private_user_activity_ts(user)
        if recent_at <= 0:
            return ""
        remaining = recent_at + idle_seconds - check_now
        if remaining <= 0:
            return ""
        minutes = max(1, int(math.ceil(remaining / 60)))
        return f"刚聊完，普通主动延后（还需安静约 {minutes} 分钟）"

    def _defer_proactive_for_recent_chat(
        self,
        user: dict[str, Any],
        *,
        now: float | None = None,
        note: str = "",
    ) -> None:
        if not isinstance(user, dict):
            return
        check_now = _now_ts() if now is None else now
        reason = normalize_legacy_tag_text(user.get("planned_proactive_reason"))
        idle_minutes = (
            self._effective_user_greeting_idle_minutes(user)
            if self._is_greeting_reason(reason)
            else self._effective_user_idle_minutes(user)
        )
        recent_at = self._latest_private_user_activity_ts(user)
        quiet_until = recent_at + max(0, idle_minutes) * 60 if recent_at > 0 else check_now + 10 * 60
        if self._is_sticky_greeting_reason(reason) and self._reschedule_greeting_within_window(user, reason, now=check_now):
            pass
        else:
            delay_minutes = (
                max(5.0, (quiet_until - check_now) / 60 + 2.0),
                max(8.0, (quiet_until - check_now) / 60 + 8.0),
            )
            replacer = getattr(self, "_defer_or_replace_planned_impulse", None)
            replaced = False
            handled_by_replacer = False
            if callable(replacer):
                try:
                    handled_by_replacer = True
                    replaced = bool(
                        replacer(
                            user,
                            now=check_now,
                            note=note or "刚聊完，普通主动延后",
                            delay_minutes=delay_minutes,
                            block_current=False,
                        )
                    )
                except Exception as exc:
                    logger.debug("刚聊完主动换念头失败,回退延后: %s", _single_line(exc, 120))
                    replaced = False
                    handled_by_replacer = False
            if not replaced:
                if handled_by_replacer and _safe_float(user.get("next_proactive_at"), 0) > check_now:
                    pass
                elif handled_by_replacer and not _single_line(normalize_legacy_tag_text(user.get("planned_proactive_reason")), 40):
                    self._schedule_next_proactive(user, now=check_now, delay_hours=(max(0.2, delay_minutes[0] / 60), max(0.35, delay_minutes[1] / 60)))
                else:
                    user["next_proactive_at"] = max(check_now + 5 * 60, quiet_until + random.uniform(2 * 60, 8 * 60))
            if normalize_legacy_tag_text(user.get("planned_proactive_source")) == "simulation":
                sim = user.get("simulation_mode")
                events = sim.get("events") if isinstance(sim, dict) else None
                if isinstance(events, list) and events and isinstance(events[0], dict):
                    events[0]["_scheduled_ts"] = user["next_proactive_at"]
            if handled_by_replacer:
                return
        self._mark_planned_candidate_status(user, "deferred", note or "刚聊完，普通主动延后")

    def _is_troubleshooting_proactive_plan(self, user: dict[str, Any]) -> bool:
        return isinstance(user, dict) and normalize_legacy_tag_text(user.get("planned_proactive_source")) == "troubleshooting"

    def _append_troubleshooting_proactive_step(
        self,
        user: dict[str, Any],
        name: str,
        status: str,
        detail: str = "",
    ) -> list[dict[str, str]]:
        steps = user.setdefault("troubleshooting_proactive_steps", [])
        if not isinstance(steps, list):
            steps = []
            user["troubleshooting_proactive_steps"] = steps
        steps.append(
            {
                "name": _single_line(name, 40),
                "status": _single_line(status, 16) or "info",
                "detail": _single_line(detail, 180),
            }
        )
        del steps[:-12]
        return steps

    def _record_troubleshooting_proactive_result(
        self,
        user_id: str,
        user: dict[str, Any],
        *,
        ok: bool,
        detail: str,
        error: str = "",
        text: str = "",
        original_text: str = "",
        final_text: str = "",
        action: str = "message",
        reason: str = "check_in",
        extra_count: int = 0,
        diagnostic_detail: str = "",
        pending: bool = False,
        outcome_type: str = "",
    ) -> None:
        raw = self.data.setdefault("troubleshooting_test_results", {})
        if not isinstance(raw, dict):
            raw = {}
            self.data["troubleshooting_test_results"] = raw
        started = _safe_float(user.get("troubleshooting_proactive_started_at"), 0)
        now = _now_ts()
        diagnostic_sanitizer = getattr(self, "_proactive_audit_safe_note", None)
        safe_diagnostic_detail = (
            diagnostic_sanitizer(diagnostic_detail, limit=2400)
            if diagnostic_detail and callable(diagnostic_sanitizer)
            else _single_line(diagnostic_detail, 2400)
        )
        outcome = _single_line(outcome_type, 40).lower()
        if not outcome:
            combined = f"{detail} {error}".lower()
            if pending:
                outcome = "running"
            elif ok:
                outcome = "completed"
            elif "发送失败" in combined or "投递失败" in combined:
                outcome = "delivery_failed"
            elif "final content gate" in combined or "复核" in combined or "校验" in combined:
                outcome = "content_rejected"
            elif "生成" in combined or "llm" in combined:
                outcome = "generation_failed"
            elif "超时" in combined or "到点" in combined or "未启用" in combined:
                outcome = "scheduler_blocked"
            else:
                outcome = "interrupted"
        raw["proactive_message"] = {
            "type": "proactive_message",
            "ok": bool(ok),
            "pending": bool(pending),
            "trace_id": _single_line(user.get("troubleshooting_proactive_test_id"), 32),
            "outcome_type": outcome,
            "title": "主动消息链路测试",
            "umo": _single_line(user.get("umo"), 180),
            "detail": _single_line(detail, 220),
            "error": _single_line(error, 220),
            "diagnostic_detail": safe_diagnostic_detail,
            "text_preview": self._proactive_visible_text_preview(text) if text else "",
            "original_text_preview": self._proactive_visible_text_preview(original_text) if original_text else "",
            "final_text_preview": self._proactive_visible_text_preview(final_text) if final_text else "",
            "action": _single_line(action, 60) or "message",
            "reason": _single_line(reason, 40) or "check_in",
            "extra_count": max(0, int(extra_count or 0)),
            "steps": list(user.get("troubleshooting_proactive_steps") or [])[:12],
            "elapsed_ms": int(max(0.0, now - started) * 1000) if started > 0 else 0,
            "ran_at": now,
            "ran_at_text": self._format_timestamp_elapsed(now),
            "user_id": _single_line(user_id, 80),
        }

    def _restore_troubleshooting_proactive_plan(self, user: dict[str, Any]) -> None:
        restore = user.get("troubleshooting_proactive_restore")
        if isinstance(restore, dict):
            values = restore.get("values")
            if isinstance(values, dict):
                missing = restore.get("missing")
                if isinstance(missing, list):
                    for key in missing:
                        if isinstance(key, str):
                            user.pop(key, None)
                for key, value in values.items():
                    if isinstance(key, str):
                        user[key] = deepcopy(value)
            else:
                for key, value in restore.items():
                    user[key] = deepcopy(value)
        else:
            self._clear_pending_proactive_plan(user)
        user.pop("troubleshooting_proactive_restore", None)
        user.pop("troubleshooting_proactive_test_id", None)
        user.pop("troubleshooting_proactive_started_at", None)
        user.pop("troubleshooting_proactive_steps", None)

    def _recover_stale_troubleshooting_proactive_plans(self) -> int:
        users = self.data.get("users")
        if not isinstance(users, dict):
            return 0
        recovered = 0
        for user_id, user in users.items():
            if not isinstance(user, dict) or not isinstance(user.get("troubleshooting_proactive_restore"), dict):
                continue
            self._append_troubleshooting_proactive_step(user, "启动恢复", "error", "上次排障临时主动未完成，已恢复原计划")
            self._record_troubleshooting_proactive_result(
                str(user_id),
                user,
                ok=False,
                detail="上次排障临时主动任务未完成，插件启动时已恢复原主动计划",
                error="插件重启或任务中断",
                action=str(user.get("planned_proactive_action") or "message"),
                reason=normalize_legacy_tag_text(user.get("planned_proactive_reason")) or "check_in",
            )
            user["proactive_sending"] = False
            user["proactive_sending_started_at"] = 0
            self._restore_troubleshooting_proactive_plan(user)
            recovered += 1
        return recovered

    async def _run_proactive_maintenance_tasks(self) -> None:
        if self._proactive_generation_disabled():
            return
        # 分批轮换：每个 tick 周期只执行约一半维护任务，交错进行，
        # 避免单个周期内串行跑完全部任务拉高瞬时负载；各任务内部自带到期门控。
        tasks = (
            ("技能成长结算", self._maybe_settle_skill_growth),
            ("B站无聊观看", self._maybe_trigger_bilibili_boredom_watch),
            ("网页探索", self._maybe_trigger_web_exploration),
            ("AI日报追踪", self._maybe_track_ai_daily),
            ("新闻无聊阅读", self._maybe_trigger_news_boredom_read),
            ("QQ空间生活说说", self._maybe_publish_qzone_life_post),
            ("QQ空间评论收件箱", self._maybe_process_qzone_comment_inbox),
        )
        batch = getattr(self, "_proactive_maintenance_batch", 0)
        self._proactive_maintenance_batch = 1 - batch
        for index, (label, task_factory) in enumerate(tasks):
            if (index % 2) != batch:
                continue
            try:
                await task_factory()
            except Exception as exc:
                logger.warning("主动维护任务失败,不阻塞私聊主动: %s error=%s", label, _single_line(exc, 160))

    @staticmethod
    def _proactive_send_disables_segmenting(reason: str, *, friend_proactive: bool = False) -> bool:
        # Friend-proactive output has already been planned by its upstream sender.
        # All locally rendered reasons, including creative shares, should respect
        # the user's segmentation settings; media remains atomic in the planner.
        return bool(friend_proactive)
