# -*- coding: utf-8 -*-
"""FinalResponsePersistencePart01Mixin。

由 tools/split_mixin_domain.py 从 final_response_persistence.py 机械抽取（22 个方法 + 0 个模块级名字 + 0 个类级赋值 / 453 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 FinalResponsePersistenceMixin）。
"""
from __future__ import annotations

import asyncio
import json
from .final_response_persistence_shared import logger
from .helpers import (
    _format_history_media_marker,
    _has_history_media_marker,
    _single_line,
    _strip_outbound_control_blocks,
)
from .persona_config import runtime_persona_setting
from .segmented_message import sanitize_llm_segment_control_tokens
from astrbot.api.event import AstrMessageEvent
from astrbot.api.message_components import Image, Plain, Record
from astrbot.core.agent.message import AssistantMessageSegment, TextPart
from astrbot.core.star.star import star_map
from astrbot.core.star.star_handler import EventType, star_handlers_registry
from typing import TYPE_CHECKING, Any
from .final_response_persistence_shared import _final_response_persistence_host

if TYPE_CHECKING:
    from .final_response_persistence import FinalResponsePersistenceCoordinator
else:
    # Bound lazily on first attribute access so module import stays cycle-free.
    def __getattr__(name: str):
        if name == "FinalResponsePersistenceCoordinator":
            return _final_response_persistence_host.FinalResponsePersistenceCoordinator
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")




class FinalResponsePersistencePart01Mixin:
    """FinalResponsePersistencePart01Mixin（从 FinalResponsePersistenceMixin 拆出）。"""


    def _final_response_persistence_coordinator(
        self,
    ) -> Any:  # FinalResponsePersistenceCoordinator (host module, lazy-bound)
        coordinator_cls = _final_response_persistence_host.FinalResponsePersistenceCoordinator
        coordinator = getattr(self, "_final_response_persistence", None)
        if not isinstance(coordinator, coordinator_cls):
            coordinator = coordinator_cls(self)
            self._final_response_persistence = coordinator
        return coordinator

    def _begin_final_response_persistence(self, event: AstrMessageEvent) -> None:
        self._final_response_persistence_coordinator().begin_passive(event)
        self._capture_final_outbound_delivery(event)
        self._defer_livingmemory_response_capture(event)

    async def _prepare_final_response_after_agent(
        self,
        event: AstrMessageEvent,
        run_context: Any,
        response: Any,
    ) -> None:
        if not bool(getattr(event, "_private_companion_persistence_managed", False)):
            return
        try:
            # Keep the live run context available to image-history enrichment.
            # AstrBot serializes it only after the after-message-sent hooks, so
            # mutating the user turn here prevents a later core save from
            # overwriting the vision marker.
            setattr(event, "_private_companion_run_context", run_context)
            self._prepare_final_response_persistence(event, run_context, response)
            image_history_writer = getattr(
                self,
                "_persist_private_image_vision_summary_to_history",
                None,
            )
            if callable(image_history_writer):
                try:
                    await image_history_writer(event)
                except Exception as exc:
                    logger.debug(
                        "Agent 完成阶段写入图片视觉摘要失败: %s",
                        _single_line(exc, 160),
                    )
            self._final_response_persistence_coordinator().mark_final_response_ready(
                event
            )
        finally:
            self._restore_livingmemory_response_capture(event)

    def _capture_final_outbound_delivery(self, event: AstrMessageEvent) -> None:
        self._final_response_persistence_coordinator().install_send_tracking(event)

    async def _persist_final_outbound_delivery(self, event: AstrMessageEvent) -> bool:
        return await self._final_response_persistence_coordinator().finalize_passive(
            event
        )

    def _track_final_response_background_task(
        self,
        task: asyncio.Task | None,
        label: str,
    ) -> None:
        self._final_response_persistence_coordinator().track_background_task(
            task,
            label,
        )

    def _confirm_outbound_delivery(
        self,
        umo: str,
        chain: list[Any] | tuple[Any, ...],
    ) -> None:
        self._final_response_persistence_coordinator().confirm(umo, chain)

    @staticmethod
    def _actual_text_from_delivered_chain(
        chain: list[Any] | tuple[Any, ...],
    ) -> str:
        text_parts: list[str] = []
        voice_parts: list[str] = []
        for component in list(chain or []):
            if isinstance(component, Plain):
                text = str(getattr(component, "text", "") or "")
                if text.strip():
                    text_parts.append(text)
                continue
            if isinstance(component, Record):
                source_text = str(
                    getattr(component, "_private_companion_tts_source_text", "")
                    or getattr(component, "_private_companion_tts_spoken_text", "")
                    or ""
                ).strip()
                if source_text:
                    voice_parts.append(source_text)
        return "".join(text_parts or voice_parts).strip()

    @staticmethod
    def _is_livingmemory_handler_module(module_path: Any) -> bool:
        normalized = str(module_path or "").strip().lower().replace("-", "_")
        return "astrbot_plugin_livingmemory" in normalized

    @staticmethod
    def _is_memory_companion_handler_module(module_path: Any) -> bool:
        normalized = str(module_path or "").strip().lower().replace("-", "_")
        return any(
            plugin_id in normalized
            for plugin_id in (
                "astrbot_plugin_memory_companion",
                "astrbot_plugin_remember_you",
            )
        )

    def _livingmemory_response_handlers(
        self,
        *,
        plugins_name: list[str] | None = None,
    ) -> list[Any]:
        if not bool(getattr(self, "enable_livingmemory_integration", False)):
            return []
        try:
            handlers = star_handlers_registry.get_handlers_by_event_type(
                EventType.OnLLMResponseEvent,
                plugins_name=plugins_name,
            )
        except Exception:
            return []
        return [
            handler
            for handler in handlers
            if self._is_livingmemory_handler_module(
                getattr(handler, "handler_module_path", "")
            )
        ]

    def _memory_companion_response_handlers(
        self,
        *,
        plugins_name: list[str] | None = None,
    ) -> list[Any]:
        if not bool(getattr(self, "enable_livingmemory_integration", False)):
            return []
        bridge_getter = getattr(self, "_memory_companion_bridge", None)
        try:
            bridge = bridge_getter() if callable(bridge_getter) else None
        except Exception:
            return []
        if not callable(getattr(bridge, "record_visible_turn", None)):
            return []
        try:
            handlers = star_handlers_registry.get_handlers_by_event_type(
                EventType.OnLLMResponseEvent,
                plugins_name=plugins_name,
            )
        except Exception:
            return []
        return [
            handler
            for handler in handlers
            if self._is_memory_companion_handler_module(
                getattr(handler, "handler_module_path", "")
            )
        ]

    @staticmethod
    def _handler_plugin_name(handler: Any) -> str:
        plugin = star_map.get(str(getattr(handler, "handler_module_path", "") or ""))
        return str(getattr(plugin, "name", "") or "").strip()

    def _defer_livingmemory_response_capture(self, event: AstrMessageEvent) -> bool:
        """Keep recall enabled while postponing raw assistant writes."""
        if event is None or bool(
            getattr(event, "_private_companion_final_memory_dispatch", False)
        ):
            return False
        if bool(getattr(event, "_private_companion_livingmemory_deferred", False)):
            return True

        original_plugins = getattr(event, "plugins_name", None)
        livingmemory_handlers = self._livingmemory_response_handlers(
            plugins_name=original_plugins
        )
        memory_companion_handlers = self._memory_companion_response_handlers(
            plugins_name=original_plugins
        )
        livingmemory_names = {
            name
            for name in (
                self._handler_plugin_name(handler)
                for handler in livingmemory_handlers
            )
            if name
        }
        memory_companion_names = {
            name
            for name in (
                self._handler_plugin_name(handler)
                for handler in memory_companion_handlers
            )
            if name
        }
        managed_names = livingmemory_names | memory_companion_names
        if not managed_names:
            return False

        if original_plugins is None or original_plugins == ["*"]:
            allowed_plugins = sorted(
                {
                    str(getattr(plugin, "name", "") or "").strip()
                    for plugin in star_map.values()
                    if bool(getattr(plugin, "activated", False))
                    and str(getattr(plugin, "name", "") or "").strip()
                    not in managed_names
                }
            )
        else:
            allowed_plugins = [
                str(name)
                for name in list(original_plugins or [])
                if str(name) not in managed_names
            ]

        setattr(event, "_private_companion_original_plugins_name", original_plugins)
        setattr(
            event,
            "_private_companion_livingmemory_plugin_names",
            tuple(sorted(livingmemory_names)),
        )
        setattr(
            event,
            "_private_companion_memory_companion_plugin_names",
            tuple(sorted(memory_companion_names)),
        )
        setattr(event, "_private_companion_livingmemory_deferred", True)
        event.plugins_name = allowed_plugins
        return True

    @staticmethod
    def _restore_livingmemory_response_capture(event: AstrMessageEvent) -> None:
        if event is None or not bool(
            getattr(event, "_private_companion_livingmemory_deferred", False)
        ):
            return
        event.plugins_name = getattr(
            event,
            "_private_companion_original_plugins_name",
            None,
        )
        setattr(event, "_private_companion_livingmemory_deferred", False)

    @staticmethod
    def _message_content_text(message: Any) -> str:
        content = getattr(message, "content", None)
        if isinstance(content, str):
            return content.strip()
        if not isinstance(content, list):
            return ""
        return "".join(
            str(getattr(part, "text", "") or "")
            for part in content
            if isinstance(part, TextPart)
        ).strip()

    @staticmethod
    def _event_uses_streaming_result(event: AstrMessageEvent) -> bool:
        try:
            result = event.get_result()
        except Exception:
            return False
        content_type = getattr(result, "result_content_type", None)
        label = str(getattr(content_type, "name", "") or content_type or "").upper()
        return "STREAMING" in label

    def _last_assistant_text(self, run_context: Any) -> str:
        messages = getattr(run_context, "messages", None)
        if not isinstance(messages, list):
            return ""
        for message in reversed(messages):
            if str(getattr(message, "role", "") or "") == "assistant":
                return self._message_content_text(message)
        return ""

    def _prepare_final_response_persistence(
        self,
        event: AstrMessageEvent,
        run_context: Any,
        response: Any,
    ) -> None:
        if event is None or not bool(
            getattr(event, "_private_companion_persistence_managed", False)
        ):
            return
        messages = getattr(run_context, "messages", None)
        if not isinstance(messages, list):
            return
        for message in reversed(messages):
            if str(getattr(message, "role", "") or "") != "assistant":
                continue
            setattr(
                event,
                "_private_companion_raw_assistant_text",
                self._message_content_text(message),
            )
            setattr(event, "_private_companion_official_assistant_message", message)
            try:
                message._no_save = True
            except Exception:
                pass
            break

        setattr(
            event,
            "_private_companion_reviewed_assistant_text",
            str(getattr(response, "completion_text", "") or "").strip(),
        )
        try:
            request = event.get_extra("provider_request")
            conversation = getattr(request, "conversation", None)
            conversation_id = str(getattr(conversation, "cid", "") or "").strip()
            if conversation_id:
                setattr(
                    event,
                    "_private_companion_response_conversation_id",
                    conversation_id,
                )
        except Exception:
            pass

    def _delivered_assistant_text_from_chain(
        self,
        chain: list[Any] | tuple[Any, ...],
        *,
        fallback_text: str = "",
    ) -> str:
        components = list(chain or [])
        text = str(fallback_text or "").strip()
        if not text:
            text = self._actual_text_from_delivered_chain(components)
        image_count = sum(isinstance(component, Image) for component in components)
        record_count = sum(isinstance(component, Record) for component in components)
        media_marker = _format_history_media_marker(
            images=image_count,
            records=record_count,
        )
        if media_marker:
            text = f"{text}\n{media_marker}" if text else media_marker
        return text.strip()

    def _stage_delivered_assistant_for_official_history(
        self,
        *,
        event: AstrMessageEvent,
        assistant_response: str,
    ) -> bool:
        response_text = sanitize_llm_segment_control_tokens(assistant_response)
        # Media markers are useful to the companion's private continuity state,
        # but AstrBot's official conversation history is rendered directly by
        # chat clients. Keep internal metadata out of that user-visible field.
        visible_response_text = _strip_outbound_control_blocks(
            response_text,
            enabled=bool(runtime_persona_setting(self, "enable_framework_error_leak_guard", True)),
            tts_enabled=bool(runtime_persona_setting(self, "enable_tts_enhancement", False)),
        )
        message = getattr(event, "_private_companion_official_assistant_message", None)
        if (
            not visible_response_text
            and _has_history_media_marker(response_text)
            and message is not None
            and str(getattr(message, "role", "") or "") == "assistant"
            and self._message_content_text(message)
        ):
            try:
                # A late decorator may replace the visible model text with a
                # pure media chain. Preserve the original assistant text in
                # AstrBot history instead of leaking the internal media marker
                # or leaving the takeover flag stuck on the message.
                message._no_save = False
            except Exception as exc:
                logger.warning(
                    "纯媒体回复恢复 AstrBot 核心保存失败: session=%s error=%s",
                    _single_line(getattr(event, "unified_msg_origin", ""), 140),
                    _single_line(exc, 160),
                )
                return False
            logger.info(
                "纯媒体回复已保留转码前正文供 AstrBot 核心保存: %s",
                _single_line(getattr(event, "unified_msg_origin", ""), 140),
            )
            return True
        if (
            not visible_response_text
            or message is None
            or str(getattr(message, "role", "") or "") != "assistant"
        ):
            return False
        try:
            message.content = [TextPart(text=visible_response_text)]
            if hasattr(message, "tool_calls"):
                message.tool_calls = None
            if hasattr(message, "tool_call_id"):
                message.tool_call_id = None
            message._no_save = False
        except Exception as exc:
            logger.warning(
                "实际回复暂存到 AstrBot 会话上下文失败: session=%s error=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 140),
                _single_line(exc, 160),
            )
            return False
        logger.info(
            "已将实际发送回复交给 AstrBot 核心保存: %s",
            _single_line(getattr(event, "unified_msg_origin", ""), 140),
        )
        return True

    async def _append_delivered_assistant_to_conversation(
        self,
        *,
        event: AstrMessageEvent,
        assistant_response: str,
    ) -> bool:
        umo = str(getattr(event, "unified_msg_origin", "") or "").strip()
        response_text = sanitize_llm_segment_control_tokens(assistant_response)
        visible_response_text = _strip_outbound_control_blocks(
            response_text,
            enabled=bool(runtime_persona_setting(self, "enable_framework_error_leak_guard", True)),
            tts_enabled=bool(runtime_persona_setting(self, "enable_tts_enhancement", False)),
        )
        conv_mgr = getattr(getattr(self, "context", None), "conversation_manager", None)
        if not umo or not visible_response_text or conv_mgr is None:
            return False
        requested_cid = str(
            getattr(event, "_private_companion_response_conversation_id", "") or ""
        ).strip()

        async def write() -> bool:
            conv_id = requested_cid or str(
                await conv_mgr.get_curr_conversation_id(umo) or ""
            ).strip()
            if not conv_id:
                return False
            conversation = await conv_mgr.get_conversation(umo, conv_id)
            if conversation is None:
                return False
            raw_history = getattr(conversation, "history", "[]")
            history = (
                json.loads(raw_history or "[]")
                if isinstance(raw_history, str)
                else list(raw_history)
                if isinstance(raw_history, list)
                else []
            )
            history.append(AssistantMessageSegment(content=visible_response_text).model_dump())
            await conv_mgr.update_conversation(umo, conv_id, history=history)
            return True

        try:
            written = bool(
                await self._conversation_db_operation(
                    "append_delivered_assistant",
                    write,
                )
            )
        except Exception as exc:
            logger.warning(
                "实际回复写入 AstrBot 会话历史失败: session=%s error=%s",
                _single_line(umo, 140),
                _single_line(exc, 160),
            )
            return False
        if written:
            logger.info(
                "已将实际发送回复写入 AstrBot 会话历史: %s",
                _single_line(umo, 140),
            )
        return written
