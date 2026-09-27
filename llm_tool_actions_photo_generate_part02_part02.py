# -*- coding: utf-8 -*-
"""LlmToolActionsPhotoGeneratePart02Part02Mixin。

由 tmp/refactor/lta2_split.py 从 llm_tool_actions_photo_generate_part02.py 的 _pc_generate_photo_impl 段级拆分而来（identity）。
段体与拆分前逐字节相同；仅段末追加 `return _StageNext(...)` 交还活跃局部名。
所有 self.xxx 依赖通过继承链解析（宿主类 LlmToolActionsPhotoGenerateMixin）。
"""
from __future__ import annotations

from typing import Any

from .helpers import _single_line
from .llm_tool_actions_photo_generate_part02_shared import _StageNext
from .persona_config import runtime_persona_setting


class LlmToolActionsPhotoGeneratePart02Part02Mixin:
    """_pc_generate_photo_impl 的 identity 段。"""

    async def _pc_generate_photo_impl_identity(
        self,
        compact_prompt,
        content,
        event,
        group_photo_requested,
        image_size,
        inbound_photo_text,
        inherited_nai_params,
        intent_kind,
        kwargs,
        legacy_generator,
        proactive_request,
        public_receipt,
        reference_image_path,
        reference_image_paths,
        scene_preset,
        scope_checker,
        send,
        structured_generator,
        tool_started_at,
        visible_caption,
        workflow_kind,
    ):
        """_pc_generate_photo_impl 段：请求者识别、私聊/群聊授权与额度闸门。"""
        try:
            requester_id = str(event.get_sender_id())
        except Exception:
            requester_id = ""
        resolver = getattr(self, "_private_user_id_for_event", None)
        if callable(resolver) and requester_id:
            requester_id = resolver(event, requester_id)
        requester = None
        request_scope = "private"
        group_gate_message = "当前群聊未启用陪伴功能，或请求者身份不可用。"
        user_getter = getattr(self, "_get_user", None)
        if callable(user_getter):
            if not requester_id:
                return public_receipt(
                    {
                        "status": "unauthorized",
                        "success": False,
                        "generated": False,
                        "sent": False,
                        "message": "这个生图工具只对已启用的陪伴对象开放。",
                        "must_not_claim_sent": True,
                        "retryable": False,
                    },
                    ensure_ascii=False,
                )
            # Group senders are not private users by default.  Looking them up
            # through ``_get_user`` would create a new private record (and the
            # configured fallback nickname) before authorization can reject it.
            scope_getter = getattr(self, "_reaction_expression_scope", None)
            try:
                private_marker = getattr(event, "is_private_chat", None)
                event_is_private = (
                    bool(private_marker())
                    if callable(private_marker)
                    else (True if private_marker is None else bool(private_marker))
                )
                request_scope = (
                    _single_line(scope_getter(event), 16).casefold()
                    if callable(scope_getter)
                    else ("private" if event_is_private else "group")
                )
            except Exception:
                request_scope = "private"
            def existing_private_user(raw_id: str) -> dict[str, Any] | None:
                data = getattr(self, "data", None)
                users = data.get("users") if isinstance(data, dict) else None
                if not isinstance(users, dict):
                    return None
                normalized = _single_line(raw_id, 160)
                if not normalized:
                    return None
                canonical = normalized
                canonicalizer = getattr(self, "_canonical_private_user_id", None)
                if callable(canonicalizer):
                    try:
                        canonical = _single_line(canonicalizer(normalized), 160) or normalized
                    except Exception:
                        canonical = normalized
                for candidate_id in dict.fromkeys((normalized, canonical)):
                    candidate = users.get(candidate_id)
                    if isinstance(candidate, dict):
                        return candidate
                for candidate in users.values():
                    if not isinstance(candidate, dict):
                        continue
                    aliases = candidate.get("alias_user_ids")
                    if (
                        _single_line(candidate.get("user_id"), 160) in {normalized, canonical}
                        or isinstance(aliases, list)
                        and any(_single_line(alias, 160) in {normalized, canonical} for alias in aliases)
                    ):
                        return candidate
                return None

            data_lock = getattr(self, "_data_lock", None)
            if data_lock is not None:
                async with data_lock:
                    requester = (
                        existing_private_user(requester_id)
                        if request_scope == "group"
                        else user_getter(requester_id)
                    )
                    group_enabled = False
                    if request_scope == "group":
                        group_id_getter = getattr(self, "_extract_group_id_from_event", None)
                        group_id = group_id_getter(event) if callable(group_id_getter) else ""
                        checker = getattr(self, "_group_enabled_for_event", None)
                        group_enabled = bool(group_id and callable(checker) and checker(group_id))
                        if not runtime_persona_setting(self, "enable_group_companion", True):
                            group_gate_message = "群聊陪伴总开关未开启。"
                        elif callable(getattr(self, "_group_allowed_by_access_mode", None)) and not self._group_allowed_by_access_mode(group_id):
                            group_gate_message = "本群不在当前群聊访问名单内。"
                        elif not group_enabled:
                            group_gate_message = "本群单独停用；请在群聊面板启用本群。"
                    requester_authorized = (group_enabled if request_scope == "group" else isinstance(requester, dict))
            else:
                requester = (
                    existing_private_user(requester_id)
                    if request_scope == "group"
                    else user_getter(requester_id)
                )
                group_enabled = False
                if request_scope == "group":
                    group_id_getter = getattr(self, "_extract_group_id_from_event", None)
                    group_id = group_id_getter(event) if callable(group_id_getter) else ""
                    checker = getattr(self, "_group_enabled_for_event", None)
                    group_enabled = bool(group_id and callable(checker) and checker(group_id))
                    if not runtime_persona_setting(self, "enable_group_companion", True):
                        group_gate_message = "群聊陪伴总开关未开启。"
                    elif callable(getattr(self, "_group_allowed_by_access_mode", None)) and not self._group_allowed_by_access_mode(group_id):
                        group_gate_message = "本群不在当前群聊访问名单内。"
                    elif not group_enabled:
                        group_gate_message = "本群单独停用；请在群聊面板启用本群。"
                requester_authorized = (group_enabled if request_scope == "group" else isinstance(requester, dict))
            if not requester_authorized:
                return public_receipt(
                    {
                        "status": "unauthorized",
                        "success": False,
                        "generated": False,
                        "sent": False,
                        "message": group_gate_message,
                        "must_not_claim_sent": True,
                        "retryable": False,
                    },
                    ensure_ascii=False,
                )
        if requester_id and requester is None and callable(user_getter):
            data_lock = getattr(self, "_data_lock", None)
            if data_lock is not None:
                async with data_lock:
                    requester = user_getter(requester_id)
            else:
                requester = user_getter(requester_id)

        photo_scope_getter = getattr(self, "_photo_generation_scope", None)
        if callable(photo_scope_getter):
            photo_scope = photo_scope_getter(
                event,
                user=requester if isinstance(requester, dict) else None,
                user_id=requester_id,
            )
        elif bool(getattr(event, "private_companion_proactive_framework", False)):
            photo_scope = "proactive"
        elif request_scope == "group":
            photo_scope = "group"
        else:
            photo_scope = ""

        scope_quota_getter = getattr(self, "_photo_generation_scope_quota_left", None)
        scope_left = (
            scope_quota_getter(
                event,
                user=requester if isinstance(requester, dict) else None,
                user_id=requester_id,
                scope=photo_scope,
            )
            if callable(scope_quota_getter)
            else None
        )
        scope_blocked = scope_left is not None and scope_left <= 0
        if not callable(scope_quota_getter) and callable(scope_checker):
            scope_blocked = not scope_checker(
                event,
                user=requester if isinstance(requester, dict) else None,
                user_id=requester_id,
            )
        if scope_blocked:
            scope_message_getter = getattr(self, "_photo_generation_scope_quota_block_message", None)
            scope_message = (
                scope_message_getter(
                    event,
                    user=requester if isinstance(requester, dict) else None,
                    user_id=requester_id,
                    scope=photo_scope,
                )
                if callable(scope_message_getter)
                else "当前不允许在这个会话范围生图/改图，或今天该范围的额度已经用完。"
            )
            return public_receipt(
                {
                    "status": "quota_exhausted",
                    "success": False,
                    "generated": False,
                    "sent": False,
                    "message": scope_message,
                    "must_not_claim_sent": True,
                    "retryable": False,
                },
                ensure_ascii=False,
            )

        if photo_scope == "proactive" and isinstance(requester, dict):
            proactive_available = True
            photo_available = getattr(self, "_photo_text_available", None)
            if callable(photo_available):
                try:
                    proactive_available = bool(photo_available(requester))
                except TypeError:
                    proactive_available = bool(photo_available())
            if not proactive_available:
                return public_receipt(
                    {
                        "status": "quota_exhausted",
                        "success": False,
                        "generated": False,
                        "sent": False,
                        "message": "今天主动生图额度已经用完，或该陪伴用户不允许主动生图。",
                        "must_not_claim_sent": True,
                        "retryable": False,
                    },
                    ensure_ascii=False,
                )
        else:
            quota_getter = getattr(self, "_command_photo_quota_left", None)
            quota_left = (
                quota_getter(requester)
                if callable(quota_getter) and isinstance(requester, dict)
                else None
            )
            if quota_left is not None and quota_left <= 0:
                quota_message_getter = getattr(self, "_command_photo_quota_block_message", None)
                quota_message = (
                    quota_message_getter()
                    if callable(quota_message_getter)
                    else "当前不允许用户请求生图/改图，或今天的用户请求生图额度已经用完。"
                )
                return public_receipt(
                    {
                        "status": "quota_exhausted",
                        "success": False,
                        "generated": False,
                        "sent": False,
                        "message": quota_message,
                        "must_not_claim_sent": True,
                        "retryable": False,
                    },
                    ensure_ascii=False,
                )
        return _StageNext((photo_scope, request_scope, requester, requester_id))
