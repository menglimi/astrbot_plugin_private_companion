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

from .game_integration_shared import (
    GAME_ASSESSMENT_CACHE_LIMIT,
    GAME_EVENT_TYPES,
    GAME_PROCESSED_EVENT_LIMIT,
    GAME_RESULTS,
    GAME_SCOPE_KEY_VERSION,
    GAME_SCOPE_RETENTION_SECONDS,
    GAME_SCOPE_STORE_LIMIT,
    GAME_STATE_VERSION,
    REMATCH_EFFECTS,
    _render_game_prompt,
    logger,
)
from .game_integration_shared import logger
from .game_integration_part03 import GameIntegrationPart03Mixin
from .game_integration_part02 import GameIntegrationPart02Mixin
from .game_integration_part01 import GameIntegrationPart01Mixin
class GameIntegrationMixin(GameIntegrationPart01Mixin, GameIntegrationPart02Mixin, GameIntegrationPart03Mixin):
    """Optional game events and persona-shaped emotional afterglow.

    Game state is deliberately scoped by persona, conversation and game.  The
    external API can therefore be used by a group game without allowing its
    short-lived tone to appear in a private conversation for the same user.
    """
