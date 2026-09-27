# -*- coding: utf-8 -*-
"""会话节流与触发信号域。

由 tools/split_mixin_domain.py 从 tts_enhancement.py 机械抽取（18 个方法 + 0 个模块级名字 + 0 个类级赋值 / 291 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 TtsEnhancementMixin）。
"""
from __future__ import annotations

import random
import re
import time
from .helpers import _single_line
from .tts_enhancement_shared import logger
from typing import Any



class TtsEnhancementSessionTriggerMixin:
    """会话节流与触发信号域（从 TtsEnhancementMixin 拆出）。"""


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
