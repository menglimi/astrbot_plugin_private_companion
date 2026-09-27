# -*- coding: utf-8 -*-
from .proactive_message_send_review_shared import (
    At,
    CoreMessageComponents,
    Image,
    _now_ts,
    logger,
)
from .proactive_message_send_review_shared import logger
from .proactive_message_send_review_part04 import ProactiveMessageSendReviewPart04Mixin
from .proactive_message_send_review_part03 import ProactiveMessageSendReviewPart03Mixin
from .proactive_message_send_review_part02 import ProactiveMessageSendReviewPart02Mixin
from .proactive_message_send_review_part01 import ProactiveMessageSendReviewPart01Mixin
class ProactiveMessageSendReviewMixin(ProactiveMessageSendReviewPart01Mixin, ProactiveMessageSendReviewPart02Mixin, ProactiveMessageSendReviewPart03Mixin, ProactiveMessageSendReviewPart04Mixin):
    """send_review 域（从 ProactiveMessageMixin 拆出）。"""
