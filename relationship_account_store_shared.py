"""REQ-041 revisioned Shadow relationship account store.

One account belongs to one verified identity.  Conversation namespaces are
authorization and provenance boundaries; they never become a second account.
The store is deliberately disconnected from the live message path until the
Shadow reconciliation gate is accepted.
"""
from __future__ import annotations

from contextlib import contextmanager
from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any, Iterator

try:
    from .identity_namespace import AssurancePolicy, NamespaceContext
    from .relationship_ledger import (
        apply_relationship_event,
        normalize_relationship_mode,
        normalize_relationship_positive_stage_cap_key,
    )
    from .relationship_policy import relationship_stage_for_score
    from .relationship_event_policy import validate_group_interaction_proof
except ImportError:  # pragma: no cover - direct-module test compatibility
    from identity_namespace import AssurancePolicy, NamespaceContext
    from relationship_ledger import (
        apply_relationship_event,
        normalize_relationship_mode,
        normalize_relationship_positive_stage_cap_key,
    )
    from relationship_policy import relationship_stage_for_score
    from relationship_event_policy import validate_group_interaction_proof


ACCOUNT_ROLES = frozenset({"friend", "owner"})
ACCOUNT_MODES = frozenset({"normal", "owner_exclusive"})
ADMIN_ACTORS = frozenset({"administrator", "migration"})
EVENT_ACTORS = frozenset({"private_pipeline", "group_pipeline", "administrator", "migration", "system"})
PRIVATE_EVENT_REASONS = frozenset({
    "boundary_violation",
    "care_feedback",
    "food_feedback",
    "friendly_exchange",
    "helpful_reply",
    "inbound",
    "interaction_pressure",
    "interaction_warmth",
    "intimate_interaction",
    "natural_decay",
    "playful_interaction",
    "proactive_reply",
    "relationship_violation",
    "relationship_violation_clawback",
    "relationship_violation_recovery",
    "schedule_adjustment",
    "support",
    "warmth",
})
GROUP_DIRECT_REASON = "direct_group_interaction"
GROUP_ZERO_REASONS = PRIVATE_EVENT_REASONS | frozenset({"group_inbound", GROUP_DIRECT_REASON})


class RelationshipStoreError(RuntimeError):
    pass


class RelationshipAccessDenied(RelationshipStoreError):
    pass


class RelationshipConflict(RelationshipStoreError):
    pass


class RelationshipNotFound(RelationshipStoreError):
    pass


@dataclass(frozen=True, slots=True)
class RelationshipEventResult:
    event_id: str
    identity_id: str
    code: str
    applied: bool
    requested_delta: int
    weighted_delta: int
    applied_delta: int
    score: int
    account_revision: int
    source_kind: str


@dataclass(frozen=True, slots=True)
class GroupAffinityAdmissionResult:
    event_id: str
    identity_id: str
    code: str
    requested_delta: int
    weighted_delta: int
    admitted_delta: int
    source_scope: str


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _token(value: Any, *, limit: int = 128) -> str:
    if not isinstance(value, str):
        return ""
    result = value.strip()
    if not result or len(result) > limit or any(ord(char) < 32 for char in result):
        return ""
    return result


def _integer(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number


def _weighted_integer(delta: int, weight: float) -> int:
    value = abs(delta) * weight
    rounded = int(math.floor(value + 0.5))
    return rounded if delta >= 0 else -rounded


def _source_scope(context: NamespaceContext) -> str:
    """Return a stable redacted namespace key, independent of policy/epoch."""
    source = {"kind": context.kind, "identity_id": context.identity_id, "group_id": context.group_id}
    digest = hashlib.sha256(_canonical(source).encode("utf-8")).hexdigest()[:24]
    return f"{context.kind}:{digest}"



class _relationship_account_storeHostRef:
    """延迟引用宿主 relationship_account_store 模块，保证 patch 宿主全局名对全部域生效。"""

    def __getattr__(self, name: str):
        from . import relationship_account_store as _host_module

        return getattr(_host_module, name)


_relationship_account_store_host = _relationship_account_storeHostRef()
