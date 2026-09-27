# -*- coding: utf-8 -*-
from .user_memory_reply_review_shared import (
    _CLAUSE_BOUNDARY,
    _IMPLICIT_LATE,
    _LATE_CLAIM_GAP,
    _LATE_CLOCK,
    _LATE_CLOCK_INTRO,
    _SLEEP_CUE,
)
from .user_memory_reply_review_shared import _CLAUSE_BOUNDARY
from .user_memory_reply_review_part04 import UserMemoryReplyReviewPart04Mixin
from .user_memory_reply_review_part03 import UserMemoryReplyReviewPart03Mixin
from .user_memory_reply_review_part02 import UserMemoryReplyReviewPart02Mixin
from .user_memory_reply_review_part01 import UserMemoryReplyReviewPart01Mixin
class UserMemoryReplyReviewMixin(UserMemoryReplyReviewPart01Mixin, UserMemoryReplyReviewPart02Mixin, UserMemoryReplyReviewPart03Mixin, UserMemoryReplyReviewPart04Mixin):
    """回答审查与行动反馈闭环（从 UserMemoryMixin 拆出）。"""
