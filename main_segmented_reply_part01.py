# -*- coding: utf-8 -*-
"""PrivateCompanionPluginSegmentedReplyPart01Mixin。

由 tools/split_mixin_domain.py 从 main_segmented_reply.py 机械抽取（15 个方法 + 0 个模块级名字 + 0 个类级赋值 / 645 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPluginSegmentedReplyMixin）。
"""
from __future__ import annotations

from .main_segmented_reply_shared import logger
from .main_segmented_reply_shared import Any
from .main_segmented_reply_shared import AstrMessageEvent
from .main_segmented_reply_shared import Plain
from .main_segmented_reply_shared import Reply
from .main_segmented_reply_shared import _multi_persona_event_context
from .main_segmented_reply_shared import _safe_int
from .main_segmented_reply_shared import _single_line
from .main_segmented_reply_shared import _strip_chain_plain_thinking
from .main_segmented_reply_shared import has_fenced_llm_segment_marker
from .main_segmented_reply_shared import parse_llm_segment_control
from .main_segmented_reply_shared import re
from .main_segmented_reply_shared import runtime_persona_setting
from .main_segmented_reply_shared import sanitize_llm_segment_control_tokens
from .main_segmented_reply_shared import strip_llm_segment_marker_lines
from .main_segmented_reply_shared import time
from .main_segmented_reply_shared import filter



class PrivateCompanionPluginSegmentedReplyPart01Mixin:
    """PrivateCompanionPluginSegmentedReplyPart01Mixin（从 PrivateCompanionPluginSegmentedReplyMixin 拆出）。"""


    @filter.on_decorating_result(priority=100)
    @_multi_persona_event_context
    async def apply_segmented_llm_reply_scope(self, event: AstrMessageEvent, *args, **kwargs):
        """按回复范围与分段策略整理 LLM 输出，减少长回复和误引用。"""
        if self is None or not self.enabled:
            return
        external_proactive = (
            str(getattr(event, "_private_companion_external_proactive_source", "") or "")
            == "proactive_chat"
        )
        # Join Plain components so enabled tag cleanup can match split blocks.
        early_result = event.get_result()
        if early_result is not None:
            early_chain = list(getattr(early_result, "chain", []) or [])
            if early_chain:
                _strip_chain_plain_thinking(self, early_chain)
        if self._proactive_only_blocks_passive_event(event, "enable_segmented_proactive_reply"):
            return
        if not self._feature_enabled_or_temp_unlocked("enable_segmented_proactive_reply"):
            return
        segmented_scope = str(
            self._segmented_setting("scope", event=event, default="proactive_only")
            or "proactive_only"
        )
        if segmented_scope != "all_llm" and not external_proactive:
            return
        if external_proactive and bool(getattr(event, "_private_companion_external_presegmented", False)):
            return
        if not self._segmented_scope_allows_event(event):
            return
        result = event.get_result()
        if result is None or not result.chain:
            return
        source_result = result
        is_llm_result = False
        try:
            is_llm_result = bool(result.is_llm_result())
        except Exception:
            is_llm_result = False
        chain = list(result.chain or [])
        if self._restore_response_review_meta_leak_before_send(event, chain):
            result = event.get_result()
            chain = list(getattr(result, "chain", []) or []) if result is not None else []
            if not chain:
                return
            source_result = result
            try:
                is_llm_result = bool(result.is_llm_result())
            except Exception:
                is_llm_result = False
        reaction_intent = getattr(
            event,
            "_private_companion_reaction_expression_intent",
            None,
        )
        has_reaction_intent = isinstance(reaction_intent, dict) and bool(
            reaction_intent
        )
        deferred_reaction_tts = getattr(
            event,
            "_private_companion_deferred_reaction_tts",
            None,
        )
        plugin_owned_reaction_text = (
            has_reaction_intent
            and isinstance(deferred_reaction_tts, dict)
            and bool(deferred_reaction_tts)
        )
        plugin_tts_plain_fallback = (
            bool(getattr(event, "_private_companion_tts_request_applied", False))
            and bool(self._plain_result_body_text(chain))
        )
        owned_non_llm_result = bool(
            getattr(result, "_private_companion_owned_result", False)
        )
        legacy_plain_result_allowed = False
        if not is_llm_result and not owned_non_llm_result:
            try:
                legacy_plain_result_allowed = bool(
                    self._private_plain_result_allows_segmenting(event, chain)
                )
            except Exception:
                legacy_plain_result_allowed = False
        if is_llm_result and await self._should_defer_segmenting_to_astrbot_tts(event, result, chain):
            logger.debug(
                "当前 LLM 结果交由 AstrBot 官方 TTS 与原生分段处理: session=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            )
            return
        if (
            not is_llm_result
            and not external_proactive
            and not owned_non_llm_result
            and not plugin_owned_reaction_text
            and not plugin_tts_plain_fallback
            and not legacy_plain_result_allowed
        ):
            # A general/plain result has no reliable producer information in
            # AstrBot. Only results explicitly built by this plugin (or its
            # TTS/reaction paths) may enter the optional splitter; otherwise a
            # response from an unrelated plugin would be rewritten here.
            return
        if getattr(result, "use_t2i_", None):
            return
        if not self._segmented_platform_allows(event=event):
            return
        if not chain:
            return
        markdown_mode = getattr(result, "use_markdown_", None)
        try:
            setattr(event, "_private_companion_segmented_markdown_mode", markdown_mode)
        except Exception:
            pass
        chunks, changed, text = self._segment_llm_reply_chain(event, chain)
        if not chunks or not text:
            return
        chunks = self._limit_private_routine_check_segments(
            str(getattr(event, "message_str", "") or ""),
            chunks,
        )
        if len(chunks) <= 1:
            if changed:
                event.set_result(
                    self._build_segmented_result_from_chain(chunks[0], source_result)
                )
            return
        llm_segment_count = max(0, _safe_int(getattr(event, "_private_companion_llm_segment_count", 0), 0, 0))
        logger.debug(
            "按分段计划整理 LLM 回复: chars=%s segments=%s llm_segments=%s",
            len(text),
            len(chunks),
            llm_segment_count,
        )
        logger.info(
            "已按分段计划发送 LLM 回复: segments=%s llm_segments=%s first=%s full=%s",
            len(chunks),
            llm_segment_count,
            _single_line(self._segmented_chunk_log_text(chunks[0]), 120),
            _single_line(text, 420),
        )
        plain_segments = self._plain_text_segments_from_chunks(chunks)
        if (
            not has_reaction_intent
            and not bool(markdown_mode)
            and not bool(
                getattr(event, "_private_companion_segmented_markdown_detected", False)
            )
            and plain_segments
            and len(plain_segments) == len(chunks)
            and await self._send_segmented_event_forward_message(
                event,
                plain_segments,
                source="decorating_result",
            )
        ):
            self._suppress_outbound_reply(
                event,
                source="分段合并转发",
                reason="分段消息已由插件转发",
                history_note="[本轮未发送：分段消息已由插件转发]",
                level="info",
            )
            return
        event.set_result(
            self._build_segmented_result_from_chain(chunks[0], source_result)
        )
        if runtime_persona_setting(self, 'enable_daily_case_review_experiment', False):
            self._record_daily_review_outbound_case(event, chunks[0])
        activity_baseline = time.time()
        if len(chunks) > 1:
            previous_segment = self._segmented_chunk_log_text(chunks[0])
            if has_reaction_intent:
                setattr(
                    event,
                    "_private_companion_reaction_expression_expected_primary_chunks",
                    chunks,
                )
                setattr(
                    event,
                    "_private_companion_reaction_expression_segmented_remainder",
                    {
                        "chunks": chunks[1:],
                        "primary_chunk": chunks[0],
                        "previous_segment": previous_segment,
                        "started_at": activity_baseline,
                        "started": False,
                        "completed": False,
                    },
                )
                logger.info(
                    "表情正文启用有序分段: session=%s segments=%s",
                    _single_line(getattr(event, "unified_msg_origin", ""), 120)
                    or "unknown",
                    len(chunks),
                )
            else:
                self._create_lifecycle_background_task(
                    self._send_segmented_llm_chain_remainder(
                        event,
                        chunks[1:],
                        previous_segment=previous_segment,
                        source="decorating_result",
                        started_at=activity_baseline,
                    ),
                    label="segmented_llm_remainder",
                )

    def _plain_result_body_text(self, chain: list[Any]) -> str:
        """Return text when a result contains only an optional quote and plain body."""
        body = [comp for comp in list(chain or []) if not self._is_reply_component(comp)]
        if not body or any(not isinstance(comp, Plain) for comp in body):
            return ""
        return "".join(str(getattr(comp, "text", "") or "") for comp in body).strip()

    def _segmented_result_from_chain(
        self,
        event: AstrMessageEvent,
        chain: list[Any],
    ) -> Any:
        result = self._build_result_from_chain(chain)
        markdown_mode = getattr(
            event,
            "_private_companion_segmented_markdown_mode",
            None,
        )
        if markdown_mode is None:
            return result
        try:
            setter = getattr(result, "use_markdown", None)
            if callable(setter):
                updated = setter(bool(markdown_mode))
                if updated is not None:
                    result = updated
            elif hasattr(result, "use_markdown_"):
                result.use_markdown_ = bool(markdown_mode)
        except Exception:
            pass
        return result

    def _private_plain_result_allows_segmenting(
        self,
        event: AstrMessageEvent,
        chain: list[Any],
    ) -> bool:
        """Allow plugin text replies while leaving functional command output intact."""
        if not self._plain_result_body_text(chain):
            return False
        # Results produced before the ownership marker was introduced have no
        # reliable producer metadata. Restrict the compatibility path to the
        # two contexts where this plugin historically emits plain fallbacks:
        # private chats and quoted replies. A bare group text may belong to an
        # unrelated plugin and must not be rewritten by this global hook.
        is_private_chat = False
        checker = getattr(event, "is_private_chat", None)
        if callable(checker):
            try:
                is_private_chat = bool(checker())
            except Exception:
                is_private_chat = False
        has_reply_quote = any(self._is_reply_component(comp) for comp in list(chain or []))
        if not is_private_chat and not has_reply_quote:
            return False
        command_reason = getattr(self, "_tts_functional_command_reason", None)
        if callable(command_reason):
            try:
                if command_reason(event):
                    return False
            except Exception:
                pass
        return True

    def _is_reply_component(self, component: Any) -> bool:
        try:
            if Reply is not None and isinstance(component, Reply):
                return True
        except Exception:
            pass
        return component.__class__.__name__.lower() == "reply"

    def _segmented_chunk_log_text(self, chunk: list[Any]) -> str:
        parts: list[str] = []
        for comp in chunk or []:
            if isinstance(comp, Plain):
                text = str(getattr(comp, "text", "") or "").strip()
                if text:
                    parts.append(text)
                continue
            if self._is_reply_component(comp):
                parts.append("[引用]")
            else:
                parts.append(f"[{comp.__class__.__name__}]")
        return " ".join(parts).strip()

    def _plain_text_segments_from_chunks(self, chunks: list[list[Any]]) -> list[str]:
        segments: list[str] = []
        for chunk in chunks or []:
            if not chunk or any(not isinstance(comp, Plain) for comp in chunk):
                return []
            text = "".join(str(getattr(comp, "text", "") or "") for comp in chunk).strip()
            text = self._strip_leading_sentence_boundary_artifacts(text)
            if not text:
                return []
            segments.append(text)
        return segments

    def _segmented_context_chars(self, text: str) -> set[str]:
        text = re.sub(r"</?(?:pc[_-]?tts|t{2,}s)\b[^>]*>", "", str(text or ""), flags=re.IGNORECASE)
        stop_chars = set(
            "的一是不了在有和人就都而及与着或个上也很到说要去会这那我你他她它们"
            "吧呢呀啊吗么啦喔哦噢嘛哈嘿诶哎被把给让才还再又没别刚边里外"
        )
        chars = {ch for ch in text if "\u4e00" <= ch <= "\u9fff" and ch not in stop_chars}
        chars.update(re.findall(r"[a-zA-Z][a-zA-Z0-9_]{1,}", text.lower()))
        return chars

    def _segmented_context_overlap_ratio(self, left: str, right: str) -> float:
        left_chars = self._segmented_context_chars(left)
        right_chars = self._segmented_context_chars(right)
        if not left_chars or not right_chars:
            return 1.0
        return len(left_chars & right_chars) / max(1, min(len(left_chars), len(right_chars)))

    def _segmented_remainder_context_drift_reason(
        self,
        event: AstrMessageEvent,
        *,
        previous_text: str,
        next_text: str,
        source: str = "",
    ) -> str:
        """Stop delayed passive chunks when they look like a different reply turn."""
        segmented_scope = self._segmented_setting(
            "scope",
            event=event,
            default="proactive_only",
        )
        if source != "decorating_result" or segmented_scope != "all_llm":
            return ""
        prev = _single_line(previous_text, 260)
        nxt = _single_line(next_text, 260)
        if not prev or not nxt:
            return ""
        inbound = ""
        getter = getattr(event, "get_message_str", None)
        if callable(getter):
            try:
                inbound = str(getter() or "")
            except Exception:
                inbound = ""
        if not inbound:
            inbound = str(getattr(event, "message_str", "") or "")
        if any(marker in inbound for marker in ("在干嘛", "干什么", "忙什么", "忙啥", "进度", "代码", "项目", "修到", "跑通", "测试", "校验")):
            return ""

        context = f"{inbound}\n{prev}"
        context_chars = self._segmented_context_chars(context)
        next_chars = self._segmented_context_chars(nxt)
        if len(context_chars) < 8 or len(next_chars) < 6:
            return ""
        overlap = self._segmented_context_overlap_ratio(context, nxt)
        if overlap >= 0.08:
            return ""

        food_markers = ("西瓜", "水果", "吃", "甜", "买", "拎", "饭", "餐", "晚饭", "午饭", "口", "手勒", "奖励")
        work_markers = ("逻辑", "校验", "进度", "跑通", "顺手", "焦躁", "代码", "编译", "测试", "调试", "需求", "项目")
        checkin_markers = ("忙完没", "忙完了吗", "忙完了没", "你那边忙", "歇会", "休息一下", "停下来")
        fresh_turn_pattern = r"^\s*(在呢|我在|我这边|这边|刚把|刚刚把|刚刚|我刚|你那边|你这边)"

        context_has_food = any(marker in context for marker in food_markers)
        next_has_work = any(marker in nxt for marker in work_markers)
        next_is_checkin = any(marker in nxt for marker in checkin_markers)
        if context_has_food and (next_has_work or next_is_checkin):
            return "food_topic_to_work_or_checkin"
        if re.search(fresh_turn_pattern, nxt) and (next_has_work or next_is_checkin):
            return "fresh_turn_without_topic_overlap"
        if re.search(r"^\s*(你那边|你这边)", nxt) and next_is_checkin:
            return "new_checkin_without_topic_overlap"
        if "昨晚" in nxt and "昨晚" not in context and re.search(fresh_turn_pattern, nxt):
            return "unexpected_time_anchor"
        return ""

    @staticmethod
    def _strip_llm_segment_marker_lines(text: Any) -> str:
        return strip_llm_segment_marker_lines(text)

    def _clean_segmented_reply_chunks(
        self,
        event: AstrMessageEvent,
        chunks: list[list[Any]],
    ) -> list[list[Any]]:
        cleaned_chunks: list[list[Any]] = []
        removed_internal_control = False
        for chunk in chunks or []:
            cleaned_chunk: list[Any] = []
            for comp in chunk or []:
                if isinstance(comp, Plain):
                    original = str(getattr(comp, "text", "") or "")
                    text = self._sanitize_segmented_plain_text(event, original)
                    removed_internal_control = removed_internal_control or text != original.strip()
                    text = self._strip_leading_sentence_boundary_artifacts(text)
                    if text:
                        cleaned_chunk.append(Plain(text))
                    continue
                cleaned_chunk.append(comp)
            if cleaned_chunk:
                cleaned_chunks.append(cleaned_chunk)
        if removed_internal_control:
            logger.warning(
                "分段前已移除内部控制标记: session=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            )
        return cleaned_chunks

    @staticmethod
    def _default_llm_controlled_segmenting_prompt() -> str:
        return (
            "你可以自行决定是否把你的本轮回复作为多条消息发送；"
            "日常对话、聊天 等需要短句输出的情景建议使用拆分；"
            "长文、教程、代码 等连续性较高的表述尽量少用分段。"
            "\n使用方法：每个消息边界都必须使用下面的完整标记： {{split_marker}}，并让它单独占一行，不能添加引号，也不能把它放进代码块。"
            "\n示例：第一段内容\n{{split_marker}}\n第二段内容。"
            "\n只有上述方法可以实现发送多条消息，换行和连续换行都不会作为多条消息发送。（仅在确有必要时使用）。"
        )

    def _split_llm_controlled_text_for_event(
        self,
        event: AstrMessageEvent | None,
        text: str,
        *,
        umo: str = "",
    ) -> list[str]:
        """Apply explicit LLM boundaries and then spend the remaining rule budget."""
        planned = PrivateCompanionPluginSegmentedReplyPart01Mixin._split_llm_controlled_text_buffers_for_event(
            self,
            event,
            [text],
            umo=umo,
        )
        return planned[0] if planned else []

    def _split_llm_controlled_text_buffers_for_event(
        self,
        event: AstrMessageEvent | None,
        texts: list[str],
        *,
        umo: str = "",
    ) -> list[list[str]]:
        """Plan LLM and plugin boundaries across every text buffer in a chain."""
        raw_buffers = [str(text or "").strip() for text in texts]
        if not raw_buffers:
            return []
        if not bool(runtime_persona_setting(self, "enable_segmented_proactive_reply", False)):
            return [
                [cleaned] if (cleaned := sanitize_llm_segment_control_tokens(text)) else []
                for text in raw_buffers
            ]
        if not bool(runtime_persona_setting(self, "enable_llm_controlled_segmenting", False)):
            return [
                self._split_proactive_text(cleaned, event=event, umo=umo) if cleaned else []
                for text in raw_buffers
                for cleaned in [sanitize_llm_segment_control_tokens(text)]
            ]
        plugin_rules_enabled = bool(
            runtime_persona_setting(self, "enable_segmented_plugin_rules", True)
        )

        def apply_common_transforms(segment: str) -> str:
            candidate = self._split_proactive_text(
                segment,
                event=event,
                umo=umo,
                force_common_transforms=True,
                common_transforms_only=True,
            )
            return str(candidate[0] if candidate else "").strip()

        parsed_buffers: list[list[str]] = []
        controlled_buffers: list[bool] = []
        parse_results = []
        for raw_text in raw_buffers:
            parsed = parse_llm_segment_control(raw_text)
            parse_results.append(parsed)
            parsed_buffers.append(
                list(parsed.segments)
                if parsed.controlled
                else ([parsed.sanitized_text] if parsed.sanitized_text else [])
            )
            controlled_buffers.append(parsed.controlled)

        if event is not None:
            setattr(
                event,
                "_private_companion_llm_segment_diagnostics",
                {
                    "exact": sum(item.exact_boundary_count for item in parse_results),
                    "recovered": sum(item.recovered_boundary_count for item in parse_results),
                    "cleaned_only": sum(item.cleaned_only_count for item in parse_results),
                },
            )
            for attr_name in (
                "_private_companion_llm_history_segments",
                "_private_companion_llm_planned_chunk_texts",
                "_private_companion_llm_planned_segment_ids",
            ):
                try:
                    delattr(event, attr_name)
                except AttributeError:
                    pass
            if len(parse_results) == 1 and parse_results[0].controlled:
                history_segments = [
                    cleaned
                    for segment in parse_results[0].segments
                    if (cleaned := apply_common_transforms(segment))
                ]
                if len(history_segments) >= 2:
                    setattr(
                        event,
                        "_private_companion_llm_history_segments",
                        tuple(history_segments),
                    )

        def split_uncontrolled_buffer(text: str, *, suppress_plugin_rules: bool = False) -> list[str]:
            if not text:
                return []
            if suppress_plugin_rules:
                transformed = apply_common_transforms(text)
                return [transformed] if transformed else []
            return self._split_proactive_text(text, event=event, umo=umo)

        if not any(controlled_buffers):
            if plugin_rules_enabled:
                return [
                    split_uncontrolled_buffer(
                        parsed.sanitized_text,
                        suppress_plugin_rules=parsed.suppress_plugin_rule_split,
                    )
                    for parsed in parse_results
                ]
            return [
                [transformed]
                if (transformed := apply_common_transforms(parsed.sanitized_text))
                else []
                for parsed in parse_results
            ]

        llm_count = sum(len(segments) for segments in parsed_buffers)
        if event is not None:
            setattr(event, "_private_companion_llm_segment_count", llm_count)
        if not plugin_rules_enabled or any(
            item.suppress_plugin_rule_split for item in parse_results
        ):
            planned = [
                [
                    cleaned
                    for segment in segments
                    if (cleaned := apply_common_transforms(segment))
                ]
                for segments in parsed_buffers
            ]
            if event is not None and len(planned) == 1 and len(planned[0]) >= 2:
                setattr(
                    event,
                    "_private_companion_llm_planned_segment_ids",
                    tuple(range(len(planned[0]))),
                )
            return planned

        max_segments = max(
            1,
            _safe_int(
                self._segmented_setting("max_segments", event=event, umo=umo, default=3),
                3,
                1,
                8,
            ),
        )
        if llm_count >= max_segments:
            planned = [
                [
                    cleaned
                    for segment in segments
                    if (cleaned := apply_common_transforms(str(segment or "")))
                ]
                for segments in parsed_buffers
            ]
            if event is not None and len(planned) == 1 and len(planned[0]) >= 2:
                setattr(
                    event,
                    "_private_companion_llm_planned_segment_ids",
                    tuple(range(len(planned[0]))),
                )
            return planned

        result: list[list[list[str] | str]] = [list(segments) for segments in parsed_buffers]
        rule_processed: set[tuple[int, int]] = set()
        remaining = max_segments - llm_count
        candidate_indices = sorted(
            (
                (buffer_index, segment_index)
                for buffer_index, segments in enumerate(parsed_buffers)
                for segment_index in range(len(segments))
                if not has_fenced_llm_segment_marker(segments[segment_index])
            ),
            key=lambda position: (
                -len(str(parsed_buffers[position[0]][position[1]])),
                position[0],
                position[1],
            ),
        )
        for buffer_index, segment_index in candidate_indices:
            if remaining <= 0:
                break
            candidate = self._split_proactive_text(
                str(parsed_buffers[buffer_index][segment_index]),
                event=event,
                umo=umo,
                max_segments_override=remaining + 1,
            )
            additions = max(0, len(candidate) - 1)
            if additions <= 0 or additions > remaining:
                continue
            result[buffer_index][segment_index] = candidate
            rule_processed.add((buffer_index, segment_index))
            remaining -= additions

        flattened_buffers: list[list[str]] = []
        flattened_ids_by_buffer: list[list[int]] = []
        for buffer_index, segments in enumerate(result):
            flattened: list[str] = []
            flattened_ids: list[int] = []
            for segment_index, item in enumerate(segments):
                if isinstance(item, list):
                    for part in item:
                        clean_part = str(part or "").strip()
                        if clean_part:
                            flattened.append(clean_part)
                            flattened_ids.append(segment_index)
                    continue
                transformed = (
                    str(item or "").strip()
                    if (buffer_index, segment_index) in rule_processed
                    else apply_common_transforms(str(item or ""))
                )
                if transformed:
                    flattened.append(transformed)
                    flattened_ids.append(segment_index)
            flattened_buffers.append(flattened)
            flattened_ids_by_buffer.append(flattened_ids)
        if (
            event is not None
            and len(flattened_buffers) == 1
            and len(set(flattened_ids_by_buffer[0])) >= 2
        ):
            setattr(
                event,
                "_private_companion_llm_planned_segment_ids",
                tuple(flattened_ids_by_buffer[0]),
            )
        return flattened_buffers
