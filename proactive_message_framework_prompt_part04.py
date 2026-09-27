# -*- coding: utf-8 -*-
"""ProactiveMessageFrameworkPromptPart04Mixin。

由 tools/split_mixin_domain.py 从 proactive_message_framework_prompt.py 机械抽取（6 个方法 + 0 个模块级名字 + 0 个类级赋值 / 389 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveMessageFrameworkPromptMixin）。
"""
from __future__ import annotations

from .proactive_message_framework_prompt_shared import logger
from .proactive_message_framework_prompt_shared import Any
from .proactive_message_framework_prompt_shared import AstrMessageEvent
from .proactive_message_framework_prompt_shared import PromptDocument
from .proactive_message_framework_prompt_shared import PromptDocumentPart
from .proactive_message_framework_prompt_shared import PromptLabelStyle
from .proactive_message_framework_prompt_shared import PromptRenderMode
from .proactive_message_framework_prompt_shared import PromptSection
from .proactive_message_framework_prompt_shared import _PROACTIVE_DOCUMENT_RENDER
from .proactive_message_framework_prompt_shared import _persona_provider_id
from .proactive_message_framework_prompt_shared import _proactive_prompt_part
from .proactive_message_framework_prompt_shared import _safe_int
from .proactive_message_framework_prompt_shared import _single_line
from .proactive_message_framework_prompt_shared import _strip_internal_message_blocks
from .proactive_message_framework_prompt_shared import prompt_document
from .proactive_message_framework_prompt_shared import prompt_section
from .proactive_message_framework_prompt_shared import re
from .proactive_message_framework_prompt_shared import render_prompt_document
from .proactive_message_framework_prompt_shared import render_prompt_sections
from .proactive_message_framework_prompt_shared import runtime_persona_setting



class ProactiveMessageFrameworkPromptPart04Mixin:
    """ProactiveMessageFrameworkPromptPart04Mixin（从 ProactiveMessageFrameworkPromptMixin 拆出）。"""


    @staticmethod
    def _fit_proactive_history_lines(lines: list[str], max_chars: int) -> list[str]:
        budget = max(0, int(max_chars))
        if not lines or budget <= 0:
            return []
        kept_reversed: list[str] = []
        used = 0
        for raw_line in reversed(lines):
            line = str(raw_line or "").strip()
            if not line:
                continue
            separator = 1 if kept_reversed else 0
            available = budget - used - separator
            if available <= 0:
                break
            if len(line) <= available:
                kept_reversed.append(line)
                used += separator + len(line)
                continue
            if not kept_reversed:
                kept_reversed.append(line[:available].rstrip())
            break
        return list(reversed([line for line in kept_reversed if line]))

    def _format_proactive_history_context(self, lines: list[str]) -> str:
        cleaned_lines = [str(line or "").strip() for line in lines if str(line or "").strip()]
        if not cleaned_lines:
            return ""
        mode = str(
            runtime_persona_setting(self, "proactive_history_context_mode", "compact")
            or "compact"
        ).strip().lower()
        if mode not in {"recent_only", "compact", "expanded"}:
            mode = "compact"
        recent_count = _safe_int(
            runtime_persona_setting(self, "proactive_history_recent_raw_count", 8),
            8,
            1,
            50,
        )
        max_chars = _safe_int(
            runtime_persona_setting(self, "proactive_history_max_chars", 6000),
            6000,
            500,
            20000,
        )

        if mode == "recent_only":
            fitted = self._fit_proactive_history_lines(cleaned_lines[-recent_count:], max_chars)
            return "\n".join(fitted)
        if mode == "expanded":
            fitted = self._fit_proactive_history_lines(cleaned_lines, max_chars)
            return "\n".join(fitted)

        def history_section(key: str, title: str, content: str) -> PromptSection:
            return prompt_section(
                key=key,
                title=title,
                source="proactive_message",
                content=content,
            )

        def labeled_overhead(key: str, title: str) -> int:
            probe = render_prompt_sections(
                [history_section(key, title, "x")],
                mode=PromptRenderMode.LABELED_BLOCK,
            )
            return len(probe) - 1

        recent_lines = cleaned_lines[-recent_count:]
        older_lines = [_single_line(line, 160) for line in cleaned_lines[:-recent_count]]
        recent_key = "proactive.history.recent"
        recent_title = "最近对话（保留原文）"
        recent_overhead = labeled_overhead(recent_key, recent_title)
        recent_budget = max(0, max_chars - recent_overhead)
        fitted_recent = self._fit_proactive_history_lines(recent_lines, recent_budget)
        recent_block = render_prompt_sections(
            [history_section(recent_key, recent_title, "\n".join(fitted_recent))],
            mode=PromptRenderMode.LABELED_BLOCK,
        )
        if not older_lines:
            return recent_block[:max_chars]

        older_key = "proactive.history.older"
        older_title = "较早对话（已压缩）"
        older_overhead = labeled_overhead(older_key, older_title)
        remaining = max_chars - len(recent_block) - older_overhead - 1
        fitted_older = self._fit_proactive_history_lines(older_lines, remaining)
        if not fitted_older:
            return recent_block[:max_chars]
        older_block = render_prompt_sections(
            [history_section(older_key, older_title, "\n".join(fitted_older))],
            mode=PromptRenderMode.LABELED_BLOCK,
        )
        return f"{older_block}\n{recent_block}"[:max_chars]

    async def _recent_private_conversation_for_proactive_review(
        self,
        user: dict[str, Any],
        *,
        limit: int = 10,
    ) -> str:
        umo = str(user.get("umo") or "").strip()
        lines: list[str] = []
        if umo:
            try:
                conv = await self._get_current_conversation_safely(umo, label="proactive_review_history_read")
                history = self._load_conversation_history_items(conv, tail_only=max(1, limit))
                for item in history[-max(1, limit):]:
                    line = self._format_history_item_for_summary(item)
                    if line:
                        lines.append(line)
            except Exception as exc:
                logger.debug("主动润色读取私聊历史失败: %s", _single_line(exc, 120))
        if not lines:
            last_user = _single_line(user.get("last_user_message"), 180)
            last_bot = _single_line(user.get("last_companion_message"), 180)
            if last_bot:
                lines.append(f"{runtime_persona_setting(self, 'bot_name', '小星')}: {last_bot}")
            if last_user:
                lines.append(f"用户: {last_user}")
        return self._format_proactive_history_context(lines[-max(1, limit):])

    def _clean_persona_reference_rewrite_text(self, text: Any, *, limit: int = 160) -> str:
        cleaned = self._sanitize_proactive_text(str(text or ""))
        if not cleaned:
            return ""
        cleaned = _strip_internal_message_blocks(
            cleaned,
            enabled=bool(runtime_persona_setting(self, "enable_framework_error_leak_guard", True)),
            tts_enabled=bool(runtime_persona_setting(self, "enable_tts_enhancement", False)),
        )
        cleaned = self._strip_parenthetical_stage_directions(cleaned)
        cleaned = re.sub(r"^(?:最终(?:聊天)?正文|正文|输出|回复)[:：]\s*", "", cleaned).strip()
        cleaned = re.sub(r"\s+", " ", cleaned).strip().strip('"').strip("'")
        if not cleaned:
            return ""
        forbidden = (
            "参考意图", "参考文案", "兜底", "模板", "系统", "提示词", "工具调用",
            "执行状态", "已发送给用户", "消息已发送", "发送成功", "无文字",
        )
        if any(token in cleaned for token in forbidden):
            return ""
        if self._framework_agent_meta_summary_leak(cleaned):
            return ""
        return _single_line(cleaned, limit)

    @staticmethod
    def _reference_rewrite_prompt_document(
        *,
        persona: str,
        style_title: str,
        reply_style: str,
        history: str,
        recipient_identity: str,
        scene: str,
        reference: str,
        creative_excerpt_rule: str,
        status_rule: str,
        segmenting_section: PromptSection | None = None,
    ) -> PromptDocument:
        sections: list[PromptSection | PromptDocumentPart] = [
            _proactive_prompt_part(prompt_section(
                key="background.reference_rewrite.task",
                title="人格参考意图改写",
                source="proactive_message",
                content=(
                    "你要把一条“参考意图”改写成当前人格会自然说出的聊天正文。"
                    "参考意图只说明要表达什么，不是要照抄的句子。"
                ),
            ), mode=PromptRenderMode.BODY_ONLY),
            prompt_section(
                key="background.reference_rewrite.persona",
                title="当前人格",
                source="proactive_message",
                content=persona or "保持自然、简洁、有边界。",
            ),
            prompt_section(
                key="background.reference_rewrite.style",
                title=style_title,
                source="proactive_message",
                content=reply_style or "像日常聊天一样短一点，不要报告式。",
            ),
            prompt_section(
                key="background.reference_rewrite.history",
                title="最近对话",
                source="proactive_message",
                content=history or "（无可用历史）",
            ),
            prompt_section(
                key="background.reference_rewrite.recipient",
                title="当前收件人",
                source="proactive_message",
                content=(
                    recipient_identity
                    or "当前收件人身份未知；不要猜测名字或套用人格中的专属称呼。"
                ),
            ),
            prompt_section(
                key="background.reference_rewrite.scene",
                title="场景",
                source="proactive_message",
                content=scene or "普通聊天回执",
            ),
            prompt_section(
                key="background.reference_rewrite.intent",
                title="参考意图",
                source="proactive_message",
                content=reference,
            ),
            _proactive_prompt_part(prompt_section(
                key="background.reference_rewrite.rules",
                title="要求",
                source="proactive_message",
                content=(
                    "- 只输出最终聊天正文，不要解释。\n"
                    "- 1 句，最多 2 句；尽量像这个人格平时聊天，不要像客服、公告或模板。\n"
                    "- 不要照抄参考意图里的固定说法；只保留事实和语义。\n"
                    "- 不要出现“参考/兜底/模板/系统/工具/执行/已发送给用户/消息已发送”等字样。\n"
                    "- 不要新增事实、承诺、动作小剧场或没有发生的状态。\n"
                    f"{creative_excerpt_rule}\n"
                    "- 如果参考意图或模型结果包含 Provider/API 报错、内容策略拒绝、敏感词提示、政策链接或内部诊断，"
                    "视为本轮失败并输出空文本；不要翻译、复述或润色这类内容。\n"
                    f"{status_rule}"
                ),
            ), label_style=PromptLabelStyle.FULLWIDTH_COLON),
        ]
        if segmenting_section is not None:
            sections.append(
                _proactive_prompt_part(
                    segmenting_section,
                    mode=PromptRenderMode.CONVERSATION_XML,
                )
            )
        return prompt_document(
            user_render=_PROACTIVE_DOCUMENT_RENDER,
            user=sections,
            metadata={"task": "proactive_reference_rewrite"},
        )

    async def _rewrite_reference_reply_with_persona(
        self,
        reference_text: str,
        *,
        scene: str = "",
        user: dict[str, Any] | None = None,
        event: AstrMessageEvent | None = None,
        history: str = "",
        fallback_text: str = "",
        task: str = "persona_reference_rewrite",
        max_chars: int = 120,
        allow_fallback: bool = False,
        preserve_status: bool = False,
    ) -> str:
        reference = _single_line(reference_text, 420)
        if not reference:
            return _single_line(fallback_text, max_chars) if allow_fallback else ""
        umo = ""
        if event is not None:
            umo = str(getattr(event, "unified_msg_origin", "") or "").strip()
        if not umo and isinstance(user, dict):
            umo = str(user.get("umo") or "").strip()
        persona = await self._resolve_proactive_persona_prompt(user, umo=umo)
        proactive_rewrite = str(task or "").startswith("proactive")
        if proactive_rewrite:
            voice_sections_getter = getattr(self, "_format_proactive_voice_prompt_sections", None)
            if callable(voice_sections_getter):
                reply_style = render_prompt_sections(
                    voice_sections_getter(),
                    mode=PromptRenderMode.LABELED_BLOCK,
                )
            else:
                voice_getter = getattr(self, "_format_proactive_voice_prompt", None)
                reply_style = voice_getter() if callable(voice_getter) else ""
            expression_section_getter = getattr(self, "_format_expression_voice_prompt_section", None)
            expression_section = (
                expression_section_getter(
                    scope="proactive",
                    target_id=(
                        _single_line(user.get("user_id") or user.get("id"), 80)
                        if isinstance(user, dict)
                        else ""
                    ),
                    context_owner=user if isinstance(user, dict) else None,
                    stage_owner=user if isinstance(user, dict) else None,
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
                        target_id=(
                            _single_line(user.get("user_id") or user.get("id"), 80)
                            if isinstance(user, dict)
                            else ""
                        ),
                        context_owner=user if isinstance(user, dict) else None,
                        stage_owner=user if isinstance(user, dict) else None,
                    )
                    if callable(expression_formatter)
                    else ""
                )
            if expression_voice:
                reply_style = f"{reply_style}\n\n{expression_voice}".strip()
        else:
            reply_style = self._format_reply_style_prompt()
        if not history and isinstance(user, dict):
            try:
                history_limit = self._proactive_history_limit("generation") if proactive_rewrite else 6
                history = await self._recent_private_conversation_for_proactive_review(user, limit=history_limit)
            except Exception:
                history = ""
        recipient_identity = self._proactive_recipient_identity_prompt_text(
            user,
            _single_line(user.get("nickname"), 40) if isinstance(user, dict) else "",
        )
        creative_excerpt_rule = (
            "- 若参考意图包含创作原文且决定引用，只能连续摘取来源原文，并用一组成对的 `「...」` 包住；"
            "聊天式引入和收尾留在 `「」` 外，不得改写或另编作品片段。"
            if "创作" in str(scene or "")
            else ""
        )
        segmenting_section: PromptSection | None = None
        if proactive_rewrite and self._proactive_llm_segmenting_allowed(umo=umo):
            segmenting_getter = getattr(self, "_llm_controlled_segmenting_prompt_section", None)
            if callable(segmenting_getter):
                candidate = segmenting_getter()
                if isinstance(candidate, PromptSection):
                    segmenting_section = candidate
        prompt = render_prompt_document(
            self._reference_rewrite_prompt_document(
                persona=persona,
                style_title="主动开口风格" if proactive_rewrite else "回复风格",
                reply_style=reply_style,
                history=history,
                recipient_identity=recipient_identity,
                scene=_single_line(scene, 180),
                reference=reference,
                creative_excerpt_rule=creative_excerpt_rule,
                status_rule=(
                    "- 必须保留成功/失败/等待/完成/稍后再说等状态语义，不要把失败说成成功。"
                    if preserve_status
                    else "- 如果只是轻轻递一句，不要补多余解释。"
                ),
                segmenting_section=segmenting_section,
            )
        )["user"]
        try:
            raw = await self._llm_call(
                prompt,
                max_tokens=140,
                provider_id=self._task_provider(
                    _persona_provider_id(
                        self,
                        "RESPONSE_REVIEW_PROVIDER_ID",
                        "response_review_provider_id",
                        "fast",
                    ),
                    _persona_provider_id(
                        self,
                        "MAI_STYLE_PROVIDER_ID",
                        "mai_style_provider_id",
                        "fast",
                    ),
                    _persona_provider_id(
                        self,
                        "LLM_PROVIDER_ID",
                        "llm_provider_id",
                        "complex",
                    ),
                ),
                task=task,
            )
        except Exception as exc:
            logger.debug("人格参考意图改写失败: %s", _single_line(exc, 120))
            raw = ""
        if self._looks_like_internal_provider_error_text(raw):
            logger.warning(
                "人格参考意图改写收到 Provider 错误正文，已丢弃: task=%s",
                _single_line(task, 80) or "persona_reference_rewrite",
            )
            raw = ""
        cleaned = self._clean_persona_reference_rewrite_text(raw, limit=max_chars)
        if cleaned:
            return cleaned
        return _single_line(fallback_text, max_chars) if allow_fallback else ""
