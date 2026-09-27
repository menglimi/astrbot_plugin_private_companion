# -*- coding: utf-8 -*-
"""LlmToolActionsInteractionRelayPart04Mixin。

由 tools/split_mixin_domain.py 从 llm_tool_actions_interaction_relay.py 机械抽取（3 个方法 + 0 个模块级名字 + 0 个类级赋值 / 123 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 LlmToolActionsInteractionRelayMixin）。
"""
from __future__ import annotations

import uuid
from .helpers import _now_ts, _safe_float, _safe_int, _single_line
from astrbot.api.event import AstrMessageEvent



class LlmToolActionsInteractionRelayPart04Mixin:
    """LlmToolActionsInteractionRelayPart04Mixin（从 LlmToolActionsInteractionRelayMixin 拆出）。"""


    async def _pc_send_to_groups_impl(self, event: AstrMessageEvent, **kwargs) -> str:
        group_ids = kwargs.get("group_ids") or kwargs.get("groups") or kwargs.get("group_id") or kwargs.get("targets") or ""
        message = kwargs.get("message") or kwargs.get("text") or kwargs.get("content") or kwargs.get("msg") or ""
        at_user = kwargs.get("at_user") or kwargs.get("at") or kwargs.get("target_user") or kwargs.get("user_id") or ""
        relay_mode = kwargs.get("relay_mode") or kwargs.get("mode") or ""
        sensitive_confirmed = kwargs.get("sensitive_confirmed", kwargs.get("confirmed", False))
        targets = []
        for item in self._parse_atrelay_target_list(group_ids, limit=self.atrelay_multi_target_limit):
            target = self._normalize_atrelay_group_target_id(item)
            if target and target not in targets:
                targets.append(target)
        if not targets:
            return "发送失败：没有有效群 ID"
        results = []
        for group_id in targets:
            result = await self._pc_send_to_group_impl(
                event,
                group_id=group_id,
                message=message,
                at_user=at_user,
                relay_mode=relay_mode,
                sensitive_confirmed=sensitive_confirmed,
            )
            results.append(f"{group_id}: {result}")
        return "多群通知完成：\n" + "\n".join(results[: self.atrelay_multi_target_limit])

    async def _pc_send_to_private_users_impl(self, event: AstrMessageEvent, **kwargs) -> str:
        user_ids = kwargs.get("user_ids") or kwargs.get("users") or kwargs.get("user_id") or kwargs.get("targets") or ""
        message = kwargs.get("message") or kwargs.get("text") or kwargs.get("content") or kwargs.get("msg") or ""
        relay_mode = kwargs.get("relay_mode") or kwargs.get("mode") or ""
        sensitive_confirmed = kwargs.get("sensitive_confirmed", kwargs.get("confirmed", False))
        targets = []
        for item in self._parse_atrelay_target_list(user_ids, limit=self.atrelay_multi_target_limit):
            target = self._normalize_atrelay_private_target_id(item)
            if target and target not in targets:
                targets.append(target)
        if not targets:
            return "发送失败：没有有效私聊目标用户 ID"
        results = []
        for user_id in targets:
            result = await self._pc_send_to_private_user_impl(
                event,
                user_id=user_id,
                message=message,
                relay_mode=relay_mode,
                sensitive_confirmed=sensitive_confirmed,
            )
            results.append(f"{user_id}: {result}")
        return "多人转述完成：\n" + "\n".join(results[: self.atrelay_multi_target_limit])

    async def _pc_schedule_group_relay_impl(self, event: AstrMessageEvent, **kwargs) -> str:
        if not self.enable_atrelay_tools:
            return "挂起失败：跨群转述工具未启用"
        authorized, _requester_id = self._atrelay_tool_authorization(event)
        if not authorized:
            return "挂起失败：跨会话转述仅允许主人使用"
        group_id = kwargs.get("group_id") or kwargs.get("group") or kwargs.get("target_group") or ""
        at_user = kwargs.get("at_user") or kwargs.get("target_user") or kwargs.get("user_id") or kwargs.get("name") or kwargs.get("nickname") or ""
        message = kwargs.get("message") or kwargs.get("text") or kwargs.get("content") or kwargs.get("msg") or ""
        relay_mode = kwargs.get("relay_mode") or kwargs.get("mode") or ""
        sensitive_confirmed = kwargs.get("sensitive_confirmed", kwargs.get("confirmed", False))
        expire_hours = kwargs.get("expire_hours", kwargs.get("ttl_hours", 24))
        target_group = self._normalize_atrelay_group_target_id(group_id) or self._extract_group_id_from_event(event)
        group_guard = self._atrelay_target_group_allowed(target_group, event)
        if group_guard:
            return group_guard.replace("发送失败", "挂起失败", 1)
        text = self._normalize_atrelay_text(message, limit=800)
        if not target_group:
            return "挂起失败：群 ID 格式不正确"
        if not text:
            return "挂起失败：消息内容为空"
        boundary = self._atrelay_boundary_guard(text)
        if boundary:
            return boundary.replace("发送失败", "挂起失败", 1)
        relay_mode_normalized = self._normalize_atrelay_relay_mode(relay_mode)
        guard = self._atrelay_confirmation_guard(
            text,
            relay_mode=relay_mode_normalized,
            sensitive_confirmed=self._atrelay_bool_flag(sensitive_confirmed) or self._atrelay_event_confirms_sensitive_send(event),
        )
        if guard:
            return guard.replace("不能直接转述", "不能直接挂起转述")
        resolved = await self._resolve_atrelay_target_user(event, target_group, at_user)
        if resolved.get("ambiguous"):
            names = "、".join(
                _single_line(item.get("name") or item.get("relation_name") or item.get("nickname") or item.get("user_id"), 30)
                for item in resolved.get("matches", [])[:5]
                if isinstance(item, dict)
            )
            return f"挂起失败：目标不唯一，请补充 QQ。候选：{names or '多个成员'}"
        target_user = _single_line(resolved.get("user_id"), 40)
        target_name = _single_line(resolved.get("name"), 60) or target_user
        if not target_user:
            return "挂起失败：未找到目标群友"
        now = _now_ts()
        expire_seconds = max(1, min(168, _safe_int(expire_hours, 24, 1, 168))) * 3600
        source_user, source_name = self._atrelay_source_snapshot_for_event(event)
        async with self._data_lock:
            group = self._get_group(target_group)
            tasks = group.setdefault("pending_atrelay_tasks", [])
            if not isinstance(tasks, list):
                tasks = []
                group["pending_atrelay_tasks"] = tasks
            signature = self._atrelay_send_signature("delayed_group", target_group, text, target_user)
            for task in tasks:
                if isinstance(task, dict) and task.get("signature") == signature and _safe_float(task.get("expires_at"), 0) > now:
                    return f"已存在相同延迟转述：等 {target_name} 在群 {target_group} 出现时发送"
            tasks.append(
                {
                    "id": uuid.uuid4().hex[:12],
                    "created_at": now,
                    "expires_at": now + expire_seconds,
                    "target_user_id": target_user,
                    "target_name": target_name,
                    "message": text,
                    "source_user": source_user,
                    "source_name": source_name,
                    "relay_mode": relay_mode_normalized,
                    "sensitive_confirmed": self._atrelay_bool_flag(sensitive_confirmed) or self._atrelay_event_confirms_sensitive_send(event),
                    "signature": signature,
                }
            )
            del tasks[:-30]
            self._save_data_sync(sections={"groups"})
        return f"已挂起：等 {target_name} 在群 {target_group} 出现时转述"
