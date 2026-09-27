# -*- coding: utf-8 -*-
from .page_api_proactive_shared import (
    logger,
)
from .page_api_proactive_shared import logger
from .page_api_proactive_part04 import PrivateCompanionPageApiProactivePart04Mixin
from .page_api_proactive_part03 import PrivateCompanionPageApiProactivePart03Mixin
from .page_api_proactive_part02 import PrivateCompanionPageApiProactivePart02Mixin
from .page_api_proactive_part01 import PrivateCompanionPageApiProactivePart01Mixin
class PrivateCompanionPageApiProactiveMixin(PrivateCompanionPageApiProactivePart01Mixin, PrivateCompanionPageApiProactivePart02Mixin, PrivateCompanionPageApiProactivePart03Mixin, PrivateCompanionPageApiProactivePart04Mixin):
    """主动消息 / 唤醒 / 候选 域（从 PrivateCompanionPageApi 拆出）。"""
