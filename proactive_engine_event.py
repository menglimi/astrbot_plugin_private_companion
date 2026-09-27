# -*- coding: utf-8 -*-
from .proactive_engine_event_shared import (
    logger,
)
from .proactive_engine_event_shared import logger
from .proactive_engine_event_part03 import ProactiveEngineEventPart03Mixin
from .proactive_engine_event_part02 import ProactiveEngineEventPart02Mixin
from .proactive_engine_event_part01 import ProactiveEngineEventPart01Mixin
class ProactiveEngineEventMixin(ProactiveEngineEventPart01Mixin, ProactiveEngineEventPart02Mixin, ProactiveEngineEventPart03Mixin):
    """事件挑选/构造域（从 ProactiveEngineMixin 拆出）。"""
