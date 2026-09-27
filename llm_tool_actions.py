# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import hashlib
import html
import inspect
import json
import os
import random
import re
import shutil
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from astrbot.api.event import AstrMessageEvent
from astrbot.api.event import MessageChain
from astrbot.core.platform.message_session import MessageSession
from astrbot.core.utils.astrbot_path import get_astrbot_data_path
try:
    from astrbot.api.message_components import At, Plain
except ImportError:
    from astrbot.api.message_components import At, Plain

from .helpers import (
    _missing_optional_model_dependency,
    _now_ts,
    _path_text,
    _photo_group_request_matches,
    _redact_outbound_secrets,
    _safe_float,
    _safe_int,
    _single_line,
    _strip_internal_message_blocks,
)
from .memo_notes import apply_memo_note_action, memo_note_sort_key, normalize_memo_note
from .persona_config import runtime_persona_setting
from .conversation_prompt_section import (
    PromptRenderMode,
    PromptSection,
    prompt_section,
    render_prompt_sections,
)
from .owned_reaction_asset_catalog import OwnedReactionAssetCatalog
from .qzone_selection import (
    QzoneViewTarget,
    classify_qzone_view_owner,
    normalize_qzone_uin,
    normalize_qzone_view_target_scope,
    parse_qzone_post_selection,
    qzone_view_owner_is_pronoun_safe,
    resolve_qzone_view_target,
)
from .reaction_expression import (
    append_reaction_expression_outcome,
    classify_reaction_expression_feedback,
    ensure_reaction_expression_state,
    evaluate_reaction_expression_gate,
    reaction_expression_explicit_opt_out,
    reaction_expression_explicit_request,
    reaction_expression_auto_disabled,
    reaction_expression_high_frequency,
    reaction_expression_normalize_probability,
    sync_reaction_expression_auto_preference,
    normalize_reaction_expression_intent,
    reaction_expression_effective_probability,
    reaction_expression_image_key,
    reaction_expression_image_keys,
    reaction_expression_reservation_owned,
    reaction_expression_selection_preferences,
    reaction_expression_scope_state,
    record_reaction_expression_feedback,
    record_reaction_expression_sent,
    release_reaction_expression_image,
    release_reaction_expression_reservation,
    reserve_reaction_expression_image,
    reserve_reaction_expression_intent,
)
from .reaction_asset_library import ReactionAssetLibrary, get_reaction_asset_library
from .logging_util import get_module_logger
from .llm_tool_actions_creative_memo import LlmToolActionsCreativeMemoMixin
from .llm_tool_actions_interaction_relay import LlmToolActionsInteractionRelayMixin
from .llm_tool_actions_reaction_exec import LlmToolActionsReactionExecMixin
from .llm_tool_actions_reaction_search import LlmToolActionsReactionSearchMixin
from .llm_tool_actions_reaction_core import LlmToolActionsReactionCoreMixin
from .llm_tool_actions_photo_generate import LlmToolActionsPhotoGenerateMixin
from .llm_tool_actions_photo_prompt import LlmToolActionsPhotoPromptMixin
from .llm_tool_actions_qzone import LlmToolActionsQzoneMixin
from .interaction_tool_contract import InteractionQuery
from .interaction_query_orchestrator import execute_interaction_query
from .photo_nai_params import merge_user_photo_nai_params, recent_cached_photo_nai_params

from .llm_tool_actions_shared import (
    PHOTO_TOOL_SILENT_SENTINEL,
    _render_tool_prompt_section_labeled,
    _render_tool_prompt_section_labeled_inline,
    logger,
)

class LlmToolActionsMixin(LlmToolActionsQzoneMixin, LlmToolActionsPhotoPromptMixin, LlmToolActionsPhotoGenerateMixin, LlmToolActionsReactionCoreMixin, LlmToolActionsReactionSearchMixin, LlmToolActionsReactionExecMixin, LlmToolActionsInteractionRelayMixin, LlmToolActionsCreativeMemoMixin):
    """Implementation bodies for LLM tools registered in main.py."""

    def _reaction_asset_library(self):
        return get_reaction_asset_library(self)
