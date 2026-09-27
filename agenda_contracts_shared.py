# -*- coding: utf-8 -*-
"""agenda_contracts 域的跨模块共享件（import 绑定 + 模块级常量）。

由 tmp/split4/mod_split.py 从 agenda_contracts.py 机械抽取：
11 条 import 语句 + 29 个模块级常量，逐字节原样。
宿主 agenda_contracts.py 与各 agenda_contracts_partNN.py 均从本模块 import，
本模块不 import 任何同族模块（叶子模块，杜绝循环 import）。
"""

from __future__ import annotations

from copy import deepcopy

from datetime import date, datetime, time, timedelta, timezone

import hashlib

import json

from numbers import Real

from typing import Any, Iterable

from zoneinfo import ZoneInfo

try:  # package import
    from .companion.contracts._agenda_primitives import (
        AgendaContractError,
        stable_id,
        timezone_or_default,
    )
except ImportError:  # direct test/import from the plugin directory
    from companion.contracts._agenda_primitives import (
        AgendaContractError,
        stable_id,
        timezone_or_default,
    )

try:  # package import
    from .bot_personal_contract import (
        BOT_PERSONAL_CANONICAL_SCHEMA_VERSION,
        SCHEDULE_WINDOWS as _CONTRACT_WINDOWS,
        WINDOW_SLUGS as _CONTRACT_WINDOW_SLUGS,
        window_for_minutes as _contract_window_for_minutes,
    )
except ImportError:  # direct test/import from the plugin directory
    from bot_personal_contract import (
        BOT_PERSONAL_CANONICAL_SCHEMA_VERSION,
        SCHEDULE_WINDOWS as _CONTRACT_WINDOWS,
        WINDOW_SLUGS as _CONTRACT_WINDOW_SLUGS,
        window_for_minutes as _contract_window_for_minutes,
    )

# The adapter lives in a small standalone module so callers that only need
# source verification do not import the full agenda normalizer.  Re-export it
# here as part of the canonical C3 contract surface.
try:
    from .schedule_authority import (
        RejectedSource,
        ScheduleAuthorityAdapter,
        TrustedScheduleRef,
        VerificationResult,
        validate_structured_schedule_ref,
    )
except ImportError:  # direct test/import from the plugin directory
    from schedule_authority import (
        RejectedSource,
        ScheduleAuthorityAdapter,
        TrustedScheduleRef,
        VerificationResult,
        validate_structured_schedule_ref,
    )

AGENDA_VERSION = 1

# ``agenda_version`` is the storage version used by the existing C3 store.  The
# canonical fields below are additive so old readers can continue to consume
# the payload.  Keep a separate semantic version for capability checks rather
# than changing the old storage marker underneath them.
CANONICAL_SCHEMA_VERSION = BOT_PERSONAL_CANONICAL_SCHEMA_VERSION

AGENDA_CONTRACT_VERSION = CANONICAL_SCHEMA_VERSION

AGENDA_SCHEMA_VERSION = CANONICAL_SCHEMA_VERSION

SCHEDULE_WINDOWS = tuple(_CONTRACT_WINDOWS)

WINDOW_SLUGS = tuple(_CONTRACT_WINDOW_SLUGS)

SOURCE_KINDS = {"planned", "observed", "projection", "reconciled"}

EVIDENCE_LEVELS = {"L0", "L1", "L2", "L3", "L4", "L5"}

TEMPORAL_PHASES = {"future", "current", "past"}

EVIDENCE_KINDS = {
    "none",
    "interaction",
    "self_state_commit",
    "tool_action",
    "external_record",
    "external_commitment",
}

AUTHORITY_KINDS = {
    "calendar",
    "timetable",
    "roster",
    "appointment",
    "user_confirmation",
    "routine",
    "persona",
    "state",
    "llm",
}

COMMITMENT_LEVELS = {"confirmed", "routine", "tentative"}

EPISTEMIC_STATUSES = {"asserted", "inferred", "observed"}

CONTENT_GRANULARITIES = {"commitment", "intent", "candidate", "scene"}

MATERIALIZATION_STATES = {"none", "candidate", "active", "rejected", "expired"}

FACT_ELIGIBILITIES = {
    "none",
    "schedule_commitment",
    "current_internal",
    "current_observed",
    "history_observed",
}

ACTOR_TYPES = {"bot", "interlocutor_user", "external_party", "system"}

# Explicit ``*_VALUES`` aliases make capability probing straightforward while
# keeping the original set-style constants readable to existing consumers.
TEMPORAL_PHASE_VALUES = TEMPORAL_PHASES

EVIDENCE_KIND_VALUES = EVIDENCE_KINDS

AUTHORITY_KIND_VALUES = AUTHORITY_KINDS

COMMITMENT_LEVEL_VALUES = COMMITMENT_LEVELS

EPISTEMIC_STATUS_VALUES = EPISTEMIC_STATUSES

CONTENT_GRANULARITY_VALUES = CONTENT_GRANULARITIES

MATERIALIZATION_STATE_VALUES = MATERIALIZATION_STATES

FACT_ELIGIBILITY_VALUES = FACT_ELIGIBILITIES

ACTOR_TYPE_VALUES = ACTOR_TYPES

AGENDA_STATUSES = {
    "planned",
    "active",
    "completed",
    "partially_completed",
    "overridden",
    "reconciled",
    "deferred",
    "cancelled",
    "unknown",
}

STATUS_ALIASES = {
    "in_progress": "active",
    "in-progress": "active",
    "ongoing": "active",
    "done": "completed",
    "complete": "completed",
    "changed": "overridden",
    "rescheduled": "overridden",
    "postponed": "deferred",
    "canceled": "cancelled",
    "revoked": "cancelled",
}

# Fields are deliberately kept as independent dimensions.  This tuple is
# useful to consumers that need to copy the canonical contract without
# silently dropping a newly added field.
CANONICAL_FIELDS = (
    "source_kind",
    "status",
    "temporal_phase",
    "evidence_kind",
    "evidence_level",
    "canonical_evidence_level",
    "archive_evidence_level",
    "evidence_level_mapping",
    "authority_kind",
    "commitment_level",
    "epistemic_status",
    "content_granularity",
    "materialization_state",
    "fact_eligibility",
    "confidence",
    "source_refs",
    "runtime_origin_refs",
    "expires_at",
    "actor_type",
    "subject_actor_id",
    "object_actor_id",
    "source_actor_id",
    "target_user_id",
    "participant_roles",
    "decision_trace",
    "canonical_schema_version",
)
