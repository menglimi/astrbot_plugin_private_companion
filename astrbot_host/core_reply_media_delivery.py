"""Keep QQ voice and optional reaction attachments in supported messages."""

from astrbot.core.message.components import (
    At,
    AtAll,
    BaseMessageComponent,
    ComponentType,
    File,
    Image,
    Plain,
    Record,
    Reply,
)
from astrbot.core.message.message_event_result import MessageChain

_DECORATION = (Reply, At, AtAll)


class _OneBotEmojiImage(BaseMessageComponent):
    type: ComponentType = ComponentType.Image
    file: str

    def toDict(self):
        return {"type": "image", "data": {"file": self.file, "sub_type": 1}}


def _derive(message, chain):
    derive = getattr(message, "derive", None)
    return derive(chain) if callable(derive) else MessageChain(chain=chain)


def _has_text(chain):
    """True when the payload carries visible text."""
    return any(isinstance(part, Plain) and part.text.strip() for part in chain)


def _renders(chain):
    """True when the payload still shows something once its quote/@ decoration is gone."""
    return _has_text(chain) or any(not isinstance(part, (Plain, *_DECORATION)) for part in chain)


def native_reply_messages(event, message):
    chain = getattr(message, "chain", None)
    if not isinstance(chain, (list, tuple)):
        return (message,)
    getter = getattr(event, "get_platform_name", None)
    qq = callable(getter) and getter() == "aiocqhttp"
    groups, current = [], []
    for part in chain:
        reaction = getattr(part, "_companion_v2_reaction_attachment", None)
        separate = (qq and isinstance(part, Record)) or (
            isinstance(reaction, dict) and reaction.get("delivery_mode") != "same_message")
        if separate:
            if current:
                groups.append(current)
                current = []
            groups.append([part])
        else:
            current.append(part)
    if current:
        groups.append(current)
    # A quote or @ only decorates a body; it is not a message of its own. Sent
    # alone it became an empty OneBot message holding just a reply segment, which
    # QQ shows as 「暂不支持的消息类型」. Attach it to the payload that carries the
    # visible text -- the voice bubble only speaks it -- and drop payloads that
    # would render nothing at all.
    headers, bodies = [], []
    for group in groups:
        if _renders(group):
            bodies.append(group)
        else:
            headers.extend(part for part in group if isinstance(part, _DECORATION))
    if not bodies:
        return ()
    target = next((index for index, group in enumerate(bodies) if _has_text(group)), 0)
    bodies[target] = headers + bodies[target]
    return tuple(_derive(message, group) for group in bodies)


async def _telegram_wire_message(event, message):
    """Convert TTS voice to a Telegram ``sendVoice`` compatible payload.

    Telegram rejects WAV voice notes, which is the common AstrBot TTS output.
    A voice part that cannot be converted is dropped; the text remains.
    """
    from companion_v2.adapters.astrbot.platform_sender import TelegramVoiceRecord
    from .telegram_media import telegram_voice_document, telegram_voice_path

    chain = getattr(message, "chain", None)
    if not isinstance(chain, (list, tuple)):
        return message
    track = getattr(event, "track_temporary_local_file", None)
    output, changed = [], False
    for part in chain:
        if not isinstance(part, Record):
            output.append(part)
            continue
        converted = await telegram_voice_path(getattr(part, "file", None))
        if converted is None:
            # Keep the audio as a document rather than losing the voice turn.
            fallback = telegram_voice_document(getattr(part, "file", None))
            if fallback is not None:
                path, name = fallback
                if callable(track):
                    track(path)
                output.append(File(name=name, file=path))
            changed = True
            continue
        if callable(track):
            track(converted)
        replacement = TelegramVoiceRecord(file=converted, path=converted,
                                          text=getattr(part, "text", None))
        spoken = getattr(part, "_companion_v2_spoken_text", None)
        if spoken is not None:
            object.__setattr__(replacement, "_companion_v2_spoken_text", spoken)
        output.append(replacement)
        changed = True
    return _derive(message, output) if changed else message


async def native_wire_message(event, message):
    """The SDK's Image conversion drops sub_type; keep it for owned QQ stickers."""
    getter = getattr(event, "get_platform_name", None)
    platform = getter() if callable(getter) else ""
    if platform == "telegram":
        return await _telegram_wire_message(event, message)
    if platform != "aiocqhttp":
        return message
    chain = getattr(message, "chain", None)
    if not isinstance(chain, (list, tuple)):
        return message
    output = []
    changed = False
    for part in chain:
        reaction = getattr(part, "_companion_v2_reaction_attachment", None)
        if isinstance(part, Image) and isinstance(reaction, dict) and reaction.get("image_format") == "qq_emoji":
            output.append(_OneBotEmojiImage(file="base64://" + await part.convert_to_base64()))
            changed = True
        else:
            output.append(part)
    return _derive(message, output) if changed else message
