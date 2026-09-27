# -*- coding: utf-8 -*-
from .user_memory_context_prompt_shared import (
    OWNER_EXCLUSIVE_RELATIONSHIP_PROMPT_MAX_CHARS,
)
from .user_memory_context_prompt_shared import OWNER_EXCLUSIVE_RELATIONSHIP_PROMPT_MAX_CHARS
from .user_memory_context_prompt_part03 import UserMemoryContextPromptPart03Mixin
from .user_memory_context_prompt_part02 import UserMemoryContextPromptPart02Mixin
from .user_memory_context_prompt_part01 import UserMemoryContextPromptPart01Mixin
class UserMemoryContextPromptMixin(UserMemoryContextPromptPart01Mixin, UserMemoryContextPromptPart02Mixin, UserMemoryContextPromptPart03Mixin):
    """上下文提示词注入与画像摘要（从 UserMemoryMixin 拆出）。"""
