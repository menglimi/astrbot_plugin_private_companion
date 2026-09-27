# -*- coding: utf-8 -*-
"""出站持久化域。

由 tools/split_main_domain.py 从 main.py 机械抽取（20 个方法 / 742 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPlugin）。
"""
from __future__ import annotations

import asyncio
import hashlib
import re
import time
import unicodedata
from .group_context_interception import restore_astrbot_group_history
from .hdsi_experiment import record_hdsi_proactive_event
from .helpers import _now_ts, _redact_outbound_secrets, _safe_float, _safe_int, _single_line
from .main_shared import _multi_persona_event_context
from .persona_config import runtime_persona_setting
from .segmented_message import bind_reply_components_to_first_text, component_kind, flatten_component_chunks
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import Plain, Record
from astrbot.api.provider import ProviderRequest
from typing import Any

from .logging_util import get_module_logger

logger = get_module_logger(__name__)

class PrivateCompanionPluginOutboundPersistenceMixin:
    """出站持久化域（从 PrivateCompanionPlugin 拆出）。"""

    @filter.after_message_sent(priority=-105000)
    @_multi_persona_event_context
    async def settle_req041_group_affinity_after_send(
        self, event: AstrMessageEvent, *args, **kwargs
    ) -> None:
        if self is None or not self.enabled:
            return
        try:
            await self._req041_settle_confirmed_group_affinity(event)
        except Exception as exc:
            status = getattr(self, "req041_migration_status", None)
            if isinstance(status, dict):
                status.update({"state": "degraded", "code": "group_affinity_settlement_failed"})
            logger.warning(
                "REQ-041 群好感度结算失败，已保持事件可重放: %s",
                _single_line(exc, 160),
            )

    @filter.after_message_sent(priority=-110000)
    @_multi_persona_event_context
    async def finish_req041_read_chain(self, event: AstrMessageEvent, *args, **kwargs) -> None:
        router = getattr(self, "req041_relationship_read_router", None)
        chain_id = str(getattr(event, "req041_read_chain_id", "") or "")
        if router is not None and chain_id:
            try:
                await asyncio.to_thread(router.finish, chain_id)
            except Exception as exc:
                logger.debug("REQ-041 读链清理失败: %s", _single_line(exc, 120))
            try:
                setattr(event, "req041_read_chain_id", "")
            except Exception:
                pass
            status = getattr(self, "req041_migration_status", None)
            metrics = getattr(self, "req041_observability", None)
            phase = str((status or {}).get("phase") or "") if isinstance(status, dict) else ""
            now = _now_ts()
            next_check = float(getattr(self, "_req041_stability_next_check_at", 0.0) or 0.0)
            if phase in {"S6", "S7", "S8"} and metrics is not None and now >= next_check:
                local_samples = sum(
                    int((item.get("local") or {}).get("samples") or 0)
                    for item in (metrics.snapshot().get("stages") or {}).values()
                    if isinstance(item, dict)
                )
                if local_samples >= 20:
                    self._req041_stability_next_check_at = now + 60.0
                    self._req041_schedule_replay()

    @filter.event_message_type(filter.EventMessageType.ALL, priority=11000)
    @_multi_persona_event_context
    async def prepare_tts_streaming_boundary(self, event: AstrMessageEvent, *args, **kwargs):
        """在 AstrBot 读取流式配置前，为可能进入插件 TTS 的回合预留完整回复。"""
        if self is None or not self.enabled:
            return
        preflight = getattr(self, "_tts_turn_requires_complete_reply", None)
        disable = getattr(self, "_disable_streaming_for_tts_turn", None)
        if not callable(preflight) or not callable(disable):
            return
        try:
            if preflight(event):
                disable(event)
        except Exception as exc:
            logger.debug(
                "TTS 流式预判失败，保留默认流式行为: session=%s error=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                _single_line(exc, 160),
            )

    @filter.on_decorating_result(priority=10000)
    @_multi_persona_event_context
    async def bridge_proactive_chat_outbound(self, event: AstrMessageEvent, *args, **kwargs):
        """识别 Proactive Chat 的装饰发送链，接入状态、边界和 TTS 统一出口。"""
        if self is None or not self.enabled or not runtime_persona_setting(self, 'enable_proactive_chat_integration', True):
            return
        bridge_context = self._proactive_chat_decorating_context()
        if not bridge_context.get("detected"):
            return
        try:
            if not bool(event.is_private_chat()):
                return
        except Exception:
            return
        session_id = str(getattr(event, "unified_msg_origin", "") or "")
        user_id, user = self._proactive_chat_bridge_user(session_id)
        if not user_id or not isinstance(user, dict) or not self._user_enabled_for_proactive(user_id, user):
            return
        result = event.get_result()
        chain = list(getattr(result, "chain", []) or []) if result is not None else []
        if not chain:
            return
        chain_text = "".join(
            str(getattr(component, "text", "") or "")
            for component in chain
            if isinstance(component, Plain)
        ).strip()
        source_text = str(bridge_context.get("full_text") or chain_text).strip()
        if not source_text:
            return
        try:
            await record_hdsi_proactive_event(
                self,
                event,
                "proactive_event",
                tick_id=_single_line(bridge_context.get("attempt_id"), 100),
                content_digest=hashlib.sha256(
                    source_text.encode("utf-8", errors="replace")
                ).hexdigest()[:20],
                content_chars=len(source_text),
            )
        except Exception:
            # HDSI continuity is observational here and cannot affect delivery.
            pass
        attempt_id = _single_line(bridge_context.get("attempt_id"), 100)
        replaced_attempts = getattr(self, "_proactive_chat_bridge_replaced_record_attempts", None)
        if not isinstance(replaced_attempts, dict):
            replaced_attempts = {}
            self._proactive_chat_bridge_replaced_record_attempts = replaced_attempts
        now = _now_ts()
        for stale_attempt, created_at in list(replaced_attempts.items()):
            if now - _safe_float(created_at, 0) > 10 * 60:
                replaced_attempts.pop(stale_attempt, None)
        if attempt_id and attempt_id in replaced_attempts and chain_text:
            self._suppress_outbound_reply(
                event,
                source="Proactive Chat 重复分支",
                reason="改写后的主动消息已处理",
                history_note="[本轮未发送：主动消息重复分支]",
            )
            logger.info(
                "已跳过 Proactive Chat 改写后的重复文本分支: session=%s attempt=%s",
                _single_line(session_id, 120),
                attempt_id,
            )
            return
        token = _single_line(bridge_context.get("token"), 80)
        if _single_line(user.get("proactive_chat_bridge_session"), 180) == _single_line(session_id, 180):
            token = token or _single_line(user.get("proactive_chat_bridge_token"), 80)
        segment_count = max(1, _safe_int(bridge_context.get("segment_count"), 1, 1))
        segment_index = max(0, min(_safe_int(bridge_context.get("segment_index"), 0, 0), segment_count - 1))
        setattr(event, "private_companion_proactive_framework", True)
        setattr(event, "_private_companion_external_proactive_source", "proactive_chat")
        setattr(event, "_private_companion_proactive_chat_attempt_id", attempt_id)
        setattr(event, "_private_companion_proactive_chat_token", token)
        setattr(event, "_private_companion_proactive_full_text", source_text)
        setattr(event, "_private_companion_proactive_segment_index", segment_index)
        setattr(event, "_private_companion_proactive_segment_count", segment_count)
        if segment_count > 1:
            setattr(event, "_private_companion_external_presegmented", True)
        review = await self._review_proactive_chat_bridge_message(
            session_id,
            source_text,
            token=token,
            attempt_id=attempt_id,
        )
        if not review.get("ok") or not review.get("text"):
            self._suppress_outbound_reply(
                event,
                source="Proactive Chat 主动候选",
                reason="主动候选未通过复核",
                history_note="[本轮未发送：主动候选未通过复核]",
                level="info",
            )
            if token:
                await self._cancel_proactive_chat_bridge(session_id, token=token)
            logger.info(
                "已拦截 Proactive Chat 主动候选: session=%s decision=%s reason=%s",
                _single_line(session_id, 120),
                _single_line(review.get("decision"), 24) or "drop",
                _single_line(review.get("reason"), 160),
            )
            return
        replacement = str(review["text"])
        should_replace_full_attempt = replacement != source_text and (
            any(isinstance(component, Record) for component in chain)
            or segment_count > 1
        )
        if should_replace_full_attempt:
            event.set_result(self._build_result_from_chain([Plain(replacement)]))
            source_text = replacement
            setattr(event, "_private_companion_proactive_full_text", replacement)
            if attempt_id:
                replaced_attempts[attempt_id] = now
        elif replacement != source_text:
            rebuilt: list[Any] = []
            replaced = False
            for component in chain:
                if isinstance(component, Plain):
                    if not replaced:
                        rebuilt.append(Plain(replacement))
                        replaced = True
                    continue
                rebuilt.append(component)
            if replaced:
                event.set_result(self._build_result_from_chain(rebuilt))
                source_text = replacement
                setattr(event, "_private_companion_proactive_full_text", replacement)
        if bool(bridge_context.get("tts_sent")) and not should_replace_full_attempt:
            setattr(event, "_private_companion_skip_tts_enhancement", "proactive_chat_prebuilt_tts")
        logger.info(
            "已接入 Proactive Chat 发送前链路: session=%s segment=%s/%s upstream_tts=%s text=%s",
            _single_line(session_id, 120),
            segment_index + 1,
            segment_count,
            bool(bridge_context.get("tts_sent")),
            _single_line(source_text, 160),
        )

    @filter.on_decorating_result(priority=-20000)
    @_multi_persona_event_context
    async def finalize_proactive_chat_outbound_bridge(self, event: AstrMessageEvent, *args, **kwargs):
        """在所有装饰器结束后，仅为仍有实际发送内容的 Proactive Chat 链同步状态。"""
        if self is None or not self.enabled or not runtime_persona_setting(self, 'enable_proactive_chat_integration', True):
            return
        if str(getattr(event, "_private_companion_external_proactive_source", "") or "") != "proactive_chat":
            return
        session_id = str(getattr(event, "unified_msg_origin", "") or "")
        token = _single_line(getattr(event, "_private_companion_proactive_chat_token", ""), 80)
        attempt_id = _single_line(getattr(event, "_private_companion_proactive_chat_attempt_id", ""), 100)
        runtime_bridge = getattr(self, "_proactive_chat_runtime_bridge", None)
        if runtime_bridge is not None and runtime_bridge.owns_outbound(session_id, attempt_id):
            # 深度桥接会在平台 send_by_session/context.send_message 无异常返回后统一结算。
            # 此处仍处于发送前装饰阶段，不能把非空消息链提前当成已送达。
            return
        result = event.get_result()
        chain = list(getattr(result, "chain", []) or []) if result is not None else []
        if not chain:
            if token:
                await self._cancel_proactive_chat_bridge(session_id, token=token)
            return
        source_text = str(getattr(event, "_private_companion_proactive_full_text", "") or "").strip()
        if not source_text:
            return
        recorded = await self._record_proactive_chat_bridge_sent(
            session_id,
            source_text,
            token=token,
            attempt_id=attempt_id,
        )
        if recorded.get("recorded"):
            logger.info(
                "已同步 Proactive Chat 最终发送状态: session=%s text=%s",
                _single_line(session_id, 120),
                _single_line(source_text, 160),
            )

    @filter.on_decorating_result()
    @_multi_persona_event_context
    async def stop_passive_input_status_before_private_send(self, event: AstrMessageEvent, *args, **kwargs):
        """LLM 回复进入发送前阶段时停止私聊持续输入状态。"""
        if self is None or not self.enabled:
            return
        if bool(getattr(event, "is_private_chat", lambda: False)()):
            self._stop_passive_input_status_loop(event)

    @filter.after_message_sent(priority=8500)
    @_multi_persona_event_context
    async def remember_confirmed_outbound_text(self, event: AstrMessageEvent, *args, **kwargs):
        """Confirm only candidates for which the platform send operation ran."""
        if self is None or not self.enabled:
            return
        if not self._reaction_expression_primary_reply_confirmed(
            event,
            require_segmented_complete=True,
        ):
            return
        candidate = getattr(event, "_private_companion_outbound_text_candidate", None)
        if isinstance(candidate, dict):
            self._confirm_outbound_text_candidate(candidate)

    @filter.after_message_sent(priority=8000)
    @_multi_persona_event_context
    async def release_tts_reply_remainder_after_send(
        self, event: AstrMessageEvent, *args, **kwargs
    ):
        """Start delayed TTS chunks only after the platform accepted the first chunk."""
        if self is None or not self.enabled:
            return
        pending = getattr(event, "_private_companion_tts_reply_remainder", None)
        if not isinstance(pending, dict):
            return
        try:
            delattr(event, "_private_companion_tts_reply_remainder")
        except Exception:
            setattr(event, "_private_companion_tts_reply_remainder", None)
        if not self._reaction_expression_primary_reply_confirmed(event):
            return
        chunks = pending.get("chunks")
        if not isinstance(chunks, list) or not chunks:
            return
        operation = self._send_tts_chain_chunks_after_first(
            event,
            chunks,
            started_at=_safe_float(pending.get("started_at"), time.time(), 0.0),
            primary_delivery_confirmed=True,
        )
        self._create_lifecycle_background_task(
            operation,
            label="tts_reply_remainder",
        )

    @filter.after_message_sent(priority=7000)
    @_multi_persona_event_context
    async def release_deferred_reaction_tts_after_send(
        self, event: AstrMessageEvent, *args, **kwargs
    ):
        """Generate optional reaction voice only after text and image delivery settle."""
        if self is None or not self.enabled:
            return
        pending = getattr(
            event,
            "_private_companion_deferred_reaction_tts",
            None,
        )
        if not isinstance(pending, dict):
            return
        try:
            delattr(event, "_private_companion_deferred_reaction_tts")
        except Exception:
            setattr(event, "_private_companion_deferred_reaction_tts", None)
        if not self._reaction_expression_primary_reply_confirmed(
            event,
            require_segmented_complete=True,
        ):
            return
        operation = self._send_deferred_reaction_tts(event, pending)
        self._create_lifecycle_background_task(
            operation,
            label="reaction_tts_after_delivery",
        )

    @filter.on_agent_begin(priority=100000)
    @_multi_persona_event_context
    async def begin_final_response_persistence(
        self,
        event: AstrMessageEvent,
        run_context: Any,
        *args,
        **kwargs,
    ):
        """Defer optional memory sinks until the platform confirms delivery."""
        if self is None or not self.enabled or event is None:
            return
        self._begin_final_response_persistence(event)

    @filter.on_agent_done(priority=-100000)
    @_multi_persona_event_context
    async def prepare_final_response_persistence(
        self,
        event: AstrMessageEvent,
        run_context: Any,
        response: Any,
        *args,
        **kwargs,
    ):
        if self is None or event is None:
            return
        await self._prepare_final_response_after_agent(event, run_context, response)

    @filter.on_agent_done(priority=1000000)
    @_multi_persona_event_context
    async def restore_intercepted_astrbot_group_history(
        self,
        event: AstrMessageEvent,
        run_context: Any,
        response: Any,
        *args,
        **kwargs,
    ):
        """Restore provider-hidden AstrBot history before the core persists it."""
        if self is None or event is None:
            return
        result = restore_astrbot_group_history(event, run_context)
        if result.get("failed"):
            logger.error(
                "AstrBot 群聊历史恢复失败，已阻止本轮覆盖旧会话: session=%s reason=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 140) or "unknown",
                _single_line(result.get("reason"), 80) or "unknown",
            )
        elif result.get("restored"):
            logger.debug(
                "已在核心保存前恢复 AstrBot 群聊历史: session=%s messages=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 140) or "unknown",
                result.get("history_messages", 0),
            )

    @filter.on_decorating_result(priority=-30000)
    @_multi_persona_event_context
    async def capture_final_outbound_chain_for_persistence(
        self,
        event: AstrMessageEvent,
        *args,
        **kwargs,
    ):
        if self is None or event is None:
            return
        self._capture_final_outbound_delivery(event)

    @filter.after_message_sent(priority=-100000)
    @_multi_persona_event_context
    async def persist_confirmed_passive_reply(
        self,
        event: AstrMessageEvent,
        *args,
        **kwargs,
    ):
        if self is None or event is None:
            return
        await self._persist_final_outbound_delivery(event)

    @staticmethod
    def _photo_tool_followup_chain_has_visible_content(chain: list[Any]) -> bool:
        for component in chain if isinstance(chain, list) else []:
            if not isinstance(component, Plain):
                return True
            text = str(getattr(component, "text", "") or "")
            visible = "".join(
                char
                for char in text
                if not char.isspace() and not unicodedata.category(char).startswith("C")
            )
            if visible:
                return True
        return False

    @filter.on_decorating_result(priority=200)
    @_multi_persona_event_context
    async def attach_group_reply_quote(self, event: AstrMessageEvent, *args, **kwargs):
        """Bind reply quotes to text instead of a leading voice or image chunk."""
        result = None
        chain: list[Any] = []
        if self is None or not self.enabled:
            return
        try:
            result = event.get_result()
        except Exception as exc:
            logger.debug("群聊补引用读取结果失败: %s", _single_line(exc, 120))
            return
        if result is None:
            return
        try:
            if hasattr(result, "is_llm_result") and not result.is_llm_result():
                return
        except Exception:
            pass
        try:
            chain = list(getattr(result, "chain", []) or [])
        except Exception as exc:
            logger.debug("群聊补引用读取消息链失败: %s", _single_line(exc, 120))
            return
        if not chain:
            return

        delivery_chunks: list[list[Any]] = [chain]
        for attr_name in (
            "_private_companion_tts_reply_remainder",
            "_private_companion_reaction_expression_segmented_remainder",
        ):
            pending = getattr(event, attr_name, None)
            pending_chunks = pending.get("chunks") if isinstance(pending, dict) else None
            if not isinstance(pending_chunks, list):
                continue
            delivery_chunks.extend(
                chunk for chunk in pending_chunks if isinstance(chunk, list)
            )

        has_voice = any(
            isinstance(component, Record)
            for chunk in delivery_chunks
            for component in chunk
        )
        has_visible_text = any(
            isinstance(component, Plain)
            and bool(str(getattr(component, "text", "") or "").strip())
            for chunk in delivery_chunks
            for component in chunk
        )
        # Keep quotes whenever a visible text companion exists. A voice
        # component must not erase a quote needed by image/forward/vision
        # consumers or by a delayed text chunk from this reply.
        suppress_reply_reason = "voice_component" if has_voice and not has_visible_text else ""
        if not has_voice:
            official_tts_checker = getattr(
                self,
                "_should_defer_segmenting_to_astrbot_tts",
                None,
            )
            if callable(official_tts_checker):
                try:
                    if await official_tts_checker(event, result, chain):
                        suppress_reply_reason = "framework_tts"
                except Exception as exc:
                    logger.debug(
                        "官方 TTS 引用预判失败: session=%s error=%s",
                        _single_line(getattr(event, "unified_msg_origin", ""), 120)
                        or "unknown",
                        _single_line(exc, 120),
                    )
        if suppress_reply_reason:
            cleaned_chunks = [
                self._without_reply_components(chunk) for chunk in delivery_chunks
            ]
            removed_count = sum(len(chunk) for chunk in delivery_chunks) - sum(
                len(chunk) for chunk in cleaned_chunks
            )
            primary_chunk = cleaned_chunks[0]
            pending_index = 1
            for attr_name in (
                "_private_companion_tts_reply_remainder",
                "_private_companion_reaction_expression_segmented_remainder",
            ):
                pending = getattr(event, attr_name, None)
                pending_chunks = (
                    pending.get("chunks") if isinstance(pending, dict) else None
                )
                if not isinstance(pending_chunks, list):
                    continue
                replacement_count = len(
                    [chunk for chunk in pending_chunks if isinstance(chunk, list)]
                )
                pending["chunks"] = cleaned_chunks[
                    pending_index : pending_index + replacement_count
                ]
                pending_index += replacement_count
            try:
                result.chain = primary_chunk
            except Exception:
                event.set_result(self._build_result_from_chain(primary_chunk))
            logger.info(
                "语音回复已移除孤立消息引用: session=%s reason=%s removed=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120)
                or "unknown",
                suppress_reply_reason,
                removed_count,
            )
            return

        existing_replies = [
            component
            for chunk in delivery_chunks
            for component in chunk
            if self._is_reply_component(component)
        ]
        if not existing_replies:
            if not bool(runtime_persona_setting(self, 'enable_proactive_quote_trigger_message', False)):
                return
            if not bool(runtime_persona_setting(self, 'enable_quote_group_reply', True)):
                return
            if self._proactive_only_blocks_passive_event(event, "enable_group_companion"):
                return
            try:
                quote_message_id = self._group_current_reply_quote_message_id(
                    event,
                    text_or_chain=flatten_component_chunks(delivery_chunks),
                )
            except Exception as exc:
                logger.debug("群聊补引用计算引用目标失败: %s", _single_line(exc, 120))
                return
            if not quote_message_id:
                return
            try:
                reply = self._make_reply_component(quote_message_id, event=event)
            except Exception as exc:
                logger.debug("群聊补引用构建消息链失败: %s", _single_line(exc, 120))
                return
            if reply is None:
                return
            existing_replies = [reply]

        bound_chunks, _changed = bind_reply_components_to_first_text(
            delivery_chunks,
            plain_type=Plain,
            classify=component_kind,
            reply_components=existing_replies,
        )
        if not bound_chunks:
            return
        primary_chunk = bound_chunks[0]
        pending_index = 1
        has_pending_delivery = False
        for attr_name in (
            "_private_companion_tts_reply_remainder",
            "_private_companion_reaction_expression_segmented_remainder",
        ):
            pending = getattr(event, attr_name, None)
            pending_chunks = pending.get("chunks") if isinstance(pending, dict) else None
            if not isinstance(pending_chunks, list):
                continue
            has_pending_delivery = True
            replacement_count = len(
                [chunk for chunk in pending_chunks if isinstance(chunk, list)]
            )
            pending["chunks"] = bound_chunks[pending_index : pending_index + replacement_count]
            pending_index += replacement_count
        if not has_pending_delivery and len(bound_chunks) > 1:
            # This result is still delivered as one message. Do not discard
            # text merely because quote binding split the in-memory chain.
            primary_chunk = flatten_component_chunks(bound_chunks)
        try:
            result.chain = primary_chunk
        except Exception:
            event.set_result(self._build_result_from_chain(primary_chunk))

    def _neutralize_stale_reaction_feedback_in_history(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
    ) -> None:
        """Remove leaked internal reaction tags from provider history.

        Reaction-expression tags are transport metadata, not conversation text.
        A failed/older response may leave them in ``req.contexts``; stripping
        only those tags keeps the surrounding user feedback intact and avoids
        teaching the model to emit the internal protocol on a later turn.
        """
        contexts = getattr(req, "contexts", None)
        if not isinstance(contexts, list) or not contexts:
            return
        tag_pattern = re.compile(
            r"(?:<|&lt;|\\<)\s*/?\s*pc[_-]?reaction[_-]?expression\b[^>]*?(?:>|&gt;|\\>)"
            r".*?"
            r"(?:<|&lt;|\\<)\s*/\s*pc[_-]?reaction[_-]?expression\s*(?:>|&gt;|\\>)",
            flags=re.IGNORECASE | re.DOTALL,
        )
        changed = 0

        def clean(value: Any) -> tuple[Any, bool]:
            if isinstance(value, str):
                updated = tag_pattern.sub("", value)
                updated = re.sub(r"\n{3,}", "\n\n", updated).strip()
                return updated, updated != value
            if isinstance(value, dict):
                updated = dict(value)
                dirty = False
                for key in ("content", "text", "value"):
                    if key not in updated:
                        continue
                    cleaned, item_dirty = clean(updated.get(key))
                    if item_dirty:
                        updated[key] = cleaned
                        dirty = True
                return updated, dirty
            if isinstance(value, list):
                items: list[Any] = []
                dirty = False
                for item in value:
                    cleaned, item_dirty = clean(item)
                    items.append(cleaned)
                    dirty = dirty or item_dirty
                return items, dirty
            return value, False

        sanitized: list[Any] = []
        for item in contexts:
            cleaned, item_changed = clean(item)
            sanitized.append(cleaned)
            if item_changed:
                changed += 1
        if changed <= 0:
            return
        try:
            req.contexts = sanitized
        except Exception:
            return
        logger.info(
            "已清理请求历史里的残留反应协议标签: session=%s contexts_changed=%s",
            _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            changed,
        )

    def _schedule_reply_interception_forward(
        self,
        category: str,
        *,
        source: str = "",
        reason: str = "",
        source_session: str = "",
        inbound: str = "",
        before: str = "",
        after: str = "",
        detail: str = "",
    ) -> None:
        if not bool(getattr(self, "enable_reply_interception_forward", False)):
            return
        enabled = {
            "plugin_block": bool(getattr(self, "reply_interception_forward_plugin_blocks", False)),
            "rewrite": bool(getattr(self, "reply_interception_forward_rewrites", False)),
            "proactive_block": bool(getattr(self, "reply_interception_forward_proactive_blocks", False)),
        }.get(str(category or ""), False)
        target = _single_line(getattr(self, "reply_interception_forward_target_umo", ""), 180)
        if not enabled or not target:
            return
        labels = {
            "plugin_block": "插件阻断消息",
            "rewrite": "回复已改写",
            "proactive_block": "主动消息被拦截",
        }
        fields = [
            f"【回复拦截转发】{labels.get(category, category)}",
            f"时间：{self._environment_now().strftime('%Y-%m-%d %H:%M:%S')}",
        ]
        for label, value, limit in (
            ("来源", source, 80),
            ("原会话", source_session, 180),
            ("原因", reason, 300),
            ("用户消息", inbound, 300),
            ("原消息", before, 500),
            ("处理后", after, 500),
            ("补充", detail, 300),
        ):
            clean = _single_line(value, limit)
            if clean:
                fields.append(f"{label}：{clean}")
        text = "\n".join(fields)
        now = _now_ts()
        signature = hashlib.sha1(f"{target}|{category}|{source_session}|{reason}|{before}|{after}".encode("utf-8", errors="ignore")).hexdigest()[:20]
        recent = getattr(self, "_reply_interception_forward_recent", None)
        if not isinstance(recent, dict):
            recent = {}
            self._reply_interception_forward_recent = recent
        recent = {key: ts for key, ts in recent.items() if now - _safe_float(ts, 0) <= 30}
        self._reply_interception_forward_recent = recent
        if now - _safe_float(recent.get(signature), 0) <= 5:
            return
        recent[signature] = now
        try:
            self._create_lifecycle_background_task(
                self._send_reply_interception_forward(target, text),
                label="reply_interception_forward",
            )
        except Exception as exc:
            logger.warning(
                "回复拦截转发无法启动: %s",
                _single_line(exc, 160),
            )

    async def _send_reply_interception_forward(self, target_umo: str, text: str) -> None:
        try:
            safe_text = _redact_outbound_secrets(text, self)
            await self.context.send_message(target_umo, MessageChain([Plain(safe_text)]))
            logger.info("已转发回复拦截情况: target=%s", _single_line(target_umo, 120))
        except Exception as exc:
            logger.warning(
                "回复拦截转发失败: target=%s error=%s",
                _single_line(target_umo, 120),
                _single_line(exc, 180),
            )

    def _redact_outbound_chain_secrets(self, chain: list[Any]) -> tuple[list[Any], bool]:
        changed = False
        for comp in list(chain or []):
            if not isinstance(comp, Plain):
                continue
            original = str(getattr(comp, "text", "") or "")
            cleaned = _redact_outbound_secrets(original, self)
            if cleaned == original:
                continue
            changed = True
            try:
                comp.text = cleaned
            except Exception:
                pass
        return chain, changed
