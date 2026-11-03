"""Deterministic reply segmentation shared by streamed and complete replies."""

from dataclasses import dataclass
import hashlib
import json
import math
import re

import regex

from .segment_control import LLM_SEGMENT_MARKER, controlled_parts, protected_ranges
from .text_processing import literal_ranges


DEFAULT_SPLIT_WORDS = ("。", "？", "！", "~", "～", "?", "!", ";", "；", "……", "…")
# The 7.0/7.1 default also split on an English period and a closing full-width
# parenthesis.  Those two made English sentences and parentheticals become
# their own bubbles; profiles that never customised the list are migrated in
# SegmentationPolicy.__post_init__ instead of requiring a manual edit.
LEGACY_DEFAULT_SPLIT_WORDS = ("。", "？", "！", "~", "～", "?", ".", "!", ";", "；", "……", "）", "…")
# Older UI builds saved the same default with a comma, newline and space added.
# Recognise that pool as an untouched legacy list too; a genuinely custom word
# outside the pool keeps the old list exactly as configured.
LEGACY_SPLIT_WORD_POOL = frozenset(LEGACY_DEFAULT_SPLIT_WORDS) | {"，", "\n", " "}
# A literal space in the old list split every English word at its spaces; the
# period and full-width closing parenthesis made whole English sentences or
# parentheticals their own bubble.  Remove all three from untouched legacy lists.
LEGACY_REMOVED_SPLIT_WORDS = (".", "）", " ")
DEFAULT_RULE_REGEX = r"(?<=[。！？!?…~～])\s*|\n+"
PART_GAP_MODES = frozenset(("fixed", "random", "log"))
# 开引号与开括号只属于后一句。把边界落在它们之后等于把引文从中间切开：
# 上一段留下一个孤零零的「“」，下一段从被引用的半句开始。
OPENING_PUNCTUATION = "“‘「『（(【[《〈"


def _visible_length(text):
    return sum(not char.isspace() for char in text)


def _balanced_groups(parts, limit):
    """Merge adjacent natural units evenly while preserving their exact text."""
    if len(parts) <= limit:
        return tuple(parts)
    sizes = tuple(_visible_length(part) for part in parts)
    remaining_chars = sum(sizes)
    start, remaining_groups, grouped = 0, limit, []
    while remaining_groups > 1:
        target = remaining_chars / remaining_groups
        latest_end = len(parts) - remaining_groups + 1
        accumulated, best_end, best_distance = 0, start + 1, None
        for end in range(start + 1, latest_end + 1):
            accumulated += sizes[end - 1]
            distance = abs(accumulated - target)
            if best_distance is None or distance < best_distance:
                best_end, best_distance = end, distance
        grouped.append("".join(parts[start:best_end]))
        remaining_chars -= sum(sizes[start:best_end])
        start = best_end
        remaining_groups -= 1
    grouped.append("".join(parts[start:]))
    return tuple(grouped)


def _pacing_factor(settings):
    """Gently stretch inter-part spacing when the role is low on energy or sleepy."""
    factor = settings.get("state_pacing_factor")
    if type(factor) in (int, float) and not math.isnan(factor) and not math.isinf(factor):
        return min(1.5, max(0.75, float(factor)))
    energy = settings.get("role_energy")
    factor = 1.0
    if type(energy) is int:
        if energy <= 25:
            factor *= 1.25
        elif energy <= 45:
            factor *= 1.12
        elif energy >= 85:
            factor *= 0.92
    if settings.get("role_sleepy") is True:
        factor = max(factor, 1.25)
    return min(1.5, max(0.75, factor))


def _visible_characters(text):
    """Count visible characters for pacing, not words or alphanumerics.

    Punctuation-only and emoji-only bubbles are common human messages; counting
    only alphanumerics made them look like zero-length text and produced a
    0.1-second "machine burst".  Non-whitespace keeps Chinese, English, emoji and
    punctuation on the same scale, with one as the floor.
    """
    return max(1, sum(1 for char in str(text or "") if not char.isspace()))


def part_gap_seconds(settings, text, seed):
    """Resolve one stable inter-part delay from the sealed reply settings."""
    mode = settings.get("part_gap_mode", "fixed")
    if mode == "fixed":
        return settings.get("part_gap_ms", 0) / 1000
    lower = settings.get("part_gap_min_ms", 1500) / 1000
    upper = settings.get("part_gap_max_ms", 3500) / 1000
    entropy = hashlib.sha256(str(seed).encode("utf-8")).digest()
    fraction = int.from_bytes(entropy[:8], "big") / ((1 << 64) - 1)
    factor = _pacing_factor(settings)
    if mode == "random":
        return min(60.0, (lower + (upper - lower) * fraction) * factor)
    if mode == "log":
        # The operator-visible lower/upper bounds used to be dead in log mode.
        # Clamp the length-derived base first, then apply the role pacing factor
        # and a small stable jitter.  This keeps short reactions from arriving
        # almost instantly and long replies from turning into a metronome.
        base = settings.get("part_gap_log_base", 2.1)
        nominal = math.log(_visible_characters(text) + 1, base)
        bounded = min(max(upper, lower), max(lower, nominal))
        return min(60.0, max(0.1, (bounded + fraction * 0.5) * factor))
    raise ValueError("part spacing mode is invalid")


def minimum_part_gap_seconds(settings, text):
    """The lower bound shown by offline previews for one inter-part delay."""
    mode = settings.get("part_gap_mode", "fixed")
    if mode == "fixed":
        return settings.get("part_gap_ms", 0) / 1000
    lower = settings.get("part_gap_min_ms", 1500) / 1000
    upper = settings.get("part_gap_max_ms", 3500) / 1000
    factor = _pacing_factor(settings)
    if mode == "random":
        return min(60.0, lower * factor)
    if mode == "log":
        base = settings.get("part_gap_log_base", 2.1)
        nominal = math.log(_visible_characters(text) + 1, base)
        bounded = min(max(upper, lower), max(lower, nominal))
        return min(60.0, max(0.1, bounded * factor))
    raise ValueError("part spacing mode is invalid")
_WIDTH_VARIANTS = (
    (",", "，"), (".", "．", "。"), ("?", "？"), ("!", "！"), (";", "；"),
    (":", "："), ("~", "～"), ("(", "（"), (")", "）"), ("[", "［"),
    ("]", "］"), ("{", "｛"), ("}", "｝"), ("<", "＜"), (">", "＞"),
    ('"', "＂"), ("'", "＇"), ("/", "／"), ("\\", "＼"), ("|", "｜"),
    ("+", "＋"), ("-", "－", "—"), ("=", "＝"), ("*", "＊"), ("&", "＆"),
    ("%", "％"), ("#", "＃"), ("@", "＠"), ("$", "＄"), ("^", "＾"),
    ("_", "＿"),
)
_FILE_SUFFIX = re.compile(
    r"(?i)(?<=[\w\u3400-\u9fff\u3040-\u30ff])\."
    r"(?:7z|aac|apk|avif|avi|bmp|bz2|com|css|csv|docx?|dmg|epub|exe|flac|"
    r"gif|gz|heic|heif|html?|ico|ini|ipa|jar|jpe?g|js|jsonl?|log|m4a|"
    r"m4v|md|mkv|mobi|mov|mp3|mp4|mpeg|mpg|msi|odt|ogg|opus|pdf|png|"
    r"pptx?|psd|py|rar|raw|rmvb|rtf|svg|tar|tgz|tiff?|toml|ts|txt|"
    r"wav|webm|webp|wmv|wma|xlsx?|xml|xz|ya?ml|zip)(?![\w])"
)


def _width_words(words):
    expanded = list(words)
    for variants in _WIDTH_VARIANTS:
        widths = {len(word) for word in words if word and word == word[0] * len(word)
                  and word[0] in variants}
        for width in sorted(widths):
            expanded.extend(char * width for char in variants)
    return tuple(sorted(set(expanded), key=lambda word: (-len(word), word)))


def _match_word(text, position, words):
    return next((word for word in words if text.startswith(word, position)), None)


def _is_punctuation_word(word):
    """Return whether a configured boundary token contains punctuation only."""
    return bool(word) and all(not char.isalnum() and not char.isspace() for char in word)


def _coalesce_punctuation_boundaries(text, boundaries):
    """Keep a run such as ``...？`` in one message boundary."""
    result = []
    for end in boundaries:
        if (result and end > result[-1]
                and text[result[-1]:end]
                and all(not char.isalnum() and not char.isspace()
                        for char in text[result[-1]:end])):
            result[-1] = end
        else:
            result.append(end)
    return tuple(result)


def _word_boundaries(text, words, protected):
    boundaries = []
    position = interval = 0
    while position < len(text):
        # A protected interval keeps every position inside it atomic, including
        # the position at its end: a paragraph the reader already retained must
        # not be split by a delimiter that happens to sit exactly on that edge.
        while interval < len(protected) and position > protected[interval][1]:
            interval += 1
        if interval < len(protected) and protected[interval][0] <= position:
            # 引文整体受保护，本身不是停顿：开引号处不再落任何边界。
            # 旧实现在开引号之后补了一个断点，结果上一段以「“」结尾、
            # 引文被切到下一段（2026-10-25 现场：…那句“ / 生日结束…）。
            position = protected[interval][1] + 1
            continue
        word = _match_word(text, position, words)
        if word is None:
            position += 1
            continue
        end = position + len(word)
        if all(char in OPENING_PUNCTUATION for char in word):
            # 配置里残留的开引号（旧预设默认带「“」）不能当断句符：
            # 在它之后断开会留下「…那句“」这样的半截气泡。
            position = end
            continue
        if word in (".", ",", "，") and position > 0 and text[position - 1].isdigit() \
                and end < len(text) and text[end].isdigit():
            position = end
            continue
        if word == "." and position > 0 and end < len(text) \
                and text[position - 1].isalpha() and text[end].isalnum():
            # A dot inside an English word is not a sentence boundary:
            # ``example.com``, ``U.S.`` and ``e.g.`` must stay one token.
            position = end
            continue
        if word == "." and position > 0:
            start = position - 1
            while start >= 0 and (text[start].isalnum() or text[start] in "._-"):
                start -= 1
            token = text[start + 1:end]
            if token.count(".") >= 2 and len(token) <= 24:
                # The trailing dot of an abbreviation (``e.g.`` / ``U.S.``)
                # follows a dotted token; splitting it would leave the
                # abbreviation alone as its own bubble.
                position = end
                continue
        if word in ("~", "～", "-", "－", "—") and position > 0 and text[position - 1].isdigit() \
                and next((char for char in text[end:] if not char.isspace()), "").isdigit():
            position = end
            continue
        if word in (".", "…", "~", "～"):
            while text.startswith(word, end):
                end += len(word)
        # Different punctuation marks in one run express one pause. Consume
        # all adjacent configured punctuation before publishing the boundary,
        # so ``...？`` cannot leave a message beginning with ``？``.
        while True:
            following = _match_word(text, end, words)
            if following is None or not _is_punctuation_word(following):
                break
            end += len(following)
        boundaries.append(end)
        position = end
    return boundaries


DEFAULT_PROMPT = (
    "按内容自然决定是否拆成多条消息。日常聊天通常先把一次完整回应放在一个气泡里；若几句是在补充同一反应、心情、解释或因果，优先合在一起，不按每个句号逐条发送。"
    "只有出现自然停顿、确实另起一个独立问题或转到另一件事时再分段，让每条气泡都独立读得通。不要拆开一句话、称呼与回应或紧密相连的步骤。长文、教程、代码、引用、列表、表格、链接和格式化内容保持完整，主题确实转折时再分段。"
    "提前看完整条回复再安排边界；避免前几条很短、把大量剩余内容堆到最后一条。不为凑条数而拆，也不为达到规则字数而牺牲意思完整；完整回应后的自然追问可另发一条。连续标点（如 ...？、？！）留在同一条，避免气泡以孤立标点开头。每条正文首尾不填空行。"
    "每个模型指定的边界使用完整标记 {{split_marker}}，在 text 字符串内单独占一行。它只表示消息边界，不属于正文；不要借此删改、概括或重排原文，也不要放进引文、代码、列表或链接。"
    "普通换行和空行不代表模型指定边界；确有需要时才用标记。JSON 字符串中的换行正确转义。claims 使用各段正文原句，不含标记。"
)

@dataclass(frozen=True)
class SegmentationPolicy:
    enabled: bool = True
    llm_controlled: bool = False
    # Kept only so profiles written before the rules became unconditional can
    # still be parsed.  The value is intentionally ignored by ``parts``.
    rules_enabled: bool = True
    rule_mode: str = "words"
    split_words: str = json.dumps(DEFAULT_SPLIT_WORDS, ensure_ascii=False)
    match_width_variants: bool = True
    rule_regex: str = DEFAULT_RULE_REGEX
    threshold: int = 500
    min_segment_chars: int = 5
    max_segments: int = 5
    prompt: str = ""
    streaming_mode: str = "buffered"

    def __post_init__(self):
        if any(type(value) is not bool for value in (self.enabled, self.llm_controlled,
                                                     self.rules_enabled, self.match_width_variants)):
            raise ValueError("segmentation switches must be booleans")
        if self.rule_mode not in ("words", "regex", "sentences", "paragraphs"):
            raise ValueError("segmentation rule mode is invalid")
        if self.streaming_mode not in ("buffered", "incremental"):
            raise ValueError("streaming segmentation mode is invalid")
        if type(self.split_words) is not str or len(self.split_words.encode("utf-8")) > 4096:
            raise ValueError("segmentation words need a bounded JSON list")
        try:
            words = json.loads(self.split_words)
        except ValueError as error:
            raise ValueError("segmentation words need a JSON list") from error
        if (type(words) is list and all(type(word) is str for word in words)
                and set(LEGACY_DEFAULT_SPLIT_WORDS) <= set(words)
                and set(words) <= LEGACY_SPLIT_WORD_POOL):
            # Migrate the old untouched defaults (and the older UI variant with
            # comma/newline/space) in place.  A customised list that contains
            # words outside that pool is left exactly as configured.
            words = [word for word in words if word not in LEGACY_REMOVED_SPLIT_WORDS]
            object.__setattr__(self, "split_words", json.dumps(words, ensure_ascii=False))
        if (type(words) is not list or len(words) > 80
                or any(type(word) is not str or not 1 <= len(word) <= 40
                       or any(ord(char) < 32 and char != "\n" for char in word) for word in words)
                or len(words) != len(set(words))):
            raise ValueError("segmentation words need unique bounded text")
        if type(self.rule_regex) is not str or not 1 <= len(self.rule_regex) <= 512:
            raise ValueError("segmentation regex must be bounded text")
        try:
            regex.compile(self.rule_regex)
        except regex.error as error:
            raise ValueError("segmentation regex is invalid") from error
        for value, low, high in ((self.threshold, 1, 65536), (self.min_segment_chars, 1, 65536),
                                 (self.max_segments, 1, 64)):
            if type(value) is not int or not low <= value <= high:
                raise ValueError("segmentation limits must be bounded integers")
        if not isinstance(self.prompt, str) or len(self.prompt) > 4000:
            raise ValueError("segmentation prompt must be bounded text")

    @property
    def instruction(self):
        """Only model-controlled boundaries need a generation protocol."""
        if not self.enabled:
            return ""
        if not self.llm_controlled:
            return ""
        prompt = re.sub(r"\{\{\s*split_marker\s*\}\}", lambda _: LLM_SEGMENT_MARKER,
                        self.prompt or DEFAULT_PROMPT, flags=re.IGNORECASE)
        return prompt

    def parts(self, text):
        if not isinstance(text, str) or not text.strip() or len(text) > 65536:
            raise ValueError("segmentation needs a bounded complete reply")
        parts, _ = controlled_parts(text)
        if not parts:
            raise ValueError("reply contains only control tokens")
        if not self.enabled:
            return ("".join(parts),)
        if not self.llm_controlled:
            parts = ("".join(parts),)
        # Excess boundaries are merged, never dropped or rejected. Balanced
        # groups avoid a short opening followed by one oversized tail.
        parts = list(parts)
        if len(parts) > self.max_segments:
            parts = list(_balanced_groups(parts, self.max_segments))
        within_rule_limit = sum(map(len, parts)) <= self.threshold
        # Rule completion is part of the segmentation pipeline whenever the
        # overall segmentation switch is on.  ``rules_enabled`` remains a
        # compatibility field for old profiles, but it is no longer a user
        # choice and must never disable the deterministic fallback.
        apply_rules = within_rule_limit
        if apply_rules:
            # Split natural units first, then merge adjacent units into balanced
            # groups within the remaining budget.
            while True:
                count = sum(len(p) if isinstance(p, tuple) else 1 for p in parts)
                candidates = [i for i, p in enumerate(parts) if isinstance(p, str)]
                if count >= self.max_segments or not candidates:
                    break
                index = max(candidates, key=lambda i: (len(parts[i]), -i))
                rule_parts = self._rule_parts(parts[index], self.max_segments - count + 1)
                parts[index] = tuple(self._merge_short_parts(rule_parts))
            parts = [item for p in parts for item in (p if isinstance(p, tuple) else (p,))]
        return tuple(parts)

    def _merge_short_parts(self, parts):
        if len(parts) <= 1:
            return parts

        def visible_length(value):
            return sum(not char.isspace() for char in value)

        def atomic_excerpt(value):
            stripped = value.strip()
            return (stripped.startswith("「") and stripped.endswith("」")
                    and visible_length(stripped[1:-1]) >= self.min_segment_chars)

        merged, index = [], 0
        while index < len(parts):
            current = parts[index]
            if atomic_excerpt(current):
                merged.append(current)
                index += 1
                continue
            while (index + 1 < len(parts)
                   and visible_length(current) < self.min_segment_chars
                   and not atomic_excerpt(parts[index + 1])):
                current += parts[index + 1]
                index += 1
            if (merged and not atomic_excerpt(merged[-1]) and not atomic_excerpt(current)
                    and visible_length(current) < self.min_segment_chars):
                merged[-1] += current
            else:
                merged.append(current)
            index += 1
        return merged

    def rule_boundaries(self, text):
        protected = tuple(sorted((*literal_ranges(text), *(match.span() for match in _FILE_SUFFIX.finditer(text)))))
        if self.rule_mode == "words":
            words = tuple(json.loads(self.split_words))
            if self.match_width_variants:
                words = _width_words(words)
            boundaries = _word_boundaries(text, words, protected)
        else:
            pattern = (self.rule_regex if self.rule_mode == "regex" else
                       r"(?:\r?\n[ \t]*){2,}" if self.rule_mode == "paragraphs" else
                       r"[。！？!?；;]+[”’）】]*|(?:\r?\n[ \t]*){2,}")
            try:
                boundaries = [(match.start(), match.end()) for match in regex.finditer(
                    pattern, text, timeout=0.05) if match.end() > 0]
            except TimeoutError as error:
                raise ValueError("segmentation regex exceeded its execution budget") from error
        boundaries = tuple(end for boundary in boundaries
                           if (end := boundary if isinstance(boundary, int) else boundary[1]) > 0
                           and (isinstance(boundary, int) or not any(a < end <= b for a, b in protected)))
        return _coalesce_punctuation_boundaries(text, boundaries)

    def _rule_parts(self, text, limit):
        if limit <= 1:
            return (text,)
        start, parts = 0, []
        for end in self.rule_boundaries(text):
            if end <= start:
                continue
            if (_visible_length(text[start:end]) < self.min_segment_chars
                    or _visible_length(text[end:]) < self.min_segment_chars):
                continue
            parts.append(text[start:end])
            start = end
        tail = text[start:]
        if tail:
            if parts and _visible_length(tail) < self.min_segment_chars:
                parts[-1] += tail
            else:
                parts.append(tail)
        if not parts:
            return (text,)
        return _balanced_groups(parts, limit)
