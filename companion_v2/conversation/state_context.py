"""Seal references to state, rechecking permissions and simulation expiry at every consumer."""

from datetime import datetime, timedelta
import json

from companion_v2.agenda.calendar_prompt import schedule_instruction, continuous_schedule_projection
from companion_v2.agenda.planning_config import SCHEDULE_MODE_CONTINUOUS, SCHEDULE_MODE_ORCHESTRATED
from companion_v2.conversation.foreground import ContextContribution, ContributionState
from companion_v2.execution.models import TaskError, digest, encode, require, stamp
from companion_v2.foundation.refs import SourceRef, VersionRef
from companion_v2.identity_policy.private_domain import source_ref
from companion_v2.role_work.state import humanized, mood
from companion_v2.role_work.state.cycle_prompts import reply_instruction
from companion_v2.sources.dependencies import link, validation_batch
from companion_v2.sources.models import SourceRecord
from companion_v2.sources.repositories import SourceRepository


class ConversationStateContext:
    def __init__(self, service, calendar=None):
        self.service, self.calendar = service, calendar
        self.sources = SourceRepository(service.runtime)

    def capture(self, context, at):
        with self.service.runtime.transaction(), validation_batch(self.service.runtime, at):
            return self._capture(context, at)

    def _capture(self, context, at):
        try:
            settings, _token = self.service.settings(context)
            schedule_mode = settings.get("schedule_simulation_mode", SCHEDULE_MODE_ORCHESTRATED)
            calendar = {"items": [], "partial": False, "skipped": 0}
            if schedule_mode == SCHEDULE_MODE_CONTINUOUS:
                calendar["partial"] = True
            schedule_source = None
            if settings.get("calendar_enabled") and self.calendar is not None:
                # Continuous mode projects the next day.  Orchestrated mode only
                # needs the current/next slot so a chat turn cannot claim the
                # role is free while its accepted programme says otherwise.
                if schedule_mode == SCHEDULE_MODE_CONTINUOUS:
                    start, end = at, at + timedelta(days=1)
                else:
                    start, end = at - timedelta(hours=2), at + timedelta(hours=4)
                try:
                    calendar = self.calendar.occurrences(context, start=start, end=end)
                except TaskError:
                    calendar = {"items": [], "partial": True, "skipped": 0}
            if schedule_mode == SCHEDULE_MODE_CONTINUOUS or calendar.get("items"):
                schedule_source = self._publish_schedule_projection(
                    context, calendar, at, mode=schedule_mode)
            state = None
            if settings.get("enabled"):
                try:
                    state = self.service.read(context, at=at, injection=True)
                except TaskError:
                    state = None
            if state is None or state["state"] == "disabled":
                if schedule_mode != SCHEDULE_MODE_CONTINUOUS and not calendar.get("items"):
                    return None
                return {"projection_version": 4, "captured_at": stamp(at),
                    "schedule_simulation_mode": schedule_mode, "calendar": calendar,
                    **({"schedule_source": schedule_source} if schedule_source is not None else {})}
            if state["state"] == "unknown" and "sleep_execution" not in state:
                if schedule_mode != SCHEDULE_MODE_CONTINUOUS and not calendar.get("items"):
                    return None
                return {"projection_version": 4, "captured_at": stamp(at),
                    "schedule_simulation_mode": schedule_mode, "calendar": calendar,
                    **({"schedule_source": schedule_source} if schedule_source is not None else {})}
            activities = self.service.activity_context(context)
            return {"revision": state["revision"], "hash": digest(state), "activity_hash": digest(activities),
                "projection_version": 3, "storage": self.service.records.storage(context),
                "sleep": self.service.body_clock.reference(context),
                "source": self.service.publish_projection(context, state, at=at),
                "captured_at": stamp(at), "activities": [{"id": a["id"], "revision": a["revision"]} for a in activities["items"]],
                "activity_unavailable": activities.get("state") == "unavailable", "activity_partial": activities["partial"],
                "schedule_simulation_mode": schedule_mode, "calendar": calendar,
                **({"schedule_source": schedule_source} if schedule_source is not None else {}),
                "humanized_intensity": self._intensity(context)}
        except TaskError:
            return None

    def _intensity(self, context):
        """Seal the expression dial with the state it applies to, so a replay is
        byte-identical and a later configuration edit cannot rewrite an old turn."""
        try:
            values, _token = self.service.settings(context)
            value = values.get(humanized.INTENSITY_FIELD)
        except (TaskError, ValueError, KeyError):
            return None
        return value if type(value) is int and 0 <= value <= 100 else None

    def contributions(self, snapshot, context, at, *, historical=False, fallback_source=None):
        if snapshot is None:
            return ()
        with validation_batch(self.service.runtime, at):
            return self._contributions(snapshot, context, at, historical=historical, fallback_source=fallback_source)

    def _contributions(self, snapshot, context, at, *, historical=False, fallback_source=None):
        old_fields = {"revision", "hash", "activity_hash"}
        fields = old_fields | {"captured_at", "activities", "activity_unavailable", "activity_partial"}
        if type(snapshot) is not dict:
            raise TaskError("state_snapshot_invalid", "invalid role state reference")
        schedule_only_fields = {"projection_version", "captured_at", "schedule_simulation_mode", "calendar", "schedule_source"}
        if set(snapshot) == schedule_only_fields:
            self._validate_calendar_snapshot(snapshot)
            if snapshot["projection_version"] != 4:
                raise TaskError("state_snapshot_invalid", "invalid schedule reference")
            calendar_items = self._calendar_items(snapshot["calendar"])
            if calendar_items:
                self._authorize_calendar_items(calendar_items, context, at)
            material = self._schedule_contribution(snapshot, context, at, fallback_source=fallback_source)
            return (material,)
        # The expression dial is additive: a batch sealed before it existed simply
        # omits the key, and the replay then injects no state expression at all.
        sealed_fields = set(snapshot)
        sealed_fields.discard("humanized_intensity")
        intensity = snapshot.get("humanized_intensity")
        schedule_fields = {"schedule_simulation_mode", "calendar", "schedule_source"}
        has_schedule = sealed_fields & schedule_fields
        base_fields = sealed_fields - schedule_fields
        if (has_schedule not in (set(), {"schedule_simulation_mode", "calendar"}, schedule_fields)
                or base_fields not in (old_fields, fields, fields | {"projection_version"},
                    fields | {"projection_version", "storage", "sleep", "source"})
                or intensity is not None and (type(intensity) is not int or not 0 <= intensity <= 100)
                or "projection_version" in snapshot and (type(snapshot["projection_version"]) is not int
                    or snapshot["projection_version"] not in (2, 3))
                or snapshot.get("projection_version") == 3 and snapshot.get("storage") not in ("role", "window")
                or "storage" in snapshot and snapshot.get("projection_version") != 3
                or has_schedule and (snapshot.get("schedule_simulation_mode") not in
                    (SCHEDULE_MODE_CONTINUOUS, SCHEDULE_MODE_ORCHESTRATED)
                    or self._invalid_calendar_snapshot(snapshot.get("calendar")))):
            raise TaskError("state_snapshot_invalid", "invalid role state reference")
        if historical and "captured_at" in snapshot:
            state = self.service.read_revision(context, snapshot["revision"],
                captured_at=datetime.fromisoformat(snapshot["captured_at"]), observed_at=at,
                storage=snapshot.get("storage", "window"), sleep_reference=snapshot.get("sleep"),
                include_sleep=snapshot.get("projection_version") == 3, injection=True)
            activities = ({"items": [], "partial": False, "state": "unavailable"} if snapshot["activity_unavailable"] else
                self.service.activity_context(context, references=snapshot["activities"], partial=snapshot["activity_partial"]))
        else:
            if self.service.records.storage(context) != snapshot.get("storage", "window"):
                raise TaskError("role_state_changed", "role body storage changed after capture")
            state = self.service.read(context, at=at, cycle_at=(datetime.fromisoformat(snapshot["captured_at"])
                if snapshot.get("projection_version") in (2, 3) else None), injection=True)
            activities = self.service.activity_context(context)
        if state.get("revision") != snapshot["revision"] or digest(state) != snapshot["hash"] or digest(activities) != snapshot["activity_hash"]:
            raise TaskError("role_state_changed", "original role state or actual activity changed")
        record = self.service.records.version(context, "current", snapshot["revision"], storage=snapshot.get("storage", "window")) if snapshot["revision"] else None
        source = source_ref(record["sources"][0]) if record else None
        if snapshot.get("projection_version") == 3:
            source = source_ref(snapshot["source"])
            self.service.access.evidence(context, (source,), at=at)
            require(self.service.access.sources.validate_reference_for_audience(source, recipient=context.actor.subject,
                observed_at=at, expected_content_hash=digest(encode(state))))
        # Activity sources can be newer than the last maintenance and need independent provenance.
        activity_sources = tuple(dict.fromkeys(source_ref(ref) for item in activities["items"] for ref in item["sources"]))
        schedule_mode = snapshot.get("schedule_simulation_mode", SCHEDULE_MODE_ORCHESTRATED)
        calendar = snapshot.get("calendar", {"items": [], "partial": False, "skipped": 0})
        calendar_items = self._calendar_items(calendar)
        if calendar_items:
            self._authorize_calendar_items(calendar_items, context, at)
        result = (ContextContribution("role-state", "role.state", "role_state", ContributionState.READY,
                                      encode({"simulation": state, "activities": activities,
                                              **({"observed_at": snapshot["captured_at"]}
                                                 if snapshot.get("projection_version") in (2, 3) else {})}), source),
                # The sealed dial travels next to the state it applies to; the
                # prompt block it produces is bounded and expressive only.
                ContextContribution("role-state-humanized", "role.state", "role_state_intensity",
                                    ContributionState.READY, encode({"intensity": intensity}), source),
                ) + tuple(
            ContextContribution("state-activity-source-" + str(index), ref.owner, "activity_source_reference",
                ContributionState.READY, "上述实际活动的原材料引用，不证明额外经历。", ref) for index, ref in enumerate(activity_sources))
        if schedule_mode == SCHEDULE_MODE_CONTINUOUS or calendar_items:
            schedule_snapshot = {"schedule_simulation_mode": schedule_mode, "calendar": calendar,
                                 "schedule_source": snapshot.get("schedule_source")}
            result += (self._schedule_contribution(schedule_snapshot, context, at,
                fallback_source=fallback_source),)
        return result

    @staticmethod
    def _invalid_calendar_snapshot(calendar):
        return (type(calendar) is not dict or set(calendar) != {"items", "partial", "skipped"}
            or type(calendar.get("items")) is not list or len(calendar["items"]) > 64
            or type(calendar.get("partial")) is not bool or type(calendar.get("skipped")) is not int
            or calendar["skipped"] < 0
            or any(type(item) is not dict or not {"kind", "state", "sources"} <= set(item)
                   or type(item["sources"]) is not list or len(item["sources"]) > 8
                   for item in calendar["items"]))

    @staticmethod
    def _calendar_items(calendar):
        return [item for item in calendar.get("items", ()) if type(item) is dict
                and item.get("kind") == "role_plan" and item.get("state") == "accepted"][:16]

    def _publish_schedule_projection(self, context, calendar, at, *, mode=SCHEDULE_MODE_CONTINUOUS):
        material = {"schedule_simulation_mode": mode,
                    "calendar": continuous_schedule_projection(calendar)}
        payload = encode(material)
        identifier = "role-calendar-view-" + digest([context.key, at, payload])
        reference = SourceRef(identifier, "role.calendar.projection", context.scope,
                              VersionRef(identifier, 1), at)
        dependencies = tuple(dict.fromkeys(source_ref(source)
            for item in self._calendar_items(calendar) for source in item["sources"]))
        with self.service.runtime.transaction():
            require(self.sources.publish(SourceRecord(reference, context.audience, digest(payload), payload), observed_at=at))
            link(self.service.runtime, reference, dependencies, at)
        return json.loads(encode(reference))

    def _schedule_contribution(self, snapshot, context, at, *, fallback_source=None):
        mode = snapshot.get("schedule_simulation_mode", SCHEDULE_MODE_CONTINUOUS)
        payload = encode({"schedule_simulation_mode": mode,
                          "calendar": continuous_schedule_projection(snapshot["calendar"])})
        raw_source = snapshot.get("schedule_source")
        if raw_source is None:
            source = fallback_source
        else:
            source = source_ref(raw_source)
            if source.owner != "role.calendar.projection" or source.scope != context.scope:
                raise TaskError("state_snapshot_invalid", "schedule source is invalid")
            self.service.access.evidence(context, (source,), at=at)
            require(self.service.access.sources.validate_reference_for_audience(source,
                recipient=context.actor.subject, observed_at=at, expected_content_hash=digest(payload)))
        if source is None or source.scope != context.scope:
            raise TaskError("schedule_context_source_missing", "schedule context has no governed source")
        return ContextContribution("role-schedule", "role.calendar", "role_schedule",
            ContributionState.READY, payload, source)

    @classmethod
    def _validate_calendar_snapshot(cls, snapshot):
        captured_at = snapshot.get("captured_at")
        try:
            parsed_at = datetime.fromisoformat(captured_at) if type(captured_at) is str else None
        except ValueError:
            parsed_at = None
        try:
            schedule_source = source_ref(snapshot["schedule_source"])
        except (KeyError, TypeError, ValueError):
            schedule_source = None
        if (type(snapshot.get("projection_version")) is not int or parsed_at is None
                or parsed_at.utcoffset() is None
                or snapshot.get("schedule_simulation_mode") not in
                    (SCHEDULE_MODE_CONTINUOUS, SCHEDULE_MODE_ORCHESTRATED)
                or cls._invalid_calendar_snapshot(snapshot.get("calendar"))):
            raise TaskError("state_snapshot_invalid", "invalid schedule reference")
        if schedule_source is None or schedule_source.owner != "role.calendar.projection":
            raise TaskError("state_snapshot_invalid", "schedule source is invalid")

    @staticmethod
    def _calendar_projection(calendar):
        return continuous_schedule_projection(calendar)

    def _authorize_calendar_items(self, items, context, at):
        if self.calendar is None:
            raise TaskError("calendar_context_unavailable", "original calendar owner is unavailable")
        self.calendar.access.check(context, "calendar.read", at=at)
        references = tuple(dict.fromkeys(source_ref(reference)
            for item in items for reference in item["sources"]))
        for offset in range(0, len(references), 8):
            self.calendar.access.evidence(context, references[offset:offset + 8], at=at)

    @staticmethod
    def instruction(materials):
        blocks, simulations, schedules, intensity = [], [], [], None
        for item in materials:
            if item.purpose == "role_state":
                simulations.append(json.loads(item.payload))
            elif item.purpose == "role_schedule":
                schedules.append(json.loads(item.payload))
            elif item.purpose == "role_state_intensity":
                try:
                    intensity = json.loads(item.payload)["intensity"]
                except (ValueError, KeyError, TypeError):
                    intensity = None
        for projection in simulations:
            simulation = projection["simulation"]
            dynamics = simulation.get("mood_dynamics") or {}
            blocks.append(reply_instruction(simulation))
            blocks.append(mood.prompts.reply(simulation.get("values", {}).get("mood"), dynamics,
                                             neutral=dynamics.get("neutral", True)))
        blocks.extend(schedule_instruction(item) for item in schedules)
        if intensity:
            # Zero injects nothing at all; a positive dial adds one bounded block.
            blocks.extend(humanized.instruction(projection["simulation"], intensity=intensity)
                          for projection in simulations)
        return "".join(blocks)
