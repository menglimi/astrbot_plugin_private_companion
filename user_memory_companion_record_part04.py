# -*- coding: utf-8 -*-
"""UserMemoryCompanionRecordPart04Mixin。

由 tools/split_mixin_domain.py 从 user_memory_companion_record.py 机械抽取（3 个方法 + 0 个模块级名字 + 0 个类级赋值 / 57 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 UserMemoryCompanionRecordMixin）。
"""
from __future__ import annotations
from .user_memory_companion_record_shared import Any
from .user_memory_companion_record_shared import _safe_float
from .user_memory_companion_record_shared import _safe_int
from .user_memory_companion_record_shared import _single_line
from .user_memory_companion_record_shared import logger
from .user_memory_companion_record_shared import runtime_persona_setting



class UserMemoryCompanionRecordPart04Mixin:
    """UserMemoryCompanionRecordPart04Mixin（从 UserMemoryCompanionRecordMixin 拆出）。"""


    async def _try_acquire_user_background_task(
        self,
        user_id: str,
        task: str,
        now: float,
        *,
        refresh_key: str,
        refresh_seconds: float,
    ) -> bool:
        retry_key = f"{task}_retry_after"
        running_key = f"{task}_running_at"
        async with self._data_lock:
            current = self._get_user(user_id)
            if now - _safe_float(current.get(refresh_key), 0) < max(0.0, float(refresh_seconds)):
                return False
            if now < _safe_float(current.get(retry_key), 0):
                return False
            running_at = _safe_float(current.get(running_key), 0)
            if running_at > 0 and now - running_at < 10 * 60:
                return False
            current[running_key] = now
            self._save_data_sync(sections={"users"})
        return True

    def _user_background_task_retry_delay(self, task: str) -> float:
        if task == "dialogue_episode":
            configured = _safe_int(
                runtime_persona_setting(self, "episode_memory_refresh_minutes", 90),
                90,
                1,
            ) * 60
        elif task == "companion_memory":
            configured = _safe_int(
                runtime_persona_setting(self, "memory_refresh_interval_minutes", 360),
                360,
                1,
            ) * 60
        else:
            configured = 10 * 60
        return float(min(max(10 * 60, configured), 30 * 60))

    async def _mark_user_background_retry(self, user_id: str, task: str, now: float, error: Any) -> None:
        retry_key = f"{task}_retry_after"
        error_key = f"{task}_last_error"
        running_key = f"{task}_running_at"
        delay = self._user_background_task_retry_delay(task)
        async with self._data_lock:
            current = self._get_user(user_id)
            current[retry_key] = now + delay
            current[error_key] = _single_line(error, 180)
            current[running_key] = 0
            self._save_data_sync(sections={"users"})
        logger.warning(
            "私聊后台整理失败,已进入短冷却避免重复请求: user=%s task=%s retry=%ss error=%s",
            user_id,
            task,
            int(delay),
            _single_line(error, 120),
        )
