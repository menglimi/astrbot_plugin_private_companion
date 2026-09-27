# -*- coding: utf-8 -*-
"""群组入站捕获域。

由 tools/split_main_domain.py 从 main.py 机械抽取（10 个方法 / 577 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPlugin）。
"""
from __future__ import annotations

from .conversation_injection_plan import PLACEMENT_DYNAMIC_SYSTEM
from .group_cycle_boundary import build_group_cycle_boundary, group_cycle_boundary_prompt_section
from .helpers import _now_ts, _safe_float, _safe_int, _single_line
from .main_shared import _multi_persona_event_context, _plugin_instance_can_dispatch
from .message_pipeline import event_data_save_boundary
from .persona_config import runtime_persona_setting
from .unified_profile_service import ensure_new_profile_capabilities as req036_ensure_new_profile_capabilities
from .wake_message_context import capture_wake_message_context
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest
from astrbot.api.message_components import Plain
from copy import deepcopy
from datetime import datetime
from typing import Any

from .logging_util import get_module_logger

logger = get_module_logger(__name__)

class PrivateCompanionPluginGroupInboundCaptureMixin:
    """群组入站捕获域（从 PrivateCompanionPlugin 拆出）。"""

    def _req039_group_observation_projection(
        self,
        event: Any,
        *,
        sender_id: str,
        sender_name: str = "",
    ) -> dict[str, Any] | None:
        """Build a transient group-speaker projection without creating a DM user."""
        raw_sender_id = _single_line(sender_id, 160)
        normalizer = getattr(self, "_normalize_private_identity_id", None)
        normalized_sender_id = normalizer(raw_sender_id) if callable(normalizer) else raw_sender_id
        normalized_sender_id = normalized_sender_id or raw_sender_id
        self_getter = getattr(self, "_event_self_id", None)
        try:
            self_id = _single_line(self_getter(event), 160) if callable(self_getter) else ""
        except Exception:
            self_id = ""
        if not normalized_sender_id or normalized_sender_id == self_id or raw_sender_id == self_id:
            return None
        canonical = _single_line(self._canonical_private_user_id(normalized_sender_id), 160)
        bot_checker = getattr(self, "_is_bot_self_user_id", None)
        if not canonical or (callable(bot_checker) and bot_checker(canonical)):
            return None
        display_name = _single_line(sender_name, 80) or canonical
        profiles = self.data.get("worldbook_member_profiles") if isinstance(getattr(self, "data", None), dict) else {}
        observation = profiles.get(normalized_sender_id) if isinstance(profiles, dict) else None
        if isinstance(observation, dict) and bool(observation.get("observation_only")):
            display_name = _single_line(observation.get("name"), 80) or display_name
        projection: dict[str, Any] = {
            "user_id": canonical,
            "nickname": display_name,
            "enabled": False,
            "manual_enabled": False,
            "manual_disabled": False,
            "relationship_role": "friend",
            "relationship_mode": "normal",
            "relationship_score": 0,
            "current_interaction": {},
            "profile_origin": "group_observation",
            "projection_kind": "group_observation",
            "observation_only": True,
            "private_companion_enabled": False,
            "proactive_private_enabled": False,
        }
        identity_context_getter = getattr(self, "_private_event_identity_context", None)
        if callable(identity_context_getter):
            try:
                identity_context = identity_context_getter(event, normalized_sender_id)
            except Exception:
                identity_context = {}
            if isinstance(identity_context, dict):
                projection["identity_subject_id"] = _single_line(identity_context.get("subject"), 128)
                projection["identity_platform_kind"] = _single_line(identity_context.get("platform"), 40)
                projection["identity_adapter_instance_id"] = _single_line(identity_context.get("adapter"), 120)
                projection["identity_bot_id"] = _single_line(identity_context.get("bot_id"), 120)
        req036_ensure_new_profile_capabilities(projection)
        return projection

    @filter.event_message_type(filter.EventMessageType.ALL, priority=230000)
    async def preserve_addressed_user_message(self, event: AstrMessageEvent, *args, **kwargs):
        """Keep the original chat wording separately from the command-routing text."""
        if (
            self is None or not _plugin_instance_can_dispatch(self)
            or not self.enabled or not self._bot_scope_allows_event(event)
        ):
            return
        capture_wake_message_context(self, event)

    @filter.event_message_type(filter.EventMessageType.ALL, priority=10000)
    @_multi_persona_event_context
    async def observe_recall_enhancement_events(self, event: AstrMessageEvent, *args, **kwargs):
        """记录普通消息和 QQ/OneBot 撤回事件，用于撤回增强。"""
        if self is None:
            return
        if self._is_onebot_poke_notice_event(event):
            # OneBot 把戳一戳同时映射为消息事件。它由专用插件处理，不能参与
            # 陪伴的活动、繁忙闸门或撤回缓存链路。
            logger.debug("放行 OneBot 戳一戳 notice 给专用插件")
            return
        self._qzone_note_event_bot(event)
        if not self.enabled:
            return
        self._note_inbound_activity_for_scope(event)
        self._busy_reply_note_inbound_event(event)
        if not runtime_persona_setting(self, 'enable_recall_enhancement', True):
            return
        raw = self._event_raw_payload(event)
        if raw.get("post_type") == "notice":
            notice_type = str(raw.get("notice_type") or "").strip()
            if notice_type not in {"friend_recall", "group_recall"}:
                return
            message_id = _single_line(raw.get("message_id") or raw.get("msg_id"), 120)
            if not message_id:
                return
            scope = _single_line(
                (f"group:{raw.get('group_id')}" if raw.get("group_id") else "")
                or (f"private:{raw.get('user_id')}" if raw.get("user_id") else "")
                or getattr(event, "unified_msg_origin", ""),
                160,
            )
            recall_user_id = _single_line(raw.get("user_id"), 80)
            self._record_recalled_message_id(
                message_id,
                scope=scope,
                notice_type=notice_type,
                sender_id=recall_user_id,
            )
            if notice_type == "group_recall":
                recall_group_id = _single_line(raw.get("group_id"), 80)
                if recall_group_id and recall_user_id:
                    try:
                        await self._note_group_joke_boundary_recall(recall_group_id, recall_user_id)
                    except Exception as exc:
                        logger.debug(
                            "[PrivateCompanion] 撤回信号写入接梗边界失败: %s",
                            _single_line(exc, 120),
                        )
            if notice_type == "friend_recall":
                if recall_user_id:
                    self._stop_passive_input_status_loop(recall_user_id)
                    logger.info(
                        "用户撤回消息，已停止私聊输入状态: user=%s message_id=%s",
                        recall_user_id,
                        message_id,
                    )
            logger.info(
                "已记录消息撤回: notice=%s scope=%s message_id=%s",
                notice_type,
                scope or "-",
                message_id,
            )
            return

        await self._cache_message_for_recall(event)
        if not runtime_persona_setting(self, 'enable_forbidden_word_recall', False) or not self._forbidden_recall_words():
            return
        message_id = self._event_message_id(event)
        if not message_id:
            return
        is_group = bool(self._extract_group_id_from_event(event))
        is_self = self._event_sender_id(event) and self._event_sender_id(event) == self._event_self_id(event)
        scope = runtime_persona_setting(self, 'recall_forbidden_scope', 'bot_and_group')
        if scope == "bot_only" and not is_self:
            return
        if scope == "group_only" and not is_group:
            return
        if scope == "bot_and_group" and not (is_self or is_group):
            return
        text = self._event_text_for_recall_cache(event, limit=2000)
        hit = self._forbidden_recall_hit(text)
        if not hit:
            return
        ok = await self._try_delete_message(event, message_id, reason=f"forbidden:{hit}")
        logger.info(
            "违禁词撤回检查命中: scope=%s self=%s group=%s ok=%s word=%s message_id=%s",
            scope,
            is_self,
            is_group,
            ok,
            _single_line(hit, 40),
            message_id,
        )

    @filter.on_decorating_result(priority=20000)
    @_multi_persona_event_context
    async def consume_group_member_safety_hidden_marker(self, event: AstrMessageEvent, *args, **kwargs):
        """Consume the reply model's internal member-risk decision before any outbound transform."""
        if self is None:
            return
        result = event.get_result()
        chain = list(getattr(result, "chain", []) or []) if result is not None else []
        plain_components = [component for component in chain if isinstance(component, Plain)]
        if not plain_components:
            return

        combined_original = "".join(str(getattr(component, "text", "") or "") for component in plain_components)
        combined_cleaned, combined_decisions = self._extract_group_member_safety_hidden_markers(combined_original)
        rebuilt: list[Any] = []
        per_component_cleaned: list[str] = []
        per_component_decisions: list[dict[str, Any]] = []
        changed = False
        for component in plain_components:
            original = str(getattr(component, "text", "") or "")
            cleaned, decisions = self._extract_group_member_safety_hidden_markers(original)
            per_component_cleaned.append(cleaned)
            per_component_decisions.extend(decisions)
            changed = changed or cleaned != original

        cross_component_marker = "".join(per_component_cleaned) != combined_cleaned
        first_plain_written = False
        plain_index = 0
        for component in chain:
            if not isinstance(component, Plain):
                rebuilt.append(component)
                continue
            if cross_component_marker:
                if not first_plain_written and combined_cleaned:
                    rebuilt.append(Plain(combined_cleaned))
                    first_plain_written = True
                changed = True
            else:
                cleaned = per_component_cleaned[plain_index]
                if cleaned:
                    rebuilt.append(Plain(cleaned) if cleaned != str(getattr(component, "text", "") or "") else component)
                plain_index += 1
        if changed:
            try:
                result.chain = rebuilt
            except Exception:
                event.set_result(self._build_result_from_chain(rebuilt))

        decisions = combined_decisions if combined_decisions else per_component_decisions
        if not decisions:
            return
        if (
            self._group_member_safety_hidden_marker_mode() == "disabled"
            or not bool(getattr(event, "_private_companion_member_safety_hidden_marker_expected", False))
        ):
            logger.warning(
                "解析到未授权的群成员风控隐性标签，未计数: session=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            )
            return
        if bool(getattr(event, "_private_companion_member_safety_hidden_marker_consumed", False)):
            return
        setattr(event, "_private_companion_member_safety_hidden_marker_consumed", True)
        decision = max(
            decisions,
            key=lambda item: (_safe_float(item.get("confidence"), 0.0), _safe_int(item.get("severity"), 1, 1, 3)),
        )
        group_id = _single_line(getattr(event, "_private_companion_member_safety_group_id", ""), 128)
        sender_id = _single_line(getattr(event, "_private_companion_member_safety_sender_id", ""), 128)
        sender_name = _single_line(getattr(event, "_private_companion_member_safety_sender_name", ""), 60)
        source_text = str(getattr(event, "_private_companion_member_safety_message_text", "") or "")
        if not group_id or not sender_id:
            return
        recorded = await self._record_group_member_safety_decision(
            event,
            group_id=group_id,
            sender_id=sender_id,
            sender_name=sender_name,
            text=source_text,
            decision=decision,
            source="reply_hidden_marker",
        )
        logger.info(
            "已消费群成员风控隐性标签: group=%s sender=%s counted=%s blocked=%s reason=%s",
            group_id,
            sender_id,
            bool(recorded.get("counted")),
            bool(recorded.get("blocked")),
            _single_line(recorded.get("reason"), 80),
        )

    @filter.on_llm_request(priority=-20400)
    @_multi_persona_event_context
    async def append_group_cycle_privacy_boundary(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        *args,
        **kwargs,
    ):
        """Add a default-off, request-only Bot cycle privacy boundary for allowed groups."""
        if self is None or req is None or not bool(runtime_persona_setting(self, 'enable_group_cycle_awareness', False)):
            return
        try:
            if bool(getattr(event, "is_private_chat", lambda: False)()):
                return
        except Exception:
            return
        group_id = self._extract_group_id_from_event(event)
        if not group_id or not self._group_enabled_for_event(group_id):
            return
        daily_state = self.data.get("daily_state") if isinstance(getattr(self, "data", None), dict) else {}
        if not isinstance(daily_state, dict):
            return
        inbound_text = getattr(event, "private_companion_group_text", "") or getattr(event, "message_str", "") or ""
        boundary = build_group_cycle_boundary(
            enabled=True,
            group_allowed=True,
            cycle_label=daily_state.get("body_cycle"),
            inbound_text=inbound_text,
        )
        boundary_section = group_cycle_boundary_prompt_section(boundary)
        if boundary_section is None:
            return
        marker = "<!-- private_companion_group_cycle_boundary_v1 -->"
        current_prompt = str(getattr(req, "system_prompt", "") or "")
        current_turn_prompt = str(getattr(req, "prompt", "") or "")
        if marker in current_prompt or marker in current_turn_prompt:
            return
        placement = "prompt" if self._append_turn_prompt_fragment_by_position(
            req,
            marker,
            boundary_section,
            priority=59,
        ) else "system_prompt"
        if placement == "system_prompt" and hasattr(req, "system_prompt"):
            self._materialize_conversation_system_block(
                req,
                section=boundary_section,
                marker=marker,
                priority=59,
                placement=PLACEMENT_DYNAMIC_SYSTEM,
            )

    def _record_c3_inbound_activity(
        self,
        event: AstrMessageEvent,
        *,
        text: str,
        received_ts: float,
        user_id: str = "",
        group_id: str = "",
        sender_id: str = "",
        sender_name: str = "",
    ) -> dict[str, Any] | None:
        """Feed the local C3 activity aggregator without changing the reply path."""
        if not _single_line(text, 400):
            return None
        capture = getattr(self, "_agenda_capture_inbound_message", None)
        if not callable(capture):
            return None
        scope = "group" if _single_line(group_id, 80) else "private"
        subject_id = _single_line(group_id or user_id, 120)
        conversation_id = f"{scope}:{subject_id}" if subject_id else scope
        source_ref = _single_line(self._event_message_id(event), 160)
        if not source_ref:
            source_ref = _single_line(getattr(event, "unified_msg_origin", ""), 180)
        if not source_ref:
            source_ref = f"{conversation_id}:{int(received_ts)}"
        try:
            event_time = self._environment_fromtimestamp(received_ts)
        except Exception:
            event_time = datetime.fromtimestamp(received_ts).astimezone()
        try:
            result = capture(
                text=_single_line(text, 400),
                event_time=event_time,
                source_ref=source_ref,
                conversation_id=conversation_id,
                participant=_single_line(sender_name or sender_id or "user", 120),
                message_count=1,
                visibility="group" if scope == "group" else "private",
            )
            if isinstance(result, dict):
                result["scope"] = scope
            if result is not None and scope == "private":
                recorder = getattr(self, "_memory_companion_record_observed_activity", None)
                if callable(recorder):
                    self._create_lifecycle_background_task(
                        recorder(result),
                        label="record_observed_activity",
                    )
            return result
        except Exception as exc:
            logger.debug(
                "C3 activity capture skipped: scope=%s id=%s error=%s",
                scope,
                subject_id or "-",
                _single_line(exc, 160),
            )
            return None

    async def _capture_group_observation_event(
        self,
        event: AstrMessageEvent,
        *,
        group_id: str,
        sender_id: str,
        sender_name: str,
        text: str,
        scene: dict[str, Any] | None = None,
    ) -> bool:
        async with self._data_lock:
            group = self._get_group(group_id)
            group["umo"] = _single_line(getattr(event, "unified_msg_origin", ""), 160)
            effective_scene = scene or self._infer_group_scene(
                event,
                group,
                sender_id=sender_id,
                sender_name=sender_name,
                text=text,
            )
            captured = self._capture_group_observation_once(
                group,
                sender_id=sender_id,
                sender_name=sender_name,
                text=text,
                group_id=group_id,
                scene=effective_scene,
                message_id=self._event_message_id(event),
                event=event,
            )
            if captured:
                    self._schedule_data_save(sections={"groups"})
        activity_recorder = getattr(self, "_record_c3_inbound_activity", None)
        if callable(activity_recorder):
            activity_recorder(
                event,
                text=text,
                received_ts=_now_ts(),
                group_id=group_id,
                sender_id=sender_id,
                sender_name=sender_name,
            )
        if (
            self._group_role_context_requested(text)
            and not bool(getattr(event, "_private_companion_group_role_refreshed", False))
        ):
            await self._refresh_group_role_snapshot(event, group_id, force=True)
            setattr(event, "_private_companion_group_role_refreshed", True)
        return captured

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE, priority=210000)
    @_multi_persona_event_context
    @event_data_save_boundary
    async def guard_blocked_group_member_early(self, event: AstrMessageEvent, *args, **kwargs):
        """在群聊观察和回复插件之前丢弃已静默成员的消息。"""
        if self is None or self._is_onebot_poke_notice_event(event):
            return
        if not self._feature_enabled_or_temp_unlocked("enable_group_companion"):
            return
        if not bool(runtime_persona_setting(self, 'enable_group_member_safety', True)):
            return
        group_id = self._extract_group_id_from_event(event)
        if not group_id or not self._group_enabled_for_event(group_id):
            return
        try:
            sender_id = str(event.get_sender_id())
        except Exception:
            sender_id = ""
        if not sender_id or sender_id == self._event_self_id(event):
            return
        async with self._data_lock:
            group = self._get_group(group_id)
            member = self._group_member_safety_member(group, sender_id, create=False)
            if not isinstance(member, dict):
                return
            if not bool(member.get("manual_blocked")) and self._group_member_safety_is_exempt_event(event, sender_id):
                return
            was_blocked = bool(member and (_safe_float(member.get("blocked_at"), 0) > 0 or member.get("manual_blocked")))
            blocked = self._group_member_safety_active(member, expire=True)
            if was_blocked and not blocked:
                self._save_data_sync(sections={"groups"})
        if blocked:
            logger.info(
                "已静默群成员消息: group=%s sender=%s",
                group_id,
                sender_id,
            )
            self._stop_group_member_safety_event(event)

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE, priority=200000)
    @_multi_persona_event_context
    @event_data_save_boundary
    async def capture_group_observation_early(self, event: AstrMessageEvent, *args, **kwargs):
        """Record allowed group messages before reply plugins can stop propagation."""
        if self is None or self._is_onebot_poke_notice_event(event):
            return
        if not self._feature_enabled_or_temp_unlocked("enable_group_companion"):
            return
        group_id = self._extract_group_id_from_event(event)
        if not group_id or not self._group_enabled_for_event(group_id):
            return
        try:
            sender_id = str(event.get_sender_id())
        except Exception:
            sender_id = ""
        self_id = self._event_self_id(event)
        if sender_id and self_id and sender_id == self_id:
            return
        text = self._group_observation_event_text(event)
        if not text or text.startswith(("陪伴群", "/陪伴群", "群陪伴", "群聊陪伴")):
            return
        if self._message_debounce_command_text(event, text):
            return
        sender_name = self._sender_display_name(event)
        await self._capture_group_observation_event(
            event,
            group_id=group_id,
            sender_id=sender_id,
            sender_name=sender_name,
            text=text,
        )
        repeat_group_snapshot: dict[str, Any] = {}
        repeat_scene: dict[str, Any] = {}
        async with self._data_lock:
            repeat_group = self._get_group(group_id)
            repeat_group_snapshot = deepcopy(repeat_group)
            repeat_scene = self._infer_group_scene(
                event,
                repeat_group,
                sender_id=sender_id,
                sender_name=sender_name,
                text=text,
            )
            projection_getter = getattr(self, "_req039_group_observation_projection", None)
            observed_user = (
                projection_getter(event, sender_id=sender_id, sender_name=sender_name)
                if callable(projection_getter)
                else None
            )
            self._req036_attach_unified_profile_context(
                event,
                user=observed_user if isinstance(observed_user, dict) else None,
                group_id=group_id,
                source="group_observation",
            )
            self._schedule_data_save(sections={"unified_person"})
        if not self._proactive_only_blocks_passive_event(event, "group_repeat_early"):
            original_repeat_state = deepcopy(repeat_group_snapshot.get("repeat_follow_state", {}))
            original_interject_at = _safe_float(repeat_group_snapshot.get("last_interject_at"), 0)
            await self._maybe_group_interject(
                event,
                repeat_group_snapshot,
                text,
                allow_interjection=False,
                repeat_scene=repeat_scene,
            )
            repeat_state_changed = repeat_group_snapshot.get("repeat_follow_state") != original_repeat_state
            repeat_acted = _safe_float(repeat_group_snapshot.get("last_interject_at"), 0) > original_interject_at
            if repeat_state_changed or repeat_acted:
                async with self._data_lock:
                    current = self._get_group(group_id)
                    current["repeat_follow_state"] = repeat_group_snapshot.get("repeat_follow_state", {})
                    if repeat_acted:
                        current["last_interject_at"] = repeat_group_snapshot.get("last_interject_at", 0)
                        current["interject_day"] = repeat_group_snapshot.get("interject_day", "")
                        current["interject_today"] = repeat_group_snapshot.get("interject_today", 0)
                        current["last_bot_interjection"] = repeat_group_snapshot.get("last_bot_interjection", {})
                        current["recent_bot_replies"] = deepcopy(
                            repeat_group_snapshot.get("recent_bot_replies", current.get("recent_bot_replies", []))
                        )
                    self._save_data_sync(sections={"groups"})
        self._start_group_image_understanding(
            event,
            group_id=group_id,
            sender_id=sender_id,
            text=text,
        )

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE, priority=190000)
    @_multi_persona_event_context
    @event_data_save_boundary
    async def review_group_member_safety_early(self, event: AstrMessageEvent, *args, **kwargs):
        """在回复链路前保守审核当前消息，达到阈值时立即静默。"""
        if self is None or self._is_onebot_poke_notice_event(event):
            return
        if bool(getattr(event, "_private_companion_member_safety_blocked", False)):
            return
        if not self._feature_enabled_or_temp_unlocked("enable_group_companion"):
            return
        if not bool(runtime_persona_setting(self, 'enable_group_member_safety', True)):
            return
        if self._group_member_safety_hidden_marker_mode() == "reply_only":
            return
        group_id = self._extract_group_id_from_event(event)
        if not group_id or not self._group_enabled_for_event(group_id):
            return
        try:
            sender_id = str(event.get_sender_id())
        except Exception:
            sender_id = ""
        if not sender_id or sender_id == self._event_self_id(event):
            return
        text = self._group_observation_event_text(event)
        if not text or text.startswith(("陪伴群", "/陪伴群", "群陪伴", "群聊陪伴")):
            return
        if self._message_debounce_command_text(event, text):
            return
        result = await self._review_group_member_safety_message(
            event,
            group_id=group_id,
            sender_id=sender_id,
            sender_name=self._sender_display_name(event),
            text=text,
        )
        if result.get("blocked"):
            logger.warning(
                "群成员风险次数达到阈值，已静默当前消息: group=%s sender=%s",
                group_id,
                sender_id,
            )
            self._stop_group_member_safety_event(event)
