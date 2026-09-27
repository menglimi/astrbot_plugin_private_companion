# -*- coding: utf-8 -*-
"""LlmToolActionsInteractionRelayPart03Mixin。

由 tools/split_mixin_domain.py 从 llm_tool_actions_interaction_relay.py 机械抽取（3 个方法 + 0 个模块级名字 + 0 个类级赋值 / 389 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 LlmToolActionsInteractionRelayMixin）。
"""
from __future__ import annotations

import json
from .helpers import _single_line
from .llm_tool_actions_shared import logger
from astrbot.api.event import AstrMessageEvent
from typing import Any
try:
    from astrbot.api.message_components import At, Image, Plain, Record, Reply
except ImportError:
    from astrbot.api.message_components import At, Image, Plain
    from astrbot.core.message.components import Record
    try:
        from astrbot.api.message_components import Reply
    except ImportError:
        try:
            from astrbot.core.message.components import Reply
        except ImportError:
            Reply = None



class LlmToolActionsInteractionRelayPart03Mixin:
    """LlmToolActionsInteractionRelayPart03Mixin（从 LlmToolActionsInteractionRelayMixin 拆出）。"""


    async def _pc_relay_message_impl(self, event: AstrMessageEvent, **kwargs) -> str:
        if not self.enable_atrelay_tools:
            return json.dumps({"status": "disabled", "message": "跨会话转述工具未启用"}, ensure_ascii=False)
        authorized, _requester_id = self._atrelay_tool_authorization(event)
        if not authorized:
            return json.dumps({"status": "forbidden", "message": "跨会话转述仅允许主人使用"}, ensure_ascii=False)
        destination_raw = _single_line(
            kwargs.get("destination")
            or kwargs.get("target_scope")
            or kwargs.get("scope")
            or kwargs.get("target_type")
            or kwargs.get("type")
            or "auto",
            40,
        ).lower()
        group_hint = kwargs.get("group_hint") or kwargs.get("group_id") or kwargs.get("group") or kwargs.get("target_group") or ""
        recipient_hint = (
            kwargs.get("recipient_hint")
            or kwargs.get("recipient")
            or kwargs.get("to")
            or kwargs.get("at_user")
            or kwargs.get("target_user")
            or kwargs.get("user_id")
            or kwargs.get("nickname")
            or kwargs.get("name")
            or ""
        )
        message = kwargs.get("message") or kwargs.get("text") or kwargs.get("content") or kwargs.get("msg") or ""
        relay_mode = kwargs.get("relay_mode") or kwargs.get("mode") or ""
        sensitive_confirmed = kwargs.get("sensitive_confirmed", kwargs.get("confirmed", False))
        delay_until_seen = self._atrelay_bool_flag(
            kwargs.get("delay_until_recipient_seen", kwargs.get("delay", kwargs.get("wait_until_seen", False)))
        )
        need_receipt = self._atrelay_bool_flag(
            kwargs.get("need_receipt", kwargs.get("wait_for_reply", kwargs.get("receipt", kwargs.get("report_back", False))))
        )
        confirm_before_report = self._atrelay_bool_flag(
            kwargs.get("confirm_before_report", kwargs.get("require_reply_confirmation", kwargs.get("confirm_reply", False)))
        )
        at_recipient = self._atrelay_bool_flag(kwargs.get("at_recipient", kwargs.get("at", False)))
        expire_hours = kwargs.get("expire_hours", kwargs.get("ttl_hours", 24))

        text = self._normalize_atrelay_text(message, limit=800)
        recipient = _single_line(recipient_hint, 128)
        if not text:
            return json.dumps({"status": "error", "message": "缺少 message/text 内容"}, ensure_ascii=False)

        if destination_raw in {"group", "groups", "群", "群聊", "send_group", "to_group"}:
            destination = "group"
        elif destination_raw in {"private", "user", "friend", "私聊", "私发", "私信", "to_user", "dm"}:
            destination = "private"
        else:
            if group_hint:
                destination = "group"
            elif recipient:
                destination = "private"
            else:
                destination = "auto"

        if destination == "auto":
            return json.dumps({"status": "need_target", "message": "需要说明发到哪个群或私聊给谁"}, ensure_ascii=False)

        boundary = self._atrelay_boundary_guard(text)
        if boundary:
            return json.dumps({"status": "error", "message": boundary}, ensure_ascii=False)
        guard = self._atrelay_confirmation_guard(
            text,
            relay_mode=self._normalize_atrelay_relay_mode(relay_mode),
            sensitive_confirmed=self._atrelay_bool_flag(sensitive_confirmed) or self._atrelay_event_confirms_sensitive_send(event),
        )
        if guard:
            return json.dumps({"status": "need_confirm", "message": guard}, ensure_ascii=False)

        if destination == "group":
            group_result = {}
            current_group_id = self._extract_group_id_from_event(event)
            if not _single_line(group_hint, 80) and recipient:
                group_result = await self._resolve_atrelay_active_group_for_recipient(
                    event,
                    recipient,
                    exclude_current_group=bool(current_group_id),
                )
            if not _single_line(group_hint, 80) and current_group_id and not group_result:
                return json.dumps(
                    {
                        "status": "need_group",
                        "message": "需要补充要发到哪个群；群聊里不会默认发回当前群。",
                    },
                    ensure_ascii=False,
                )
            if not group_result:
                group_result = await self._resolve_atrelay_target_group(event, group_hint)
            if group_result.get("status") != "success":
                return json.dumps(group_result, ensure_ascii=False)
            group_id = _single_line(group_result.get("group_id"), 40)
            group_guard = self._atrelay_target_group_allowed(group_id, event)
            if group_guard:
                return json.dumps({"status": "forbidden", "message": group_guard}, ensure_ascii=False)
            send_text = await self._rewrite_atrelay_message_with_llm(
                event,
                destination="group",
                recipient_hint=recipient,
                text=text,
                relay_mode=relay_mode,
            )
            send_text = self._normalize_atrelay_text(send_text, limit=800)
            if delay_until_seen:
                if not recipient:
                    return json.dumps({"status": "need_recipient", "message": "延迟转述需要目标群友"}, ensure_ascii=False)
                result = await self._pc_schedule_group_relay_impl(
                    event,
                    group_id=group_id,
                    at_user=recipient,
                    message=send_text,
                    relay_mode=relay_mode,
                    sensitive_confirmed=sensitive_confirmed,
                    expire_hours=expire_hours,
                )
                return json.dumps({"status": "scheduled" if result.startswith("已挂起") else "error", "message": result}, ensure_ascii=False)
            result = await self._pc_send_to_group_impl(
                event,
                group_id=group_id,
                message=send_text,
                at_user=recipient if (recipient and (at_recipient or recipient)) else "",
                relay_mode=relay_mode,
                sensitive_confirmed=sensitive_confirmed,
            )
            ok = result.startswith("消息已发送")
            if ok:
                setattr(
                    event,
                    "private_companion_atrelay_tool_result",
                    {
                        "status": "success",
                        "destination": "group",
                        "final_reply": "带到了。",
                        "final_reply_reference": "参考意图：转述已经成功发到目标群；只给用户一个很短的成功回执，不要复述转述正文，也不要写工具执行状态。",
                        "sent_text": send_text,
                        "recipient": recipient,
                        "group_id": group_id,
                    },
                )
            return json.dumps(
                {
                    "status": "success" if ok else "error",
                    "message": "带到了。" if ok else result,
                    "final_reply": "带到了。" if ok else "",
                    "final_reply_reference": "参考意图：转述已经成功发到目标群；只给用户一个很短的成功回执，不要复述转述正文，也不要写工具执行状态。" if ok else "",
                    "sent_text": send_text if ok else "",
                },
                ensure_ascii=False,
            )

        target_user = recipient
        if not target_user:
            return json.dumps({"status": "need_recipient", "message": "需要补充私聊目标用户 ID 或称呼"}, ensure_ascii=False)
        if not target_user.isdigit():
            resolved = await self._resolve_atrelay_target_user(event, "", target_user)
            if not resolved.get("user_id") and not resolved.get("ambiguous"):
                group_result = await self._resolve_atrelay_target_group(event, group_hint)
            else:
                group_result = {}
            group_id = _single_line(group_result.get("group_id"), 40) if group_result.get("status") == "success" else ""
            if not group_id and self._extract_group_id_from_event(event):
                group_id = self._extract_group_id_from_event(event)
            if not resolved.get("user_id") and not resolved.get("ambiguous") and not group_id:
                return json.dumps(
                    {
                        "status": "need_group_or_user_id",
                        "message": "关系网里没有唯一确认这个称呼；请补充目标所在群号/群名，或直接提供用户 ID。",
                    },
                    ensure_ascii=False,
                )
            if not resolved.get("user_id") and not resolved.get("ambiguous"):
                resolved = await self._resolve_atrelay_target_user(event, group_id, target_user)
            if resolved.get("ambiguous"):
                return json.dumps(
                    {
                        "status": "ambiguous",
                        "message": "匹配到多个用户，请补充用户 ID",
                        "matches": resolved.get("matches", [])[:8],
                    },
                    ensure_ascii=False,
                )
            target_user = _single_line(resolved.get("user_id"), 128)
            if not target_user:
                return json.dumps({"status": "not_found", "message": "未找到私聊目标"}, ensure_ascii=False)
        send_text = await self._rewrite_atrelay_message_with_llm(
            event,
            destination="private",
            recipient_hint=target_user,
            text=text,
            relay_mode=relay_mode,
        )
        send_text = self._normalize_atrelay_text(send_text, limit=800)
        result = await self._pc_send_to_private_user_impl(
            event,
            user_id=target_user,
            message=send_text,
            relay_mode=relay_mode,
            sensitive_confirmed=sensitive_confirmed,
            need_receipt=need_receipt,
            confirm_before_report=confirm_before_report,
            receipt_expire_hours=expire_hours,
        )
        ok = result.startswith("已向")
        if ok:
            setattr(
                event,
                "private_companion_atrelay_tool_result",
                {
                        "status": "success",
                        "destination": "private",
                        "final_reply": "带到了。" if not need_receipt else "带到了，有回复我再告诉你。",
                        "final_reply_reference": (
                            "参考意图：转述已经成功发给目标私聊用户，并且如果对方回复会再告诉当前用户；只给一个很短的成功回执。"
                            if need_receipt
                            else "参考意图：转述已经成功发给目标私聊用户；只给用户一个很短的成功回执，不要复述转述正文，也不要写工具执行状态。"
                        ),
                        "sent_text": send_text,
                        "recipient": target_user,
                    },
                )
        return json.dumps(
            {
                "status": "success" if ok else "error",
                "message": "带到了，有回复我再告诉你。" if ok and need_receipt else ("带到了。" if ok else result),
                "final_reply": "带到了，有回复我再告诉你。" if ok and need_receipt else ("带到了。" if ok else ""),
                "final_reply_reference": (
                    "参考意图：转述已经成功发给目标私聊用户，并且如果对方回复会再告诉当前用户；只给一个很短的成功回执。"
                    if ok and need_receipt
                    else (
                        "参考意图：转述已经成功发给目标私聊用户；只给用户一个很短的成功回执，不要复述转述正文，也不要写工具执行状态。"
                        if ok
                        else ""
                    )
                ),
                "sent_text": send_text if ok else "",
            },
            ensure_ascii=False,
        )

    async def _pc_send_to_group_impl(self, event: AstrMessageEvent, **kwargs) -> str:
        if not self.enable_atrelay_tools:
            return "发送失败：跨群转述工具未启用"
        authorized, _requester_id = self._atrelay_tool_authorization(event)
        if not authorized:
            return "发送失败：跨会话转述仅允许主人使用"
        group_id = kwargs.get("group_id") or kwargs.get("group") or kwargs.get("target_group") or ""
        message = kwargs.get("message") or kwargs.get("text") or kwargs.get("content") or kwargs.get("msg") or ""
        at_user = kwargs.get("at_user") or kwargs.get("at") or kwargs.get("target_user") or kwargs.get("user_id") or ""
        at_qq_list = kwargs.get("at_qq_list") or kwargs.get("at_users") or kwargs.get("at_list")
        if not at_user and isinstance(at_qq_list, list) and at_qq_list:
            at_user = str(at_qq_list[0])
        relay_mode = kwargs.get("relay_mode") or kwargs.get("mode") or ""
        sensitive_confirmed = kwargs.get("sensitive_confirmed", kwargs.get("confirmed", False))
        target_group = self._normalize_atrelay_group_target_id(group_id)
        group_guard = self._atrelay_target_group_allowed(target_group, event)
        if group_guard:
            return group_guard
        text = self._normalize_atrelay_text(message, limit=800)
        relay_mode_normalized = self._normalize_atrelay_relay_mode(relay_mode)
        if not target_group:
            return "发送失败：群 ID 格式不正确"
        if not text:
            return "发送失败：消息内容为空"
        boundary = self._atrelay_boundary_guard(text)
        if boundary:
            return boundary
        duplicate = self._atrelay_duplicate_guard("group", target_group, text, at_user)
        if duplicate:
            return duplicate
        guard = self._atrelay_confirmation_guard(
            text,
            relay_mode=relay_mode_normalized,
            sensitive_confirmed=self._atrelay_bool_flag(sensitive_confirmed) or self._atrelay_event_confirms_sensitive_send(event),
        )
        if guard:
            return guard
        at_qq = ""
        at_label = ""
        if _single_line(at_user, 60):
            resolved = await self._resolve_atrelay_target_user(event, target_group, at_user)
            if resolved.get("ambiguous"):
                names = "、".join(_single_line(item.get("name") or item.get("relation_name") or item.get("nickname") or item.get("user_id"), 30) for item in resolved.get("matches", [])[:5] if isinstance(item, dict))
                return f"发送失败：@ 对象不唯一，请补充 QQ。候选：{names or '多个成员'}"
            at_qq = _single_line(resolved.get("user_id"), 40)
            at_label = _single_line(resolved.get("name"), 60)
            if not at_qq:
                return "发送失败：未找到要 @ 的群友"
            resting = self._atrelay_target_resting_reason(at_qq)
            if resting:
                return f"发送失败：{resting}，不会在群里继续 @ 打扰；可以改用延迟转述，等对方出现时再说。"
        chain: list[Any] = []
        if at_qq:
            chain.extend([At(qq=at_qq), Plain(" ")])
        chain.append(Plain(text))
        ok, error, used_umo = await self._send_atrelay_chain_to_target(
            event,
            message_type="group",
            target_id=target_group,
            chain=chain,
        )
        if not ok:
            logger.warning(
                "跨群转述发送失败: group=%s at=%s error=%s",
                target_group,
                at_qq or at_user or "-",
                _single_line(error, 240),
            )
            return f"发送失败：{_single_line(error, 180)}"
        self._note_atrelay_send("group", target_group, text, at_qq or at_user, event=event)
        self._save_data_sync(sections={"recent_atrelay_contexts", "atrelay_send_log"})
        logger.info(
            "跨群转述发送完成: group=%s at=%s umo=%s",
            target_group,
            at_qq or at_user or "-",
            _single_line(used_umo, 160),
        )
        return f"消息已发送到群 {target_group}" + (f", 已 @ {at_label or at_qq}" if at_qq else "")

    async def _pc_send_to_private_user_impl(self, event: AstrMessageEvent, **kwargs) -> str:
        if not self.enable_atrelay_tools:
            return "发送失败：跨群转述工具未启用"
        authorized, _requester_id = self._atrelay_tool_authorization(event)
        if not authorized:
            return "发送失败：跨会话转述仅允许主人使用"
        user_id = kwargs.get("user_id") or kwargs.get("qq") or kwargs.get("target_user") or kwargs.get("target") or ""
        message = kwargs.get("message") or kwargs.get("text") or kwargs.get("content") or kwargs.get("msg") or ""
        relay_mode = kwargs.get("relay_mode") or kwargs.get("mode") or ""
        sensitive_confirmed = kwargs.get("sensitive_confirmed", kwargs.get("confirmed", False))
        need_receipt = self._atrelay_bool_flag(
            kwargs.get("need_receipt", kwargs.get("wait_for_reply", kwargs.get("receipt", kwargs.get("report_back", False))))
        )
        confirm_before_report = self._atrelay_bool_flag(
            kwargs.get("confirm_before_report", kwargs.get("require_reply_confirmation", kwargs.get("confirm_reply", False)))
        )
        receipt_expire_hours = kwargs.get("receipt_expire_hours", kwargs.get("expire_hours", kwargs.get("ttl_hours", 12)))
        target_user = self._normalize_atrelay_private_target_id(user_id)
        text = self._normalize_atrelay_text(message, limit=800)
        relay_mode_normalized = self._normalize_atrelay_relay_mode(relay_mode)
        if not target_user:
            return "发送失败：目标用户 ID 无效或尚未登记"
        if not text:
            return "发送失败：消息内容为空"
        boundary = self._atrelay_boundary_guard(text)
        if boundary:
            return boundary
        resting = self._atrelay_target_resting_reason(target_user)
        if resting:
            return f"私聊发送失败：{resting}，不会私聊叫醒；可以改成延迟转述或等对方醒来后再发。"
        duplicate = self._atrelay_duplicate_guard("private", target_user, text)
        if duplicate:
            return duplicate
        guard = self._atrelay_confirmation_guard(
            text,
            relay_mode=relay_mode_normalized,
            sensitive_confirmed=self._atrelay_bool_flag(sensitive_confirmed) or self._atrelay_event_confirms_sensitive_send(event),
        )
        if guard:
            return guard
        ok, error, used_umo = await self._send_atrelay_chain_to_target(
            event,
            message_type="private",
            target_id=target_user,
            chain=[Plain(text)],
        )
        if not ok:
            logger.warning(
                "私聊转述发送失败: user=%s error=%s",
                target_user,
                _single_line(error, 240),
            )
            return f"私聊发送失败：{_single_line(error, 180)}"
        self._note_atrelay_send("private", target_user, text, event=event)
        if need_receipt:
            self._note_atrelay_private_receipt_task(
                event,
                target_user=target_user,
                question=text,
                sent_text=text,
                confirm_before_report=confirm_before_report,
                expire_hours=receipt_expire_hours,
            )
        self._save_data_sync(sections={"pending_atrelay_receipts", "recent_atrelay_contexts", "atrelay_send_log"})
        logger.info(
            "私聊转述发送完成: user=%s umo=%s",
            target_user,
            _single_line(used_umo, 160),
        )
        return f"已向 {target_user} 发送私聊消息" + ("，会等待对方回复后带回回执" if need_receipt else "")
