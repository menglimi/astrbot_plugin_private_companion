# -*- coding: utf-8 -*-
"""TtsEnhancementVisibleChineseMixin。

由 tools/split_mixin_domain.py 从 tts_enhancement.py 机械抽取（19 个方法 + 0 个模块级名字 + 0 个类级赋值 / 592 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 TtsEnhancementMixin）。
"""
from __future__ import annotations

import re
from .conversation_prompt_section import PromptRenderMode, PromptSection, prompt_section, render_prompt_sections
from .helpers import _single_line
from .tts_enhancement_shared import logger
from typing import Any



class TtsEnhancementVisibleChineseMixin:
    """TtsEnhancementVisibleChineseMixin（从 TtsEnhancementMixin 拆出）。"""


    def _tts_visible_text_is_safe_nonlinguistic(self, text: str) -> bool:
        """Allow numeric/formula/code-like visible text after a voice block.

        The Chinese-meaning guard exists to prevent Japanese/foreign TTS text from
        leaking into chat. Some useful answers, however, are mostly numbers or
        formulas, for example prime numbers, modulo values, URLs, or command
        snippets. Those should remain visible even without two Chinese characters.
        """
        cleaned = self._sanitize_tts_visible_text(text)
        if not cleaned:
            return False
        if re.search(r"[\u3040-\u30ff\u31f0-\u31ff]", cleaned):
            return False
        digit_count = len(re.findall(r"\d", cleaned))
        if digit_count < 1:
            return False
        cjk_chars = re.findall(r"[\u4e00-\u9fff]", cleaned)
        allowed_cjk = set("和及以及或与到至第个号位长度模数约等于大小常见")
        if any(char not in allowed_cjk for char in cjk_chars):
            return False
        latin_words = re.findall(r"[A-Za-z]+", cleaned)
        allowed_words = {"e", "x", "y", "n", "mod", "url", "http", "https", "id", "api", "ip"}
        if any(word.lower() not in allowed_words for word in latin_words):
            return False
        residue = re.sub(r"[\dA-Za-z\s,，.。:：;；、+\-*/\\%^=≈<>≤≥()（）\[\]【】{}#_&|~`'\"!！?？@￥$]+", "", cleaned)
        residue = "".join(char for char in residue if char not in allowed_cjk)
        return not residue

    def _tts_visible_text_is_allowed_after_voice(self, text: str) -> bool:
        return self._tts_visible_text_has_chinese(text) or self._tts_visible_text_is_safe_nonlinguistic(text)

    def _tts_plain_text_is_unwrapped_foreign_reply(self, text: str, event: Any = None) -> bool:
        """Identify an obvious foreign-language leak from postprocess fallback."""
        if self._tts_voice_language_for_event(event) == "zh":
            return False
        if self._tts_setting("tts_foreign_text_mode", "translation") == "original":
            return False
        if self._event_explicitly_requests_foreign_visible_text(event):
            return False
        cleaned = self._sanitize_tts_visible_text(text)
        if not cleaned or "http" in cleaned.lower() or self._tts_visible_text_is_allowed_after_voice(cleaned):
            return False
        kana_count = len(re.findall(r"[\u3040-\u30ff\u31f0-\u31ff]", cleaned))
        latin_count = len(re.findall(r"[A-Za-z]", cleaned))
        return kana_count >= 2 or latin_count >= 12

    def _tts_visible_text_is_complete_before_voice(self, text: str, spoken: str) -> bool:
        """Recognize a model-authored Chinese reply placed before its voice block."""
        cleaned = self._sanitize_tts_visible_text(text)
        if not cleaned or not self._tts_visible_text_has_chinese(cleaned):
            return False
        cjk_count = len(re.findall(r"[\u4e00-\u9fff]", cleaned))
        spoken_units = len(
            re.findall(r"[\u3040-\u30ff\u31f0-\u31ff\u4e00-\u9fffA-Za-z0-9]", str(spoken or ""))
        )
        required_cjk = min(16, max(6, (spoken_units + 2) // 3))
        if cjk_count < required_cjk:
            return False
        compact = re.sub(r"[\s，。！？!?,.、~～…]+$", "", cleaned)
        incomplete_endings = (
            "我说", "你说", "想说", "要说", "会说", "告诉", "因为", "所以",
            "但是", "然后", "如果", "虽然", "关于", "至于", "例如", "比如",
            "以及", "或者", "还是", "要不要", "能不能", "是否",
        )
        return bool(compact) and not compact.endswith(incomplete_endings)

    def _tts_chinese_visible_fallback_from_mixed(self, text: str) -> str:
        """Extract visible Chinese explanation from a mixed spoken-language fallback."""
        cleaned = self._sanitize_tts_visible_text(text)
        if not cleaned:
            return ""
        if self._tts_visible_text_is_allowed_after_voice(cleaned):
            return _single_line(cleaned, 800)
        parts: list[str] = []
        candidates = re.findall(r"[\u4e00-\u9fff][^\u3040-\u30ff\u31f0-\u31ff\r\n]*", cleaned)
        if not candidates:
            candidates = re.split(r"(?<=[。！？!?…])\s+|[\r\n]+", cleaned)
        for part in candidates:
            part = part.strip()
            if not part or re.search(r"[\u3040-\u30ff\u31f0-\u31ff]", part):
                continue
            if self._tts_visible_text_is_allowed_after_voice(part):
                parts.append(part)
        return _single_line("\n".join(parts), 800)

    def _tts_unwrapped_foreign_translation_fallback(self, text: str, event: Any = None) -> str:
        """Recover the visible Chinese half of an unwrapped fast-tag reply.

        Fast-tag replies reserve foreign text for ``<pc_tts>``. A few models
        occasionally omit the wrapper but still emit the prescribed
        "foreign speech + Chinese display text" layout. Restrict recovery to
        that exact shape so ordinary Chinese replies with a foreign word, and
        user-requested foreign text, remain untouched.
        """
        if self._tts_setting("tts_generation_mode", "fast_tag") != "fast_tag":
            return ""
        if self._tts_voice_language_for_event(event) == "zh":
            return ""
        if self._tts_setting("tts_foreign_text_mode", "translation") != "translation":
            return ""
        if self._event_explicitly_requests_foreign_visible_text(event):
            return ""
        cleaned = self._sanitize_tts_visible_text(text, max_chars=1600)
        if not cleaned or re.search(r"</?(?:pc[_-]?tts|t{2,}s)\b", cleaned, flags=re.IGNORECASE):
            return ""
        first_visible = re.search(r"[^\s\[\(（\"'“‘]", cleaned)
        if first_visible is None or not re.match(r"[\u3040-\u30ff\u31f0-\u31ff]", first_visible.group(0)):
            return ""
        kana_count = len(re.findall(r"[\u3040-\u30ff\u31f0-\u31ff]", cleaned))
        if kana_count < 2:
            return ""
        visible_chinese = self._tts_chinese_visible_fallback_from_mixed(cleaned)
        if not visible_chinese or visible_chinese == cleaned:
            return ""
        return visible_chinese

    async def _translate_tts_spoken_to_chinese(self, text: str, event: Any, *, provider_kind: str) -> str:
        spoken = self._normalize_tts_spoken_text(text, provider_kind=provider_kind)
        if not spoken:
            return ""
        if self._tts_visible_text_has_chinese(spoken):
            return spoken
        provider = await self._get_tts_conversion_provider(event) if event is not None else None
        persona_context = await self._format_tts_persona_voice_context(event)
        prompt_section_value = prompt_section(
            key="background.tts_visible_translation",
            title="TTS 可见中文翻译",
            source="tts_enhancement",
            content=f"""
请把下面这句 TTS 朗读文本翻译成自然中文，只输出中文句子，不要解释，不要保留 <tts> 标签。
要求：
- 保留原本亲近、害羞、吐槽或撒娇的语气。
- 翻译后的中文要像当前人格自己会发在聊天里的文字，而不是字幕腔或机器翻译腔。
- 不要添加原文没有的新信息。
- 输出适合作为聊天里语音后的可见中文说明。
- 输出必须是完整自然中文句子，不能以“还/还是/或者/要不要/因为/所以/但是/然后/和/对/从/到/让”等连接词或半个问题结尾。
{persona_context}

TTS 朗读文本：
{spoken}
""".strip(),
        )
        prompt = render_prompt_sections(
            [prompt_section_value],
            mode=PromptRenderMode.BODY_ONLY,
        )
        try:
            if provider is not None:
                resp = await self._tts_provider_text_chat(provider, prompt, max_tokens=240, task="tts_visible_translation")
                translated = str(getattr(resp, "completion_text", resp) or "").strip()
                translated = self._strip_any_tts_markup(translated)
                translated = _single_line(translated, 300)
                same_as_source = (
                    re.sub(r"\W+", "", translated, flags=re.UNICODE).lower()
                    == re.sub(r"\W+", "", spoken, flags=re.UNICODE).lower()
                )
                if self._tts_visible_text_has_chinese(translated) and not same_as_source:
                    return translated
                if translated:
                    logger.warning(
                        "TTS中文释义结果不像中文,已丢弃: source=%s result=%s",
                        _single_line(spoken, 80),
                        _single_line(translated, 80),
                    )
        except Exception as exc:
            logger.warning("TTS中文释义生成失败: %s", _single_line(exc, 120))
        return ""

    async def _ensure_tts_blocks_have_visible_chinese(self, text: str, event: Any, *, provider_kind: str) -> str:
        normalized = self._normalize_tts_tags(text)
        if (
            self._tts_voice_language_for_event(event) == "zh"
            or self._tts_setting("tts_delivery_mode", "voice_and_text") == "voice_only"
            or self._tts_setting("tts_foreign_text_mode", "translation") == "original"
            or self._event_explicitly_requests_foreign_visible_text(event)
        ):
            return normalized
        matches = list(re.finditer(r"<tts>(.*?)</tts>", normalized, flags=re.IGNORECASE | re.DOTALL))
        if not matches:
            return normalized
        pieces: list[str] = []
        pos = 0
        changed = False
        for index, match in enumerate(matches):
            pieces.append(normalized[pos:match.end()])
            next_start = matches[index + 1].start() if index + 1 < len(matches) else len(normalized)
            visible_after_this_block = normalized[match.end():next_start]
            spoken = self._normalize_tts_spoken_text(match.group(1), provider_kind=provider_kind)
            complete_chinese_before_voice = (
                index == 0
                and self._tts_visible_text_is_complete_before_voice(
                    normalized[:match.start()],
                    spoken,
                )
            )
            if (
                not self._tts_visible_text_is_allowed_after_voice(visible_after_this_block)
                and not complete_chinese_before_voice
            ):
                visible_translation = await self._translate_tts_spoken_to_chinese(spoken, event, provider_kind=provider_kind)
                if visible_translation:
                    separator = "\n" if not visible_after_this_block.startswith(("\n", "\r")) else ""
                    pieces.append(f"{separator}{visible_translation}")
                    changed = True
                    logger.info(
                        "TTS记录文本已补中文释义: 语音=%s 中文=%s",
                        _single_line(spoken, 80),
                        _single_line(visible_translation, 80),
                    )
                else:
                    logger.warning(
                        "TTS记录文本缺少中文释义且自动补充失败: 语音=%s",
                        _single_line(spoken, 100),
                    )
            pieces.append(visible_after_this_block)
            pos = next_start
        pieces.append(normalized[pos:])
        return "".join(pieces) if changed else normalized

    def _tts_text_needs_language_conversion(
        self,
        text: str,
        *,
        provider_kind: str,
        event: Any = None,
    ) -> bool:
        spoken = self._normalize_tts_spoken_text(text, provider_kind=provider_kind)
        if not spoken:
            return False
        lang = self._tts_voice_language_for_event(event)
        kana_count = len(re.findall(r"[\u3040-\u30ff\u31f0-\u31ff]", spoken))
        cjk_count = len(re.findall(r"[\u4e00-\u9fff]", spoken))
        latin_count = len(re.findall(r"[A-Za-z]", spoken))
        if lang == "zh":
            return kana_count > 0 or (cjk_count == 0 and latin_count >= 12)
        if lang == "en":
            return cjk_count > 0 or kana_count > 0
        if lang != "ja":
            return False
        if not kana_count and cjk_count == 0 and latin_count >= 12:
            return True
        chinese_markers = (
            "的", "了", "吗", "呢", "吧", "呀", "哦", "啊", "嘛",
            "就是", "有点", "很", "超", "画风", "氛围", "标签", "喜欢",
            "不好意思", "说出口", "温柔",
        )
        if any(marker in spoken for marker in chinese_markers):
            return True
        if not kana_count and cjk_count >= 4:
            return True
        return bool(cjk_count >= 6 and kana_count < max(2, int(cjk_count * 0.35)))

    @staticmethod
    def _tts_expression_style_context(event: Any) -> str:
        decision = getattr(event, "_private_companion_expression_decision", None)
        if not isinstance(decision, dict):
            return ""
        return (
            "统一表达投影："
            f"tts={_single_line(decision.get('tts_style'), 16) or 'neutral'}；"
            f"节奏={_single_line(decision.get('pacing'), 16) or 'steady'}；"
            f"直接度={_single_line(decision.get('directness'), 16) or 'natural'}；"
            f"回应={_single_line(decision.get('validation_style'), 20) or 'none'}；"
            f"自述={_single_line(decision.get('self_disclosure'), 16) or 'none'}；"
            f"幽默={_single_line(decision.get('humor_mode'), 16) or 'off'}；"
            f"话题={_single_line(decision.get('topic_initiative'), 20) or 'reply_only'}。"
        )

    def _build_tts_rule_prompt_section(
        self,
        provider_kind: str = "generic",
        *,
        event: Any = None,
    ) -> PromptSection:
        voice_lang = self._tts_voice_language_for_event(event)
        lang = self._tts_language_label(voice_language=voice_lang)
        mode = self._tts_setting("tts_generation_mode", "fast_tag")
        frequency_mode = self._tts_setting("tts_frequency_control_mode", "global")
        delivery_mode = self._tts_setting("tts_delivery_mode", "voice_and_text")
        foreign_text_mode = self._tts_setting("tts_foreign_text_mode", "translation")
        conversion_scope = self._tts_setting("tts_conversion_scope", "partial")
        full_scope = conversion_scope == "full"
        tts_signal, tts_signal_match, _ = self._event_tts_request_signal(event)
        keyword_rule = (
            f"用户消息命中已配置的 TTS 关键词（{_single_line(tts_signal_match[8:], 80)}）；本轮按语音请求处理，"
            "请在回复中使用符合下方格式的语音块，同时保持内容适合朗读。"
            if tts_signal == "positive" and tts_signal_match.startswith("keyword:")
            else ""
        )
        supports_emotion = self._tts_provider_allows_emotion_tags(provider_kind)
        auto_emotion = supports_emotion and not (
            provider_kind.startswith("fishaudio") and self._fishaudio_emotion_mode() == "manual"
        )
        if mode == "fast_tag":
            if frequency_mode == "legacy":
                usage_rule = "由你根据当前回复是否适合被听见、情绪是否更贴近、用户是否明显期待语音来自行判断；不要为了格式而滥用。"
            else:
                usage_rule = "不要刻意使用语音；只有在当前回复更适合被听见、情绪更贴近或用户明显期待语音时才写 <pc_tts>。"
        else:
            usage_rule = "本轮主模型可以正常回复，不需要主动写 <pc_tts> 或 <tts>；后处理会生成语音格式。"
        positive_emotion, negative_emotion = self._tts_emotion_tag_examples(
            provider_kind,
            voice_language=voice_lang,
        )
        emotion_rule = (
            f"3.{self._tts_emotion_tag_rule(provider_kind, voice_language=voice_lang)}"
            if supports_emotion
            else ""
        )
        language_rule = ""
        if delivery_mode == "voice_only":
            language_rule = "语音合成成功后只发送语音，不需要在语音块后重复对应文字；生成失败时插件会自动保留文字兜底。"
        elif voice_lang == "zh":
            language_rule = "<pc_tts> 内也用自然中文；语音块后不强制再写重复翻译。"
        elif foreign_text_mode == "original":
            language_rule = f"<pc_tts> 内必须是自然{lang}；可见文字显示最终朗读原文，不要求补中文翻译。"
        elif foreign_text_mode == "bilingual":
            language_rule = f"<pc_tts> 内必须是自然{lang}；插件最终会同时显示朗读原文和自然中文译文。"
        elif voice_lang == "en":
            language_rule = "<pc_tts> 内必须是自然英语；每个语音块后直接补一句自然中文，不要加“中文含义：”“对应文本：”这类标题。英语朗读稿绝不能裸写在标签外；若本轮不用标签，整条可见正文只能使用中文。"
        else:
            language_rule = "<pc_tts> 内必须是自然日语，除极短语气词外要包含假名；每个语音块后直接补一句自然中文，不要加“中文含义：”“对应文本：”这类标题。日语朗读稿绝不能裸写在标签外；若本轮不用标签，整条可见正文只能使用中文。"
        examples = ""
        if mode == "fast_tag":
            if full_scope and voice_lang == "zh":
                examples = (
                    "示例：\n"
                    "不使用语音：嗯，我在听。你慢慢说。\n"
                    f"使用语音：<pc_tts>{positive_emotion if auto_emotion else ''}嗯，我在听。你慢慢说。</pc_tts>"
                )
            elif full_scope and voice_lang == "en":
                visible = "" if delivery_mode == "voice_only" or foreign_text_mode == "original" else "我在听，你慢慢说。"
                examples = (
                    "示例：\n"
                    "不使用语音：我在听，你慢慢说。\n"
                    f"使用语音：<pc_tts>{negative_emotion if auto_emotion else ''}I am listening. Take your time.</pc_tts>{visible}"
                )
            elif full_scope:
                visible = "" if delivery_mode == "voice_only" or foreign_text_mode == "original" else "我有在好好听哦，你慢慢说。"
                examples = (
                    "示例：\n"
                    "不使用语音：我有在好好听哦，你慢慢说。\n"
                    f"使用语音：<pc_tts>{negative_emotion if auto_emotion else ''}ちゃんと聞いてるよ。ゆっくり話してね。</pc_tts>{visible}"
                )
            elif voice_lang == "zh":
                examples = "示例：先别急，<pc_tts>我陪你想一下。</pc_tts>这件事可以一点点拆开。"
            elif voice_lang == "en":
                examples = "示例：先别急，<pc_tts>Let me stay with you for a moment.</pc_tts>我先在你旁边待一会儿。"
            else:
                examples = "示例：先别急，<pc_tts>少しだけ、そばにいるね。</pc_tts>我先在你旁边待一会儿。"
        extra = _single_line(self._tts_setting("tts_extra_prompt", ""), 800)
        if not extra:
            extra = self._legacy_nondefault_tts_prompt()
        if full_scope and delivery_mode == "voice_only":
            first_rule = "1.选择使用语音时，把整条回复的全部有效内容放进唯一一对<pc_tts>；标签外不要留下未朗读正文，也不要重复同一句；"
        elif full_scope and voice_lang == "zh":
            first_rule = "1.选择使用语音时，把整条中文回复放进唯一一对<pc_tts>；不要在标签外留下未朗读正文；"
        elif full_scope and foreign_text_mode == "original":
            first_rule = f"1.选择使用语音时，把整条回复完整改写为自然{lang}并放进唯一一对<pc_tts>；不要在标签外留下未朗读正文；"
        elif full_scope:
            first_rule = f"1.选择使用语音时，把整条回复完整改写为自然{lang}并放进唯一一对<pc_tts>，标签后只补对应的完整自然中文；不要留下未朗读正文；"
        elif delivery_mode == "voice_only":
            first_rule = "1.把适合朗读的内容用一对<pc_tts>包起来；语音成功后对应文字会隐藏，不要在标签外重复同一句；"
        elif voice_lang == "zh":
            first_rule = "1.自然聊天时用中文文字推进对话，把适合朗读的中文部分用一对<pc_tts>包起来；"
        elif foreign_text_mode == "original":
            first_rule = "1.把适合朗读的外语部分用一对<pc_tts>包起来；可见文字会使用最终外语朗读原文，不强制补中文；"
        else:
            first_rule = "1.自然聊天时用中文文字推进对话，把适合朗读的外语部分用一对<pc_tts>包起来，并在后面直接补一句自然中文，不要写“中文含义：”“对应文本：”这类标题；"
        scope_rule = (
            "2.选择使用语音时，让语音块覆盖整条回复的全部有效内容，不要只截取一句；"
            if conversion_scope == "full"
            else "2.只把最适合听的一小段放进语音块，其余信息继续用普通文字表达；"
        )
        rules = [
            (
                f"用户本轮明确要求使用{lang}回复；本轮语音正文必须服从这个临时语种，"
                "不要沿用长期 TTS 语种。该要求只作用于当前回复。"
                if self._normalize_tts_voice_language_value(
                    getattr(event, "_private_companion_tts_voice_language", "")
                    if event is not None
                    else ""
                )
                else ""
            ),
            keyword_rule,
            first_rule,
            scope_rule,
            "自动语音概率命中只表示本轮可以考虑语音，不表示必须使用语音。功能性回复默认保持纯文字，包括指令执行结果、帮助或菜单、配置或状态、查询结果、报错或权限说明、清单、教程、代码以及主要由卡片或图片承载的结果；只有用户明确要求语音或朗读，或回复本身主要是适合听见的自然角色表达时，才考虑语音。",
            "URL、域名、邮箱、命令、文件路径、长编号和邀请码不适合朗读：不要放进 <pc_tts>；必须在语音块外保留原文供用户点击或复制。语音里需要承接时，只自然说“链接在文字里”或“我把链接发给你了”，不要念出协议、域名、路径或参数。",
        ]
        if (
            voice_lang != "zh"
            and delivery_mode != "voice_only"
            and foreign_text_mode != "original"
        ):
            rules.append(
                "非中文语音的结构必须完整：每个 </pc_tts> 后都要紧跟非空、自然、与该语音含义一致的中文可见正文；如果无法同时给出中文正文，就不要使用语音标签，直接用普通中文回复。"
            )
            rules.append(
                "语音内容对应的中文释义只放在对应 </pc_tts> 后面；不要先把语音内容完整写成中文再附语音块，也不要在语音块前后重复同一含义。"
            )
            if full_scope:
                rules.append(
                    "全量外语语音的标准结构是唯一一对 <pc_tts> 外语朗读块后紧跟同义中文可见正文；这段中文是显示译文，不是未朗读的额外正文，发送前应优先保留外语语音块。"
                )
        if emotion_rule:
            rules.append(emotion_rule)
        body = "\n".join(
            item
            for item in [
                "\n".join(rules),
                f"当前转换范围：{'全量转换' if full_scope else '局部转换'}。",
                f"当前语音正文目标语种：{lang}。",
                language_rule,
                usage_rule,
                examples,
                f"补充规则：{extra}" if extra else "",
            ]
            if item
        )
        return prompt_section(
            key="tts.rule",
            title="语音消息规则",
            source="tts_enhancement",
            content=body,
        )

    def _build_tts_rule_prompt(self, provider_kind: str = "generic", *, event: Any = None) -> str:
        return render_prompt_sections(
            [self._build_tts_rule_prompt_section(provider_kind, event=event)],
            mode=PromptRenderMode.LABELED_BLOCK,
        )

    def _legacy_nondefault_tts_prompt(self) -> str:
        try:
            config = getattr(self, "config", {}) or {}
            value = str(config.get("tts_prompt", "") or "").strip()
        except Exception:
            value = ""
        if not value:
            return ""
        lowered = value.lower()
        if "<tts>" in lowered and "日语" in value and len(value) > 80:
            return ""
        return _single_line(value, 800)

    async def _tts_persona_voice_context(self, event: Any, *, max_chars: int = 900) -> str:
        """Return a compact persona reference for TTS text-only models."""
        umo = str(getattr(event, "unified_msg_origin", "") or "") if event is not None else ""
        refresher = getattr(self, "_refresh_default_persona_prompt", None)
        persona = ""
        if callable(refresher):
            try:
                persona = str(await refresher(umo) or "").strip()
            except Exception as exc:
                logger.debug("TTS读取人格上下文失败,使用缓存: %s", _single_line(exc, 120))
        if not persona:
            getter = getattr(self, "_get_default_persona_prompt", None)
            if callable(getter):
                try:
                    persona = str(getter() or "").strip()
                except Exception:
                    persona = ""
        if not persona:
            return ""
        persona = re.sub(r"\s+", "\n", persona).strip()
        return _single_line(persona, max_chars)

    async def _format_tts_persona_voice_context(self, event: Any) -> str:
        persona = await self._tts_persona_voice_context(event)
        if not persona:
            return ""
        return (
            "人格语音风格参考：\n"
            f"{persona}\n"
            "使用方式：只用于保持当前人格的称呼、距离感、语气、口癖和角色边界；不要复述人格设定，不要添加原回复没有的新信息。"
        )

    def _disable_streaming_for_tts_turn(self, event: Any) -> bool:
        """让插件 TTS 在完整消息链上运行，避免流式结果绕过发送前钩子。"""
        if event is None or bool(getattr(event, "_private_companion_tts_streaming_disabled", False)):
            return bool(getattr(event, "_private_companion_tts_streaming_disabled", False))
        setter = getattr(event, "set_extra", None)
        if not callable(setter):
            logger.debug(
                "TTS 回合无法关闭流式：事件不支持 set_extra session=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            )
            return False
        previous = None
        getter = getattr(event, "get_extra", None)
        if callable(getter):
            try:
                previous = getter("enable_streaming")
            except Exception:
                previous = None
        try:
            setter("enable_streaming", False)
            setattr(event, "_private_companion_tts_streaming_disabled", True)
            setattr(event, "_private_companion_tts_streaming_previous", previous)
        except Exception as exc:
            logger.debug(
                "TTS 回合关闭流式失败 session=%s error=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                _single_line(exc, 160),
            )
            return False
        logger.info(
            "TTS 已预留本回合完整消息链并关闭流式输出: session=%s previous=%s",
            _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            previous,
        )
        return True

    def _tts_turn_requires_complete_reply(self, event: Any) -> bool:
        """在 AstrBot 读取流式开关前，预判本轮是否可能进入插件 TTS。"""
        if event is None or bool(getattr(event, "_private_companion_tts_streaming_disabled", False)):
            return bool(getattr(event, "_private_companion_tts_streaming_disabled", False))
        feature_enabled = getattr(self, "_feature_enabled_or_temp_unlocked", None)
        tts_enabled = feature_enabled("enable_tts_enhancement") if callable(feature_enabled) else self._tts_setting("enable_tts_enhancement", False)
        if not getattr(self, "enabled", False) or not tts_enabled:
            return False
        proactive_blocker = getattr(self, "_proactive_only_blocks_passive_event", None)
        if callable(proactive_blocker):
            try:
                if proactive_blocker(event, "enable_tts_enhancement"):
                    return False
            except Exception:
                return False
        turn_voice_language = self._ensure_turn_tts_voice_language(event)
        user_requested_tts = self._event_explicitly_requests_tts(event) or bool(turn_voice_language)
        if self._tts_functional_command_reason(event) and not user_requested_tts:
            return False
        mode = self._tts_setting("tts_generation_mode", "fast_tag")
        if mode not in {"fast_tag", "postprocess"}:
            return False
        if (
            not user_requested_tts
            and self._tts_setting("tts_frequency_control_mode", "global") != "legacy"
            and not self._tts_trigger_probability_allows(event, reason="streaming_preflight")
        ):
            return False
        if mode == "fast_tag":
            strong_block_reason = self._tts_strong_constraint_block_reason(
                event,
                user_requested_tts=user_requested_tts,
                check_probability=False,
                reason="streaming_preflight",
            )
            if strong_block_reason:
                self._set_tts_hard_block(event, strong_block_reason)
                return False
        return True

    def _build_tts_postprocess_mode_prompt_section(
        self,
        event: Any,
        *,
        full_scope: bool,
        turn_voice_language: str,
    ) -> PromptSection:
        scope_text = "是否将整条回复转成语音" if full_scope else "是否把其中一小段转成语音"
        language_text = self._tts_language_label(event)
        foreign_visible_requested = self._event_explicitly_requests_foreign_visible_text(
            event,
            voice_language=turn_voice_language,
        )
        temporary_language_rule = (
            f"用户本轮明确指定了{language_text}；后处理语音必须使用{language_text}，该临时要求只作用于当前回复。"
            if turn_voice_language
            else ""
        )
        temporary_visible_rule = (
            f"用户同时明确要求显示{language_text}文字，因此普通正文可以保留{language_text}。"
            if foreign_visible_requested
            else "用户没有明确要求显示外语文字时，普通正文继续使用当前聊天语言。"
        )
        body = (
            "本轮主回复请只输出普通聊天文字，不要主动写 <pc_tts>、<tts>、语音、朗读、音频或任何等价语音标签。"
            "主回复是直接展示给用户看的正文，保持当前聊天语言（通常为中文）；不要把准备送入语音的日语或英语朗读稿直接写进普通正文。"
            "目标语种只交给发送前 TTS 后处理生成 voice_text，visible_text 保持用户看得懂的正文；只有用户本轮明确要求目标语种文字回复时才例外。"
            "如果用户是在补要或追问上一条语音，只回复这次应该说的内容；不要预告或确认“语音已经发出”“这次真发了”，实际发送结果由插件决定。"
            f"当前是{'全量' if full_scope else '局部'}转换；{scope_text}，将由插件发送前的 TTS 后处理模型统一判断。"
            f"{temporary_language_rule}{temporary_visible_rule}"
        )
        return prompt_section(
            key="tts.rule",
            title="TTS 后处理模式",
            source="tts_enhancement",
            content=body,
        )

    def _build_tts_postprocess_mode_prompt(
        self,
        event: Any,
        *,
        full_scope: bool,
        turn_voice_language: str,
    ) -> str:
        return render_prompt_sections(
            [
                self._build_tts_postprocess_mode_prompt_section(
                    event,
                    full_scope=full_scope,
                    turn_voice_language=turn_voice_language,
                )
            ],
            mode=PromptRenderMode.LABELED_BLOCK,
        )
