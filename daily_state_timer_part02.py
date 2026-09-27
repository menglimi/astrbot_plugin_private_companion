# -*- coding: utf-8 -*-
"""DailyStateTimerPart02Mixin。

由 tools/split_mixin_domain.py 从 daily_state_timer.py 机械抽取（18 个方法 + 0 个模块级名字 + 0 个类级赋值 / 369 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 DailyStateTimerMixin）。
"""
from __future__ import annotations

from .daily_state_timer_shared import _now_ts, logger
from .daily_state_timer_shared import Any
from .daily_state_timer_shared import _safe_float
from .daily_state_timer_shared import _safe_int
from .daily_state_timer_shared import _single_line
from .daily_state_timer_shared import asyncio
from .daily_state_timer_shared import datetime
from .daily_state_timer_shared import normalize_legacy_tag_text
from .daily_state_timer_shared import zoneinfo



class DailyStateTimerPart02Mixin:
    """DailyStateTimerPart02Mixin（从 DailyStateTimerMixin 拆出）。"""


    def _clear_llm_timer_internal_plan_fields(self, user: dict[str, Any]) -> None:
        if not isinstance(user, dict):
            return
        if normalize_legacy_tag_text(user.get("planned_proactive_source")) != "timer":
            return
        self._clear_pending_proactive_plan(user)

    def _clear_llm_timer_event(self, user: dict[str, Any], *, event_id: str = "") -> None:
        raw = user.get("llm_timer_event")
        if not isinstance(raw, dict):
            user["llm_timer_event"] = {}
            return
        if event_id and str(raw.get("id") or "") != event_id:
            return
        user["llm_timer_event"] = {}

    def _format_llm_timer_context(self, user: dict[str, Any], *, now: float | None = None) -> str:
        event = self._get_active_llm_timer(user)
        if not isinstance(event, dict):
            return ""
        now = now or _now_ts()
        scheduled_ts = _safe_float(event.get("scheduled_ts"), 0)
        if scheduled_ts <= 0:
            return ""
        summary_parts = ["这是你之前自己留给自己的一个回头时间。"]
        topic = _single_line(event.get("topic"), 36)
        motive = _single_line(event.get("motive"), 60)
        seed = _single_line(event.get("seed_text"), 60)
        if topic:
            summary_parts.append(f"话题线索是“{topic}”。")
        elif seed:
            summary_parts.append(f"当时留下来的那句线索是：{seed}")
        if motive:
            summary_parts.append(f"当时心里的余味：{motive}")
        deferred = event.get("deferred_context")
        if isinstance(deferred, dict) and deferred:
            deferred_topic = _single_line(deferred.get("topic"), 40)
            deferred_motive = _single_line(deferred.get("motive"), 80)
            deferred_reason = _single_line(deferred.get("reason"), 30)
            deferred_text = deferred_topic or deferred_motive or deferred_reason
            if deferred_text:
                summary_parts.append(
                    f"这段静默期间原本还有一个顺带话头被留到了现在：{deferred_text}。"
                    "本次回复必须先完成预约/叫醒本意，再把这个话头当成一句顺带内容自然接上；不要单独展开成长篇。"
                )
        if now < scheduled_ts:
            summary_parts.append(f"现在离约好的时间还差 {self._format_duration_brief(scheduled_ts - now)}。")
        return " ".join(summary_parts)

    def _llm_timer_timezone_name(self) -> str:
        timezone_name = _single_line(getattr(self, "environment_perception_timezone", ""), 64) or "Asia/Shanghai"
        try:
            zoneinfo.ZoneInfo(timezone_name)
            return timezone_name
        except Exception:
            return "Asia/Shanghai"

    def _llm_timer_run_at(self, scheduled_ts: float) -> datetime:
        timezone_name = self._llm_timer_timezone_name()
        try:
            tzinfo = zoneinfo.ZoneInfo(timezone_name)
        except Exception:
            tzinfo = zoneinfo.ZoneInfo("Asia/Shanghai")
        return datetime.fromtimestamp(scheduled_ts, tzinfo)

    def _format_official_timer_note(
        self,
        *,
        scheduled_ts: float,
        reason: str,
        action: str,
        topic: str,
        motive: str,
        source_text: str,
        style: str = "",
        activity: str = "",
        estimated_minutes: int = 0,
        followup_intensity: int = 1,
    ) -> str:
        when = self._environment_fromtimestamp(scheduled_ts).strftime("%Y-%m-%d %H:%M")
        lines = [
            "这是 PrivateCompanion 从聊天中确认出的临时约定。到点后请按约定自然联系用户,不要解释这是定时任务。",
            f"约定时间：{when}",
        ]
        if topic:
            lines.append(f"约定内容：{topic}")
        if motive:
            lines.append(f"补充语境：{motive}")
        if reason:
            lines.append(f"类型：{reason}")
        if reason == "activity_followup":
            lines[0] = "这是 PrivateCompanion 根据用户暂时离开的动作生成的一次动作查岗主动消息。到点后自然联系用户,不要解释定时任务或内部判断。"
            if activity:
                lines.append(f"用户动作：{activity}")
            if estimated_minutes > 0:
                lines.append(f"生成念头时的预计耗时：{estimated_minutes} 分钟")
            lines.append(f"查岗强度：{followup_intensity}/3")
            intensity_rules = {
                1: "轻轻问一句动作是否结束或人是否回来了，不要求立即回复。",
                2: "可以更直接、更有存在感地问一句，但保持亲近和可拒绝。",
                3: "可带符合人格的轻度监督感或小小不满，但不得命令、指责、威胁或连续追发。",
            }
            lines.append(f"表达要求：{intensity_rules.get(followup_intensity, intensity_rules[1])}")
            proactive_voice = ""
            formatter = getattr(self, "_format_proactive_voice_prompt", None)
            if callable(formatter):
                proactive_voice = _single_line(formatter(), 500)
            if proactive_voice:
                lines.append(f"人格化主动风格：{proactive_voice}")
        if style:
            lines.append(f"语气参考：{style}")
        if action and action != "message":
            lines.append(f"期望动作：{action}")
        seed = _single_line(source_text, 180)
        if seed:
            lines.append(f"聊天线索：{seed}")
        lines.append("执行方式：使用 send_message_to_user 给原会话发一条简短自然的消息；如果是叫醒/提醒,直接完成提醒。只发送一次，不因用户未回复而自行追加。")
        return "\n".join(lines)

    def _official_cron_manager(self) -> Any | None:
        context = getattr(self, "context", None)
        manager = getattr(context, "cron_manager", None)
        if manager is not None:
            return manager
        nested = getattr(context, "context", None)
        return getattr(nested, "cron_manager", None)

    def _llm_timer_operation_lock(self, user_id: str) -> asyncio.Lock:
        locks = getattr(self, "_llm_timer_operation_locks", None)
        if not isinstance(locks, dict):
            locks = {}
            setattr(self, "_llm_timer_operation_locks", locks)
        key = _single_line(user_id, 120) or "_unknown"
        lock = locks.get(key)
        if not isinstance(lock, asyncio.Lock):
            lock = asyncio.Lock()
            locks[key] = lock
        return lock

    async def _official_llm_timer_job_runtime(self, job_id: str) -> tuple[bool, str]:
        """Return whether runtime lookup is supported and the current official status."""
        normalized_job_id = _single_line(job_id, 80)
        if not normalized_job_id:
            return True, "missing"
        cron_mgr = self._official_cron_manager()
        if cron_mgr is None:
            return False, ""
        getter = getattr(cron_mgr, "get_job", None)
        if not callable(getter):
            getter = getattr(getattr(cron_mgr, "db", None), "get_cron_job", None)
        if not callable(getter):
            return False, ""
        try:
            job = await getter(normalized_job_id)
        except Exception as exc:
            logger.debug(
                "查询官方定时任务状态失败: job=%s error=%s",
                normalized_job_id,
                _single_line(exc, 160),
            )
            return False, ""
        if job is None:
            return True, "missing"
        return True, _single_line(getattr(job, "status", ""), 40).lower() or "scheduled"

    @staticmethod
    def _official_llm_timer_event_metadata(event: Any) -> dict[str, str]:
        getter = getattr(event, "get_extra", None)
        if not callable(getter):
            return {}
        try:
            payload = getter("cron_payload", {})
            cron_job = getter("cron_job", {})
        except Exception:
            return {}
        if not isinstance(payload, dict) or payload.get("origin") != "private_companion_timer":
            return {}
        private_payload = payload.get("private_companion")
        if not isinstance(private_payload, dict):
            return {}
        timer_id = _single_line(private_payload.get("timer_id"), 40)
        user_id = _single_line(payload.get("sender_id"), 120)
        job_id = _single_line(cron_job.get("id"), 80) if isinstance(cron_job, dict) else ""
        if not timer_id or not user_id:
            return {}
        return {"timer_id": timer_id, "user_id": user_id, "job_id": job_id}

    @staticmethod
    def _official_llm_timer_matches(current: Any, metadata: dict[str, str]) -> bool:
        if not isinstance(current, dict) or not metadata:
            return False
        if _single_line(current.get("backend"), 40) != "astrbot_cron":
            return False
        if _single_line(current.get("id"), 40) != metadata.get("timer_id"):
            return False
        current_job_id = _single_line(current.get("job_id") or current.get("candidate_job_id"), 80)
        event_job_id = metadata.get("job_id", "")
        return not (current_job_id and event_job_id and current_job_id != event_job_id)

    async def _acknowledge_official_llm_timer_trigger(self, event: Any) -> bool:
        metadata = self._official_llm_timer_event_metadata(event)
        if not metadata:
            return False
        user_id = metadata["user_id"]
        async with self._llm_timer_operation_lock(user_id):
            async with self._data_lock:
                users = self.data.get("users")
                current_user = users.get(user_id) if isinstance(users, dict) else None
                current = current_user.get("llm_timer_event") if isinstance(current_user, dict) else None
                if not self._official_llm_timer_matches(current, metadata):
                    return False
                current["status"] = "triggered"
                current["triggered_at"] = _now_ts()
                if metadata.get("job_id"):
                    current["job_id"] = metadata["job_id"]
                    current["cron_job_id"] = metadata["job_id"]
                self._clear_llm_timer_internal_plan_fields(current_user)
                self._save_data_sync(sections={"users"})
        logger.info(
            "官方临时预约开始执行: user=%s timer=%s job=%s",
            user_id,
            metadata["timer_id"],
            metadata.get("job_id") or "-",
        )
        return True

    @staticmethod
    def _official_llm_timer_tool_result_succeeded(tool_result: Any) -> bool:
        if tool_result is None or bool(getattr(tool_result, "isError", False)):
            return False
        content = getattr(tool_result, "content", None)
        if not isinstance(content, list):
            return False
        result_text = "\n".join(
            str(getattr(item, "text", "") or "")
            for item in content
            if getattr(item, "text", None) is not None
        ).strip()
        return result_text.startswith("Message sent to session ")

    async def _record_official_llm_timer_tool_result(
        self,
        event: Any,
        tool: Any,
        tool_result: Any,
    ) -> bool:
        if _single_line(getattr(tool, "name", ""), 80) != "send_message_to_user":
            return False
        metadata = self._official_llm_timer_event_metadata(event)
        if not metadata:
            return False
        succeeded = self._official_llm_timer_tool_result_succeeded(tool_result)
        user_id = metadata["user_id"]
        async with self._llm_timer_operation_lock(user_id):
            async with self._data_lock:
                users = self.data.get("users")
                current_user = users.get(user_id) if isinstance(users, dict) else None
                current = current_user.get("llm_timer_event") if isinstance(current_user, dict) else None
                if not self._official_llm_timer_matches(current, metadata):
                    return False
                current["status"] = "delivered" if succeeded else "delivery_failed"
                current["delivery_at"] = _now_ts()
                current["delivery_error"] = "" if succeeded else "send_message_to_user 未确认发送成功"
                self._save_data_sync(sections={"users"})
        return True

    async def _complete_official_llm_timer_event(self, event: Any) -> bool:
        metadata = self._official_llm_timer_event_metadata(event)
        if not metadata:
            return False
        user_id = metadata["user_id"]
        async with self._llm_timer_operation_lock(user_id):
            async with self._data_lock:
                users = self.data.get("users")
                current_user = users.get(user_id) if isinstance(users, dict) else None
                current = current_user.get("llm_timer_event") if isinstance(current_user, dict) else None
                if not self._official_llm_timer_matches(current, metadata):
                    return False
                status = _single_line(current.get("status"), 40)
                if status == "delivered":
                    current["status"] = "completed"
                    current["delivery_status"] = "sent"
                elif status == "triggered":
                    current["status"] = "completed_without_delivery"
                    current["delivery_status"] = "not_confirmed"
                elif status == "delivery_failed":
                    current["delivery_status"] = "failed"
                else:
                    return False
                current["completed_at"] = _now_ts()
                self._save_data_sync(sections={"users"})
        return True

    def _expire_stale_official_llm_timers_locked(self, *, now: float | None = None) -> int:
        check_now = _now_ts() if now is None else now
        users = self.data.get("users")
        if not isinstance(users, dict):
            return 0
        changed = 0
        for user in users.values():
            if not isinstance(user, dict):
                continue
            timer = user.get("llm_timer_event")
            if not isinstance(timer, dict) or _single_line(timer.get("backend"), 40) != "astrbot_cron":
                continue
            status = _single_line(timer.get("status"), 40)
            scheduled_ts = _safe_float(timer.get("scheduled_ts"), 0)
            triggered_at = _safe_float(timer.get("triggered_at"), 0)
            if status in {"pending", "registering", "replacing", "scheduled"} and scheduled_ts > 0 and check_now - scheduled_ts > 30 * 60:
                timer["status"] = "expired_unconfirmed"
                timer["expired_at"] = check_now
                timer["error"] = "官方任务已过期，但插件未收到执行回执"
                changed += 1
            elif status == "triggered" and triggered_at > 0 and check_now - triggered_at > 2 * 3600:
                timer["status"] = "triggered_unconfirmed"
                timer["expired_at"] = check_now
                timer["error"] = "官方任务已开始，但插件未收到完成回执"
                changed += 1
        return changed

    async def _add_official_llm_timer_job(
        self,
        *,
        user_id: str,
        user: dict[str, Any],
        timer_event: dict[str, Any],
        note: str,
        trigger_umo: str,
    ) -> tuple[str, str]:
        cron_mgr = self._official_cron_manager()
        if cron_mgr is None:
            return "", "AstrBot 官方定时计划不可用"
        scheduled_ts = _safe_float(timer_event.get("scheduled_ts"), 0)
        if scheduled_ts <= 0:
            return "", "预约时间无效"
        run_at = self._llm_timer_run_at(scheduled_ts)
        session = _single_line(trigger_umo, 180) or _single_line(user.get("umo"), 180)
        if not session:
            return "", "缺少私聊会话"
        payload = {
            "session": session,
            "sender_id": str(user_id),
            "note": note,
            "origin": "private_companion_timer",
            "private_companion": {
                "timer_id": _single_line(timer_event.get("id"), 40),
                "reason": _single_line(timer_event.get("reason"), 40),
                "action": _single_line(timer_event.get("action"), 40),
                "topic": _single_line(timer_event.get("topic"), 80),
                "activity": _single_line(timer_event.get("activity"), 60),
                "estimated_minutes": _safe_int(timer_event.get("estimated_minutes"), 0, 0, 720),
                "followup_intensity": _safe_int(timer_event.get("followup_intensity"), 1, 1, 3),
            },
        }
        try:
            job = await cron_mgr.add_active_job(
                name=(
                    "PrivateCompanion 动作查岗"
                    if _single_line(timer_event.get("reason"), 40) == "activity_followup"
                    else "PrivateCompanion 临时约定"
                ),
                cron_expression=None,
                payload=payload,
                description=_single_line(timer_event.get("topic") or note, 180),
                timezone=self._llm_timer_timezone_name(),
                enabled=True,
                persistent=True,
                run_once=True,
                run_at=run_at,
            )
        except Exception as exc:
            return "", _single_line(exc, 180) or repr(exc)
        return _single_line(getattr(job, "job_id", ""), 80), ""

    async def _delete_official_llm_timer_job(self, job_id: str) -> tuple[bool, str]:
        normalized_job_id = _single_line(job_id, 80)
        if not normalized_job_id:
            return False, "缺少官方任务 ID"
        cron_mgr = self._official_cron_manager()
        if cron_mgr is None:
            return False, "AstrBot 官方定时计划不可用"
        try:
            await cron_mgr.delete_job(normalized_job_id)
        except Exception as exc:
            return False, _single_line(exc, 180) or repr(exc)
        return True, ""
