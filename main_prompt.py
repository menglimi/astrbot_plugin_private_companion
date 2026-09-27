# -*- coding: utf-8 -*-
from .main_prompt_shared import (
    _default_segmenting_prompt_for,
    _host_plugin_class,
    logger,
)
from .main_prompt_shared import logger
from .main_prompt_part05 import PrivateCompanionPluginPromptPart05Mixin
from .main_prompt_part04 import PrivateCompanionPluginPromptPart04Mixin
from .main_prompt_part03 import PrivateCompanionPluginPromptPart03Mixin
from .main_prompt_part02 import PrivateCompanionPluginPromptPart02Mixin
from .main_prompt_part01 import PrivateCompanionPluginPromptPart01Mixin
class PrivateCompanionPluginPromptMixin(PrivateCompanionPluginPromptPart01Mixin, PrivateCompanionPluginPromptPart02Mixin, PrivateCompanionPluginPromptPart03Mixin, PrivateCompanionPluginPromptPart04Mixin, PrivateCompanionPluginPromptPart05Mixin):
    """LLM 请求提示词编排域（从 PrivateCompanionPlugin 拆出）。"""
