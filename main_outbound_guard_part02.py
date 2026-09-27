# -*- coding: utf-8 -*-
"""PrivateCompanionPluginOutboundGuardPart02Mixin。

由 tools/split_mixin_domain.py 从 main_outbound_guard.py 机械抽取（9 个方法 + 0 个模块级名字 + 0 个类级赋值 / 454 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPluginOutboundGuardMixin）。
"""
from __future__ import annotations

from .main_outbound_guard_shared import filter
from .main_outbound_guard_shared import logger
from .main_outbound_guard_shared import Any
from .main_outbound_guard_shared import AstrMessageEvent
from .main_outbound_guard_shared import Plain
from .main_outbound_guard_shared import PromptRenderMode
from .main_outbound_guard_shared import _multi_persona_event_context
from .main_outbound_guard_shared import _single_line
from .main_outbound_guard_shared import prompt_section
from .main_outbound_guard_shared import render_prompt_sections
from .main_outbound_guard_shared import runtime_persona_setting
from .main_outbound_guard_shared import time



class PrivateCompanionPluginOutboundGuardPart02Mixin:
    """PrivateCompanionPluginOutboundGuardPart02Mixin（从 PrivateCompanionPluginOutboundGuardMixin 拆出）。"""


    def _restore_response_review_meta_leak_before_send(self, event: AstrMessageEvent, chain: list[Any]) -> bool:
        if not chain or any(not isinstance(comp, Plain) for comp in chain):
            return False
        outbound = "\n".join(str(getattr(comp, "text", "") or "") for comp in chain).strip()
        cleaned, reason = self._strip_response_review_meta_leak(outbound)
        if not reason:
            return False
        fallback = str(getattr(event, "_private_companion_response_review_fallback_text", "") or "").strip()
        replacement = cleaned or fallback
        if replacement and self._response_review_meta_leak_reason(replacement):
            replacement = ""
        setattr(event, "_private_companion_response_review_guard_active", False)
        logger.error(
            "发送前拦截到回复复核内部判断: session=%s reason=%s before=%s after=%s",
            _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            reason,
            _single_line(outbound, 180),
            _single_line(replacement, 180),
        )
        if replacement:
            try:
                current_result = event.get_result()
                current_result.chain = [Plain(replacement)]
            except Exception:
                event.set_result(self._build_result_from_chain([Plain(replacement)]))
            self._schedule_reply_interception_forward(
                "rewrite",
                source="回复复核发送前保护",
                reason=f"复核模型返回内部判断，已回退可发送正文：{reason}",
                source_session=_single_line(getattr(event, "unified_msg_origin", ""), 180),
                before=outbound,
                after=replacement,
            )
            return True
        self._suppress_outbound_reply(
            event,
            source="发送前拦截",
            reason=f"回复复核内部判断泄漏：{reason}",
            history_note="[本轮未发送：回复复核输出无法安全改写]",
            level="warn",
        )
        return True

    @filter.on_decorating_result()
    @_multi_persona_event_context
    async def suppress_group_question_wakeup_collision_reply(self, event: AstrMessageEvent, *args, **kwargs):
        """答疑唤醒的群聊回复发送前复核，避免 Bot 碰瓷式插话。"""
        if self is None or not self.enabled:
            return
        if self._proactive_only_blocks_passive_event(event, "enable_group_companion"):
            return
        if not self._feature_enabled_or_temp_unlocked("enable_group_companion"):
            return
        if not self._passive_response_review_enabled():
            return
        if self._effective_passive_review_mode() == "local_only":
            return
        if bool(getattr(event, "_private_companion_group_question_review_done", False)):
            return
        setattr(event, "_private_companion_group_question_review_done", True)
        group_id = self._extract_group_id_from_event(event)
        if not group_id:
            return
        scene = getattr(event, "private_companion_group_scene", None)
        if not isinstance(scene, dict) or str(scene.get("trigger") or "") != "group_wakeup_question":
            return
        result = event.get_result()
        if result is None:
            return
        try:
            if hasattr(result, "is_llm_result") and not result.is_llm_result():
                return
        except Exception:
            pass
        chain = list(getattr(result, "chain", []) or [])
        if not chain:
            return
        reply_text = self._chain_text_for_forbidden_recall(chain, limit=600)
        if not reply_text:
            return
        try:
            review = await self._review_group_question_wakeup_reply_before_send(event, reply_text=reply_text)
        except Exception as exc:
            logger.warning(
                "群聊答疑回复发送前复核失败,默认放行: %s",
                _single_line(exc, 160),
            )
            return
        if str(review.get("decision") or "") != "drop":
            return
        logger.info(
            "已拦截群聊答疑碰瓷回复: group=%s reason=%s text=%s",
            group_id,
            _single_line(review.get("reason"), 120),
            _single_line(reply_text, 160),
        )
        self._suppress_outbound_reply(
            event,
            source="群聊答疑复核",
            reason=_single_line(review.get("reason"), 120) or "群聊答疑碰瓷回复被拦截",
            history_note="[本轮未发送：群聊答疑复核未通过]",
            level="info",
        )

    @filter.on_decorating_result()
    @_multi_persona_event_context
    async def suppress_smart_silence_reply_before_send(self, event: AstrMessageEvent, *args, **kwargs):
        """用户明确想停下当前话题时，用小模型决定是否静默取消待发送回复。"""
        if self is None or not self.enabled:
            return
        if (
            self._passive_response_review_enabled()
            and bool(getattr(event, "_private_companion_response_review_drop", False))
        ):
            logger.info("回复复核去重发送前兜底拦截")
            self._suppress_outbound_reply(
                event,
                source="回复复核去重",
                reason="最终回复与上一条 Bot 消息重复",
                history_note="[本轮未发送：最终回复与上一条消息重复]",
                level="info",
            )
            return
        if bool(getattr(event, "_private_companion_smart_silence_drop", False)):
            logger.info(
                "智能沉默发送前兜底拦截: reason=%s",
                _single_line(getattr(event, "_private_companion_smart_silence_reason", ""), 120),
            )
            self._suppress_outbound_reply(
                event,
                source="智能沉默",
                reason=_single_line(getattr(event, "_private_companion_smart_silence_reason", ""), 120) or "用户边界语义触发静默",
                history_note="[本轮未发送：智能沉默判定]",
                level="info",
            )
            return
        if not bool(runtime_persona_setting(self, 'enable_smart_silence', True)):
            return
        try:
            if bool(getattr(event, "is_private_chat", lambda: False)()):
                return
        except Exception:
            pass
        result = event.get_result()
        if result is None:
            return
        try:
            if hasattr(result, "is_llm_result") and not result.is_llm_result():
                return
        except Exception:
            pass
        chain = list(getattr(result, "chain", []) or [])
        if not chain or any(not isinstance(comp, Plain) for comp in chain):
            return
        reply_text = self._chain_text_for_forbidden_recall(chain, limit=600)
        if not reply_text:
            return
        inbound_text = _single_line(
            getattr(event, "private_companion_group_text", "") or getattr(event, "message_str", ""),
            260,
        )
        trigger_checker = getattr(self, "_smart_silence_contextual_trigger_reason", None)
        trigger_reason = (
            trigger_checker(inbound_text, reply_text, session_kind="group")
            if callable(trigger_checker)
            else self._smart_silence_trigger_reason(inbound_text)
        )
        if not trigger_reason:
            return
        recent_context: list[str] = []
        group_id = self._extract_group_id_from_event(event)
        if group_id:
            group = self._get_group(group_id)
            sender_id = ""
            try:
                sender_id = str(event.get_sender_id())
            except Exception:
                sender_id = ""
            flow_formatter = getattr(self, "_format_group_recent_flow_for_review", None)
            recent_flow = (
                flow_formatter(group, sender_id=sender_id, text=inbound_text, max_lines=8, max_chars=1000)
                if callable(flow_formatter)
                else ""
            )
            for line in recent_flow.splitlines():
                line = _single_line(line, 140)
                if line.startswith("- "):
                    line = line[2:].strip()
                if line:
                    recent_context.append(line)
        try:
            decision = await self._decide_smart_silence(
                inbound_text=inbound_text,
                response_text=reply_text,
                user=None,
                session_kind="group" if group_id else "chat",
                recent_context=recent_context,
            )
        except Exception as exc:
            logger.warning("智能沉默发送前判定失败,默认放行: %s", _single_line(exc, 120))
            return
        if str(decision.get("decision") or "") != "silent":
            return
        logger.info(
            "智能沉默已取消本轮群聊回复: group=%s reason=%s inbound=%s reply=%s",
            group_id or "-",
            _single_line(decision.get("reason"), 120),
            _single_line(inbound_text, 120),
            _single_line(reply_text, 140),
        )
        self._suppress_outbound_reply(
            event,
            source="智能沉默",
            reason=_single_line(decision.get("reason"), 120) or "群聊边界语义触发静默",
            history_note="[本轮未发送：群聊智能沉默判定]",
            level="info",
        )

    @filter.on_decorating_result()
    @_multi_persona_event_context
    async def record_empty_passive_result_before_send(self, event: AstrMessageEvent, *args, **kwargs):
        """发送前兜底记录空结果，避免被动不回复却没有排障原因。"""
        if self is None or not self.enabled:
            return
        if bool(getattr(event, "_private_companion_passive_no_reply_recorded", False)):
            return
        if bool(getattr(event, "private_companion_proactive_framework", False)):
            return
        result = event.get_result()
        if result is None:
            return
        chain = list(getattr(result, "chain", []) or [])
        if chain:
            return
        try:
            is_private = bool(getattr(event, "is_private_chat", lambda: False)())
        except Exception:
            is_private = False
        is_group = bool(self._extract_group_id_from_event(event))
        if not is_private and not is_group:
            return
        self._record_passive_no_reply(
            event,
            source="发送前检查",
            reason="发送前结果为空",
            level="info",
        )

    @filter.on_decorating_result(priority=-19000)
    @_multi_persona_event_context
    async def suppress_empty_photo_tool_followup_before_send(self, event: AstrMessageEvent, *args, **kwargs):
        """Stop any adapter-visible followup after a tool already sent the photo."""
        if self is None or not self.enabled:
            return
        if not bool(getattr(event, "_private_companion_photo_tool_sent", False)):
            return
        result = event.get_result()
        if result is None:
            return
        chain = list(getattr(result, "chain", []) or [])
        had_visible_content = self._photo_tool_followup_chain_has_visible_content(chain)
        self._suppress_outbound_reply(
            event,
            source="图片工具尾随",
            reason="图片工具已发送成功，取消尾随文本",
            history_note="[本轮未发送：图片工具已发送成功]",
            level="info",
        )
        logger.info(
            "已阻止图片工具成功发送后的尾随消息: session=%s components=%s visible=%s",
            _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            len(chain),
            had_visible_content,
        )

    @filter.on_decorating_result(priority=300)
    @_multi_persona_event_context
    async def apply_tts_enhancement_before_send_hook(self, event: AstrMessageEvent, *args, **kwargs):
        """发送前处理 TTS强化标签和自动语音转换。"""
        if self is None or not self.enabled:
            return
        if self._proactive_only_blocks_passive_event(event, "enable_tts_enhancement"):
            return
        await self.apply_tts_enhancement_before_send(event)

    @filter.on_decorating_result()
    @_multi_persona_event_context
    async def strip_group_internal_identity_anchors(self, event: AstrMessageEvent, *args, **kwargs):
        """发送前清理群聊内部身份锚点，避免调试标记泄露到回复。"""
        if self is None or not self.enabled:
            return
        if self._proactive_only_blocks_passive_event(event, "enable_group_companion"):
            return
        if not self._feature_enabled_or_temp_unlocked("enable_group_companion"):
            return
        if not self._extract_group_id_from_event(event):
            return
        result = event.get_result()
        chain = list(getattr(result, "chain", []) or []) if result is not None else []
        if not chain:
            return
        for comp in chain:
            if not isinstance(comp, Plain):
                continue
            original = str(getattr(comp, "text", "") or "")
            cleaned = self._strip_internal_identity_anchors(original)
            if cleaned != original:
                try:
                    comp.text = cleaned
                except Exception:
                    pass

    @filter.on_decorating_result()
    @_multi_persona_event_context
    async def suppress_group_silent_control_reply(self, event: AstrMessageEvent, *args, **kwargs):
        """模型输出“不回复”控制语时静默吞掉，避免把内部判断发到群里。"""
        if self is None or not self.enabled:
            return
        if self._proactive_only_blocks_passive_event(event, "enable_group_companion"):
            return
        if not self._feature_enabled_or_temp_unlocked("enable_group_companion"):
            return
        if not self._extract_group_id_from_event(event):
            return
        result = event.get_result()
        chain = list(getattr(result, "chain", []) or []) if result is not None else []
        if not chain or any(not isinstance(comp, Plain) for comp in chain):
            return
        text = "".join(str(getattr(comp, "text", "") or "") for comp in chain).strip()
        if not self._is_silent_control_reply_text(text):
            return
        logger.info("已静默吞掉群聊不回复控制语: %s", _single_line(text, 120))
        self._suppress_outbound_reply(
            event,
            source="群聊静默",
            reason="模型输出不回复控制语",
            history_note="[本轮未发送：模型输出不回复控制语]",
            level="info",
        )

    async def _review_group_question_wakeup_reply_before_send(
        self,
        event: AstrMessageEvent,
        *,
        reply_text: str,
    ) -> dict[str, str]:
        provider_id = self._task_provider(
            runtime_persona_setting(self, "response_review_provider_id", ""),
            runtime_persona_setting(self, "group_followup_judge_provider_id", ""),
            runtime_persona_setting(self, "mai_style_provider_id", ""),
        )
        if not provider_id:
            return {"decision": "send", "reason": "未配置复核模型"}
        scene = getattr(event, "private_companion_group_scene", None)
        if not isinstance(scene, dict):
            scene = {}
        group_id = self._extract_group_id_from_event(event)
        group = self._get_group(group_id) if group_id else {}
        inbound_text = _single_line(
            getattr(event, "private_companion_group_text", "") or getattr(event, "message_str", "") or "",
            220,
        )
        sender_id = ""
        try:
            sender_id = str(event.get_sender_id())
        except Exception:
            sender_id = ""
        flow_formatter = getattr(self, "_format_group_recent_flow_for_review", None)
        recent_flow = (
            flow_formatter(group, sender_id=sender_id, text=inbound_text, max_lines=12, max_chars=1400)
            if callable(flow_formatter)
            else ""
        )
        wakeup = group.get("last_group_wakeup") if isinstance(group.get("last_group_wakeup"), dict) else {}
        intro_section = prompt_section(
            key="background.group_question_wakeup_review.intro",
            title="群唤醒回复发送前复核",
            source="main",
            content=(
                "判断这条群聊回复是否应该在发送前拦截。\n\n"
                "只输出 JSON 对象，不要解释。\n\n"
                "可选 decision：\n"
                "- send：确实是在自然回答群里的公共求助/开放问题，可以发送。\n"
                "- drop：像 Bot 碰瓷插话，或问题明显是在接群友的话、问别人、吐槽/反问，不该发送。\n\n"
                "判断标准：\n"
                "- 没有明确 @ Bot 或引用 Bot 时，要更保守。\n"
                "- 如果触发句只是“为什么/啥情况/怎么回事/不会吧？”这类接话、吐槽、反问，通常 drop。\n"
                "- 如果是“有没有人懂/谁会/求问/报错/怎么解决/帮忙”这类公共求助，通常 send。\n"
                "- 如果待发送内容虽然正确，但当前群聊并不需要 Bot 插入，也应 drop。"
            ),
        )
        context_sections = [
            prompt_section(
                key="background.group_question_wakeup_review.wakeup",
                title="本轮群唤醒",
                source="main",
                content=(
                    f"trigger={_single_line(scene.get('trigger'), 40)} reason={_single_line(scene.get('reason'), 60)}\n"
                    f"wakeup_type={_single_line(wakeup.get('type'), 40)} score={_single_line(wakeup.get('score'), 20)}/{_single_line(wakeup.get('threshold'), 20)} detail={_single_line(wakeup.get('reason_detail'), 160)}"
                ),
            ),
            prompt_section(
                key="background.group_question_wakeup_review.history",
                title="真实最近群聊",
                source="main",
                content=recent_flow or "（无）",
            ),
            prompt_section(
                key="background.group_question_wakeup_review.trigger",
                title="触发消息",
                source="main",
                content=inbound_text,
            ),
            prompt_section(
                key="background.group_question_wakeup_review.reply",
                title="待发送回复",
                source="main",
                content=_single_line(reply_text, 360),
            ),
        ]
        output_section = prompt_section(
            key="background.group_question_wakeup_review.output",
            title="输出契约",
            source="main",
            content='请输出：\n{"decision":"send|drop","reason":"一句很短的原因"}',
        )
        prompt = "\n\n".join(
            (
                render_prompt_sections([intro_section], mode=PromptRenderMode.BODY_ONLY),
                render_prompt_sections(context_sections, mode=PromptRenderMode.LABELED_BLOCK),
                render_prompt_sections([output_section], mode=PromptRenderMode.BODY_ONLY),
            )
        )
        started = time.perf_counter()
        raw = await self._llm_call(
            prompt,
            max_tokens=120,
            provider_id=provider_id,
            task="group_question_wakeup_reply_review",
        )
        payload = self._parse_json_object(raw)
        decision = str((payload or {}).get("decision") or "").strip().lower()
        reason = _single_line((payload or {}).get("reason"), 120)
        if decision not in {"send", "drop"}:
            decision = "send"
            reason = reason or "复核输出不可解析，默认放行"
        logger.info(
            "群聊答疑回复发送前复核: decision=%s elapsed=%dms reason=%s trigger=%s text=%s",
            decision,
            int((time.perf_counter() - started) * 1000),
            reason,
            _single_line(scene.get("trigger"), 40),
            _single_line(reply_text, 140),
        )
        if recent_flow:
            logger.info(
                "群聊答疑复核已附带真实群聊上下文: group=%s lines=%s chars=%s",
                group_id or "-",
                len([line for line in recent_flow.splitlines() if line.strip()]),
                len(recent_flow),
            )
        return {"decision": decision, "reason": reason}
