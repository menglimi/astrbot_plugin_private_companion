"""Validated voice presentation policy and model-authored spoken spans."""

from dataclasses import dataclass, replace
from datetime import timedelta
import json
import math
import re

from companion_v2.conversation.segment_control import controlled_parts
from companion_v2.execution.models import digest, stamp
from companion_v2.foundation.refs import ScopeKind
from .voice_markup import (
    VOICE_PROTOCOL_GT, VOICE_PROTOCOL_LT, VOICE_TAG_NAME_PATTERN, canonical_voice_tag,
    slice_speech, strip_performance_controls, visible_speech,
)


_TAG = re.compile(
    rf"(?is){VOICE_PROTOCOL_LT}\s*(?P<open_tag>{VOICE_TAG_NAME_PATTERN})\b[^>]*?{VOICE_PROTOCOL_GT}(?P<speech>.*?)"
    rf"{VOICE_PROTOCOL_LT}\s*/\s*(?P<close_tag>{VOICE_TAG_NAME_PATTERN})\s*{VOICE_PROTOCOL_GT}"
)
_STRAY_TAG = re.compile(
    rf"(?is){VOICE_PROTOCOL_LT}\s*/?\s*{VOICE_TAG_NAME_PATTERN}\b[^>]*?{VOICE_PROTOCOL_GT}"
)
_BARE_STRAY_TAG = re.compile(
    rf"(?is){VOICE_PROTOCOL_LT}\s*/?\s*{VOICE_TAG_NAME_PATTERN}\b(?![^>]*>)"
)
_TURN_LANGUAGE_NAMES = {
    "ja": r"(?:日语|日語|日文|日本语|日本語|japanese)",
    "en": r"(?:英语|英語|英文|english)",
    "zh": r"(?:中文|汉语|漢語|普通话|国语|國語|中国语|中国語|chinese)",
}


def requested_voice_language(input_text):
    """Recognize a direct, turn-local language request without changing settings."""
    if not isinstance(input_text, str) or not input_text.strip():
        return ""
    text = input_text.strip()
    text = re.sub(r"^(?:\s*\[At:\d+\]\s*)+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^@\S+\s+", "", text).strip()
    if re.search(r"(?:是什么意思|怎么理解|如何理解|指什么|含义|语法|词义|单词怎么读|meaning of|what does .{1,48} mean)", text, re.I):
        return ""
    if (re.search(r"(?:不要|别|不用|不必|不需要|取消|停止|避免).{0,16}(?:用|说|讲|回复|回答|答复|朗读|念|读)", text)
            or re.search(r"\b(?:do not|don't|never|stop|avoid)\b.{0,32}\b(?:speak|say|reply|answer|read)\b", text, re.I)):
        return ""
    if (re.search(r"(?:翻译|译成|翻成|改写|怎么说|如何表达|翻訳).{0,24}(?:成|为|into|to)?\s*(?:英语|英語|英文|日语|日語|日文|中文|汉语|日本語|english|japanese|chinese)", text, re.I)
            or re.search(r"\b(?:translate|translation)\b.{0,36}\b(?:into|to)\b", text, re.I)):
        return ""

    for language, name in _TURN_LANGUAGE_NAMES.items():
        chinese_request = re.search(
            rf"(?:请|麻烦|这次|这回|本次|接下来)?\s*(?:用|以)\s*{name}\s*(?:语音)?\s*[，,、\s]*(?:跟我|对我|给我)?\s*(?:回复|回答|答复|说|讲|朗读|念|读)(?:一句|一段|一下)?",
            text, re.I,
        ) or re.search(
            rf"(?:请|麻烦)?\s*{name}\s*(?:语音)?\s*(?:回复|回答|答复|朗读|念|读)", text, re.I,
        )
        if chinese_request:
            return language

    english_request = re.search(
        r"\b(?:please\s+)?(?:speak|say|reply|answer|read)(?:\s+(?:to\s+me|it))?\s+in\s+(english|japanese|chinese)\b",
        text, re.I,
    )
    if english_request:
        language = english_request.group(1).casefold()
        return {"english": "en", "japanese": "ja", "chinese": "zh"}[language]

    japanese_request = re.search(
        r"(?:英語|日本語|中国語|中文)で(?:話して|答えて|返事して|返信して|読んで|朗読して)(?:ください|お願い)?",
        text,
    )
    if japanese_request:
        return "en" if japanese_request.group(0).startswith("英語") else \
            "ja" if japanese_request.group(0).startswith("日本語") else "zh"
    return ""


def voice_policy_for_turn(policy, input_text):
    """Apply an explicit request to this turn's copy of the saved voice policy."""
    if policy is None:
        return None
    language = requested_voice_language(input_text)
    return replace(policy, language=language) if language and language != policy.language else policy


def strip_voice_markup(text):
    """Remove internal voice wrappers while keeping their authored text visible."""
    if type(text) is not str:
        return text
    return strip_performance_controls(_strip_stray_voice_tags(_TAG.sub(_visible_voice_match, text)))


def _matching_voice_tags(match):
    return canonical_voice_tag(match.group("open_tag")) == canonical_voice_tag(match.group("close_tag"))


def _visible_voice_match(match):
    if not _matching_voice_tags(match):
        return match.group(0)
    return visible_speech(match.group("speech"))


def _strip_stray_voice_tags(text):
    return _BARE_STRAY_TAG.sub("", _STRAY_TAG.sub("", text))


def voice_wrapper_pairing(text):
    """Count well-formed voice wrappers and the stray tag tokens left over."""
    if type(text) is not str:
        return 0, 0
    paired = sum(1 for match in _TAG.finditer(text) if _matching_voice_tags(match))
    remaining = _STRAY_TAG.sub("", text)
    tokens = len(_STRAY_TAG.findall(text)) + len(_BARE_STRAY_TAG.findall(remaining))
    return paired, max(tokens - 2 * paired, 0)


def _has_chinese_translation(text):
    visible = _strip_stray_voice_tags(text)
    visible = "".join(controlled_parts(visible)[0]).strip()
    if not visible or re.search(r"[\u3040-\u30ff\u31f0-\u31ff]", visible):
        return False
    han = re.findall(r"[\u4e00-\u9fff]", visible)
    if len(han) >= 4:
        return True
    if len(han) < 2:
        return False
    markers = ("的", "了", "是", "我", "你", "这", "那", "不", "有", "吗", "呢", "吧",
               "呀", "啊", "哦", "嘛", "想", "要", "可以", "知道", "今天", "明天", "喜欢", "辛苦")
    short_phrases = ("你好", "您好", "再见", "谢谢", "晚安", "早安", "拜拜")
    return any(marker in visible for marker in markers) or any(phrase in visible for phrase in short_phrases)


@dataclass(frozen=True)
class VoiceReplyPolicy:
    trigger_mode: str = "always"
    generation_mode: str = "plain"
    conversion_scope: str = "full"
    segment_mode: str = "whole"
    delivery_mode: str = "voice_and_text"
    position: str = "after_media"
    language: str = "zh"
    foreign_text_mode: str = "original"
    probability: float = 1.0
    private_probability: float = -1.0
    group_probability: float = -1.0
    cooldown_seconds: int = 0
    private_cooldown_seconds: int = -1
    group_cooldown_seconds: int = -1
    max_speech_chars: int = 1800
    timeout_seconds: int = 30
    trigger_keywords: str = ""
    primary_mention_keywords: str = ""
    primary_mention_probability: float = 0.8
    primary_mention_prompt: str = ""
    provider_id: str = ""
    zh_provider_id: str = ""
    ja_provider_id: str = ""
    en_provider_id: str = ""
    private_zh_provider_id: str = ""
    private_ja_provider_id: str = ""
    private_en_provider_id: str = ""
    group_zh_provider_id: str = ""
    group_ja_provider_id: str = ""
    group_en_provider_id: str = ""
    extra_prompt: str = ""

    def __post_init__(self):
        choices = ((self.trigger_mode, {"always", "request", "adaptive"}),
                   (self.generation_mode, {"plain", "model_tag"}),
                   (self.conversion_scope, {"full", "partial"}),
                   (self.segment_mode, {"whole", "each", "first", "last"}),
                   (self.delivery_mode, {"voice_and_text", "voice_only"}),
                   (self.position, {"after_media", "after_text", "before_text"}),
                   (self.language, {"zh", "ja", "en"}),
                   (self.foreign_text_mode, {"original", "translation", "bilingual"}))
        if any(value not in options for value, options in choices):
            raise ValueError("voice reply mode is unsupported")
        for value in (self.probability, self.private_probability, self.group_probability,
                      self.primary_mention_probability):
            if type(value) not in (int, float) or not math.isfinite(value) or not -1 <= value <= 1:
                raise ValueError("voice reply probability must be between zero and one, or -1 for inheritance")
        if not 0 <= self.probability <= 1 or not 0 <= self.primary_mention_probability <= 1:
            raise ValueError("voice reply probability must be between zero and one")
        for value, low, high in ((self.cooldown_seconds, 0, 86400),
                                 (self.private_cooldown_seconds, -1, 86400),
                                 (self.group_cooldown_seconds, -1, 86400),
                                 (self.max_speech_chars, 1, 10000), (self.timeout_seconds, 1, 120)):
            if type(value) is not int or not low <= value <= high:
                raise ValueError("voice reply limit is invalid")
        for value, limit in ((self.trigger_keywords, 2000), (self.primary_mention_keywords, 2000),
                             (self.primary_mention_prompt, 2000), (self.provider_id, 128),
                             (self.zh_provider_id, 128), (self.ja_provider_id, 128),
                             (self.en_provider_id, 128), (self.private_zh_provider_id, 128),
                             (self.private_ja_provider_id, 128), (self.private_en_provider_id, 128),
                             (self.group_zh_provider_id, 128), (self.group_ja_provider_id, 128),
                             (self.group_en_provider_id, 128), (self.extra_prompt, 2000)):
            if not isinstance(value, str) or len(value) > limit:
                raise ValueError("voice reply text setting is invalid")
        words = tuple(word.strip() for word in re.split(r"[,，;；\n]+", self.primary_mention_keywords)
                      if word.strip())
        if len(words) > 80 or any(len(word) > 80 for word in words):
            raise ValueError("primary mention voice keywords are invalid")

    @property
    def uses_model_tags(self):
        return (self.generation_mode == "model_tag" or self.trigger_mode == "adaptive"
                or self.conversion_scope == "partial")

    @property
    def instruction(self):
        if not self.uses_model_tags:
            return ""
        prompt = ("标签内就是要朗读、给用户看的那句话：标签外不再复述同一句，只写另说的内容。"
                  "停顿、呼吸等表演指令只用方括号，如 [pause]，不要写成 <pause>。"
                  "只有实际要朗读的原文放在 <pc_tts> 与 </pc_tts> 之间；标签内不放分段标记、代码、链接或图片说明。")
        if self.conversion_scope == "partial":
            prompt = "按本轮语境选取适合口述的自然短句放进语音标签，其余内容保留为文字；无需让语音覆盖整条回复。" + prompt
        elif self.trigger_mode == "request":
            prompt = "用户本轮请求语音时，用语音标签承载实际说给对方听的完整回应。" + prompt
        elif self.trigger_mode == "always":
            prompt = "本轮采用语音回复，普通短句也将完整口语回应放进语音标签，不必等用户重复请求。" + prompt
        else:
            prompt = "本轮按语境决定是否使用语音；适合口述的内容写入语音标签，没有合适内容时直接写普通正文。" + prompt
        prompt += (
            "标签由程序消费，不向用户解释或当作代码展示。代码、链接等适合阅读的内容留在标签外；"
            "本轮明确要求只用文字时，尊重这次要求。"
        )
        if self.conversion_scope == "full" and self.trigger_mode in {"always", "request"}:
            prompt += "同一条回复若自然分成多段，各段都保持一致的语音标签与可见文字配对；不要只给前几段加标签，却把后续仍属于本次口语回应的内容留在标签外。"
        prompt += f"每段朗读控制在 {self.max_speech_chars} 字以内；较长内容按自然语意停顿分成多个语音标签。"
        if self.language != "zh":
            label = "日语" if self.language == "ja" else "英语"
            prompt += f"标签内用自然{label}完整表达该段意思，语气贴合当前对话。"
            if self.foreign_text_mode == "translation":
                prompt += "每个语音标签后紧接与该段完整对应的自然中文译文，不加标题或省略关键信息；译文作为可见文字。"
                if self.conversion_scope == "full" and self.trigger_mode in {"always", "request"}:
                    prompt += "中文聊天正文都对应相邻的朗读片段，不在译文后另加没有朗读的口语回应；代码和链接可单独保留。"
                example = ("<pc_tts>おやすみ。ゆっくり休んでね。</pc_tts>晚安，好好休息呀。"
                           if self.language == "ja" else "<pc_tts>Good night. Rest well.</pc_tts>晚安，好好休息呀。")
                prompt += "格式示例：" + example + " 示例仅说明格式，内容和语气按当前对话生成。"
            elif self.foreign_text_mode == "bilingual":
                prompt += "每个标签后附完整、自然的中文译文，不加标题；可见文字保留外语原文与译文。"
            else:
                prompt += "标签外保留自然显示文字即可，不必翻译；不要重复朗读原文。"
        if self.extra_prompt.strip():
            prompt += "\n" + self.extra_prompt.strip()
        return prompt

    def provider_for_language(self, channel=None):
        transient = getattr(self, "_provider_override", "")
        if transient:
            return transient
        if channel in ("private", "group"):
            channel_override = getattr(self, f"{channel}_{self.language}_provider_id", "")
            if channel_override:
                return channel_override
        return getattr(self, self.language + "_provider_id") or self.provider_id

    def chance(self, kind):
        override = self.group_probability if kind == "group" else self.private_probability
        return float(self.probability if override < 0 else override)

    def cooldown(self, kind):
        override = self.group_cooldown_seconds if kind == "group" else self.private_cooldown_seconds
        return self.cooldown_seconds if override < 0 else override

    def keywords(self):
        return tuple(word.strip().casefold() for word in re.split(r"[,，;；\n]+", self.trigger_keywords)
                     if word.strip())

    def primary_mention_words(self):
        return tuple(word.strip().casefold() for word in re.split(r"[,，;；\n]+", self.primary_mention_keywords)
                     if word.strip())


def with_voice_override(policy, *, provider_id="", model="", voice=""):
    """Attach a per-turn provider override without changing persisted fields."""
    policy = replace(policy)
    object.__setattr__(policy, "_provider_override", str(provider_id or ""))
    object.__setattr__(policy, "_model_override", str(model or ""))
    object.__setattr__(policy, "_voice_override", str(voice or ""))
    return policy


def capability_instruction(policy):
    language = {"zh": "中文", "ja": "日语", "en": "英语"}[policy.language]
    delivery = "优先语音呈现" if policy.delivery_mode == "voice_only" else "语音与文字并行"
    mode = {"always": "语音回复", "request": "应用户请求语音回复", "adaptive": "按语境选择语音"}[policy.trigger_mode]
    return (f"本轮呈现方式：{mode}，目标语种{language}，{delivery}。"
            "本轮设置已通过开关和触发条件检查；人格中旧的仅文字、需另行请求语音等呈现规则由本轮设置替代，角色身份和语气仍遵循人格。"
            "对话模型生成朗读文字，程序在投递时交给 TTS Provider 合成；合成失败时退回文字。"
            "没有成功投递结果前，不要声称已经发送语音。")


# One statement written twice: the wrapper's words and the body copy of them.
# The wrapper is the speaker's full statement, so it is the copy that stays; its
# span still covers exactly the words it voices.
_TAIL_LETTER = re.compile(r"[^\W\d_]")
# A word character carries meaning: a letter, a digit or a CJK character.
_WORD_CHARACTER = re.compile(r"[\w\u4e00-\u9fff]")
# Han characters are words by themselves, so a clause that adds them says more.
_HAN = re.compile(r"[\u4e00-\u9fff]")
# A tail attached to the copy with no space and no Han character decorates the
# ending it follows only while it stays this short: ``mua~`` decorates, a run of
# letters is wording.
_ATTACHED_TAIL = 4
# Punctuation that closes a statement. A statement ends at one of these, and the
# mark itself is spoken rather than written, so it takes no part in comparing a
# statement with a copy of it.
_SENTENCE_END = frozenset("。！？!?")
# Punctuation inside a statement: a trailing off, a pause, a list separator. A
# copy may punctuate the middle of the same statement differently (``A…B。``
# spoken, ``A…B，mua~`` written), so these marks are dropped from the wording
# without ending the statement they stand in.
_CLAUSE_MARK = frozenset("…~〜～，,、；;：:·・♪♫♬")


class _VisibleText:
    """The displayed reply under construction, and where the next text starts."""

    def __init__(self):
        self.parts, self.length = [], 0

    def add(self, text):
        self.parts.append(text)
        self.length += len(text)
        return self.length


def _body_text(text):
    """Show body text: delivery marks go, performance marks stay as written.

    A pause mark and a bracketed direction are delivery, so they are read out but
    never displayed; ``voice_markup`` owns which brackets those are. Paired
    performance marks such as ``<soft>`` are left exactly as written, because
    prose may mention them and only the wrapper decides whether one is delivery.
    """
    return _strip_stray_voice_tags(strip_performance_controls(text))


def _wording(text):
    """The words a listener hears, each with its end position in ``text`` itself.

    Delivery takes no part in what is said, so directions, pause marks and
    spacing are skipped. Positions are source positions, because callers cut the
    authored text with them and a cut must never land inside a mark. The marks
    are located once in the source and then skipped, which keeps the scan linear
    and stops a word character from being found inside a mark's own text.
    """
    marks = _control_spans(text)
    words, ends = [], []
    cursor = 0
    while cursor < len(text):
        span = _span_at(marks, cursor)
        if span is not None:
            cursor = span
            continue
        char = text[cursor]
        cursor += 1
        if char.isspace():
            continue
        words.append(char)
        ends.append(cursor)
    return "".join(words), ends


def _control_spans(text):
    """Report where the delivery marks in ``text`` begin and end.

    A mark is found by removing it with the same functions the display side uses
    and seeing which characters disappeared, so both agree on what a mark is
    without sharing private state. Whitespace alone is never a mark.
    """
    kept = visible_speech(strip_performance_controls(_strip_stray_voice_tags(text)))
    spans, cursor = [], 0
    for char in kept:
        if char.isspace():
            continue
        found = text.find(char, cursor)
        if found > cursor:
            spans.append((cursor, found))
        cursor = found + 1
    if cursor < len(text):
        spans.append((cursor, len(text)))
    return tuple((start, end) for start, end in spans
                 if any(not char.isspace() for char in text[start:end]))


def _span_at(spans, cursor):
    """Report where the mark starting at ``cursor`` ends, or ``None``."""
    for start, end in spans:
        if start == cursor:
            return end
    return None


def _statement_words(words):
    """Split wording into statements, keeping only what a listener hears.

    A closing mark ends a statement; a mark inside one is dropped without ending
    it. Punctuation therefore never decides whether two statements are the same
    one, which is what lets a copy punctuated differently be recognised.
    """
    statements, current = [], []
    for char in words:
        if char in _SENTENCE_END:
            if current:
                statements.append("".join(current))
                current = []
        elif char not in _CLAUSE_MARK:
            current.append(char)
    if current:
        statements.append("".join(current))
    return tuple(statements)


def _copy_length(speech, following):
    """Report how much of ``following`` restates the wrapper, ``0`` for none.

    The clause has to open with the wrapper's own wording; punctuation is not
    compared, because a copy may punctuate the same statement differently
    (``A…B。`` spoken, ``A…B，mua~`` written). What follows that wording decides
    whether the clause is one statement said twice or one statement plus new
    speech, and two boundaries make that decidable without any word list:

    * A sentence end in what follows means the copy is over. Only punctuation
      may then remain -- ``晚安。`` restated as ``晚安！！`` -- because anything
      else is a further statement ``晚安！！明天见。`` written after it, which is
      new speech the reader was owed.
    * With no sentence end, the clause either runs straight on or is a separate
      word. A space before it is what English uses between words, so ``走吧 he``
      adds a word and is kept, while ``A…B，mua~`` attaches a short Latin tail
      with no space -- an interjection decorating the ending it follows, which a
      Han tail never is (``别笑我，噗嗤`` says something new).
    """
    inside = _statement_words(_wording(speech)[0])
    if not inside:
        return 0
    outer = _statement_words(_wording(following)[0])
    # The clause has to open with the wrapper's wording. A tail attached to it
    # stays in the same statement (``ABmua``), so the first statement is compared
    # as a prefix rather than for equality.
    if len(outer) < len(inside) or not _opens_with(outer, inside):
        return 0
    speech_end = _wording(speech)[0].rstrip(" ")[-1:] or ""
    return len(following) if _decorates(following, "".join(inside), speech_end) else 0


def _opens_with(outer, inside):
    """Report whether the clause opens with the wrapper's statements.

    The wrapper's last statement may carry the tail that follows it, so that one
    is compared as a prefix while the statements before it must match exactly.
    """
    if len(outer) < len(inside):
        return False
    if outer[:len(inside) - 1] != inside[:-1]:
        return False
    return outer[len(inside) - 1].startswith(inside[-1])


def _decorates(following, insided, speech_end):
    """Report whether everything after the copy in ``following`` is decoration.

    A copy may carry a tail only when the tail decorates the ending it follows.
    Three boundaries make that decidable without any word list:

    * A closing mark after the copy means the statement is over: only
      punctuation may then remain (``晚安！！``), because anything else is a
      further statement and therefore new speech (``晚安！！明天见。``).
    * With no closing mark, the tail has to attach to the copy with no space: a
      space is how a language separates words, so ``走吧 he`` adds one and is
      kept even though ``he`` is short.
    * An attached tail decorates only an ending the wrapper already finished.
      ``A…B。`` is complete, so ``mua~`` after it is decoration; ``A…`` is not, so
      ``B…`` is the rest of the sentence and dropping it would lose content.
    """
    rest = following[_after_copy(following, insided):]
    if not rest:
        return True
    if any(char in _SENTENCE_END for char in rest):
        return not _meaning_of(rest)
    tail = rest.lstrip()
    if len(tail) != len(rest):
        # A space opens a new word: a clause saying something else.
        return False
    body = _meaning_of(tail)
    if _HAN.search(body) or not speech_end in _SENTENCE_END:
        # Han characters are words on their own, and a wrapper that trails off
        # has not finished its statement, so nothing after it is decoration.
        return False
    return bool(body) and len(body) <= _ATTACHED_TAIL


def _meaning_of(text):
    """Report the text with spacing, closing marks and decoration removed."""
    return "".join(char for char in text
                   if not char.isspace() and char not in _SENTENCE_END
                   and char not in _CLAUSE_MARK)


def _after_copy(following, insided):
    """Report where the wrapper's wording ends inside ``following``, or ``0``.

    The copy is located by matching the wrapper's wording character by character
    and letting punctuation pass by, because a copy may punctuate the same
    wording differently. The answer is a source position, so the caller can cut
    ``following`` without leaving a fragment of either the copy or a mark.
    """
    spoken, ends = _wording(following)
    matched = 0
    for index, char in enumerate(spoken):
        if char in _SENTENCE_END or char in _CLAUSE_MARK or char.isspace():
            continue
        if matched >= len(insided):
            return ends[index - 1]
        if char != insided[matched]:
            return 0
        matched += 1
    return ends[len(spoken) - 1] if matched >= len(insided) and insided else 0


def _opening_length(speech, following, remaining):
    """Report how much of a longer clause repeats the wrapper before saying more.

    The clause quotes the wrapper and then continues in its own words, the usual
    shape of a partial voice reply. The wrapper's statement has to be what the
    rest of the body keeps saying, so a different sentence that merely starts the
    same way is not a copy. The answer is a source position in ``following`` and
    is moved back off any mark, so the clause is never cut inside one.
    """
    inside, _ = _wording(speech)
    outer, ends = _wording(following)
    if not inside or not outer.startswith(inside) or len(outer) <= len(inside):
        return 0
    spoken, _ = _wording(remaining)
    if not spoken.startswith(inside):
        return 0
    return _before_mark(following, ends[len(inside) - 1])


def _before_mark(text, position):
    """Move ``position`` so no mark is left cut in half.

    A cut inside a mark, or right before one, leaves a fragment such as
    ``</soft>`` on screen. The cut is therefore moved to the mark's start when it
    lands inside a mark, and past the mark when the mark begins exactly where the
    cut does. A paired mark spanning the cut moves the cut ahead of its opening
    tag, so the pair is dropped or kept together and never split.
    """
    for start, end in _paired_marks(text):
        if start < position < end:
            return start
    for start, end in _control_spans(text):
        if start < position < end:
            return start
        if start == position:
            return end
    return position


def _paired_marks(text):
    """Report the ranges of paired marks in ``text``, opening tag through closing."""
    ranges, open_at = [], {}
    for start, end in _control_spans(text):
        mark = text[start:end]
        name = _mark_name(mark)
        if not name:
            continue
        if mark.lstrip("<＜").lstrip().startswith("/"):
            opened = open_at.pop(name, None)
            if opened is not None:
                ranges.append((opened, end))
        else:
            open_at.setdefault(name, start)
    return tuple(ranges)


def _mark_name(mark):
    """Report the tag a mark names, or ``""`` when it is not an angle tag."""
    name = re.match(r"(?:<|＜|&lt;)\s*(/?)\s*([A-Za-z]+)", mark)
    return name[2].lower() if name else ""


def _has_paired_mark(text):
    """Report whether a span of body text carries a paired mark of its own."""
    return bool(_paired_marks(text))


def _restatement(speech, following, remaining):
    """Report the prefix of ``following`` that repeats the wrapper before it.

    ``0`` means the clause is new speech and is displayed whole. Otherwise the
    answer is the length of the copy, and only that copy is dropped; the clause's
    own words always stay, because a statement shown twice is a presentation flaw
    while a statement removed is lost content.

    A clause carrying a paired mark of its own is never merged. Removing the copy
    would have to remove that mark with it, and a body mark exists only in the
    clause -- the wrapper has none -- so dropping it would silently delete
    something the reader can see. Cutting around it instead would split the pair
    and leave ``</soft>`` on screen. Keeping the clause whole is the only outcome
    that loses nothing, so an occasional repeat is preferred to either.
    """
    if _has_paired_mark(following):
        return 0
    return _copy_length(speech, following) or _opening_length(speech, following, remaining)


def separate_spoken(text, policy):
    """Return visible text and (offset, speech) pairs before segmentation.

    The reply is assembled once, left to right, so each span carries the offset
    the returned text really has when its wrapper is reached; nothing is
    re-derived afterwards, so offsets cannot drift. A wrapper is displayed without
    its directions unless a translation follows it, in which case the translation
    is the visible body and the wrapper only carries the speech that is spoken. A
    clause restating the wrapper it follows is displayed once.

    Boundary tokens are part of the reply and stay in the visible text: they are
    what splits it later. Only the comparison that decides a restatement ignores
    them, because a copy is the same statement whether or not a boundary follows.
    """
    if policy is None or not policy.uses_model_tags:
        return strip_voice_markup(text), ()
    translate = policy.language != "zh" and policy.foreign_text_mode == "translation"
    matches = tuple(match for match in _TAG.finditer(text) if _matching_voice_tags(match))
    visible, spoken, cursor = _VisibleText(), [], 0
    for index, match in enumerate(matches):
        visible.add(_body_text(text[cursor:match.start()]))
        speech = match.group("speech").strip()
        if speech:
            spoken.append((visible.length, speech))
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        following = text[match.end():end]
        if translate and _has_chinese_translation(following):
            visible.add(_body_text(following))
        else:
            visible.add(visible_speech(match.group("speech")))
            # A statement said again right after its wrapper is shown once; the
            # wrapper is the copy kept because it is the one being voiced.
            repeated = _restatement(speech, following, text[match.end():])
            visible.add(_body_text(following[repeated:]))
        cursor = end
    visible.add(_body_text(text[cursor:]))
    return "".join(visible.parts), tuple(spoken)


def _part_offsets(visible, parts):
    """Where each part of ``parts`` starts, measured inside ``visible``.

    ``parts`` is ``visible`` cut up: every character of the reply is in exactly
    one part, and only a boundary line's newline sits between two of them. Reading
    the parts against the reply in order therefore recovers each range exactly,
    which keeps spans (measured on ``visible``) on the same coordinates.
    """
    starts, cursor = [], 0
    for part in parts:
        starts.append(cursor)
        for char in part:
            if char.isspace():
                continue
            found = visible.find(char, cursor)
            cursor = found + 1 if found >= 0 else cursor
    return tuple(starts)


def speech_candidates(visible, parts, spans, policy):
    """Map authored speech to the visible segments that carry it.

    ``spans`` are measured on ``visible`` and ``parts`` are that same text cut
    up, so one coordinate system covers both: a span belongs to the part whose
    range in ``visible`` contains its offset.
    """
    if not policy.uses_model_tags:
        return tuple(part.strip() for part in parts)
    starts = _part_offsets(visible, parts)
    buckets = [[] for _ in parts]
    for offset, speech in spans:
        index = max((i for i, start in enumerate(starts) if start <= offset), default=0)
        buckets[min(index, len(parts) - 1)].append(speech)
    return tuple("\n".join(bucket) for bucket in buckets)


def speech_decisions(visible, parts, spans, policy, *, candidates=None):
    """Retain why a final segment was skipped, after any blank parts were removed."""
    if candidates is None:
        candidates = speech_candidates(visible, parts, spans, policy)
    if len(candidates) != len(parts):
        raise ValueError("speech candidates must match their visible segments")
    selected = {"whole": range(len(parts)), "each": range(len(parts)),
                "first": (0,), "last": (len(parts) - 1,)}[policy.segment_mode]
    decisions = []
    for index, candidate in enumerate(candidates):
        if not parts[index].strip():
            decisions.append(("", "empty_part"))
        elif index not in selected:
            decisions.append(("", "segment_skipped"))
        elif not candidate:
            decisions.append(("", "no_speech"))
        elif len(visible_speech(candidate)) > policy.max_speech_chars:
            decisions.append(("", "too_long"))
        else:
            decisions.append((candidate, "planned"))
    return tuple(decisions)


def planned_speech(visible, parts, spans, policy):
    return tuple(speech for speech, _ in speech_decisions(visible, parts, spans, policy))


def eligible(policy, *, input_text, kind, batch_id, primary_mention=False):
    primary_trigger = (primary_mention and policy.primary_mention_probability > 0
        and any(word in input_text.casefold() for word in policy.primary_mention_words()))
    if policy.trigger_mode == "request" and not primary_trigger:
        keywords = policy.keywords() or ("语音", "说给我听", "念给我听", "读给我听", "voice", "tts")
        if not any(word in input_text.casefold() for word in keywords):
            return False
    chance = max(policy.chance(kind), policy.primary_mention_probability) if primary_trigger else policy.chance(kind)
    return chance >= 1 or chance > 0 and int(digest(batch_id)[:12], 16) / 0xFFFFFFFFFFFF < chance


def speech_chunks(text, maximum):
    """Split at spoken pauses while keeping performance directions intact."""
    chunks = []
    source = text.strip()
    remaining = visible_speech(source)
    if len(remaining) <= maximum:
        return (source,) if source else ()
    start = 0
    while len(remaining) > maximum:
        pauses = tuple(re.finditer(r"[。！？!?；;\n]|[.](?=\s|$)|[，,](?=\s|[\u4e00-\u9fff\u3040-\u30ff])", remaining[:maximum]))
        boundary = pauses[-1].end() if pauses else remaining.rfind(" ", 0, maximum + 1)
        if boundary <= 0:
            boundary = maximum
        chunks.append(slice_speech(source, start, start + boundary).strip())
        start += boundary
        tail = remaining[boundary:]
        start += len(tail) - len(tail.lstrip())
        remaining = tail.lstrip()
    if remaining:
        chunks.append(slice_speech(source, start, start + len(remaining)).strip())
    return tuple(chunk for chunk in chunks if chunk)


class VoiceReplyDelivery:
    """Durable per-route cooldown; a failed pre-dispatch synthesis releases its claim."""

    def __init__(self, runtime, now):
        self.runtime, self.now = runtime, now

    def claim(self, envelope):
        with self.runtime.transaction() as connection:
            row = connection.execute("""SELECT v.*, p.batch_id FROM conversation_voice_parts v
                JOIN conversation_parts p ON p.part_id = v.part_id WHERE v.part_id = ?""", (envelope.part_id,)).fetchone()
            if row is None:
                return None
            plan = dict(row)
            if not plan["cooldown_seconds"]:
                return plan
            context = envelope.context
            key = digest([context.scope.installation_id, context.scope.platform, context.scope.kind.value,
                          context.scope.bot_id,
                          context.scope.account_id, context.scope.conversation_id, context.role_binding.role])
            old = connection.execute("""SELECT c.*, p.batch_id FROM conversation_voice_cooldowns c
                LEFT JOIN conversation_parts p ON p.part_id = c.part_id WHERE c.route_key = ?""", (key,)).fetchone()
            at = self.now()
            if old is not None and old["until_at"] > stamp(at):
                return plan if old["batch_id"] == plan["batch_id"] else None
            connection.execute("""INSERT INTO conversation_voice_cooldowns(route_key, part_id, until_at)
                VALUES (?, ?, ?) ON CONFLICT(route_key) DO UPDATE SET part_id = excluded.part_id,
                until_at = excluded.until_at""", (key, envelope.part_id, stamp(at + timedelta(seconds=plan["cooldown_seconds"]))))
            plan["cooldown_key"] = key
            return plan

    def claim_core_route(self, context, event_id, cooldown_seconds):
        """Claim the same durable route cooldown for AstrBot-owned replies."""
        owner = "core:" + digest([context.key, event_id])
        if not cooldown_seconds:
            return {"part_id": owner, "cooldown_key": ""}
        key = digest([context.scope.installation_id, context.scope.platform, context.scope.kind.value,
                      context.scope.bot_id, context.scope.account_id, context.scope.conversation_id,
                      context.role_binding.role])
        with self.runtime.transaction() as connection:
            old = connection.execute(
                "SELECT part_id, until_at FROM conversation_voice_cooldowns WHERE route_key = ?", (key,)
            ).fetchone()
            at = self.now()
            if old is not None and old["until_at"] > stamp(at) and old["part_id"] != owner:
                return None
            connection.execute("""INSERT INTO conversation_voice_cooldowns(route_key, part_id, until_at)
                VALUES (?, ?, ?) ON CONFLICT(route_key) DO UPDATE SET part_id = excluded.part_id,
                until_at = excluded.until_at""",
                (key, owner, stamp(at + timedelta(seconds=cooldown_seconds))))
        return {"part_id": owner, "cooldown_key": key}

    def release(self, plan, part_id):
        if plan is not None and plan.get("cooldown_key"):
            with self.runtime.transaction() as connection:
                connection.execute("DELETE FROM conversation_voice_cooldowns WHERE route_key = ? AND part_id = ?",
                                   (plan["cooldown_key"], part_id))

    def record(self, part_id, render_state, component_order, text_hidden):
        """Keep the prepared components separate from the platform delivery receipt."""
        order = tuple(component_order)
        if render_state not in {"not_applicable", "cooldown", "unavailable", "failed", "empty", "invalid", "ready"}:
            raise ValueError("voice render state is invalid")
        if any(kind not in {"text", "image", "voice", "at", "face"} for kind in order):
            raise ValueError("voice component order is invalid")
        with self.runtime.transaction() as connection:
            connection.execute("""UPDATE conversation_voice_diagnostics SET
                render_state = CASE WHEN candidate_reason = 'planned' THEN ? ELSE 'not_applicable' END,
                component_order_json = ?, text_hidden = ? WHERE part_id = ?""",
                (render_state, json.dumps(order, ensure_ascii=False), int(text_hidden), part_id))
