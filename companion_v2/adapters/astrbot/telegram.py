"""Telegram platform helpers for the AstrBot companion adapter.

The AstrBot Telegram adapter (python-telegram-bot v22) exposes a different
native surface than the OneBot/QQ Official adapters this plugin was built
against:

* the event carries ``.client`` (``telegram.ext.ExtBot``) instead of ``.bot``;
* the stable account key is the configured platform instance id, while
  ``message_obj.self_id`` is the bot *username*;
* inbound media components carry full Telegram Bot API URLs (PTB rewrites
  ``File.file_path`` to ``{base_file_url}/{token}/{path}``) and voice/audio
  arrive as an already-downloaded local wav;
* a reply component already embeds the quoted chain, so no native history
  read is required to capture a quote.

Everything in this module is pure value handling.  Downloading media and
converting audio formats belong to the trusted host layer
(``astrbot_host.telegram_media``).
"""

from __future__ import annotations

from dataclasses import dataclass

from companion_v2.conversation.quotes.models import CapturedQuote, message_number
from companion_v2.execution.models import TaskError


PLATFORM_NAME = "telegram"
USER_SUBJECT_PREFIX = "telegram-user:"


def is_telegram(value):
    return value == PLATFORM_NAME


def event_client(event):
    """The native client an event arrived on, across AstrBot adapters.

    OneBot, QQ Official and Lark events expose ``.bot``; the Telegram event
    only carries ``.client``.  Reading either keeps one call site for every
    adapter instead of branching in each host module.
    """
    client = getattr(event, "bot", None)
    if client is None:
        client = getattr(event, "client", None)
    return client


def event_bot_username(event):
    """The bot username Telegram puts on ``message_obj.self_id``."""
    return str(getattr(getattr(event, "message_obj", None), "self_id", "") or "")


def bot_mention_ids(event, account_id):
    """Identifiers that count as "the bot was mentioned" on Telegram.

    Inbound ``At`` components carry usernames, so the bot username is the
    match; the configured account id is kept for symmetry with other adapters.
    """
    values = {str(account_id), event_bot_username(event)}
    return frozenset(value for value in values if value)


def component_value(part):
    """The media locator an AstrBot component carries (URL, path or base64)."""
    value = getattr(part, "url", None) or getattr(part, "file", None)
    return value if type(value) is str else ""


@dataclass(frozen=True)
class TelegramQuoteImage:
    position: int
    locator: str


@dataclass(frozen=True)
class TelegramQuoteLocator:
    """A quote frozen from the inbound ``Comp.Reply`` chain.

    Telegram's adapter resolves the replied-to message before creating the
    component, so the text, attribution, timestamp and images are already
    present.  Keeping them in one immutable value means the later loader never
    has to read host history again.
    """

    position: int
    message_id: str
    sender_id: str
    sender_nickname: str
    sent_at: int | None
    text: str
    images: tuple[TelegramQuoteImage, ...]


def _canonical_numeric(value):
    try:
        return message_number(value)
    except TaskError:
        return None


def telegram_quote_locator(position, part):
    """Freeze one Telegram ``Reply`` component, or ``None`` when unusable.

    A quote that carries neither text nor an image is not a usable quote; the
    caller keeps the ``[引用消息]`` placeholder and moves on.  Attribution is
    validated here so the loader can trust the frozen values.
    """
    message_id = _canonical_numeric(getattr(part, "id", None))
    sender_id = _canonical_numeric(getattr(part, "sender_id", None)
                                   if getattr(part, "sender_id", None) is not None
                                   else getattr(part, "qq", None))
    if message_id is None or sender_id is None:
        return None
    sent_at = getattr(part, "time", None)
    if type(sent_at) is not int or not 0 < sent_at <= 253402300799:
        sent_at = None
    text = getattr(part, "message_str", None)
    if type(text) is not str or not text.strip():
        text = getattr(part, "text", None)
    text = text if type(text) is str else ""
    images = []
    chain = getattr(part, "chain", ())
    for index, component in enumerate(chain if isinstance(chain, (list, tuple)) else ()):
        kind = getattr(getattr(component, "type", None), "value", getattr(component, "type", None))
        if kind != "Image":
            continue
        value = component_value(component)
        if value:
            images.append(TelegramQuoteImage(index, value))
    if not text.strip() and not images:
        return None
    if not text.strip():
        text = "\n" + "\n".join("[图片 " + str(i + 1) + "]" for i in range(len(images))) + "\n"
    return TelegramQuoteLocator(position, message_id, sender_id,
                                str(getattr(part, "sender_nickname", "") or ""),
                                sent_at, text, tuple(images))


async def read_telegram_quote(locator, *, image_loader, current=None):
    """Build a ``CapturedQuote`` from the frozen Telegram reply chain."""
    if not isinstance(locator, TelegramQuoteLocator):
        raise TaskError("host_quote_unsupported", "Telegram quote requires its frozen reply component")
    # Imported lazily: quotes.py imports the locator types from native.py, and
    # native.py imports this module at load time.
    from .quotes import image_locator

    images = []
    for item in locator.images:
        if callable(current):
            current()
        captured = await image_loader(image_locator(item.position, None, item.locator))
        if callable(current):
            current()
        if getattr(captured, "descriptor", None) is None or captured.descriptor.position != item.position:
            raise TaskError("host_image_invalid", "Telegram quote loader changed the original image position")
        images.append(captured)
    return CapturedQuote(locator.position, locator.message_id, locator.sender_id,
                         locator.sent_at, locator.text, tuple(images))
