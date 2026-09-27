# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import copy
from difflib import SequenceMatcher
import inspect
import json
import os
import random
import re
import struct
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

try:
    from astrbot.api.message_components import Plain, Record
except ImportError:
    from astrbot.api.message_components import Plain
    from astrbot.core.message.components import Record
from astrbot.core import file_token_service
from astrbot.core.message.message_event_result import ResultContentType
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .conversation_injection_plan import (
    PLACEMENT_DYNAMIC_SYSTEM,
    get_conversation_injection_plan,
)
from .conversation_prompt_section import (
    PromptDocument,
    PromptRenderMode,
    PromptSection,
    exact_text,
    prompt_document,
    prompt_section,
    render_prompt_document,
    render_prompt_sections,
)
from .helpers import (
    _has_history_media_marker,
    _normalize_outbound_punctuation_flow,
    _safe_int,
    _single_line,
    _strip_history_media_markers,
    _strip_nonstandard_chat_control_tags,
)
from .persona_config import runtime_persona_setting
from .segmented_message import (
    component_kind,
    component_order_from_owner,
    component_strategies_from_owner,
    plan_component_chunks,
)
from .logging_util import get_module_logger
from .tts_enhancement_visible_chinese import TtsEnhancementVisibleChineseMixin
from .tts_enhancement_tags_realtime import TtsEnhancementTagsRealtimeMixin
from .tts_enhancement_request_apply import TtsEnhancementRequestApplyMixin
from .tts_enhancement_playback_delivery import TtsEnhancementPlaybackDeliveryMixin
from .tts_enhancement_markup_record import TtsEnhancementMarkupRecordMixin
from .tts_enhancement_fishaudio_emotion import TtsEnhancementFishaudioEmotionMixin
from .tts_enhancement_convert_postprocess import TtsEnhancementConvertPostprocessMixin
from .tts_enhancement_config_provider import (  # noqa: F401  # re-export
    TtsEnhancementConfigProviderMixin,
    _MimoVoiceCloneTtsAdapter,
)
from .tts_enhancement_chain_send import TtsEnhancementChainSendMixin
from .tts_enhancement_session_trigger import TtsEnhancementSessionTriggerMixin

from .tts_enhancement_shared import (
    build_tts_spoken_conversion_prompt_document,
    build_tts_spoken_conversion_prompts,
    DEFAULT_AUTO_VOICE_PROMPT_MARKERS,
    DEFAULT_MIMO_VOICE_CLONE_TOOL_NAME,
    DEFAULT_TTS_SANITIZE_FILTER_WORDS,
    DEFAULT_TTS_SANITIZE_REMOVE_PATTERNS,
    DEFAULT_TTS_SANITIZE_REPLACEMENTS,
    EMOTION_TAG_PATTERN,
    FISH_AUDIO_AUTO_BLOCKED_EFFECTS,
    FISH_AUDIO_CUE_ALIASES,
    FISH_AUDIO_EMOTION_MODES,
    FISH_AUDIO_EXPLICIT_SIGH_PATTERN,
    FISH_AUDIO_MODELS,
    FISH_AUDIO_S1_ALIAS_OVERRIDES,
    FISH_AUDIO_S1_CUE_PATTERN,
    FISH_AUDIO_S1_CUES,
    FISH_AUDIO_S2_CUE_PATTERN,
    logger,
    PRIVATE_TTS_BLOCK_TOKEN_PATTERN,
    TTS_BLOCK_PATTERN,
    TTS_BLOCK_TOKEN_PATTERN,
    TTS_EMOTION_PLACEHOLDER_PREFIX,
    TTS_LANGUAGE_PROVIDER_ATTRS,
    TTS_MARKDOWN_LINK_PATTERN,
    TTS_SPOKEN_URL_PATTERN,
    TTS_TAG_PATTERN,
    TTS_VISIBLE_EMOTION_CUES,
    TTS_VISIBLE_LABEL_PATTERN,
)



class TtsEnhancementMixin(TtsEnhancementSessionTriggerMixin, TtsEnhancementChainSendMixin, TtsEnhancementConfigProviderMixin, TtsEnhancementConvertPostprocessMixin, TtsEnhancementFishaudioEmotionMixin, TtsEnhancementMarkupRecordMixin, TtsEnhancementPlaybackDeliveryMixin, TtsEnhancementRequestApplyMixin, TtsEnhancementTagsRealtimeMixin, TtsEnhancementVisibleChineseMixin):
    """Integrated TTS enhancement for private_companion.

    This is intentionally not a verbatim copy of tts_modify. It keeps the useful
    behavior surface but maps identity and prompts to private_companion concepts.
    """
