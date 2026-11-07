"""Install the verified OneBot client and media loaders for scoped quotes."""

from aiocqhttp.api_impl import WebSocketReverseApi

from companion_v2.adapters.astrbot.quotes import read_private_quote
from companion_v2.adapters.astrbot.group_quotes import read_group_quote
from companion_v2.adapters.astrbot.telegram import TelegramQuoteLocator, read_telegram_quote
from companion_v2.conversation.forwards.models import ForwardIntakePolicy

from .media import load_native_image
from .audio.loader import load_native_audio
from .video.loader import load_native_video


async def load_native_quote(locator, *, client, scope, intake=ForwardIntakePolicy()):
    if isinstance(locator, TelegramQuoteLocator):
        return await read_telegram_quote(locator, image_loader=load_native_image)
    return await read_private_quote(locator, client=client, scope=scope,
        reverse_api_type=WebSocketReverseApi, image_loader=load_native_image,
        audio_loader=load_native_audio, video_loader=load_native_video, intake=intake)


async def load_native_group_quote(locator, *, client, scope, intake=ForwardIntakePolicy()):
    if isinstance(locator, TelegramQuoteLocator):
        return await read_telegram_quote(locator, image_loader=load_native_image)
    return await read_group_quote(locator, client=client, scope=scope,
        reverse_api_type=WebSocketReverseApi, image_loader=load_native_image,
        audio_loader=load_native_audio, video_loader=load_native_video, intake=intake)
