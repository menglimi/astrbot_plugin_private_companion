"""Telegram shows a voice message only for OGG/Opus; AstrBot's Record always resolves to WAV."""

import logging

from astrbot.core.message.components import Record
from astrbot.core.utils.media_utils import convert_audio_format


logger = logging.getLogger(__name__)


class TelegramVoice(Record):
    async def convert_to_file_path(self):
        wav = await super().convert_to_file_path()
        try:
            return await convert_audio_format(wav, output_format="ogg")
        except Exception as error:
            # Without Opus the recording still goes out, as AstrBot sent it before: a WAV file.
            logger.warning("[CompanionV2] Telegram 语音转 OGG 失败，改发原录音 error_type=%s",
                           type(error).__name__)
            return wav


def telegram_voice(part):
    """The same recording, sent by the Telegram adapter as an Opus voice message."""
    if type(part) is not Record:
        return part
    return TelegramVoice(file=part.file, url=part.url, path=part.path, text=part.text)
