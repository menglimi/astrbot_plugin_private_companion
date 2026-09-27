from __future__ import annotations

import asyncio
import base64
from collections import OrderedDict
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import secrets
import stat
import threading
import time
from typing import Any, Callable


try:  # package import
    from .memory_page_snapshot_shared import (
        MEMORY_PAGE_API_FAMILY,
        MEMORY_PAGE_API_VERSION,
        MEMORY_PAGE_OWNER_ID,
        MEMORY_PAGE_PHOTO_BASE64_MAX_BYTES,
        MEMORY_PAGE_PHOTO_MAX_BYTES,
        MEMORY_PAGE_PHOTO_REF_MAX_ENTRIES,
        MEMORY_PAGE_PHOTO_REF_TTL_SECONDS,
        MEMORY_PAGE_PHOTO_RESULT_MAX_BYTES,
        MEMORY_PAGE_PHOTO_VERSION,
        MEMORY_PAGE_SNAPSHOT_MAX_BYTES,
        MEMORY_PAGE_SNAPSHOT_VERSION,
        MEMORY_PAGE_TARGET_ID,
        _CONTROL_RE,
        _COORDINATION_STATES,
        _DATE_RE,
        _DETAIL_STATUSES,
        _GENERATION_RE,
        _PHOTO_REF_RE,
        _REASON_RE,
        _canonical_bytes,
        _date_text,
        _energy,
        _event_list,
        _event_text,
        _text,
        _timestamp,
        _valid_generation,
    )
except ImportError:  # direct test/import from the plugin directory
    from memory_page_snapshot_shared import (
        MEMORY_PAGE_API_FAMILY,
        MEMORY_PAGE_API_VERSION,
        MEMORY_PAGE_OWNER_ID,
        MEMORY_PAGE_PHOTO_BASE64_MAX_BYTES,
        MEMORY_PAGE_PHOTO_MAX_BYTES,
        MEMORY_PAGE_PHOTO_REF_MAX_ENTRIES,
        MEMORY_PAGE_PHOTO_REF_TTL_SECONDS,
        MEMORY_PAGE_PHOTO_RESULT_MAX_BYTES,
        MEMORY_PAGE_PHOTO_VERSION,
        MEMORY_PAGE_SNAPSHOT_MAX_BYTES,
        MEMORY_PAGE_SNAPSHOT_VERSION,
        MEMORY_PAGE_TARGET_ID,
        _CONTROL_RE,
        _COORDINATION_STATES,
        _DATE_RE,
        _DETAIL_STATUSES,
        _GENERATION_RE,
        _PHOTO_REF_RE,
        _REASON_RE,
        _canonical_bytes,
        _date_text,
        _energy,
        _event_list,
        _event_text,
        _text,
        _timestamp,
        _valid_generation,
    )
try:  # package import
    from .memory_page_snapshot_shared import MEMORY_PAGE_API_FAMILY
except ImportError:  # direct test/import from the plugin directory
    from memory_page_snapshot_shared import MEMORY_PAGE_API_FAMILY
try:  # package import
    from .memory_page_snapshot_part03 import MemoryPageSnapshotServicePart03Mixin
except ImportError:  # direct test/import from the plugin directory
    from memory_page_snapshot_part03 import MemoryPageSnapshotServicePart03Mixin
try:  # package import
    from .memory_page_snapshot_part02 import MemoryPageSnapshotServicePart02Mixin
except ImportError:  # direct test/import from the plugin directory
    from memory_page_snapshot_part02 import MemoryPageSnapshotServicePart02Mixin
try:  # package import
    from .memory_page_snapshot_part01 import MemoryPageSnapshotServicePart01Mixin
except ImportError:  # direct test/import from the plugin directory
    from memory_page_snapshot_part01 import MemoryPageSnapshotServicePart01Mixin
try:  # package import
    from .memory_page_snapshot_shared import _PhotoRegistration
except ImportError:  # direct test/import from the plugin directory
    from memory_page_snapshot_shared import _PhotoRegistration
# 门面保留 MemoryPageSnapshotError（tests 从宿主导入该名字）。
try:  # package import
    from .memory_page_snapshot_shared import MemoryPageSnapshotError
except ImportError:  # direct test/import from the plugin directory
    from memory_page_snapshot_shared import MemoryPageSnapshotError
class MemoryPageSnapshotService(MemoryPageSnapshotServicePart01Mixin, MemoryPageSnapshotServicePart02Mixin, MemoryPageSnapshotServicePart03Mixin):
    """Generation-bound, read-only Memory Page snapshot and photo producer."""

    __slots__ = (
        "_clock",
        "_owner",
        "_photo_refs",
        "_photo_refs_lock",
        "_secret",
    )

    def __init__(
        self,
        owner: Any,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._owner = owner
        self._clock = clock
        self._secret = secrets.token_bytes(32)
        self._photo_refs: OrderedDict[str, _PhotoRegistration] = OrderedDict()
        self._photo_refs_lock = threading.RLock()
