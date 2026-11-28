"""Morning contact producer: one role greeting, then at most one wake nudge.

The producer only reads typed routine/device/receipt data and returns a normal
``ContactIntent``.  It never sends, never writes its own ledger and never
bypasses the existing proactive gates: the opportunity is queued through the
same ``proactive_contacts`` task id as every other automatic contact.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, time, timedelta
import json
from zoneinfo import ZoneInfo

from companion_v2.execution.models import TaskError, digest, require, stamp

from .configuration import GROUP
from .contact_models import ContactIntent
from .models import EvidenceSnapshot, Opportunity, Thought, ThoughtKind
from .morning_rhythm import (
    MORNING_INSTRUCTION, NUDGE_INSTRUCTION, MorningContact, RoutineSample,
    estimate, manual_wake_window, morning_plan, predict_wake_window, rhythm_adjustment, role_wake_at,
)
from .wake_detection import (
    AWAKE_STATES, NEGATIVE, SIGNALS, evidence_from_mobile_snapshot,
    evidence_from_wakeup_session, intent_summary, observe,
)


SLOT_WINDOW = timedelta(minutes=5)
MORNING_KIND = "morning"
MARKER_KEYS = frozenset({"kind", "action", "episode_key", "local_date", "window_start",
                         "window_end", "confidence", "source", "samples"})




def _minutes(value, fallback):
    try:
        hour, minute = str(value).split(":", 1)
        parsed = int(hour) * 60 + int(minute)
    except (TypeError, ValueError):
        return fallback
    return parsed if 0 <= parsed < 1440 else fallback


class WakeContactEvents:
    """Reuse the contact ledger for role-morning and wake-nudge opportunities."""

    def __init__(self, contacts, role_state=None, port=None):
        self.contacts, self.runtime = contacts, contacts.runtime
        self.role_state = role_state
        self.port = port
        self.last_diagnostic = None

    # ---- reads -----------------------------------------------------------
    def _body_sleep(self, context, at):
        if self.role_state is None:
            return {}
        try:
            state = self.role_state.read(context, at=at)
        except (TaskError, ValueError, TypeError, KeyError, AttributeError):
            return {}
        if not isinstance(state, Mapping):
            return {}
        sleep = state.get("sleep_execution")
        if isinstance(sleep, Mapping):
            return sleep
        body = state.get("body_sleep")
        return body if isinstance(body, Mapping) else {}

    def _wake_window(self, preferences, profile, at, zone):
        if not preferences.values["routine_learning_enabled"]:
            return manual_wake_window(
                at=at,
                zone=zone.key,
                morning_start_minutes=_minutes(preferences.values["wake_morning_start"], 300),
                morning_end_minutes=_minutes(preferences.values["wake_morning_end"], 690),
            )
        return predict_wake_window(
            profile,
            at=at,
            zone=zone.key,
            confidence_floor=preferences.values["morning_wake_confidence_floor"],
        )

    def _role_woke_at(self, context, at, profile, zone, confidence_floor=0.2, *, window=None):
        """The role's own wake receipt, or the active window opening as fallback.

        Role state is optional. An installation without an enabled body clock
        still gets the configured daily morning rhythm: once the wake window
        opens, the role may greet first. This never uses user silence as
        evidence.
        """
        woke = role_wake_at(self._body_sleep(context, at))
        if woke is not None:
            return woke
        active_window = window or predict_wake_window(profile, at=at, zone=zone.key,
                                                      confidence_floor=confidence_floor)
        if active_window.source == "manual":
            return active_window.start_at
        return active_window.start_at - timedelta(minutes=15)

    def _port_snapshot(self, context):
        if not callable(self.port):
            return {}
        try:
            value = self.port(context)
        except Exception:
            return {}
        return value if isinstance(value, Mapping) else {}

    def _routine_samples(self, context, at, *, zone, window_days):
        cutoff = at - timedelta(days=max(1, int(window_days)))
        rows = self.runtime.read_all("""SELECT i.event_json
            FROM conversation_inputs i JOIN conversation_batches b ON b.batch_id = i.batch_id
            WHERE b.origin_key = ? AND i.withdrawn = 0
              AND json_extract(i.event_json, '$.received_at') >= ?
            ORDER BY json_extract(i.event_json, '$.received_at') ASC LIMIT 600""",
            (context.key, stamp(cutoff)))
        times_by_day = {}
        for row in rows:
            try:
                payload = json.loads(row["event_json"])
                received = datetime.fromisoformat(payload["received_at"])
            except (TypeError, ValueError, KeyError):
                continue
            if received.tzinfo is None:
                continue
            local = received.astimezone(zone)
            times_by_day.setdefault(local.date().isoformat(), []).append(received)
        contacts_by_day = self._morning_contacts_by_day(context, zone)
        samples = []
        for day in sorted(set(times_by_day) | set(contacts_by_day)):
            moments = sorted(times_by_day.get(day, ()))
            first = moments[0].astimezone(zone) if moments else None
            contact = contacts_by_day.get(day)
            replied, delay = False, None
            if contact is not None:
                sent = contact["sent_at"]
                after = [moment for moment in moments if moment > sent]
                if after:
                    replied = True
                    delay = max(0, int((after[0] - sent).total_seconds() // 60))
            samples.append(RoutineSample(day,
                wake_minutes=(first.hour * 60 + first.minute) if first is not None else None,
                sleep_minutes=None,
                morning_contact=contact is not None,
                replied_after_contact=replied,
                response_delay_minutes=delay))
        return tuple(samples)

    def _morning_contacts_by_day(self, context, zone):
        rows = self.runtime.read_all("""SELECT c.created_at, c.record_json,
                (SELECT MAX(d.delivered_at) FROM background_deliveries d
                 WHERE d.owner = 'proactive.contact' AND d.state = 'delivered'
                   AND json_extract(d.request_json, '$.source_ref') = c.contact_id) AS delivered_at
            FROM proactive_contacts c
            WHERE c.origin_key = ?
              AND json_extract(c.record_json, '$.automatic_origin.morning_event.kind') = 'morning'
            ORDER BY c.created_at DESC LIMIT 120""", (context.key,))
        found = {}
        for row in rows:
            try:
                saved = json.loads(row["record_json"])
            except (TypeError, ValueError):
                continue
            if saved.get("decision") == "skip":
                continue
            marker = (saved.get("automatic_origin") or {}).get("morning_event")
            if not isinstance(marker, Mapping) or marker.get("action") != "role_morning":
                continue
            raw = row["delivered_at"] or row["created_at"]
            try:
                sent = datetime.fromisoformat(raw)
            except (TypeError, ValueError):
                continue
            if sent.tzinfo is None:
                continue
            day = sent.astimezone(zone).date().isoformat()
            if day not in found or sent > found[day]["sent_at"]:
                found[day] = {"sent_at": sent}
        return found

    def profile(self, context, at):
        preferences = require(self.contacts.preferences.read(context, observed_at=at))
        return self._profile(context, preferences, at)

    def _profile(self, context, preferences, at):
        zone = ZoneInfo(preferences.values["quiet_timezone"])
        if not preferences.values["routine_learning_enabled"]:
            return estimate((), now=at, zone=zone.key)
        samples = self._routine_samples(context, at, zone=zone,
            window_days=preferences.values["routine_window_days"])
        return estimate(samples, now=at, zone=zone.key,
                        window_days=preferences.values["routine_window_days"])

    def adjusted_curve(self, context, preferences, at, curve):
        if not preferences.values["morning_chain_enabled"] or not preferences.values["routine_learning_enabled"]:
            return None
        try:
            return rhythm_adjustment(self._profile(context, preferences, at)).apply(curve)
        except (TaskError, ValueError, TypeError, KeyError):
            return None

    def _evidence(self, context, at):
        snapshot = self._port_snapshot(context)
        mobile = snapshot.get("mobile") if isinstance(snapshot.get("mobile"), Mapping) else {}
        wakeup = snapshot.get("wakeup") if isinstance(snapshot.get("wakeup"), Mapping) else {}
        return (*evidence_from_mobile_snapshot(mobile, at=at),
                *evidence_from_wakeup_session(wakeup, at=at))

    def _morning_contact(self, context, observation, at, interaction, zone):
        rows = self.runtime.read_all("""SELECT c.contact_id, c.created_at, c.record_json,
                (SELECT MAX(d.delivered_at) FROM background_deliveries d
                 WHERE d.owner = 'proactive.contact' AND d.state = 'delivered'
                   AND json_extract(d.request_json, '$.source_ref') = c.contact_id) AS delivered_at
            FROM proactive_contacts c
            WHERE c.origin_key = ?
              AND json_extract(c.record_json, '$.automatic_origin.morning_event.kind') = 'morning'
            ORDER BY c.created_at DESC LIMIT 60""", (context.key,))
        role_sent, nudge_count, last_nudge = None, 0, None
        for row in rows:
            try:
                saved = json.loads(row["record_json"])
            except (TypeError, ValueError):
                continue
            if saved.get("decision") == "skip":
                continue
            marker = (saved.get("automatic_origin") or {}).get("morning_event")
            if not isinstance(marker, Mapping):
                continue
            action = marker.get("action")
            if action not in ("role_morning", "wake_nudge"):
                continue
            raw = row["delivered_at"] or row["created_at"]
            try:
                sent = datetime.fromisoformat(raw)
            except (TypeError, ValueError):
                continue
            if sent.tzinfo is None:
                continue
            if action == "role_morning":
                if role_sent is None or sent > role_sent:
                    role_sent = sent
            elif marker.get("episode_key") == observation.episode_key and row["delivered_at"]:
                nudge_count += 1
                if last_nudge is None or sent > last_nudge:
                    last_nudge = sent
        if role_sent is None:
            return MorningContact()
        replied = None
        if isinstance(interaction, Mapping) and interaction.get("last_input_at"):
            try:
                latest = datetime.fromisoformat(interaction["last_input_at"])
            except (TypeError, ValueError):
                latest = None
            if latest is not None and latest.tzinfo is not None and latest > role_sent:
                replied = latest
        return MorningContact(sent_at=role_sent, user_replied_at=replied,
                              nudge_count=nudge_count, last_nudge_at=last_nudge)

    def _daily_count(self, context, at, zone):
        local = at.astimezone(zone)
        start = datetime.combine(local.date(), time.min, tzinfo=zone)
        end = start + timedelta(days=1)
        row = self.runtime.read_one("""SELECT COUNT(*) FROM proactive_contacts
            WHERE origin_key = ?
              AND json_extract(record_json, '$.automatic_origin.morning_event.kind') = 'morning'
              AND json_extract(record_json, '$.automatic_origin.morning_event.action') = 'wake_nudge'
              AND COALESCE(json_extract(record_json, '$.decision'), 'send') != 'skip'
              AND created_at >= ? AND created_at < ?""",
            (context.key, stamp(start), stamp(end)))
        return row[0] if row is not None else 0

    def select(self, context, preferences, at, *, source=None, interaction=None):
        self.last_diagnostic = None
        if not preferences.values["morning_chain_enabled"]:
            self.last_diagnostic = {"reason": "morning_chain_disabled"}
            return None
        if not preferences.values["morning_role_greeting_enabled"] and not preferences.values["morning_nudge_enabled"]:
            self.last_diagnostic = {"reason": "morning_actions_disabled"}
            return None
        reason = preferences.reason("greeting")
        if reason:
            self.last_diagnostic = {"reason": reason}
            return None
        zone = ZoneInfo(preferences.values["quiet_timezone"])
        profile = self._profile(context, preferences, at)
        window = self._wake_window(preferences, profile, at, zone)
        evidence = self._evidence(context, at)
        observation = observe(evidence, at=at, zone=zone.key)
        role_woke = self._role_woke_at(context, at, profile, zone,
                                       preferences.values["morning_wake_confidence_floor"],
                                       window=window)
        if interaction is None and self.contacts.history is not None:
            interaction = self.contacts.history.interaction(context, at, action=GROUP)
        contact = self._morning_contact(context, observation, at, interaction, zone)
        negative = None
        for item in evidence:
            if SIGNALS[item.kind].polarity == NEGATIVE:
                negative = item.observed_at if negative is None else max(negative, item.observed_at)
        last_input = None
        raw_input = (interaction or {}).get("last_input_at")
        if isinstance(raw_input, str):
            try:
                last_input = datetime.fromisoformat(raw_input)
            except ValueError:
                last_input = None
        step = morning_plan(profile, at=at, zone=zone.key, role_woke_at=role_woke,
                            contact=contact,
                            user_last_input_at=last_input,
                            negative_evidence_at=negative,
                            nudge_delay_minutes=preferences.values["morning_nudge_delay_minutes"],
                            max_nudges=preferences.values["morning_nudge_max_attempts"],
                            morning_start_minutes=_minutes(preferences.values["wake_morning_start"], 300),
                            morning_end_minutes=_minutes(preferences.values["wake_morning_end"], 690),
                            wake_confidence_floor=preferences.values["morning_wake_confidence_floor"],
                            window=window)
        self.last_diagnostic = {"reason": step.reason, "next_at": step.due_at,
                                "state": observation.state, "confidence": observation.confidence}
        if step.action == "role_morning" and not preferences.values["morning_role_greeting_enabled"]:
            self.last_diagnostic = {"reason": "role_morning_disabled", "next_at": step.due_at}
            return None
        if step.action == "wake_nudge" and not preferences.values["morning_nudge_enabled"]:
            self.last_diagnostic = {"reason": "nudge_disabled", "next_at": step.due_at}
            return None
        if step.action not in ("role_morning", "wake_nudge"):
            return None
        local_date = at.astimezone(zone).date().isoformat()
        episode_key = (f"morning-role:{local_date}" if step.action == "role_morning"
                       else (observation.episode_key or f"morning-nudge:{local_date}"))
        if step.action == "wake_nudge":
            if observation.state not in AWAKE_STATES:
                self.last_diagnostic = {"reason": "wake_state_not_confirmed", "state": observation.state}
                return None
            if observation.confidence < preferences.values["wake_contact_min_confidence"]:
                self.last_diagnostic = {"reason": "wake_confidence_below_threshold",
                                        "confidence": observation.confidence}
                return None
            if observation.woke_at is not None and at - observation.woke_at > timedelta(
                    minutes=preferences.values["wake_contact_window_minutes"]):
                self.last_diagnostic = {"reason": "wake_window_elapsed"}
                return None
            if preferences.values["wake_require_device_evidence"] and not any(
                    item.source.startswith("mobile.") for item in observation.evidence):
                self.last_diagnostic = {"reason": "device_evidence_required"}
                return None
            if self._daily_count(context, at, zone) >= preferences.values["wake_contact_max_daily"]:
                self.last_diagnostic = {"reason": "wake_daily_cap"}
                return None
        opportunity = Opportunity("morning-" + digest([context.key, step.action, episode_key]),
            context.scope, at, at + SLOT_WINDOW)
        task_id = self.contacts.task_id(context, opportunity)
        if (self.runtime.read_one("SELECT 1 FROM execution_tasks WHERE task_id = ?", (task_id,))
                or self.runtime.read_one("SELECT 1 FROM proactive_contacts WHERE task_id = ?", (task_id,))):
            self.last_diagnostic = {"reason": "slot_already_processed"}
            return None
        summary = MORNING_INSTRUCTION if step.action == "role_morning" else (
            NUDGE_INSTRUCTION + "\n" + intent_summary(observation))
        thought = Thought("morning-thought-" + digest([context.key, step.action, episode_key]),
            opportunity, ThoughtKind.CONVERSATION, summary,
            EvidenceSnapshot(context.scope, at, (source,) if source is not None else ()), 1.0)
        marker = {"kind": MORNING_KIND, "action": step.action, "episode_key": episode_key,
                  "local_date": local_date, "window_start": stamp(step.window.start_at),
                  "window_end": stamp(step.window.end_at), "confidence": observation.confidence,
                  "source": step.window.source, "samples": profile.sample_days}
        return opportunity, (ContactIntent("greeting", thought),), marker

    def validate(self, context, marker, at, *, interaction=None):
        if not isinstance(marker, Mapping) or set(marker) != MARKER_KEYS or marker.get("kind") != MORNING_KIND:
            raise TaskError("contact_morning_invalid", "morning candidate marker is invalid")
        preferences = require(self.contacts.preferences.read(context, observed_at=at))
        preferences.require_contact("greeting")
        if not preferences.values["morning_chain_enabled"]:
            raise TaskError("contact_morning_disabled", "the morning chain was disabled after selection")
        zone = ZoneInfo(preferences.values["quiet_timezone"])
        local_date = at.astimezone(zone).date().isoformat()
        if marker.get("local_date") != local_date:
            raise TaskError("contact_morning_changed", "the morning candidate belongs to another local day")
        profile = self._profile(context, preferences, at)
        window = self._wake_window(preferences, profile, at, zone)
        if stamp(window.start_at) != marker.get("window_start") or stamp(window.end_at) != marker.get("window_end"):
            raise TaskError("contact_morning_changed", "the wake window changed after selection")
        action = marker.get("action")
        if action == "role_morning":
            if not preferences.values["morning_role_greeting_enabled"]:
                raise TaskError("contact_morning_disabled", "role morning greeting was disabled")
            role_woke = self._role_woke_at(context, at, profile, zone,
                                       preferences.values["morning_wake_confidence_floor"],
                                       window=window)
            if at - role_woke > timedelta(minutes=180):
                raise TaskError("contact_morning_expired", "the role woke too long ago for this morning greeting")
            latest = window.end_at if window.source == "manual" else window.end_at + timedelta(minutes=60)
            if at > latest:
                raise TaskError("contact_morning_expired", "the wake window has closed")
            return True
        if action == "wake_nudge":
            if not preferences.values["morning_nudge_enabled"]:
                raise TaskError("contact_morning_disabled", "wake-up nudge was disabled")
            evidence = self._evidence(context, at)
            observation = observe(evidence, at=at, zone=zone.key)
            if observation.episode_key != marker.get("episode_key"):
                raise TaskError("contact_morning_changed", "the wake episode changed after selection")
            if observation.state not in AWAKE_STATES:
                raise TaskError("contact_morning_changed", "the user wake evidence is no longer current")
            if observation.confidence < preferences.values["wake_contact_min_confidence"]:
                raise TaskError("contact_morning_changed", "the wake confidence fell below the threshold")
            if not window.start_at <= at <= window.end_at:
                raise TaskError("contact_morning_expired", "the learned wake window is no longer open")
            if interaction is None and self.contacts.history is not None:
                interaction = self.contacts.history.interaction(context, at, action=GROUP)
            contact = self._morning_contact(context, observation, at, interaction, zone)
            if contact.user_replied_at is not None:
                raise TaskError("contact_morning_superseded", "the user already took the conversation")
            if contact.nudge_count >= preferences.values["morning_nudge_max_attempts"]:
                raise TaskError("contact_morning_expired", "the wake-up nudge budget is exhausted")
            return True
        raise TaskError("contact_morning_invalid", "unknown morning action")
