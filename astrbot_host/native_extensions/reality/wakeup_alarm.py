# -*- coding: utf-8 -*-
"""现实触及提醒、授权和设备交付。"""

from __future__ import annotations

import asyncio
import copy
import re
import uuid
import zoneinfo
from datetime import datetime, timedelta
from typing import Any

from astrbot.api import logger

from .helpers import _now_ts, _safe_int, _single_line
from .reality_touch_audio import RealityTouchAudioMixin
from .reality_touch_camera import RealityTouchCameraMixin


class WakeupAlarmMixin(RealityTouchCameraMixin, RealityTouchAudioMixin):
    """Schedule local TTS contact without coupling to a Bluetooth vendor API."""

    _WAKEUP_DEFAULT_MESSAGE = "早上好，该起床啦。先坐起来喝口水，再慢慢开始今天吧。"
    _WAKEUP_DYNAMIC_MESSAGE_HINT = "每次触发时按人格、关系与当天语境动态生成"
    _REALITY_TOUCH_CONSENT_VERSION = 1
    _REALITY_TOUCH_CONFIRMATION_TEXT = "我理解风险并确认授权"
    _WAKEUP_CONTACT_ACTIVE_STATES = {"pending", "playing", "snoozed"}
    _WAKEUP_DELIVERY_MODES = {"audio_only", "audio_and_chat", "chat_on_failure"}

    def _reality_touch_consent(self, user: dict[str, Any]) -> dict[str, Any]:
        consent = user.get("reality_touch_consent")
        return consent if isinstance(consent, dict) else {}

    def _reality_touch_capability_consented(self, user: dict[str, Any], capability: str) -> bool:
        consent = self._reality_touch_consent(user)
        capabilities = consent.get("granted_capabilities")
        return (
            consent.get("confirmed") is True
            and _safe_int(consent.get("version"), 0, 0) >= self._REALITY_TOUCH_CONSENT_VERSION
            and isinstance(capabilities, list)
            and capability in capabilities
        )

    def _reality_touch_audio_consented(self, user: dict[str, Any]) -> bool:
        return self._reality_touch_capability_consented(user, "local_audio")

    def _reality_touch_confirmation_prompt(self) -> str:
        return (
            "现实触及会让插件主动调用本机设备。当前音频能力使用管理员选择的电脑输出设备；"
            "摄像头是另一项独立能力，必须再次单独确认，不会沿用本次授权。\n"
            "风险说明展示后，用户本人只需在 10 分钟内单独发送：\n"
            f"{self._REALITY_TOUCH_CONFIRMATION_TEXT}"
        )

    @staticmethod
    def _reality_touch_confirmation_valid(text: str) -> bool:
        compact = re.sub(r"[\s，,。.!！;；:：]+", "", str(text or ""))
        return compact == "我理解风险并确认授权"

    def _reality_touch_apply_pending_confirmation(self, user: dict[str, Any], text: str) -> str | None:
        if not self._reality_touch_confirmation_valid(text):
            return None
        pending = user.get("reality_touch_pending_consent")
        if not isinstance(pending, dict):
            return None
        if _safe_int(pending.get("expires_at"), 0, 0) < _now_ts():
            user.pop("reality_touch_pending_consent", None)
            self._save_data_sync()
            return "这次授权确认已经过期，请重新打开对应能力的风险说明。"
        capability = _single_line(pending.get("capability"), 40)
        if capability == self._REALITY_TOUCH_CAMERA_CAPABILITY:
            reply, _ = self._reality_touch_camera_command(
                user,
                f"摄像头确认 {self._REALITY_TOUCH_CAMERA_CONFIRMATION_TEXT}",
            )
            return reply
        if capability == "local_audio":
            reply, _ = self._wakeup_alarm_command(user, f"确认 {self._REALITY_TOUCH_CONFIRMATION_TEXT}")
            return reply
        user.pop("reality_touch_pending_consent", None)
        self._save_data_sync()
        return "没有找到可确认的现实触及能力，请重新打开对应风险说明。"

    @staticmethod
    def _wakeup_parse_time(value: Any) -> str:
        text = str(value or "").strip().replace("：", ":")
        match = re.fullmatch(r"(\d{1,2}):(\d{1,2})", text)
        if not match:
            return ""
        hour, minute = int(match.group(1)), int(match.group(2))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return ""
        return f"{hour:02d}:{minute:02d}"

    @staticmethod
    def _wakeup_days(value: Any) -> list[int]:
        if isinstance(value, list):
            raw = value
        else:
            raw = re.split(r"[,，、\s]+", str(value or ""))
        aliases = {
            "一": 0, "周一": 0, "星期一": 0, "1": 0,
            "二": 1, "周二": 1, "星期二": 1, "2": 1,
            "三": 2, "周三": 2, "星期三": 2, "3": 2,
            "四": 3, "周四": 3, "星期四": 3, "4": 3,
            "五": 4, "周五": 4, "星期五": 4, "5": 4,
            "六": 5, "周六": 5, "星期六": 5, "6": 5,
            "日": 6, "天": 6, "周日": 6, "星期日": 6, "周天": 6, "星期天": 6, "7": 6,
        }
        days = []
        for item in raw:
            if isinstance(item, int) and 0 <= item <= 6:
                days.append(item)
                continue
            key = str(item or "").strip()
            if key in {"每天", "全周", "每日"}:
                return list(range(7))
            if key in aliases:
                days.append(aliases[key])
        return sorted(set(days))

    def _wakeup_alarm_for_user(self, user: dict[str, Any]) -> dict[str, Any]:
        alarm = user.get("wakeup_alarm")
        if not isinstance(alarm, dict):
            alarm = {}
            user["wakeup_alarm"] = alarm
        return alarm

    @staticmethod
    def _wakeup_contact_session(alarm: dict[str, Any]) -> dict[str, Any]:
        session = alarm.get("contact_session")
        return session if isinstance(session, dict) else {}

    def _wakeup_delivery_mode(self, alarm: dict[str, Any]) -> str:
        mode = _single_line(alarm.get("delivery_mode"), 32).lower()
        return mode if mode in self._WAKEUP_DELIVERY_MODES else "chat_on_failure"

    def _wakeup_attempt_volume(self, alarm: dict[str, Any], attempt: int) -> int:
        default_volume = self._reality_touch_playback_volume()
        start = _safe_int(alarm.get("playback_volume"), default_volume, 0, 100)
        step = _safe_int(alarm.get("volume_step"), 8, 0, 30)
        maximum = _safe_int(alarm.get("max_volume"), max(start, 70), 0, 100)
        maximum = max(start, maximum)
        return min(maximum, start + max(0, int(attempt) - 1) * step)

    def _wakeup_contact_task_registry(self) -> dict[str, asyncio.Task]:
        registry = getattr(self, "_wakeup_contact_tasks", None)
        if not isinstance(registry, dict):
            registry = {}
            self._wakeup_contact_tasks = registry
        return registry

    @staticmethod
    def _reality_touch_reminder_registry(user: dict[str, Any]) -> dict[str, dict[str, Any]]:
        reminders = user.get("reality_touch_reminders")
        if not isinstance(reminders, dict):
            reminders = {}
            user["reality_touch_reminders"] = reminders
        return reminders

    @staticmethod
    def _official_reality_touch_metadata(event: Any) -> dict[str, str]:
        getter = getattr(event, "get_extra", None)
        if not callable(getter):
            return {}
        try:
            payload = getter("cron_payload", {})
            cron_job = getter("cron_job", {})
        except Exception:
            return {}
        if not isinstance(payload, dict) or payload.get("origin") not in {
            "companion_v2.reality",
            "reality_companion",
            "private_companion_reality_touch",
        }:
            return {}
        private_payload = payload.get("reality_v2") or payload.get("reality_companion") or payload.get("private_companion")
        if not isinstance(private_payload, dict):
            return {}
        reminder_id = _single_line(private_payload.get("reminder_id"), 40)
        user_id = _single_line(payload.get("sender_id"), 120)
        job_id = _single_line(cron_job.get("id"), 80) if isinstance(cron_job, dict) else ""
        if not reminder_id or not user_id:
            return {}
        return {"reminder_id": reminder_id, "user_id": user_id, "job_id": job_id}

    @staticmethod
    def _official_reality_touch_matches(reminder: Any, metadata: dict[str, str]) -> bool:
        if not isinstance(reminder, dict) or not metadata:
            return False
        if _single_line(reminder.get("id"), 40) != metadata.get("reminder_id"):
            return False
        current_job_id = _single_line(reminder.get("job_id"), 80)
        event_job_id = metadata.get("job_id", "")
        return not (current_job_id and event_job_id and current_job_id != event_job_id)

    @staticmethod
    def _prune_reality_touch_reminders(reminders: dict[str, dict[str, Any]]) -> None:
        if len(reminders) <= 48:
            return
        inactive = sorted(
            (
                item
                for item in reminders.items()
                if _single_line(item[1].get("status"), 32)
                not in {"registering", "scheduled", "triggered", "delivering"}
            ),
            key=lambda item: _safe_int(item[1].get("created_at"), 0, 0),
        )
        for reminder_id, _ in inactive[: max(0, len(reminders) - 40)]:
            reminders.pop(reminder_id, None)

    async def _schedule_reality_touch_official_reminder(
        self,
        user_id: str,
        payload: dict[str, Any],
        *,
        source_text: str,
        trigger_umo: str = "",
    ) -> bool:
        if bool(payload.get("cancel")):
            return await self._cancel_reality_touch_official_reminder(
                user_id,
                reminder_id=_single_line(payload.get("reminder_id"), 40),
                topic=_single_line(payload.get("topic"), 80),
            )
        if not bool(getattr(self, "enable_experimental_bluetooth_wakeup", False)):
            logger.warning("[PrivateCompanion] 现实触及总开关关闭，未创建官方提醒: user=%s", user_id)
            return False
        scheduled_ts = _safe_int(payload.get("scheduled_ts"), 0, 0)
        if scheduled_ts <= _now_ts():
            return False
        cron_mgr = getattr(self, "_official_cron_manager", lambda: None)()
        if cron_mgr is None:
            logger.warning("[PrivateCompanion] AstrBot 官方 Cron 不可用，未创建现实触及提醒")
            return False

        identity = self.v2_host_api().reality_mobile_context(str(user_id))
        if not identity or not identity.get("context_key"):
            return False

        reminder_id = uuid.uuid4().hex
        async with self._data_lock:
            users = self.data.get("users", {}) if isinstance(self.data, dict) else {}
            user = users.get(str(user_id)) if isinstance(users, dict) else None
            if not isinstance(user, dict) or not self._reality_touch_audio_consented(user):
                return False
            topic = _single_line(payload.get("topic") or source_text, 240) or "到点提醒"
            alarm = user.get("wakeup_alarm") if isinstance(user.get("wakeup_alarm"), dict) else {}
            requested_mode = _single_line(payload.get("delivery_mode"), 32).lower()
            delivery_mode = requested_mode if requested_mode in self._WAKEUP_DELIVERY_MODES else self._wakeup_delivery_mode(alarm)
            requested_volume = _safe_int(payload.get("playback_volume"), -1, -1, 100)
            requested_fade = _safe_int(payload.get("fade_in_ms"), -1, -1, 5000)
            reminder = {
                "id": reminder_id,
                "request_key": _single_line(payload.get("request_key"), 120),
                "backend": "astrbot_cron",
                "status": "registering",
                "scheduled_ts": scheduled_ts,
                "topic": topic,
                "source_text": _single_line(source_text, 240),
                "delivery_mode": delivery_mode,
                "playback_volume": (
                    requested_volume
                    if requested_volume >= 0
                    else _safe_int(alarm.get("playback_volume"), self._reality_touch_playback_volume(), 0, 100)
                ),
                "fade_in_ms": (
                    requested_fade
                    if requested_fade >= 0
                    else _safe_int(alarm.get("fade_in_ms"), 800, 0, 5000)
                ),
                "created_at": _now_ts(),
                "context_key": identity["context_key"],
                "umo": _single_line(trigger_umo, 180) or _single_line(user.get("umo"), 180),
            }
            if not reminder["umo"]:
                return False
            reminders = self._reality_touch_reminder_registry(user)
            reminders[reminder_id] = reminder
            self._prune_reality_touch_reminders(reminders)
            self._save_data_sync()

        when = self._environment_fromtimestamp(scheduled_ts).strftime("%Y-%m-%d %H:%M")
        note = (
            "这是用户明确要求由 V2 原生现实触及能力交付的提醒。"
            "到点后根据人格和关系，把提醒事项改写成一到两句自然、准确的话，然后只调用一次 "
            "pc_reality_touch_reminder(text=最终提醒文本)。"
            "不要调用 send_message_to_user、future_task 或其他 TTS 工具，不要自行追加第二次提醒；"
            "专用工具会按用户设置处理本机音频、设备回退和聊天副本。\n"
            f"提醒时间：{when}\n提醒事项：{reminder['topic']}\n"
            "不要声称看见或监听用户，不编造任务之外的事实。"
        )
        try:
            job = await cron_mgr.add_active_job(
                name="V2 现实触及提醒",
                cron_expression=None,
                payload={
                    "session": reminder["umo"],
                    "sender_id": str(user_id),
                    "note": note,
                    "origin": "companion_v2.reality",
                    "reality_v2": {
                        "reminder_id": reminder_id,
                        "topic": reminder["topic"],
                        "delivery_mode": reminder["delivery_mode"],
                    },
                },
                description=_single_line(reminder["topic"], 180),
                timezone=self._llm_timer_timezone_name(),
                enabled=True,
                persistent=True,
                run_once=True,
                run_at=self._llm_timer_run_at(scheduled_ts),
            )
            job_id = _single_line(getattr(job, "job_id", ""), 80)
            if not job_id:
                raise RuntimeError("官方 Cron 未返回任务 ID")
        except Exception as exc:
            async with self._data_lock:
                user = self.data.get("users", {}).get(str(user_id))
                current = self._reality_touch_reminder_registry(user).get(reminder_id) if isinstance(user, dict) else None
                if isinstance(current, dict) and _single_line(current.get("status"), 32) == "registering":
                    current.update({"status": "failed", "error": _single_line(exc, 180)})
                    self._save_data_sync()
            logger.warning("[PrivateCompanion] 现实触及官方提醒创建失败: %s", _single_line(exc, 180))
            return False

        should_reclaim_job = False
        async with self._data_lock:
            user = self.data.get("users", {}).get(str(user_id))
            current = self._reality_touch_reminder_registry(user).get(reminder_id) if isinstance(user, dict) else None
            should_reclaim_job = (
                not isinstance(current, dict)
                or _single_line(current.get("status"), 32) != "registering"
            )
            if not should_reclaim_job:
                current.update({"status": "scheduled", "job_id": job_id, "registered_at": _now_ts()})
                self._save_data_sync()
        if should_reclaim_job:
            await self._delete_official_llm_timer_job(job_id)
            return False
        logger.info(
            "[PrivateCompanion] 现实触及提醒已注册到官方 Cron: user=%s reminder=%s job=%s time=%s",
            user_id,
            reminder_id,
            job_id,
            when,
        )
        return True

    async def _cancel_reality_touch_official_reminder(
        self,
        user_id: str,
        *,
        reminder_id: str = "",
        topic: str = "",
    ) -> bool:
        async with self._data_lock:
            user = self.data.get("users", {}).get(str(user_id)) if isinstance(self.data, dict) else None
            if not isinstance(user, dict):
                return False
            reminders = self._reality_touch_reminder_registry(user)
            candidates = [
                item
                for item in reminders.values()
                if _single_line(item.get("status"), 32) in {"registering", "scheduled", "triggered"}
                and (not reminder_id or _single_line(item.get("id"), 40) == reminder_id)
                and (not topic or topic in _single_line(item.get("topic"), 240))
            ]
            if not candidates:
                return False
            target = max(candidates, key=lambda item: _safe_int(item.get("scheduled_ts"), 0, 0))
            target_id = _single_line(target.get("id"), 40)
            job_id = _single_line(target.get("job_id"), 80)
            target["status"] = "cancel_pending"
            self._save_data_sync()
        if not job_id:
            ok, error = True, ""
        else:
            ok, error = await self._delete_official_llm_timer_job(job_id)
        async with self._data_lock:
            user = self.data.get("users", {}).get(str(user_id))
            current = self._reality_touch_reminder_registry(user).get(target_id) if isinstance(user, dict) else None
            if isinstance(current, dict):
                current.update(
                    {
                        "status": "cancelled" if ok else "scheduled",
                        "cancelled_at": _now_ts() if ok else 0,
                        "cancel_error": "" if ok else _single_line(error, 180),
                    }
                )
                self._save_data_sync()
        return bool(ok)

    async def _acknowledge_official_reality_touch_trigger(self, event: Any) -> bool:
        metadata = self._official_reality_touch_metadata(event)
        if not metadata:
            return False
        async with self._data_lock:
            user = self.data.get("users", {}).get(metadata["user_id"]) if isinstance(self.data, dict) else None
            reminder = self._reality_touch_reminder_registry(user).get(metadata["reminder_id"]) if isinstance(user, dict) else None
            if not self._official_reality_touch_matches(reminder, metadata):
                return False
            if _single_line(reminder.get("status"), 32) not in {"scheduled", "triggered"}:
                return False
            reminder.update({"status": "triggered", "triggered_at": _now_ts()})
            if metadata.get("job_id"):
                reminder["job_id"] = metadata["job_id"]
            self._save_data_sync()
        try:
            setattr(event, "companion_v2_reality_reminder_id", metadata["reminder_id"])
        except Exception:
            pass
        return True

    async def _execute_official_reality_touch_reminder(self, event: Any, text: str) -> tuple[bool, str]:
        metadata = self._official_reality_touch_metadata(event)
        if not metadata:
            return False, "当前事件不是 RealityCompanion 现实触及官方任务"
        async with self._data_lock:
            user = self.data.get("users", {}).get(metadata["user_id"]) if isinstance(self.data, dict) else None
            reminder = self._reality_touch_reminder_registry(user).get(metadata["reminder_id"]) if isinstance(user, dict) else None
            if not self._official_reality_touch_matches(reminder, metadata):
                return False, "现实触及提醒记录与官方任务不匹配"
            if _safe_int(reminder.get("delivery_at"), 0, 0) > 0:
                delivered = bool(reminder.get("audio_success") or reminder.get("chat_success"))
                return delivered, "该现实触及提醒已经执行，已跳过重复调用"
            if _safe_int(reminder.get("tool_called_at"), 0, 0) > 0:
                return False, "该现实触及提醒正在执行，已跳过重复调用"
            if (reminder.get("status") not in {"scheduled", "triggered"}
                    or reminder.get("paused_by_v2")):
                return False, "该现实触及提醒已取消、暂停或不再有效"
            identity = self.v2_host_api().reality_mobile_context(metadata["user_id"])
            if (not identity or (reminder.get("context_key")
                    and reminder["context_key"] != identity.get("context_key"))):
                return False, "现实触及提醒所属的 V2 私聊窗口已改变"
            reminder_snapshot = copy.deepcopy(reminder)
            user_snapshot = copy.deepcopy(user)
            reminder.update({"status": "delivering", "tool_called_at": _now_ts()})
            self._save_data_sync()

        reminder_text = _single_line(text, 500) or _single_line(reminder_snapshot.get("topic"), 240)
        if not reminder_text:
            return False, "提醒文本为空"
        consented = bool(
            getattr(self, "enable_experimental_bluetooth_wakeup", False)
            and self._reality_touch_audio_consented(user_snapshot)
        )
        volume = _safe_int(
            reminder_snapshot.get("playback_volume"),
            self._reality_touch_playback_volume(),
            0,
            100,
        )
        fade_in_ms = _safe_int(reminder_snapshot.get("fade_in_ms"), 800, 0, 5000)
        audio_success = False
        if consented:
            audio_success = await self._play_reality_touch_text(
                reminder_text,
                repeat=1,
                interval=20,
                volume=volume,
                fade_in_ms=fade_in_ms,
                source="official_reminder",
                user_id=metadata["user_id"],
            )
            if audio_success:
                recorder = getattr(self, "_record_reality_touch_delivery", None)
                if callable(recorder):
                    await recorder(user_snapshot, reminder_text, source="official_reminder")
        delivery_mode = self._wakeup_delivery_mode(reminder_snapshot)
        chat_success = False
        if delivery_mode == "audio_and_chat" or (delivery_mode == "chat_on_failure" and not audio_success):
            chat_success = await self._send_wakeup_chat_copy(user_snapshot, reminder_text,
                operation_key="official-reminder:" + metadata["reminder_id"])
        delivered = bool(audio_success or chat_success)

        async with self._data_lock:
            user = self.data.get("users", {}).get(metadata["user_id"])
            reminder = self._reality_touch_reminder_registry(user).get(metadata["reminder_id"]) if isinstance(user, dict) else None
            if self._official_reality_touch_matches(reminder, metadata):
                reminder.update(
                    {
                        "status": "delivered" if delivered else "delivery_failed",
                        "delivery_at": _now_ts(),
                        "delivered_text": reminder_text,
                        "audio_success": audio_success,
                        "chat_success": chat_success,
                        "delivery_error": "" if delivered else (
                            "现实触及授权不可用且聊天兜底失败"
                            if not consented
                            else "本机音频与聊天兜底均未确认成功"
                        ),
                    }
                )
                self._save_data_sync()
        detail = "本机音频播放成功" if audio_success else (
            "本机音频失败，聊天兜底成功" if chat_success else "现实触及交付失败"
        )
        return delivered, detail

    async def _record_official_reality_touch_tool_result(
        self,
        event: Any,
        tool: Any,
        tool_result: Any,
    ) -> bool:
        if _single_line(getattr(tool, "name", ""), 80) != "pc_reality_touch_reminder":
            return False
        metadata = self._official_reality_touch_metadata(event)
        if not metadata:
            return False
        failed = bool(tool_result is None or getattr(tool_result, "isError", False))
        if not failed:
            return True
        async with self._data_lock:
            user = self.data.get("users", {}).get(metadata["user_id"])
            reminder = self._reality_touch_reminder_registry(user).get(metadata["reminder_id"]) if isinstance(user, dict) else None
            if self._official_reality_touch_matches(reminder, metadata) and not reminder.get("delivery_at"):
                reminder.update(
                    {
                        "status": "delivery_failed",
                        "delivery_at": _now_ts(),
                        "delivery_error": "现实触及工具调用失败",
                    }
                )
                self._save_data_sync()
        return True

    async def _complete_official_reality_touch_reminder(self, event: Any) -> bool:
        metadata = self._official_reality_touch_metadata(event)
        if not metadata:
            return False
        async with self._data_lock:
            user = self.data.get("users", {}).get(metadata["user_id"]) if isinstance(self.data, dict) else None
            reminder = self._reality_touch_reminder_registry(user).get(metadata["reminder_id"]) if isinstance(user, dict) else None
            if not self._official_reality_touch_matches(reminder, metadata):
                return False
            status = _single_line(reminder.get("status"), 32)
            if status == "delivered":
                reminder["status"] = "completed"
                reminder["completed_at"] = _now_ts()
            elif status == "triggered":
                reminder.update(
                    {
                        "status": "completed_without_delivery",
                        "completed_at": _now_ts(),
                        "delivery_error": "官方任务未调用现实触及工具",
                    }
                )
            elif status != "delivery_failed":
                return False
            self._save_data_sync()
        return True

    def _cancel_wakeup_contact_task(self, user_id: str) -> None:
        task = self._wakeup_contact_task_registry().pop(str(user_id), None)
        if isinstance(task, asyncio.Task) and not task.done():
            task.cancel()

    def _stop_wakeup_contact_session(self, user: dict[str, Any], *, status: str = "cancelled") -> None:
        alarm = self._wakeup_alarm_for_user(user)
        session = self._wakeup_contact_session(alarm)
        if _single_line(session.get("status"), 24) in self._WAKEUP_CONTACT_ACTIVE_STATES:
            session.update({"status": status, "completed_at": _now_ts(), "next_attempt_at": 0})
        users = self.data.get("users", {}) if isinstance(getattr(self, "data", None), dict) else {}
        if isinstance(users, dict):
            for candidate_id, candidate in users.items():
                if candidate is user:
                    self._cancel_wakeup_contact_task(str(candidate_id))
                    break

    def _launch_wakeup_contact_session(self, user_id: str, session_id: str) -> asyncio.Task | None:
        user_key = str(user_id)
        registry = self._wakeup_contact_task_registry()
        previous = registry.get(user_key)
        if isinstance(previous, asyncio.Task) and not previous.done():
            return previous
        operation = self._run_wakeup_contact_session(user_key, session_id)
        scheduler = getattr(self, "_create_lifecycle_background_task", None)
        if callable(scheduler):
            task = scheduler(operation, label=f"wakeup_contact:{_single_line(user_key, 48)}")
        else:
            try:
                task = asyncio.create_task(operation)
            except RuntimeError:
                operation.close()
                return None
        if isinstance(task, asyncio.Task):
            registry[user_key] = task

            def clear(finished: asyncio.Task) -> None:
                current = self._wakeup_contact_task_registry().get(user_key)
                if current is finished:
                    self._wakeup_contact_task_registry().pop(user_key, None)

            task.add_done_callback(clear)
        return task

    def _wakeup_alarm_status_text(self, user: dict[str, Any]) -> str:
        alarm = self._wakeup_alarm_for_user(user)
        consent_text = "已确认当前音频能力" if self._reality_touch_audio_consented(user) else "尚未完成用户知情确认"
        if not alarm.get("enabled"):
            return (
                "现实触及：当前用户的起床语音关闭\n"
                f"用户授权：{consent_text}\n"
                "设置方式：陪伴 现实触及 07:30\n"
                "先在系统里把蓝牙音响连接并设为默认输出，再用“陪伴 现实触及 测试”试听。"
            )
        days = self._wakeup_days(alarm.get("days")) or list(range(7))
        day_labels = "一二三四五六日"
        day_text = "每天" if len(days) == 7 else "周" + "、".join(day_labels[int(day)] for day in days if 0 <= int(day) <= 6)
        repeat = _safe_int(alarm.get("repeat_count"), 1, 1, 6)
        interval = _safe_int(alarm.get("repeat_interval_seconds"), 20, 5, 300)
        start_volume = _safe_int(alarm.get("playback_volume"), self._reality_touch_playback_volume(), 0, 100)
        max_volume = _safe_int(alarm.get("max_volume"), max(start_volume, 70), 0, 100)
        acknowledgement = "等待醒来确认" if alarm.get("require_acknowledgement", True) else "按次数播放"
        return (
            f"现实触及：起床语音已开启\n时间：{alarm.get('time', '未设置')}（{day_text}）\n"
            f"用户授权：{consent_text}（仅本机音频，不含摄像头）\n"
            f"触达：最多 {repeat} 次，等待 {interval} 秒（{acknowledgement}）\n"
            f"音量：{start_volume}% 起步，最高 {max_volume}%\n"
            f"叫醒偏好：{_single_line(alarm.get('message'), 120) or '未填写'}\n"
            f"话术：{self._WAKEUP_DYNAMIC_MESSAGE_HINT}\n"
            "播放目标：系统默认音频输出（请确认它是已连接的蓝牙音响）。"
        )

    def _wakeup_next_trigger(self, alarm: dict[str, Any], now: datetime | None = None) -> datetime | None:
        alarm_time = self._wakeup_parse_time(alarm.get("time"))
        if not alarm.get("enabled") or not alarm_time:
            return None
        current = now or self._wakeup_now()
        days = self._wakeup_days(alarm.get("days")) or list(range(7))
        hour, minute = (int(part) for part in alarm_time.split(":", 1))
        for offset in range(8):
            candidate = (current + timedelta(days=offset)).replace(
                hour=hour,
                minute=minute,
                second=0,
                microsecond=0,
            )
            if candidate.weekday() in days and candidate > current:
                return candidate
        return None

    def _reality_touch_user_snapshot(
        self,
        user_id: str,
        user: dict[str, Any],
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        alarm = user.get("wakeup_alarm") if isinstance(user.get("wakeup_alarm"), dict) else {}
        consent = self._reality_touch_consent(user)
        policy = self._reality_touch_policy(user)
        next_trigger = self._wakeup_next_trigger(alarm, now=now)
        session = self._wakeup_contact_session(alarm)
        reminder_rows = [
            {
                "id": _single_line(item.get("id"), 40),
                "job_id": _single_line(item.get("job_id"), 80),
                "status": _single_line(item.get("status"), 32),
                "scheduled_ts": _safe_int(item.get("scheduled_ts"), 0, 0),
                "scheduled_text": (
                    datetime.fromtimestamp(
                        _safe_int(item.get("scheduled_ts"), 0, 0),
                        tz=(now or self._wakeup_now()).tzinfo,
                    ).strftime("%m-%d %H:%M")
                    if _safe_int(item.get("scheduled_ts"), 0, 0) > 0
                    else ""
                ),
                "topic": _single_line(item.get("topic"), 240),
                "delivery_mode": self._wakeup_delivery_mode(item),
                "playback_volume": _safe_int(item.get("playback_volume"), self._reality_touch_playback_volume(), 0, 100),
                "audio_success": bool(item.get("audio_success")),
                "chat_success": bool(item.get("chat_success")),
                "delivery_error": _single_line(item.get("delivery_error") or item.get("error"), 180),
            }
            for item in self._reality_touch_reminder_registry(user).values()
            if isinstance(item, dict)
        ]
        reminder_rows.sort(
            key=lambda item: (
                item.get("status") not in {"registering", "scheduled", "triggered", "delivering"},
                -_safe_int(item.get("scheduled_ts"), 0, 0),
            )
        )
        reminder_rows = reminder_rows[:20]
        label = _single_line(
            user.get("display_name") or user.get("nickname") or user.get("name") or user_id,
            80,
        ) or str(user_id)
        return {
            "user_id": str(user_id),
            "label": label,
            "has_private_route": bool(user.get("umo")),
            "consent": {
                "confirmed": self._reality_touch_audio_consented(user),
                "version": _safe_int(consent.get("version"), 0, 0),
                "confirmed_at": _safe_int(consent.get("confirmed_at"), 0, 0),
                "local_audio": self._reality_touch_capability_consented(user, "local_audio"),
                "camera": self._reality_touch_camera_consented(user),
            },
            "camera": self._reality_touch_camera_user_snapshot(user, user_id=str(user_id)),
            "policy": {
                "proactive_voice_enabled": bool(policy.get("proactive_voice_enabled")),
                "playback_volume": _safe_int(
                    policy.get("playback_volume"),
                    self._reality_touch_playback_volume(),
                    0,
                    100,
                ),
                "updated_at": _safe_int(policy.get("updated_at"), 0, 0),
            },
            "alarm": {
                "enabled": bool(alarm.get("enabled")),
                "time": self._wakeup_parse_time(alarm.get("time")),
                "days": self._wakeup_days(alarm.get("days")) or list(range(7)),
                "message": _single_line(alarm.get("message"), 240),
                "message_mode": "dynamic",
                "repeat_count": _safe_int(alarm.get("repeat_count"), 1, 1, 6),
                "repeat_interval_seconds": _safe_int(alarm.get("repeat_interval_seconds"), 20, 5, 300),
                "require_acknowledgement": bool(alarm.get("require_acknowledgement", True)),
                "snooze_minutes": _safe_int(alarm.get("snooze_minutes"), 10, 1, 120),
                "playback_volume": _safe_int(
                    alarm.get("playback_volume"),
                    self._reality_touch_playback_volume(),
                    0,
                    100,
                ),
                "volume_step": _safe_int(alarm.get("volume_step"), 8, 0, 30),
                "max_volume": _safe_int(alarm.get("max_volume"), 70, 0, 100),
                "fade_in_ms": _safe_int(alarm.get("fade_in_ms"), 800, 0, 5000),
                "delivery_mode": self._wakeup_delivery_mode(alarm),
                "last_trigger_key": _single_line(alarm.get("last_trigger_key"), 32),
                "next_trigger_at": int(next_trigger.timestamp()) if next_trigger else 0,
                "next_trigger_text": next_trigger.strftime("%m-%d %H:%M") if next_trigger else "",
                "contact_session": {
                    "id": _single_line(session.get("id"), 96),
                    "status": _single_line(session.get("status"), 24),
                    "attempt": _safe_int(session.get("attempt"), 0, 0, 20),
                    "max_attempts": _safe_int(session.get("max_attempts"), 0, 0, 20),
                    "triggered_at": _safe_int(session.get("triggered_at"), 0, 0),
                    "next_attempt_at": _safe_int(session.get("next_attempt_at"), 0, 0),
                    "completed_at": _safe_int(session.get("completed_at"), 0, 0),
                    "last_message": _single_line(session.get("last_message"), 300),
                    "last_volume": _safe_int(session.get("last_volume"), 0, 0, 100),
                    "last_playback_success": bool(session.get("last_playback_success")),
                    "feedback": _single_line(session.get("feedback"), 120),
                },
            },
            "custom_reminders": reminder_rows,
        }

    def _reality_touch_page_snapshot(self) -> dict[str, Any]:
        users = self.data.get("users", {}) if isinstance(getattr(self, "data", None), dict) else {}
        configuration_getter = getattr(self, "_reality_touch_configuration_snapshot", None)
        configuration = configuration_getter() if callable(configuration_getter) else {}
        now = self._wakeup_now()
        rows = [
            self._reality_touch_user_snapshot(str(user_id), user, now=now)
            for user_id, user in users.items()
            if isinstance(user, dict)
            and (
                user.get("umo")
                or user.get("reality_touch_consent")
                or user.get("reality_touch_policy")
                or user.get("reality_touch_camera_consent")
                or user.get("reality_touch_camera_policy")
                or user.get("reality_touch_pending_consent")
                or user.get("reality_touch_reminders")
                or user.get("wakeup_alarm")
            )
        ] if isinstance(users, dict) else []
        rows.sort(
            key=lambda row: (
                not bool(row.get("consent", {}).get("confirmed")),
                not bool(row.get("alarm", {}).get("enabled")),
                str(row.get("label") or row.get("user_id") or ""),
            )
        )
        consented = sum(1 for row in rows if row.get("consent", {}).get("confirmed"))
        proactive_voice = sum(1 for row in rows if row.get("policy", {}).get("proactive_voice_enabled"))
        camera_consented = sum(1 for row in rows if row.get("camera", {}).get("consented"))
        camera_enabled = sum(1 for row in rows if row.get("camera", {}).get("enabled"))
        enabled = sum(1 for row in rows if row.get("alarm", {}).get("enabled"))
        scheduled = sum(1 for row in rows if row.get("alarm", {}).get("next_trigger_at"))
        custom_scheduled = sum(
            1
            for row in rows
            for item in (row.get("custom_reminders") or [])
            if item.get("status") in {"registering", "scheduled", "triggered", "delivering"}
        )
        mobile_context_getter = getattr(self, "mobile_context", None)
        mobile_observations: list[dict[str, Any]] = []
        if callable(mobile_context_getter):
            mobile_rows = list(rows)
            allowed_mobile_user = _single_line(
                (configuration.get("mobile") or {}).get("allowed_user_id")
                if isinstance(configuration, dict)
                else "",
                120,
            )
            if allowed_mobile_user and not any(
                _single_line(row.get("user_id"), 120) == allowed_mobile_user for row in mobile_rows
            ):
                mobile_rows.append({"user_id": allowed_mobile_user, "label": allowed_mobile_user})
            for row in mobile_rows:
                user_id = _single_line(row.get("user_id"), 120)
                try:
                    context = mobile_context_getter(user_id) if user_id else {}
                except Exception:
                    context = {}
                context = context if isinstance(context, dict) else {}
                location = context.get("location") if isinstance(context.get("location"), dict) else {}
                place = location.get("place") if isinstance(location.get("place"), dict) else {}
                location_area = _single_line(
                    place.get("area_label") or place.get("name") or place.get("parent_name"), 80
                )
                location_summary = {
                    "available": location.get("available") is True,
                    "area": location_area or ("已收到地点上报" if location.get("available") else ""),
                    "captured_at": location.get("captured_at"),
                    "age_seconds": location.get("age_seconds"),
                }
                connection_getter = getattr(self, "_mobile_connection_status", None)
                try:
                    connection = connection_getter(user_id) if user_id and callable(connection_getter) else {}
                except Exception:
                    connection = {}
                mobile_observations.append(
                    {
                        "user_id": user_id,
                        "label": _single_line(row.get("label") or user_id, 80),
                        "connections": connection.get("devices", []) if isinstance(connection, dict) else [],
                        "device": context.get("device") if isinstance(context.get("device"), dict) else {},
                        "location": location_summary,
                        "activity": context.get("activity") if isinstance(context.get("activity"), dict) else {},
                        "telemetry": context.get("telemetry") if isinstance(context.get("telemetry"), dict) else {},
                    }
                )
        unavailable_reason = getattr(self, "mihome_unavailable_reason", None)
        mihome_snapshot = {"available": False, "reason": unavailable_reason()
                           if callable(unavailable_reason) else "米家运行时尚未初始化"}
        mihome_api_getter = getattr(self, "_mihome_api", None)
        mihome_api = mihome_api_getter() if callable(mihome_api_getter) else None
        snapshotter = getattr(mihome_api, "page_snapshot", None) if mihome_api is not None else None
        if callable(snapshotter):
            try:
                mihome_snapshot = snapshotter()
                if not isinstance(mihome_snapshot, dict):
                    mihome_snapshot = {"available": True}
                else:
                    mihome_snapshot = {"available": True, **mihome_snapshot}
            except Exception:
                mihome_snapshot = {"available": False, "reason": "米家运行状态暂不可读取，请重载后核对"}
        return {
            "global_enabled": bool(getattr(self, "enable_experimental_bluetooth_wakeup", False)),
            "consent_version": self._REALITY_TOUCH_CONSENT_VERSION,
            "confirmation_command": "陪伴 现实触及 确认",
            "default_message": self._WAKEUP_DEFAULT_MESSAGE,
            "dynamic_message_hint": self._WAKEUP_DYNAMIC_MESSAGE_HINT,
            "audio_output": self._reality_touch_audio_snapshot(),
            "camera": self._reality_touch_camera_page_snapshot(),
            "configuration": configuration,
            "mihome": mihome_snapshot,
            "mobile_observations": mobile_observations,
            "counts": {
                "users": len(rows),
                "consented": consented,
                "proactive_voice": proactive_voice,
                "camera_consented": camera_consented,
                "camera_enabled": camera_enabled,
                "enabled": enabled,
                "scheduled": scheduled,
                "custom_scheduled": custom_scheduled,
            },
            "users": rows,
        }

    def _reality_touch_update_alarm(self, user: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        alarm_time = self._wakeup_parse_time(payload.get("time"))
        if not alarm_time:
            raise ValueError("请设置有效的起床时间")
        enabled = bool(payload.get("enabled"))
        if enabled and not self._reality_touch_audio_consented(user):
            raise ValueError("该用户尚未在私聊中完成现实触及知情确认")
        days = self._wakeup_days(payload.get("days"))
        if not days:
            raise ValueError("请至少选择一个重复日期")
        alarm = self._wakeup_alarm_for_user(user)
        alarm.update(
            {
                "enabled": enabled,
                "time": alarm_time,
                "days": days,
                "message": _single_line(payload.get("message"), 240),
                "repeat_count": _safe_int(payload.get("repeat_count"), 1, 1, 6),
                "repeat_interval_seconds": _safe_int(payload.get("repeat_interval_seconds"), 20, 5, 300),
                "require_acknowledgement": bool(payload.get("require_acknowledgement", True)),
                "snooze_minutes": _safe_int(payload.get("snooze_minutes"), 10, 1, 120),
                "playback_volume": _safe_int(
                    payload.get("playback_volume"),
                    self._reality_touch_playback_volume(),
                    0,
                    100,
                ),
                "volume_step": _safe_int(payload.get("volume_step"), 8, 0, 30),
                "max_volume": _safe_int(payload.get("max_volume"), 70, 0, 100),
                "fade_in_ms": _safe_int(payload.get("fade_in_ms"), 800, 0, 5000),
                "delivery_mode": (
                    _single_line(payload.get("delivery_mode"), 32).lower()
                    if _single_line(payload.get("delivery_mode"), 32).lower() in self._WAKEUP_DELIVERY_MODES
                    else "chat_on_failure"
                ),
            }
        )
        alarm["max_volume"] = max(
            _safe_int(alarm.get("playback_volume"), self._reality_touch_playback_volume(), 0, 100),
            _safe_int(alarm.get("max_volume"), 70, 0, 100),
        )
        alarm.pop("last_trigger_key", None)
        if not enabled:
            self._stop_wakeup_contact_session(user)
        return alarm

    def _wakeup_alarm_command(self, user: dict[str, Any], value: str) -> tuple[str, Any]:
        """Apply a chat command. Returns (reply, play_test_now)."""
        text = str(value or "").strip()
        camera_result = self._reality_touch_camera_command(user, text)
        if camera_result is not None:
            return camera_result
        compact = re.sub(r"\s+", "", text).lower()
        alarm = self._wakeup_alarm_for_user(user)
        if not text or compact in {"查看", "状态", "status"}:
            return self._wakeup_alarm_status_text(user), False
        if compact in {"确认", "同意", "授权"}:
            user["reality_touch_pending_consent"] = {
                "capability": "local_audio",
                "requested_at": _now_ts(),
                "expires_at": _now_ts() + 600,
            }
            self._save_data_sync()
            return self._reality_touch_confirmation_prompt(), False
        if text.startswith("确认"):
            confirmation = text[len("确认"):].strip()
            pending = user.get("reality_touch_pending_consent")
            shown = (isinstance(pending, dict) and pending.get("capability") == "local_audio"
                     and _safe_int(pending.get("expires_at"), 0, 0) >= _now_ts())
            if not shown or not self._reality_touch_confirmation_valid(confirmation):
                user["reality_touch_pending_consent"] = {
                    "capability": "local_audio",
                    "requested_at": _now_ts(),
                    "expires_at": _now_ts() + 600,
                }
                self._save_data_sync()
                return (
                    "请先阅读本次音频能力的风险说明，再由本人在私聊手动输入“我理解风险并确认授权”。\n"
                    + self._reality_touch_confirmation_prompt()
                ), False
            user["reality_touch_consent"] = {
                "confirmed": True,
                "version": self._REALITY_TOUCH_CONSENT_VERSION,
                "confirmed_at": _now_ts(),
                "confirmation_text": _single_line(confirmation, 300),
                "granted_capabilities": ["local_audio"],
                "camera_granted": False,
            }
            user.pop("reality_touch_pending_consent", None)
            self._save_data_sync()
            return "现实触及知情确认已记录。当前只授权本机音频能力，未授权摄像头。", False
        if compact in {"撤销音频确认", "撤销音频授权", "取消音频确认", "取消音频授权"}:
            user.pop("reality_touch_consent", None)
            pending = user.get("reality_touch_pending_consent")
            if isinstance(pending, dict) and pending.get("capability") == "local_audio":
                user.pop("reality_touch_pending_consent", None)
            alarm["enabled"] = False
            self._stop_wakeup_contact_session(user)
            self._save_data_sync()
            return "已撤销现实触及音频授权，并关闭当前用户的起床语音；摄像头独立授权不受影响。", False
        if compact in {"撤销确认", "撤销授权", "取消确认", "取消授权", "撤销全部授权", "撤销现实触及授权"}:
            user.pop("reality_touch_consent", None)
            user.pop("reality_touch_camera_consent", None)
            user.pop("reality_touch_pending_consent", None)
            self._reality_touch_camera_policy(user)["enabled"] = False
            alarm["enabled"] = False
            self._stop_wakeup_contact_session(user)
            self._save_data_sync()
            return "已撤销现实触及的音频与摄像头授权，并关闭当前用户的设备触达。", False
        if compact in {"关闭", "取消", "停用", "off", "disable"}:
            alarm["enabled"] = False
            self._stop_wakeup_contact_session(user)
            self._save_data_sync()
            return "已关闭现实触及的起床语音。", False
        if not self._reality_touch_audio_consented(user):
            user["reality_touch_pending_consent"] = {
                "capability": "local_audio",
                "requested_at": _now_ts(),
                "expires_at": _now_ts() + 600,
            }
            self._save_data_sync()
            return self._reality_touch_confirmation_prompt(), False
        if compact in {"测试", "试听", "test"}:
            if not alarm.get("enabled") or not self._wakeup_parse_time(alarm.get("time")):
                return "请先设置时间，例如：陪伴 现实触及 07:30，然后再测试。", False
            return "正在通过系统默认音频输出试听起床语音。", True

        parts = text.split(maxsplit=1)
        alarm_time = self._wakeup_parse_time(parts[0])
        if not alarm_time:
            return (
                "时间格式不对。请使用：陪伴 现实触及 07:30\n"
                "也可以：陪伴 现实触及 关闭 / 查看 / 测试。"
            ), False
        alarm["time"] = alarm_time
        alarm["enabled"] = True
        alarm.setdefault("days", list(range(7)))
        alarm.setdefault("message", "")
        alarm.setdefault("repeat_count", 1)
        alarm.setdefault("repeat_interval_seconds", 20)
        alarm.setdefault("require_acknowledgement", True)
        alarm.setdefault("snooze_minutes", 10)
        alarm.setdefault("volume_step", 8)
        alarm.setdefault("max_volume", 70)
        alarm.setdefault("fade_in_ms", 800)
        alarm.setdefault("delivery_mode", "chat_on_failure")
        if len(parts) == 2:
            option = parts[1].strip()
            if option:
                day_part, _, message = option.partition(" ")
                parsed_days = self._wakeup_days(day_part)
                if parsed_days:
                    alarm["days"] = parsed_days
                    if message.strip():
                        alarm["message"] = _single_line(message, 240)
                else:
                    alarm["message"] = _single_line(option, 240)
        alarm.pop("last_trigger_key", None)
        self._save_data_sync()
        return self._wakeup_alarm_status_text(user), False

    def _wakeup_now(self) -> datetime:
        timezone_name = _single_line(getattr(self, "environment_perception_timezone", "Asia/Shanghai"), 64)
        try:
            return datetime.now(zoneinfo.ZoneInfo(timezone_name or "Asia/Shanghai"))
        except Exception:
            return datetime.now().astimezone()

    def _wakeup_user_sleep_hint(self, user: dict[str, Any]) -> str:
        """Read the phone's recent sleep measurement as optional background only."""
        getter = getattr(self, "mobile_context", None)
        if not callable(getter):
            return ""
        try:
            snapshot = getter(_single_line(user.get("user_id"), 120))
            telemetry = snapshot.get("telemetry") if isinstance(snapshot, dict) else None
            measurements = telemetry.get("measurements") if isinstance(telemetry, dict) else None
            if not isinstance(measurements, list):
                return ""
            for item in measurements:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("type") or item.get("label") or "").lower()
                if "sleep" not in name and "睡眠" not in name:
                    continue
                value = item.get("value")
                unit = str(item.get("unit") or "").lower()
                if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
                    continue
                minutes = float(value) * 60 if ("hour" in unit or "小时" in unit) else float(value)
                if minutes <= 0 or minutes > 24 * 60:
                    continue
                age = telemetry.get("age_seconds") if isinstance(telemetry, dict) else None
                if isinstance(age, (int, float)) and age > 24 * 3600:
                    return ""
                return "手机最近上报的睡眠时长约%d分钟" % round(minutes)
        except Exception:
            return ""
        return ""

    async def _generate_wakeup_alarm_message(
        self,
        user: dict[str, Any],
        alarm: dict[str, Any],
        *,
        test: bool = False,
    ) -> str:
        """Create one in-character utterance for this firing; stored text is only a preference."""
        llm_call = getattr(self, "_llm_call", None)
        if not callable(llm_call):
            return self._WAKEUP_DEFAULT_MESSAGE

        umo = _single_line(user.get("umo"), 240)
        name = _single_line(
            user.get("nickname")
            or user.get("last_display_name")
            or user.get("display_name")
            or user.get("name"),
            48,
        )
        persona = ""
        persona_resolver = getattr(self, "_resolve_proactive_persona_prompt", None)
        if callable(persona_resolver):
            try:
                persona = str(await persona_resolver(user, umo=umo) or "").strip()
            except TypeError:
                try:
                    persona = str(await persona_resolver(user) or "").strip()
                except Exception:
                    persona = ""
            except Exception:
                persona = ""

        relationship = ""
        relationship_formatter = getattr(self, "_format_proactive_relationship_fact", None)
        if callable(relationship_formatter):
            try:
                relationship = str(relationship_formatter(user) or "").strip()
            except Exception:
                relationship = ""

        history = ""
        history_getter = getattr(self, "_recent_private_conversation_for_proactive_review", None)
        if callable(history_getter):
            try:
                history = str(await history_getter(user, limit=8) or "").strip()
            except Exception:
                history = ""
        if not history:
            recent_lines = []
            last_bot = _single_line(user.get("last_companion_message"), 180)
            last_user = _single_line(user.get("last_user_message"), 180)
            if last_bot:
                recent_lines.append(f"Bot：{last_bot}")
            if last_user:
                recent_lines.append(f"用户：{last_user}")
            history = "\n".join(recent_lines)

        now = self._wakeup_now()
        weekday = "一二三四五六日"[now.weekday()]
        preference = _single_line(alarm.get("message"), 240)
        sleep_hint = self._wakeup_user_sleep_hint(user)
        sleep_line = (("用户已授权的近期睡眠背景：" + sleep_hint +
                       "。只可自然关心一句，不报具体数字、不诊断、不声称实时看见。\n")
                      if sleep_hint else "")
        sleep_guard = ("不要声称实时看见或监听；睡眠背景已获得用户授权，只作语气参考。"
                       if sleep_hint else
                       "不要声称看见、监听或确认对方正在睡觉或已经醒来，不补造天气、日程或昨晚经历；")
        history_guard = ("只按已给的睡眠摘要，不补写其他昨晚经历" if sleep_hint
                         else "不要补写昨晚或今天的经历")
        attempt = _safe_int(alarm.get("_contact_attempt"), 1, 1, 20)
        max_attempts = _safe_int(alarm.get("_contact_max_attempts"), 1, 1, 20)
        previous_messages = alarm.get("_contact_previous_messages")
        previous_text = "\n".join(
            f"- {_single_line(item, 240)}"
            for item in (previous_messages if isinstance(previous_messages, list) else [])[-4:]
            if _single_line(item, 240)
        )
        acknowledgement_hint = (
            "如果符合当前语气，可以轻轻请对方醒来后回一句；不要提系统如何处理回复，也不把回应说成义务。"
            if bool(alarm.get("require_acknowledgement", True))
            else "无需索要回复。"
        )
        prompt = (
            "写一条此刻真正会说出口的叫醒提醒，延续选定人格和你们的关系距离。时间只作语境参考，不必报时；触达次数只帮助避免重复，不要说出次数。\n"
            f"当前时间：{now.strftime('%Y-%m-%d %H:%M')}，周{weekday}\n"
            f"本轮触达序号：{attempt}/{max_attempts}\n"
            f"用户称呼：{name or '没有可靠称呼时直接自然地说“你”'}\n"
            f"关系资料：{relationship or '没有额外资料，保持自然、低压力'}\n"
            f"用户的叫醒偏好：{preference or '无，由你按人格和语境发挥'}\n"
            f"{sleep_line}"
            f"本轮此前已说的话：\n{previous_text or '无'}\n"
            f"最近对话：\n{history[-2400:] or '无可用对话；' + history_guard}\n\n"
            "用一两句短话，亲切、自然、有生活感，像熟悉的人轻声提醒；可结合偏好，但换成符合当下语气的说法，不照抄设置或上一条。重复触达时换个角度，让提醒更清楚即可，不逐渐施压。"
            "避免通知、客服、打卡口吻。只输出正文，不要标题、引号、Markdown、动作旁白、TTS 标签或解释。"
            f"{acknowledgement_hint}"
            + sleep_guard + "语气不命令、不训斥、不制造内疚或紧迫感。"
        )
        system_prompt = (
            "延续下面选定的人格，用原本的口吻自然地提醒对方起床。人格只作为表达依据，不复述或讲解设定。\n\n"
            + (persona[:6000] if persona else "保持自然、有生活感的陪伴者口吻。")
        )
        provider_id = None
        provider_picker = getattr(self, "_task_provider", None)
        if callable(provider_picker):
            try:
                provider_id = provider_picker(
                    getattr(self, "voice_prompt_provider_id", ""),
                    getattr(self, "mai_style_provider_id", ""),
                    getattr(self, "fast_response_provider_id", ""),
                    getattr(self, "llm_provider_id", ""),
                ) or None
            except Exception:
                provider_id = None
        try:
            raw = await llm_call(
                prompt,
                max_tokens=160,
                provider_id=provider_id,
                task="wakeup_alarm_message",
                user_id=_single_line(user.get("user_id"), 120),
                umo=umo,
                system_prompt=system_prompt,
                timeout_key="VOICE_PROMPT_PROVIDER_ID",
            )
        except Exception as exc:
            logger.warning("[PrivateCompanion] 起床提醒动态话术生成失败，使用兜底文本: %s", _single_line(exc, 160))
            return self._WAKEUP_DEFAULT_MESSAGE

        message = str(raw or "").strip()
        message = re.sub(r"^```(?:\w+)?\s*|\s*```$", "", message, flags=re.IGNORECASE)
        message = re.sub(r"^(?:最终(?:话术|文本|回复)|话术|回复|输出)\s*[:：]\s*", "", message)
        message = re.sub(r"<[^>]{1,80}>", "", message)
        message = _single_line(message.strip().strip('"\'“”‘’'), 500)
        if not message:
            return self._WAKEUP_DEFAULT_MESSAGE
        return message

    async def _play_wakeup_alarm(
        self,
        user: dict[str, Any],
        alarm: dict[str, Any],
        *,
        test: bool = False,
        volume: Any = None,
    ) -> bool:
        message = await self._generate_wakeup_alarm_message(user, alarm, test=test)
        repeat = 1 if test else _safe_int(alarm.get("repeat_count"), 1, 1, 6)
        interval = _safe_int(alarm.get("repeat_interval_seconds"), 20, 5, 300)
        playback_kwargs = {
            "repeat": repeat,
            "interval": interval,
            "fade_in_ms": _safe_int(alarm.get("fade_in_ms"), 800, 0, 5000),
            "source": "wakeup_test" if test else "wakeup_alarm",
            "user_id": _single_line(user.get("user_id"), 120),
        }
        if volume is not None:
            playback_kwargs["volume"] = volume
        played = await self._play_reality_touch_text(message, **playback_kwargs)
        if played:
            if not test:
                recorder = getattr(self, "_record_reality_touch_delivery", None)
                if callable(recorder):
                    await recorder(user, message, source="wakeup_alarm")
            logger.info("[PrivateCompanion] 起床提醒场景已播放: test=%s repeat=%s", test, repeat)
        return played

    async def _send_wakeup_chat_copy(self, user: dict[str, Any], message: str, *, operation_key: str) -> bool:
        umo = _single_line(user.get("umo"), 240)
        direct_sender = getattr(self, "_send_chain_components", None)
        sender = getattr(self, "_send_proactive_message_chain", None)
        if not umo or (not callable(direct_sender) and not callable(sender)):
            return False
        try:
            if callable(direct_sender):
                from astrbot.api.message_components import Plain

                return bool(await direct_sender(umo, [Plain(message)],
                    user_id=_single_line(user.get("user_id"), 120), operation_key=operation_key))
            outcome = await sender(umo, message)
            if isinstance(outcome, bool):
                return outcome
            return bool(getattr(outcome, "delivered", False))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("[PrivateCompanion] 起床提醒聊天副本发送失败: %s", _single_line(exc, 160))
            return False

    async def _run_wakeup_contact_session(self, user_id: str, session_id: str) -> None:
        """Run cancellable attempts until the user responds or the attempt budget is exhausted."""
        while True:
            users = self.data.get("users", {}) if isinstance(getattr(self, "data", None), dict) else {}
            user = users.get(str(user_id)) if isinstance(users, dict) else None
            if not isinstance(user, dict):
                return
            alarm = user.get("wakeup_alarm") if isinstance(user.get("wakeup_alarm"), dict) else {}
            session = self._wakeup_contact_session(alarm)
            if _single_line(session.get("id"), 96) != session_id:
                return
            status = _single_line(session.get("status"), 24)
            if status not in self._WAKEUP_CONTACT_ACTIVE_STATES:
                return
            now_ts = _now_ts()
            next_attempt_at = _safe_int(session.get("next_attempt_at"), 0, 0)
            if next_attempt_at > now_ts:
                await asyncio.sleep(max(0.2, next_attempt_at - now_ts))
                continue

            attempt = _safe_int(session.get("attempt"), 0, 0, 20) + 1
            maximum = _safe_int(
                session.get("max_attempts"),
                _safe_int(alarm.get("repeat_count"), 1, 1, 6),
                1,
                6,
            )
            if attempt > maximum:
                session.update({"status": "exhausted", "completed_at": _now_ts(), "next_attempt_at": 0})
                self._schedule_data_save(delay=0.2)
                return

            previous = session.get("messages") if isinstance(session.get("messages"), list) else []
            alarm_for_attempt = copy.deepcopy(alarm)
            alarm_for_attempt["_contact_attempt"] = attempt
            alarm_for_attempt["_contact_max_attempts"] = maximum
            alarm_for_attempt["_contact_previous_messages"] = list(previous)
            session.update({"status": "playing", "attempt": attempt, "last_attempt_at": _now_ts()})
            self._schedule_data_save(delay=0.2)

            message = await self._generate_wakeup_alarm_message(user, alarm_for_attempt)
            volume = self._wakeup_attempt_volume(alarm, attempt)
            played = await self._play_reality_touch_text(
                message,
                repeat=1,
                interval=_safe_int(alarm.get("repeat_interval_seconds"), 20, 5, 300),
                volume=volume,
                fade_in_ms=_safe_int(alarm.get("fade_in_ms"), 800, 0, 5000),
                source="wakeup_alarm",
                user_id=str(user_id),
            )
            if played:
                recorder = getattr(self, "_record_reality_touch_delivery", None)
                if callable(recorder):
                    await recorder(user, message, source="wakeup_alarm")
            delivery_mode = self._wakeup_delivery_mode(alarm)
            chat_sent = False
            if delivery_mode == "audio_and_chat" or (delivery_mode == "chat_on_failure" and not played):
                chat_sent = await self._send_wakeup_chat_copy(user, message,
                    operation_key=f"wakeup:{session_id}:{attempt}")

            current = self._wakeup_contact_session(alarm)
            if _single_line(current.get("id"), 96) != session_id:
                return
            if _single_line(current.get("status"), 24) not in self._WAKEUP_CONTACT_ACTIVE_STATES:
                return
            messages = current.get("messages") if isinstance(current.get("messages"), list) else []
            messages.append(message)
            current.update(
                {
                    "status": "pending",
                    "messages": messages[-6:],
                    "last_message": message,
                    "last_volume": volume,
                    "last_playback_success": played,
                    "last_chat_success": chat_sent,
                }
            )
            if attempt >= maximum or not bool(alarm.get("require_acknowledgement", True)):
                if attempt >= maximum:
                    current.update({"status": "exhausted", "completed_at": _now_ts(), "next_attempt_at": 0})
                    self._schedule_data_save(delay=0.2)
                    return
            interval = _safe_int(alarm.get("repeat_interval_seconds"), 20, 5, 300)
            current["next_attempt_at"] = _now_ts() + interval
            self._schedule_data_save(delay=0.2)

    async def _classify_wakeup_feedback(self, text: str, default_snooze: int, *, user_id: str) -> tuple[str, int]:
        compact = re.sub(r"[\s，,。.!！?？~～]+", "", str(text or "")).lower()
        if not compact:
            return "other", 0
        duration = re.search(r"(\d{1,3})\s*(小时|分钟|分)(?:钟)?后", str(text or ""))
        if duration and any(marker in compact for marker in ("叫", "提醒", "喊", "再睡", "晚点")):
            value = int(duration.group(1)) * (60 if duration.group(2) == "小时" else 1)
            return "snooze", max(1, min(120, value))
        if any(marker in compact for marker in ("稍后叫", "等会叫", "过会叫", "晚点叫", "再睡会", "还没醒", "没醒呢")):
            return "snooze", default_snooze
        if any(marker in compact for marker in ("今天不用叫", "今天别叫", "停止这次", "这次取消", "不用再叫")):
            return "stop", 0
        explicit_awake = re.fullmatch(
            r"(?:好|好啦|嗯|恩)?(?:我)?(?:已经)?(?:醒了|醒啦|起了|起来了|起床了)(?:呀|啊|哦|呢|哈|谢谢)?",
            compact,
        )
        if explicit_awake or compact in {
            "早", "早安", "早上好", "早啊", "早呀",
            "不用叫了", "别叫了我醒了", "不用提醒了我醒了",
        }:
            return "awake", 0

        llm_call = getattr(self, "_llm_call", None)
        if not callable(llm_call) or len(compact) > 120:
            return "other", 0
        prompt = (
            "根据语义判断这条消息是否回应刚才的起床提醒，只输出 awake、snooze、stop、other 之一。"
            "awake 表示明确说已醒或已起床；snooze 表示明确想稍后再提醒；stop 表示只取消今天这轮；"
            "普通聊天或意图不清楚时选 other，不把寒暄当成控制请求。\n"
            f"用户消息：{_single_line(text, 240)}"
        )
        try:
            result = _single_line(
                await llm_call(prompt, max_tokens=12, task="wakeup_feedback_intent", user_id=user_id),
                24,
            ).lower()
        except Exception:
            return "other", 0
        if result.startswith("awake"):
            return "awake", 0
        if result.startswith("snooze"):
            return "snooze", default_snooze
        if result.startswith("stop"):
            return "stop", 0
        return "other", 0

    async def _maybe_handle_wakeup_feedback(
        self,
        event: Any,
        user_id: str,
        user: dict[str, Any],
        text: str,
    ) -> bool:
        alarm = user.get("wakeup_alarm") if isinstance(user.get("wakeup_alarm"), dict) else {}
        session = self._wakeup_contact_session(alarm)
        if _single_line(session.get("status"), 24) not in self._WAKEUP_CONTACT_ACTIVE_STATES:
            return False
        contexts = self.v2_host_api()._reality_context
        original = contexts.target(str(user_id), umo=str(event.unified_msg_origin))
        await contexts.verified_persona(original, str(user_id), umo=str(event.unified_msg_origin))
        session_id = session.get("id")
        snooze_default = _safe_int(alarm.get("snooze_minutes"), 10, 1, 120)
        intent, snooze_minutes = await self._classify_wakeup_feedback(text, snooze_default, user_id=str(user_id))
        if intent == "other":
            return False
        await contexts.verified_persona(original, str(user_id), umo=str(event.unified_msg_origin))
        if (self._wakeup_contact_session(alarm) is not session or session.get("id") != session_id
                or session.get("status") not in self._WAKEUP_CONTACT_ACTIVE_STATES):
            return False

        now_ts = _now_ts()
        if intent == "snooze":
            minutes = max(1, min(120, snooze_minutes or snooze_default))
            session.update(
                {
                    "status": "snoozed",
                    "feedback": _single_line(text, 120),
                    "feedback_at": now_ts,
                    "next_attempt_at": now_ts + minutes * 60,
                }
            )
            reply = f"好，{minutes} 分钟后再叫你。这期间不会继续播放。"
        elif intent == "awake":
            session.update(
                {
                    "status": "acknowledged",
                    "feedback": _single_line(text, 120),
                    "feedback_at": now_ts,
                    "completed_at": now_ts,
                    "next_attempt_at": 0,
                }
            )
            reply = "好，收到你已经醒来的确认了，今天这轮提醒已经停止。"
        else:
            session.update(
                {
                    "status": "cancelled",
                    "feedback": _single_line(text, 120),
                    "feedback_at": now_ts,
                    "completed_at": now_ts,
                    "next_attempt_at": 0,
                }
            )
            reply = "好，今天这轮提醒已取消，不影响之后设定的日期。"

        self._cancel_wakeup_contact_task(user_id)
        self._schedule_data_save(delay=0.1)
        if intent == "snooze":
            self._launch_wakeup_contact_session(user_id, _single_line(session.get("id"), 96))
        setter = getattr(event, "set_extra", None)
        if callable(setter):
            setter("companion_v2_reality_wakeup_feedback", {
                "intent": intent, "state": session["status"], "detail": reply})
        return True

    async def _run_wakeup_alarm_tick(self) -> None:
        if not bool(getattr(self, "enable_experimental_bluetooth_wakeup", False)):
            return
        users = self.data.get("users", {}) if isinstance(getattr(self, "data", None), dict) else {}
        if not isinstance(users, dict):
            return
        now = self._wakeup_now()
        check_window = max(90, _safe_int(getattr(self, "check_interval_seconds", 60), 60, 30) + 15)
        for user_id, user in list(users.items()):
            if not isinstance(user, dict) or not user.get("umo"):
                continue
            alarm = user.get("wakeup_alarm")
            if not isinstance(alarm, dict):
                continue
            if not self._reality_touch_audio_consented(user):
                continue
            session = self._wakeup_contact_session(alarm)
            session_status = _single_line(session.get("status"), 24)
            session_id = _single_line(session.get("id"), 96)
            session_due = _safe_int(session.get("next_attempt_at"), 0, 0) <= _now_ts()
            running_task = self._wakeup_contact_task_registry().get(str(user_id))
            if session_status == "playing" and not (isinstance(running_task, asyncio.Task) and not running_task.done()):
                session.update({"status": "delivery_unknown", "completed_at": _now_ts(), "next_attempt_at": 0})
                self._save_data_sync()
                continue
            if (
                session_id
                and session_status in self._WAKEUP_CONTACT_ACTIVE_STATES
                and session_due
                and not (isinstance(running_task, asyncio.Task) and not running_task.done())
            ):
                self._launch_wakeup_contact_session(str(user_id), session_id)
            alarm_time = self._wakeup_parse_time(alarm.get("time"))
            if not alarm.get("enabled") or not alarm_time:
                continue
            hour, minute = (int(part) for part in alarm_time.split(":", 1))
            scheduled_at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if not 0 <= (now - scheduled_at).total_seconds() <= check_window:
                continue
            days = self._wakeup_days(alarm.get("days")) or list(range(7))
            scheduled_key = scheduled_at.strftime("%Y-%m-%d %H:%M")
            if now.weekday() not in days or alarm.get("last_trigger_key") == scheduled_key:
                continue
            last_activity_at = _safe_int(user.get("last_private_activity_at"), 0, 0)
            if last_activity_at >= int(scheduled_at.timestamp()):
                alarm["last_trigger_key"] = scheduled_key
                alarm["last_skip_reason"] = "user_active_after_scheduled_time"
                alarm["last_skipped_at"] = _now_ts()
                self._schedule_data_save(delay=0.2)
                continue
            alarm["last_trigger_key"] = scheduled_key
            alarm.pop("last_skip_reason", None)
            alarm.pop("last_skipped_at", None)
            session_id = f"{user_id}:{scheduled_at.strftime('%Y%m%d%H%M')}"
            alarm["contact_session"] = {
                "id": session_id,
                "status": "pending",
                "attempt": 0,
                "max_attempts": _safe_int(alarm.get("repeat_count"), 1, 1, 6),
                "triggered_at": _now_ts(),
                "next_attempt_at": _now_ts(),
                "messages": [],
            }
            self._schedule_data_save(delay=0.2)
            task = self._launch_wakeup_contact_session(str(user_id), session_id)
            if task is None:
                await self._run_wakeup_contact_session(str(user_id), session_id)

    async def _test_wakeup_alarm(self, user: dict[str, Any]) -> None:
        alarm = self._wakeup_alarm_for_user(user)
        await self._play_wakeup_alarm(copy.deepcopy(user), copy.deepcopy(alarm), test=True)
