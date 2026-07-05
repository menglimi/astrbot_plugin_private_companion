# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import json
import os
import random
import re
import struct
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from astrbot.api import logger
try:
    from astrbot.api.message_components import Plain, Record
except ImportError:
    from astrbot.api.message_components import Plain
    from astrbot.core.message.components import Record
from astrbot.core import file_token_service
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .helpers import _normalize_outbound_punctuation_flow, _safe_int, _single_line


TTS_BLOCK_PATTERN = re.compile(r"<t{2,}s\b[^>]*>.*?</t{2,}s>", re.IGNORECASE | re.DOTALL)
TTS_TAG_PATTERN = re.compile(r"</?t{2,}s\b[^>]*>", re.IGNORECASE)
TTS_BLOCK_TOKEN_PATTERN = re.compile(r"\[\[TTSBLOCK:([0-9a-f]{16})\]\]")
PRIVATE_TTS_BLOCK_TOKEN_PATTERN = re.compile(r"\[\[PCTTS:([0-9a-f]{16})\]\]")
EMOTION_TAG_PATTERN = re.compile(r"\[([^\[\]\n]{1,24})\]")
DEFAULT_AUTO_VOICE_PROMPT_MARKERS = (
    "随机日语语音模式",
    "日语语音",
    "原中文文本",
    "自动日语语音",
)
DEFAULT_TTS_SANITIZE_REMOVE_PATTERNS = (
    r"[（(][^（()]*[）)]",
    r"[＞>][＿_][＜<]",
    r"[＾^][＿_][＾^]",
    r"[oO][＿_][oO]",
    r"[xX][＿_][xX]",
    r"[－-][＿_][－-]",
    r"[★☆♪♫♬♩♡♥❤️💖💕💗💓💝💟💜💛💚💙🧡🤍🖤🤎💔❣️💋]",
    r"[→←↑↓↖↗↘↙↔↕↺↻]",
)
DEFAULT_TTS_SANITIZE_FILTER_WORDS = (
    "ω", "Ω", "σ", "Σ", "ε", "д", "Д",
    "´", "`", "＝", "∀", "∇",
    "orz", "OTZ", "QAQ", "QWQ", "TAT", "TUT", "www",
)
DEFAULT_TTS_SANITIZE_REPLACEMENTS = {
    "233": "哈哈哈",
    "666": "厉害",
    "999": "很棒",
    "555": "呜呜呜",
}
TTS_EMOTION_PLACEHOLDER_PREFIX = "__PRIVATE_COMPANION_TTS_EMOTION_"
TTS_VISIBLE_LABEL_PATTERN = re.compile(
    r"^(?:[\s:：|｜-]*(?:中文含义|中文释义|对应文本|原中文文本|显示文本|可见文本|文本|翻译|释义)[\s:：|｜-]*)+"
)


class TtsEnhancementMixin:
    """Integrated TTS enhancement for private_companion.

    This is intentionally not a verbatim copy of tts_modify. It keeps the useful
    behavior surface but maps identity and prompts to private_companion concepts.
    """

    def _load_tts_enhancement_config(self, config: Any) -> None:
        self.enable_tts_enhancement = self._cfg_bool(config, "enable_tts_enhancement", False)
        raw_mode = self._cfg_str(config, "tts_generation_mode", "fast_tag", "fast_tag").lower()
        mode_aliases = {
            "hybrid": "fast_tag",
            "direct": "fast_tag",
            "tag": "fast_tag",
            "tags": "fast_tag",
            "fast": "fast_tag",
            "convert": "postprocess",
            "post": "postprocess",
            "llm": "postprocess",
        }
        self.tts_generation_mode = mode_aliases.get(raw_mode, raw_mode)
        if self.tts_generation_mode not in {"fast_tag", "postprocess"}:
            self.tts_generation_mode = "fast_tag"
        self.tts_legacy_generation_mode = raw_mode
        self.tts_voice_language = self._cfg_str(config, "tts_voice_language", "ja", "ja").lower()
        if self.tts_voice_language not in {"ja", "zh", "en"}:
            self.tts_voice_language = "ja"
        self.tts_conversion_provider_id = self._cfg_str(config, "tts_conversion_provider_id", "")
        self.tts_extra_prompt = self._cfg_str(config, "tts_extra_prompt", "")
        self.tts_frequency_control_mode = self._cfg_str(config, "tts_frequency_control_mode", "global", "global").lower()
        if self.tts_frequency_control_mode not in {"global", "legacy"}:
            self.tts_frequency_control_mode = "global"
        self.tts_constraint_mode = self._cfg_str(config, "tts_constraint_mode", "weak", "weak").lower()
        if self.tts_constraint_mode not in {"weak", "strong"}:
            self.tts_constraint_mode = "weak"
        self.tts_session_min_interval_seconds = self._cfg_float(config, "tts_session_min_interval_seconds", 90.0, 0.0)
        self.tts_private_min_interval_seconds = self._cfg_float(config, "tts_private_min_interval_seconds", -1.0, -1.0)
        self.tts_group_min_interval_seconds = self._cfg_float(config, "tts_group_min_interval_seconds", -1.0, -1.0)
        self.tts_trigger_probability = self._cfg_int(
            config,
            "tts_trigger_probability",
            self._cfg_int(config, "auto_voice_probability", self._cfg_int(config, "auto_japanese_voice_probability", 25, 0, 100), 0, 100),
            0,
            100,
        ) / 100.0
        self.tts_private_trigger_probability = self._cfg_int(config, "tts_private_trigger_probability", -1, -1, 100) / 100.0
        self.tts_group_trigger_probability = self._cfg_int(config, "tts_group_trigger_probability", -1, -1, 100) / 100.0
        self.auto_voice_enabled = self._cfg_bool(config, "auto_voice_enabled", self._cfg_bool(config, "auto_japanese_voice_enabled", False))
        self.auto_voice_full_conversion_enabled = self._cfg_bool(
            config,
            "auto_voice_full_conversion_enabled",
            self._cfg_bool(config, "auto_japanese_voice_full_conversion_enabled", False),
        )
        self.auto_voice_probability = self._cfg_int(
            config,
            "auto_voice_probability",
            int(round(self.tts_trigger_probability * 100)),
            0,
            100,
        ) / 100.0
        self.auto_voice_max_chars = self._cfg_int(
            config,
            "auto_voice_max_chars",
            self._cfg_int(config, "auto_japanese_voice_max_chars", 50, 0),
            0,
        )
        self.auto_voice_cooldown_seconds = self._cfg_int(
            config,
            "auto_voice_cooldown_seconds",
            self._cfg_int(config, "auto_japanese_voice_cooldown_seconds", 120, 0),
            0,
        )
        self.main_user_voice_probability = self._cfg_int(
            config,
            "main_user_voice_probability",
            self._cfg_int(config, "auto_japanese_voice_admin_probability", -1, -1, 100),
            -1,
            100,
        ) / 100.0
        self.main_user_mention_voice_keywords = self._parse_text_list_config(
            config.get("main_user_mention_voice_keywords", config.get("admin_mention_keyword_voice_keywords", "")),
            limit=80,
        )
        self.main_user_mention_voice_probability = self._cfg_int(
            config,
            "main_user_mention_voice_probability",
            self._cfg_int(config, "admin_mention_keyword_voice_probability", 0, 0, 100),
            0,
            100,
        ) / 100.0
        self.main_user_mention_voice_prompt = self._cfg_str(
            config,
            "main_user_mention_voice_prompt",
            self._cfg_str(config, "admin_mention_keyword_voice_prompt", ""),
        )
        self.enable_tts_local_playback = self._cfg_bool(config, "enable_tts_local_playback", False)
        self.enable_tts_local_playback_live_only = self._cfg_bool(config, "enable_tts_local_playback_live_only", False)
        self.enable_tts_live_subtitle_sync = self._cfg_bool(config, "enable_tts_live_subtitle_sync", False)
        self.tts_live_subtitle_url = self._cfg_str(config, "tts_live_subtitle_url", "http://127.0.0.1:18081/show", "http://127.0.0.1:18081/show")
        self.tts_local_playback_volume = self._cfg_int(config, "tts_local_playback_volume", 35, 0, 100)
        self.tts_local_playback_min_interval_seconds = self._cfg_float(config, "tts_local_playback_min_interval_seconds", 0.0, 0.0)
        self._tts_local_playback_last_at = 0.0
        self._tts_auto_voice_last_at: dict[str, float] = {}
        if not isinstance(getattr(self, "_tts_session_last_at", None), dict):
            self._tts_session_last_at: dict[str, float] = {}
        self._apply_tts_runtime_overrides()

    def _tts_provider_kind(self, tts_provider: Any = None, provider_settings: dict[str, Any] | None = None) -> str:
        pieces: list[str] = []
        if tts_provider is not None:
            pieces.extend([
                tts_provider.__class__.__name__,
                str(getattr(tts_provider, "name", "") or ""),
                str(getattr(tts_provider, "provider_type", "") or ""),
            ])
        if provider_settings:
            for key in ("type", "provider", "provider_id", "api_base", "model", "name"):
                pieces.append(str(provider_settings.get(key, "") or ""))
        text = " ".join(pieces).lower()
        if "fish" in text:
            return "fishaudio"
        if "gsv" in text or "gptsovits" in text or "so-vits" in text:
            return "gsv"
        if "openai" in text:
            return "openai"
        if "edge" in text:
            return "edge"
        if "azure" in text:
            return "azure"
        if "gemini" in text:
            return "gemini"
        if "minimax" in text:
            return "minimax"
        if "aliyun" in text or "alibaba" in text or "阿里" in text:
            return "aliyun"
        if "volc" in text or "huoshan" in text or "火山" in text:
            return "volcengine"
        return "generic"

    def _tts_provider_kind_for_event(self, event: Any, *, config: dict[str, Any] | None = None) -> str:
        tts_provider = None
        provider_settings: dict[str, Any] = {}
        try:
            if config is None:
                config = self.context.get_config(str(getattr(event, "unified_msg_origin", "") or "")) or {}
            provider_settings = dict((config or {}).get("provider_tts_settings", {}) or {})
        except Exception:
            provider_settings = {}
        try:
            if event is not None:
                tts_provider = self.context.get_using_tts_provider(str(getattr(event, "unified_msg_origin", "") or ""))
        except Exception:
            tts_provider = None
        return self._tts_provider_kind(tts_provider, provider_settings)

    def _tts_provider_allows_emotion_tags(self, kind: str) -> bool:
        return kind in {"fishaudio", "gsv"}

    def _tts_emotion_tag_examples(self, provider_kind: str = "generic") -> tuple[str, str]:
        if not self._tts_provider_allows_emotion_tags(provider_kind):
            return "", ""
        voice_lang = getattr(self, "tts_voice_language", "ja")
        if voice_lang == "zh":
            return "[开心]", "[难过]"
        if voice_lang == "en":
            return "[happy]", "[sad]"
        return "[嬉しい]", "[悲しい]"

    def _tts_emotion_tag_rule(self, provider_kind: str = "generic", *, subject: str = "语音块内") -> str:
        positive, negative = self._tts_emotion_tag_examples(provider_kind)
        if not positive or not negative:
            return ""
        return f"可以在{subject}插入方括号情绪标签，如 {positive}、{negative}。"

    def _tts_language_label(self) -> str:
        return {"ja": "日语", "zh": "中文", "en": "英语"}.get(getattr(self, "tts_voice_language", "ja"), "日语")

    def _normalize_tts_voice_language_value(self, value: Any) -> str:
        text = str(value or "").strip().lower()
        compact = re.sub(r"[\s_\\/-]+", "", text)
        aliases = {
            "ja": "ja",
            "jp": "ja",
            "japanese": "ja",
            "日语": "ja",
            "日文": "ja",
            "日本语": "ja",
            "日本語": "ja",
            "zh": "zh",
            "cn": "zh",
            "chinese": "zh",
            "中文": "zh",
            "汉语": "zh",
            "汉文": "zh",
            "普通话": "zh",
            "国语": "zh",
            "en": "en",
            "eng": "en",
            "english": "en",
            "英语": "en",
            "英文": "en",
        }
        return aliases.get(compact, "")

    def _apply_tts_runtime_overrides(self) -> None:
        settings = self.data.get("runtime_settings") if isinstance(getattr(self, "data", None), dict) else None
        if not isinstance(settings, dict):
            return
        lang = self._normalize_tts_voice_language_value(settings.get("tts_voice_language"))
        if not lang:
            lang = self._normalize_tts_voice_language_value(getattr(self, "tts_voice_language", "ja"))
        if lang:
            self.tts_voice_language = lang

    def _format_tts_voice_language_status(self) -> str:
        settings = self.data.get("runtime_settings") if isinstance(getattr(self, "data", None), dict) else None
        override = ""
        if isinstance(settings, dict):
            override = self._normalize_tts_voice_language_value(settings.get("tts_voice_language"))
        source = "指令覆盖" if override else "配置页"
        return f"当前 TTS 语音语种：{self._tts_language_label()}（来源：{source}）。可用：日语 / 中文 / 英语；发送“陪伴 TTS语种 默认”可恢复配置页设置。"

    def _set_tts_voice_language_from_command(self, value: str) -> str:
        text = str(value or "").strip()
        if not text or text in {"查看", "状态", "当前"}:
            return self._format_tts_voice_language_status()
        settings = self.data.setdefault("runtime_settings", {})
        if not isinstance(settings, dict):
            settings = {}
            self.data["runtime_settings"] = settings
        if text.lower() in {"default", "config", "reset", "clear"} or text in {"默认", "配置", "配置页", "重置", "清除", "跟随配置"}:
            settings.pop("tts_voice_language", None)
            configured = self._normalize_tts_voice_language_value(getattr(self, "config", {}).get("tts_voice_language", "ja") if getattr(self, "config", None) is not None else "ja")
            self.tts_voice_language = configured or "ja"
            self._save_data_sync()
            return f"已恢复 TTS 语音语种为配置页设置：{self._tts_language_label()}。"
        lang = self._normalize_tts_voice_language_value(text)
        if not lang:
            return "没认出这个 TTS 语种。可用：日语 / 中文 / 英语；例如：陪伴 TTS语种 日语。"
        self.tts_voice_language = lang
        settings["tts_voice_language"] = lang
        self._save_data_sync()
        return f"已切换 TTS 语音语种：{self._tts_language_label()}。之后 <tts> 和自动语音转换会按这个语种处理。"

    def _normalize_tts_tags(self, text: str) -> str:
        source = str(text or "")
        source = re.sub(r"<(/?)pc[_-]?tts\b[^>]*>", lambda m: f"</tts>" if m.group(1) else "<tts>", source, flags=re.IGNORECASE)
        source = re.sub(r"<(/?)t{2,}s\b[^>]*>", lambda m: f"</tts>" if m.group(1) else "<tts>", source, flags=re.IGNORECASE)
        source = re.sub(r"</tts>\s*</tts>+", "</tts>", source, flags=re.IGNORECASE)
        pieces: list[str] = []
        open_count = 0
        pos = 0
        for match in re.finditer(r"</?tts>", source, flags=re.IGNORECASE):
            pieces.append(source[pos:match.start()])
            tag = match.group(0).lower()
            if tag == "<tts>":
                open_count += 1
                pieces.append("<tts>")
            elif open_count > 0:
                open_count -= 1
                pieces.append("</tts>")
            pos = match.end()
        pieces.append(source[pos:])
        if open_count > 0:
            pieces.append("</tts>" * open_count)
        return "".join(pieces)

    def _strip_any_tts_markup(self, text: str) -> str:
        cleaned = re.sub(r"</?pc[_-]?tts\b[^>]*>", "", str(text or ""), flags=re.IGNORECASE)
        cleaned = re.sub(r"</?t{2,}s\b[^>]*>", "", cleaned, flags=re.IGNORECASE)
        return cleaned.strip()

    def _sanitize_tts_visible_text(self, text: Any, *, max_chars: int = 800) -> str:
        cleaned = self._strip_any_tts_markup(str(text or ""))
        cleaned = re.sub(TTS_TAG_PATTERN, "", cleaned).strip()
        cleaned = re.sub(r"(?m)^\s*[>＞]\s*", "", cleaned).strip()
        previous = None
        while cleaned and previous != cleaned:
            previous = cleaned
            cleaned = TTS_VISIBLE_LABEL_PATTERN.sub("", cleaned).strip()
        cleaned = re.sub(
            r"(?m)^(\s*)(?:中文含义|中文释义|对应文本|原中文文本|显示文本|可见文本|文本|翻译|释义)[\s:：|｜-]+",
            r"\1",
            cleaned,
        ).strip()
        return _single_line(_normalize_outbound_punctuation_flow(cleaned), max_chars) if cleaned else ""

    def _mark_tts_visible_plain(self, text: Any, *, max_chars: int = 800) -> Plain | None:
        visible = self._sanitize_tts_visible_text(text, max_chars=max_chars)
        if not visible:
            return None
        comp = Plain(visible)
        try:
            setattr(comp, "_private_companion_tts_visible_text", True)
        except Exception:
            pass
        return comp

    def _tts_proactive_segment_visible_policy(self, event: Any) -> tuple[str, bool]:
        try:
            result = event.get_result()
        except Exception:
            result = None
        chain = list(getattr(result, "chain", []) or []) if result is not None else []
        full_text = ""
        index = 0
        count = 1
        for comp in chain:
            full_text = _single_line(getattr(comp, "_private_companion_proactive_full_text", ""), 1200)
            if not full_text:
                continue
            try:
                index = max(0, int(getattr(comp, "_private_companion_proactive_segment_index", 0) or 0))
            except Exception:
                index = 0
            try:
                count = max(1, int(getattr(comp, "_private_companion_proactive_segment_count", 1) or 1))
            except Exception:
                count = 1
            break
        if not full_text:
            return "", False
        if count <= 1 or index >= count - 1:
            return self._sanitize_tts_visible_text(full_text, max_chars=1000), False
        return "", True

    def _protect_tts_blocks_for_framework(self, text: str, event: Any) -> str:
        normalized = self._normalize_tts_tags(str(text or ""))
        if "<tts>" not in normalized.lower() or "</tts>" not in normalized.lower():
            return normalized
        protected = getattr(event, "_private_companion_tts_block_tokens", None)
        if not isinstance(protected, dict):
            protected = {}
            try:
                setattr(event, "_private_companion_tts_block_tokens", protected)
            except Exception:
                protected = {}

        def repl(match: re.Match[str]) -> str:
            token = uuid.uuid4().hex[:16]
            protected[token] = match.group(0)
            return f"[[PCTTS:{token}]]"

        return re.sub(r"<tts>.*?</tts>", repl, normalized, flags=re.IGNORECASE | re.DOTALL)

    def _restore_protected_tts_blocks(self, text: str, event: Any) -> str:
        source = str(text or "")
        protected = getattr(event, "_private_companion_tts_block_tokens", None)
        if not isinstance(protected, dict) or not protected:
            return source

        def repl(match: re.Match[str]) -> str:
            return str(protected.get(match.group(1)) or "")

        return PRIVATE_TTS_BLOCK_TOKEN_PATTERN.sub(repl, source)

    def _sanitize_orphan_tts_placeholders(self, text: str) -> str:
        """Remove private TTS placeholders that escaped their original event scope."""
        source = str(text or "")
        if not source:
            return ""
        source = PRIVATE_TTS_BLOCK_TOKEN_PATTERN.sub("", source)
        source = TTS_BLOCK_TOKEN_PATTERN.sub("", source)
        source = re.sub(r"(?:^|[\s\r\n])([。！？!?，,、；;：:~～…]+)(?=\s|$)", " ", source)
        source = re.sub(r"\s{2,}", " ", source)
        source = re.sub(r"^\s*[。！？!?，,、；;：:~～…]+\s*", "", source)
        source = source.lstrip(" \t\r\n。！？!?，,、；;：:~～…")
        return source.strip()

    def _tts_record_refs(self, component: Any) -> list[str]:
        refs: list[str] = []
        for attr in ("file", "url", "path"):
            value = str(getattr(component, attr, "") or "").strip()
            if value and value not in refs:
                refs.append(value)
        try:
            data = getattr(component, "data", None)
            if isinstance(data, dict):
                for key in ("file", "url", "path"):
                    value = str(data.get(key) or "").strip()
                    if value and value not in refs:
                        refs.append(value)
        except Exception:
            pass
        return refs

    def _remember_tts_record_text(self, component: Any, spoken: str, source: str) -> None:
        refs = self._tts_record_refs(component)
        if not refs:
            return
        index = getattr(self, "_tts_record_text_index", None)
        if not isinstance(index, dict):
            index = {}
            try:
                setattr(self, "_tts_record_text_index", index)
            except Exception:
                return
        now = time.time()
        for ref in refs:
            index[ref] = {"spoken": spoken, "source": source, "ts": now}
        if len(index) > 300:
            kept = sorted(index.items(), key=lambda item: float((item[1] or {}).get("ts") or 0))[-180:]
            index.clear()
            index.update(kept)

    def _lookup_tts_record_text(self, component: Any) -> tuple[str, str]:
        index = getattr(self, "_tts_record_text_index", None)
        if not isinstance(index, dict):
            return "", ""
        for ref in self._tts_record_refs(component):
            item = index.get(ref)
            if isinstance(item, dict):
                return (
                    _single_line(item.get("spoken"), 180),
                    _single_line(item.get("source"), 180),
                )
        return "", ""

    def _annotate_tts_record_component(self, component: Any, spoken_text: str, *, source_text: str = "") -> Any:
        spoken = _single_line(self._strip_any_tts_markup(spoken_text), 500)
        source = _single_line(self._strip_any_tts_markup(source_text), 500)
        try:
            setattr(component, "_private_companion_tts_spoken_text", spoken)
            setattr(component, "_private_companion_tts_source_text", source)
        except Exception:
            pass
        self._remember_tts_record_text(component, spoken, source)
        return component

    def _tts_component_log_note(self, component: Any) -> str:
        spoken = _single_line(getattr(component, "_private_companion_tts_spoken_text", ""), 180)
        source = _single_line(getattr(component, "_private_companion_tts_source_text", ""), 180)
        if not spoken:
            spoken, source = self._lookup_tts_record_text(component)
        if spoken and source and spoken != source:
            return f"语音：{spoken}｜对应文本：{source}"
        if spoken:
            return f"语音：{spoken}"
        return "语音消息"

    def _tts_audio_source_for_event(self, event: Any | None) -> str:
        if event is None:
            return "private_companion"
        try:
            get_extra = getattr(event, "get_extra", None)
            if callable(get_extra) and bool(get_extra("bili_live_auto_reply")):
                return "bili_live_auto_reply"
        except Exception:
            pass
        try:
            if bool(getattr(event, "bili_live_auto_reply", False)):
                return "bili_live_auto_reply"
        except Exception:
            pass
        umo = str(getattr(event, "unified_msg_origin", "") or "")
        if "bili_live_" in umo or "live_stream" in umo:
            return "bili_live_auto_reply"
        return "private_companion"

    def _tts_chain_log_text(self, chain: list[Any]) -> str:
        parts: list[str] = []
        for comp in chain:
            if isinstance(comp, Plain):
                text = _single_line(getattr(comp, "text", ""), 180)
                if text:
                    parts.append(f"文本：{text}")
            elif isinstance(comp, Record):
                parts.append(self._tts_component_log_note(comp))
        return "；".join(parts)

    def _strip_or_keep_emotion_tags(self, text: str, *, provider_kind: str) -> str:
        if self._tts_provider_allows_emotion_tags(provider_kind):
            return str(text or "")
        return EMOTION_TAG_PATTERN.sub("", str(text or "")).strip()

    def _normalize_tts_spoken_text(self, text: str, *, provider_kind: str) -> str:
        cleaned = self._normalize_tts_tags(text)
        cleaned = self._strip_or_keep_emotion_tags(cleaned, provider_kind=provider_kind)
        cleaned = re.sub(r"</?tts>", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"</?t{2,}s\b[^>]*>", "", cleaned, flags=re.IGNORECASE)
        return _single_line(cleaned, 2000)

    def _sanitize_tts_spoken_text(self, text: str, *, provider_kind: str) -> str:
        """Clean text immediately before get_audio, scoped to TTS强化 only."""
        if not text:
            return ""
        source = str(text)
        protected: dict[str, str] = {}
        if self._tts_provider_allows_emotion_tags(provider_kind):
            def _protect_emotion(match: re.Match[str]) -> str:
                token = f"{TTS_EMOTION_PLACEHOLDER_PREFIX}{len(protected)}__"
                protected[token] = match.group(0)
                return token

            source = EMOTION_TAG_PATTERN.sub(_protect_emotion, source)

        if len(source) > 10000:
            return ""

        for pattern in DEFAULT_TTS_SANITIZE_REMOVE_PATTERNS:
            try:
                source = re.sub(pattern, "", source)
            except re.error:
                continue
        for word in DEFAULT_TTS_SANITIZE_FILTER_WORDS:
            source = source.replace(word, "")
        for original, replacement in DEFAULT_TTS_SANITIZE_REPLACEMENTS.items():
            source = source.replace(original, replacement)

        source = re.sub(r"(.)\1{2,}", lambda m: m.group(1) * 2, source)
        source = re.sub(r'[""\u201c\u201d]\s*[""\u201c\u201d]', "", source)
        source = re.sub(r"[''\u2018\u2019]\s*[''\u2018\u2019]", "", source)
        source = re.sub(r"[「」『』【】\[\]]\s*[「」『』【】\[\]]", "", source)
        source = re.sub(r"[,，、;；]\s*(?=[,，、;；\s])", "", source)
        source = re.sub(r"[,，、;；]\s*$", "", source)
        source = re.sub(r"^\s*[,，、;；]\s*", "", source)
        source = re.sub(r"\s+", " ", source).strip()

        for token, original in protected.items():
            source = source.replace(token, original)
        source = source.strip()
        compact = re.sub(r"[\s\W_]+", "", source, flags=re.UNICODE)
        return source if compact else ""

    def _tts_session_key(self, event: Any) -> str:
        return _single_line(getattr(event, "unified_msg_origin", ""), 160) if event is not None else ""

    def _tts_event_scope_kind(self, event: Any) -> str:
        origin = str(getattr(event, "unified_msg_origin", "") or "")
        if "GroupMessage" in origin:
            return "group"
        if "FriendMessage" in origin:
            return "private"
        return ""

    def _tts_effective_min_interval_seconds(self, event: Any) -> float:
        interval = float(getattr(self, "tts_session_min_interval_seconds", 0.0) or 0.0)
        scope = self._tts_event_scope_kind(event)
        override = None
        if scope == "private":
            override = getattr(self, "tts_private_min_interval_seconds", -1.0)
        elif scope == "group":
            override = getattr(self, "tts_group_min_interval_seconds", -1.0)
        try:
            override_value = float(override)
        except (TypeError, ValueError):
            override_value = -1.0
        return max(0.0, override_value if override_value >= 0 else interval)

    def _tts_effective_trigger_probability(self, event: Any) -> float:
        probability = float(getattr(self, "tts_trigger_probability", 1.0) or 0.0)
        scope = self._tts_event_scope_kind(event)
        override = None
        if scope == "private":
            override = getattr(self, "tts_private_trigger_probability", -0.01)
        elif scope == "group":
            override = getattr(self, "tts_group_trigger_probability", -0.01)
        try:
            override_value = float(override)
        except (TypeError, ValueError):
            override_value = -0.01
        return max(0.0, min(1.0, override_value if override_value >= 0 else probability))

    def _tts_session_interval_remaining(self, event: Any) -> float:
        if getattr(self, "tts_frequency_control_mode", "global") == "legacy":
            return 0.0
        session = self._tts_session_key(event)
        interval = self._tts_effective_min_interval_seconds(event)
        if not session or interval <= 0:
            return 0.0
        last = float(getattr(self, "_tts_session_last_at", {}).get(session, 0.0) or 0.0)
        return max(0.0, interval - (time.time() - last))

    def _mark_tts_session_sent(self, event: Any) -> None:
        session = self._tts_session_key(event)
        if not session:
            return
        state = getattr(self, "_tts_session_last_at", None)
        if not isinstance(state, dict):
            state = {}
            self._tts_session_last_at = state
        state[session] = time.time()

    def _tts_strong_constraint_enabled(self) -> bool:
        return (
            getattr(self, "tts_frequency_control_mode", "global") != "legacy"
            and getattr(self, "tts_generation_mode", "fast_tag") == "fast_tag"
            and getattr(self, "tts_constraint_mode", "weak") == "strong"
        )

    def _set_tts_hard_block(self, event: Any, reason: str) -> None:
        if event is None:
            return
        try:
            setattr(event, "_private_companion_tts_hard_block_reason", _single_line(reason, 120))
        except Exception:
            pass

    def _tts_hard_block_reason(self, event: Any) -> str:
        return _single_line(getattr(event, "_private_companion_tts_hard_block_reason", ""), 120)

    def _tts_strong_constraint_block_reason(
        self,
        event: Any,
        *,
        user_requested_tts: bool = False,
        check_probability: bool = True,
        reason: str = "llm_tts_prompt",
    ) -> str:
        if not self._tts_strong_constraint_enabled():
            return ""
        remaining = self._tts_session_interval_remaining(event)
        if remaining > 0:
            return f"cooldown:{remaining:.1f}s"
        if check_probability and not user_requested_tts and not self._tts_trigger_probability_allows(event, reason=reason):
            return "probability_miss"
        return ""

    def _event_explicitly_requests_tts(self, event: Any) -> bool:
        return self._event_tts_request_signal(event)[0] == "positive"

    def _event_tts_request_signal(self, event: Any) -> tuple[str, str, str]:
        raw_text = str(getattr(event, "message_str", "") or "").strip()
        text = raw_text.lower()
        if not text:
            return "uncertain", "", ""
        compact = re.sub(r"\s+", "", text)
        negative_patterns = (
            r"(不要|别|不用|不必|禁止|关闭|取消|别再|先别).{0,8}(语音|tts|朗读|念出来|读出来)",
            r"(语音|tts|朗读|念出来|读出来).{0,8}(不要|别|不用|不必|禁止|关闭|取消)",
            r"(不想|不是想|没想|暂时不想|先不想).{0,8}(听|听听|听一下|听见|听到).{0,8}(你|妳|你的|妳的).{0,4}(声音|声)",
            r"(不想|不是想|没想|暂时不想|先不想).{0,8}(你的|妳的).{0,4}(声音|声)",
        )
        for pattern in negative_patterns:
            match = re.search(pattern, compact, flags=re.IGNORECASE)
            if match:
                return "negative", _single_line(match.group(0), 80), raw_text
        positive_patterns = (
            r"^(?:听|听听|听一下|想听|想听听|想听一下)(?:你|妳|你的|妳的)?(?:声音|声|语音)$",
            r"(用|发|来|回|回复|说|讲).{0,10}(语音|tts|朗读|念出来|读出来)",
            r"(语音|tts|朗读|念出来|读出来).{0,10}(回|回复|发|来|说|讲|一下|模式)",
            r"(开|启用|打开).{0,8}(语音|tts)",
            r"(想|想要|想听|想听听|想听一下|想听见|想听到|想听你|想听妳).{0,8}(你|妳|你的|妳的).{0,4}(声音|声)",
            r"(想|想要).{0,6}(听|听听|听一下|听见|听到).{0,8}(你|妳|你的|妳的).{0,4}(声音|声)",
            r"(让我|给我|陪我).{0,6}(听|听听|听一下).{0,8}(你|妳|你的|妳的).{0,4}(声音|声)",
        )
        for pattern in positive_patterns:
            match = re.search(pattern, compact, flags=re.IGNORECASE)
            if match:
                return "positive", _single_line(match.group(0), 80), raw_text
        return "uncertain", "", raw_text

    def _tts_trigger_probability_allows(self, event: Any, *, reason: str) -> bool:
        if getattr(self, "tts_frequency_control_mode", "global") == "legacy":
            return True
        cached = getattr(event, "_private_companion_tts_trigger_probability_allowed", None)
        if isinstance(cached, bool):
            return cached
        probability = self._tts_effective_trigger_probability(event)
        if probability >= 1.0:
            try:
                setattr(event, "_private_companion_tts_trigger_probability_allowed", True)
            except Exception:
                pass
            return True
        if probability <= 0.0:
            logger.info(
                "[PrivateCompanion] TTS全局触发概率为0,本轮不注入TTS提示词: reason=%s session=%s",
                reason,
                _single_line(self._tts_session_key(event), 80) or "unknown",
            )
            try:
                setattr(event, "_private_companion_tts_trigger_probability_allowed", False)
            except Exception:
                pass
            return False
        allowed = random.random() <= probability
        try:
            setattr(event, "_private_companion_tts_trigger_probability_allowed", allowed)
        except Exception:
            pass
        if not allowed:
            logger.info(
                "[PrivateCompanion] TTS全局触发概率未命中,本轮不注入TTS提示词: reason=%s probability=%.2f session=%s",
                reason,
                probability,
                _single_line(self._tts_session_key(event), 80) or "unknown",
            )
        return allowed

    def _tts_visible_text_has_chinese(self, text: str) -> bool:
        cleaned = self._sanitize_tts_visible_text(text)
        cleaned = re.sub(r"[\s\W_]+", "", cleaned, flags=re.UNICODE)
        if not cleaned:
            return False
        # Japanese uses CJK ideographs too. A visible explanation for a non-Chinese
        # TTS block must be actual Chinese, not merely Japanese text containing kanji.
        if re.search(r"[\u3040-\u30ff\u31f0-\u31ff]", cleaned):
            return False
        cjk_count = len(re.findall(r"[\u4e00-\u9fff]", cleaned))
        if cjk_count < 2:
            return False
        chinese_markers = (
            "的", "了", "是", "我", "你", "他", "她", "它", "们", "这", "那",
            "不", "有", "在", "就", "吗", "呢", "吧", "呀", "啊", "哦", "嘛",
            "想", "要", "可以", "知道", "睡", "觉", "终于", "今天", "明天",
            "晚上", "早上", "下次", "这次", "喜欢", "辛苦", "轻点", "等会",
        )
        return any(marker in cleaned for marker in chinese_markers) or cjk_count >= 4

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

    async def _translate_tts_spoken_to_chinese(self, text: str, event: Any, *, provider_kind: str) -> str:
        spoken = self._normalize_tts_spoken_text(text, provider_kind=provider_kind)
        if not spoken:
            return ""
        if self._tts_visible_text_has_chinese(spoken):
            return spoken
        provider = await self._get_tts_conversion_provider(event) if event is not None else None
        persona_context = await self._format_tts_persona_voice_context(event)
        prompt = f"""
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
""".strip()
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
                        "[PrivateCompanion] TTS中文释义结果不像中文,已丢弃: source=%s result=%s",
                        _single_line(spoken, 80),
                        _single_line(translated, 80),
                    )
        except Exception as exc:
            logger.warning("[PrivateCompanion] TTS中文释义生成失败: %s", _single_line(exc, 120))
        return ""

    async def _ensure_tts_blocks_have_visible_chinese(self, text: str, event: Any, *, provider_kind: str) -> str:
        normalized = self._normalize_tts_tags(text)
        if getattr(self, "tts_voice_language", "ja") == "zh":
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
            if not self._tts_visible_text_is_allowed_after_voice(visible_after_this_block):
                spoken = self._normalize_tts_spoken_text(match.group(1), provider_kind=provider_kind)
                visible_translation = await self._translate_tts_spoken_to_chinese(spoken, event, provider_kind=provider_kind)
                if visible_translation:
                    separator = "\n" if not visible_after_this_block.startswith(("\n", "\r")) else ""
                    pieces.append(f"{separator}{visible_translation}")
                    changed = True
                    logger.info(
                        "[PrivateCompanion] TTS记录文本已补中文释义: 语音=%s 中文=%s",
                        _single_line(spoken, 80),
                        _single_line(visible_translation, 80),
                    )
                else:
                    logger.warning(
                        "[PrivateCompanion] TTS记录文本缺少中文释义且自动补充失败: 语音=%s",
                        _single_line(spoken, 100),
                    )
            pieces.append(visible_after_this_block)
            pos = next_start
        pieces.append(normalized[pos:])
        return "".join(pieces) if changed else normalized

    def _tts_text_needs_language_conversion(self, text: str, *, provider_kind: str) -> bool:
        spoken = self._normalize_tts_spoken_text(text, provider_kind=provider_kind)
        if not spoken:
            return False
        lang = getattr(self, "tts_voice_language", "ja")
        if lang == "zh":
            return False
        cjk_count = len(re.findall(r"[\u4e00-\u9fff]", spoken))
        if lang == "en":
            return cjk_count > 0
        if lang != "ja":
            return False
        kana_count = len(re.findall(r"[\u3040-\u30ff\u31f0-\u31ff]", spoken))
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

    def _build_tts_rule_prompt(self, provider_kind: str = "generic") -> str:
        lang = self._tts_language_label()
        mode = getattr(self, "tts_generation_mode", "fast_tag")
        frequency_mode = getattr(self, "tts_frequency_control_mode", "global")
        voice_lang = getattr(self, "tts_voice_language", "ja")
        supports_emotion = self._tts_provider_allows_emotion_tags(provider_kind)
        if mode == "fast_tag":
            if frequency_mode == "legacy":
                usage_rule = "由你根据当前回复是否适合被听见、情绪是否更贴近、用户是否明显期待语音来自行判断；不要为了格式而滥用。"
            else:
                usage_rule = "不要刻意使用语音；只有在一句话更适合被听见、情绪更贴近或用户明显期待语音时才写 <pc_tts>。"
        else:
            usage_rule = "本轮主模型可以正常回复，不需要主动写 <pc_tts> 或 <tts>；后处理会生成语音格式。"
        positive_emotion, negative_emotion = self._tts_emotion_tag_examples(provider_kind)
        emotion_rule = f"3.{self._tts_emotion_tag_rule(provider_kind)}" if supports_emotion else ""
        language_rule = ""
        if voice_lang == "zh":
            language_rule = "<pc_tts> 内也用自然中文；语音块后不强制再写重复翻译。"
        elif voice_lang == "en":
            language_rule = "<pc_tts> 内必须是自然英语；每个语音块后直接补一句自然中文，不要加“中文含义：”“对应文本：”这类标题。"
        else:
            language_rule = "<pc_tts> 内必须是自然日语，除极短语气词外要包含假名；每个语音块后直接补一句自然中文，不要加“中文含义：”“对应文本：”这类标题。"
        examples = ""
        if mode == "fast_tag":
            if voice_lang == "zh":
                examples = (
                    "示例：\n"
                    "例1：嗯，我在听。你慢慢说。\n"
                    "例2：<pc_tts>嗯，我在听。</pc_tts>你慢慢说。\n"
                    f"例3：先别急，<pc_tts>{positive_emotion if supports_emotion else ''}我陪你想一下。</pc_tts>这件事可以一点点拆开。"
                )
            elif voice_lang == "en":
                examples = (
                    "示例：\n"
                    "例1：我在听。你慢慢说。\n"
                    "例2：<pc_tts>I am listening.</pc_tts>我在听。你慢慢说。\n"
                    f"例3：先别急，<pc_tts>{negative_emotion if supports_emotion else ''}Let me stay with you for a moment.</pc_tts>我先在你旁边待一会儿。我们一点点想。"
                )
            else:
                examples = (
                    "示例：\n"
                    "例1：我有在好好听哦。你慢慢说。\n"
                    "例2：<pc_tts>ちゃんと聞いてるよ。</pc_tts>我有在好好听哦。你慢慢说。\n"
                    f"例3：先别急，<pc_tts>{negative_emotion if supports_emotion else ''}少しだけ、そばにいるね。</pc_tts>我先在你旁边待一会儿。我们一点点想。"
                )
        extra = _single_line(getattr(self, "tts_extra_prompt", ""), 800)
        if not extra:
            extra = self._legacy_nondefault_tts_prompt()
        if voice_lang == "zh":
            first_rule = "1.自然聊天时用中文文字推进对话，把适合朗读的中文部分用一对<pc_tts>包起来；"
        else:
            first_rule = "1.自然聊天时用中文文字推进对话，把适合朗读的外语部分用一对<pc_tts>包起来，并在后面直接补一句自然中文，不要写“中文含义：”“对应文本：”这类标题；"
        rules = [
            "【语音消息规则】",
            first_rule,
            "2.语音块可以出现在回复的开头、中间或结尾，只要读起来像自然聊天即可；",
        ]
        if emotion_rule:
            rules.append(emotion_rule)
        return "\n".join(
            item
            for item in [
                "\n".join(rules),
                f"当前语音正文目标语种：{lang}。",
                language_rule,
                usage_rule,
                examples,
                f"补充规则：{extra}" if extra else "",
            ]
            if item
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
                logger.debug("[PrivateCompanion] TTS读取人格上下文失败,使用缓存: %s", _single_line(exc, 120))
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

    async def apply_tts_enhancement_request(self, event: Any, req: Any) -> None:
        feature_enabled = getattr(self, "_feature_enabled_or_temp_unlocked", None)
        tts_enabled = feature_enabled("enable_tts_enhancement") if callable(feature_enabled) else getattr(self, "enable_tts_enhancement", False)
        if not getattr(self, "enabled", False) or not tts_enabled:
            return
        if not hasattr(req, "system_prompt"):
            return
        try:
            config = self.context.get_config(str(getattr(event, "unified_msg_origin", "") or "")) or {}
        except Exception:
            config = getattr(self, "config", {}) or {}
        provider_kind = self._tts_provider_kind_for_event(event, config=config)
        marker = "<!-- private_companion_tts_enhancement_v1 -->"
        prompt = str(getattr(req, "system_prompt", "") or "")

        def append_dynamic_tts_fragment(fragment_marker: str, text: str, *, priority: int = 55) -> str:
            helper = getattr(self, "_append_turn_prompt_fragment_by_position", None)
            if callable(helper):
                try:
                    if helper(req, fragment_marker, text, priority=priority, source="tts"):
                        return "prompt"
                except TypeError:
                    if helper(req, fragment_marker, text):
                        return "prompt"
                except Exception as exc:
                    logger.debug("[PrivateCompanion] TTS 指定位置动态注入失败,回退 system_prompt: %s", _single_line(exc, 120))
            req.system_prompt = f"{getattr(req, 'system_prompt', '') or ''}\n\n{text}".strip()
            return "system_prompt"

        async def record_tts_fragment(title: str, key: str, text: str, mode: str = "", placement: str = "system_prompt") -> None:
            recorder = getattr(self, "_record_prompt_injection_snapshot", None)
            if not callable(recorder):
                return
            await recorder(
                kind="request",
                session=_single_line(getattr(event, "unified_msg_origin", ""), 160) or "unknown",
                title=title,
                text=text,
                mode=mode or str(getattr(self, "tts_generation_mode", "fast_tag") or ""),
                modules=[
                    {
                        "key": key,
                        "source": "tts_enhancement",
                        "priority": 20,
                        "content": text,
                        "chars": len(text),
                    }
                ],
                metadata={
                    "语种": self._tts_language_label(),
                    "模式": getattr(self, "tts_generation_mode", "fast_tag"),
                    "频控": getattr(self, "tts_frequency_control_mode", "global"),
                    "provider": provider_kind,
                    "注入位置": placement,
                },
            )

        user_requested_tts = self._event_explicitly_requests_tts(event)
        strong_block_reason = ""
        mode = getattr(self, "tts_generation_mode", "fast_tag")
        probability_allowed = True
        if (
            mode in {"fast_tag", "postprocess"}
            and not user_requested_tts
            and getattr(self, "tts_frequency_control_mode", "global") != "legacy"
        ):
            probability_allowed = self._tts_trigger_probability_allows(event, reason="llm_tts_prompt")
            if not probability_allowed:
                if self._tts_strong_constraint_enabled():
                    self._set_tts_hard_block(event, "probability_miss")
                return
        if mode == "fast_tag":
            strong_block_reason = self._tts_strong_constraint_block_reason(
                event,
                user_requested_tts=user_requested_tts,
                check_probability=False,
                reason="llm_tts_prompt",
            )
            if strong_block_reason:
                self._set_tts_hard_block(event, strong_block_reason)
        if marker not in prompt and mode == "fast_tag" and not strong_block_reason:
            rule_prompt = self._build_tts_rule_prompt(provider_kind)
            req.system_prompt = f"{prompt}\n\n{marker}\n{rule_prompt}".strip()
            await record_tts_fragment("TTS 基础规则注入", "tts.rule", rule_prompt)
        elif marker not in prompt and mode == "postprocess" and not strong_block_reason:
            postprocess_prompt = (
                "【TTS 后处理模式】\n"
                "本轮主回复请只输出普通聊天文字，不要主动写 <pc_tts>、<tts>、语音、朗读、音频或任何等价语音标签。"
                "是否把其中一小段转成语音，将由插件发送前的 TTS 后处理模型统一判断。"
            )
            req.system_prompt = f"{prompt}\n\n{marker}\n{postprocess_prompt}".strip()
            await record_tts_fragment("TTS 后处理模式注入", "tts.rule", postprocess_prompt, mode="postprocess")
        if strong_block_reason:
            reverse_prompt = (
                "【本轮 TTS 强约束】\n"
                f"本轮语音被硬性禁止，原因：{strong_block_reason}。\n"
                "请只输出普通文字回复，不要包含 <pc_tts>...</pc_tts>、<tts>...</tts>、语音、朗读、音频、发声、Record 或任何等价语音内容。"
                "如果用户要求语音，也先用文字自然回应当前内容，不要承诺已经发送语音。"
            )
            placement = append_dynamic_tts_fragment("<!-- private_companion_tts_block_v1 -->", reverse_prompt, priority=22)
            await record_tts_fragment("TTS 强约束禁用注入", "tts.block", reverse_prompt, mode="strong_block", placement=placement)
        if mode == "fast_tag" and self._should_force_tts_for_main_user_event(event) and not strong_block_reason:
            frequency_mode = getattr(self, "tts_frequency_control_mode", "global")
            if frequency_mode == "legacy":
                force_rule = "这轮消息来自主用户或明确 @ 到主用户。若当前回复适合语音表达，适合采用一段 <pc_tts>...</pc_tts>；由你根据语境判断，仍需遵守目标语种和中文释义。"
            else:
                force_rule = "这轮消息来自主用户或明确 @ 到主用户。如果语音比纯文字更自然，可以采用一段 <pc_tts>...</pc_tts>；不要刻意使用语音，仍需遵守目标语种、中文释义和会话最小间隔。"
            force_prompt = f"【本轮 TTS 强化触发】\n{force_rule}"
            placement = append_dynamic_tts_fragment("<!-- private_companion_tts_force_v1 -->", force_prompt, priority=54)
            await record_tts_fragment("TTS 主用户倾向注入", "tts.force", force_prompt, mode="main_user", placement=placement)
        if user_requested_tts and mode == "fast_tag" and not strong_block_reason:
            user_request_prompt = (
                "【用户语音请求】\n"
                "用户本轮明确希望听到语音或你的声音。请以回应用户需求为主：如果当前回复适合用语音表达，可以直接写一段 <pc_tts>...</pc_tts>；"
                "这类顺应用户请求的语音不受自动语音触发概率限制，但仍需自然克制、遵守目标语种和中文释义，不要为了格式而硬加。"
            )
            placement = append_dynamic_tts_fragment("<!-- private_companion_tts_user_request_v1 -->", user_request_prompt, priority=54)
            await record_tts_fragment("用户语音请求注入", "tts.user_request", user_request_prompt, mode="user_request", placement=placement)

    async def protect_tts_enhancement_response_blocks(self, event: Any, resp: Any) -> None:
        feature_enabled = getattr(self, "_feature_enabled_or_temp_unlocked", None)
        tts_enabled = feature_enabled("enable_tts_enhancement") if callable(feature_enabled) else getattr(self, "enable_tts_enhancement", False)
        if not tts_enabled:
            text = str(getattr(resp, "completion_text", "") or "")
            if re.search(r"</?(?:pc[_-]?tts|t{2,}s)\b", text, flags=re.IGNORECASE):
                resp.completion_text = _normalize_outbound_punctuation_flow(self._strip_any_tts_markup(text))
                logger.info(
                    "[PrivateCompanion] TTS强化未开启,已从模型回复中移除 TTS 标签: session=%s preview=%s",
                    _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                    _single_line(resp.completion_text, 160),
                )
            return
        text = self._normalize_tts_tags(str(getattr(resp, "completion_text", "") or ""))
        if text:
            if "<tts>" in text.lower() and "</tts>" in text.lower():
                if getattr(self, "tts_generation_mode", "fast_tag") == "postprocess":
                    cleaned = re.sub(r"<tts\b[^>]*>.*?</tts>", "", text, flags=re.IGNORECASE | re.DOTALL).strip()
                    cleaned = re.sub(TTS_TAG_PATTERN, "", cleaned).strip() or self._tts_visible_fallback_text(text)
                    cleaned = cleaned or self._strip_any_tts_markup(text)
                    resp.completion_text = _normalize_outbound_punctuation_flow(cleaned)
                    logger.info(
                        "[PrivateCompanion] TTS后处理模式已移除主模型自写语音标签,改由发送前后处理判断: session=%s preview=%s",
                        _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                        _single_line(cleaned, 160),
                    )
                    return
                if not self._tts_hard_block_reason(event):
                    try:
                        config = self.context.get_config(str(getattr(event, "unified_msg_origin", "") or "")) or {}
                    except Exception:
                        config = getattr(self, "config", {}) or {}
                    provider_settings = dict((config or {}).get("provider_tts_settings", {}) or {})
                    provider_kind = self._tts_provider_kind(provider_settings=provider_settings)
                    text = await self._ensure_tts_blocks_have_visible_chinese(text, event, provider_kind=provider_kind)
                text = self._protect_tts_blocks_for_framework(text, event)
            resp.completion_text = _normalize_outbound_punctuation_flow(text)

    async def apply_tts_enhancement_before_send(self, event: Any) -> None:
        feature_enabled = getattr(self, "_feature_enabled_or_temp_unlocked", None)
        tts_enabled = feature_enabled("enable_tts_enhancement") if callable(feature_enabled) else getattr(self, "enable_tts_enhancement", False)
        if not getattr(self, "enabled", False) or not tts_enabled:
            return
        result = event.get_result()
        chain = list(getattr(result, "chain", []) or []) if result is not None else []
        if not chain or any(isinstance(comp, Record) for comp in chain):
            return
        plain_parts = [str(getattr(comp, "text", "") or "") for comp in chain if isinstance(comp, Plain)]
        if not plain_parts:
            return
        text = self._restore_protected_tts_blocks("".join(plain_parts), event).strip()
        if not text:
            return
        normalized = self._normalize_tts_tags(text)
        if "<tts>" in normalized.lower() and "</tts>" in normalized.lower():
            new_chain = await self._process_tts_tags(normalized, event)
        else:
            new_chain = await self._maybe_convert_plain_reply_to_tts(normalized, event)
        if not new_chain:
            if PRIVATE_TTS_BLOCK_TOKEN_PATTERN.search("".join(plain_parts)):
                fallback_text = self._tts_visible_fallback_text(normalized)
                event.set_result(self._build_result_from_chain([Plain(fallback_text)] if fallback_text else []))
            return
        if len(plain_parts) != len(chain):
            non_plain_tail = [comp for comp in chain if not isinstance(comp, Plain)]
            if non_plain_tail:
                new_chain = list(new_chain) + non_plain_tail
        ordered_chunks = self._split_tts_chain_for_ordered_send(new_chain)
        expanded_chunks: list[list[Any]] = []
        for chunk in ordered_chunks:
            expanded_chunks.extend(self._tts_segment_plain_chunk_for_ordered_send(event, chunk))
        ordered_chunks = expanded_chunks
        if len(ordered_chunks) > 1:
            inbound_ts_getter = getattr(self, "_event_inbound_activity_ts", None)
            if callable(inbound_ts_getter):
                try:
                    remainder_started_at = float(inbound_ts_getter(event))
                except Exception:
                    remainder_started_at = time.time()
            else:
                remainder_started_at = time.time()
            event.set_result(self._build_result_from_chain(ordered_chunks[0]))
            asyncio.create_task(
                self._send_tts_chain_chunks_after_first(
                    event,
                    ordered_chunks[1:],
                    started_at=remainder_started_at,
                )
            )
            return
        event.set_result(self._build_result_from_chain(ordered_chunks[0] if ordered_chunks else new_chain))

    async def finalize_outbound_tts_markup_guard(self, event: Any) -> None:
        """Last-resort guard so raw <tts> tags never reach the chat surface."""
        if not getattr(self, "enabled", False):
            return
        result = event.get_result()
        chain = list(getattr(result, "chain", []) or []) if result is not None else []
        if not chain or any(isinstance(comp, Record) for comp in chain):
            return
        plain_parts = [str(getattr(comp, "text", "") or "") for comp in chain if isinstance(comp, Plain)]
        if not plain_parts:
            return
        text = self._restore_protected_tts_blocks("".join(plain_parts), event).strip()
        if not re.search(r"</?(?:pc[_-]?tts|t{2,}s)\b", text, flags=re.IGNORECASE):
            return
        normalized = self._normalize_tts_tags(text)
        feature_enabled = getattr(self, "_feature_enabled_or_temp_unlocked", None)
        tts_enabled = feature_enabled("enable_tts_enhancement") if callable(feature_enabled) else getattr(self, "enable_tts_enhancement", False)
        new_chain: list[Any] = []
        if tts_enabled and re.search(r"<tts\b[^>]*>.*?</tts>", normalized, flags=re.IGNORECASE | re.DOTALL):
            new_chain = await self._process_tts_tags(normalized, event)
        if not new_chain:
            fallback_text = self._tts_visible_fallback_text(normalized) or self._strip_any_tts_markup(normalized)
            fallback_text = self._sanitize_tts_visible_text(fallback_text)
            new_chain = [Plain(fallback_text)] if fallback_text else []
        if len(plain_parts) != len(chain):
            non_plain_tail = [comp for comp in chain if not isinstance(comp, Plain)]
            if non_plain_tail:
                new_chain = list(new_chain) + non_plain_tail
        logger.warning(
            "[PrivateCompanion] 发送前终检拦截残留 TTS 标签: session=%s preview=%s",
            _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            _single_line(self._tts_chain_log_text(new_chain), 160),
        )
        event.set_result(self._build_result_from_chain(new_chain))

    async def _sanitize_outbound_tts_chain_without_event(self, chain: list[Any], *, umo: str = "") -> list[Any]:
        if not chain or any(isinstance(comp, Record) for comp in chain):
            return chain
        changed = False
        cleaned_chain: list[Any] = []
        for comp in chain:
            if not isinstance(comp, Plain):
                cleaned_chain.append(comp)
                continue
            original = str(getattr(comp, "text", "") or "")
            if not re.search(r"</?(?:pc[_-]?tts|t{2,}s)\b", original, flags=re.IGNORECASE):
                cleaned_chain.append(comp)
                continue
            changed = True
            normalized = self._normalize_tts_tags(original)
            fallback_text = self._tts_visible_fallback_text(normalized) or self._strip_any_tts_markup(normalized)
            fallback_text = self._sanitize_tts_visible_text(fallback_text)
            if fallback_text:
                cleaned_chain.append(Plain(fallback_text))
        if changed:
            logger.warning(
                "[PrivateCompanion] 外发兜底清理残留 TTS 标签: umo=%s preview=%s",
                _single_line(umo, 120) or "unknown",
                _single_line(self._tts_chain_log_text(cleaned_chain), 160),
            )
        return cleaned_chain

    def _split_tts_chain_for_ordered_send(self, chain: list[Any]) -> list[list[Any]]:
        chunks: list[list[Any]] = []
        current_visible: list[Any] = []
        has_record = False
        has_visible = False
        for comp in chain:
            if isinstance(comp, Record):
                has_record = True
                if current_visible:
                    chunks.append(current_visible)
                    current_visible = []
                chunks.append([comp])
            else:
                has_visible = True
                current_visible.append(comp)
        if current_visible:
            chunks.append(current_visible)
        return chunks if has_record and has_visible else [chain]

    def _tts_segment_plain_chunk_for_ordered_send(self, event: Any, chunk: list[Any]) -> list[list[Any]]:
        if not (
            bool(getattr(self, "enable_segmented_proactive_reply", False))
            and str(getattr(self, "segmented_proactive_scope", "") or "") == "all_llm"
        ):
            return [chunk]
        scope_checker = getattr(self, "_segmented_scope_allows_event", None)
        if callable(scope_checker):
            try:
                if not scope_checker(event):
                    return [chunk]
            except Exception:
                return [chunk]
        if not chunk or any(not isinstance(comp, Plain) for comp in chunk):
            return [chunk]
        text = "".join(str(getattr(comp, "text", "") or "") for comp in chunk).strip()
        if not text:
            return []
        original_text = text
        is_tts_visible_text = any(bool(getattr(comp, "_private_companion_tts_visible_text", False)) for comp in chunk)
        if is_tts_visible_text:
            cleaned_visible = self._sanitize_tts_visible_text(text)
            return [[Plain(cleaned_visible)]] if cleaned_visible else []
        if getattr(self, "tts_voice_language", "ja") != "zh" and not self._tts_visible_text_is_allowed_after_voice(text):
            chinese_text = self._tts_chinese_visible_fallback_from_mixed(text)
            if chinese_text:
                logger.warning(
                    "[PrivateCompanion] TTS 后置文本混有朗读语种,已仅保留中文释义: session=%s text=%s",
                    _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                    _single_line(chinese_text, 120),
                )
                text = chinese_text
            else:
                logger.warning(
                    "[PrivateCompanion] TTS 后置文本不是中文释义,已跳过发送: session=%s text=%s",
                    _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                    _single_line(text, 120),
                )
                return []
        splitter = getattr(self, "_split_proactive_text", None)
        if not callable(splitter):
            return [[Plain(text)]]
        try:
            segments = [item for item in splitter(text) if str(item or "").strip()]
        except Exception as exc:
            logger.debug("[PrivateCompanion] TTS 后置文本分段失败,保持原样: %s", _single_line(exc, 120))
            return [chunk]
        if len(segments) <= 1:
            cleaned = segments[0] if segments else text
            return [[Plain(cleaned)]] if cleaned and (cleaned != text or text != original_text) else [chunk]
        logger.info(
            "[PrivateCompanion] TTS 后置文本按分段规则拆分: session=%s segments=%s first=%s",
            _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            len(segments),
            _single_line(segments[0], 100),
        )
        return [[Plain(segment)] for segment in segments]

    async def _send_tts_chain_chunks_after_first(
        self,
        event: Any,
        chunks: list[list[Any]],
        *,
        started_at: float | None = None,
    ) -> None:
        if not chunks:
            return
        expanded_chunks: list[list[Any]] = []
        for chunk in chunks:
            expanded_chunks.extend(self._tts_segment_plain_chunk_for_ordered_send(event, chunk))
        scope_getter = getattr(self, "_event_scope_key", None)
        scope = ""
        if callable(scope_getter):
            try:
                scope = _single_line(scope_getter(event), 160)
            except Exception:
                scope = ""
        if not scope:
            scope = _single_line(getattr(event, "unified_msg_origin", ""), 160) or "unknown"
        lock_getter = getattr(self, "_segmented_remainder_lock", None)
        lock = lock_getter(scope) if callable(lock_getter) else asyncio.Lock()
        started_at = float(started_at or time.time())
        previous_text = ""
        async with lock:
            for chunk in expanded_chunks:
                if not chunk:
                    continue
                stop_after_send = False
                delay = 0.45
                if previous_text and len(expanded_chunks) > 1:
                    calc_interval = getattr(self, "_calc_segmented_proactive_interval", None)
                    if callable(calc_interval):
                        try:
                            delay = max(0.45, float(await calc_interval(previous_text)))
                        except Exception:
                            delay = 0.45
                await asyncio.sleep(delay)
                activity_checker = getattr(self, "_scope_has_new_inbound_activity", None)
                if callable(activity_checker):
                    try:
                        if activity_checker(scope, started_at, ignore_self=True):
                            if not previous_text:
                                stop_after_send = True
                                logger.info(
                                    "[PrivateCompanion] 会话已有新消息，但仍补发 TTS 语音对应首段文本: session=%s",
                                    scope,
                                )
                            else:
                                logger.info(
                                    "[PrivateCompanion] 会话已有新消息，停止发送 TTS 后续分块: session=%s sent_preview=%s",
                                    scope,
                                    _single_line(previous_text, 120) or "0",
                                )
                                return
                    except Exception:
                        pass
                try:
                    await event.send(event.chain_result(chunk))
                    logger.info(
                        "[PrivateCompanion] TTS 分块后台补发完成: session=%s %s",
                        _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                        self._tts_chain_log_text(chunk),
                    )
                except Exception as exc:
                    try:
                        await event.send(self._build_result_from_chain(chunk))
                        logger.info(
                            "[PrivateCompanion] TTS 分块后台补发完成: session=%s %s",
                            _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                            self._tts_chain_log_text(chunk),
                        )
                    except Exception:
                        logger.warning("[PrivateCompanion] TTS 分块后台补发失败: %s", _single_line(exc, 120))
                        return
                previous_text = " ".join(
                    str(getattr(comp, "text", "") or "").strip()
                    for comp in chunk
                    if isinstance(comp, Plain)
                ).strip() or previous_text
                if stop_after_send:
                    logger.info(
                        "[PrivateCompanion] TTS 语音对应首段文本已补发，停止发送剩余分块: session=%s sent_preview=%s",
                        scope,
                        _single_line(previous_text, 120) or "0",
                    )
                    return

    async def _maybe_convert_plain_reply_to_tts(self, text: str, event: Any) -> list[Any]:
        mode = getattr(self, "tts_generation_mode", "fast_tag")
        strong_block_reason = self._tts_strong_constraint_block_reason(
            event,
            user_requested_tts=self._event_explicitly_requests_tts(event),
            check_probability=False,
            reason="auto_convert_cooldown",
        )
        if strong_block_reason:
            self._set_tts_hard_block(event, strong_block_reason)
            return []
        should_convert = mode == "postprocess"
        reason = "postprocess" if should_convert else ""
        if not should_convert:
            ok, reason = self._auto_voice_trigger_reason(text, event)
            should_convert = ok
        if not should_convert:
            return []
        user_requested_tts = self._event_explicitly_requests_tts(event)
        if mode == "postprocess":
            probability_allowed = user_requested_tts or self._tts_trigger_probability_allows(event, reason=reason or mode)
            try:
                setattr(event, "_private_companion_tts_postprocess_probability_allowed", bool(probability_allowed))
            except Exception:
                pass
        else:
            probability_allowed = (
                user_requested_tts and not self._tts_strong_constraint_enabled()
            ) or self._tts_trigger_probability_allows(event, reason=reason or mode)
        if not probability_allowed:
            if self._tts_strong_constraint_enabled():
                self._set_tts_hard_block(event, "probability_miss")
            return []
        visible_override, suppress_visible = self._tts_proactive_segment_visible_policy(event)
        converted = await self._convert_text_to_tts_markup(text, event, full=(mode == "postprocess" or self.auto_voice_full_conversion_enabled))
        if not converted:
            return []
        if visible_override or suppress_visible:
            try:
                setattr(event, "_private_companion_tts_visible_text_override", visible_override)
                setattr(event, "_private_companion_tts_visible_text_suppress", bool(suppress_visible))
            except Exception:
                pass
        fallback_plain = visible_override if visible_override else ("" if suppress_visible else text)
        try:
            chain = await self._process_tts_tags(converted, event, fallback_plain=fallback_plain)
        finally:
            if visible_override or suppress_visible:
                for attr in (
                    "_private_companion_tts_visible_text_override",
                    "_private_companion_tts_visible_text_suppress",
                ):
                    try:
                        delattr(event, attr)
                    except Exception:
                        pass
        if chain:
            session = str(getattr(event, "unified_msg_origin", "") or "")
            self._tts_auto_voice_last_at[session] = time.time()
            logger.info(
                "[PrivateCompanion] TTS强化已转换纯文本回复: reason=%s session=%s %s",
                reason,
                _single_line(session, 80),
                self._tts_chain_log_text(chain),
            )
        return chain

    def _auto_voice_trigger_reason(self, text: str, event: Any) -> tuple[bool, str]:
        if not getattr(self, "auto_voice_enabled", False):
            return False, ""
        session = str(getattr(event, "unified_msg_origin", "") or "")
        use_legacy_frequency = getattr(self, "tts_frequency_control_mode", "global") == "legacy"
        is_main = self._event_targets_main_user(event)
        if use_legacy_frequency and is_main and self.main_user_voice_probability >= 0:
            probability = self.main_user_voice_probability
            bypass_limits = True
            reason = "main_user"
        else:
            probability = getattr(self, "auto_voice_probability", 0.0) if use_legacy_frequency else 1.0
            bypass_limits = False
            reason = "auto"
        if use_legacy_frequency and self._event_mentions_main_user_with_keyword(event):
            probability = max(probability, getattr(self, "main_user_mention_voice_probability", 0.0))
            bypass_limits = True
            reason = "main_user_keyword"
        if probability <= 0 or random.random() > probability:
            return False, ""
        cleaned = _single_line(self._normalize_tts_spoken_text(text, provider_kind="generic"), 10000)
        max_chars = int(getattr(self, "auto_voice_max_chars", 0) or 0)
        if max_chars > 0 and not bypass_limits and len(cleaned) > max_chars:
            return False, ""
        cooldown = int(getattr(self, "auto_voice_cooldown_seconds", 0) or 0) if use_legacy_frequency else 0
        if cooldown > 0 and not bypass_limits and session:
            last = float(getattr(self, "_tts_auto_voice_last_at", {}).get(session, 0) or 0)
            if time.time() - last < cooldown:
                return False, ""
        return True, reason

    def _configured_main_user_ids(self) -> set[str]:
        ids: set[str] = set()
        for value in getattr(self, "target_user_ids", []) or []:
            text = re.sub(r"\D+", "", str(value or ""))
            if text:
                ids.add(text)
        aliases = getattr(self, "private_user_aliases", {}) or {}
        if isinstance(aliases, dict):
            for key, value in aliases.items():
                for raw in (key, value):
                    text = re.sub(r"\D+", "", str(raw or ""))
                    if text:
                        ids.add(text)
        return ids

    def _event_targets_main_user(self, event: Any) -> bool:
        main_ids = self._configured_main_user_ids()
        if not main_ids:
            return False
        try:
            sender = re.sub(r"\D+", "", str(event.get_sender_id()))
        except Exception:
            sender = ""
        if sender and sender in main_ids:
            return True
        for target in self._event_at_qq_ids(event):
            if target in main_ids:
                return True
        return False

    def _event_mentions_main_user_with_keyword(self, event: Any) -> bool:
        if not self._event_targets_main_user(event):
            return False
        keywords = [item for item in getattr(self, "main_user_mention_voice_keywords", []) or [] if item]
        if not keywords:
            return False
        text = str(getattr(event, "message_str", "") or "")
        return any(keyword in text for keyword in keywords)

    def _should_force_tts_for_main_user_event(self, event: Any) -> bool:
        if not getattr(self, "auto_voice_enabled", False):
            return False
        if getattr(self, "tts_frequency_control_mode", "global") != "legacy":
            return False
        if self._event_mentions_main_user_with_keyword(event) and self.main_user_mention_voice_probability > 0:
            return random.random() <= self.main_user_mention_voice_probability
        if self._event_targets_main_user(event) and self.main_user_voice_probability >= 0:
            return random.random() <= self.main_user_voice_probability
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
        source = _single_line(text, 1200)
        if not source:
            return ""
        provider = await self._get_tts_conversion_provider(event)
        provider_kind = self._tts_provider_kind_for_event(event)
        lang = self._tts_language_label()
        voice_lang = getattr(self, "tts_voice_language", "ja")
        mode = getattr(self, "tts_generation_mode", "fast_tag")
        if mode == "postprocess":
            return await self._postprocess_text_to_tts_markup(source, event, provider_kind=provider_kind)
        extra = _single_line(getattr(self, "main_user_mention_voice_prompt", ""), 500) if self._event_mentions_main_user_with_keyword(event) else ""
        persona_context = await self._format_tts_persona_voice_context(event)
        emotion_rule = self._tts_emotion_tag_rule(provider_kind, subject="<pc_tts> 内")
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
        prompt = f"""
请把下面这条回复转换成适合 TTS 朗读的最终输出。

目标语种：{lang}
输出格式：{output_rule}
显示文本规则：{display_rule}
语种规则：{language_rule}
Provider 规则：{emotion_rule}
补充要求：{extra or "无"}
{persona_context}

原回复：
{source}

只输出最终消息，不要解释。
""".strip()
        try:
            if provider is not None:
                resp = await self._tts_provider_text_chat(provider, prompt, max_tokens=700)
                converted = str(getattr(resp, "completion_text", resp) or "").strip()
            else:
                converted = f"<tts>{source}</tts>"
        except Exception as exc:
            logger.warning("[PrivateCompanion] TTS强化转换模型失败: %s", _single_line(exc, 120))
            converted = f"<tts>{source}</tts>"
        converted = self._normalize_tts_tags(converted)
        if "<tts>" not in converted.lower():
            converted = f"<tts>{converted}</tts>"
        return converted

    async def _postprocess_text_to_tts_markup(self, text: str, event: Any, *, provider_kind: str) -> str:
        source = _single_line(text, 1600)
        if not source:
            return ""
        provider = await self._get_tts_conversion_provider(event)
        if provider is None:
            return ""
        lang = self._tts_language_label()
        voice_lang = getattr(self, "tts_voice_language", "ja")
        tts_signal, tts_signal_match, user_text = self._event_tts_request_signal(event)
        extra = _single_line(getattr(self, "tts_extra_prompt", ""), 800)
        if not extra:
            extra = self._legacy_nondefault_tts_prompt()
        persona_context = await self._format_tts_persona_voice_context(event)
        if voice_lang == "zh":
            language_rule = "voice_text 必须是自然中文。"
            visible_rule = "visible_text 仍是最终可见中文文本；如果和 voice_text 一样，可以保持同一句。"
        elif voice_lang == "en":
            language_rule = "voice_text 必须是自然英语，不要夹中文说明。"
            visible_rule = "visible_text 必须保留完整自然中文句子，让用户能看懂这段语音对应什么，但不要写“中文含义：”“对应文本：”这类标题。"
        else:
            language_rule = "voice_text 必须是自然日语，不要夹中文说明；除极短语气词外必须包含假名。"
            visible_rule = "visible_text 必须保留完整自然中文句子，让用户能看懂这段语音对应什么，但不要写“中文含义：”“对应文本：”这类标题。"
        emotion_rule = self._tts_emotion_tag_rule(provider_kind, subject="voice_text 中")
        if not emotion_rule:
            emotion_rule = "voice_text 不要使用方括号情绪标签。"
        probability_allowed = getattr(event, "_private_companion_tts_postprocess_probability_allowed", None)
        if isinstance(probability_allowed, bool):
            probability_hint = "命中，正常判断是否适合语音" if probability_allowed else "未命中，除非你判断用户本轮确实在要求语音，否则应保持纯文本"
        else:
            probability_hint = "未记录，按普通后处理规则判断"
        prompt = f"""
你是 TTS 后处理模型。请判断这条已经生成好的聊天回复是否需要把其中一小段转成语音，并在需要时完成目标语种改写。

目标语种：{lang}
用户本轮原话：
{user_text or "（无）"}
插件规则快判语音请求线索：{tts_signal}{"；命中片段：" + tts_signal_match if tts_signal_match else ""}
本轮自动语音概率线索：{probability_hint}
补充规则：{extra or "无"}
{persona_context}

判断规则：
- 你要自己根据用户原话、规则线索和回复内容判断用户是否在要求或期待语音；规则快判只是线索，不是最终结论。
- 如果用户明确要求语音、想听声音或要求朗读，可以更积极使用语音。
- 如果规则线索为 negative，通常不要使用语音；除非原话里有更强的相反语境，否则 use_tts=false。
- 如果用户没有明确要求，只有在非常适合被听见、情绪很贴近、短句更有表现力时才使用语音。
- 不要为了展示功能而使用语音；普通说明、长解释、信息密集回复应保持纯文字。
- 只选择一小段最适合朗读的内容，不要把整条长回复都转成语音。
- {visible_rule}
- voice_text 是送入 TTS 的朗读文本。{language_rule}
- {emotion_rule}
- voice_text 和 visible_text 都要保持当前人格的说话方式、称呼和距离感。
- 不要添加原回复没有的新信息。

原回复：
{source}

只输出 JSON：
{{
  "use_tts": true/false,
  "reason": "一句话说明",
  "visible_text": "最终可见文本",
  "voice_text": "需要朗读的目标语种文本；不用语音则为空"
}}
""".strip()
        try:
            resp = await self._tts_provider_text_chat(provider, prompt, max_tokens=700, task="tts_postprocess")
            raw = str(getattr(resp, "completion_text", resp) or "").strip()
            extractor = getattr(self, "_extract_json_payload", None)
            payload = extractor(raw) if callable(extractor) else json.loads(raw)
            if not isinstance(payload, dict):
                return ""
            use_tts = bool(payload.get("use_tts"))
            visible = self._sanitize_tts_visible_text(payload.get("visible_text"), max_chars=900) or source
            voice = self._normalize_tts_spoken_text(str(payload.get("voice_text") or ""), provider_kind=provider_kind)
            reason = _single_line(payload.get("reason"), 120)
            if not use_tts or not voice:
                logger.info(
                    "[PrivateCompanion] TTS 后处理判定不使用语音: session=%s reason=%s",
                    _single_line(getattr(event, "unified_msg_origin", ""), 100) or "unknown",
                    reason or "no_voice",
                )
                return ""
            if getattr(self, "tts_voice_language", "ja") != "zh" and not self._tts_visible_text_is_allowed_after_voice(visible):
                visible = source
            logger.info(
                "[PrivateCompanion] TTS 后处理判定使用语音: session=%s reason=%s voice=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 100) or "unknown",
                reason or "use_voice",
                _single_line(voice, 80),
            )
            if getattr(self, "tts_voice_language", "ja") == "zh":
                return f"<tts>{voice}</tts>\n{visible}" if visible and visible != voice else f"<tts>{voice}</tts>"
            return f"<tts>{voice}</tts>\n{visible}"
        except Exception as exc:
            logger.warning("[PrivateCompanion] TTS 后处理判断失败,已保持纯文本: %s", _single_line(exc, 120))
            return ""

    async def _get_tts_conversion_provider(self, event: Any) -> Any:
        provider_id = str(getattr(self, "tts_conversion_provider_id", "") or "").strip()
        if provider_id:
            getter = getattr(self.context, "get_provider_by_id", None)
            if callable(getter):
                try:
                    return getter(provider_id)
                except Exception:
                    pass
        get_using = getattr(self.context, "get_using_provider", None)
        if callable(get_using) and event is not None:
            umo = str(getattr(event, "unified_msg_origin", "") or "")
            try:
                return get_using(umo=umo)
            except TypeError:
                try:
                    return get_using(umo)
                except Exception:
                    return None
            except Exception:
                return None
        return None

    async def _tts_provider_text_chat(self, provider: Any, prompt: str, *, max_tokens: int = 700, task: str = "tts_conversion") -> Any:
        start = time.time()
        provider_id = ""
        provider_id_getter = getattr(self, "_provider_id_from_instance", None)
        if callable(provider_id_getter):
            try:
                provider_id = provider_id_getter(provider)
            except Exception:
                provider_id = ""
        record_usage = getattr(self, "_record_llm_usage", None)
        try:
            try:
                resp = await provider.text_chat(prompt=prompt, max_tokens=max_tokens)
            except TypeError:
                resp = await provider.text_chat(prompt=prompt)
            elapsed_ms = int((time.time() - start) * 1000)
            completion = str(getattr(resp, "completion_text", resp) or "")
            logger.info(
                "[PrivateCompanion] TTS文本模型完成: task=%s provider=%s elapsed=%sms prompt_chars=%s completion_chars=%s",
                task,
                _single_line(provider_id, 80) or "default",
                elapsed_ms,
                len(str(prompt or "")),
                len(completion),
            )
            if callable(record_usage):
                record_usage(
                    provider_id=provider_id,
                    task=task,
                    prompt=prompt,
                    completion=completion,
                    elapsed_ms=elapsed_ms,
                    success=True,
                    resp=resp,
                )
            return resp
        except Exception as exc:
            elapsed_ms = int((time.time() - start) * 1000)
            logger.warning(
                "[PrivateCompanion] TTS文本模型失败: task=%s provider=%s elapsed=%sms prompt_chars=%s error=%s",
                task,
                _single_line(provider_id, 80) or "default",
                elapsed_ms,
                len(str(prompt or "")),
                _single_line(exc, 120),
            )
            if callable(record_usage):
                record_usage(
                    provider_id=provider_id,
                    task=task,
                    prompt=prompt,
                    completion="",
                    elapsed_ms=elapsed_ms,
                    success=False,
                    error=str(exc),
                )
            raise

    def _open_tts_audio_file_local(self, audio_path: str) -> None:
        path = str(audio_path or "").strip()
        if not path:
            return
        volume = max(0, min(100, _safe_int(getattr(self, "tts_local_playback_volume", 35), 35)))
        if sys.platform.startswith("win"):
            self._play_tts_audio_file_windows_silent(path, volume=volume)
            return
        if sys.platform == "darwin":
            subprocess.run(["afplay", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            return
        subprocess.run(["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", "-volume", str(volume), path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

    def _play_tts_audio_file_windows_silent(self, path: str, *, volume: int = 35) -> None:
        if Path(path).suffix.lower() == ".wav":
            path = self._prepare_windows_wav_for_playback(path)
        if self._run_windows_media_player_script(path, use_wpf=True, volume=volume):
            return
        if self._run_windows_media_player_script(path, use_wpf=False, volume=volume):
            return
        raise RuntimeError("Windows 后台播放器均未能播放该音频")

    def _prepare_windows_wav_for_playback(self, path: str) -> str:
        source = Path(path)
        try:
            data = source.read_bytes()
            if len(data) < 44 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
                return path
            riff_size = struct.unpack_from("<I", data, 4)[0]
            pos = 12
            fmt_chunk: bytes | None = None
            data_start = 0
            data_size = 0
            while pos + 8 <= len(data):
                chunk_id = data[pos:pos + 4]
                chunk_size = struct.unpack_from("<I", data, pos + 4)[0]
                chunk_start = pos + 8
                remaining = max(0, len(data) - chunk_start)
                actual_size = min(chunk_size, remaining)
                if chunk_id == b"fmt ":
                    fmt_chunk = data[chunk_start:chunk_start + actual_size]
                elif chunk_id == b"data":
                    data_start = chunk_start
                    data_size = actual_size
                    break
                if chunk_size > remaining:
                    break
                pos = chunk_start + chunk_size + (chunk_size % 2)
            if not fmt_chunk or not data_start or data_size <= 0:
                return path
            if riff_size == len(data) - 8:
                declared_data_size = struct.unpack_from("<I", data, data_start - 4)[0]
                if declared_data_size == data_size:
                    return path
            fixed = source.with_name(f"{source.stem}.playback.wav")
            payload = data[data_start:data_start + data_size]
            riff_payload_size = 4 + (8 + len(fmt_chunk)) + (8 + len(payload))
            with fixed.open("wb") as f:
                f.write(b"RIFF")
                f.write(struct.pack("<I", riff_payload_size))
                f.write(b"WAVE")
                f.write(b"fmt ")
                f.write(struct.pack("<I", len(fmt_chunk)))
                f.write(fmt_chunk)
                f.write(b"data")
                f.write(struct.pack("<I", len(payload)))
                f.write(payload)
            return str(fixed)
        except Exception as exc:
            logger.debug("[PrivateCompanion] 修正 WAV 播放头失败，使用原文件: %s", _single_line(exc, 120))
            return path

    def _run_windows_media_player_script(self, path: str, *, use_wpf: bool, volume: int = 35) -> bool:
        volume = max(0, min(100, int(volume)))
        if use_wpf:
            script = (
                "$p = [System.IO.Path]::GetFullPath($args[0]); "
                "$vol = [Math]::Max(0, [Math]::Min(100, [int]$args[1])); "
                "Add-Type -AssemblyName PresentationCore; "
                "$player = New-Object System.Windows.Media.MediaPlayer; "
                "$player.Volume = $vol / 100.0; "
                "$player.Open([Uri]::new($p)); "
                "$deadline = (Get-Date).AddSeconds(10); "
                "while (-not $player.NaturalDuration.HasTimeSpan -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 50 }; "
                "$duration = if ($player.NaturalDuration.HasTimeSpan) { $player.NaturalDuration.TimeSpan.TotalMilliseconds } else { 5000 }; "
                "$player.Play(); "
                "Start-Sleep -Milliseconds ([Math]::Min([Math]::Max([int]$duration + 300, 800), 90000)); "
                "$player.Close()"
            )
            args = ["powershell", "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass", "-Command", script, path, str(volume)]
        else:
            script = (
                "$p = [System.IO.Path]::GetFullPath($args[0]); "
                "$vol = [Math]::Max(0, [Math]::Min(100, [int]$args[1])); "
                "$player = New-Object -ComObject WMPlayer.OCX; "
                "$player.settings.volume = $vol; "
                "$player.URL = $p; "
                "$player.controls.play(); "
                "$deadline = (Get-Date).AddSeconds(90); "
                "while ($player.playState -notin 1,8 -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 100 }; "
                "$player.close()"
            )
            args = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script, path, str(volume)]
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=95,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode == 0:
            return True
        logger.debug(
            "[PrivateCompanion] Windows 静默播放方式失败: mode=%s code=%s err=%s",
            "wpf" if use_wpf else "wmp",
            result.returncode,
            _single_line(result.stderr or result.stdout, 160),
        )
        return False

    async def _post_tts_live_subtitle(self, text: str) -> None:
        if not bool(getattr(self, "enable_tts_live_subtitle_sync", False)):
            return
        cleaned = _single_line(text, 500)
        if not cleaned:
            return
        url = str(getattr(self, "tts_live_subtitle_url", "") or "").strip() or "http://127.0.0.1:18081/show"

        def _post() -> None:
            payload = json.dumps({"text": cleaned}, ensure_ascii=False).encode("utf-8")
            request = urllib.request.Request(
                url,
                data=payload,
                headers={"Content-Type": "application/json; charset=utf-8"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=2.0) as response:
                response.read(256)

        try:
            await asyncio.to_thread(_post)
            logger.info("[PrivateCompanion] 已同步 TTS 文本到直播打字机字幕: %s", _single_line(cleaned, 80))
        except Exception as exc:
            logger.debug("[PrivateCompanion] TTS 直播字幕同步失败: %s", _single_line(exc, 120))

    async def _after_tts_audio_generated(
        self,
        audio_path: str,
        spoken_text: str,
        *,
        source: str = "",
        subtitle_text: str = "",
    ) -> None:
        is_live_reply = source == "bili_live_auto_reply"
        visible_text = subtitle_text or spoken_text
        subtitle_task = (
            asyncio.create_task(self._post_tts_live_subtitle(visible_text))
            if is_live_reply
            else None
        )
        local_playback_enabled = bool(getattr(self, "enable_tts_local_playback", False))
        live_only = bool(getattr(self, "enable_tts_local_playback_live_only", False))
        should_play_local = local_playback_enabled and (is_live_reply or not live_only)
        if should_play_local:
            interval = max(0.0, float(getattr(self, "tts_local_playback_min_interval_seconds", 0.0) or 0.0))
            now = time.time()
            if interval <= 0 or now - float(getattr(self, "_tts_local_playback_last_at", 0.0) or 0.0) >= interval:
                self._tts_local_playback_last_at = now
                try:
                    await asyncio.to_thread(self._open_tts_audio_file_local, audio_path)
                    logger.info(
                        "[PrivateCompanion] 已触发 TTS 本机播放: source=%s live_only=%s path=%s",
                        source or "unknown",
                        live_only,
                        _single_line(audio_path, 160),
                    )
                except Exception as exc:
                    logger.warning("[PrivateCompanion] TTS 本机播放失败: %s", _single_line(exc, 120))
        if subtitle_task is not None:
            await subtitle_task

    def _tts_visible_fallback_text(self, text: str, fallback_plain: str = "") -> str:
        normalized = self._normalize_tts_tags(str(text or ""))
        visible = re.sub(r"<tts\b[^>]*>.*?</tts>", "", normalized, flags=re.IGNORECASE | re.DOTALL)
        visible = re.sub(TTS_TAG_PATTERN, "", visible).strip()
        if visible:
            return self._sanitize_tts_visible_text(visible)
        fallback = str(fallback_plain or "").strip()
        if fallback:
            return self._sanitize_tts_visible_text(fallback)
        if getattr(self, "tts_voice_language", "ja") == "zh":
            return self._sanitize_tts_visible_text(re.sub(TTS_TAG_PATTERN, "", normalized).strip())
        return ""


    def _find_mimo_tts_plugin(self) -> Any:
        """Find mimo_tts plugin instance via AstrBot star_registry."""
        try:
            from astrbot.core.star.star import star_registry
            for metadata in star_registry:
                name = str(getattr(metadata, "name", "") or "").lower()
                if "mimo_tts" in name:
                    star_cls = getattr(metadata, "star_cls", None)
                    if star_cls is not None and hasattr(star_cls, "synthesize_text"):
                        return star_cls
        except Exception:
            pass
        return None
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
            # Fallback: use mimo_tts plugin directly
            if tts_provider is None:
                tts_provider = self._find_mimo_tts_plugin()
        else:
            event = None
            tts_provider = event_or_provider
            config = config or getattr(self, "config", {}) or {}
            provider_settings = provider_settings or dict((config or {}).get("provider_tts_settings", {}) or {})
        normalized = self._normalize_tts_tags(text)
        hard_block = self._tts_hard_block_reason(event)
        if hard_block:
            fallback_text = self._tts_visible_fallback_text(normalized, fallback_plain)
            logger.info(
                "[PrivateCompanion] TTS强约束已阻止语音生成: session=%s reason=%s text=%s",
                _single_line(self._tts_session_key(event), 80) or "unknown",
                hard_block,
                _single_line(fallback_text or normalized, 120),
            )
            fallback_text = self._sanitize_tts_visible_text(fallback_text)
            return [Plain(fallback_text)] if fallback_text else []
        if not tts_provider:
            fallback_text = self._tts_visible_fallback_text(text, fallback_plain)
            fallback_text = self._sanitize_tts_visible_text(fallback_text)
            if fallback_text:
                logger.warning(
                    "[PrivateCompanion] TTS强化检测到标签但当前会话没有可用 TTS provider,已隐藏朗读文本并按普通文本发送: %s",
                    _single_line(fallback_text, 160),
                )
                return [Plain(fallback_text)]
            return []
        provider_kind = self._tts_provider_kind(tts_provider, provider_settings)
        output: list[Any] = []
        record_failed = False
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
            remaining = self._tts_session_interval_remaining(event)
            if remaining > 0:
                logger.info(
                    "[PrivateCompanion] TTS会话级节流生效,已隐藏朗读文本并保留可见文本: session=%s remain=%.1fs text=%s",
                    _single_line(self._tts_session_key(event), 80) or "unknown",
                    remaining,
                    _single_line(spoken, 80),
                )
                pos = match.end()
                continue
            if self._tts_text_needs_language_conversion(spoken, provider_kind=provider_kind):
                before_convert = spoken
                spoken = await self._convert_text_to_spoken_language(spoken, event, provider_kind=provider_kind)
                if spoken != before_convert:
                    logger.info(
                        "[PrivateCompanion] TTS语音块已按目标语种修正: '%s' -> '%s'",
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
            )
            if record is not None:
                output.append(record)
                self._mark_tts_session_sent(event)
                if getattr(self, "tts_voice_language", "ja") != "zh":
                    next_start = matches[index + 1].start() if index + 1 < len(matches) else len(normalized)
                    visible_after_this_block = normalized[match.end():next_start]
                    if not self._tts_visible_text_is_allowed_after_voice(visible_after_this_block):
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
                                "[PrivateCompanion] TTS语音块已补中文释义: 语音=%s 中文=%s",
                                _single_line(spoken, 80),
                                _single_line(visible_translation, 80),
                            )
                        else:
                            logger.warning(
                                "[PrivateCompanion] TTS语音块缺少中文释义且自动补充失败: 语音=%s",
                                _single_line(spoken, 100),
                            )
            else:
                record_failed = True
                if fallback_plain:
                    logger.warning(
                        "[PrivateCompanion] TTS语音组件生成失败,已隐藏朗读文本并保留可见中文: %s",
                        _single_line(spoken, 120),
                    )
                elif getattr(self, "tts_voice_language", "ja") != "zh":
                    next_start = matches[index + 1].start() if index + 1 < len(matches) else len(normalized)
                    visible_after_this_block = normalized[match.end():next_start]
                    if self._tts_visible_text_is_allowed_after_voice(visible_after_this_block):
                        logger.warning(
                            "[PrivateCompanion] TTS语音组件生成失败,已隐藏朗读文本并保留后置中文: %s",
                            _single_line(spoken, 120),
                        )
                    else:
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
                                "[PrivateCompanion] TTS语音组件生成失败,已改用中文释义文本: %s",
                                _single_line(visible_translation, 120),
                            )
                        else:
                            logger.warning(
                                "[PrivateCompanion] TTS语音组件生成失败且无法得到中文释义,已隐藏朗读文本: %s",
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
        if after and getattr(self, "tts_voice_language", "ja") != "zh" and not self._tts_visible_text_is_allowed_after_voice(after):
            chinese_after = self._tts_chinese_visible_fallback_from_mixed(after)
            if chinese_after:
                logger.warning(
                    "[PrivateCompanion] TTS语音块后置可见文本混有朗读语种,已仅保留中文释义: text=%s",
                    _single_line(chinese_after, 120),
                )
                after = chinese_after
            else:
                logger.warning(
                    "[PrivateCompanion] TTS语音块后置可见文本不是中文释义,已丢弃: text=%s",
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
            and getattr(self, "tts_voice_language", "ja") != "zh"
            and has_record
            and not plain_after_last_record
        ):
            visible_plain = self._mark_tts_visible_plain(fallback_plain)
            if visible_plain is not None:
                output.append(visible_plain)
        if not output:
            fallback_text = self._tts_visible_fallback_text(normalized, fallback_plain)
            fallback_text = self._sanitize_tts_visible_text(fallback_text)
            if fallback_text:
                output.append(Plain(fallback_text))
        return output

    async def _convert_text_to_spoken_language(self, text: str, event: Any, *, provider_kind: str) -> str:
        provider = await self._get_tts_conversion_provider(event) if event is not None else None
        lang = self._tts_language_label()
        persona_context = await self._format_tts_persona_voice_context(event)
        prompt = f"""
把下面内容改写成自然{lang}口语，只输出朗读文本，不要解释。
要求：
- 作品名、人名、专有名词可以按原文保留或自然音译。
- 中文评价、语气词和说明句必须改成{lang}，不要夹中文。
- 保留原回复的情绪，并贴合当前人格的称呼、距离感、口癖和说话方式。
- 不要添加原文没有的新信息。
{persona_context}

原文：
{text}
""".strip()
        try:
            if provider is not None:
                resp = await self._tts_provider_text_chat(provider, prompt, max_tokens=360)
                converted = str(getattr(resp, "completion_text", resp) or "").strip()
                return self._normalize_tts_spoken_text(converted, provider_kind=provider_kind) or text
        except Exception:
            pass
        return text

    async def _tts_record_component(
        self,
        spoken: str,
        tts_provider: Any,
        provider_settings: dict[str, Any],
        config: dict[str, Any],
        *,
        source_text: str = "",
        source: str = "private_companion",
    ) -> Any | None:
        provider_kind = self._tts_provider_kind(tts_provider, provider_settings)
        sanitized = self._sanitize_tts_spoken_text(spoken, provider_kind=provider_kind)
        if not sanitized:
            return None
        if sanitized != spoken:
            logger.info(
                "[PrivateCompanion] TTS强化朗读文本已清洗: '%s' -> '%s'",
                _single_line(spoken, 80),
                _single_line(sanitized, 80),
            )
        try:
            if hasattr(tts_provider, "synthesize_text"):
                # mimo_tts plugin: returns list[Path]
                paths = await tts_provider.synthesize_text(sanitized)
                audio_path = str(paths[0]) if paths else ""
            else:
                # AstrBot framework TTS provider: returns str path
                audio_path = await tts_provider.get_audio(sanitized)
        except Exception as exc:
            logger.warning(
                "[PrivateCompanion] TTS强化生成语音失败: provider=%s error_type=%s error=%s text=%s",
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
                logger.warning("[PrivateCompanion] TTS强化拒绝不安全语音路径: %s", _single_line(audio_path, 160))
                return None
        except Exception as exc:
            logger.warning("[PrivateCompanion] TTS强化检查语音路径失败: %s", _single_line(exc, 120))
            return None
        final_ref = str(audio_path)
        asyncio.create_task(
            self._after_tts_audio_generated(
                str(audio_path),
                sanitized,
                source=source or "private_companion",
            )
        )
        if provider_settings.get("use_file_service", False):
            callback_api_base = str((config or {}).get("callback_api_base", "") or "").strip()
            if callback_api_base:
                try:
                    token = await file_token_service.register_file(str(audio_path))
                    final_ref = f"{callback_api_base}/api/file/{token}"
                except Exception as exc:
                    logger.warning("[PrivateCompanion] TTS强化注册语音文件失败: %s", _single_line(exc, 120))
        try:
            component = Record(file=final_ref, url=final_ref)
        except TypeError:
            try:
                component = Record(file=final_ref)
            except TypeError:
                component = Record.fromFileSystem(str(audio_path))
        self._annotate_tts_record_component(component, sanitized, source_text=source_text or spoken)
        logger.info("[PrivateCompanion] TTS语音组件已生成: %s", self._tts_component_log_note(component))
        return component
