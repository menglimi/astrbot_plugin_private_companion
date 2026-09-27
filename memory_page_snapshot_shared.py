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


MEMORY_PAGE_OWNER_ID = "astrbot_plugin_private_companion"
MEMORY_PAGE_TARGET_ID = "astrbot_plugin_memory_companion"
MEMORY_PAGE_API_FAMILY = "companion.memory-page"
MEMORY_PAGE_API_VERSION = "companion.memory-page-api.v1"
MEMORY_PAGE_SNAPSHOT_VERSION = "companion.memory-page-snapshot.v1"
MEMORY_PAGE_PHOTO_VERSION = "companion.memory-page-photo.v1"

MEMORY_PAGE_SNAPSHOT_MAX_BYTES = 256 * 1024
MEMORY_PAGE_PHOTO_MAX_BYTES = 8 * 1024 * 1024
MEMORY_PAGE_PHOTO_BASE64_MAX_BYTES = 11_184_812
MEMORY_PAGE_PHOTO_RESULT_MAX_BYTES = 12 * 1024 * 1024
MEMORY_PAGE_PHOTO_REF_TTL_SECONDS = 900
MEMORY_PAGE_PHOTO_REF_MAX_ENTRIES = 256

_GENERATION_RE = re.compile(r"^[0-9a-f]{32}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_PHOTO_REF_RE = re.compile(r"^mphoto_([0-9a-f]{12})_([A-Za-z0-9_-]{22})$")
_REASON_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_COORDINATION_STATES = {
    "ready",
    "degraded",
    "local_only",
    "disabled",
    "inactive",
    "unavailable",
}
_DETAIL_STATUSES = {
    "planned",
    "ready",
    "observed",
    "degraded",
    "story_plan",
    "unknown",
}


class MemoryPageSnapshotError(RuntimeError):
    """Stable, body-free error raised by the Memory Page producer."""

    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class _PhotoBlob:
    root: Path
    parts: tuple[str, ...]
    device: int
    inode: int
    size: int
    mtime_ns: int
    mime_type: str
    sha256: str
    content: bytes


@dataclass(frozen=True, slots=True)
class _PhotoRegistration:
    generation: str
    root: Path
    parts: tuple[str, ...]
    device: int
    inode: int
    size: int
    mtime_ns: int
    mime_type: str
    sha256: str
    expires_at: float


def _text(value: Any, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    value = value.encode("utf-8", errors="ignore").decode("utf-8")
    value = _CONTROL_RE.sub(" ", value)
    return " ".join(value.split())[:limit]


def _date_text(value: Any) -> str:
    value = _text(value, 10)
    if not _DATE_RE.fullmatch(value):
        return ""
    try:
        return value if date.fromisoformat(value).isoformat() == value else ""
    except ValueError:
        return ""


def _timestamp(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    number = float(value)
    if not math.isfinite(number) or number < 0 or number > 10_000_000_000:
        return 0
    return int(number)


def _energy(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 0 <= value <= 100 else None


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _event_text(value: Any, limit: int = 180) -> str:
    if isinstance(value, str):
        return _text(value, limit)
    if not isinstance(value, dict):
        return ""
    for key in (
        "event",
        "summary",
        "topic",
        "title",
        "text",
        "status",
        "scene",
        "why",
        "motive",
        "impulse",
        "next_hint",
    ):
        result = _text(value.get(key), limit)
        if result:
            return result
    name = _text(value.get("name") or value.get("key"), 60)
    raw_scalar = value.get("value")
    scalar = ""
    if isinstance(raw_scalar, str):
        scalar = _text(raw_scalar, max(0, limit - len(name) - 2))
    elif isinstance(raw_scalar, bool):
        scalar = "true" if raw_scalar else "false"
    elif isinstance(raw_scalar, int):
        scalar = str(raw_scalar)
    if name and scalar:
        return _text(f"{name}: {scalar}", limit)
    return name


def _event_list(value: Any, *, limit: int = 5, item_limit: int = 180) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value[:64]:
        text = _event_text(item, item_limit)
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _valid_generation(value: Any) -> str:
    return value if isinstance(value, str) and _GENERATION_RE.fullmatch(value) else ""



class _memory_page_snapshotHostRef:
    """延迟引用宿主 memory_page_snapshot 模块，保证 patch 宿主全局名对全部域生效。"""

    def __getattr__(self, name: str):
        try:  # package import
            from . import memory_page_snapshot as _host_module
        except ImportError:  # direct test/import from the plugin directory
            import memory_page_snapshot as _host_module

        return getattr(_host_module, name)


_memory_page_snapshot_host = _memory_page_snapshotHostRef()
