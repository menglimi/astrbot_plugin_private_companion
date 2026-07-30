# -*- coding: utf-8 -*-

from __future__ import annotations

import re
import time
import unicodedata
import zoneinfo
from datetime import date, datetime
from typing import Any

_today_key_timezone = ""

_GROUP_MESSAGE_URL_PATTERN = re.compile(
    r"(?i)(?<![\w@])(?:https?://|www\.)[^\s<>\"“”‘’]+"
)
_GROUP_SHARE_MARKER_PATTERN = re.compile(
    r"(?i)(?:"
    r"[\[【](?:分享|链接|网页|网页分享|卡片|小程序|QQ小程序|转发消息|合并转发|JSON消息|XML消息)[\]】]"
    r"|\[CQ:(?:json|xml|share|miniapp)\b[^\]]*\]"
    r")"
)
_GROUP_SHARE_BOILERPLATE_PATTERN = re.compile(
    r"(?i)(?:"
    r"当前\s*QQ\s*版本不支持(?:此|该)?应用[，,、\s]*请升级"
    r"|当前\s*QQ\s*版本不支持查看(?:此|该)?内容[，,、\s]*请升级"
    r"|(?:你的|您(?:的)?|当前)?\s*QQ\s*版本过低[，,、\s]*"
    r"(?:暂不支持查看(?:此|该)?内容|请(?:升级|更新)(?:后)?查看)"
    r"|请使用最新版本(?:手机)?\s*QQ\s*查看"
    r")"
)


def _now_ts() -> float:
    return time.time()


def _normalize_timezone_name(timezone_name: Any, default: str = "Asia/Shanghai") -> str:
    candidate = str(timezone_name or "").strip() or default
    try:
        zoneinfo.ZoneInfo(candidate)
        return candidate
    except Exception:
        return default


def _normalize_timezone_setting(timezone_name: Any) -> str:
    candidate = str(timezone_name or "").strip()
    if candidate.lower() in {"", "global", "astrbot", "auto", "follow_global"}:
        return "global"
    return _normalize_timezone_name(candidate)


def _resolve_timezone_setting(
    timezone_name: Any,
    *,
    global_timezone: Any = "",
    system_timezone: Any = "",
) -> str:
    configured = _normalize_timezone_setting(timezone_name)
    if configured != "global":
        return configured
    for candidate in (global_timezone, system_timezone):
        normalized = _normalize_timezone_name(candidate, "")
        if normalized:
            return normalized
    return "Asia/Shanghai"


def _set_today_key_timezone(timezone_name: Any) -> None:
    global _today_key_timezone
    _today_key_timezone = _normalize_timezone_name(timezone_name)


def _today_key() -> str:
    if _today_key_timezone:
        try:
            return datetime.now(zoneinfo.ZoneInfo(_today_key_timezone)).strftime("%Y-%m-%d")
        except Exception:
            pass
    return datetime.now().strftime("%Y-%m-%d")


def _date_key(value: date) -> str:
    return value.strftime("%Y-%m-%d")


def _safe_int(value: Any, default: int, minimum: int = 0, maximum: int | None = None) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    parsed = max(minimum, parsed)
    if maximum is not None:
        parsed = min(maximum, parsed)
    return parsed


def _safe_float(
    value: Any,
    default: float,
    minimum: float = 0.0,
    maximum: float | None = None,
) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    parsed = max(minimum, parsed)
    if maximum is not None:
        parsed = min(maximum, parsed)
    return parsed


def _single_line(text: Any, limit: int = 80) -> str:
    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    return normalized[:limit]


def normalize_bot_relationship_cards(value: Any, *, limit: int = 16) -> list[str]:
    """Normalize relationship cards shared by config, page API, and prompt injection."""
    if isinstance(value, (list, tuple)):
        raw_lines = [str(item or "") for item in value]
    else:
        raw_lines = str(value or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")

    cards: list[str] = []
    seen_names: set[str] = set()
    max_cards = max(0, int(limit))
    if max_cards == 0:
        return cards
    for raw_line in raw_lines:
        parts = [
            _single_line(part, 200)
            for part in re.split(r"\s*(?:\|\||｜｜)\s*", raw_line, maxsplit=2)
        ]
        name = parts[0] if parts else ""
        if not name:
            continue
        name_key = name.casefold()
        if name_key in seen_names:
            continue
        seen_names.add(name_key)
        relation = parts[1] if len(parts) > 1 else ""
        appearance = parts[2] if len(parts) > 2 else ""
        cards.append(f"{name} || {relation} || {appearance}")
        if len(cards) >= max_cards:
            break
    return cards


def _photo_group_request_matches(text: Any) -> bool:
    """Return whether a photo request explicitly asks multiple people to share the frame."""
    source = _single_line(text, 1600)
    if not source:
        return False
    compact = re.sub(r"\s+", "", source).lower()
    compact = re.sub(
        r"(?:不要|不想要?|不需要|无需|避免|禁止|拒绝|别|不是|并非)(?:生成|画|拍|做|来)?"
        r"(?:任何)?(?:合影|合照|双人照|双人自拍|多人照|多人自拍|大合照|一起入镜|一同入镜|"
        r"两人同框|二人同框|多人同框|同框照|情侣照|情侣写真)",
        "",
        compact,
    )
    compact = re.sub(
        r"(?:不要|不想要?|不需要|无需|避免|禁止|拒绝|别|不是|并非)"
        r"(?:(?:我(?:们|俩)?|咱(?:们|俩)?)(?:和|跟|与)[^，。！？]{1,18}|两个人|两位|三个人|大家|朋友们|一家人)"
        r".{0,18}(?:一起)?(?:拍照|自拍|拍张照片|拍一张照片|照片|相片|写真|同框|入镜)",
        "",
        compact,
    )
    english_source = re.sub(
        r"\b(?:no|not|avoid|without|do\s+not\s+(?:make|generate|draw|show)?)\s+"
        r"(?:a\s+)?(?:group|couple|two[-\s]+person|multi[-\s]+person)\s+"
        r"(?:photo|portrait|selfie)\b",
        " ",
        source,
        flags=re.I,
    )
    if any(
        marker in compact
        for marker in (
            "合影",
            "合照",
            "双人照",
            "双人自拍",
            "多人照",
            "多人自拍",
            "大合照",
            "一起入镜",
            "一同入镜",
            "两人同框",
            "二人同框",
            "多人同框",
            "同框照",
            "情侣照",
            "情侣写真",
        )
    ):
        return True
    if re.search(
        r"(?:(?:我(?:们|俩)?|咱(?:们|俩)?)(?:和|跟|与)[^，。！？]{1,18}|两个人|两位|三个人|大家|朋友们|一家人)"
        r".{0,18}(?:一起)?(?:拍照|自拍|拍张照片|拍一张照片|照片|相片|写真|同框|入镜)",
        compact,
    ):
        return True
    return bool(
        re.search(
            r"\b(?:group\s+(?:photo|portrait|selfie)|two[-\s]+person\s+(?:photo|portrait|selfie)|"
            r"couple\s+(?:photo|portrait|selfie)|(?:photo|portrait|selfie)\s+of\s+us\s+together|"
            r"(?:both|two\s+people)\s+in\s+(?:the\s+)?(?:same\s+)?(?:frame|photo)|"
            r"(?:photo|portrait|selfie)\s+(?:with|of)\s+(?:my\s+)?(?:friend|partner|family)|"
            r"(?:me|us)\s+(?:and|with)\s+[^,.!?]{1,40}\s+(?:photo|portrait|selfie))\b",
            english_source,
            flags=re.I,
        )
    )


def _path_text(value: Any, limit: int = 1000) -> str:
    """Normalize a configured path without changing legal internal whitespace."""
    normalized = str(value or "").replace("\r", "").replace("\n", "").strip()
    if len(normalized) >= 2 and normalized[0] == normalized[-1] and normalized[0] in {'"', "'"}:
        normalized = normalized[1:-1].strip()
    return normalized[:limit] if limit > 0 else normalized


def _normalize_photo_subject_owner(value: Any) -> str:
    normalized = _single_line(value, 40).strip().lower().replace("-", "_")
    if normalized in {"bot", "self", "persona", "character", "当前人格", "机器人", "角色本人"}:
        return "bot"
    if normalized in {"third_party", "thirdparty", "other_person", "第三方", "第三方人物", "其他人物"}:
        return "third_party"
    if normalized in {"scene", "object", "animal", "environment", "画面", "物体", "动物", "环境"}:
        return "scene"
    if normalized in {"unknown", "unclear", "ambiguous", "未知", "不明", "无法判断"}:
        return "unknown"
    return ""


def _photo_subject_owner_prompt_label(value: Any) -> str:
    owner = _normalize_photo_subject_owner(value) or "unknown"
    return {
        "bot": "Bot/当前人格（图片描述中的“我/她/角色本人”）",
        "third_party": "画面中的第三方人物（不是 Bot，也不是用户）",
        "scene": "画面中的物体、动物或环境主体（不是用户）",
        "unknown": "画面中的实际主体（归属不明，但不能据此归到用户）",
    }[owner]


def _group_link_message_context(text: Any, limit: int = 260) -> tuple[str, bool]:
    """Return non-link user text and whether the message contains a link/share payload."""
    raw = str(text or "")[:4000].replace("\u200b", "").replace("\ufeff", "")
    has_link_payload = bool(
        _GROUP_MESSAGE_URL_PATTERN.search(raw)
        or _GROUP_SHARE_MARKER_PATTERN.search(raw)
        or _GROUP_SHARE_BOILERPLATE_PATTERN.search(raw)
    )
    if not has_link_payload:
        return _single_line(raw, limit), False
    remainder = _GROUP_MESSAGE_URL_PATTERN.sub(" ", raw)
    remainder = _GROUP_SHARE_MARKER_PATTERN.sub(" ", remainder)
    remainder = _GROUP_SHARE_BOILERPLATE_PATTERN.sub(" ", remainder)
    remainder = re.sub(r"(?i)(?:网页)?(?:链接|网址|link)\s*[:：]", " ", remainder)
    remainder = re.sub(r"\s+", " ", remainder).strip(" \t\r\n,，。;；|｜-—")
    return _single_line(remainder, limit), True


_SECRET_FIELD_PATTERN = re.compile(
    r"(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|authorization|auth[_ -]?token|secret|password|passwd|cookie|密钥|令牌|口令)",
    flags=re.IGNORECASE,
)


def _runtime_secret_values(owner: Any) -> list[str]:
    """Collect configured credentials without exposing them to logs or prompts."""
    values: set[str] = set()

    def add(value: Any) -> None:
        if isinstance(value, (str, bytes)):
            text = value.decode("utf-8", errors="ignore") if isinstance(value, bytes) else value
            text = text.strip()
            if len(text) >= 6:
                values.add(text)

    for attr in (
        "external_image_api_key",
        "backup_external_image_api_key",
        "balance_api_key",
        "weather_api_key",
        "weather_token",
        "qweather_token",
        "weather_alert_token",
        "weather_alert_jwt",
        "weather_alert_api_key",
        "web_exploration_api_key",
    ):
        add(getattr(owner, attr, ""))
    endpoints = getattr(owner, "external_image_api_endpoints", None)
    if isinstance(endpoints, list):
        for endpoint in endpoints[:24]:
            if not isinstance(endpoint, dict):
                continue
            for key, value in endpoint.items():
                if _SECRET_FIELD_PATTERN.search(str(key or "")):
                    add(value)

    def walk(value: Any, depth: int = 0) -> None:
        if depth > 5:
            return
        try:
            items = value.items() if hasattr(value, "items") else None
        except Exception:
            items = None
        if items is not None:
            try:
                for key, child in list(items)[:500]:
                    if _SECRET_FIELD_PATTERN.search(str(key or "")):
                        add(child)
                    elif isinstance(child, (dict, list, tuple)) or hasattr(child, "items"):
                        walk(child, depth + 1)
            except Exception:
                return
        elif isinstance(value, (list, tuple)):
            for child in value[:100]:
                if isinstance(child, (dict, list, tuple)) or hasattr(child, "items"):
                    walk(child, depth + 1)

    walk(getattr(owner, "config", None))
    return sorted(values, key=len, reverse=True)


def _redact_outbound_secrets(text: Any, owner: Any = None) -> str:
    """Redact credentials from chat-bound text while preserving the useful reply."""
    cleaned = str(text or "")
    if not cleaned:
        return ""
    for secret in _runtime_secret_values(owner) if owner is not None else []:
        cleaned = cleaned.replace(secret, "[密钥已隐藏]")
    patterns = (
        (r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}", "Bearer [密钥已隐藏]"),
        (r"(?i)\b(?:sk|pk|rk|ghp|github_pat|xox[baprs])[-_][A-Za-z0-9_-]{10,}", "[密钥已隐藏]"),
        (r"\bAIza[A-Za-z0-9_-]{20,}\b", "[密钥已隐藏]"),
        (r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b", "[密钥已隐藏]"),
        (r"(?i)([?&](?:api[_-]?key|access_token|refresh_token|token|secret|key)=)[^&#\s]+", r"\1[密钥已隐藏]"),
        (
            r"(?i)((?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|authorization|auth[_ -]?token|secret|password|passwd|密钥|令牌|口令)\s*(?::|：|=)\s*[\"']?)[^\s,，;；\"']{6,}",
            r"\1[密钥已隐藏]",
        ),
    )
    for pattern, replacement in patterns:
        cleaned = re.sub(pattern, replacement, cleaned)
    return cleaned


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


def _strip_internal_message_blocks(text: Any) -> str:
    normalized = str(text or "")
    normalized = _strip_group_member_safety_markers(normalized)
    normalized = re.sub(r"\[\[TTSBLOCK:[^\]]*\]\]", "", normalized)
    normalized = re.sub(r"\[\[PCTTS:[^\]]*\]\]", "", normalized)
    normalized = re.sub(r"<timer\b[^>]*>.*?</timer>", "", normalized, flags=re.IGNORECASE | re.DOTALL)
    normalized = re.sub(r"<tts\b[^>]*>.*?</tts>", "", normalized, flags=re.IGNORECASE | re.DOTALL)
    normalized = re.sub(r"<think\b[^>]*>.*?</think>", "", normalized, flags=re.IGNORECASE | re.DOTALL)
    normalized = re.sub(r"<reasoning\b[^>]*>.*?</reasoning>", "", normalized, flags=re.IGNORECASE | re.DOTALL)
    normalized = _strip_nonstandard_chat_control_tags(normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


_CHAT_SELF_CLOSING_TAG_ALLOWLIST = (
    r"br|image|img|video|audio|record|file|at|face|emoji|reply|tts|pc[_-]?tts|timer"
)

_NONSTANDARD_SELF_CLOSING_TAG_PATTERN = re.compile(
    rf"<\s*(?!(?:{_CHAT_SELF_CLOSING_TAG_ALLOWLIST})\b)"
    r"[A-Za-z][A-Za-z0-9_-]{0,31}(?:\s+[^<>\r\n]{0,160})?/\s*>",
    re.IGNORECASE,
)
_ESCAPED_NONSTANDARD_SELF_CLOSING_TAG_PATTERN = re.compile(
    rf"&lt;\s*(?!(?:{_CHAT_SELF_CLOSING_TAG_ALLOWLIST})\b)"
    r"[A-Za-z][A-Za-z0-9_-]{0,31}(?:\s+[^&\r\n]{0,160})?/\s*&gt;",
    re.IGNORECASE,
)
_MARKDOWN_CODE_SPAN_PATTERN = re.compile(
    r"(```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\r\n]+`)",
    re.MULTILINE,
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


def _strip_nonstandard_chat_control_tags(text: Any) -> str:
    """Remove leaked pseudo-control tags such as <bubble/> without touching media blocks."""
    normalized = str(text or "")
    if not normalized:
        return ""
    normalized = _NONSTANDARD_SELF_CLOSING_TAG_PATTERN.sub("", normalized)
    normalized = _ESCAPED_NONSTANDARD_SELF_CLOSING_TAG_PATTERN.sub("", normalized)
    normalized = re.sub(r"\s+([，,。！？!?；;：:、~～…])", r"\1", normalized)
    normalized = re.sub(r"([（(【\[])\s+", r"\1", normalized)
    normalized = re.sub(r"\s+([）)】\]])", r"\1", normalized)
    normalized = re.sub(r"[ \t]{2,}", " ", normalized)
    return normalized


def _strip_persisted_chat_control_tags(text: Any) -> str:
    """Clean leaked controls while preserving literal tags shown as Markdown code."""
    normalized = str(text or "")
    if not normalized:
        return ""
    parts = _MARKDOWN_CODE_SPAN_PATTERN.split(normalized)
    for index in range(0, len(parts), 2):
        parts[index] = _strip_nonstandard_chat_control_tags(parts[index])
    return "".join(parts)


def _strip_outbound_control_blocks(
    text: Any,
    *,
    preserve_private_tts_tokens: bool = False,
    allowed_private_tts_tokens: set[str] | None = None,
) -> str:
    normalized = str(text or "")
    normalized = _strip_group_member_safety_markers(normalized)
    normalized = re.sub(r"\[\[TTSBLOCK:[^\]]*\]\]", "", normalized)
    if preserve_private_tts_tokens and allowed_private_tts_tokens:
        allowed = {str(token) for token in allowed_private_tts_tokens if str(token)}

        def _private_tts_repl(match: re.Match[str]) -> str:
            token = str(match.group(1) or "")
            return match.group(0) if token in allowed else ""

        normalized = re.sub(r"\[\[PCTTS:([^\]]*)\]\]", _private_tts_repl, normalized)
    elif not preserve_private_tts_tokens:
        normalized = re.sub(r"\[\[PCTTS:[^\]]*\]\]", "", normalized)
    normalized = re.sub(r"<timer\b[^>]*>.*?</timer>", "", normalized, flags=re.IGNORECASE | re.DOTALL)
    normalized = re.sub(r"<think\b[^>]*>.*?</think>", "", normalized, flags=re.IGNORECASE | re.DOTALL)
    normalized = re.sub(r"<reasoning\b[^>]*>.*?</reasoning>", "", normalized, flags=re.IGNORECASE | re.DOTALL)
    normalized = _strip_nonstandard_chat_control_tags(normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized).strip()
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


def _flat_get(config: Any, key: str, default: Any = None) -> Any:
    """Read both flat config keys and keys nested under schema object/items groups."""
    if isinstance(config, dict):
        # Prefer schema-group values over top-level legacy compatibility keys.
        # AstrBot may add invisible legacy flat defaults before plugin init; if
        # those are read first they would shadow the user's real grouped config.
        for value in config.values():
            if isinstance(value, dict):
                found = _flat_get(value, key, _MISSING)
                if found is not _MISSING:
                    return found
        if key in config:
            return config[key]
    for attr in ("data", "config"):
        target = getattr(config, attr, None)
        if isinstance(target, dict):
            found = _flat_get(target, key, _MISSING)
            if found is not _MISSING:
                return found
    getter = getattr(config, "get", None)
    if callable(getter):
        try:
            value = getter(key, _MISSING)
        except Exception:
            value = _MISSING
        if value is not _MISSING:
            return value
    return default


def _set_into_config(config: Any, key: str, value: Any, *, allow_flat_fallback: bool = True) -> bool:
    """Write a config value back to its existing flat or nested location."""

    def convert(existing: Any, new_value: Any) -> Any:
        if isinstance(existing, bool) and isinstance(new_value, str):
            text = new_value.strip().lower()
            if text in {"true", "1", "yes", "y", "on", "enable", "enabled", "启用", "开启", "开", "是"}:
                return True
            if text in {"false", "0", "no", "n", "off", "disable", "disabled", "停用", "关闭", "关", "否", ""}:
                return False
        if isinstance(existing, int) and not isinstance(existing, bool) and isinstance(new_value, str):
            try:
                return int(new_value)
            except (TypeError, ValueError):
                return new_value
        if isinstance(existing, float) and isinstance(new_value, str):
            try:
                return float(new_value)
            except (TypeError, ValueError):
                return new_value
        return new_value

    def find_and_set(target: dict[str, Any]) -> bool:
        # Match _flat_get(): schema-grouped values are searched before legacy
        # flat compatibility keys.  When a key exists in more than one place,
        # keep every location in sync so later readers cannot see a stale copy.
        changed = False
        for child in target.values():
            if isinstance(child, dict):
                changed = find_and_set(child) or changed
        if key in target:
            target[key] = convert(target.get(key), value)
            changed = True
        return changed

    if isinstance(config, dict) and find_and_set(config):
        return True
    for attr in ("data", "config"):
        target = getattr(config, attr, None)
        if isinstance(target, dict) and find_and_set(target):
            return True
    if not allow_flat_fallback:
        return False
    try:
        config[key] = value
        return True
    except Exception:
        pass
    setter = getattr(config, "set", None)
    if callable(setter):
        try:
            setter(key, value)
            return True
        except Exception:
            pass
    return False
