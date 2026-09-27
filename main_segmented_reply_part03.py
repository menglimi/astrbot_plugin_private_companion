# -*- coding: utf-8 -*-
"""PrivateCompanionPluginSegmentedReplyPart03Mixin。

由 tools/split_mixin_domain.py 从 main_segmented_reply.py 机械抽取（2 个方法 + 0 个模块级名字 + 0 个类级赋值 / 333 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPluginSegmentedReplyMixin）。
"""
from __future__ import annotations

from .main_segmented_reply_shared import logger
from .main_segmented_reply_shared import Any
from .main_segmented_reply_shared import AstrMessageEvent
from .main_segmented_reply_shared import Plain
from .main_segmented_reply_shared import _single_line
from .main_segmented_reply_shared import asyncio
from .main_segmented_reply_shared import re
from .main_segmented_reply_shared import runtime_persona_setting



class PrivateCompanionPluginSegmentedReplyPart03Mixin:
    """PrivateCompanionPluginSegmentedReplyPart03Mixin（从 PrivateCompanionPluginSegmentedReplyMixin 拆出）。"""


    async def _send_segmented_llm_chain_remainder(
        self,
        event: AstrMessageEvent,
        chunks: list[list[Any]],
        *,
        previous_segment: str = "",
        source: str = "",
        started_at: float | None = None,
    ) -> None:
        """后台补发被动分段的剩余组件片段；只拆文本，媒体组件保持原子发送。"""
        prev = previous_segment
        total = len([item for item in chunks if item])
        sent_index = 0
        case_id = _single_line(getattr(event, "_private_companion_daily_review_case_id", ""), 20)
        proactive_delivery_umo = _single_line(
            getattr(event, "_private_companion_proactive_delivery_umo", ""),
            240,
        )
        scope = self._event_scope_key(event)
        async with self._segmented_remainder_lock(scope):
            for chunk in chunks:
                if not chunk:
                    continue
                sent_index += 1
                try:
                    preview = self._segmented_chunk_log_text(chunk)
                    outbound_chunk = chunk
                    drift_reason = self._segmented_remainder_context_drift_reason(
                        event,
                        previous_text=prev,
                        next_text=preview,
                        source=source,
                    )
                    if drift_reason:
                        if case_id:
                            self._update_daily_review_case(
                                case_id,
                                outcome="incomplete",
                                signals={"stop_reason": drift_reason, "segments_expected": total + 1, "segments_sent": sent_index},
                            )
                        logger.info(
                            "分段剩余组件疑似上下文割裂，停止发送: source=%s reason=%s sent=%s/%s prev=%s next=%s",
                            source or "unknown",
                            drift_reason,
                            max(0, sent_index - 1),
                            total,
                            _single_line(prev, 120),
                            _single_line(preview, 120),
                        )
                        return
                    wait_for = prev or preview
                    delay = await self._calc_segmented_proactive_interval(wait_for, event=event)
                    if delay > 0:
                        await asyncio.sleep(delay)
                    recalled_message_id = await self._should_cancel_reply_for_missing_or_recalled_trigger(event)
                    if recalled_message_id:
                        if case_id:
                            self._update_daily_review_case(
                                case_id,
                                outcome="incomplete",
                                signals={"stop_reason": "trigger_recalled", "segments_expected": total + 1, "segments_sent": sent_index},
                            )
                        logger.info(
                            "触发消息已撤回或发送前不可见，停止发送分段剩余组件: source=%s message_id=%s sent=%s/%s",
                            source or "unknown",
                            recalled_message_id,
                            max(0, sent_index - 1),
                            total,
                        )
                        return
                    if chunk and all(isinstance(comp, Plain) for comp in chunk):
                        normalized_segment = "".join(str(getattr(comp, "text", "") or "") for comp in chunk).strip()
                        normalizer = getattr(self, "_normalize_tts_tags", None)
                        if callable(normalizer) and re.search(r"</?(?:pc[_-]?tts|t{2,}s)\b", normalized_segment, flags=re.IGNORECASE):
                            try:
                                normalized_segment = str(normalizer(normalized_segment) or normalized_segment).strip()
                            except Exception:
                                pass
                        if (
                            bool(runtime_persona_setting(self, 'enable_tts_enhancement', False))
                            and re.search(r"<tts\b[^>]*>.*?</tts>", normalized_segment, flags=re.IGNORECASE | re.DOTALL)
                        ):
                            processor = getattr(self, "_process_tts_tags", None)
                            if callable(processor):
                                fallback_plain = re.sub(r"</?(?:pc[_-]?tts|t{2,}s)\b[^>]*>", "", normalized_segment, flags=re.IGNORECASE).strip()
                                processed_chunk = await processor(normalized_segment, event, fallback_plain=fallback_plain)
                                if processed_chunk:
                                    outbound_chunk = processed_chunk
                        elif re.search(r"</?(?:pc[_-]?tts|t{2,}s)\b", normalized_segment, flags=re.IGNORECASE):
                            cleaned = re.sub(r"</?(?:pc[_-]?tts|t{2,}s)\b[^>]*>", "", normalized_segment, flags=re.IGNORECASE).strip()
                            outbound_chunk = [Plain(cleaned)] if cleaned else []
                    if not outbound_chunk:
                        continue
                    sanitized_chunk: list[Any] = []
                    leaked_tools: list[str] = []
                    for component in outbound_chunk:
                        if not isinstance(component, Plain):
                            sanitized_chunk.append(component)
                            continue
                        original_text = str(getattr(component, "text", "") or "")
                        visible_text = self._sanitize_segmented_plain_text(event, original_text)
                        cleaned_text, calls = self._strip_plaintext_tool_call_envelopes(
                            visible_text
                        )
                        leaked_tools.extend(str(item.get("name") or "") for item in calls)
                        if cleaned_text:
                            sanitized_chunk.append(
                                Plain(cleaned_text)
                                if calls or cleaned_text != original_text else component
                            )
                    if leaked_tools:
                        logger.warning(
                            "分段组件发送前已移除明文工具调用: tools=%s",
                            ",".join(leaked_tools),
                        )
                    outbound_chunk = sanitized_chunk
                    if not outbound_chunk:
                        continue
                    hit = self._forbidden_recall_hit(self._chain_text_for_forbidden_recall(outbound_chunk))
                    if hit:
                        if case_id:
                            self._update_daily_review_case(
                                case_id,
                                outcome="incomplete",
                                signals={"stop_reason": "forbidden_recall", "segments_expected": total + 1, "segments_sent": sent_index},
                            )
                        logger.warning("分段剩余组件命中违禁词，停止发送: word=%s", _single_line(hit, 40))
                        return
                    delivery_path = await self._send_segmented_remainder_chain(
                        event,
                        outbound_chunk,
                    )
                    if case_id:
                        self._update_daily_review_case(
                            case_id,
                            append_output=self._segmented_chunk_log_text(outbound_chunk),
                            outcome="delivered" if sent_index >= total else "delivery_pending",
                            signals={"segments_expected": total + 1, "segments_sent": sent_index + 1},
                        )
                    logger.info(
                        "分段 LLM 剩余组件已发送: source=%s delivery=%s index=%s/%s preview=%s",
                        source or "unknown",
                        delivery_path,
                        sent_index,
                        total,
                        _single_line(preview, 120),
                    )
                    prev = preview
                except asyncio.CancelledError:
                    if case_id:
                        self._update_daily_review_case(
                            case_id,
                            outcome="incomplete",
                            signals={"stop_reason": "task_cancelled", "segments_expected": total + 1, "segments_sent": sent_index},
                        )
                    raise
                except Exception as exc:
                    if (
                        str(getattr(event, "_private_companion_external_proactive_source", "") or "")
                        == "proactive_chat"
                        or proactive_delivery_umo
                    ):
                        if case_id:
                            self._update_daily_review_case(
                                case_id,
                                outcome="delivery_failed",
                                signals={"segments_expected": total + 1, "segments_sent": sent_index},
                            )
                        logger.warning(
                            "主动分段 LLM 剩余组件发送失败: source=%s error=%s",
                            source or "unknown",
                            _single_line(exc, 160),
                            exc_info=True,
                        )
                        return
                    try:
                        if not self._event_can_deliver_directly(event):
                            sender = getattr(self, "_send_chain_components", None)
                            fallback_umo = _single_line(
                                getattr(event, "unified_msg_origin", ""),
                                240,
                            )
                            if not fallback_umo or not callable(sender):
                                raise RuntimeError("被动分段补发缺少可用的平台发送入口")
                            accepted = await sender(
                                fallback_umo,
                                list(outbound_chunk),
                                apply_decorating_hooks=False,
                            )
                            if not accepted:
                                raise RuntimeError("被动分段补发未被平台接受")
                        else:
                            await event.send(
                                self._segmented_result_from_chain(event, outbound_chunk)
                            )
                        if case_id:
                            self._update_daily_review_case(
                                case_id,
                                append_output=self._segmented_chunk_log_text(outbound_chunk),
                                outcome="delivered" if sent_index >= total else "delivery_pending",
                                signals={"segments_expected": total + 1, "segments_sent": sent_index + 1},
                            )
                        logger.info(
                            "分段 LLM 剩余组件已发送: source=%s index=%s/%s preview=%s",
                            source or "unknown",
                            sent_index,
                            total,
                            _single_line(self._segmented_chunk_log_text(chunk), 120),
                        )
                        prev = self._segmented_chunk_log_text(chunk)
                    except Exception:
                        if case_id:
                            self._update_daily_review_case(
                                case_id,
                                outcome="delivery_failed",
                                signals={"segments_expected": total + 1, "segments_sent": sent_index},
                            )
                        logger.warning(
                            "分段 LLM 剩余组件发送失败: source=%s error=%s",
                            source or "unknown",
                            _single_line(exc, 160),
                            exc_info=True,
                        )
                        return

    async def _send_segmented_llm_reply_remainder(
        self,
        event: AstrMessageEvent,
        segments: list[str],
        *,
        previous_segment: str = "",
        source: str = "",
        started_at: float | None = None,
    ) -> None:
        """后台补发被动分段的剩余片段，避免阻塞主链首包。"""
        prev = previous_segment
        total = len([item for item in segments if str(item or "").strip()])
        sent_index = 0
        for segment in segments:
            segment = str(segment or "").strip()
            if not segment:
                continue
            segment, leaked_calls = self._strip_plaintext_tool_call_envelopes(segment)
            if leaked_calls:
                logger.warning(
                    "分段文本发送前已移除明文工具调用: tools=%s",
                    ",".join(str(item.get("name") or "") for item in leaked_calls),
                )
            if not segment:
                continue
            sent_index += 1
            try:
                drift_reason = self._segmented_remainder_context_drift_reason(
                    event,
                    previous_text=prev,
                    next_text=segment,
                    source=source,
                )
                if drift_reason:
                    logger.info(
                        "分段剩余片段疑似上下文割裂，停止发送: source=%s reason=%s sent=%s/%s prev=%s next=%s",
                        source or "unknown",
                        drift_reason,
                        max(0, sent_index - 1),
                        total,
                        _single_line(prev, 120),
                        _single_line(segment, 120),
                    )
                    return
                wait_for = prev or segment
                delay = await self._calc_segmented_proactive_interval(wait_for, event=event)
                if delay > 0:
                    await asyncio.sleep(delay)
                recalled_message_id = await self._should_cancel_reply_for_missing_or_recalled_trigger(event)
                if recalled_message_id:
                    logger.info(
                        "触发消息已撤回或发送前不可见，停止发送分段剩余片段: source=%s message_id=%s sent=%s/%s",
                        source or "unknown",
                        recalled_message_id,
                        max(0, sent_index - 1),
                        total,
                    )
                    return
                sent_tts_chain = False
                normalized_segment = segment
                normalizer = getattr(self, "_normalize_tts_tags", None)
                if callable(normalizer) and re.search(r"</?(?:pc[_-]?tts|t{2,}s)\b", normalized_segment, flags=re.IGNORECASE):
                    try:
                        normalized_segment = str(normalizer(normalized_segment) or normalized_segment).strip()
                    except Exception:
                        pass
                if (
                    bool(runtime_persona_setting(self, 'enable_tts_enhancement', False))
                    and re.search(r"<tts\b[^>]*>.*?</tts>", normalized_segment, flags=re.IGNORECASE | re.DOTALL)
                ):
                        processor = getattr(self, "_process_tts_tags", None)
                        if callable(processor):
                            fallback_plain = re.sub(r"</?(?:pc[_-]?tts|t{2,}s)\b[^>]*>", "", normalized_segment, flags=re.IGNORECASE).strip()
                            chain = await processor(normalized_segment, event, fallback_plain=fallback_plain)
                            if chain:
                                hit = self._forbidden_recall_hit(self._chain_text_for_forbidden_recall(chain))
                                if hit:
                                    logger.warning("分段 TTS 剩余片段命中违禁词，停止发送: word=%s", _single_line(hit, 40))
                                    return
                                try:
                                    await event.send(event.chain_result(chain))
                                except Exception:
                                    await event.send(self._build_result_from_chain(chain))
                                sent_tts_chain = True
                if not sent_tts_chain:
                    outbound = re.sub(r"</?(?:pc[_-]?tts|t{2,}s)\b[^>]*>", "", normalized_segment, flags=re.IGNORECASE).strip() or segment
                    hit = self._forbidden_recall_hit(outbound)
                    if hit:
                        logger.warning("分段剩余片段命中违禁词，停止发送: word=%s", _single_line(hit, 40))
                        return
                    await event.send(event.plain_result(outbound))
                logger.info(
                    "分段 LLM 剩余片段已发送: source=%s index=%s/%s preview=%s",
                    source or "unknown",
                    sent_index,
                    total,
                    _single_line(segment, 120),
                )
                prev = segment
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "分段 LLM 剩余片段发送失败: source=%s error=%s",
                    source or "unknown",
                    _single_line(exc, 160),
                    exc_info=True,
                )
                return
