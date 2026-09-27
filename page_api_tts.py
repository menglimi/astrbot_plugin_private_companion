# -*- coding: utf-8 -*-
from .page_api_tts_shared import (
    FISH_AUDIO_MODEL_OPTIONS,
    TTS_PROVIDER_SECRET_KEYS,
    TTS_PROVIDER_SYSTEM_KEYS,
    logger,
)
from .page_api_tts_shared import logger
from .page_api_tts_part03 import PrivateCompanionPageApiTtsPart03Mixin
from .page_api_tts_part02 import PrivateCompanionPageApiTtsPart02Mixin
from .page_api_tts_part01 import PrivateCompanionPageApiTtsPart01Mixin
class PrivateCompanionPageApiTtsMixin(PrivateCompanionPageApiTtsPart01Mixin, PrivateCompanionPageApiTtsPart02Mixin, PrivateCompanionPageApiTtsPart03Mixin):
    """TTS/语音域（从 PrivateCompanionPageApi 拆出）。"""
