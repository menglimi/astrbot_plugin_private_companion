from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass, replace
from typing import Any, Collection, Iterable, Literal, cast
try:  # package import
    from .photo_reference_catalog_part01 import (
        CATALOG_VERSION,
        CatalogLoadResult,
        CatalogValidationError,
        MAX_LIBRARY_REFERENCES,
        PhotoReference,
        PhotoReferenceKind,
        _CATEGORY_PRESETS,
        _FALSE_BOOLEAN_VALUES,
        _OUTFIT_ALIASES,
        _OUTFIT_PATTERNS,
        _ROLE_ALIASES,
        _SCENE_ALIASES,
        _SCENE_TOKENS,
        _TIME_ALIASES,
        _TIME_TOKENS,
        _TRUE_BOOLEAN_VALUES,
        _append_error,
        _as_values,
        _clean_text,
        _custom_value,
        _migration_bool,
        _normalize_bool,
        _normalize_outfit_category,
        _normalize_roles,
        _normalize_scene_categories,
        _normalize_time_categories,
        _serialize_reference,
        _strict_bool,
        _strict_custom_value,
        _strict_reference,
        _strict_roles,
        _strict_scenes,
        _strict_times,
    )
except ImportError:  # direct test/import from the plugin directory
    from photo_reference_catalog_part01 import (
        CATALOG_VERSION,
        CatalogLoadResult,
        CatalogValidationError,
        MAX_LIBRARY_REFERENCES,
        PhotoReference,
        PhotoReferenceKind,
        _CATEGORY_PRESETS,
        _FALSE_BOOLEAN_VALUES,
        _OUTFIT_ALIASES,
        _OUTFIT_PATTERNS,
        _ROLE_ALIASES,
        _SCENE_ALIASES,
        _SCENE_TOKENS,
        _TIME_ALIASES,
        _TIME_TOKENS,
        _TRUE_BOOLEAN_VALUES,
        _append_error,
        _as_values,
        _clean_text,
        _custom_value,
        _migration_bool,
        _normalize_bool,
        _normalize_outfit_category,
        _normalize_roles,
        _normalize_scene_categories,
        _normalize_time_categories,
        _serialize_reference,
        _strict_bool,
        _strict_custom_value,
        _strict_reference,
        _strict_roles,
        _strict_scenes,
        _strict_times,
    )
try:  # package import
    from .photo_reference_catalog_part02 import (
        _canonical_catalog_is_strictly_persistable,
        _catalog_items,
        _infer_outfit_category,
        _infer_reference_roles,
        _infer_scene_categories,
        _infer_time_categories,
        _legacy_item_parts,
        _legacy_library_items,
        _legacy_source,
        _load_canonical_references,
        _merge_persona_source_duplicates,
        _migrate_library_reference,
        _raw_catalog_has_content,
        _stable_library_id,
        _tolerant_catalog_reference,
        build_daily_outfit_reference,
        delete_reference,
        project_reference_candidate,
        validate_and_serialize,
    )
except ImportError:  # direct test/import from the plugin directory
    from photo_reference_catalog_part02 import (
        _canonical_catalog_is_strictly_persistable,
        _catalog_items,
        _infer_outfit_category,
        _infer_reference_roles,
        _infer_scene_categories,
        _infer_time_categories,
        _legacy_item_parts,
        _legacy_library_items,
        _legacy_source,
        _load_canonical_references,
        _merge_persona_source_duplicates,
        _migrate_library_reference,
        _raw_catalog_has_content,
        _stable_library_id,
        _tolerant_catalog_reference,
        build_daily_outfit_reference,
        delete_reference,
        project_reference_candidate,
        validate_and_serialize,
    )
try:  # package import
    from .photo_reference_catalog_part03 import (
        _migrate_legacy_catalog,
        add_reference,
        load_catalog,
    )
except ImportError:  # direct test/import from the plugin directory
    from photo_reference_catalog_part03 import (
        _migrate_legacy_catalog,
        add_reference,
        load_catalog,
    )


__all__ = [
    "CATALOG_VERSION",
    "MAX_LIBRARY_REFERENCES",
    "PhotoReferenceKind",
    "PhotoReference",
    "CatalogLoadResult",
    "CatalogValidationError",
    "load_catalog",
    "validate_and_serialize",
    "add_reference",
    "delete_reference",
    "build_daily_outfit_reference",
    "project_reference_candidate",
]
