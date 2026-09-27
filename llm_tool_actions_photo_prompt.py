# -*- coding: utf-8 -*-
from .llm_tool_actions_photo_prompt_shared import (
    _CURRENT_MEDIA_IMAGE_SUFFIXES,
    _CURRENT_MEDIA_MAX_AGE_SECONDS,
    _CURRENT_MEDIA_MAX_BYTES,
    _PHOTO_TOOL_HTTP_URL_RE,
    _PHOTO_TOOL_POSIX_PATH_START_RE,
    _PHOTO_TOOL_REDACTED_LOCAL_PATH,
    _PHOTO_TOOL_RELATIVE_PATH_START_RE,
    _PHOTO_TOOL_WINDOWS_PATH_START_RE,
)
from .llm_tool_actions_photo_prompt_shared import _CURRENT_MEDIA_IMAGE_SUFFIXES
from .llm_tool_actions_photo_prompt_part03 import LlmToolActionsPhotoPromptPart03Mixin
from .llm_tool_actions_photo_prompt_part02 import LlmToolActionsPhotoPromptPart02Mixin
from .llm_tool_actions_photo_prompt_part01 import LlmToolActionsPhotoPromptPart01Mixin
class LlmToolActionsPhotoPromptMixin(LlmToolActionsPhotoPromptPart01Mixin, LlmToolActionsPhotoPromptPart02Mixin, LlmToolActionsPhotoPromptPart03Mixin):
    """生图提示与当前媒体域（从 LlmToolActionsMixin 拆出）。"""
