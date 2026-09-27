# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import os
import base64
import hashlib
import re
import shutil
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any

from astrbot.api.event import AstrMessageEvent

from .constants import DEFAULT_NATURAL_LANGUAGE_PHOTO_EXTRA_PROMPT
from .conversation_prompt_section import (
    PhotoPromptContent,
    PromptRenderMode,
    PromptSection,
    prompt_section,
    render_prompt_sections,
)
from .helpers import _flat_get, _missing_optional_model_dependency, _now_ts, _path_text, _photo_group_request_matches, _safe_float, _safe_int, _set_into_config, _single_line, _today_key
from .command_handlers_shared import _PHOTO_REFERENCE_SUFFIXES, logger
from .command_handlers_photo_reference import CommandHandlersPhotoReferenceMixin
from .command_handlers_cm_snapshot_config import CommandHandlersCmSnapshotConfigMixin
from .command_handlers_cm_proposal_build import CommandHandlersCmProposalBuildMixin
from .command_handlers_photo_dispatch import CommandHandlersPhotoDispatchMixin
from .command_handlers_nl_photo_intent_prompt import CommandHandlersNlPhotoIntentPromptMixin
from .command_handlers_cm_entries_apply import CommandHandlersCmEntriesApplyMixin
from .command_handlers_cm_answer import CommandHandlersCmAnswerMixin
from .command_handlers_cm_pending_recent import CommandHandlersCmPendingRecentMixin
from .command_handlers_qweather_image_api import CommandHandlersQweatherImageApiMixin
from .command_handlers_group_command import CommandHandlersGroupCommandMixin
from .group_command_system import (
    GROUP_COMMAND_HELP,
    GroupLLMAction,
    format_llm_blocked,
    format_llm_status,
    parse_group_command,
)
from .photo_generation_scope import PHOTO_GENERATION_SCOPE_LIMIT_KEYS
from .photo_reference_catalog import (
    CATALOG_VERSION,
    CatalogValidationError,
    PhotoReference,
    add_reference,
    delete_reference,
    load_catalog,
    validate_and_serialize,
)
from .persona_config import runtime_persona_setting
from .admin_config_command import (
    apply_config_value,
    apply_pending_config,
    apply_setting_command,
    cancel_pending_config,
    can_apply_config,
    get_pending_config,
    parse_setting_text,
)


class CommandHandlersMixin(CommandHandlersGroupCommandMixin, CommandHandlersQweatherImageApiMixin, CommandHandlersCmPendingRecentMixin, CommandHandlersCmAnswerMixin, CommandHandlersCmEntriesApplyMixin, CommandHandlersNlPhotoIntentPromptMixin, CommandHandlersPhotoDispatchMixin, CommandHandlersCmProposalBuildMixin, CommandHandlersCmSnapshotConfigMixin, CommandHandlersPhotoReferenceMixin):
    """Implementation bodies for command handlers registered in main.py."""
