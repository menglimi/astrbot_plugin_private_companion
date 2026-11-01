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


# --- 出站计划文本闸门 --------------------------------------------------------
# 上游偶尔把隐藏的思维链/计划写进普通正文：首行一个协议词（hthought），随后
# 是英文计划，最后才是台词。这里只认可识别的泄露形态，并且采用"强标记解锁
# 弱删除"的双信号门：本条里没有强标记时一个字都不动，正常英文回复（技术解释、
# 引用他人原话、成语）因此不会被截断。保护范围是 fenced code、URL、分段标记
# 与已登记控制/语音标签的成对标；markdown 的整段保护会连泄露原文一起藏起来，
# 实测会漏掉大部分样本。
#
# 调用约定：闸门与 strip_internal_expression_protocol 相邻，正常顺序是先 strip
# 再闸门（真标签先被吃掉）。闸门自己也保护标签 span，所以即使被单独调用（新
# 发送点直接挂纯函数）也不会把成对标签拆开、把载荷留成可见正文。
#
# 已知中转标记：模型用 "h" 前缀把内部步骤写进正文（现场样本只有 hthought）。
# 这批词没有英文含义，整行出现即可判强。
_RELAY_PROTOCOL_TOKENS = frozenset(
    {"hthought", "hthinking", "hthink", "hreasoning", "hscratchpad", "hcot"})
# 语义词本身不是协议：ascot/apricot/mascot 以 cot 结尾，afterthought 以 thought
# 结尾，unthinking 以 thinking 结尾。它们只有在同时出现弱标记或中日文正文时才
# 按协议词处理，其余情况一律当正文（不做任何后缀匹配）。
_SEMANTIC_PLAN_WORDS = frozenset({"thought", "thinking", "reasoning", "scratchpad", "cot"})
_PROTOCOL_TOKEN_LINE = re.compile(r"(?m)^[ \t]*([a-z][a-z0-9_]{2,31})[ \t\r]*$")
_PROTOCOL_TOKEN = re.compile(r"[a-z][a-z0-9_]{2,31}")
_INTERNAL_SECTION_NAME = re.compile(
    r"`[^`\n]{0,80}?<\s*(?:companion_v2|private_companion)_[a-z0-9_]+\s*>[^`\n]{0,80}?`"
    r"|<\s*(?:companion_v2|private_companion)_[a-z0-9_]+\s*>")
_INTERNAL_TAG_NAME = re.compile(r"<\s*([a-z0-9_]+)")
# 已登记的控制标签是协议，不是计划文本：它们由 strip_internal_expression_protocol
# 成对处理，闸门即使先于它运行也不能拆散标签配对。
_REGISTERED_CONTROL_NAMES = frozenset(name.casefold() for name in _CONTROL_TAG_NAMES)
_INTERNAL_NAMESPACE_TOOL = re.compile(
    r"(?i)\b(?:call|emit|invoke|use)\s+`((?:newapi_|emit_text|companion_v2_|private_companion_)"
    r"[A-Za-z0-9_.]{2,})`")
# 弱信号只扩大删除区间，并在没有强信号时仅供观测。
_WEAK_PLAN_SIGNALS = (
    ("note_that", re.compile(r"(?i)\bnote that\b")),
    ("so_no_tag", re.compile(r"(?i)\bso no\b.{0,60}?\btag\b")),
    ("meta_verb", re.compile(
        r"(?i)\b(?:let'?s|let us)\s+(?:craft|emit|output|produce|answer|write|call|use|keep)\b")),
    ("backticked_call", re.compile(r"(?i)\b(?:call|emit|invoke|use)\s+`[A-Za-z_][A-Za-z0-9_.]{2,}`")),
)
_FENCE_LINE = re.compile(r"(?m)^ {0,3}(`{3,}|~{3,})([^\n]*)$")
_PLAN_URL = re.compile(r"(?:https?://|www\.)[^\s<>。！？，]+")
_CJK = re.compile(r"[\u3040-\u30ff\u31f0-\u31ff\u3400-\u4dbf\u4e00-\u9fff]")
_CJK_EDGE = "…—~～，,、；;：:。！？!?　"
_PLAN_CLAUSE_EDGE = "。！？!?；;…\n"


def _protocol_token_kind(token):
    """Classify a bare protocol-shaped word; relay markers are unambiguous."""
    if token in _RELAY_PROTOCOL_TOKENS:
        return "relay"
    if token in _SEMANTIC_PLAN_WORDS:
        return "semantic"
    return None


def is_isolated_protocol_token(text):
    """Whether a whole segment is one bare relay marker such as ``hthought``.

    ``thought``/``reasoning``/``cot`` on their own are ordinary English words and
    stay valid replies, and a whole segment carries no plan context that could
    prove otherwise.  Any other word (``ascot``, ``afterthought``, ``unthinking``)
    is prose: only exact relay markers qualify, never a suffix match.
    """
    if type(text) is not str:
        return False
    token = text.strip()
    return _PROTOCOL_TOKEN.fullmatch(token) is not None and token in _RELAY_PROTOCOL_TOKENS


def _plan_fence_ranges(text):
    """Pair fenced code blocks line by line; a regex here backtracks quadratically."""
    ranges, opener, start = [], "", 0
    for match in _FENCE_LINE.finditer(text):
        marker, info = match.group(1), match.group(2)
        if not opener:
            opener, start = marker, match.start()
        elif marker[0] == opener[0] and len(marker) >= len(opener) and not info.strip():
            ranges.append((start, match.end()))
            opener = ""
    return ranges


_PLAN_TAG_TOKEN = re.compile(
    rf"(?P<close>{_CONTROL_LITERAL_LT}\s*/\s*(?:{_CONTROL_REGISTERED_PATTERN})\s*{_CONTROL_LITERAL_GT}"
    rf"|{_CONTROL_ESCAPED_LT}\s*/\s*(?:{_CONTROL_REGISTERED_PATTERN})\s*{_CONTROL_ESCAPED_GT})"
    rf"|(?P<open>{_CONTROL_LITERAL_LT}\s*(?:{_CONTROL_REGISTERED_PATTERN})\b[^<>]*?{_CONTROL_LITERAL_GT}"
    rf"|{_CONTROL_ESCAPED_LT}\s*(?:{_CONTROL_REGISTERED_PATTERN})\b[^<>]*?{_CONTROL_ESCAPED_GT})",
    re.IGNORECASE,
)


def _plan_tag_ranges(text):
    """Keep registered control/voice tag spans whole so a removal cannot split a pair.

    ``<pc_tts>`` begins exactly at the boundary the gate deletes from and its
    payload is CJK, so an unprotected run removed the opening tag and left
    ``</pc_tts>`` together with the spoken line in the visible reply.  Both tag
    tokens and every paired block between them are protected.  A lone tag name
    written inside inline code is a mention (``So no `<pc_reaction_expression>`
    tag``), not protocol, and stays deletable with the plan it belongs to.
    """
    if _CONTROL_CANDIDATE_PATTERN.search(text) is None:
        return ()
    inline = _plan_backtick_pairs(text)
    spans, opens = [], []
    for match in _PLAN_TAG_TOKEN.finditer(text):
        if match.group("close") is None and any(
                left < match.start() and match.end() <= right for left, right in inline):
            continue
        spans.append(match.span())
        if match.group("close") is not None:
            pending = opens.pop() if opens else None
            if pending is not None and match.end() - pending <= _CONTROL_PAIRED_PAYLOAD_LIMIT:
                spans.append((pending, match.end()))
        else:
            opens.append(match.start())
    return spans


def _plan_protected_ranges(text):
    """Only fenced code, URLs, reserved tokens and registered tag spans stay intact.

    Markdown-wide protection is deliberately absent: it hides the leaked sample
    together with the prose around it.  The segmentation marker is protected so
    the gate can never eat a boundary before the splitter consumed it, and every
    registered control/voice tag span is protected so a paired wrapper cannot be
    cut in half (see ``_plan_tag_ranges``).
    """
    ranges = _plan_fence_ranges(text)
    ranges.extend(match.span() for match in _PLAN_URL.finditer(text))
    ranges.extend(control_token_ranges(text))
    ranges.extend(_plan_tag_ranges(text))
    return _merged(ranges)


def _plan_masked(text, ranges):
    chunks, cursor = [], 0
    for start, stop in ranges:
        chunks.append(text[cursor:start])
        chunks.append(" " * (stop - start))
        cursor = stop
    chunks.append(text[cursor:])
    return "".join(chunks)


def _plan_backtick_pairs(text):
    """Inline-code spans: a tag name written there is an example, not protocol."""
    if "`" not in text:
        return ()
    pairs, stack = [], []
    for index, char in enumerate(text):
        if char != "`":
            continue
        if stack:
            pairs.append((stack.pop(), index))
        else:
            stack.append(index)
    return tuple(pairs)


def _plan_quoted_pairs(text):
    """Offsets between backticks and quotes: plan words quoted there are speech."""
    pairs = list(_plan_backtick_pairs(text))
    for opener, closer in (("“", "”"), ("「", "」")):
        stack = []
        for index, char in enumerate(text):
            if char == opener:
                stack.append(index)
            elif char == closer and stack:
                pairs.append((stack.pop(), index))
    quotes = [index for index, char in enumerate(text) if char == '"']
    pairs.extend(zip(quotes[0::2], quotes[1::2]))
    return tuple(pairs)


def _plan_prefix_start(text, offset):
    """Keep the body in front of the markers and drop the English plan clause."""
    position = offset
    while position > 0:
        char = text[position - 1]
        if char in _PLAN_CLAUSE_EDGE or _CJK.match(char) is not None:
            break
        position -= 1
    return position


def _plan_body_start(text, offset, pairs):
    """First CJK body character after the markers, ignoring quoted plan text."""
    for match in _CJK.finditer(text, offset):
        position = match.start()
        if any(left <= position <= right for left, right in pairs):
            continue
        # 「喂，」「唔……」这类开场语气词属于正文，接缝向前回溯。
        while position > offset and (text[position - 1] in _CJK_EDGE
                                     or _CJK.match(text[position - 1]) is not None):
            position -= 1
        return position
    return len(text)


def _plan_removal(text, protected, spans):
    """Delete only the judged intervals; code fences and URLs are kept verbatim."""
    editable = []
    for start, stop in _merged(spans):
        cursor = start
        for left, right in protected:
            if right <= cursor or left >= stop:
                continue
            if left > cursor:
                editable.append((cursor, min(left, stop)))
            cursor = max(cursor, right)
        if cursor < stop:
            editable.append((cursor, stop))
    chunks, cursor = [], 0
    for start, stop in _merged(editable):
        left = text[cursor:start].rstrip(" \t")
        arc = stop
        while arc < len(text) and text[arc] in " \t":
            arc += 1
        right = text[arc:arc + 1]
        # 接缝处收成一个空格，避免删段后留下双空格或把两个词粘在一起。
        # 左边界是闭合标签时（成对 <pc_tts>…</pc_tts> 后面接正文）不加空格：
        # 标签由语音管线消费，插空格会改变用户可见正文。
        spacer = (" " if left and right and left[-1] != ">"
                  and not left.endswith("\n") and right not in "\n" + _CJK_EDGE else "")
        chunks.append(left)
        chunks.append(spacer)
        cursor = arc
    chunks.append(text[cursor:])
    return "".join(chunks).strip(" \t\r\n，,、;；")


def _plan_quoted_body(text, pairs, protected):
    """Fallback body for a plan that only quotes the reply.

    A model that writes ``Let's reply "晚安，做个好梦".`` leaves the whole turn
    plan-shaped: every CJK character that could start the body sits inside the
    quotes, so deleting the plan would remove everything.  Rather than silencing
    the turn, keep the quoted line itself (the actual reply) as the body.
    """
    chunks, accepted = [], []
    for left, right in sorted(pairs, key=lambda item: (item[0], -item[1])):
        if any(start < right and left < stop for start, stop in protected):
            continue
        if any(start <= left and right <= stop for start, stop in accepted):
            continue
        body = text[left + 1:right].strip()
        if not body or _CJK.search(body) is None:
            continue
        accepted.append((left, right))
        chunks.append(body)
    return " ".join(chunks)


# 多段计划：一段删掉后，下一段可能仍然满足强标记形态（被标签、URL 等隔开），
# 这里做有界多轮，直到没有新命中或上限耗尽；标签 span 始终受保护，多轮不会拆标签。
_PLAN_GUARD_PASSES = 4


def _plan_leak_guard_pass(text):
    """One deletion pass: first strong marker to the first unquoted body character."""
    protected = _plan_protected_ranges(text)
    masked = _plan_masked(text, protected)
    weak = tuple(name for name, pattern in _WEAK_PLAN_SIGNALS if pattern.search(masked) is not None)
    # 语义词（thought/thinking/cot…）单独出现时就是普通英文；只有本条里另有弱标记
    # 或中日文正文时才当中转标记，避免 ascot/apricot/mascot/unthinking 这类误杀。
    contextual = bool(weak) or _CJK.search(masked) is not None
    marks = []
    for match in _PROTOCOL_TOKEN_LINE.finditer(masked):
        token = match.group(1)
        kind = _protocol_token_kind(token)
        if kind is None or (kind == "semantic" and not contextual):
            continue
        # 标签 span 在 masked 里是空格：`</pc_tts>hthought` 这种「计划段贴着标签
        # 收尾」的形态在这里变成行首 token，多轮处理后与行首形态同一条规则即可覆盖。
        marks.append((match.start(), match.end(), "line", f"protocol_token_line:{token}"))
    for match in _INTERNAL_SECTION_NAME.finditer(masked):
        tag = _INTERNAL_TAG_NAME.search(match.group(0))
        if tag is not None and tag.group(1).casefold() in _REGISTERED_CONTROL_NAMES:
            continue
        marks.append((match.start(), match.end(), "mention", "internal_section_name"))
    for match in _INTERNAL_NAMESPACE_TOOL.finditer(masked):
        marks.append((match.start(), match.end(), "mention", f"internal_namespace_tool:{match.group(1)}"))
    if not marks:
        return text, (), tuple(f"weak_only:{name}" for name in weak)
    marks.sort()
    reasons = list(dict.fromkeys(label for _, _, _, label in marks))
    reasons.extend(f"weak:{name}" for name in weak)
    pairs = _plan_quoted_pairs(masked)
    mentions = [(start, stop) for start, stop, kind, label in marks if label == "internal_section_name"
                and _CJK.search(masked[:start]) is not None
                and _CJK.search(masked[stop:]) is not None]
    if mentions and len(mentions) == len(marks):
        # 正文中间提到内部段落名：只删这个名字，句子与正文都保留。
        cleaned = _plan_removal(text, protected, mentions)
        if cleaned == text:
            return text, (), tuple(reasons)
        return cleaned, _merged(mentions), ("inline_mention", *reasons)
    # 删除终点只由「正文之前」的标记决定：正文之后再出现的计划段不会把已存在的
    # 正文一起吞掉，而是留给下一轮。每轮只把终点扩到「在正文之前」的最后一处标记，
    # 避免对每个标记都重扫一遍正文（长正文会退化成二次方）。
    end = marks[0][1]
    body = _plan_body_start(masked, end, pairs)
    while True:
        extended = end
        for mark_start, mark_end, _kind, _label in marks[1:]:
            if mark_start >= body:
                break
            if mark_end > extended:
                extended = mark_end
        if extended == end:
            break
        end = extended
        body = _plan_body_start(masked, end, pairs)
    start = (_plan_prefix_start(masked, marks[0][0])
             if _CJK.search(masked[:marks[0][0]]) is not None else 0)
    stop = max(body, end)
    cleaned = _plan_removal(text, protected, ((start, stop),))
    if not cleaned.strip():
        # 删除后为空且引号内含中日文时，保留引号内正文（计划把正文用引号或
        # 反引号包住的情形）：宁可接缝不完美，也不让整轮静默。
        quoted = _plan_quoted_body(text, pairs, protected)
        if quoted:
            return quoted, _merged(((start, stop),)), (*reasons, "quoted_body_fallback")
    if cleaned == text:
        return text, (), tuple(reasons)
    return cleaned, _merged(((start, stop),)), tuple(reasons)


def plan_leak_guard(text):
    """Remove recognizable model plan text; return (cleaned, removed_spans, reasons).

    A strong signal unlocks deletion: an exact relay token line (``hthought``),
    an internal section name (``R2``), or an internal-namespace tool call (``R3``).
    Semantic words (``thought``/``thinking``/``reasoning``/``scratchpad``/``cot``)
    count only when the same message also carries a weak signal or CJK body;
    no suffix matching is used, so ``ascot``/``afterthought``/``unthinking`` stay
    ordinary prose.  Weak signals (``note that`` / ``so no … tag`` / ``Let's +
    元话语`` / any backticked call) only extend the removed interval and are
    reported; they never delete on their own.  Without a strong signal the text
    comes back unchanged, which is what keeps ordinary English replies intact.

    Deletion never crosses a fenced block, URL, segmentation marker or a
    registered control/voice tag span (paired ``<pc_tts>…</pc_tts>`` survives
    whole).  A plan that quotes the reply keeps that quoted body instead of
    silencing the turn, and a bounded number of passes removes later plan
    segments that only become visible once the first one is gone.  An empty
    result means the whole reply was plan text: the caller drops it exactly like
    other fully removed bodies.  ``reasons`` starting with ``weak_only:`` reports
    a plan-shaped message the gate deliberately did not touch.  ``spans`` always
    describe the first pass and are for logging only.
    """
    if type(text) is not str or not text.strip() or len(text) > MAX_TEXT:
        return text, (), ()
    cleaned, spans, reasons = _plan_leak_guard_pass(text)
    if not spans:
        return text, (), reasons
    collected = list(reasons)
    passes = 1
    for _pass in range(_PLAN_GUARD_PASSES - 1):
        if not cleaned.strip():
            break
        following, following_spans, following_reasons = _plan_leak_guard_pass(cleaned)
        if not following_spans:
            break
        passes += 1
        collected.extend(reason for reason in following_reasons if reason not in collected)
        cleaned = following
    if passes > 1:
        collected.append(f"passes:{passes}")
    if cleaned == text:
        return text, (), tuple(collected)
    return cleaned, spans, tuple(collected)


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
