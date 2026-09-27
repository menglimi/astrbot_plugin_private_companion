# -*- coding: utf-8 -*-
from .wardrobe_runtime_shared import (
    WARDROBE_ASSET_ORIGIN_LABELS,
    WARDROBE_DETAIL_FULL,
    WARDROBE_DETAIL_MAX_CHARS,
    WARDROBE_DETAIL_MAX_ITEMS,
    WARDROBE_DETAIL_PROGRESSIVE,
    WARDROBE_DETAIL_SCOPES,
    WARDROBE_DETAIL_TOOL_HINT,
    WARDROBE_DETAIL_TOOL_NAME,
    WARDROBE_DRAFT_KIND_LABELS,
    WARDROBE_INTENT_KEY,
    WARDROBE_INTENT_SOURCE_MODEL,
    WARDROBE_INTENT_TOOL_NAME,
    WARDROBE_INTENT_TTL_SECONDS,
    WARDROBE_MINIMAL_MAX_CHARS,
    WARDROBE_OVERRIDE_MAX_ITEMS,
    WARDROBE_PROMPT_KEY,
    _MISSING,
    _WARDROBE_DETAIL_CONTEXT_TRIGGERS,
    _WARDROBE_DETAIL_CONTEXT_WORDS,
    _WARDROBE_DETAIL_TRIGGERS,
    _WARDROBE_DETAIL_TRIGGER_PATTERNS,
    _WARDROBE_DRAFT_IMAGE_SUFFIXES,
    _WARDROBE_VISION_MAX_IMAGES,
    _WARDROBE_VISION_TERSE_SUFFIX,
    _WARDROBE_VISION_TIMEOUT_SECONDS,
)
from .wardrobe_runtime_shared import WARDROBE_ASSET_ORIGIN_LABELS
from .wardrobe_runtime_part04 import WardrobePart04Mixin
from .wardrobe_runtime_part03 import WardrobePart03Mixin
from .wardrobe_runtime_part02 import WardrobePart02Mixin
from .wardrobe_runtime_part01 import WardrobePart01Mixin
class WardrobeMixin(WardrobePart01Mixin, WardrobePart02Mixin, WardrobePart03Mixin, WardrobePart04Mixin):
    """角色衣柜：配置、识图入库、提示词与命令。"""

    # ------------------------------------------------------------------
    # 读数
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 落盘
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 提示词
    # ------------------------------------------------------------------


    # ------------------------------------------------------------------
    # 生成器（模型路径）
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 按需索取：只读工具
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 识图：图片 → 衣物描述
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 命令
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 草稿队列（素材 → 语义的人工确认环节）
    #
    # 批量导入只落草稿、不碰衣柜：识图会错，落库必须有人点头。这三个方法
    # 与 scripts/wardrobe_review.py 共用同一套数据层（list_pending_drafts /
    # apply_wardrobe_draft / mark_asset_status），所以面板与 CLI 不会分叉。
    # ------------------------------------------------------------------



__all__ = ["WardrobeMixin", "WARDROBE_PROMPT_KEY"]
