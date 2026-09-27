# -*- coding: utf-8 -*-
"""atrelay_relay。

由 tools/split_main_domain.py 从 main.py 机械抽取（17 个方法 / 397 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPlugin）。
"""
from __future__ import annotations

import json
import re
from .conversation_prompt_section import PromptRenderMode, render_prompt_sections
from .helpers import _now_ts, _safe_float, _single_line
from .main_shared import _multi_persona_event_context
from astrbot.api.event import AstrMessageEvent, filter
from typing import Any

from .logging_util import get_module_logger

logger = get_module_logger(__name__)

class PrivateCompanionPluginAtrelayRelayMixin:
    """atrelay_relay（从 PrivateCompanionPlugin 拆出）。"""

    @filter.llm_tool(name="pc_get_group_id_by_name")
    @_multi_persona_event_context
    async def pc_get_group_id_by_name(self, event: AstrMessageEvent, **kwargs) -> str:
        """按群名关键词查询机器人已加入的群号。

        Args:
            group_name(string): 群名关键词或群号。
        """
        if self is None or self._proactive_only_blocks_passive_event(event, "pc_tools"):
            return '{"status":"disabled","message":"主动消息专用模式下，普通被动回复不可使用 Private Companion 工具。"}'
        return await self._pc_get_group_id_by_name_impl(event, **kwargs)

    @filter.llm_tool(name="pc_get_user_id_by_name")
    @_multi_persona_event_context
    async def pc_get_user_id_by_name(self, event: AstrMessageEvent, **kwargs) -> str:
        """按关系网名称、别名、群名片或昵称解析群友 QQ。

        Args:
            group_id(string): 目标群号；私聊中可填写要查询的群号。
            nickname(string): 关系网名称、别名、群名片、昵称或 QQ。
        """
        if self is None or self._proactive_only_blocks_passive_event(event, "pc_tools"):
            return '{"status":"disabled","message":"主动消息专用模式下，普通被动回复不可使用 Private Companion 工具。"}'
        return await self._pc_get_user_id_by_name_impl(event, **kwargs)

    @filter.llm_tool(name="pc_relay_message")
    @_multi_persona_event_context
    async def pc_relay_message(self, event: AstrMessageEvent, **kwargs) -> str:
        """统一转述入口：把用户明确要求转发/转述/提醒的话发送到群聊或私聊。

        Args:
            destination(string): group/private/auto。发群填 group, 私聊填 private, 不确定填 auto。
            group_hint(string): 群号或群名。群聊转私聊时可用于按群成员名解析 QQ。
            recipient_hint(string): 收件人 QQ、关系网名称、别名、群名片或昵称。
            message(string): 最终要发送的内容。
            at_recipient(boolean): 发到群时是否 @ recipient_hint。
            relay_mode(string): persona/soft/original。默认 persona。
            sensitive_confirmed(boolean): 敏感内容是否已获得用户确认。
            delay_until_recipient_seen(boolean): 是否等目标群友在群里出现后再转述。
            need_receipt(boolean): 私聊询问时是否等待对方回复并带回结果。
            confirm_before_report(boolean): 带回私聊回复前是否先向对方确认。
            expire_hours(number): 延迟转述有效小时数。
        """
        if self is None or self._proactive_only_blocks_passive_event(event, "pc_tools"):
            return '{"status":"disabled","message":"主动消息专用模式下，普通被动回复不可使用 Private Companion 工具。"}'
        return await self._pc_relay_message_impl(event, **kwargs)

    @filter.llm_tool(name="pc_send_to_group")
    @_multi_persona_event_context
    async def pc_send_to_group(self, event: AstrMessageEvent, **kwargs) -> str:
        """向指定群聊发送消息,可按 QQ/关系网名称/别名/群名片 @ 群友。

        Args:
            group_id(string): 目标群号。
            message(string): 最终要发送的转述文本。
            at_user(string): 可选,要 @ 的 QQ、关系网名称、别名、群名片或昵称。
            relay_mode(string): persona/soft/original。
            sensitive_confirmed(boolean): 敏感内容是否已获得用户确认。
        """
        if self is None or self._proactive_only_blocks_passive_event(event, "pc_tools"):
            return '{"status":"disabled","message":"主动消息专用模式下，普通被动回复不可使用 Private Companion 工具。"}'
        result = await self._pc_send_to_group_impl(event, **kwargs)
        if str(result or "").startswith("消息已发送"):
            setattr(
                event,
                "private_companion_atrelay_tool_result",
                {
                    "status": "success",
                    "destination": "group",
                    "final_reply": "带到了。",
                    "final_reply_reference": "参考意图：转述已经成功发到目标群；只给用户一个很短的成功回执，不要复述转述正文，也不要写工具执行状态。",
                    "sent_text": _single_line(kwargs.get("message") or kwargs.get("text") or kwargs.get("content") or kwargs.get("msg"), 800),
                    "recipient": _single_line(kwargs.get("at_user") or kwargs.get("at") or kwargs.get("target_user") or kwargs.get("user_id"), 80),
                    "group_id": _single_line(kwargs.get("group_id") or kwargs.get("group") or kwargs.get("target_group"), 40),
                },
            )
        return result

    @filter.llm_tool(name="pc_send_to_private_user")
    @_multi_persona_event_context
    async def pc_send_to_private_user(self, event: AstrMessageEvent, **kwargs) -> str:
        """向指定平台用户 ID 发送私聊消息。

        Args:
            user_id(string): 目标用户 ID；OneBot 通常是 QQ 号，QQ 官方机器人通常是 openid/平台用户 ID。
            message(string): 最终要发送的转述文本。
            relay_mode(string): persona/soft/original。
            sensitive_confirmed(boolean): 敏感内容是否已获得用户确认。
            need_receipt(boolean): 是否等待对方回复并带回结果。
            confirm_before_report(boolean): 带回私聊回复前是否先向对方确认。
            receipt_expire_hours(number): 等待回执的有效小时数。
        """
        if self is None or self._proactive_only_blocks_passive_event(event, "pc_tools"):
            return '{"status":"disabled","message":"主动消息专用模式下，普通被动回复不可使用 Private Companion 工具。"}'
        result = await self._pc_send_to_private_user_impl(event, **kwargs)
        if str(result or "").startswith("已向") and "发送私聊消息" in str(result or ""):
            need_receipt = self._atrelay_bool_flag(
                kwargs.get("need_receipt", kwargs.get("wait_for_reply", kwargs.get("receipt", kwargs.get("report_back", False))))
            )
            setattr(
                event,
                "private_companion_atrelay_tool_result",
                {
                    "status": "success",
                    "destination": "private",
                    "final_reply": "带到了，有回复我再告诉你。" if need_receipt else "带到了。",
                    "final_reply_reference": (
                        "参考意图：转述已经成功发给目标私聊用户，并且如果对方回复会再告诉当前用户；只给一个很短的成功回执。"
                        if need_receipt
                        else "参考意图：转述已经成功发给目标私聊用户；只给用户一个很短的成功回执，不要复述转述正文，也不要写工具执行状态。"
                    ),
                    "sent_text": _single_line(kwargs.get("message") or kwargs.get("text") or kwargs.get("content") or kwargs.get("msg"), 800),
                    "recipient": _single_line(kwargs.get("user_id") or kwargs.get("qq") or kwargs.get("target_user") or kwargs.get("target"), 128),
                },
            )
        return result

    @filter.llm_tool(name="pc_send_to_groups")
    @_multi_persona_event_context
    async def pc_send_to_groups(self, event: AstrMessageEvent, **kwargs) -> str:
        """向多个群发送同一条通知。

        Args:
            group_ids(string): 目标群号,可用逗号、空格或换行分隔。
            message(string): 最终要发送的转述文本。
            at_user(string): 可选,要 @ 的 QQ、关系网名称、别名、群名片或昵称。
            relay_mode(string): persona/soft/original。
            sensitive_confirmed(boolean): 敏感内容是否已获得用户确认。
        """
        if self is None or self._proactive_only_blocks_passive_event(event, "pc_tools"):
            return '{"status":"disabled","message":"主动消息专用模式下，普通被动回复不可使用 Private Companion 工具。"}'
        return await self._pc_send_to_groups_impl(event, **kwargs)

    @filter.llm_tool(name="pc_send_to_private_users")
    @_multi_persona_event_context
    async def pc_send_to_private_users(self, event: AstrMessageEvent, **kwargs) -> str:
        """向多个平台用户 ID 发送同一条私聊转述。

        Args:
            user_ids(string): 目标用户 ID,可用逗号、空格或换行分隔；OneBot 通常是 QQ 号，QQ 官方机器人通常是 openid/平台用户 ID。
            message(string): 最终要发送的转述文本。
            relay_mode(string): persona/soft/original。
            sensitive_confirmed(boolean): 敏感内容是否已获得用户确认。
        """
        if self is None or self._proactive_only_blocks_passive_event(event, "pc_tools"):
            return '{"status":"disabled","message":"主动消息专用模式下，普通被动回复不可使用 Private Companion 工具。"}'
        return await self._pc_send_to_private_users_impl(event, **kwargs)

    @filter.llm_tool(name="pc_schedule_group_relay")
    @_multi_persona_event_context
    async def pc_schedule_group_relay(self, event: AstrMessageEvent, **kwargs) -> str:
        """挂起一条群聊转述,等目标用户在群里发言后自动 @ 并转述。

        Args:
            group_id(string): 目标群号。
            at_user(string): 目标 QQ、关系网名称、别名、群名片或昵称。
            message(string): 最终要发送的转述文本。
            relay_mode(string): persona/soft/original。
            sensitive_confirmed(boolean): 敏感内容是否已获得用户确认。
            expire_hours(number): 挂起有效小时数。
        """
        if self is None or self._proactive_only_blocks_passive_event(event, "pc_tools"):
            return '{"status":"disabled","message":"主动消息专用模式下，普通被动回复不可使用 Private Companion 工具。"}'
        return await self._pc_schedule_group_relay_impl(event, **kwargs)

    def _message_looks_like_atrelay_request(self, text: str) -> bool:
        text = str(text or "")
        return any(token in text for token in (
            "发到", "发给", "告诉", "转告", "转达", "带话", "捎话", "通知", "私聊",
            "帮我", "替我", "你去", "跟他说", "和他说", "跟她说", "和她说", "说一声",
            "@", "艾特", "群友", "群里", "群聊", "出现", "冒泡", "上线",
        ))

    def _format_atrelay_target_summary_for_prompt(
        self,
        text: str,
    ) -> str:
        section = self._format_atrelay_target_summary_prompt_section(text)
        if section is None:
            return ""
        return render_prompt_sections(
            [section],
            mode=PromptRenderMode.LABELED_BLOCK,
        )

    def _parse_direct_atrelay_request(self, text: str) -> dict[str, Any]:
        cleaned = _single_line(text, 260)
        if not cleaned or not self._message_looks_like_atrelay_request(cleaned):
            return {}
        destination = ""
        if "私聊" in cleaned or "私信" in cleaned:
            destination = "private"
        elif any(token in cleaned for token in ("群里", "群聊", "发到群", "发群", "到群里", "去群里")):
            destination = "group"
        if not destination:
            return {}

        profiles = self._select_worldbook_member_profiles_for_private_text(cleaned, limit=3)
        if len(profiles) != 1:
            return {}
        profile = profiles[0]
        recipient_id = _single_line(profile.get("user_id"), 40)
        recipient_name = _single_line(profile.get("name"), 40) or recipient_id
        tokens = sorted(
            [token for token in self._worldbook_profile_tokens(profile) if token and token in cleaned],
            key=len,
            reverse=True,
        )
        target_token = tokens[0] if tokens else recipient_name
        if not target_token or target_token not in cleaned:
            return {}

        _, after = cleaned.split(target_token, 1)
        after = re.sub(r"^(?:说一句|说一声|说下|说|告诉|转告|带话|发|：|:|，|,|\s)+", "", after).strip()
        if not after:
            # “告诉 A B”这类没有“说一句”的短命令，目标后面的内容就是正文。
            after = cleaned[cleaned.find(target_token) + len(target_token):].strip()
        message = _single_line(after, 300).strip(" ：:，,。")
        if not message:
            return {}

        group_hint = ""
        if destination == "group":
            group_matches = self._atrelay_cached_group_matches(cleaned)
            if len(group_matches) == 1:
                group_hint = _single_line(group_matches[0].get("group_id") or group_matches[0].get("group_name"), 80)
        return {
            "destination": destination,
            "recipient_hint": recipient_id or recipient_name or target_token,
            "group_hint": group_hint,
            "message": message,
            "target_token": target_token,
        }

    def _pending_atrelay_requests(self) -> dict[str, Any]:
        pending = self.data.setdefault("pending_atrelay_requests", {})
        if not isinstance(pending, dict):
            pending = {}
            self.data["pending_atrelay_requests"] = pending
        now = _now_ts()
        expired = [
            key for key, item in pending.items()
            if not isinstance(item, dict) or now - _safe_float(item.get("ts"), 0) > 10 * 60
        ]
        for key in expired:
            pending.pop(key, None)
        return pending

    def _store_pending_atrelay_request(self, user_id: str, payload: dict[str, Any], reason: str = "") -> None:
        uid = _single_line(user_id, 40)
        if not uid or not isinstance(payload, dict):
            return
        pending = self._pending_atrelay_requests()
        pending[uid] = {
            "ts": _now_ts(),
            "payload": {
                "destination": _single_line(payload.get("destination"), 20),
                "recipient_hint": _single_line(payload.get("recipient_hint"), 80),
                "group_hint": _single_line(payload.get("group_hint"), 80),
                "message": _single_line(payload.get("message"), 300),
                "target_token": _single_line(payload.get("target_token"), 80),
            },
            "reason": _single_line(reason, 120),
        }
        self._save_data_sync(sections={"pending_atrelay_requests"})
        logger.info(
            "转述请求等待补群: user=%s target=%s text=%s reason=%s",
            uid,
            _single_line(payload.get("recipient_hint"), 80),
            _single_line(payload.get("message"), 80),
            _single_line(reason, 120),
        )

    async def _format_direct_atrelay_final_reply(
        self,
        event: AstrMessageEvent,
        payload: dict[str, Any],
        result: dict[str, Any],
    ) -> str:
        status = _single_line(result.get("status"), 40)
        fallback = _single_line(result.get("final_reply") or result.get("message"), 240)
        sender_id = ""
        try:
            sender_id = self._canonical_private_user_id(str(event.get_sender_id()))
        except Exception:
            try:
                sender_id = str(event.get_sender_id())
            except Exception:
                sender_id = ""
        users = self.data.get("users") if isinstance(getattr(self, "data", None), dict) else {}
        resolver = getattr(self, "_private_user_id_for_event", None)
        scoped_sender_id = (
            resolver(event, sender_id)
            if callable(resolver) and sender_id
            else self._canonical_private_user_id(sender_id)
        )
        user = users.get(scoped_sender_id) if scoped_sender_id and isinstance(users, dict) and isinstance(users.get(scoped_sender_id), dict) else {}
        rewriter = getattr(self, "_rewrite_reference_reply_with_persona", None)
        if status not in {"success", "scheduled"}:
            if callable(rewriter):
                rewritten = await rewriter(
                    f"参考意图：转述没有成功；原因是「{fallback or '未知'}」。用当前人格简短告诉用户失败，不要说成已经发出。",
                    scene="跨群/私聊转述失败回执",
                    user=user,
                    event=event,
                    fallback_text=fallback or "转述没成功。",
                    task="atrelay_receipt_rewrite",
                    max_chars=80,
                    allow_fallback=True,
                    preserve_status=True,
                )
                if rewritten:
                    return rewritten
            return fallback or "转述没成功。"
        recipient = _single_line(payload.get("target_token") or payload.get("recipient_hint"), 60) or "对方"
        if status == "scheduled":
            reference = f"参考意图：转述已挂起，等{recipient}下次在群里出现或冒泡时再转达；简短告诉用户会稍后带到。"
            fallback_ok = f"等{recipient}出现我再说。"
        else:
            reference = (
                f"参考意图：转述已经成功发给{recipient}；只给用户一个很短的成功回执，"
                "不要复述转述正文，也不要写工具执行状态。"
            )
            fallback_ok = f"给{recipient}带到了。" if recipient and recipient not in {"对方", "群里"} else "带到了。"
        if callable(rewriter):
            rewritten = await rewriter(
                reference,
                scene="跨群/私聊转述成功回执",
                user=user,
                event=event,
                fallback_text=fallback_ok,
                task="atrelay_receipt_rewrite",
                max_chars=70,
                allow_fallback=True,
                preserve_status=True,
            )
            if rewritten:
                return rewritten
        return fallback_ok

    async def _send_direct_atrelay_result_reply(
        self,
        event: AstrMessageEvent,
        payload: dict[str, Any],
        result: dict[str, Any],
    ) -> None:
        reply = await self._format_direct_atrelay_final_reply(event, payload, result)
        await event.send(event.plain_result(reply))

    async def _maybe_resume_pending_atrelay_request(self, event: AstrMessageEvent, user_id: str, text: str) -> bool:
        uid = _single_line(user_id, 40)
        pending = self._pending_atrelay_requests()
        item = pending.get(uid)
        if not isinstance(item, dict):
            return False
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        if not payload or _single_line(payload.get("destination"), 20) != "group":
            pending.pop(uid, None)
            return False
        hint = _single_line(text, 100)
        if not hint:
            return False
        group_result = await self._resolve_atrelay_target_group(event, hint)
        if group_result.get("status") != "success":
            return False
        payload = dict(payload)
        payload["group_hint"] = _single_line(group_result.get("group_id") or hint, 80)
        result_raw = await self._pc_relay_message_impl(event, **payload)
        try:
            result = json.loads(result_raw)
        except Exception:
            result = {"status": "error", "message": _single_line(result_raw, 240)}
        pending.pop(uid, None)
        self._save_data_sync(sections={"pending_atrelay_requests"})
        logger.info(
            "已用补充群名续发转述: user=%s group=%s status=%s target=%s",
            uid,
            _single_line(group_result.get("group_id"), 40),
            _single_line(result.get("status"), 40),
            _single_line(payload.get("recipient_hint"), 80),
        )
        await self._send_direct_atrelay_result_reply(event, payload, result)
        event.stop_event()
        return True

    async def _maybe_handle_direct_atrelay_request(self, event: AstrMessageEvent, text: str) -> bool:
        payload = self._parse_direct_atrelay_request(text)
        if not payload:
            return False
        result_raw = await self._pc_relay_message_impl(event, **payload)
        try:
            result = json.loads(result_raw)
        except Exception:
            result = {"status": "error", "message": _single_line(result_raw, 240)}
        status = _single_line(result.get("status"), 40)
        if status in {"need_group", "not_found"} and _single_line(payload.get("destination"), 20) == "group":
            resolver = getattr(self, "_private_user_id_for_event", None)
            pending_user_id = (
                resolver(event)
                if callable(resolver)
                else str(event.get_sender_id())
            )
            self._store_pending_atrelay_request(pending_user_id, payload, _single_line(result.get("message"), 120))
        logger.info(
            "明确转述请求已本地直通: status=%s destination=%s target=%s text=%s",
            status or "-",
            _single_line(payload.get("destination"), 20),
            _single_line(payload.get("recipient_hint"), 40),
            _single_line(payload.get("message"), 80),
        )
        await self._send_direct_atrelay_result_reply(event, payload, result)
        event.stop_event()
        return True
