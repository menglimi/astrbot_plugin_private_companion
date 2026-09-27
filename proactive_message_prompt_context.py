# -*- coding: utf-8 -*-
from .proactive_message_prompt_context_shared import (
    _now_ts,
    logger,
)
from .proactive_message_prompt_context_shared import logger
from .proactive_message_prompt_context_part03 import ProactiveMessagePromptContextPart03Mixin
from .proactive_message_prompt_context_part02 import ProactiveMessagePromptContextPart02Mixin
from .proactive_message_prompt_context_part01 import ProactiveMessagePromptContextPart01Mixin
class ProactiveMessagePromptContextMixin(ProactiveMessagePromptContextPart01Mixin, ProactiveMessagePromptContextPart02Mixin, ProactiveMessagePromptContextPart03Mixin):
    """prompt_context 域（从 ProactiveMessageMixin 拆出）。"""
