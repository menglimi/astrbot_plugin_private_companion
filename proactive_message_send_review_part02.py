# -*- coding: utf-8 -*-
"""ProactiveMessageSendReviewPart02Mixin。

由 tools/split_mixin_domain.py 从 proactive_message_send_review.py 机械抽取（5 个方法 + 0 个模块级名字 + 0 个类级赋值 / 294 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveMessageSendReviewMixin）。
"""
from __future__ import annotations

from .proactive_message_send_review_shared import _now_ts
from .proactive_message_send_review_shared import Any
from .proactive_message_send_review_shared import PromptDocument
from .proactive_message_send_review_shared import PromptDocumentPart
from .proactive_message_send_review_shared import PromptLabelStyle
from .proactive_message_send_review_shared import PromptRenderMode
from .proactive_message_send_review_shared import PromptSection
from .proactive_message_send_review_shared import _PROACTIVE_DOCUMENT_RENDER
from .proactive_message_send_review_shared import _proactive_prompt_part
from .proactive_message_send_review_shared import _safe_float
from .proactive_message_send_review_shared import _safe_int
from .proactive_message_send_review_shared import _single_line
from .proactive_message_send_review_shared import datetime
from .proactive_message_send_review_shared import prompt_document
from .proactive_message_send_review_shared import prompt_section
from .proactive_message_send_review_shared import re



class ProactiveMessageSendReviewPart02Mixin:
    """ProactiveMessageSendReviewPart02Mixin（从 ProactiveMessageSendReviewMixin 拆出）。"""


    def _stale_proactive_review_defer_release_reason(
        self,
        user: dict[str, Any],
        *,
        note: str = "",
        reason: str = "",
        now: float | None = None,
    ) -> str:
        note_text = _single_line(note, 120)
        reason_key = _single_line(reason, 40).lower()
        time_sensitive_reasons = {
            "morning_greeting": 12 * 60,
            "noon_greeting": 15 * 60,
            "evening_greeting": 23 * 60 + 30,
            "environment_change": 120,
            "creative_share": 180,
            "reminder": 180,
            "meal_care": 120,
            "meal_care_followup": 120,
            "weather_alert": 180,
        }
        if not note_text and not reason_key:
            return ""
        check_now = _now_ts() if now is None else now
        if reason_key in time_sensitive_reasons:
            window_start = _safe_float(user.get("planned_proactive_window_start_at"), 0)
            expire_at = _safe_float(user.get("planned_proactive_expire_at"), 0)
            if expire_at > 0 and check_now >= expire_at:
                return "主动候选的有效窗口已结束，放弃过期复核结果并重新编排"
            if window_start > 0 and check_now - window_start >= time_sensitive_reasons[reason_key] * 60:
                return "主动候选已超过当前场景有效期，放弃过期复核结果并重新编排"
        if not re.search(r"(早安|今早|早上|睡前|晚安)", note_text):
            return ""
        now_minutes = datetime.fromtimestamp(check_now).hour * 60 + datetime.fromtimestamp(check_now).minute
        stale_after = 9 * 60 if ("睡前" in note_text or "晚安" in note_text) else 12 * 60
        if now_minutes < stale_after:
            return ""
        recent_private_at = max(
            _safe_float(user.get("last_user_message_at"), 0),
            _safe_float(user.get("last_private_seen"), 0),
        )
        if recent_private_at > 0 and check_now - recent_private_at < 45 * 60:
            return ""
        return "复核理由沿用了过期早间/睡前语境，已改按当前运行态放行"

    @staticmethod
    def _proactive_rewrite_blacklist_reason(text: str) -> str:
        """Reject structured model leakage without blocking ordinary chat words."""
        candidate = str(text or "")
        if not candidate:
            return ""
        patterns = (
            (r"```(?:json|python|javascript|text)?\s*", "改写残留代码块"),
            (r"\{[^{}\n]{0,600}(?:[\"'](?:decision|text|reason|status|tool|delay_minutes)[\"']\s*:)", "改写残留结构化 JSON"),
            (r"(?:作为(?:一个)?(?:AI|人工智能|语言模型)|我是(?:一个)?(?:AI|语言模型))", "改写暴露模型身份"),
            (r"(?:系统提示|系统消息|提示词泄漏|模型输出|模型回复|工具调用|调用工具|主动消息复核|附加组件(?:发送|列表))", "改写残留内部流程"),
            (r"[\"'](?:decision|text|reason|delay_minutes|planned_reason)[\"']\s*:", "改写残留 JSON 字段"),
        )
        for pattern, reason in patterns:
            if re.search(pattern, candidate, re.IGNORECASE | re.DOTALL):
                return reason
        return ""

    def _accept_proactive_rewrite(
        self,
        text: str,
        *,
        original_text: str = "",
        user: dict[str, Any] | None = None,
        reason: str = "",
        action: str = "",
        topic: str = "",
        motive: str = "",
        action_context: str = "",
        image_path: str = "",
    ) -> str | None:
        """Run the single acceptance chain shared by every proactive rewrite."""
        candidate = self._sanitize_action_boundaries(
            self._sanitize_proactive_text(str(text or "")),
            reason=reason,
            action=action,
            action_context=action_context,
            has_real_image=bool(image_path)
            or "真实图片文件：" in str(action_context or "")
            or "图片路径：" in str(action_context or ""),
        )
        candidate = self._normalize_proactive_sentence_flow(candidate)
        if not candidate:
            return None
        if re.fullmatch(
            r"[嗯哦唔呃诶欸啊呀哎噢喔哈]+[。！？!?…~～]*",
            re.sub(r"\s+", "", candidate),
        ):
            return None
        current_user = user if isinstance(user, dict) else {}
        recipient_name = _single_line(current_user.get("nickname"), 40)
        candidate, _ = self._repair_proactive_recipient_address(
            candidate,
            current_user,
            recipient_name,
        )
        if not candidate or self._wrong_proactive_recipient_address(candidate, current_user, recipient_name):
            return None
        meta_leak_checker = getattr(self, "_response_review_meta_leak_reason", None)
        if callable(meta_leak_checker) and meta_leak_checker(candidate):
            return None
        if self._framework_agent_meta_summary_leak(candidate):
            return None
        original_length = len(str(original_text or "").strip())
        if original_length and len(candidate) > max(original_length + 60, 240):
            return None
        if self._proactive_rewrite_blacklist_reason(candidate):
            return None
        if reason in {"bili_video_share", "news_share", "web_exploration_share"}:
            if self._external_share_source_consistency_decision(
                current_user,
                candidate,
                reason=reason,
                topic=topic,
                motive=motive,
                action_context=action_context,
            ):
                return None
        return candidate

    def _normalize_proactive_review_decision_policy(
        self,
        user: dict[str, Any],
        payload: dict[str, Any],
        *,
        strength: str,
        source: str = "model",
        reason: str = "",
        action: str = "",
        topic: str = "",
        motive: str = "",
        action_context: str = "",
        image_path: str = "",
        original_text: str = "",
    ) -> dict[str, Any]:
        """Normalize the final proactive content gate result."""
        if not isinstance(payload, dict):
            return {"decision": "send", "reason": "empty review result; local safety gate allowed the message"}
        decision = str(payload.get("decision") or "send").strip().lower()
        note = _single_line(payload.get("reason"), 120)
        reviewed_text = str(payload.get("text") or "").strip()
        delay_minutes = max(5, min(240, _safe_int(payload.get("delay_minutes"), 60, 5, 240)))
        if decision not in {"send", "rewrite", "defer", "drop"}:
            decision = "send"
        if decision == "rewrite" and not reviewed_text:
            decision = "drop"
            note = _single_line(f"{note or 'rewrite result is empty'}; candidate dropped", 120)
        if decision == "rewrite" and reviewed_text:
            accepted_text = self._accept_proactive_rewrite(
                reviewed_text,
                original_text=original_text,
                user=user,
                reason=reason,
                action=action,
                topic=topic,
                motive=motive,
                action_context=action_context,
                image_path=image_path,
            )
            if accepted_text is None:
                decision = "drop"
                reviewed_text = ""
                note = _single_line(f"{note or '改写未通过统一验收'}; 最终改写已拒绝", 120)
            else:
                reviewed_text = accepted_text
        return {
            "decision": decision,
            "text": reviewed_text if decision == "rewrite" else "",
            "reason": note or "proactive final content gate",
            "hard": bool(payload.get("hard")),
            "delay_minutes": delay_minutes if decision == "defer" else 0,
        }

    @staticmethod
    def _proactive_send_review_prompt_document(
        *,
        creative_excerpt_section: PromptSection | None,
        history: str,
        runtime_context: str,
        troubleshooting_context: str,
        fact_source_context: str,
        local_context: str,
        source_context: str,
        route_review_directive: str,
        persona_context: str,
        intent_hint: str,
        proactive_voice: str,
        expression_voice: str,
        recipient_identity: str,
        candidate: str,
    ) -> PromptDocument:
        def square(
            key: str,
            title: str,
            content: str,
            *,
            separator_before: str = "\n\n",
        ) -> PromptDocumentPart:
            return _proactive_prompt_part(
                prompt_section(
                    key=key,
                    title=title,
                    source="proactive_message",
                    content=content,
                ),
                label_style=PromptLabelStyle.SQUARE,
                separator_before=separator_before,
            )

        sections: list[PromptSection | PromptDocumentPart] = [
            _proactive_prompt_part(prompt_section(
                key="background.proactive_send_review.contract",
                title="主动消息发送终审",
                source="proactive_message",
                content=(
                    "You are the final content gate immediately before one proactive private message is sent.\n"
                    "Return JSON only. You must decide exactly one of send, rewrite, or drop.\n\n"
                    "Decision contract:\n"
                    "- send: the candidate is natural, persona-consistent, useful now, and ready to send unchanged. Leave text empty.\n"
                    "- rewrite: the message still has a concrete reason to exist, but needs a small rewrite to sound natural in this exact conversation. text must be the complete sendable final message.\n"
                    "- drop: do not send this candidate. Use it for weak, generic, intrusive, fabricated, context-conflicting, reply-to-nothing, internal-status, tool-result, or unsafe content.\n\n"
                    "Rules:\n"
                    "- This is a content gate, not a scheduler. Never output defer, waiting, or a delay.\n"
                    "- Read the recent conversation and runtime context first. The candidate must read like a natural message from the current persona, not a system-triggered interruption.\n"
                    "- Do not invent facts or promise tools, searches, media, relays, or actions that were not actually performed.\n"
                    "- Planned schedules, persona continuity, and message seeds are narrative inspiration, not evidence that an action happened.\n"
                    "- Relative dates such as yesterday must be supported by the recent conversation or an explicitly dated reliable source.\n"
                    "- Preserve real media context. Do not claim an image exists when none is attached.\n"
                    "- A rewrite must be shorter or similarly sized and must not add new factual claims.\n"
                    "- A rewrite must preserve the candidate's concrete communicative purpose. Never collapse a meaningful reminder, question, warning, or check-in into a standalone filler such as “嗯。”, “哦。”, “唔。”, or “诶。”. If no complete rewrite is better, choose send and keep the candidate unchanged.\n"
                    "- If a user has just been discussing something and the candidate cannot naturally fit, drop it; do not defer it.\n"
                    "- If the candidate or any model output contains a Provider/API error, policy refusal, sensitive-word notice, policy URL, or internal diagnostic, choose drop with an empty text; never translate, quote, or polish it.\n"
                    "- When the current request context says the user explicitly requested this troubleshooting message, treat that request as a concrete reason to speak. Do not drop solely because it is late, the normal proactive interval is short, or there is no spontaneous life story. If the wording is too strong or generic, prefer a shorter, softer rewrite. Fact, safety, privacy, identity, and conversation-conflict checks still apply.\n"
                    "- For a creative share, preserve any `「...」` excerpt exactly as one continuous source quote. Keep conversational introduction and closing outside it; never paraphrase or fabricate text inside the excerpt."
                ),
            ), mode=PromptRenderMode.BODY_ONLY),
        ]
        if creative_excerpt_section is not None:
            sections.append(creative_excerpt_section)
        sections.extend(
            (
                square(
                    "background.proactive_send_review.history",
                    "Recent conversation",
                    history or "(none)",
                    separator_before="\n\n\n\n" if creative_excerpt_section is None else "",
                ),
                square("background.proactive_send_review.runtime", "Runtime state", runtime_context),
                square(
                    "background.proactive_send_review.request",
                    "Current request context",
                    troubleshooting_context
                    or "(ordinary proactive message; no explicit user-requested test)",
                ),
                square("background.proactive_send_review.fact_boundary", "Verified fact boundary", fact_source_context),
                square("background.proactive_send_review.local", "Local safety result", local_context or "local gate passed"),
                square("background.proactive_send_review.source", "Proactive source", source_context),
                square("background.proactive_send_review.route", "Route-specific final gate", route_review_directive),
                square("background.proactive_send_review.persona", "Full persona", persona_context),
                square("background.proactive_send_review.intent", "Persona and intent constraints", intent_hint or "(none)"),
                square(
                    "background.proactive_send_review.voice",
                    "Proactive voice",
                    proactive_voice or "(natural, low-pressure private chat)",
                ),
                square(
                    "background.proactive_send_review.expression",
                    "Learned expression voice",
                    expression_voice or "(none)",
                ),
                square(
                    "background.proactive_send_review.recipient",
                    "Recipient identity boundary",
                    recipient_identity
                    or "Use only the current recipient identity. Do not guess or copy an exclusive name from persona examples.",
                ),
                square("background.proactive_send_review.candidate", "Candidate", candidate),
                _proactive_prompt_part(
                    prompt_section(
                        key="background.proactive_send_review.output",
                        title="Output",
                        source="proactive_message",
                        content='{"decision":"send|rewrite|drop","text":"","reason":"brief reason"}',
                    ),
                    label_style=PromptLabelStyle.COLON,
                ),
            )
        )
        return prompt_document(
            user_render=_PROACTIVE_DOCUMENT_RENDER,
            user=sections,
            metadata={"task": "proactive_send_review"},
        )
