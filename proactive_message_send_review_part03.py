# -*- coding: utf-8 -*-
"""ProactiveMessageSendReviewPart03Mixin。

由 tools/split_mixin_domain.py 从 proactive_message_send_review.py 机械抽取（1 个方法 + 0 个模块级名字 + 0 个类级赋值 / 511 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveMessageSendReviewMixin）。
"""
from __future__ import annotations

from .proactive_message_send_review_shared import logger
from .proactive_message_send_review_shared import Any
from .proactive_message_send_review_shared import PROACTIVE_ROUTE_REGISTRY
from .proactive_message_send_review_shared import PromptRenderMode
from .proactive_message_send_review_shared import PromptSection
from .proactive_message_send_review_shared import _persona_provider_id
from .proactive_message_send_review_shared import _safe_int
from .proactive_message_send_review_shared import _single_line
from .proactive_message_send_review_shared import asyncio
from .proactive_message_send_review_shared import render_prompt_document
from .proactive_message_send_review_shared import render_prompt_sections
from .proactive_message_send_review_shared import runtime_persona_setting
from .proactive_message_send_review_shared import time



class ProactiveMessageSendReviewPart03Mixin:
    """ProactiveMessageSendReviewPart03Mixin（从 ProactiveMessageSendReviewMixin 拆出）。"""


    async def _review_proactive_message_send_decision(
        self,
        user: dict[str, Any],
        text: str,
        *,
        reason: str,
        action: str,
        motive: str = "",
        topic: str = "",
        action_summary: str = "",
        image_path: str = "",
    ) -> dict[str, Any]:
        strength = self._proactive_review_strength()
        route_getter = getattr(self, "_proactive_route_for", None)
        route = (
            route_getter(
                reason=reason,
                source=user.get("planned_proactive_source"),
                semantic_kind=user.get("planned_proactive_semantic_kind"),
                kind=user.get("planned_proactive_kind"),
            )
            if callable(route_getter)
            else PROACTIVE_ROUTE_REGISTRY.route_for(
                reason=reason,
                source=user.get("planned_proactive_source"),
                semantic_kind=user.get("planned_proactive_semantic_kind"),
                kind=user.get("planned_proactive_kind"),
            )
        )
        review_context = _single_line(action_summary, 240)
        if image_path:
            review_context = _single_line(f"{review_context}\n真实图片文件：{image_path}", 360)

        def normalize_review_result(payload: dict[str, Any], *, source: str) -> dict[str, Any]:
            return self._normalize_proactive_review_decision_policy(
                user,
                payload,
                strength=strength,
                source=source,
                reason=reason,
                action=action,
                topic=topic,
                motive=motive,
                action_context=review_context,
                image_path=image_path,
                original_text=text,
            )

        def local_model_fallback(fallback_reason: str) -> dict[str, Any]:
            result = normalize_review_result(local, source="local")
            result["review_fallback"] = True
            result["review_fallback_reason"] = _single_line(fallback_reason, 180)
            return result

        local = self._local_proactive_send_decision(
            user,
            text,
            reason=reason,
            action=action,
            motive=motive,
            topic=topic,
            action_context=review_context,
        )
        local_decision = str(local.get("decision") or "send").strip().lower()
        local_hard_block = bool(local.get("hard")) or self._proactive_review_hard_block_reason(_single_line(local.get("reason"), 120))
        if route.key == "transactional" and local_decision in {"drop", "defer"} and not local_hard_block:
            local = {
                "decision": "send",
                "text": "",
                "reason": "事务路线保留原始提醒事实，忽略通用低价值软拦截",
            }
            local_decision = "send"
        review_enabled = bool(
            runtime_persona_setting(self, "enable_proactive_message_review", True)
        )
        review_mode = self._effective_proactive_review_mode()
        if not review_enabled:
            local_mode_label = "主动发送前审核未启用"
            if local_decision in {"drop", "defer"}:
                if local_hard_block:
                    return normalize_review_result(local, source="local")
                return {
                    "decision": "send",
                    "text": "",
                    "reason": f"{local_mode_label}，已跳过非安全性的本地软拦截",
                }
            if local_decision == "rewrite":
                local_rewrite_text = str(local.get("text") or "").strip()
                if local_rewrite_text:
                    local_result = normalize_review_result(local, source="local")
                    local_result["reason"] = _single_line(
                        f"{local_mode_label}，已采用本地确定性改写："
                        + (_single_line(local.get("reason"), 80) or "轻量清理"),
                        120,
                    )
                    return local_result
                if not local_hard_block:
                    return {
                        "decision": "send",
                        "text": "",
                        "reason": f"{local_mode_label}，本地软建议未形成确定改写，保留原文",
                    }
                return {
                    "decision": "drop",
                    "text": "",
                    "reason": f"{local_mode_label}，本地检查仅能提供参考意图，无法形成确定正文，已取消本轮发送",
                    "hard": True,
                }
            return {
                "decision": "send",
                "text": "",
                "reason": f"{local_mode_label}，本地检查允许原文发送",
            }
        if review_mode == "local_only":
            local_mode_label = "仅本地检查模式"
            if local_decision in {"drop", "defer"}:
                return normalize_review_result(local, source="local")
            if local_decision == "rewrite":
                local_rewrite_text = str(local.get("text") or "").strip()
                if local_rewrite_text:
                    local_result = normalize_review_result(local, source="local")
                    local_result["reason"] = _single_line(
                        f"{local_mode_label}，已采用本地确定性改写："
                        + (_single_line(local.get("reason"), 80) or "轻量清理"),
                        120,
                    )
                    return local_result
                return {
                    "decision": "drop",
                    "text": "",
                    "reason": f"{local_mode_label}，本地检查仅能提供参考意图，无法形成确定正文，已取消本轮发送",
                    "hard": True,
                }
            return {
                "decision": "send",
                "text": "",
                "reason": f"{local_mode_label}，本地检查允许原文发送",
            }
        if local_decision in {"drop", "defer"} and local_hard_block:
            return normalize_review_result(local, source="local")
        if local.get("decision") == "rewrite" and str(local.get("reference_text") or "").strip():
            rewrite_scene = _single_line(
                "自然地向用户分享自己刚看的这条内容；保留真实标题、来源和链接"
                if reason in {"bili_video_share", "news_share", "web_exploration_share"}
                else f"主动消息改写；reason={reason or 'check_in'}；action={action or 'message'}",
                180,
            )
            rewritten_reference = await self._rewrite_reference_reply_with_persona(
                str(local.get("reference_text") or ""),
                scene=rewrite_scene,
                user=user,
                fallback_text="",
                task="proactive_reference_rewrite",
                max_chars=140,
                allow_fallback=False,
            )
            if rewritten_reference:
                accepted_reference = self._accept_proactive_rewrite(
                    rewritten_reference,
                    original_text=str(local.get("reference_text") or ""),
                    user=user,
                    reason=reason,
                    action=action,
                    topic=topic,
                    motive=motive,
                    action_context=review_context,
                    image_path=image_path,
                )
                if accepted_reference is None:
                    safe_reference = _single_line(local.get("reference_text"), 300)
                    accepted_reference = self._accept_proactive_rewrite(
                        safe_reference,
                        original_text=safe_reference,
                        user=user,
                        reason=reason,
                        action=action,
                        topic=topic,
                        motive=motive,
                        action_context=review_context,
                        image_path=image_path,
                    )
                    if accepted_reference:
                        logger.info(
                            "主动外界分享人格润色未通过统一验收，已使用确定性来源文本: reason=%s",
                            reason,
                        )
                rewritten_reference = accepted_reference or ""
            if rewritten_reference:
                local = dict(local)
                local["text"] = rewritten_reference
                local.pop("reference_text", None)
            else:
                return {
                    "decision": "drop",
                    "reason": _single_line(local.get("reason"), 80) or "兜底参考意图未能按人格改写",
                    "hard": True,
                }
        if local.get("decision") == "rewrite" and bool(local.get("hard")):
            return normalize_review_result(local, source="local")
        if local_decision == "drop":
            return normalize_review_result(local, source="local")
        if review_mode == "severe_only" and local_decision == "send" and not local_hard_block:
            return {
                "decision": "send",
                "text": "",
                "reason": "主动终审严重问题模式：本地检查通过",
            }
        persona = await self._resolve_proactive_persona_prompt(user)
        history = await self._recent_private_conversation_for_proactive_review(
            user,
            limit=self._proactive_history_limit("review"),
        )
        intent_hint = self._format_proactive_generation_intent_hint(
            user,
            reason=reason,
            action=action,
            motive=motive,
            action_context=review_context,
        )
        proactive_voice_sections_getter = getattr(self, "_format_proactive_voice_prompt_sections", None)
        proactive_voice = (
            render_prompt_sections(
                proactive_voice_sections_getter(),
                mode=PromptRenderMode.LABELED_BLOCK,
            )
            if callable(proactive_voice_sections_getter)
            else ""
        )
        if not callable(proactive_voice_sections_getter):
            proactive_voice_getter = getattr(self, "_format_proactive_voice_prompt", None)
            proactive_voice = proactive_voice_getter() if callable(proactive_voice_getter) else ""
        expression_section_getter = getattr(self, "_format_expression_voice_prompt_section", None)
        expression_section = (
            expression_section_getter(
                scope="proactive",
                target_id=_single_line(user.get("user_id") or user.get("id"), 80),
                context_owner=user,
                stage_owner=user,
            )
            if callable(expression_section_getter)
            else None
        )
        expression_voice = (
            render_prompt_sections([expression_section], mode=PromptRenderMode.LABELED_BLOCK)
            if isinstance(expression_section, PromptSection)
            else ""
        )
        if not callable(expression_section_getter):
            expression_formatter = getattr(self, "_format_expression_voice_for_prompt", None)
            expression_voice = (
                expression_formatter(
                    scope="proactive",
                    target_id=_single_line(user.get("user_id") or user.get("id"), 80),
                    context_owner=user,
                    stage_owner=user,
                )
                if callable(expression_formatter)
                else ""
            )
        recipient_identity = self._proactive_recipient_identity_prompt_text(
            user,
            _single_line(user.get("nickname"), 40),
        )
        runtime_context = self._format_proactive_review_runtime_context(user)
        troubleshooting_hint = self._proactive_troubleshooting_request_hint(user)
        has_verified_fact_source = self._proactive_has_verified_recent_fact_source(
            reason=reason,
            action=action,
            action_context=review_context,
        )
        fact_source_context = (
            f"本轮存在可核验动作/来源：{review_context}"
            if has_verified_fact_source
            else "本轮没有可核验的近期动作或外部来源；不得声称自己刚刚看见、刷到、听到、收到或完成了某件事。"
        )
        local_context = "；".join(
            part
            for part in (
                f"本地结论={local_decision or 'send'}",
                f"说明={_single_line(local.get('reason'), 100)}" if local.get("reason") else "",
                "硬风险=yes" if local_hard_block else "硬风险=no",
                f"本地建议文本={_single_line(local.get('text'), 120)}" if local.get("text") else "",
            )
            if part
        )
        route_review_directive = route.review_directive()
        persona_context = (
            "(Creative-share compact review: use the proactive voice and excerpt rule below; do not restate the full persona.)"
            if reason == "creative_share"
            else self._truncate_proactive_context(persona, 2600)
        ) if persona else "(No explicit persona was resolved. Preserve the candidate instead of inventing a new voice.)"
        creative_excerpt_section = (
            self._creative_share_excerpt_prompt_section()
            if reason == "creative_share"
            else None
        )
        source_context = (
            f"route={route.key}({route.label}); review_profile={route.review_profile}; "
            f"reason={reason or 'check_in'}; action={action or 'message'}; "
            f"topic={_single_line(topic, 80) or 'none'}; "
            f"motive={_single_line(motive, 120) or 'none'}; "
            f"summary={_single_line(action_summary, 80) or 'none'}"
        )
        prompt = render_prompt_document(
            self._proactive_send_review_prompt_document(
                creative_excerpt_section=creative_excerpt_section,
                history=history,
                runtime_context=runtime_context,
                troubleshooting_context=troubleshooting_hint,
                fact_source_context=fact_source_context,
                local_context=local_context,
                source_context=source_context,
                route_review_directive=route_review_directive,
                persona_context=persona_context,
                intent_hint=intent_hint,
                proactive_voice=proactive_voice,
                expression_voice=expression_voice,
                recipient_identity=recipient_identity,
                candidate=text,
            )
        )["user"]
        started = time.perf_counter()
        review_provider_id = self._task_provider(
            _persona_provider_id(self, "RESPONSE_REVIEW_PROVIDER_ID", "response_review_provider_id", "fast"),
            _persona_provider_id(self, "MAI_STYLE_PROVIDER_ID", "mai_style_provider_id", "fast"),
        )
        timeout_seconds = 8.0
        timeout_getter = getattr(self, "_model_timeout_seconds_for_call", None)
        timeout_override = (
            timeout_getter(
                task="proactive_send_review",
                provider_id=review_provider_id,
                timeout_key="RESPONSE_REVIEW_PROVIDER_ID",
            )
            if callable(timeout_getter)
            else None
        )
        if timeout_override is not None:
            timeout_seconds = float(timeout_override)
        try:
            raw = await asyncio.wait_for(
                self._llm_call(
                    prompt,
                    max_tokens=220,
                    provider_id=review_provider_id,
                    task="proactive_send_review",
                ),
                timeout=timeout_seconds,
            )
        except Exception as exc:
            now = time.time()
            last_log_at = float(getattr(self, "_proactive_review_fallback_log_at", 0.0) or 0.0)
            if now - last_log_at >= 600:
                self._proactive_review_fallback_log_at = now
                logger.info(
                    "主动最终内容复核模型暂不可用，已安全回退本地复核（同类日志 10 分钟内不重复）: %s",
                    self._format_send_exception(exc),
                )
            return local_model_fallback(self._format_send_exception(exc))
        payload = self._parse_json_object(raw)
        if not isinstance(payload, dict):
            return local_model_fallback("复核模型未返回有效 JSON")
        decision = str(payload.get("decision") or "").strip().lower()
        if decision not in {"send", "rewrite", "drop"}:
            return local_model_fallback("复核模型返回了无效 decision")
        reviewed_text = str(payload.get("text") or "").strip()
        note = _single_line(payload.get("reason"), 120)
        original_decision = decision
        if decision == "rewrite":
            if not reviewed_text:
                return local_model_fallback("复核模型要求改写但未返回正文")
            accepted_text = self._accept_proactive_rewrite(
                reviewed_text,
                original_text=text,
                user=user,
                reason=reason,
                action=action,
                topic=topic,
                motive=motive,
                action_context=review_context,
                image_path=image_path,
            )
            if accepted_text is None and reason in {"bili_video_share", "news_share", "web_exploration_share"}:
                original_safe = self._accept_proactive_rewrite(
                    text,
                    original_text=text,
                    user=user,
                    reason=reason,
                    action=action,
                    topic=topic,
                    motive=motive,
                    action_context=review_context,
                    image_path=image_path,
                )
                safe_reference = original_safe or self._external_share_fallback_reference(
                    _single_line(topic or action_summary or text, 300),
                )
                accepted_text = self._accept_proactive_rewrite(
                    safe_reference,
                    original_text=safe_reference,
                    user=user,
                    reason=reason,
                    action=action,
                    topic=topic,
                    motive=motive,
                    action_context=review_context,
                    image_path=image_path,
                ) if safe_reference else None
                if accepted_text:
                    note = "终审改写未通过统一验收，已恢复复核前或确定性来源文本"
            if accepted_text is None:
                original_safe = self._accept_proactive_rewrite(
                    text,
                    original_text=text,
                    user=user,
                    reason=reason,
                    action=action,
                    topic=topic,
                    motive=motive,
                    action_context=review_context,
                    image_path=image_path,
                )
                if original_safe:
                    decision = "send"
                    reviewed_text = ""
                    note = "终审改写未通过统一验收，已保留完整原候选"
                else:
                    local_result = normalize_review_result(local, source="local")
                    local_result["review_model_ok"] = True
                    return local_result
            else:
                reviewed_text = accepted_text
        normalized_payload = self._normalize_proactive_review_decision_policy(
            user,
            {
                "decision": decision,
                "text": reviewed_text,
                "reason": note,
                "delay_minutes": payload.get("delay_minutes", 0),
            },
            strength=strength,
            source="model",
            reason=reason,
            action=action,
            topic=topic,
            motive=motive,
            action_context=review_context,
            image_path=image_path,
            original_text=text,
        )
        decision = str(normalized_payload.get("decision") or decision).strip().lower()
        reviewed_text = str(normalized_payload.get("text") or reviewed_text or "").strip()
        note = _single_line(normalized_payload.get("reason") or note, 120)
        if decision == "send" and str(local.get("decision") or "") == "rewrite" and str(local.get("text") or "").strip():
            local_text = self._accept_proactive_rewrite(
                str(local.get("text") or "").strip(),
                original_text=text,
                user=user,
                reason=reason,
                action=action,
                topic=topic,
                motive=motive,
                action_context=review_context,
                image_path=image_path,
            )
            if local_text:
                reviewed_text = local_text
                decision = "rewrite"
                note = _single_line(note or local.get("reason") or "本地轻改写后放行", 120)
            else:
                reviewed_text = ""
                decision = "send"
                note = _single_line(note or "本地改写未通过统一验收，保留原候选", 120)
        if str(local.get("decision") or "").strip().lower() == "defer" and not local_hard_block:
            delay_minutes = max(5, min(240, _safe_int(local.get("delay_minutes"), 60, 5, 240)))
            if decision in {"send", "rewrite"}:
                return {
                    "decision": "defer",
                    "text": "",
                    "delay_minutes": delay_minutes,
                    "reason": _single_line(
                        f"本地时机判断保留延后 {delay_minutes} 分钟：{local.get('reason') or '当前不宜立即发送'}",
                        180,
                    ),
                    "review_model_ok": True,
                }
        final_text = reviewed_text if decision == "rewrite" and reviewed_text else text
        link_platform_mismatch = self._proactive_link_platform_mismatch_reason(final_text)
        if decision in {"send", "rewrite"} and link_platform_mismatch:
            decision = "drop"
            reviewed_text = ""
            note = link_platform_mismatch
        if decision in {"send", "rewrite"} and self._framework_agent_meta_summary_leak(final_text):
            decision = "drop"
            reviewed_text = ""
            note = "主动候选疑似工具循环/内部发送摘要泄漏"
        logger.info(
            "Proactive final content gate: decision=%s raw=%s strength=%s elapsed=%dms reason=%s",
            decision,
            original_decision,
            strength,
            int((time.perf_counter() - started) * 1000),
            note or "-",
        )
        return {
            "decision": decision,
            "text": reviewed_text,
            "reason": note or "主动发送前价值复核",
            "delay_minutes": max(0, _safe_int(normalized_payload.get("delay_minutes"), 0, 0, 240)),
            "review_model_ok": True,
        }
