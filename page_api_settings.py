# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any

from .companion_interaction_expression import normalize_normal_interaction_band_cap
from .constants import PAGE_FONT_NAMES, PAGE_THEME_NAMES
from .helpers import (
    _normalize_timezone_name,
    _normalize_timezone_setting,
    _path_text,
    normalize_bot_relationship_cards,
    normalize_photo_generation_scopes,
)
from .photo_generation_scope import (
    PHOTO_GENERATION_SCOPE_LIMIT_KEYS,
    normalize_photo_generation_scope_limit,
)
from .photo_reference_catalog import CatalogValidationError, validate_and_serialize
from .relationship_ledger import normalize_relationship_positive_stage_cap_key
from .relationship_policy import (
    normalize_relationship_stage_provider_routes,
    relationship_stage_policy_json,
)
from .segmented_message import normalize_component_order
from .model_routing import normalize_rule_configs, normalize_scope
from .wardrobe import (
    WARDROBE_MAX_ITEMS,
    WARDROBE_PROMPT_MAX_ITEMS,
    normalize_wardrobe_image_prompt,
    normalize_wardrobe_items,
    normalize_wardrobe_outfits,
    normalize_wardrobe_tendency,
)

from .page_api_settings_shared import (
    _SETTING_UNHANDLED,
)
from .page_api_settings_shared import _SETTING_UNHANDLED
from .page_api_settings_part04 import PageSettingNormalizerPart04Mixin
from .page_api_settings_part03 import PageSettingNormalizerPart03Mixin
from .page_api_settings_part02 import PageSettingNormalizerPart02Mixin
from .page_api_settings_part01 import PageSettingNormalizerPart01Mixin
class PageSettingNormalizerMixin(PageSettingNormalizerPart01Mixin, PageSettingNormalizerPart02Mixin, PageSettingNormalizerPart03Mixin, PageSettingNormalizerPart04Mixin):
    """Normalize panel setting payloads while preserving the page API contract."""