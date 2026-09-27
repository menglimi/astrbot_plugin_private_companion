# -*- coding: utf-8 -*-
"""TtsEnhancementMarkupRecordMixin。

由 tools/split_mixin_domain.py 从 tts_enhancement.py 机械抽取（18 个方法 + 0 个模块级名字 + 0 个类级赋值 / 316 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 TtsEnhancementMixin）。
"""
from __future__ import annotations

import re
import time
import uuid
from .helpers import (
    _normalize_outbound_punctuation_flow,
    _single_line,
    _strip_history_media_markers,
    _strip_nonstandard_chat_control_tags,
)
from .persona_config import runtime_persona_setting
from .tts_enhancement_shared import (
    EMOTION_TAG_PATTERN,
    PRIVATE_TTS_BLOCK_TOKEN_PATTERN,
    TTS_BLOCK_TOKEN_PATTERN,
    TTS_TAG_PATTERN,
    TTS_VISIBLE_EMOTION_CUES,
    TTS_VISIBLE_LABEL_PATTERN,
)
from typing import Any
from urllib.parse import unquote, urlparse
from .tts_enhancement_shared import Plain
from .tts_enhancement_shared import Record
from .tts_enhancement_shared import logger



class TtsEnhancementMarkupRecordMixin:
    """TtsEnhancementMarkupRecordMixin（从 TtsEnhancementMixin 拆出）。"""


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
        cleaned = str(text or "")
        if bool(runtime_persona_setting(self, "enable_framework_error_leak_guard", True)):
            cleaned = _strip_history_media_markers(cleaned)
        cleaned = self._strip_any_tts_markup(cleaned)
        if bool(runtime_persona_setting(self, "enable_tts_enhancement", False)):
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
        if not bool(runtime_persona_setting(self, "enable_framework_error_leak_guard", True)):
            return str(text or "")
        source = str(text or "")
        if not source:
            return ""
        source = _strip_nonstandard_chat_control_tags(
            source, tts_enabled=bool(runtime_persona_setting(self, "enable_tts_enhancement", False))
        )
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
