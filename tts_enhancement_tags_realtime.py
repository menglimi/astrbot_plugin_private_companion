# -*- coding: utf-8 -*-
"""TtsEnhancementTagsRealtimeMixin。

由 tools/split_mixin_domain.py 从 tts_enhancement.py 机械抽取（10 个方法 + 0 个模块级名字 + 0 个类级赋值 / 673 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 TtsEnhancementMixin）。
"""
from __future__ import annotations

import asyncio
import copy
import inspect
import re
from .helpers import _single_line
from .tts_enhancement_shared import build_tts_spoken_conversion_prompts, logger
from astrbot.core import file_token_service
from astrbot.core.utils.astrbot_path import get_astrbot_data_path
from pathlib import Path
from typing import Any
from .tts_enhancement_shared import Plain
from .tts_enhancement_shared import Record



class TtsEnhancementTagsRealtimeMixin:
    """TtsEnhancementTagsRealtimeMixin（从 TtsEnhancementMixin 拆出）。"""


    async def _process_tts_tags(self, text: str, event_or_provider: Any, provider_settings: dict[str, Any] | None = None, config: dict[str, Any] | None = None, fallback_plain: str = "") -> list[Any]:
        if hasattr(event_or_provider, "get_result"):
            event = event_or_provider
            try:
                config = self.context.get_config(str(getattr(event, "unified_msg_origin", "") or "")) or {}
            except Exception:
                config = getattr(self, "config", {}) or {}
            provider_settings = dict((config or {}).get("provider_tts_settings", {}) or {})
            try:
                tts_provider = self.context.get_using_tts_provider(str(getattr(event, "unified_msg_origin", "") or ""))
            except Exception:
                tts_provider = None
        else:
            event = None
            tts_provider = event_or_provider
            config = config or getattr(self, "config", {}) or {}
            provider_settings = provider_settings or dict((config or {}).get("provider_tts_settings", {}) or {})
        tts_provider = self._resolve_tts_synthesis_provider(event, tts_provider)
        normalized = self._normalize_tts_tags(text)
        hard_block = self._tts_hard_block_reason(event)
        if hard_block:
            fallback_text = self._tts_visible_fallback_text(
                normalized,
                fallback_plain,
                event=event,
            ) or self._tts_plain_markup_fallback_text(normalized)
            logger.info(
                "TTS强约束已阻止语音生成: session=%s reason=%s text=%s",
                _single_line(self._tts_session_key(event), 80) or "unknown",
                hard_block,
                _single_line(fallback_text or normalized, 120),
            )
            fallback_text = self._sanitize_tts_visible_text(fallback_text)
            return [Plain(fallback_text)] if fallback_text else []
        if not tts_provider:
            fallback_text = self._tts_visible_fallback_text(
                text,
                fallback_plain,
                event=event,
            )
            fallback_text = self._sanitize_tts_visible_text(fallback_text)
            if not fallback_text:
                fallback_text = "我这边暂时没有可用的语音通道，先用文字陪你说。"
            if fallback_text:
                logger.warning(
                    "TTS强化检测到标签但当前没有可用合成后端,已隐藏朗读文本并按普通文本发送: backend=%s text=%s",
                    _single_line(self._tts_setting("tts_synthesis_backend", "astrbot_provider"), 40),
                    _single_line(fallback_text, 160),
                )
                return [Plain(fallback_text)]
        voice_language = self._tts_voice_language_for_event(event)
        provider_kind = self._tts_provider_kind(
            tts_provider,
            provider_settings,
            voice_language=voice_language,
        )
        output: list[Any] = []
        successful_spoken: list[str] = []
        record_failed = False
        deferred_delivery = bool(
            getattr(
                event,
                "_private_companion_deferred_reaction_tts_active",
                False,
            )
        )
        pos = 0
        matches = list(re.finditer(r"<tts>(.*?)</tts>", normalized, flags=re.IGNORECASE | re.DOTALL))
        for index, match in enumerate(matches):
            before = normalized[pos:match.start()]
            if before.strip():
                output.append(Plain(before.strip()))
            spoken = self._normalize_tts_spoken_text(match.group(1), provider_kind=provider_kind)
            if not spoken:
                pos = match.end()
                continue
            source_spoken = spoken
            complete_chinese_before_voice = (
                index == 0
                and self._tts_visible_text_is_complete_before_voice(
                    normalized[:match.start()],
                    source_spoken,
                )
            )
            spoken = self._sanitize_tts_spoken_text(spoken, provider_kind=provider_kind)
            if not spoken:
                if self._tts_text_is_provider_safety_refusal(source_spoken):
                    record_failed = True
                    logger.warning(
                        "TTS转换结果命中提供商安全回执,已跳过合成并保留原回复: session=%s preview=%s",
                        _single_line(self._tts_session_key(event), 80) or "unknown",
                        _single_line(source_spoken, 140),
                    )
                pos = match.end()
                continue
            remaining = self._tts_session_interval_remaining(event)
            if remaining > 0:
                logger.info(
                    "TTS会话级节流生效,已隐藏朗读文本并保留可见文本: session=%s remain=%.1fs text=%s",
                    _single_line(self._tts_session_key(event), 80) or "unknown",
                    remaining,
                    _single_line(spoken, 80),
                )
                pos = match.end()
                continue
            if self._tts_text_needs_language_conversion(
                spoken,
                provider_kind=provider_kind,
                event=event,
            ):
                before_convert = spoken
                spoken = await self._convert_text_to_spoken_language(spoken, event, provider_kind=provider_kind)
                if spoken != before_convert:
                    logger.info(
                        "TTS语音块已按目标语种修正: '%s' -> '%s'",
                        _single_line(before_convert, 80),
                        _single_line(spoken, 80),
                    )
            record = await self._tts_record_component(
                spoken,
                tts_provider,
                provider_settings,
                config or {},
                source_text=fallback_plain or source_spoken,
                source=self._tts_audio_source_for_event(event),
                voice_language=voice_language,
                retry_transient=deferred_delivery,
                defer_delivery_effects=deferred_delivery,
            )
            if record is not None:
                output.append(record)
                successful_spoken.append(spoken)
                if not deferred_delivery:
                    self._mark_tts_session_sent(event)
                if (
                    voice_language != "zh"
                    and self._tts_setting("tts_delivery_mode", "voice_and_text") != "voice_only"
                    and self._tts_setting("tts_foreign_text_mode", "translation") in {"translation", "bilingual"}
                ):
                    next_start = matches[index + 1].start() if index + 1 < len(matches) else len(normalized)
                    visible_after_this_block = normalized[match.end():next_start]
                    if (
                        not self._tts_visible_text_is_allowed_after_voice(visible_after_this_block)
                        and not complete_chinese_before_voice
                    ):
                        visible_translation = (
                            _single_line(fallback_plain, 300)
                            if fallback_plain and self._tts_visible_text_is_allowed_after_voice(fallback_plain)
                            else await self._translate_tts_spoken_to_chinese(source_spoken, event, provider_kind=provider_kind)
                        )
                        if visible_translation:
                            visible_plain = self._mark_tts_visible_plain(visible_translation, max_chars=300)
                            if visible_plain is not None:
                                output.append(visible_plain)
                            logger.info(
                                "TTS语音块已补中文释义: 语音=%s 中文=%s",
                                _single_line(spoken, 80),
                                _single_line(visible_translation, 80),
                            )
                        else:
                            logger.warning(
                                "TTS语音块缺少中文释义且自动补充失败: 语音=%s",
                                _single_line(spoken, 100),
                            )
            else:
                record_failed = True
                if fallback_plain:
                    logger.warning(
                        "TTS语音组件生成失败,已隐藏朗读文本并保留可见中文: %s",
                        _single_line(spoken, 120),
                    )
                elif voice_language != "zh":
                    next_start = matches[index + 1].start() if index + 1 < len(matches) else len(normalized)
                    visible_after_this_block = normalized[match.end():next_start]
                    if self._tts_visible_text_is_allowed_after_voice(visible_after_this_block):
                        logger.warning(
                            "TTS语音组件生成失败,已隐藏朗读文本并保留后置中文: %s",
                            _single_line(spoken, 120),
                        )
                    elif not complete_chinese_before_voice:
                        visible_translation = await self._translate_tts_spoken_to_chinese(
                            source_spoken,
                            event,
                            provider_kind=provider_kind,
                        )
                        if visible_translation:
                            visible_plain = self._mark_tts_visible_plain(visible_translation, max_chars=300)
                            if visible_plain is not None:
                                output.append(visible_plain)
                            logger.warning(
                                "TTS语音组件生成失败,已改用中文释义文本: %s",
                                _single_line(visible_translation, 120),
                            )
                        else:
                            logger.warning(
                                "TTS语音组件生成失败且无法得到中文释义,已隐藏朗读文本: %s",
                                _single_line(spoken, 120),
                            )
                else:
                    output.append(Plain(spoken))
            pos = match.end()
        after = re.sub(r"</?t{2,}s\b[^>]*>", "", normalized[pos:], flags=re.IGNORECASE).strip()
        visible_override = self._sanitize_tts_visible_text(
            getattr(event, "_private_companion_tts_visible_text_override", ""),
            max_chars=1000,
        ) if event is not None else ""
        suppress_visible = bool(getattr(event, "_private_companion_tts_visible_text_suppress", False)) if event is not None else False
        if suppress_visible:
            after = ""
        elif visible_override:
            after = visible_override
        if after and voice_language != "zh" and not self._tts_visible_text_is_allowed_after_voice(after):
            chinese_after = self._tts_chinese_visible_fallback_from_mixed(after)
            if chinese_after:
                logger.warning(
                    "TTS语音块后置可见文本混有朗读语种,已仅保留中文释义: text=%s",
                    _single_line(chinese_after, 120),
                )
                after = chinese_after
            else:
                logger.warning(
                    "TTS语音块后置可见文本不是中文释义,已丢弃: text=%s",
                    _single_line(after, 120),
                )
                after = ""
        if after:
            visible_plain = self._mark_tts_visible_plain(after)
            if visible_plain is not None:
                output.append(visible_plain)
        has_record = any(isinstance(comp, Record) for comp in output)
        if record_failed and fallback_plain and not has_record:
            fallback_text = self._sanitize_tts_visible_text(fallback_plain)
            visible_text = "\n".join(
                str(getattr(comp, "text", "") or "").strip()
                for comp in output
                if isinstance(comp, Plain)
            ).strip()
            if fallback_text and fallback_text not in visible_text:
                output.append(Plain(fallback_text))
        plain_after_last_record = False
        for comp in reversed(output):
            if isinstance(comp, Record):
                break
            if isinstance(comp, Plain) and str(getattr(comp, "text", "") or "").strip():
                plain_after_last_record = True
        if (
            fallback_plain
            and voice_language != "zh"
            and has_record
            and not plain_after_last_record
        ):
            visible_plain = self._mark_tts_visible_plain(fallback_plain)
            if visible_plain is not None:
                output.append(visible_plain)
        if not output:
            fallback_text = self._tts_visible_fallback_text(
                normalized,
                fallback_plain,
                event=event,
            ) or self._tts_plain_markup_fallback_text(normalized)
            if fallback_text:
                output.append(Plain(fallback_text))
        return await self._finalize_tts_delivery_chain(
            output,
            event=event,
            provider_kind=provider_kind,
            fallback_plain=fallback_plain,
            successful_spoken=successful_spoken,
            suppress_visible=suppress_visible,
        )

    async def _convert_text_to_spoken_language(self, text: str, event: Any, *, provider_kind: str) -> str:
        provider = await self._get_tts_conversion_provider(event)
        voice_language = self._tts_voice_language_for_event(event)
        lang = self._tts_language_label(voice_language=voice_language)
        persona_context = await self._format_tts_persona_voice_context(event)
        fish_rule = ""
        if provider_kind.startswith("fishaudio"):
            fish_rule = (
                "\n- "
                + self._tts_emotion_tag_rule(
                    provider_kind,
                    subject="最终朗读文本中",
                    voice_language=voice_language,
                )
                + " 这些控制词属于合成指令，不要翻译成口语，也不要另行解释。"
            )
        system_prompt, prompt = build_tts_spoken_conversion_prompts(
            text,
            language_name=lang,
            persona_context=persona_context,
            provider_rule=fish_rule,
        )
        try:
            if provider is not None:
                spoken_max_tokens = max(360, min(3000, len(text) * 2 + 120))
                resp = await self._tts_provider_text_chat(
                    provider,
                    prompt,
                    system_prompt=system_prompt,
                    max_tokens=spoken_max_tokens,
                    task="tts_spoken_conversion",
                )
                converted = str(getattr(resp, "completion_text", resp) or "").strip()
                normalized = self._normalize_tts_spoken_text(converted, provider_kind=provider_kind)
                if normalized and self._tts_text_is_provider_safety_refusal(normalized):
                    logger.warning(
                        "TTS语种转换最终结果仍为安全拒绝话术,已回退原回复: session=%s preview=%s",
                        _single_line(self._tts_session_key(event), 80) or "unknown",
                        _single_line(normalized, 160),
                    )
                    return text
                return normalized or text
        except Exception:
            pass
        return text

    @staticmethod
    def _tts_browser_language(voice_language: str) -> str:
        return {"ja": "ja-JP", "en": "en-US", "zh": "zh-CN"}.get(voice_language, "zh-CN")

    def _realtime_voice_config(self) -> dict[str, Any]:
        voice_language = self._normalize_tts_voice_language_value(
            self._tts_setting("tts_voice_language", "zh")
        ) or "zh"
        return {
            "available": True,
            "voice_language": voice_language,
            "browser_language": self._tts_browser_language(voice_language),
        }

    async def _synthesize_realtime_voice(
        self,
        text: str,
        *,
        tts_provider: Any = None,
        provider_settings: dict[str, Any] | None = None,
        source: str = "external_realtime",
        play_local: bool = True,
    ) -> dict[str, Any]:
        settings = dict(provider_settings or {})
        tts_provider = self._resolve_tts_synthesis_provider(None, tts_provider)
        voice_config = self._realtime_voice_config()
        voice_language = str(voice_config["voice_language"])
        target_language = str(voice_config["browser_language"])
        source_text = str(text or "").strip()
        provider_kind = self._tts_provider_kind(tts_provider, settings)
        spoken = self._normalize_tts_spoken_text(source_text, provider_kind=provider_kind)
        result = {
            **voice_config,
            "audio_path": "",
            "spoken_text": spoken,
            "fallback_text": source_text,
            "language": target_language,
            "reason": "",
        }
        if not spoken:
            result["reason"] = "empty_text"
            return result
        if self._tts_text_needs_language_conversion(spoken, provider_kind=provider_kind):
            converted = ""
            for attempt in range(2):
                converted = await self._convert_text_to_spoken_language(
                    spoken,
                    None,
                    provider_kind=provider_kind,
                )
                converted = self._normalize_tts_spoken_text(converted, provider_kind=provider_kind)
                if converted and not self._tts_text_needs_language_conversion(
                    converted,
                    provider_kind=provider_kind,
                ):
                    break
                if attempt == 0:
                    logger.info(
                        "外部实时 TTS 语种转换结果不合格,正在重试: target=%s",
                        self._tts_language_label(),
                    )
            if not converted or self._tts_text_needs_language_conversion(
                converted,
                provider_kind=provider_kind,
            ):
                result["fallback_text"] = ""
                result["reason"] = "language_conversion_failed"
                logger.warning(
                    "外部实时 TTS 语种转换失败,已阻止原文送入%s声线或浏览器朗读: text=%s",
                    self._tts_language_label(),
                    _single_line(source_text, 120),
                )
                return result
            spoken = converted

        if provider_kind.startswith("fishaudio"):
            tts_provider, _ = self._fishaudio_request_provider(
                tts_provider,
                settings,
                voice_language=voice_language,
            )
        sanitized = self._sanitize_tts_spoken_text(spoken, provider_kind=provider_kind)
        if provider_kind.startswith("fishaudio"):
            sanitized, _ = self._apply_fishaudio_emotion_control(
                sanitized,
                provider_kind=provider_kind,
                source_text=source_text,
            )
        if not sanitized:
            result["reason"] = "empty_spoken_text"
            return result
        result["spoken_text"] = sanitized
        result["fallback_text"] = sanitized

        if tts_provider is None:
            result["available"] = False
            result["reason"] = "tts_provider_unavailable"
            return result

        try:
            audio_path = await self._tts_generate_audio_path(tts_provider, sanitized)
        except Exception as exc:
            logger.warning(
                "外部实时 TTS 合成失败: provider=%s error=%s",
                provider_kind,
                _single_line(exc, 120),
            )
            result["reason"] = "synthesis_failed"
            return result
        if not audio_path:
            result["reason"] = "empty_audio_path"
            return result
        try:
            audio_file = Path(audio_path).resolve()
            expected_dir = Path(get_astrbot_data_path()).resolve()
            if not audio_file.is_file() or not audio_file.is_relative_to(expected_dir):
                result["reason"] = "invalid_audio_path"
                return result
        except Exception:
            result["reason"] = "invalid_audio_path"
            return result

        result["audio_path"] = str(audio_file)
        self._create_tts_background_task(
            self._after_tts_audio_generated(
                str(audio_file),
                sanitized,
                source=source or "external_realtime",
                allow_local_playback=play_local,
            ),
            label="tts_audio_postprocess",
        )
        return result

    def _prepare_fishaudio_provider_model(
        self,
        tts_provider: Any,
        provider_settings: dict[str, Any] | None = None,
        *,
        voice_language: str = "",
    ) -> str:
        """Resolve Fish Audio compatibility without mutating AstrBot's provider."""
        return self._tts_fishaudio_model_for_provider(
            tts_provider,
            provider_settings,
            voice_language=voice_language,
        )

    def _fishaudio_request_provider(
        self,
        tts_provider: Any,
        provider_settings: dict[str, Any] | None = None,
        *,
        voice_language: str = "",
    ) -> tuple[Any, str]:
        model = self._prepare_fishaudio_provider_model(
            tts_provider,
            provider_settings,
            voice_language=voice_language,
        )
        if not model:
            return tts_provider, ""

        try:
            request_provider = copy.copy(tts_provider)
            if request_provider is tts_provider:
                raise TypeError("provider copy returned the shared instance")

            for attr in ("provider_config", "provider_settings"):
                value = getattr(tts_provider, attr, None)
                if isinstance(value, dict):
                    setattr(request_provider, attr, dict(value))

            headers = getattr(tts_provider, "headers", None)
            if isinstance(headers, dict):
                isolated_headers = dict(headers)
                isolated_headers["model"] = model
                setattr(request_provider, "headers", isolated_headers)
        except Exception as exc:
            warning_key = f"{tts_provider.__class__.__name__}:{id(tts_provider)}:{model}"
            if getattr(self, "_tts_fishaudio_provider_copy_warning_key", "") != warning_key:
                self._tts_fishaudio_provider_copy_warning_key = warning_key
                logger.warning(
                    "FishAudio Provider 无法隔离本次模型参数，已保留 AstrBot 原配置: "
                    "provider=%s model=%s error_type=%s",
                    tts_provider.__class__.__name__,
                    model,
                    exc.__class__.__name__,
                )
            return tts_provider, ""

        set_model = getattr(request_provider, "set_model", None)
        if callable(set_model):
            try:
                set_model(model)
            except Exception:
                pass
        log_key = f"{tts_provider.__class__.__name__}:{id(tts_provider)}:{model}"
        if getattr(self, "_tts_fishaudio_request_model_log_key", "") != log_key:
            self._tts_fishaudio_request_model_log_key = log_key
            logger.info(
                "FishAudio TTS 已在隔离请求副本应用模型参数: model=%s",
                model,
            )
        return request_provider, model

    async def _tts_generate_audio_path(self, tts_provider: Any, text: str) -> str:
        if hasattr(tts_provider, "get_audio"):
            result = tts_provider.get_audio(text)
        elif hasattr(tts_provider, "synthesize_text"):
            result = tts_provider.synthesize_text(text)
        else:
            return ""
        if inspect.isawaitable(result):
            result = await result
        if isinstance(result, (list, tuple)):
            result = result[0] if result else ""
        return str(result or "")

    @staticmethod
    def _tts_transient_synthesis_error(exc: BaseException) -> bool:
        transient_names = {
            "ConnectError",
            "ConnectTimeout",
            "ReadTimeout",
            "WriteTimeout",
            "PoolTimeout",
        }
        current: BaseException | None = exc
        seen: set[int] = set()
        for _ in range(8):
            if current is None or id(current) in seen:
                break
            seen.add(id(current))
            if isinstance(current, (ConnectionError, TimeoutError)):
                return True
            if current.__class__.__name__ in transient_names:
                return True
            current = current.__cause__ or current.__context__
        return False

    async def _tts_record_component(
        self,
        spoken: str,
        tts_provider: Any,
        provider_settings: dict[str, Any],
        config: dict[str, Any],
        *,
        source_text: str = "",
        source: str = "private_companion",
        voice_language: str = "",
        retry_transient: bool = False,
        defer_delivery_effects: bool = False,
    ) -> Any | None:
        provider_kind = self._tts_provider_kind(
            tts_provider,
            provider_settings,
            voice_language=voice_language,
        )
        fish_model = ""
        if provider_kind.startswith("fishaudio"):
            tts_provider, fish_model = self._fishaudio_request_provider(
                tts_provider,
                provider_settings,
                voice_language=voice_language,
            )
        sanitized = self._sanitize_tts_spoken_text(spoken, provider_kind=provider_kind)
        if not sanitized:
            return None
        if sanitized != spoken:
            logger.info(
                "TTS强化朗读文本已清洗: '%s' -> '%s'",
                _single_line(spoken, 80),
                _single_line(sanitized, 80),
            )
        if provider_kind.startswith("fishaudio"):
            sanitized, applied_cues = self._apply_fishaudio_emotion_control(
                sanitized,
                provider_kind=provider_kind,
                source_text=source_text,
            )
            if applied_cues:
                s1 = provider_kind == "fishaudio_s1"
                rendered_cues = "".join(
                    f"({cue})" if s1 else f"[{cue}]"
                    for cue in applied_cues
                )
                logger.info(
                    "FishAudio 专用情绪控制已应用: model=%s mode=%s cues=%s",
                    fish_model or ("s1" if s1 else "s2-compatible"),
                    self._fishaudio_emotion_mode(),
                    rendered_cues,
                )
        audio_path = ""
        attempts = 2 if retry_transient else 1
        for attempt in range(attempts):
            try:
                audio_path = await self._tts_generate_audio_path(
                    tts_provider,
                    sanitized,
                )
                break
            except Exception as exc:
                can_retry = (
                    attempt == 0
                    and attempts > 1
                    and self._tts_transient_synthesis_error(exc)
                )
                if can_retry:
                    logger.info(
                        "后台 TTS 瞬时连接失败,准备重试一次: provider=%s error_type=%s",
                        provider_kind or "unknown",
                        exc.__class__.__name__,
                    )
                    await asyncio.sleep(0.2)
                    continue
                logger.warning(
                    "TTS强化生成语音失败: provider=%s error_type=%s error=%s text=%s",
                    provider_kind or "unknown",
                    exc.__class__.__name__,
                    _single_line(repr(exc), 160),
                    _single_line(sanitized, 120),
                    exc_info=True,
                )
                return None
        if not audio_path:
            return None
        try:
            audio_file = Path(audio_path).resolve()
            expected_dir = Path(get_astrbot_data_path()).resolve()
            if not audio_file.is_relative_to(expected_dir):
                logger.warning("TTS强化拒绝不安全语音路径: %s", _single_line(audio_path, 160))
                return None
        except Exception as exc:
            logger.warning("TTS强化检查语音路径失败: %s", _single_line(exc, 120))
            return None
        final_ref = str(audio_path)
        if not defer_delivery_effects:
            self._create_tts_background_task(
                self._after_tts_audio_generated(
                    str(audio_path),
                    sanitized,
                    source=source or "private_companion",
                ),
                label="tts_audio_postprocess",
            )
        if provider_settings.get("use_file_service", False):
            callback_api_base = str((config or {}).get("callback_api_base", "") or "").strip()
            if callback_api_base:
                try:
                    token = await file_token_service.register_file(str(audio_path))
                    final_ref = f"{callback_api_base}/api/file/{token}"
                except Exception as exc:
                    logger.warning("TTS强化注册语音文件失败: %s", _single_line(exc, 120))
        record_text = source_text or sanitized
        try:
            component = Record(file=final_ref, url=final_ref, text=record_text)
        except TypeError:
            try:
                component = Record(file=final_ref, text=record_text)
            except TypeError:
                component = Record.fromFileSystem(str(audio_path), text=record_text)
        self._annotate_tts_record_component(component, sanitized, source_text=source_text or spoken)
        logger.info("TTS语音组件已生成: %s", self._tts_component_log_note(component))
        return component
