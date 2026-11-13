"""Sparse contact preferences; pauses overlay, and never overwrite, permanent values."""

from dataclasses import dataclass, replace
from datetime import date, datetime, time
import json
import re
from types import MappingProxyType
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from companion_v2.configuration.models import ApplyMode, ConfigLayer, ConfigTarget, ConfigurationError, EffectiveValue
from companion_v2.configuration.owners import SparseConfigurationOwner
from companion_v2.configuration.registry import FieldDefinition, FieldRegistry, ValueKind
from companion_v2.foundation.refs import ScopeKind, normalize_utc

from .news_sources import NewsSource, SOURCE_KINDS, news_uid, news_url
from .prompt_templates import PROMPT_DEFAULTS, checked_template
from .rhythm_configuration import (
    SHARED_RHYTHM_NAMES, global_contact_snapshot, personal_contact_snapshot,
    private_contact_operations,
)


GROUP = "proactive.contact"
PURPOSES = MappingProxyType({
    "affection": "想念与亲近", "care": "情绪关怀", "play": "轻松互动", "repair": "关系修复",
    "chat": "日常话题", "greeting": "问候与纪念", "life": "分享自己的生活", "creation": "作品与阅读",
    "news": "外部资讯", "followup": "接回旧话题", "invitation": "共同活动邀请",
    "reminder": "约定与提醒", "environment": "环境与状态变化", "meal": "饭点问候",
    "screen": "已授权屏幕观察",
})
LAYERS = (ConfigLayer.INSTALLATION, ConfigLayer.ROLE, ConfigLayer.RELATIONSHIP, ConfigLayer.WINDOW)
WINDOW_QUOTA_FIELDS = frozenset({"max_contacts_24h"})
DEDUP_POLICIES = frozenset({"semantic", "event_anchor", "event_revision", "conversation_anchor",
                           "daily_slot", "content_fingerprint", "life_event"})
# T38: the V1 hourly activity curve, one weight per local hour. A wrong length
# falls back to this curve, a single unusable value falls back to 1.0 for that
# hour, and every weight stays inside the same bounds V1 clamped to.
DEFAULT_HOUR_CURVE = ("0.22,0.16,0.12,0.10,0.10,0.14,0.28,0.50,0.66,0.72,0.78,0.92,1.0,0.94,"
                      "0.82,0.74,0.78,0.92,1.0,0.98,0.88,0.70,0.48,0.32")
CURVE_FLOOR, CURVE_CEILING = 0.05, 2.0
# T38: named intensity tiers, in the V1 vocabulary. A tier only scales the
# configured interval and idle gate, bounds the burst round length and offers a
# body-length hint; it never widens an allowance, a right or a delivery rule.
INTENSITY_PRESETS = MappingProxyType({
    "off": {"interval_factor": 1.0, "idle_factor": 1.0, "burst_messages": 0, "body_chars": 0},
    "balanced": {"interval_factor": 0.8, "idle_factor": 0.7, "burst_messages": 2, "body_chars": 300},
    "high_private": {"interval_factor": 0.5, "idle_factor": 0.35, "burst_messages": 3, "body_chars": 300},
    "high_group": {"interval_factor": 0.9, "idle_factor": 0.8, "burst_messages": 2, "body_chars": 240},
    "live": {"interval_factor": 0.2, "idle_factor": 0.1, "burst_messages": 3, "body_chars": 400}})
INTENSITY_ALIASES = MappingProxyType({
    "": "off", "manual": "off", "none": "off", "default": "off", "standard": "balanced",
    "active": "high_private", "private": "high_private", "group": "high_group",
    "online": "live", "直播": "live", "高频": "live"})
ANCHOR_PURPOSES = ("morning_greeting", "noon_greeting", "evening_greeting", "insomnia_night")
ANCHOR_WINDOW_NAMES = ("morning", "noon", "evening")


def anchor_window(value):
    """A local ``HH:MM-HH:MM`` anchor window; anchors never cross local midnight."""
    start, separator, end = value.partition("-")
    if not separator:
        raise ValueError("anchor windows require HH:MM-HH:MM")
    opened, closed = local_time(start), local_time(end)
    if opened >= closed:
        raise ValueError("anchor window must start before it ends on the same local day")
    return f"{start}-{end}"


def hour_activity_curve(value):
    """The configured 24-hour weights; unusable entries degrade instead of failing."""
    parts = value.replace("，", ",").split(",")
    if len(parts) != 24:
        parts = DEFAULT_HOUR_CURVE.split(",")
    weights = []
    for part in parts:
        try:
            weights.append(max(CURVE_FLOOR, min(CURVE_CEILING, float(part))))
        except (TypeError, ValueError):
            weights.append(1.0)
    return tuple(weights)


def curve_text(value):
    return ",".join(f"{weight:.2f}" for weight in hour_activity_curve(value))


def intensity_preset(value):
    """Canonical tier name; an unknown tier is rejected instead of silently guessed."""
    name = INTENSITY_ALIASES.get(value.strip().lower(), value.strip().lower())
    if name not in INTENSITY_PRESETS:
        raise ValueError("intensity preset must name a registered proactive rhythm tier")
    return name


def judge_exempt_purposes(value):
    if type(value) is not str or len(value) > 256:
        raise ValueError("judge exemptions require a short comma-separated list")
    purposes = tuple(part for part in re.split(r"[,，;；\s]+", value.strip().lower()) if part)
    if len(set(purposes)) != len(purposes) or not set(purposes) <= set(PURPOSES):
        raise ValueError("judge exemptions contain an unknown or repeated purpose")
    return purposes


def dedup_policies(value):
    if type(value) is not str or len(value) > 256:
        raise ValueError("duplicate policies require a short comma-separated list")
    policies = tuple(part for part in re.split(r"[,，;；\s]+", value.strip().lower()) if part)
    if len(set(policies)) != len(policies) or not set(policies) <= DEDUP_POLICIES:
        raise ValueError("duplicate policies contain an unknown or repeated route")
    return policies


def pause_time(value):
    return normalize_utc(datetime.fromisoformat(value), field="pause deadline") if value else None


def local_time(value):
    parsed = time.fromisoformat(value)
    if len(value) != 5 or parsed.tzinfo is not None or parsed.second or parsed.microsecond:
        raise ValueError("quiet times require HH:MM")
    return parsed


@dataclass(frozen=True)
class ImportantDate:
    calendar: str
    month: int
    day: int
    label: str
    leap_month: bool = False


def important_dates(value):
    if type(value) is not str:
        raise ValueError("important dates require a JSON list")
    if len(value.encode("utf-8")) > 8192:
        raise ValueError("important dates are too long")
    rows = json.loads(value or "[]")
    if type(rows) is not list or len(rows) > 32:
        raise ValueError("important dates require at most 32 entries")
    result, seen = [], set()
    for row in rows:
        if type(row) is not dict:
            raise ValueError("important date requires an object")
        calendar = row.get("calendar", "gregorian")
        fields = {"month", "day", "label"} | ({"calendar"} if "calendar" in row else set())
        if calendar == "lunar":
            fields |= {"leap_month"} if "leap_month" in row else set()
        if calendar not in ("gregorian", "lunar") or set(row) != fields:
            raise ValueError("important date has invalid calendar fields")
        month, day, label = row["month"], row["day"], row["label"]
        if type(month) is not int or type(day) is not int:
            raise ValueError("important date month and day must be integers")
        if calendar == "gregorian":
            date(2000, month, day)
        elif not 1 <= month <= 12 or not 1 <= day <= 30:
            raise ValueError("lunar month or day is outside its possible range")
        leap_month = row.get("leap_month", False)
        if type(leap_month) is not bool:
            raise ValueError("lunar leap_month must be boolean")
        if (type(label) is not str or not 1 <= len(label.strip()) <= 64
                or any(ord(character) < 32 for character in label)):
            raise ValueError("important date label must be short text")
        key = ImportantDate(calendar, month, day, label.strip(), leap_month)
        if key in seen:
            raise ValueError("important date is duplicated")
        seen.add(key)
        result.append(key)
    return tuple(result)


class ContactField(FieldDefinition):
    def validate(self, value):
        value = super().validate(value)
        try:
            if self.field_id.endswith(".pause_until"):
                instant = pause_time(value)
                return instant.isoformat() if instant else ""
            if self.field_id.endswith((".quiet_start", ".quiet_end", ".meal_lunch_time", ".meal_dinner_time",
                                       ".weather_checkin_time", ".date_checkin_time",
                                       ".insomnia_night_start", ".insomnia_night_end", ".wake_morning_start", ".wake_morning_end")):
                local_time(value)
            if self.field_id.endswith(".proactive_intensity_preset"):
                return intensity_preset(value)
            if self.field_id.endswith(".generation_mode") and value not in ("single", "two_stage"):
                raise ValueError("generation mode must be single or two_stage")
            if self.field_id.endswith(".proactive_hour_activity_curve"):
                return curve_text(value)
            if self.field_id.endswith(tuple(".greeting_" + name + "_window" for name in ANCHOR_WINDOW_NAMES)):
                return anchor_window(value)
            if self.field_id.endswith(".proactive_persona_judge_exempt_purposes"):
                return ",".join(judge_exempt_purposes(value))
            if self.field_id.endswith(".important_dates"):
                important_dates(value)
            if self.field_id.endswith(".dedup_policies"):
                return ",".join(dedup_policies(value))
            if self.field_id.endswith(".quiet_timezone"):
                ZoneInfo(value)
            if self.field_id.endswith(".balance_currency_label") and (
                    not value.strip() or len(value) > 20 or any(ord(char) < 32 for char in value)):
                raise ValueError("balance currency label must be short text")
            if self.field_id.endswith(".news_source_kind") and value not in SOURCE_KINDS:
                raise ValueError("unknown news source kind")
            if self.field_id.endswith(".official_alert_min_severity") and value not in (
                    "", "minor", "moderate", "severe", "extreme"):
                raise ValueError("unknown official alert severity")
            if self.field_id.endswith(".news_source_uid") and value:
                news_uid(value)
            if self.field_id.endswith(".news_source_url") and value:
                news_url(value)
            if self.field_id.endswith(".news_hot_sources"):
                sources = tuple(part.strip().lower() for part in value.split(",") if part.strip())
                if not sources or len(set(sources)) != len(sources) or not set(sources) <= {"weibo", "hackernews"}:
                    raise ValueError("news hot sources must be unique supported source names")
                return ",".join(sources)
            if self.field_id.endswith(".web_exploration_interests"):
                if len(value) > 512 or any(ord(character) < 32 and character not in "\t\n\r" for character in value):
                    raise ValueError("web exploration interests are too long or contain control characters")
            prompt_field = self.field_id.removeprefix(GROUP + ".")
            if prompt_field in PROMPT_DEFAULTS:
                value = checked_template(prompt_field, value)
            if self.kind is ValueKind.TEXT and len(value) > 8192:
                raise ValueError("contact preference text is too long")
        except (ValueError, ZoneInfoNotFoundError) as error:
            raise ConfigurationError("invalid_contact_preference", str(error)) from error
        return value


def configuration_fields():
    defaults = {"enabled": False, "pause_until": "", "note": "", "style_prompt": "",
                "generation_mode": "single", "quiet_enabled": False,
                "quiet_start": "23:00", "quiet_end": "07:00", "quiet_timezone": "Asia/Shanghai",
                "meal_checkin_enabled": False, "meal_lunch_time": "12:00", "meal_dinner_time": "18:00",
                "date_checkin_enabled": False, "date_checkin_time": "09:00", "important_dates": "[]",
                "weather_checkin_enabled": False, "weather_checkin_time": "08:30",
                "weather_change_enabled": False, "official_weather_alert_enabled": False,
                "news_share_enabled": False,
                "news_source_kind": "host", "news_source_uid": "", "news_source_url": "",
                "web_exploration_enabled": False, "web_exploration_interests": "",
                "news_daily_hot_read_enabled": False, "news_hot_sources": "weibo,hackernews",
                "external_event_self_link_enabled": True,
                "screen_glance_enabled": False,
                # T20: the two V1 peek initiators. Both stay off until an operator
                # enables them for this window; the capture itself is still a native
                # device call that the Screen extension may refuse.
                "screen_glance_goodnight_enabled": False,
                "screen_glance_followup_enabled": False,
                "official_alert_min_severity": "",
                "balance_awareness_enabled": False, "balance_include_amount_in_message": False,
                "voice_action_enabled": False, "poke_action_enabled": False,
                "dedup_enabled": True, "dedup_last_message_enabled": True,
                # ``event_revision`` is what weather-change and official-alert contacts
                # route to (see DEDUP_EVENT_POLICY). Leaving it out meant
                # ``dedup_weather_window_minutes`` governed only the scheduled
                # weather check-in and did not suppress a repeated warning at all --
                # the window existed but never fired for the two contacts most likely
                # to repeat themselves. It also covers calendar events, which is the
                # intended reading: a revised event may speak again, an unchanged one
                # may not.
                "dedup_policies": "semantic,content_fingerprint,life_event,event_revision",
                "balance_currency_label": "元",
                # Rhythm (T38): the V1 burst, hourly curve, intensity tier and closing grace.
                "enable_proactive_burst": False, "proactive_intensity_preset": "off",
                "proactive_hour_activity_enabled": False,
                "proactive_hour_activity_curve": DEFAULT_HOUR_CURVE,
                # Day anchors and the bounded insomnia window (T40).
                "enable_daily_greetings": False, "allow_insomnia_night_message": False,
                "greeting_morning_window": "07:45-10:20", "greeting_noon_window": "12:05-13:35",
                "greeting_evening_window": "20:10-21:20",
                "insomnia_night_start": "23:00", "insomnia_night_end": "06:00",
                "insomnia_require_sleep_state": False,
                # Morning chain (T43): role wake greeting, routine learning and one
                # bounded wake-up confirmation. All switches default off.
                "morning_chain_enabled": False,
                "morning_role_greeting_enabled": True,
                "morning_nudge_enabled": True,
                "routine_learning_enabled": True,
                "wake_require_device_evidence": False,
                "wake_morning_start": "05:00",
                "wake_morning_end": "11:30",
                # Appointment execution (T39) and the post-draft persona judge (T41).
                "enable_llm_proactive_persona_judge": False,
                "proactive_persona_judge_exempt_purposes": ""}
    defaults.update(PROMPT_DEFAULTS)
    for purpose in PURPOSES:
        defaults.update({purpose + ".enabled": True, purpose + ".weight": 1.0, purpose + ".pause_until": ""})
    fields = tuple(ContactField(GROUP + "." + name, GROUP, GROUP,
        ValueKind.BOOLEAN if type(value) is bool else ValueKind.WEIGHT if type(value) is float else ValueKind.TEXT,
        value, layers=LAYERS, apply_mode=ApplyMode.IMMEDIATE, owner_id=GROUP) for name, value in defaults.items())
    return fields + (ContactField(GROUP + ".automatic_enabled", GROUP, GROUP, ValueKind.BOOLEAN,
        False, layers=LAYERS, apply_mode=ApplyMode.IMMEDIATE, owner_id=GROUP),) + tuple(
        ContactField(GROUP + "." + name, GROUP, GROUP, ValueKind.INTEGER, default,
            minimum=minimum, maximum=maximum,
            layers=LAYERS,
            apply_mode=ApplyMode.IMMEDIATE, owner_id=GROUP)
        for name, default, minimum, maximum in (
            ("interval_seconds", 3600, 60, 604800), ("idle_seconds", 900, 0, 86400),
            ("unanswered_slowdown_start", 1, 1, 10), ("unanswered_pause_after", 0, 0, 50),
            ("dedup_sent_window_minutes", 240, 0, 1440),
            ("dedup_last_message_window_minutes", 240, 0, 1440),
            ("dedup_weather_window_minutes", 1080, 0, 2880),
            ("dedup_min_shared_tokens", 1, 1, 4),
            ("event_max_age_seconds", 3600, 60, 86400),
            ("max_contacts_24h", 3, 0, 100),
            ("balance_check_interval_minutes", 60, 5, 1440),
            ("news_check_interval_minutes", 180, 15, 1440),
            ("web_exploration_min_interval_hours", 8, 1, 168),
            ("web_exploration_result_count", 8, 1, 30),
            ("news_hot_result_count", 10, 3, 30),
            ("external_event_self_link_cooldown_hours", 12, 0, 168),
            ("screen_glance_daily_limit", 1, 0, 20),
            ("screen_glance_cooldown_minutes", 240, 0, 10080),
            # V1 goodnight_screen_check_delay_minutes (1-180) and the unanswered
            # peek window/cooldown (10-240 / 30-1440).
            ("screen_glance_goodnight_delay_minutes", 45, 1, 180),
            ("screen_glance_followup_after_minutes", 45, 10, 240),
            ("screen_glance_followup_cooldown_minutes", 180, 30, 1440),
            ("contact_followup_after_minutes", 45, 10, 1440),
            ("contact_followup_cooldown_minutes", 180, 30, 2880),
            ("balance_message_cooldown_hours", 24, 1, 168),
            # C2-5: how many days ahead an upcoming anniversary may be noticed.
            # 0 keeps the V1 behaviour exactly — same-day greeting only. Named after
            # the V1 key so the ledger mapping stays one-to-one.
            ("important_date_lookahead_days", 0, 0, 30),
            # C2-5: how long after a meal checkin the role may follow up. The meal
            # slot width was a hard-coded 45 minutes before this.
            ("meal_care_followup_minutes", 45, 1, 240),
            ("balance_low_threshold", 10, 0, 1000000000),
            ("balance_critical_threshold", 3, 0, 1000000000),
            ("balance_low_percent_threshold", 15, 0, 100),
            ("balance_critical_percent_threshold", 5, 0, 100),
            ("voice_action_max_chars", 30, 6, 80),
            ("voice_action_cooldown_minutes", 60, 0, 1440),
            ("poke_action_daily_limit", 1, 0, 20),
            ("poke_action_cooldown_minutes", 30, 0, 1440),
            # T38 rhythm: burst length, burst gaps, closing silence and body length.
            ("proactive_burst_max_messages", 2, 2, 3),
            ("proactive_burst_gap_min_seconds", 45, 10, 600),
            ("proactive_burst_gap_max_seconds", 180, 10, 900),
            ("proactive_closing_grace_minutes", 45, 0, 240),
            ("proactive_closing_contacts_threshold", 3, 1, 10),
            ("proactive_intensity_body_chars", 0, 0, 2000),
            # T40 anchors and the explicitly bounded insomnia window.
            ("greeting_idle_minutes", 30, 0, 240),
            ("insomnia_night_daily_limit", 1, 0, 3),
            ("proactive_share_probability", 100, 0, 100),
            # T39 appointment timing and T41 persona judgement.
            ("appointment_lead_minutes", 30, 0, 240),
            ("appointment_lag_minutes", 0, 0, 180),
            ("appointment_pre_silence_minutes", 20, 0, 240),
            ("proactive_persona_judge_threshold", 62, 0, 100),
            ("proactive_persona_judge_cache_minutes", 180, 0, 720),
            # T43 morning chain: one wake-up confirmation at most, bounded window.
            ("morning_nudge_delay_minutes", 25, 10, 90),
            ("morning_nudge_max_attempts", 1, 0, 2),
            ("routine_window_days", 60, 14, 180),
            ("wake_contact_window_minutes", 90, 15, 240),
            ("wake_contact_min_confidence", 65, 40, 100),
            ("wake_contact_max_daily", 1, 0, 3))) + tuple(
        ContactField(GROUP + "." + name, GROUP, GROUP, ValueKind.INTEGER, default,
            minimum=minimum, maximum=maximum, layers=LAYERS, apply_mode=ApplyMode.IMMEDIATE, owner_id=GROUP)
        for name, default, minimum, maximum in (
            ("generation_history_limit", 8, 0, 64),
            ("history_max_chars", 8192, 0, 8192),
            ("official_alert_poll_minutes", 5, 1, 1440),
            ("weather_sample_minutes", 60, 10, 1440))) + (ContactField(
        GROUP + ".unanswered_max_interval_multiplier", GROUP, GROUP, ValueKind.WEIGHT, 2.2,
        minimum=1.0, maximum=8.0, layers=LAYERS, apply_mode=ApplyMode.IMMEDIATE, owner_id=GROUP), ContactField(
        GROUP + ".dedup_min_overlap_ratio", GROUP, GROUP, ValueKind.PROBABILITY, 0.0,
        layers=LAYERS, apply_mode=ApplyMode.IMMEDIATE, owner_id=GROUP), ContactField(
        GROUP + ".silence_min_confidence", GROUP, GROUP, ValueKind.PROBABILITY, 0.66,
        layers=LAYERS, apply_mode=ApplyMode.IMMEDIATE, owner_id=GROUP), ContactField(
        GROUP + ".silence_feedback_penalty", GROUP, GROUP, ValueKind.PROBABILITY, 0.2,
        layers=LAYERS, apply_mode=ApplyMode.IMMEDIATE, owner_id=GROUP), ContactField(
        GROUP + ".external_event_self_link_weight", GROUP, GROUP, ValueKind.WEIGHT, 1.5,
        minimum=0.1, maximum=5.0, layers=LAYERS, apply_mode=ApplyMode.IMMEDIATE, owner_id=GROUP),
        ContactField(GROUP + ".morning_wake_confidence_floor", GROUP, GROUP, ValueKind.PROBABILITY, 0.25,
            layers=LAYERS, apply_mode=ApplyMode.IMMEDIATE, owner_id=GROUP))


def with_contact_fields(registry):
    """Preserve caller definitions; reject an incompatible duplicate instead of replacing it."""
    fields = dict(registry.fields)
    for field in configuration_fields():
        if field.field_id in fields and fields[field.field_id] != field:
            raise ValueError("contact preferences must use their registered owner and field contract")
        fields[field.field_id] = field
    return FieldRegistry(version=registry.version, fields=tuple(fields.values()))


def target_for(context):
    return ConfigTarget(context.scope.installation_id, ConfigLayer.WINDOW, context.role_binding.role,
                        context.scope, subject=context.actor.subject)


class ContactConfigurationOwner(SparseConfigurationOwner):
    owner_id = GROUP

    @staticmethod
    def _effective_fields(fields):
        return tuple(replace(field, layers=tuple(layer for layer in field.layers
            if layer is not ConfigLayer.RELATIONSHIP)) if
            field.field_id.removeprefix(GROUP + ".") in WINDOW_QUOTA_FIELDS else field for field in fields)

    @staticmethod
    def _validate_quota_target(target, keys):
        changed = {key.removeprefix(GROUP + ".") for key in keys}
        if target.layer is ConfigLayer.RELATIONSHIP and changed & WINDOW_QUOTA_FIELDS:
            raise ConfigurationError("configuration_scope_not_supported",
                                     "daily contact quotas are configured per private window")

    @staticmethod
    def _target(target):
        if ((target.layer is not ConfigLayer.INSTALLATION and not target.bot_id)
                or (target.layer is ConfigLayer.WINDOW and (target.subject is None
                    or not target.scope.is_resolved or target.scope.kind is not ScopeKind.PRIVATE))):
            raise ConfigurationError("configuration_relationship_required", "contact preferences require a bot and the window's human relationship")

    def _pauses(self, target, fields, result, *, changes=None, old_public_revisions=()):
        # A later expiry at any ancestor still applies when a descendant clears its own pause.
        # Keeping expired timestamps is deliberate: expiration needs no write or cleanup job.
        pause_fields = tuple(field for field in fields if field.field_id.endswith(".pause_until"))
        layers = target.chain()
        placeholders = ",".join("?" for _ in pause_fields)
        stored = {layer.key: {row["field_id"]: row for row in self.store.read_all(
            f"SELECT field_id, value_json, revision FROM configuration_values WHERE target_key = ? AND field_id IN ({placeholders})",
            (layer.key, *(field.field_id for field in pause_fields)))} for layer in layers}
        for field in pause_fields:
            effective = result[field.field_id]
            until = pause_time(effective.value)
            chosen = (until, effective) if until is not None else None
            for layer in layers:
                if layer == target and changes is not None and field.field_id in changes:
                    value, revision = changes[field.field_id], result[field.field_id].source_revision
                else:
                    row = stored[layer.key].get(field.field_id)
                    if row is None:
                        continue
                    if (layer.layer is ConfigLayer.WINDOW and row["revision"] in old_public_revisions
                            and effective.source is not None and effective.source.layer is not ConfigLayer.WINDOW):
                        continue
                    value, revision = field.validate(json.loads(row["value_json"])), row["revision"]
                until = pause_time(value)
                if until is not None and (chosen is None or until >= chosen[0]):
                    chosen = (until, EffectiveValue(value, layer, layer == target, revision))
            if chosen is not None:
                result[field.field_id] = chosen[1]
        return result

    def read(self, target, fields):
        fields = self._effective_fields(fields)
        self._target(target)
        current = super().read(target, fields)
        shared = global_contact_snapshot(self.runtime, target, fields)
        public_revisions = set()
        if target.layer is ConfigLayer.INSTALLATION:
            current = shared
        else:
            stable_target = (target.chain()[-2] if target.layer is ConfigLayer.WINDOW else target)
            stable = super().read(stable_target, fields)
            stable = personal_contact_snapshot(self.runtime, stable_target, fields, stable)
            if target.layer is ConfigLayer.WINDOW:
                rows = self.store.read_all("""SELECT operation_id,
                        json_extract(CASE WHEN json_valid(result_json) THEN result_json ELSE '{}' END,
                                     '$.revision') AS revision
                    FROM configuration_changes WHERE target_key = ? AND group_id = ? AND changed = 1""",
                    (target.key, GROUP))
                personal_operations = private_contact_operations(self.runtime, {target.key})
                public_revisions = {row["revision"] for row in rows
                    if row["operation_id"].startswith("proactive.contact-config:")
                    and row["operation_id"] not in personal_operations}
                personal_window_revisions = {row["revision"] for row in rows
                    if row["operation_id"] in personal_operations}
            values = dict(current.fields)
            for field in fields:
                key = field.field_id
                item = stable.fields[key]
                global_value = shared.fields[key]
                # Installation values include exact legacy public-window fallbacks.
                # They take precedence over old role defaults; relationship values
                # remain this participant's explicit overrides.
                if (item.source is not None and item.source.layer is ConfigLayer.ROLE
                        and global_value.source is not None):
                    item = global_value
                local = current.fields[key]
                old_window = local.source is not None and local.source.layer is ConfigLayer.WINDOW
                old_public = old_window and local.source_revision in public_revisions
                private_window = old_window and local.source_revision in personal_window_revisions
                # Preserve direct WINDOW API writes for non-legacy fields. Only
                # a row proven to be an old public editor save may be replaced
                # by the stable global/personal address.
                shared_field = key.removeprefix(GROUP + ".") in SHARED_RHYTHM_NAMES
                if private_window:
                    values[key] = replace(local, explicit=local.explicit and local.source == target)
                elif old_window and not old_public and not shared_field:
                    values[key] = replace(local, explicit=local.explicit and local.source == target)
                elif item.source is not None and (shared_field or old_public or not old_window):
                    values[key] = replace(item, explicit=item.explicit and item.source == target)
                elif shared_field and (current.fields[key].source is None or old_public):
                    item = shared.fields[key]
                    values[key] = replace(item, explicit=False)
            current = replace(current, fields=values,
                dependencies=tuple(dict((*current.dependencies, *stable.dependencies, *shared.dependencies)).items()))
        return replace(current, fields=self._pauses(target, fields, dict(current.fields),
            old_public_revisions=public_revisions))

    def preview_set(self, target, fields, *, changes):
        self._validate_quota_target(target, changes)
        result = super().preview_set(target, fields, changes=changes)
        try:
            NewsSource.read({key.removeprefix(GROUP + "."): item.value for key, item in result.items()})
        except ValueError as error:
            raise ConfigurationError("invalid_contact_preference", str(error)) from error
        return self._pauses(target, fields, result, changes=changes)

    def write(self, target, fields, *, changes, reset, expected_revision, observed_at):
        self._validate_quota_target(target, (*changes, *reset))
        return super().write(target, fields, changes=changes, reset=reset,
            expected_revision=expected_revision, observed_at=observed_at)
