# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import base64
import hashlib
import html
import io
import json
import os
import re
import shutil
import tempfile
import time
import urllib.request
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlparse, urlsplit, urlunparse, urlunsplit

from astrbot.api.event import AstrMessageEvent
try:
    from astrbot.api.message_components import Image, Plain
except ImportError:
    from astrbot.api.message_components import Image, Plain
from astrbot.api.provider import ProviderRequest
from astrbot.core.agent.message import AssistantMessageSegment, TextPart, UserMessageSegment
from astrbot.core import file_token_service
from astrbot.core.astr_main_agent import MainAgentBuildConfig, build_main_agent
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .conversation_injection_plan import (
    PLACEMENT_DYNAMIC_SYSTEM,
    get_conversation_injection_plan,
)
from .conversation_prompt_section import (
    PromptRenderMode,
    PromptSection,
    prompt_section,
    exact_text,
    render_prompt_sections,
)
from .helpers import _missing_optional_model_dependency, _now_ts, _safe_float, _safe_int, _single_line, _strip_internal_message_blocks, _strip_outbound_control_blocks, _today_key, _url_host_is_public
from .persona_config import runtime_persona_setting
from .segmented_message import (
    component_kind,
    component_order_from_owner,
    component_strategies_from_owner,
    plan_component_chunks,
    sanitize_llm_segment_control_tokens,
)
from .logging_util import get_module_logger
from .private_image_history_delayed import PrivateImageHistoryDelayedMixin
from .private_image_ingest_cache import PrivateImageIngestCacheMixin
from .private_image_transcribe_group import PrivateImageTranscribeGroupMixin
from .private_image_reply_send import PrivateImageReplySendMixin
from .private_image_persona_visual import PrivateImagePersonaVisualMixin
from .private_image_placeholder_buffer import PrivateImagePlaceholderBufferMixin
from .private_image_review_delivery import PrivateImageReviewDeliveryMixin
from .private_image_provider_governance import PrivateImageProviderGovernanceMixin
from .private_image_model_capability import PrivateImageModelCapabilityMixin

from .private_image_shared import logger, _private_image_host, PREPARED_IMAGE_MAX_AGE_SECONDS, CONTEXT_IMAGE_FAILURE_COOLDOWN_SECONDS


class _PublicOnlyRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Re-check redirects so a public image URL cannot pivot into local networks."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        if not _url_host_is_public(newurl):
            logger.warning(
                "remote image redirect rejected: url=%s",
                _single_line(newurl, 160),
            )
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class PrivateImageMixin(PrivateImageModelCapabilityMixin, PrivateImageProviderGovernanceMixin, PrivateImageReviewDeliveryMixin, PrivateImagePlaceholderBufferMixin, PrivateImagePersonaVisualMixin, PrivateImageReplySendMixin, PrivateImageTranscribeGroupMixin, PrivateImageIngestCacheMixin, PrivateImageHistoryDelayedMixin):
    """Methods split from main.PrivateCompanionPlugin."""
