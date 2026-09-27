from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import math
import re
import time
import unicodedata
from contextlib import asynccontextmanager
from copy import deepcopy
from typing import Any, AsyncIterator
from .conversation_prompt_section import (
    PromptRenderMode,
    PromptSection,
    prompt_group,
    prompt_section,
    render_prompt_sections,
    xml_element,
)
from .logging_util import get_module_logger

logger = get_module_logger(__name__)


def _render_game_prompt(section: PromptSection) -> str:
    return render_prompt_sections([section], mode=PromptRenderMode.BODY_ONLY)



GAME_EVENT_TYPES = frozenset({"round_finished", "rematch_requested"})
GAME_RESULTS = frozenset({"bot_win", "bot_loss", "draw", "completed"})
REMATCH_EFFECTS = frozenset({"clear", "shorten", "keep", "extend"})
GAME_STATE_VERSION = 2
GAME_SCOPE_KEY_VERSION = "v2"
GAME_PROCESSED_EVENT_LIMIT = 512
GAME_ASSESSMENT_CACHE_LIMIT = 64
GAME_SCOPE_STORE_LIMIT = 128
GAME_SCOPE_RETENTION_SECONDS = 90 * 24 * 3600



class _game_integrationHostRef:
    """延迟引用宿主 game_integration 模块，保证 patch 宿主全局名对全部域生效。"""

    def __getattr__(self, name: str):
        from . import game_integration as _host_module

        return getattr(_host_module, name)


_game_integration_host = _game_integrationHostRef()
