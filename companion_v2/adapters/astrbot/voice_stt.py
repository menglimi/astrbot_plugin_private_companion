"""Shared upstream voice STT extra & transcript helpers for private, group, and core reply paths."""

from __future__ import annotations

from collections.abc import Mapping


VOICE_TRANSCRIPT_PREFIX = "[语音转写] "
ASTRBOT_STT_TRANSCRIPTS_EXTRA = "astrbot_stt_transcripts"
STT_EXTRA_KEYS = {
    "transcript": ("_hear_voice_transcript", "_gemini_stt_transcript"),
    "raw_text": ("_hear_voice_raw_text", "_gemini_stt_raw_text"),
    "should_reply": ("_hear_voice_should_reply", "_gemini_stt_should_reply"),
    "cache_only": ("_hear_voice_cache_only", "_gemini_stt_cache_only"),
    "non_hijacking": ("_hear_voice_non_hijacking", "_gemini_stt_non_hijacking"),
    "hijacked": ("_hear_voice_hijacked", "_gemini_stt_hijacked"),
    "notify_only_sent": ("_hear_voice_notify_only_sent", "_gemini_stt_notify_only_sent"),
    "done": ("_hear_voice_done", "_gemini_stt_done"),
    "status": ("_hear_voice_status", "_gemini_stt_status"),
}


def _get(value, name, default=None):
    return value.get(name, default) if isinstance(value, Mapping) else getattr(value, name, default)


def _component_kind(part):
    kind = _get(part, "type")
    return getattr(kind, "value", kind)


def read_stt_extra(event, field: str, default=None):
    """Read upstream ``astrbot_plugin_hear_voice``（兼容 ``_gemini_stt_*``）extra, preferring ``_hear_voice_*``."""
    if event is None:
        return default
    getter = getattr(event, "get_extra", None)
    if not callable(getter):
        return default
    for key in STT_EXTRA_KEYS[field]:
        val = getter(key)
        if val is not None:
            return val
    return default


def has_external_stt_extra(event) -> bool:
    """Return True when upstream ``astrbot_plugin_hear_voice``（兼容 ``_gemini_stt_*``）wrote any STT extra on ``event``."""
    if event is None:
        return False
    getter = getattr(event, "get_extra", None)
    if not callable(getter):
        return False
    if getter(ASTRBOT_STT_TRANSCRIPTS_EXTRA) is not None:
        return True
    return any(
        getter(key) is not None
        for keys in STT_EXTRA_KEYS.values()
        for key in keys
    )


def _clean_stt_text(value) -> str:
    if not isinstance(value, str):
        return ""
    return value.replace("\x00", "").strip()[:8192]


def _structured_stt_transcript(entries, *, rich: bool = False) -> str:
    if not isinstance(entries, (list, tuple)):
        return ""
    field_names = ("rich_text", "raw_text") if rich else ("transcript",)
    pieces = []
    for item in entries:
        if not isinstance(item, Mapping):
            continue
        for name in field_names:
            cleaned = _clean_stt_text(item.get(name))
            if cleaned:
                pieces.append(cleaned)
                break
    if not pieces:
        return ""
    sep = "\n\n" if rich else "\n"
    return sep.join(pieces).strip()[:8192]


def _detect_stt_source(event) -> str:
    if event is None:
        return "hear_voice"
    getter = getattr(event, "get_extra", None)
    if not callable(getter):
        return "hear_voice"
    if any(getter(keys[0]) is not None for keys in STT_EXTRA_KEYS.values()):
        return "hear_voice"
    if any(getter(keys[1]) is not None for keys in STT_EXTRA_KEYS.values()):
        return "gemini_stt"
    return "hear_voice"


def trusted_stt_extra_with_source(event) -> tuple[str, str]:
    """Read upstream ``astrbot_plugin_hear_voice``（兼容 ``_gemini_stt_*`` / ``astrbot_stt_transcripts``）transcript and source strictly."""
    if event is None:
        return "", ""
    getter = getattr(event, "get_extra", None)
    if not callable(getter):
        return "", ""
    for key, source in (
        ("_hear_voice_transcript", "hear_voice"),
        ("_gemini_stt_transcript", "gemini_stt"),
    ):
        cleaned = _clean_stt_text(getter(key))
        if cleaned:
            return cleaned, source
    structured_plain = _structured_stt_transcript(getter(ASTRBOT_STT_TRANSCRIPTS_EXTRA), rich=False)
    if structured_plain:
        return structured_plain, _detect_stt_source(event)
    for key, source in (
        ("_hear_voice_raw_text", "hear_voice"),
        ("_gemini_stt_raw_text", "gemini_stt"),
    ):
        cleaned = _clean_stt_text(getter(key))
        if cleaned:
            return cleaned, source
    structured_rich = _structured_stt_transcript(getter(ASTRBOT_STT_TRANSCRIPTS_EXTRA), rich=True)
    if structured_rich:
        return structured_rich, _detect_stt_source(event)
    return "", ""


def trusted_stt_extra_transcript(event) -> str:
    """Read upstream ``astrbot_plugin_hear_voice``（兼容 ``_gemini_stt_*`` / ``astrbot_stt_transcripts``）extra transcript strictly."""
    text, _ = trusted_stt_extra_with_source(event)
    return text


def trusted_stt_transcript(event) -> str:
    """Read upstream ``astrbot_plugin_hear_voice``（兼容 ``_gemini_stt_*``）extra transcript safely; only trust ``[语音转写]`` Plain if paired with native Record."""
    text = trusted_stt_extra_transcript(event)
    if text:
        return text
    parts = getattr(getattr(event, "message_obj", None), "message", ())
    if isinstance(parts, (list, tuple)):
        has_record = any(_component_kind(part) == "Record" for part in parts)
        if has_record:
            for part in parts:
                if _component_kind(part) == "Plain":
                    raw = _get(part, "text", "")
                    if isinstance(raw, str) and raw.startswith(VOICE_TRANSCRIPT_PREFIX):
                        body = _clean_stt_text(raw[len(VOICE_TRANSCRIPT_PREFIX):])
                        if body:
                            return body
    return ""
