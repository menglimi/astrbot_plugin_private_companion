"""Bounded planning material from the original calendar, goals and memory owners."""

from datetime import date, datetime, time, timedelta
import json
from zoneinfo import ZoneInfo

from companion_v2.conversation.messages import event_from_document
from companion_v2.conversation.date_context import FIXED_HOLIDAYS, project_local_date
from companion_v2.execution.models import TaskError, digest, require, stamp
from companion_v2.identity_policy.private_domain import source_ref
from companion_v2.memory.models import AtomRef
from companion_v2.memory.pinned import visible_fact
from companion_v2.role_work.growth.configuration import INFLUENCE_NOTES, skill_settings, skills_enabled
from companion_v2.role_work.life.scheduling import sleep_schedule_summary
from .planning_config import (SCHEDULE_MODE_ORCHESTRATED, configured_annual_calendars,
                              configured_weather_location, configured_weather_max_age, planning_values)
from .weather_material import planning_weather_material


MATERIAL_CHARS = 16384
INTERVENTION_DIALOGUE_WINDOW = timedelta(minutes=3)


def _intervention_dialogue(app, context, at):
    """Read the same bounded, validated private dialogue used by Laya."""
    from companion_v2.conversation.native_history import dialogue

    return dialogue(app.messages, context, at,
                    start=stamp(at - INTERVENTION_DIALOGUE_WINDOW), end=stamp(at),
                    limit=16, max_chars=8000)


def _dialogue_fingerprint(dialogue):
    return digest([(item["observation_id"], item["content_hash"], item["speaker"])
                   for item in dialogue]) if dialogue else None


def planning_calendar_context(at, end, zone, annual_calendars=(), *, holiday_enabled=True,
                              holiday_country="CN", automatic_solar_terms_enabled=True,
                              solar_term_port=None):
    local_start, local_end = at.astimezone(zone), end.astimezone(zone)
    last_day = local_end.date() - timedelta(days=1) if local_end.time() == time.min else local_end.date()
    holidays = []
    for year in range(local_start.year, last_day.year + 1):
        for (month, day), name in FIXED_HOLIDAYS.items():
            candidate = date(year, month, day)
            if local_start.date() <= candidate <= last_day:
                holidays.append({"date": candidate.isoformat(), "name": name})
    holidays.sort(key=lambda item: item["date"])
    configured = {}
    for calendar in annual_calendars:
        for field, values in (("holiday", calendar.holiday_days),
                              ("makeup_workday", calendar.makeup_workdays),
                              ("solar_terms", calendar.solar_terms)):
            for day, name in values:
                if local_start.date() <= day <= last_day:
                    row = configured.setdefault(day, {"date": day.isoformat(), "holiday": None,
                                                        "makeup_workday": None, "solar_terms": []})
                    if field == "solar_terms":
                        row[field].append(name)
                    else:
                        row[field] = name
    days, used_chars, partial = [], 0, False
    for row in (configured[day] for day in sorted(configured)):
        size = len(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
        if used_chars + size > 4096:
            partial = True
            break
        days.append(row)
        used_chars += size
    return {"today": project_local_date(at, zone.key, annual_calendars,
                                        holiday_enabled=holiday_enabled,
                                        holiday_country=holiday_country,
                                        automatic_solar_terms_enabled=automatic_solar_terms_enabled,
                                        solar_term_port=solar_term_port),
            "fixed_gregorian_holidays_in_window": holidays,
            "configured_annual_calendar": {"basis": "operator_configured_annual_calendar",
             "years": sorted(item.year for item in annual_calendars), "days": days, "partial": partial}}


def _role_state_summary(app, context, at):
    service = getattr(app, "role_state", None)
    if service is None:
        return None
    try:
        state = service.read(context, at=at)
    except Exception:
        return None
    if not isinstance(state, dict) or state.get("state") in ("disabled", "unknown"):
        return None
    values = state.get("values") or {}
    if not isinstance(values, dict):
        return None
    summary = {}
    if isinstance(values.get("energy"), int):
        summary["energy"] = values["energy"]
    for key in ("sleep", "busy", "hunger", "health", "location"):
        if isinstance(values.get(key), str) and values[key].strip():
            summary[key] = values[key].strip()[:120]
    mood_val = values.get("mood")
    if isinstance(mood_val, str) and mood_val.strip():
        summary["mood"] = mood_val.strip()[:120]
    elif isinstance(mood_val, dict):
        desc = mood_val.get("description")
        if isinstance(desc, str) and desc.strip():
            summary["mood"] = desc.strip()[:120]
    conditions = [item.get("text", "").strip()[:120] for item in (values.get("conditions") or ())
                  if isinstance(item, dict) and isinstance(item.get("text"), str) and item.get("text").strip()]
    if conditions:
        summary["conditions"] = conditions[:4]
    if summary:
        summary["basis"] = "role_simulation"
        summary["updated_at"] = state.get("updated_at")
        if state.get("updated_at"):
            summary["age_minutes"] = max(0, round((at - datetime.fromisoformat(state["updated_at"])).total_seconds() / 60))
    return summary or None


def _recent_life_summary(app, context, at):
    service = getattr(app, "role_state", None)
    if service is None:
        return None
    try:
        activities = service.activity_context(context)
    except Exception:
        return None
    items = []
    for item in (activities.get("items") or ())[:3]:
        if not isinstance(item, dict) or item.get("sources"):
            continue
        entry = {"kind": item.get("kind") or "activity", "state": item.get("state")}
        desc = item.get("description")
        if isinstance(desc, str) and desc.strip():
            entry["description"] = desc.strip()[:140]
        for key in ("started_at", "updated_at", "due_at"):
            if isinstance(item.get(key), str) and item[key]:
                entry[key] = item[key]
        items.append(entry)
    return items or None


def _sleep_schedule_summary(app, context):
    body_clock = getattr(getattr(app, "role_state", None), "body_clock", None)
    if body_clock is None:
        return None
    try:
        return sleep_schedule_summary(body_clock.sleep_settings(context))
    except Exception:
        return None


def _content_authoring_summary(app, context, at):
    from companion_v2.capabilities.models import CapabilityDesiredState
    from companion_v2.capabilities.repositories import CapabilityRepository
    from companion_v2.content.catalogue import content_database
    from companion_v2.conversation.content.authoring_service import effective_settings
    from companion_v2.conversation.content.library import summarize_role_works

    try:
        settings = effective_settings(app.runtime, context)
    except Exception:
        return None
    if not settings.enabled:
        return None
    role = getattr(getattr(context, "role_binding", None), "role", None)
    scope = getattr(context, "scope", None)
    if role is None or scope is None:
        return None
    try:
        projected = CapabilityRepository(app.runtime).projection(
            extension_id="content", role=role, scope=scope
        )
    except Exception:
        return None
    if not projected.is_success:
        return None
    projection = projected.unwrap()
    if (
        projection.installation.desired_state is not CapabilityDesiredState.ACTIVE
        or projection.role_selection is None
        or not projection.role_selection.enabled
    ):
        return None
    works = {"works_total": 0, "drafting_works": 0, "days_since_last_work": None}
    bot_id = getattr(scope, "bot_id", None)
    if bot_id:
        works = summarize_role_works(
            content_database(app.runtime.data_root),
            installation_id=scope.installation_id,
            bot_id=bot_id,
            role_id=role.subject_id,
            now=at,
        )
    summary = {
        "enabled": True,
        "works_total": works["works_total"],
        "drafting_works": works["drafting_works"],
        "days_since_last_work": works["days_since_last_work"],
    }
    direction = settings.direction_prompt.strip()
    if direction:
        summary["direction_prompt"] = direction[:400]
    return summary


def calendar_inventory(app, context, *, role_global=False):
    if not role_global:
        inventory = app.calendar.inventory(context, limit=100)
        return inventory, digest(inventory)
    app.calendar.access.check(context, "calendar.read")
    records, partial = app.calendar.records.shared_role_plans(context, limit=100)
    items = tuple(item for item in records if not item["sources"])
    return {"items": items, "next_cursor": None, "skipped": 0, "partial": partial}, \
        digest({"items": items, "partial": partial})


def capture(planner, context, instruction, mode, slot, occurrence=None, *, weather=None, weather_location=None,
            manual=False, refresh=False, role_global=False):
    app, at = planner.app, planner.tasks.now()
    app.calendar.access.check(context, "calendar.write", at=at)
    settings = planning_values(app.role_state.settings(context)[0])
    if not settings["calendar_enabled"]:
        raise TaskError("calendar_disabled", "enable the role-wide calendar switch in schedule and planning settings")
    if not manual and not settings["planning_enabled"]:
        raise TaskError("planning_disabled", "enable automatic planning in schedule and planning settings")
    if mode != "intervention" and settings["schedule_simulation_mode"] != SCHEDULE_MODE_ORCHESTRATED:
        raise TaskError("planning_mode_inactive", "automatic planning is inactive in continuous schedule mode")
    zone = ZoneInfo(settings["planning_timezone"])
    window_days = min(31, settings["planning_horizon_days"]) if mode == "intervention" else settings["planning_horizon_days"]
    end = (datetime.combine(at.astimezone(zone).date() + timedelta(days=1), time(), zone)
           if mode == "daily" else at + timedelta(days=window_days))
    from companion_v2.conversation.perception_settings import read_perception_values
    perception = read_perception_values(app, context, at, role_only=role_global)
    solar_term_port = getattr(getattr(app, "messages", None), "solar_term_port", None)
    calendar_context = planning_calendar_context(
        at, end, zone,
        configured_annual_calendars(settings["planning_annual_calendars"]),
        holiday_enabled=perception["holiday_enabled"],
        holiday_country=perception["holiday_country"],
        automatic_solar_terms_enabled=perception["automatic_solar_terms_enabled"],
        solar_term_port=solar_term_port,
    )
    inventory, calendar_snapshot = calendar_inventory(app, context, role_global=role_global)
    sources, calendar, goals, memories, inputs = [], [], [], [], []
    intervention_dialogue = (_intervention_dialogue(app, context, at)
                             if mode == "intervention" else ())
    intervention_dialogue_fingerprint = _dialogue_fingerprint(intervention_dialogue)
    goal_refs, calendar_refs, memory_refs, growth_refs, experience_refs = {}, {}, [], [], []
    coverage = {"calendar_partial": bool(inventory.get("partial", False)
                                           or inventory["next_cursor"] or inventory["skipped"])}
    used_chars = len(settings["planning_prompt"]) + 2048 + len(json.dumps(calendar_context, ensure_ascii=False))
    current_weather = None
    if settings["planning_weather_enabled"]:
        location = configured_weather_location(settings["planning_weather_location"])
        if location == weather_location and weather is not None:
            current_weather = planning_weather_material(weather, location, at,
                                                        max_age=configured_weather_max_age(settings))
        if current_weather is None:
            coverage["weather_unavailable"] = True
        elif used_chars + len(json.dumps(current_weather, ensure_ascii=False)) <= MATERIAL_CHARS:
            used_chars += len(json.dumps(current_weather, ensure_ascii=False))
        else:
            current_weather = None
            coverage["material_limited"] = True

    def include(references):
        refs = tuple(source_ref(ref) if type(ref) is dict else ref for ref in references)
        combined = tuple(dict.fromkeys((*sources, *refs)))
        if len(combined) > 8 or any(ref.scope != context.scope for ref in refs):
            coverage["evidence_limited"] = True
            return False
        app.calendar.access.evidence(context, combined, at=at)
        sources[:] = combined
        return True

    def offer(bucket, value, references):
        nonlocal used_chars
        size = len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
        if used_chars + size > MATERIAL_CHARS:
            coverage["material_limited"] = True
            return False
        if not include(references):
            return False
        bucket.append(value)
        used_chars += size
        return True

    if occurrence is not None:
        original = app.calendar.read(context, occurrence["id"])
        if original["revision"] != occurrence["revision"] or original["kind"] != "role_plan" or original["state"] != "accepted":
            raise TaskError("planning_target_changed", "the selected role plan changed")
        include(original["sources"])

    # Ordinary planning keeps its small report window. Intervention planning uses
    # the complete validated user/assistant dialogue below, so it never falls back
    # to a keyword or user-only prefilter.
    if mode != "intervention":
        rows = (() if role_global else app.runtime.read_all("""SELECT i.event_json FROM conversation_inputs i
            JOIN conversation_batches b USING(batch_id) JOIN conversation_flows f USING(batch_id)
            WHERE b.origin_key = ? AND i.withdrawn = 0 AND f.state = 'complete'
            ORDER BY f.sequence DESC, i.position DESC LIMIT 2""", (context.key,)))
        for row in reversed(rows):
            event = event_from_document(json.loads(row["event_json"]), context)
            try:
                record = app.messages._read_source(event.source, context, event.capability, at)
                offer(inputs, record.payload[:2048], (event.source,))
                if len(record.payload) > 2048:
                    coverage["inputs_partial"] = True
            except TaskError:
                coverage["inputs_unavailable"] = True

        from companion_v2.conversation.native_history import body, reference, rows as native_rows
        current_native_rows = (() if role_global else
            native_rows(app.runtime, context, end=at.isoformat(timespec="microseconds"), kind="input", limit=2))
        for row in reversed(current_native_rows):
            try:
                item = body(app.messages, reference(row), context, at)
                original = source_ref(json.loads(row["source_json"]))
                offer(inputs, item["text"][:2048], (original,))
                if len(item["text"]) > 2048:
                    coverage["inputs_partial"] = True
            except TaskError:
                coverage["inputs_unavailable"] = True
    else:
        dialogue_material = []
        for item in intervention_dialogue:
            if not offer(dialogue_material, item, ()):
                coverage["intervention_dialogue_partial"] = True
                break

    calendar_items = inventory["items"]
    if mode == "intervention":
        calendar_items = tuple(item for item in calendar_items
                               if item["kind"] == "role_plan" and item["state"] == "accepted")
    for item in calendar_items:
        if item["state"] == "cancelled":
            continue
        projected = {name: item[name] for name in ("id", "revision", "kind", "state", "title", "schedule", "duration_minutes")}
        projected["detail"] = item["detail"][:512]
        projected["exceptions"] = item["exceptions"]
        if len(item["detail"]) > 512 or item.get("details"):
            coverage["calendar_details_partial"] = True
        if offer(calendar, projected, item["sources"]):
            calendar_refs[item["id"]] = {"id": item["id"], "revision": item["revision"]}
    try:
        reader = app.role_growth_context.reader
        candidates = () if role_global else reader.candidates(context, at)
        coverage["goals_partial"] = (False if role_global else
                                      candidates.truncated or bool(candidates.skipped))
        use_skills = False if role_global else skills_enabled(app.runtime, context)
        for candidate in (() if role_global else candidates.items[:8]):
            record = reader.ledger.record(candidate.reference["growth_id"], owner=reader.owner(context))
            if record.kind.value == "skill" and not use_skills:
                continue
            value = json.loads(candidate.payload)
            for key in ("description", "progress_note"):
                if len(value[key]) > 512:
                    coverage["goals_partial"] = True
                value[key] = value[key][:512]
            if offer(goals, {**value, **candidate.reference}, record.sources):
                growth_refs.append(candidate.reference)
                if value["kind"] == "goal" and value["state"] == "active":
                    goal_refs[record.growth_id] = candidate.reference
        if not role_global:
            coverage["goals_partial"] |= len(candidates.items) > 8
    except TaskError:
        coverage["goals_unavailable"] = True
    if growth_refs:
        _, strength = skill_settings(app.runtime, context)
        if strength:
            instruction = instruction + "\n" + INFLUENCE_NOTES[strength]
    snapshot = ({"references": [], "experience_references": []} if role_global else
        app.memory.context.capture(context, "planning-" + digest([context.key, slot]), tuple(sources),
                                   at + timedelta(minutes=3)))
    coverage["memory_partial"] = (False if role_global else
        snapshot.get("state") != "ready" or any(snapshot.get(k) for k in
            ("recall_pending", "core_omitted", "recall_unavailable")) or len(snapshot["references"]) > 4)
    for reference in snapshot["references"][:4]:
        try:
            item = require(app.memory.writer.read(context, AtomRef(**reference)))
            if offer(memories, visible_fact(item), (item["source"],)):
                memory_refs.append(reference)
        except TaskError:
            coverage["memory_partial"] = True
    for reference in snapshot.get("experience_references", ())[:1]:
        try:
            item = require(app.memory.experiences.read(context, reference["view_id"], expected_revision=reference["revision"]))
            projected = {"basis": "recorded_experience", "view_id": reference["view_id"],
                "statements": [{"text": s["text"], "kind": s.get("kind")} for s in item["statements"]]}
            if offer(memories, projected, (item["source"],)):
                experience_refs.append(reference)
        except TaskError:
            coverage["memory_partial"] = True
    refresh_plans = []
    if manual and refresh and mode == "daily":
        today = at.astimezone(zone).date()
        for item in inventory["items"]:
            basis = item.get("planning") or {}
            if (item["state"] == "accepted" and item["kind"] == "role_plan"
                    and basis.get("mode") == "daily"
                    and datetime.fromisoformat(item["schedule"]["anchor"]).astimezone(zone).date() == today):
                refresh_plans.append({"id": item["id"], "revision": item["revision"]})
    refresh_ids = {item["id"] for item in refresh_plans}
    existing = [(item["title"], item["schedule"]["anchor"], item["duration_minutes"])
                for item in inventory["items"] if item["state"] != "cancelled" and item["id"] not in refresh_ids]
    material = {"mode": mode, "now": at.isoformat(), "end": end.isoformat(),
        "timezone": settings["planning_timezone"], "max_items": settings["planning_max_items"],
        "planning_preferences": settings["planning_prompt"], "calendar_context": calendar_context,
        "planning_scope": "role" if role_global else "conversation",
        "current_reports": inputs,
        "intervention_dialogue": tuple(dialogue_material) if mode == "intervention" else (),
        "calendar": calendar, "confirmed_goals_and_skills": goals, "memories": memories,
        "coverage": coverage, "occurrence_to_refine": ({key: occurrence[key] for key in
            ("id", "revision", "sequence", "kind", "state", "title", "start", "end")} if occurrence else None)}
    if current_weather is not None:
        material["current_weather"] = current_weather
    sleep_schedule = _sleep_schedule_summary(app, context)
    if sleep_schedule is not None:
        size = len(json.dumps(sleep_schedule, ensure_ascii=False, separators=(",", ":")))
        if used_chars + size <= MATERIAL_CHARS:
            material["sleep_schedule"] = sleep_schedule
            used_chars += size
    role_state = _role_state_summary(app, context, at)
    if role_state is not None:
        size = len(json.dumps(role_state, ensure_ascii=False, separators=(",", ":")))
        if used_chars + size <= MATERIAL_CHARS:
            material["role_state"] = role_state
            used_chars += size
    recent_life = _recent_life_summary(app, context, at)
    if recent_life is not None:
        size = len(json.dumps(recent_life, ensure_ascii=False, separators=(",", ":")))
        if used_chars + size <= MATERIAL_CHARS:
            material["recent_life"] = recent_life
            used_chars += size
    if mode in ("daily", "horizon"):
        content_authoring = _content_authoring_summary(app, context, at)
        if content_authoring is not None:
            size = len(json.dumps(content_authoring, ensure_ascii=False, separators=(",", ":")))
            if used_chars + size <= MATERIAL_CHARS:
                material["content_authoring"] = content_authoring
                used_chars += size
    return {"context": context, "instruction": instruction, "mode": mode, "slot": slot, "at": at, "end": end,
        "settings": settings, "sources": tuple(sources),
        "goal_refs": goal_refs, "calendar_refs": calendar_refs, "memory_refs": memory_refs, "growth_refs": growth_refs,
        "experience_refs": experience_refs, "existing": existing, "refresh_plans": refresh_plans,
        "occurrence": occurrence, "role_global": role_global, "material": material,
        "intervention_dialogue": intervention_dialogue,
        "intervention_dialogue_fingerprint": intervention_dialogue_fingerprint,
        "calendar_snapshot": calendar_snapshot}


def validate(planner, job, at, *, committed=False):
    app, context = planner.app, job["context"]
    app.calendar.access.check(context, "calendar.write", at=at)
    if job.get("intervention"):
        planner._check_intervention(context, at)
    if planning_values(app.role_state.settings(context)[0]) != job["settings"]:
        raise TaskError("planning_settings_changed", "planning preferences changed")
    binding = app.state_maintenance.bindings.get(context.key)
    if binding is None or binding[:2] != (context, job["instruction"]):
        raise TaskError("planning_role_changed", "the original role is no longer bound")
    app.calendar.access.evidence(context, job["sources"], at=at)
    if committed:
        return
    if (job.get("intervention") and app.state_maintenance._input_epochs.get(context.key, 0)
            != job["input_epoch"]):
        raise TaskError("planning_inputs_changed", "new conversation input superseded this schedule intervention")
    if job.get("intervention"):
        dialogue = _intervention_dialogue(app, context, at)
        if _dialogue_fingerprint(dialogue) != job.get("intervention_dialogue_fingerprint"):
            raise TaskError("planning_inputs_changed", "the intervention dialogue changed")
    _, current_calendar_snapshot = calendar_inventory(app, context, role_global=job.get("role_global", False))
    if current_calendar_snapshot != job["calendar_snapshot"]:
        raise TaskError("planning_calendar_changed", "calendar changed while planning")
    if job["growth_refs"]:
        app.role_growth_context.reader.read(tuple(job["growth_refs"]), context, at)
    for reference in job["memory_refs"]:
        require(app.memory.writer.read(context, AtomRef(**reference)))
    for reference in job["experience_refs"]:
        require(app.memory.experiences.read(context, reference["view_id"], expected_revision=reference["revision"]))
    if at >= job["end"] or job["occurrence"] is not None and at >= datetime.fromisoformat(job["occurrence"]["end"]):
        raise TaskError("planning_window_expired", "the original planning window ended")
