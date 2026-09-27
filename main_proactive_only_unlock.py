# -*- coding: utf-8 -*-
"""proactive_only_unlock。

由 tools/split_main_domain.py 从 main.py 机械抽取（12 个方法 / 173 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPlugin）。
"""
from __future__ import annotations

from .helpers import _safe_float, _safe_int, _single_line
from .main_shared import (
    _PROACTIVE_ONLY_TEMP_UNLOCK_GROUPS,
    _PROACTIVE_ONLY_TEMP_UNLOCK_LABELS,
    _PROACTIVE_ONLY_TEMP_UNLOCK_RELATED,
)
from .persona_config import runtime_persona_setting
from .photo_nai_params import extract_user_photo_nai_params
from astrbot.api.event import AstrMessageEvent
from typing import Any

from .logging_util import get_module_logger

logger = get_module_logger(__name__)

class PrivateCompanionPluginProactiveOnlyUnlockMixin:
    """proactive_only_unlock（从 PrivateCompanionPlugin 拆出）。"""

    def _proactive_only_unlock_store(self) -> set[str]:
        data = getattr(self, "data", None)
        if not isinstance(data, dict):
            return set()
        raw = data.get("proactive_only_temp_unlocks", [])
        if isinstance(raw, dict):
            items = raw.keys()
        elif isinstance(raw, (list, tuple, set)):
            items = raw
        else:
            items = []
        return {str(item).strip() for item in items if str(item or "").strip()}

    def _set_proactive_only_unlock_store(self, keys: set[str]) -> None:
        self.data["proactive_only_temp_unlocks"] = sorted(keys)

    def _proactive_only_unlock_label(self, key: str) -> str:
        return _PROACTIVE_ONLY_TEMP_UNLOCK_LABELS.get(key, key)

    def _proactive_only_temp_unlock_allows(self, feature: str = "") -> bool:
        unlocks = self._proactive_only_unlock_store()
        if not unlocks:
            return False
        if "all" in unlocks:
            return True
        feature = str(feature or "").strip()
        if not feature:
            return False
        if feature in unlocks:
            return True
        group = _PROACTIVE_ONLY_TEMP_UNLOCK_GROUPS.get(feature, set())
        return bool(group and (group & unlocks))

    def _feature_enabled_or_temp_unlocked(self, feature: str, default: bool = False) -> bool:
        if bool(runtime_persona_setting(self, feature, default)):
            return True
        return bool(
            runtime_persona_setting(self, 'enable_proactive_only_mode', False)
            and self._proactive_only_temp_unlock_allows(feature)
        )

    def _proactive_only_limited_passive_event(self, event: AstrMessageEvent | None) -> bool:
        return bool(
            runtime_persona_setting(self, 'enable_proactive_only_mode', False)
            and not bool(getattr(event, "private_companion_proactive_framework", False))
        )

    def _proactive_only_llm_request_needs_full_path(self) -> bool:
        unlocks = self._proactive_only_unlock_store()
        if "all" in unlocks or "llm_request" in unlocks:
            return True
        full_path_keys = {
            "inject_passive_states",
            "enable_intent_emotion_analysis",
            "enable_llm_timer_scheduling",
            "enable_passive_topic_suppression",
            "enable_private_image_self_recognition",
            "enable_group_companion",
            "enable_skill_growth_passive_injection",
            "enable_worldbook_member_recognition",
            "enable_livingmemory_integration",
        }
        return bool(full_path_keys & unlocks)

    def _clear_proactive_only_temp_unlocks_if_mode_off(self) -> None:
        if runtime_persona_setting(self, 'enable_proactive_only_mode', False):
            return
        if not self._proactive_only_unlock_store():
            return
        self.data["proactive_only_temp_unlocks"] = []
        self._schedule_data_save(sections={"proactive_only_temp_unlocks"})

    def _related_proactive_only_unlock_keys(self, key: str) -> list[str]:
        related = list(_PROACTIVE_ONLY_TEMP_UNLOCK_RELATED.get(key, []) or [])
        return [item for item in related if item and item != key]

    def _proactive_only_blocks_passive_event(self, event: AstrMessageEvent | None, feature: str = "") -> bool:
        proactive_framework = bool(getattr(event, "private_companion_proactive_framework", False))
        allow_proactive_photo = feature == "pc_generate_photo"
        effective_feature = "pc_tools" if allow_proactive_photo else feature
        if effective_feature == "pc_tools" and proactive_framework and not allow_proactive_photo:
            return True
        if not bool(runtime_persona_setting(self, 'enable_proactive_only_mode', False)):
            self._clear_proactive_only_temp_unlocks_if_mode_off()
            return False
        if proactive_framework:
            return False
        return not self._proactive_only_temp_unlock_allows(effective_feature)

    def _extract_user_photo_nai_params(self, text: str) -> str:
        return extract_user_photo_nai_params(text)

    async def _record_proactive_only_private_feedback(
        self,
        event: AstrMessageEvent,
        *,
        user_id: str,
        sender_display_name: str,
        text: str,
        received_ts: float,
    ) -> None:
        """主动专用模式下只记录用户回应,不接管被动回复链路。"""
        async with self._data_lock:
            users = self.data.get("users", {})
            canonical_user_id = self._canonical_private_user_id(user_id)
            user = users.get(canonical_user_id) if isinstance(users, dict) else None
            if not isinstance(user, dict):
                return
            user_id = canonical_user_id
            if not self._private_passive_profile_available(user_id, user):
                return
            if self._is_recent_poke_echo(user, text):
                logger.info("主动专用模式忽略 poke 回流事件: user=%s", user_id)
                return
            if self._is_duplicate_inbound_message(event, scope=f"private:{user_id}", sender_id=user_id, text=text):
                self._schedule_data_save(sections={"inbound_debounce_stats"})
                return
            self._note_private_user_umo(user_id, user, event.unified_msg_origin)
            self._note_private_display_name_observation(user, user_id, sender_display_name, now=received_ts)
            user["last_seen"] = received_ts
            user["last_activity_at"] = received_ts
            self._note_private_inbound_activity(user, received_ts, text=text)
            self._mark_greetings_satisfied_by_recent_activity(user, activity_ts=received_ts)
            self._note_morning_greeting_reply(user, now=received_ts)
            if self._cancel_inbound_conflicting_greeting(
                user,
                now=received_ts,
                user_id=user_id,
                trigger_umo=str(getattr(event, "unified_msg_origin", "") or ""),
            ):
                logger.info("用户已在当前问候时段自然来聊,已请求取消冲突问候候选: %s", user_id)
                if not self._simulation_active(user) and _safe_float(user.get("next_proactive_at"), 0) <= 0:
                    self._schedule_next_proactive(user, now=received_ts)
            if text:
                safe_text = self._sanitize_orphan_tts_placeholders(text)
                user["last_user_message"] = safe_text or text
                user["last_user_message_at"] = received_ts
                if self._clear_state_share_proactive_after_user_status_question(user, user_id=user_id, text=safe_text or text, now=received_ts):
                    if not self._simulation_active(user) and _safe_float(user.get("next_proactive_at"), 0) <= 0:
                        self._schedule_next_proactive(user, now=received_ts)
                user["inbound_count"] = _safe_int(user.get("inbound_count"), 0) + 1
                user["episode_message_count"] = _safe_int(user.get("episode_message_count"), 0, 0) + 1
                self._apply_user_rest_silence_from_message(user, safe_text or text, now=received_ts)
            if _safe_float(user.get("awaiting_reply_since"), 0) > 0:
                audit_outcome_recorder = getattr(self, "_mark_proactive_audit_reply_outcome", None)
                if callable(audit_outcome_recorder):
                    audit_outcome_recorder(
                        user,
                        received_at=received_ts,
                        message_id=self._event_message_id(event),
                    )
                user["reply_count"] = _safe_int(user.get("reply_count"), 0) + 1
                self._note_action_reply_feedback(
                    user,
                    str(user.get("last_proactive_action") or "message"),
                    text,
                )
                self._apply_relationship_event(
                    user,
                    2,
                    reason_code="proactive_reply",
                    event_id=self._event_message_id(event),
                    now=received_ts,
                )
                user["awaiting_reply_since"] = 0
                user["last_reply_at"] = received_ts
                user["last_private_reply_at"] = received_ts
                user["pending_followup_event"] = {}
                user["planned_proactive_quota_exempt"] = False
            user["ignored_streak"] = 0
            user["friend_unanswered_silenced_since"] = 0
            user["friend_unanswered_silence_note"] = ""
            meal_care_result: dict[str, Any] = {}
            if self._private_user_role(user, user_id) == "owner" and text:
                meal_care_result = self._handle_meal_care_inbound(user, text, now=received_ts)
            save_sections = {"users"}
            if meal_care_result.get("foods"):
                save_sections.add("food_menu")
            self._schedule_data_save(sections=save_sections)
        logger.info(
            "主动消息专用模式已跳过私聊被动增强: user=%s text=%s",
            user_id,
            _single_line(text, 80) or "非文本消息",
        )
