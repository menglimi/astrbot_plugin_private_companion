# -*- coding: utf-8 -*-
from .proactive_engine_persona_shared import (
    logger,
)
from .proactive_engine_persona_shared import logger
from .proactive_engine_persona_part03 import ProactiveEnginePersonaPart03Mixin
from .proactive_engine_persona_part02 import ProactiveEnginePersonaPart02Mixin
from .proactive_engine_persona_part01 import ProactiveEnginePersonaPart01Mixin
class ProactiveEnginePersonaMixin(ProactiveEnginePersonaPart01Mixin, ProactiveEnginePersonaPart02Mixin, ProactiveEnginePersonaPart03Mixin):
    """人格对齐/模型评审域（从 ProactiveEngineMixin 拆出）。"""
