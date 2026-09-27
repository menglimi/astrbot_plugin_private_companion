# -*- coding: utf-8 -*-
from .page_api_media_shared import (
    IMAGE_CACHE_THUMBNAIL_MAX_EDGE,
    IMAGE_CACHE_THUMBNAIL_QUALITY,
    PAGE_API_PREFIX,
    PLUGIN_NAME,
    logger,
)
# 门面显式暴露 request：域模块经 _page_api_media_host 按调用时解析，
# tests 对 page_api_media.request 的 patch 必须能穿透。
from .page_api_shared import _page_api_host_request as request  # noqa: F401
from .page_api_media_shared import logger
from .page_api_media_part02 import PrivateCompanionPageApiMediaPart02Mixin
from .page_api_media_part01 import PrivateCompanionPageApiMediaPart01Mixin
from .page_api_media_shared import PrivateCompanionPageApiMediaDiagnosticsMixin
from .page_api_media_shared import PrivateCompanionPageApiMediaReferenceMixin
class PrivateCompanionPageApiMediaMixin(PrivateCompanionPageApiMediaDiagnosticsMixin, PrivateCompanionPageApiMediaReferenceMixin, PrivateCompanionPageApiMediaPart01Mixin, PrivateCompanionPageApiMediaPart02Mixin):
    """图片 / 素材 / 参考图 域（从 PrivateCompanionPageApi 拆出）。"""
