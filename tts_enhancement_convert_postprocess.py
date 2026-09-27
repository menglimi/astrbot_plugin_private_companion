# -*- coding: utf-8 -*-
"""TtsEnhancementConvertPostprocessMixin。

由 tools/split_mixin_domain.py 从 tts_enhancement.py 机械抽取（13 个方法 + 0 个模块级名字 + 0 个类级赋值 / 675 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 TtsEnhancementMixin）。
"""
from __future__ import annotations

import asyncio
import json
import random
import re
import subprocess
import sys
import time
from .conversation_prompt_section import PromptRenderMode, prompt_section, render_prompt_sections
from .helpers import _safe_int, _single_line
from .tts_enhancement_shared import logger
from typing import Any



class TtsEnhancementConvertPostprocessMixin:
    """TtsEnhancementConvertPostprocessMixin（从 TtsEnhancementMixin 拆出）。"""


    async def _translate_unwrapped_foreign_postprocess_text(self, text: str, event: Any) -> str:
        if not self._tts_plain_text_is_unwrapped_foreign_reply(text, event):
            return ""
        provider_kind = "generic"
        try:
            config = self.context.get_config(str(getattr(event, "unified_msg_origin", "") or "")) or {}
            provider_kind = self._tts_provider_kind_for_event(event, config=config)
        except Exception:
            pass
        return await self._translate_tts_spoken_to_chinese(
            text,
            event,
            provider_kind=provider_kind,
        )

    def _auto_voice_trigger_reason(self, text: str, event: Any) -> tuple[bool, str]:
        use_legacy_frequency = self._tts_setting("tts_frequency_control_mode", "global") == "legacy"
        probability_forces_conversion = (
            not use_legacy_frequency
            and self._tts_effective_trigger_probability(event) >= 1.0
        )
        if not self._tts_setting("auto_voice_enabled", False) and not probability_forces_conversion:
            return False, ""
        session = str(getattr(event, "unified_msg_origin", "") or "")
        is_main = self._event_targets_main_user(event)
        main_user_voice_probability = float(self._tts_setting("main_user_voice_probability", 0.0) or 0.0)
        if use_legacy_frequency and is_main and main_user_voice_probability >= 0:
            probability = main_user_voice_probability
            bypass_limits = True
            reason = "main_user"
        else:
            probability = self._tts_setting("auto_voice_probability", 0.0) if use_legacy_frequency else 1.0
            bypass_limits = False
            reason = "probability_100" if probability_forces_conversion else "auto"
        if use_legacy_frequency and self._event_mentions_main_user_with_keyword(event):
            probability = max(probability, self._tts_setting("main_user_mention_voice_probability", 0.0))
            bypass_limits = True
            reason = "main_user_keyword"
        if probability <= 0 or random.random() > probability:
            return False, ""
        cleaned = _single_line(self._normalize_tts_spoken_text(text, provider_kind="generic"), 10000)
        max_chars = 0 if probability_forces_conversion else int(self._tts_setting("auto_voice_max_chars", 0) or 0)
        if max_chars > 0 and not bypass_limits and len(cleaned) > max_chars:
            return False, ""
        cooldown = int(self._tts_setting("auto_voice_cooldown_seconds", 0) or 0) if use_legacy_frequency else 0
        if cooldown > 0 and not bypass_limits and session:
            last = float(getattr(self, "_tts_auto_voice_last_at", {}).get(session, 0) or 0)
            if time.time() - last < cooldown:
                return False, ""
        return True, reason

    def _configured_main_user_ids(self) -> set[str]:
        ids: set[str] = set()
        normalizer = getattr(self, "_normalize_private_identity_id", None)

        def add(raw: Any) -> None:
            text = normalizer(raw) if callable(normalizer) else _single_line(raw, 128)
            if text:
                ids.add(text)

        for value in self._tts_setting("target_user_ids", []) or []:
            add(value)
        aliases = self._tts_setting("private_user_aliases", {}) or {}
        if isinstance(aliases, dict):
            for key, value in aliases.items():
                for raw in (key, value):
                    add(raw)
        return ids

    def _event_main_user_profile_match(self, event: Any, raw_user_id: Any) -> tuple[bool, bool]:
        """Return ``(is_owner, identity_resolved)`` for this event scope.

        ``target_user_ids`` predates platform/account-scoped profiles.  Once an
        event resolves to a stamped profile (or to a different scoped storage
        key), that identity must take precedence so an equal raw ID on another
        adapter cannot inherit the owner's TTS rules.
        """
        normalizer = getattr(self, "_normalize_private_identity_id", None)
        raw_id = normalizer(raw_user_id) if callable(normalizer) else _single_line(raw_user_id, 128)
        if not raw_id:
            return False, False
        resolver = getattr(self, "_private_user_id_for_event", None)
        if not callable(resolver):
            return False, False
        try:
            storage_id = _single_line(resolver(event, raw_id), 160)
        except Exception:
            return False, False
        if not storage_id:
            return False, False
        users = self.data.get("users", {}) if isinstance(getattr(self, "data", None), dict) else {}
        profile = users.get(storage_id) if isinstance(users, dict) else None
        if isinstance(profile, dict):
            identity_stamped = bool(
                _single_line(profile.get("identity_subject_id"), 128)
                or _single_line(profile.get("identity_platform_kind"), 40)
                or storage_id != raw_id
            )
            if identity_stamped:
                role_normalizer = getattr(self, "_normalize_private_user_role", None)
                role = (
                    role_normalizer(profile.get("relationship_role"))
                    if callable(role_normalizer)
                    else _single_line(profile.get("relationship_role"), 40).lower()
                )
                return role == "owner", True
        return False, storage_id != raw_id

    def _event_targets_main_user(self, event: Any) -> bool:
        main_ids = self._configured_main_user_ids()
        if not main_ids:
            return False
        try:
            normalizer = getattr(self, "_normalize_private_identity_id", None)
            sender = normalizer(event.get_sender_id()) if callable(normalizer) else _single_line(event.get_sender_id(), 128)
        except Exception:
            sender = ""
        if sender:
            sender_is_owner, sender_identity_resolved = self._event_main_user_profile_match(event, sender)
            if sender_is_owner:
                return True
            if not sender_identity_resolved and sender in main_ids:
                return True
        for target in self._event_at_qq_ids(event):
            target_is_owner, target_identity_resolved = self._event_main_user_profile_match(event, target)
            if target_is_owner or (not target_identity_resolved and target in main_ids):
                return True
        return False

    def _event_mentions_main_user_with_keyword(self, event: Any) -> bool:
        if not self._event_targets_main_user(event):
            return False
        keywords = [item for item in self._tts_setting("main_user_mention_voice_keywords", []) or [] if item]
        if not keywords:
            return False
        text = str(getattr(event, "message_str", "") or "")
        return any(keyword in text for keyword in keywords)

    def _should_force_tts_for_main_user_event(self, event: Any) -> bool:
        if not self._tts_setting("auto_voice_enabled", False):
            return False
        if self._tts_setting("tts_frequency_control_mode", "global") != "legacy":
            return False
        mention_probability = float(self._tts_setting("main_user_mention_voice_probability", 0.0) or 0.0)
        main_probability = float(self._tts_setting("main_user_voice_probability", 0.0) or 0.0)
        if self._event_mentions_main_user_with_keyword(event) and mention_probability > 0:
            return random.random() <= mention_probability
        if self._event_targets_main_user(event) and main_probability >= 0:
            return random.random() <= main_probability
        return False

    def _event_at_qq_ids(self, event: Any) -> set[str]:
        ids: set[str] = set()
        message_obj = getattr(event, "message_obj", None)
        chain = getattr(message_obj, "message", None)
        for comp in chain or []:
            qq = getattr(comp, "qq", None) or getattr(comp, "target", None)
            text = re.sub(r"\D+", "", str(qq or ""))
            if text:
                ids.add(text)
        raw = str(getattr(event, "message_str", "") or "")
        for match in re.finditer(r"\[At:(\d+)\]|@(\d{5,})", raw):
            ids.add(match.group(1) or match.group(2))
        return ids

    async def _convert_text_to_tts_markup(self, text: str, event: Any, *, full: bool = False) -> str:
        source = _single_line(
            text,
            self._tts_complete_text_limit(text, 1200) if full else 1200,
        )
        if not source:
            return ""
        provider = await self._get_tts_conversion_provider(event)
        provider_kind = self._tts_provider_kind_for_event(event)
        voice_lang = self._tts_voice_language_for_event(event)
        lang = self._tts_language_label(voice_language=voice_lang)
        mode = self._tts_setting("tts_generation_mode", "fast_tag")
        if mode == "postprocess":
            return await self._postprocess_text_to_tts_markup(source, event, provider_kind=provider_kind, full=full)
        extra = _single_line(self._tts_setting("main_user_mention_voice_prompt", ""), 500) if self._event_mentions_main_user_with_keyword(event) else ""
        persona_context = await self._format_tts_persona_voice_context(event)
        expression_context = self._tts_expression_style_context(event)
        emotion_rule = self._tts_emotion_tag_rule(
            provider_kind,
            subject="<pc_tts> 内",
            voice_language=voice_lang,
        )
        if not emotion_rule:
            emotion_rule = "不要加入方括号情绪标签。"
        if voice_lang == "zh":
            output_rule = "必须包含一个 <pc_tts>...</pc_tts> 语音块"
            display_rule = "语音和显示文本同为中文时，不需要额外翻译，标签外仍可保留自然中文聊天文本。"
            language_rule = "语音块内必须是自然中文。"
        elif voice_lang == "en":
            output_rule = "必须包含一个 <pc_tts>...</pc_tts> 英语语音块，且语音块后必须直接补一句自然中文"
            display_rule = "不要只输出 <pc_tts>...</pc_tts>；最终格式建议为：<pc_tts>English voice text</pc_tts>\\n我会在这里。中文句子必须完整收口，不要写“中文含义：”“对应文本：”这类标题。"
            language_rule = "语音块内必须完全使用自然英语，不要夹中文评价、中文语气词或中文说明。"
        else:
            output_rule = "必须包含一个 <pc_tts>...</pc_tts> 日语语音块，且语音块后必须直接补一句自然中文"
            display_rule = "不要只输出 <pc_tts>...</pc_tts>；最终格式建议为：<pc_tts>日本語の朗読文</pc_tts>\\n我会在这里。中文句子必须完整收口，不要写“中文含义：”“对应文本：”这类标题。"
            language_rule = "语音块内必须完全使用自然日语，不要夹中文评价、中文语气词或中文说明；除极短语气词外必须包含假名，不要只输出汉字词。"
        scope_rule = (
            "把原回复中适合朗读的全部自然语言转换成一个完整语音块，不要只截取一句；URL、域名、邮箱、命令、文件路径、长编号和邀请码必须保留在语音块外的可见文字中，不得朗读。"
            if full
            else "只选择最适合朗读的一小段转换成语音，其余信息保留为可见文字；不要把整条长回复都塞进语音，尤其不要朗读 URL、域名、邮箱、命令、文件路径、长编号或邀请码。"
        )
        conversion_section = prompt_section(
            key="background.tts_conversion",
            title="TTS 最终消息转换",
            source="tts_enhancement",
            content=f"""
请把下面这条回复转换成适合 TTS 朗读的最终输出。

目标语种：{lang}
转换范围：{scope_rule}
输出格式：{output_rule}
显示文本规则：{display_rule}
语种规则：{language_rule}
Provider 规则：{emotion_rule}
补充要求：{extra or "无"}
{persona_context}
{expression_context}

原回复：
{source}

只输出最终消息，不要解释。
""".strip(),
        )
        prompt = render_prompt_sections(
            [conversion_section],
            mode=PromptRenderMode.BODY_ONLY,
        )
        try:
            if provider is not None:
                resp = await self._tts_provider_text_chat(provider, prompt, max_tokens=700, task="tts_conversion")
                converted = str(getattr(resp, "completion_text", resp) or "").strip()
            else:
                converted = f"<tts>{source}</tts>"
        except Exception as exc:
            logger.warning("TTS强化转换模型失败: %s", _single_line(exc, 120))
            converted = f"<tts>{source}</tts>"
        converted = self._normalize_tts_tags(converted)
        if "<tts>" not in converted.lower():
            converted = f"<tts>{converted}</tts>"
        return converted

    async def _postprocess_text_to_tts_markup(self, text: str, event: Any, *, provider_kind: str, full: bool = False) -> str:
        source = _single_line(
            text,
            self._tts_complete_text_limit(text, 1600) if full else 1600,
        )
        if not source:
            return ""
        tts_signal, tts_signal_match, user_text = self._event_tts_request_signal(event)
        provider = await self._get_tts_conversion_provider(event)
        if provider is None:
            return ""
        voice_lang = self._tts_voice_language_for_event(event)
        lang = self._tts_language_label(voice_language=voice_lang)
        foreign_visible_requested = self._event_explicitly_requests_foreign_visible_text(
            event,
            voice_language=voice_lang,
        )
        extra = _single_line(self._tts_setting("tts_extra_prompt", ""), 800)
        if not extra:
            extra = self._legacy_nondefault_tts_prompt()
        persona_context = await self._format_tts_persona_voice_context(event)
        expression_context = self._tts_expression_style_context(event)
        if voice_lang == "zh":
            language_rule = "voice_text 必须是自然中文。"
            visible_rule = "visible_text 仍是最终可见中文文本；如果和 voice_text 一样，可以保持同一句。"
        elif voice_lang == "en":
            language_rule = "voice_text 必须是自然英语，不要夹中文说明。"
            visible_rule = (
                "visible_text 使用自然英语并保留完整正文。"
                if foreign_visible_requested
                else "visible_text 必须保留完整自然中文句子，让用户能看懂这段语音对应什么，但不要写“中文含义：”“对应文本：”这类标题。"
            )
        else:
            language_rule = "voice_text 必须是自然日语，不要夹中文说明；除极短语气词外必须包含假名。"
            visible_rule = (
                "visible_text 使用自然日语并保留完整正文。"
                if foreign_visible_requested
                else "visible_text 必须保留完整自然中文句子，让用户能看懂这段语音对应什么，但不要写“中文含义：”“对应文本：”这类标题。"
            )
        emotion_rule = self._tts_emotion_tag_rule(
            provider_kind,
            subject="voice_text 中",
            voice_language=voice_lang,
        )
        if not emotion_rule:
            emotion_rule = "voice_text 不要使用方括号情绪标签。"
        probability_allowed = getattr(event, "_private_companion_tts_postprocess_probability_allowed", None)
        if isinstance(probability_allowed, bool):
            probability_hint = "命中，正常判断是否适合语音" if probability_allowed else "未命中，除非你判断用户本轮确实在要求语音，否则应保持纯文本"
        else:
            probability_hint = "未记录，按普通后处理规则判断"
        scope_rule = (
            "use_tts=true 时，voice_text 必须覆盖原回复的全部有效内容，不得只挑一小段；允许为自然朗读调整句式，但不能遗漏信息。"
            if full
            else "use_tts=true 时，只选择一小段最适合朗读的内容，不要把整条长回复都转成语音。"
        )
        visible_language_hint = (
            f"用户明确要求显示{lang}文字，visible_text 可以保留{lang}。"
            if foreign_visible_requested
            else "用户没有明确要求显示外语文字，visible_text 保持当前聊天语言（通常为中文）。"
        )
        postprocess_section = prompt_section(
            key="background.tts_postprocess",
            title="TTS 后处理判断",
            source="tts_enhancement",
            content=f"""
你是 TTS 后处理模型。请判断这条已经生成好的聊天回复是否需要转成语音，并在需要时完成目标语种改写。

目标语种：{lang}
用户本轮原话：
{user_text or "（无）"}
插件规则快判语音请求线索：{tts_signal}{"；命中片段：" + tts_signal_match if tts_signal_match else ""}
本轮自动语音概率线索：{probability_hint}
补充规则：{extra or "无"}
{persona_context}
{expression_context}

判断规则：
- 规则线索为 positive 时，用户多半正在明确要语音、补发语音或指出语音漏发；只要原回复里有自然可朗读的内容，就优先 use_tts=true 并生成非空 voice_text。
- 即使规则线索为 positive，原回复若只有 URL、命令、代码、文件路径、空白占位或其他不适合朗读的功能内容，仍可 use_tts=false，并在 reason 中说明具体原因。
- 规则线索为 uncertain 时，再根据用户原话和回复内容判断用户是否期待语音。
- 如果规则线索为 negative，通常不要使用语音；除非原话里有更强的相反语境，否则 use_tts=false。
- 如果用户没有明确要求，只有在非常适合被听见、情绪很贴近、短句更有表现力时才使用语音。
- 自动语音概率命中只表示本轮允许考虑语音，不表示必须使用语音。
- 不要为了展示功能而使用语音；指令执行结果、帮助或菜单、配置或状态、查询结果、报错或权限说明、清单、教程、代码，以及主要由卡片或图片承载的结果，都应默认保持纯文字。
- {scope_rule}
- {visible_rule}
- {visible_language_hint}
- visible_text 是直接展示给用户看的正文，应保持当前聊天语言（通常为中文）；不要把 voice_text 的日语或英语朗读稿原样复制到 visible_text。只有用户本轮明确要求目标语种文字回复时才保留该语种正文。
- voice_text 是送入 TTS 的朗读文本。{language_rule}
- URL、域名、邮箱、命令、文件路径、长编号和邀请码不得写入 voice_text；它们必须原样保留在 visible_text，语音只需自然说明链接或信息已放在文字里。
- {emotion_rule}
- voice_text 和 visible_text 都要保持当前人格的说话方式、称呼和距离感。
- 不要添加原回复没有的新信息。
- 用户补要或追问语音时，只保留真正要说的内容，不要预告或确认“语音已经发出”“这次真发了”；实际发送结果由插件决定。

原回复：
{source}

只输出 JSON：
{{
  "use_tts": true/false,
  "reason": "一句话说明",
  "visible_text": "最终可见文本",
  "voice_text": "需要朗读的目标语种文本；不用语音则为空"
}}
""".strip(),
        )
        prompt = render_prompt_sections(
            [postprocess_section],
            mode=PromptRenderMode.BODY_ONLY,
        )
        try:
            postprocess_max_tokens = (
                max(700, min(3000, len(source) * 2 + 200))
                if full
                else 700
            )
            resp = await self._tts_provider_text_chat(
                provider,
                prompt,
                max_tokens=postprocess_max_tokens,
                task="tts_postprocess",
            )
            raw = str(getattr(resp, "completion_text", resp) or "").strip()
            extractor = getattr(self, "_extract_json_payload", None)
            payload = extractor(raw) if callable(extractor) else json.loads(raw)
            if not isinstance(payload, dict):
                return ""
            use_tts = bool(payload.get("use_tts"))
            # Keep the complete visible transcript in full conversion mode. A fixed
            # 900-character cap silently dropped URLs, commands, and the tail of
            # otherwise valid long replies after the voice block was generated.
            visible_limit = (
                self._tts_complete_text_limit(source, 1600)
                if full
                else 900
            )
            visible = self._sanitize_tts_visible_text(
                payload.get("visible_text"),
                max_chars=visible_limit,
            ) or source
            voice = self._normalize_tts_spoken_text(str(payload.get("voice_text") or ""), provider_kind=provider_kind)
            reason = _single_line(payload.get("reason"), 120)
            if not use_tts or not voice:
                logger.info(
                    "TTS 后处理判定不使用语音: session=%s reason=%s",
                    _single_line(getattr(event, "unified_msg_origin", ""), 100) or "unknown",
                    reason or "no_voice",
                )
                return ""
            if self._tts_voice_language_for_event(event) != "zh" and not self._tts_visible_text_is_allowed_after_voice(visible):
                visible = source
            logger.info(
                "TTS 后处理判定使用语音: session=%s reason=%s voice=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 100) or "unknown",
                reason or "use_voice",
                _single_line(voice, 80),
            )
            if self._tts_voice_language_for_event(event) == "zh":
                return f"<tts>{voice}</tts>\n{visible}" if visible and visible != voice else f"<tts>{voice}</tts>"
            return f"<tts>{voice}</tts>\n{visible}"
        except Exception as exc:
            if bool(getattr(exc, "_private_companion_tts_provider_logged", False)):
                logger.info("TTS 后处理已回退纯文本: %s", _single_line(exc, 120))
            else:
                logger.warning("TTS 后处理判断失败,已保持纯文本: %s", _single_line(exc, 120))
            return ""

    async def _get_tts_conversion_provider(self, event: Any) -> Any:
        provider_id = str(self._tts_setting("tts_conversion_provider_id", "") or "").strip()
        if provider_id:
            getter = getattr(self.context, "get_provider_by_id", None)
            if callable(getter):
                try:
                    provider = getter(provider_id)
                    if provider is not None:
                        return provider
                    fallback_getter = getattr(self, "_model_fallback_provider_id", None)
                    fallback_id = (
                        fallback_getter("tts_conversion_provider_id", provider_id)
                        if callable(fallback_getter)
                        else ""
                    )
                    if fallback_id:
                        return getter(fallback_id)
                except Exception:
                    pass
        get_using = getattr(self.context, "get_using_provider", None)
        if callable(get_using):
            umo = str(getattr(event, "unified_msg_origin", "") or "") if event is not None else ""
            try:
                return get_using(umo=umo) if event is not None else get_using()
            except TypeError:
                try:
                    return get_using(umo) if event is not None else get_using(umo="")
                except Exception:
                    return None
            except Exception:
                return None
        return None

    async def _tts_provider_text_chat(
        self,
        provider: Any,
        prompt: str,
        *,
        system_prompt: str | None = None,
        max_tokens: int = 700,
        task: str = "tts_conversion",
        allow_fallback: bool = True,
    ) -> Any:
        start = time.time()
        prompt_applier = getattr(self, "_apply_task_prompt_override_for_call", None)
        if callable(prompt_applier):
            prompt, system_prompt = prompt_applier(
                task,
                prompt,
                system_prompt,
            )
        stable_system_prompt = str(system_prompt or "").strip()
        usage_prompt = (
            f"{stable_system_prompt}\n\n{str(prompt or '').strip()}".strip()
            if stable_system_prompt
            else str(prompt or "")
        )
        provider_id = ""
        provider_id_getter = getattr(self, "_provider_id_from_instance", None)
        if callable(provider_id_getter):
            try:
                provider_id = provider_id_getter(provider)
            except Exception:
                provider_id = ""
        record_usage = getattr(self, "_record_llm_usage", None)
        fallback_getter = getattr(self, "_model_fallback_provider_id", None)
        fallback_id = (
            fallback_getter("tts_conversion_provider_id", provider_id)
            if callable(fallback_getter)
            else ""
        )
        provider_context = getattr(self, "context", None)
        provider_getter = getattr(provider_context, "get_provider_by_id", None)
        fallback_provider = (
            provider_getter(fallback_id)
            if fallback_id and callable(provider_getter)
            else None
        )
        token_skip_getter = getattr(self, "_model_token_limit_should_skip_primary", None)
        if (
            allow_fallback
            and fallback_provider is not None
            and callable(token_skip_getter)
            and token_skip_getter(
                task=task,
                provider_id=provider_id,
                primary_provider_id=provider_id,
                fallback_provider_id=fallback_id,
                provider_key="tts_conversion_provider_id",
                prompt=usage_prompt,
                max_tokens=max_tokens,
            )
        ):
            if callable(record_usage):
                record_usage(
                    provider_id=provider_id,
                    task=task,
                    prompt=usage_prompt,
                    completion="",
                    elapsed_ms=0,
                    success=False,
                    error="model_token_limit_exceeded",
                )
            logger.info(
                "TTS主模型预估超出 Token 上限，跳过并切换备用模型: primary=%s fallback=%s",
                _single_line(provider_id, 80) or "default",
                _single_line(fallback_id, 80),
            )
            return await self._tts_provider_text_chat(
                fallback_provider,
                prompt,
                system_prompt=stable_system_prompt or None,
                max_tokens=max_tokens,
                task=task,
                allow_fallback=False,
            )
        try:
            timeout_getter = getattr(self, "_model_timeout_seconds_for_call", None)
            timeout = (
                timeout_getter(
                    task=task,
                    provider_id=provider_id,
                    timeout_key="tts_conversion_provider_id",
                )
                if callable(timeout_getter)
                else None
            )
            async def request_text_chat():
                request_kwargs: dict[str, Any] = {"prompt": prompt}
                if stable_system_prompt:
                    request_kwargs["system_prompt"] = stable_system_prompt
                if max_tokens and max_tokens > 0:
                    request_kwargs["max_tokens"] = max_tokens
                try:
                    return await provider.text_chat(**request_kwargs)
                except TypeError:
                    request_kwargs.pop("max_tokens", None)
                    try:
                        return await provider.text_chat(**request_kwargs)
                    except TypeError:
                        if not stable_system_prompt:
                            raise
                        legacy_prompt = f"{stable_system_prompt}\n\n{str(prompt or '').strip()}".strip()
                        try:
                            return await provider.text_chat(prompt=legacy_prompt, max_tokens=max_tokens)
                        except TypeError:
                            return await provider.text_chat(prompt=legacy_prompt)

            try:
                request_call = request_text_chat()
                resp = await asyncio.wait_for(request_call, timeout=timeout) if timeout is not None else await request_call
            except asyncio.TimeoutError as exc:
                raise TimeoutError(f"TTS 文本模型超过 {timeout:.0f} 秒未返回") from exc
            elapsed_ms = int((time.time() - start) * 1000)
            completion = str(getattr(resp, "completion_text", resp) or "")
            logger.info(
                "TTS文本模型完成: task=%s provider=%s elapsed=%sms prompt_chars=%s completion_chars=%s",
                task,
                _single_line(provider_id, 80) or "default",
                elapsed_ms,
                len(usage_prompt),
                len(completion),
            )
            safety_refusal = bool(
                task == "tts_spoken_conversion"
                and completion.strip()
                and self._tts_text_is_provider_safety_refusal(completion)
            )
            if callable(record_usage):
                record_usage(
                    provider_id=provider_id,
                    task=task,
                    prompt=usage_prompt,
                    completion=completion,
                    elapsed_ms=elapsed_ms,
                    success=bool(completion.strip()) and not safety_refusal,
                    error="provider_safety_refusal" if safety_refusal else "",
                    resp=resp,
                )
            if safety_refusal:
                logger.warning(
                    "TTS语种转换模型返回安全拒绝话术,不作为朗读文本: provider=%s preview=%s",
                    _single_line(provider_id, 80) or "default",
                    _single_line(completion, 160),
                )
            if (not completion.strip() or safety_refusal) and allow_fallback:
                if fallback_provider is not None:
                    logger.warning(
                        "TTS文本主模型%s,尝试卡片备用模型: primary=%s fallback=%s",
                        "返回安全拒绝话术" if safety_refusal else "返回空结果",
                        _single_line(provider_id, 80) or "default",
                        _single_line(fallback_id, 80),
                    )
                    return await self._tts_provider_text_chat(
                        fallback_provider,
                        prompt,
                        system_prompt=stable_system_prompt or None,
                        max_tokens=max_tokens,
                        task=task,
                        allow_fallback=False,
                    )
            return resp
        except Exception as exc:
            elapsed_ms = int((time.time() - start) * 1000)
            logger.warning(
                "TTS文本模型失败: task=%s provider=%s elapsed=%sms prompt_chars=%s error=%s",
                task,
                _single_line(provider_id, 80) or "default",
                elapsed_ms,
                len(usage_prompt),
                _single_line(exc, 120),
            )
            try:
                setattr(exc, "_private_companion_tts_provider_logged", True)
            except Exception:
                pass
            if callable(record_usage):
                record_usage(
                    provider_id=provider_id,
                    task=task,
                    prompt=usage_prompt,
                    completion="",
                    elapsed_ms=elapsed_ms,
                    success=False,
                    error=str(exc),
                )
            if allow_fallback:
                if fallback_provider is not None:
                    logger.warning(
                        "TTS文本主模型失败,尝试卡片备用模型: primary=%s fallback=%s",
                        _single_line(provider_id, 80) or "default",
                        _single_line(fallback_id, 80),
                    )
                    return await self._tts_provider_text_chat(
                        fallback_provider,
                        prompt,
                        system_prompt=stable_system_prompt or None,
                        max_tokens=max_tokens,
                        task=task,
                        allow_fallback=False,
                    )
            raise

    def _open_tts_audio_file_local(
        self,
        audio_path: str,
        *,
        volume: int | None = None,
        fade_in_ms: int = 0,
    ) -> None:
        path = str(audio_path or "").strip()
        if not path:
            return
        volume = max(
            0,
            min(
                100,
                _safe_int(
                    self._tts_setting("tts_local_playback_volume", 35) if volume is None else volume,
                    35,
                ),
            ),
        )
        fade_in_ms = max(0, min(5000, _safe_int(fade_in_ms, 0)))
        if sys.platform.startswith("win"):
            self._play_tts_audio_file_windows_silent(path, volume=volume, fade_in_ms=fade_in_ms)
            return
        if sys.platform == "darwin":
            subprocess.run(["afplay", "-v", str(volume / 100.0), path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            return
        subprocess.run(["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", "-volume", str(volume), path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
