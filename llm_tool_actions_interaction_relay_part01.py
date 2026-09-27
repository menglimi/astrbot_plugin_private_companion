# -*- coding: utf-8 -*-
"""LlmToolActionsInteractionRelayPart01Mixin。

由 tools/split_mixin_domain.py 从 llm_tool_actions_interaction_relay.py 机械抽取（14 个方法 + 0 个模块级名字 + 0 个类级赋值 / 362 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 LlmToolActionsInteractionRelayMixin）。
"""
from __future__ import annotations

import json
import re
from .conversation_prompt_section import PromptSection, prompt_section
from .helpers import _now_ts, _safe_float, _safe_int, _single_line
from .interaction_query_orchestrator import execute_interaction_query
from .interaction_tool_contract import InteractionQuery
from .llm_tool_actions_shared import _render_tool_prompt_section_labeled, logger
from .persona_config import runtime_persona_setting
from astrbot.api.event import AstrMessageEvent
from typing import Any



class LlmToolActionsInteractionRelayPart01Mixin:
    """LlmToolActionsInteractionRelayPart01Mixin（从 LlmToolActionsInteractionRelayMixin 拆出）。"""


    def _cross_user_memory_query_prompt_section(self) -> PromptSection | None:
        if not (self.enabled and getattr(self, "enable_cross_user_memory_bridge", False)):
            return None
        body = """用户在私聊里问“你和某人聊了什么”“最近和某群互动怎样”“某人在群里说过什么”时，可以用 `pc_query_interaction` 读取近期互动摘要。
- 只用于查询，不发送消息。
- 优先传 scope=private/group、user_hint 或 group_hint；不确定时传原始称呼给 hint。
- “最近和他私聊说了什么”传 scope=private,user_hint=对象；“他在群里说了什么”传 scope=group,user_hint=对象，有具体群再加 group_hint。
- 回答时概括最近互动和重点即可，不要大段复述原文。
""".strip()
        return prompt_section(
            key="tools.cross_user_memory",
            title="跨用户记忆互通",
            source="tools",
            content=body,
        )

    def _cross_user_memory_query_instruction(self) -> str:
        return _render_tool_prompt_section_labeled(
            self._cross_user_memory_query_prompt_section()
        )

    def _relation_lookup_prompt_section(self) -> PromptSection | None:
        if not (self.enabled and runtime_persona_setting(self, 'enable_worldbook_member_recognition', False)):
            return None
        body = """用户明确要求“查一下关系网/帮我查某个 QQ 或昵称”时，可以用 `pc_query_relation_person` 查询关系网。
- 如果刚用 LivingMemory/长期记忆召回到某个人名、昵称、QQ 或群成员别名,并且需要判断 TA 是谁、和用户什么关系、能不能套用某段关系时,也可以先查关系网再回答。
- 只用于确认是否认识和读取稳定称呼、别名、简短身份备注；不要发送消息。
- 参数用 keyword 传 QQ 号、昵称、别名或用户原话里最像名字的部分。
- 查不到就自然说明没在关系网里确认过，不要编造。
""".strip()
        return prompt_section(
            key="tools.relation_lookup",
            title="关系网查询",
            source="tools",
            content=body,
        )

    def _relation_lookup_instruction(self) -> str:
        return _render_tool_prompt_section_labeled(
            self._relation_lookup_prompt_section()
        )

    def _interaction_query_platform(self, event: AstrMessageEvent) -> str:
        origin = str(getattr(event, "unified_msg_origin", "") or "")
        platform = origin.split(":", 1)[0] if ":" in origin else ""
        return platform or getattr(self, "target_platform", "") or "aiocqhttp"

    def _interaction_query_private_targets(self, hint: str = "") -> list[dict[str, str]]:
        query = _single_line(hint, 128)
        users = self.data.get("users") if isinstance(self.data.get("users"), dict) else {}
        profiles = self.data.get("worldbook_member_profiles") if isinstance(self.data.get("worldbook_member_profiles"), dict) else {}
        targets: dict[str, dict[str, str]] = {}

        def add(user_id: str, label: str = "", source: str = "") -> None:
            user_id = _single_line(user_id, 128)
            if not user_id:
                return
            existing = targets.setdefault(user_id, {"user_id": user_id, "label": "", "source": ""})
            if label and (not existing.get("label") or existing.get("label") == user_id):
                existing["label"] = label
            if source and not existing.get("source"):
                existing["source"] = source

        if query and query.isdigit():
            add(query, query, "qq")
        configured_ids = set(self._configured_target_ids()) if callable(getattr(self, "_configured_target_ids", None)) else set()
        for configured_id in configured_ids:
            uid = _single_line(configured_id, 128)
            if uid and (not query or query == uid or query in uid):
                add(uid, uid, "target_config")
        for user_id, user in users.items():
            if not isinstance(user, dict):
                continue
            uid = _single_line(user.get("user_id") or user_id, 128)
            try:
                uid = self._canonical_private_user_id(uid)
            except Exception:
                pass
            if not uid or not self._is_target_private_user(uid, user) or not bool(user.get("enabled", True)):
                continue
            tokens = [
                uid,
                user.get("nickname"),
                user.get("display_name"),
                user.get("last_display_name"),
                user.get("stable_name"),
                *(user.get("observed_display_names") if isinstance(user.get("observed_display_names"), list) else []),
                *(user.get("aliases") if isinstance(user.get("aliases"), list) else []),
            ]
            clean_tokens = [_single_line(token, 60) for token in tokens if _single_line(token, 60)]
            if not query or any(query == token or (query and query in token) for token in clean_tokens):
                label = next((token for token in clean_tokens if token and token != uid), uid)
                add(uid, label, "private_user")
        for user_id, profile in profiles.items():
            if not isinstance(profile, dict):
                continue
            uid = _single_line(profile.get("linked_qq_user_id") or profile.get("user_id") or user_id, 40)
            if not uid or not uid.isdigit():
                continue
            try:
                uid = self._canonical_private_user_id(uid)
            except Exception:
                pass
            linked_user = users.get(uid) if isinstance(users, dict) else None
            configured_target = uid in configured_ids
            if not configured_target and not (
                isinstance(linked_user, dict)
                and self._is_target_private_user(uid, linked_user)
                and bool(linked_user.get("enabled", True))
            ):
                continue
            tokens = [
                uid,
                profile.get("name"),
                *(profile.get("aliases") if isinstance(profile.get("aliases"), list) else []),
                *(profile.get("observed_names") if isinstance(profile.get("observed_names"), list) else []),
            ]
            clean_tokens = [_single_line(token, 60) for token in tokens if _single_line(token, 60)]
            if not query or any(query == token or (query and query in token) for token in clean_tokens):
                label = next((token for token in clean_tokens if token and token != uid), uid)
                add(uid, label, "worldbook")
        return list(targets.values())[:12]

    async def _interaction_query_group_targets(self, event: AstrMessageEvent, hint: str = "") -> list[dict[str, str]]:
        query = _single_line(hint, 80)
        targets: dict[str, dict[str, str]] = {}

        def group_allowed(group_id: str) -> bool:
            checker = getattr(self, "_group_enabled_for_event", None)
            if not callable(checker):
                return False
            try:
                return bool(checker(group_id))
            except Exception:
                return False

        def add(group_id: str, label: str = "", source: str = "") -> None:
            group_id = _single_line(group_id, 40)
            if not group_id or not group_allowed(group_id):
                return
            existing = targets.setdefault(group_id, {"group_id": group_id, "label": "", "source": ""})
            if label and (not existing.get("label") or existing.get("label") == group_id):
                existing["label"] = label
            if source and not existing.get("source"):
                existing["source"] = source

        if query and query.isdigit():
            add(query, query, "group_id")
        groups = self.data.get("groups") if isinstance(self.data.get("groups"), dict) else {}
        for group_id, group in groups.items():
            if not isinstance(group, dict):
                continue
            gid = _single_line(group.get("group_id") or group_id, 40)
            tokens = [
                gid,
                group.get("name"),
                group.get("group_name"),
                group.get("display_name"),
                group.get("nickname"),
            ]
            clean_tokens = [_single_line(token, 80) for token in tokens if _single_line(token, 80)]
            if not query or any(query == token or (query and query in token) for token in clean_tokens):
                label = next((token for token in clean_tokens if token and token != gid), gid)
                add(gid, label, "plugin_group")
        profiles = self.data.get("worldbook_group_profiles") if isinstance(self.data.get("worldbook_group_profiles"), dict) else {}
        for group_id, profile in profiles.items():
            if not isinstance(profile, dict):
                continue
            gid = _single_line(profile.get("group_id") or group_id, 40)
            tokens = [gid, profile.get("name"), profile.get("title"), profile.get("display_name")]
            clean_tokens = [_single_line(token, 80) for token in tokens if _single_line(token, 80)]
            if not query or any(query == token or (query and query in token) for token in clean_tokens):
                label = next((token for token in clean_tokens if token and token != gid), gid)
                add(gid, label, "worldbook_group")
        return list(targets.values())[:12]

    async def _interaction_query_read_history(self, umo: str, *, limit: int = 40, hours: int = 72) -> list[dict[str, Any]]:
        getter = getattr(self, "_get_current_conversation_safely", None)
        try:
            if callable(getter):
                conv = await getter(umo, label="cross_user_memory_query")
            else:
                conv_id = await self.context.conversation_manager.get_curr_conversation_id(umo)
                if not conv_id:
                    return []
                conv = await self.context.conversation_manager.get_conversation(umo, conv_id)
        except Exception:
            return []
        history = self._load_conversation_history_items(conv)
        if not history:
            return []
        max_items = max(5, min(120, _safe_int(limit, 40, 5)))
        cutoff = _now_ts() - max(1, min(24 * 30, _safe_int(hours, 72, 1))) * 3600
        dated: list[dict[str, Any]] = []
        undated: list[dict[str, Any]] = []
        for item in history:
            if not isinstance(item, dict):
                continue
            if not self._history_item_content_text(item):
                continue
            ts = self._history_item_timestamp(item)
            if ts is None:
                undated.append(item)
            elif ts >= cutoff:
                dated.append(item)
        selected = dated[-max_items:]
        return [item for item in selected if isinstance(item, dict)][-max_items:]

    def _interaction_query_lines(self, history: list[dict[str, Any]], *, limit: int = 24) -> list[str]:
        lines: list[str] = []
        for item in history[-max(1, limit):]:
            line = self._format_history_item_for_summary(item)
            if not line:
                continue
            line = re.sub(r"\s+", " ", line).strip()
            if line and line not in lines:
                lines.append(line)
        return lines

    def _interaction_query_user_filter_tokens(self, user_hint: str = "") -> tuple[set[str], set[str]]:
        user_hint = _single_line(user_hint, 128)
        ids: set[str] = set()
        names: set[str] = set()
        if user_hint:
            if user_hint.isdigit():
                ids.add(user_hint)
            else:
                names.add(user_hint)
        for target in self._interaction_query_private_targets(user_hint):
            user_id = _single_line(target.get("user_id"), 40)
            label = _single_line(target.get("label"), 60)
            if user_id:
                ids.add(user_id)
            if label and label != user_id:
                names.add(label)
        return ids, names

    def _interaction_query_group_recent_lines(self, group_id: str, *, limit: int = 24, user_hint: str = "", hours: int = 72) -> list[str]:
        groups = self.data.get("groups") if isinstance(self.data.get("groups"), dict) else {}
        group = groups.get(str(group_id))
        if not isinstance(group, dict):
            return []
        checker = getattr(self, "_group_enabled_for_event", None)
        if not callable(checker):
            return []
        try:
            if not checker(str(group_id)):
                return []
        except Exception:
            return []
        recent = group.get("recent_messages") if isinstance(group.get("recent_messages"), list) else []
        filter_ids, filter_names = self._interaction_query_user_filter_tokens(user_hint)
        cutoff = _now_ts() - max(1, min(24 * 30, _safe_int(hours, 72, 1))) * 3600
        lines: list[str] = []
        for item in recent[-max(1, limit):]:
            if not isinstance(item, dict):
                continue
            sender_id = _single_line(item.get("sender_id") or item.get("user_id"), 40)
            speaker = _single_line(item.get("identity_name") or item.get("name") or item.get("sender_name") or sender_id, 40) or "群友"
            if user_hint:
                speaker_hit = any(token and (token == speaker or token in speaker) for token in filter_names)
                if not ((sender_id and sender_id in filter_ids) or speaker_hit):
                    continue
            text = _single_line(item.get("text") or item.get("message"), 220)
            if not text:
                continue
            ts = _safe_float(item.get("ts") or item.get("time") or item.get("timestamp"), 0)
            if ts > 10_000_000_000:
                ts /= 1000
            if ts <= 0 or ts < cutoff:
                continue
            prefix = ""
            if ts > 0:
                try:
                    prefix = self._environment_fromtimestamp(ts).strftime("%m-%d %H:%M") + " "
                except Exception:
                    prefix = ""
            lines.append(f"{prefix}{speaker}: {text}")
        return lines

    def _interaction_query_group_user_recent_lines(self, user_hint: str, *, limit: int = 36, hours: int = 72) -> list[str]:
        user_hint = _single_line(user_hint, 128)
        if not user_hint:
            return []
        groups = self.data.get("groups") if isinstance(self.data.get("groups"), dict) else {}
        lines: list[str] = []
        per_group_limit = max(4, min(12, limit // 3 or 8))
        for group_id, group in groups.items():
            if not isinstance(group, dict):
                continue
            group_label = _single_line(group.get("name") or group.get("group_name") or group_id, 60)
            group_lines = self._interaction_query_group_recent_lines(
                str(group_id),
                limit=per_group_limit,
                user_hint=user_hint,
                hours=hours,
            )
            for line in group_lines:
                lines.append(f"{group_label}｜{line}")
        return lines[-max(1, limit):]

    async def _pc_query_interaction_impl(self, event: AstrMessageEvent, **kwargs) -> str:
        if not getattr(self, "enable_cross_user_memory_bridge", False):
            return json.dumps({"status": "disabled", "message": "跨用户记忆互通未启用"}, ensure_ascii=False)
        try:
            is_private = bool(getattr(event, "is_private_chat", lambda: False)())
        except Exception:
            is_private = ":FriendMessage:" in str(getattr(event, "unified_msg_origin", "") or "")
        try:
            identity_for_event = getattr(self, "_event_permission_identity_id", None)
            requester_id = (
                identity_for_event(event)
                if callable(identity_for_event)
                else self._permission_identity_id(event.get_sender_id())
            )
        except Exception:
            requester_id = ""
        owner_only = bool(getattr(self, "cross_user_memory_owner_only", True))
        owner_allowed = bool(requester_id and self._is_private_companion_owner_user_id(requester_id))
        admin_allowed = bool(requester_id and self._is_configured_admin_user_id(requester_id))
        allowed = owner_allowed or (not owner_only and admin_allowed)
        forbidden_message = "只有配置的主要用户可以查询 Bot 与其他人的互动。" if owner_only else "只有配置的主要用户或 AstrBot 全局管理员可以查询 Bot 与其他人的互动。"
        if not is_private or not allowed:
            logger.info(
                "跨用户互动查询权限未通过: sender=%s owner=%s admin=%s owner_only=%s umo=%s",
                requester_id or "-",
                owner_allowed,
                admin_allowed,
                owner_only,
                _single_line(getattr(event, "unified_msg_origin", ""), 120),
            )
            return json.dumps({"status": "forbidden", "message": forbidden_message}, ensure_ascii=False)
        query = InteractionQuery.parse(kwargs)
        scope = query.scope
        user_hint = query.user_hint
        group_hint = query.group_hint
        hint = query.hint
        hours = query.hours
        limit = query.limit

        return await execute_interaction_query(self, event, query)

    async def _pc_get_group_id_by_name_impl(self, event: AstrMessageEvent, **kwargs) -> str:
        if not self.enable_atrelay_tools:
            return json.dumps({"status": "disabled", "message": "跨群转述工具未启用"}, ensure_ascii=False)
        group_name = kwargs.get("group_name") or kwargs.get("name") or kwargs.get("keyword") or kwargs.get("group_id") or ""
        keyword = _single_line(group_name, 80)
        cached = self._atrelay_cached_group_matches(keyword)
        if cached:
            return json.dumps(
                {
                    "status": "success",
                    "count": len(cached),
                    "groups": cached[:20],
                    "source": "local_cache",
                    "message": "已从插件群缓存/关系网群档案匹配，未依赖平台群列表。",
                },
                ensure_ascii=False,
            )
        bot = getattr(event, "bot", None)
        api = getattr(bot, "api", None)
        call_action = getattr(api, "call_action", None)
        if not callable(call_action):
            return json.dumps({"status": "error", "message": "当前平台不支持获取群列表，本地群缓存/关系网群档案也未命中"}, ensure_ascii=False)
        try:
            groups = await call_action("get_group_list")
            matches = []
            for item in groups if isinstance(groups, list) else []:
                group_id = str(item.get("group_id") or "")
                name = _single_line(item.get("group_name") or item.get("group_remark"), 100)
                if not keyword or keyword in name or keyword in group_id:
                    matches.append({"group_id": group_id, "group_name": name})
            return json.dumps({"status": "success", "count": len(matches), "groups": matches[:20]}, ensure_ascii=False)
        except Exception as exc:
            return json.dumps({"status": "error", "message": f"获取群列表失败: {_single_line(exc, 120)}"}, ensure_ascii=False)
