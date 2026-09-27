# -*- coding: utf-8 -*-
from __future__ import annotations

import base64
import hashlib
import io
import json
import mimetypes
import os
import re
import statistics
import threading
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any, Iterable

from .helpers import _safe_float, _safe_int, _single_line
from .reaction_asset_index import ReactionAssetLookupIndex
from .reaction_asset_usage import ReactionAssetUsageStore


from .reaction_asset_library_shared import (
    ANALYSIS_STATUSES,
    CATALOG_VERSION,
    LOOKUP_CACHE_TTL_SECONDS,
    MAX_BATCH_BYTES,
    MAX_EMBEDDING_DIMENSION,
    MAX_SINGLE_FILE_BYTES,
    MAX_ZIP_MEMBERS,
    MIME_BY_EXTENSION,
    SUPPORTED_EXTENSIONS,
    _SEMANTIC_MATCH_CLUSTERS,
    _SEMANTIC_NEGATION_PATTERN,
    _image_signature_matches,
    _query_list,
    _safe_bool,
    _safe_filename,
    _semantic_features,
    _text_list,
)
from .reaction_asset_library_shared import ANALYSIS_STATUSES
from .reaction_asset_library_part03 import ReactionAssetLibraryPart03Mixin
from .reaction_asset_library_part02 import ReactionAssetLibraryPart02Mixin
from .reaction_asset_library_part01 import ReactionAssetLibraryPart01Mixin
class ReactionAssetLibrary(ReactionAssetLibraryPart01Mixin, ReactionAssetLibraryPart02Mixin, ReactionAssetLibraryPart03Mixin):
    """Small, self-contained reaction-image catalog owned by this plugin."""

    def __init__(self, data_dir: str | os.PathLike[str]) -> None:
        self.root = (Path(data_dir) / "reaction_expression_library").resolve()
        self.images_dir = self.root / "images"
        self.catalog_path = self.root / "catalog.json"
        self._usage = ReactionAssetUsageStore(self.root)
        self._lock = threading.RLock()
        self._lookup_index = ReactionAssetLookupIndex(LOOKUP_CACHE_TTL_SECONDS)
        self._selection_revision = 0
        # Memory cache for catalog: avoids re-parsing catalog.json on every
        # read operation. Invalidated on file identity changes or after _save().
        self._cached_catalog: dict[str, Any] | None = None
        self._cached_catalog_stamp: tuple[int, int, int, int] | None = None
        # Lightweight flag for has_enabled_assets(), updated in _load(), _save() and
        # lookup_revision() so the hot path avoids a full catalog walk.
        self._cached_has_enabled_assets: bool = False
        # Summary cache: invalidated alongside the catalog cache.
        self._cached_summary: dict[str, Any] | None = None
        self.images_dir.mkdir(parents=True, exist_ok=True)

    @property
    def _lookup_revision_checked_at(self) -> float:
        """Compatibility alias retained for existing diagnostics/tests."""
        return self._lookup_index.checked_at

    @_lookup_revision_checked_at.setter
    def _lookup_revision_checked_at(self, value: float) -> None:
        self._lookup_index.checked_at = float(value)


def get_reaction_asset_library(plugin: Any) -> ReactionAssetLibrary | None:
    data_dir = str(getattr(plugin, "data_dir", "") or "").strip()
    if not data_dir:
        return None
    current = getattr(plugin, "_reaction_asset_library_instance", None)
    if isinstance(current, ReactionAssetLibrary):
        return current
    legacy = getattr(plugin, "_reaction_asset_library", None)
    if isinstance(legacy, ReactionAssetLibrary):
        setattr(plugin, "_reaction_asset_library_instance", legacy)
        return legacy
    library = ReactionAssetLibrary(data_dir)
    setattr(plugin, "_reaction_asset_library_instance", library)
    return library
