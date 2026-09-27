# -*- coding: utf-8 -*-
"""ProactiveMessageGenerationPart02Mixin。

由 tools/split_mixin_domain.py 从 proactive_message_generation.py 机械抽取（5 个方法 + 0 个模块级名字 + 0 个类级赋值 / 464 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveMessageGenerationMixin）。
"""
from __future__ import annotations

from .proactive_message_generation_shared import logger
from .proactive_message_generation_shared import Any
from .proactive_message_generation_shared import PromptDocument
from .proactive_message_generation_shared import PromptLabelStyle
from .proactive_message_generation_shared import PromptRenderMode
from .proactive_message_generation_shared import PromptSection
from .proactive_message_generation_shared import _PROACTIVE_DOCUMENT_RENDER
from .proactive_message_generation_shared import _persona_provider_id
from .proactive_message_generation_shared import _proactive_prompt_part
from .proactive_message_generation_shared import _single_line
from .proactive_message_generation_shared import prompt_document
from .proactive_message_generation_shared import prompt_section
from .proactive_message_generation_shared import re
from .proactive_message_generation_shared import render_prompt_document
from .proactive_message_generation_shared import render_prompt_sections
from .proactive_message_generation_shared import runtime_persona_setting
from .proactive_message_generation_shared import time



class ProactiveMessageGenerationPart02Mixin:
    """ProactiveMessageGenerationPart02Mixin（从 ProactiveMessageGenerationMixin 拆出）。"""


    @staticmethod
    def _response_review_prompt_document(
        *,
        original_text: str,
        flags: str,
        reason: str,
        motive: str,
        topic: str,
        action_context: str,
        intent_hint: str,
        persona: str,
        proactive_voice: str,
        expression_voice: str,
        recipient_identity: str,
        creative_excerpt_rule: str,
    ) -> PromptDocument:
        return prompt_document(
            user_render=_PROACTIVE_DOCUMENT_RENDER,
            user=(
                _proactive_prompt_part(prompt_section(
                    key="background.response_review.task",
                    title="主动回复空气修正",
                    source="proactive_message",
                    content=(
                        "把下面这条主动私聊消息改成真正的主动开口。\n"
                        "它不是在回复用户刚发来的消息；聊天历史只能当背景。"
                    ),
                ), mode=PromptRenderMode.BODY_ONLY),
                prompt_section(
                    key="background.response_review.original",
                    title="原主动消息",
                    source="proactive_message",
                    content=original_text,
                ),
                prompt_section(
                    key="background.response_review.flags",
                    title="问题",
                    source="proactive_message",
                    content=flags,
                ),
                prompt_section(
                    key="background.response_review.reason",
                    title="主动原因",
                    source="proactive_message",
                    content=reason or "check_in",
                ),
                prompt_section(
                    key="background.response_review.motive_topic",
                    title="动机/话题",
                    source="proactive_message",
                    content=f"{motive}\n{topic}",
                ),
                _proactive_prompt_part(
                    prompt_section(
                        key="background.response_review.action_context",
                        title="动作上下文",
                        source="proactive_message",
                        content=action_context or "（无）",
                    ),
                    separator_before="\n\n\n" if not topic else "\n\n",
                ),
                prompt_section(
                    key="background.response_review.intent",
                    title="内在约束",
                    source="proactive_message",
                    content=intent_hint or "（无额外约束）",
                ),
                prompt_section(
                    key="background.response_review.persona",
                    title="完整人格",
                    source="proactive_message",
                    content=(
                        persona
                        or "（没有解析到显式人格；尽量保留原文语气，不要另造一种通用陪伴人格）"
                    ),
                ),
                prompt_section(
                    key="background.response_review.voice",
                    title="主动开口风格",
                    source="proactive_message",
                    content=proactive_voice or "（无额外主动风格；保持原文已有的人格语气）",
                ),
                prompt_section(
                    key="background.response_review.expression",
                    title="已形成的表达底色",
                    source="proactive_message",
                    content=expression_voice or "（无额外表达底色）",
                ),
                prompt_section(
                    key="background.response_review.recipient",
                    title="当前收件人",
                    source="proactive_message",
                    content=recipient_identity or "不要猜名字或套用其他对象的专属称呼。",
                ),
                _proactive_prompt_part(prompt_section(
                    key="background.response_review.rules",
                    title="要求",
                    source="proactive_message",
                    content=(
                        "- 只输出要发送的正文\n"
                        "- 不要把“用户”“对方”“收信人”这类内部称呼写进正文；需要称呼时用自然的“你”或对方昵称\n"
                        "- 不要写成“好呀/确实/我也觉得/刚看到/你刚刚问我/你来找我了”\n"
                        "- 不要把历史消息当成当前正在发生的对话\n"
                        "- 没有真实图片或工具结果时，只写聊天内容本身，不描述动作结果\n"
                        "- 如果原文只是过程状态或工具结果，请不要改写成另一种状态汇报；改不成自然聊天就输出空文本\n"
                        "- 如果原文或模型结果包含 Provider/API 报错、内容策略拒绝、敏感词提示、政策链接或内部诊断，输出空文本；不要翻译、复述或润色\n"
                        "- 改写后仍要贴合内在约束里的候选语义；不能把分享型改成泛泛问候，也不能把低压关心改成追问\n"
                        "- 只修正“回复空气”的问题；不得把原文改成另一种人格，也不得降低或升级当前关系亲密度\n"
                        "- 尽量 1 到 2 句，像自然想起对方后随手说一句\n"
                        f"{creative_excerpt_rule}"
                    ),
                ), label_style=PromptLabelStyle.FULLWIDTH_COLON),
            ),
            metadata={"task": "response_review"},
        )

    async def _review_proactive_message_stance(
        self,
        user: dict[str, Any],
        text: str,
        *,
        reason: str,
        action: str,
        action_context: str = "",
        motive: str = "",
    ) -> str:
        cleaned = str(text or "").strip()
        if not cleaned:
            return ""
        relationship_sanitizer = getattr(self, "_sanitize_generation_relationship_context", None)

        def sanitize_relationship_source(value: Any, source: str) -> str:
            if callable(relationship_sanitizer):
                try:
                    return relationship_sanitizer(value, source=source)
                except Exception:
                    pass
            return str(value or "").strip()

        flags = self._proactive_reply_air_flags(
            cleaned,
            reason=reason,
            action=action,
            action_context=action_context,
        )
        if not flags:
            return cleaned
        mode = self._effective_proactive_review_mode()
        review_disabled = not bool(
            runtime_persona_setting(self, "enable_proactive_message_review", True)
        )
        if review_disabled or mode == "local_only":
            hard_flags = {"delivery_receipt", "claims_missing_media"}
            if hard_flags.intersection(flags):
                # 只移除命中硬风险的句子；同一候选里若还有安全正文，继续交给
                # 轻量主动修正，避免一条附带回执的多句消息被整条吞掉。
                safe_units: list[str] = []
                for unit in self._split_proactive_sentence_units(cleaned) or [cleaned]:
                    unit_flags = self._proactive_reply_air_flags(
                        unit,
                        reason=reason,
                        action=action,
                        action_context=action_context,
                    )
                    if hard_flags.intersection(unit_flags):
                        continue
                    safe_units.append(unit)
                cleaned = "\n".join(safe_units).strip()
                if not cleaned:
                    logger.info(
                        "主动消息仅剩不可用动作/内部回执,本地安全检查已丢弃: flags=%s",
                        ",".join(flags),
                    )
                    return ""
                flags = self._proactive_reply_air_flags(
                    cleaned,
                    reason=reason,
                    action=action,
                    action_context=action_context,
                )
            repaired = self._repair_proactive_reply_air_locally(cleaned, flags)
            remaining_flags = self._proactive_reply_air_flags(
                repaired,
                reason=reason,
                action=action,
                action_context=action_context,
            ) if repaired else flags
            if repaired and not remaining_flags:
                logger.info(
                    "主动消息疑似回复空气,已用本地轻量规则修正: flags=%s before=%s after=%s",
                    ",".join(flags),
                    _single_line(cleaned, 100),
                    _single_line(repaired, 100),
                )
                return repaired
            logger.warning(
                "主动消息疑似回复空气但终审未启用,本地无法可靠改写，保留原文并交由生成提示词约束: flags=%s text=%s",
                ",".join(flags),
                _single_line(cleaned, 120),
            )
            return cleaned
        intent_hint = self._format_proactive_generation_intent_hint(
            user,
            reason=reason,
            action=action,
            motive=motive,
            action_context=action_context,
        )
        intent_hint = sanitize_relationship_source(intent_hint, "proactive_review.intent")
        review_motive = _single_line(
            sanitize_relationship_source(
                motive or user.get("planned_proactive_motive"),
                "proactive_review.motive",
            ),
            160,
        )
        review_topic = _single_line(
            sanitize_relationship_source(
                user.get("planned_proactive_topic"),
                "proactive_review.topic",
            ),
            120,
        )
        review_action_context = _single_line(
            sanitize_relationship_source(action_context, "proactive_review.action_context"),
            260,
        )
        persona = await self._resolve_proactive_persona_prompt(user)
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
        creative_excerpt_rule = (
            self._creative_share_excerpt_prompt_hint()
            if reason == "creative_share"
            else ""
        )
        prompt = render_prompt_document(
            self._response_review_prompt_document(
                original_text=cleaned,
                flags=", ".join(flags),
                reason=reason,
                motive=review_motive,
                topic=review_topic,
                action_context=review_action_context,
                intent_hint=intent_hint,
                persona=(self._truncate_proactive_context(persona, 2600) if persona else ""),
                proactive_voice=proactive_voice,
                expression_voice=expression_voice,
                recipient_identity=recipient_identity,
                creative_excerpt_rule=creative_excerpt_rule,
            )
        )["user"]
        started = time.perf_counter()
        rewritten = await self._llm_call(
            prompt,
            max_tokens=180,
            provider_id=self._task_provider(
                _persona_provider_id(self, "RESPONSE_REVIEW_PROVIDER_ID", "response_review_provider_id", "fast"),
                _persona_provider_id(self, "MAI_STYLE_PROVIDER_ID", "mai_style_provider_id", "fast"),
            ),
            task="response_review",
        )
        candidate = self._sanitize_proactive_text(str(rewritten or "").strip())
        candidate = self._sanitize_action_boundaries(
            candidate,
            reason=reason,
            action=action,
            action_context=action_context,
            has_real_image="真实图片文件：" in action_context or "图片路径：" in action_context,
        )
        if self._looks_like_internal_provider_error_text(candidate):
            logger.warning(
                "回复/主动复核返回 Provider 错误正文，已丢弃: task=response_review"
            )
            return ""
        meta_leak_checker = getattr(self, "_response_review_meta_leak_reason", None)
        if callable(meta_leak_checker) and meta_leak_checker(candidate):
            logger.error(
                "回复/主动复核返回内部判断，已丢弃: output=%s",
                _single_line(candidate, 180),
            )
            return ""
        logger.info(
            "回复/主动复核完成: mode=%s flags=%s elapsed=%dms before=%s after=%s",
            mode,
            ",".join(flags),
            int((time.perf_counter() - started) * 1000),
            _single_line(cleaned, 100),
            _single_line(candidate, 100),
        )
        if not candidate:
            return ""
        if len(candidate) > max(len(cleaned) + 80, 260):
            return ""
        if re.search(r"(提示词|系统|JSON|改写后|以下是|主动消息|聊天历史)", candidate, re.IGNORECASE):
            return ""
        remaining_flags = self._proactive_reply_air_flags(
            candidate,
            reason=reason,
            action=action,
            action_context=action_context,
        )
        if remaining_flags:
            logger.info(
                "回复/主动复核后仍疑似回复空气,已丢弃: flags=%s text=%s",
                ",".join(remaining_flags),
                _single_line(candidate, 120),
            )
            return ""
        return candidate

    def _repair_proactive_subject_drift(
        self,
        text: str,
        *,
        reason: str,
        action: str,
        action_context: str = "",
    ) -> str:
        cleaned = str(text or "").strip()
        if not cleaned or action != "message":
            return cleaned
        state_context = "\n".join(
            _single_line(part, 260)
            for part in (
                action_context,
                self._format_schedule_context_for_prompt(),
                self._format_plan_item_for_prompt(self._proactive_current_plan_item(self.data.get("daily_plan", {}))),
            )
            if _single_line(part, 260)
        )
        bot_task_markers = (
            "作业", "写题", "题", "上课", "放学", "课本", "书桌", "试卷", "复习", "预习",
            "任务", "代码", "创作", "草稿", "报告", "练习",
        )
        if not any(token in state_context for token in bot_task_markers):
            return cleaned
        user_progress_patterns = (
            r"你[^。！？\n]{0,12}(?:作业|题|试卷|课|任务|代码|报告|草稿|练习)[^。！？\n]{0,18}(?:还差多少|写完了吗|做完了吗|弄完了吗|忙完了吗|上完了吗|差多少|完成了吗|怎么样了)[呀啊嘛呢了]*[？?。!！]?",
            r"(?:作业|题|试卷|课|任务|代码|报告|草稿|练习)[^。！？\n]{0,12}(?:还差多少|写完了吗|做完了吗|弄完了吗|忙完了吗|上完了吗|差多少|完成了吗)[呀啊嘛呢了]*[？?。!！]?",
        )
        repaired = cleaned
        changed = False
        for pattern in user_progress_patterns:
            repaired, count = re.subn(pattern, "", repaired)
            changed = changed or count > 0
        if not changed:
            return cleaned
        repaired = re.sub(r"\s+", " ", repaired).strip(" ，,。！？!?、")
        if repaired:
            logger.info(
                "主动消息修正主客体错位问句: reason=%s before=%s after=%s",
                reason,
                _single_line(cleaned, 120),
                _single_line(repaired, 120),
            )
            return repaired
        logger.info(
            "主动消息主客体错位且无剩余自然内容,已丢弃本轮生成: reason=%s text=%s",
            reason,
            _single_line(cleaned, 120),
        )
        return ""

    def _should_drop_misstaged_proactive_text(self, text: str, *, reason: str, action: str) -> bool:
        cleaned = _single_line(text, 220)
        if not cleaned:
            return True
        if action != "message" or reason not in {"morning_greeting", "noon_greeting", "evening_greeting", "check_in"}:
            return False
        reply_openers = ("好呀", "好啊", "可以呀", "可以啊", "行呀", "行啊", "嗯好", "那就", "你说呢", "要不", "不然")
        old_invite_markers = (
            "下午陪你", "陪你出去", "出去走走", "五点", "放学之后", "下班之后",
            "到时候叫我", "到时候喊我", "到时候", "垫上", "我哪来的钱",
            "一直等着", "等着呢", "想去哪", "去哪儿", "去哪逛", "哪儿逛", "哪里逛", "去逛",
        )
        if reason in {"morning_greeting", "noon_greeting", "evening_greeting"} and cleaned.startswith(reply_openers) and any(token in cleaned for token in old_invite_markers):
            logger.info(
                "主动消息疑似把旧邀约当成当前回复,已丢弃: reason=%s text=%s",
                reason,
                cleaned,
            )
            return True
        if reason in {"morning_greeting", "noon_greeting", "evening_greeting"}:
            stale_reply_patterns = (
                r"^(?:好呀|好啊|可以呀|可以啊|行呀|行啊|嗯好|那就).{0,30}(?:你到时候|到时候你|到时候叫|到时候喊)",
                r"^(?:好呀|好啊|可以呀|可以啊|行呀|行啊|嗯好|那就).{0,30}(?:我得|我得等|我只能|我可以).{0,18}(?:之后|以后|才行)",
                r"^(?:你说呢|要不|不然).{0,30}(?:我哪来|哪来的钱|先帮我|帮我垫|垫上)",
                r"^(?:好呀|好啊|可以呀|可以啊|行呀|行啊|嗯好|那就|你说呢|要不|不然).{0,36}(?:下午|五点|放学|下班|垫上|哪来的钱)",
                r"^(?:好呀|好啊|可以呀|可以啊|行呀|行啊|嗯好|那就).{0,30}(?:一直等|等着呢|等你).{0,30}(?:去哪|哪儿|哪里|逛|走走)",
                r"^(?:好呀|好啊|可以呀|可以啊|行呀|行啊|嗯好|那就).{0,36}(?:想去哪|去哪儿|去哪逛|哪儿逛|哪里逛|去逛)",
            )
            if any(re.search(pattern, cleaned) for pattern in stale_reply_patterns):
                logger.info(
                    "主动消息疑似接续旧对话而非主动开口,已丢弃: reason=%s text=%s",
                    reason,
                    cleaned,
                )
                return True
        return False

    def _proactive_time_mismatch_reason(self, text: str, *, reason: str, action: str) -> str:
        if str(action or "message").strip() != "message":
            return ""
        cleaned = _single_line(text, 240)
        if not cleaned:
            return ""
        now = self._environment_now()
        minutes = now.hour * 60 + now.minute
        current_item = self._proactive_current_plan_item(self.data.get("daily_plan", {}))
        current_text = _single_line(self._format_plan_item_for_prompt(current_item), 180)
        current_is_school_or_afternoon = bool(re.search(r"(上课|课间|放学|校门|教室|作业|书包|回家路上)", current_text))
        if reason == "morning_greeting" and re.search(r"(晚上|晚安|睡觉|好梦|睡前|夜里|放学|下班)", cleaned):
            return f"早间主动含有非早间场景: {cleaned}"
        if reason == "noon_greeting" and re.search(r"(早安|刚醒|赖床|晚安|好梦|睡觉|夜里)", cleaned):
            return f"午间主动含有错时问候: {cleaned}"
        if reason == "evening_greeting" and re.search(r"(早安|刚醒|赖床|上午|中午吃了吗)", cleaned):
            return f"晚间主动含有错时问候: {cleaned}"
        if minutes < 12 * 60 and re.search(r"(放学|放学就|放学后|放学回来|下课回来|下午回来|傍晚回来|晚上回来)", cleaned):
            return f"上午主动提前叙述放学/傍晚场景: {cleaned}"
        if minutes < 15 * 60 and re.search(r"(五点|5点|17点|下午五点|傍晚|晚上见|晚点回来找你)", cleaned):
            return f"当前时段过早,主动含有傍晚/五点场景: {cleaned}"
        if minutes >= 22 * 60 and re.search(r"(放学|下课|下午|傍晚|出去走走|等我回来找你)", cleaned):
            return f"夜间主动含有已过时段场景: {cleaned}"
        if re.search(r"(放学|下课|校门|教室|书包|回家路上)", cleaned) and not current_is_school_or_afternoon and not (14 * 60 <= minutes <= 19 * 60):
            return f"主动文本与当前日程不匹配: 当前={current_text or '无'} 文本={cleaned}"
        return ""
