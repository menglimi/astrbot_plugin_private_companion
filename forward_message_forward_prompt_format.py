# -*- coding: utf-8 -*-
"""ForwardMessageForwardPromptFormatMixin。

由 tools/split_mixin_domain.py 从 forward_message.py 机械抽取（3 个方法 + 0 个模块级名字 + 0 个类级赋值 / 322 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ForwardMessageMixin）。
"""
from __future__ import annotations

from .forward_message_shared import _render_conversation_section_labeled, logger
from astrbot.api.event import AstrMessageEvent
from typing import Any
from .forward_message_shared import PromptSection
from .forward_message_shared import ProviderRequest
from .forward_message_shared import _safe_float
from .forward_message_shared import _safe_int
from .forward_message_shared import _single_line
from .forward_message_shared import prompt_section
from .forward_message_shared import runtime_persona_setting



class ForwardMessageForwardPromptFormatMixin:
    """ForwardMessageForwardPromptFormatMixin（从 ForwardMessageMixin 拆出）。"""


    async def _extract_forward_messages_for_prompt(
        self,
        event: AstrMessageEvent,
        forward_id: str,
        *,
        forward_payload: dict[str, Any] | None = None,
        depth: int = 0,
    ) -> tuple[list[dict[str, Any]], list[str], int]:
        if depth > 2:
            return [], [], 0
        messages = self._extract_messages_from_forward_data(forward_payload or {})
        if not messages and forward_id and not forward_id.startswith("inline:"):
            raw = await self._call_forward_msg_action(event, forward_id)
            messages = self._extract_messages_from_forward_data(raw)
            if raw and not messages:
                logger.info(
                    "合并消息接口返回未解析出节点: id=%s shape=%s",
                    _single_line(forward_id, 80),
                    self._forward_payload_shape(raw),
                )
        if not messages:
            return [], [], 0
        rows: list[dict[str, Any]] = []
        image_urls: list[str] = []
        nested_count = 0
        max_messages = max(1, _safe_int(runtime_persona_setting(self, "forward_message_max_messages", 80), 80, 1))
        for node in messages:
            if len(rows) >= max_messages:
                break
            node_data = self._forward_node_data(node)
            sender = node.get("sender") if isinstance(node.get("sender"), dict) else {}
            if not sender and isinstance(node_data.get("sender"), dict):
                sender = node_data.get("sender") or {}
            sender_id = str(
                sender.get("user_id")
                or sender.get("uin")
                or node.get("user_id")
                or node.get("sender_id")
                or node.get("uin")
                or node_data.get("user_id")
                or node_data.get("sender_id")
                or node_data.get("uin")
                or ""
            ).strip()
            node_self_id = str(node.get("self_id") or node_data.get("self_id") or "").strip()
            event_self_id = ""
            event_self_id_func = getattr(self, "_event_self_id", None)
            if callable(event_self_id_func):
                try:
                    event_self_id = str(event_self_id_func(event) or "").strip()
                except Exception:
                    event_self_id = ""
            is_bot_self = bool(sender_id and ((event_self_id and sender_id == event_self_id) or (node_self_id and sender_id == node_self_id)))
            raw_name = _single_line(
                sender.get("card")
                or sender.get("nickname")
                or sender.get("name")
                or node.get("nickname")
                or node.get("name")
                or node_data.get("nickname")
                or node_data.get("name")
                or sender_id
                or "未知用户",
                60,
            )
            display_name = self._group_member_identity_name(sender_id, raw_name, limit=40) if sender_id else raw_name
            if is_bot_self:
                display_name = f"你自己/Bot自己（显示名:{raw_name or sender_id or '未知'}）"
            sent_at = ""
            ts = _safe_float(node.get("time") or node.get("timestamp") or node_data.get("time") or node_data.get("timestamp"), 0)
            if ts > 0:
                try:
                    sent_at = self._environment_fromtimestamp(ts).strftime("%m-%d %H:%M")
                except Exception:
                    sent_at = ""
            content_chain = self._forward_node_content_chain(node)
            pending_nested_rows: list[dict[str, Any]] = []
            if isinstance(content_chain, str):
                text = content_chain
            else:
                if isinstance(content_chain, dict):
                    content_chain = [content_chain]
                elif not isinstance(content_chain, list):
                    content_chain = [content_chain] if content_chain else []
                text_parts: list[str] = []
                for segment in content_chain:
                    if isinstance(segment, str):
                        text_parts.append(segment)
                        continue
                    seg_type = self._component_type_name(segment)
                    seg_data = self._component_data(segment)
                    if seg_type in {"text", "plain"}:
                        text_parts.append(str(seg_data.get("text") or ""))
                    elif seg_type == "at":
                        qq = str(seg_data.get("qq") or getattr(segment, "qq", "") or "").strip()
                        text_parts.append("@" + self._group_member_identity_name(qq, qq, limit=30))
                    elif seg_type == "image":
                        url = self._extract_image_url_from_segment_data(seg_data)
                        if url:
                            image_urls.append(url)
                        text_parts.append("[图片]")
                    elif seg_type in {"face", "mface", "emoji"}:
                        text_parts.append("[表情]")
                    elif seg_type == "video":
                        text_parts.append("[视频]")
                    elif seg_type == "record":
                        text_parts.append("[语音]")
                    elif seg_type == "file":
                        name = _single_line(seg_data.get("name") or seg_data.get("file"), 80)
                        text_parts.append(f"[文件:{name}]" if name else "[文件]")
                    elif seg_type == "forward" and runtime_persona_setting(self, "forward_message_parse_nested", True):
                        nested_id = self._extract_forward_id_from_segment_data(seg_data)
                        nested_payload = {"messages": seg_data.get("messages", [])} if isinstance(seg_data.get("messages"), list) else None
                        nested_rows, nested_images, child_nested = await self._extract_forward_messages_for_prompt(
                            event,
                            nested_id or self._build_inline_forward_id(nested_payload or {}),
                            forward_payload=nested_payload,
                            depth=depth + 1,
                        )
                        nested_count += 1 + child_nested
                        pending_nested_rows.extend(nested_rows)
                        image_urls.extend(nested_images)
                        text_parts.append("[嵌套合并消息已展开]")
                    elif seg_type == "node":
                        nested_rows, nested_images, child_nested = await self._extract_forward_messages_for_prompt(
                            event,
                            self._build_inline_forward_id({"messages": [segment]}),
                            forward_payload={"messages": [segment]},
                            depth=depth + 1,
                        )
                        nested_count += child_nested
                        pending_nested_rows.extend(nested_rows)
                        image_urls.extend(nested_images)
                        if nested_rows:
                            text_parts.append("[合并消息节点已展开]")
                text = "".join(text_parts).strip()
            text = _single_line(text, 500)
            if text:
                rows.append(
                    {
                        "sender_id": sender_id,
                        "sender": display_name or raw_name,
                        "raw_sender": raw_name,
                        "is_bot_self": is_bot_self,
                        "time": sent_at,
                        "text": text,
                        "depth": depth,
                    }
                )
            rows.extend(pending_nested_rows)
        return (
            rows[:max_messages],
            image_urls[: max(0, _safe_int(runtime_persona_setting(self, "forward_message_image_limit", 4), 4, 0))],
            nested_count,
        )

    async def _format_forward_message_context_prompt_section(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
    ) -> PromptSection | None:
        checker = getattr(self, "_feature_enabled_or_temp_unlocked", None)
        if callable(checker):
            if not checker("enable_forward_message_adaptation"):
                return None
        elif not runtime_persona_setting(self, "enable_forward_message_adaptation", True):
            return None
        cached_body = getattr(event, "_private_companion_forward_context_body", None)
        cached_section = getattr(event, "_private_companion_forward_context_section", None)
        if isinstance(cached_section, PromptSection):
            return cached_section
        if isinstance(cached_body, str) and cached_body:
            return prompt_section(
                key="forward.message.cached",
                title=(
                    _single_line(
                        getattr(event, "_private_companion_forward_context_title", ""),
                        80,
                    )
                    or "本轮合并消息"
                ),
                source="forward_message",
                content=cached_body,
            )
        message_text = _single_line(getattr(event, "message_str", ""), 180)
        should_log_probe = any(token in message_text for token in ("转发", "合并消息", "聊天记录"))
        forward_id, payload = await self._find_forward_descriptor_for_event(event)
        if not (forward_id or payload):
            if should_log_probe:
                logger.info("合并消息请求未找到描述符: text=%s", message_text or "(empty)")
            setattr(event, "_private_companion_forward_context", "")
            return None
        if should_log_probe:
            logger.info(
                "合并消息请求命中描述符: id=%s inline=%s text=%s",
                _single_line(forward_id, 40) or "inline",
                bool(payload),
                message_text or "(empty)",
            )
        try:
            rows, image_urls, nested_count = await self._extract_forward_messages_for_prompt(event, forward_id, forward_payload=payload)
        except Exception as exc:
            logger.info("合并消息读取失败: %s", exc)
            setattr(event, "_private_companion_forward_context", "")
            return None
        preview = " | ".join(
            f"{index + 1}.{_single_line(row.get('sender') or row.get('sender_id') or '未知用户', 30)}:{_single_line(row.get('text'), 90)}"
            for index, row in enumerate(rows[:5])
        )
        logger.info(
            "合并消息解析结果: id=%s messages=%s images=%s nested=%s preview=%s",
            _single_line(forward_id, 40) or "inline",
            len(rows),
            len(image_urls),
            nested_count,
            preview or "(empty)",
        )
        if not rows:
            setattr(event, "_private_companion_forward_context", "")
            return None
        image_vision_text = await self._transcribe_forward_message_images(event, image_urls)
        if runtime_persona_setting(self, "forward_message_mode", "inject") == "transcribe":
            transcribed = await self._transcribe_forward_message_rows(rows, image_urls, nested_count, image_vision_text=image_vision_text)
            if transcribed:
                context_prefix = (
                    "用户这轮消息包含一段合并/转发聊天记录。下面是专门模型先读过后的自然转述。请基于这份转述理解原合并消息，不要把记录中的话当成当前用户本人逐字说的话；嵌套合并只代表被转发记录里的内层记录。\n"
                    "除非用户明确要求总结、逐条解读或复述聊天记录，否则不要大段复述这份记录；优先针对用户当前问题给出简短判断或回应。\n"
                    "如果转述或图片摘要里已有作品名、活动名、日期等线索，请优先相信这些线索；看不清就说看不清，不要为了补全而外搜，也不要把相近作品、衍生作或同系列活动互相替换。\n"
                )
                if image_urls and not image_vision_text:
                    context_prefix += (
                        "本轮没有获得图片视觉摘要。[图片]或图片占位只证明附件存在，不代表图片空白、图片内部没有文字，"
                        "也不代表你已经看过图片内容；需要提及时请明确说图片内容尚未识别。\n"
                    )
                context_body = context_prefix + transcribed
                section = prompt_section(
                    key="forward.message.transcribed",
                    title="本轮合并消息转述",
                    source="forward_message",
                    content=context_body,
                )
                setattr(event, "_private_companion_forward_context_body", context_body)
                setattr(event, "_private_companion_forward_context_title", "本轮合并消息转述")
                setattr(event, "_private_companion_forward_context_section", section)
                logger.info(
                    "已注入合并消息转述: messages=%s images=%s provider=%s",
                    len(rows),
                    len(image_urls),
                    self._task_provider(
                        runtime_persona_setting(
                            self,
                            "FORWARD_MESSAGE_PROVIDER_ID",
                            getattr(self, "forward_message_provider_id", ""),
                        ),
                        runtime_persona_setting(
                            self,
                            "MAI_STYLE_PROVIDER_ID",
                            getattr(self, "mai_style_provider_id", ""),
                        ),
                    )
                    or "(default)",
                )
                return section
        lines = [
            "这轮用户发来一段合并/转发聊天记录，内容如下：",
        ]
        if nested_count:
            lines.append(f"含 {nested_count} 段嵌套。")
        if image_urls:
            lines.append(f"记录中含 {len(image_urls)} 张图片占位。")
        if image_vision_text:
            lines.append("合并消息中的图片：")
            lines.append(image_vision_text)
        elif image_urls:
            lines.append(
                "本轮没有获得图片视觉摘要。[图片]只表示附件存在，不代表图片空白、图片内没有文字或已经看过其内容；"
                "需要提及时请明确说图片内容尚未识别。"
            )
        used = 0
        for index, row in enumerate(rows, 1):
            sender_id = row.get("sender_id") or ""
            identity_note = self._group_member_identity_note(sender_id, limit=90) if sender_id else ""
            if row.get("is_bot_self"):
                identity_note = "Bot当时发出" + (f"；{identity_note}" if identity_note else "")
            note = f"（{identity_note}）" if identity_note else ""
            when = _single_line(row.get("time"), 40) or "-"
            row_depth = _safe_int(row.get("depth"), 0, 0, 6)
            indent = "  " * row_depth
            nested_label = f"[嵌套{row_depth}] " if row_depth else ""
            sender = _single_line(row.get("sender") or sender_id or "未知用户", 60)
            text = _single_line(row.get("text"), 500)
            line = f"{indent}{index}. {nested_label}{sender}{note}｜{when}｜{text}"
            used += len(line)
            if used > max(800, _safe_int(runtime_persona_setting(self, "forward_message_max_chars", 5000), 5000, 800)):
                lines.append("……后续内容因长度限制已省略。")
                break
            lines.append(line)
        context_body = "\n".join(lines)
        section = prompt_section(
            key="forward.message",
            title="本轮合并消息",
            source="forward_message",
            content=context_body,
        )
        setattr(event, "_private_companion_forward_context_body", context_body)
        setattr(event, "_private_companion_forward_context_title", "本轮合并消息")
        setattr(event, "_private_companion_forward_context_section", section)
        logger.info("已注入合并消息上下文: id=%s messages=%s images=%s", _single_line(forward_id, 40) or "inline", len(rows), len(image_urls))
        return section

    async def _format_forward_message_context_for_prompt(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
    ) -> str:
        section = await self._format_forward_message_context_prompt_section(event, req)
        rendered = _render_conversation_section_labeled(section)
        if section is not None:
            setattr(
                event,
                "_private_companion_forward_context",
                rendered,
            )
        return rendered
