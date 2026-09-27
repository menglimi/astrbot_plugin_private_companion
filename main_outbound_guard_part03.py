# -*- coding: utf-8 -*-
"""PrivateCompanionPluginOutboundGuardPart03Mixin。

由 tools/split_mixin_domain.py 从 main_outbound_guard.py 机械抽取（19 个方法 + 0 个模块级名字 + 0 个类级赋值 / 440 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPluginOutboundGuardMixin）。
"""
from __future__ import annotations

from .main_outbound_guard_shared import filter
from .main_outbound_guard_shared import logger
from .main_outbound_guard_shared import Any
from .main_outbound_guard_shared import AstrMessageEvent
from .main_outbound_guard_shared import ProviderRequest
from .main_outbound_guard_shared import _multi_persona_event_context
from .main_outbound_guard_shared import _now_ts
from .main_outbound_guard_shared import _safe_float
from .main_outbound_guard_shared import _single_line
from .main_outbound_guard_shared import _strip_outbound_control_blocks
from .main_outbound_guard_shared import re
from .main_outbound_guard_shared import runtime_persona_setting
from .main_outbound_guard_shared import sanitize_history_image_blocks
from .main_outbound_guard_shared import sanitize_llm_segment_control_tokens
from .main_outbound_guard_shared import sanitize_openai_tool_history



class PrivateCompanionPluginOutboundGuardPart03Mixin:
    """PrivateCompanionPluginOutboundGuardPart03Mixin（从 PrivateCompanionPluginOutboundGuardMixin 拆出）。"""


    @filter.on_decorating_result(priority=-1000)
    @_multi_persona_event_context
    async def strip_unexpected_private_passive_reply(self, event: AstrMessageEvent, *args, **kwargs):
        """私聊被动主链不沿用框架误带的引用，避免 QQ 显示跨会话引用。"""
        if self is None or not self.enabled:
            return
        if bool(getattr(event, "private_companion_proactive_framework", False)):
            return
        try:
            if not bool(event.is_private_chat()):
                return
        except Exception:
            return
        try:
            result = event.get_result()
        except Exception:
            return
        if result is None:
            return
        try:
            is_llm_result = bool(result.is_llm_result())
        except Exception:
            return
        if not is_llm_result:
            return
        chain = list(getattr(result, "chain", []) or [])
        if not chain:
            return
        current_message_ids = set(self._event_message_id_candidates(event))
        cleaned_chain: list[Any] = []
        removed_reply_ids: list[str] = []
        for component in chain:
            if not self._is_reply_component(component):
                cleaned_chain.append(component)
                continue
            reply_id = _single_line(self._extract_reply_message_id(component), 120)
            if reply_id and reply_id in current_message_ids:
                cleaned_chain.append(component)
                continue
            removed_reply_ids.append(reply_id or "unknown")
        if len(cleaned_chain) == len(chain):
            return
        try:
            result.chain = cleaned_chain
        except Exception:
            event.set_result(self._build_result_from_chain(cleaned_chain))
        logger.info(
            "已移除私聊被动主链中的跨目标引用组件: session=%s current=%s removed=%s targets=%s",
            _single_line(getattr(event, "unified_msg_origin", ""), 120) or "-",
            ",".join(sorted(current_message_ids)) or "-",
            len(chain) - len(cleaned_chain),
            ",".join(removed_reply_ids) or "-",
        )

    @filter.on_decorating_result()
    @_multi_persona_event_context
    async def remember_group_bot_reply_context_before_send(self, event: AstrMessageEvent, *args, **kwargs):
        """记录群聊 Bot 实际候选回复，供下一轮连续对话判断使用。"""
        # Group continuity is committed only by the confirmed-delivery
        # finalizer. This decorating hook must remain a pure read/transform
        # stage because the adapter can reject the outgoing result afterwards.
        return

    @filter.on_decorating_result(priority=-9000)
    @_multi_persona_event_context
    async def final_tts_markup_guard_before_send(self, event: AstrMessageEvent, *args, **kwargs):
        """发送前终检 TTS 标签，避免 <tts> 原样泄漏到聊天。"""
        if self is None or not self.enabled:
            return
        if self._proactive_only_blocks_passive_event(event, "enable_tts_enhancement"):
            return
        guard = getattr(self, "finalize_outbound_tts_markup_guard", None)
        if callable(guard):
            await guard(event)

    def _sanitize_segmented_plain_text(self, event: AstrMessageEvent, text: Any) -> str:
        if not bool(runtime_persona_setting(self, "enable_framework_error_leak_guard", True)):
            return str(text or "")
        protected_tts_tokens = getattr(event, "_private_companion_tts_block_tokens", None)
        preserve_private_tts_tokens = (
            bool(runtime_persona_setting(self, 'enable_tts_enhancement', False))
            and isinstance(protected_tts_tokens, dict)
            and bool(protected_tts_tokens)
        )
        cleaned = _strip_outbound_control_blocks(
            text,
            tts_enabled=bool(runtime_persona_setting(self, "enable_tts_enhancement", False)),
            preserve_private_tts_tokens=preserve_private_tts_tokens,
            allowed_private_tts_tokens=set(protected_tts_tokens.keys())
            if isinstance(protected_tts_tokens, dict) else None,
        )
        if not bool(runtime_persona_setting(self, 'enable_tts_enhancement', False)):
            cleaned = re.sub(r"</?t{2,}s\b[^>]*>", "", cleaned, flags=re.IGNORECASE).strip()
        cleaned = sanitize_llm_segment_control_tokens(cleaned)
        return cleaned

    def _sanitize_request_context_new_conversation_boundary(self, event: AstrMessageEvent, req: ProviderRequest) -> None:
        contexts = getattr(req, "contexts", None)
        if not isinstance(contexts, list) or not contexts:
            return
        boundary_index = -1
        for index, item in enumerate(contexts):
            text = self._plain_context_content_for_fast_reply(item.get("content") if isinstance(item, dict) else item)
            if self._context_text_is_new_conversation_boundary(text):
                boundary_index = index
        if boundary_index < 0:
            return
        trimmed: list[Any] = []
        for item in contexts[boundary_index + 1:]:
            text = self._plain_context_content_for_fast_reply(item.get("content") if isinstance(item, dict) else item)
            if self._context_text_is_new_conversation_boundary(text):
                continue
            trimmed.append(item)
        if len(trimmed) == len(contexts):
            return
        try:
            req.contexts = trimmed
        except Exception:
            return
        logger.info(
            "已按新会话边界裁剪 AstrBot 上下文: session=%s contexts=%s->%s boundary_index=%s",
            _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            len(contexts),
            len(trimmed),
            boundary_index,
        )

    def _stop_reply_for_rest_gate(self, event: AstrMessageEvent, reason: str) -> None:
        self._record_rest_reply_backlog(event, reason)
        logger.info(
            "睡眠/休息回复闸门拦截本轮被动回复: session=%s reason=%s",
            _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            _single_line(reason, 120),
        )
        self._record_passive_no_reply(
            event,
            source="休息闸门",
            reason=reason or "睡眠/休息回复闸门拦截",
            level="info",
        )
        empty_result = self._build_result_from_chain([])
        try:
            empty_result.stop_event()
        except Exception:
            pass
        event.set_result(empty_result)
        event.stop_event()

    def _stop_private_reply_after_user_rest_signal(self, event: AstrMessageEvent, user_id: str, text: str) -> None:
        logger.info(
            "用户明确勿扰/不用回复,已前置拦截本轮私聊回复: user=%s text=%s",
            _single_line(user_id, 80),
            _single_line(text, 120),
        )
        self._record_passive_no_reply(
            event,
            source="休息静默",
            reason="用户明确要求勿扰或不用回复",
            detail=text,
            level="info",
        )
        empty_result = self._build_result_from_chain([])
        try:
            empty_result.stop_event()
        except Exception:
            pass
        event.set_result(empty_result)
        event.stop_event()

    def _stop_group_llm_reply_if_blocked(self, event: AstrMessageEvent, *, source: str) -> bool:
        if self._is_private_companion_command_event(event):
            return False
        item = self._group_llm_reply_block_for_event(event)
        if not item:
            return False
        if bool(getattr(event, "_private_companion_group_llm_reply_blocked", False)):
            return True
        group_id = _single_line(item.get("group_id"), 80) or self._extract_group_id_from_event(event)
        logger.info(
            "本群 LLM 回复已被单独关闭,拦截本轮回复: group=%s source=%s",
            group_id or "-",
            _single_line(source, 40),
        )
        setattr(event, "_private_companion_group_llm_reply_blocked", True)
        self._record_passive_no_reply(
            event,
            source="群聊 LLM 熔断",
            reason="本群所有 LLM 回复已关闭",
            detail=f"group={group_id or '-'} source={_single_line(source, 40)}",
            level="warn",
        )
        event.set_result(self._build_result_from_chain([]))
        event.stop_event()
        return True

    @filter.on_decorating_result()
    @_multi_persona_event_context
    async def redact_outbound_secrets_before_send(self, event: AstrMessageEvent, *args, **kwargs):
        """Final passive-reply guard against API keys, tokens and passwords."""
        if self is None or not self.enabled or not bool(getattr(self, "enable_outbound_secret_redaction", True)):
            return
        result = event.get_result()
        chain = list(getattr(result, "chain", []) or []) if result is not None else []
        if not chain:
            return
        _, changed = self._redact_outbound_chain_secrets(chain)
        if changed:
            logger.error(
                "发送前检测到敏感凭据并已脱敏: session=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            )

    @filter.on_decorating_result(priority=-21000)
    @_multi_persona_event_context
    async def record_daily_review_outbound_case_before_send(self, event: AstrMessageEvent, *args, **kwargs):
        """Experimental final-stage sampling for the next daily case review."""
        if self is None or not self.enabled or not runtime_persona_setting(self, 'enable_daily_case_review_experiment', False):
            return
        result = event.get_result()
        chain = list(getattr(result, "chain", []) or []) if result is not None else []
        if chain:
            self._record_daily_review_outbound_case(event, chain)

    @filter.on_llm_request(priority=-22000)
    @_multi_persona_event_context
    async def prepare_p5_memory_attestation(self, event: AstrMessageEvent, req: ProviderRequest, *args, **kwargs):
        """Expose a per-request attestation issuer before MemoryCompanion runs."""
        if self is None or event is None or not bool(getattr(self, "enable_p5_source_observer", False)):
            return
        request_carrier = req if req is not None else event
        p3_state = getattr(event, "private_companion_p5_p3_state", None)
        if p3_state is None:
            p3_state = object()
            try:
                setattr(event, "private_companion_p5_p3_state", p3_state)
            except Exception:
                pass
        try:
            setattr(event, "private_companion_p5_request_carrier", request_carrier)
            setattr(
                event,
                "private_companion_p5_issue_attestation",
                lambda sink, _event=event, _request=request_carrier: self._p5_issue_attestation_for_event(
                    event=_event,
                    request=_request,
                    sink=str(sink or "memory_recall"),
                ),
            )
            setattr(event, "private_companion_p5_status", self.p5_source_observer_status())
        except Exception:
            logger.debug("P5 request carrier attach failed")

    @filter.on_llm_request(priority=-21000)
    @_multi_persona_event_context
    async def sanitize_sensitive_screen_tools(self, event: AstrMessageEvent, req: ProviderRequest, *args, **kwargs):
        """屏幕工具只能保留给已启用的主要用户私聊，群聊和第三方场景一律裁掉。"""
        if self is None or req is None:
            return
        removed = self._remove_sensitive_screen_tools_from_request(event, req)
        if removed:
            await self._append_sensitive_screen_tool_guard_to_request(event, req, removed)

    @filter.on_llm_request(priority=-20500)
    @_multi_persona_event_context
    async def sanitize_deepseek_tool_call_history(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        *args,
        **kwargs,
    ):
        """Drop only malformed tool-call groups before a DeepSeek provider request."""
        if self is None or req is None:
            return
        if not self._llm_request_uses_deepseek_openai_compatible_provider(event, req):
            return
        contexts = getattr(req, "contexts", None)
        cleaned, stats = sanitize_openai_tool_history(contexts)
        if not stats.get("changed"):
            return
        try:
            req.contexts = cleaned
        except Exception:
            return
        logger.info(
            "Cleaned malformed DeepSeek tool history: groups=%s assistants=%s tool_results=%s orphans=%s",
            stats.get("removed_groups", 0),
            stats.get("removed_assistants", 0),
            stats.get("removed_tool_results", 0),
            stats.get("removed_orphans", 0),
        )

    @filter.on_llm_request(priority=-20000)
    @_multi_persona_event_context
    async def sanitize_incompatible_web_search_tools(self, event: AstrMessageEvent, req: ProviderRequest, *args, **kwargs):
        """移除 Gemini/OpenAI 兼容层会拒绝的 Baidu AI Search MCP 工具声明。"""
        if self is None or req is None:
            return
        tool_set = getattr(req, "func_tool", None)
        if tool_set is None:
            return
        if not self._tool_set_has_named_tool(tool_set, "AIsearch"):
            return
        if not self._llm_request_uses_gemini_family_provider(event, req):
            return
        remove_tool = getattr(tool_set, "remove_tool", None)
        if not callable(remove_tool):
            return
        try:
            remove_tool("AIsearch")
        except Exception as exc:
            logger.debug("移除不兼容 AIsearch 工具失败: %s", _single_line(exc, 160))
            return
        settings = self._llm_request_provider_settings_for_event(event)
        provider_label = " / ".join(self._llm_request_provider_identity_parts(event, req)[:3]) or "unknown"
        umo = _single_line(getattr(event, "unified_msg_origin", ""), 120)
        log_key = f"{umo}:{provider_label}:AIsearch"
        logged = getattr(self, "_incompatible_web_search_tool_logged_keys", None)
        if not isinstance(logged, set):
            logged = set()
            setattr(self, "_incompatible_web_search_tool_logged_keys", logged)
        if log_key not in logged:
            logged.add(log_key)
            logger.warning(
                "已移除本轮 Gemini 不兼容的 AIsearch 搜索工具，避免请求 400: provider=%s websearch_provider=%s session=%s",
                _single_line(provider_label, 200),
                _single_line(settings.get("websearch_provider"), 80) or "unknown",
                umo or "unknown",
            )

    @filter.on_llm_request(priority=-249000)
    @_multi_persona_event_context
    async def sanitize_historical_image_blocks_before_provider(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        *args,
        **kwargs,
    ):
        """Keep legacy multimodal history compatible with text-only chat endpoints."""
        if self is None or req is None or not bool(getattr(self, "enabled", False)):
            return
        cleaned, stats = sanitize_history_image_blocks(getattr(req, "contexts", None))
        if not stats.get("changed"):
            return
        try:
            req.contexts = cleaned
        except Exception:
            return
        logger.info(
            "已兼容化历史图片消息: session=%s messages=%s image_blocks=%s",
            _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            stats.get("messages_changed", 0),
            stats.get("image_blocks_replaced", 0),
        )

    @filter.on_llm_request(priority=-250000)
    @_multi_persona_event_context
    async def sanitize_gif_inputs_before_provider(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        *args,
        **kwargs,
    ):
        """Keep provider adapters from receiving unsupported raw GIF inputs."""
        if self is None or req is None or not bool(getattr(self, "enabled", False)):
            return
        replaced, dropped = self._sanitize_provider_request_gif_inputs(req)
        if replaced or dropped:
            logger.info(
                "Provider 请求中的 GIF 已兼容化: converted=%s dropped=%s session=%s",
                replaced,
                dropped,
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            )

    def _sanitize_unverified_repeat_elapsed_claim(
        self,
        inbound_text: str,
        response_text: str,
        user: dict[str, Any],
    ) -> str:
        text = str(response_text or "").strip()
        if not text:
            return ""
        inbound = str(inbound_text or "").strip()
        if not re.search(r"(说过|讲过|提过|聊过|发过|说了|讲了|提了).{0,4}(啦|了|呀|啊)?$", inbound):
            return text
        if not re.search(r"\d+\s*(?:个)?\s*(?:小时|分钟|天)前.{0,8}(?:说过|讲过|提过|聊过|发过)", text):
            return text

        last_at = 0.0
        if isinstance(user, dict):
            last_at = _safe_float(user.get("last_companion_message_at"), 0) or _safe_float(user.get("last_reply_at"), 0)
        elapsed = _now_ts() - last_at if last_at > 0 else 0.0
        if 0 < elapsed <= 90 * 60:
            replacement = "刚才说过了"
        elif 0 < elapsed <= 6 * 3600:
            replacement = "前面说过了"
        else:
            replacement = "之前说过了"
        cleaned = re.sub(
            r"\d+\s*(?:个)?\s*(?:小时|分钟|天)前.{0,4}(?:已经|就)?(?:说过|讲过|提过|聊过|发过)(?:了)?",
            replacement,
            text,
        )
        cleaned = re.sub(r"(刚才说过了|前面说过了|之前说过了)(?:了)+", r"\1", cleaned)
        return cleaned.strip()

    def _sanitize_robotic_topic_choice_after_repeat_correction(
        self,
        inbound_text: str,
        response_text: str,
    ) -> str:
        text = str(response_text or "").strip()
        if not text:
            return ""
        inbound = str(inbound_text or "").strip()
        if not re.search(r"(说过|讲过|提过|聊过|发过|说了|讲了|提了).{0,4}(啦|了|呀|啊)?$", inbound):
            return text
        original = text
        text = re.sub(r"刚醒(?=脑子|反应|没转|有点懵)", "刚才", text)
        text = re.sub(
            r"(?:（|\()\s*(?:看来|可能|大概)?\s*刚(?:才)?脑子([^）)]{0,24}?没(?:转|反应)[^）)]*?)\s*(?:）|\))",
            r"刚才脑子\1。",
            text,
        )
        text = re.sub(
            r"(?:那)?\s*(?:你)?(?:希望|想让|要不要|要我|我是不是该)?[^。！？!?]{0,36}(?:换个话题|换话题)[^。！？!?]{0,36}(?:继续聊|接着聊|聊下去)[^。！？!?]*[？?。！!]*",
            "",
            text,
        )
        text = re.sub(
            r"(?:那)?\s*(?:你)?(?:希望|想让|要不要|要我|我是不是该)?[^。！？!?]{0,36}(?:继续聊|接着聊|聊下去)[^。！？!?]{0,36}(?:换个话题|换话题)[^。！？!?]*[？?。！!]*",
            "",
            text,
        )
        text = re.sub(r"\s+", " ", text).strip()
        text = re.sub(r"([。！？!?])\s+", r"\1", text)
        text = re.sub(r"[，,、；;]\s*$", "。", text).strip()
        if text != original and not re.search(r"(不绕|先收|换个轻点|我记住|脑子|对哦|说过)", text):
            text = f"{text.rstrip('。！？!?')}，我先不绕这个了。"
        if text != original and re.fullmatch(r"(啊[，,。…]*)?(对哦[，,。…]*)?", text):
            text = "啊，对哦，刚才脑子没转过来，我先不绕这个了。"
        return text or original

    def _stop_group_member_safety_event(self, event: AstrMessageEvent) -> None:
        """清空可能已生成的结果，并停止已静默成员的当前群消息。"""
        try:
            event.set_result(self._build_result_from_chain([]))
        except Exception:
            pass
        try:
            event.stop_event()
        except Exception:
            pass
        setattr(event, "_private_companion_member_safety_blocked", True)
