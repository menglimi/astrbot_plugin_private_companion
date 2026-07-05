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


class InteractionUtilsMixin:
    """Common command permission and reply helpers."""

    def _help_text(self) -> str:
        return (
            "我会永远陪着你 命令：\n"
            "陪伴 状态\n"
            "陪伴 查看主动判定\n"
            "陪伴 撤回消息\n"
            "陪伴 重置插件\n"
            "陪伴 增添状态 <状态描述>[|持续小时]\n"
            "陪伴 查看今日日程\n"
            "陪伴 重置日程\n"
            "陪伴 当前细化\n"
            "陪伴 重置细化\n"
            "陪伴 今日穿搭图\n"
            "陪伴 生图 <画面描述>\n"
            "陪伴 自拍 [画面要求]\n"
            "陪伴 改图 <修改要求>（带图或回复图片）\n"
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
            "提示：私聊陪伴默认开启；配置页 target_user_ids 里的 QQ 会自动预热主动消息。"
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
            for item in parts:
                value = str(item or "").strip()
                if value and value.isdigit():
                    ids.add(value)
        return ids

    def _is_plugin_manager_user_id(self, user_id: str) -> bool:
        user_id = str(user_id or "").strip()
        if not user_id:
            return False
        if user_id in set(self._configured_target_ids()):
            return True
        return user_id in self._configured_admin_ids()

    def _is_group_admin_event(self, event: AstrMessageEvent) -> bool:
        message_obj = getattr(event, "message_obj", None)
        sender = getattr(message_obj, "sender", None) if message_obj is not None else None
        raw_role = ""
        for attr in ("role", "user_role", "group_role"):
            value = getattr(sender, attr, None) if sender is not None else None
            if value:
                raw_role = str(value)
                break
        raw_role = _single_line(raw_role, 20).lower()
        return raw_role in {"owner", "admin", "群主", "管理员"}

    def _can_manage_private_companion(self, event: AstrMessageEvent) -> bool:
        try:
            user_id = str(event.get_sender_id())
        except Exception:
            user_id = ""
        return self._is_plugin_manager_user_id(user_id)

    def _can_manage_group_companion(self, event: AstrMessageEvent) -> bool:
        try:
            user_id = str(event.get_sender_id())
        except Exception:
            user_id = ""
        return self._is_plugin_manager_user_id(user_id) or self._is_group_admin_event(event)

    def _management_denied_text(self) -> str:
        return (
            "这个操作需要管理权限。\n"
            "管理权限来源：\n"
            "1. Bot 管理员（AstrBot 全局配置 admins_id）\n"
            "2. 插件目标用户（拓展页-配置-快速启动-私聊目标 QQ）\n"
            "3. 群管理员（自动识别）\n"
            "请检查以上配置后重试。"
        )

    async def _reply(self, event: AstrMessageEvent, text: str, *, quote_current: bool = True):
        recalled_message_id = await self._should_cancel_reply_for_missing_or_recalled_trigger(event)
        if recalled_message_id:
            return
        quote_message_id = self._group_current_reply_quote_message_id(event, text_or_chain=text) if quote_current else ""
        if quote_message_id and text:
            await event.send(event.chain_result(self._with_optional_reply([Plain(text)], quote_message_id, event=event)))
            return
        await event.send(event.plain_result(text))

    async def _reply_with_optional_media(
        self,
        event: AstrMessageEvent,
        text: str,
        image_path: str = "",
        extra_components: list[Any] | None = None,
        quote_message_id: str = "",
    ):
        quote_message_id = _single_line(quote_message_id, 120) if getattr(self, "enable_proactive_quote_trigger_message", False) else ""
        if quote_message_id and self._quote_skip_reason_for_short_reply(text):
            quote_message_id = ""
        recalled_message_id = await self._should_cancel_reply_for_missing_or_recalled_trigger(event, quote_message_id)
        if recalled_message_id:
            return
        if (image_path and os.path.exists(image_path)) or extra_components:
            await event.send(
                event.chain_result(
                    self._with_optional_reply(
                        self._build_outbound_chain(text, image_path, extra_components=extra_components),
                        quote_message_id,
                        event=event,
                    )
                )
            )
            return
        if quote_message_id and text:
            await event.send(event.chain_result(self._with_optional_reply([Plain(text)], quote_message_id, event=event)))
            return
        if not text:
            return
        await event.send(event.plain_result(text))
