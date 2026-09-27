# -*- coding: utf-8 -*-
"""DailyStateTimerPart03Mixin。

由 tools/split_mixin_domain.py 从 daily_state_timer.py 机械抽取（5 个方法 + 0 个模块级名字 + 0 个类级赋值 / 241 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 DailyStateTimerMixin）。
"""
from __future__ import annotations

from .daily_state_timer_shared import _now_ts, logger
from .daily_state_timer_shared import Any
from .daily_state_timer_shared import _safe_float
from .daily_state_timer_shared import _single_line
from .daily_state_timer_shared import asyncio
from .daily_state_timer_shared import deepcopy
from .daily_state_timer_shared import uuid



class DailyStateTimerPart03Mixin:
    """DailyStateTimerPart03Mixin（从 DailyStateTimerMixin 拆出）。"""


    async def _cancel_llm_timer(
        self,
        user_id: str,
        payload: dict[str, Any],
        *,
        source_text: str,
        source_origin: str,
        trigger_message_id: str = "",
        trigger_umo: str = "",
    ) -> bool:
        normalized_user_id = _single_line(user_id, 120)
        async with self._llm_timer_operation_lock(normalized_user_id):
            now_ts = _now_ts()
            async with self._data_lock:
                user = self._get_user(normalized_user_id)
                existing_raw = user.get("llm_timer_event")
                existing = deepcopy(existing_raw) if isinstance(existing_raw, dict) else {}
                expected_event_id = _single_line(payload.get("_expected_event_id"), 40)
                existing_event_id = _single_line(existing.get("id"), 40)
                if expected_event_id and existing_event_id != expected_event_id:
                    return False
                existing_status = _single_line(existing.get("status"), 40)
                existing_job_id = _single_line(
                    existing.get("job_id") or existing.get("candidate_job_id"),
                    80,
                )
                existing_active = (
                    _single_line(existing.get("backend"), 40) == "astrbot_cron"
                    and existing_status in {"pending", "registering", "replacing", "scheduled"}
                    and bool(existing_job_id)
                )
                if not existing_active:
                    if expected_event_id:
                        return False
                    user["llm_timer_event"] = {
                        "id": uuid.uuid4().hex,
                        "scheduled_ts": _safe_float(existing.get("scheduled_ts"), 0) or now_ts,
                        "action": "cancel",
                        "topic": _single_line(payload.get("topic") or "取消临时约定", 60),
                        "motive": _single_line(source_text, 140),
                        "origin": source_origin,
                        "created_at": now_ts,
                        "backend": "astrbot_cron",
                        "status": "cancel_skipped",
                        "error": "没有可取消的对话临时预约",
                    }
                    self._save_data_sync(sections={"users"})
                    return False

            runtime_supported, runtime_status = await self._official_llm_timer_job_runtime(existing_job_id)
            if runtime_supported and runtime_status in {"running", "completed", "failed", "missing"}:
                async with self._data_lock:
                    user = self._get_user(normalized_user_id)
                    current = user.get("llm_timer_event")
                    if not isinstance(current, dict) or _single_line(current.get("id"), 40) != existing_event_id:
                        return False
                    if _single_line(current.get("job_id") or current.get("candidate_job_id"), 80) != existing_job_id:
                        return False
                    if runtime_status == "running":
                        current["status"] = "triggered"
                        current["triggered_at"] = _safe_float(current.get("triggered_at"), 0) or now_ts
                        current["cancel_status"] = "too_late"
                        current["cancel_error"] = "官方任务已经开始执行，无法确认取消"
                    else:
                        current["status"] = "expired_unconfirmed"
                        current["cancel_status"] = "not_found"
                        current["cancel_error"] = "官方任务已结束或不存在，无法确认取消"
                    current.pop("cancel_requested_at", None)
                    self._save_data_sync(sections={"users"})
                return False

            async with self._data_lock:
                user = self._get_user(normalized_user_id)
                current = user.get("llm_timer_event")
                if not isinstance(current, dict) or _single_line(current.get("id"), 40) != existing_event_id:
                    return False
                if _single_line(current.get("job_id") or current.get("candidate_job_id"), 80) != existing_job_id:
                    return False
                current["status"] = "cancel_pending"
                current["cancel_requested_at"] = now_ts
                current["cancel_origin"] = source_origin
                current["cancel_topic"] = _single_line(payload.get("topic") or "取消临时约定", 60)
                current["cancel_source_text"] = _single_line(source_text, 140)
                self._save_data_sync(sections={"users"})

            ok, error = await self._delete_official_llm_timer_job(existing_job_id)
            async with self._data_lock:
                user = self._get_user(normalized_user_id)
                current = user.get("llm_timer_event")
                if not isinstance(current, dict) or _single_line(current.get("id"), 40) != existing_event_id:
                    return False
                if _single_line(current.get("job_id") or current.get("candidate_job_id"), 80) != existing_job_id:
                    return False
                if ok:
                    current["status"] = "cancelled"
                    current["cancelled_at"] = _now_ts()
                    current["cancelled_job_id"] = existing_job_id
                    current["cancel_status"] = "cancelled"
                    current["error"] = ""
                    current.pop("cancel_requested_at", None)
                    self._clear_llm_timer_internal_plan_fields(user)
                else:
                    restored = deepcopy(existing)
                    restored["cancel_status"] = "failed"
                    restored["cancel_error"] = error or "官方任务删除失败"
                    restored["cancel_failed_at"] = _now_ts()
                    restored.pop("cancel_requested_at", None)
                    user["llm_timer_event"] = restored
                self._save_data_sync(sections={"users"})
            logger.info(
                "对话临时预约取消%s: user=%s job=%s error=%s",
                "完成" if ok else "失败",
                normalized_user_id,
                existing_job_id,
                error or "-",
            )
            return ok

    def _queue_official_llm_timer_cancel(
        self,
        user_id: str,
        timer_event: dict[str, Any],
        *,
        source_text: str,
        source_origin: str,
        trigger_umo: str = "",
    ) -> bool:
        if not isinstance(timer_event, dict) or _single_line(timer_event.get("backend"), 40) != "astrbot_cron":
            return False
        if _single_line(timer_event.get("status"), 40) not in {"pending", "registering", "replacing", "scheduled"}:
            return False
        timer_id = _single_line(timer_event.get("id"), 40)
        normalized_user_id = _single_line(user_id or timer_event.get("user_id"), 120)
        if not timer_id or not normalized_user_id or _safe_float(timer_event.get("cancel_requested_at"), 0) > 0:
            return False
        timer_event["cancel_requested_at"] = _now_ts()
        operation = self._cancel_llm_timer(
            normalized_user_id,
            {
                "cancel": True,
                "topic": "用户已在问候时段自然出现，取消冲突问候",
                "_expected_event_id": timer_id,
            },
            source_text=source_text,
            source_origin=source_origin,
            trigger_umo=trigger_umo,
        )
        creator = getattr(self, "_create_lifecycle_background_task", None)
        try:
            if callable(creator):
                task = creator(operation, label=f"official_timer_cancel:{normalized_user_id}")
            else:
                task = asyncio.create_task(operation)
        except Exception:
            try:
                operation.close()
            except Exception:
                pass
            timer_event.pop("cancel_requested_at", None)
            return False
        if task is None:
            try:
                operation.close()
            except Exception:
                pass
            timer_event.pop("cancel_requested_at", None)
            return False
        return True

    def _has_active_activity_followup_timer(
        self,
        user: dict[str, Any] | None,
        *,
        trigger_message_id: str = "",
    ) -> bool:
        if not isinstance(user, dict):
            return False
        event = self._get_active_llm_timer(user)
        if not isinstance(event, dict) or _single_line(event.get("reason"), 40) != "activity_followup":
            return False
        current_message_id = _single_line(trigger_message_id, 120)
        original_message_id = _single_line(event.get("trigger_message_id"), 120)
        return not (current_message_id and original_message_id and current_message_id == original_message_id)

    async def _cancel_activity_followup_on_user_return(
        self,
        user_id: str,
        *,
        trigger_message_id: str = "",
        trigger_umo: str = "",
        source_text: str = "",
    ) -> bool:
        async with self._data_lock:
            user = self._get_user(user_id)
            should_cancel = self._has_active_activity_followup_timer(
                user,
                trigger_message_id=trigger_message_id,
            )
            event = self._get_active_llm_timer(user) if should_cancel else None
            expected_event_id = _single_line((event or {}).get("id"), 40)
        if not should_cancel:
            return False
        await self._cancel_llm_timer(
            user_id,
            {
                "cancel": True,
                "topic": "用户已提前回来，取消动作查岗",
                "_expected_event_id": expected_event_id,
            },
            source_text=_single_line(source_text, 140) or "用户在动作查岗到点前发来了新消息",
            source_origin="user_returned_before_activity_followup",
            trigger_message_id=trigger_message_id,
            trigger_umo=trigger_umo,
        )
        return True

    async def _schedule_llm_timer(
        self,
        user_id: str,
        payload: dict[str, Any],
        *,
        source_text: str,
        source_origin: str,
        trigger_message_id: str = "",
        trigger_umo: str = "",
    ) -> None:
        if bool(payload.get("cancel")):
            await self._cancel_llm_timer(
                user_id,
                payload,
                source_text=source_text,
                source_origin=source_origin,
                trigger_message_id=trigger_message_id,
                trigger_umo=trigger_umo,
            )
            return
        async with self._llm_timer_operation_lock(user_id):
            await self._schedule_llm_timer_locked(
                user_id,
                payload,
                source_text=source_text,
                source_origin=source_origin,
                trigger_message_id=trigger_message_id,
                trigger_umo=trigger_umo,
            )
