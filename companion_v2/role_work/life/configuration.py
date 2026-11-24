"""Role-wide sleep defaults with explicit window overrides for routines."""

from dataclasses import replace
from datetime import time
from zoneinfo import ZoneInfo

from companion_v2.configuration.models import ApplyMode, ConfigLayer, ConfigTarget
from companion_v2.configuration.owners import OwnerSnapshot
from companion_v2.configuration.role_settings import RoleSharedSettingsOwner
from companion_v2.configuration.registry import FieldDefinition, ValueKind
from .rest_gate import DEFAULTS as REST_GATE_DEFAULTS, LIMITS as REST_GATE_LIMITS
from .rest_gate import validate_changes as validate_rest_changes, validate_values as validate_rest_values

GROUP = "role.life"
DEFAULTS = {"enabled": False, "diary_enabled": True, "diary_time": "23:50", "timezone": "Asia/Shanghai",
            "sleep_enabled": False, "sleep_time": "00:30", "sleep_timezone": "Asia/Shanghai",
            "sleep_variation_minutes": 20, "sleep_window_minutes": 120, "sleep_minutes": 480,
            "reading_enabled": False, "reading_share_in_group": True, "derive_enabled": True,
            "diary_style_prompt": "",
            "dream_theme_candidates": "", "dream_mixed_themes_enabled": False,
            "dream_intimate_theme_enabled": False, "dream_afterglow_mode": "auto",
            **REST_GATE_DEFAULTS}
OWNER = "role.life.settings"
SLEEP_FIELDS = frozenset(("sleep_enabled", "sleep_time", "sleep_timezone", "sleep_variation_minutes",
                          "sleep_window_minutes", "sleep_minutes", "reading_share_in_group"))
AFTERGLOW_MODES = ("auto", "轻", "标准", "明显")


def configuration_fields():
    fields = []
    for name, value in DEFAULTS.items():
        kind = ValueKind.BOOLEAN if type(value) is bool else ValueKind.INTEGER if type(value) is int else ValueKind.TEXT
        bounds = ({"minimum": 0, "maximum": 120} if name == "sleep_variation_minutes" else
                  {"minimum": 0, "maximum": 360} if name == "sleep_window_minutes" else
                  {"minimum": REST_GATE_LIMITS[name][0], "maximum": REST_GATE_LIMITS[name][1]}
                  if name in REST_GATE_LIMITS else {"minimum": 1, "maximum": 1440})
        fields.append(FieldDefinition(GROUP + "." + name, GROUP, GROUP, kind, value,
            layers=(ConfigLayer.WINDOW, ConfigLayer.ROLE), apply_mode=ApplyMode.IMMEDIATE, owner_id=OWNER,
            **(bounds if kind is ValueKind.INTEGER else {})))
    return tuple(fields)


def target_for(context):
    return ConfigTarget(context.scope.installation_id, ConfigLayer.WINDOW, context.role_binding.role, context.scope)


def values(snapshot):
    # A configuration-service snapshot carries ``values``; an owner snapshot
    # carries the same effective fields under ``fields``.
    source = getattr(snapshot, "values", None)
    if not isinstance(source, dict):
        source = {key: field.value for key, field in snapshot.fields.items()}
    result = {name.rsplit(".", 1)[-1]: value for name, value in source.items()}
    ZoneInfo(result["timezone"])
    ZoneInfo(result["sleep_timezone"])
    for name in ("diary_time", "sleep_time"):
        parsed = time.fromisoformat(result[name])
        if parsed.tzinfo is not None or parsed.second or parsed.microsecond or len(result[name]) != 5:
            raise ValueError("use HH:MM local time")
    if type(result["sleep_variation_minutes"]) is not int or not 0 <= result["sleep_variation_minutes"] <= 120:
        raise ValueError("sleep variation must be 0..120 minutes")
    if type(result["sleep_window_minutes"]) is not int or not 0 <= result["sleep_window_minutes"] <= 360:
        raise ValueError("sleep window must be 0..360 minutes")
    if result["dream_afterglow_mode"] not in AFTERGLOW_MODES:
        raise ValueError("unsupported dream afterglow mode")
    # The eight rest-gate fields are closed here too, so a malformed stored value
    # degrades to the defaults instead of reaching the reply gate.
    validate_rest_values(result)
    return result


def dream_settings(runtime, context):
    """Best-effort dream writing preferences; unusable stored values degrade to defaults."""
    try:
        snapshot = LifeConfigurationOwner(runtime).read(target_for(context), configuration_fields())
        return values(snapshot)
    except (ValueError, TypeError):
        return dict(DEFAULTS)


class LifeConfigurationOwner(RoleSharedSettingsOwner):
    owner_id = OWNER
    # Before role-wide sleep settings were introduced, this group used the
    # generic sparse owner.  Keep that identity readable for an already
    # recorded operation, but never accept it for a newly created operation.
    legacy_owner_ids = ("configuration",)

    def is_shared(self, field):
        return field.group_id == GROUP and field.field_id.rsplit(".", 1)[-1] in SLEEP_FIELDS

    # The rest-gate fields are window-scoped and must not reach the reader in a
    # shape it would have to coerce, so a write is checked before it is stored.
    def preview_set(self, target, fields, *, changes):
        result = super().preview_set(target, fields, changes=changes)
        validate_rest_changes(changes)
        return result

    def write(self, target, fields, *, changes, reset, expected_revision, observed_at):
        validate_rest_changes(changes, reset)
        return super().write(target, fields, changes=changes, reset=reset,
                             expected_revision=expected_revision, observed_at=observed_at)

    def read(self, target, fields):
        snapshot = super().read(target, fields)
        timezone_id, sleep_timezone_id = GROUP + ".timezone", GROUP + ".sleep_timezone"
        if (sleep_timezone_id in snapshot.fields and snapshot.fields[sleep_timezone_id].source is None
                and timezone_id in snapshot.fields):
            fallback = dict(snapshot.fields)
            fallback[sleep_timezone_id] = replace(fallback[sleep_timezone_id], value=fallback[timezone_id].value,
                source=fallback[timezone_id].source, explicit=fallback[timezone_id].explicit,
                source_revision=fallback[timezone_id].source_revision)
            snapshot = OwnerSnapshot(snapshot.revision, snapshot.dependencies, fallback, snapshot.lifecycle_token)
        return snapshot
