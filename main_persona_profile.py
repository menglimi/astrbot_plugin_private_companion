# -*- coding: utf-8 -*-
from .main_persona_profile_shared import (
    logger,
)
from .main_persona_profile_shared import logger
from .main_persona_profile_part02 import PrivateCompanionPluginPersonaProfilePart02Mixin
from .main_persona_profile_part01 import PrivateCompanionPluginPersonaProfilePart01Mixin
class PrivateCompanionPluginPersonaProfileMixin(PrivateCompanionPluginPersonaProfilePart01Mixin, PrivateCompanionPluginPersonaProfilePart02Mixin):
    """人格档案域（从 PrivateCompanionPlugin 拆出）。"""
