"""Remove the Telegram Bot API token from a private provider request before it is sent."""

from astrbot.core.agent.message import AudioURLPart, ContentPart, ImageURLPart, TextPart, ThinkPart

from .telegram_private_media import UNAVAILABLE, private_token


def protect_telegram_request(event, request):
    """Last guard after every request hook, including history and other plugins' additions."""
    token = private_token(event)
    if token is None:
        return

    def clean(value):
        if isinstance(value, str):
            return value.replace(token, "[redacted]")
        if isinstance(value, list):
            return [clean(item) for item in value]
        if isinstance(value, dict):
            for kind in ("audio_url", "image_url"):
                url = value.get(kind)
                url = url.get("url") if isinstance(url, dict) else url
                if isinstance(url, str) and token in url:
                    # A URL fetched with the token is not a usable attachment once redacted.
                    return {"type": "text", "text": UNAVAILABLE}
            return {key: clean(item) for key, item in value.items()}
        if isinstance(value, (ImageURLPart, AudioURLPart)):
            target = getattr(value, value.type)
            if token in target.url:
                return TextPart(text=UNAVAILABLE)
            target.id = clean(target.id)
        if isinstance(value, TextPart):
            value.text = clean(value.text)
        elif isinstance(value, ThinkPart):
            value.think = clean(value.think)
        if isinstance(value, ContentPart) and token in value.model_dump_json():
            # Any other field still naming the token (a signature, a new field) makes the part unusable.
            return TextPart(text=UNAVAILABLE)
        return value

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
