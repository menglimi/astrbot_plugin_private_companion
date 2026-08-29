# -*- coding: utf-8 -*-

from __future__ import annotations

import os
import re
from typing import Any


from .helpers import (
    _redact_outbound_secrets,
    _single_line,
    _strip_nonstandard_chat_control_tags,
    _strip_outbound_control_blocks,
)
from .persona_config import runtime_persona_setting
from .segmented_message import sanitize_llm_segment_control_tokens
from .logging_util import get_module_logger

logger = get_module_logger(__name__)


class TtsToolSanitizerMixin:
    """Handle TTS tags in send_message_to_user tool calls."""

    @staticmethod
    def _event_requires_direct_same_session_tool_delivery(event: Any) -> bool:
        """Return whether tool text must be sent before the agent loop ends."""
        if event is None:
            return False

        event_type = type(event)
        type_name = str(getattr(event_type, "__name__", "") or "").strip().lower()
        type_module = str(getattr(event_type, "__module__", "") or "").strip().lower()
        if type_name == "cronmessageevent" or (
            type_name.endswith("cronmessageevent") and ".cron." in type_module
        ):
            return True

        platform_name = ""
        getter = getattr(event, "get_platform_name", None)
        if callable(getter):
            try:
                platform_name = str(getter() or "").strip().lower()
            except Exception:
                platform_name = ""
        if platform_name == "cron":
            return True

        platform_meta = getattr(event, "platform_meta", None) or getattr(event, "platform", None)
        if platform_meta is not None:
            meta_name = str(getattr(platform_meta, "name", "") or "").strip().lower()
            meta_description = str(getattr(platform_meta, "description", "") or "").strip().lower()
            if meta_name == "cron" or meta_description == "cronjob":
                return True

        # Compatibility fallback for AstrBot versions that do not expose the
        # synthetic cron platform marker to plugins.
        sender_name = ""
        sender_getter = getattr(event, "get_sender_name", None)
        if callable(sender_getter):
            try:
                sender_name = str(sender_getter() or "").strip().lower()
            except Exception:
                sender_name = ""
        if not sender_name:
            sender = getattr(getattr(event, "message_obj", None), "sender", None)
            sender_name = str(getattr(sender, "nickname", "") or "").strip().lower()
        return sender_name == "scheduler"

    @staticmethod
    def _tool_response_names_and_args(resp: Any) -> tuple[list[str], list[Any]]:
        names = getattr(resp, "tools_call_name", None)
        if isinstance(names, str):
            names = [names]
        elif not isinstance(names, (list, tuple)):
            names = []
        args = getattr(resp, "tools_call_args", None)
        if not isinstance(args, (list, tuple)):
            args = []
        return [str(item or "").strip() for item in names], list(args)

    def _same_session_tool_text(self, event: Any, kwargs: dict[str, Any]) -> str:
        messages = kwargs.get("messages")
        if not isinstance(messages, list) or not messages:
            return ""
        if not self._send_tool_targets_current_session(event, kwargs):
            return ""
        visible_parts: list[str] = []
        for item in messages:
            if not isinstance(item, dict):
                return ""
            msg_type = str(item.get("type") or "").strip().lower()
            if msg_type == "plain":
                text = self._clean_tool_plain_text_tts_markup(item.get("text"))
            elif msg_type == "record" and not (item.get("path") or item.get("url")):
                text = self._clean_tool_plain_text_tts_markup(
                    item.get("text") or item.get("content") or item.get("message")
                )
            else:
                return ""
            text = _redact_outbound_secrets(text, self).strip()
            if text:
                visible_parts.append(text)
        return "\n".join(visible_parts).strip()

    @staticmethod
    def _send_tool_targets_current_session(event: Any, kwargs: dict[str, Any]) -> bool:
        try:
            current_session = str(getattr(event, "unified_msg_origin", "") or "")
        except Exception:
            current_session = ""
        target_session = str(kwargs.get("session") or current_session or "")
        return bool(current_session and target_session == current_session)

    def _same_session_tool_has_only_empty_plain(self, event: Any, kwargs: dict[str, Any]) -> bool:
        if not self._send_tool_targets_current_session(event, kwargs):
            return False
        messages = kwargs.get("messages")
        if not isinstance(messages, list) or not messages:
            return False
        for item in messages:
            if not isinstance(item, dict):
                return False
            if str(item.get("type") or "").strip().lower() != "plain":
                return False
            text = self._clean_tool_plain_text_tts_markup(item.get("text"))
            if _redact_outbound_secrets(text, self).strip():
                return False
        return True

    def _prepare_same_session_send_tool_response(self, event: Any, resp: Any) -> tuple[bool, str]:
        if self._event_requires_direct_same_session_tool_delivery(event):
            return False, ""
        names, args = self._tool_response_names_and_args(resp)
        for index, name in enumerate(names):
            if name != "send_message_to_user":
                continue
            payload = args[index] if index < len(args) and isinstance(args[index], dict) else {}
            if not self._send_tool_targets_current_session(event, payload):
                continue
            text = self._same_session_tool_text(event, payload)
            if text:
                try:
                    setattr(event, "_private_companion_same_session_tool_pending", True)
                    setattr(event, "_private_companion_same_session_tool_text", text)
                except Exception:
                    pass
            return True, text
        return False, ""

    def _defer_same_session_send_tool(self, event: Any, kwargs: dict[str, Any]) -> str:
        if self._event_requires_direct_same_session_tool_delivery(event):
            return ""
        text = self._same_session_tool_text(event, kwargs)
        if not text:
            return ""
        try:
            setattr(event, "_private_companion_same_session_tool_pending", True)
            setattr(event, "_private_companion_same_session_tool_text", text)
        except Exception:
            pass
        logger.info(
            "同会话 send_message_to_user 文本延后到最终回复: session=%s text=%s",
            _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            _single_line(text, 160),
        )
        return text

    def _clean_tool_plain_text_tts_markup(self, raw_text: Any) -> str:
        text = str(raw_text or "")
        if not text:
            return ""
        # Tool payloads can bypass the normal decorating-result chain. Apply
        # the same outbound control cleanup here so internal sentinels (such
        # as the photo-delivery marker) are never sent as visible text.
        cleaned_outbound = sanitize_llm_segment_control_tokens(
            _strip_outbound_control_blocks(text)
        )
        if cleaned_outbound != text:
            logger.info(
                "已清理工具直发文本中的内部控制标记: before=%s after=%s",
                _single_line(text, 120),
                _single_line(cleaned_outbound, 120),
            )
            text = cleaned_outbound
        if not text:
            return ""
        cleaned_control = _strip_nonstandard_chat_control_tags(text)
        if cleaned_control != text:
            logger.info(
                "已清理工具直发文本中的非标准控制标签: before=%s after=%s",
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
                "已清理工具直发文本中的 TTS 标签: before=%s after=%s",
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
                cleaned_text = _redact_outbound_secrets(cleaned_text, self)
                if cleaned_text != copied.get("text"):
                    changed = True
                    copied["text"] = cleaned_text
            cleaned_messages.append(copied)
        return cleaned_messages if changed else messages

    async def _process_tool_plain_tts_components(
        self,
        text: str,
        event: Any,
        *,
        fallback_plain: str,
    ) -> list[Any]:
        if not bool(runtime_persona_setting(self, "enable_tts_enhancement", False)):
            return []
        if runtime_persona_setting(self, "tts_generation_mode", "fast_tag") == "postprocess":
            converter = getattr(self, "_maybe_convert_plain_reply_to_tts", None)
            return await converter(fallback_plain, event) if callable(converter) else []
        processor = getattr(self, "_process_tts_tags", None)
        if not callable(processor):
            return []
        normalized = text
        full_scope_fallback = ""
        scope_enforcer = getattr(self, "_enforce_full_tts_scope_markup", None)
        if callable(scope_enforcer):
            normalized, full_scope_fallback = scope_enforcer(
                text,
                source_text=fallback_plain,
            )
        return await processor(
            normalized,
            event,
            fallback_plain=full_scope_fallback or fallback_plain,
        )

    async def _send_message_to_user_tool_with_tts_processing(
        self,
        tool_self: Any,
        context: Any,
        kwargs: dict[str, Any],
    ) -> Any:
        messages = kwargs.get("messages")
        if not isinstance(messages, list) or not messages:
            return None
        try:
            event = context.context.event
        except Exception:
            event = None
        if event is not None:
            deferred_text = self._defer_same_session_send_tool(event, kwargs)
            if deferred_text:
                return "Current-session text delivery is deferred to the final assistant response; do not send it again."
            if (
                not self._event_requires_direct_same_session_tool_delivery(event)
                and self._same_session_tool_has_only_empty_plain(event, kwargs)
            ):
                logger.info(
                    "已忽略同会话 send_message_to_user 空文本，等待 Agent 输出最终回复: session=%s",
                    _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                )
                return (
                    "No visible message was sent because the current-session plain text was empty. "
                    "Reply once in the final assistant response and do not call this tool again."
                )
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
            logger.debug("send_message_to_user TTS 接管不可用: %s", _single_line(exc, 120))
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
                    text = _redact_outbound_secrets(msg.get("text"), self).strip()
                    if not text:
                        return f"error: messages[{idx}].text is required for plain component."
                    if re.search(r"</?(?:pc[_-]?tts|t{2,}s)\b", text, flags=re.IGNORECASE):
                        fallback_plain = self._clean_tool_plain_text_tts_markup(text)
                        tts_components = await self._process_tool_plain_tts_components(
                            text,
                            event,
                            fallback_plain=fallback_plain,
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
                        text = _redact_outbound_secrets(msg.get("text") or msg.get("content") or msg.get("message"), self).strip()
                        if not text:
                            return f"error: messages[{idx}] must include path or url for record component."
                        processor = getattr(self, "_process_tts_tags", None)
                        tts_components = (
                            await processor(f"<pc_tts>{text}</pc_tts>", event, fallback_plain=text)
                            if callable(processor)
                            and bool(runtime_persona_setting(self, "enable_tts_enhancement", False))
                            else []
                        )
                        if tts_components:
                            components.extend(tts_components)
                            logger.info(
                                "已接管 send_message_to_user 的 record 文本并转为插件 TTS: session=%s text=%s",
                                _single_line(session, 120),
                                _single_line(text, 120),
                            )
                        else:
                            components.append(Comp.Plain(text=text))
                            logger.warning(
                                "send_message_to_user 的 record 文本无法生成语音,已改为普通文字: session=%s text=%s",
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
            "send_message_to_user 工具文本已接管 TTS 处理: session=%s components=%s",
            _single_line(session, 120),
            len(components),
        )
        return f"Message sent to session {target_session}"

    def _install_send_message_to_user_tool_sanitizer(self) -> None:
        try:
            from astrbot.core.tools.message_tools import SendMessageToUserTool
        except Exception as exc:
            logger.debug("send_message_to_user 工具清理包装未安装: %s", _single_line(exc, 120))
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
                    logger.debug("send_message_to_user 文本清理失败: %s", _single_line(exc, 120))
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
        logger.info("send_message_to_user 工具 TTS 标签处理已安装/刷新")
