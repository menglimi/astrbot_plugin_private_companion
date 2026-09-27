# -*- coding: utf-8 -*-
"""TtsEnhancementChainSendMixin。

由 tools/split_mixin_domain.py 从 tts_enhancement.py 机械抽取（11 个方法 + 0 个模块级名字 + 0 个类级赋值 / 834 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 TtsEnhancementMixin）。
"""
from __future__ import annotations

import asyncio
import re
import time
from .helpers import (
    _has_history_media_marker,
    _safe_int,
    _single_line,
    _strip_history_media_markers,
    _strip_nonstandard_chat_control_tags,
)
from .persona_config import runtime_persona_setting
from .segmented_message import (
    component_kind,
    component_order_from_owner,
    component_strategies_from_owner,
    plan_component_chunks,
)
from .tts_enhancement_shared import logger
from astrbot.core.message.message_event_result import ResultContentType
from typing import Any
from .tts_enhancement_shared import Plain
from .tts_enhancement_shared import Record



class TtsEnhancementChainSendMixin:
    """TtsEnhancementChainSendMixin（从 TtsEnhancementMixin 拆出）。"""


    async def finalize_outbound_tts_markup_guard(self, event: Any) -> None:
        """Last-resort guard so raw <tts> tags never reach the chat surface."""
        if not getattr(self, "enabled", False):
            return
        if not bool(getattr(event, "_private_companion_tts_request_applied", False)):
            return
        result = event.get_result()
        try:
            if result is not None and bool(result.is_llm_result()):
                result.set_result_content_type(ResultContentType.GENERAL_RESULT)
                logger.debug(
                    "插件已接管本轮 TTS，阻止 AstrBot 官方 TTS 二次处理: session=%s",
                    _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                )
        except Exception:
            pass
        chain = list(getattr(result, "chain", []) or []) if result is not None else []
        if not chain:
            return
        if any(isinstance(comp, Record) for comp in chain):
            cleaned_chain = await self._sanitize_outbound_tts_chain_without_event(
                chain,
                umo=str(getattr(event, "unified_msg_origin", "") or ""),
            )
            if cleaned_chain != chain:
                event.set_result(self._build_result_from_chain(cleaned_chain))
            return
        if bool(getattr(event, "_private_companion_skip_tts_enhancement", False)) or any(
            bool(getattr(comp, "_private_companion_skip_tts_enhancement", False))
            for comp in chain
        ):
            return
        plain_parts = [str(getattr(comp, "text", "") or "") for comp in chain if isinstance(comp, Plain)]
        if not plain_parts:
            return
        text = self._restore_protected_tts_blocks("".join(plain_parts), event).strip()
        if not re.search(r"</?(?:pc[_-]?tts|t{2,}s)\b", text, flags=re.IGNORECASE):
            return
        normalized = self._normalize_tts_tags(text)
        normalized_before_safety_drop = normalized
        normalized, dropped_safety_voice = self._drop_tts_provider_safety_blocks(normalized)
        if dropped_safety_voice and not normalized:
            fallback_text = self._tts_plain_markup_fallback_text(
                normalized_before_safety_drop
            )
            event.set_result(
                self._build_result_from_chain(
                    [Plain(fallback_text)] if fallback_text else []
                )
            )
            logger.warning(
                "发送前终检已丢弃仅包含提供商安全回执的语音块: session=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            )
            return
        feature_enabled = getattr(self, "_feature_enabled_or_temp_unlocked", None)
        tts_enabled = feature_enabled("enable_tts_enhancement") if callable(feature_enabled) else self._tts_setting("enable_tts_enhancement", False)
        new_chain: list[Any] = []
        if (
            tts_enabled
            and self._tts_setting("tts_generation_mode", "fast_tag") != "postprocess"
            and re.search(r"<tts\b[^>]*>.*?</tts>", normalized, flags=re.IGNORECASE | re.DOTALL)
        ):
            normalized, full_scope_fallback = self._enforce_full_tts_scope_markup(
                normalized,
                event=event,
            )
            new_chain = await self._process_tts_tags(
                normalized,
                event,
                fallback_plain=full_scope_fallback,
            )
        if not new_chain:
            fallback_text = self._tts_visible_fallback_text(
                normalized,
                event=event,
            ) or self._tts_plain_markup_fallback_text(normalized)
            new_chain = [Plain(fallback_text)] if fallback_text else []
        if len(plain_parts) != len(chain):
            non_plain_tail = [comp for comp in chain if not isinstance(comp, Plain)]
            if non_plain_tail:
                new_chain = list(new_chain) + non_plain_tail
        new_chain = self._tts_record_first_visible_last_chain(new_chain)
        logger.warning(
            "发送前终检拦截残留 TTS 标签: session=%s preview=%s",
            _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            _single_line(self._tts_chain_log_text(new_chain), 160),
        )
        event.set_result(self._build_result_from_chain(new_chain))

    async def _sanitize_outbound_tts_chain_without_event(self, chain: list[Any], *, umo: str = "") -> list[Any]:
        if not bool(runtime_persona_setting(self, "enable_framework_error_leak_guard", True)):
            return chain
        if not chain:
            return chain
        changed = False
        cleaned_chain: list[Any] = []
        for comp in chain:
            if not isinstance(comp, Plain):
                cleaned_chain.append(comp)
                continue
            original = str(getattr(comp, "text", "") or "")
            if _has_history_media_marker(original):
                cleaned_history = _strip_history_media_markers(original)
                if cleaned_history:
                    leading_whitespace = original[: len(original) - len(original.lstrip())]
                    trailing_whitespace = original[len(original.rstrip()) :]
                    cleaned_control = f"{leading_whitespace}{cleaned_history}{trailing_whitespace}"
                else:
                    cleaned_control = ""
            else:
                cleaned_control = original
            tts_enabled = bool(runtime_persona_setting(self, "enable_tts_enhancement", False))
            cleaned_control = _strip_nonstandard_chat_control_tags(cleaned_control, tts_enabled=tts_enabled)
            if tts_enabled:
                cleaned_control = self._strip_visible_tts_emotion_cues(cleaned_control)
            has_tts_markup = re.search(r"</?(?:pc[_-]?tts|t{2,}s)\b", cleaned_control, flags=re.IGNORECASE)
            if not has_tts_markup:
                if cleaned_control != original:
                    changed = True
                if cleaned_control:
                    cleaned_chain.append(Plain(cleaned_control) if cleaned_control != original else comp)
                continue
            changed = True
            normalized = self._normalize_tts_tags(cleaned_control)
            fallback_text = self._tts_visible_fallback_text(normalized) or self._strip_any_tts_markup(normalized)
            fallback_text = self._sanitize_tts_visible_text(fallback_text)
            if fallback_text:
                cleaned_chain.append(Plain(fallback_text))
        if changed:
            logger.warning(
                "外发兜底清理残留内部控制标记: umo=%s preview=%s",
                _single_line(umo, 120) or "unknown",
                _single_line(self._tts_chain_log_text(cleaned_chain), 160),
            )
        return cleaned_chain

    @staticmethod
    def _without_reply_components(chain: list[Any]) -> list[Any]:
        """Return a copy without quote components across AstrBot versions."""
        return [
            component
            for component in list(chain or [])
            if component.__class__.__name__.lower() != "reply"
        ]

    def _suppress_reply_components_for_voice_chain(self, chain: list[Any]) -> list[Any]:
        """Drop quotes only when a voice chain has no visible text companion.

        A quote on a mixed voice/text reply remains meaningful to the platform
        and to downstream image/forward/vision consumers, so it must be moved
        to the text chunk instead of being removed merely because a ``Record``
        is present.
        """
        working_chain = list(chain or [])
        if not any(isinstance(component, Record) for component in working_chain):
            return working_chain
        if any(
            isinstance(component, Plain)
            and bool(str(getattr(component, "text", "") or "").strip())
            for component in working_chain
        ):
            return working_chain
        return self._without_reply_components(working_chain)

    def _split_tts_chain_for_ordered_send(self, chain: list[Any]) -> list[list[Any]]:
        chain = self._suppress_reply_components_for_voice_chain(chain)
        has_record = False
        has_visible = False
        for comp in chain:
            if isinstance(comp, Record):
                has_record = True
            else:
                has_visible = True
        if not has_record or not has_visible:
            return [chain]
        chunks, _changed, _split_changed, _full_text = plan_component_chunks(
            chain,
            plain_type=Plain,
            split_text=lambda text: [text],
            strategies=component_strategies_from_owner(self),
            component_order=component_order_from_owner(self),
            classify=component_kind,
        )
        return chunks or [chain]

    def _tts_record_first_visible_last_chain(self, chain: list[Any]) -> list[Any]:
        if not chain or not any(isinstance(comp, Record) for comp in chain):
            return chain
        records: list[Any] = []
        others: list[Any] = []
        visible_marked: list[str] = []
        visible_plain: list[str] = []
        for comp in chain:
            if isinstance(comp, Record):
                records.append(comp)
                continue
            if isinstance(comp, Plain):
                text = str(getattr(comp, "text", "") or "").strip()
                if not text:
                    continue
                if bool(getattr(comp, "_private_companion_tts_visible_text", False)):
                    visible_marked.append(text)
                else:
                    visible_plain.append(text)
                continue
            others.append(comp)

        def append_unique(target: list[str], value: str) -> None:
            value = self._sanitize_tts_visible_text(value, max_chars=1000)
            if not value:
                return
            normalized = re.sub(r"\s+", "", value)
            if any(normalized == re.sub(r"\s+", "", item) for item in target):
                return
            if any(normalized and normalized in re.sub(r"\s+", "", item) for item in target):
                return
            target[:] = [
                item
                for item in target
                if re.sub(r"\s+", "", item) not in normalized
            ]
            target.append(value)

        visible_lines: list[str] = []
        preferred_visible = visible_marked if visible_marked else visible_plain
        fallback_visible = visible_plain if visible_marked else []
        for text in preferred_visible:
            append_unique(visible_lines, text)
        for text in fallback_visible:
            append_unique(visible_lines, text)
        normalized_chain = list(others) + list(records)
        visible = "\n".join(visible_lines).strip()
        if visible:
            visible_comp = self._mark_tts_visible_plain(visible, max_chars=1000)
            if visible_comp is not None:
                normalized_chain.append(visible_comp)
        return normalized_chain

    @staticmethod
    def _replace_plain_components_preserving_order(
        source_chain: list[Any],
        replacement: list[Any],
    ) -> list[Any]:
        rebuilt: list[Any] = []
        inserted = False
        for component in source_chain:
            if isinstance(component, Plain):
                if not inserted:
                    rebuilt.extend(replacement)
                    inserted = True
                continue
            rebuilt.append(component)
        if not inserted:
            rebuilt.extend(replacement)
        return rebuilt

    def _tts_segment_plain_chunk_for_ordered_send(self, event: Any, chunk: list[Any]) -> list[list[Any]]:
        if not chunk or any(not isinstance(comp, Plain) for comp in chunk):
            return [chunk]
        reaction_intent = getattr(
            event,
            "_private_companion_reaction_expression_intent",
            None,
        )
        if isinstance(reaction_intent, dict) and reaction_intent:
            return [chunk]
        text = "".join(str(getattr(comp, "text", "") or "") for comp in chunk).strip()
        if not text:
            return []

        source_segments = getattr(event, "_private_companion_tts_source_plain_segments", ())
        if isinstance(source_segments, (list, tuple)) and len(source_segments) > 1:
            segment_limit = self._tts_complete_text_limit(
                "".join(str(item or "") for item in source_segments),
                minimum=1000,
            )
            cleaned_segments = [
                self._sanitize_tts_visible_text(item, max_chars=segment_limit)
                for item in source_segments
            ]
            cleaned_segments = [item for item in cleaned_segments if item]
            cleaned_visible = self._sanitize_tts_visible_text(text, max_chars=segment_limit)

            def visible_signature(value: str) -> str:
                return re.sub(r"\s+", "", str(value or ""))

            if (
                len(cleaned_segments) > 1
                and visible_signature("".join(cleaned_segments)) == visible_signature(cleaned_visible)
            ):
                logger.info(
                    "TTS 完整合成后恢复上游正文分段: session=%s segments=%s first=%s",
                    _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                    len(cleaned_segments),
                    _single_line(cleaned_segments[0], 100),
                )
                restored_chunks: list[list[Any]] = []
                for segment in cleaned_segments:
                    visible_part = self._mark_tts_visible_plain(segment, max_chars=segment_limit)
                    if visible_part is not None:
                        restored_chunks.append([visible_part])
                if restored_chunks:
                    return restored_chunks

        scope_getter = getattr(self, "_segmented_setting", None)
        segmented_scope = (
            scope_getter("scope", event=event, default="proactive_only")
            if callable(scope_getter)
            else self._tts_setting("segmented_proactive_scope", "proactive_only")
        )
        if not (
            bool(self._tts_setting("enable_segmented_proactive_reply", False))
            and str(segmented_scope or "") == "all_llm"
        ):
            return [chunk]
        scope_checker = getattr(self, "_segmented_scope_allows_event", None)
        if callable(scope_checker):
            try:
                if not scope_checker(event):
                    return [chunk]
            except Exception:
                return [chunk]
        platform_checker = getattr(self, "_segmented_platform_allows", None)
        if callable(platform_checker):
            try:
                if not platform_checker(event=event):
                    return [chunk]
            except Exception:
                return [chunk]
        original_text = text
        tool_cleaner = getattr(self, "_strip_plaintext_tool_call_envelopes", None)
        if callable(tool_cleaner):
            cleaned_text, leaked_calls = tool_cleaner(text)
            if leaked_calls:
                logger.warning(
                    "TTS 分块前已移除明文工具调用: session=%s tools=%s",
                    _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                    ",".join(str(item.get("name") or "") for item in leaked_calls),
                )
                text = cleaned_text
                if not text:
                    return []
        is_tts_visible_text = any(bool(getattr(comp, "_private_companion_tts_visible_text", False)) for comp in chunk)
        if is_tts_visible_text:
            cleaned_visible = self._sanitize_tts_visible_text(text)
            if not cleaned_visible:
                return []
            text = cleaned_visible
        if (
            not is_tts_visible_text
            and self._tts_voice_language_for_event(event) != "zh"
            and not self._tts_visible_text_is_allowed_after_voice(text)
        ):
            chinese_text = self._tts_chinese_visible_fallback_from_mixed(text)
            if chinese_text:
                logger.warning(
                    "TTS 后置文本混有朗读语种,已仅保留中文释义: session=%s text=%s",
                    _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                    _single_line(chinese_text, 120),
                )
                text = chinese_text
            else:
                logger.warning(
                    "TTS 后置文本不是中文释义,已跳过发送: session=%s text=%s",
                    _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                    _single_line(text, 120),
                )
                return []
        splitter = getattr(self, "_split_proactive_text", None)
        llm_splitter = getattr(self, "_split_llm_controlled_text_for_event", None)
        llm_allowed = getattr(self, "_llm_controlled_segmenting_allowed", None)
        if (
            callable(llm_splitter)
            and callable(llm_allowed)
            and bool(llm_allowed(event))
        ):
            splitter = llm_splitter
        if not callable(splitter):
            visible_part = self._mark_tts_visible_plain(text) if is_tts_visible_text else Plain(text)
            return [[visible_part]] if visible_part is not None else []
        try:
            try:
                if splitter is llm_splitter:
                    split_result = splitter(event, text)
                else:
                    split_result = splitter(text, event=event)
            except TypeError:
                # Preserve compatibility with lightweight test/plugin overrides
                # that still expose the original one-argument splitter contract.
                split_result = splitter(text)
            segments = [item for item in split_result if str(item or "").strip()]
        except Exception as exc:
            logger.debug("TTS 后置文本分段失败,保持原样: %s", _single_line(exc, 120))
            return [chunk]
        if len(segments) <= 1:
            cleaned = segments[0] if segments else text
            if not cleaned:
                return []
            if is_tts_visible_text:
                visible_part = self._mark_tts_visible_plain(cleaned)
                return [[visible_part]] if visible_part is not None else []
            return [[Plain(cleaned)]] if cleaned != text or text != original_text else [chunk]
        logger.info(
            "TTS 后置文本按分段规则拆分: session=%s segments=%s first=%s",
            _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            len(segments),
            _single_line(segments[0], 100),
        )
        if is_tts_visible_text:
            visible_chunks: list[list[Any]] = []
            for segment in segments:
                visible_part = self._mark_tts_visible_plain(segment)
                if visible_part is not None:
                    visible_chunks.append([visible_part])
            return visible_chunks
        return [[Plain(segment)] for segment in segments]

    async def _send_tts_chain_chunks_after_first(
        self,
        event: Any,
        chunks: list[list[Any]],
        *,
        started_at: float | None = None,
        primary_delivery_confirmed: bool = False,
    ) -> None:
        if not chunks:
            return
        expanded_chunks: list[list[Any]] = []
        for chunk in chunks:
            expanded_chunks.extend(self._tts_segment_plain_chunk_for_ordered_send(event, chunk))
        outbound_umo = _single_line(
            getattr(event, "unified_msg_origin", ""),
            160,
        ) or "unknown"
        sanitized_chunks: list[list[Any]] = []
        for chunk in expanded_chunks:
            cleaned_chunk = await self._sanitize_outbound_tts_chain_without_event(
                chunk,
                umo=outbound_umo,
            )
            if not cleaned_chunk:
                continue
            has_visible_plain = any(
                isinstance(component, Plain)
                and bool(str(getattr(component, "text", "") or "").strip())
                for component in cleaned_chunk
            )
            has_delivery_component = any(
                not isinstance(component, Plain)
                and component_kind(component) not in {"at", "reply"}
                for component in cleaned_chunk
            )
            source_had_plain = any(isinstance(component, Plain) for component in chunk)
            if source_had_plain and not has_visible_plain and not has_delivery_component:
                logger.warning(
                    "TTS 尾段清理后仅剩孤立上下文组件,已跳过: session=%s",
                    outbound_umo,
                )
                continue
            sanitized_chunks.append(cleaned_chunk)
        expanded_chunks = sanitized_chunks
        case_id = _single_line(getattr(event, "_private_companion_daily_review_case_id", ""), 20)
        case_updater = getattr(self, "_update_daily_review_case", None)
        if not expanded_chunks:
            logger.info(
                "TTS 尾段清理后无可发送内容: session=%s",
                outbound_umo,
            )
            if case_id and callable(case_updater):
                case_updater(
                    case_id,
                    outcome="delivered",
                    signals={
                        "segments_expected": 1,
                        "segments_sent": 1,
                        "visible_text_complete": True,
                    },
                )
            return
        total_chunks = len(expanded_chunks) + 1
        sent_chunks = 1
        scope_getter = getattr(self, "_event_scope_key", None)
        scope = ""
        if callable(scope_getter):
            try:
                scope = _single_line(scope_getter(event), 160)
            except Exception:
                scope = ""
        if not scope:
            scope = _single_line(getattr(event, "unified_msg_origin", ""), 160) or "unknown"
        lock_getter = getattr(self, "_segmented_remainder_lock", None)
        lock = lock_getter(scope) if callable(lock_getter) else asyncio.Lock()
        previous_text = ""
        turn_generation = _safe_int(
            getattr(event, "_private_companion_reply_turn_generation", 0),
            0,
            0,
        )
        generation_checker = getattr(self, "_reply_turn_is_current", None)
        async with lock:
            for chunk in expanded_chunks:
                if not chunk:
                    continue
                if (
                    not primary_delivery_confirmed
                    and callable(generation_checker)
                    and not generation_checker(scope, turn_generation)
                ):
                    logger.info(
                        "新回合已到达，停止旧 TTS 尾段: session=%s sent=%s/%s",
                        _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                        sent_chunks,
                        total_chunks,
                    )
                    return
                delay = 0.45
                if previous_text and len(expanded_chunks) > 1:
                    calc_interval = getattr(self, "_calc_segmented_proactive_interval", None)
                    if callable(calc_interval):
                        try:
                            try:
                                interval_result = await calc_interval(previous_text, event=event)
                            except TypeError:
                                interval_result = await calc_interval(previous_text)
                            delay = max(0.45, float(interval_result))
                        except Exception:
                            delay = 0.45
                await asyncio.sleep(delay)
                if (
                    not primary_delivery_confirmed
                    and callable(generation_checker)
                    and not generation_checker(scope, turn_generation)
                ):
                    logger.info(
                        "等待期间收到新回合，停止旧 TTS 尾段: session=%s",
                        _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                    )
                    return
                proactive_umo = _single_line(
                    getattr(event, "_private_companion_proactive_delivery_umo", ""),
                    180,
                )
                try:
                    proactive_sender = getattr(self, "_send_chain_components", None)
                    if proactive_umo and callable(proactive_sender):
                        await proactive_sender(
                            proactive_umo,
                            chunk,
                            apply_decorating_hooks=False,
                        )
                    else:
                        await event.send(event.chain_result(chunk))
                    logger.info(
                        "TTS 分块后台补发完成: session=%s %s",
                        _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                        self._tts_chain_log_text(chunk),
                    )
                    sent_chunks += 1
                    if case_id and callable(case_updater):
                        case_updater(
                            case_id,
                            append_output=self._tts_chain_log_text(chunk),
                            outcome="delivered" if sent_chunks >= total_chunks else "delivery_pending",
                            signals={
                                "segments_expected": total_chunks,
                                "segments_sent": sent_chunks,
                                "visible_text_complete": sent_chunks >= total_chunks,
                            },
                        )
                except Exception as exc:
                    if proactive_umo:
                        if case_id and callable(case_updater):
                            case_updater(
                                case_id,
                                outcome="delivery_failed",
                                signals={"segments_expected": total_chunks, "segments_sent": sent_chunks},
                            )
                        logger.warning(
                            "TTS 主动消息中文正文补发失败: session=%s error=%s %s",
                            proactive_umo,
                            _single_line(exc, 160),
                            self._tts_chain_log_text(chunk),
                        )
                        return
                    try:
                        await event.send(self._build_result_from_chain(chunk))
                        logger.info(
                            "TTS 分块后台补发完成: session=%s %s",
                            _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                            self._tts_chain_log_text(chunk),
                        )
                        sent_chunks += 1
                        if case_id and callable(case_updater):
                            case_updater(
                                case_id,
                                append_output=self._tts_chain_log_text(chunk),
                                outcome="delivered" if sent_chunks >= total_chunks else "delivery_pending",
                                signals={
                                    "segments_expected": total_chunks,
                                    "segments_sent": sent_chunks,
                                    "visible_text_complete": sent_chunks >= total_chunks,
                                },
                            )
                    except Exception:
                        if case_id and callable(case_updater):
                            case_updater(
                                case_id,
                                outcome="delivery_failed",
                                signals={
                                    "segments_expected": total_chunks,
                                    "segments_sent": sent_chunks,
                                    "visible_text_complete": False,
                                },
                            )
                        logger.warning("TTS 分块后台补发失败: %s", _single_line(exc, 120))
                        return
                previous_text = " ".join(
                    str(getattr(comp, "text", "") or "").strip()
                    for comp in chunk
                    if isinstance(comp, Plain)
                ).strip() or previous_text

    async def _send_deferred_reaction_tts(
        self,
        event: Any,
        pending: dict[str, Any],
    ) -> None:
        scope_getter = getattr(self, "_event_scope_key", None)
        scope = ""
        if callable(scope_getter):
            try:
                scope = _single_line(scope_getter(event), 160)
            except Exception:
                scope = ""
        scope = scope or _single_line(getattr(event, "unified_msg_origin", ""), 160)
        generation_checker = getattr(self, "_reply_turn_is_current", None)
        if callable(generation_checker) and not generation_checker(
            scope,
            pending.get("turn_generation", 0),
        ):
            logger.info(
                "新回合已到达，跳过旧表情 TTS: session=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            )
            return
        normalized = str(pending.get("normalized") or "").strip()
        fallback_plain = self._sanitize_tts_visible_text(
            pending.get("fallback_plain"),
            max_chars=1600,
        )
        if not normalized:
            return

        setattr(event, "_private_companion_deferred_reaction_tts_active", True)
        try:
            if self._tts_setting("tts_generation_mode", "fast_tag") == "postprocess":
                source_text = self._sanitize_tts_visible_text(
                    self._strip_any_tts_markup(normalized),
                    max_chars=1600,
                )
                generated = (
                    await self._maybe_convert_plain_reply_to_tts(source_text, event)
                    if source_text
                    else []
                )
            elif "<tts>" in normalized.lower() and "</tts>" in normalized.lower():
                tagged, full_scope_fallback = self._enforce_full_tts_scope_markup(
                    normalized,
                    source_text=fallback_plain,
                    event=event,
                )
                generated = await self._process_tts_tags(
                    tagged,
                    event,
                    fallback_plain=full_scope_fallback or fallback_plain,
                )
            else:
                generated = await self._maybe_convert_plain_reply_to_tts(
                    normalized,
                    event,
                )
        finally:
            try:
                delattr(event, "_private_companion_deferred_reaction_tts_active")
            except Exception:
                pass

        records = [component for component in generated if isinstance(component, Record)]
        if not records:
            logger.info(
                "表情表达后台 TTS 未生成语音,正文与表情已保持送达: session=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120)
                or "unknown",
            )
            return
        proactive_umo = _single_line(
            getattr(event, "_private_companion_proactive_delivery_umo", ""),
            180,
        )
        try:
            proactive_sender = getattr(self, "_send_chain_components", None)
            if proactive_umo and callable(proactive_sender):
                sent = await proactive_sender(
                    proactive_umo,
                    records,
                    apply_decorating_hooks=False,
                )
            else:
                sender = getattr(event, "send", None)
                result_builder = getattr(event, "chain_result", None)
                if not callable(sender) or not callable(result_builder):
                    return
                sent = await sender(result_builder(records))
            if sent is False:
                return
        except Exception as exc:
            logger.warning(
                "表情表达后台语音投递失败: error_type=%s",
                type(exc).__name__,
            )
            return

        self._mark_tts_session_sent(event)
        session = str(getattr(event, "unified_msg_origin", "") or "")
        if session:
            state = getattr(self, "_tts_auto_voice_last_at", None)
            if not isinstance(state, dict):
                state = {}
                self._tts_auto_voice_last_at = state
            state[session] = time.time()
        logger.info(
            "表情表达后台语音已在正文和图片后单独送达: session=%s records=%s",
            _single_line(session, 120) or "unknown",
            len(records),
        )

    async def _maybe_convert_plain_reply_to_tts(self, text: str, event: Any) -> list[Any]:
        mode = self._tts_setting("tts_generation_mode", "fast_tag")
        if self._tts_text_is_provider_safety_refusal(text):
            logger.info(
                "提供商安全回执保持纯文字,不进入 TTS: session=%s preview=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                _single_line(text, 140),
            )
            return []
        visible_override, suppress_visible, conversion_source, skip_conversion = self._tts_proactive_segment_visible_policy(event)
        if skip_conversion:
            logger.info(
                "主动分段 TTS 只在首段判定,后续分段保持文字: session=%s text=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                _single_line(text, 100),
            )
            return []
        user_requested_tts = self._event_explicitly_requests_tts(event)
        if self._tts_functional_command_reason(event) and not user_requested_tts:
            return []
        strong_block_reason = self._tts_strong_constraint_block_reason(
            event,
            user_requested_tts=user_requested_tts,
            check_probability=False,
            reason="auto_convert_cooldown",
        )
        if strong_block_reason:
            self._set_tts_hard_block(event, strong_block_reason)
            return []
        should_convert = mode == "postprocess" or user_requested_tts
        reason = "explicit_request" if user_requested_tts else ("postprocess" if should_convert else "")
        if not should_convert:
            ok, reason = self._auto_voice_trigger_reason(text, event)
            should_convert = ok
        if not should_convert:
            return []
        if mode == "postprocess":
            probability_allowed = user_requested_tts or self._tts_trigger_probability_allows(event, reason=reason or mode)
            try:
                setattr(event, "_private_companion_tts_postprocess_probability_allowed", bool(probability_allowed))
            except Exception:
                pass
        else:
            probability_allowed = (
                user_requested_tts and not self._tts_strong_constraint_enabled()
            ) or self._tts_trigger_probability_allows(event, reason=reason or mode)
        if not probability_allowed:
            if self._tts_strong_constraint_enabled():
                self._set_tts_hard_block(event, "probability_miss")
            return []
        source_text = conversion_source or text
        full_scope = self._tts_setting("tts_conversion_scope", "partial") == "full"
        if mode == "fast_tag" and full_scope:
            # Full fast-tag conversion has one authoritative source: the complete
            # visible reply. Let the spoken-language pass translate it once instead
            # of calling a conversion model whose markup would be discarded below.
            converted = f"<tts>{source_text}</tts>"
        else:
            converted = await self._convert_text_to_tts_markup(
                source_text,
                event,
                full=full_scope,
            )
        if not converted:
            return []
        converted, full_scope_fallback = self._enforce_full_tts_scope_markup(
            converted,
            source_text=source_text,
            event=event,
            prefer_authored_voice=(mode == "postprocess"),
        )
        if visible_override or suppress_visible:
            try:
                setattr(event, "_private_companion_tts_visible_text_override", visible_override)
                setattr(event, "_private_companion_tts_visible_text_suppress", bool(suppress_visible))
            except Exception:
                pass
        fallback_plain = visible_override if visible_override else ("" if suppress_visible else (full_scope_fallback or source_text))
        try:
            chain = await self._process_tts_tags(converted, event, fallback_plain=fallback_plain)
        finally:
            if visible_override or suppress_visible:
                for attr in (
                    "_private_companion_tts_visible_text_override",
                    "_private_companion_tts_visible_text_suppress",
                ):
                    try:
                        delattr(event, attr)
                    except Exception:
                        pass
        if chain:
            session = str(getattr(event, "unified_msg_origin", "") or "")
            if not bool(
                getattr(
                    event,
                    "_private_companion_deferred_reaction_tts_active",
                    False,
                )
            ):
                self._tts_auto_voice_last_at[session] = time.time()
            logger.info(
                "TTS强化已转换纯文本回复: reason=%s session=%s %s",
                reason,
                _single_line(session, 80),
                self._tts_chain_log_text(chain),
            )
        return chain
