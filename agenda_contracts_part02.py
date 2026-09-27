# -*- coding: utf-8 -*-
"""agenda_contracts_part02：从 agenda_contracts.py 机械抽取的模块级函数。

由 tmp/split4/mod_split.py 生成（7 个函数 / 498 行）。函数体逐字节原样，仅位置变化。
对外经由宿主 agenda_contracts.py re-export，接口不变。
"""
from __future__ import annotations

try:  # package import
    from .agenda_contracts_part01 import (
        _actor_fields,
        _certainty,
        _enum,
        _evidence_level_mapping,
        _float_confidence,
        _items,
        _list,
        _normalize_participant_roles,
        _normalize_status,
        _now_iso,
        _text,
        _trace,
        _trace_list,
        _version,
        derive_temporal_phase,
        normalize_authority_kind,
        normalize_commitment_level,
        normalize_content_granularity,
        normalize_epistemic_status,
        normalize_evidence_kind,
        normalize_evidence_level,
        normalize_fact_eligibility,
        normalize_materialization_state,
        normalize_temporal_phase,
        normalize_window,
    )
except ImportError:  # direct test/import from the plugin directory
    from agenda_contracts_part01 import (
        _actor_fields,
        _certainty,
        _enum,
        _evidence_level_mapping,
        _float_confidence,
        _items,
        _list,
        _normalize_participant_roles,
        _normalize_status,
        _now_iso,
        _text,
        _trace,
        _trace_list,
        _version,
        derive_temporal_phase,
        normalize_authority_kind,
        normalize_commitment_level,
        normalize_content_granularity,
        normalize_epistemic_status,
        normalize_evidence_kind,
        normalize_evidence_level,
        normalize_fact_eligibility,
        normalize_materialization_state,
        normalize_temporal_phase,
        normalize_window,
    )
try:  # package import
    from .agenda_contracts_shared import (
        ACTOR_TYPES,
        AGENDA_VERSION,
        AgendaContractError,
        Any,
        CANONICAL_SCHEMA_VERSION,
        Iterable,
        datetime,
        deepcopy,
        stable_id,
        validate_structured_schedule_ref,
    )
except ImportError:  # direct test/import from the plugin directory
    from agenda_contracts_shared import (
        ACTOR_TYPES,
        AGENDA_VERSION,
        AgendaContractError,
        Any,
        CANONICAL_SCHEMA_VERSION,
        Iterable,
        datetime,
        deepcopy,
        stable_id,
        validate_structured_schedule_ref,
    )


def normalize_plan_item(
    raw: dict[str, Any],
    *,
    plan_id: str = "",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Normalize an ordinary plan through the C3 write gate.

    Plans describe intent only.  A caller cannot promote one to an observed or
    completed fact by putting ``status``, ``source_kind`` or evidence fields in
    JSON.  Reconciliation/authority adapters may write a later status while
    retaining this normalized plan as their input.
    """

    if not isinstance(raw, dict):
        raise AgendaContractError("plan item must be an object")
    title = _text(raw.get("title") or raw.get("activity") or raw.get("description"), 240)
    if not title:
        raise AgendaContractError("plan item requires a title")
    result = deepcopy(raw)
    trace = _trace_list(raw.get("decision_trace"))
    raw_status = _text(raw.get("status") or raw.get("lifecycle_status"), 32).lower()
    raw_source_kind = _text(raw.get("source_kind"), 32).lower()
    raw_evidence_kind = _text(raw.get("evidence_kind"), 48).lower()
    if raw_source_kind and raw_source_kind != "planned":
        trace.append(_trace("normalizer.forced_source_kind", "ordinary plan source_kind is always planned", requested=raw_source_kind))
        result["legacy_source_kind"] = raw_source_kind
    if raw_status and _normalize_status(raw_status, "planned") != "planned":
        trace.append(_trace("normalizer.forced_status", "ordinary plan status is always planned", requested=raw_status))
        result["legacy_status"] = raw_status
    if raw.get("lifecycle_status") not in (None, ""):
        result["legacy_lifecycle_status"] = _text(raw.get("lifecycle_status"), 32)
        mapped = _normalize_status(raw.get("lifecycle_status"), "planned")
        if mapped != "planned":
            trace.append(_trace("normalizer.lifecycle_status_compat", "legacy lifecycle_status retained for compatibility", requested=mapped))
    basis = _list(raw.get("basis"))
    source_refs = _list(raw.get("source_refs"))
    if basis and not source_refs:
        trace.append(_trace("normalizer.basis_not_source_refs", "basis is generation input and cannot become evidence references", basis=basis))
    elif basis:
        trace.append(_trace("normalizer.basis_ignored", "basis retained as legacy input; source_refs remain explicit only", basis=basis))

    actor = _actor_fields(raw)
    if not actor["subject_actor_id"]:
        trace.append(_trace("normalizer.missing_subject", "plan has no subject_actor_id and is diagnostic-only until bound"))
    epistemic_default = "asserted" if actor.get("actor_type") == "interlocutor_user" else "inferred"

    requested_authority = normalize_authority_kind(raw.get("authority_kind"), "llm")
    external_authorities = {"calendar", "timetable", "roster", "appointment", "user_confirmation"}
    explicit_trust = bool(
        raw.get("source_refs_trusted")
        or raw.get("trusted_source_refs")
        or raw.get("trusted_source")
        or raw.get("authority_verified")
        or raw.get("source_adapter")
    )
    schedule_ref_status = "not_applicable"
    schedule_ref_reason = ""
    if requested_authority in external_authorities:
        schedule_ref_status, schedule_ref_reason = validate_structured_schedule_ref(
            raw.get("schedule_ref"),
            source_refs=source_refs,
            expected_authority=requested_authority,
            expected_subject=actor.get("subject_actor_id"),
            expected_target=raw.get("target_user_id"),
            now=now,
        )
        trusted_refs = schedule_ref_status == "valid"
    else:
        trusted_refs = explicit_trust
    authority = requested_authority
    if requested_authority in external_authorities and not trusted_refs:
        authority = "llm"
        result["legacy_authority_kind"] = requested_authority
        trace.append(
            _trace(
                "normalizer.untrusted_authority",
                "external authority requires an adapter-issued source reference",
                requested=requested_authority,
            )
        )
    requested_commitment = normalize_commitment_level(raw.get("commitment_level"), "tentative")
    commitment = requested_commitment
    if authority in external_authorities and trusted_refs and source_refs:
        commitment = "confirmed"
    elif authority == "routine":
        commitment = "routine"
    elif authority not in external_authorities and commitment == "confirmed":
        commitment = "tentative"
        trace.append(_trace("normalizer.downgraded_commitment", "confirmed commitment requires trusted schedule authority"))
    if raw_evidence_kind and raw_evidence_kind != "none":
        trace.append(_trace("normalizer.plan_evidence_rejected", "ordinary plans cannot carry execution evidence", requested=raw_evidence_kind))
    level = "L0"
    if raw.get("evidence_level") not in (None, "", "L0", "l0", 0):
        trace.append(_trace("normalizer.plan_evidence_level_reset", "ordinary plans use L0 until an evidence adapter writes a result", requested=raw.get("evidence_level")))
    mapping = _evidence_level_mapping(level)
    phase = derive_temporal_phase(raw, now=now)
    granularity = normalize_content_granularity(raw.get("content_granularity"), "intent")
    materialization = normalize_materialization_state(raw.get("materialization_state"), "none")
    if granularity == "scene" and materialization == "none":
        materialization = "candidate"
        trace.append(_trace("normalizer.scene_candidate", "scene-level plan is candidate materialization only"))
    expires_at = _text(raw.get("expires_at"), 96)
    result.update(
        {
            "plan_id": _text(plan_id or raw.get("plan_id") or raw.get("event_id"), 120)
            or stable_id("plan", title, raw.get("date"), raw.get("time"), raw.get("start_at")),
            "title": title,
            "source_kind": "planned",
            "status": "planned",
            "temporal_phase": phase,
            "evidence_kind": "none",
            "evidence_level": level,
            **mapping,
            "evidence_level_mapping": mapping,
            "authority_kind": authority,
            "commitment_level": commitment,
            "epistemic_status": normalize_epistemic_status(raw.get("epistemic_status"), epistemic_default),
            "content_granularity": granularity,
            "materialization_state": materialization,
            "fact_eligibility": "none",
            "confidence": _float_confidence(raw.get("confidence"), 0.4),
            "version": _version(raw.get("version")),
            "source_refs": source_refs,
            "source_refs_trusted": trusted_refs,
            "schedule_ref_status": schedule_ref_status,
            "schedule_ref_reason": schedule_ref_reason,
            "runtime_origin_refs": _list(raw.get("runtime_origin_refs")),
            "expires_at": expires_at,
            **actor,
            "decision_trace": trace,
            "canonical_schema_version": CANONICAL_SCHEMA_VERSION,
            "visibility": _text(raw.get("visibility"), 32) or "private",
            "certainty": _certainty(raw.get("certainty"), "medium"),
            "updated_at": _text(raw.get("updated_at"), 64) or _now_iso(now),
        }
    )
    result.pop("lossy", None)
    return result

def normalize_observed_activity(
    raw: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise AgendaContractError("observed activity must be an object")
    title = _text(raw.get("title") or raw.get("summary") or raw.get("activity"), 240)
    if not title:
        raise AgendaContractError("observed activity requires a title")
    source_refs = _list(raw.get("source_refs") or raw.get("evidence_refs"))
    result = deepcopy(raw)
    trace = _trace_list(raw.get("decision_trace"))
    source_text = _text(raw.get("source"), 64).lower()
    kind_text = _text(raw.get("kind"), 64).lower()
    requested_status = _normalize_status(raw.get("status") or raw.get("lifecycle_status"), "active")
    if raw.get("lifecycle_status") not in (None, ""):
        result["legacy_lifecycle_status"] = _text(raw.get("lifecycle_status"), 32)
    if raw.get("status") not in (None, "") and _normalize_status(raw.get("status"), "active") != requested_status:
        result["legacy_status"] = _text(raw.get("status"), 32)
    evidence_kind = normalize_evidence_kind(raw.get("evidence_kind"), "")
    if not evidence_kind:
        if source_text in {"tool", "tool_action", "api", "browser"} or kind_text in {"tool", "tool_action"}:
            evidence_kind = "tool_action"
        elif source_text in {"self_state", "self_state_commit", "runtime"}:
            evidence_kind = "self_state_commit"
        elif source_text in {"conversation", "chat", "interaction", "message"} or kind_text in {"conversation", "chat", "interaction"}:
            evidence_kind = "interaction"
        else:
            evidence_kind = "external_record"
    if requested_status in {"active", "completed", "partially_completed"} and not source_refs and evidence_kind not in {"interaction", "self_state_commit"}:
        requested_status = "unknown"
        trace.append(_trace("normalizer.missing_evidence", "observed status requires a concrete evidence reference"))
    actor = _actor_fields(raw)
    if not actor["subject_actor_id"]:
        trace.append(_trace("normalizer.missing_subject", "observed activity has no subject_actor_id and is diagnostic-only until bound"))
    level = normalize_evidence_level(raw.get("evidence_level"), "L2")
    mapping = _evidence_level_mapping(level)
    phase_item = raw
    if evidence_kind == "self_state_commit" and not any(raw.get(key) for key in ("start_at", "start", "time")):
        phase_item = dict(raw)
        phase_item["start_at"] = raw.get("committed_at") or raw.get("created_at")
        phase_item["end_at"] = raw.get("valid_until") or raw.get("expires_at")
    phase = derive_temporal_phase(phase_item, now=now)
    granularity = normalize_content_granularity(raw.get("content_granularity"), "intent" if evidence_kind != "tool_action" else "commitment")
    materialization = normalize_materialization_state(raw.get("materialization_state"), "active")
    if evidence_kind == "self_state_commit":
        materialization = "active"
    result.update(
        {
            "activity_id": _text(raw.get("activity_id") or raw.get("id"), 120)
            or stable_id("activity", title, raw.get("start_at") or raw.get("start"), raw.get("end_at") or raw.get("end"), source_refs),
            "title": title,
            "kind": _text(raw.get("kind"), 48) or "conversation",
            "source_kind": "observed",
            "source": _text(raw.get("source"), 64) or "conversation",
            "source_refs": source_refs,
            "participants": _list(raw.get("participants")),
            "evidence_kind": evidence_kind,
            "evidence_level": level,
            **mapping,
            "evidence_level_mapping": mapping,
            "temporal_phase": phase,
            "authority_kind": normalize_authority_kind(raw.get("authority_kind"), "state"),
            "commitment_level": normalize_commitment_level(raw.get("commitment_level"), "tentative"),
            "epistemic_status": "asserted" if actor.get("actor_type") == "interlocutor_user" and evidence_kind == "interaction" else "observed",
            "content_granularity": granularity,
            "materialization_state": materialization,
            "fact_eligibility": "none",
            "confidence": _float_confidence(raw.get("confidence"), 0.75),
            "runtime_origin_refs": _list(raw.get("runtime_origin_refs")),
            "expires_at": _text(raw.get("expires_at"), 96),
            **actor,
            "decision_trace": trace,
            "canonical_schema_version": CANONICAL_SCHEMA_VERSION,
            "visibility": _text(raw.get("visibility"), 32) or "private",
            "certainty": _certainty(raw.get("certainty"), "medium"),
            "status": requested_status,
            "version": _version(raw.get("version")),
            "updated_at": _text(raw.get("updated_at"), 64) or _now_iso(now),
        }
    )
    result.pop("lossy", None)
    return result

def normalize_window_snapshot(
    raw: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise AgendaContractError("window snapshot must be an object")
    window_date = _text(raw.get("window_date") or raw.get("date"), 20)
    window = normalize_window(raw.get("window") or raw.get("slug"))
    if not window_date or not window:
        raise AgendaContractError("window snapshot requires window_date and window")
    result = deepcopy(raw)
    trace = _trace_list(raw.get("decision_trace"))
    actor = _actor_fields(raw)
    if not actor["subject_actor_id"]:
        trace.append(_trace("normalizer.missing_subject", "projection snapshot has no subject_actor_id"))
    level = normalize_evidence_level(raw.get("evidence_level"), "L2")
    mapping = _evidence_level_mapping(level)
    phase = derive_temporal_phase(raw, now=now)
    result.update(
        {
            "snapshot_id": _text(raw.get("snapshot_id"), 160) or stable_id("agenda_snapshot", window_date, window),
            "date": _text(raw.get("date"), 20) or window_date,
            "window_date": window_date,
            "window": window,
            "planned": _items(raw.get("planned")),
            "observed": _items(raw.get("observed")),
            "reconciled": _items(raw.get("reconciled")),
            "open_items": _list(raw.get("open_items")),
            "source_refs": _list(raw.get("source_refs")),
            "source_kind": "projection",
            "evidence_level": level,
            **mapping,
            "evidence_level_mapping": mapping,
            "temporal_phase": phase,
            "evidence_kind": "none",
            "authority_kind": "state",
            "commitment_level": "tentative",
            "epistemic_status": "inferred",
            "content_granularity": "intent",
            "materialization_state": "none",
            "fact_eligibility": "none",
            "confidence": _float_confidence(raw.get("confidence"), 0.5),
            "runtime_origin_refs": _list(raw.get("runtime_origin_refs")),
            "expires_at": _text(raw.get("expires_at"), 96),
            **actor,
            "decision_trace": trace,
            "canonical_schema_version": CANONICAL_SCHEMA_VERSION,
            "visibility": _text(raw.get("visibility"), 32) or "private",
            "certainty": _certainty(raw.get("certainty"), "medium"),
            # A projection is a snapshot, not proof that every planned item
            # completed.  Keep it in the C3 reconciliation state until an
            # evidence adapter provides item-level completion.
            "status": _normalize_status(raw.get("status"), "reconciled"),
            "version": _version(raw.get("version")),
            "generated_at": _text(raw.get("generated_at"), 64) or _now_iso(now),
            "timezone": _text(raw.get("timezone"), 64) or "Asia/Shanghai",
        }
    )
    result.pop("lossy", None)
    return result

def normalize_reconciliation(raw: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise AgendaContractError("reconciliation must be an object")
    window_date = _text(raw.get("window_date") or raw.get("date"), 20)
    window = normalize_window(raw.get("window") or raw.get("slug"))
    result = deepcopy(raw)
    trace = _trace_list(raw.get("decision_trace"))
    actor = _actor_fields(raw)
    if not actor["subject_actor_id"]:
        trace.append(_trace("normalizer.missing_subject", "reconciliation has no subject_actor_id"))
    level = normalize_evidence_level(raw.get("evidence_level"), "L3")
    mapping = _evidence_level_mapping(level)
    phase = derive_temporal_phase(raw, now=now)
    reconciliation_evidence_kind = normalize_evidence_kind(raw.get("evidence_kind"), "none")
    if _list(raw.get("source_refs")) and reconciliation_evidence_kind == "none":
        trace.append(_trace("normalizer.reconciliation_refs_not_execution", "snapshot/reconciliation refs do not prove execution without an evidence kind"))
    result.update(
        {
            "reconciliation_id": _text(raw.get("reconciliation_id"), 160)
            or stable_id("reconciliation", window_date, window),
            "window_date": window_date,
            "date": _text(raw.get("date"), 20) or window_date,
            "window": window,
            "source_kind": "reconciled",
            "evidence_level": level,
            **mapping,
            "evidence_level_mapping": mapping,
            "temporal_phase": phase,
            "evidence_kind": reconciliation_evidence_kind,
            "authority_kind": normalize_authority_kind(raw.get("authority_kind"), "state"),
            "commitment_level": normalize_commitment_level(raw.get("commitment_level"), "tentative"),
            "epistemic_status": "observed" if reconciliation_evidence_kind != "none" else "inferred",
            "content_granularity": normalize_content_granularity(raw.get("content_granularity"), "intent"),
            "materialization_state": normalize_materialization_state(raw.get("materialization_state"), "none"),
            "fact_eligibility": "none",
            "confidence": _float_confidence(raw.get("confidence"), 0.75),
            "runtime_origin_refs": _list(raw.get("runtime_origin_refs")),
            "expires_at": _text(raw.get("expires_at"), 96),
            **actor,
            "decision_trace": trace,
            "canonical_schema_version": CANONICAL_SCHEMA_VERSION,
            "visibility": _text(raw.get("visibility"), 32) or "private",
            "certainty": _certainty(raw.get("certainty"), "high"),
            "status": _normalize_status(raw.get("status"), "reconciled"),
            "source_refs": _list(raw.get("source_refs")),
            "version": _version(raw.get("version")),
            "generated_at": _text(raw.get("generated_at"), 64) or _now_iso(now),
        }
    )
    result.pop("lossy", None)
    return result

def migrate_store(data: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Additive migration for old plugin JSON without deleting old fields."""

    if not isinstance(data, dict):
        raise AgendaContractError("store must be an object")
    changed = False
    defaults: dict[str, Any] = {
        "agenda_version": AGENDA_VERSION,
        "agenda_contract_version": CANONICAL_SCHEMA_VERSION,
        "observed_activities": [],
        "window_snapshots": [],
        "agenda_reconciliation_history": [],
    }
    for key, default in defaults.items():
        if key not in data:
            data[key] = deepcopy(default)
            changed = True
        elif not isinstance(data.get(key), type(default)):
            data[f"legacy_{key}"] = deepcopy(data[key])
            data[key] = deepcopy(default)
            changed = True

    if "activities" in data and not data["observed_activities"] and isinstance(data["activities"], list):
        data["observed_activities"] = deepcopy(data["activities"])
        changed = True

    try:
        current_version = max(0, int(data.get("agenda_version") or 0))
    except (TypeError, ValueError):
        current_version = 0
    if current_version < AGENDA_VERSION:
        data["agenda_version"] = AGENDA_VERSION
        changed = True
    try:
        current_contract_version = max(0, int(data.get("agenda_contract_version") or 0))
    except (TypeError, ValueError):
        current_contract_version = 0
    if current_contract_version < CANONICAL_SCHEMA_VERSION:
        data["agenda_contract_version"] = CANONICAL_SCHEMA_VERSION
        changed = True

    if isinstance(data.get("daily_plan"), dict) and isinstance(data["daily_plan"].get("items"), list):
        migrated_items: list[Any] = []
        for item in data["daily_plan"]["items"]:
            if not isinstance(item, dict):
                migrated_items.append(item)
                continue
            try:
                # ``daily_plan`` is the Bot-owned C3 edit store.  Bind legacy
                # rows to that owner during migration so later ``setdefault``
                # compatibility readers do not mistake an explicitly empty
                # actor field for an unresolved subject.  Other normalizer
                # entry points remain strict and keep missing subjects in the
                # diagnostic view.
                legacy_plan = dict(item)
                if not legacy_plan.get("actor_type"):
                    legacy_plan["actor_type"] = "bot"
                if not legacy_plan.get("subject_actor_id"):
                    legacy_plan["subject_actor_id"] = "bot_self"
                migrated_items.append(normalize_plan_item(legacy_plan))
            except AgendaContractError:
                migrated_items.append(deepcopy(item))
        if migrated_items != data["daily_plan"]["items"]:
            data["daily_plan"]["items"] = migrated_items
            changed = True
    # Calendar is an additive long-lived constraint layer.  Keep the import
    # local so the historical C3 contract remains importable in isolation and
    # malformed calendar rows cannot affect legacy agenda normalization.
    try:
        try:
            from .calendar_contracts import migrate_calendar_store
        except ImportError:
            from calendar_contracts import migrate_calendar_store
        data, calendar_changed = migrate_calendar_store(data)
        changed = changed or calendar_changed
    except Exception:
        # The existing agenda store must remain loadable even if a deployment
        # carries an unexpectedly malformed calendar extension.
        pass
    return data, changed

def agenda_entry_from_plan(plan: dict[str, Any], *, reason: str = "") -> dict[str, Any]:
    plan_id = _text(plan.get("plan_id"), 120)
    result = {
        "entry_id": stable_id("agenda_entry", "plan", plan_id),
        "title": _text(plan.get("title") or plan.get("activity"), 240),
        "kind": "planned",
        "status": _normalize_status(plan.get("status"), "planned"),
        "source_kind": "planned",
        "evidence_level": normalize_evidence_level(plan.get("evidence_level"), "L0"),
        "visibility": _text(plan.get("visibility"), 32) or "private",
        "certainty": _certainty(plan.get("certainty"), "medium"),
        # A plan ID identifies an intent, not execution evidence.  Do not put
        # it into source_refs merely to make the list non-empty.
        "source_refs": _list(plan.get("source_refs")),
        "runtime_origin_refs": _list(plan.get("runtime_origin_refs")),
        "temporal_phase": normalize_temporal_phase(plan.get("temporal_phase"), "future"),
        "evidence_kind": normalize_evidence_kind(plan.get("evidence_kind"), "none"),
        "canonical_evidence_level": normalize_evidence_level(plan.get("canonical_evidence_level") or plan.get("evidence_level"), "L0"),
        "archive_evidence_level": normalize_evidence_level(plan.get("archive_evidence_level"), _evidence_level_mapping(plan.get("evidence_level"))["archive_evidence_level"]),
        "evidence_level_mapping": deepcopy(plan.get("evidence_level_mapping") or _evidence_level_mapping(plan.get("evidence_level"))),
        "authority_kind": normalize_authority_kind(plan.get("authority_kind"), "llm"),
        "commitment_level": normalize_commitment_level(plan.get("commitment_level"), "tentative"),
        "epistemic_status": normalize_epistemic_status(plan.get("epistemic_status"), "inferred"),
        "content_granularity": normalize_content_granularity(plan.get("content_granularity"), "intent"),
        "materialization_state": normalize_materialization_state(plan.get("materialization_state"), "none"),
        "fact_eligibility": normalize_fact_eligibility(plan.get("fact_eligibility"), "none"),
        "confidence": _float_confidence(plan.get("confidence"), 0.4),
        "expires_at": _text(plan.get("expires_at"), 96),
        "actor_type": _enum(plan.get("actor_type"), ACTOR_TYPES, ""),
        "subject_actor_id": _text(plan.get("subject_actor_id"), 120),
        "object_actor_id": _text(plan.get("object_actor_id"), 120),
        "source_actor_id": _text(plan.get("source_actor_id"), 120) or "system",
        "target_user_id": _text(plan.get("target_user_id"), 120),
        "participant_roles": _normalize_participant_roles(plan.get("participant_roles"), plan.get("participants")),
        "decision_trace": _trace_list(plan.get("decision_trace")),
        "canonical_schema_version": _version(plan.get("canonical_schema_version"), CANONICAL_SCHEMA_VERSION),
    }
    if reason:
        result["reconciliation_reason"] = reason
    for key in ("plan_id", "start_at", "end_at", "date", "time", "end", "participants", "version", "legacy_status", "legacy_lifecycle_status"):
        if key in plan:
            result[key] = deepcopy(plan[key])
    return result

def agenda_entry_from_activity(activity: dict[str, Any], *, source_refs: Iterable[str] = ()) -> dict[str, Any]:
    refs = _list(list(source_refs)) or _list(activity.get("source_refs"))
    level = normalize_evidence_level(activity.get("evidence_level"), "L2")
    result = {
        "entry_id": stable_id("agenda_entry", "activity", activity.get("activity_id")),
        "title": _text(activity.get("title"), 240),
        "kind": "observed",
        "status": _normalize_status(activity.get("status"), "active"),
        "source_kind": "observed",
        "evidence_level": level,
        "canonical_evidence_level": normalize_evidence_level(activity.get("canonical_evidence_level") or level, level),
        "archive_evidence_level": normalize_evidence_level(activity.get("archive_evidence_level"), _evidence_level_mapping(level)["archive_evidence_level"]),
        "evidence_level_mapping": deepcopy(activity.get("evidence_level_mapping") or _evidence_level_mapping(level)),
        "temporal_phase": normalize_temporal_phase(activity.get("temporal_phase"), "current"),
        "evidence_kind": normalize_evidence_kind(activity.get("evidence_kind"), "external_record"),
        "authority_kind": normalize_authority_kind(activity.get("authority_kind"), "state"),
        "commitment_level": normalize_commitment_level(activity.get("commitment_level"), "tentative"),
        "epistemic_status": normalize_epistemic_status(activity.get("epistemic_status"), "observed"),
        "content_granularity": normalize_content_granularity(activity.get("content_granularity"), "intent"),
        "materialization_state": normalize_materialization_state(activity.get("materialization_state"), "active"),
        "fact_eligibility": normalize_fact_eligibility(activity.get("fact_eligibility"), "none"),
        "confidence": _float_confidence(activity.get("confidence"), 0.75),
        "visibility": _text(activity.get("visibility"), 32) or "private",
        "certainty": _certainty(activity.get("certainty"), "medium"),
        "source_refs": refs,
        "runtime_origin_refs": _list(activity.get("runtime_origin_refs")),
        "expires_at": _text(activity.get("expires_at"), 96),
        "actor_type": _enum(activity.get("actor_type"), ACTOR_TYPES, ""),
        "subject_actor_id": _text(activity.get("subject_actor_id"), 120),
        "object_actor_id": _text(activity.get("object_actor_id"), 120),
        "source_actor_id": _text(activity.get("source_actor_id"), 120) or "system",
        "target_user_id": _text(activity.get("target_user_id"), 120),
        "participant_roles": _normalize_participant_roles(activity.get("participant_roles"), activity.get("participants")),
        "decision_trace": _trace_list(activity.get("decision_trace")),
        "canonical_schema_version": _version(activity.get("canonical_schema_version"), CANONICAL_SCHEMA_VERSION),
    }
    for key in ("activity_id", "start_at", "end_at", "participants", "version", "kind"):
        if key in activity:
            result[key] = deepcopy(activity[key])
    return result
