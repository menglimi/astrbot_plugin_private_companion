# -*- coding: utf-8 -*-
from .main_segmented_reply_shared import (
    logger,
)
from .main_segmented_reply_shared import logger
from .main_segmented_reply_part03 import PrivateCompanionPluginSegmentedReplyPart03Mixin
from .main_segmented_reply_part02 import PrivateCompanionPluginSegmentedReplyPart02Mixin
from .main_segmented_reply_part01 import PrivateCompanionPluginSegmentedReplyPart01Mixin
class PrivateCompanionPluginSegmentedReplyMixin(PrivateCompanionPluginSegmentedReplyPart01Mixin, PrivateCompanionPluginSegmentedReplyPart02Mixin, PrivateCompanionPluginSegmentedReplyPart03Mixin):
    """分段回复域（从 PrivateCompanionPlugin 拆出）。"""
