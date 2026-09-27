# -*- coding: utf-8 -*-
from .proactive_message_generation_shared import (
    logger,
)
from .proactive_message_generation_shared import logger
from .proactive_message_generation_part03 import ProactiveMessageGenerationPart03Mixin
from .proactive_message_generation_part02 import ProactiveMessageGenerationPart02Mixin
from .proactive_message_generation_part01 import ProactiveMessageGenerationPart01Mixin
class ProactiveMessageGenerationMixin(ProactiveMessageGenerationPart01Mixin, ProactiveMessageGenerationPart02Mixin, ProactiveMessageGenerationPart03Mixin):
    """generation 域（从 ProactiveMessageMixin 拆出）。"""
