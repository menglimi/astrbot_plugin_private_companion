# -*- coding: utf-8 -*-
from .page_api_creative_shared import (
    _render_page_background_prompt,
    _render_page_background_prompt_pair,
    logger,
)
from .page_api_creative_shared import logger
from .page_api_creative_part03 import PrivateCompanionPageApiCreativePart03Mixin
from .page_api_creative_part02 import PrivateCompanionPageApiCreativePart02Mixin
from .page_api_creative_part01 import PrivateCompanionPageApiCreativePart01Mixin
class PrivateCompanionPageApiCreativeMixin(PrivateCompanionPageApiCreativePart01Mixin, PrivateCompanionPageApiCreativePart02Mixin, PrivateCompanionPageApiCreativePart03Mixin):
    """创作域（从 PrivateCompanionPageApi 拆出）。"""
