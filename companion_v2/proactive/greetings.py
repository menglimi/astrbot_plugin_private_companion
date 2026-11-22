"""Day anchors and one bounded insomnia window, in the relationship's local time.

V1 produced one morning, noon and evening greeting window per local day and kept
one low-pressure message for nights the role was awake. V2 keeps the same anchors
as explicit local windows, computed on the relationship's existing
``quiet_timezone``, and routes both through the single automatic contact
opportunity: one durable decision per anchor and local day, and one per night
slot.

Neither path can bypass a rule: an anchor or night message is still a voluntary
contact that answers to its purpose switch, the pause, the quiet hours, the
24-hour allowance and every send-boundary recheck. The configured night window in
particular does **not** exempt a message from quiet hours -- a night message is
only possible where the relationship's quiet hours leave room for it.
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from companion_v2.execution.models import TaskError, digest, require, stamp

from .configuration import anchor_window, local_time
from .contact_models import ContactIntent
from .models import EvidenceSnapshot, Opportunity, Thought, ThoughtKind


ANCHOR_PURPOSE = "greeting"
INSOMNIA_PURPOSE = "care"
DAY_ANCHORS = ("morning_greeting", "noon_greeting", "evening_greeting")
ANCHOR_MEANING = {
    "morning_greeting": ("早上有一个轻轻碰面的机会", "早上"),
    "noon_greeting": ("午间有一个自然开口的机会", "午间"),
    "evening_greeting": ("晚上有一个轻轻碰面的机会", "晚上")}


def window_bounds(value):
    """A configured anchor window as two validated local times."""
    start, _, end = anchor_window(value).partition("-")
    return local_time(start), local_time(end)


def _minutes(wall):
    return wall.hour * 60 + wall.minute


class GreetingContactEvents:
    """Morning/noon/evening anchors plus the bounded insomnia night slot."""

    def __init__(self, contacts, role_state=None, health_source=None):
        if role_state is not None and contacts.runtime is not role_state.runtime:
            raise ValueError("greeting contacts must share the original role runtime")
        self.contacts, self.role_state, self.runtime = contacts, role_state, contacts.runtime
        self.health_source = health_source

    def _user_sleep_hint(self, context, at):
        from .sleep_context import from_health_source
        sleep = from_health_source(self.health_source, at)
        return sleep.morning_hint() if sleep is not None else ""

    # ---- shared helpers --------------------------------------------------
    @staticmethod
    def _zone(preferences):
        return ZoneInfo(preferences.values["quiet_timezone"])

    def _idle_ok(self, preferences, at, interaction):
        """The shorter anchor idle gate; a very recent input postpones the greeting."""
        minutes = preferences.values["greeting_idle_minutes"]
        if minutes <= 0 or interaction is None:
            return True
        last = interaction.get("last_input_at")
        if not last:
            return True
        return at >= datetime.fromisoformat(last) + timedelta(minutes=minutes)

    def _sleep_hint(self, context, at):
        """The role's own live sleep condition as a hint only, when it is readable."""
        if self.role_state is None:
            return ""
        try:
            state = self.role_state.read(context, at=at)
        except (TaskError, ValueError, TypeError, KeyError):
            return ""
        conditions = (state.get("values") or {}).get("conditions") or []
        texts = [item.get("text", "") for item in conditions
                 if isinstance(item, dict) and item.get("kind") == "sleep" and item.get("text")]
        if texts:
            return texts[0][:200]
        body_clock = getattr(self.role_state, "body_clock", None)
        if body_clock is not None:
            try:
                body_sleep = (state.get("body_sleep") or {})
                if isinstance(body_sleep, dict) and body_sleep.get("state") == "awake":
                    last = body_sleep.get("last_completed")
                    if isinstance(last, dict) and isinstance(last.get("completed_at"), str):
                        woke_at = datetime.fromisoformat(last["completed_at"])
                        elapsed_min = int((at - woke_at).total_seconds() // 60)
                        if 0 <= elapsed_min <= 90:
                            slept_min = max(1, int((last.get("slept_seconds") or 0) // 60))
                            return f"角色刚醒来约{elapsed_min}分钟（共睡约{slept_min}分钟）"
            except Exception:
                pass
        return ""

    def _state_hint(self, context, at):
        """A short, non-binding summary of the role's own current energy/mood/sleep."""
        if self.role_state is None:
            return ""
        try:
            state = self.role_state.read(context, at=at)
        except (TaskError, ValueError, TypeError, KeyError):
            return ""
        values = state.get("values") or {}
        if not isinstance(values, dict):
            return ""
        parts = []
        if isinstance(values.get("energy"), int):
            parts.append(f"精力={values['energy']}")
        for key in ("sleep", "busy"):
            if isinstance(values.get(key), str) and values[key].strip():
                parts.append(values[key].strip()[:60])
        mood_val = values.get("mood")
        if isinstance(mood_val, str) and mood_val.strip():
            parts.append("心情=" + mood_val.strip()[:60])
        elif isinstance(mood_val, dict) and isinstance(mood_val.get("description"), str) and mood_val["description"].strip():
            parts.append("心情=" + mood_val["description"].strip()[:60])
        sleep_hint = self._sleep_hint(context, at)
        if sleep_hint and sleep_hint not in parts:
            parts.append(sleep_hint[:80])
        return "；".join(parts)[:180]

    # ---- day anchors -----------------------------------------------------
    def _anchor_slot(self, context, preferences, at):
        if not preferences.values["enable_daily_greetings"] or preferences.reason(ANCHOR_PURPOSE):
            return None
        zone = self._zone(preferences)
        local = at.astimezone(zone)
        day, minute = local.date(), local.hour * 60 + local.minute
        midnight = datetime.combine(day, datetime.min.time(), tzinfo=zone)
        for name in DAY_ANCHORS:
            start, end = window_bounds(preferences.values["greeting_" + name.split("_")[0] + "_window"])
            if not _minutes(start) <= minute < _minutes(end):
                continue
            opened = midnight + timedelta(minutes=_minutes(start))
            expires = midnight + timedelta(minutes=_minutes(end))
            opportunity = Opportunity("greeting-" + digest([context.key, name, day.isoformat()]),
                context.scope, opened.astimezone(timezone.utc), expires.astimezone(timezone.utc))
            marker = {"kind": "day_anchor", "anchor": name, "local_date": day.isoformat(),
                      "timezone": zone.key, "opened_at": stamp(opportunity.opened_at),
                      "expires_at": stamp(opportunity.expires_at)}
            return opportunity, marker
        return None

    # ---- insomnia night --------------------------------------------------
    def _night(self, preferences, at):
        """The current night slot: a bounded, evenly divided window around local midnight."""
        limit = preferences.values["insomnia_night_daily_limit"]
        if limit <= 0:
            return None
        zone = self._zone(preferences)
        local = at.astimezone(zone)
        start = local_time(preferences.values["insomnia_night_start"])
        end = local_time(preferences.values["insomnia_night_end"])
        if start == end:
            return None
        wall = local.time().replace(tzinfo=None)
        if start < end:
            if not start <= wall < end:
                return None
            night = local.date()
        elif wall >= start:
            night = local.date()
        elif wall < end:
            night = local.date() - timedelta(days=1)
        else:
            return None
        midnight = datetime.combine(night, datetime.min.time(), tzinfo=zone)
        opened = midnight + timedelta(hours=start.hour, minutes=start.minute)
        closed = midnight + timedelta(days=0 if start < end else 1, hours=end.hour, minutes=end.minute)
        width = (closed - opened) / limit
        index = min(limit - 1, max(0, int((local.astimezone(zone) - opened) / width)))
        return {"night_key": night.isoformat(), "slot": index, "limit": limit, "zone": zone.key,
                "opened": (opened + width * index).astimezone(timezone.utc),
                "expires": (opened + width * (index + 1)).astimezone(timezone.utc)}

    def _insomnia_slot(self, context, preferences, at):
        if not preferences.values["allow_insomnia_night_message"] or preferences.reason(INSOMNIA_PURPOSE):
            return None
        night = self._night(preferences, at)
        if night is None:
            return None
        if preferences.values["insomnia_require_sleep_state"] and not self._sleep_hint(context, at):
            return None
        opportunity = Opportunity("insomnia-" + digest([context.key, night["night_key"], night["slot"]]),
            context.scope, night["opened"], night["expires"])
        marker = {"kind": "insomnia_night", "night_key": night["night_key"], "slot": night["slot"],
                  "slot_limit": night["limit"], "timezone": night["zone"],
                  "opened_at": stamp(opportunity.opened_at), "expires_at": stamp(opportunity.expires_at)}
        return opportunity, marker

    # ---- selection and validation ---------------------------------------
    def _slot(self, context, preferences, at, interaction=None):
        slot = self._insomnia_slot(context, preferences, at)
        if slot is None:
            slot = self._anchor_slot(context, preferences, at)
        if slot is None or not self._idle_ok(preferences, at, interaction):
            return None
        return slot

    def select(self, context, preferences, at, interaction=None):
        slot = self._slot(context, preferences, at, interaction=interaction)
        if slot is None:
            return None
        opportunity, marker = slot
        task_id = self.contacts.task_id(context, opportunity)
        if (self.runtime.read_one("SELECT 1 FROM execution_tasks WHERE task_id = ?", (task_id,))
                or self.runtime.read_one("SELECT 1 FROM proactive_contacts WHERE task_id = ?", (task_id,))):
            return None
        if marker["kind"] == "insomnia_night":
            hint = self._sleep_hint(context, at)
            summary = ("现在落在配置的夜间低压力问候窗口，可以留一句轻的话；不把时段当成事实，不报时、不报日期和星期、不追问、不要求回应。"
                       + ("角色状态里有一条睡眠相关的临时状况：" + hint if hint else ""))
            purpose = INSOMNIA_PURPOSE
        else:
            why, topic = ANCHOR_MEANING[marker["anchor"]]
            state_hint = self._state_hint(context, at)
            user_sleep = self._user_sleep_hint(context, at) if marker["anchor"] == "morning_greeting" else ""
            summary = (f"按已配置时区，现在的本地日期是{marker['local_date']}，这是「{topic}」的问候窗口：{why}。"
                       "窗口只提供一个可以自然碰面的时机，不代表对方刚醒、刚吃完饭或已经空闲；没有合适的具体话头就跳过。"
                       + ("（角色当前自身状态参考：" + state_hint + "）" if state_hint else "")
                       + ("（用户最近睡眠参考：" + user_sleep + "）" if user_sleep else ""))
            purpose = ANCHOR_PURPOSE
        thought = Thought("greeting-event-" + digest(marker), opportunity, ThoughtKind.CONVERSATION,
                          summary, EvidenceSnapshot(context.scope, at), 1.0)
        return opportunity, (ContactIntent(purpose, thought),), marker

    def validate(self, context, marker, at, interaction=None):
        if type(marker) is not dict or marker.get("kind") not in ("day_anchor", "insomnia_night"):
            raise TaskError("contact_greeting_invalid", "greeting contact requires its original local slot")
        preferences = require(self.contacts.preferences.read(context, observed_at=at))
        current = self._slot(context, preferences, at, interaction=interaction)
        if current is None or current[1] != marker:
            raise TaskError("contact_greeting_expired", "original greeting slot is no longer current")
        preferences.require_contact(INSOMNIA_PURPOSE if marker["kind"] == "insomnia_night" else ANCHOR_PURPOSE)
