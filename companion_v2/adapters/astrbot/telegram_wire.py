"""Telegram shows a voice message only for OGG/Opus; AstrBot's Record always resolves to WAV."""

from astrbot.core.message.components import Record
from astrbot.core.utils.media_utils import convert_audio_format


class TelegramVoice(Record):
    async def convert_to_file_path(self):
        wav = await super().convert_to_file_path()
        return await convert_audio_format(wav, output_format="ogg")


def telegram_voice(part):
    """The same recording, sent by the Telegram adapter as an Opus voice message."""
    if type(part) is not Record:
        return part
    return TelegramVoice(file=part.file, url=part.url, path=part.path, text=part.text)
