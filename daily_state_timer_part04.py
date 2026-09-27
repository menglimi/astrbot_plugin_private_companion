# -*- coding: utf-8 -*-
"""DailyStateTimerPart04Mixin。

由 tools/split_mixin_domain.py 从 daily_state_timer.py 机械抽取（1 个方法 + 0 个模块级名字 + 0 个类级赋值 / 276 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 DailyStateTimerMixin）。
"""
from __future__ import annotations

from .daily_state_timer_shared import _now_ts, logger
from .daily_state_timer_shared import Any
from .daily_state_timer_shared import _safe_float
from .daily_state_timer_shared import _safe_int
from .daily_state_timer_shared import _single_line
from .daily_state_timer_shared import deepcopy
from .daily_state_timer_shared import uuid



class DailyStateTimerPart04Mixin:
    """DailyStateTimerPart04Mixin（从 DailyStateTimerMixin 拆出）。"""


    async def _schedule_llm_timer_locked(
        self,
        user_id: str,
        payload: dict[str, Any],
        *,
        source_text: str,
        source_origin: str,
        trigger_message_id: str = "",
        trigger_umo: str = "",
    ) -> None:
        scheduled_ts = max(_now_ts() + 30, _safe_float(payload.get("scheduled_ts"), 0))
        if scheduled_ts <= 0:
            return
        timer_event: dict[str, Any] | None = None
        note = ""
        user_snapshot: dict[str, Any] = {}
        replaced_job_id = ""
        existing_snapshot: dict[str, Any] = {}
        existing_event_id = ""
        operation_id = uuid.uuid4().hex
        async with self._data_lock:
            user = self._get_user(user_id)
            if not self._user_enabled_for_proactive(user_id, user):
                self._clear_pending_proactive_plan(user)
                self._save_data_sync(sections={"users"})
                return
            reason = _single_line(payload.get("reason"), 40) or self._infer_timer_reason(
                scheduled_ts,
                source_text,
            )
            if reason == "activity_followup":
                scheduling_now = _now_ts()
                estimated_minutes = _safe_int(payload.get("estimated_minutes"), 0, 0, 720)
                if estimated_minutes <= 0:
                    estimated_minutes = max(5, min(720, int(round((scheduled_ts - scheduling_now) / 60))))
                followup_policy = self._activity_followup_quota_policy(user)
                completion_buffer_minutes = _safe_int(
                    followup_policy.get("completion_buffer_minutes"),
                    0,
                    0,
                    30,
                )
                scheduled_ts = max(
                    scheduled_ts,
                    scheduling_now + 5 * 60,
                    scheduling_now + (estimated_minutes + completion_buffer_minutes) * 60,
                )
            else:
                estimated_minutes = 0
            action = _single_line(payload.get("action"), 24) or "message"
            if action not in {"message", "screen_peek", "photo_text", "voice"}:
                action = "message"
            if not self._friend_can_receive_proactive_reason(user, reason, action):
                reason = "check_in"
                action = "message"
            topic = _single_line(payload.get("topic"), 60) or self._timer_default_topic(
                reason,
                user,
                source_text,
            )
            motive = _single_line(payload.get("motive"), 140) or self._timer_default_motive(
                reason,
                user,
                source_text=source_text,
                topic=topic,
            )
            existing = user.get("llm_timer_event") if isinstance(user.get("llm_timer_event"), dict) else {}
            existing_active = (
                isinstance(existing, dict)
                and _single_line(existing.get("backend"), 40) == "astrbot_cron"
                and _single_line(existing.get("status"), 40) in {"scheduled", "pending", "registering", "replacing"}
                and bool(_single_line(existing.get("job_id") or existing.get("candidate_job_id"), 80))
            )
            if (
                reason == "activity_followup"
                and existing_active
                and _single_line(existing.get("reason"), 40) != "activity_followup"
            ):
                logger.info(
                    "保留已有明确预约,跳过自动动作查岗: user=%s existing=%s topic=%s",
                    user_id,
                    _single_line(existing.get("reason"), 40) or "appointment",
                    _single_line(existing.get("topic"), 80) or "-",
                )
                return
            if (
                existing_active
            ):
                replaced_job_id = _single_line(existing.get("job_id") or existing.get("candidate_job_id"), 80)
            existing_snapshot = deepcopy(existing) if isinstance(existing, dict) else {}
            existing_event_id = _single_line(existing_snapshot.get("id"), 40)
            activity = _single_line(payload.get("activity"), 60) if reason == "activity_followup" else ""
            followup_intensity = (
                self._activity_followup_intensity_for_user(payload.get("followup_intensity"), user)
                if reason == "activity_followup"
                else 1
            )
            timer_event = {
                "id": uuid.uuid4().hex,
                "scheduled_ts": scheduled_ts,
                "raw_time": _single_line(payload.get("raw_time"), 32),
                "reason": reason,
                "action": action,
                "topic": topic,
                "motive": self._normalize_internal_motive_text(motive),
                "style": _single_line(payload.get("style"), 40),
                "activity": activity,
                "estimated_minutes": estimated_minutes,
                "followup_intensity": followup_intensity,
                "seed_text": _single_line(source_text, 80),
                "origin": source_origin,
                "created_at": _now_ts(),
                "trigger_message_id": _single_line(trigger_message_id, 120),
                "trigger_umo": _single_line(trigger_umo, 160),
                "trigger_ts": _now_ts() if trigger_message_id else 0,
                "chain": list(payload.get("chain") or []) if isinstance(payload.get("chain"), list) else [],
                "silence_until_due": self._timer_source_implies_user_unavailable(source_text, payload),
                "backend": "astrbot_cron",
                "status": "replacing" if replaced_job_id else "registering",
                "operation_id": operation_id,
                "replaced_job_id": replaced_job_id,
                "previous_timer_id": existing_event_id,
            }
            note = self._format_official_timer_note(
                scheduled_ts=scheduled_ts,
                reason=reason,
                action=action,
                topic=topic,
                motive=timer_event["motive"],
                source_text=source_text,
                style=timer_event["style"],
                activity=activity,
                estimated_minutes=estimated_minutes,
                followup_intensity=followup_intensity,
            )
            user_snapshot = dict(user)
            user["llm_timer_event"] = deepcopy(timer_event)
            self._clear_llm_timer_internal_plan_fields(user)
            self._save_data_sync(sections={"users"})

        previous_running_job_id = ""
        if replaced_job_id:
            runtime_supported, runtime_status = await self._official_llm_timer_job_runtime(replaced_job_id)
            if runtime_supported and runtime_status == "running":
                previous_running_job_id = replaced_job_id
                replaced_job_id = ""
            elif runtime_supported and runtime_status in {"completed", "failed", "missing"}:
                replaced_job_id = ""

        job_id, error = await self._add_official_llm_timer_job(
            user_id=user_id,
            user=user_snapshot,
            timer_event=timer_event,
            note=note,
            trigger_umo=trigger_umo,
        )
        if not job_id:
            async with self._data_lock:
                user = self._get_user(user_id)
                current = user.get("llm_timer_event")
                if (
                    isinstance(current, dict)
                    and _single_line(current.get("id"), 40) == _single_line(timer_event.get("id"), 40)
                    and _single_line(current.get("operation_id"), 40) == operation_id
                ):
                    if existing_snapshot:
                        restored = deepcopy(existing_snapshot)
                        restored["last_replace_error"] = error or "新官方任务登记失败"
                        restored["last_replace_failed_at"] = _now_ts()
                        user["llm_timer_event"] = restored
                    else:
                        timer_event["status"] = "failed"
                        timer_event["error"] = error or "官方定时计划登记失败"
                        timer_event.pop("operation_id", None)
                        user["llm_timer_event"] = timer_event
                    self._save_data_sync(sections={"users"})
            logger.warning(
                "LLM 临时预约登记失败,已保留原任务: user=%s old_job=%s error=%s",
                user_id,
                replaced_job_id or previous_running_job_id or "-",
                error or "官方定时计划登记失败",
            )
            return

        async with self._data_lock:
            user = self._get_user(user_id)
            current = user.get("llm_timer_event")
            reservation_current = bool(
                isinstance(current, dict)
                and _single_line(current.get("id"), 40) == _single_line(timer_event.get("id"), 40)
                and _single_line(current.get("operation_id"), 40) == operation_id
            )
            if reservation_current:
                current["candidate_job_id"] = job_id
                self._save_data_sync(sections={"users"})
        if not reservation_current:
            await self._delete_official_llm_timer_job(job_id)
            logger.warning(
                "LLM 临时预约预留已失效,已回收新官方任务: user=%s job=%s",
                user_id,
                job_id,
            )
            return

        replace_error = ""
        rollback_error = ""
        if replaced_job_id:
            replaced_ok, replace_error = await self._delete_official_llm_timer_job(replaced_job_id)
            if not replaced_ok:
                rollback_ok, rollback_error = await self._delete_official_llm_timer_job(job_id)
                async with self._data_lock:
                    user = self._get_user(user_id)
                    current = user.get("llm_timer_event")
                    if (
                        isinstance(current, dict)
                        and _single_line(current.get("id"), 40) == _single_line(timer_event.get("id"), 40)
                        and _single_line(current.get("operation_id"), 40) == operation_id
                    ):
                        if rollback_ok and existing_snapshot:
                            restored = deepcopy(existing_snapshot)
                            restored["last_replace_error"] = replace_error or "旧官方任务删除失败"
                            restored["last_replace_failed_at"] = _now_ts()
                            user["llm_timer_event"] = restored
                        else:
                            current["status"] = "replace_rollback_failed"
                            current["job_id"] = replaced_job_id
                            current["candidate_job_id"] = job_id
                            current["replace_error"] = replace_error or "旧官方任务删除失败"
                            current["rollback_error"] = rollback_error or "新官方任务回滚失败"
                        self._save_data_sync(sections={"users"})
                logger.warning(
                    "LLM 临时预约替换失败,新任务回滚%s: user=%s old_job=%s new_job=%s error=%s rollback_error=%s",
                    "完成" if rollback_ok else "失败",
                    user_id,
                    replaced_job_id,
                    job_id,
                    replace_error or "-",
                    rollback_error or "-",
                )
                return

        async with self._data_lock:
            user = self._get_user(user_id)
            current = user.get("llm_timer_event")
            reservation_current = bool(
                isinstance(current, dict)
                and _single_line(current.get("id"), 40) == _single_line(timer_event.get("id"), 40)
                and _single_line(current.get("operation_id"), 40) == operation_id
            )
            if reservation_current:
                timer_event["job_id"] = job_id
                timer_event["status"] = "scheduled"
                timer_event["note"] = _single_line(note, 220)
                timer_event["replaced_job_id"] = replaced_job_id
                if previous_running_job_id:
                    timer_event["previous_running_job_id"] = previous_running_job_id
                timer_event.pop("candidate_job_id", None)
                timer_event.pop("operation_id", None)
                user["llm_timer_event"] = timer_event
                self._clear_llm_timer_internal_plan_fields(user)
                self._save_data_sync(sections={"users"})
        if not reservation_current:
            await self._delete_official_llm_timer_job(job_id)
            return
        logger.info(
            "LLM 临时预约已转写到官方定时计划: user=%s time=%s reason=%s action=%s topic=%s job=%s replaced=%s error=%s replace_error=%s",
            user_id,
            self._environment_fromtimestamp(scheduled_ts).strftime("%m-%d %H:%M:%S"),
            reason,
            action,
            topic,
            job_id or "-",
            replaced_job_id or "-",
            "-",
            replace_error or "-",
        )
