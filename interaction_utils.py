# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import re
from typing import Any

from astrbot.api.event import AstrMessageEvent
try:
    from astrbot.api.message_components import Plain
except ImportError:
    from astrbot.api.message_components import Plain

from .helpers import _single_line
from .persona_config import runtime_persona_setting


class InteractionUtilsMixin:
    """Common command permission and reply helpers."""

    @staticmethod
    def _normalize_companion_command_action(action: Any, value: Any) -> tuple[str, str]:
        verb = str(action or "").strip()
        remainder = str(value or "").strip()
        if not verb or not remainder:
            return verb, remainder
        aliases = {
            ("重置", "插件"): "重置插件",
            ("重置", "当前人格"): "重置当前人格",
            ("重置", "人格"): "重置当前人格",
            ("重置", "日程"): "重置日程",
            ("刷新", "日程"): "刷新日程",
            ("生成", "日程"): "生成日程",
            ("重新生成", "日程"): "重新生成日程",
            ("删除", "日程"): "删除日程",
            ("取消", "日程"): "取消日程",
            ("移除", "日程"): "移除日程",
            ("重置", "细化"): "重置细化",
            ("重置", "穿搭图"): "重置穿搭图",
            ("刷新", "穿搭图"): "刷新穿搭图",
            ("生成", "穿搭图"): "生成穿搭图",
            ("重新生成", "穿搭图"): "重新生成穿搭图",
            ("重置", "穿搭"): "重置穿搭",
            ("刷新", "穿搭"): "刷新穿搭",
            ("生成", "穿搭"): "生成穿搭",
            ("重新生成", "穿搭"): "重新生成穿搭",
            ("重置", "夹层密码"): "重置夹层密码",
            ("重置", "资料柜密码"): "重置资料柜密码",
            ("重置", "抽屉密码"): "重置资料柜密码",
            ("删除", "重要日期"): "重要日期删除",
            ("删除", "日期"): "日期删除",
            ("添加", "重要日期"): "重要日期添加",
            ("添加", "日期"): "日期添加",
            ("删除", "未完话头"): "删除未完话头",
            ("删除", "话头"): "删除话头",
            ("绑定", "城市"): "绑定城市",
            ("设置", "城市"): "绑定城市",
            ("查看", "城市"): "查看城市",
            ("解绑", "城市"): "解绑城市",
            ("清除", "城市"): "解绑城市",
            ("绑定", "主动消息"): "绑定主动消息",
            ("绑定", "主动会话"): "绑定主动消息",
            ("查看", "主动路由"): "查看主动路由",
            ("查看", "主动绑定"): "查看主动路由",
            ("解绑", "主动消息"): "解绑主动消息",
            ("解绑", "主动会话"): "解绑主动消息",
        }
        targets = sorted(
            (target for alias_verb, target in aliases if alias_verb == verb),
            key=len,
            reverse=True,
        )
        for target in targets:
            if not remainder.startswith(target):
                continue
            tail = remainder[len(target) :].strip()
            return aliases[(verb, target)], tail
        return verb, remainder

    def _help_text(self) -> str:
        return (
            "我会永远陪着你 命令：\n"
            "陪伴 状态\n"
            "陪伴 查看主动判定\n"
            "陪伴 绑定主动消息 / 查看主动路由 / 解绑主动消息\n"
            "陪伴 撤回消息\n"
            "陪伴 重置当前人格\n"
            "陪伴 重置插件\n"
            "陪伴 增添状态 <状态描述>[|持续小时]\n"
            "陪伴 查看今日日程\n"
            "陪伴 重置日程\n"
            "陪伴 重置日程 <时间|活动名>\n"
            "陪伴 删除日程 <时间|活动名>\n"
            "陪伴 当前细化\n"
            "陪伴 重置细化\n"
            "陪伴 今日穿搭图\n"
            "陪伴 生图 <画面描述>\n"
            "陪伴 自拍 [画面要求]\n"
            "陪伴 改图 <修改要求>（带图或回复图片）\n"
            "陪伴 查看生图API / 切换生图API\n"
            "陪伴 绑定城市 <城市|区县,城市|LocationID>\n"
            "陪伴 查看城市 / 解绑城市\n"
            "陪伴 能力列表\n"
            "陪伴 答疑 <问题>\n"
            "陪伴 答疑确认 / 答疑取消 / 答疑设置 <配置项|中文名> <值>\n"
            "陪伴 TTS语种 日语|中文|英语|默认\n"
            "陪伴 参考图 <本地图片路径|图片URL|清空>（也可带图/回复图片）\n"
            "陪伴 查看提示词 日程|细化|主动|回复注入\n"
            "陪伴 生成状态\n"
            "陪伴 梦境\n"
            "陪伴 梦境碎片\n"
            "陪伴 画像\n"
            "陪伴 记忆\n"
            "陪伴 表达学习\n"
            "陪伴 气氛\n"
            "陪伴 片段\n"
            "陪伴 删除话头 <关键词|全部>\n"
            "陪伴 长期记忆\n"
            "陪伴 日记\n"
            "陪伴 发说说 <正文>\n"
            "陪伴 重置夹层密码\n"
            "陪伴 输出夹层密码\n"
            "陪伴 强制输出 夹层密码\n"
            "陪伴 生成日记\n"
            "陪伴 日期列表\n"
            "陪伴 日期添加 <标题> <YYYY-MM-DD或MM-DD> [备注]\n"
            "陪伴 日期删除 <标题关键词>\n"
            "陪伴 可做事项\n"
            "陪伴 昵称 <称呼>\n"
            "陪伴 语气 <简短语气描述>\n"
            "陪伴 清空记忆\n"
            "提示：首次使用请在接收主动消息的私聊窗口执行“陪伴 绑定主动消息”，再到陪伴面板完成配置引导。"
        )

    def _private_only_text(self) -> str:
        return "为了避免误打扰,陪伴功能需要在私聊里管理。"

    def _configured_admin_ids(self) -> set[str]:
        ids: set[str] = set()
        configs: list[Any] = []
        context = getattr(self, "context", None)
        get_config = getattr(context, "get_config", None)
        if callable(get_config):
            for args in ((), ("default",)):
                try:
                    configs.append(get_config(*args))
                except Exception:
                    continue
        config = getattr(self, "config", None)
        if config is not None:
            configs.append(config)
        for cfg in configs:
            raw = None
            if isinstance(cfg, dict):
                raw = cfg.get("admins_id") or cfg.get("admins") or cfg.get("admin_ids")
            else:
                raw = getattr(cfg, "admins_id", None) or getattr(cfg, "admins", None) or getattr(cfg, "admin_ids", None)
            if isinstance(raw, str):
                parts = re.split(r"[\s,，、;；]+", raw)
            elif isinstance(raw, list):
                parts = raw
            else:
                parts = []
            normalizer = getattr(self, "_normalize_private_identity_id", None)
            for item in parts:
                value = normalizer(item) if callable(normalizer) else _single_line(item, 128)
                if value:
                    ids.add(value)
        return ids

    def _is_plugin_manager_user_id(self, user_id: str) -> bool:
        permission_id = self._permission_identity_id(user_id)
        if not permission_id:
            return False
        if self._is_private_companion_owner_user_id(permission_id):
            return True
        return permission_id in self._configured_admin_ids()

    def _permission_identity_id(self, user_id: Any) -> str:
        """Return the raw sender identity used for authorization.

        Private-user aliases intentionally merge conversation data, but they must
        never turn a different sender into an owner or administrator.
        """
        raw = _single_line(user_id, 160)
        normalizer = getattr(self, "_normalize_private_identity_id", None)
        if callable(normalizer):
            normalized = normalizer(raw)
            if normalized:
                return normalized
        users = self.data.get("users", {}) if isinstance(getattr(self, "data", None), dict) else {}
        user = users.get(raw) if isinstance(users, dict) else None
        if (
            raw
            and isinstance(user, dict)
            and _single_line(user.get("identity_subject_id"), 128)
            and _single_line(user.get("identity_platform_kind"), 40)
        ):
            return raw
        return ""

    def _event_permission_identity_id(self, event: AstrMessageEvent | None) -> str:
        if event is None:
            return ""
        try:
            raw_user_id = event.get_sender_id()
        except Exception:
            return ""
        resolver = getattr(self, "_private_user_id_for_event", None)
        if callable(resolver):
            try:
                resolved = _single_line(resolver(event, raw_user_id), 160)
            except Exception:
                resolved = ""
            if resolved:
                return resolved
        return self._permission_identity_id(raw_user_id)

    def _relationship_owner_user_ids(self) -> set[str]:
        users = self.data.get("users", {}) if isinstance(getattr(self, "data", None), dict) else {}
        if not isinstance(users, dict):
            return set()
        normalizer = getattr(self, "_normalize_private_user_role", None)
        owner_ids: set[str] = set()
        for raw_user_id, user in users.items():
            if not isinstance(user, dict):
                continue
            role = normalizer(user.get("relationship_role")) if callable(normalizer) else str(user.get("relationship_role") or "").strip().lower()
            permission_id = self._permission_identity_id(raw_user_id)
            if role == "owner" and permission_id:
                owner_ids.add(permission_id)
        return owner_ids

    def _is_private_companion_owner_user_id(self, user_id: Any) -> bool:
        permission_id = self._permission_identity_id(user_id)
        if not permission_id:
            return False
        if permission_id in set(self._configured_target_ids()):
            return True
        return permission_id in self._relationship_owner_user_ids()

    def _is_configured_admin_user_id(self, user_id: Any) -> bool:
        permission_id = self._permission_identity_id(user_id)
        if not permission_id:
            return False
        return permission_id in self._configured_admin_ids()

    def _is_group_admin_event(self, event: AstrMessageEvent) -> bool:
        role_getter = getattr(self, "_group_sender_role_from_event", None)
        raw_role = role_getter(event) if callable(role_getter) else "unknown"
        if raw_role in {"owner", "admin"}:
            return True
        group_id_getter = getattr(self, "_extract_group_id_from_event", None)
        summary_getter = getattr(self, "_group_role_snapshot_summary", None)
        try:
            group_id = _single_line(group_id_getter(event), 80) if callable(group_id_getter) else ""
            sender_id = _single_line(event.get_sender_id(), 128)
        except Exception:
            return False
        groups = self.data.get("groups") if isinstance(getattr(self, "data", None), dict) else {}
        group = groups.get(group_id) if isinstance(groups, dict) else None
        if not isinstance(group, dict) or not callable(summary_getter):
            return False
        summary = summary_getter(group)
        if not isinstance(summary, dict) or bool(summary.get("stale")):
            return False
        owner = summary.get("owner") if isinstance(summary.get("owner"), dict) else {}
        if sender_id and _single_line(owner.get("user_id"), 128) == sender_id:
            return True
        return any(
            isinstance(item, dict) and _single_line(item.get("user_id"), 128) == sender_id
            for item in (summary.get("admins") if isinstance(summary.get("admins"), list) else [])
        )

    def _can_manage_private_companion(self, event: AstrMessageEvent) -> bool:
        user_id = self._event_permission_identity_id(event)
        return self._is_plugin_manager_user_id(user_id)

    def _can_manage_sensitive_location(self, event: AstrMessageEvent) -> bool:
        """Keep the shared weather location inside its owner's private chat."""

        try:
            if not bool(getattr(event, "is_private_chat", lambda: False)()):
                return False
        except Exception:
            return False
        user_id = self._event_permission_identity_id(event)
        return bool(user_id and self._is_private_companion_owner_user_id(user_id))

    @staticmethod
    def _sensitive_location_denied_text() -> str:
        # Do not reveal whether a location is currently configured.
        return "城市设置只允许主要用户本人在自己的私聊中管理。"

    def _can_manage_group_companion(self, event: AstrMessageEvent) -> bool:
        user_id = self._event_permission_identity_id(event)
        return self._is_plugin_manager_user_id(user_id) or self._is_group_admin_event(event)

    def _management_denied_text(self) -> str:
        return (
            "这个操作需要管理权限。\n"
            "私聊里会识别三类用户 ID：AstrBot 全局管理员 admins_id、本插件私聊目标用户，或私聊页中关系角色设为主要用户的用户。\n"
            "OneBot/aiocqhttp 通常填 QQ 号；QQ 官方机器人请填日志或私聊页显示的 openid/平台用户 ID。\n"
            "优先直接填写用户 ID；误粘贴私聊 UMO 时会尝试提取 FriendMessage 后面的用户 ID。身份别名只用于归并记忆，不授予管理或跨用户查询权限。不要填写 UID、default、平台名或群聊会话串。"
        )

    async def _reply(self, event: AstrMessageEvent, text: str, *, quote_current: bool = True) -> bool:
        recalled_message_id = await self._should_cancel_reply_for_missing_or_recalled_trigger(event)
        if recalled_message_id:
            return False
        quote_message_id = self._group_current_reply_quote_message_id(event, text_or_chain=text) if quote_current else ""
        if quote_message_id and text:
            await event.send(event.chain_result(self._with_optional_reply([Plain(text)], quote_message_id, event=event)))
            return True
        await event.send(event.plain_result(text))
        return True

    async def _reply_with_optional_media(
        self,
        event: AstrMessageEvent,
        text: str,
        image_path: str = "",
        extra_components: list[Any] | None = None,
        quote_message_id: str = "",
    ) -> bool:
        quote_message_id = (
            _single_line(quote_message_id, 120)
            if runtime_persona_setting(self, "enable_proactive_quote_trigger_message", False)
            else ""
        )
        if quote_message_id and self._quote_skip_reason_for_short_reply(text):
            quote_message_id = ""
        recalled_message_id = await self._should_cancel_reply_for_missing_or_recalled_trigger(event, quote_message_id)
        if recalled_message_id:
            return False
        if (image_path and os.path.exists(image_path)) or extra_components:
            if image_path and os.path.exists(image_path):
                marker = getattr(self, "_mark_private_companion_skip_reaction_expression", None)
                if callable(marker):
                    marker(event)
            await event.send(
                event.chain_result(
                    self._with_optional_reply(
                        self._build_outbound_chain(text, image_path, extra_components=extra_components),
                        quote_message_id,
                        event=event,
                    )
                )
            )
            return True
        if quote_message_id and text:
            await event.send(event.chain_result(self._with_optional_reply([Plain(text)], quote_message_id, event=event)))
            return True
        if not text:
            return False
        await event.send(event.plain_result(text))
        return True
