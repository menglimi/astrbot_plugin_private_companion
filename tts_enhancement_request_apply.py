# -*- coding: utf-8 -*-
"""TtsEnhancementRequestApplyMixin。

由 tools/split_mixin_domain.py 从 tts_enhancement.py 机械抽取（4 个方法 + 0 个模块级名字 + 0 个类级赋值 / 731 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 TtsEnhancementMixin）。
"""
from __future__ import annotations

import inspect
import re
import time
from .conversation_injection_plan import PLACEMENT_DYNAMIC_SYSTEM, get_conversation_injection_plan
from .conversation_prompt_section import PromptRenderMode, PromptSection, exact_text, prompt_section, render_prompt_sections
from .helpers import _normalize_outbound_punctuation_flow, _safe_int, _single_line
from .tts_enhancement_shared import PRIVATE_TTS_BLOCK_TOKEN_PATTERN, logger
from typing import Any
from .tts_enhancement_shared import Plain
from .tts_enhancement_shared import Record



class TtsEnhancementRequestApplyMixin:
    """TtsEnhancementRequestApplyMixin（从 TtsEnhancementMixin 拆出）。"""


    async def apply_tts_enhancement_request(self, event: Any, req: Any) -> None:
        if bool(getattr(event, "_private_companion_tts_request_applied", False)):
            return
        feature_enabled = getattr(self, "_feature_enabled_or_temp_unlocked", None)
        tts_enabled = feature_enabled("enable_tts_enhancement") if callable(feature_enabled) else self._tts_setting("enable_tts_enhancement", False)
        if not getattr(self, "enabled", False) or not tts_enabled:
            return
        if not hasattr(req, "system_prompt"):
            logger.info(
                "TTS请求注入跳过: req无system_prompt session=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            )
            return
        turn_voice_language = self._ensure_turn_tts_voice_language(event)
        try:
            config = self.context.get_config(str(getattr(event, "unified_msg_origin", "") or "")) or {}
        except Exception:
            config = getattr(self, "config", {}) or {}
        provider_kind = self._tts_provider_kind_for_event(event, config=config)
        marker = "<!-- private_companion_tts_enhancement_v1 -->"
        prompt = str(getattr(req, "system_prompt", "") or "")

        def append_dynamic_tts_fragment(
            fragment_marker: str,
            section: PromptSection,
            *,
            priority: int = 55,
        ) -> str:
            placer = getattr(self, "_place_conversation_prompt_section", None)
            if callable(placer):
                return placer(
                    req,
                    fragment_marker,
                    section,
                    priority=priority,
                )
            helper = getattr(self, "_append_turn_prompt_fragment_by_position", None)
            if callable(helper):
                try:
                    if helper(
                        req,
                        fragment_marker,
                        section,
                        priority=priority,
                    ):
                        return "prompt"
                except Exception as exc:
                    logger.debug("TTS 指定位置动态注入失败,回退 system_prompt: %s", _single_line(exc, 120))
            plan = get_conversation_injection_plan(req)
            if plan is None:
                return "none"
            plan.materialize_system_block(
                req,
                section=section,
                marker=fragment_marker,
                priority=priority,
                placement=PLACEMENT_DYNAMIC_SYSTEM,
            )
            return "system_prompt"

        async def record_tts_fragment(title: str, key: str, text: str, mode: str = "", placement: str = "system_prompt") -> None:
            common_recorder = getattr(self, "_record_request_prompt_fragment", None)
            if callable(common_recorder):
                await common_recorder(
                    event,
                    title=title,
                    key=key,
                    text=text,
                    source="tts_enhancement",
                    mode=mode or str(self._tts_setting("tts_generation_mode", "fast_tag") or ""),
                    priority=20,
                    metadata={
                        "语种": self._tts_language_label(event),
                        "本轮临时语种": bool(turn_voice_language),
                        "模式": self._tts_setting("tts_generation_mode", "fast_tag"),
                        "频控": self._tts_setting("tts_frequency_control_mode", "global"),
                        "范围": self._tts_setting("tts_conversion_scope", "partial"),
                        "provider": provider_kind,
                        "注入位置": placement,
                    },
                )
                return
            recorder = getattr(self, "_record_prompt_injection_snapshot", None)
            if not callable(recorder):
                return
            await recorder(
                kind="request",
                session=_single_line(getattr(event, "unified_msg_origin", ""), 160) or "unknown",
                title=title,
                text=text,
                mode=mode or str(self._tts_setting("tts_generation_mode", "fast_tag") or ""),
                modules=[
                    {
                        "key": key,
                        "source": "tts_enhancement",
                        "priority": 20,
                        "content": text,
                        "chars": len(text),
                    }
                ],
                metadata={
                    "语种": self._tts_language_label(event),
                    "本轮临时语种": bool(turn_voice_language),
                    "模式": self._tts_setting("tts_generation_mode", "fast_tag"),
                    "频控": self._tts_setting("tts_frequency_control_mode", "global"),
                    "范围": self._tts_setting("tts_conversion_scope", "partial"),
                    "provider": provider_kind,
                    "注入位置": placement,
                },
            )

        try:
            setattr(event, "_private_companion_tts_request_applied", True)
        except Exception:
            pass
        expression = getattr(event, "_private_companion_expression_decision", None)
        if isinstance(expression, dict):
            tts_style = _single_line(expression.get("tts_style"), 24)
            expression_band = _single_line(expression.get("expression_band"), 24)
            content_tier = _single_line(expression.get("content_tier"), 16) or "normal"
            expression_context = self._tts_expression_style_context(event)
            if tts_style or expression_band:
                expression_prompt = (
                    f"当前互动档位={expression_band or 'relaxed'}，TTS 风格上限={tts_style or 'natural'}，"
                    f"内容尺度={content_tier}。{expression_context}\n"
                    "语音只能收敛语气，不能扩大文字内容尺度、切换 Provider 或绕过文本复核。"
                )
                placement = append_dynamic_tts_fragment(
                    "<!-- private_companion_tts_expression_v1 -->",
                    prompt_section(
                        key="tts.relationship_expression",
                        title="统一陪伴表达的语音上限",
                        source="tts_enhancement",
                        content=expression_prompt,
                    ),
                    priority=54,
                )
                await record_tts_fragment(
                    "TTS 统一表达上限注入",
                    "tts.relationship_expression",
                    expression_prompt,
                    placement=placement,
                )
        user_requested_tts = self._event_explicitly_requests_tts(event) or bool(turn_voice_language)
        functional_command_reason = self._tts_functional_command_reason(event)
        if functional_command_reason and not user_requested_tts:
            functional_prompt = (
                "用户本轮发来的是指令或功能操作。请优先把执行结果、帮助、菜单、状态、配置、查询信息、错误说明和卡片说明保留为普通文字，"
                "不要仅因自动语音概率命中就添加 <pc_tts>、<tts> 或等价语音标签。"
                "只有用户在本轮明确要求语音或朗读时，才把确实适合听见的自然表达交给语音。"
            )
            placement = append_dynamic_tts_fragment(
                "<!-- private_companion_tts_functional_reply_v1 -->",
                prompt_section(
                    key="tts.functional_reply",
                    title="功能性回复的语音取舍",
                    source="tts_enhancement",
                    content=functional_prompt,
                ),
                priority=56,
            )
            await record_tts_fragment(
                "TTS 功能性回复取舍注入",
                "tts.functional_reply",
                functional_prompt,
                mode=functional_command_reason,
                placement=placement,
            )
            return
        strong_block_reason = ""
        mode = self._tts_setting("tts_generation_mode", "fast_tag")
        full_scope = self._tts_setting("tts_conversion_scope", "partial") == "full"
        probability_allowed = True
        if (
            mode in {"fast_tag", "postprocess"}
            and not user_requested_tts
            and self._tts_setting("tts_frequency_control_mode", "global") != "legacy"
        ):
            probability_allowed = self._tts_trigger_probability_allows(event, reason="llm_tts_prompt")
            if not probability_allowed:
                if self._tts_strong_constraint_enabled():
                    self._set_tts_hard_block(event, "probability_miss")
                return
        if mode == "fast_tag":
            strong_block_reason = self._tts_strong_constraint_block_reason(
                event,
                user_requested_tts=user_requested_tts,
                check_probability=False,
                reason="llm_tts_prompt",
            )
            if strong_block_reason:
                self._set_tts_hard_block(event, strong_block_reason)
        if not strong_block_reason:
            self._disable_streaming_for_tts_turn(event)
        if marker not in prompt and mode == "fast_tag" and not strong_block_reason:
            authored_rule_section = self._build_tts_rule_prompt_section(
                provider_kind,
                event=event,
            )
            rule_prompt = render_prompt_sections(
                [authored_rule_section],
                mode=PromptRenderMode.LABELED_BLOCK,
            )
            rule_section = prompt_section(
                key=authored_rule_section.key,
                title=authored_rule_section.title,
                source=authored_rule_section.source,
                content=exact_text(f"{marker}\n{rule_prompt}"),
                metadata=authored_rule_section.metadata,
            )
            plan = get_conversation_injection_plan(req)
            if plan is not None:
                plan.materialize_system_block(
                    req,
                    section=rule_section,
                    marker=marker,
                    priority=20,
                    placement=PLACEMENT_DYNAMIC_SYSTEM,
                )
            await record_tts_fragment("TTS 基础规则注入", "tts.rule", rule_prompt)
        elif marker not in prompt and mode == "postprocess" and not strong_block_reason:
            authored_postprocess_section = self._build_tts_postprocess_mode_prompt_section(
                event,
                full_scope=full_scope,
                turn_voice_language=turn_voice_language,
            )
            postprocess_prompt = render_prompt_sections(
                [authored_postprocess_section],
                mode=PromptRenderMode.LABELED_BLOCK,
            )
            postprocess_section = prompt_section(
                key=authored_postprocess_section.key,
                title=authored_postprocess_section.title,
                source=authored_postprocess_section.source,
                content=exact_text(f"{marker}\n{postprocess_prompt}"),
                metadata=authored_postprocess_section.metadata,
            )
            plan = get_conversation_injection_plan(req)
            if plan is not None:
                plan.materialize_system_block(
                    req,
                    section=postprocess_section,
                    marker=marker,
                    priority=20,
                    placement=PLACEMENT_DYNAMIC_SYSTEM,
                )
            await record_tts_fragment("TTS 后处理模式注入", "tts.rule", postprocess_prompt, mode="postprocess")
        if strong_block_reason:
            reverse_prompt = (
                f"本轮语音被硬性禁止，原因：{strong_block_reason}。\n"
                "请只输出普通文字回复，不要包含 <pc_tts>...</pc_tts>、<tts>...</tts>、语音、朗读、音频、发声、Record 或任何等价语音内容。"
                "如果用户要求语音，也先用文字自然回应当前内容，不要承诺已经发送语音。"
            )
            placement = append_dynamic_tts_fragment(
                "<!-- private_companion_tts_block_v1 -->",
                prompt_section(
                    key="tts.block",
                    title="本轮 TTS 强约束",
                    source="tts_enhancement",
                    content=reverse_prompt,
                ),
                priority=22,
            )
            await record_tts_fragment("TTS 强约束禁用注入", "tts.block", reverse_prompt, mode="strong_block", placement=placement)
        if mode == "fast_tag" and self._should_force_tts_for_main_user_event(event) and not strong_block_reason:
            frequency_mode = self._tts_setting("tts_frequency_control_mode", "global")
            if full_scope:
                force_rule = (
                    "这轮消息来自主用户或明确 @ 到主用户。如果当前回复适合语音表达，可以使用 <pc_tts>；"
                    "一旦决定使用，唯一语音块必须覆盖整条回复的全部有效内容，不得只圈出一句，仍需遵守目标语种、发送形态和文字显示规则。"
                )
            elif frequency_mode == "legacy":
                force_rule = "这轮消息来自主用户或明确 @ 到主用户。若当前回复适合语音表达，适合采用一段 <pc_tts>...</pc_tts>；由你根据语境判断，仍需遵守目标语种、发送形态和文字显示规则。"
            else:
                force_rule = "这轮消息来自主用户或明确 @ 到主用户。如果语音比纯文字更自然，可以采用一段 <pc_tts>...</pc_tts>；不要刻意使用语音，仍需遵守目标语种、发送形态、文字显示规则和会话最小间隔。"
            force_prompt = force_rule
            placement = append_dynamic_tts_fragment(
                "<!-- private_companion_tts_force_v1 -->",
                prompt_section(
                    key="tts.force",
                    title="本轮 TTS 强化触发",
                    source="tts_enhancement",
                    content=force_prompt,
                ),
                priority=54,
            )
            await record_tts_fragment("TTS 主用户倾向注入", "tts.force", force_prompt, mode="main_user", placement=placement)
        if user_requested_tts and mode == "fast_tag" and not strong_block_reason:
            request_scope_rule = (
                "请把唯一语音块覆盖整条回复的全部有效内容，不得只圈出一句；"
                if full_scope
                else "请直接把适合朗读的内容写进一段 <pc_tts>...</pc_tts>；"
            )
            user_request_prompt = (
                f"用户本轮明确希望听到语音或你的声音。请以回应用户需求为主：{request_scope_rule}"
                "只写这次真正要说的内容，不要预告或确认“语音已经发出”“这次真发了”，实际发送结果由插件决定。"
                "这类顺应用户请求的语音不受自动语音触发概率限制，但仍需自然克制、遵守目标语种、发送形态和文字显示规则。"
            )
            placement = append_dynamic_tts_fragment(
                "<!-- private_companion_tts_user_request_v1 -->",
                prompt_section(
                    key="tts.user_request",
                    title="用户语音请求",
                    source="tts_enhancement",
                    content=user_request_prompt,
                ),
                priority=54,
            )
            await record_tts_fragment("用户语音请求注入", "tts.user_request", user_request_prompt, mode="user_request", placement=placement)

    async def protect_tts_enhancement_response_blocks(self, event: Any, resp: Any) -> None:
        self._ensure_turn_tts_voice_language(event)
        feature_enabled = getattr(self, "_feature_enabled_or_temp_unlocked", None)
        tts_enabled = feature_enabled("enable_tts_enhancement") if callable(feature_enabled) else self._tts_setting("enable_tts_enhancement", False)
        if not bool(getattr(event, "_private_companion_tts_request_applied", False)):
            return
        if not tts_enabled:
            text = str(getattr(resp, "completion_text", "") or "")
            if re.search(r"</?(?:pc[_-]?tts|t{2,}s)\b", text, flags=re.IGNORECASE):
                resp.completion_text = _normalize_outbound_punctuation_flow(self._strip_any_tts_markup(text))
                logger.info(
                    "TTS强化未开启,已从模型回复中移除 TTS 标签: session=%s preview=%s",
                    _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                    _single_line(resp.completion_text, 160),
                )
            return
        text = self._normalize_tts_tags(str(getattr(resp, "completion_text", "") or ""))
        text_before_safety_drop = text
        text, dropped_safety_voice = self._drop_tts_provider_safety_blocks(text)
        if dropped_safety_voice:
            if not text:
                text = self._tts_plain_markup_fallback_text(text_before_safety_drop)
            resp.completion_text = _normalize_outbound_punctuation_flow(text)
            logger.warning(
                "已从模型回复中移除提供商安全回执语音块: session=%s remaining=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                _single_line(text, 160) or "empty",
            )
        if text:
            has_tts_markup = bool(re.search(r"</?(?:pc[_-]?tts|t{2,}s)\b", text, flags=re.IGNORECASE))
            if has_tts_markup:
                if self._tts_setting("tts_generation_mode", "fast_tag") == "postprocess":
                    # In postprocess mode every model-authored tag is input noise, including
                    # the private <pc_tts> form. Only the postprocessor may create a voice block.
                    cleaned = self._strip_any_tts_markup(text)
                    cleaned = self._sanitize_tts_visible_text(cleaned) or self._tts_visible_fallback_text(
                        text,
                        event=event,
                    )
                    resp.completion_text = _normalize_outbound_punctuation_flow(cleaned)
                    logger.info(
                        "TTS后处理模式已移除主模型自写语音标签,改由发送前后处理判断: session=%s preview=%s",
                        _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                        _single_line(cleaned, 160),
                    )
                    return
                if not self._tts_hard_block_reason(event):
                    try:
                        config = self.context.get_config(str(getattr(event, "unified_msg_origin", "") or "")) or {}
                    except Exception:
                        config = getattr(self, "config", {}) or {}
                    provider_kind = self._tts_provider_kind_for_event(event, config=config)
                    text = await self._ensure_tts_blocks_have_visible_chinese(text, event, provider_kind=provider_kind)
                text = self._protect_tts_blocks_for_framework(text, event)
            else:
                visible_fallback = self._tts_unwrapped_foreign_translation_fallback(text, event)
                if visible_fallback:
                    logger.warning(
                        "快速标签回复漏写语音标记，已仅保留中文可见正文: session=%s original=%s visible=%s",
                        _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                        _single_line(text, 160),
                        _single_line(visible_fallback, 160),
                    )
                    text = visible_fallback
            resp.completion_text = _normalize_outbound_punctuation_flow(text)

    async def apply_tts_enhancement_before_send(self, event: Any) -> None:
        turn_voice_language = self._ensure_turn_tts_voice_language(event)
        feature_enabled = getattr(self, "_feature_enabled_or_temp_unlocked", None)
        tts_enabled = feature_enabled("enable_tts_enhancement") if callable(feature_enabled) else self._tts_setting("enable_tts_enhancement", False)
        if not getattr(self, "enabled", False) or not tts_enabled:
            return
        if not bool(getattr(event, "_private_companion_tts_request_applied", False)):
            logger.debug(
                "TTS 强化跳过未经过主回复链的发送结果: session=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            )
            return
        result = event.get_result()
        chain = list(getattr(result, "chain", []) or []) if result is not None else []
        if not chain or any(isinstance(comp, Record) for comp in chain):
            return
        skip_reason = _single_line(getattr(event, "_private_companion_skip_tts_enhancement", ""), 80)
        if not skip_reason and any(
            bool(getattr(comp, "_private_companion_skip_tts_enhancement", False))
            for comp in chain
        ):
            skip_reason = "proactive_prebuilt_voice"
        if skip_reason:
            logger.info(
                "主动正文已有预生成语音,跳过二次 TTS 转换: session=%s reason=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                skip_reason,
            )
            return
        plain_parts = [str(getattr(comp, "text", "") or "") for comp in chain if isinstance(comp, Plain)]
        if not plain_parts:
            return
        source_segments: list[str] = []
        llm_splitter = getattr(self, "_split_llm_controlled_text_for_event", None)
        llm_allowed = getattr(self, "_llm_controlled_segmenting_allowed", None)
        if (
            callable(llm_splitter)
            and callable(llm_allowed)
            and bool(llm_allowed(event))
        ):
            try:
                planned_segments = [
                    str(item or "").strip()
                    for item in llm_splitter(event, "".join(plain_parts))
                    if str(item or "").strip()
                ]
            except Exception as exc:
                logger.debug("TTS 前自主分段解析失败: %s", _single_line(exc, 120))
                planned_segments = []
            if len(planned_segments) > 1:
                source_segments = planned_segments
                # Synthesize the visible reply without speaking the transport
                # marker; the downstream ordered-send stage restores segments.
                plain_parts = ["".join(planned_segments)]
        if not source_segments and len(plain_parts) > 1 and len(plain_parts) == len(chain):
            source_limit = self._tts_complete_text_limit("".join(plain_parts), minimum=1000)
            for part in plain_parts:
                restored_part = self._restore_protected_tts_blocks(part, event)
                visible_part = self._sanitize_tts_visible_text(restored_part, max_chars=source_limit)
                if visible_part:
                    source_segments.append(visible_part)
        try:
            if len(source_segments) > 1:
                setattr(event, "_private_companion_tts_source_plain_segments", tuple(source_segments))
            elif hasattr(event, "_private_companion_tts_source_plain_segments"):
                delattr(event, "_private_companion_tts_source_plain_segments")
        except Exception:
            pass
        text = self._restore_protected_tts_blocks("".join(plain_parts), event).strip()
        if not text:
            return
        tool_cleaner = getattr(self, "_strip_plaintext_tool_call_envelopes", None)
        if callable(tool_cleaner):
            cleaned_text, leaked_calls = tool_cleaner(text)
            if leaked_calls:
                logger.warning(
                    "TTS 发送前已移除明文工具调用: session=%s tools=%s",
                    _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                    ",".join(str(item.get("name") or "") for item in leaked_calls),
                )
                text = cleaned_text
                if not text:
                    event.set_result(self._build_result_from_chain([]))
                    return
        normalized = self._normalize_tts_tags(text)
        normalized_before_safety_drop = normalized
        normalized, dropped_safety_voice = self._drop_tts_provider_safety_blocks(normalized)
        if dropped_safety_voice:
            logger.warning(
                "TTS发送前已移除提供商安全回执语音块: session=%s remaining=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                _single_line(normalized, 160) or "empty",
            )
            if not normalized:
                fallback_text = self._tts_plain_markup_fallback_text(
                    normalized_before_safety_drop
                )
                event.set_result(
                    self._build_result_from_chain(
                        [Plain(fallback_text)] if fallback_text else []
                    )
                )
                return
        reaction_intent = getattr(
            event,
            "_private_companion_reaction_expression_intent",
            None,
        )
        defer_reaction_tts = (
            isinstance(reaction_intent, dict)
            and bool(reaction_intent)
            and not self._event_explicitly_requests_tts(event)
            and not bool(turn_voice_language)
        )
        if defer_reaction_tts:
            visible_text = self._tts_visible_fallback_text(
                normalized,
                text,
                event=event,
            ) or self._tts_plain_markup_fallback_text(normalized)
            visible_text = self._sanitize_tts_visible_text(
                visible_text,
                max_chars=self._tts_complete_text_limit(visible_text, 1600),
            )
            if visible_text:
                primary_chain = self._replace_plain_components_preserving_order(
                    chain,
                    [Plain(visible_text)],
                )
                inbound_ts_getter = getattr(self, "_event_inbound_activity_ts", None)
                try:
                    started_at = (
                        float(inbound_ts_getter(event))
                        if callable(inbound_ts_getter)
                        else time.time()
                    )
                except Exception:
                    started_at = time.time()
                setattr(
                    event,
                    "_private_companion_deferred_reaction_tts",
                    {
                        "normalized": normalized,
                        "fallback_plain": visible_text,
                        "started_at": started_at,
                        "turn_generation": _safe_int(
                            getattr(event, "_private_companion_reply_turn_generation", 0),
                            0,
                            0,
                        ),
                    },
                )
                event.set_result(self._build_result_from_chain(primary_chain))
                logger.info(
                    "表情表达先发送完整正文,自动 TTS 延后生成: session=%s chars=%s",
                    _single_line(getattr(event, "unified_msg_origin", ""), 120)
                    or "unknown",
                    len(visible_text),
                )
                return
        if self._tts_setting("tts_generation_mode", "fast_tag") == "postprocess":
            # A tag can also arrive from a tool or an extension that bypasses the LLM response hook.
            # Treat it as plain source text so it cannot re-enter the fast-tag path.
            normalized = self._sanitize_tts_visible_text(self._strip_any_tts_markup(normalized))
            new_chain = await self._maybe_convert_plain_reply_to_tts(normalized, event) if normalized else []
        elif "<tts>" in normalized.lower() and "</tts>" in normalized.lower():
            normalized, full_scope_fallback = self._enforce_full_tts_scope_markup(
                normalized,
                event=event,
            )
            if full_scope_fallback:
                logger.info(
                    "TTS全量转换已在发送前覆盖整条回复: session=%s chars=%s preview=%s",
                    _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                    len(full_scope_fallback),
                    _single_line(full_scope_fallback, 140),
                )
            new_chain = await self._process_tts_tags(
                normalized,
                event,
                fallback_plain=full_scope_fallback,
            )
        else:
            new_chain = await self._maybe_convert_plain_reply_to_tts(normalized, event)
        if not new_chain:
            if self._tts_setting("tts_generation_mode", "fast_tag") == "postprocess" and normalized:
                translated = await self._translate_unwrapped_foreign_postprocess_text(normalized, event)
                if translated:
                    normalized = translated
                    logger.info(
                        "TTS后处理纯文本检测到未包装外语,已转为可见中文: session=%s",
                        _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                    )
                event.set_result(self._build_result_from_chain([Plain(normalized)]))
                return
            if PRIVATE_TTS_BLOCK_TOKEN_PATTERN.search("".join(plain_parts)):
                fallback_text = self._tts_visible_fallback_text(
                    normalized,
                    event=event,
                ) or self._tts_plain_markup_fallback_text(normalized)
                event.set_result(self._build_result_from_chain([Plain(fallback_text)] if fallback_text else []))
            return
        if self._tts_setting("tts_generation_mode", "fast_tag") == "postprocess":
            visible_text = "\n".join(
                str(getattr(component, "text", "") or "").strip()
                for component in new_chain
                if isinstance(component, Plain) and str(getattr(component, "text", "") or "").strip()
            ).strip()
            translated = await self._translate_unwrapped_foreign_postprocess_text(visible_text, event)
            if translated:
                new_chain = self._replace_plain_components_preserving_order(
                    new_chain,
                    [Plain(translated)],
                )
                logger.info(
                    "TTS后处理可见组件检测到未包装外语,已转为可见中文: session=%s",
                    _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                )
        new_chain = self._tts_record_first_visible_last_chain(new_chain)
        if len(plain_parts) != len(chain):
            new_chain = self._replace_plain_components_preserving_order(
                chain,
                new_chain,
            )
        if isinstance(reaction_intent, dict) and reaction_intent:
            ordered_chunks = [new_chain]
        else:
            ordered_chunks = self._split_tts_chain_for_ordered_send(new_chain)
        expanded_chunks: list[list[Any]] = []
        for chunk in ordered_chunks:
            expanded_chunks.extend(self._tts_segment_plain_chunk_for_ordered_send(event, chunk))
        ordered_chunks = expanded_chunks
        if len(ordered_chunks) > 1:
            first_chunk_has_record = any(isinstance(comp, Record) for comp in ordered_chunks[0])
            if first_chunk_has_record:
                remainder_started_at = time.time()
            else:
                inbound_ts_getter = getattr(self, "_event_inbound_activity_ts", None)
                if callable(inbound_ts_getter):
                    try:
                        remainder_started_at = float(inbound_ts_getter(event))
                    except Exception:
                        remainder_started_at = time.time()
                else:
                    remainder_started_at = time.time()
            event.set_result(self._build_result_from_chain(ordered_chunks[0]))
            recorder = getattr(self, "_record_daily_review_outbound_case", None)
            if callable(recorder):
                case_id = recorder(event, ordered_chunks[0])
                updater = getattr(self, "_update_daily_review_case", None)
                if case_id and callable(updater):
                    updater(
                        case_id,
                        outcome="delivery_pending",
                        signals={"segments_expected": len(ordered_chunks), "segments_sent": 1},
                    )
            pending = {
                "chunks": ordered_chunks[1:],
                "started_at": remainder_started_at,
                "turn_generation": _safe_int(
                    getattr(event, "_private_companion_reply_turn_generation", 0),
                    0,
                    0,
                ),
            }
            proactive_umo = _single_line(
                getattr(event, "_private_companion_proactive_delivery_umo", ""),
                180,
            )
            if proactive_umo:
                remainder = self._send_tts_chain_chunks_after_first(
                    event,
                    pending["chunks"],
                    started_at=remainder_started_at,
                )
                self._create_tts_background_task(remainder, label="tts_reply_remainder")
            else:
                setattr(
                    event,
                    "_private_companion_tts_reply_remainder",
                    pending,
                )
            return
        event.set_result(self._build_result_from_chain(ordered_chunks[0] if ordered_chunks else new_chain))

    async def _should_defer_segmenting_to_astrbot_tts(
        self,
        event: Any,
        result: Any,
        chain: list[Any],
    ) -> bool:
        """Keep the original LLM result intact when AstrBot still owns this turn's TTS."""
        if bool(getattr(event, "_private_companion_tts_request_applied", False)):
            return False
        if not chain or any(isinstance(component, Record) for component in chain):
            return False
        try:
            if result is None or not bool(result.is_llm_result()):
                return False
        except Exception:
            return False

        context = getattr(self, "context", None)
        if context is None:
            return False
        umo = str(getattr(event, "unified_msg_origin", "") or "")
        config_getter = getattr(context, "get_config", None)
        try:
            config = config_getter(umo) if callable(config_getter) else {}
        except Exception:
            return False
        if not isinstance(config, dict):
            return False
        settings = config.get("provider_tts_settings")
        if not isinstance(settings, dict):
            return False
        enabled_value = settings.get("enable", False)
        if isinstance(enabled_value, str):
            enabled = enabled_value.strip().lower() in {"1", "true", "yes", "on", "enabled"}
        else:
            enabled = bool(enabled_value)
        if not enabled:
            return False
        try:
            trigger_probability = max(
                0.0,
                min(1.0, float(settings.get("trigger_probability", 1))),
            )
        except (TypeError, ValueError):
            trigger_probability = 1.0
        if trigger_probability <= 0:
            return False
        provider_getter = getattr(context, "get_using_tts_provider", None)
        try:
            provider = provider_getter(umo) if callable(provider_getter) else None
        except Exception:
            provider = None
        if provider is None:
            return False

        try:
            from astrbot.core.star.session_llm_manager import SessionServiceManager

            allowed = SessionServiceManager.should_process_tts_request(event)
            if inspect.isawaitable(allowed):
                allowed = await allowed
            if not bool(allowed):
                return False
        except ImportError:
            pass
        except Exception as exc:
            logger.debug(
                "查询 AstrBot 官方 TTS 会话状态失败，保留插件分段: session=%s error=%s",
                _single_line(umo, 120) or "unknown",
                _single_line(exc, 120),
            )
            return False
        return True
