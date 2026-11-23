"""Group mention positions from native components and QQ Official source text."""

from collections.abc import Mapping
import re
from types import SimpleNamespace

from companion_v2.conversation.group_timing import SELF_MENTION
from .group_quotes import mention_text
from .voice_stt import (
    ASTRBOT_STT_TRANSCRIPTS_EXTRA,
    STT_EXTRA_KEYS,
    VOICE_TRANSCRIPT_PREFIX,
    read_stt_extra,
    trusted_stt_extra_transcript,
    trusted_stt_extra_with_source,
    trusted_stt_transcript,
)


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
    # The host adapter may collapse the spaces around a removed At into a
    # single blank; compare after whitespace normalization so a stale raw body
    # is still rejected without losing a valid mention-only message.
    normalized = lambda value: " ".join(parse_face(value).split())
    if normalized(plain) not in (normalized(projected), normalized(content.strip())):
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
    if _get(_get(event, "platform_meta"), "name") not in OFFICIAL_PLATFORMS:
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
    if not inserted and restored:
        result.append(SimpleNamespace(type="Plain", text=restored))
    return result


def current_mention_text(target, event, account_id):
    if target is not None and str(target).strip() in bot_mention_ids(event, account_id):
        return SELF_MENTION
    return mention_text(target, account_id)


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
