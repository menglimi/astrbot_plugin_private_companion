"""Keep QQ voice and optional reaction attachments in supported messages."""

import re

from astrbot.core.message.components import (
    At,
    AtAll,
    BaseMessageComponent,
    ComponentType,
    Image,
    Face,
    Plain,
    Record,
    Reply,
)
from astrbot.core.message.message_event_result import MessageChain

_DECORATION = (Reply, At, AtAll)
# meme_manager and similar expression hooks decorate outgoing text with
# ``&&angry&&``-style tokens.  They are internal markers, never user-facing.
_EMOTION_MARKER = re.compile(r"&&[A-Za-z][A-Za-z0-9_:-]{0,32}&&")


def _without_emotion_markers(chain):
    cleaned = []
    for part in chain:
        if isinstance(part, Plain) and _EMOTION_MARKER.search(part.text or ""):
            cleaned.append(Plain(text=_EMOTION_MARKER.sub("", part.text or "")))
        else:
            cleaned.append(part)
    return cleaned


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
    chain = _without_emotion_markers(chain)
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


async def native_wire_message(event, message):
    """The SDK's Image conversion drops sub_type; keep it for owned QQ stickers."""
    getter = getattr(event, "get_platform_name", None)
    if callable(getter) and getter() == "wechat_native":
        chain = getattr(message, "chain", ())
        if any(isinstance(part, Face) for part in chain):
            return _derive(message, [Plain(text=f"[QQ表情 {part.id}]") if isinstance(part, Face) else part
                                     for part in chain])
        return message
    if not callable(getter) or getter() != "aiocqhttp":
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
