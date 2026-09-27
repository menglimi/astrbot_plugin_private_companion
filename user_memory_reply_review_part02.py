# -*- coding: utf-8 -*-
"""UserMemoryReplyReviewPart02Mixin。

由 tools/split_mixin_domain.py 从 user_memory_reply_review.py 机械抽取（12 个方法 + 0 个模块级名字 + 0 个类级赋值 / 476 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 UserMemoryReplyReviewMixin）。
"""
from __future__ import annotations
from .user_memory_reply_review_shared import Any
from .user_memory_reply_review_shared import _render_user_memory_background_prompt
from .user_memory_reply_review_shared import _render_user_memory_labeled_section
from .user_memory_reply_review_shared import _single_line
from .user_memory_reply_review_shared import logger
from .user_memory_reply_review_shared import prompt_section
from .user_memory_reply_review_shared import re
from .user_memory_reply_review_shared import runtime_persona_setting
from .user_memory_reply_review_shared import time



class UserMemoryReplyReviewPart02Mixin:
    """UserMemoryReplyReviewPart02Mixin（从 UserMemoryReplyReviewMixin 拆出）。"""


    def _response_claims_user_prior_action(self, text: str, user: dict[str, Any]) -> bool:
        cleaned = _single_line(text, 500)
        if not cleaned:
            return False
        names = ["你"]
        if isinstance(user, dict):
            for key in ("nickname", "last_display_name", "display_name"):
                name = _single_line(user.get(key), 24)
                if name and not name.isdigit() and name not in names:
                    names.append(name)
        subject = "|".join(re.escape(name) for name in names)
        titled_name = r"[\u4e00-\u9fffA-Za-z0-9_]{1,16}(?:大人|主人|先生|小姐)"
        return bool(
            re.search(
                rf"(?:{subject}|{titled_name})[^。！？!?\n]{{0,18}}(?:上次|之前|先|早就|原来)[^。！？!?\n]{{0,18}}(?:说|提|想|拿|问|做|告诉|推荐|诱惑)",
                cleaned,
            )
            or re.search(r"明明是[^。！？!?\n]{1,24}先[^。！？!?\n]{0,18}(?:说|提|想|拿|问|做|告诉|推荐|诱惑)", cleaned)
        )

    @staticmethod
    def _response_denies_existing_creative_work(response_text: str, creative_context: str) -> bool:
        response = _single_line(response_text, 500)
        context = str(creative_context or "")
        if not response or "真实创作记录：共有" not in context:
            return False
        denial_patterns = (
            r"(?:没|没有|还没|从没|并没|未曾)[^。！？!?\n]{0,10}(?:写过|写|创作过|创作|完成)[^。！？!?\n]{0,10}(?:书|小说|作品|故事|诗|随笔|散文|剧本|手稿)",
            r"(?:没|没有|还没有|并没有)[^。！？!?\n]{0,8}(?:自己写的|自己的|成型的)?[^。！？!?\n]{0,5}(?:书|小说|作品|故事|手稿)",
            r"(?:我)?哪有[^。！？!?\n]{0,12}(?:书|小说|作品|手稿)",
        )
        return any(re.search(pattern, response, re.IGNORECASE) for pattern in denial_patterns)

    @staticmethod
    def _response_content_tier(review_event: Any | None) -> str:
        decision = getattr(review_event, "_private_companion_expression_decision", None) if review_event is not None else None
        tier = str(decision.get("content_tier") or "normal").strip().lower() if isinstance(decision, dict) else "normal"
        return tier if tier in {"normal", "flirt"} else "normal"

    @staticmethod
    def _response_contains_content_tier_review_candidate(value: Any) -> bool:
        text = _single_line(value, 1200).lower()
        if not text:
            return False
        if re.search(
            r"疼痛|激素|就医|医生|医学|科普|治疗|检查|炎症|艺术|美术史|文学|小说|剧情|诈骗|链接|风险|怀孕|避孕|没有露骨|并非露骨|不是露骨",
            text,
            re.IGNORECASE,
        ):
            return False
        signals = set(
            re.findall(
                r"nsfw|色情|露骨|性行为|性交|做爱|口交|肛交|阴茎|阴道|射精|裸体|全裸|性器官|乳房",
                text,
                re.IGNORECASE,
            )
        )
        return len(signals) >= 2 or bool(
            re.search(r"(?:写|描写|展开|继续)[^。！？!?\n]{0,20}(?:性爱|做爱|性交|口交|肛交|射精)", text, re.IGNORECASE)
        )

    @staticmethod
    def _response_contains_explicit_sensitive_content(value: Any) -> bool:
        """Compatibility-safe detector for clearly explicit sexual output."""
        return UserMemoryReplyReviewPart02Mixin._response_contains_content_tier_review_candidate(value)

    @staticmethod
    def _content_tier_boundary_reply() -> str:
        return "这个尺度我先不往露骨方向展开，我们换成更含蓄一点的说法吧。"

    async def _review_and_rewrite_response(
        self,
        user: dict[str, Any],
        inbound_text: str,
        response_text: str,
        *,
        music_album_context: dict[str, Any] | None = None,
        creative_context: str = "",
        review_event: Any | None = None,
    ) -> str:
        # Any rewrite can break the protected voice/text correspondence for this turn.
        if "[[PCTTS:" in str(response_text or ""):
            return response_text
        relay_claim_checker = getattr(self, "_unexecuted_relay_claim_reason", None)
        if callable(relay_claim_checker):
            relay_claim_note = relay_claim_checker(response_text)
            if relay_claim_note:
                fallback_builder = getattr(self, "_fallback_unexecuted_relay_reply", None)
                fallback = fallback_builder(inbound_text) if callable(fallback_builder) else ""
                logger.info(
                    "被动回复含未执行转述承诺,已改为诚实边界: reason=%s before=%s after=%s",
                    relay_claim_note,
                    _single_line(response_text, 120),
                    _single_line(fallback, 120),
                )
                return fallback or response_text
        if isinstance(music_album_context, dict) and self._music_album_reply_needs_disambiguation_fix(response_text):
            fallback = self._music_album_reply_from_context(music_album_context, user_text=inbound_text)
            if fallback:
                logger.info(
                    "音乐专辑回复已按卡片上下文纠偏: before=%s after=%s",
                    _single_line(response_text, 120),
                    _single_line(fallback, 160),
                )
                return fallback
        content_policy_enabled = bool(runtime_persona_setting(self, "enable_relationship_content_tiers", False))
        content_tier = self._response_content_tier(review_event) if content_policy_enabled else "unmanaged"
        if not self._passive_response_review_enabled():
            return self._fallback_temporal_or_continuity_confused_reply(inbound_text, response_text, user=user) or response_text
        flags = self._response_review_flags(response_text, user, inbound_text=inbound_text)
        if (
            content_policy_enabled
            and self._response_contains_content_tier_review_candidate(response_text)
        ):
            flags.append("content_tier_review_candidate")
        if self._response_denies_existing_creative_work(response_text, creative_context):
            flags.append("denies_existing_creative_work")
            flags = list(dict.fromkeys(flags))
        if not flags:
            return response_text
        review_mode = self._effective_passive_review_mode()
        review_strength = self._effective_passive_review_strength()
        if review_mode == "local_only":
            return self._fallback_temporal_or_continuity_confused_reply(
                inbound_text,
                response_text,
                flags=flags,
                user=user,
            ) or response_text
        severe_flags = self._response_review_severe_flags(flags)
        if review_mode == "severe_only" and not severe_flags:
            return response_text
        effective_flags = severe_flags if review_mode == "severe_only" else flags
        lightweight_checker = getattr(self, "_is_lightweight_private_passive_inbound", None)
        if callable(lightweight_checker) and lightweight_checker(inbound_text):
            critical_flags = {
                "too_long",
                "meta_or_assistant",
                "over_structured",
                "leaks_internal",
                "repeats_last_bot_message",
                "invalid_current_time_anchor",
                "false_no_reply_claim",
                "fact_attribution_after_correction",
                "unverified_fact_attribution",
                "proactive_media_ownership_reversal",
                "denies_existing_creative_work",
                "content_tier_review_candidate",
            }
            if not any(flag in critical_flags for flag in effective_flags):
                return response_text
        intent = user.get("intent_profile") if isinstance(user.get("intent_profile"), dict) else {}
        allow_repeat = self._inbound_explicitly_requests_repeat(inbound_text)
        last_message = _single_line(user.get("last_companion_message"), 300)
        last_message_label = "用户本轮明确要求复述上一条,仅用于确认原文" if allow_repeat else "刚才 Bot 已经说过，禁止复述或换皮重复"
        persona = ""
        persona_resolver = getattr(self, "_resolve_proactive_persona_prompt", None)
        if callable(persona_resolver):
            try:
                persona = str(await persona_resolver(user) or "").strip()
            except Exception:
                persona = ""
        reply_style = self._format_reply_style_prompt() if callable(getattr(self, "_format_reply_style_prompt", None)) else ""
        attribution_guard = self._format_private_fact_attribution_guard(user, inbound_text)
        creative_review_context = str(creative_context or "").strip()[:3200]
        content_tier_prompt = (
            _render_user_memory_labeled_section(
                prompt_section(
                    key="background.memory.response_review.content_tier",
                    title="统一内容尺度",
                    source="user_memory",
                    content=f"{content_tier}；normal 不主动升级，flirt 只允许非露骨暧昧。",
                )
            )
            if content_policy_enabled
            else ""
        )
        inbound_block = _render_user_memory_labeled_section(
            prompt_section(
                key="background.memory.response_review.inbound",
                title="用户刚才说",
                source="user_memory",
                content=_single_line(inbound_text, 260) or "（无）",
            )
        )
        last_bot_block = _render_user_memory_labeled_section(
            prompt_section(
                key="background.memory.response_review.last_bot_message",
                title=last_message_label,
                source="user_memory",
                content=last_message or "（无）",
            )
        )
        response_block = _render_user_memory_labeled_section(
            prompt_section(
                key="background.memory.response_review.original_response",
                title="原回复",
                source="user_memory",
                content=response_text,
            )
        )
        issues_block = _render_user_memory_labeled_section(
            prompt_section(
                key="background.memory.response_review.issues",
                title="需要修正的问题",
                source="user_memory",
                content=", ".join(effective_flags),
            )
        )
        intent_block = _render_user_memory_labeled_section(
            prompt_section(
                key="background.memory.response_review.intent",
                title="当前意图/情绪",
                source="user_memory",
                content=(
                    f"{intent.get('intent', 'chat')}｜{intent.get('emotion', 'neutral')}｜"
                    f"{intent.get('reply_style', 'natural')}"
                ),
            )
        )
        current_time_block = _render_user_memory_labeled_section(
            prompt_section(
                key="background.memory.response_review.current_time",
                title="真实当前时间",
                source="user_memory",
                content=self._environment_now().strftime("%Y-%m-%d %H:%M"),
            )
        )
        persona_block = _render_user_memory_labeled_section(
            prompt_section(
                key="background.memory.response_review.persona",
                title="当前人格",
                source="user_memory",
                content=(
                    persona[:2600]
                    if persona
                    else "（沿用原回复已有的人格语气，不要另造通用助手口吻）"
                ),
            )
        )
        reply_style_block = _render_user_memory_labeled_section(
            prompt_section(
                key="background.memory.response_review.reply_style",
                title="回复风格",
                source="user_memory",
                content=reply_style or "（保持当前私聊的自然表达）",
            )
        )
        creative_block = _render_user_memory_labeled_section(
            prompt_section(
                key="background.memory.response_review.creative_context",
                title="本轮真实创作记录",
                source="user_memory",
                content=creative_review_context or "（本轮没有创作记录上下文）",
            )
        )
        prompt = prompt_section(
            key="background.memory.response_review",
            title="被动回复模型自检",
            source="user_memory",
            content=f"""
把下面这条回复改写成更像真实私聊里的自然回复。
保留原意,不要新增事实,不要解释你在改写。

{inbound_block}

{last_bot_block}

{response_block}

{issues_block}

{intent_block}

{current_time_block}

{persona_block}

{reply_style_block}

{content_tier_prompt}

{attribution_guard}

{creative_block}

要求：
- 只输出改写后的正文
- 不要标题、列表、JSON、括号动作、系统/AI/提示词字眼
- 普通闲聊尽量 1 到 3 句；求助类可以保留必要步骤,但更口语
- 如果用户只是短句闲聊、报天气、说一句状态或轻轻接话,改成 1 句或 2 句短回复；不要扩展成关心清单、建议清单或连续状态复述
- 如果用户情绪低,先接住情绪,少讲道理
- 如果是边界/不想被打扰,短一点,退一步
- 如果回复已经在说晚安、睡觉、做梦、告别,不要再突然追加天气、日程、生活观察或另一个新话题
- 如果用户明确要求复述/原话/再说一遍,允许保留上一条 Bot 原文,不要把它误判为复读
- 如果问题是无意重复上一条 Bot 消息,必须直接承接用户这句话,不要再说上一条里的“吃饱犯困/下午还有事/有什么安排”等同义内容
- 如果用户并未要求复述,且无论怎样改写都只能重复上一条 Bot 消息,只输出 {self._response_review_drop_marker()}；不要为了“不重复”再补一句客套话
- 如果原回复为了表现困、迷糊、半梦半醒或低能量而变得含混,优先改成清楚承接用户；状态只能留在语气里,不能牺牲回答质量
- 如果用户没有问 Bot 近况,删掉由内部模拟状态带出的“我刚在/正在/继续做某事”等动作或日程复述；不要把模拟状态说成现实事件
- 如果问题是表达学习过头、异常断句或照抄用户样本,保留意思,改成自然中文私聊；不要为了模仿口癖而加奇怪逗号、空格、断句或复读用户原话
- 如果问题是 invalid_current_time_anchor,删除或改正“快十一点/该睡了/晚安”等与真实当前时间冲突的说法；不要继续围绕错误时间展开
- 如果问题是 false_no_reply_claim,不要说“看你没回我/等你回话/你没理我”；用户本轮已经发来消息,直接解释上一句或重新接住当前问题
- 如果问题是 fact_attribution_after_correction，必须以用户刚才的纠正和上一条 Bot 已承认的内容为准；不要换个说法再次把 Bot 的行为安到用户身上
- 如果问题是 unverified_fact_attribution，原回复正在断言“用户之前/先做过某事”，但当前短句没有提供这个归属；没有明确依据就改成中性主语或只谈那件事本身
- 如果问题是 proactive_media_ownership_reversal，用户只是在评价 Bot 刚主动发送的图片；把图中“我/她/角色本人”的动作改回 Bot/当前人格，绝不能责怪或关心用户仿佛是用户弄洒、摔倒或受伤
- 如果问题是 denies_existing_creative_work，必须依据本轮真实创作记录承认已有文本作品；不得把“未正式出版”偷换成“没写过”，也不要虚构出版、发行或实体书经历
- 如果问题是 content_tier_review_candidate，先按完整语境判断；只有确实在生成露骨性描写时才收敛表达，医疗、科普、艺术、文学、风险提示和否定语境必须保留原意，不得换成固定拒答话术
""".strip(),
        )
        if review_event is not None:
            setattr(review_event, "_private_companion_response_review_guard_active", True)
            setattr(review_event, "_private_companion_response_review_fallback_text", response_text)
        started = time.perf_counter()
        try:
            review_provider_id = self._task_provider(
                runtime_persona_setting(self, "response_review_provider_id", ""),
                runtime_persona_setting(self, "mai_style_provider_id", ""),
            )
            rewritten = await self._llm_call(
                _render_user_memory_background_prompt(prompt),
                max_tokens=260,
                provider_id=review_provider_id,
                task="response_review",
                strict_provider=False,
            )
        except Exception as exc:
            logger.warning(
                "被动回复模型自检失败,保留原回复: flags=%s error=%s",
                ",".join(effective_flags),
                _single_line(exc, 160),
            )
            return self._fallback_temporal_or_continuity_confused_reply(
                inbound_text,
                response_text,
                flags=effective_flags,
                user=user,
            ) or response_text
        logger.info(
            "被动回复模型自检完成: mode=%s flags=%s elapsed=%dms",
            review_mode,
            ",".join(effective_flags),
            int((time.perf_counter() - started) * 1000),
        )
        cleaned = str(rewritten or "").strip()
        if not cleaned:
            return response_text
        if self._is_response_review_drop_marker(cleaned):
            if review_strength == "lenient":
                logger.info(
                    "被动回复宽松复核忽略取消判定,保留原回复: flags=%s",
                    ",".join(effective_flags),
                )
                return response_text
            logger.info(
                "被动回复模型自检判定重复,已标记丢弃: flags=%s before=%s",
                ",".join(effective_flags),
                _single_line(response_text, 120),
            )
            return self._response_review_drop_marker()
        meta_leak_reason = self._response_review_meta_leak_reason(cleaned)
        if meta_leak_reason:
            logger.error(
                "被动回复复核模型返回内部判断，已回退复核前正文: reason=%s output=%s",
                meta_leak_reason,
                _single_line(cleaned, 180),
            )
            return self._fallback_temporal_or_continuity_confused_reply(
                inbound_text,
                response_text,
                flags=effective_flags,
                user=user,
            ) or response_text
        if len(cleaned) > max(
            len(response_text) + 80,
            runtime_persona_setting(self, "response_review_max_chars", 260) + 160,
        ):
            fallback = self._fallback_overlong_casual_reply(inbound_text, response_text)
            return fallback or response_text
        if re.search(r"(提示词|系统|JSON|改写后|以下是)", cleaned, re.IGNORECASE):
            return self._fallback_temporal_or_continuity_confused_reply(
                inbound_text,
                response_text,
                flags=effective_flags,
                user=user,
            ) or response_text
        if last_message and not allow_repeat and self._text_repeats_recent_message(cleaned, last_message):
            if review_strength == "lenient":
                return response_text
            logger.info(
                "被动回复模型自检后仍复读,已标记丢弃: before=%s",
                _single_line(cleaned, 120),
            )
            return self._response_review_drop_marker()
        if (
            any(flag in effective_flags for flag in ("casual_overexplained", "weather_overexplained"))
            and len(cleaned) > self._casual_reply_review_limit(inbound_text)
        ):
            fallback = self._fallback_overlong_casual_reply(inbound_text, cleaned)
            return fallback or cleaned
        return cleaned

    def _passive_response_review_enabled(self) -> bool:
        return bool(
            runtime_persona_setting(
                self,
                "enable_passive_response_review",
                runtime_persona_setting(self, "enable_response_self_review", True),
            )
        )

    def _effective_passive_review_mode(self) -> str:
        mode = str(
            runtime_persona_setting(
                self,
                "passive_review_mode",
                runtime_persona_setting(self, "response_review_mode", "severe_only"),
            )
            or "severe_only"
        ).strip().lower()
        return mode if mode in {"local_only", "severe_only", "full"} else "severe_only"

    def _effective_passive_review_strength(self) -> str:
        strength = str(runtime_persona_setting(self, "passive_review_strength", "lenient") or "lenient").strip().lower()
        return strength if strength in {"lenient", "balanced", "strict"} else "lenient"

    @staticmethod
    def _response_review_meta_leak_reason(text: Any) -> str:
        raw = str(text or "").strip()
        if not raw:
            return ""
        compact = re.sub(r"\s+", " ", raw).strip()
        lower = compact.lower()
        if re.search(r"\bmaybe\s+\d+(?:\.\d+)?%\s+of\s+the\s+time\b", lower):
            return "复核模型输出概率说明"
        if re.search(r"\bat\s+the\s+(?:very\s+)?end\s+of\s+(?:a|the)\s+run\b", lower):
            return "复核模型输出运行说明"
        if re.search(
            r"\b(?:decision|verdict|review result|review reason|reason)\s*[:：]",
            lower,
        ):
            return "复核模型输出判定字段"
        if re.search(
            r"\b(?:response|output|message)\b.{0,80}\b(?:needs?\s+(?:to\s+be\s+)?rewritten|"
            r"cannot\s+be\s+saniti[sz]ed|formatting\s+(?:issue|problem)|should\s+not\s+be\s+sent)\b",
            lower,
        ):
            return "复核模型输出英文审核评语"
        chinese_review_context = re.search(
            r"(?:原(?:回复|文本|输出)|这条(?:回复|消息|输出)|回复内容|输出内容|后处理|清洗|复核|审核|"
            r"格式化表达|重复标点|一字废话|最终回复|正常人无法容忍)",
            compact,
        )
        chinese_verdict = re.search(
            r"(?:无法|不能|不应|不宜|不适合|未通过|拒绝|需要|应当|建议).{0,24}"
            r"(?:清洗|规整|发送|通过|重写|改写|修正)",
            compact,
        )
        if chinese_review_context and chinese_verdict:
            return "复核模型输出中文审核评语"
        if re.search(r"(?:判定|审核|复核)(?:结果|结论|原因)?\s*[:：]", compact):
            return "复核模型输出判定字段"
        return ""

    def _strip_response_review_meta_leak(self, text: Any) -> tuple[str, str]:
        raw = str(text or "").strip()
        if not raw:
            return "", ""
        kept: list[str] = []
        reasons: list[str] = []
        for line in raw.splitlines():
            stripped = line.strip()
            if not stripped:
                if kept and kept[-1] != "":
                    kept.append("")
                continue
            reason = self._response_review_meta_leak_reason(stripped)
            if reason:
                reasons.append(reason)
                continue
            kept.append(stripped)
        if not reasons:
            whole_reason = self._response_review_meta_leak_reason(raw)
            if whole_reason:
                return "", whole_reason
            return raw, ""
        cleaned = "\n".join(kept).strip()
        return cleaned, "、".join(dict.fromkeys(reasons))
