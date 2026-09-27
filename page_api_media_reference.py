# -*- coding: utf-8 -*-
from .page_api_media_reference_shared import (
    PHOTO_REFERENCE_ASSET_MAX_BYTES,
    PHOTO_REFERENCE_ASSET_MAX_COUNT,
    PHOTO_REFERENCE_ASSET_MAX_PER_OWNER,
    PHOTO_REFERENCE_ASSET_MIMES,
    PHOTO_REFERENCE_ASSET_SCOPES,
    PHOTO_REFERENCE_METADATA_REVIEW_TIMEOUT_SECONDS,
    PHOTO_REFERENCE_PREVIEW_MAX_BYTES,
    PHOTO_REFERENCE_UPLOAD_MAX_COUNT,
    PHOTO_REFERENCE_UPLOAD_MAX_REQUEST_BYTES,
    PHOTO_REFERENCE_UPLOAD_MAX_TOTAL_BYTES,
    _render_page_background_prompt_pair,
    logger,
)
from .page_api_media_reference_shared import logger
# 门面显式暴露 run_photo_selection_trial：域模块经 _page_api_media_reference_host
# 按调用时解析，tests 对宿主门面的 patch 必须能穿透。
from .page_api_media_reference_shared import (  # noqa: F401
    run_photo_selection_trial,
)
from .page_api_media_reference_part04 import PrivateCompanionPageApiMediaReferencePart04Mixin
from .page_api_media_reference_part03 import PrivateCompanionPageApiMediaReferencePart03Mixin
from .page_api_media_reference_part02 import PrivateCompanionPageApiMediaReferencePart02Mixin
from .page_api_media_reference_part01 import PrivateCompanionPageApiMediaReferencePart01Mixin
class PrivateCompanionPageApiMediaReferenceMixin(PrivateCompanionPageApiMediaReferencePart01Mixin, PrivateCompanionPageApiMediaReferencePart02Mixin, PrivateCompanionPageApiMediaReferencePart03Mixin, PrivateCompanionPageApiMediaReferencePart04Mixin):
    """参考图与素材域（从 PrivateCompanionPageApiMediaMixin 拆出）。"""
