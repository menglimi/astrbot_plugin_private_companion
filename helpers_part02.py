# -*- coding: utf-8 -*-
"""helpers 拆分件 part02（机械搬移，行为不变）。

函数 / 常量 / 类逐字搬自 helpers.py，仅调整模块级依赖的导入来源。
"""
import re
import unicodedata
from typing import Any
try:  # package import
    from .outbound_tag_registry import (
        _ESCAPED_NONSTANDARD_SELF_CLOSING_TAG_PATTERN,
        _NONSTANDARD_SELF_CLOSING_TAG_PATTERN,
        strip_own_tags,
    )
except ImportError:  # direct test/import from the plugin directory
    from outbound_tag_registry import (
        _ESCAPED_NONSTANDARD_SELF_CLOSING_TAG_PATTERN,
        _NONSTANDARD_SELF_CLOSING_TAG_PATTERN,
        strip_own_tags,
    )

try:  # package import
    from .helpers_part01 import (
        _safe_int,
    )
except ImportError:  # direct test/import from the plugin directory
    from helpers_part01 import (
        _safe_int,
    )


_OPTIONAL_MODEL_DEPENDENCIES = {
    "torch",
    "torchvision",
    "torchaudio",
    "sentence_transformers",
    "transformers",
}


def _missing_optional_model_dependency(exc: BaseException) -> str:
    def optional_root(name: Any) -> str:
        normalized = str(name or "").strip()
        for dependency in _OPTIONAL_MODEL_DEPENDENCIES:
            if normalized == dependency or normalized.startswith(f"{dependency}."):
                return dependency
        return ""

    pending: list[BaseException] = [exc]
    visited: set[int] = set()
    while pending:
        current = pending.pop(0)
        if id(current) in visited:
            continue
        visited.add(id(current))
        if isinstance(current, ModuleNotFoundError):
            dependency = optional_root(getattr(current, "name", ""))
            if dependency:
                return dependency
        match = re.search(r"No module named ['\"]([^'\"]+)['\"]", str(current or ""))
        if match:
            dependency = optional_root(match.group(1))
            if dependency:
                return dependency
        for linked in (getattr(current, "__cause__", None), getattr(current, "__context__", None)):
            if isinstance(linked, BaseException) and id(linked) not in visited:
                pending.append(linked)
    return ""


_GARBLED_TEXT_MARKERS = ("Ã", "â", "鈥", "銆", "鏉", "锟", "Ð", "Ê", "¤", "\ufffd")


_BINARY_TEXT_PREFIXES = ("JFIF", "EXIF", "GIF87A", "GIF89A", "%PDF-", "PK\x03\x04")

def _text_looks_garbled(text: Any) -> bool:
    normalized = str(text or "").strip()
    if not normalized:
        return False
    compact = re.sub(r"\s+", "", normalized)
    if not compact:
        return False
    head = compact[:32].upper()
    if any(head.startswith(prefix) for prefix in _BINARY_TEXT_PREFIXES):
        return True
    replacement_count = compact.count("\ufffd")
    if replacement_count >= 2:
        return True
    mojibake_count = sum(compact.count(marker) for marker in _GARBLED_TEXT_MARKERS if marker != "\ufffd")
    if mojibake_count >= 3 and len(compact) >= 12:
        return True
    control_count = 0
    for ch in compact[:400]:
        if ch in "\n\r\t":
            continue
        if unicodedata.category(ch).startswith("C"):
            control_count += 1
    return control_count >= 2


_PERSONALITY_SYNC_COMMENT_PATTERN = re.compile(
    r"<!--\s*private_companion_personality_sync_v\d+\s*-->",
    re.IGNORECASE,
)


_TRUNCATED_PERSONALITY_SYNC_COMMENT_PATTERN = re.compile(
    r"<!--\s*private_companion_personality_sync_v\d+[\s\S]*$",
    re.IGNORECASE,
)

_PERSONALITY_SYNC_BLOCK_PATTERN = re.compile(
    r"<\s*personality_sync\b[^>]*>[\s\S]*?<\s*/\s*personality_sync\s*>",
    re.IGNORECASE,
)

_PERSONALITY_SYNC_CLOSING_TAG_PATTERN = re.compile(
    r"<\s*/\s*personality_sync\s*>",
    re.IGNORECASE,
)

_PHOTO_TOOL_SILENT_SENTINEL_PATTERN = re.compile(
    r"\[\[PC_PHOTO_SENT_NO_FOLLOWUP\]\]",
    re.IGNORECASE,
)

def _strip_personality_sync_blocks(text: Any) -> str:
    """Remove complete or truncated internal personality synchronization blocks."""
    normalized = str(text or "")
    normalized = _PERSONALITY_SYNC_COMMENT_PATTERN.sub("", normalized)
    normalized = _TRUNCATED_PERSONALITY_SYNC_COMMENT_PATTERN.sub("", normalized)
    normalized = _PERSONALITY_SYNC_BLOCK_PATTERN.sub("", normalized)
    # A generation can be cut off before the closing tag is produced.
    normalized = re.sub(
        r"<\s*personality_sync\b[^>]*>[\s\S]*$",
        "",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = _PERSONALITY_SYNC_CLOSING_TAG_PATTERN.sub("", normalized)
    return normalized


def _strip_internal_message_blocks(
    text: Any, *, enabled: bool = True, tts_enabled: bool = False
) -> str:
    """Remove registered internal blocks while preserving Markdown examples."""
    source_text = str(text or "")
    if not enabled:
        return source_text
    original = source_text.replace("\r\n", "\n").replace("\r", "\n")
    parts = _MARKDOWN_CODE_SPAN_PATTERN.split(original)
    for index in range(0, len(parts), 2):
        segment = _strip_history_media_markers(parts[index], preserve_whitespace=True)
        segment = strip_own_tags(
            segment,
            tts_enabled=tts_enabled,
            preserve_code_spans=False,
        )
        segment = _strip_personality_sync_blocks(segment)
        segment = _strip_group_member_safety_markers(segment)
        parts[index] = segment
    normalized = "".join(parts)
    if not original.startswith("\n"):
        normalized = normalized.lstrip("\n")
    if not original.endswith("\n"):
        normalized = normalized.rstrip("\n")
    return normalized


def _format_history_media_marker(*, images: int = 0, records: int = 0) -> str:
    """Encode delivered media as context metadata instead of chat-like prose."""
    image_count = _safe_int(images, 0, 0, 999)
    record_count = _safe_int(records, 0, 0, 999)
    attributes: list[str] = []
    if image_count:
        attributes.append(f'images="{image_count}"')
    if record_count:
        attributes.append(f'records="{record_count}"')
    if not attributes:
        return ""
    return f"<pc_history_media {' '.join(attributes)} />"


_HISTORY_MEDIA_MARKER_NAME = (
    r"pc[_-]?history[_-]?media(?:[_-]?(?:records?|images?))?"
)


_HISTORY_MEDIA_MARKER_PATTERN = re.compile(
    rf"<\s*{_HISTORY_MEDIA_MARKER_NAME}\b[^>]*>"
    rf"(?:[\s\S]*?<\s*/\s*{_HISTORY_MEDIA_MARKER_NAME}\s*>)?",
    re.IGNORECASE,
)

_ESCAPED_HISTORY_MEDIA_MARKER_PATTERN = re.compile(
    rf"&lt;\s*/?\s*{_HISTORY_MEDIA_MARKER_NAME}\b[^&\r\n]{{0,240}}&gt;",
    re.IGNORECASE,
)

def _has_history_media_marker(text: Any) -> bool:
    """Return whether text contains raw, escaped, or mutated media metadata."""
    normalized = str(text or "")
    return bool(
        _HISTORY_MEDIA_MARKER_PATTERN.search(normalized)
        or _ESCAPED_HISTORY_MEDIA_MARKER_PATTERN.search(normalized)
    )


def _strip_history_media_markers(
    text: Any,
    *,
    preserve_whitespace: bool = False,
) -> str:
    """Remove internal media metadata and legacy chat-like attachment notes."""
    normalized = str(text or "")
    had_marker = _has_history_media_marker(normalized)
    legacy_pattern = re.compile(
        r"[（(]\s*(?:(?:随消息)?发送(?:了)?\s*(?:(?:一张|\d+\s*张)\s*图片|"
        r"(?:一条|\d+\s*条)\s*语音)\s*(?:[，,]\s*)?)+[）)]"
    )
    for pattern in (
        _HISTORY_MEDIA_MARKER_PATTERN,
        _ESCAPED_HISTORY_MEDIA_MARKER_PATTERN,
        legacy_pattern,
    ):
        flags = pattern.flags | re.MULTILINE
        normalized = re.sub(
            rf"(?<=[^\s])[ \t]+(?:{pattern.pattern})[ \t]+(?=[^\s])",
            " ",
            normalized,
            flags=flags,
        )
    standalone_patterns = "|".join(
        f"(?:{pattern.pattern})"
        for pattern in (
            _HISTORY_MEDIA_MARKER_PATTERN,
            _ESCAPED_HISTORY_MEDIA_MARKER_PATTERN,
            legacy_pattern,
        )
    )
    standalone_block = re.compile(
        rf"^(?:[ \t]*(?:{standalone_patterns})[ \t]*(?:\n|$))+",
        flags=re.IGNORECASE | re.MULTILINE,
    )

    def remove_standalone_block(match: re.Match[str]) -> str:
        has_visible_before = bool(normalized[: match.start()].rstrip("\n"))
        has_visible_after = bool(normalized[match.end() :].lstrip("\n"))
        return "\n" if has_visible_before and has_visible_after else ""

    normalized = standalone_block.sub(remove_standalone_block, normalized)
    normalized = _HISTORY_MEDIA_MARKER_PATTERN.sub("", normalized)
    normalized = _ESCAPED_HISTORY_MEDIA_MARKER_PATTERN.sub("", normalized)
    normalized = legacy_pattern.sub("", normalized)
    if had_marker:
        normalized = re.sub(r"(?<!\w)[（(]\s*[）)]", "", normalized)
    if preserve_whitespace:
        return normalized
    normalized = re.sub(r"[ \t]+([，,。！？!?；;：:、~～…])", r"\1", normalized)
    normalized = re.sub(r"\n[ \t]+", "\n", normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()


_MARKDOWN_CODE_SPAN_PATTERN = re.compile(
    r"(```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\r\n]+`)",
    re.MULTILINE,
)


_LEAKED_CHAT_EMOTION_CONTROL_PATTERN = re.compile(
    r"(?i)(?<![\w`])\[(?:affectionate|shy|happy|sad|angry|calm|excited|surprised|"
    r"nervous|scared|worried|upset|frustrated|embarrassed|disgusted|moved|proud|"
    r"relaxed|grateful|confident|curious|confused|nostalgic|sleepy|thoughtful|"
    r"yawning|comforting|warm|softly|whispering|laughing|chuckling|sighing)\]"
)


_GROUP_MEMBER_SAFETY_MARKER_PATTERN = re.compile(
    r"<\s*pc_member_safety\s*>(?P<body>[\s\S]*?)<\s*/\s*pc_member_safety\s*>",
    re.IGNORECASE,
)


_ESCAPED_GROUP_MEMBER_SAFETY_MARKER_PATTERN = re.compile(
    r"&lt;\s*pc_member_safety\s*&gt;[\s\S]*?&lt;\s*/\s*pc_member_safety\s*&gt;",
    re.IGNORECASE,
)

def _strip_group_member_safety_markers(text: Any) -> str:
    """Remove complete or malformed internal member-safety markers from outbound text."""
    normalized = str(text or "")
    normalized = _GROUP_MEMBER_SAFETY_MARKER_PATTERN.sub("", normalized)
    normalized = _ESCAPED_GROUP_MEMBER_SAFETY_MARKER_PATTERN.sub("", normalized)
    # A truncated generation must not leak a partial control block either.
    normalized = re.sub(
        r"<\s*/?\s*pc_member_safety\s*>|<\s*pc_member_safety\b[\s\S]*$",
        "",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"&lt;\s*/?\s*pc_member_safety\s*&gt;|&lt;\s*pc_member_safety\b[\s\S]*$",
        "",
        normalized,
        flags=re.IGNORECASE,
    )
    return normalized


def _strip_nonstandard_chat_control_tags(
    text: Any,
    *,
    tts_enabled: bool = False,
    preserve_whitespace: bool = False,
) -> str:
    """Remove plugin control tags and internal media metadata."""
    normalized = str(text or "")
    if not normalized:
        return ""
    # This cleaner is also used by TTS/tool delivery paths that do not call the
    # full outbound cleaner. Models occasionally mutate the internal marker to
    # forms such as <pc_history_media_records="1" />.
    normalized = _HISTORY_MEDIA_MARKER_PATTERN.sub("", normalized)
    normalized = _ESCAPED_HISTORY_MEDIA_MARKER_PATTERN.sub("", normalized)
    normalized = _NONSTANDARD_SELF_CLOSING_TAG_PATTERN.sub("", normalized)
    normalized = _ESCAPED_NONSTANDARD_SELF_CLOSING_TAG_PATTERN.sub("", normalized)
    if tts_enabled:
        normalized = _LEAKED_CHAT_EMOTION_CONTROL_PATTERN.sub("", normalized)
    if not preserve_whitespace:
        normalized = re.sub(r"\s+([，,。！？!?；;：:、~～…])", r"\1", normalized)
        normalized = re.sub(r"([（(【\[])\s+", r"\1", normalized)
        normalized = re.sub(r"\s+([）)】\]])", r"\1", normalized)
        normalized = re.sub(r"[ \t]{2,}", " ", normalized)
    return normalized


# Known HTML/XML element names that must never be treated as leaked control tags.
# Matched self-closing tags whose name is NOT in this set are removed from
# persisted companion data (e.g. model pseudo-action tags such as <bubble/>).
_KNOWN_HTML_SELF_CLOSING_EXCLUDED = (
    "img", "br", "hr", "input", "meta", "link", "source", "area", "base", "col",
    "embed", "param", "track", "wbr", "colgroup", "command", "keygen",
    "menuitem", "picture", "audio", "video", "iframe", "svg", "path", "circle",
    "rect", "use", "g", "canvas", "line", "polygon", "ellipse", "polyline",
)

_GENERIC_SELF_CLOSING_TAG_PATTERN = re.compile(
    r"<\s*(?!(?:" + "|".join(_KNOWN_HTML_SELF_CLOSING_EXCLUDED) + r")\b)"
    r"[A-Za-z][A-Za-z0-9_-]*(?:\s+[^<>\r\n]{0,200})?/\s*>",
    re.IGNORECASE,
)
_GENERIC_SELF_CLOSING_ESCAPED_PATTERN = re.compile(
    r"&lt;\s*(?!(?:" + "|".join(_KNOWN_HTML_SELF_CLOSING_EXCLUDED) + r")\b)"
    r"[A-Za-z][A-Za-z0-9_-]*(?:\s+[^&\r\n]{0,200})?/\s*&gt;",
    re.IGNORECASE,
)


def _strip_persisted_chat_control_tags(text: Any, *, tts_enabled: bool = False) -> str:
    """Clean leaked controls while preserving literal tags shown as Markdown code.

    Removal covers plugin control tags (pc_/private_companion_) and unknown
    self-closing tags such as <bubble/>, but Markdown code spans and known HTML
    void/element tags are left untouched.
    """
    normalized = str(text or "")
    if not normalized:
        return ""
    parts = _MARKDOWN_CODE_SPAN_PATTERN.split(normalized)
    for index in range(0, len(parts), 2):
        segment = _strip_nonstandard_chat_control_tags(parts[index], tts_enabled=tts_enabled)
        segment = _GENERIC_SELF_CLOSING_TAG_PATTERN.sub("", segment)
        segment = _GENERIC_SELF_CLOSING_ESCAPED_PATTERN.sub("", segment)
        parts[index] = segment
    return "".join(parts)


def _strip_outbound_control_blocks(
    text: Any,
    *,
    enabled: bool = True,
    tts_enabled: bool = False,
    preserve_private_tts_tokens: bool = False,
    allowed_private_tts_tokens: set[str] | None = None,
) -> str:
    source_text = str(text or "")
    if not enabled:
        return source_text
    had_photo_sentinel = bool(_PHOTO_TOOL_SILENT_SENTINEL_PATTERN.search(source_text))
    original = source_text.replace("\r\n", "\n").replace("\r", "\n")
    original = re.sub(
        rf"(?m)^[ \t]*{_PHOTO_TOOL_SILENT_SENTINEL_PATTERN.pattern}[ \t]*(?:\n|$)",
        "",
        original,
        flags=_PHOTO_TOOL_SILENT_SENTINEL_PATTERN.flags | re.MULTILINE,
    )
    if had_photo_sentinel:
        original = original.rstrip(" \t")
    protected: dict[str, str] = {}
    if preserve_private_tts_tokens and allowed_private_tts_tokens:
        for index, token in enumerate(sorted(allowed_private_tts_tokens)):
            marker = f"\x00PCTTS{index}\x00"
            protected[marker] = f"[[PCTTS:{token}]]"
            original = original.replace(protected[marker], marker)
    parts = _MARKDOWN_CODE_SPAN_PATTERN.split(original)
    for index in range(0, len(parts), 2):
        segment = _strip_history_media_markers(parts[index], preserve_whitespace=True)
        segment = strip_own_tags(
            segment,
            tts_enabled=tts_enabled,
            preserve_code_spans=False,
        )
        segment = _strip_personality_sync_blocks(segment)
        segment = _strip_group_member_safety_markers(segment)
        parts[index] = segment
    normalized = "".join(parts)
    for marker, token in protected.items():
        normalized = normalized.replace(marker, token)
    if not preserve_private_tts_tokens:
        normalized = re.sub(r"\[\[PCTTS:[^\]]*\]\]", "", normalized)
    if not original.startswith("\n"):
        normalized = normalized.lstrip("\n")
    if not original.endswith("\n"):
        normalized = normalized.rstrip("\n")
    if had_photo_sentinel:
        normalized = normalized.rstrip()
    return normalized


def _normalize_outbound_punctuation_flow(text: Any) -> str:
    normalized = str(text or "")
    if not normalized:
        return ""
    soft = "呢呀啊嘛吧哦喔诶欸啦哇哟"
    short_token = r"(?:[A-Za-z0-9_\-/\\]{1,60}|[\u4e00-\u9fff]{1,10}|[\u4e00-\u9fffA-Za-z0-9_\-/\\]{1,24})"
    normalized = re.sub(
        rf"([A-Za-z0-9_\-/\\]{{1,60}})[。！？!?]\s+([{soft}])(?=[，,。！？!?~～\s]|$)",
        r"\1\2",
        normalized,
    )
    normalized = re.sub(
        rf"({short_token})[。！？!?]\s+([{soft}])(?=[，,。！？!?~～\s]|$)",
        r"\1\2",
        normalized,
    )
    normalized = re.sub(
        rf"(/[A-Za-z0-9_\-\u4e00-\u9fff]{{1,24}})[，,]\s*([{soft}])(?=[。！？!?~～\s]|$)",
        r"\1 \2",
        normalized,
    )
    command_like = r"(?:[A-Za-z0-9_\-]{1,24}|[\u4e00-\u9fff]{1,8}(?:/[\u4e00-\u9fffA-Za-z0-9_\-]{1,12})+)"
    normalized = re.sub(
        rf"({command_like})[，,]\s*([{soft}])(?=[。！？!?~～\s]|$)",
        r"\1\2",
        normalized,
    )
    normalized = re.sub(
        rf"([A-Za-z0-9_\-/\\]{{1,60}})[，,]\s*([{soft}])(?=[。！？!?~～\s]|$)",
        r"\1\2",
        normalized,
    )
    normalized = re.sub(
        rf"([\u4e00-\u9fff]{{1,10}})[，,]\s*([{soft}])(?=[。！？!?~～\s]|$)",
        r"\1\2",
        normalized,
    )
    return normalized


def _semantic_text_compact(text: Any) -> str:
    normalized = str(text or "")
    normalized = re.sub(r"^(?:读后感|画面记录|札记\s*\d*|笔记\s*\d*)[:：]\s*", "", normalized.strip())
    normalized = re.sub(r"[\s\r\n\t\"'“”‘’《》【】\[\]（）(){}<>.,，。！？!?；;：:、~～…—_\-]+", "", normalized)
    return normalized.lower()


def _text_similarity(left: Any, right: Any) -> float:
    a = _semantic_text_compact(left)
    b = _semantic_text_compact(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    if len(shorter) >= 12 and shorter in longer:
        return len(shorter) / max(1, len(longer))

    def grams(value: str) -> set[str]:
        if len(value) <= 2:
            return {value}
        return {value[index : index + 2] for index in range(len(value) - 1)}

    left_grams = grams(a)
    right_grams = grams(b)
    overlap = len(left_grams & right_grams)
    union = len(left_grams | right_grams)
    if union <= 0:
        return 0.0
    return overlap / union


_LEGACY_TAG_PATTERN = re.compile(r"&&([A-Za-z_][A-Za-z0-9_]*)&&")


_LEGACY_TAG_CANONICAL_ALIASES = {
    "morning": "morning_greeting",
    "noon": "noon_greeting",
    "evening": "evening_greeting",
    "daily_greeting": "daily_greeting",
    "pending_followup": "pending_followup",
    "followup": "pending_followup",
    "random": "random",
    "state": "state_share",
    "event": "event",
    "group": "group_share",
    "diary": "diary_share",
    "check_in": "check_in",
    "quiet_care": "quiet_care",
}

_LEGACY_TAG_LABEL_ALIASES = {
    "morning_greeting": "早安",
    "noon_greeting": "午安",
    "evening_greeting": "晚安",
    "daily_greeting": "日常招呼",
    "pending_followup": "补一句",
    "random": "轻微想念",
    "state_share": "身体状态",
    "event": "具体事件",
    "group_share": "群里那点事",
    "diary_share": "日记碎片",
    "check_in": "顺手问候",
    "quiet_care": "轻轻关心",
}

def normalize_legacy_tag_text(value: Any, *, label: bool = False) -> str:
    text = str(value or "")
    if not text:
        return ""

    def _replace(match: re.Match[str]) -> str:
        token = str(match.group(1) or "").strip().lower()
        canonical = _LEGACY_TAG_CANONICAL_ALIASES.get(token, token)
        if label:
            return _LEGACY_TAG_LABEL_ALIASES.get(canonical, canonical.replace("_", " ") if canonical else "")
        return canonical

    normalized = _LEGACY_TAG_PATTERN.sub(_replace, text)
    return normalized.strip()


_MISSING = object()
