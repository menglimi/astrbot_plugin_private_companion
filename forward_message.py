# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import hashlib
import html
import json
import re
import time
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from astrbot.api.event import AstrMessageEvent
from .forward_message_shared import (
    _render_conversation_section_body,
    _render_conversation_section_labeled,
    logger,
)
from .forward_message_shared import logger
from .forward_message_transcribe_rows_append import ForwardMessageTranscribeRowsAppendMixin
from .forward_message_transcribe_images import ForwardMessageTranscribeImagesMixin
from .forward_message_reply_rich_card import ForwardMessageReplyRichCardMixin
from .forward_message_reply_event_forward import ForwardMessageReplyEventForwardMixin
from .forward_message_reply_chain import ForwardMessageReplyChainMixin
from .forward_message_message_obj_inspect import ForwardMessageMessageObjInspectMixin
from .forward_message_forward_prompt_format import ForwardMessageForwardPromptFormatMixin
from .forward_message_descriptor_extract import ForwardMessageDescriptorExtractMixin
class ForwardMessageMixin(ForwardMessageDescriptorExtractMixin, ForwardMessageForwardPromptFormatMixin, ForwardMessageMessageObjInspectMixin, ForwardMessageReplyChainMixin, ForwardMessageReplyEventForwardMixin, ForwardMessageReplyRichCardMixin, ForwardMessageTranscribeImagesMixin, ForwardMessageTranscribeRowsAppendMixin):
    """Forward-message parsing and prompt-context helpers."""
