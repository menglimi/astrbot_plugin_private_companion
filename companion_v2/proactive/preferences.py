"""Time-aware contact policy consumed at both generation and dispatch boundaries."""

from dataclasses import dataclass
from datetime import datetime
import math
from types import MappingProxyType
from typing import Mapping
from zoneinfo import ZoneInfo

from companion_v2.configuration.models import ConfigurationSnapshot, Scalar
from companion_v2.execution.models import TaskError, require
from companion_v2.execution.service import outcome
from companion_v2.foundation.refs import normalize_utc

from .configuration import GROUP, PURPOSES, dedup_policies, local_time, pause_time, target_for


DEDUP_PURPOSE_POLICY = {
    "affection": "semantic", "care": "semantic", "play": "semantic", "repair": "semantic",
    "chat": "semantic", "greeting": "daily_slot", "life": "life_event",
    "creation": "content_fingerprint", "news": "content_fingerprint",
    "followup": "conversation_anchor", "invitation": "event_anchor", "reminder": "event_anchor",
    "environment": "event_revision", "meal": "daily_slot", "screen": "semantic",
}
DEDUP_EVENT_POLICY = (
    ("official_alert_event", "event_revision"), ("weather_change_event", "event_revision"),
    ("weather_event", "semantic"), ("calendar_event", "event_anchor"),
    ("activity_event", "life_event"), ("journal_event", "life_event"),
    ("date_event", "daily_slot"), ("meal_event", "daily_slot"),
    ("news_event", "content_fingerprint"), ("balance_event", "event_anchor"),
    ("reality_location_event", "event_revision"),
)


@dataclass(frozen=True)
class ContactPreferences:
    snapshot: ConfigurationSnapshot
    values: Mapping[str, Scalar]
    observed_at: datetime
    local_values: Mapping[str, Scalar] | None = None

    def __post_init__(self):
        object.__setattr__(self, "values", MappingProxyType(dict(self.values)))

    def automatic_reason(self):
        for values in (self.values, self.local_values):
            if values is None:
                continue
            if values.get("proactive_intensity_preset") == "off":
                return "contact_intensity_off"
            if values.get("automatic_enabled") is False:
                return "contact_automatic_disabled"
        return None

    def reason(self, purpose, *, ignore_time_restrictions=False, ignore_master_switch=False):
        if purpose not in PURPOSES:
            raise TaskError("unknown_contact_purpose", "contact purpose must be registered")
        if self.local_values is not None:
            original = ContactPreferences(self.snapshot, self.local_values, self.observed_at)
            local_reason = original.reason(purpose, ignore_time_restrictions=ignore_time_restrictions,
                                           ignore_master_switch=ignore_master_switch)
            if local_reason:
                return local_reason
        values = self.values
        if (not values["enabled"] and not ignore_master_switch) or not values[purpose + ".enabled"]:
            return "proactive_disabled"
        if not ignore_time_restrictions:
            if any(until is not None and self.observed_at < until for until in
                   (pause_time(values["pause_until"]), pause_time(values[purpose + ".pause_until"]))):
                return "proactive_paused"
        if not ignore_time_restrictions and values["quiet_enabled"]:
            now = self.observed_at.astimezone(ZoneInfo(values["quiet_timezone"])).time()
            start, end = local_time(values["quiet_start"]), local_time(values["quiet_end"])
            if (start == end or (start <= now < end if start < end else now >= start or now < end)):
                return "proactive_quiet_hours"
        return None

    def require_contact(self, purpose, *, ignore_time_restrictions=False, ignore_master_switch=False):
        reason = self.reason(purpose, ignore_time_restrictions=ignore_time_restrictions,
                             ignore_master_switch=ignore_master_switch)
        if reason:
            raise TaskError(reason, "current contact preferences do not permit this contact")

    def weight(self, purpose):
        return self.values[purpose + ".weight"]

    def dedup_applies(self, purpose, origin=None):
        if not self.values["dedup_enabled"]:
            return False
        policy = next((route for marker, route in DEDUP_EVENT_POLICY if origin and marker in origin),
                      DEDUP_PURPOSE_POLICY[purpose])
        return policy in dedup_policies(self.values["dedup_policies"])

    def unanswered_paused(self, count):
        threshold = self.values["unanswered_pause_after"]
        return threshold > 0 and count >= threshold

    def contact_interval_seconds(self, count):
        start = self.values["unanswered_slowdown_start"]
        steps = max(0, count - start + 1)
        multiplier = min(self.values["unanswered_max_interval_multiplier"], 1.0 + steps * 0.35)
        return math.ceil(self.values["interval_seconds"] * multiplier)


class ProactivePreferences:
    def __init__(self, configuration, tasks):
        self.configuration, self.tasks = configuration, tasks

    @outcome
    def read(self, context, *, observed_at):
        at = normalize_utc(observed_at, field="contact policy time")
        require(self.tasks.check_context_access(context, action=GROUP, observed_at=at))
        snapshot = require(self.configuration.read(context.actor, target=target_for(context), group_id=GROUP, observed_at=at))
        from companion_v2.relationships.account_link_settings import group_for_target, unshared
        local_values = None
        group = group_for_target(self.configuration.runtime, target_for(context))
        if group and group.get("sharing", {}).get("contact"):
            with unshared():
                local = require(self.configuration.read(context.actor, target=target_for(context), group_id=GROUP, observed_at=at))
            local_values = {key.removeprefix(GROUP + "."): value for key, value in local.values.items()}
        return ContactPreferences(snapshot, {key.removeprefix(GROUP + "."): value for key, value in snapshot.values.items()}, at, local_values)
