from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, contextmanager
from copy import deepcopy
import functools
import hashlib
import hmac
import json
import secrets
import sys
import threading
import time
from types import ModuleType
from typing import Any, AsyncIterator, Awaitable, Callable, Iterator, Mapping

from .story_migration_contract import (
    MAX_SNAPSHOT_BYTES,
    STORY_MIGRATION_OWNER_ID,
    STORY_MIGRATION_SNAPSHOT_VERSION,
    StoryMigrationSnapshotError,
    build_story_migration_snapshot,
    canonical_story_snapshot_payload,
)


STORY_HANDOFF_TARGET_PLUGIN_ID = "astrbot_plugin_content_companion"
STORY_HANDOFF_LEASE_VERSION = "companion.story-handoff-lease.v1"
STORY_HANDOFF_DRAIN_TIMEOUT_SECONDS = 5.0
STORY_HANDOFF_LEASE_TTL_SECONDS = 60.0
_STORY_SNAPSHOT_ENVELOPE_KEYS = frozenset(
    {
        "version",
        "owner_id",
        "projects",
        "snapshot_id",
        "snapshot_sha256",
    }
)


class StoryAuthorityError(RuntimeError):
    """Stable, body-free error raised at the legacy Story authority boundary."""

    def __init__(self, code: str) -> None:
        self.code = str(code)
        super().__init__(self.code)


def _resolve_future(future: asyncio.Future[None]) -> None:
    if not future.done():
        future.set_result(None)


def _validate_pinned_snapshot_identity(snapshot: dict[str, Any]) -> None:
    """Bind the S1 wire identity to the exact bounded envelope being leased."""
    version = snapshot.get("version")
    owner_id = snapshot.get("owner_id")
    snapshot_id = snapshot.get("snapshot_id")
    snapshot_sha256 = snapshot.get("snapshot_sha256")
    if (
        set(snapshot) != _STORY_SNAPSHOT_ENVELOPE_KEYS
        or type(version) is not str
        or version != STORY_MIGRATION_SNAPSHOT_VERSION
        or type(owner_id) is not str
        or owner_id != STORY_MIGRATION_OWNER_ID
        or type(snapshot_id) is not str
        or type(snapshot_sha256) is not str
        or len(snapshot_sha256) != 64
        or any(
            character not in "0123456789abcdef"
            for character in snapshot_sha256
        )
        or snapshot_id != f"storysnap_{snapshot_sha256}"
    ):
        raise StoryAuthorityError("story_handoff_snapshot_identity_invalid")
    try:
        rebuilt = build_story_migration_snapshot(
            snapshot.get("projects"),
            owner_id=STORY_MIGRATION_OWNER_ID,
        )
        if rebuilt != snapshot:
            raise ValueError("snapshot is not the canonical S1 projection")
        canonical = canonical_story_snapshot_payload(snapshot)
        encoded = json.dumps(
            snapshot,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except StoryMigrationSnapshotError as exc:
        if exc.code == "story_snapshot_too_large":
            raise StoryAuthorityError("story_handoff_snapshot_too_large") from None
        raise StoryAuthorityError(
            "story_handoff_snapshot_identity_invalid"
        ) from None
    except Exception:
        raise StoryAuthorityError(
            "story_handoff_snapshot_identity_invalid"
        ) from None
    if len(canonical) > MAX_SNAPSHOT_BYTES or len(encoded) > MAX_SNAPSHOT_BYTES:
        raise StoryAuthorityError("story_handoff_snapshot_too_large")
    calculated = hashlib.sha256(canonical).hexdigest()
    if not hmac.compare_digest(calculated, snapshot_sha256):
        raise StoryAuthorityError("story_handoff_snapshot_identity_invalid")



class _story_authorityHostRef:
    """延迟引用宿主 story_authority 模块，保证 patch 宿主全局名对全部域生效。"""

    def __getattr__(self, name: str):
        from . import story_authority as _host_module

        return getattr(_host_module, name)


_story_authority_host = _story_authorityHostRef()
