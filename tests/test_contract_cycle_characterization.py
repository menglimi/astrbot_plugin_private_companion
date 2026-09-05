# -*- coding: utf-8 -*-
"""Characterize the public and compatibility surfaces around agenda/calendar."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
import inspect

import pytest

import agenda_contracts as agenda
import calendar_contracts as calendar
from companion.contracts import calendar_contracts as calendar_impl


AGENDA_PUBLIC_NAMES = {
    "ACTOR_TYPES", "ACTOR_TYPE_VALUES", "AGENDA_CONTRACT_VERSION",
    "AGENDA_SCHEMA_VERSION", "AGENDA_STATUSES", "AGENDA_VERSION",
    "AUTHORITY_KINDS", "AUTHORITY_KIND_VALUES", "AgendaContractError", "Any",
    "BOT_PERSONAL_CANONICAL_SCHEMA_VERSION", "CANONICAL_FIELDS",
    "CANONICAL_SCHEMA_VERSION", "COMMITMENT_LEVELS", "COMMITMENT_LEVEL_VALUES",
    "CONTENT_GRANULARITIES", "CONTENT_GRANULARITY_VALUES", "EPISTEMIC_STATUSES",
    "EPISTEMIC_STATUS_VALUES", "EVIDENCE_KINDS", "EVIDENCE_KIND_VALUES",
    "EVIDENCE_LEVELS", "FACT_ELIGIBILITIES", "FACT_ELIGIBILITY_VALUES",
    "Iterable", "MATERIALIZATION_STATES", "MATERIALIZATION_STATE_VALUES", "Real",
    "RejectedSource", "SCHEDULE_WINDOWS", "SOURCE_KINDS", "STATUS_ALIASES",
    "ScheduleAuthorityAdapter", "TEMPORAL_PHASES", "TEMPORAL_PHASE_VALUES",
    "TrustedScheduleRef", "VerificationResult", "WINDOW_SLUGS", "ZoneInfo",
    "agenda_entry_from_activity", "agenda_entry_from_plan", "annotations", "date",
    "datetime", "deepcopy", "derive_temporal_phase", "hashlib",
    "interval_overlaps_window", "json", "migrate_store", "normalize_authority_kind",
    "normalize_commitment_level", "normalize_content_granularity",
    "normalize_epistemic_status", "normalize_evidence_kind", "normalize_evidence_level",
    "normalize_fact_eligibility", "normalize_materialization_state",
    "normalize_observed_activity", "normalize_plan_item", "normalize_reconciliation",
    "normalize_source_kind", "normalize_status", "normalize_temporal_phase",
    "normalize_window", "normalize_window_snapshot", "parse_datetime", "stable_id",
    "time", "timedelta", "timezone", "timezone_or_default",
    "validate_structured_schedule_ref", "window_bounds", "window_for_datetime",
    "window_for_minutes", "window_for_plan_minutes",
}

CALENDAR_VISIBLE_NAMES = {
    "AgendaContractError", "Any", "CALENDAR_EVENT_KINDS", "CALENDAR_EVIDENCE_KINDS",
    "CALENDAR_LIFECYCLE_STATES", "CALENDAR_RECORD_KINDS", "CALENDAR_STATUSES",
    "CALENDAR_VERSION", "DEFAULT_PRIORITIES", "EXCEPTION_ACTIONS", "Iterable",
    "RECURRENCE_FREQUENCIES", "advance_calendar_lifecycle", "annotations",
    "calendar_candidate_from_record", "calendar_lifecycle_summary",
    "calendar_records_from_store", "date", "datetime", "deepcopy",
    "detect_calendar_conflicts", "expand_calendar_records", "merge_calendar_evidence",
    "migrate_calendar_store", "normalize_calendar_candidate",
    "normalize_calendar_evidence", "normalize_calendar_evidence_chain",
    "normalize_calendar_record", "normalize_calendar_records",
    "resolve_calendar_snapshot", "resolve_calendar_timeline", "stable_id", "time",
    "timedelta", "timezone_or_default",
}

CALENDAR_EXPLICIT_EXPORTS = [
    "CALENDAR_VERSION", "CALENDAR_RECORD_KINDS", "CALENDAR_EVENT_KINDS",
    "CALENDAR_STATUSES", "CALENDAR_LIFECYCLE_STATES", "CALENDAR_EVIDENCE_KINDS",
    "RECURRENCE_FREQUENCIES", "EXCEPTION_ACTIONS", "normalize_calendar_evidence",
    "normalize_calendar_evidence_chain", "merge_calendar_evidence",
    "normalize_calendar_record", "normalize_calendar_records",
    "calendar_candidate_from_record", "normalize_calendar_candidate",
    "advance_calendar_lifecycle", "calendar_lifecycle_summary",
    "expand_calendar_records", "detect_calendar_conflicts", "resolve_calendar_snapshot",
    "resolve_calendar_timeline", "calendar_records_from_store", "migrate_calendar_store",
]


def public_names(module: object) -> set[str]:
    return {name for name in dir(module) if not name.startswith("_")}


def test_agenda_public_surface_and_key_signatures_are_stable() -> None:
    assert public_names(agenda) == AGENDA_PUBLIC_NAMES
    assert str(inspect.signature(agenda.stable_id)) == "(prefix: 'str', *parts: 'Any') -> 'str'"
    assert str(inspect.signature(agenda.timezone_or_default)) == "(timezone_name: 'Any' = 'Asia/Shanghai') -> 'ZoneInfo'"
    assert str(inspect.signature(agenda.migrate_store)) == "(data: 'dict[str, Any]') -> 'tuple[dict[str, Any], bool]'"


def test_calendar_compatibility_surface_forwards_every_visible_symbol() -> None:
    assert public_names(calendar) == CALENDAR_VISIBLE_NAMES
    assert calendar_impl.__all__ == CALENDAR_EXPLICIT_EXPORTS
    for name in CALENDAR_VISIBLE_NAMES:
        assert getattr(calendar, name) is getattr(calendar_impl, name)


def test_shared_primitives_keep_identity_exception_and_value_semantics() -> None:
    assert calendar.AgendaContractError is agenda.AgendaContractError
    assert calendar.stable_id is agenda.stable_id
    assert calendar.timezone_or_default is agenda.timezone_or_default

    parts_a = ({"b": 2, "a": [1, {"x", "y"}]}, date(2026, 8, 31), time(7, 5))
    parts_b = ({"a": [1, {"y", "x"}], "b": 2}, date(2026, 8, 31), time(7, 5))
    assert agenda.stable_id("  event  ", *parts_a) == "event-06d9002a65dbb69203bf"
    assert agenda.stable_id("  event  ", *parts_b) == "event-06d9002a65dbb69203bf"
    assert agenda.stable_id("", "x").startswith("agenda-")

    shanghai = agenda.timezone_or_default("Asia/Shanghai")
    assert datetime(2026, 1, 1, tzinfo=shanghai).utcoffset() == timedelta(hours=8)
    fallback = agenda.timezone_or_default("Not/A_Real_Zone")
    assert datetime(2026, 1, 1, tzinfo=fallback).utcoffset() == timedelta(hours=8)

    with pytest.raises(agenda.AgendaContractError, match="store must be an object"):
        agenda.migrate_store([])  # type: ignore[arg-type]
    with pytest.raises(calendar.AgendaContractError):
        calendar.normalize_calendar_record("bad")  # type: ignore[arg-type]


def test_agenda_migration_still_delegates_to_calendar_without_format_changes() -> None:
    source = {
        "calendar_records": [
            {"kind": "event", "calendar_id": "exam", "title": "Exam", "date": "2026-09-01"}
        ]
    }
    migrated, changed = agenda.migrate_store(source)
    assert migrated is source
    assert changed is True
    assert migrated["calendar_records"] == []
    assert migrated["calendar_events"][0]["calendar_id"] == "exam"
    assert migrated["agenda_version"] == agenda.AGENDA_VERSION
    assert migrated["agenda_contract_version"] == agenda.CANONICAL_SCHEMA_VERSION
