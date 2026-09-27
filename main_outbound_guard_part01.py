# -*- coding: utf-8 -*-
"""PrivateCompanionPluginOutboundGuardPart01Mixin。

由 tools/split_mixin_domain.py 从 main_outbound_guard.py 机械抽取（12 个方法 + 0 个模块级名字 + 0 个类级赋值 / 488 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPluginOutboundGuardMixin）。
"""
from __future__ import annotations

from .main_outbound_guard_shared import filter
from .main_outbound_guard_shared import logger
from .main_outbound_guard_shared import Any
from .main_outbound_guard_shared import AstrMessageEvent
from .main_outbound_guard_shared import Image
from .main_outbound_guard_shared import Plain
from .main_outbound_guard_shared import ProviderRequest
from .main_outbound_guard_shared import _multi_persona_event_context
from .main_outbound_guard_shared import _path_text
from .main_outbound_guard_shared import _plugin_instance_can_dispatch
from .main_outbound_guard_shared import _safe_int
from .main_outbound_guard_shared import _single_line
from .main_outbound_guard_shared import _strip_outbound_control_blocks
from .main_outbound_guard_shared import json
from .main_outbound_guard_shared import os
from .main_outbound_guard_shared import re
from .main_outbound_guard_shared import restore_wake_message_request
from .main_outbound_guard_shared import runtime_persona_setting
from .main_outbound_guard_shared import sanitize_llm_segment_control_tokens
from .main_outbound_guard_shared import unicodedata



class PrivateCompanionPluginOutboundGuardPart01Mixin:
    """PrivateCompanionPluginOutboundGuardPart01Mixin（从 PrivateCompanionPluginOutboundGuardMixin 拆出）。"""


    @staticmethod
    def _sanitize_persona_id(value: Any) -> str:
        text = unicodedata.normalize("NFC", str(value or ""))
        text = "".join(
            character
            for character in text
            if unicodedata.category(character) not in {"Cc", "Cs"}
        ).strip()
        return text[:96]

    @filter.on_llm_request(priority=230000)
    async def restore_addressed_user_request(
        self, event: AstrMessageEvent, req: ProviderRequest, *args, **kwargs,
    ):
        """Restore addressed chat before state enrichment and MemoryCompanion run."""
        if (
            self is None or not _plugin_instance_can_dispatch(self)
            or not self.enabled or not self._bot_scope_allows_event(event)
        ):
            return
        restore_wake_message_request(self, event, req)

    @filter.on_decorating_result(priority=-18000)
    @_multi_persona_event_context
    async def attach_reaction_expression_image_before_send(
        self, event: AstrMessageEvent, *args, **kwargs
    ):
        """Prepare a local reaction image without weakening the text reply."""
        if self is None or not self.enabled:
            return
        if bool(getattr(event, "_private_companion_skip_reaction_expression", False)):
            for attr in (
                "_private_companion_reaction_expression_intent",
                "_private_companion_deferred_reaction_tts",
            ):
                try:
                    delattr(event, attr)
                except (AttributeError, TypeError):
                    pass
            logger.debug(
                "本轮已有真实生图，跳过追加表情附件: session=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            )
            return
        intent = getattr(
            event, "_private_companion_reaction_expression_intent", None
        )
        if not isinstance(intent, dict) or not intent:
            return
        tracker_installer = getattr(
            self,
            "_install_reaction_expression_delivery_tracker",
            None,
        )
        if callable(tracker_installer):
            # TTS may already be deferred even when lookup later misses. Track the
            # primary reply before any attachment-only early return.
            tracker_installer(event, {})
        if bool(
            getattr(
                event,
                "_private_companion_reaction_expression_attachment_attempted",
                False,
            )
        ):
            return
        setattr(
            event,
            "_private_companion_reaction_expression_attachment_attempted",
            True,
        )

        result = event.get_result()
        chain = list(getattr(result, "chain", []) or []) if result is not None else []
        visible_text = "".join(
            str(getattr(component, "text", "") or "")
            for component in chain
            if isinstance(component, Plain)
        ).strip()
        if not self._reaction_expression_has_visible_text(visible_text):
            self._note_reaction_expression_runtime(
                skipped=1, last_reason="missing_visible_text"
            )
            self._log_reaction_expression_event(
                event,
                stage="attachment",
                decision="skip",
                reason="missing_visible_text",
                scope=self._reaction_expression_scope(event),
                found=False,
                sent=False,
            )
            return
        if any(isinstance(component, Image) for component in chain):
            self._note_reaction_expression_runtime(
                skipped=1, last_reason="existing_image"
            )
            self._log_reaction_expression_event(
                event,
                stage="attachment",
                decision="skip",
                reason="existing_image",
                scope=self._reaction_expression_scope(event),
                found=False,
                sent=False,
            )
            return

        context_text = _single_line(intent.get("context"), 1000) or _single_line(
            visible_text, 700
        )
        raw_prepared = await self._pc_reaction_expression_impl(
            event,
            query=_single_line(intent.get("provider_query"), 500),
            context=context_text,
            meme_only=True,
            send=True,
            purpose=_single_line(intent.get("purpose"), 120),
            emotion=_single_line(intent.get("emotion"), 80),
            intensity=_safe_int(intent.get("intensity"), 0, 0, 5),
            candidate_queries=intent.get("candidate_queries", []),
            attach_only=True,
        )
        try:
            prepared = json.loads(raw_prepared)
        except (TypeError, ValueError, json.JSONDecodeError):
            prepared = {}
        if not isinstance(prepared, dict) or prepared.get("decision") != "attach":
            return

        pending = getattr(
            event,
            "_private_companion_reaction_expression_pending_attachment",
            None,
        )
        image_path = _path_text(prepared.get("path"), 1000)
        if not isinstance(pending, dict) or not image_path or not os.path.isfile(
            image_path
        ):
            if isinstance(pending, dict):
                await self._settle_reaction_expression_attachment_data(
                    pending,
                    sent=False,
                    reason="attachment_file_missing",
                )
            return
        try:
            builder = getattr(self, "_build_reaction_image_component", None)
            if callable(builder):
                image_component = builder(event, image_path)
            else:
                try:
                    image_component = Image.fromFileSystem(image_path)
                except AttributeError:
                    image_component = Image.from_file_system(image_path)
                try:
                    object.__setattr__(
                        image_component,
                        "_private_companion_reaction_expression",
                        True,
                    )
                except Exception:
                    pass
        except Exception as exc:
            await self._settle_reaction_expression_attachment_data(
                pending,
                sent=False,
                reason="attachment_component_failed",
            )
            logger.warning(
                "表情图片附件构建失败: error_type=%s",
                type(exc).__name__,
            )
            return
        delivery_mode = self._reaction_expression_delivery_mode()
        pending["delivery_mode"] = delivery_mode
        pending["component"] = image_component
        pending["delivery_started"] = False
        self._install_reaction_expression_delivery_tracker(event, pending)
        if delivery_mode == "same_message":
            chain.append(image_component)
            try:
                result.chain = chain
            except Exception:
                event.set_result(self._build_result_from_chain(chain))
            pending["attached"] = True
        elif delivery_mode == "separate_before":
            pending["delivery_started"] = True
            sent = await self._send_reaction_expression_component_separately(
                event,
                image_component,
            )
            await self._settle_reaction_expression_attachment_data(
                pending,
                sent=sent,
                reason="delivered" if sent else "delivery_failed",
            )
        self._log_reaction_expression_event(
            event,
            stage="attachment",
            decision="accepted",
            reason=(
                "attachment_appended"
                if delivery_mode == "same_message"
                else "delivered_before_primary"
                if delivery_mode == "separate_before" and pending.get("sent")
                else "delivery_failed"
                if delivery_mode == "separate_before"
                else "attachment_prepared"
            ),
            scope=self._reaction_expression_scope(event),
            found=True,
            sent=bool(pending.get("sent")),
            image_id=prepared.get("image_id"),
            confidence=prepared.get("confidence"),
            cache_hit=prepared.get("cache_hit"),
            latency_ms=prepared.get("lookup_latency_ms"),
            match_basis=pending.get("match_basis"),
        )

    @staticmethod
    def _restore_reaction_expression_delivery_tracker(event: AstrMessageEvent) -> None:
        tracker = getattr(
            event,
            "_private_companion_reaction_expression_delivery_tracker",
            None,
        )
        if not isinstance(tracker, dict) or tracker.get("restored"):
            return
        tracker["restored"] = True
        original_send = tracker.get("original_send")
        if callable(original_send):
            try:
                setattr(event, "send", original_send)
            except Exception:
                pass
        try:
            delattr(
                event,
                "_private_companion_reaction_expression_delivery_tracker",
            )
        except Exception:
            try:
                setattr(
                    event,
                    "_private_companion_reaction_expression_delivery_tracker",
                    None,
                )
            except Exception:
                pass

    @filter.on_decorating_result(priority=-10000)
    @_multi_persona_event_context
    async def suppress_recent_duplicate_outbound_text(self, event: AstrMessageEvent, *args, **kwargs):
        """Last-mile idempotency guard for adapter echoes and concurrent reply chains."""
        if self is None or not self.enabled:
            return
        candidate = self._outbound_text_duplicate_candidate(event)
        if not candidate:
            return
        duplicate_state = self._reserve_outbound_text_candidate(candidate)
        if not duplicate_state:
            setattr(event, "_private_companion_outbound_text_candidate", candidate)
            return
        logger.warning(
            "发送前拦截短时间重复正文: scope=%s sender=%s previous=%s text=%s",
            candidate.get("scope") or "unknown",
            candidate.get("sender_id") or "-",
            duplicate_state,
            _single_line(candidate.get("text"), 120),
        )
        self._suppress_outbound_reply(
            event,
            source="重复正文",
            reason="短时间内重复发送相同正文",
            history_note="[本轮未发送：短时间内重复正文]",
            level="info",
        )

    @filter.on_decorating_result()
    @_multi_persona_event_context
    async def strip_outbound_control_blocks_before_send(self, event: AstrMessageEvent, *args, **kwargs):
        """发送前兜底清理内部控制块，避免 timer/TTSBLOCK 泄漏到聊天。"""
        if self is None or not self.enabled:
            return
        if self._proactive_only_blocks_passive_event(event, "llm_request"):
            return
        if not bool(runtime_persona_setting(self, "enable_framework_error_leak_guard", True)):
            return
        result = event.get_result()
        chain = list(getattr(result, "chain", []) or []) if result is not None else []
        if not chain:
            return
        changed = False
        protected_tts_tokens = getattr(event, "_private_companion_tts_block_tokens", None)
        preserve_private_tts_tokens = (
            bool(runtime_persona_setting(self, 'enable_tts_enhancement', False))
            and isinstance(protected_tts_tokens, dict)
            and bool(protected_tts_tokens)
        )
        for comp in chain:
            if not isinstance(comp, Plain):
                continue
            original = str(getattr(comp, "text", "") or "")
            cleaned = _strip_outbound_control_blocks(
                original,
                tts_enabled=bool(runtime_persona_setting(self, "enable_tts_enhancement", False)),
                preserve_private_tts_tokens=preserve_private_tts_tokens,
                allowed_private_tts_tokens=set(protected_tts_tokens.keys()) if isinstance(protected_tts_tokens, dict) else None,
            )
            if not bool(runtime_persona_setting(self, 'enable_tts_enhancement', False)):
                cleaned = re.sub(r"</?t{2,}s\b[^>]*>", "", cleaned, flags=re.IGNORECASE).strip()
            cleaned = sanitize_llm_segment_control_tokens(cleaned)
            if cleaned != original:
                changed = True
                try:
                    comp.text = cleaned
                except Exception:
                    pass
        if changed:
            logger.warning(
                "发送前已清理内部控制标签: session=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            )

    @filter.on_decorating_result(priority=-29999)
    @_multi_persona_event_context
    async def final_strip_outbound_control_blocks_before_send(
        self, event: AstrMessageEvent, *args, **kwargs
    ):
        """最后一环清理，防止后续 TTS/分段钩子重新带出内部标签。"""
        if self is None or not bool(getattr(self, "enabled", False)):
            return
        if not bool(runtime_persona_setting(self, "enable_framework_error_leak_guard", True)):
            return
        result = event.get_result()
        chain = list(getattr(result, "chain", []) or []) if result is not None else []
        if not chain:
            return
        protected_tts_tokens = getattr(event, "_private_companion_tts_block_tokens", None)
        preserve_private_tts_tokens = (
            bool(runtime_persona_setting(self, 'enable_tts_enhancement', False))
            and isinstance(protected_tts_tokens, dict)
            and bool(protected_tts_tokens)
        )
        cleaned_chain: list[Any] = []
        changed = False
        for component in chain:
            if not isinstance(component, Plain):
                cleaned_chain.append(component)
                continue
            original = str(getattr(component, "text", "") or "")
            cleaned = _strip_outbound_control_blocks(
                original,
                tts_enabled=bool(runtime_persona_setting(self, "enable_tts_enhancement", False)),
                preserve_private_tts_tokens=preserve_private_tts_tokens,
                allowed_private_tts_tokens=(
                    set(protected_tts_tokens.keys())
                    if isinstance(protected_tts_tokens, dict)
                    else None
                ),
            )
            if not bool(runtime_persona_setting(self, 'enable_tts_enhancement', False)):
                cleaned = re.sub(r"</?t{2,}s\b[^>]*>", "", cleaned, flags=re.IGNORECASE).strip()
            cleaned = sanitize_llm_segment_control_tokens(cleaned)
            if cleaned:
                cleaned_chain.append(Plain(cleaned) if cleaned != original else component)
            if cleaned != original:
                changed = True
        if not changed:
            return
        try:
            result.chain = cleaned_chain
        except Exception:
            event.set_result(self._build_result_from_chain(cleaned_chain))
        logger.warning(
            "最终发送前已清理内部控制标签: session=%s",
            _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
        )

    @filter.on_decorating_result()
    @_multi_persona_event_context
    async def strip_plaintext_tool_calls_before_send(self, event: AstrMessageEvent, *args, **kwargs):
        """阻止兼容模型把工具调用 JSON 当普通聊天正文发送。"""
        if self is None or not self.enabled:
            return
        result = event.get_result()
        chain = list(getattr(result, "chain", []) or []) if result is not None else []
        if not chain:
            return
        changed = False
        leaked_names: list[str] = []
        cleaned_chain: list[Any] = []
        for comp in chain:
            if not isinstance(comp, Plain):
                cleaned_chain.append(comp)
                continue
            original = str(getattr(comp, "text", "") or "")
            cleaned, calls = self._strip_plaintext_tool_call_envelopes(original)
            if not calls:
                cleaned_chain.append(comp)
                continue
            changed = True
            leaked_names.extend(str(item.get("name") or "") for item in calls)
            if cleaned:
                try:
                    comp.text = cleaned
                    cleaned_chain.append(comp)
                except Exception:
                    cleaned_chain.append(Plain(cleaned))
        if not changed:
            return
        try:
            result.chain = cleaned_chain
        except Exception:
            event.set_result(self._build_result_from_chain(cleaned_chain))
        logger.warning(
            "发送前终检已移除明文工具调用: session=%s tools=%s",
            _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            ",".join(leaked_names),
        )

    @filter.on_decorating_result()
    @_multi_persona_event_context
    async def cancel_reply_if_trigger_recalled_before_send(self, event: AstrMessageEvent, *args, **kwargs):
        """若触发/唤醒消息在回复发出前被撤回，则静默取消本次回复。"""
        if self is None or not self.enabled:
            return
        if self._proactive_only_blocks_passive_event(event, "enable_recall_enhancement"):
            return
        recalled_message_id = await self._should_cancel_reply_for_missing_or_recalled_trigger(event)
        if not recalled_message_id:
            return
        logger.info(
            "触发消息已撤回或发送前不可见，取消本次发送: session=%s message_id=%s",
            _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            recalled_message_id,
        )
        self._suppress_outbound_reply(
            event,
            source="撤回取消",
            reason="触发消息已撤回或发送前不可见",
            history_note="[本轮未发送：触发消息已撤回]",
            detail=str(recalled_message_id),
            level="info",
        )

    @filter.on_decorating_result()
    @_multi_persona_event_context
    async def suppress_forbidden_outbound_before_send(self, event: AstrMessageEvent, *args, **kwargs):
        """自己的待发送消息命中违禁词时，优先在发送前拦截。"""
        if self is None or not self.enabled:
            return
        if self._proactive_only_blocks_passive_event(event, "enable_recall_enhancement"):
            return
        if not runtime_persona_setting(self, 'enable_recall_enhancement', True) or not runtime_persona_setting(self, 'enable_forbidden_word_recall', False):
            return
        if not self._forbidden_recall_words():
            return
        result = event.get_result()
        chain = list(getattr(result, "chain", []) or []) if result is not None else []
        if not chain:
            return
        text = self._chain_text_for_forbidden_recall(chain)
        hit = self._forbidden_recall_hit(text)
        if not hit:
            return
        logger.warning(
            "待发送消息命中违禁词，已拦截发送: word=%s session=%s",
            _single_line(hit, 40),
            _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
        )
        self._suppress_outbound_reply(
            event,
            source="发送前拦截",
            reason="待发送消息命中屏蔽词",
            history_note="[本轮未发送：待发送消息命中屏蔽词]",
            detail=hit,
            level="warn",
        )

    @filter.on_decorating_result()
    @_multi_persona_event_context
    async def suppress_framework_error_leak_before_send(self, event: AstrMessageEvent, *args, **kwargs):
        """还原本插件复核评语泄漏的正文。"""
        if self is None or not self.enabled:
            return
        result = event.get_result()
        chain = list(getattr(result, "chain", []) or []) if result is not None else []
        if not chain or any(not isinstance(comp, Plain) for comp in chain):
            return
        self._restore_response_review_meta_leak_before_send(event, chain)

    @filter.on_decorating_result()
    @_multi_persona_event_context
    async def rewrite_atrelay_delivery_receipt_before_send(self, event: AstrMessageEvent, *args, **kwargs):
        if self is None or not self.enabled:
            return
        await self._rewrite_atrelay_delivery_receipt_before_send(event)
