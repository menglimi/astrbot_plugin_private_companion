# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import uuid
from copy import deepcopy
from functools import wraps
from typing import Any


from .companion_interaction_expression import current_interaction_projection
from .helpers import (
    _group_link_message_context,
    _missing_optional_model_dependency,
    _now_ts,
    _reset_unanswered_proactive,
    _safe_float,
    _safe_int,
    _single_line,
)
from .photo_nai_params import cache_user_photo_nai_params

from .message_pipeline_shared import logger

from . import message_pipeline_part01 as _message_pipeline_part01

_coalesce_event_data_saves = _message_pipeline_part01._coalesce_event_data_saves
event_data_save_boundary = _message_pipeline_part01.event_data_save_boundary
_persona_value = _message_pipeline_part01._persona_value
_persona_feature_enabled = _message_pipeline_part01._persona_feature_enabled

from . import message_pipeline_part02 as _message_pipeline_part02

handle_private_message = _message_pipeline_part02.handle_private_message

from . import message_pipeline_part03 as _message_pipeline_part03

handle_group_message = _message_pipeline_part03.handle_group_message

_message_pipeline_function_exports = {
    "_coalesce_event_data_saves": _coalesce_event_data_saves,
    "event_data_save_boundary": event_data_save_boundary,
    "_persona_value": _persona_value,
    "_persona_feature_enabled": _persona_feature_enabled,
    "handle_private_message": handle_private_message,
    "handle_group_message": handle_group_message,
}
for _message_pipeline_part in (
    _message_pipeline_part01,
    _message_pipeline_part02,
    _message_pipeline_part03,
):
    _message_pipeline_part.__dict__.update(_message_pipeline_function_exports)
del _message_pipeline_part, _message_pipeline_function_exports
del _message_pipeline_part01, _message_pipeline_part02, _message_pipeline_part03
