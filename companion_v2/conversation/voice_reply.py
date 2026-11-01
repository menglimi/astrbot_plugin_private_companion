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
    slice_speech, visible_speech,
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
    return _strip_stray_voice_tags(_TAG.sub(_visible_voice_match, text))


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
        prompt = "只有实际要朗读的原文放在 <pc_tts> 与 </pc_tts> 之间；标签内不放分段标记、代码、链接或图片说明。"
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


def separate_spoken(text, policy):
    """Return visible text and (offset, speech) pairs before segmentation."""
    if policy is None or not policy.uses_model_tags:
        return strip_voice_markup(text), ()
    translate = policy.language != "zh" and policy.foreign_text_mode == "translation"
    matches = tuple(match for match in _TAG.finditer(text) if _matching_voice_tags(match))
    visible, spoken, cursor = [], [], 0
    for index, match in enumerate(matches):
        visible.append(text[cursor:match.start()])
        speech = match.group("speech").strip()
        if speech:
            spoken.append((len(_strip_stray_voice_tags("".join(visible))), speech))
        following = text[match.end():matches[index + 1].start() if index + 1 < len(matches) else len(text)]
        translated = translate and _has_chinese_translation(following)
        if not translated:
            visible.append(visible_speech(match.group("speech")))
        cursor = match.end()
    visible.append(text[cursor:])
    return _strip_stray_voice_tags("".join(visible)), tuple(spoken)


def speech_candidates(text, parts, spans, policy):
    """Map authored speech to the original segments before presentation cleanup."""
    if not policy.uses_model_tags:
        candidates = tuple(part.strip() for part in parts)
    else:
        buckets = [[] for _ in parts]
        boundaries = []
        total = 0
        for part in parts:
            total += len(part)
            boundaries.append(total)
        for offset, speech in spans:
            prefix, _ = controlled_parts(text[:offset])
            position = min(sum(map(len, prefix)), total)
            index = next((i for i, end in enumerate(boundaries) if position < end), len(parts) - 1)
            buckets[index].append(speech)
        candidates = tuple("\n".join(bucket) for bucket in buckets)
    return candidates


def speech_decisions(text, parts, spans, policy, *, candidates=None):
    """Retain why a final segment was skipped, after any blank parts were removed."""
    if candidates is None:
        candidates = speech_candidates(text, parts, spans, policy)
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


def planned_speech(text, parts, spans, policy):
    return tuple(speech for speech, _ in speech_decisions(text, parts, spans, policy))


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
