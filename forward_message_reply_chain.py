# -*- coding: utf-8 -*-
"""ForwardMessageReplyChainMixin。

由 tools/split_mixin_domain.py 从 forward_message.py 机械抽取（6 个方法 + 0 个模块级名字 + 0 个类级赋值 / 239 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ForwardMessageMixin）。
"""
from __future__ import annotations

from .forward_message_shared import _render_conversation_section_labeled, logger
from astrbot.api.event import AstrMessageEvent
from typing import Any
from .forward_message_shared import PromptSection
from .forward_message_shared import _safe_int
from .forward_message_shared import _single_line
from .forward_message_shared import prompt_section



class ForwardMessageReplyChainMixin:
    """ForwardMessageReplyChainMixin（从 ForwardMessageMixin 拆出）。"""


    def _reply_message_author_role(self, event: AstrMessageEvent, sender_id: str) -> str:
        normalized_sender_id = _single_line(sender_id, 80)
        if not normalized_sender_id:
            return "unknown"
        self_id = ""
        self_id_getter = getattr(self, "_event_self_id", None)
        if callable(self_id_getter):
            try:
                self_id = _single_line(self_id_getter(event), 80)
            except Exception:
                self_id = ""
        if not self_id:
            try:
                self_id = _single_line(event.get_self_id(), 80)
            except Exception:
                self_id = ""
        current_sender_id = ""
        try:
            current_sender_id = _single_line(event.get_sender_id(), 80)
        except Exception:
            current_sender_id = ""
        if self_id and normalized_sender_id == self_id:
            return "bot_self"
        if current_sender_id and normalized_sender_id == current_sender_id:
            return "current_user"
        return "other"

    @staticmethod
    def _reply_message_author_label(role: str, sender_id: str, sender_name: str) -> str:
        role_label = {
            "bot_self": "Bot 自己",
            "current_user": "当前用户",
            "other": "其他人",
            "unknown": "未知（平台未返回发送者）",
        }.get(role, "未知（平台未返回发送者）")
        details: list[str] = []
        normalized_name = _single_line(sender_name, 60)
        normalized_id = _single_line(sender_id, 80)
        if normalized_name and normalized_name != normalized_id:
            details.append(f"显示名：{normalized_name}")
        if normalized_id:
            details.append(f"ID：{normalized_id}")
        return role_label + (f"（{'；'.join(details)}）" if details else "")

    async def _reply_message_chain_for_event(self, event: AstrMessageEvent, *, max_depth: int = 3) -> list[dict[str, Any]]:
        cached = getattr(event, "_private_companion_reply_message_chain", None)
        if isinstance(cached, list):
            return [item for item in cached if isinstance(item, dict)]

        direct_ids: list[str] = []
        for item in self._event_components(event):
            type_name = self._component_type_name(item)
            if type_name != "reply" and "reply" not in type_name:
                continue
            message_id = _single_line(self._extract_reply_message_id(item), 120)
            if message_id and message_id not in direct_ids:
                direct_ids.append(message_id)
        queue: list[tuple[str, int]] = [(message_id, 1) for message_id in direct_ids]
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        current_scope = _single_line(self._event_scope_key(event), 160)
        while queue and len(rows) < max(1, max_depth):
            message_id, depth = queue.pop(0)
            message_id = _single_line(message_id, 120)
            if not message_id or message_id in seen or depth > max_depth:
                continue
            seen.add(message_id)
            recalled_message_id = await self._should_cancel_reply_for_missing_or_recalled_trigger(event, message_id)
            if recalled_message_id:
                logger.info("引用链读取跳过: 被引用消息已撤回或不可见 message_id=%s", recalled_message_id)
                continue
            message_obj = await self._get_message_obj_by_id(event, message_id)
            if not message_obj:
                continue
            snapshot = message_obj.get("_private_companion_snapshot") if isinstance(message_obj, dict) else None
            if isinstance(snapshot, dict):
                snapshot_scope = _single_line(snapshot.get("scope"), 160)
                if current_scope and snapshot_scope and snapshot_scope != current_scope:
                    continue
            raw_message = self._raw_message_from_message_obj(message_obj)
            text = self._message_obj_text_preview(raw_message, limit=280)
            sender_id, sender_name = self._message_obj_sender_info(message_obj, snapshot=snapshot)
            author_role = self._reply_message_author_role(event, sender_id)
            media_types = self._message_obj_media_types(raw_message)
            voice_spoken_text = ""
            voice_source_text = ""
            if author_role == "bot_self" and "语音" in media_types:
                voice_spoken_text, voice_source_text = self._message_obj_known_tts_voice_text(
                    raw_message,
                    snapshot=snapshot,
                )
            rows.append(
                {
                    "message_id": message_id,
                    "depth": depth,
                    "raw_message": raw_message,
                    "text": text,
                    "sender_id": sender_id,
                    "sender_name": sender_name,
                    "author_role": author_role,
                    "media_types": media_types,
                    "voice_spoken_text": voice_spoken_text,
                    "voice_source_text": voice_source_text,
                }
            )
            next_ids = self._message_obj_reply_message_ids(raw_message)
            if not next_ids and isinstance(snapshot, dict) and isinstance(snapshot.get("reply_message_ids"), list):
                next_ids = [_single_line(item, 120) for item in snapshot.get("reply_message_ids") if _single_line(item, 120)]
            for next_id in next_ids:
                if next_id and next_id not in seen:
                    queue.append((next_id, depth + 1))

        try:
            setattr(event, "_private_companion_reply_message_chain", list(rows))
        except Exception:
            pass
        return rows

    async def _format_reply_chain_context_prompt_section(
        self,
        event: AstrMessageEvent,
    ) -> PromptSection | None:
        chain = await self._reply_message_chain_for_event(event, max_depth=3)
        if not chain:
            return None
        lines = [
            (
                "用户这轮回复/引用了一条消息；被引用消息本身还引用了更早的消息。下面按距离当前消息由近到远列出，请结合最深层原始消息和用户当前文字理解关系。"
                if len(chain) > 1
                else "用户这轮回复/引用了一条消息。下面的原消息作者和内容类型来自平台消息数据，请据此理解归属，不要根据语气猜测。"
            ),
        ]
        lines.extend(self._reply_actor_binding_prompt_lines())
        for row in chain:
            depth = _safe_int(row.get("depth"), 1, 1)
            message_id = _single_line(row.get("message_id"), 80)
            text = _single_line(row.get("text"), 280) or "[无可读文字]"
            label = "直接被引用" if depth == 1 else f"第 {depth} 层原始引用"
            author_role = _single_line(row.get("author_role"), 30) or "unknown"
            author = self._reply_message_author_label(
                author_role,
                _single_line(row.get("sender_id"), 80),
                _single_line(row.get("sender_name"), 60),
            )
            media_types = row.get("media_types") if isinstance(row.get("media_types"), list) else []
            media_label = "、".join(_single_line(item, 20) for item in media_types if _single_line(item, 20)) or "普通消息"
            lines.append(
                f"- {label}（消息ID：{message_id or '无'}；原消息发送者：{author}；内容类型：{media_label}）：{text}"
            )
            voice_spoken_text = _single_line(row.get("voice_spoken_text"), 500)
            voice_source_text = _single_line(row.get("voice_source_text"), 500)
            if author_role == "bot_self" and voice_spoken_text:
                lines.append(f"  - 插件生成语音实际朗读：{voice_spoken_text}")
                if voice_source_text and voice_source_text != voice_spoken_text:
                    lines.append(f"  - 生成语音对应原回复：{voice_source_text}")

        direct = next((row for row in chain if _safe_int(row.get("depth"), 1, 1) == 1), chain[0])
        direct_media = direct.get("media_types") if isinstance(direct.get("media_types"), list) else []
        if "语音" in direct_media:
            direct_role = _single_line(direct.get("author_role"), 30) or "unknown"
            if direct_role == "bot_self":
                direct_spoken = _single_line(direct.get("voice_spoken_text"), 500)
                if direct_spoken:
                    lines.append(
                        "语音归属锚点：这条被引用语音是你自己/Bot 此前发送的，且上方朗读文本是插件生成时保留的原始记录，可以直接据此理解语音内容。"
                        "当前用户只是引用或评价它；不要说成用户自己配的，也不要声称听不到或不知道语音说了什么。"
                    )
                else:
                    lines.append(
                        "语音归属锚点：这条被引用语音是你自己/Bot 此前发送的，当前用户只是引用或评价它。"
                        "不要把它说成当前用户自己配的、制作的或刚刚发送的；除非用户当前文字明确补充了其参与制作。"
                    )
            elif direct_role == "current_user":
                lines.append(
                    "语音归属锚点：这条被引用语音此前由当前用户发送；“发送者”本身不足以证明是用户亲自配音或制作，不要额外编造创作归属。"
                )
            elif direct_role == "other":
                lines.append(
                    "语音归属锚点：这条被引用语音此前由其他人发送，当前用户只是引用或评价它；不要把发送、配音或制作动作归给当前用户。"
                )
            else:
                lines.append(
                    "语音归属锚点：平台没有返回这条被引用语音的发送者，归属未知；不要默认说成当前用户发送、配音或制作。"
                )
        return prompt_section(
            key="reply.chain",
            title="引用链上下文",
            source="forward_message",
            content="\n".join(lines),
        )

    async def _format_reply_chain_context_for_prompt(
        self,
        event: AstrMessageEvent,
    ) -> str:
        return _render_conversation_section_labeled(
            await self._format_reply_chain_context_prompt_section(event)
        )

    async def _reply_raw_message_for_event(self, event: AstrMessageEvent) -> tuple[str, Any]:
        cached = getattr(event, "_private_companion_reply_raw_message", None)
        if cached is not None:
            cached_id = _single_line(getattr(event, "_private_companion_reply_raw_message_id", ""), 120)
            return cached_id, cached
        chain = await self._reply_message_chain_for_event(event, max_depth=3)
        if chain:
            row = chain[-1]
            message_id = _single_line(row.get("message_id"), 120)
            raw_message = row.get("raw_message")
            try:
                setattr(event, "_private_companion_reply_raw_message", raw_message)
                setattr(event, "_private_companion_reply_raw_message_id", message_id)
            except Exception:
                pass
            return message_id, raw_message
        for item in self._event_components(event):
            type_name = self._component_type_name(item)
            if type_name != "reply" and "reply" not in type_name:
                continue
            message_id = self._extract_reply_message_id(item)
            if not message_id:
                continue
            recalled_message_id = await self._should_cancel_reply_for_missing_or_recalled_trigger(event, message_id)
            if recalled_message_id:
                logger.info("引用原消息读取跳过: 被引用消息已撤回或不可见 message_id=%s", recalled_message_id)
                return message_id, None
            message_obj = None
            try:
                message_obj = await self._call_platform_action(event, "get_msg", message_id=int(message_id))
            except Exception:
                try:
                    message_obj = await self._call_platform_action(event, "get_msg", message_id=message_id)
                except Exception:
                    message_obj = None
            if not message_obj:
                continue
            raw_message = message_obj.get("message") if isinstance(message_obj, dict) else message_obj
            try:
                setattr(event, "_private_companion_reply_raw_message", raw_message)
                setattr(event, "_private_companion_reply_raw_message_id", message_id)
            except Exception:
                pass
            return message_id, raw_message
        return "", None
