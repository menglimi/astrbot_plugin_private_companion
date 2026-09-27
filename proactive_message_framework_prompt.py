# -*- coding: utf-8 -*-
from .proactive_message_framework_prompt_shared import (
    _host_build_main_agent,
    _now_ts,
    logger,
)
from .proactive_message_framework_prompt_shared import logger
from .proactive_message_framework_prompt_part04 import ProactiveMessageFrameworkPromptPart04Mixin
from .proactive_message_framework_prompt_part03 import ProactiveMessageFrameworkPromptPart03Mixin
from .proactive_message_framework_prompt_part02 import ProactiveMessageFrameworkPromptPart02Mixin
from .proactive_message_framework_prompt_part01 import ProactiveMessageFrameworkPromptPart01Mixin
class ProactiveMessageFrameworkPromptMixin(ProactiveMessageFrameworkPromptPart01Mixin, ProactiveMessageFrameworkPromptPart02Mixin, ProactiveMessageFrameworkPromptPart03Mixin, ProactiveMessageFrameworkPromptPart04Mixin):
    """framework_prompt 域（从 ProactiveMessageMixin 拆出）。"""
