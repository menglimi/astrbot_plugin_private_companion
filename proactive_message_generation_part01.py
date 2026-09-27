# -*- coding: utf-8 -*-
"""ProactiveMessageGenerationPart01Mixin。

由 tools/split_mixin_domain.py 从 proactive_message_generation.py 机械抽取（6 个方法 + 0 个模块级名字 + 0 个类级赋值 / 476 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveMessageGenerationMixin）。
"""
from __future__ import annotations

from .proactive_message_generation_shared import logger
from .proactive_message_generation_shared import Any
from .proactive_message_generation_shared import LLM_SEGMENT_MARKER
from .proactive_message_generation_shared import _single_line
from .proactive_message_generation_shared import re
from .proactive_message_generation_shared import runtime_persona_setting
from .proactive_message_generation_shared import split_llm_controlled_text



class ProactiveMessageGenerationPart01Mixin:
    """ProactiveMessageGenerationPart01Mixin（从 ProactiveMessageGenerationMixin 拆出）。"""


    async def _generate_proactive_message_with_llm(
        self,
        user: dict[str, Any],
        name: str,
        reason: str,
        action_context: str = "",
        action: str = "message",
        motive: str = "",
    ) -> str:
        user.pop("_proactive_render_failure_stage", None)
        umo = _single_line(user.get("umo"), 240)
        self._clear_proactive_reaction_intent(umo)
        if not runtime_persona_setting(self, "enable_llm_proactive_message", True):
            user["_proactive_render_failure_stage"] = "主动消息模型生成已关闭"
            return ""
        raw_text = await self._generate_proactive_message_via_framework(
            user,
            name,
            reason,
            action_context=action_context,
            action=action,
            motive=motive,
        )
        deferred_photo_cache = getattr(self, "_framework_deferred_photo_cache", None)
        if isinstance(deferred_photo_cache, dict) and umo in deferred_photo_cache:
            logger.info(
                "主动正文已由 pc_generate_photo caption/纯图承载，跳过文本兜底: user=%s",
                _single_line(user.get("user_id"), 40),
            )
            return str(raw_text or "")
        async def finalize_candidate(candidate: str) -> tuple[str, str]:
            extractor = getattr(self, "_extract_reaction_expression_hidden_intent", None)
            visible_candidate, reaction_intent = (
                extractor(candidate)
                if callable(extractor)
                else (str(candidate or ""), {})
            )
            if isinstance(reaction_intent, dict) and reaction_intent:
                reaction_intent["_proactive_reason"] = reason
                reaction_intent["_proactive_action"] = action
            sticker_only = self._proactive_reaction_intent_allows_sticker_only(reaction_intent)
            if not reaction_intent:
                fallback_builder = getattr(
                    self,
                    "_proactive_reaction_expression_fallback_intent",
                    None,
                )
                if callable(fallback_builder):
                    try:
                        reaction_intent = fallback_builder(
                            visible_candidate,
                            action=action,
                        )
                    except Exception as exc:
                        logger.debug(
                            "高频主动表情兜底构建失败: error_type=%s",
                            type(exc).__name__,
                        )
            if sticker_only and not visible_candidate.strip():
                finalized, failure_stage = "", ""
            else:
                finalized, failure_stage = await self._finalize_proactive_generated_text(
                    user,
                    visible_candidate,
                    name=name,
                    reason=reason,
                    action=action,
                    action_context=action_context,
                    motive=motive,
                )
            if finalized or sticker_only:
                self._store_proactive_reaction_intent(
                    user,
                    reaction_intent if isinstance(reaction_intent, dict) else {},
                    action=action,
                )
            return finalized, failure_stage

        failure_stages: list[str] = []
        if raw_text:
            finalized, failure_stage = await finalize_candidate(raw_text)
            if finalized or self._proactive_sticker_only_pending(umo):
                return finalized
            failure_stages.append(f"框架主链{failure_stage or '处理后为空'}")
        else:
            failure_stages.append("框架主链返回空文本")

        fallback_text = await self._generate_proactive_message_direct_fallback(
            user,
            name=name,
            reason=reason,
            action=action,
            action_context=action_context,
            motive=motive,
        )
        if fallback_text:
            finalized, failure_stage = await finalize_candidate(fallback_text)
            if finalized or self._proactive_sticker_only_pending(umo):
                logger.info(
                    "主动框架主链为空后已由直接人格化兜底恢复: user=%s reason=%s",
                    _single_line(user.get("user_id"), 40),
                    reason,
                )
                return finalized
            failure_stages.append(f"直接人格化兜底{failure_stage or '处理后为空'}")
        else:
            failure_stages.append("直接人格化兜底返回空文本")

        failure_detail = "；".join(failure_stages)[:240]
        user["_proactive_render_failure_stage"] = failure_detail
        logger.warning(
            "主动正文两级生成均未产出: user=%s reason=%s stage=%s",
            _single_line(user.get("user_id"), 40),
            reason,
            failure_detail,
        )
        return ""

    async def _generate_proactive_message_direct_fallback(
        self,
        user: dict[str, Any],
        *,
        name: str,
        reason: str,
        action: str,
        action_context: str = "",
        motive: str = "",
    ) -> str:
        relationship_sanitizer = getattr(self, "_sanitize_generation_relationship_context", None)

        def sanitize_relationship_source(value: Any, source: str) -> str:
            if callable(relationship_sanitizer):
                try:
                    return relationship_sanitizer(value, source=source)
                except Exception:
                    pass
            return str(value or "").strip()

        topic = _single_line(
            sanitize_relationship_source(user.get("planned_proactive_topic"), "proactive_fallback.topic"),
            120,
        )
        planned_motive = _single_line(
            sanitize_relationship_source(
                motive or user.get("planned_proactive_motive"),
                "proactive_fallback.motive",
            ),
            220,
        )
        context = sanitize_relationship_source(
            self._format_action_prompt_context(action, action_context),
            "proactive_fallback.action_context",
        )
        if (
            (context.startswith("message：") and "图片动作本轮未产出" not in context)
            or context in {"普通文字", "普通私聊文本"}
        ):
            context = ""
        reference = "\n".join(
            part
            for part in (
                f"主动话题：{topic}" if topic else "",
                f"想表达：{planned_motive}" if planned_motive else "",
                f"真实动作上下文：{context}" if context else "",
            )
            if part
        )
        body_health_hint_getter = getattr(self, "_format_body_monitor_health_prompt", None)
        if reason == "health_alert" and callable(body_health_hint_getter):
            body_health_hint = body_health_hint_getter(user, reason=reason)
            if body_health_hint:
                reference = f"{reference}\n{body_health_hint}" if reference else body_health_hint
        balance_hint_getter = getattr(self, "_format_balance_awareness_prompt", None)
        if reason == "low_balance" and callable(balance_hint_getter):
            balance_hint = balance_hint_getter(user, reason=reason)
            if balance_hint:
                reference = f"{reference}\n{balance_hint}" if reference else balance_hint
        environment_hint_getter = getattr(self, "_format_environment_change_prompt", None)
        if reason == "environment_change" and callable(environment_hint_getter):
            environment_hint = environment_hint_getter(user, reason=reason)
            if environment_hint:
                reference = f"{reference}\n{environment_hint}" if reference else environment_hint
        weather_alert_hint_getter = getattr(self, "_format_weather_alert_prompt", None)
        if reason == "weather_alert" and callable(weather_alert_hint_getter):
            weather_alert_hint = weather_alert_hint_getter(user, reason=reason)
            if weather_alert_hint:
                reference = f"{reference}\n{weather_alert_hint}" if reference else weather_alert_hint
        personal_goal_hint_getter = getattr(self, "_format_personal_goal_prompt", None)
        if reason == "personal_goal_progress" and callable(personal_goal_hint_getter):
            personal_goal_hint = personal_goal_hint_getter(user, reason=reason)
            if personal_goal_hint:
                reference = f"{reference}\n{personal_goal_hint}" if reference else personal_goal_hint
        memo_hint_getter = getattr(self, "_format_memo_note_prompt", None)
        if reason == "memo_note_reminder" and callable(memo_hint_getter):
            memo_hint = memo_hint_getter(user, reason=reason)
            if memo_hint:
                reference = f"{reference}\n{memo_hint}" if reference else memo_hint
        if reason == "goodnight_screen_check":
            reference = (
                f"互道晚安后，如果{name or '对方'}还没睡，就轻声提醒忙完早点休息；"
                "不提看见了什么，不追问，不要求回复，也不表现成在监控。"
            )
        elif reason == "anonymous_area_dwell":
            reference = (
                f"{reference}\n" if reference else ""
            ) + (
                "这是用户离开一个未命名区域后的延迟关心。不要提位置、地图、城市、城区、定位或停留时长；"
                "只写成后来想起用户刚才在外面待了挺久，轻轻关心是否顺利，不追问具体去了哪里。"
            )
        elif reason == "anonymous_area_familiarity":
            reference = (
                f"{reference}\n" if reference else ""
            ) + (
                "这是多次匿名区域到访留下的模糊熟悉感。不要提位置来源、地图、次数或具体地点；"
                "可以说‘最近好像有个常去的地方’，但必须给用户留出否认或不解释的空间。"
            )
        relationship_initiative_hint = self._format_proactive_relationship_initiative_hint(
            user,
            reason=reason,
            action=action,
        )
        if relationship_initiative_hint:
            reference = f"{reference}\n{relationship_initiative_hint}" if reference else relationship_initiative_hint
        if not reference:
            reference = f"自然地向{name or '对方'}主动说一句与当前状态有关、低压力且无需立即回复的话。"
        reference = sanitize_relationship_source(reference, "proactive_fallback.reference")
        if not reference:
            reference = f"自然地向{name or '对方'}主动说一句低压力且无需立即回复的话。"
        fallback_scene = f"主动开口；原因={reason or 'check_in'}；动作={action or 'message'}"
        if reason == "creative_share":
            fallback_scene = "主动分享自己的创作；作品原文与聊天引入必须保持清晰边界"
        return await self._rewrite_reference_reply_with_persona(
            reference,
            scene=fallback_scene,
            user=user,
            fallback_text="",
            task="proactive_message_fallback",
            max_chars=180,
            allow_fallback=False,
        )

    async def _finalize_proactive_generated_text(
        self,
        user: dict[str, Any],
        raw_text: str,
        *,
        name: str,
        reason: str,
        action: str,
        action_context: str = "",
        motive: str = "",
    ) -> tuple[str, str]:
        controlled_segments, controlled = (
            split_llm_controlled_text(raw_text)
            if self._proactive_llm_segmenting_allowed(
                umo=_single_line(user.get("umo"), 240),
            )
            else ([str(raw_text or "").strip()], False)
        )
        if controlled:
            finalized_segments: list[str] = []
            failure_stages: list[str] = []
            for segment in controlled_segments:
                finalized_segment, failure_stage = await self._finalize_proactive_generated_text(
                    user,
                    segment,
                    name=name,
                    reason=reason,
                    action=action,
                    action_context=action_context,
                    motive=motive,
                )
                if finalized_segment:
                    finalized_segments.append(finalized_segment)
                elif failure_stage:
                    failure_stages.append(failure_stage)
            if not finalized_segments:
                return "", "；".join(failure_stages)[:240] or "自主分段正文处理后为空"

            # Preserve the previous proactive visible-text ceiling. The marker
            # itself is transport metadata and does not consume that budget.
            remaining = 260
            bounded_segments: list[str] = []
            for segment in finalized_segments:
                if remaining <= 0:
                    break
                if len(segment) <= remaining:
                    bounded_segments.append(segment)
                    remaining -= len(segment)
                    continue
                truncated = self._truncate_proactive_text(segment, remaining)
                if truncated:
                    bounded_segments.append(truncated)
                break
            return (
                f"\n{LLM_SEGMENT_MARKER}\n".join(bounded_segments),
                "",
            ) if bounded_segments else ("", "自主分段正文处理后为空")
        if self._looks_like_internal_provider_error_text(raw_text):
            logger.warning(
                "主动正文生成收到 Provider 错误正文，跳过清洗并进入回退: user=%s reason=%s",
                _single_line(user.get("user_id"), 40),
                _single_line(reason, 60) or "check_in",
            )
            return "", "Provider/API 错误正文"
        cleaned = self._sanitize_action_boundaries(
            self._sanitize_proactive_text(raw_text),
            reason=reason,
            action=action,
            action_context=action_context,
            has_real_image="真实图片文件：" in action_context or "图片路径：" in action_context,
        )
        if not cleaned:
            return "", "在动作边界清洗后为空"
        cleaned, repaired_address = self._repair_proactive_recipient_address(cleaned, user, name)
        if repaired_address:
            logger.warning(
                "主动消息已纠正串用户句首称呼: user=%s wrong=%s replacement=%s",
                _single_line(user.get("user_id"), 40),
                repaired_address,
                _single_line(name or user.get("nickname"), 40) or "你",
            )
        remaining_wrong_address = self._wrong_proactive_recipient_address(cleaned, user, name)
        if remaining_wrong_address:
            return "", f"含其他用户专属称呼：{remaining_wrong_address}"
        if self._is_overabstract_proactive_text(cleaned, action=action):
            cleaned = self._ground_proactive_text(
                cleaned,
                reason=reason,
                action=action,
                action_context=action_context,
            )
        cleaned = self._apply_proactive_style_variation(cleaned, user)
        cleaned = self._collapse_multi_candidate_proactive_text(cleaned, user=user, name=name)
        cleaned = self._repair_proactive_subject_drift(cleaned, reason=reason, action=action, action_context=action_context)
        if reason == "morning_greeting":
            cleaned = self._strip_morning_meal_questions(cleaned)
        cleaned = self._visible_text_without_tts_reading(cleaned, limit=1000)
        if not cleaned:
            return "", "在主客体/可见文本清洗后为空"
        relay_claim_note = self._unexecuted_relay_claim_reason(cleaned, action_context=action_context)
        if relay_claim_note:
            logger.info(
                "主动消息含未执行转述承诺,已丢弃: reason=%s text=%s",
                relay_claim_note,
                _single_line(cleaned, 120),
            )
            return "", f"含未执行转述承诺：{_single_line(relay_claim_note, 80)}"
        if self._should_drop_vague_generic_proactive(
            user,
            reason=reason,
            action=action,
            action_context=action_context,
            text=cleaned,
        ):
            # 连续未回应时的泛泛措辞是表达质量问题，不是安全问题。
            # 交给主动生成提示词收短、降压，避免在终审关闭时被本地规则直接吞掉。
            logger.debug(
                "泛化主动由提示词收敛，不再直接拦截: user=%s text=%s",
                _single_line(user.get("user_id") or user.get("umo"), 80),
                _single_line(cleaned, 140),
            )
        if self._should_drop_misstaged_proactive_text(cleaned, reason=reason, action=action):
            return "", "错接旧对话或时段"
        reviewed = await self._review_proactive_message_stance(
            user,
            cleaned,
            reason=reason,
            action=action,
            action_context=action_context,
            motive=motive,
        )
        if not reviewed:
            return "", "回复空气复核后为空"
        reviewed, repaired_review_address = self._repair_proactive_recipient_address(reviewed, user, name)
        if repaired_review_address:
            logger.warning(
                "主动复核结果已纠正串用户称呼: user=%s wrong=%s",
                _single_line(user.get("user_id"), 40),
                repaired_review_address,
            )
        remaining_review_address = self._wrong_proactive_recipient_address(reviewed, user, name)
        if remaining_review_address:
            return "", f"回复空气复核引入其他用户专属称呼：{remaining_review_address}"
        reviewed = self._trim_proactive_status_inventory(reviewed)
        reviewed = self._trim_performative_self_state_tail(reviewed)
        if reason == "morning_greeting":
            reviewed = self._strip_morning_meal_questions(reviewed)
        finalized = self._normalize_proactive_sentence_flow(reviewed)
        return (finalized, "") if finalized else ("", "最终句式整理后为空")

    @staticmethod
    def _strip_morning_meal_questions(text: str) -> str:
        """Keep a morning greeting while removing an accidentally appended meal question."""
        source = str(text or "").strip()
        if not source:
            return ""
        query_pattern = re.compile(
            r"(?:早餐|早饭).{0,12}(?:吗|没|没有|什么|啥|呢|[？?])"
            r"|(?:吃|喝).{0,6}(?:了吗|了没|没有|什么|啥)(?:呢|[？?])?"
        )
        kept: list[str] = []
        for unit in re.split(r"(?<=[。！？!?])\s*|\n+", source):
            candidate = unit.strip()
            if not candidate:
                continue
            match = query_pattern.search(candidate)
            if not match:
                kept.append(candidate)
                continue
            prefix = candidate[: match.start()].rstrip(" ，,；;、")
            if prefix:
                kept.append(prefix)
        return "\n".join(kept).strip()

    def _proactive_reply_air_flags(
        self,
        text: str,
        *,
        reason: str,
        action: str,
        action_context: str = "",
    ) -> list[str]:
        cleaned = _single_line(text, 260)
        if not cleaned or action not in {"message", "photo_text"}:
            return []
        flags: list[str] = []
        # 外部分享（新闻/B站/搜索）是「分享外界信息」场景，不做回复空气检查，直接放行。
        if reason in {"news_share", "bili_video_share", "web_exploration_share"}:
            return flags
        reply_opener_pattern = (
            r"^(?:好呀|好啊|可以呀|可以啊|行呀|行啊|嗯好|那就|你说呢|要不|不然|"
            r"确实|对呀|对啊|是吧|也是|哈哈[,，\s]*我也|我也觉得|你说得对)"
        )
        if re.search(reply_opener_pattern, cleaned):
            flags.append("reply_air_opener")
        if re.search(r"(?:刚看到|才看到|刚才看到|看到你(?:刚刚|刚才)?发|看到你说)", cleaned):
            flags.append("pretends_recent_inbound")
        if re.search(r"你(?:刚刚|刚才|现在)?(?:叫|喊|问|说|发|来找|找|催)我", cleaned):
            flags.append("inverts_initiator")
        if re.search(r"(?:你问|你说|你刚才说|你刚刚说)[^。！？\n]{0,24}(?:我觉得|我也|确实|可以|好呀|好啊)", cleaned):
            flags.append("answers_old_context")
        if self._is_proactive_delivery_receipt_text(cleaned):
            flags.append("delivery_receipt")
        if reason in {"morning_greeting", "noon_greeting", "evening_greeting", "check_in"} and re.search(
            r"(?:一直等着|等你问|你到时候|到时候叫|到时候喊|那就这么说定|按你说的)",
            cleaned,
        ):
            flags.append("stale_agreement")
        if "真实图片文件：" not in str(action_context or "") and "图片路径：" not in str(action_context or ""):
            if re.search(r"(?:发你看|给你看图|看图|图里|照片里|图片里)", cleaned):
                flags.append("claims_missing_media")
        return list(dict.fromkeys(flags))

    def _repair_proactive_reply_air_locally(self, text: str, flags: list[str]) -> str:
        cleaned = str(text or "").strip()
        if not cleaned:
            return ""
        units = self._split_proactive_sentence_units(cleaned) or [cleaned]
        repaired: list[str] = []
        opener_pattern = (
            r"^(?:好呀|好啊|可以呀|可以啊|行呀|行啊|嗯好|那就|你说呢|要不|不然|"
            r"确实|对呀|对啊|是吧|也是|哈哈[,，\s]*我也|我也觉得|你说得对)"
            r"[，,、。！？!?；;:\s]*"
        )
        stale_patterns = (
            r"(?:刚看到|才看到|刚才看到|看到你(?:刚刚|刚才)?发|看到你说)",
            r"你(?:刚刚|刚才|现在)?(?:叫|喊|问|说|发|来找|找|催)我",
            r"(?:你问|你说|你刚才说|你刚刚说)[^。！？\n]{0,24}(?:我觉得|我也|确实|可以|好呀|好啊)",
        )
        for unit in units:
            candidate = str(unit or "").strip()
            if not candidate:
                continue
            if "reply_air_opener" in flags:
                candidate = re.sub(opener_pattern, "", candidate, count=1).strip()
            if any(re.search(pattern, candidate) for pattern in stale_patterns):
                continue
            if candidate:
                repaired.append(self._ensure_chat_sentence_punctuation(candidate))
        return "\n".join(repaired).strip()
