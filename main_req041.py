# -*- coding: utf-8 -*-
from .main_req041_shared import (
    logger,
)
from .main_req041_shared import logger
from .main_req041_part04 import PrivateCompanionPluginReq041Part04Mixin
from .main_req041_part03 import PrivateCompanionPluginReq041Part03Mixin
from .main_req041_part02 import PrivateCompanionPluginReq041Part02Mixin
from .main_req041_part01 import PrivateCompanionPluginReq041Part01Mixin
class PrivateCompanionPluginReq041Mixin(PrivateCompanionPluginReq041Part01Mixin, PrivateCompanionPluginReq041Part02Mixin, PrivateCompanionPluginReq041Part03Mixin, PrivateCompanionPluginReq041Part04Mixin):
    """REQ041 迁移 / 作用域重绑域（从 PrivateCompanionPlugin 拆出）。"""
