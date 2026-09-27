# -*- coding: utf-8 -*-
"""TtsEnhancementFishaudioEmotionMixin。

由 tools/split_mixin_domain.py 从 tts_enhancement.py 机械抽取（14 个方法 + 0 个模块级名字 + 0 个类级赋值 / 347 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 TtsEnhancementMixin）。
"""
from __future__ import annotations

import re
from .helpers import _single_line
from .tts_enhancement_shared import (
    DEFAULT_TTS_SANITIZE_FILTER_WORDS,
    DEFAULT_TTS_SANITIZE_REMOVE_PATTERNS,
    DEFAULT_TTS_SANITIZE_REPLACEMENTS,
    EMOTION_TAG_PATTERN,
    FISH_AUDIO_AUTO_BLOCKED_EFFECTS,
    FISH_AUDIO_CUE_ALIASES,
    FISH_AUDIO_EMOTION_MODES,
    FISH_AUDIO_EXPLICIT_SIGH_PATTERN,
    FISH_AUDIO_S1_ALIAS_OVERRIDES,
    FISH_AUDIO_S1_CUES,
    FISH_AUDIO_S1_CUE_PATTERN,
    FISH_AUDIO_S2_CUE_PATTERN,
    TTS_EMOTION_PLACEHOLDER_PREFIX,
    TTS_MARKDOWN_LINK_PATTERN,
    TTS_SPOKEN_URL_PATTERN,
    logger,
)



class TtsEnhancementFishaudioEmotionMixin:
    """TtsEnhancementFishaudioEmotionMixin（从 TtsEnhancementMixin 拆出）。"""


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
