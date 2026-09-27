# -*- coding: utf-8 -*-
from .main_outbound_guard_shared import (
    logger,
)
from .main_outbound_guard_shared import logger
from .main_outbound_guard_part03 import PrivateCompanionPluginOutboundGuardPart03Mixin
from .main_outbound_guard_part02 import PrivateCompanionPluginOutboundGuardPart02Mixin
from .main_outbound_guard_part01 import PrivateCompanionPluginOutboundGuardPart01Mixin
class PrivateCompanionPluginOutboundGuardMixin(PrivateCompanionPluginOutboundGuardPart01Mixin, PrivateCompanionPluginOutboundGuardPart02Mixin, PrivateCompanionPluginOutboundGuardPart03Mixin):
    """出站守卫 / 内容净化域（从 PrivateCompanionPlugin 拆出）。"""
