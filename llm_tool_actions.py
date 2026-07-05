# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import re
import uuid
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.event import MessageChain
from astrbot.core.platform.message_session import MessageSession
try:
    from astrbot.api.message_components import At, Plain
except ImportError:
    from astrbot.api.message_components import At, Plain

from .helpers import _now_ts, _safe_float, _safe_int, _single_line
from .qzone_selection import parse_qzone_post_selection


class LlmToolActionsMixin:
    """Implementation bodies for LLM tools registered in main.py."""

    def _cross_user_memory_query_instruction(self) -> str:
        if not (self.enabled and getattr(self, "enable_cross_user_memory_bridge", False)):
            return ""
        return """
【跨用户记忆互通】
用户在私聊里问“你和某人聊了什么”“最近和某群互动怎样”“某人在群里说过什么”时，可以用 `pc_query_interaction` 读取近期互动摘要。
- 只用于查询，不发送消息。
- 优先传 scope=private/group、user_hint 或 group_hint；不确定时传原始称呼给 hint。
- “最近和他私聊说了什么”传 scope=private,user_hint=对象；“他在群里说了什么”传 scope=group,user_hint=对象，有具体群再加 group_hint。
- 回答时概括最近互动和重点即可，不要大段复述原文。
""".strip()

    def _relation_lookup_instruction(self) -> str:
        if not (self.enabled and getattr(self, "enable_worldbook_member_recognition", False)):
            return ""
        return """
【关系网查询】
用户明确要求“查一下关系网/帮我查某个 QQ 或昵称”时，可以用 `pc_query_relation_person` 查询关系网。
- 如果刚用 LivingMemory/长期记忆召回到某个人名、昵称、QQ 或群成员别名,并且需要判断 TA 是谁、和用户什么关系、能不能套用某段关系时,也可以先查关系网再回答。
- 只用于确认是否认识和读取稳定称呼、别名、简短身份备注；不要发送消息。
- 参数用 keyword 传 QQ 号、昵称、别名或用户原话里最像名字的部分。
- 查不到就自然说明没在关系网里确认过，不要编造。
""".strip()

    def _qzone_tool_instruction(self) -> str:
        if not (self.enabled and self.enable_qzone_integration):
            return ""
        return """
【QQ 空间动态工具】
当用户明确要求你查看说说、QQ 空间动态、点赞/评论说说,或要求你发一条说说时,可以使用 Private Companion 的 QQ 空间工具。
- 查看说说：使用 `pc_qzone_view_feed`。不知道目标 QQ 时默认当前用户；可用 `selector` 传“最新”“第2条”“最后”或 fid。
- 发布说说：使用 `pc_qzone_publish_feed`。必须把最终要发布的正文放进 `text` 参数,例如 `{"text":"今天想慢一点。"}`；如需带图,可传 `{"text":"配图说说","images":["本地图片路径或图片URL"]}`；如果用户明确要求“发布刚才/最近生成的生活说说草稿”,可传 `{"use_latest_draft":true}`；不要空调用,不要把草稿当作已发布。
- 发布内容必须服从当前人格与世界观,但不要泄露私聊隐私、内部状态数值、关系网资料或插件实现。
- 工具失败时简短说明失败原因,不要假装已经发布或点赞。
""".strip()

    async def _pc_qzone_view_feed_impl(
        self,
        event: AstrMessageEvent,
        user_id: str = "",
        pos: int = 0,
        like: bool = False,
        reply: bool = False,
        selector: str = "",
        fid: str = "",
    ) -> str:
        if not self.enable_qzone_integration:
            return json.dumps({"status": "disabled", "message": "QQ 空间动态层未启用"}, ensure_ascii=False)
        target = _single_line(user_id, 40)
        if not target:
            try:
                target = str(event.get_sender_id())
            except Exception:
                target = ""
        try:
            selection = parse_qzone_post_selection(user_id=target, selector=selector, pos=pos, fid=fid)
            if selection.fid:
                candidates = await self._qzone_query_feeds(event, target_id=selection.target_id or None, pos=0, num=20, with_detail=True)
                posts = [
                    item for item in candidates
                    if str(getattr(item, "tid", "") or "") == selection.fid
                    or str(self._qzone_post_value(item, "fid", "") or "") == selection.fid
                ][:1]
            elif selection.is_last:
                candidates = await self._qzone_query_feeds(event, target_id=selection.target_id or None, pos=0, num=10, with_detail=True)
                posts = candidates[-1:] if candidates else []
            else:
                posts = await self._qzone_query_feeds(event, target_id=selection.target_id or None, pos=max(0, int(selection.pos or 0)), num=1, with_detail=True)
            if not posts:
                return json.dumps({"status": "empty", "message": "查询结果为空"}, ensure_ascii=False)
            post = posts[0]
            action_msg = ""
            if reply:
                comment = await self._qzone_comment_post(event, post)
                action_msg = f"已评论：{comment}"
            like_result: dict[str, Any] | None = None
            if like:
                like_result = await self._qzone_like_post(event, post)
                like_text = "已点赞" if like_result.get("verified") else "点赞请求已受理，等待 QQ 空间同步"
                action_msg = (action_msg + f"；{like_text}") if action_msg else like_text
            return json.dumps(
                {
                    "status": "success",
                    "action": action_msg,
                    "like_result": like_result or {},
                    "author": _single_line(getattr(post, "name", ""), 60),
                    "uin": str(getattr(post, "uin", "") or ""),
                    "text": _single_line(getattr(post, "text", "") or getattr(post, "rt_con", ""), 300),
                    "images": list(getattr(post, "images", []) or [])[:6],
                },
                ensure_ascii=False,
            )
        except Exception as exc:
            return json.dumps({"status": "error", "message": _single_line(exc, 160)}, ensure_ascii=False)

    async def _pc_qzone_publish_feed_impl(self, event: AstrMessageEvent, text: str = "", **kwargs) -> str:
        content = _single_line(text or kwargs.get("content") or kwargs.get("message") or kwargs.get("draft"), 300)
        images: list[str] = []
        for key in ("images", "image_paths", "image_urls"):
            value = kwargs.get(key)
            if isinstance(value, (list, tuple)):
                images.extend(str(item).strip() for item in value if str(item or "").strip())
            elif isinstance(value, str) and value.strip():
                images.append(value.strip())
        for key in ("image", "image_path", "image_url", "path"):
            value = kwargs.get(key)
            if isinstance(value, str) and value.strip():
                images.append(value.strip())
        images = list(dict.fromkeys(images))[:9]
        if not content and kwargs.get("use_latest_draft"):
            state = self.data.get("qzone_integration") if isinstance(self.data.get("qzone_integration"), dict) else {}
            content = _single_line(state.get("last_life_publish_draft") or state.get("last_life_publish_text"), 300)
        if not content and not images:
            return json.dumps(
                {
                    "status": "need_text",
                    "success": False,
                    "message": "缺少 text 或 images 参数。请把要发布的说说正文作为 text 传入；如需带图,传 images；若要发布最近自动生成的生活草稿,传 use_latest_draft=true。",
                    "required_args": {"text": "要发布到 QQ 空间的说说正文", "images": "可选，本地图片路径或图片URL列表"},
                },
                ensure_ascii=False,
            )
        result = await self._publish_qzone_text(content, event, images=images, auto_generate_image=True)
        return json.dumps({"status": "success" if result.get("success") else "error", **result}, ensure_ascii=False)

    def _interaction_query_platform(self, event: AstrMessageEvent) -> str:
        origin = str(getattr(event, "unified_msg_origin", "") or "")
        platform = origin.split(":", 1)[0] if ":" in origin else ""
        return platform or getattr(self, "target_platform", "") or "aiocqhttp"

    def _interaction_query_private_targets(self, hint: str = "") -> list[dict[str, str]]:
        query = _single_line(hint, 80)
        users = self.data.get("users") if isinstance(self.data.get("users"), dict) else {}
        profiles = self.data.get("worldbook_member_profiles") if isinstance(self.data.get("worldbook_member_profiles"), dict) else {}
        targets: dict[str, dict[str, str]] = {}

        def add(user_id: str, label: str = "", source: str = "") -> None:
            user_id = _single_line(user_id, 40)
            if not user_id:
                return
            existing = targets.setdefault(user_id, {"user_id": user_id, "label": "", "source": ""})
            if label and (not existing.get("label") or existing.get("label") == user_id):
                existing["label"] = label
            if source and not existing.get("source"):
                existing["source"] = source

        if query and query.isdigit():
            add(query, query, "qq")
        for user_id, user in users.items():
            if not isinstance(user, dict):
                continue
            uid = _single_line(user.get("user_id") or user_id, 40)
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

        def add(group_id: str, label: str = "", source: str = "") -> None:
            group_id = _single_line(group_id, 40)
            if not group_id:
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
        if query and len(targets) <= 1:
            try:
                result = await self._resolve_atrelay_target_group(event, query)
                if isinstance(result, dict) and result.get("status") == "success":
                    gid = _single_line(result.get("group_id"), 40)
                    label = _single_line(result.get("group_name") or result.get("name"), 80) or gid
                    add(gid, label, "platform")
            except Exception:
                pass
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
        selected = dated[-max_items:] if dated else (undated or history)[-max_items:]
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
        user_hint = _single_line(user_hint, 80)
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

    def _interaction_query_group_recent_lines(self, group_id: str, *, limit: int = 24, user_hint: str = "") -> list[str]:
        groups = self.data.get("groups") if isinstance(self.data.get("groups"), dict) else {}
        group = groups.get(str(group_id))
        if not isinstance(group, dict):
            return []
        recent = group.get("recent_messages") if isinstance(group.get("recent_messages"), list) else []
        filter_ids, filter_names = self._interaction_query_user_filter_tokens(user_hint)
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
            prefix = ""
            if ts > 0:
                try:
                    prefix = self._environment_fromtimestamp(ts / 1000 if ts > 10_000_000_000 else ts).strftime("%m-%d %H:%M") + " "
                except Exception:
                    prefix = ""
            lines.append(f"{prefix}{speaker}: {text}")
        return lines

    def _interaction_query_group_user_recent_lines(self, user_hint: str, *, limit: int = 36) -> list[str]:
        user_hint = _single_line(user_hint, 80)
        if not user_hint:
            return []
        groups = self.data.get("groups") if isinstance(self.data.get("groups"), dict) else {}
        lines: list[str] = []
        per_group_limit = max(4, min(12, limit // 3 or 8))
        for group_id, group in groups.items():
            if not isinstance(group, dict):
                continue
            group_label = _single_line(group.get("name") or group.get("group_name") or group_id, 60)
            group_lines = self._interaction_query_group_recent_lines(str(group_id), limit=per_group_limit, user_hint=user_hint)
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
            requester_id = str(event.get_sender_id())
        except Exception:
            requester_id = ""
        owner_only = bool(getattr(self, "cross_user_memory_owner_only", True))
        if owner_only:
            users = self.data.get("users") if isinstance(self.data.get("users"), dict) else {}
            requester_profile = users.get(requester_id) if isinstance(users, dict) else None
            try:
                requester_is_owner = isinstance(requester_profile, dict) and self._private_user_role(requester_profile, requester_id) == "owner"
            except Exception:
                requester_is_owner = False
            allowed = requester_id in set(self._configured_target_ids()) or requester_is_owner
            forbidden_message = "只有主人用户可以查询 Bot 与其他人的互动。"
        else:
            allowed = self._can_manage_private_companion(event)
            forbidden_message = "只有主人/管理员在私聊中可以查询 Bot 与其他人的互动。"
        if not is_private or not allowed:
            return json.dumps({"status": "forbidden", "message": forbidden_message}, ensure_ascii=False)
        scope = _single_line(kwargs.get("scope") or kwargs.get("type") or "auto", 20).lower()
        user_hint = _single_line(kwargs.get("user_hint") or kwargs.get("user") or kwargs.get("user_id") or kwargs.get("target_user") or "", 80)
        group_hint = _single_line(kwargs.get("group_hint") or kwargs.get("group") or kwargs.get("group_id") or kwargs.get("target_group") or "", 80)
        hint = _single_line(kwargs.get("hint") or kwargs.get("target") or kwargs.get("name") or "", 80)
        hours = max(1, min(24 * 30, _safe_int(kwargs.get("hours"), 72, 1)))
        limit = max(5, min(80, _safe_int(kwargs.get("limit"), 36, 5)))
        if scope in {"群", "群聊", "group_message"}:
            scope = "group"
        elif scope in {"私聊", "好友", "friend", "private_message", "user"}:
            scope = "private"
        elif scope not in {"auto", "private", "group"}:
            scope = "auto"
        if scope == "auto":
            if group_hint:
                scope = "group"
            elif user_hint:
                scope = "private"
            elif hint and "群" in hint:
                scope = "group"
            else:
                scope = "private"
        platform = self._interaction_query_platform(event)
        target_hint = user_hint or group_hint or hint
        if scope == "private":
            targets = self._interaction_query_private_targets(user_hint or hint)
            if not targets:
                return json.dumps({"status": "not_found", "message": "没有找到匹配的私聊对象", "hint": target_hint}, ensure_ascii=False)
            if len(targets) > 1 and not (user_hint or hint).isdigit():
                return json.dumps({"status": "ambiguous", "message": "匹配到多个私聊对象，需要补充 QQ 或更明确称呼", "matches": targets[:8]}, ensure_ascii=False)
            target = targets[0]
            user_id = target.get("user_id", "")
            umo = f"{platform}:FriendMessage:{user_id}"
            history = await self._interaction_query_read_history(umo, limit=limit, hours=hours)
            lines = self._interaction_query_lines(history, limit=min(limit, 28))
            return json.dumps(
                {
                    "status": "success" if lines else "empty",
                    "scope": "private",
                    "target": target,
                    "session": umo,
                    "hours": hours,
                    "message_count": len(lines),
                    "recent_lines": lines,
                    "reply_hint": "请用自然口吻向主人概括最近互动；可以提到对象和大致话题，不要大段复述原文。",
                },
                ensure_ascii=False,
            )
        if user_hint and not (group_hint or hint):
            lines = self._interaction_query_group_user_recent_lines(user_hint, limit=min(limit, 36))
            return json.dumps(
                {
                    "status": "success" if lines else "empty",
                    "scope": "group_user",
                    "target": {"user_hint": user_hint},
                    "hours": hours,
                    "message_count": len(lines),
                    "recent_lines": lines,
                    "reply_hint": "请概括这个人最近在群里的发言和互动；如果线索不足，就说明目前只看到这些近期群聊记录。",
                },
                ensure_ascii=False,
            )
        targets = await self._interaction_query_group_targets(event, group_hint or hint)
        if not targets:
            return json.dumps({"status": "not_found", "message": "没有找到匹配的群聊", "hint": target_hint}, ensure_ascii=False)
        if len(targets) > 1 and not (group_hint or hint).isdigit():
            return json.dumps({"status": "ambiguous", "message": "匹配到多个群聊，需要补充群号或更明确群名", "matches": targets[:8]}, ensure_ascii=False)
        target = targets[0]
        group_id = target.get("group_id", "")
        umo = f"{platform}:GroupMessage:{group_id}"
        if user_hint:
            history = []
            lines = self._interaction_query_group_recent_lines(group_id, limit=min(limit, 28), user_hint=user_hint)
        else:
            history = await self._interaction_query_read_history(umo, limit=limit, hours=hours)
            lines = self._interaction_query_lines(history, limit=min(limit, 28))
            if not lines:
                lines = self._interaction_query_group_recent_lines(group_id, limit=min(limit, 28))
        return json.dumps(
            {
                "status": "success" if lines else "empty",
                "scope": "group",
                "target": target,
                "user_hint": user_hint,
                "session": umo,
                "hours": hours,
                "message_count": len(lines),
                "recent_lines": lines,
                "reply_hint": "请用自然口吻向主人概括 Bot 最近在这个群里的互动；不要把群聊原文整段搬出来。",
            },
            ensure_ascii=False,
        )

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

    def _relation_lookup_authorized(self, event: AstrMessageEvent) -> bool:
        try:
            is_private = bool(getattr(event, "is_private_chat", lambda: False)())
        except Exception:
            is_private = ":FriendMessage:" in str(getattr(event, "unified_msg_origin", "") or "")
        if not is_private:
            return False
        try:
            requester_id = self._canonical_private_user_id(str(event.get_sender_id()))
        except Exception:
            requester_id = ""
        requester_profile = None
        try:
            requester_profile = self._get_user(requester_id) if requester_id else None
        except Exception:
            users = self.data.get("users") if isinstance(self.data.get("users"), dict) else {}
            requester_profile = users.get(requester_id) if isinstance(users, dict) else None
        if requester_id and self._is_target_private_user(requester_id, requester_profile if isinstance(requester_profile, dict) else None):
            return True
        try:
            if isinstance(requester_profile, dict) and self._private_user_role(requester_profile, requester_id) == "owner":
                return True
        except Exception:
            pass
        try:
            allowed = bool(self._can_manage_private_companion(event))
            if not allowed:
                role = ""
                try:
                    role = self._private_user_role(requester_profile, requester_id) if isinstance(requester_profile, dict) else ""
                except Exception:
                    role = ""
                logger.info(
                    "[PrivateCompanion] 关系网查询权限未通过: private=%s sender=%s role=%s target=%s umo=%s",
                    is_private,
                    requester_id or "-",
                    role or "-",
                    bool(requester_id and self._is_target_private_user(requester_id, requester_profile if isinstance(requester_profile, dict) else None)),
                    _single_line(getattr(event, "unified_msg_origin", ""), 120),
                )
            return allowed
        except Exception:
            return False

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
        if not getattr(self, "enable_worldbook_member_recognition", False):
            return json.dumps({"status": "disabled", "message": "关系网未启用"}, ensure_ascii=False)
        if not self._relation_lookup_authorized(event):
            return json.dumps({"status": "forbidden", "message": "关系网查询只允许主人/管理员在私聊中使用"}, ensure_ascii=False)
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
            logger.info("[PrivateCompanion] 关系网查询未命中: keyword=%s", keyword)
            return json.dumps({"status": "not_found", "keyword": keyword, "message": "关系网里没有确认匹配对象"}, ensure_ascii=False)
        status = "success" if len(matches) == 1 else "ambiguous"
        logger.info("[PrivateCompanion] 关系网查询命中: keyword=%s count=%s", keyword, len(matches))
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
        query = _single_line(nickname, 60)
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
            if self.enable_worldbook_member_recognition:
                async with self._data_lock:
                    self._save_data_sync()
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
        target = _single_line(target_id, 40)
        if not target:
            return []
        message_type = "GroupMessage" if message_type == "group" else "FriendMessage"
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
                    logger.info("[PrivateCompanion] 转述已通过精确平台发送: umo=%s", _single_line(umo, 160))
                    return True, "", umo
                except Exception as exc:
                    errors.append(f"{umo}: 精确发送失败 {self._format_send_exception(exc)}")
                try:
                    result = await self.context.send_message(umo, MessageChain(chain))
                    if result is not False:
                        logger.info("[PrivateCompanion] 转述已通过 AstrBot 核心发送: umo=%s", _single_line(umo, 160))
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

    async def _pc_relay_message_impl(self, event: AstrMessageEvent, **kwargs) -> str:
        if not self.enable_atrelay_tools:
            return json.dumps({"status": "disabled", "message": "跨会话转述工具未启用"}, ensure_ascii=False)
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
        recipient = _single_line(recipient_hint, 80)
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
            return json.dumps({"status": "need_recipient", "message": "需要补充私聊目标 QQ 或称呼"}, ensure_ascii=False)
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
                        "status": "need_group_or_qq",
                        "message": "关系网里没有唯一确认这个称呼；请补充目标所在群号/群名，或直接提供 QQ。",
                    },
                    ensure_ascii=False,
                )
            if not resolved.get("user_id") and not resolved.get("ambiguous"):
                resolved = await self._resolve_atrelay_target_user(event, group_id, target_user)
            if resolved.get("ambiguous"):
                return json.dumps(
                    {
                        "status": "ambiguous",
                        "message": "匹配到多个用户，请补充 QQ",
                        "matches": resolved.get("matches", [])[:8],
                    },
                    ensure_ascii=False,
                )
            target_user = _single_line(resolved.get("user_id"), 40)
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
        group_id = kwargs.get("group_id") or kwargs.get("group") or kwargs.get("target_group") or ""
        message = kwargs.get("message") or kwargs.get("text") or kwargs.get("content") or kwargs.get("msg") or ""
        at_user = kwargs.get("at_user") or kwargs.get("at") or kwargs.get("target_user") or kwargs.get("user_id") or ""
        at_qq_list = kwargs.get("at_qq_list") or kwargs.get("at_users") or kwargs.get("at_list")
        if not at_user and isinstance(at_qq_list, list) and at_qq_list:
            at_user = str(at_qq_list[0])
        relay_mode = kwargs.get("relay_mode") or kwargs.get("mode") or ""
        sensitive_confirmed = kwargs.get("sensitive_confirmed", kwargs.get("confirmed", False))
        target_group = _single_line(group_id, 40)
        text = self._normalize_atrelay_text(message, limit=800)
        relay_mode_normalized = self._normalize_atrelay_relay_mode(relay_mode)
        if not target_group.isdigit():
            return "发送失败：群号格式不正确"
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
                "[PrivateCompanion] 跨群转述发送失败: group=%s at=%s error=%s",
                target_group,
                at_qq or at_user or "-",
                _single_line(error, 240),
            )
            return f"发送失败：{_single_line(error, 180)}"
        self._note_atrelay_send("group", target_group, text, at_qq or at_user, event=event)
        self._save_data_sync()
        logger.info(
            "[PrivateCompanion] 跨群转述发送完成: group=%s at=%s umo=%s",
            target_group,
            at_qq or at_user or "-",
            _single_line(used_umo, 160),
        )
        return f"消息已发送到群 {target_group}" + (f", 已 @ {at_label or at_qq}" if at_qq else "")

    async def _pc_send_to_private_user_impl(self, event: AstrMessageEvent, **kwargs) -> str:
        if not self.enable_atrelay_tools:
            return "发送失败：跨群转述工具未启用"
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
        target_user = _single_line(user_id, 40)
        text = self._normalize_atrelay_text(message, limit=800)
        relay_mode_normalized = self._normalize_atrelay_relay_mode(relay_mode)
        if not target_user.isdigit():
            return "发送失败：QQ 号格式不正确"
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
                "[PrivateCompanion] 私聊转述发送失败: user=%s error=%s",
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
        self._save_data_sync()
        logger.info(
            "[PrivateCompanion] 私聊转述发送完成: user=%s umo=%s",
            target_user,
            _single_line(used_umo, 160),
        )
        return f"已向 {target_user} 发送私聊消息" + ("，会等待对方回复后带回回执" if need_receipt else "")

    async def _pc_send_to_groups_impl(self, event: AstrMessageEvent, **kwargs) -> str:
        group_ids = kwargs.get("group_ids") or kwargs.get("groups") or kwargs.get("group_id") or kwargs.get("targets") or ""
        message = kwargs.get("message") or kwargs.get("text") or kwargs.get("content") or kwargs.get("msg") or ""
        at_user = kwargs.get("at_user") or kwargs.get("at") or kwargs.get("target_user") or kwargs.get("user_id") or ""
        relay_mode = kwargs.get("relay_mode") or kwargs.get("mode") or ""
        sensitive_confirmed = kwargs.get("sensitive_confirmed", kwargs.get("confirmed", False))
        targets = [item for item in self._parse_atrelay_target_list(group_ids, limit=self.atrelay_multi_target_limit) if item.isdigit()]
        if not targets:
            return "发送失败：没有有效群号"
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
        targets = [item for item in self._parse_atrelay_target_list(user_ids, limit=self.atrelay_multi_target_limit) if item.isdigit()]
        if not targets:
            return "发送失败：没有有效 QQ"
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
        group_id = kwargs.get("group_id") or kwargs.get("group") or kwargs.get("target_group") or ""
        at_user = kwargs.get("at_user") or kwargs.get("target_user") or kwargs.get("user_id") or kwargs.get("name") or kwargs.get("nickname") or ""
        message = kwargs.get("message") or kwargs.get("text") or kwargs.get("content") or kwargs.get("msg") or ""
        relay_mode = kwargs.get("relay_mode") or kwargs.get("mode") or ""
        sensitive_confirmed = kwargs.get("sensitive_confirmed", kwargs.get("confirmed", False))
        expire_hours = kwargs.get("expire_hours", kwargs.get("ttl_hours", 24))
        target_group = _single_line(group_id, 40) or self._extract_group_id_from_event(event)
        text = self._normalize_atrelay_text(message, limit=800)
        if not target_group.isdigit():
            return "挂起失败：群号格式不正确"
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
            self._save_data_sync()
        return f"已挂起：等 {target_name} 在群 {target_group} 出现时转述"
