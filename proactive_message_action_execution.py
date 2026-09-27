# -*- coding: utf-8 -*-
from .proactive_message_action_execution_shared import (
    _now_ts,
    logger,
)
from .proactive_message_action_execution_shared import logger
from .proactive_message_action_execution_part03 import ProactiveMessageActionExecutionPart03Mixin
from .proactive_message_action_execution_part02 import ProactiveMessageActionExecutionPart02Mixin
from .proactive_message_action_execution_part01 import ProactiveMessageActionExecutionPart01Mixin
class ProactiveMessageActionExecutionMixin(ProactiveMessageActionExecutionPart01Mixin, ProactiveMessageActionExecutionPart02Mixin, ProactiveMessageActionExecutionPart03Mixin):
    """action_execution 域（从 ProactiveMessageMixin 拆出）。"""
