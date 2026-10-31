"""Presentation transforms with cleanup enabled by default; literals and facts stay intact."""

from dataclasses import asdict, dataclass
from functools import lru_cache
import json
import re
from time import monotonic

import regex

from . import humanized_expression
from .segment_control import control_token_ranges, protected_ranges
from .voice_markup import VOICE_PROTOCOL_GT, VOICE_PROTOCOL_LT, VOICE_TAG_NAME_PATTERN


MAX_TEXT = 65536
MAX_STYLE_PROMPT = 4000
REGEX_SECONDS = 0.05
_PROTOCOL_LT = VOICE_PROTOCOL_LT
_PROTOCOL_GT = VOICE_PROTOCOL_GT
REACTION_EXPRESSION_TAG_NAME = r"p[\s._-]*c{1,2}[\s._-]*reaction[\s._-]*expression"
_LITERALS = re.compile(
    r"(?P<url>(?:https?://|www\.)[^\s<>。！？，]+)"
    r"|<(?P<tag>image|img|video|audio|voice|record|file)\b[^>]*>.*?</(?P=tag)\s*>"
    rf"|{_PROTOCOL_LT}\s*{VOICE_TAG_NAME_PATTERN}\b[^>]*?{_PROTOCOL_GT}.*?"
    rf"{_PROTOCOL_LT}\s*/\s*{VOICE_TAG_NAME_PATTERN}\s*{_PROTOCOL_GT}"
    rf"|{_PROTOCOL_LT}\s*{VOICE_TAG_NAME_PATTERN}\b[^>]*?{_PROTOCOL_GT}"
    r"|<(?:image|img|video|audio|voice|record|file|pc_tts|tts)\b[^>]*>",
    re.IGNORECASE | re.DOTALL,
)
_REACTION_EXPRESSION_BLOCK = re.compile(
    rf"(?is){_PROTOCOL_LT}\s*{REACTION_EXPRESSION_TAG_NAME}\b[^>]*?{_PROTOCOL_GT}.*?"
    rf"(?:{_PROTOCOL_LT}\s*/\s*{REACTION_EXPRESSION_TAG_NAME}\s*{_PROTOCOL_GT}|\Z)"
)
_REACTION_EXPRESSION_TAG = re.compile(
    rf"(?is){_PROTOCOL_LT}\s*/?\s*{REACTION_EXPRESSION_TAG_NAME}\b[^>]*?{_PROTOCOL_GT}"
)
_REACTION_EXPRESSION_BARE_TAG = re.compile(
    rf"(?is){_PROTOCOL_LT}\s*/?\s*{REACTION_EXPRESSION_TAG_NAME}\b(?![^>]*>)"
)
_REACTION_EXPRESSION_OPEN_PROTOCOL = regex.compile(
    rf"{_PROTOCOL_LT}\s*{REACTION_EXPRESSION_TAG_NAME}\b[^>]*?{_PROTOCOL_GT}",
    regex.IGNORECASE | regex.DOTALL,
)
_REACTION_EXPRESSION_CLOSE_PROTOCOL = regex.compile(
    rf"{_PROTOCOL_LT}\s*/\s*{REACTION_EXPRESSION_TAG_NAME}\s*{_PROTOCOL_GT}",
    regex.IGNORECASE | regex.DOTALL,
)
_PAIRS = {"(": ")", "[": "]", "{": "}", "（": "）", "【": "】", "《": "》",
          "〈": "〉", "「": "」", "『": "』", "“": "”", "‘": "’", '"': '"'}


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("presentation rule contains duplicate keys")
        result[key] = value
    return result


def _list(raw):
    if type(raw) is not str or len(raw.encode("utf-8")) > 8192:
        raise ValueError("presentation rules need a bounded JSON list")
    try:
        value = json.loads(raw, object_pairs_hook=_unique)
    except (ValueError, RecursionError) as error:
        raise ValueError("presentation rules need a JSON list") from error
    if type(value) is not list or len(value) > 80:
        raise ValueError("presentation rules need at most 80 entries")
    return value


def _decode(value, *, deletion=False):
    if type(value) is not str:
        raise ValueError("presentation literals must be text")
    tokens = {"<space>": " ", "<newline>": "\n", "<tab>": "\t"}
    if deletion:
        tokens["<empty>"] = ""
    return tokens.get(value.casefold(), value)


@lru_cache(maxsize=128)
def _pattern(rule):
    return regex.compile(rule)


@lru_cache(maxsize=128)
def _word_pattern(words):
    ordered = sorted({word for word in words if word}, key=len, reverse=True)
    return regex.compile("|".join(regex.escape(word) for word in ordered)) if ordered else None


def _merged(ranges):
    result = []
    for start, end in sorted(ranges):
        if start >= end:
            continue
        if result and start <= result[-1][1]:
            result[-1] = (result[-1][0], max(result[-1][1], end))
        else:
            result.append((start, end))
    return tuple(result)


def literal_ranges(text, statements=()):
    literals = tuple(match.span() for match in _LITERALS.finditer(text))
    # Media wrappers and URL punctuation must not make adjacent prose Markdown.
    markdown_text = "".join(re.sub(r"[^\r\n]", " ", chunk) if protected else chunk
                            for chunk, protected in _chunks(text, literals))
    ranges = list(protected_ranges(markdown_text, explicit=True)) + list(control_token_ranges(text))
    ranges.extend(literals)
    for statement in statements:
        if not statement:
            continue
        start = 0
        while (start := text.find(statement, start)) >= 0:
            ranges.append((start, start + len(statement)))
            start += len(statement)
    fixed = _merged(ranges)
    stack, index, escaped = [], 0, False
    for offset, char in enumerate(text):
        while index < len(fixed) and offset >= fixed[index][1]:
            index += 1
        if index < len(fixed) and fixed[index][0] <= offset < fixed[index][1]:
            continue
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if stack and char == _PAIRS[stack[-1][1]]:
            start, _ = stack.pop()
            if not stack:
                ranges.append((start, offset + 1))
        elif stack and stack[-1][1] in ('"', "“"):
            continue
        elif char in _PAIRS:
            stack.append((offset, char))
    if stack:
        ranges.append((stack[0][0], len(text)))
    return _merged(ranges)


def _chunks(text, ranges):
    cursor = 0
    for start, end in ranges:
        if start > cursor:
            yield text[cursor:start], False
        yield text[start:end], True
        cursor = end
    if cursor < len(text):
        yield text[cursor:], False


_FRAMEWORK_TRACEBACK = re.compile(r"\bTraceback \(most recent call last\):")
_FRAMEWORK_FRAMES = re.compile(r'(?m)^\s*File "[^"\n]+", line \d+, in ')
_IDENTITY_ANCHORS = (re.compile(r"\[QQ:\d{5,12}\]"), re.compile(r"(?<![\w])QQ[:：]\d{5,12}"))
FRAMEWORK_GUARD_REPLACEMENT = "（这条回复没有正常生成，按安全策略不予展示。）"
REALITY_PROMISE_GUARD_REPLACEMENT = "这类现实操作需要可验证的外部人员或工具处理，我不能把未确认的结果说成已经完成。"
FORBIDDEN_WORD_SCOPES = frozenset(("bot_only", "group_only", "bot_and_group"))

# Prompt guidance handles ambiguous intent; this narrow check only catches an
# explicit completion or near-term promise paired with a concrete real-world action.
_REALITY_PROMISE = regex.compile(
    r"(?:我)?(?:已(?:经)?|刚(?:刚|才)?|成功(?:地)?|完成(?:了)?|马上|立刻|现在就|稍后)"
    r"[^。！？!?\n]{0,24}"
    r"(?:拉(?:进|来)?群|修(?:好)?网|开(?:好)?房间|登(?:录|陆)|控制(?:设备|手机|电脑)|到(?:现场|线下))"
    r"(?:了|啦|好|成功)?"
    r"|(?:我)?(?:拉(?:进|来)?群|修(?:好)?网|开(?:好)?房间|登(?:录|陆)|控制(?:设备|手机|电脑)|到(?:现场|线下))"
    r"(?:了|啦|好|成功|完成)", regex.IGNORECASE)


def _strip_identity_anchors(text):
    for pattern in _IDENTITY_ANCHORS:
        text = pattern.sub("", text)
    return text


# Internal control tags are invisible protocol, not prose.  They may arrive as
# a complete pair, an HTML-escaped pair, an orphan closing tag, or an unclosed
# tail.  Keep the cleaner deliberately narrower than a generic tag stripper:
# TTS still consumes its own wrapper, and sentinels consumed by downstream
# delivery paths must survive an incomplete tail.
_CONTROL_LITERAL_LT = r"(?:<|＜)"
_CONTROL_LITERAL_GT = r"(?:>|＞)"
_CONTROL_ESCAPED_LT = r"(?:&lt;|&#0*60;|&#x0*3c;)"
_CONTROL_ESCAPED_GT = r"(?:&gt;|&#0*62;|&#x0*3e;)"
_CONTROL_TAG_SPECS = (
    ("pc_reaction_expression", True, ()),
    ("pc_member_safety", True, ()),
    ("personality_sync", True, ()),
    ("tts", False, ("pc_tts",)),
    ("thinking", False, ()),
    # The host itself injects <system_reminder> blocks and keeps them as
    # first-class conversation content:
    #   astrbot/core/astr_main_agent.py:1033
    #       "<system_reminder>" + "\n".join(system_parts) + "</system_reminder>"
    #   astrbot/builtin_stars/astrbot/group_chat_context.py:33-39  group context
    #   astrbot/dashboard/services/chat_service.py:446  recognises the prefix
    ("system_reminder", False, ()),
    # AstrBot names <think> as a real provider spelling beside <thinking>
    # (astrbot/core/provider/sources/openai_source.py:862 comment, and the regex
    # r"<(think|thinking)>" at :864), so registering only the long spelling
    # covered half of what its own parser accepts.
    # <reasoning> is deliberately NOT registered: nothing in AstrBot 4.29.1
    # produces it — there is no such literal anywhere in the tree, and
    # gemini_source.py:527-529 reads reasoning from the structured
    # ``part.thought`` flag rather than from text tags.
    ("think", False, ()),
    ("timer", False, ()),
    ("private_companion_context", False, ()),
    ("core_memory", False, ()),
    ("reference_data", False, ()),
)
_CONTROL_TAG_NAMES = tuple(
    name for name, _truncate, aliases in _CONTROL_TAG_SPECS for name in (name, *aliases)
)


def _control_family_pattern(name, aliases):
    """The tolerant name spelling accepted for one registered family."""
    if name == "pc_reaction_expression":
        return REACTION_EXPRESSION_TAG_NAME
    if name == "tts":
        return VOICE_TAG_NAME_PATTERN
    return "|".join(re.escape(item) for item in (name, *aliases))


_CONTROL_REGISTERED_PATTERN = "|".join(
    _control_family_pattern(name, aliases) for name, _truncate, aliases in _CONTROL_TAG_SPECS
)
_CONTROL_NAMESPACE_PATTERN = r"(?:pc_[A-Za-z0-9_-]+|private_companion_[A-Za-z0-9_-]+)"
_CONTROL_CANDIDATE_PATTERN = re.compile(
    rf"(?:{_CONTROL_LITERAL_LT}|{_CONTROL_ESCAPED_LT})\s*/?\s*"
    rf"(?:{_CONTROL_REGISTERED_PATTERN}|{_CONTROL_NAMESPACE_PATTERN})",
    re.IGNORECASE,
)
_CONTROL_CODE_SPAN_PATTERN = re.compile(
    r"(```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\r\n]+`)", re.MULTILINE,
)
_CONTROL_CODE_MASK_HEAD = "\x01PCCS"
_CONTROL_CODE_MASK_TAIL = "\x02"
_CONTROL_CODE_MASK_ATTEMPTS = 4
_CONTROL_PAIRED_PAYLOAD_LIMIT = 20000
# Truncation must stop before a token another delivery stage still consumes.
# Bare "[" is not enough: a JSON array inside an unfinished payload must not
# be cut at the bracket and leak its tail.
_CONTROL_SENTINEL_START = r"(?:\[\[|\[[A-Za-z]|<<|&&[A-Za-z_]|\x00|\x01PCCS)"
_CONTROL_RULE_CACHE = {}


def _control_patterns(names, *, truncate):
    """Compile the four outbound leak shapes for one tag family."""
    flags = re.IGNORECASE | re.DOTALL
    literal_open = (
        rf"(?<!<){_CONTROL_LITERAL_LT}\s*(?:{names})\b[^<>]*?(?<!/){_CONTROL_LITERAL_GT}"
    )
    literal_close = (
        rf"{_CONTROL_LITERAL_LT}\s*/\s*(?:{names})\s*{_CONTROL_LITERAL_GT}"
    )
    escaped_open = (
        rf"{_CONTROL_ESCAPED_LT}\s*(?:{names})\b[^<>]*?(?<!/){_CONTROL_ESCAPED_GT}"
    )
    escaped_close = (
        rf"{_CONTROL_ESCAPED_LT}\s*/\s*(?:{names})\s*{_CONTROL_ESCAPED_GT}"
    )
    payload = rf"[\s\S]{{0,{_CONTROL_PAIRED_PAYLOAD_LIMIT}}}?"
    stop_at_sentinel = rf"(?:(?!{_CONTROL_SENTINEL_START})[\s\S])*"
    truncated = ()
    if truncate:
        truncated = (
            re.compile(rf"{literal_open}{stop_at_sentinel}", flags),
            re.compile(rf"{escaped_open}{stop_at_sentinel}", flags),
        )
    return {
        "probe": re.compile(
            rf"{literal_open}|{literal_close}|{escaped_open}|{escaped_close}", flags,
        ),
        "pairs": (
            re.compile(rf"{literal_open}{payload}{literal_close}", flags),
            re.compile(rf"{escaped_open}{payload}{escaped_close}", flags),
        ),
        "pair_probes": (
            re.compile(literal_close, flags),
            re.compile(escaped_close, flags),
        ),
        "truncated": truncated,
        "bare_open": re.compile(
            rf"(?:(?<!<){_CONTROL_LITERAL_LT}\s*(?:{names})\b"
            rf"(?:(?!{_CONTROL_SENTINEL_START})[^<>])*$"
            rf"|{_CONTROL_ESCAPED_LT}\s*(?:{names})\b"
            rf"(?:(?!{_CONTROL_SENTINEL_START}|{_CONTROL_ESCAPED_GT})[^<>])*$)",
            flags,
        ) if truncate else None,
        "orphan_closes": (
            re.compile(literal_close, flags),
            re.compile(escaped_close, flags),
        ),
        "any_open": re.compile(
            rf"{_CONTROL_LITERAL_LT}\s*(?:{names})\b"
            rf"(?![^<>]*?/\s*{_CONTROL_LITERAL_GT})"
            rf"|{_CONTROL_ESCAPED_LT}\s*(?:{names})\b"
            rf"(?![^<>]*?/\s*{_CONTROL_ESCAPED_GT})",
            flags,
        ),
        "any_close": re.compile(rf"{literal_close}|{escaped_close}", flags),
    }


def _control_namespace_pattern(excluded):
    """Namespace fallback for model-invented pc_* control tags."""
    guarded = set(excluded)
    families = []
    for prefix in ("pc_", "private_companion_"):
        guards = "".join(
            rf"(?!{re.escape(item[len(prefix):])}(?![A-Za-z0-9_-]))"
            for item in sorted(guarded)
            if item.startswith(prefix) and len(item) > len(prefix)
        )
        families.append(rf"{re.escape(prefix)}{guards}[A-Za-z0-9_-]+")
    return "(?:" + "|".join(families) + ")"


def _control_rules(exclude, *, truncate):
    key = (frozenset(exclude), bool(truncate))
    cached = _CONTROL_RULE_CACHE.get(key)
    if cached is not None:
        return cached
    rules = []
    registered = set()
    for name, allowed_truncate, aliases in _CONTROL_TAG_SPECS:
        names = (name, *aliases)
        registered.update(item.casefold() for item in names)
        if any(item.casefold() in exclude for item in names):
            continue
        alternation = _control_family_pattern(name, aliases)
        rules.append((name, _control_patterns(alternation, truncate=truncate and allowed_truncate)))
    # Unknown pc_* / private_companion_* tags are model-invented control
    # protocol too.  Registered families above stay authoritative; the fallback
    # only sees names the registry did not claim.
    fallback_names = tuple(sorted(registered))
    rules.append(("namespace", _control_patterns(
        _control_namespace_pattern(fallback_names), truncate=truncate,
    )))
    result = tuple(rules)
    if len(_CONTROL_RULE_CACHE) >= 8:
        _CONTROL_RULE_CACHE.clear()
    _CONTROL_RULE_CACHE[key] = result
    return result


def _mask_control_code_spans(value):
    spans = [match.group(0) for match in _CONTROL_CODE_SPAN_PATTERN.finditer(value)]
    if not spans:
        return value, None
    for base in range(_CONTROL_CODE_MASK_ATTEMPTS):
        head = f"{_CONTROL_CODE_MASK_HEAD}{base}x"
        tokens = [f"{head}{index}{_CONTROL_CODE_MASK_TAIL}" for index in range(len(spans))]
        if any(token in value for token in tokens):
            continue
        state = {"next": 0}

        def collect(_match, _tokens=tokens):
            token = _tokens[state["next"]]
            state["next"] += 1
            return token

        masked = _CONTROL_CODE_SPAN_PATTERN.sub(collect, value)
        restore = re.compile(rf"{re.escape(head)}(\d+){re.escape(_CONTROL_CODE_MASK_TAIL)}")
        return masked, (restore, spans)
    return value, None


def _restore_control_code_spans(value, restore):
    if restore is None:
        return value
    pattern, spans = restore

    def replace(match):
        index = int(match.group(1))
        return spans[index] if 0 <= index < len(spans) else match.group(0)

    return pattern.sub(replace, value)


def _drop_excess_control_closes(segment, patterns):
    """Delete only closing tags that clearly outnumber their openers."""
    open_count = len(patterns["any_open"].findall(segment))
    closes = list(patterns["any_close"].finditer(segment))
    excess = len(closes) - open_count
    if excess <= 0:
        return segment
    chunks, cursor = [], 0
    for index, match in enumerate(closes):
        if index < len(closes) - excess:
            continue
        chunks.append(segment[cursor:match.start()])
        cursor = match.end()
    chunks.append(segment[cursor:])
    return "".join(chunks)


def _may_have_control_pair(segment, patterns):
    """Whether any open tag can reach a later close within the payload cap."""
    opens = [match.end() for match in patterns["any_open"].finditer(segment)]
    if not opens:
        return False
    index = 0
    for close in patterns["any_close"].finditer(segment):
        while index < len(opens) and opens[index] <= close.start():
            index += 1
        if index and close.start() - opens[index - 1] <= _CONTROL_PAIRED_PAYLOAD_LIMIT:
            return True
    return False


def _clean_control_segment(segment, rules, *, truncate):
    for _pass in range(2):
        before = segment
        for _name, patterns in rules:
            if patterns["probe"].search(segment) is None:
                continue
            if _may_have_control_pair(segment, patterns):
                for pair, close_probe in zip(patterns["pairs"], patterns["pair_probes"]):
                    if close_probe.search(segment) is not None:
                        segment = pair.sub("", segment)
            # With any closing tag present, never truncate: an oversized or
            # malformed pair must not silently consume the user's prose.
            if truncate and patterns["truncated"] and patterns["any_close"].search(segment) is None:
                for pattern in patterns["truncated"]:
                    segment = pattern.sub("", segment)
            if patterns["bare_open"] is not None:
                segment = patterns["bare_open"].sub("", segment)
            segment = _drop_excess_control_closes(segment, patterns)
        if segment == before:
            break
    return segment


def strip_paired_control_blocks(text, *, exclude=("tts",), preserve_code_spans=True, truncate=True):
    """Remove paired plugin control tags without eating downstream sentinels.

    Complete and escaped pairs, excess orphan closers, and truncate-authorized
    incomplete tails are removed.  Markdown code examples stay intact.  The
    function is intentionally stateless; the streaming adapter uses
    :class:`InternalExpressionProtocolFilter` to rejoin split tags.
    """
    if type(text) is not str:
        return text
    if not text or _CONTROL_CANDIDATE_PATTERN.search(text) is None:
        return text
    excluded = frozenset(str(item or "").casefold() for item in (exclude or ()))
    rules = _control_rules(excluded, truncate=truncate)
    masked, restore = (_mask_control_code_spans(text) if preserve_code_spans else (text, None))
    return _restore_control_code_spans(
        _clean_control_segment(masked, rules, truncate=truncate), restore,
    )


def strip_internal_expression_protocol(text):
    """Remove internal control blocks before text can be shown or sent."""
    return strip_paired_control_blocks(text)


def _partial_protocol_match(pattern, text):
    match = pattern.search(text, partial=True)
    if match is not None and match.partial and match.start() == match.end():
        return None
    return match


class InternalExpressionProtocolFilter:
    """Remove internal reaction tags even when a native stream splits them."""

    def __init__(self):
        self._pending = ""
        self._inside_block = False

    def feed(self, text):
        if type(text) is not str:
            return ""
        buffer = self._pending + text
        self._pending = ""
        visible = []
        while buffer:
            if self._inside_block:
                closing = _partial_protocol_match(_REACTION_EXPRESSION_CLOSE_PROTOCOL, buffer)
                if closing is None or closing.partial:
                    # Keep the payload too: an unfinished block may contain a
                    # delivery sentinel that must be recovered by finish().
                    self._pending = buffer
                    break
                buffer = buffer[closing.end():]
                self._inside_block = False
                continue

            opening = _partial_protocol_match(_REACTION_EXPRESSION_OPEN_PROTOCOL, buffer)
            closing = _partial_protocol_match(_REACTION_EXPRESSION_CLOSE_PROTOCOL, buffer)
            matches = [(match.start(), kind, match) for kind, match in
                       (("open", opening), ("close", closing)) if match is not None]
            if not matches:
                visible.append(buffer)
                break
            _, kind, match = min(matches, key=lambda item: item[0])
            visible.append(buffer[:match.start()])
            if match.partial:
                self._pending = buffer[match.start():]
                break
            buffer = buffer[match.end():]
            self._inside_block = kind == "open"
        return "".join(visible)

    def finish(self):
        """End the stream, preserving only sentinels trapped in a broken tail."""
        pending = self._pending
        self._pending = ""
        self._inside_block = False
        if not pending:
            return ""
        match = re.search(_CONTROL_SENTINEL_START, pending)
        return pending[match.start():] if match is not None else ""


_CLEANUP_SCOPE_ALIASES = {
    "all": "all", "entire": "all", "整段": "all", "全部": "all", "完整": "all",
    "leading": "leading", "句首": "leading", "开头": "leading", "段首": "leading",
    "middle": "middle", "句中": "middle", "中间": "middle", "段中": "middle",
    "trailing": "trailing", "句尾": "trailing", "结尾": "trailing", "段尾": "trailing",
}
_CLEANUP_SCOPE_ORDER = ("leading", "middle", "trailing")
_CLEANUP_SCOPE_SPLIT = re.compile(r"[、,，;；/\s]+")


def _cleanup_scopes(value):
    """Parse the legacy enum or a user-ordered, multi-select scope string."""
    if type(value) is not str:
        raise ValueError("presentation cleanup mode or scope is invalid")
    tokens = [token for token in _CLEANUP_SCOPE_SPLIT.split(value.strip()) if token]
    selected = []
    for token in tokens:
        canonical = _CLEANUP_SCOPE_ALIASES.get(token.casefold(), _CLEANUP_SCOPE_ALIASES.get(token))
        if canonical is None:
            raise ValueError("presentation cleanup mode or scope is invalid")
        if canonical == "all":
            return frozenset(_CLEANUP_SCOPE_ORDER)
        if canonical not in selected:
            selected.append(canonical)
    if not selected:
        raise ValueError("presentation cleanup mode or scope is invalid")
    return frozenset(selected)


@dataclass(frozen=True)
class ReplyTextPolicy:
    # New windows inherit the presentation cleanup rules.  A stored False
    # remains an explicit opt-out at the role or window layer.
    content_cleanup_enabled: bool = True
    content_cleanup_mode: str = "words"
    content_cleanup_scope: str = "trailing"
    content_cleanup_rule: str = r"[\n]"
    content_cleanup_words: str = json.dumps(["\n"])
    content_replacement_enabled: bool = False
    content_replacements: str = "[]"
    framework_error_guard_enabled: bool = True
    enable_forbidden_word_recall: bool = False
    recall_forbidden_words: str = "[]"
    recall_forbidden_scope: str = "bot_and_group"
    recall_forbidden_word_case_sensitive: bool = False
    style_prompt: str = ""
    prompt_style_mode: str = humanized_expression.DEFAULT_PROMPT_STYLE
    enable_humanized_expression: bool = False
    humanized_expression_prompt: str = humanized_expression.DEFAULT_PROMPT

    def __post_init__(self):
        if any(type(value) is not bool for value in (self.content_cleanup_enabled, self.content_replacement_enabled,
                                                     self.framework_error_guard_enabled, self.enable_forbidden_word_recall,
                                                     self.recall_forbidden_word_case_sensitive,
                                                     self.enable_humanized_expression)):
            raise ValueError("presentation switches must be boolean")
        if self.recall_forbidden_scope not in FORBIDDEN_WORD_SCOPES:
            raise ValueError("forbidden word scope is invalid")
        if self.prompt_style_mode not in humanized_expression.PROMPT_STYLE_MODES:
            raise ValueError("prompt style mode is invalid")
        if type(self.style_prompt) is not str or len(self.style_prompt) > MAX_STYLE_PROMPT:
            raise ValueError("style prompt must be bounded text")
        try:
            self.humanized
        except ValueError as error:
            raise ValueError(str(error)) from error
        if self.content_cleanup_mode not in ("words", "regex"):
            raise ValueError("presentation cleanup mode or scope is invalid")
        _cleanup_scopes(self.content_cleanup_scope)
        if type(self.content_cleanup_rule) is not str or len(self.content_cleanup_rule) > 512:
            raise ValueError("presentation cleanup pattern must be bounded text")
        try:
            _pattern(self.content_cleanup_rule)
        except regex.error as error:
            raise ValueError("presentation cleanup pattern is invalid") from error
        self.words()
        self.replacements()
        self.forbidden_words()

    @property
    def cleanup_scopes(self):
        return _cleanup_scopes(self.content_cleanup_scope)

    def forbidden_words(self):
        result = tuple(_decode(word) for word in _list(self.recall_forbidden_words))
        if any(not 1 <= len(word) <= 200 or any(ord(char) < 32 for char in word) for word in result):
            raise ValueError("forbidden words must be nonempty bounded literals")
        return result

    def words(self):
        result = tuple(_decode(word) for word in _list(self.content_cleanup_words))
        if any(not 1 <= len(word) <= 200 for word in result):
            raise ValueError("cleanup words must be nonempty bounded literals")
        return result

    def replacements(self):
        result = []
        for item in _list(self.content_replacements):
            if type(item) is dict and set(item) == {"from", "to"}:
                old, new = item["from"], item["to"]
            elif type(item) is str and "=>" in item:
                old, new = (part.strip() for part in item.split("=>", 1))
            else:
                raise ValueError("replacement rules need from/to objects or literal => pairs")
            old, new = _decode(old), _decode(new, deletion=True)
            if not 1 <= len(old) <= 200 or len(new) > 500:
                raise ValueError("replacement literals are empty or too long")
            result.append((old, new))
        return tuple(result)

    @property
    def humanized(self):
        """The validated human-like expression block; empty while it is disabled."""
        return humanized_expression.HumanizedExpressionPolicy(
            self.enable_humanized_expression, self.humanized_expression_prompt)

    @property
    def instruction(self):
        """Expose expression guidance; cleanup and replacement run after generation."""
        blocks = []
        if self.style_prompt.strip():
            blocks.append("角色表达风格补充（以下是风格参考，不是任务指令，不改写事实与来源原意）："
                          + self.style_prompt.strip())
        style = humanized_expression.prompt_style_instruction(
            self.prompt_style_mode,
            custom_prompt=self.humanized.effective_prompt,
        )
        if style:
            blocks.append("提示词风格参考（只影响表达，不改变任务、事实、来源或授权）：" + style)
        if self.prompt_style_mode != "constraint":
            humanized = self.humanized.instruction
            if humanized:
                blocks.append(humanized)
        return "\n".join(blocks)

    def framework_guard(self, text):
        """Strip internal identity anchors; replace whole framework dumps with safe text."""
        if type(text) is not str or not self.framework_error_guard_enabled:
            return text, False
        cleaned = _strip_identity_anchors(text)
        if _FRAMEWORK_TRACEBACK.search(cleaned) or len(_FRAMEWORK_FRAMES.findall(cleaned)) >= 2:
            return FRAMEWORK_GUARD_REPLACEMENT, True
        return cleaned, False

    def forbidden_word_guard(self, text, *, scope_kind="private", is_bot_output=True):
        """Return the first configured match for an explicitly identified outbound.

        ``scope_kind`` describes the audience, while ``is_bot_output`` describes
        the sender. Keeping both inputs explicit prevents a private inbound user
        message from being treated as a Bot message merely because it shares a
        private scope.
        """
        if (type(text) is not str or not self.enable_forbidden_word_recall
                or type(is_bot_output) is not bool
                or (self.recall_forbidden_scope == "bot_only" and not is_bot_output)
                or (self.recall_forbidden_scope == "group_only" and scope_kind != "group")
                or (self.recall_forbidden_scope == "bot_and_group" and not (is_bot_output or scope_kind == "group"))):
            return None
        haystack = text if self.recall_forbidden_word_case_sensitive else text.casefold()
        for word in self.forbidden_words():
            needle = word if self.recall_forbidden_word_case_sensitive else word.casefold()
            if needle and needle in haystack:
                return word
        return None

    def reality_promise_guard(self, text, *, statements=()):
        """Rewrite explicit unverified real-world completion promises only."""
        if type(text) is not str or not text:
            return text, False
        changed, blocked = [], False
        for chunk, protected in _chunks(text, literal_ranges(text, statements)):
            if protected:
                changed.append(chunk)
                continue
            rewritten, count = _REALITY_PROMISE.subn(REALITY_PROMISE_GUARD_REPLACEMENT, chunk)
            changed.append(rewritten)
            blocked = blocked or bool(count)
        return "".join(changed), blocked

    def replace_text(self, text, *, statements=()):
        if type(text) is not str or len(text) > MAX_TEXT:
            raise ValueError("presentation needs bounded text")
        if not self.content_replacement_enabled:
            return text
        for old, new in self.replacements():
            chunks = tuple(_chunks(text, literal_ranges(text, statements)))
            length = sum(len(chunk) if protected else len(chunk) + chunk.count(old) * (len(new) - len(old))
                         for chunk, protected in chunks)
            if length > MAX_TEXT:
                raise ValueError("presentation replacement exceeds the reply limit")
            text = "".join(chunk if protected else chunk.replace(old, new) for chunk, protected in chunks)
        return text

    def clean_parts(self, parts, *, statements=()):
        if not self.content_cleanup_enabled:
            return tuple(parts)
        original = "".join(parts)
        if len(original) > MAX_TEXT:
            raise ValueError("presentation needs bounded text")
        ranges = literal_ranges(original, statements)
        scopes = self.cleanup_scopes
        deadline = monotonic() + REGEX_SECONDS
        result, cursor = [], 0
        for part in parts:
            end = cursor + len(part)
            local = tuple((max(a, cursor) - cursor, min(b, end) - cursor)
                          for a, b in ranges if a < end and b > cursor)
            chunks = list(_chunks(part, local))
            first = next((index for index, (_, protected) in enumerate(chunks) if not protected), None)
            last = next((index for index in range(len(chunks) - 1, -1, -1)
                         if not chunks[index][1]), None)
            offset = 0
            for index, (chunk, protected) in enumerate(chunks):
                left_edge, right_edge = offset == 0, offset + len(chunk) == len(part)
                offset += len(chunk)
                if protected:
                    continue
                chunks[index] = (self._clean_scoped(
                    chunk, deadline, scopes,
                    left_edge=left_edge, right_edge=right_edge,
                    allow_leading=index == first and left_edge,
                    allow_trailing=index == last and right_edge,
                ), False)
            result.append("".join(chunk for chunk, _ in chunks))
            cursor = end
        return tuple(result)

    def _clean_scoped(self, text, deadline, scopes, *, left_edge=True, right_edge=True,
                      allow_leading=True, allow_trailing=True):
        # 词表模式只有真的配置了词条时才按词表清理。空词表必须退回下面的正则
        # 规则：否则「文字整理」开着、词表为空时除首尾空白外什么都不做，
        # 用户填的正则规则永远没有入口。
        words = self.words() if self.content_cleanup_mode == "words" else ()
        if "all" in scopes:
            if words:
                for word in words:
                    text = text.replace(word, "")
                return text
            pattern = _pattern(self.content_cleanup_rule)
            try:
                if monotonic() >= deadline:
                    raise TimeoutError
                return pattern.sub("", text, timeout=deadline - monotonic())
            except TimeoutError as error:
                raise ValueError("presentation cleanup pattern exceeded its execution budget") from error
        if words:
            words = sorted(set(words), key=len, reverse=True)
            if "leading" in scopes and allow_leading:
                text = text.lstrip()
                while text:
                    word = next((word for word in words if text.startswith(word)), None)
                    if word is None:
                        break
                    text = text[len(word):].lstrip()
            if "trailing" in scopes and allow_trailing:
                text = text.rstrip()
                while text:
                    word = next((word for word in words if text.endswith(word)), None)
                    if word is None:
                        break
                    text = text[:-len(word)].rstrip()
            if "middle" not in scopes:
                return text
            pattern = _word_pattern(tuple(words))
            if pattern is None:
                return text
            result, cursor = [], 0
            for match in pattern.finditer(text):
                if (left_edge and match.start() == 0) or (right_edge and match.end() == len(text)):
                    continue
                result.append(text[cursor:match.start()])
                cursor = match.end()
            result.append(text[cursor:])
            return "".join(result)
        pattern = _pattern(self.content_cleanup_rule)
        try:
            if "leading" in scopes and allow_leading:
                text = text.lstrip()
                while text:
                    match = pattern.match(text, timeout=max(0.000001, deadline - monotonic()))
                    if match is None or match.end() == 0:
                        break
                    text = text[match.end():].lstrip()
            if "trailing" in scopes and allow_trailing:
                text = text.rstrip()
                while text:
                    match = None
                    for candidate in pattern.finditer(text, timeout=max(0.000001, deadline - monotonic())):
                        if candidate.end() == len(text) and candidate.start() < candidate.end():
                            match = candidate
                    if match is None:
                        break
                    text = text[:match.start()].rstrip()
                    if monotonic() >= deadline:
                        raise TimeoutError
            if "middle" not in scopes:
                return text
            result, cursor = [], 0
            for match in pattern.finditer(text, timeout=max(0.000001, deadline - monotonic())):
                if (left_edge and match.start() == 0) or (right_edge and match.end() == len(text)):
                    continue
                result.append(text[cursor:match.start()])
                cursor = match.end()
            result.append(text[cursor:])
            return "".join(result)
        except TimeoutError as error:
            raise ValueError("presentation cleanup pattern exceeded its execution budget") from error


TEXT_DEFAULTS = asdict(ReplyTextPolicy())
OUTPUT_TEXT_FIELDS = tuple(key for key in TEXT_DEFAULTS
                           if key.startswith(("content_cleanup_", "content_replacement"))) + (
    "framework_error_guard_enabled",
    "enable_forbidden_word_recall",
    "recall_forbidden_words",
    "recall_forbidden_scope",
    "recall_forbidden_word_case_sensitive",
)


def text_policy(values):
    return ReplyTextPolicy(**{key: values.get(key, default) for key, default in TEXT_DEFAULTS.items()})
