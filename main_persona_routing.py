# -*- coding: utf-8 -*-
from .main_persona_routing_shared import logger
from .main_persona_routing_part03 import PrivateCompanionPluginPersonaRoutingPart03Mixin
from .main_persona_routing_part02 import PrivateCompanionPluginPersonaRoutingPart02Mixin
from .main_persona_routing_part01 import PrivateCompanionPluginPersonaRoutingPart01Mixin
class PrivateCompanionPluginPersonaRoutingMixin(PrivateCompanionPluginPersonaRoutingPart01Mixin, PrivateCompanionPluginPersonaRoutingPart02Mixin, PrivateCompanionPluginPersonaRoutingPart03Mixin):
    """人格路由 / 统一身份域（从 PrivateCompanionPlugin 拆出）。"""
    pass
