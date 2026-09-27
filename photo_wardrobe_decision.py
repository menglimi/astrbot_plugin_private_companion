from __future__ import annotations

import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass, replace
from typing import Any
try:  # package import
    from .photo_wardrobe_decision_part01 import (
        DECISION_VERSION,
        PhotoWardrobeDecision,
        PhotoWardrobeIntent,
        _CATEGORY_LABELS,
        _CATEGORY_PRESETS,
        _DAILY_OUTFIT_PATTERN,
        _EDIT_WORKFLOWS,
        _OUTFIT_PATTERNS,
        _PRESET_CATEGORIES,
        _SELFIE_WORKFLOWS,
        _ambient_location_categories,
        _clean_text,
        _contains_specific_outfit_text,
        _current_user_request_parts,
        _daily_outfit_categories,
        _location_categories,
        _negative_clause_content,
        _outfit_category_matches,
        _preset_category,
        _scene_without_daily_outfit_details,
        _semantic_prompt_parts,
        analyze_photo_wardrobe,
        merge_photo_wardrobe_continuity,
    )
except ImportError:  # direct test/import from the plugin directory
    from photo_wardrobe_decision_part01 import (
        DECISION_VERSION,
        PhotoWardrobeDecision,
        PhotoWardrobeIntent,
        _CATEGORY_LABELS,
        _CATEGORY_PRESETS,
        _DAILY_OUTFIT_PATTERN,
        _EDIT_WORKFLOWS,
        _OUTFIT_PATTERNS,
        _PRESET_CATEGORIES,
        _SELFIE_WORKFLOWS,
        _ambient_location_categories,
        _clean_text,
        _contains_specific_outfit_text,
        _current_user_request_parts,
        _daily_outfit_categories,
        _location_categories,
        _negative_clause_content,
        _outfit_category_matches,
        _preset_category,
        _scene_without_daily_outfit_details,
        _semantic_prompt_parts,
        analyze_photo_wardrobe,
        merge_photo_wardrobe_continuity,
    )
try:  # package import
    from .photo_wardrobe_decision_part02 import (
        _automatic_presets,
        _explicit_mirror_request,
        _explicit_prompt_preset,
        _location_categories_conflict,
        _outfit_label,
        _prompt_without_generated_daily_outfit_continuity,
        _scene_without_ambient_location_fields,
        _selected_presets,
        _validated_reference_preferred_preset,
    )
except ImportError:  # direct test/import from the plugin directory
    from photo_wardrobe_decision_part02 import (
        _automatic_presets,
        _explicit_mirror_request,
        _explicit_prompt_preset,
        _location_categories_conflict,
        _outfit_label,
        _prompt_without_generated_daily_outfit_continuity,
        _scene_without_ambient_location_fields,
        _selected_presets,
        _validated_reference_preferred_preset,
    )
try:  # package import
    from .photo_wardrobe_decision_part03 import (
        resolve_photo_wardrobe_decision,
    )
except ImportError:  # direct test/import from the plugin directory
    from photo_wardrobe_decision_part03 import (
        resolve_photo_wardrobe_decision,
    )


__all__ = [
    "PhotoWardrobeIntent",
    "PhotoWardrobeDecision",
    "analyze_photo_wardrobe",
    "merge_photo_wardrobe_continuity",
    "resolve_photo_wardrobe_decision",
]
