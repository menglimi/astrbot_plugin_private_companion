# -*- coding: utf-8 -*-

from __future__ import annotations

import re
import time
import unicodedata
import zoneinfo
from datetime import date, datetime
from typing import Any

_today_key_timezone = ""


def _now_ts() -> float:
    return time.time()


def _set_today_key_timezone(timezone_name: Any) -> None:
    global _today_key_timezone
    _today_key_timezone = str(timezone_name or "").strip()


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


def _safe_float(value: Any, default: float, minimum: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, parsed)


def _single_line(text: Any, limit: int = 80) -> str:
    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    return normalized[:limit]


_OPTIONAL_MODEL_DEPENDENCIES = {
    "torch",
    "torchvision",
    "torchaudio",
    "sentence_transformers",
    "transformers",
}


def _missing_optional_model_dependency(exc: BaseException) -> str:
    if isinstance(exc, ModuleNotFoundError):
        name = str(getattr(exc, "name", "") or "").strip()
        if name in _OPTIONAL_MODEL_DEPENDENCIES:
            return name
    text = str(exc or "")
    match = re.search(r"No module named ['\"]([^'\"]+)['\"]", text)
    if not match:
        return ""
    name = match.group(1).strip()
    return name if name in _OPTIONAL_MODEL_DEPENDENCIES else ""


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
    normalized = re.sub(r"\[\[TTSBLOCK:[^\]]*\]\]", "", normalized)
    normalized = re.sub(r"\[\[PCTTS:[^\]]*\]\]", "", normalized)
    normalized = re.sub(r"<timer\b[^>]*>.*?</timer>", "", normalized, flags=re.IGNORECASE | re.DOTALL)
    normalized = re.sub(r"<tts\b[^>]*>.*?</tts>", "", normalized, flags=re.IGNORECASE | re.DOTALL)
    normalized = re.sub(r"<think\b[^>]*>.*?</think>", "", normalized, flags=re.IGNORECASE | re.DOTALL)
    normalized = re.sub(r"<reasoning\b[^>]*>.*?</reasoning>", "", normalized, flags=re.IGNORECASE | re.DOTALL)
    normalized = _strip_nonstandard_chat_control_tags(normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


_NONSTANDARD_SELF_CLOSING_TAG_PATTERN = re.compile(
    r"<\s*(?!(?:br|image|video|audio|tts|pc[_-]?tts|timer)\b)"
    r"[A-Za-z][A-Za-z0-9_-]{0,31}(?:\s+[^<>\r\n]{0,160})?/\s*>",
    re.IGNORECASE,
)
_ESCAPED_NONSTANDARD_SELF_CLOSING_TAG_PATTERN = re.compile(
    r"&lt;\s*(?!(?:br|image|video|audio|tts|pc[_-]?tts|timer)\b)"
    r"[A-Za-z][A-Za-z0-9_-]{0,31}(?:\s+[^&\r\n]{0,160})?/\s*&gt;",
    re.IGNORECASE,
)


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


def _strip_outbound_control_blocks(
    text: Any,
    *,
    preserve_private_tts_tokens: bool = False,
    allowed_private_tts_tokens: set[str] | None = None,
) -> str:
    normalized = str(text or "")
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
