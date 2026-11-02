"""Opt-in contact for significant changes between fresh role-location observations."""

import asyncio
from datetime import datetime, timedelta
import json

from companion_v2.agenda.planning_config import configured_weather_location, configured_weather_max_age
from companion_v2.agenda.weather_material import planning_weather_material
from companion_v2.conversation.messages import source_from_document
from companion_v2.execution.models import TaskContext, TaskError, digest, encode, require, stamp
from companion_v2.foundation.refs import SourceRef, VersionRef
from companion_v2.proactive.external_materials import WeatherSnapshot
from companion_v2.sources.governance import bind
from companion_v2.sources.models import SourceRecord

from .contact_models import ContactIntent
from .models import EvidenceSnapshot, Opportunity, Thought, ThoughtKind


SOURCE_OWNER = "proactive.weather_change"
SAMPLE_INTERVAL = timedelta(hours=1)
MAX_COMPARISON_GAP = timedelta(hours=6)
EVENT_COOLDOWN = timedelta(hours=3)
EVENT_WINDOW = timedelta(minutes=45)
RETRY = timedelta(minutes=5)
MEANING = ("两次观测均来自管理员配置的角色所在地，只说明该地点在两个观测时点之间的变化；"
           "不代表对方所在地、未来天气、官方预警或角色已出门。")


def _condition_group(code):
    if code in (0, 1, 2, 3):
        return "dry"
    if code in (45, 48):
        return "fog"
    if code in (51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82):
        return "rain"
    if code in (71, 73, 75, 77, 85, 86):
        return "snow"
    if code in (95, 96, 99):
        return "thunder"
    return None


def significant_change(previous, current):
    old, new = _condition_group(previous.get("weather_code")), _condition_group(current.get("weather_code"))
    if old is None or new is None:
        return None
    if old != new and (old != "dry" or new != "dry"):
        return "weather_type_changed"
    if abs(current["temperature_c"] - previous["temperature_c"]) >= 6:
        return "temperature_shift"
    if (previous["wind_kph"] is not None and current["wind_kph"] is not None
            and previous["wind_kph"] < 20 <= 35 <= current["wind_kph"]):
        return "wind_strengthened"
    return None


class WeatherChangeContactEvents:
    def __init__(self, contacts, role_state, port):
        if contacts.runtime is not role_state.runtime:
            raise ValueError("weather changes must share the original role runtime")
        self.contacts, self.role_state, self.port = contacts, role_state, port
        self.runtime = contacts.runtime
        self.retry_at = {}
        self.guard = bind(self.runtime, SOURCE_OWNER, self._validate_source)

    def close(self):
        self.guard.close()
        self.retry_at.clear()

    def _max_age(self, context):
        """The freshness window this window's planning settings currently allow."""
        settings, _ = self.role_state.settings(context)
        return configured_weather_max_age(settings)

    def _location(self, context, preferences):
        if not preferences.values["weather_change_enabled"] or preferences.reason("environment"):
            return None
        settings, _ = self.role_state.settings(context)
        location = configured_weather_location(settings["planning_weather_location"])
        return (location, digest(location)) if location is not None else None

    def _row(self, context):
        return self.runtime.read_one("SELECT * FROM proactive_weather_samples WHERE origin_key = ?", (context.key,))

    @staticmethod
    def _opportunity(context, event):
        return Opportunity(event["event_id"], context.scope,
                           datetime.fromisoformat(event["opened_at"]), datetime.fromisoformat(event["expires_at"]))

    def _pending(self, context, row, location_hash, at):
        if row is None or row["location_hash"] != location_hash or row["event_json"] is None:
            return None
        event = json.loads(row["event_json"])
        opportunity = self._opportunity(context, event)
        if not opportunity.opened_at <= at < opportunity.expires_at:
            return None
        task_id = self.contacts.task_id(context, opportunity)
        if (self.runtime.read_one("SELECT 1 FROM execution_tasks WHERE task_id = ?", (task_id,))
                or self.runtime.read_one("SELECT 1 FROM proactive_contacts WHERE task_id = ?", (task_id,))):
            return None
        return opportunity, event

    def select(self, context, preferences, at):
        if self.port is None or self.retry_at.get(context.key, at) > at:
            return None
        configured = self._location(context, preferences)
        if configured is None:
            return None
        _, location_hash = configured
        row = self._row(context)
        pending = self._pending(context, row, location_hash, at)
        if pending is not None:
            opportunity, event = pending
            return opportunity, {"event": event}
        if (row is not None and row["location_hash"] == location_hash
                and at < datetime.fromisoformat(row["checked_at"])
                + timedelta(minutes=preferences.values["weather_sample_minutes"])):
            return None
        opportunity = Opportunity("weather-change-poll-" + digest([context.key, stamp(at)]),
                                  context.scope, at, at + EVENT_WINDOW)
        return opportunity, {"poll": location_hash}

    def _save(self, context, location_hash, material, at, event, last_event_at):
        with self.runtime.transaction() as connection:
            connection.execute("""INSERT INTO proactive_weather_samples
                (origin_key, location_hash, sample_json, observed_at, checked_at, event_json, last_event_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(origin_key) DO UPDATE SET location_hash=excluded.location_hash,
                    sample_json=excluded.sample_json, observed_at=excluded.observed_at,
                    checked_at=excluded.checked_at, event_json=excluded.event_json,
                    last_event_at=excluded.last_event_at""",
                (context.key, location_hash, encode(material), material["observed_at"], stamp(at),
                 encode(event) if event is not None else None, last_event_at))

    @staticmethod
    def _body(event):
        return {"event": "role_location_weather_change", "provider": event["current"]["provider"],
                "previous": event["previous"], "current": event["current"],
                "change_kind": event["change_kind"], "meaning": MEANING}

    def _record(self, context, event):
        reference_id = "contact-weather-change-" + digest([context.key, event])
        reference = SourceRef(reference_id, SOURCE_OWNER, context.scope, VersionRef(reference_id, 1),
                              datetime.fromisoformat(event["opened_at"]),
                              datetime.fromisoformat(event["expires_at"]))
        body = self._body(event)
        payload = encode({**body, "_receipt": {"context": json.loads(encode(context)), "event": event}})
        return SourceRecord(reference, context.audience, digest(payload), payload)

    def _project(self, context, event, at):
        record = self._record(context, event)
        require(self.contacts.sources.publish(record, observed_at=at))
        require(self.contacts.sources.validate_reference_for_audience(
            record.reference, recipient=context.actor.subject, observed_at=at))
        opportunity = self._opportunity(context, event)
        marker = {"event": event, "source": json.loads(encode(record.reference))}
        thought = Thought("weather-change-" + digest(event), opportunity, ThoughtKind.CONVERSATION,
            "角色所在地的两次当前天气观测出现明显差异；只有与原对话自然相关时才简短提及，也可以跳过。",
            EvidenceSnapshot(context.scope, at, (record.reference,)), 1.0)
        return opportunity, (ContactIntent("environment", thought),), marker

    async def prepare(self, context, marker):
        if self.port is None or type(marker) is not dict:
            return None
        at = self.contacts.tasks.now()
        preferences = require(self.contacts.preferences.read(context, observed_at=at))
        configured = self._location(context, preferences)
        if configured is None:
            return None
        location, location_hash = configured
        if "event" in marker:
            row = self._row(context)
            pending = self._pending(context, row, location_hash, at)
            if pending is None or pending[1] != marker["event"]:
                return None
            return self._project(context, pending[1], at)
        if marker != {"poll": location_hash}:
            return None
        row = self._row(context)
        if (row is not None and row["location_hash"] == location_hash
                and at < datetime.fromisoformat(row["checked_at"])
                + timedelta(minutes=preferences.values["weather_sample_minutes"])):
            return None
        try:
            snapshot = await asyncio.wait_for(self.port.fetch(location), timeout=6)
        except Exception:
            self.retry_at[context.key] = self.contacts.tasks.now() + RETRY
            return None
        at = self.contacts.tasks.now()
        preferences = require(self.contacts.preferences.read(context, observed_at=at))
        if self._location(context, preferences) != configured:
            return None
        material = planning_weather_material(snapshot, location, at, max_age=self._max_age(context))
        if material is None or material.get("weather_code") is None or datetime.fromisoformat(material["valid_until"]) <= at:
            self.retry_at[context.key] = at + RETRY
            return None
        previous = json.loads(row["sample_json"]) if row is not None and row["location_hash"] == location_hash else None
        last_event_at = row["last_event_at"] if previous is not None else None
        event = None
        if previous is not None:
            gap = snapshot.observed_at - datetime.fromisoformat(previous["observed_at"])
            if timedelta(0) < gap <= MAX_COMPARISON_GAP:
                reason = significant_change(previous, material)
                if reason and (last_event_at is None or at >= datetime.fromisoformat(last_event_at) + EVENT_COOLDOWN):
                    expires = min(at + EVENT_WINDOW, datetime.fromisoformat(material["valid_until"]))
                    event = {"event_id": "weather-change-" + digest([context.key, location_hash,
                              previous["observed_at"], material["observed_at"], reason]),
                             "location_hash": location_hash, "previous": previous, "current": material,
                             "change_kind": reason, "opened_at": stamp(at), "expires_at": stamp(expires)}
                    last_event_at = stamp(at)
            elif gap <= timedelta(0):
                material = previous
        self._save(context, location_hash, material, at, event, last_event_at)
        self.retry_at.pop(context.key, None)
        return self._project(context, event, at) if event is not None else None

    def _validate_source(self, record, at):
        try:
            body = json.loads(record.payload)
            receipt = body.pop("_receipt")
            context = TaskContext.restore(receipt["context"])
            event = receipt["event"]
            preferences = require(self.contacts.preferences.read(context, observed_at=at))
            configured = self._location(context, preferences)
            row = self._row(context)
            if (configured is None or row is None or row["location_hash"] != configured[1]
                    or json.loads(row["event_json"]) != event or event["location_hash"] != configured[1]
                    or not self._opportunity(context, event).opened_at <= at < self._opportunity(context, event).expires_at
                    or body != self._body(event)
                    or record != self._record(context, event)):
                raise ValueError("original weather change is no longer current")
        except TaskError:
            raise
        except (KeyError, TypeError, ValueError, AttributeError) as error:
            raise TaskError("contact_weather_change_source_changed",
                            "original weather change is no longer current") from error

    def validate(self, context, marker, at):
        if type(marker) is not dict or set(marker) != {"event", "source"}:
            raise TaskError("contact_weather_change_invalid", "weather change needs its original observation")
        preferences = require(self.contacts.preferences.read(context, observed_at=at))
        configured = self._location(context, preferences)
        row = self._row(context)
        if (configured is None or row is None or row["location_hash"] != configured[1]
                or json.loads(row["event_json"]) != marker["event"]
                or not self._opportunity(context, marker["event"]).opened_at <= at < self._opportunity(context, marker["event"]).expires_at):
            raise TaskError("contact_weather_change_expired", "original weather change is no longer current")
        try:
            source = source_from_document(marker["source"])
        except (KeyError, TypeError, ValueError, AttributeError) as error:
            raise TaskError("contact_weather_change_invalid", "weather change source reference is invalid") from error
        if source.scope != context.scope or source.owner != SOURCE_OWNER:
            raise TaskError("contact_weather_change_invalid", "weather change belongs to another window")
        require(self.contacts.sources.validate_reference_for_audience(
            source, recipient=context.actor.subject, observed_at=at))
