# -*- coding: utf-8 -*-
from .page_api_bookshelf_shared import (
    BOOKSHELF_ACCESS_TOKEN_MAX_PERSISTED,
    BOOKSHELF_ACCESS_TOKEN_TTL_SECONDS,
    logger,
)
from .page_api_bookshelf_shared import logger
from .page_api_bookshelf_part04 import PrivateCompanionPageApiBookshelfPart04Mixin
from .page_api_bookshelf_part03 import PrivateCompanionPageApiBookshelfPart03Mixin
from .page_api_bookshelf_part02 import PrivateCompanionPageApiBookshelfPart02Mixin
from .page_api_bookshelf_part01 import PrivateCompanionPageApiBookshelfPart01Mixin
class PrivateCompanionPageApiBookshelfMixin(PrivateCompanionPageApiBookshelfPart01Mixin, PrivateCompanionPageApiBookshelfPart02Mixin, PrivateCompanionPageApiBookshelfPart03Mixin, PrivateCompanionPageApiBookshelfPart04Mixin):
    """书架域（从 PrivateCompanionPageApi 拆出）。"""
