# -*- coding: utf-8 -*-

from __future__ import annotations

import os
import re
from typing import Any

from astrbot.api import logger

from .helpers import _single_line, _strip_nonstandard_chat_control_tags


class TtsToolSanitizerMixin:
    """Handle TTS tags in send_message_to_user tool calls."""

    def _clean_tool_plain_text_tts_markup(self, raw_text: Any) -> str:
        text = str(raw_text or "")
        if not text:
            return ""
        cleaned_control = _strip_nonstandard_chat_control_tags(text)
        if cleaned_control != text:
            logger.info(
                "[PrivateCompanion] 已清理工具直发文本中的非标准控制标签: before=%s after=%s",
                _single_line(text, 120),
                _single_line(cleaned_control, 120),
            )
            text = cleaned_control
        if not re.search(r"</?(?:pc[_-]?tts|t{2,}s)\b", text, flags=re.IGNORECASE):
            return text
        try:
            normalizer = getattr(self, "_normalize_tts_tags", None)
            normalized = normalizer(text) if callable(normalizer) else text
            visible_getter = getattr(self, "_tts_visible_fallback_text", None)
            visible = visible_getter(normalized, "") if callable(visible_getter) else ""
        except Exception:
            normalized = text
            visible = ""
        if not visible:
            visible = re.sub(r"</?(?:pc[_-]?tts|t{2,}s)\b[^>]*>", "", normalized, flags=re.IGNORECASE).strip()
        visible = re.sub(r"\n{3,}", "\n\n", str(visible or "").strip())
        if visible and visible != text:
            logger.info(
                "[PrivateCompanion] 已清理工具直发文本中的 TTS 标签: before=%s after=%s",
                _single_line(text, 120),
                _single_line(visible, 120),
            )
        return visible

    def _clean_send_message_to_user_tool_messages(self, messages: Any) -> Any:
        if not isinstance(messages, list):
            return messages
        changed = False
        cleaned_messages: list[Any] = []
        for item in messages:
            if not isinstance(item, dict):
                cleaned_messages.append(item)
                continue
            copied = dict(item)
            if str(copied.get("type") or "").strip().lower() == "plain":
                cleaned_text = self._clean_tool_plain_text_tts_markup(copied.get("text"))
                if cleaned_text != copied.get("text"):
                    changed = True
                    copied["text"] = cleaned_text
            cleaned_messages.append(copied)
        return cleaned_messages if changed else messages

    async def _send_message_to_user_tool_with_tts_processing(
        self,
        tool_self: Any,
        context: Any,
        kwargs: dict[str, Any],
    ) -> Any:
        messages = kwargs.get("messages")
        if not isinstance(messages, list) or not messages:
            return None
        if not any(
            isinstance(item, dict)
            and (
                (
                    str(item.get("type") or "").strip().lower() == "plain"
                    and re.search(r"</?(?:pc[_-]?tts|t{2,}s)\b", str(item.get("text") or ""), flags=re.IGNORECASE)
                )
                or (
                    str(item.get("type") or "").strip().lower() == "record"
                    and not (item.get("path") or item.get("url"))
                    and str(item.get("text") or item.get("content") or item.get("message") or "").strip()
                )
            )
            for item in messages
        ):
            return None
        try:
            event = context.context.event
            current_session = str(getattr(event, "unified_msg_origin", "") or "")
        except Exception:
            event = None
            current_session = ""
        session = str(kwargs.get("session") or current_session or "")
        if not current_session or session != current_session:
            return None
        try:
            import astrbot.core.message.components as Comp
            from astrbot.core.message.message_event_result import MessageChain as CoreMessageChain
            from astrbot.core.platform.message_session import MessageSession
        except Exception as exc:
            logger.debug("[PrivateCompanion] send_message_to_user TTS 接管不可用: %s", _single_line(exc, 120))
            return None

        components: list[Any] = []
        for idx, msg in enumerate(messages):
            if not isinstance(msg, dict):
                return f"error: messages[{idx}] should be an object."
            msg_type = str(msg.get("type") or "").strip().lower()
            if not msg_type:
                return f"error: messages[{idx}].type is required."
            try:
                if msg_type == "plain":
                    text = str(msg.get("text") or "").strip()
                    if not text:
                        return f"error: messages[{idx}].text is required for plain component."
                    if re.search(r"</?(?:pc[_-]?tts|t{2,}s)\b", text, flags=re.IGNORECASE):
                        fallback_plain = self._clean_tool_plain_text_tts_markup(text)
                        processor = getattr(self, "_process_tts_tags", None)
                        tts_components = (
                            await processor(text, event, fallback_plain=fallback_plain)
                            if callable(processor) and bool(getattr(self, "enable_tts_enhancement", False))
                            else []
                        )
                        if tts_components:
                            components.extend(tts_components)
                        elif fallback_plain:
                            components.append(Comp.Plain(text=fallback_plain))
                    else:
                        components.append(Comp.Plain(text=text))
                elif msg_type == "image":
                    path = msg.get("path")
                    url = msg.get("url")
                    if path:
                        local_path, _ = await tool_self._resolve_path_from_sandbox(context, path, component_type="image")
                        components.append(Comp.Image.fromFileSystem(path=local_path))
                    elif url:
                        components.append(Comp.Image.fromURL(url=url))
                    else:
                        return f"error: messages[{idx}] must include path or url for image component."
                elif msg_type == "record":
                    path = msg.get("path")
                    url = msg.get("url")
                    if path:
                        local_path, _ = await tool_self._resolve_path_from_sandbox(context, path, component_type="record")
                        components.append(Comp.Record.fromFileSystem(path=local_path))
                    elif url:
                        components.append(Comp.Record.fromURL(url=url))
                    else:
                        text = str(msg.get("text") or msg.get("content") or msg.get("message") or "").strip()
                        if not text:
                            return f"error: messages[{idx}] must include path or url for record component."
                        processor = getattr(self, "_process_tts_tags", None)
                        tts_components = (
                            await processor(f"<pc_tts>{text}</pc_tts>", event, fallback_plain=text)
                            if callable(processor) and bool(getattr(self, "enable_tts_enhancement", False))
                            else []
                        )
                        if tts_components:
                            components.extend(tts_components)
                            logger.info(
                                "[PrivateCompanion] 已接管 send_message_to_user 的 record 文本并转为插件 TTS: session=%s text=%s",
                                _single_line(session, 120),
                                _single_line(text, 120),
                            )
                        else:
                            components.append(Comp.Plain(text=text))
                            logger.warning(
                                "[PrivateCompanion] send_message_to_user 的 record 文本无法生成语音,已改为普通文字: session=%s text=%s",
                                _single_line(session, 120),
                                _single_line(text, 120),
                            )
                elif msg_type == "video":
                    path = msg.get("path")
                    url = msg.get("url")
                    if path:
                        local_path, _ = await tool_self._resolve_path_from_sandbox(context, path, component_type="video")
                        components.append(Comp.Video.fromFileSystem(path=local_path))
                    elif url:
                        components.append(Comp.Video.fromURL(url=url))
                    else:
                        return f"error: messages[{idx}] must include path or url for video component."
                elif msg_type == "file":
                    path = msg.get("path")
                    url = msg.get("url")
                    name = (
                        msg.get("text")
                        or (os.path.basename(str(path)) if path else "")
                        or (os.path.basename(str(url)) if url else "")
                        or "file"
                    )
                    if path:
                        local_path, _ = await tool_self._resolve_path_from_sandbox(context, path, component_type="file")
                        components.append(Comp.File(name=name, file=local_path))
                    elif url:
                        components.append(Comp.File(name=name, url=url))
                    else:
                        return f"error: messages[{idx}] must include path or url for file component."
                elif msg_type == "mention_user":
                    mention_user_id = msg.get("mention_user_id")
                    if not mention_user_id:
                        return f"error: messages[{idx}].mention_user_id is required for mention_user component."
                    components.append(Comp.At(qq=mention_user_id))
                else:
                    return f"error: unsupported message type '{msg_type}' at index {idx}."
            except FileNotFoundError as exc:
                return f"error: {exc}"
            except PermissionError as exc:
                return f"error: {exc}"
            except Exception as exc:
                return f"error: failed to build messages[{idx}] component: {exc}"
        if not components:
            return "error: messages became empty after TTS processing."
        try:
            target_session = MessageSession.from_str(session)
        except Exception:
            return f"error: invalid session: {session}"
        await context.context.context.send_message(target_session, CoreMessageChain(chain=components))
        logger.info(
            "[PrivateCompanion] send_message_to_user 工具文本已接管 TTS 处理: session=%s components=%s",
            _single_line(session, 120),
            len(components),
        )
        return f"Message sent to session {target_session}"

    def _install_send_message_to_user_tool_sanitizer(self) -> None:
        try:
            from astrbot.core.tools.message_tools import SendMessageToUserTool
        except Exception as exc:
            logger.debug("[PrivateCompanion] send_message_to_user 工具清理包装未安装: %s", _single_line(exc, 120))
            return
        original_call = getattr(SendMessageToUserTool, "_private_companion_tts_sanitizer_original_call", None)
        if original_call is None:
            original_call = SendMessageToUserTool.call

        async def _private_companion_sanitized_call(tool_self, context, **kwargs):
            plugin = getattr(SendMessageToUserTool, "_private_companion_tts_sanitizer_plugin", None)
            if plugin is not None and bool(getattr(plugin, "enabled", False)) and isinstance(kwargs.get("messages"), list):
                try:
                    kwargs = dict(kwargs)
                    processed = await plugin._send_message_to_user_tool_with_tts_processing(tool_self, context, kwargs)
                    if processed is not None:
                        return processed
                    kwargs["messages"] = plugin._clean_send_message_to_user_tool_messages(kwargs.get("messages"))
                except Exception as exc:
                    logger.debug("[PrivateCompanion] send_message_to_user 文本清理失败: %s", _single_line(exc, 120))
                    try:
                        kwargs = dict(kwargs)
                        kwargs["messages"] = plugin._clean_send_message_to_user_tool_messages(kwargs.get("messages"))
                    except Exception:
                        pass
            return await original_call(tool_self, context, **kwargs)

        setattr(SendMessageToUserTool, "_private_companion_tts_sanitizer_original_call", original_call)
        setattr(SendMessageToUserTool, "_private_companion_tts_sanitizer_plugin", self)
        SendMessageToUserTool.call = _private_companion_sanitized_call
        setattr(SendMessageToUserTool, "_private_companion_tts_sanitizer_installed", True)
        logger.info("[PrivateCompanion] send_message_to_user 工具 TTS 标签处理已安装/刷新")
