"""Group mention positions from native components and QQ Official source text."""

from collections.abc import Mapping
import re
from types import SimpleNamespace

from companion_v2.conversation.group_timing import SELF_MENTION
from .group_quotes import mention_text


QQ_MENTION = re.compile(r'<@!?(?P<short>[^<>\s]+)>|<qqbot-at-user\s+id="(?P<xml>[^"]+)"\s*/>')
OFFICIAL_PLATFORMS = {"qq_official", "qq_official_webhook"}


def _get(value, name, default=None):
    return value.get(name, default) if isinstance(value, Mapping) else getattr(value, name, default)


def component_kind(part):
    kind = _get(part, "type")
    return getattr(kind, "value", kind)


def bot_mention_ids(event, account_id):
    identifiers = {str(account_id)}
    if _get(_get(event, "platform_meta"), "name") in OFFICIAL_PLATFORMS:
        raw = getattr(event.message_obj, "raw_message", None)
        mentions = _get(raw, "mentions") or _get(_get(raw, "raw_data"), "mentions", ())
        identifiers.update(str(_get(item, "id")) for item in mentions
                           if _get(item, "is_you") is True and _get(item, "id") is not None)
        identifiers.update((str(getattr(event.message_obj, "self_id", "")), "qq_official"))
    return identifiers


def official_mention_targets(event):
    """Other QQ mentions may remain in Plain, including debounced messages."""
    if _get(_get(event, "platform_meta"), "name") not in OFFICIAL_PLATFORMS:
        return ()
    raw = getattr(event.message_obj, "raw_message", None)
    mentions = _get(raw, "mentions") or _get(_get(raw, "raw_data"), "mentions", ())
    targets = tuple(str(_get(item, "id")) for item in mentions if _get(item, "id") is not None)
    return targets + tuple(match.group("short") or match.group("xml")
                 for part in event.message_obj.message if component_kind(part) == "Plain"
                 for match in QQ_MENTION.finditer(part.text))


def _official_original(event, identifiers):
    raw = getattr(event.message_obj, "raw_message", None)
    content = _get(raw, "content")
    if not isinstance(content, str):
        content = _get(_get(raw, "raw_data"), "content")
    if not isinstance(content, str):
        return None
    tokens = [match.group() for match in QQ_MENTION.finditer(content)
              if (match.group("short") or match.group("xml")) in identifiers]
    if not tokens:
        return None
    # Match the adapter's projection before using raw content: debounce and
    # other input transforms can leave raw_message pointing at an older body.
    pattern = "(?:" + "|".join(re.escape(token) for token in set(tokens)) + ")"
    projected = re.sub(r"(?:[ \t]*" + pattern + r"[ \t]*)+", " ", content).strip()
    from astrbot.core.platform.sources.qqofficial.qqofficial_platform_adapter import QQOfficialPlatformAdapter
    parse_face = getattr(QQOfficialPlatformAdapter, "_parse_face_message", lambda text: text)
    plain = "".join(part.text for part in event.message_obj.message if component_kind(part) == "Plain")
    if plain.strip() not in (parse_face(projected), parse_face(content.strip())):
        return None
    restored = QQ_MENTION.sub(lambda match: SELF_MENTION
        if (match.group("short") or match.group("xml")) in identifiers else match.group(), content)
    return parse_face(restored).strip()


def group_message_parts(event, account_id, *, native_parts=None):
    """Keep original order; fall back to native components on older hosts."""
    message = event.message_obj
    raw = getattr(message, "raw_message", None)
    segments = _get(raw, "message")
    if native_parts is None and isinstance(segments, list):
        return segments
    parts = tuple(message.message if native_parts is None else native_parts)
    if event.platform_meta.name not in OFFICIAL_PLATFORMS:
        return parts
    identifiers = bot_mention_ids(event, account_id)
    restored = _official_original(event, identifiers)
    if restored is None:
        return parts
    result, inserted = [], False
    for part in parts:
        kind = component_kind(part)
        if kind == "At" and str(part.qq) in identifiers:
            # Keep component indices stable for the full media intake path.
            result.append(SimpleNamespace(type="Plain", text=""))
            continue
        if kind == "Plain":
            if not inserted:
                result.append(SimpleNamespace(type="Plain", text=restored))
                inserted = True
            else:
                result.append(SimpleNamespace(type="Plain", text=""))
        else:
            result.append(part)
    return result


def current_mention_text(target, event, account_id):
    if str(target) in bot_mention_ids(event, account_id):
        return SELF_MENTION
    return mention_text(target, account_id)


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
        has_record = any(component_kind(part) == "Record" for part in parts)
        if has_record:
            for part in parts:
                if component_kind(part) == "Plain":
                    raw = _get(part, "text", "")
                    if isinstance(raw, str) and raw.startswith(VOICE_TRANSCRIPT_PREFIX):
                        body = _clean_stt_text(raw[len(VOICE_TRANSCRIPT_PREFIX):])
                        if body:
                            return body
    return ""


def group_message_text(event, account_id):
    """Text and mentions for the model, folding trusted upstream voice transcripts cleanly."""
    stt_text = trusted_stt_transcript(event)
    text = []
    stt_used = False
    for part in group_message_parts(event, account_id):
        kind = component_kind(part)
        data = part.get("data", {}) if isinstance(part, Mapping) else part
        if kind in ("Plain", "text"):
            piece = _get(data, "text", "")
            if stt_text and isinstance(piece, str) and piece.strip() == "[语音消息]" and not stt_used:
                text.append(VOICE_TRANSCRIPT_PREFIX + stt_text)
                stt_used = True
            else:
                if stt_text and isinstance(piece, str) and stt_text in piece:
                    stt_used = True
                text.append(piece)
        elif kind in ("At", "at"):
            text.append(current_mention_text(_get(data, "qq"), event, account_id))
        elif kind in ("Record", "record") and stt_text and not stt_used:
            text.append(VOICE_TRANSCRIPT_PREFIX + stt_text)
            stt_used = True
    return "".join(text)
