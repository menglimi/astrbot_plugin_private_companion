# -*- coding: utf-8 -*-
from .daily_review_shared import (
    logger,
)
from .daily_review_shared import logger
from .daily_review_part04 import DailyReviewPart04Mixin
from .daily_review_part03 import DailyReviewPart03Mixin
from .daily_review_part02 import DailyReviewPart02Mixin
from .daily_review_part01 import DailyReviewPart01Mixin
class DailyReviewMixin(DailyReviewPart01Mixin, DailyReviewPart02Mixin, DailyReviewPart03Mixin, DailyReviewPart04Mixin):
    _DAILY_REVIEW_FAILURE_COOLDOWN_SECONDS = 30 * 60
    _DAILY_REVIEW_FAILURE_MAX_BACKOFF_SECONDS = 6 * 60 * 60
    _DAILY_REVIEW_FAILURE_CIRCUIT_SECONDS = 24 * 60 * 60
    _DAILY_REVIEW_MAX_CONSECUTIVE_FAILURES = 5
    _DAILY_REVIEW_SEVERITIES = {"info", "warn", "error"}
    _DAILY_REVIEW_CATEGORIES = {
        "reply",
        "proactive",
        "group",
        "member_safety",
        "tts",
        "model",
        "storage",
        "schedule",
        "other",
    }
    _DAILY_REVIEW_GUIDANCE_SCOPES = {"reply", "proactive", "group", "tts"}
