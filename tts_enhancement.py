# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import copy
import inspect
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
from urllib.parse import unquote, urlparse

try:
    from astrbot.api.message_components import Plain, Record
except ImportError:
    from astrbot.api.message_components import Plain
    from astrbot.core.message.components import Record
from astrbot.core import file_token_service
from astrbot.core.message.message_event_result import ResultContentType
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .conversation_injection_plan import (
    PLACEMENT_DYNAMIC_SYSTEM,
    PLACEMENT_TOOL_CONTRACT,
    get_conversation_injection_plan,
)
from .helpers import (
    _has_history_media_marker,
    _normalize_outbound_punctuation_flow,
    _safe_int,
    _single_line,
    _strip_history_media_markers,
    _strip_nonstandard_chat_control_tags,
)
from .persona_config import runtime_persona_setting
from .segmented_message import (
    component_kind,
    component_order_from_owner,
    component_strategies_from_owner,
    plan_component_chunks,
)
from .logging_util import get_module_logger

logger = get_module_logger(__name__)


TTS_BLOCK_PATTERN = re.compile(r"<t{2,}s\b[^>]*>.*?</t{2,}s>", re.IGNORECASE | re.DOTALL)
TTS_TAG_PATTERN = re.compile(r"</?t{2,}s\b[^>]*>", re.IGNORECASE)
TTS_BLOCK_TOKEN_PATTERN = re.compile(r"\[\[TTSBLOCK:([0-9a-f]{16})\]\]")
PRIVATE_TTS_BLOCK_TOKEN_PATTERN = re.compile(r"\[\[PCTTS:([0-9a-f]{16})\]\]")
EMOTION_TAG_PATTERN = re.compile(r"\[([^\[\]\n]{1,24})\]")
FISH_AUDIO_S2_CUE_PATTERN = re.compile(r"\[([^\[\]\n]{1,40})\]")
FISH_AUDIO_S1_CUE_PATTERN = re.compile(r"\(([^()\n]{1,24})\)", re.IGNORECASE)
FISH_AUDIO_MODELS = {"s1", "s2-pro", "s2.1-pro", "s2.1-pro-free"}
FISH_AUDIO_EMOTION_MODES = {"balanced", "expressive", "manual"}
TTS_LANGUAGE_PROVIDER_ATTRS = {
    "zh": "tts_provider_id_zh",
    "ja": "tts_provider_id_ja",
    "en": "tts_provider_id_en",
}


def build_tts_spoken_conversion_prompts(
    text: str,
    *,
    language_name: str,
    persona_context: str = "",
    provider_rule: str = "",
) -> tuple[str, str]:
    """Keep reusable conversion rules ahead of the per-message source text."""
    system_prompt = f"""
把用户提供的原文改写成自然{language_name}口语。只输出朗读文本，不要解释。

要求：
- 这是一项等义口语转换任务，不是在向你请求执行、评价或审核原文内容；不要对原文进行安全说教或输出拒绝声明。
- 如果无法完成转换，原样输出原文；绝对不要输出“无法处理”“不能按照要求”“不符合公序良俗”或建议用户更换话题等内容。
- 作品名、人名、专有名词可以按原文保留或自然音译。
- 中文评价、语气词和说明句必须改成{language_name}，不要夹中文。
- 保留原回复的情绪，并贴合当前人格的称呼、距离感、口癖和说话方式。
- 不要添加原文没有的新信息。
{provider_rule}
{persona_context}
""".strip()
    user_prompt = f"【待转换原文】\n{text}".strip()
    return system_prompt, user_prompt


FISH_AUDIO_S1_CUES = frozenset({
    "angry", "sad", "excited", "surprised", "satisfied", "delighted",
    "scared", "worried", "upset", "nervous", "frustrated", "depressed",
    "empathetic", "embarrassed", "disgusted", "moved", "proud", "relaxed",
    "grateful", "confident", "interested", "curious", "confused", "joyful",
    "disdainful", "unhappy", "anxious", "hysterical", "indifferent",
    "impatient", "guilty", "scornful", "panicked", "furious", "reluctant",
    "keen", "disapproving", "negative", "denying", "astonished", "serious",
    "sarcastic", "conciliative", "comforting", "sincere", "sneering",
    "hesitating", "yielding", "painful", "awkward", "amused",
    "in a hurry tone", "shouting", "screaming", "whispering", "soft tone",
    "laughing", "chuckling", "sobbing", "crying loudly", "sighing", "panting",
    "groaning", "crowd laughing", "background laughter", "audience laughing",
})
FISH_AUDIO_CUE_ALIASES = {
    "开心": "happy", "高兴": "happy", "快乐": "happy", "嬉しい": "happy",
    "喜び": "happy", "难过": "sad", "難過": "sad", "悲しい": "sad",
    "伤心": "sad", "傷心": "sad", "生气": "angry", "生氣": "angry",
    "怒り": "angry", "兴奋": "excited", "興奮": "excited",
    "惊讶": "surprised", "驚訝": "surprised", "驚き": "surprised",
    "平静": "calm", "平靜": "calm", "落ち着く": "calm",
    "紧张": "nervous", "緊張": "nervous", "害怕": "scared", "怖い": "scared",
    "担心": "worried", "擔心": "worried", "心配": "worried",
    "委屈": "upset", "拗ねる": "upset", "沮丧": "frustrated",
    "沮喪": "frustrated", "害羞": "embarrassed", "照れ": "embarrassed",
    "恥ずかしい": "embarrassed", "厌恶": "disgusted", "嫌悪": "disgusted",
    "感动": "moved", "感動": "moved", "骄傲": "proud", "誇らしい": "proud",
    "放松": "relaxed", "放鬆": "relaxed", "感谢": "grateful",
    "感謝": "grateful", "自信": "confident", "好奇": "curious",
    "困惑": "confused", "懐かしい": "nostalgic", "怀旧": "nostalgic",
    "懷舊": "nostalgic", "眠い": "sleepy", "困倦": "sleepy",
    "考え込む": "thoughtful", "沉思": "thoughtful", "耳语": "whispering",
    "耳語": "whispering", "囁き": "whispering", "小声": "soft tone",
    "小聲": "soft tone", "大喊": "shouting", "叫ぶ": "shouting",
    "笑": "laughing", "笑う": "laughing", "轻笑": "chuckling",
    "輕笑": "chuckling", "叹气": "sighing", "嘆氣": "sighing",
    "ため息": "sighing", "叹息": "sighing", "嘆息": "sighing",
    "sigh": "sighing", "哭泣": "sobbing", "すすり泣く": "sobbing",
    "喘气": "panting", "喘氣": "panting", "喘息": "panting",
    "喘ぎ": "panting", "breathing": "panting", "heavy breathing": "panting",
    "gasping": "panting", "呻吟": "groaning", "うめき声": "groaning",
    "あくび": "yawning",
    "哈欠": "yawning", "停顿": "break", "停頓": "break", "間": "break",
}
FISH_AUDIO_AUTO_BLOCKED_EFFECTS = frozenset({"panting", "groaning"})
FISH_AUDIO_EXPLICIT_SIGH_PATTERN = re.compile(
    r"叹(?:了)?(?:一口|口)?气|嘆(?:了)?(?:一口|口)?氣|叹息|嘆息|"
    r"ため息(?:を)?|sigh(?:ed|ing|s)?\b",
    flags=re.IGNORECASE,
)
FISH_AUDIO_S1_ALIAS_OVERRIDES = {
    "happy": "joyful",
    "calm": "relaxed",
    "sleepy": "soft tone",
    "thoughtful": "hesitating",
    "nostalgic": "moved",
    "yawning": "soft tone",
    "break": "hesitating",
}
TTS_VISIBLE_EMOTION_CUES = frozenset(
    str(item).strip().lower()
    for item in (
        set(FISH_AUDIO_S1_CUES)
        | set(FISH_AUDIO_CUE_ALIASES)
        | set(FISH_AUDIO_CUE_ALIASES.values())
        | set(FISH_AUDIO_S1_ALIAS_OVERRIDES)
        | set(FISH_AUDIO_S1_ALIAS_OVERRIDES.values())
        | {
            "happy", "sad", "angry", "calm", "excited", "surprised",
            "nervous", "scared", "worried", "upset", "frustrated",
            "embarrassed", "disgusted", "moved", "proud", "relaxed",
            "grateful", "confident", "curious", "confused", "nostalgic",
            "sleepy", "thoughtful", "yawning", "comforting",
            "affectionate", "shy", "warm", "softly",
        }
    )
    if str(item).strip()
)
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
TTS_EMOTION_PLACEHOLDER_PREFIX = "PCTTSEMOTION"
TTS_VISIBLE_LABEL_PATTERN = re.compile(
    r"^(?:[\s:：|｜-]*(?:中文含义|中文释义|对应文本|原中文文本|显示文本|可见文本|文本|翻译|释义)[\s:：|｜-]*)+"
)
TTS_MARKDOWN_LINK_PATTERN = re.compile(
    r"\[([^\]\r\n]{1,120})\]\(((?:https?://|www\.)[^\s<>()]+)\)",
    re.IGNORECASE,
)
TTS_SPOKEN_URL_PATTERN = re.compile(
    r"(?:https?://|www\.)[^\s<>\[\]{}\"'“”‘’]+",
    re.IGNORECASE,
)
DEFAULT_MIMO_VOICE_CLONE_TOOL_NAME = "mimo_tts_speak"


class _MimoVoiceCloneTtsAdapter:
    """Expose MiMo TTS Voice Clone's public service as an AstrBot-like TTS provider."""

    name = "MiMo TTS Voice Clone plugin"
    provider_type = "tts"
    model_name = "mimo-v2.5-tts-voiceclone"

    def __init__(
        self,
        plugin: Any,
        event: Any,
        *,
        voice_name: str = "",
        style: str = "",
        tool_name: str = DEFAULT_MIMO_VOICE_CLONE_TOOL_NAME,
    ) -> None:
        self.plugin = plugin
        self.event = event
        self.voice_name = str(voice_name or "").strip()
        self.style = str(style or "").strip()
        self.tool_name = str(tool_name or DEFAULT_MIMO_VOICE_CLONE_TOOL_NAME).strip()

    def get_model(self) -> str:
        return self.model_name

    def readiness(self) -> tuple[bool, str]:
        plugin_config = getattr(self.plugin, "plugin_config", None)
        if plugin_config is not None:
            api_key = str(getattr(plugin_config, "api_key", "") or "").strip()
            if not api_key:
                return False, "missing_api_key"

        voice_getter = getattr(self.plugin, "list_available_voices", None)
        if callable(voice_getter):
            try:
                voices = list(voice_getter() or [])
            except Exception:
                voices = []
            if not voices:
                return False, "missing_voice"
            if self.voice_name:
                matched = any(
                    self.voice_name
                    in {
                        str(item.get("id", "") or "").strip(),
                        str(item.get("name", "") or "").strip(),
                    }
                    for item in voices
                    if isinstance(item, dict)
                )
                if not matched:
                    return False, "voice_not_found"
        return True, "ready"

    @staticmethod
    def _supported_kwargs(method: Any, values: dict[str, Any]) -> dict[str, Any]:
        try:
            signature = inspect.signature(method)
        except (TypeError, ValueError):
            return values
        accepts_extra = any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD
            for parameter in signature.parameters.values()
        )
        if accepts_extra:
            return values
        return {key: value for key, value in values.items() if key in signature.parameters}

    def _event_user_id(self) -> str:
        getter = getattr(self.event, "get_sender_id", None)
        if callable(getter):
            try:
                return str(getter() or "").strip()
            except Exception:
                pass
        session_id = self._event_session_id()
        if ":" in session_id:
            return session_id.rsplit(":", 1)[-1].strip()
        return ""

    def _event_session_id(self) -> str:
        return str(getattr(self.event, "unified_msg_origin", "") or "").strip()

    @staticmethod
    async def _await_result(result: Any) -> Any:
        if inspect.isawaitable(result):
            return await result
        return result

    @staticmethod
    def _audio_path_from_result(result: Any) -> str:
        if isinstance(result, (list, tuple)):
            result = result[0] if result else ""
        if isinstance(result, os.PathLike):
            return os.fspath(result)
        if isinstance(result, str):
            return result.strip()
        for attr in ("audio_path", "output_path", "path", "file", "url"):
            value = getattr(result, attr, "")
            if isinstance(value, os.PathLike):
                return os.fspath(value)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    async def get_audio(self, text: str) -> str:
        synthesize = getattr(self.plugin, "synthesize_text", None)
        if callable(synthesize):
            kwargs = self._supported_kwargs(
                synthesize,
                {
                    "voice_name": self.voice_name or None,
                    "context": self.style,
                    "user_id": self._event_user_id(),
                    "group_id": self._event_session_id(),
                    "split": False,
                },
            )
            result = await self._await_result(synthesize(str(text or ""), **kwargs))
            return self._audio_path_from_result(result)

        compatibility = getattr(self.plugin, "text_to_speech", None)
        if callable(compatibility):
            kwargs = self._supported_kwargs(
                compatibility,
                {
                    "voice_name": self.voice_name,
                    "context": self.style,
                    "target_umo": self._event_session_id(),
                    "session_id": self._event_session_id(),
                },
            )
            result = await self._await_result(compatibility(str(text or ""), **kwargs))
            return self._audio_path_from_result(result)
        return ""


class TtsEnhancementMixin:
    """Integrated TTS enhancement for private_companion.

    This is intentionally not a verbatim copy of tts_modify. It keeps the useful
    behavior surface but maps identity and prompts to private_companion concepts.
    """

    def _tts_setting(self, key: str, default: Any = None) -> Any:
        """Read TTS configuration for the active persona without shared mutation."""
        return runtime_persona_setting(self, key, default)

    def _create_tts_background_task(self, operation: Any, *, label: str) -> asyncio.Task | None:
        creator = getattr(self, "_create_lifecycle_background_task", None)
        if callable(creator):
            task = creator(operation, label=label)
            if task is None:
                close = getattr(operation, "close", None)
                if callable(close):
                    close()
            return task
        try:
            task = asyncio.create_task(operation, name=f"private-companion-tts-{label}")
        except RuntimeError:
            close = getattr(operation, "close", None)
            if callable(close):
                close()
            return None

        def consume(done_task: asyncio.Task) -> None:
            try:
                done_task.result()
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.warning(
                    "TTS background task failed: label=%s error=%s",
                    label,
                    _single_line(exc, 160),
                )

        task.add_done_callback(consume)
        return task

    def _load_tts_enhancement_config(self, config: Any) -> None:
        self.enable_tts_enhancement = self._cfg_bool(config, "enable_tts_enhancement", False)
        self.tts_message_scope = self._cfg_str(
            config,
            "tts_message_scope",
            "replies_only",
            "replies_only",
        ).lower()
        if self.tts_message_scope not in {"replies_only", "replies_and_proactive"}:
            self.tts_message_scope = "replies_only"
        raw_synthesis_backend = self._cfg_str(
            config,
            "tts_synthesis_backend",
            "astrbot_provider",
            "astrbot_provider",
        ).lower()
        synthesis_backend_aliases = {
            "astrbot": "astrbot_provider",
            "provider": "astrbot_provider",
            "official": "astrbot_provider",
            "mimo": "mimo_voice_clone",
            "mimotts": "mimo_voice_clone",
            "mimo_plugin": "mimo_voice_clone",
            "plugin": "mimo_voice_clone",
        }
        self.tts_synthesis_backend = synthesis_backend_aliases.get(
            raw_synthesis_backend,
            raw_synthesis_backend,
        )
        if self.tts_synthesis_backend not in {"astrbot_provider", "mimo_voice_clone", "auto"}:
            self.tts_synthesis_backend = "astrbot_provider"
        self.tts_mimo_tool_name = self._cfg_str(
            config,
            "tts_mimo_tool_name",
            DEFAULT_MIMO_VOICE_CLONE_TOOL_NAME,
            DEFAULT_MIMO_VOICE_CLONE_TOOL_NAME,
        )
        self.tts_mimo_voice_name = self._cfg_str(config, "tts_mimo_voice_name", "")
        self.tts_mimo_style_prompt = self._cfg_str(config, "tts_mimo_style_prompt", "")
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
        self.tts_voice_language = self._cfg_str(config, "tts_voice_language", "zh", "zh").lower()
        if self.tts_voice_language not in {"ja", "zh", "en"}:
            self.tts_voice_language = "zh"
        for language, attr in TTS_LANGUAGE_PROVIDER_ATTRS.items():
            setattr(self, attr, self._cfg_str(config, attr, ""))
        self.tts_delivery_mode = self._cfg_str(config, "tts_delivery_mode", "voice_and_text", "voice_and_text").lower()
        if self.tts_delivery_mode not in {"voice_only", "voice_and_text"}:
            self.tts_delivery_mode = "voice_and_text"
        self.tts_foreign_text_mode = self._cfg_str(config, "tts_foreign_text_mode", "translation", "translation").lower()
        if self.tts_foreign_text_mode not in {"original", "translation", "bilingual"}:
            self.tts_foreign_text_mode = "translation"
        self.tts_conversion_provider_id = self._cfg_str(config, "tts_conversion_provider_id", "")
        self.tts_extra_prompt = self._cfg_str(config, "tts_extra_prompt", "")
        self.tts_fishaudio_model = self._cfg_str(config, "tts_fishaudio_model", "auto", "auto").lower()
        if self.tts_fishaudio_model not in {"auto", *FISH_AUDIO_MODELS}:
            self.tts_fishaudio_model = "auto"
        self.tts_fishaudio_emotion_mode = self._cfg_str(
            config,
            "tts_fishaudio_emotion_mode",
            "balanced",
            "balanced",
        ).lower()
        if self.tts_fishaudio_emotion_mode not in FISH_AUDIO_EMOTION_MODES:
            self.tts_fishaudio_emotion_mode = "balanced"
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
        self.tts_trigger_keywords = self._normalize_tts_trigger_keywords(
            self._cfg_raw(config, "tts_trigger_keywords", "")
        )
        self.auto_voice_enabled = self._cfg_bool(config, "auto_voice_enabled", self._cfg_bool(config, "auto_japanese_voice_enabled", False))
        legacy_full_conversion = self._cfg_bool(
            config,
            "auto_voice_full_conversion_enabled",
            self._cfg_bool(config, "auto_japanese_voice_full_conversion_enabled", False),
        )
        raw_conversion_scope = self._cfg_raw(config, "tts_conversion_scope", None)
        self.tts_conversion_scope = (
            str(raw_conversion_scope).strip().lower()
            if raw_conversion_scope not in (None, "")
            else ("full" if legacy_full_conversion else "partial")
        )
        if self.tts_conversion_scope not in {"partial", "full"}:
            self.tts_conversion_scope = "partial"
        self.auto_voice_full_conversion_enabled = self.tts_conversion_scope == "full"
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
        self._tts_local_playback_failures = 0
        self._tts_local_playback_retry_after = 0.0
        self._tts_auto_voice_last_at: dict[str, float] = {}
        if not isinstance(getattr(self, "_tts_session_last_at", None), dict):
            self._tts_session_last_at: dict[str, float] = {}
        self._apply_tts_runtime_overrides()

    @staticmethod
    def _mimo_voice_clone_plugin_from_handler(handler: Any) -> Any | None:
        pending = [handler]
        visited: set[int] = set()
        while pending:
            current = pending.pop(0)
            if current is None or id(current) in visited:
                continue
            visited.add(id(current))
            owner = getattr(current, "__self__", None)
            if owner is not None:
                pending.append(owner)
            pending.extend(list(getattr(current, "args", ()) or ()))
            wrapped = getattr(current, "func", None)
            if wrapped is not None and wrapped is not current:
                pending.append(wrapped)
            if callable(getattr(current, "synthesize_text", None)) or callable(
                getattr(current, "text_to_speech", None)
            ):
                return current
        return None

    def _find_mimo_voice_clone_tts_adapter(self, event: Any) -> Any | None:
        tool_name = _single_line(
            self._tts_setting("tts_mimo_tool_name", DEFAULT_MIMO_VOICE_CLONE_TOOL_NAME),
            120,
        ) or DEFAULT_MIMO_VOICE_CLONE_TOOL_NAME
        try:
            manager = self.context.get_llm_tool_manager()
        except Exception as exc:
            logger.debug("读取 MiMo TTS 工具管理器失败: %s", _single_line(exc, 120))
            return None
        if manager is None:
            return None

        tool = None
        get_tool = getattr(manager, "get_tool", None)
        if callable(get_tool):
            try:
                tool = get_tool(tool_name)
            except Exception:
                tool = None
        if tool is None:
            get_func = getattr(manager, "get_func", None)
            if callable(get_func):
                try:
                    tool = get_func(tool_name)
                except Exception:
                    tool = None
        if tool is None:
            return None

        handler = getattr(tool, "handler", None)
        plugin = self._mimo_voice_clone_plugin_from_handler(handler)
        if plugin is None:
            warning_key = f"{tool_name}:{id(handler)}"
            if getattr(self, "_tts_mimo_bridge_handler_warning_key", "") != warning_key:
                self._tts_mimo_bridge_handler_warning_key = warning_key
                logger.warning(
                    "已找到 MiMo TTS 工具但无法取得插件公开合成服务: tool=%s",
                    tool_name,
                )
            return None
        bridge_key = f"{tool_name}:{id(plugin)}"
        if getattr(self, "_tts_mimo_bridge_key", "") != bridge_key:
            self._tts_mimo_bridge_key = bridge_key
            logger.info(
                "已发现 MiMo TTS Voice Clone: tool=%s service=%s",
                tool_name,
                plugin.__class__.__name__,
            )
        return _MimoVoiceCloneTtsAdapter(
            plugin,
            event,
            voice_name=self._tts_setting("tts_mimo_voice_name", ""),
            style=self._tts_setting("tts_mimo_style_prompt", ""),
            tool_name=tool_name,
        )

    @staticmethod
    def _tts_synthesis_provider_id(provider: Any) -> str:
        if provider is None:
            return ""
        config = getattr(provider, "provider_config", None) or getattr(provider, "config", None) or {}
        if isinstance(config, dict):
            provider_id = _single_line(config.get("id") or config.get("provider_id"), 160)
            if provider_id:
                return provider_id
        for attr in ("provider_id", "id"):
            provider_id = _single_line(getattr(provider, attr, ""), 160)
            if provider_id:
                return provider_id
        meta_getter = getattr(provider, "meta", None)
        if callable(meta_getter):
            try:
                metadata = meta_getter()
                if isinstance(metadata, dict):
                    return _single_line(metadata.get("id"), 160)
                return _single_line(getattr(metadata, "id", ""), 160)
            except Exception:
                pass
        return ""

    def _language_tts_provider(self, event: Any = None) -> Any:
        language = self._tts_voice_language_for_event(event)
        attr = TTS_LANGUAGE_PROVIDER_ATTRS.get(language, "")
        provider_id = _single_line(self._tts_setting(attr, ""), 160) if attr else ""
        if not provider_id:
            return None
        context = getattr(self, "context", None)
        get_all = getattr(context, "get_all_tts_providers", None)
        try:
            providers = list(get_all() or []) if callable(get_all) else []
        except Exception:
            providers = []
        if not providers:
            manager = getattr(context, "provider_manager", None)
            providers = list(getattr(manager, "tts_provider_insts", None) or [])
        for provider in providers:
            if self._tts_synthesis_provider_id(provider) == provider_id:
                return provider
        warning_key = f"{language}:{provider_id}"
        if getattr(self, "_tts_language_provider_warning_key", "") != warning_key:
            self._tts_language_provider_warning_key = warning_key
            logger.warning(
                "当前语种配置的 TTS Provider 不可用,已回退现有合成链路: language=%s provider=%s",
                language,
                provider_id,
            )
        return None

    def _resolve_tts_synthesis_provider(self, event: Any, astrbot_provider: Any = None) -> Any:
        mode = str(
            self._tts_setting("tts_synthesis_backend", "astrbot_provider")
            or "astrbot_provider"
        ).lower()
        if mode != "mimo_voice_clone":
            language_provider = self._language_tts_provider(event)
            if language_provider is not None:
                return language_provider
        if mode == "astrbot_provider" or (mode == "auto" and astrbot_provider is not None):
            return astrbot_provider

        mimo_adapter = self._find_mimo_voice_clone_tts_adapter(event)
        if mimo_adapter is not None:
            if mode == "auto":
                ready, reason = mimo_adapter.readiness()
                if not ready:
                    state_key = f"{mimo_adapter.tool_name}:{reason}"
                    if getattr(self, "_tts_mimo_auto_readiness_log_key", "") != state_key:
                        self._tts_mimo_auto_readiness_log_key = state_key
                        reason_label = {
                            "missing_api_key": "尚未配置 API Key",
                            "missing_voice": "尚未上传可用克隆音色",
                            "voice_not_found": "指定音色不存在或已停用",
                        }.get(reason, reason)
                        logger.info(
                            "自动识别到 MiMo TTS Voice Clone,但暂不接管合成: reason=%s fallback=%s",
                            reason_label,
                            "AstrBot TTS provider" if astrbot_provider is not None else "文字/浏览器朗读",
                        )
                    return astrbot_provider
                ready_key = f"{mimo_adapter.tool_name}:ready:{id(mimo_adapter.plugin)}"
                if getattr(self, "_tts_mimo_auto_ready_log_key", "") != ready_key:
                    self._tts_mimo_auto_ready_log_key = ready_key
                    logger.info(
                        "MiMo TTS Voice Clone 已自动识别并接管语音合成: tool=%s",
                        mimo_adapter.tool_name,
                    )
            return mimo_adapter
        if mode == "mimo_voice_clone" and astrbot_provider is not None:
            fallback_key = _single_line(self._tts_setting("tts_mimo_tool_name", ""), 120) or DEFAULT_MIMO_VOICE_CLONE_TOOL_NAME
            if getattr(self, "_tts_mimo_bridge_fallback_warning_key", "") != fallback_key:
                self._tts_mimo_bridge_fallback_warning_key = fallback_key
                logger.warning(
                    "MiMo TTS Voice Clone 联动不可用,本次回退 AstrBot TTS provider: tool=%s",
                    fallback_key,
                )
        return astrbot_provider

    def _tts_fishaudio_model_for_provider(
        self,
        tts_provider: Any = None,
        provider_settings: dict[str, Any] | None = None,
        *,
        voice_language: str = "",
    ) -> str:
        configured = str(self._tts_setting("tts_fishaudio_model", "auto") or "auto").strip().lower()
        language = self._normalize_tts_voice_language_value(
            voice_language or self._tts_setting("tts_voice_language", "zh")
        ) or "zh"
        language_attr = TTS_LANGUAGE_PROVIDER_ATTRS.get(language, "")
        language_provider_id = (
            _single_line(self._tts_setting(language_attr, ""), 160)
            if language_attr
            else ""
        )
        active_provider_id = self._tts_synthesis_provider_id(tts_provider)
        has_dedicated_language_provider = bool(
            language_provider_id
            and active_provider_id
            and language_provider_id == active_provider_id
        )
        if configured in FISH_AUDIO_MODELS and not has_dedicated_language_provider:
            return configured

        candidates: list[str] = []
        if tts_provider is not None:
            get_model = getattr(tts_provider, "get_model", None)
            if callable(get_model):
                try:
                    candidates.append(str(get_model() or ""))
                except Exception:
                    pass
            for attr in ("model_name", "model"):
                candidates.append(str(getattr(tts_provider, attr, "") or ""))
        if provider_settings:
            candidates.append(str(provider_settings.get("model", "") or ""))
        for candidate in candidates:
            normalized = candidate.strip().lower()
            if normalized in FISH_AUDIO_MODELS:
                return normalized

        api_base = str(getattr(tts_provider, "api_base", "") or "").strip().lower()
        if not api_base and provider_settings:
            api_base = str(provider_settings.get("api_base", "") or "").strip().lower()
        if "api.fish.audio" in api_base or "api.fish-audio.cn" in api_base:
            return "s2.1-pro-free"
        return ""

    def _tts_provider_kind(
        self,
        tts_provider: Any = None,
        provider_settings: dict[str, Any] | None = None,
        *,
        voice_language: str = "",
    ) -> str:
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
            model = self._tts_fishaudio_model_for_provider(
                tts_provider,
                provider_settings,
                voice_language=voice_language,
            )
            return "fishaudio_s1" if model == "s1" else "fishaudio_s2"
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
        if "mimo" in text:
            return "mimo_tts"
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
        tts_provider = self._resolve_tts_synthesis_provider(event, tts_provider)
        return self._tts_provider_kind(
            tts_provider,
            provider_settings,
            voice_language=self._tts_voice_language_for_event(event),
        )

    def _tts_provider_allows_emotion_tags(self, kind: str) -> bool:
        return kind.startswith("fishaudio") or kind == "gsv"

    def _tts_emotion_tag_examples(
        self,
        provider_kind: str = "generic",
        *,
        voice_language: str = "",
    ) -> tuple[str, str]:
        if not self._tts_provider_allows_emotion_tags(provider_kind):
            return "", ""
        if provider_kind == "fishaudio_s1":
            return "(joyful)", "(sad)"
        if provider_kind.startswith("fishaudio"):
            return "[happy]", "[sad]"
        voice_lang = self._normalize_tts_voice_language_value(
            voice_language or self._tts_setting("tts_voice_language", "zh")
        ) or "zh"
        if voice_lang == "zh":
            return "[开心]", "[难过]"
        if voice_lang == "en":
            return "[happy]", "[sad]"
        return "[嬉しい]", "[悲しい]"

    def _tts_emotion_tag_rule(
        self,
        provider_kind: str = "generic",
        *,
        subject: str = "语音块内",
        voice_language: str = "",
    ) -> str:
        positive, negative = self._tts_emotion_tag_examples(
            provider_kind,
            voice_language=voice_language,
        )
        if not positive or not negative:
            return ""
        emotion_mode = str(self._tts_setting("tts_fishaudio_emotion_mode", "balanced") or "balanced").lower()
        if provider_kind.startswith("fishaudio") and emotion_mode == "manual":
            syntax = "英文圆括号" if provider_kind == "fishaudio_s1" else "方括号"
            return f"Fish Audio 手动模式：{subject}只保留输入中已有的合法{syntax}控制词，不要自动新增情绪或语气控制。"
        if provider_kind == "fishaudio_s1":
            base = (
                f"Fish Audio S1 在{subject}只使用官方英文圆括号控制标记，如 {positive}、{negative}、"
                "(whispering)、(sighing)；句级情绪放在句首，每句只选一个主要情绪，避免冲突和滥用。"
            )
            if emotion_mode == "expressive":
                return base + "情绪明确时可再组合语气或音效，总数最多 3 个。"
            return base + "仅在情绪明确时使用 1 个主要情绪，必要时再加 1 个语气控制。"
        if provider_kind.startswith("fishaudio"):
            base = (
                f"Fish Audio S2 在{subject}使用简短方括号自然语言控制，如 {positive}、{negative}；"
                "控制词优先使用朗读语言，并紧贴放在实际生效的短语前。日语可参考官方写法："
                "あれ？[くすくす笑い]知らなかった？私が[強調]胡桃だよ！[興奮]これからも頑張るね。"
                "同一位置只放一个标签，不要在句首连续堆叠多个标签。停顿词、拖音以及“唔、呜、うーん”"
                "只是口语表达，不代表叹气或喘息；原文没有明确的叹气动作时不要使用 [sighing]，"
                "不要自动使用喘息、喘气、呼吸急促、呻吟、panting、breathing 或 groaning。"
            )
            if emotion_mode == "expressive":
                return base + "可随句意在不同短语前稀疏切换表现，但每个短语只选最贴切的一种控制，中性短句不要硬加标签。"
            return base + "本模式仅在情绪明确时使用控制；一条短回复通常只需 1 个，中性短句不要硬加标签。"
        return f"可以在{subject}插入方括号情绪标签，如 {positive}、{negative}。"

    def _tts_language_label(self, event: Any = None, *, voice_language: str = "") -> str:
        language = self._normalize_tts_voice_language_value(voice_language)
        if not language:
            language = self._tts_voice_language_for_event(event)
        return {"ja": "日语", "zh": "中文", "en": "英语"}.get(language, "中文")

    def _normalize_tts_voice_language_value(self, value: Any) -> str:
        text = str(value or "").strip().lower()
        compact = re.sub(r"[\s_\\/-]+", "", text)
        aliases = {
            "ja": "ja",
            "jp": "ja",
            "japanese": "ja",
            "日语": "ja",
            "日語": "ja",
            "日文": "ja",
            "日本语": "ja",
            "日本語": "ja",
            "zh": "zh",
            "cn": "zh",
            "chinese": "zh",
            "中文": "zh",
            "汉语": "zh",
            "漢語": "zh",
            "汉文": "zh",
            "普通话": "zh",
            "国语": "zh",
            "國語": "zh",
            "中国语": "zh",
            "中国語": "zh",
            "en": "en",
            "eng": "en",
            "english": "en",
            "英语": "en",
            "英語": "en",
            "英文": "en",
        }
        return aliases.get(compact, "")

    def _tts_voice_language_for_event(self, event: Any = None) -> str:
        turn_language = self._normalize_tts_voice_language_value(
            getattr(event, "_private_companion_tts_voice_language", "")
            if event is not None
            else ""
        )
        if turn_language:
            return turn_language
        runtime_settings = self.data.get("runtime_settings") if isinstance(getattr(self, "data", None), dict) else None
        if isinstance(runtime_settings, dict):
            runtime_language = self._normalize_tts_voice_language_value(runtime_settings.get("tts_voice_language"))
            if runtime_language:
                return runtime_language
        return self._normalize_tts_voice_language_value(
            self._tts_setting("tts_voice_language", "zh")
        ) or "zh"

    def _detect_turn_tts_voice_language(self, event: Any) -> tuple[str, str]:
        """Recognize an explicit reply-language request without changing saved settings."""
        raw_text = str(getattr(event, "message_str", "") or "").strip()
        if not raw_text or re.search(r"(?:陪伴\s*)?TTS\s*语种", raw_text, flags=re.IGNORECASE):
            return "", ""
        text = re.sub(r"^(?:\s*\[At:\d+\]\s*)+", "", raw_text, flags=re.IGNORECASE)
        text = re.sub(r"^@\S+\s+", "", text).strip().lower()
        compact = re.sub(r"[\s，,。！？!?、；;：:~～]+", "", text)
        if not compact:
            return "", ""
        if re.search(r"(?:不要|别|不用|不必|禁止|取消|停止).{0,6}(?:用|说|讲|回复|回答|朗读|念|读)", compact):
            return "", ""
        if re.search(r"(?:怎么|如何).{0,4}(?:说|表达|翻译)|(?:翻译|译).{0,3}(?:成|为)", compact):
            return "", ""

        language_tokens = {
            "ja": r"(?:日语|日語|日文|日本语|日本語|japanese)",
            "zh": r"(?:中文|汉语|漢語|普通话|国语|國語|中国语|中国語|chinese)",
            "en": r"(?:英语|英語|英文|english)",
        }
        request_before = r"(?:这次|这回|本次|这一条|这一句|接下来)?(?:请|麻烦|可以|能不能|能否|改成|换成|改用|切成|来|直接)?(?:用|说|讲|回复|回答|回我|朗读|念|读)"
        request_after = r"(?:说|讲|回复|回答|回我|朗读|念|读|来一句|来一段|语音回复|语音回答)"
        for language, token in language_tokens.items():
            patterns = (
                rf"{request_before}.{{0,5}}{token}",
                rf"{token}.{{0,5}}{request_after}",
                rf"(?:来|说|讲|回|回复|回答|念|读)(?:一句|一段|一下)?{token}",
            )
            for pattern in patterns:
                match = re.search(pattern, compact, flags=re.IGNORECASE)
                if match:
                    return language, _single_line(match.group(0), 80)

        english_patterns = {
            "ja": r"(?:speak|reply|answer|say|read)(?:it)?(?:in)?(?:japanese)|in(?:japanese)(?:please)?",
            "zh": r"(?:speak|reply|answer|say|read)(?:it)?(?:in)?(?:chinese)|in(?:chinese)(?:please)?",
            "en": r"(?:speak|reply|answer|say|read)(?:it)?(?:in)?(?:english)|in(?:english)(?:please)?",
        }
        for language, pattern in english_patterns.items():
            match = re.search(pattern, compact, flags=re.IGNORECASE)
            if match:
                return language, _single_line(match.group(0), 80)
        japanese_patterns = {
            "ja": r"(?:日本語|日語)で(?:話して|答えて|返事して|読んで|お願い)",
            "zh": r"(?:中国語|中文)で(?:話して|答えて|返事して|読んで|お願い)",
            "en": r"英語で(?:話して|答えて|返事して|読んで|お願い)",
        }
        for language, pattern in japanese_patterns.items():
            match = re.search(pattern, compact, flags=re.IGNORECASE)
            if match:
                return language, _single_line(match.group(0), 80)
        return "", ""

    def _ensure_turn_tts_voice_language(self, event: Any) -> str:
        existing = self._normalize_tts_voice_language_value(
            getattr(event, "_private_companion_tts_voice_language", "")
            if event is not None
            else ""
        )
        if existing or event is None:
            return existing
        language, matched = self._detect_turn_tts_voice_language(event)
        if not language:
            return ""
        try:
            setattr(event, "_private_companion_tts_voice_language", language)
            setattr(event, "_private_companion_tts_voice_language_match", matched)
        except Exception:
            return ""
        logger.info(
            "已识别本轮 TTS 语种要求: session=%s language=%s match=%s",
            _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            language,
            matched,
        )
        return language

    def _apply_tts_runtime_overrides(self) -> None:
        settings = self.data.get("runtime_settings") if isinstance(getattr(self, "data", None), dict) else None
        if not isinstance(settings, dict):
            return
        lang = self._normalize_tts_voice_language_value(settings.get("tts_voice_language"))
        if not lang:
            lang = self._normalize_tts_voice_language_value(self._tts_setting("tts_voice_language", "zh"))
        # Runtime overrides are stored in the active persona data document and
        # read by _tts_voice_language_for_event; do not mutate shared attrs.

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
            if not bool(getattr(self, "enable_multi_persona_mode", False)) and hasattr(self, "tts_voice_language"):
                config_value = ""
                config = getattr(self, "config", {})
                if isinstance(config, dict):
                    config_value = config.get("tts_voice_language", "")
                    for group in config.values():
                        if isinstance(group, dict) and "tts_voice_language" in group:
                            config_value = group["tts_voice_language"]
                            break
                self.tts_voice_language = self._normalize_tts_voice_language_value(
                    config_value or self._tts_setting("tts_voice_language", "zh")
                ) or "zh"
            self._save_data_sync(sections={"runtime_settings"})
            return f"已恢复 TTS 语音语种为配置页设置：{self._tts_language_label()}。"
        lang = self._normalize_tts_voice_language_value(text)
        if not lang:
            return "没认出这个 TTS 语种。可用：日语 / 中文 / 英语；例如：陪伴 TTS语种 日语。"
        settings["tts_voice_language"] = lang
        if not bool(getattr(self, "enable_multi_persona_mode", False)) and hasattr(self, "tts_voice_language"):
            self.tts_voice_language = lang
        self._save_data_sync(sections={"runtime_settings"})
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

    @staticmethod
    def _strip_visible_tts_emotion_cues(text: Any) -> str:
        """Remove known synthesis cues while preserving ordinary bracketed text."""
        source = str(text or "")

        def strip_square(match: re.Match[str]) -> str:
            label = re.sub(r"\s+", " ", str(match.group(1) or "").strip()).lower()
            return "" if label in TTS_VISIBLE_EMOTION_CUES else match.group(0)

        source = EMOTION_TAG_PATTERN.sub(strip_square, source)

        def strip_parenthesized(match: re.Match[str]) -> str:
            label = re.sub(r"\s+", " ", str(match.group(3) or "").strip()).lower()
            if label not in TTS_VISIBLE_EMOTION_CUES:
                return match.group(0)
            return f"{match.group(1)}{match.group(2)}"

        source = re.sub(
            r"(^|[。！？.!?\n])(\s*)\(([^()\n]{1,40})\)",
            strip_parenthesized,
            source,
        )
        return source

    def _sanitize_tts_visible_text(self, text: Any, *, max_chars: int = 800) -> str:
        cleaned = _strip_history_media_markers(str(text or ""))
        cleaned = self._strip_any_tts_markup(cleaned)
        cleaned = self._strip_visible_tts_emotion_cues(cleaned)
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

    @staticmethod
    def _tts_complete_text_limit(text: Any, minimum: int = 1600) -> int:
        return max(int(minimum), len(str(text or "")) + 32)

    def _mark_tts_visible_plain(self, text: Any, *, max_chars: int = 800) -> Plain | None:
        visible = self._sanitize_tts_visible_text(text, max_chars=max_chars)
        if not visible:
            return None
        comp = Plain(visible)
        try:
            object.__setattr__(comp, "_private_companion_tts_visible_text", True)
        except Exception:
            pass
        return comp

    def _tts_proactive_segment_visible_policy(self, event: Any) -> tuple[str, bool, str, bool]:
        try:
            result = event.get_result()
        except Exception:
            result = None
        chain = list(getattr(result, "chain", []) or []) if result is not None else []
        raw_full_text = getattr(event, "_private_companion_proactive_full_text", "")
        full_text = _single_line(
            raw_full_text,
            self._tts_complete_text_limit(raw_full_text, 1200),
        )
        try:
            index = max(0, int(getattr(event, "_private_companion_proactive_segment_index", 0) or 0))
        except Exception:
            index = 0
        try:
            count = max(1, int(getattr(event, "_private_companion_proactive_segment_count", 1) or 1))
        except Exception:
            count = 1
        if not full_text:
            for comp in chain:
                raw_full_text = getattr(comp, "_private_companion_proactive_full_text", "")
                full_text = _single_line(
                    raw_full_text,
                    self._tts_complete_text_limit(raw_full_text, 1200),
                )
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
            return "", False, "", False
        if count <= 1:
            return self._sanitize_tts_visible_text(full_text, max_chars=1000), False, full_text, False
        if index > 0:
            # Automatic TTS is decided once for the whole proactive message.
            # Later segments must remain text in both partial and full modes.
            return "", False, "", True
        if self._tts_setting("tts_conversion_scope", "partial") == "full":
            first_visible = ""
            for comp in chain:
                if isinstance(comp, Plain):
                    first_visible = self._sanitize_tts_visible_text(
                        getattr(comp, "text", ""),
                        max_chars=1000,
                    )
                    if first_visible:
                        break
            return first_visible, False, full_text, False
        return "", False, "", False

    def _protect_tts_blocks_for_framework(self, text: str, event: Any) -> str:
        normalized = self._normalize_tts_tags(str(text or ""))
        if "<tts>" not in normalized.lower() or "</tts>" not in normalized.lower():
            # 清除可能存在的旧 TTS tokens，避免模型切换后残留内容被恢复
            try:
                setattr(event, "_private_companion_tts_block_tokens", {})
            except Exception:
                pass
            return normalized
        # 清除之前模型响应留下的旧 tokens，防止模型切换后旧的 TTS 内容被错误恢复
        protected: dict[str, str] = {}
        try:
            setattr(event, "_private_companion_tts_block_tokens", protected)
        except Exception:
            pass

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
        source = _strip_nonstandard_chat_control_tags(source)
        source = PRIVATE_TTS_BLOCK_TOKEN_PATTERN.sub("", source)
        source = TTS_BLOCK_TOKEN_PATTERN.sub("", source)
        source = re.sub(r"(?:^|[\s\r\n])([。！？!?，,、；;：:~～…]+)(?=\s|$)", " ", source)
        source = re.sub(r"\s{2,}", " ", source)
        source = re.sub(r"^\s*[。！？!?，,、；;：:~～…]+\s*", "", source)
        source = source.lstrip(" \t\r\n。！？!?，,、；;：:~～…")
        return source.strip()

    @staticmethod
    def _tts_record_ref_aliases(value: Any) -> list[str]:
        raw = str(value or "").strip()
        if not raw:
            return []
        aliases = [raw]
        decoded = unquote(raw).strip()
        normalized = decoded.replace("\\", "/")
        if normalized:
            aliases.append(f"normalized:{normalized.casefold()}")
        try:
            parsed = urlparse(decoded)
        except Exception:
            parsed = None
        path_text = unquote(parsed.path).replace("\\", "/") if parsed and parsed.scheme else normalized
        basename = path_text.rsplit("/", 1)[-1].strip()
        stem = basename.rsplit(".", 1)[0] if "." in basename else basename
        # Generated TTS names are normally random/unique. Avoid broad aliases such as voice.wav.
        if basename and len(stem) >= 8:
            aliases.append(f"basename:{basename.casefold()}")
        return list(dict.fromkeys(alias for alias in aliases if alias))

    def _tts_record_refs(self, component: Any) -> list[str]:
        raw_refs: list[Any] = []

        def add_source(source: Any) -> None:
            if isinstance(source, dict):
                for key in ("file", "url", "path"):
                    if source.get(key):
                        raw_refs.append(source.get(key))
                data = source.get("data")
                if isinstance(data, dict) and data is not source:
                    add_source(data)
                return
            for attr in ("file", "url", "path"):
                value = getattr(source, attr, "")
                if value:
                    raw_refs.append(value)
            try:
                data = getattr(source, "data", None)
            except Exception:
                data = None
            if isinstance(data, dict):
                add_source(data)

        add_source(component)
        refs: list[str] = []
        for raw_ref in raw_refs:
            for alias in self._tts_record_ref_aliases(raw_ref):
                if alias not in refs:
                    refs.append(alias)
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
                    _single_line(item.get("spoken"), 500),
                    _single_line(item.get("source"), 500),
                )
        return "", ""

    def _annotate_tts_record_component(self, component: Any, spoken_text: str, *, source_text: str = "") -> Any:
        spoken = _single_line(self._strip_any_tts_markup(spoken_text), 500)
        source = _single_line(self._strip_any_tts_markup(source_text), 500)
        try:
            object.__setattr__(component, "_private_companion_tts_spoken_text", spoken)
            object.__setattr__(component, "_private_companion_tts_source_text", source)
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

    @staticmethod
    def _fishaudio_canonical_cue(label: str, *, s1: bool) -> str:
        raw = re.sub(r"\s+", " ", str(label or "").strip())
        if not raw:
            return ""
        if s1:
            canonical = FISH_AUDIO_CUE_ALIASES.get(raw, raw.lower() if raw.isascii() else raw)
            canonical = FISH_AUDIO_S1_ALIAS_OVERRIDES.get(canonical, canonical)
            return canonical if canonical in FISH_AUDIO_S1_CUES else ""
        # S2 officially accepts concise natural-language controls. Keep CJK labels
        # in the spoken language instead of needlessly translating them to English.
        canonical = raw.lower() if raw.isascii() else raw
        if (
            len(canonical) > 40
            or re.fullmatch(r"[\d\W_]+", canonical, flags=re.UNICODE)
            or re.search(r"(?:https?://|www\.|<|>|=|\{|\}|\[|\])", canonical, flags=re.IGNORECASE)
            or re.match(r"^(?:at|qq|pctts|ttsblock)\s*:", canonical, flags=re.IGNORECASE)
        ):
            return ""
        return canonical

    @staticmethod
    def _fishaudio_cue_effect_key(label: str) -> str:
        raw = re.sub(r"\s+", " ", str(label or "").strip())
        if not raw:
            return ""
        return FISH_AUDIO_CUE_ALIASES.get(raw, raw.lower() if raw.isascii() else raw)

    def _fishaudio_auto_cue_allowed(self, label: str, *, context: str) -> tuple[bool, str]:
        if self._fishaudio_emotion_mode() == "manual":
            return True, ""
        effect = self._fishaudio_cue_effect_key(label)
        if effect in FISH_AUDIO_AUTO_BLOCKED_EFFECTS:
            return False, "high_impact_breath_effect"
        if effect == "sighing" and not FISH_AUDIO_EXPLICIT_SIGH_PATTERN.search(str(context or "")):
            return False, "sigh_without_explicit_action"
        return True, ""

    def _normalize_fishaudio_s2_cues(self, text: str) -> str:
        source = str(text or "")
        segments = re.split(r"([。！？.!?]+)", source)
        normalized: list[str] = []
        removed_cues: list[str] = []
        mode = self._fishaudio_emotion_mode()
        for segment in segments:
            if not segment or re.fullmatch(r"[。！？.!?]+", segment):
                normalized.append(segment)
                continue
            cue_count = 0
            last_cue_end = -1
            cue_run_has_kept = False
            segment_context = FISH_AUDIO_S2_CUE_PATTERN.sub("", segment)

            def repl(match: re.Match[str]) -> str:
                nonlocal cue_count, last_cue_end, cue_run_has_kept
                if match.end() < len(segment) and segment[match.end()] == "(":
                    return ""
                adjacent = last_cue_end >= 0 and not segment[last_cue_end:match.start()].strip()
                if not adjacent:
                    cue_run_has_kept = False
                last_cue_end = match.end()
                canonical = self._fishaudio_canonical_cue(match.group(1), s1=False)
                if not canonical or cue_count >= 3:
                    return ""
                allowed, reason = self._fishaudio_auto_cue_allowed(
                    canonical,
                    context=segment_context,
                )
                if not allowed:
                    removed_cues.append(f"{canonical}:{reason}")
                    return ""
                if mode != "manual" and adjacent and cue_run_has_kept:
                    removed_cues.append(f"{canonical}:stacked")
                    return ""
                cue_count += 1
                cue_run_has_kept = True
                return f"[{canonical}]"

            normalized_segment = FISH_AUDIO_S2_CUE_PATTERN.sub(repl, segment)
            normalized_segment = re.sub(r"\[[^\[\]\n]{41,200}\]", "", normalized_segment)
            normalized.append(normalized_segment)
        if removed_cues:
            logger.info(
                "FishAudio 自动控制已移除高风险或堆叠标签: mode=%s cues=%s",
                mode,
                ",".join(removed_cues[:8]),
            )
        return "".join(normalized)

    def _normalize_fishaudio_s1_cues(self, text: str) -> str:
        source = str(text or "")

        def repl(match: re.Match[str]) -> str:
            canonical = self._fishaudio_canonical_cue(match.group(1), s1=True)
            return f"({canonical})" if canonical else ""

        source = FISH_AUDIO_S1_CUE_PATTERN.sub(repl, source)
        source = FISH_AUDIO_S2_CUE_PATTERN.sub(repl, source)
        return source

    def _fishaudio_emotion_mode(self) -> str:
        mode = str(self._tts_setting("tts_fishaudio_emotion_mode", "balanced") or "balanced").strip().lower()
        return mode if mode in FISH_AUDIO_EMOTION_MODES else "balanced"

    @staticmethod
    def _fishaudio_context_emotion_cues(text: str, *, mode: str) -> list[str]:
        source = re.sub(r"\s+", " ", str(text or "")).strip().lower()
        if not source:
            return []

        emotion_rules = (
            ("angry", ((r"气死|氣死|生气|生氣|火大|滚开|滾開|混蛋|ふざけ|むかつ|怒って|怒る", 4),)),
            ("upset", (
                (r"笨蛋|ばか|バカ|都说了|都說了|怎么还|怎麼還|不许|不許|不准|烦死|煩死|讨厌啦|討厭啦|やめて|って言った|しつこい|ひどい", 3),
                (r"(?:^|[\s，,。.!！?？…~～])(哼|ふん|むぅ|まったく)(?:[\s，,。.!！?？…~～]|$)", 1),
            )),
            ("sad", ((r"难过|難過|伤心|傷心|想哭|泪|淚|悲しい|つらい|寂しい|泣きたい", 3),)),
            ("worried", ((r"担心|擔心|小心一点|小心一點|没事吧|沒事吧|还好吗|還好嗎|心配|大丈夫[？?]|気をつけ", 3),)),
            ("surprised", ((r"竟然|居然|真的吗|真的嗎|真的假的|没想到|沒想到|えっ|ええっ|まさか|本当[？?]", 3),)),
            ("excited", ((r"好期待|太棒了|好耶|冲冲冲|衝衝衝|迫不及待|楽しみ|わくわく|最高|やった", 3),)),
            ("happy", ((r"开心|開心|高兴|高興|喜欢你|喜歡你|爱你|愛你|太好了|嬉しい|楽しい|大好き|よかった", 3),)),
            ("grateful", ((r"谢谢你|謝謝你|感谢|感謝|多亏你|多虧你|ありがとう|助かった", 3),)),
            ("comforting", ((r"别怕|別怕|没关系|沒關係|我陪你|我在呢|慢慢来|慢慢來|そばにいる|無理しないで|安心して", 3),)),
            ("sleepy", ((r"好困|困死|想睡|睡着|睡著|打哈欠|眠い|眠たい|寝たい|あくび", 3),)),
            ("embarrassed", ((r"害羞|羞死|脸红|臉紅|不好意思|别看|別看|被发现|被發現|恥ずか|照れ|顔が赤|見ないで", 3),)),
        )
        scores: dict[str, int] = {}
        for cue, patterns in emotion_rules:
            scores[cue] = sum(weight for pattern, weight in patterns if re.search(pattern, source, flags=re.IGNORECASE))

        playful_complaint = bool(re.search(r"嘛|啦|呀|哦|呜|嗚|唔|じゃん|だもん|バカ|ばか|[~～]", source))
        if scores.get("upset", 0) >= 3 and scores.get("angry", 0) < scores["upset"] and playful_complaint:
            scores["embarrassed"] = max(scores.get("embarrassed", 0), 2)

        priority = (
            "angry", "upset", "sad", "worried", "surprised", "excited",
            "happy", "grateful", "comforting", "sleepy", "embarrassed",
        )
        primary_candidates = [cue for cue in priority if scores.get(cue, 0) >= 3]
        primary = max(primary_candidates, key=lambda cue: (scores[cue], -priority.index(cue))) if primary_candidates else ""

        tone_rules = (
            ("sighing", FISH_AUDIO_EXPLICIT_SIGH_PATTERN.pattern),
            ("whispering", r"悄悄|小声|小聲|耳边|耳邊|こっそり|囁|小声で"),
            ("laughing", r"哈哈|嘿嘿|嘻嘻|笑死|ふふ|はは|あはは|笑っ"),
            ("sobbing", r"哭了|哭泣|抽泣|泣いて|すすり泣|しくしく"),
            ("soft tone", r"晚安|慢慢说|慢慢說|轻声|輕聲|おやすみ|優しく|そっと"),
        )
        tones = [cue for cue, pattern in tone_rules if re.search(pattern, source, flags=re.IGNORECASE)]
        # This fallback can only prefix the whole utterance, so it deliberately
        # chooses one control. Rich S2 expression is produced clause by clause by
        # the conversion model; stacking inferred controls here causes breathing
        # artefacts and conflicts with Fish Audio's official placement examples.
        if tones:
            return tones[:1]
        return [primary] if primary else []

    def _apply_fishaudio_emotion_control(
        self,
        text: str,
        *,
        provider_kind: str,
        source_text: str = "",
    ) -> tuple[str, list[str]]:
        spoken = str(text or "").strip()
        if not spoken or not provider_kind.startswith("fishaudio"):
            return spoken, []
        mode = self._fishaudio_emotion_mode()
        if mode == "manual":
            return spoken, []

        s1 = provider_kind == "fishaudio_s1"
        cue_pattern = FISH_AUDIO_S1_CUE_PATTERN if s1 else FISH_AUDIO_S2_CUE_PATTERN
        for match in cue_pattern.finditer(spoken):
            if self._fishaudio_canonical_cue(match.group(1), s1=s1):
                return spoken, []

        context = f"{source_text}\n{spoken}".strip()
        context = FISH_AUDIO_S2_CUE_PATTERN.sub("", context)
        context = FISH_AUDIO_S1_CUE_PATTERN.sub("", context)
        inferred = self._fishaudio_context_emotion_cues(context, mode=mode)
        canonical: list[str] = []
        for cue in inferred:
            normalized = self._fishaudio_canonical_cue(cue, s1=s1)
            if normalized and normalized not in canonical:
                canonical.append(normalized)
        if not canonical:
            return spoken, []

        if s1:
            prefix = "".join(f"({cue})" for cue in canonical)
        else:
            prefix = "".join(f"[{cue}]" for cue in canonical)
        return f"{prefix}{spoken}", canonical

    def _strip_or_keep_emotion_tags(self, text: str, *, provider_kind: str) -> str:
        if provider_kind == "fishaudio_s1":
            return self._normalize_fishaudio_s1_cues(text)
        if provider_kind.startswith("fishaudio"):
            return self._normalize_fishaudio_s2_cues(text)
        if self._tts_provider_allows_emotion_tags(provider_kind):
            return str(text or "")
        return EMOTION_TAG_PATTERN.sub("", str(text or "")).strip()

    def _normalize_tts_spoken_text(self, text: str, *, provider_kind: str) -> str:
        cleaned = self._normalize_tts_tags(text)
        cleaned = self._strip_or_keep_emotion_tags(cleaned, provider_kind=provider_kind)
        cleaned = re.sub(r"</?tts>", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"</?t{2,}s\b[^>]*>", "", cleaned, flags=re.IGNORECASE)
        return _single_line(cleaned, 2000)

    @staticmethod
    def _has_meaningful_tts_content(text: str) -> bool:
        content = str(text or "").strip()
        if not content:
            return False
        simplified = EMOTION_TAG_PATTERN.sub("", content)
        simplified = re.sub(r"[（(][^（()]*[）)]", "", simplified)
        simplified = re.sub(r"</?(?:pc[_-]?tts|t{2,}s)\b[^>]*>", "", simplified, flags=re.IGNORECASE)
        simplified = re.sub(
            r"[\s\.,，。!！?？~～…:：;；、\-—_()（）\[\]{}<>《》'\"“”‘’`|｜/\\]+",
            "",
            simplified,
        )
        return bool(simplified)

    @staticmethod
    def _tts_text_is_provider_safety_refusal(text: str) -> bool:
        compact = re.sub(r"\s+", "", str(text or "")).lower()
        if not compact:
            return False
        normalized = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", compact)
        exact_refusals = {
            "contentpolicyviolation",
            "safetyfilter",
            "违反内容安全策略",
            "违反内容政策",
            "违反社区准则",
        }
        if normalized in exact_refusals:
            return True
        policy_markers = (
            "您的请求包含低俗色情内容",
            "你的描述包含低俗色情",
            "您的描述包含低俗色情",
            "不符合公序良俗",
            "违反内容安全策略",
            "违反内容政策",
            "违反社区准则",
            "contentpolicyviolation",
            "safetyfilter",
        )
        refusal_markers = (
            "已被平台拒绝",
            "请求被拒绝",
            "无法处理该请求",
            "无法生成",
            "无法提供",
            "不能处理该请求",
            "不能按照你的要求进行处理",
            "不能按照您的要求进行处理",
            "无法按照你的要求进行处理",
            "无法按照您的要求进行处理",
            "不能生成",
            "不能提供",
            "requestrejected",
            "requestwasrejected",
            "cannotcomply",
            "unabletocomply",
            "wasblocked",
            "hasbeenblocked",
        )
        return any(marker in compact for marker in policy_markers) and any(
            marker in compact for marker in refusal_markers
        )

    def _drop_tts_provider_safety_blocks(self, text: str) -> tuple[str, bool]:
        """Remove only provider safety refusals that were incorrectly wrapped as voice."""
        source = str(text or "")
        removed = False

        def _replace(match: re.Match[str]) -> str:
            nonlocal removed
            if not self._tts_text_is_provider_safety_refusal(match.group(1)):
                return match.group(0)
            removed = True
            return ""

        cleaned = re.sub(
            r"<tts\b[^>]*>(.*?)</tts>",
            _replace,
            source,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if removed:
            cleaned = re.sub(r"\n[ \t]*\n[ \t]*\n+", "\n\n", cleaned).strip()
        return cleaned, removed

    def _sanitize_tts_spoken_text(self, text: str, *, provider_kind: str) -> str:
        """Clean text immediately before get_audio, scoped to TTS强化 only."""
        if not text:
            return ""
        if self._tts_text_is_provider_safety_refusal(text):
            return ""
        source = str(text)
        source = TTS_MARKDOWN_LINK_PATTERN.sub(lambda match: match.group(1).strip(), source)

        def _remove_spoken_url(match: re.Match[str]) -> str:
            value = match.group(0)
            trailing = ""
            while value and value[-1] in ".,!?;:，。！？；：":
                trailing = value[-1] + trailing
                value = value[:-1]
            return trailing

        source = TTS_SPOKEN_URL_PATTERN.sub(_remove_spoken_url, source)
        source = self._strip_or_keep_emotion_tags(source, provider_kind=provider_kind)
        protected: dict[str, str] = {}
        if self._tts_provider_allows_emotion_tags(provider_kind):
            def _protect_emotion(match: re.Match[str]) -> str:
                token = f"{TTS_EMOTION_PLACEHOLDER_PREFIX}{len(protected)}TOKEN"
                protected[token] = match.group(0)
                return token

            if provider_kind == "fishaudio_s1":
                source = FISH_AUDIO_S1_CUE_PATTERN.sub(_protect_emotion, source)
            elif provider_kind.startswith("fishaudio"):
                source = FISH_AUDIO_S2_CUE_PATTERN.sub(_protect_emotion, source)
            else:
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

        source = re.sub(r"([^\d])\1{2,}", lambda m: m.group(1) * 2, source)
        source = re.sub(r'[""\u201c\u201d]\s*[""\u201c\u201d]', "", source)
        source = re.sub(r"[''\u2018\u2019]\s*[''\u2018\u2019]", "", source)
        source = re.sub(r"[「」『』【】\[\]]\s*[「」『』【】\[\]]", "", source)
        source = re.sub(r"[,，、;；]\s*(?=[,，、;；\s])", "", source)
        source = re.sub(r"[:：]\s*(?=$|[。！？!?])", "", source)
        source = re.sub(r"[,，、;；]\s*$", "", source)
        source = re.sub(r"^\s*[,，、;；]\s*", "", source)
        source = re.sub(r"\s+", " ", source).strip()

        for token, original in protected.items():
            source = source.replace(token, original)
        source = source.strip()
        if not self._has_meaningful_tts_content(source):
            return ""
        return source

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
        interval = float(self._tts_setting("tts_session_min_interval_seconds", 0.0) or 0.0)
        scope = self._tts_event_scope_kind(event)
        override = None
        if scope == "private":
            override = self._tts_setting("tts_private_min_interval_seconds", -1.0)
        elif scope == "group":
            override = self._tts_setting("tts_group_min_interval_seconds", -1.0)
        try:
            override_value = float(override)
        except (TypeError, ValueError):
            override_value = -1.0
        return max(0.0, override_value if override_value >= 0 else interval)

    def _tts_effective_trigger_probability(self, event: Any) -> float:
        probability = float(self._tts_setting("tts_trigger_probability", 1.0) or 0.0)
        scope = self._tts_event_scope_kind(event)
        override = None
        if scope == "private":
            override = self._tts_setting("tts_private_trigger_probability", -0.01)
        elif scope == "group":
            override = self._tts_setting("tts_group_trigger_probability", -0.01)
        try:
            override_value = float(override)
        except (TypeError, ValueError):
            override_value = -0.01
        return max(0.0, min(1.0, override_value if override_value >= 0 else probability))

    def _tts_session_interval_remaining(self, event: Any) -> float:
        if self._tts_setting("tts_frequency_control_mode", "global") == "legacy":
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
            self._tts_setting("tts_frequency_control_mode", "global") != "legacy"
            and self._tts_setting("tts_generation_mode", "fast_tag") == "fast_tag"
            and self._tts_setting("tts_constraint_mode", "weak") == "strong"
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

    def _normalize_tts_trigger_keywords(self, raw: Any) -> tuple[str, ...]:
        """Normalize the optional keyword list used to opt a turn into TTS."""
        if isinstance(raw, (list, tuple, set)):
            values = raw
        else:
            values = re.split(r"[,，;；\n\r]+", str(raw or ""))
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            keyword = str(value or "").strip()
            if not keyword:
                continue
            folded = keyword.casefold()
            if folded in seen:
                continue
            seen.add(folded)
            normalized.append(keyword[:80])
            if len(normalized) >= 50:
                break
        return tuple(normalized)

    def _event_tts_keyword_match(self, event: Any) -> str:
        if event is None:
            return ""
        text = str(getattr(event, "message_str", "") or "")
        if not text:
            return ""
        configured = self._tts_setting("tts_trigger_keywords", None)
        keywords = self._normalize_tts_trigger_keywords(
            configured if configured is not None else getattr(self, "tts_trigger_keywords", "")
        )
        folded_text = text.casefold()
        for keyword in keywords:
            if keyword.casefold() in folded_text:
                return keyword
        return ""

    def _tts_functional_command_reason(self, event: Any) -> str:
        """Identify command turns whose functional output should stay readable by default."""
        if event is None:
            return ""
        if bool(getattr(event, "is_command", False)):
            return "event_command"
        if bool(getattr(event, "is_admin_command", False)):
            return "admin_command"

        raw_text = str(getattr(event, "message_str", "") or "").strip()
        if not raw_text:
            return ""
        command_checker = getattr(self, "_message_debounce_command_text", None)
        if callable(command_checker):
            try:
                if command_checker(event, raw_text):
                    return "command_text"
            except Exception:
                pass

        cleaned = re.sub(r"^(?:\s*\[At:\d+\]\s*)+", "", raw_text, flags=re.IGNORECASE).lstrip()
        cleaned = re.sub(r"^@\S+\s+", "", cleaned).lstrip()
        if cleaned.startswith(("/", "／", "!", "！", "#")) and re.search(
            r"[\w\u4e00-\u9fff]",
            cleaned[1:],
        ):
            return "command_prefix"
        if cleaned.startswith(("陪伴", "私聊陪伴", "主动陪伴", "陪伴群", "群陪伴", "群聊陪伴")):
            return "companion_command"
        return ""

    def _event_tts_request_signal(self, event: Any) -> tuple[str, str, str]:
        raw_text = str(getattr(event, "message_str", "") or "").strip()
        if bool(getattr(event, "_private_companion_tts_forced_by_message_scope", False)):
            return "positive", "configured_proactive_scope", raw_text
        text = raw_text.lower()
        if not text:
            return "uncertain", "", ""
        compact = re.sub(r"\s+", "", text)
        retry_patterns = (
            r"(语音|tts|朗读|念出来|读出来).{0,8}(标签|标记)?.{0,6}(漏了|漏掉|漏发|没发成|没发出来|没出去|没生成|没合成|失效|失败)",
            r"(漏了|漏掉|漏发|没发成|没发出来|没出去|没生成|没合成|失效|失败).{0,8}(语音|tts|朗读|念出来|读出来)",
            r"(补发|重发|再发|重新发|补一下|再来一次).{0,8}(语音|tts|朗读|念出来|读出来)",
            r"(语音|tts|朗读|念出来|读出来).{0,8}(补发|重发|再发|重新发|补一下|再来一次)",
            r"(不要|别)(?:再)?(漏|忘|少|丢).{0,8}(语音|tts|朗读|念出来|读出来)",
            r"(语音|tts|朗读|念出来|读出来).{0,8}(不要|别)(?:再)?(漏|忘|少|丢)",
        )
        for pattern in retry_patterns:
            match = re.search(pattern, compact, flags=re.IGNORECASE)
            if match:
                return "positive", _single_line(match.group(0), 80), raw_text
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
        keyword_match = self._event_tts_keyword_match(event)
        if keyword_match:
            return "positive", f"keyword:{_single_line(keyword_match, 80)}", raw_text
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

    def _event_explicitly_requests_foreign_visible_text(
        self,
        event: Any,
        *,
        voice_language: str = "",
    ) -> bool:
        """Distinguish a visible foreign-text request from a voice-language request."""
        language = (
            self._normalize_tts_voice_language_value(voice_language)
            or self._tts_voice_language_for_event(event)
        )
        if language == "zh" or event is None:
            return False
        raw_text = str(getattr(event, "message_str", "") or "").strip().lower()
        compact = re.sub(r"[\s，,。！？!?、；;：:~～]+", "", raw_text)
        if not compact:
            return False
        language_token = {
            "ja": r"(?:日语|日語|日文|日本语|日本語|japanese)",
            "en": r"(?:英语|英語|英文|english)",
        }.get(language, "")
        if not language_token or not re.search(language_token, compact, flags=re.IGNORECASE):
            return False
        if re.search(
            rf"(?:不要|别|不用|不必|禁止|取消).{{0,6}}{language_token}.{{0,6}}(?:文字|文本|打字|书面|原文|字幕)",
            compact,
            flags=re.IGNORECASE,
        ):
            return False
        patterns = (
            rf"(?:用|以|改用|换成|切成|直接用|请用){language_token}(?:的)?(?:文字|文本|打字|书面|原文|字幕)",
            rf"{language_token}(?:的)?(?:文字|文本|打字|书面|原文|字幕)(?:回复|回答|回我|发送|发出|输出|显示)?",
            rf"(?:文字|文本|打字|书面|原文|字幕)(?:回复|回答|回我|发送|发出|输出|显示)?.{{0,4}}{language_token}",
            rf"(?:只发|只要|仅发|仅要|显示|保留){language_token}(?:原文|文字|文本|字幕)",
            rf"(?:write|type|textreply)(?:it)?(?:in)?{language_token}",
        )
        return any(re.search(pattern, compact, flags=re.IGNORECASE) for pattern in patterns)

    def _tts_trigger_probability_allows(self, event: Any, *, reason: str) -> bool:
        if self._tts_setting("tts_frequency_control_mode", "global") == "legacy":
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
                "TTS全局触发概率为0,本轮不注入TTS提示词: reason=%s session=%s",
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
                "TTS全局触发概率未命中,本轮不注入TTS提示词: reason=%s probability=%.2f session=%s",
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

    def _build_tts_rule_prompt(self, provider_kind: str = "generic", *, event: Any = None) -> str:
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
            "【语音消息规则】",
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
        return "\n".join(
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

    async def apply_tts_enhancement_request(self, event: Any, req: Any) -> None:
        if bool(getattr(event, "_private_companion_tts_request_applied", False)):
            return
        feature_enabled = getattr(self, "_feature_enabled_or_temp_unlocked", None)
        tts_enabled = feature_enabled("enable_tts_enhancement") if callable(feature_enabled) else self._tts_setting("enable_tts_enhancement", False)
        if not getattr(self, "enabled", False) or not tts_enabled:
            return
        if not hasattr(req, "system_prompt"):
            logger.info(
                "TTS请求注入跳过: req无system_prompt session=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            )
            return
        turn_voice_language = self._ensure_turn_tts_voice_language(event)
        try:
            config = self.context.get_config(str(getattr(event, "unified_msg_origin", "") or "")) or {}
        except Exception:
            config = getattr(self, "config", {}) or {}
        provider_kind = self._tts_provider_kind_for_event(event, config=config)
        marker = "<!-- private_companion_tts_enhancement_v1 -->"
        prompt = str(getattr(req, "system_prompt", "") or "")

        def register_materialized_tts_fragment(
            fragment_marker: str,
            text: str,
            *,
            key: str = "",
            title: str = "",
            priority: int = 55,
            placement: str = PLACEMENT_DYNAMIC_SYSTEM,
            opaque: bool = False,
        ) -> None:
            plan = get_conversation_injection_plan(req)
            if plan is None or plan.contains_marker(fragment_marker):
                return
            plan.add(
                key=key,
                marker=fragment_marker,
                content=text,
                title=title,
                priority=priority,
                source="tts",
                placement=placement,
                temporary=False,
                materialized=True,
                opaque=opaque,
            )

        def append_dynamic_tts_fragment(
            fragment_marker: str,
            text: str,
            *,
            title: str,
            priority: int = 55,
        ) -> str:
            helper = getattr(self, "_append_turn_prompt_fragment_by_position", None)
            if callable(helper):
                try:
                    if helper(
                        req,
                        fragment_marker,
                        text,
                        title=title,
                        priority=priority,
                        source="tts",
                    ):
                        return "prompt"
                except TypeError:
                    if helper(req, fragment_marker, text):
                        return "prompt"
                except Exception as exc:
                    logger.debug("TTS 指定位置动态注入失败,回退 system_prompt: %s", _single_line(exc, 120))
            req.system_prompt = (
                f"{getattr(req, 'system_prompt', '') or ''}\n\n{fragment_marker}\n{text}"
            ).strip()
            register_materialized_tts_fragment(
                fragment_marker,
                text,
                title=title,
                priority=priority,
                placement=PLACEMENT_DYNAMIC_SYSTEM,
            )
            return "system_prompt"

        async def record_tts_fragment(title: str, key: str, text: str, mode: str = "", placement: str = "system_prompt") -> None:
            common_recorder = getattr(self, "_record_request_prompt_fragment", None)
            if callable(common_recorder):
                await common_recorder(
                    event,
                    title=title,
                    key=key,
                    text=text,
                    source="tts_enhancement",
                    mode=mode or str(self._tts_setting("tts_generation_mode", "fast_tag") or ""),
                    priority=20,
                    metadata={
                        "语种": self._tts_language_label(event),
                        "本轮临时语种": bool(turn_voice_language),
                        "模式": self._tts_setting("tts_generation_mode", "fast_tag"),
                        "频控": self._tts_setting("tts_frequency_control_mode", "global"),
                        "范围": self._tts_setting("tts_conversion_scope", "partial"),
                        "provider": provider_kind,
                        "注入位置": placement,
                    },
                )
                return
            recorder = getattr(self, "_record_prompt_injection_snapshot", None)
            if not callable(recorder):
                return
            await recorder(
                kind="request",
                session=_single_line(getattr(event, "unified_msg_origin", ""), 160) or "unknown",
                title=title,
                text=text,
                mode=mode or str(self._tts_setting("tts_generation_mode", "fast_tag") or ""),
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
                    "语种": self._tts_language_label(event),
                    "本轮临时语种": bool(turn_voice_language),
                    "模式": self._tts_setting("tts_generation_mode", "fast_tag"),
                    "频控": self._tts_setting("tts_frequency_control_mode", "global"),
                    "范围": self._tts_setting("tts_conversion_scope", "partial"),
                    "provider": provider_kind,
                    "注入位置": placement,
                },
            )

        try:
            setattr(event, "_private_companion_tts_request_applied", True)
        except Exception:
            pass
        expression = getattr(event, "_private_companion_expression_decision", None)
        if isinstance(expression, dict):
            tts_style = _single_line(expression.get("tts_style"), 24)
            expression_band = _single_line(expression.get("expression_band"), 24)
            content_tier = _single_line(expression.get("content_tier"), 16) or "normal"
            expression_context = self._tts_expression_style_context(event)
            if tts_style or expression_band:
                expression_prompt = (
                    f"当前互动档位={expression_band or 'relaxed'}，TTS 风格上限={tts_style or 'natural'}，"
                    f"内容尺度={content_tier}。{expression_context}\n"
                    "语音只能收敛语气，不能扩大文字内容尺度、切换 Provider 或绕过文本复核。"
                )
                placement = append_dynamic_tts_fragment(
                    "<!-- private_companion_tts_expression_v1 -->",
                    expression_prompt,
                    title="统一陪伴表达的语音上限",
                    priority=54,
                )
                await record_tts_fragment(
                    "TTS 统一表达上限注入",
                    "tts.relationship_expression",
                    expression_prompt,
                    placement=placement,
                )
        user_requested_tts = self._event_explicitly_requests_tts(event) or bool(turn_voice_language)
        functional_command_reason = self._tts_functional_command_reason(event)
        if functional_command_reason and not user_requested_tts:
            functional_prompt = (
                "用户本轮发来的是指令或功能操作。请优先把执行结果、帮助、菜单、状态、配置、查询信息、错误说明和卡片说明保留为普通文字，"
                "不要仅因自动语音概率命中就添加 <pc_tts>、<tts> 或等价语音标签。"
                "只有用户在本轮明确要求语音或朗读时，才把确实适合听见的自然表达交给语音。"
            )
            placement = append_dynamic_tts_fragment(
                "<!-- private_companion_tts_functional_reply_v1 -->",
                functional_prompt,
                title="功能性回复的语音取舍",
                priority=56,
            )
            await record_tts_fragment(
                "TTS 功能性回复取舍注入",
                "tts.functional_reply",
                functional_prompt,
                mode=functional_command_reason,
                placement=placement,
            )
            return
        strong_block_reason = ""
        mode = self._tts_setting("tts_generation_mode", "fast_tag")
        full_scope = self._tts_setting("tts_conversion_scope", "partial") == "full"
        probability_allowed = True
        if (
            mode in {"fast_tag", "postprocess"}
            and not user_requested_tts
            and self._tts_setting("tts_frequency_control_mode", "global") != "legacy"
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
        if not strong_block_reason:
            self._disable_streaming_for_tts_turn(event)
        if marker not in prompt and mode == "fast_tag" and not strong_block_reason:
            rule_prompt = self._build_tts_rule_prompt(provider_kind, event=event)
            req.system_prompt = f"{prompt}\n\n{marker}\n{rule_prompt}".strip()
            register_materialized_tts_fragment(
                marker,
                rule_prompt,
                key="tts.rule",
                title="语音消息规则",
                priority=20,
                placement=PLACEMENT_TOOL_CONTRACT,
                opaque=True,
            )
            await record_tts_fragment("TTS 基础规则注入", "tts.rule", rule_prompt)
        elif marker not in prompt and mode == "postprocess" and not strong_block_reason:
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
            postprocess_prompt = (
                "【TTS 后处理模式】\n"
                "本轮主回复请只输出普通聊天文字，不要主动写 <pc_tts>、<tts>、语音、朗读、音频或任何等价语音标签。"
                "主回复是直接展示给用户看的正文，保持当前聊天语言（通常为中文）；不要把准备送入语音的日语或英语朗读稿直接写进普通正文。"
                "目标语种只交给发送前 TTS 后处理生成 voice_text，visible_text 保持用户看得懂的正文；只有用户本轮明确要求目标语种文字回复时才例外。"
                "如果用户是在补要或追问上一条语音，只回复这次应该说的内容；不要预告或确认“语音已经发出”“这次真发了”，实际发送结果由插件决定。"
                f"当前是{'全量' if full_scope else '局部'}转换；{scope_text}，将由插件发送前的 TTS 后处理模型统一判断。"
                f"{temporary_language_rule}{temporary_visible_rule}"
            )
            req.system_prompt = f"{prompt}\n\n{marker}\n{postprocess_prompt}".strip()
            register_materialized_tts_fragment(
                marker,
                postprocess_prompt,
                key="tts.rule",
                title="TTS 后处理模式",
                priority=20,
                placement=PLACEMENT_TOOL_CONTRACT,
                opaque=True,
            )
            await record_tts_fragment("TTS 后处理模式注入", "tts.rule", postprocess_prompt, mode="postprocess")
        if strong_block_reason:
            reverse_prompt = (
                f"本轮语音被硬性禁止，原因：{strong_block_reason}。\n"
                "请只输出普通文字回复，不要包含 <pc_tts>...</pc_tts>、<tts>...</tts>、语音、朗读、音频、发声、Record 或任何等价语音内容。"
                "如果用户要求语音，也先用文字自然回应当前内容，不要承诺已经发送语音。"
            )
            placement = append_dynamic_tts_fragment(
                "<!-- private_companion_tts_block_v1 -->",
                reverse_prompt,
                title="本轮 TTS 强约束",
                priority=22,
            )
            await record_tts_fragment("TTS 强约束禁用注入", "tts.block", reverse_prompt, mode="strong_block", placement=placement)
        if mode == "fast_tag" and self._should_force_tts_for_main_user_event(event) and not strong_block_reason:
            frequency_mode = self._tts_setting("tts_frequency_control_mode", "global")
            if full_scope:
                force_rule = (
                    "这轮消息来自主用户或明确 @ 到主用户。如果当前回复适合语音表达，可以使用 <pc_tts>；"
                    "一旦决定使用，唯一语音块必须覆盖整条回复的全部有效内容，不得只圈出一句，仍需遵守目标语种、发送形态和文字显示规则。"
                )
            elif frequency_mode == "legacy":
                force_rule = "这轮消息来自主用户或明确 @ 到主用户。若当前回复适合语音表达，适合采用一段 <pc_tts>...</pc_tts>；由你根据语境判断，仍需遵守目标语种、发送形态和文字显示规则。"
            else:
                force_rule = "这轮消息来自主用户或明确 @ 到主用户。如果语音比纯文字更自然，可以采用一段 <pc_tts>...</pc_tts>；不要刻意使用语音，仍需遵守目标语种、发送形态、文字显示规则和会话最小间隔。"
            force_prompt = force_rule
            placement = append_dynamic_tts_fragment(
                "<!-- private_companion_tts_force_v1 -->",
                force_prompt,
                title="本轮 TTS 强化触发",
                priority=54,
            )
            await record_tts_fragment("TTS 主用户倾向注入", "tts.force", force_prompt, mode="main_user", placement=placement)
        if user_requested_tts and mode == "fast_tag" and not strong_block_reason:
            request_scope_rule = (
                "请把唯一语音块覆盖整条回复的全部有效内容，不得只圈出一句；"
                if full_scope
                else "请直接把适合朗读的内容写进一段 <pc_tts>...</pc_tts>；"
            )
            user_request_prompt = (
                f"用户本轮明确希望听到语音或你的声音。请以回应用户需求为主：{request_scope_rule}"
                "只写这次真正要说的内容，不要预告或确认“语音已经发出”“这次真发了”，实际发送结果由插件决定。"
                "这类顺应用户请求的语音不受自动语音触发概率限制，但仍需自然克制、遵守目标语种、发送形态和文字显示规则。"
            )
            placement = append_dynamic_tts_fragment(
                "<!-- private_companion_tts_user_request_v1 -->",
                user_request_prompt,
                title="用户语音请求",
                priority=54,
            )
            await record_tts_fragment("用户语音请求注入", "tts.user_request", user_request_prompt, mode="user_request", placement=placement)

    async def protect_tts_enhancement_response_blocks(self, event: Any, resp: Any) -> None:
        self._ensure_turn_tts_voice_language(event)
        feature_enabled = getattr(self, "_feature_enabled_or_temp_unlocked", None)
        tts_enabled = feature_enabled("enable_tts_enhancement") if callable(feature_enabled) else self._tts_setting("enable_tts_enhancement", False)
        if not bool(getattr(event, "_private_companion_tts_request_applied", False)):
            return
        if not tts_enabled:
            text = str(getattr(resp, "completion_text", "") or "")
            if re.search(r"</?(?:pc[_-]?tts|t{2,}s)\b", text, flags=re.IGNORECASE):
                resp.completion_text = _normalize_outbound_punctuation_flow(self._strip_any_tts_markup(text))
                logger.info(
                    "TTS强化未开启,已从模型回复中移除 TTS 标签: session=%s preview=%s",
                    _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                    _single_line(resp.completion_text, 160),
                )
            return
        text = self._normalize_tts_tags(str(getattr(resp, "completion_text", "") or ""))
        text_before_safety_drop = text
        text, dropped_safety_voice = self._drop_tts_provider_safety_blocks(text)
        if dropped_safety_voice:
            if not text:
                text = self._tts_plain_markup_fallback_text(text_before_safety_drop)
            resp.completion_text = _normalize_outbound_punctuation_flow(text)
            logger.warning(
                "已从模型回复中移除提供商安全回执语音块: session=%s remaining=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                _single_line(text, 160) or "empty",
            )
        if text:
            has_tts_markup = bool(re.search(r"</?(?:pc[_-]?tts|t{2,}s)\b", text, flags=re.IGNORECASE))
            if has_tts_markup:
                if self._tts_setting("tts_generation_mode", "fast_tag") == "postprocess":
                    # In postprocess mode every model-authored tag is input noise, including
                    # the private <pc_tts> form. Only the postprocessor may create a voice block.
                    cleaned = self._strip_any_tts_markup(text)
                    cleaned = self._sanitize_tts_visible_text(cleaned) or self._tts_visible_fallback_text(
                        text,
                        event=event,
                    )
                    resp.completion_text = _normalize_outbound_punctuation_flow(cleaned)
                    logger.info(
                        "TTS后处理模式已移除主模型自写语音标签,改由发送前后处理判断: session=%s preview=%s",
                        _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                        _single_line(cleaned, 160),
                    )
                    return
                if not self._tts_hard_block_reason(event):
                    try:
                        config = self.context.get_config(str(getattr(event, "unified_msg_origin", "") or "")) or {}
                    except Exception:
                        config = getattr(self, "config", {}) or {}
                    provider_kind = self._tts_provider_kind_for_event(event, config=config)
                    text = await self._ensure_tts_blocks_have_visible_chinese(text, event, provider_kind=provider_kind)
                text = self._protect_tts_blocks_for_framework(text, event)
            else:
                visible_fallback = self._tts_unwrapped_foreign_translation_fallback(text, event)
                if visible_fallback:
                    logger.warning(
                        "快速标签回复漏写语音标记，已仅保留中文可见正文: session=%s original=%s visible=%s",
                        _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                        _single_line(text, 160),
                        _single_line(visible_fallback, 160),
                    )
                    text = visible_fallback
            resp.completion_text = _normalize_outbound_punctuation_flow(text)

    async def apply_tts_enhancement_before_send(self, event: Any) -> None:
        turn_voice_language = self._ensure_turn_tts_voice_language(event)
        feature_enabled = getattr(self, "_feature_enabled_or_temp_unlocked", None)
        tts_enabled = feature_enabled("enable_tts_enhancement") if callable(feature_enabled) else self._tts_setting("enable_tts_enhancement", False)
        if not getattr(self, "enabled", False) or not tts_enabled:
            return
        if not bool(getattr(event, "_private_companion_tts_request_applied", False)):
            logger.debug(
                "TTS 强化跳过未经过主回复链的发送结果: session=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            )
            return
        result = event.get_result()
        chain = list(getattr(result, "chain", []) or []) if result is not None else []
        if not chain or any(isinstance(comp, Record) for comp in chain):
            return
        skip_reason = _single_line(getattr(event, "_private_companion_skip_tts_enhancement", ""), 80)
        if not skip_reason and any(
            bool(getattr(comp, "_private_companion_skip_tts_enhancement", False))
            for comp in chain
        ):
            skip_reason = "proactive_prebuilt_voice"
        if skip_reason:
            logger.info(
                "主动正文已有预生成语音,跳过二次 TTS 转换: session=%s reason=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                skip_reason,
            )
            return
        plain_parts = [str(getattr(comp, "text", "") or "") for comp in chain if isinstance(comp, Plain)]
        if not plain_parts:
            return
        source_segments: list[str] = []
        llm_splitter = getattr(self, "_split_llm_controlled_text_for_event", None)
        llm_allowed = getattr(self, "_llm_controlled_segmenting_allowed", None)
        if (
            callable(llm_splitter)
            and callable(llm_allowed)
            and bool(llm_allowed(event))
        ):
            try:
                planned_segments = [
                    str(item or "").strip()
                    for item in llm_splitter(event, "".join(plain_parts))
                    if str(item or "").strip()
                ]
            except Exception as exc:
                logger.debug("TTS 前自主分段解析失败: %s", _single_line(exc, 120))
                planned_segments = []
            if len(planned_segments) > 1:
                source_segments = planned_segments
                # Synthesize the visible reply without speaking the transport
                # marker; the downstream ordered-send stage restores segments.
                plain_parts = ["".join(planned_segments)]
        if not source_segments and len(plain_parts) > 1 and len(plain_parts) == len(chain):
            source_limit = self._tts_complete_text_limit("".join(plain_parts), minimum=1000)
            for part in plain_parts:
                restored_part = self._restore_protected_tts_blocks(part, event)
                visible_part = self._sanitize_tts_visible_text(restored_part, max_chars=source_limit)
                if visible_part:
                    source_segments.append(visible_part)
        try:
            if len(source_segments) > 1:
                setattr(event, "_private_companion_tts_source_plain_segments", tuple(source_segments))
            elif hasattr(event, "_private_companion_tts_source_plain_segments"):
                delattr(event, "_private_companion_tts_source_plain_segments")
        except Exception:
            pass
        text = self._restore_protected_tts_blocks("".join(plain_parts), event).strip()
        if not text:
            return
        tool_cleaner = getattr(self, "_strip_plaintext_tool_call_envelopes", None)
        if callable(tool_cleaner):
            cleaned_text, leaked_calls = tool_cleaner(text)
            if leaked_calls:
                logger.warning(
                    "TTS 发送前已移除明文工具调用: session=%s tools=%s",
                    _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                    ",".join(str(item.get("name") or "") for item in leaked_calls),
                )
                text = cleaned_text
                if not text:
                    event.set_result(self._build_result_from_chain([]))
                    return
        normalized = self._normalize_tts_tags(text)
        normalized_before_safety_drop = normalized
        normalized, dropped_safety_voice = self._drop_tts_provider_safety_blocks(normalized)
        if dropped_safety_voice:
            logger.warning(
                "TTS发送前已移除提供商安全回执语音块: session=%s remaining=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                _single_line(normalized, 160) or "empty",
            )
            if not normalized:
                fallback_text = self._tts_plain_markup_fallback_text(
                    normalized_before_safety_drop
                )
                event.set_result(
                    self._build_result_from_chain(
                        [Plain(fallback_text)] if fallback_text else []
                    )
                )
                return
        reaction_intent = getattr(
            event,
            "_private_companion_reaction_expression_intent",
            None,
        )
        defer_reaction_tts = (
            isinstance(reaction_intent, dict)
            and bool(reaction_intent)
            and not self._event_explicitly_requests_tts(event)
            and not bool(turn_voice_language)
        )
        if defer_reaction_tts:
            visible_text = self._tts_visible_fallback_text(
                normalized,
                text,
                event=event,
            ) or self._tts_plain_markup_fallback_text(normalized)
            visible_text = self._sanitize_tts_visible_text(
                visible_text,
                max_chars=self._tts_complete_text_limit(visible_text, 1600),
            )
            if visible_text:
                primary_chain = self._replace_plain_components_preserving_order(
                    chain,
                    [Plain(visible_text)],
                )
                inbound_ts_getter = getattr(self, "_event_inbound_activity_ts", None)
                try:
                    started_at = (
                        float(inbound_ts_getter(event))
                        if callable(inbound_ts_getter)
                        else time.time()
                    )
                except Exception:
                    started_at = time.time()
                setattr(
                    event,
                    "_private_companion_deferred_reaction_tts",
                    {
                        "normalized": normalized,
                        "fallback_plain": visible_text,
                        "started_at": started_at,
                        "turn_generation": _safe_int(
                            getattr(event, "_private_companion_reply_turn_generation", 0),
                            0,
                            0,
                        ),
                    },
                )
                event.set_result(self._build_result_from_chain(primary_chain))
                logger.info(
                    "表情表达先发送完整正文,自动 TTS 延后生成: session=%s chars=%s",
                    _single_line(getattr(event, "unified_msg_origin", ""), 120)
                    or "unknown",
                    len(visible_text),
                )
                return
        if self._tts_setting("tts_generation_mode", "fast_tag") == "postprocess":
            # A tag can also arrive from a tool or an extension that bypasses the LLM response hook.
            # Treat it as plain source text so it cannot re-enter the fast-tag path.
            normalized = self._sanitize_tts_visible_text(self._strip_any_tts_markup(normalized))
            new_chain = await self._maybe_convert_plain_reply_to_tts(normalized, event) if normalized else []
        elif "<tts>" in normalized.lower() and "</tts>" in normalized.lower():
            normalized, full_scope_fallback = self._enforce_full_tts_scope_markup(
                normalized,
                event=event,
            )
            if full_scope_fallback:
                logger.info(
                    "TTS全量转换已在发送前覆盖整条回复: session=%s chars=%s preview=%s",
                    _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                    len(full_scope_fallback),
                    _single_line(full_scope_fallback, 140),
                )
            new_chain = await self._process_tts_tags(
                normalized,
                event,
                fallback_plain=full_scope_fallback,
            )
        else:
            new_chain = await self._maybe_convert_plain_reply_to_tts(normalized, event)
        if not new_chain:
            if self._tts_setting("tts_generation_mode", "fast_tag") == "postprocess" and normalized:
                translated = await self._translate_unwrapped_foreign_postprocess_text(normalized, event)
                if translated:
                    normalized = translated
                    logger.info(
                        "TTS后处理纯文本检测到未包装外语,已转为可见中文: session=%s",
                        _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                    )
                event.set_result(self._build_result_from_chain([Plain(normalized)]))
                return
            if PRIVATE_TTS_BLOCK_TOKEN_PATTERN.search("".join(plain_parts)):
                fallback_text = self._tts_visible_fallback_text(
                    normalized,
                    event=event,
                ) or self._tts_plain_markup_fallback_text(normalized)
                event.set_result(self._build_result_from_chain([Plain(fallback_text)] if fallback_text else []))
            return
        if self._tts_setting("tts_generation_mode", "fast_tag") == "postprocess":
            visible_text = "\n".join(
                str(getattr(component, "text", "") or "").strip()
                for component in new_chain
                if isinstance(component, Plain) and str(getattr(component, "text", "") or "").strip()
            ).strip()
            translated = await self._translate_unwrapped_foreign_postprocess_text(visible_text, event)
            if translated:
                new_chain = self._replace_plain_components_preserving_order(
                    new_chain,
                    [Plain(translated)],
                )
                logger.info(
                    "TTS后处理可见组件检测到未包装外语,已转为可见中文: session=%s",
                    _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                )
        new_chain = self._tts_record_first_visible_last_chain(new_chain)
        if len(plain_parts) != len(chain):
            new_chain = self._replace_plain_components_preserving_order(
                chain,
                new_chain,
            )
        if isinstance(reaction_intent, dict) and reaction_intent:
            ordered_chunks = [new_chain]
        else:
            ordered_chunks = self._split_tts_chain_for_ordered_send(new_chain)
        expanded_chunks: list[list[Any]] = []
        for chunk in ordered_chunks:
            expanded_chunks.extend(self._tts_segment_plain_chunk_for_ordered_send(event, chunk))
        ordered_chunks = expanded_chunks
        if len(ordered_chunks) > 1:
            first_chunk_has_record = any(isinstance(comp, Record) for comp in ordered_chunks[0])
            if first_chunk_has_record:
                remainder_started_at = time.time()
            else:
                inbound_ts_getter = getattr(self, "_event_inbound_activity_ts", None)
                if callable(inbound_ts_getter):
                    try:
                        remainder_started_at = float(inbound_ts_getter(event))
                    except Exception:
                        remainder_started_at = time.time()
                else:
                    remainder_started_at = time.time()
            event.set_result(self._build_result_from_chain(ordered_chunks[0]))
            recorder = getattr(self, "_record_daily_review_outbound_case", None)
            if callable(recorder):
                case_id = recorder(event, ordered_chunks[0])
                updater = getattr(self, "_update_daily_review_case", None)
                if case_id and callable(updater):
                    updater(
                        case_id,
                        outcome="delivery_pending",
                        signals={"segments_expected": len(ordered_chunks), "segments_sent": 1},
                    )
            pending = {
                "chunks": ordered_chunks[1:],
                "started_at": remainder_started_at,
                "turn_generation": _safe_int(
                    getattr(event, "_private_companion_reply_turn_generation", 0),
                    0,
                    0,
                ),
            }
            proactive_umo = _single_line(
                getattr(event, "_private_companion_proactive_delivery_umo", ""),
                180,
            )
            if proactive_umo:
                remainder = self._send_tts_chain_chunks_after_first(
                    event,
                    pending["chunks"],
                    started_at=remainder_started_at,
                )
                self._create_tts_background_task(remainder, label="tts_reply_remainder")
            else:
                setattr(
                    event,
                    "_private_companion_tts_reply_remainder",
                    pending,
                )
            return
        event.set_result(self._build_result_from_chain(ordered_chunks[0] if ordered_chunks else new_chain))

    async def _should_defer_segmenting_to_astrbot_tts(
        self,
        event: Any,
        result: Any,
        chain: list[Any],
    ) -> bool:
        """Keep the original LLM result intact when AstrBot still owns this turn's TTS."""
        if bool(getattr(event, "_private_companion_tts_request_applied", False)):
            return False
        if not chain or any(isinstance(component, Record) for component in chain):
            return False
        try:
            if result is None or not bool(result.is_llm_result()):
                return False
        except Exception:
            return False

        context = getattr(self, "context", None)
        if context is None:
            return False
        umo = str(getattr(event, "unified_msg_origin", "") or "")
        config_getter = getattr(context, "get_config", None)
        try:
            config = config_getter(umo) if callable(config_getter) else {}
        except Exception:
            return False
        if not isinstance(config, dict):
            return False
        settings = config.get("provider_tts_settings")
        if not isinstance(settings, dict):
            return False
        enabled_value = settings.get("enable", False)
        if isinstance(enabled_value, str):
            enabled = enabled_value.strip().lower() in {"1", "true", "yes", "on", "enabled"}
        else:
            enabled = bool(enabled_value)
        if not enabled:
            return False
        try:
            trigger_probability = max(
                0.0,
                min(1.0, float(settings.get("trigger_probability", 1))),
            )
        except (TypeError, ValueError):
            trigger_probability = 1.0
        if trigger_probability <= 0:
            return False
        provider_getter = getattr(context, "get_using_tts_provider", None)
        try:
            provider = provider_getter(umo) if callable(provider_getter) else None
        except Exception:
            provider = None
        if provider is None:
            return False

        try:
            from astrbot.core.star.session_llm_manager import SessionServiceManager

            allowed = SessionServiceManager.should_process_tts_request(event)
            if inspect.isawaitable(allowed):
                allowed = await allowed
            if not bool(allowed):
                return False
        except ImportError:
            pass
        except Exception as exc:
            logger.debug(
                "查询 AstrBot 官方 TTS 会话状态失败，保留插件分段: session=%s error=%s",
                _single_line(umo, 120) or "unknown",
                _single_line(exc, 120),
            )
            return False
        return True

    async def finalize_outbound_tts_markup_guard(self, event: Any) -> None:
        """Last-resort guard so raw <tts> tags never reach the chat surface."""
        if not getattr(self, "enabled", False):
            return
        if not bool(getattr(event, "_private_companion_tts_request_applied", False)):
            return
        result = event.get_result()
        try:
            if result is not None and bool(result.is_llm_result()):
                result.set_result_content_type(ResultContentType.GENERAL_RESULT)
                logger.debug(
                    "插件已接管本轮 TTS，阻止 AstrBot 官方 TTS 二次处理: session=%s",
                    _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                )
        except Exception:
            pass
        chain = list(getattr(result, "chain", []) or []) if result is not None else []
        if not chain:
            return
        if any(isinstance(comp, Record) for comp in chain):
            cleaned_chain = await self._sanitize_outbound_tts_chain_without_event(
                chain,
                umo=str(getattr(event, "unified_msg_origin", "") or ""),
            )
            if cleaned_chain != chain:
                event.set_result(self._build_result_from_chain(cleaned_chain))
            return
        if bool(getattr(event, "_private_companion_skip_tts_enhancement", False)) or any(
            bool(getattr(comp, "_private_companion_skip_tts_enhancement", False))
            for comp in chain
        ):
            return
        plain_parts = [str(getattr(comp, "text", "") or "") for comp in chain if isinstance(comp, Plain)]
        if not plain_parts:
            return
        text = self._restore_protected_tts_blocks("".join(plain_parts), event).strip()
        if not re.search(r"</?(?:pc[_-]?tts|t{2,}s)\b", text, flags=re.IGNORECASE):
            return
        normalized = self._normalize_tts_tags(text)
        normalized_before_safety_drop = normalized
        normalized, dropped_safety_voice = self._drop_tts_provider_safety_blocks(normalized)
        if dropped_safety_voice and not normalized:
            fallback_text = self._tts_plain_markup_fallback_text(
                normalized_before_safety_drop
            )
            event.set_result(
                self._build_result_from_chain(
                    [Plain(fallback_text)] if fallback_text else []
                )
            )
            logger.warning(
                "发送前终检已丢弃仅包含提供商安全回执的语音块: session=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            )
            return
        feature_enabled = getattr(self, "_feature_enabled_or_temp_unlocked", None)
        tts_enabled = feature_enabled("enable_tts_enhancement") if callable(feature_enabled) else self._tts_setting("enable_tts_enhancement", False)
        new_chain: list[Any] = []
        if (
            tts_enabled
            and self._tts_setting("tts_generation_mode", "fast_tag") != "postprocess"
            and re.search(r"<tts\b[^>]*>.*?</tts>", normalized, flags=re.IGNORECASE | re.DOTALL)
        ):
            normalized, full_scope_fallback = self._enforce_full_tts_scope_markup(
                normalized,
                event=event,
            )
            new_chain = await self._process_tts_tags(
                normalized,
                event,
                fallback_plain=full_scope_fallback,
            )
        if not new_chain:
            fallback_text = self._tts_visible_fallback_text(
                normalized,
                event=event,
            ) or self._tts_plain_markup_fallback_text(normalized)
            new_chain = [Plain(fallback_text)] if fallback_text else []
        if len(plain_parts) != len(chain):
            non_plain_tail = [comp for comp in chain if not isinstance(comp, Plain)]
            if non_plain_tail:
                new_chain = list(new_chain) + non_plain_tail
        new_chain = self._tts_record_first_visible_last_chain(new_chain)
        logger.warning(
            "发送前终检拦截残留 TTS 标签: session=%s preview=%s",
            _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            _single_line(self._tts_chain_log_text(new_chain), 160),
        )
        event.set_result(self._build_result_from_chain(new_chain))

    async def _sanitize_outbound_tts_chain_without_event(self, chain: list[Any], *, umo: str = "") -> list[Any]:
        if not chain:
            return chain
        changed = False
        cleaned_chain: list[Any] = []
        for comp in chain:
            if not isinstance(comp, Plain):
                cleaned_chain.append(comp)
                continue
            original = str(getattr(comp, "text", "") or "")
            if _has_history_media_marker(original):
                cleaned_history = _strip_history_media_markers(original)
                if cleaned_history:
                    leading_whitespace = original[: len(original) - len(original.lstrip())]
                    trailing_whitespace = original[len(original.rstrip()) :]
                    cleaned_control = f"{leading_whitespace}{cleaned_history}{trailing_whitespace}"
                else:
                    cleaned_control = ""
            else:
                cleaned_control = original
            cleaned_control = _strip_nonstandard_chat_control_tags(cleaned_control)
            cleaned_control = self._strip_visible_tts_emotion_cues(cleaned_control)
            has_tts_markup = re.search(r"</?(?:pc[_-]?tts|t{2,}s)\b", cleaned_control, flags=re.IGNORECASE)
            if not has_tts_markup:
                if cleaned_control != original:
                    changed = True
                if cleaned_control:
                    cleaned_chain.append(Plain(cleaned_control) if cleaned_control != original else comp)
                continue
            changed = True
            normalized = self._normalize_tts_tags(cleaned_control)
            fallback_text = self._tts_visible_fallback_text(normalized) or self._strip_any_tts_markup(normalized)
            fallback_text = self._sanitize_tts_visible_text(fallback_text)
            if fallback_text:
                cleaned_chain.append(Plain(fallback_text))
        if changed:
            logger.warning(
                "外发兜底清理残留内部控制标记: umo=%s preview=%s",
                _single_line(umo, 120) or "unknown",
                _single_line(self._tts_chain_log_text(cleaned_chain), 160),
            )
        return cleaned_chain

    @staticmethod
    def _without_reply_components(chain: list[Any]) -> list[Any]:
        """Return a copy without quote components across AstrBot versions."""
        return [
            component
            for component in list(chain or [])
            if component.__class__.__name__.lower() != "reply"
        ]

    def _suppress_reply_components_for_voice_chain(self, chain: list[Any]) -> list[Any]:
        """Drop quotes only when a voice chain has no visible text companion.

        A quote on a mixed voice/text reply remains meaningful to the platform
        and to downstream image/forward/vision consumers, so it must be moved
        to the text chunk instead of being removed merely because a ``Record``
        is present.
        """
        working_chain = list(chain or [])
        if not any(isinstance(component, Record) for component in working_chain):
            return working_chain
        if any(
            isinstance(component, Plain)
            and bool(str(getattr(component, "text", "") or "").strip())
            for component in working_chain
        ):
            return working_chain
        return self._without_reply_components(working_chain)

    def _split_tts_chain_for_ordered_send(self, chain: list[Any]) -> list[list[Any]]:
        chain = self._suppress_reply_components_for_voice_chain(chain)
        has_record = False
        has_visible = False
        for comp in chain:
            if isinstance(comp, Record):
                has_record = True
            else:
                has_visible = True
        if not has_record or not has_visible:
            return [chain]
        chunks, _changed, _split_changed, _full_text = plan_component_chunks(
            chain,
            plain_type=Plain,
            split_text=lambda text: [text],
            strategies=component_strategies_from_owner(self),
            component_order=component_order_from_owner(self),
            classify=component_kind,
        )
        return chunks or [chain]

    def _tts_record_first_visible_last_chain(self, chain: list[Any]) -> list[Any]:
        if not chain or not any(isinstance(comp, Record) for comp in chain):
            return chain
        records: list[Any] = []
        others: list[Any] = []
        visible_marked: list[str] = []
        visible_plain: list[str] = []
        for comp in chain:
            if isinstance(comp, Record):
                records.append(comp)
                continue
            if isinstance(comp, Plain):
                text = str(getattr(comp, "text", "") or "").strip()
                if not text:
                    continue
                if bool(getattr(comp, "_private_companion_tts_visible_text", False)):
                    visible_marked.append(text)
                else:
                    visible_plain.append(text)
                continue
            others.append(comp)

        def append_unique(target: list[str], value: str) -> None:
            value = self._sanitize_tts_visible_text(value, max_chars=1000)
            if not value:
                return
            normalized = re.sub(r"\s+", "", value)
            if any(normalized == re.sub(r"\s+", "", item) for item in target):
                return
            if any(normalized and normalized in re.sub(r"\s+", "", item) for item in target):
                return
            target[:] = [
                item
                for item in target
                if re.sub(r"\s+", "", item) not in normalized
            ]
            target.append(value)

        visible_lines: list[str] = []
        preferred_visible = visible_marked if visible_marked else visible_plain
        fallback_visible = visible_plain if visible_marked else []
        for text in preferred_visible:
            append_unique(visible_lines, text)
        for text in fallback_visible:
            append_unique(visible_lines, text)
        normalized_chain = list(others) + list(records)
        visible = "\n".join(visible_lines).strip()
        if visible:
            visible_comp = self._mark_tts_visible_plain(visible, max_chars=1000)
            if visible_comp is not None:
                normalized_chain.append(visible_comp)
        return normalized_chain

    @staticmethod
    def _replace_plain_components_preserving_order(
        source_chain: list[Any],
        replacement: list[Any],
    ) -> list[Any]:
        rebuilt: list[Any] = []
        inserted = False
        for component in source_chain:
            if isinstance(component, Plain):
                if not inserted:
                    rebuilt.extend(replacement)
                    inserted = True
                continue
            rebuilt.append(component)
        if not inserted:
            rebuilt.extend(replacement)
        return rebuilt

    def _tts_segment_plain_chunk_for_ordered_send(self, event: Any, chunk: list[Any]) -> list[list[Any]]:
        if not chunk or any(not isinstance(comp, Plain) for comp in chunk):
            return [chunk]
        reaction_intent = getattr(
            event,
            "_private_companion_reaction_expression_intent",
            None,
        )
        if isinstance(reaction_intent, dict) and reaction_intent:
            return [chunk]
        text = "".join(str(getattr(comp, "text", "") or "") for comp in chunk).strip()
        if not text:
            return []

        source_segments = getattr(event, "_private_companion_tts_source_plain_segments", ())
        if isinstance(source_segments, (list, tuple)) and len(source_segments) > 1:
            segment_limit = self._tts_complete_text_limit(
                "".join(str(item or "") for item in source_segments),
                minimum=1000,
            )
            cleaned_segments = [
                self._sanitize_tts_visible_text(item, max_chars=segment_limit)
                for item in source_segments
            ]
            cleaned_segments = [item for item in cleaned_segments if item]
            cleaned_visible = self._sanitize_tts_visible_text(text, max_chars=segment_limit)

            def visible_signature(value: str) -> str:
                return re.sub(r"\s+", "", str(value or ""))

            if (
                len(cleaned_segments) > 1
                and visible_signature("".join(cleaned_segments)) == visible_signature(cleaned_visible)
            ):
                logger.info(
                    "TTS 完整合成后恢复上游正文分段: session=%s segments=%s first=%s",
                    _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                    len(cleaned_segments),
                    _single_line(cleaned_segments[0], 100),
                )
                restored_chunks: list[list[Any]] = []
                for segment in cleaned_segments:
                    visible_part = self._mark_tts_visible_plain(segment, max_chars=segment_limit)
                    if visible_part is not None:
                        restored_chunks.append([visible_part])
                if restored_chunks:
                    return restored_chunks

        scope_getter = getattr(self, "_segmented_setting", None)
        segmented_scope = (
            scope_getter("scope", event=event, default="proactive_only")
            if callable(scope_getter)
            else self._tts_setting("segmented_proactive_scope", "proactive_only")
        )
        if not (
            bool(self._tts_setting("enable_segmented_proactive_reply", False))
            and str(segmented_scope or "") == "all_llm"
        ):
            return [chunk]
        scope_checker = getattr(self, "_segmented_scope_allows_event", None)
        if callable(scope_checker):
            try:
                if not scope_checker(event):
                    return [chunk]
            except Exception:
                return [chunk]
        platform_checker = getattr(self, "_segmented_platform_allows", None)
        if callable(platform_checker):
            try:
                if not platform_checker(event=event):
                    return [chunk]
            except Exception:
                return [chunk]
        original_text = text
        tool_cleaner = getattr(self, "_strip_plaintext_tool_call_envelopes", None)
        if callable(tool_cleaner):
            cleaned_text, leaked_calls = tool_cleaner(text)
            if leaked_calls:
                logger.warning(
                    "TTS 分块前已移除明文工具调用: session=%s tools=%s",
                    _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                    ",".join(str(item.get("name") or "") for item in leaked_calls),
                )
                text = cleaned_text
                if not text:
                    return []
        is_tts_visible_text = any(bool(getattr(comp, "_private_companion_tts_visible_text", False)) for comp in chunk)
        if is_tts_visible_text:
            cleaned_visible = self._sanitize_tts_visible_text(text)
            if not cleaned_visible:
                return []
            text = cleaned_visible
        if (
            not is_tts_visible_text
            and self._tts_voice_language_for_event(event) != "zh"
            and not self._tts_visible_text_is_allowed_after_voice(text)
        ):
            chinese_text = self._tts_chinese_visible_fallback_from_mixed(text)
            if chinese_text:
                logger.warning(
                    "TTS 后置文本混有朗读语种,已仅保留中文释义: session=%s text=%s",
                    _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                    _single_line(chinese_text, 120),
                )
                text = chinese_text
            else:
                logger.warning(
                    "TTS 后置文本不是中文释义,已跳过发送: session=%s text=%s",
                    _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                    _single_line(text, 120),
                )
                return []
        splitter = getattr(self, "_split_proactive_text", None)
        llm_splitter = getattr(self, "_split_llm_controlled_text_for_event", None)
        llm_allowed = getattr(self, "_llm_controlled_segmenting_allowed", None)
        if (
            callable(llm_splitter)
            and callable(llm_allowed)
            and bool(llm_allowed(event))
        ):
            splitter = llm_splitter
        if not callable(splitter):
            visible_part = self._mark_tts_visible_plain(text) if is_tts_visible_text else Plain(text)
            return [[visible_part]] if visible_part is not None else []
        try:
            try:
                if splitter is llm_splitter:
                    split_result = splitter(event, text)
                else:
                    split_result = splitter(text, event=event)
            except TypeError:
                # Preserve compatibility with lightweight test/plugin overrides
                # that still expose the original one-argument splitter contract.
                split_result = splitter(text)
            segments = [item for item in split_result if str(item or "").strip()]
        except Exception as exc:
            logger.debug("TTS 后置文本分段失败,保持原样: %s", _single_line(exc, 120))
            return [chunk]
        if len(segments) <= 1:
            cleaned = segments[0] if segments else text
            if not cleaned:
                return []
            if is_tts_visible_text:
                visible_part = self._mark_tts_visible_plain(cleaned)
                return [[visible_part]] if visible_part is not None else []
            return [[Plain(cleaned)]] if cleaned != text or text != original_text else [chunk]
        logger.info(
            "TTS 后置文本按分段规则拆分: session=%s segments=%s first=%s",
            _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            len(segments),
            _single_line(segments[0], 100),
        )
        if is_tts_visible_text:
            visible_chunks: list[list[Any]] = []
            for segment in segments:
                visible_part = self._mark_tts_visible_plain(segment)
                if visible_part is not None:
                    visible_chunks.append([visible_part])
            return visible_chunks
        return [[Plain(segment)] for segment in segments]

    async def _send_tts_chain_chunks_after_first(
        self,
        event: Any,
        chunks: list[list[Any]],
        *,
        started_at: float | None = None,
        primary_delivery_confirmed: bool = False,
    ) -> None:
        if not chunks:
            return
        expanded_chunks: list[list[Any]] = []
        for chunk in chunks:
            expanded_chunks.extend(self._tts_segment_plain_chunk_for_ordered_send(event, chunk))
        outbound_umo = _single_line(
            getattr(event, "unified_msg_origin", ""),
            160,
        ) or "unknown"
        sanitized_chunks: list[list[Any]] = []
        for chunk in expanded_chunks:
            cleaned_chunk = await self._sanitize_outbound_tts_chain_without_event(
                chunk,
                umo=outbound_umo,
            )
            if not cleaned_chunk:
                continue
            has_visible_plain = any(
                isinstance(component, Plain)
                and bool(str(getattr(component, "text", "") or "").strip())
                for component in cleaned_chunk
            )
            has_delivery_component = any(
                not isinstance(component, Plain)
                and component_kind(component) not in {"at", "reply"}
                for component in cleaned_chunk
            )
            source_had_plain = any(isinstance(component, Plain) for component in chunk)
            if source_had_plain and not has_visible_plain and not has_delivery_component:
                logger.warning(
                    "TTS 尾段清理后仅剩孤立上下文组件,已跳过: session=%s",
                    outbound_umo,
                )
                continue
            sanitized_chunks.append(cleaned_chunk)
        expanded_chunks = sanitized_chunks
        case_id = _single_line(getattr(event, "_private_companion_daily_review_case_id", ""), 20)
        case_updater = getattr(self, "_update_daily_review_case", None)
        if not expanded_chunks:
            logger.info(
                "TTS 尾段清理后无可发送内容: session=%s",
                outbound_umo,
            )
            if case_id and callable(case_updater):
                case_updater(
                    case_id,
                    outcome="delivered",
                    signals={
                        "segments_expected": 1,
                        "segments_sent": 1,
                        "visible_text_complete": True,
                    },
                )
            return
        total_chunks = len(expanded_chunks) + 1
        sent_chunks = 1
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
        previous_text = ""
        turn_generation = _safe_int(
            getattr(event, "_private_companion_reply_turn_generation", 0),
            0,
            0,
        )
        generation_checker = getattr(self, "_reply_turn_is_current", None)
        async with lock:
            for chunk in expanded_chunks:
                if not chunk:
                    continue
                if (
                    not primary_delivery_confirmed
                    and callable(generation_checker)
                    and not generation_checker(scope, turn_generation)
                ):
                    logger.info(
                        "新回合已到达，停止旧 TTS 尾段: session=%s sent=%s/%s",
                        _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                        sent_chunks,
                        total_chunks,
                    )
                    return
                delay = 0.45
                if previous_text and len(expanded_chunks) > 1:
                    calc_interval = getattr(self, "_calc_segmented_proactive_interval", None)
                    if callable(calc_interval):
                        try:
                            try:
                                interval_result = await calc_interval(previous_text, event=event)
                            except TypeError:
                                interval_result = await calc_interval(previous_text)
                            delay = max(0.45, float(interval_result))
                        except Exception:
                            delay = 0.45
                await asyncio.sleep(delay)
                if (
                    not primary_delivery_confirmed
                    and callable(generation_checker)
                    and not generation_checker(scope, turn_generation)
                ):
                    logger.info(
                        "等待期间收到新回合，停止旧 TTS 尾段: session=%s",
                        _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                    )
                    return
                proactive_umo = _single_line(
                    getattr(event, "_private_companion_proactive_delivery_umo", ""),
                    180,
                )
                try:
                    proactive_sender = getattr(self, "_send_chain_components", None)
                    if proactive_umo and callable(proactive_sender):
                        await proactive_sender(
                            proactive_umo,
                            chunk,
                            apply_decorating_hooks=False,
                        )
                    else:
                        await event.send(event.chain_result(chunk))
                    logger.info(
                        "TTS 分块后台补发完成: session=%s %s",
                        _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                        self._tts_chain_log_text(chunk),
                    )
                    sent_chunks += 1
                    if case_id and callable(case_updater):
                        case_updater(
                            case_id,
                            append_output=self._tts_chain_log_text(chunk),
                            outcome="delivered" if sent_chunks >= total_chunks else "delivery_pending",
                            signals={
                                "segments_expected": total_chunks,
                                "segments_sent": sent_chunks,
                                "visible_text_complete": sent_chunks >= total_chunks,
                            },
                        )
                except Exception as exc:
                    if proactive_umo:
                        if case_id and callable(case_updater):
                            case_updater(
                                case_id,
                                outcome="delivery_failed",
                                signals={"segments_expected": total_chunks, "segments_sent": sent_chunks},
                            )
                        logger.warning(
                            "TTS 主动消息中文正文补发失败: session=%s error=%s %s",
                            proactive_umo,
                            _single_line(exc, 160),
                            self._tts_chain_log_text(chunk),
                        )
                        return
                    try:
                        await event.send(self._build_result_from_chain(chunk))
                        logger.info(
                            "TTS 分块后台补发完成: session=%s %s",
                            _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                            self._tts_chain_log_text(chunk),
                        )
                        sent_chunks += 1
                        if case_id and callable(case_updater):
                            case_updater(
                                case_id,
                                append_output=self._tts_chain_log_text(chunk),
                                outcome="delivered" if sent_chunks >= total_chunks else "delivery_pending",
                                signals={
                                    "segments_expected": total_chunks,
                                    "segments_sent": sent_chunks,
                                    "visible_text_complete": sent_chunks >= total_chunks,
                                },
                            )
                    except Exception:
                        if case_id and callable(case_updater):
                            case_updater(
                                case_id,
                                outcome="delivery_failed",
                                signals={
                                    "segments_expected": total_chunks,
                                    "segments_sent": sent_chunks,
                                    "visible_text_complete": False,
                                },
                            )
                        logger.warning("TTS 分块后台补发失败: %s", _single_line(exc, 120))
                        return
                previous_text = " ".join(
                    str(getattr(comp, "text", "") or "").strip()
                    for comp in chunk
                    if isinstance(comp, Plain)
                ).strip() or previous_text

    async def _send_deferred_reaction_tts(
        self,
        event: Any,
        pending: dict[str, Any],
    ) -> None:
        scope_getter = getattr(self, "_event_scope_key", None)
        scope = ""
        if callable(scope_getter):
            try:
                scope = _single_line(scope_getter(event), 160)
            except Exception:
                scope = ""
        scope = scope or _single_line(getattr(event, "unified_msg_origin", ""), 160)
        generation_checker = getattr(self, "_reply_turn_is_current", None)
        if callable(generation_checker) and not generation_checker(
            scope,
            pending.get("turn_generation", 0),
        ):
            logger.info(
                "新回合已到达，跳过旧表情 TTS: session=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            )
            return
        normalized = str(pending.get("normalized") or "").strip()
        fallback_plain = self._sanitize_tts_visible_text(
            pending.get("fallback_plain"),
            max_chars=1600,
        )
        if not normalized:
            return

        setattr(event, "_private_companion_deferred_reaction_tts_active", True)
        try:
            if self._tts_setting("tts_generation_mode", "fast_tag") == "postprocess":
                source_text = self._sanitize_tts_visible_text(
                    self._strip_any_tts_markup(normalized),
                    max_chars=1600,
                )
                generated = (
                    await self._maybe_convert_plain_reply_to_tts(source_text, event)
                    if source_text
                    else []
                )
            elif "<tts>" in normalized.lower() and "</tts>" in normalized.lower():
                tagged, full_scope_fallback = self._enforce_full_tts_scope_markup(
                    normalized,
                    source_text=fallback_plain,
                    event=event,
                )
                generated = await self._process_tts_tags(
                    tagged,
                    event,
                    fallback_plain=full_scope_fallback or fallback_plain,
                )
            else:
                generated = await self._maybe_convert_plain_reply_to_tts(
                    normalized,
                    event,
                )
        finally:
            try:
                delattr(event, "_private_companion_deferred_reaction_tts_active")
            except Exception:
                pass

        records = [component for component in generated if isinstance(component, Record)]
        if not records:
            logger.info(
                "表情表达后台 TTS 未生成语音,正文与表情已保持送达: session=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120)
                or "unknown",
            )
            return
        proactive_umo = _single_line(
            getattr(event, "_private_companion_proactive_delivery_umo", ""),
            180,
        )
        try:
            proactive_sender = getattr(self, "_send_chain_components", None)
            if proactive_umo and callable(proactive_sender):
                sent = await proactive_sender(
                    proactive_umo,
                    records,
                    apply_decorating_hooks=False,
                )
            else:
                sender = getattr(event, "send", None)
                result_builder = getattr(event, "chain_result", None)
                if not callable(sender) or not callable(result_builder):
                    return
                sent = await sender(result_builder(records))
            if sent is False:
                return
        except Exception as exc:
            logger.warning(
                "表情表达后台语音投递失败: error_type=%s",
                type(exc).__name__,
            )
            return

        self._mark_tts_session_sent(event)
        session = str(getattr(event, "unified_msg_origin", "") or "")
        if session:
            state = getattr(self, "_tts_auto_voice_last_at", None)
            if not isinstance(state, dict):
                state = {}
                self._tts_auto_voice_last_at = state
            state[session] = time.time()
        logger.info(
            "表情表达后台语音已在正文和图片后单独送达: session=%s records=%s",
            _single_line(session, 120) or "unknown",
            len(records),
        )

    async def _maybe_convert_plain_reply_to_tts(self, text: str, event: Any) -> list[Any]:
        mode = self._tts_setting("tts_generation_mode", "fast_tag")
        if self._tts_text_is_provider_safety_refusal(text):
            logger.info(
                "提供商安全回执保持纯文字,不进入 TTS: session=%s preview=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                _single_line(text, 140),
            )
            return []
        visible_override, suppress_visible, conversion_source, skip_conversion = self._tts_proactive_segment_visible_policy(event)
        if skip_conversion:
            logger.info(
                "主动分段 TTS 只在首段判定,后续分段保持文字: session=%s text=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                _single_line(text, 100),
            )
            return []
        user_requested_tts = self._event_explicitly_requests_tts(event)
        if self._tts_functional_command_reason(event) and not user_requested_tts:
            return []
        strong_block_reason = self._tts_strong_constraint_block_reason(
            event,
            user_requested_tts=user_requested_tts,
            check_probability=False,
            reason="auto_convert_cooldown",
        )
        if strong_block_reason:
            self._set_tts_hard_block(event, strong_block_reason)
            return []
        should_convert = mode == "postprocess" or user_requested_tts
        reason = "explicit_request" if user_requested_tts else ("postprocess" if should_convert else "")
        if not should_convert:
            ok, reason = self._auto_voice_trigger_reason(text, event)
            should_convert = ok
        if not should_convert:
            return []
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
        source_text = conversion_source or text
        full_scope = self._tts_setting("tts_conversion_scope", "partial") == "full"
        if mode == "fast_tag" and full_scope:
            # Full fast-tag conversion has one authoritative source: the complete
            # visible reply. Let the spoken-language pass translate it once instead
            # of calling a conversion model whose markup would be discarded below.
            converted = f"<tts>{source_text}</tts>"
        else:
            converted = await self._convert_text_to_tts_markup(
                source_text,
                event,
                full=full_scope,
            )
        if not converted:
            return []
        converted, full_scope_fallback = self._enforce_full_tts_scope_markup(
            converted,
            source_text=source_text,
            event=event,
        )
        if visible_override or suppress_visible:
            try:
                setattr(event, "_private_companion_tts_visible_text_override", visible_override)
                setattr(event, "_private_companion_tts_visible_text_suppress", bool(suppress_visible))
            except Exception:
                pass
        fallback_plain = visible_override if visible_override else ("" if suppress_visible else (full_scope_fallback or source_text))
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
            if not bool(
                getattr(
                    event,
                    "_private_companion_deferred_reaction_tts_active",
                    False,
                )
            ):
                self._tts_auto_voice_last_at[session] = time.time()
            logger.info(
                "TTS强化已转换纯文本回复: reason=%s session=%s %s",
                reason,
                _single_line(session, 80),
                self._tts_chain_log_text(chain),
            )
        return chain

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
        prompt = f"""
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
""".strip()
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
        prompt = f"""
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
""".strip()
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

    def _play_tts_audio_file_windows_silent(
        self,
        path: str,
        *,
        volume: int = 35,
        fade_in_ms: int = 0,
    ) -> None:
        source_path = path
        if Path(path).suffix.lower() == ".wav":
            path = self._prepare_windows_wav_for_playback(path)
        try:
            if self._run_windows_media_player_script(path, use_wpf=True, volume=volume, fade_in_ms=fade_in_ms):
                return
            if self._run_windows_media_player_script(path, use_wpf=False, volume=volume, fade_in_ms=fade_in_ms):
                return
            raise RuntimeError("Windows 后台播放器均未能播放该音频")
        finally:
            source = Path(source_path)
            playback = Path(path)
            expected = source.with_name(f"{source.stem}.playback.wav")
            if playback != source and playback == expected:
                try:
                    playback.unlink(missing_ok=True)
                except Exception as exc:
                    logger.debug(
                        "清理 TTS 播放修复文件失败: %s",
                        _single_line(exc, 120),
                    )

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
            logger.debug("修正 WAV 播放头失败，使用原文件: %s", _single_line(exc, 120))
            return path

    def _run_windows_media_player_script(
        self,
        path: str,
        *,
        use_wpf: bool,
        volume: int = 35,
        fade_in_ms: int = 0,
    ) -> bool:
        volume = max(0, min(100, int(volume)))
        fade_in_ms = max(0, min(5000, int(fade_in_ms)))
        playback_env = os.environ.copy()
        playback_env["PRIVATE_COMPANION_TTS_AUDIO_PATH"] = str(Path(path).expanduser().resolve())
        playback_env["PRIVATE_COMPANION_TTS_VOLUME"] = str(volume)
        playback_env["PRIVATE_COMPANION_TTS_FADE_MS"] = str(fade_in_ms)
        if use_wpf:
            script = (
                "$p = [System.IO.Path]::GetFullPath($env:PRIVATE_COMPANION_TTS_AUDIO_PATH); "
                "$vol = [Math]::Max(0, [Math]::Min(100, [int]$env:PRIVATE_COMPANION_TTS_VOLUME)); "
                "$fade = [Math]::Max(0, [Math]::Min(5000, [int]$env:PRIVATE_COMPANION_TTS_FADE_MS)); "
                "Add-Type -AssemblyName PresentationCore; "
                "$player = New-Object System.Windows.Media.MediaPlayer; "
                "$player.Volume = $(if ($fade -gt 0) { 0 } else { $vol / 100.0 }); "
                "$player.Open([Uri]::new($p)); "
                "$deadline = (Get-Date).AddSeconds(10); "
                "while (-not $player.NaturalDuration.HasTimeSpan -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 50 }; "
                "$duration = if ($player.NaturalDuration.HasTimeSpan) { $player.NaturalDuration.TimeSpan.TotalMilliseconds } else { 5000 }; "
                "$player.Play(); "
                "if ($fade -gt 0) { $stepMs = [Math]::Max(20, [int]($fade / 10)); for ($i = 1; $i -le 10; $i++) { Start-Sleep -Milliseconds $stepMs; $player.Volume = ($vol / 100.0) * ($i / 10.0) } }; "
                "Start-Sleep -Milliseconds ([Math]::Min([Math]::Max([int]$duration + 300, 800), 90000)); "
                "$player.Close()"
            )
            args = ["powershell", "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass", "-Command", script]
        else:
            script = (
                "$p = [System.IO.Path]::GetFullPath($env:PRIVATE_COMPANION_TTS_AUDIO_PATH); "
                "$vol = [Math]::Max(0, [Math]::Min(100, [int]$env:PRIVATE_COMPANION_TTS_VOLUME)); "
                "$fade = [Math]::Max(0, [Math]::Min(5000, [int]$env:PRIVATE_COMPANION_TTS_FADE_MS)); "
                "$player = New-Object -ComObject WMPlayer.OCX; "
                "$player.settings.volume = $(if ($fade -gt 0) { 0 } else { $vol }); "
                "$player.URL = $p; "
                "$player.controls.play(); "
                "if ($fade -gt 0) { $stepMs = [Math]::Max(20, [int]($fade / 10)); for ($i = 1; $i -le 10; $i++) { Start-Sleep -Milliseconds $stepMs; $player.settings.volume = [int]($vol * $i / 10) } }; "
                "$deadline = (Get-Date).AddSeconds(90); "
                "while ($player.playState -notin 1,8 -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 100 }; "
                "$player.close()"
            )
            args = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script]
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=95,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            env=playback_env,
        )
        if result.returncode == 0:
            return True
        logger.debug(
            "Windows 静默播放方式失败: mode=%s code=%s err=%s",
            "wpf" if use_wpf else "wmp",
            result.returncode,
            _single_line(result.stderr or result.stdout, 160),
        )
        return False

    async def _post_tts_live_subtitle(self, text: str) -> None:
        if not bool(self._tts_setting("enable_tts_live_subtitle_sync", False)):
            return
        cleaned = _single_line(text, 500)
        if not cleaned:
            return
        url = str(self._tts_setting("tts_live_subtitle_url", "") or "").strip() or "http://127.0.0.1:18081/show"

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
            logger.info("已同步 TTS 文本到直播打字机字幕: %s", _single_line(cleaned, 80))
        except Exception as exc:
            logger.debug("TTS 直播字幕同步失败: %s", _single_line(exc, 120))

    async def _after_tts_audio_generated(
        self,
        audio_path: str,
        spoken_text: str,
        *,
        source: str = "",
        subtitle_text: str = "",
        allow_local_playback: bool = True,
    ) -> None:
        is_live_reply = source == "bili_live_auto_reply"
        visible_text = subtitle_text or spoken_text
        subtitle_task = (
            self._create_tts_background_task(
                self._post_tts_live_subtitle(visible_text),
                label="tts_live_subtitle",
            )
            if is_live_reply
            else None
        )
        local_playback_enabled = bool(self._tts_setting("enable_tts_local_playback", False))
        live_only = bool(self._tts_setting("enable_tts_local_playback_live_only", False))
        should_play_local = (
            allow_local_playback
            and local_playback_enabled
            and (is_live_reply or not live_only)
        )
        if should_play_local:
            interval = max(0.0, float(self._tts_setting("tts_local_playback_min_interval_seconds", 0.0) or 0.0))
            now = time.time()
            retry_after = float(getattr(self, "_tts_local_playback_retry_after", 0.0) or 0.0)
            if retry_after > now:
                logger.debug(
                    "TTS 本机播放处于失败退避: remain=%.1fs failures=%s",
                    retry_after - now,
                    int(getattr(self, "_tts_local_playback_failures", 0) or 0),
                )
                if subtitle_task is not None:
                    await subtitle_task
                return
            if interval <= 0 or now - float(getattr(self, "_tts_local_playback_last_at", 0.0) or 0.0) >= interval:
                self._tts_local_playback_last_at = now
                try:
                    await asyncio.to_thread(self._open_tts_audio_file_local, audio_path)
                    self._tts_local_playback_failures = 0
                    self._tts_local_playback_retry_after = 0.0
                    logger.info(
                        "已触发 TTS 本机播放: source=%s live_only=%s path=%s",
                        source or "unknown",
                        live_only,
                        _single_line(audio_path, 160),
                    )
                except Exception as exc:
                    failures = int(getattr(self, "_tts_local_playback_failures", 0) or 0) + 1
                    retry_seconds = min(1800.0, 30.0 * (2 ** min(6, failures - 1)))
                    self._tts_local_playback_failures = failures
                    self._tts_local_playback_retry_after = now + retry_seconds
                    logger.warning(
                        "TTS 本机播放失败,已进入退避: failures=%s retry=%.0fs error=%s",
                        failures,
                        retry_seconds,
                        _single_line(exc, 120),
                    )
        if subtitle_task is not None:
            await subtitle_task

    def _tts_visible_fallback_text(
        self,
        text: str,
        fallback_plain: str = "",
        *,
        event: Any = None,
    ) -> str:
        normalized = self._normalize_tts_tags(str(text or ""))
        visible = re.sub(r"<tts\b[^>]*>.*?</tts>", "", normalized, flags=re.IGNORECASE | re.DOTALL)
        visible = re.sub(TTS_TAG_PATTERN, "", visible).strip()
        if visible:
            return self._sanitize_tts_visible_text(
                visible,
                max_chars=self._tts_complete_text_limit(visible, 800),
            )
        fallback = str(fallback_plain or "").strip()
        if fallback:
            return self._sanitize_tts_visible_text(
                fallback,
                max_chars=self._tts_complete_text_limit(fallback, 800),
            )
        if self._tts_voice_language_for_event(event) == "zh":
            visible_zh = re.sub(TTS_TAG_PATTERN, "", normalized).strip()
            return self._sanitize_tts_visible_text(
                visible_zh,
                max_chars=self._tts_complete_text_limit(visible_zh, 800),
            )
        return ""

    def _tts_plain_markup_fallback_text(self, text: Any) -> str:
        """Demote a failed TTS structure to its original visible plain text."""
        normalized = self._normalize_tts_tags(str(text or ""))
        plain = self._strip_any_tts_markup(normalized)
        return self._sanitize_tts_visible_text(
            plain,
            max_chars=self._tts_complete_text_limit(plain, 800),
        )

    def _enforce_full_tts_scope_markup(
        self,
        text: str,
        *,
        source_text: str = "",
        event: Any = None,
    ) -> tuple[str, str]:
        """Turn any tagged full-scope reply into one structurally complete voice block."""
        normalized = self._normalize_tts_tags(str(text or ""))
        if self._tts_setting("tts_conversion_scope", "partial") != "full":
            return normalized, ""
        if not re.search(r"<tts\b[^>]*>.*?</tts>", normalized, flags=re.IGNORECASE | re.DOTALL):
            return normalized, ""

        voice_lang = self._tts_voice_language_for_event(event)
        delivery_mode = self._tts_setting("tts_delivery_mode", "voice_and_text")
        foreign_text_mode = self._tts_setting("tts_foreign_text_mode", "translation")
        complete_limit = self._tts_complete_text_limit(
            source_text or normalized,
            1600,
        )
        full_source = self._sanitize_tts_visible_text(source_text, max_chars=complete_limit)
        if (
            not full_source
            and voice_lang != "zh"
            and delivery_mode == "voice_and_text"
            and foreign_text_mode in {"translation", "bilingual"}
        ):
            matches = list(
                re.finditer(
                    r"<tts\b[^>]*>.*?</tts>",
                    normalized,
                    flags=re.IGNORECASE | re.DOTALL,
                )
            )
            if len(matches) == 1 and not normalized[: matches[0].start()].strip():
                spoken = self._normalize_tts_spoken_text(
                    matches[0].group(0),
                    provider_kind="generic",
                )
                visible_after = self._sanitize_tts_visible_text(
                    normalized[matches[0].end() :],
                    max_chars=self._tts_complete_text_limit(normalized, 1600),
                )
                spoken_units = len(
                    re.findall(
                        r"[\u3040-\u30ff\u31f0-\u31ff\u4e00-\u9fffA-Za-z0-9]",
                        spoken,
                    )
                )
                visible_units = len(
                    re.findall(
                        r"[\u4e00-\u9fffA-Za-z0-9]",
                        visible_after,
                    )
                )
                # One complete foreign block followed by Chinese is the canonical
                # voice-and-translation form, so keep the authored spoken text.
                if (
                    spoken
                    and visible_after
                    and visible_units <= max(8, spoken_units * 2)
                    and not self._tts_text_needs_language_conversion(
                        spoken,
                        provider_kind="generic",
                        event=event,
                    )
                    and self._tts_visible_text_is_allowed_after_voice(visible_after)
                ):
                    logger.info(
                        "TTS全量快速标签已保留主模型外语块,中文仅作可见译文: voice=%s visible=%s",
                        _single_line(spoken, 80),
                        _single_line(visible_after, 80),
                    )
                    return normalized, ""
            # A noncanonical full reply may contain leading text, multiple blocks, or
            # substantially more visible content than the foreign block can cover.
            # Rebuild those cases from the complete visible source.
            visible = re.sub(
                r"<tts\b[^>]*>.*?</tts>",
                "",
                normalized,
                flags=re.IGNORECASE | re.DOTALL,
            )
            full_source = self._sanitize_tts_visible_text(
                visible,
                max_chars=self._tts_complete_text_limit(visible, 1600),
            )
        if not full_source:
            full_source = self._sanitize_tts_visible_text(
                self._strip_any_tts_markup(normalized),
                max_chars=self._tts_complete_text_limit(normalized, 1600),
            )
        if not full_source:
            return normalized, ""
        return f"<tts>{full_source}</tts>", full_source

    async def _finalize_tts_delivery_chain(
        self,
        output: list[Any],
        *,
        event: Any,
        provider_kind: str,
        fallback_plain: str,
        successful_spoken: list[str],
        suppress_visible: bool,
    ) -> list[Any]:
        records = [comp for comp in output if isinstance(comp, Record)]
        if not records:
            return output
        if suppress_visible:
            return records
        if self._tts_setting("tts_delivery_mode", "voice_and_text") == "voice_only":
            if self._tts_setting("tts_conversion_scope", "partial") == "full":
                return records
            remaining_text = "\n".join(
                str(getattr(comp, "text", "") or "").strip()
                for comp in output
                if isinstance(comp, Plain) and str(getattr(comp, "text", "") or "").strip()
            ).strip()
            remaining_plain = self._mark_tts_visible_plain(remaining_text, max_chars=1400)
            return records + ([remaining_plain] if remaining_plain is not None else [])
        plain_text = "\n".join(
            str(getattr(comp, "text", "") or "").strip()
            for comp in output
            if isinstance(comp, Plain) and str(getattr(comp, "text", "") or "").strip()
        ).strip()
        spoken_text = "\n".join(item for item in successful_spoken if item).strip()
        voice_lang = self._tts_voice_language_for_event(event)
        if voice_lang == "zh":
            visible_source = fallback_plain or plain_text or spoken_text
            visible_text = self._sanitize_tts_visible_text(
                visible_source,
                max_chars=self._tts_complete_text_limit(visible_source, 1200),
            )
        else:
            foreign_mode = self._tts_setting("tts_foreign_text_mode", "translation")
            foreign_visible_requested = self._event_explicitly_requests_foreign_visible_text(
                event,
                voice_language=voice_lang,
            )
            translated_text = ""
            if fallback_plain and self._tts_visible_text_is_allowed_after_voice(fallback_plain):
                translated_text = self._sanitize_tts_visible_text(fallback_plain, max_chars=1200)
            elif self._tts_visible_text_is_allowed_after_voice(plain_text):
                translated_text = self._sanitize_tts_visible_text(plain_text, max_chars=1200)
            if not translated_text and foreign_mode in {"translation", "bilingual"} and successful_spoken:
                translations = [
                    await self._translate_tts_spoken_to_chinese(item, event, provider_kind=provider_kind)
                    for item in successful_spoken
                ]
                translated_text = "\n".join(item for item in translations if item).strip()
            if foreign_visible_requested or foreign_mode == "original":
                visible_text = spoken_text
            elif foreign_mode == "bilingual":
                visible_text = "\n".join(item for item in (spoken_text, translated_text) if item).strip()
            else:
                visible_text = translated_text
        visible_plain = self._mark_tts_visible_plain(
            visible_text,
            max_chars=self._tts_complete_text_limit(visible_text, 1400),
        )
        return records + ([visible_plain] if visible_plain is not None else [])

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
