# -*- coding: utf-8 -*-
"""PrivateCompanionPluginReq036UnifiedPersonPart04Mixin。

由 tools/split_mixin_domain.py 从 main_req036_unified_person.py 机械抽取（3 个方法 + 0 个模块级名字 + 0 个类级赋值 / 178 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPluginReq036UnifiedPersonMixin）。
"""
from __future__ import annotations

from .main_req036_unified_person_shared import filter
from .main_req036_unified_person_shared import logger
from .main_req036_unified_person_shared import AstrMessageEvent
from .main_req036_unified_person_shared import ProviderRequest
from .main_req036_unified_person_shared import _multi_persona_event_context
from .main_req036_unified_person_shared import _now_ts
from .main_req036_unified_person_shared import _single_line
from .main_req036_unified_person_shared import build_expression_decision
from .main_req036_unified_person_shared import content_intent_from_text
from .main_req036_unified_person_shared import event_data_save_boundary
from .main_req036_unified_person_shared import expression_decision_prompt_section
from .main_req036_unified_person_shared import hashlib
from .main_req036_unified_person_shared import runtime_persona_setting



class PrivateCompanionPluginReq036UnifiedPersonPart04Mixin:
    """PrivateCompanionPluginReq036UnifiedPersonPart04Mixin（从 PrivateCompanionPluginReq036UnifiedPersonMixin 拆出）。"""


    @filter.on_llm_request(priority=-30000)
    @_multi_persona_event_context
    async def inject_unified_relationship_expression(self, event: AstrMessageEvent, req: ProviderRequest, *args, **kwargs):
        """Inject one fail-closed relationship expression decision before Memory enrichment."""
        if self is None or req is None or not bool(getattr(self, "enabled", False)):
            return
        if not bool(runtime_persona_setting(self, 'enable_custom_relationship_stage_policy', False)):
            return
        is_private = self._safe_event_is_private(event)
        group_id = "" if is_private else self._extract_group_id_from_event(event)
        if not is_private and not group_id:
            return
        raw_sender_id = self._safe_event_sender_id(event)
        current_user = None
        if is_private:
            try:
                resolver = getattr(self, "_private_user_id_for_event", None)
                sender_id = (
                    resolver(event)
                    if callable(resolver)
                    else self._canonical_private_user_id(raw_sender_id)
                )
            except Exception:
                sender_id = ""
            users = self.data.get("users", {}) if isinstance(getattr(self, "data", None), dict) else {}
            current_user = users.get(sender_id) if sender_id and isinstance(users, dict) else None
        else:
            projection_getter = getattr(self, "_req039_group_observation_projection", None)
            if callable(projection_getter):
                current_user = projection_getter(
                    event,
                    sender_id=raw_sender_id,
                    sender_name=self._sender_display_name(event),
                )
        if not isinstance(current_user, dict):
            return
        fixture_user = self._lab_fixture_relationship_view(event, current_user)
        fixture_relationship_applied = fixture_user is not current_user
        current_user = fixture_user
        expression_builder = getattr(self, "_build_expression_decision_for_user", None)
        if not callable(expression_builder):
            return
        try:
            expression_args = {
                "passive_reengagement": True,
                "bot_state": {
                    "energy": current_user.get("bot_energy", 70),
                    "mood": current_user.get("bot_mood", ""),
                },
                "message_intent": content_intent_from_text(getattr(event, "message_str", "")),
                "content_policy": {
                    "enabled": bool(runtime_persona_setting(self, 'enable_relationship_content_tiers', False)),
                    "flirt_enabled": bool(runtime_persona_setting(self, 'enable_flirt_content_tier', True)),
                    "private_chat": is_private,
                },
                "channel_scope": "private" if is_private else "group",
            }
            if fixture_relationship_applied:
                expression_args["_authoritative_relationship_view"] = True
            expression = expression_builder(current_user, **expression_args)
            projection = expression.to_dict() if hasattr(expression, "to_dict") else dict(expression or {})
            if is_private:
                violation_hint_getter = getattr(self, "_relationship_violation_prompt_hint", None)
                if callable(violation_hint_getter):
                    hint = violation_hint_getter(current_user, now=_now_ts())
                    if hint:
                        projection["relationship_violation_hint"] = hint
        except Exception as exc:
            logger.debug("统一表达决策生成失败，使用日常保守默认值: %s", _single_line(exc, 120))
            projection = build_expression_decision({}).to_dict()
        try:
            setattr(req, "_private_companion_expression_decision", projection)
            setattr(event, "_private_companion_expression_decision", projection)
        except Exception:
            pass
        section = expression_decision_prompt_section(projection)
        self._append_turn_prompt_fragment_by_position(
            req,
            "<!-- private_companion_expression_decision_v2 -->",
            section,
            priority=5,
            force_dynamic=True,
        )

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE, priority=220000)
    @_multi_persona_event_context
    @event_data_save_boundary
    async def guard_req036_private_capability_early(self, event: AstrMessageEvent, *args, **kwargs):
        """Reject an unauthorized private event before any normal message plugin runs."""
        if self is None or bool(getattr(event, "private_companion_req036_denied", False)):
            return
        inbound_checker = getattr(self, "_event_is_inbound_chat_message", None)
        if callable(inbound_checker) and not inbound_checker(event):
            logger.debug("非入站聊天事件跳过私聊档案预建")
            return
        try:
            user_id = str(event.get_sender_id())
        except Exception:
            user_id = ""
        self_id = self._event_self_id(event)
        if user_id and self_id and user_id == self_id:
            return
        sender_display_name = _single_line(self._sender_display_name(event), 40)
        async with self._data_lock:
            private_user, auto_profile_created = self._ensure_auto_private_user_profile(
                event,
                user_id=user_id,
                sender_display_name=sender_display_name,
                now=_now_ts(),
            )
            if isinstance(private_user, dict):
                user_id = _single_line(private_user.get("user_id"), 160) or user_id
            migrator = getattr(self, "_req036_migrate_configured_target_capability", None)
            migrated = bool(migrator(user_id, private_user)) if callable(migrator) else False
            if migrated:
                self._schedule_data_save(sections={"users"})
        if auto_profile_created:
            logger.info(
                "已建立最小用户档案: user=%s platform=%s",
                _single_line(self._canonical_private_user_id(user_id), 80),
                _single_line(self._platform_kind_for_event(event), 40),
            )

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE, priority=180000)
    @_multi_persona_event_context
    @event_data_save_boundary
    async def guard_req036_group_portrait_queries(self, event: AstrMessageEvent, *args, **kwargs):
        """Reject third-party portrait probing before any retrieval or LLM hook."""
        if self is None or bool(getattr(event, "_private_companion_member_safety_blocked", False)):
            return
        if not bool(runtime_persona_setting(self, 'enable_group_third_party_portrait_guard', True)):
            return
        group_id = self._extract_group_id_from_event(event)
        if not group_id:
            return
        if not self._req036_group_portrait_query_is_directed(event):
            return
        text = self._group_observation_event_text(event)
        kind = self._req036_group_portrait_query_kind(text)
        if not kind:
            return
        if kind == "bot_self":
            return
        if kind == "third_party":
            logger.info(
                "群聊第三方画像查询已拦截: reason=explicit_third_party_query group_hash=%s text_hash=%s text_len=%s",
                hashlib.sha256(str(group_id).encode("utf-8", errors="ignore")).hexdigest()[:12],
                hashlib.sha256(str(text).encode("utf-8", errors="ignore")).hexdigest()[:12],
                len(str(text)),
            )
            event.stop_event()
            await self._reply(event, "这个我不方便替别人整理啦。")
            return
        # An observation-disabled group must not become a wording bypass.  It
        # still does not receive normal group capture; this narrow explicit
        # self-query only prepares a minimal identity/scene reference for the
        # low-sensitivity, same-person Memory request below.
        if not isinstance(getattr(event, "private_companion_unified_profile_context", None), dict):
            try:
                raw_sender_id = str(event.get_sender_id())
                resolver = getattr(self, "_event_private_user_storage_id", None)
                sender_id = (
                    resolver(event, raw_sender_id)
                    if callable(resolver)
                    else self._canonical_private_user_id(raw_sender_id)
                )
            except Exception:
                sender_id = ""
            async with self._data_lock:
                users = self.data.get("users", {}) if isinstance(self.data, dict) else {}
                user = users.get(sender_id) if sender_id and isinstance(users, dict) else None
                self._req036_attach_unified_profile_context(
                    event,
                    user=user if isinstance(user, dict) else None,
                    group_id=group_id,
                    source="group_portrait_query",
                )
                self._schedule_data_save(sections={"users", "unified_person"})
        event.stop_event()
        await self._reply(event, await self._req036_read_group_self_portrait(event))
