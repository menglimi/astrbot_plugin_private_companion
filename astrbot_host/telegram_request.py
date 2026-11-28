"""Remove the Telegram Bot API token from a private provider request before it is sent."""

import json

from astrbot.core.agent.message import AudioURLPart, ContentPart, ImageURLPart, TextPart, ThinkPart
from pydantic import BaseModel, ValidationError

from .telegram_private_media import UNAVAILABLE, private_token


def _cleaner(token):
    """A function that removes ``token`` from any request value, typed or not."""

    def unavailable(part):
        # The note takes the replaced part's place, including AstrBot's turn-only mark.
        note = TextPart(text=UNAVAILABLE)
        return note.mark_as_temp() if getattr(part, "_no_save", False) else note

    def clean(value):
        if isinstance(value, str):
            return value.replace(token, "[redacted]")
        if isinstance(value, (list, tuple)):
            return [clean(item) for item in value]
        if isinstance(value, dict):
            # Serialized parts carry AstrBot's turn-only mark as a key; the note keeps it.
            note = {"type": "text", "text": UNAVAILABLE, **({"_no_save": True} if value.get("_no_save") else {})}
            if value.get("type") == "think" and token in json.dumps(value, ensure_ascii=False, default=str):
                # A signed thinking block cannot be edited and still verify; drop it whole.
                return note
            for kind in ("audio_url", "image_url"):
                url = value.get(kind)
                url = url.get("url") if isinstance(url, dict) else url
                if isinstance(url, str) and token in url:
                    # A URL fetched with the token is not a usable attachment once redacted.
                    return note
            return {clean(key): clean(item) for key, item in value.items()}
        if isinstance(value, (ImageURLPart, AudioURLPart)):
            target = getattr(value, value.type)
            if token in target.url:
                return unavailable(value)
            target.id = clean(target.id)
        if isinstance(value, TextPart):
            value.text = clean(value.text)
        elif isinstance(value, ThinkPart) and (token in (value.think or "") or token in (value.encrypted or "")):
            # A signed thinking block cannot be edited and still verify; drop it whole.
            return unavailable(value)
        if isinstance(value, ContentPart) and token in value.model_dump_json():
            # Any other field still naming the token (a signature, a new field) makes the part unusable.
            return unavailable(value)
        if isinstance(value, BaseModel) and not isinstance(value, ContentPart) and token in value.model_dump_json():
            # Typed entries (messages, tool calls): clean each field in place. Content parts stay
            # typed, and private attributes -- AstrBot's turn-only marks and checkpoints, which
            # decide what is saved to history -- survive; a dump would drop them.
            try:
                for name in type(value).model_fields:
                    setattr(value, name, clean(getattr(value, name)))
            except (TypeError, ValueError, AttributeError):
                pass
            if token not in value.model_dump_json():
                return value
            # Last resort for a model that cannot be edited in place: rebuild, keeping its private state.
            data = clean(value.model_dump(mode="json"))
            try:
                rebuilt = type(value).model_validate(data)
            except ValidationError:
                return data
            if token in rebuilt.model_dump_json():
                return data
            if getattr(value, "__pydantic_private__", None):
                rebuilt.__pydantic_private__ = dict(value.__pydantic_private__)
            return rebuilt
        return value

    return clean


def protect_telegram_request(event, request):
    """Last guard after every request hook, including history and other plugins' additions."""
    token = private_token(event)
    if token is None:
        return
    clean = _cleaner(token)

    for name in ("prompt", "system_prompt", "contexts", "extra_user_content_parts"):
        value = getattr(request, name, None)
        if value:
            setattr(request, name, clean(value))
    dropped = False
    for name in ("image_urls", "audio_urls"):
        urls = getattr(request, name, None)
        if urls:
            kept = [url for url in urls if not isinstance(url, str) or token not in url]
            dropped |= len(kept) != len(urls)
            setattr(request, name, kept)
    if dropped:
        parts = list(request.extra_user_content_parts or ())
        if not any(isinstance(part, TextPart) and part.text == UNAVAILABLE for part in parts):
            request.extra_user_content_parts = [*parts, TextPart(text=UNAVAILABLE)]


def protect_telegram_messages(event, run_context):
    """The same pass over the messages the runner will actually send.

    AstrBot rebuilds parts of the request after every request hook -- its image
    notices, from paths it cached before them -- so the assembled messages are
    cleaned once more just before the provider call.
    """
    token = private_token(event)
    messages = getattr(run_context, "messages", None)
    if token is not None and messages:
        run_context.messages = _cleaner(token)(messages)
