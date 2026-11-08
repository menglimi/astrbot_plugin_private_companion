"""Voluntary rhythm: burst rounds, local-hour weighting and closing silence.

V1 shaped proactive timing with a burst follow-up, an hourly activity curve, an
intensity tier, a closing grace period and a per-moment share probability. V2
keeps those inputs in the existing ``proactive.contact`` preference group and
applies them where the single automatic opportunity is already decided, so no
second scheduler and no second send path appears.

Every value this module reads is registered configuration; every decision it
returns is a bound on an existing window (interval, idle gate, opportunity
width, 24-hour allowance). A rhythm decision can only hold a voluntary contact
back: it never widens an allowance, changes an audience or waives a rule.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
import json
from zoneinfo import ZoneInfo

from companion_v2.execution.models import digest, stamp

from .configuration import CURVE_CEILING, CURVE_FLOOR, INTENSITY_PRESETS, hour_activity_curve, intensity_preset


# An unclaimed burst slot is gone, exactly like an unclaimed ordinary slot: the
# window is the same five minutes the ordinary opportunity already uses.
SLOT_WINDOW = timedelta(minutes=5)
# Only role-share candidates (activity and journal sharing) answer to the share
# probability; reminders, alerts and weather keep their own event authorization.
SHARE_PURPOSES = frozenset({"life", "creation"})
BURST_ORIGIN_PURPOSES = frozenset({"chat", "life", "creation", "affection", "care", "play", "followup"})


def sample(parts):
    """A stable draw in [0, 1) from the sealed inputs; never a wall-clock random."""
    return int(digest(list(parts))[:12], 16) / float(16**12)


def effects(value):
    """The tier effects for an already stored value; unusable values mean ``off``."""
    try:
        name = intensity_preset(value)
    except (AttributeError, ValueError):
        name = "off"
    return name, dict(INTENSITY_PRESETS[name])


@dataclass(frozen=True)
class BurstSlot:
    """One bounded continuation of a burst round, tied to its original contact."""

    opened_at: datetime
    expires_at: datetime
    index: int
    limit: int
    gap_seconds: int
    origin_contact_id: str

    def document(self):
        return {"index": self.index, "limit": self.limit, "gap_seconds": self.gap_seconds,
                "opened_at": stamp(self.opened_at), "expires_at": stamp(self.expires_at),
                "origin_contact_id": self.origin_contact_id}


def tier(preferences):
    """The tier name and its effects for the current preferences."""
    return effects(preferences.values["proactive_intensity_preset"])


def base_interval_seconds(preferences):
    """The configured slot interval after the tier factor, without the unanswered slowdown."""
    _, preset = tier(preferences)
    return max(1, int(preferences.values["interval_seconds"] * preset["interval_factor"]))


def interval_seconds(preferences, *, unanswered):
    """The ordinary slot interval after the tier factor and the unanswered slowdown."""
    _, preset = tier(preferences)
    base = preferences.contact_interval_seconds(unanswered)
    return max(1, int(base * preset["interval_factor"]))


def idle_seconds(preferences):
    """The first opportunity after input, after the tier factor."""
    _, preset = tier(preferences)
    return max(0, int(preferences.values["idle_seconds"] * preset["idle_factor"]))


def burst_limit(preferences):
    """Messages allowed in one round; 1 means no burst continuation."""
    if not preferences.values["enable_proactive_burst"]:
        return 1
    _, preset = tier(preferences)
    configured = preferences.values["proactive_burst_max_messages"]
    if preset["burst_messages"] <= 0:
        return max(1, configured)
    return max(1, min(configured, preset["burst_messages"]))


def body_chars(preferences):
    """A body-length hint, or 0 when neither the field nor the tier sets one."""
    _, preset = tier(preferences)
    return preferences.values["proactive_intensity_body_chars"] or preset["body_chars"]


class ContactRhythm:
    def __init__(self, runtime):
        self.runtime = runtime

    # ---- intensity tier -------------------------------------------------
    tier = staticmethod(tier)
    base_interval_seconds = staticmethod(base_interval_seconds)
    interval_seconds = staticmethod(interval_seconds)
    idle_seconds = staticmethod(idle_seconds)
    burst_limit = staticmethod(burst_limit)
    body_chars = staticmethod(body_chars)

    # ---- hourly activity curve ------------------------------------------
    def hour_allows(self, context, preferences, opened_at, *, observed_at=None, curve=None):
        """Voluntary opportunity weighting by the relationship's local hour.

        The curve is opt-in: an unconfigured deployment keeps the exact slot grid
        it had, and an enabled curve only removes slots that fall in the
        relationship's less active local hours. The draw is seeded by the sealed
        slot, so polling never re-rolls it. Event producers may use the current
        observation's hour while sharing that stable slot: a long interval must
        not apply yesterday's afternoon weight to a news item found overnight.
        """
        if not preferences.values["proactive_hour_activity_enabled"]:
            return True
        if isinstance(curve, (tuple, list)) and len(curve) == 24:
            weights = tuple(max(CURVE_FLOOR, min(CURVE_CEILING, float(value))) for value in curve)
        else:
            weights = hour_activity_curve(preferences.values["proactive_hour_activity_curve"])
        zone = ZoneInfo(preferences.values["quiet_timezone"])
        weight = weights[(observed_at or opened_at).astimezone(zone).hour]
        share = min(1.0, weight / max(weights))
        return sample([context.key, "hour-activity", stamp(opened_at)]) < share

    # ---- burst rounds ----------------------------------------------------
    def last_contact(self, context):
        """The newest sealed contact of this window with its recorded purpose."""
        row = self.runtime.read_one("""SELECT c.contact_id, c.created_at, c.record_json,
                (SELECT MAX(d.delivered_at) FROM background_deliveries d
                 WHERE d.owner = 'proactive.contact' AND d.state = 'delivered'
                   AND json_extract(d.request_json, '$.source_ref') = c.contact_id) AS delivered_at
            FROM proactive_contacts c
            WHERE c.origin_key = ? ORDER BY c.created_at DESC, c.contact_id DESC LIMIT 1""", (context.key,))
        if row is None:
            return None
        try:
            purpose = json.loads(row["record_json"]).get("purpose")
        except (TypeError, ValueError):
            return None
        delivered_at = (datetime.fromisoformat(row["delivered_at"])
                        if row["delivered_at"] else None)
        return {"contact_id": row["contact_id"], "created_at": datetime.fromisoformat(row["created_at"]),
                "delivered_at": delivered_at,
                "purpose": purpose if isinstance(purpose, str) else ""}

    @staticmethod
    def _round_length(interaction):
        return interaction["contacts_since_last_input"] if interaction else 0

    def burst_slot(self, context, preferences, at, *, interaction, last_contact):
        """The next message of an open burst round, bounded by the tier and the gaps.

        A round is a run of confirmed voluntary contacts after the last accepted
        input. It continues from a conversational or share contact, so reminders,
        alerts and scheduled greetings never drag a burst behind them, and it never
        stacks behind an unresolved delivery: an unconfirmed message ends the round
        instead of adding another one.
        """
        limit = self.burst_limit(preferences)
        run = self._round_length(interaction)
        if limit <= 1 or not 0 < run < limit or preferences.reason("chat"):
            return None
        if interaction and interaction.get("unresolved_contacts"):
            return None
        if (last_contact is None or last_contact.get("delivered_at") is None
                or last_contact["purpose"] not in BURST_ORIGIN_PURPOSES):
            # A sealed candidate that never got a delivered receipt must not
            # authorise a follow-up burst: its created_at is not a send time.
            return None
        gap = self.burst_gap(context, preferences, last_contact["contact_id"])
        anchor_at = last_contact["delivered_at"]
        opened = anchor_at + timedelta(seconds=gap)
        expires = opened + min(timedelta(seconds=gap), SLOT_WINDOW)
        if at < opened or at >= expires:
            return None
        return BurstSlot(opened, expires, run, limit, gap, last_contact["contact_id"])

    def burst_gap(self, context, preferences, origin_contact_id):
        """The deterministic gap after one contact; the same round never re-rolls it."""
        low = preferences.values["proactive_burst_gap_min_seconds"]
        high = max(low, preferences.values["proactive_burst_gap_max_seconds"])
        gap = low + int(sample([context.key, origin_contact_id, "burst-gap"]) * (high - low + 1))
        return min(gap, high)

    def validate_burst(self, context, preferences, marker, at, *, interaction):
        """Re-derive the sealed burst slot from its own origin contact and round.

        The origin contact is read by ID, never as "the newest contact", because
        the draft being validated is itself already sealed in this transaction.
        """
        expected = {"index", "limit", "gap_seconds", "opened_at", "expires_at", "origin_contact_id"}
        if not isinstance(marker, dict) or set(marker) != expected:
            return False
        limit = self.burst_limit(preferences)
        run = self._round_length(interaction)
        if (limit <= 1 or preferences.reason("chat") or not 0 < marker["index"] < limit
                or not 0 < run <= marker["index"] or marker["limit"] != limit):
            return False
        if interaction and interaction.get("unresolved_contacts"):
            return False
        row = self.runtime.read_one("""SELECT c.contact_id, c.created_at, c.record_json,
                (SELECT MAX(d.delivered_at) FROM background_deliveries d
                 WHERE d.owner = 'proactive.contact' AND d.state = 'delivered'
                   AND json_extract(d.request_json, '$.source_ref') = c.contact_id) AS delivered_at
            FROM proactive_contacts c
            WHERE c.contact_id = ? AND c.origin_key = ?""", (marker["origin_contact_id"], context.key))
        if row is None:
            return False
        try:
            purpose = json.loads(row["record_json"]).get("purpose")
        except (TypeError, ValueError):
            return False
        if purpose not in BURST_ORIGIN_PURPOSES:
            return False
        if row["delivered_at"] is None:
            return False
        gap = self.burst_gap(context, preferences, row["contact_id"])
        if gap != marker["gap_seconds"]:
            return False
        opened = datetime.fromisoformat(row["delivered_at"]) + timedelta(seconds=gap)
        expires = opened + min(timedelta(seconds=gap), SLOT_WINDOW)
        return (stamp(opened) == marker["opened_at"]
                and stamp(expires) == marker["expires_at"] and opened <= at < expires)

    # ---- closing silence -------------------------------------------------
    def closing_hold(self, preferences, at, *, interaction, last_contact):
        """The instant a closing silence ends, while a run of contacts is unwinding.

        Continuous voluntary contact ends in a held silence instead of an endless
        monologue; a new accepted input, or the end of the grace period, releases
        the ordinary slot again.
        """
        grace = preferences.values["proactive_closing_grace_minutes"]
        threshold = preferences.values["proactive_closing_contacts_threshold"]
        if grace <= 0 or threshold <= 0 or last_contact is None:
            return None
        if self._round_length(interaction) < threshold:
            return None
        hold = last_contact["created_at"] + timedelta(minutes=grace)
        return hold if at < hold else None

    # ---- share probability ----------------------------------------------
    def share_allowed(self, context, preferences, opportunity_id, *, purpose):
        """V1's proactive share probability, applied to role-share candidates only.

        The draw is seeded by the sealed opportunity, so a suppressed share is
        suppressed for good instead of reappearing on the next tick.
        """
        if purpose not in SHARE_PURPOSES:
            return True
        probability = preferences.values["proactive_share_probability"]
        if probability >= 100:
            return True
        if probability <= 0:
            return False
        return sample([context.key, "share-probability", opportunity_id, purpose]) < probability / 100.0
