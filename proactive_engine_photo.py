# -*- coding: utf-8 -*-
"""图片/照片动作域。

由 tools/split_mixin_domain.py 从 proactive_engine.py 机械抽取（24 个方法 + 0 个模块级名字 + 0 个类级赋值 / 308 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveEngineMixin）。
"""
from __future__ import annotations

import re
from .helpers import _now_ts, _path_text, _safe_float, _safe_int, _single_line, _today_key
from .persona_config import runtime_persona_setting
from datetime import datetime
from typing import Any

from .logging_util import get_module_logger
from .proactive_engine_shared import _engine_host

logger = get_module_logger(__name__)



class ProactiveEnginePhotoMixin:
    """图片/照片动作域（从 ProactiveEngineMixin 拆出）。"""


    def _note_photo_generation_attempt(self, user_id: str, image_path: str = "") -> None:
        if not str(user_id or "").strip():
            return
        today = _today_key()
        user = self._get_user(str(user_id or ""))
        if user.get("photo_generated_day") != today:
            user["photo_generated_day"] = today
            user["photo_generated_today"] = 0
        user["photo_generated_today"] = _safe_int(user.get("photo_generated_today"), 0) + 1
        user["last_generated_photo_path"] = _path_text(image_path, 1000)
        user["last_generated_photo_at"] = _engine_host._now_ts()

    def _note_screen_peek_attempt(self, user_id: str, reason: str = "", *, count_daily: bool = True) -> None:
        if not str(user_id or "").strip():
            return
        today = _today_key()
        user = self._get_user(str(user_id or ""))
        if user.get("screen_peek_day") != today:
            user["screen_peek_day"] = today
            user["screen_peek_today"] = 0
        if count_daily:
            user["screen_peek_today"] = _safe_int(user.get("screen_peek_today"), 0) + 1
        user["screen_peek_last_at"] = _engine_host._now_ts()
        user["last_screen_peek_reason"] = _single_line(reason, 120)
        if not count_daily:
            user["last_unanswered_screen_peek_at"] = _engine_host._now_ts()

    def _screen_peek_failure_cooldown_active(self, user: dict[str, Any] | None = None, *, now: float | None = None) -> bool:
        if not isinstance(user, dict):
            return False
        check_now = _engine_host._now_ts() if now is None else now
        return _safe_float(user.get("screen_peek_failure_until"), 0.0) > check_now

    def _note_screen_peek_failure(self, user: dict[str, Any] | None, reason: str = "", *, cooldown_minutes: int = 60) -> None:
        if not isinstance(user, dict):
            return
        now = _engine_host._now_ts()
        user["screen_peek_failure_until"] = now + max(5, _safe_int(cooldown_minutes, 60, 5)) * 60
        user["screen_peek_failure_reason"] = _single_line(reason, 180)
        user["screen_peek_failure_count"] = _safe_int(user.get("screen_peek_failure_count"), 0, 0) + 1
        try:
            self._save_data_sync(sections={"users"})
        except Exception:
            pass

    def _visual_share_tokens(self) -> tuple[str, ...]:
        # Broad visual anchors only. Specific subjects should be chosen by the model from context.
        return (
            "看", "拍", "图", "照片", "画面", "颜色", "形状", "光", "影", "反光",
            "桌", "纸", "书", "本", "笔", "杯", "饭", "饮", "路", "窗", "镜",
            "小物", "随手", "涂", "画", "包装", "屏幕", "边角",
        )

    def _strong_photo_share_intent(self, *parts: Any) -> bool:
        text = " ".join(_single_line(part, 160) for part in parts if _single_line(part, 160))
        if not text:
            return False
        strong_tokens = ("拍了张照", "拍了照", "拍照", "照片", "图片", "发你看", "给你看", "你看看")
        if any(token in text for token in strong_tokens):
            return True
        visual_tokens = (
            "花", "颜色", "蓝紫", "矮牵牛", "雨", "小雨", "毛毛雨", "路边", "校门",
            "晚霞", "阳光", "云", "窗边", "倒影", "影子", "小猫", "桌面", "杯", "包装",
        )
        return sum(1 for token in visual_tokens if token in text) >= 2

    def _days_since_last_photo_sent(self, user: dict[str, Any] | None = None) -> int | None:
        if not isinstance(user, dict):
            return None
        day_text = str(user.get("photo_sent_day") or "").strip()
        if not day_text:
            return None
        try:
            last_day = datetime.strptime(day_text[:10], "%Y-%m-%d").date()
            today = datetime.strptime(_today_key(), "%Y-%m-%d").date()
            return max(0, (today - last_day).days)
        except Exception:
            return None

    def _photo_text_overdue_boost(self, user: dict[str, Any] | None = None) -> float:
        days = self._days_since_last_photo_sent(user)
        if days is None:
            return 0.18
        if days >= 10:
            return 0.22
        if days >= 5:
            return 0.12
        if days >= 2:
            return 0.05
        return 0.0

    def _photo_text_plan_field_patch(
        self,
        *,
        reason: str,
        topic: str = "",
        motive: str = "",
        planned_event: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        current_item = self._proactive_current_agenda_item()
        current_text = self._format_plan_item_for_prompt(current_item)
        if current_text.strip(" （）()") in {"", "暂无"}:
            current_text = ""
        event_text = ""
        if isinstance(planned_event, dict):
            event_text = " ".join(
                _single_line(planned_event.get(key), 80)
                for key in ("topic", "scene", "why", "motive", "impulse")
            )
        seed = _single_line(topic, 60) or _single_line(event_text, 60) or _single_line(current_text, 60)
        if not seed:
            seed = {
                "morning_greeting": "早上眼前那点小画面",
                "noon_greeting": "中午手边的小东西",
                "evening_greeting": "晚一点的光线",
                "diary_share": "今天记下来的画面",
                "background_schedule": "手边这一小段",
            }.get(reason, "眼前这个小画面")
        cleaned_seed = re.sub(r"^(?:刚刚|刚才|现在|这会儿)\s*", "", seed).strip(" ，,。")
        patched_topic = _single_line(cleaned_seed, 60) or "眼前这个小画面"
        text = " ".join([motive, topic, event_text, current_text])
        selfie_tokens = ("自拍", "穿搭", "衣服", "校服", "镜子", "发型", "脸", "表情")
        if any(token in text for token in selfie_tokens):
            patched_motive = f"看到“{patched_topic}”那一下,第一反应是想自拍一张给你看"
        else:
            patched_motive = f"看到“{patched_topic}”那一下,第一反应是想拍下来发给你"
        return {
            "topic": self._soften_topic_hook(patched_topic),
            "motive": self._normalize_internal_motive_text(patched_motive),
        }

    def _screen_glance_available(
        self,
        user: dict[str, Any] | None = None,
        *,
        ignore_daily_limit: bool = False,
    ) -> bool:
        if not runtime_persona_setting(self, "enable_screen_glance_action", False):
            return False
        if isinstance(user, dict) and self._private_user_role(user) == "friend":
            return False
        daily_limit = self._effective_user_screen_peek_daily_limit(user)
        if daily_limit <= 0 and not ignore_daily_limit:
            return False
        if isinstance(user, dict):
            if self._screen_peek_failure_cooldown_active(user):
                return False
            today = _today_key()
            used_today = (
                _safe_int(user.get("screen_peek_today"), 0)
                if str(user.get("screen_peek_day") or "") == today
                else 0
            )
            if not ignore_daily_limit and used_today >= daily_limit:
                return False
            cooldown_seconds = max(
                0,
                runtime_persona_setting(self, "screen_peek_cooldown_minutes", 240),
            ) * 60
            last_at = _safe_float(user.get("screen_peek_last_at"), 0.0)
            if cooldown_seconds > 0 and last_at > 0 and _engine_host._now_ts() - last_at < cooldown_seconds:
                return False
        try:
            plugin = self._get_screen_companion_plugin()
            return plugin is not None and callable(getattr(plugin, "_invoke_screen_skill", None))
        except Exception:
            return False

    def _comfyui_photo_available(self) -> bool:
        return bool(runtime_persona_setting(self, "enable_photo_text_action", True)) and self._image_companion_backend_available("comfyui")

    def _external_photo_available(self) -> bool:
        return bool(runtime_persona_setting(self, "enable_photo_text_action", True)) and self._image_companion_backend_available("external")

    def _backup_external_photo_unavailable_note(self) -> str:
        if not runtime_persona_setting(self, "enable_photo_text_action", True):
            return "photo_action_disabled"
        status = self._image_companion_status()
        return _single_line(status.get("backup_external_note"), 120) or ""

    def _backup_external_photo_available(self) -> bool:
        return not bool(self._backup_external_photo_unavailable_note())

    def _sdgen_photo_available(self) -> bool:
        return bool(runtime_persona_setting(self, "enable_photo_text_action", True)) and self._image_companion_backend_available("sdgen")

    def _custom_tool_photo_available(self) -> bool:
        return bool(runtime_persona_setting(self, "enable_photo_text_action", True)) and self._image_companion_backend_available("tool_call")

    def _local_photo_generation_load_state(self, *, force_refresh: bool = False) -> dict[str, Any]:
        return self._image_companion_load_state(force_refresh=force_refresh)

    def _local_photo_generation_busy_state(self, *, force_refresh: bool = False) -> dict[str, Any] | None:
        state = self._local_photo_generation_load_state(force_refresh=force_refresh)
        if bool(state.get("enabled")) and bool(state.get("available")) and bool(state.get("busy")):
            return state
        return None

    def _action_has_photo_text(self, action: str) -> bool:
        return "photo_text" in {part.strip() for part in str(action or "").split("+") if part.strip()}

    def _proactive_photo_text_trigger_probability(
        self,
        reason: str,
        *parts: Any,
        user: dict[str, Any] | None = None,
    ) -> float:
        base = max(
            0.0,
            min(
                1.0,
                float(runtime_persona_setting(self, "proactive_photo_text_probability", 0.18)),
            ),
        )
        if base <= 0:
            return 0.0
        base = max(base, min(0.45, base + self._photo_text_overdue_boost(user)))
        hard_reasons = {"activity_share", "diary_share", "background_schedule", "noon_greeting", "evening_greeting"}
        soft_reasons = {"check_in", "quiet_care", "state_share"}
        text = " ".join(_single_line(part, 180) for part in parts if _single_line(part, 180))
        has_visual_cut = any(token in text for token in self._visual_share_tokens())
        if reason in hard_reasons:
            return base if has_visual_cut else base * 0.45
        if reason in soft_reasons and has_visual_cut:
            return base * 0.55
        return 0.0

    def _photo_text_load_defer_note(self, action: str = "photo_text", *, force_refresh: bool = False) -> str:
        if not self._action_has_photo_text(action):
            return ""
        if self._daily_token_soft_limit_should_defer("photo_prompt"):
            return (
                "每日 Token 软限额已暂缓主动生图"
                f"（今日已用约 {self._today_llm_token_total()} Token；软限额 {self.daily_token_soft_limit}）"
            )
        nai_selected = getattr(self, "_nai_image_selected", None)
        if callable(nai_selected) and nai_selected():
            return ""
        image_status = self._image_companion_status()
        selected_backend = _single_line(image_status.get("selected_backend"), 30)
        if selected_backend in {"external", "tool_call"}:
            return ""
        if selected_backend in {"sdgen", "anima_master"}:
            local_available = bool((image_status.get("backends") or {}).get(selected_backend))
        else:
            local_available = bool((image_status.get("backends") or {}).get("comfyui")) or (
                selected_backend == "auto" and bool((image_status.get("backends") or {}).get("sdgen"))
            )
        if not local_available:
            return ""
        state = self._local_photo_generation_busy_state(force_refresh=force_refresh)
        if not state:
            return ""
        if selected_backend == "auto" and bool((image_status.get("backends") or {}).get("external")):
            return ""
        return (
            "电脑高负荷,已延后本地生图"
            f"（{state.get('reason') or '负载偏高'}；"
            f"{runtime_persona_setting(self, 'local_photo_defer_minutes', 30)} 分钟后重试）"
        )

    def _defer_planned_photo_text_for_load(self, user: dict[str, Any], *, now: float, note: str) -> None:
        delay_seconds = max(
            60,
            int(runtime_persona_setting(self, "local_photo_defer_minutes", 30)) * 60,
        )
        self._defer_or_replace_planned_impulse(
            user,
            now=now,
            note=note,
            delay_minutes=(delay_seconds / 60, delay_seconds / 60 + min(5.0, delay_seconds / 300)),
            block_current=False,
        )
        user["proactive_sending"] = False
        user["proactive_sending_started_at"] = 0

    def _photo_text_available(self, user: dict[str, Any] | None = None) -> bool:
        if not runtime_persona_setting(self, "enable_photo_text_action", True):
            return False
        if isinstance(user, dict) and self._private_user_role(user) == "friend":
            return False
        if isinstance(user, dict):
            scope_quota_getter = getattr(self, "_photo_generation_scope_quota_left", None)
            if callable(scope_quota_getter):
                scope_left = scope_quota_getter(
                    proactive=True,
                    user=user,
                    user_id=str(user.get("user_id") or ""),
                )
                if scope_left is not None and scope_left <= 0:
                    return False
        if self._daily_token_soft_limit_should_defer("photo_prompt"):
            return False
        nai_selected = getattr(self, "_nai_image_selected", None)
        if callable(nai_selected) and nai_selected():
            if not self._nai_image_available():
                return False
        elif not self._image_companion_available():
            return False
        else:
            image_status = self._image_companion_status()
            selected_backend = _single_line(image_status.get("selected_backend"), 30)
            if selected_backend in {"comfyui", "sdgen"} and self._local_photo_generation_busy_state():
                return False
            if selected_backend == "auto" and self._local_photo_generation_busy_state():
                backends = image_status.get("backends") if isinstance(image_status.get("backends"), dict) else {}
                if not bool(backends.get("external") or backends.get("tool_call")):
                    return False
        photo_limit = self._effective_user_photo_daily_limit(user)
        if user and photo_limit == 0:
            return False
        if user and photo_limit > 0:
            today = _today_key()
            photo_sent_day = str(user.get("photo_sent_day") or "")
            photo_sent_today = _safe_int(user.get("photo_sent_today"), 0)
            photo_generated_day = str(user.get("photo_generated_day") or "")
            photo_generated_today = _safe_int(user.get("photo_generated_today"), 0)
            used_today = max(
                photo_sent_today if photo_sent_day == today else 0,
                photo_generated_today if photo_generated_day == today else 0,
            )
            if used_today >= photo_limit:
                return False
        return True

    def _photo_text_planning_available(self, user: dict[str, Any] | None = None) -> bool:
        try:
            return bool(self._photo_text_available(user))
        except Exception as exc:
            logger.debug("主动生图规划可用性检查失败: %s", _single_line(exc, 120))
            return False
