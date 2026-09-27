# -*- coding: utf-8 -*-
from .user_memory_companion_record_shared import (
    _REQ041_COMPANION_MEMORY_FIELDS,
    _REQ041_DIALOGUE_EPISODE_FIELDS,
)
from .user_memory_companion_record_shared import _REQ041_COMPANION_MEMORY_FIELDS
from .user_memory_companion_record_part04 import UserMemoryCompanionRecordPart04Mixin
from .user_memory_companion_record_part03 import UserMemoryCompanionRecordPart03Mixin
from .user_memory_companion_record_part02 import UserMemoryCompanionRecordPart02Mixin
from .user_memory_companion_record_part01 import UserMemoryCompanionRecordPart01Mixin
class UserMemoryCompanionRecordMixin(UserMemoryCompanionRecordPart01Mixin, UserMemoryCompanionRecordPart02Mixin, UserMemoryCompanionRecordPart03Mixin, UserMemoryCompanionRecordPart04Mixin):
    """伴侣记忆与对话情节开放回路（从 UserMemoryMixin 拆出）。"""
