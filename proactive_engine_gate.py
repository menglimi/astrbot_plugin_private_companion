# -*- coding: utf-8 -*-
from .proactive_engine_gate_shared import (
    logger,
)
from .proactive_engine_gate_shared import logger
from .proactive_engine_gate_part03 import ProactiveEngineGatePart03Mixin
from .proactive_engine_gate_part02 import ProactiveEngineGatePart02Mixin
from .proactive_engine_gate_part01 import ProactiveEngineGatePart01Mixin
class ProactiveEngineGateMixin(ProactiveEngineGatePart01Mixin, ProactiveEngineGatePart02Mixin, ProactiveEngineGatePart03Mixin):
    """发送闸门/决策域（从 ProactiveEngineMixin 拆出）。"""
