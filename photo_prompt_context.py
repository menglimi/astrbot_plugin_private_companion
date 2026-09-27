from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from . import photo_wardrobe_decision as _wardrobe_rules
from .conversation_prompt_section import (
    PhotoPromptContent,
    PromptRenderMode,
    PromptSection,
    exact_text,
    prompt_section,
    render_prompt_sections,
)
from .photo_prompt_context_part01 import (
    ResolvedPhotoPromptContext,
    _CLIP_BOUNDARY_CHARS,
    _CLIP_BOUNDARY_PATTERN,
    _COMPACTED_MARKER,
    _DAILY_OUTFIT_PATTERN,
    _EDIT_WORKFLOWS,
    _EMBEDDED_NEGATION_PATTERN,
    _GENERIC_WARDROBE_PATTERN,
    _MISSING,
    _NEGATIVE_LABEL_MAX_WORDS,
    _NEGATIVE_TO_POSITIVE_TRANSITION_PATTERN,
    _OUTFIT_ROTATION_NEGATIVE_PATTERN,
    _RECENT_OUTFIT_CONTINUITY_PATTERN,
    _SANITIZER_VERSION,
    _SECTION_SOURCES,
    _SELFIE_WORKFLOWS,
    _SPECIFIC_OUTFIT_ITEM_PATTERN,
    _audit,
    _categories,
    _clip,
    _clip_prefix_at_boundary,
    _clip_tail_at_boundary,
    _compatible,
    _conflicts_in_section,
    _excluded_outfit_terms,
    _generic_wardrobe_is_compatible,
    _negative_conflict,
    _photo_content,
    _photo_prompt_section_payload,
    _positive_conflict,
    _preview,
    _replace_photo_prompt_section,
    _sanitize_field,
    _section_conflict_sanitization_enabled,
    _specific_outfit_items,
    _split_clauses,
    _split_embedded_polarity,
    _validated_photo_prompt_section,
    _value,
)
from .photo_prompt_context_part02 import (
    _LOCAL_VISUAL_PROMPT_SOURCES,
    _apply_budget,
    _assemble,
    _budget_sections,
    _join_field,
    _local_visual_section_text,
    _reference_roles,
    _render_photo_wire,
    _sanitize_sections,
    _scan_residual_conflicts,
    _strip_compaction_markers,
    compile_local_photo_prompt,
)
from .photo_prompt_context_part03 import (
    _remove_reference_dependent_context,
    _sanitize_reference,
    resolve_photo_prompt_context,
)


__all__ = [
    "ResolvedPhotoPromptContext",
    "compile_local_photo_prompt",
    "resolve_photo_prompt_context",
]
