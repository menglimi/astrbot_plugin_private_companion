# -*- coding: utf-8 -*-
from .page_api_config_shared import (
    CYCLE_SETTING_KEYS,
    PAGE_PRIVATE_CONFIG_KEYS,
    logger,
)
from .page_api_config_shared import logger
from .page_api_config_part05 import PrivateCompanionPageApiConfigPart05Mixin
from .page_api_config_part04 import PrivateCompanionPageApiConfigPart04Mixin
from .page_api_config_part03 import PrivateCompanionPageApiConfigPart03Mixin
from .page_api_config_part02 import PrivateCompanionPageApiConfigPart02Mixin
from .page_api_config_part01 import PrivateCompanionPageApiConfigPart01Mixin
class PrivateCompanionPageApiConfigMixin(PrivateCompanionPageApiConfigPart01Mixin, PrivateCompanionPageApiConfigPart02Mixin, PrivateCompanionPageApiConfigPart03Mixin, PrivateCompanionPageApiConfigPart04Mixin, PrivateCompanionPageApiConfigPart05Mixin):
    """配置/设置域（从 PrivateCompanionPageApi 拆出）。"""
