# -*- coding: utf-8 -*-
"""LlmToolActionsInteractionRelayPart02Mixin。

由 tools/split_mixin_domain.py 从 llm_tool_actions_interaction_relay.py 机械抽取（8 个方法 + 0 个模块级名字 + 0 个类级赋值 / 252 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 LlmToolActionsInteractionRelayMixin）。
"""
from __future__ import annotations

import json
import re
from .helpers import _single_line
from .llm_tool_actions_shared import logger
from .persona_config import runtime_persona_setting
from astrbot.api.event import AstrMessageEvent, MessageChain
from astrbot.core.platform.message_session import MessageSession
from typing import Any



class LlmToolActionsInteractionRelayPart02Mixin:
    """LlmToolActionsInteractionRelayPart02Mixin（从 LlmToolActionsInteractionRelayMixin 拆出）。"""


    def _relation_lookup_authorized(self, event: AstrMessageEvent) -> bool:
        try:
            is_private = bool(getattr(event, "is_private_chat", lambda: False)())
        except Exception:
            is_private = ":FriendMessage:" in str(getattr(event, "unified_msg_origin", "") or "")
        if not is_private:
            return False
        try:
            identity_for_event = getattr(self, "_event_permission_identity_id", None)
            requester_id = (
                identity_for_event(event)
                if callable(identity_for_event)
                else self._permission_identity_id(event.get_sender_id())
            )
        except Exception:
            requester_id = ""
        owner_allowed = bool(requester_id and self._is_private_companion_owner_user_id(requester_id))
        admin_allowed = bool(requester_id and self._is_configured_admin_user_id(requester_id))
        allowed = owner_allowed or admin_allowed
        if not allowed:
            logger.info(
                "关系网查询权限未通过: private=%s sender=%s owner=%s admin=%s umo=%s",
                is_private,
                requester_id or "-",
                owner_allowed,
                admin_allowed,
                _single_line(getattr(event, "unified_msg_origin", ""), 120),
            )
        return allowed

    def _relation_lookup_clean_keyword(self, value: Any) -> str:
        text = _single_line(value, 120)
        if not text:
            return ""
        match = re.search(r"\d{5,12}", text)
        if match:
            return match.group(0)
        text = re.sub(r"(这个|那个|此人|这人|那人|用户|群友|qq号|QQ号|QQ|qq)", "", text, flags=re.I)
        text = re.sub(r"(你认识吗|认识吗|认得吗|知道吗|是谁呀|是谁啊|是谁|什么人|哪位|吗|呀|啊|呢)", "", text)
        return _single_line(text.strip(" ：:，,。？?"), 60)

    async def _pc_query_relation_person_impl(self, event: AstrMessageEvent, **kwargs) -> str:
        if not runtime_persona_setting(self, 'enable_worldbook_member_recognition', False):
            return json.dumps({"status": "disabled", "message": "关系网未启用"}, ensure_ascii=False)
        if not self._relation_lookup_authorized(event):
            return json.dumps({"status": "forbidden", "message": "关系网查询只允许主要用户/管理员在私聊中使用"}, ensure_ascii=False)
        keyword = self._relation_lookup_clean_keyword(
            kwargs.get("keyword")
            or kwargs.get("name")
            or kwargs.get("user")
            or kwargs.get("user_id")
            or kwargs.get("nickname")
            or kwargs.get("query")
            or ""
        )
        if not keyword:
            return json.dumps({"status": "error", "message": "缺少要查询的 QQ 号、昵称或别名"}, ensure_ascii=False)

        matches: list[dict[str, Any]] = []
        if keyword.isdigit():
            matches.extend(self._resolve_worldbook_member_by_name(keyword))
            if not matches:
                users = self.data.get("users") if isinstance(self.data.get("users"), dict) else {}
                user = users.get(keyword) if isinstance(users, dict) else None
                if isinstance(user, dict):
                    label = _single_line(
                        user.get("stable_name") or user.get("nickname") or user.get("display_name") or user.get("last_display_name"),
                        60,
                    )
                    matches.append({"user_id": keyword, "name": label or keyword, "source": "private_user"})
        else:
            matches.extend(self._resolve_worldbook_member_by_name(keyword))
            existing_ids = {str(item.get("user_id") or "") for item in matches}
            for target in self._interaction_query_private_targets(keyword):
                uid = _single_line(target.get("user_id"), 40)
                if uid and uid not in existing_ids and target.get("source") != "qq":
                    matches.append({
                        "user_id": uid,
                        "name": _single_line(target.get("label"), 60) or uid,
                        "source": target.get("source") or "private_user",
                    })
                    existing_ids.add(uid)

        if not matches:
            logger.info("关系网查询未命中: keyword=%s", keyword)
            return json.dumps({"status": "not_found", "keyword": keyword, "message": "关系网里没有确认匹配对象"}, ensure_ascii=False)
        status = "success" if len(matches) == 1 else "ambiguous"
        logger.info("关系网查询命中: keyword=%s count=%s", keyword, len(matches))
        return json.dumps(
            {
                "status": status,
                "keyword": keyword,
                "count": len(matches),
                "matches": matches[:8],
            },
            ensure_ascii=False,
        )

    async def _pc_get_user_id_by_name_impl(self, event: AstrMessageEvent, **kwargs) -> str:
        if not self.enable_atrelay_tools:
            return json.dumps({"status": "disabled", "message": "跨群转述工具未启用"}, ensure_ascii=False)
        group_id = kwargs.get("group_id") or kwargs.get("group") or kwargs.get("group_name") or ""
        nickname = kwargs.get("nickname") or kwargs.get("name") or kwargs.get("keyword") or kwargs.get("user_name") or kwargs.get("user") or ""
        target_group = _single_line(group_id, 40) or self._extract_group_id_from_event(event)
        query = _single_line(nickname, 128)
        if not query:
            return json.dumps({"status": "error", "message": "缺少 nickname/name 参数"}, ensure_ascii=False)
        resolved = await self._resolve_atrelay_target_user(event, target_group, query)
        if resolved.get("ambiguous"):
            return json.dumps({"status": "ambiguous", "message": "匹配到多个群友,需要用户补充 QQ 或更明确称呼", "matches": resolved.get("matches", [])}, ensure_ascii=False)
        if resolved.get("user_id"):
            return json.dumps({"status": "success", **resolved}, ensure_ascii=False)
        return json.dumps({"status": "not_found", "message": "未找到匹配群友"}, ensure_ascii=False)

    async def _pc_get_specified_group_members_impl(self, event: AstrMessageEvent, **kwargs) -> str:
        if not self.enable_atrelay_tools:
            return json.dumps({"status": "disabled", "message": "跨群转述工具未启用"}, ensure_ascii=False)
        group_id = kwargs.get("group_id") or kwargs.get("group") or kwargs.get("group_name") or ""
        keyword = kwargs.get("keyword") or kwargs.get("name") or kwargs.get("nickname") or kwargs.get("user") or ""
        target_group = _single_line(group_id, 40) or self._extract_group_id_from_event(event)
        if not target_group:
            return json.dumps({"status": "error", "message": "未指定群号且当前不在群聊环境中"}, ensure_ascii=False)
        query = _single_line(keyword, 60)
        try:
            members = await self._get_group_member_list_for_tool(event, target_group)
            formatted = [self._format_atrelay_member(item) for item in members]
            if query:
                formatted = [
                    item for item in formatted
                    if query in item.get("user_id", "")
                    or query in item.get("nickname", "")
                    or query in item.get("group_card", "")
                    or query in item.get("relation_name", "")
                ]
            if runtime_persona_setting(self, 'enable_worldbook_member_recognition', True):
                async with self._data_lock:
                    self._save_data_sync(sections={"worldbook_member_profiles"})
            return json.dumps({"status": "success", "group_id": target_group, "count": len(formatted), "members": formatted[:80]}, ensure_ascii=False)
        except Exception as exc:
            return json.dumps({"status": "error", "message": f"查询群成员失败: {_single_line(exc, 120)}"}, ensure_ascii=False)

    def _atrelay_platform_prefix_candidates(self, event: AstrMessageEvent) -> list[str]:
        prefixes: list[str] = []

        def add(value: Any) -> None:
            text = _single_line(value, 80)
            if not text:
                return
            prefix = text.split(":", 1)[0] if ":" in text else text
            if prefix and prefix not in prefixes:
                prefixes.append(prefix)

        add(getattr(event, "unified_msg_origin", ""))
        try:
            sender_id = str(event.get_sender_id())
        except Exception:
            sender_id = ""
        resolver = getattr(self, "_private_user_id_for_event", None)
        if callable(resolver) and sender_id:
            try:
                sender_id = _single_line(resolver(event, sender_id), 160) or sender_id
            except Exception:
                pass
        users = self.data.get("users") if isinstance(self.data.get("users"), dict) else {}
        user = users.get(sender_id) if sender_id and isinstance(users, dict) else None
        if isinstance(user, dict):
            add(user.get("umo"))
        add(getattr(self, "target_platform", ""))
        manager = getattr(getattr(self, "context", None), "platform_manager", None)
        if manager is not None:
            try:
                platforms = list(manager.get_insts())
            except Exception:
                platforms = list(getattr(manager, "platform_insts", []) or [])
            for platform in platforms:
                try:
                    meta = platform.meta()
                except Exception:
                    continue
                add(getattr(meta, "id", ""))
                add(getattr(meta, "name", ""))
        return prefixes

    def _atrelay_target_umo_candidates(self, event: AstrMessageEvent, message_type: str, target_id: str) -> list[str]:
        message_type = "GroupMessage" if message_type == "group" else "FriendMessage"
        target = _single_line(target_id, 40 if message_type == "GroupMessage" else 128)
        if not target:
            return []
        candidates: list[str] = []

        def add_umo(value: Any) -> None:
            umo = _single_line(value, 160)
            if not umo or f":{message_type}:{target}" not in umo:
                return
            if umo not in candidates:
                candidates.append(umo)

        if message_type == "GroupMessage":
            groups = self.data.get("groups") if isinstance(self.data.get("groups"), dict) else {}
            group = groups.get(target) if isinstance(groups, dict) else None
            if isinstance(group, dict):
                add_umo(group.get("umo"))
        else:
            users = self.data.get("users") if isinstance(self.data.get("users"), dict) else {}
            user = users.get(target) if isinstance(users, dict) else None
            if isinstance(user, dict):
                add_umo(user.get("umo"))
        for prefix in self._atrelay_platform_prefix_candidates(event):
            add_umo(f"{prefix}:{message_type}:{target}")
        return candidates

    async def _send_atrelay_chain_to_target(
        self,
        event: AstrMessageEvent,
        *,
        message_type: str,
        target_id: str,
        chain: list[Any],
    ) -> tuple[bool, str, str]:
        errors: list[str] = []
        candidates = self._atrelay_target_umo_candidates(event, message_type, target_id)
        if not candidates:
            return False, "没有可用目标会话", ""
        for umo in candidates:
            session = self._parse_message_session(umo)
            platform = self._get_platform_for_session(session) if session else None
            if session and platform:
                try:
                    session_obj = MessageSession(
                        platform_name=str(getattr(session, "platform_id", "") or ""),
                        message_type=self._message_type_for_session(session),
                        session_id=str(getattr(session, "session_id", "") or ""),
                    )
                    await platform.send_by_session(session_obj, MessageChain(chain))
                    logger.info("转述已通过精确平台发送: umo=%s", _single_line(umo, 160))
                    return True, "", umo
                except Exception as exc:
                    errors.append(f"{umo}: 精确发送失败 {self._format_send_exception(exc)}")
                try:
                    result = await self.context.send_message(umo, MessageChain(chain))
                    if result is not False:
                        logger.info("转述已通过 AstrBot 核心发送: umo=%s", _single_line(umo, 160))
                        return True, "", umo
                    errors.append(f"{umo}: 核心发送返回 False")
                except Exception as exc:
                    errors.append(f"{umo}: 核心发送失败 {self._format_send_exception(exc)}")
            elif session:
                errors.append(f"{umo}: 未找到匹配平台，跳过 AstrBot 核心发送")
            else:
                errors.append(f"{umo}: UMO 无法解析，跳过 AstrBot 核心发送")
            try:
                direct_ok, direct_error = await self._send_chain_components_via_onebot_direct(umo, session, chain)
            except Exception as exc:
                direct_ok, direct_error = False, self._format_send_exception(exc)
            if direct_ok:
                return True, "", umo
            if direct_error:
                errors.append(f"{umo}: OneBot 兜底失败 {direct_error}")
        return False, "；".join(errors[-5:]) or "所有发送链路都失败", candidates[0]
