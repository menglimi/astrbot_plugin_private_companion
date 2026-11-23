"""Role perception defaults with private-window overrides, fixed at input sealing."""

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from companion_v2.configuration.models import ConfigLayer, ConfigTarget, ConfigurationError
from companion_v2.configuration.role_settings import RoleSharedSettingsOwner
from companion_v2.configuration.registry import FieldDefinition, ValueKind
from companion_v2.execution.models import require


GROUP = "conversation.perception"
OWNER = GROUP
DEFAULTS = {
    "calendar_enabled": True,
    "calendar_timezone": "Asia/Shanghai",
    "health_enabled": False,
    "health_proactive_enabled": False,
    "screen_context_enabled": True,
    "holiday_enabled": True,
    "holiday_country": "CN",
    "lunar_enabled": True,
    "automatic_solar_terms_enabled": True,
    "almanac_enabled": False,
    "platform_enabled": True,
    "model_route_enabled": True,
    "configured_modalities_enabled": True,
    "guidance": "",
}


def target_for(context):
    return ConfigTarget(context.scope.installation_id, ConfigLayer.WINDOW,
                        context.role_binding.role, context.scope)


def role_target_for(context):
    return ConfigTarget(context.scope.installation_id, ConfigLayer.ROLE,
                        context.role_binding.role, bot_id=context.scope.bot_id)


def validate(values):
    if type(values) is not dict or set(values) != set(DEFAULTS):
        raise ConfigurationError("perception_configuration_invalid", "provide the declared perception settings")
    if any(type(values[name]) is not bool for name in (
            "calendar_enabled", "health_enabled", "health_proactive_enabled", "screen_context_enabled",
            "holiday_enabled", "lunar_enabled",
            "automatic_solar_terms_enabled", "almanac_enabled",
            "platform_enabled", "model_route_enabled", "configured_modalities_enabled")):
        raise ConfigurationError("perception_configuration_invalid", "perception switches must be boolean")
    if values["holiday_country"] not in ("CN", "WEEKEND_ONLY"):
        raise ConfigurationError("perception_holiday_country_invalid", "choose China holidays or weekend-only recognition")
    if (type(values["calendar_timezone"]) is not str or len(values["calendar_timezone"]) > 128
            or not values["calendar_timezone"].strip()):
        raise ConfigurationError("perception_timezone_invalid", "use an IANA calendar timezone")
    try:
        ZoneInfo(values["calendar_timezone"])
    except (ZoneInfoNotFoundError, ValueError, TypeError) as error:
        raise ConfigurationError("perception_timezone_invalid", "use an IANA calendar timezone") from error
    guidance = values["guidance"]
    if type(guidance) is not str or len(guidance.encode("utf-8")) > 2048 or any(
            ord(character) < 32 and character not in "\n\t" for character in guidance):
        raise ConfigurationError("perception_guidance_invalid", "keep guidance within 2 KiB")
    return values


def validate_snapshot(values):
    """Read old sealed snapshots by filling fields introduced after sealing."""
    if type(values) is not dict or set(values) - set(DEFAULTS):
        raise ConfigurationError("perception_configuration_invalid", "sealed perception settings are invalid")
    return validate({**DEFAULTS, **values})


def configuration_fields():
    return tuple(FieldDefinition(GROUP + "." + name, GROUP, GROUP,
        ValueKind.BOOLEAN if type(value) is bool else ValueKind.TEXT, value,
        layers=(ConfigLayer.ROLE, ConfigLayer.WINDOW), owner_id=OWNER)
        for name, value in DEFAULTS.items())


class PerceptionConfigurationOwner(RoleSharedSettingsOwner):
    owner_id = OWNER

    def is_shared(self, field):
        return field.group_id == GROUP

    def window_write_role_default(self, field):
        return False

    def adopt_window_value_on_write(self, field, changed_fields):
        return False

    def preview_set(self, target, fields, *, changes):
        result = super().preview_set(target, fields, changes=changes)
        validate({key.removeprefix(GROUP + "."): item.value for key, item in result.items()})
        return result

    def write(self, target, fields, *, changes, reset, expected_revision, observed_at):
        with self.store.transaction():
            result = super().write(target, fields, changes=changes, reset=reset,
                                   expected_revision=expected_revision, observed_at=observed_at)
            validate({key.removeprefix(GROUP + "."): item.value
                      for key, item in self.read(target, fields).fields.items()})
            return result


class PerceptionSettings:
    def __init__(self, configuration):
        self.configuration = configuration

    def snapshot(self, actor, context, at, *, target=None):
        return require(self.configuration.read(actor, target=target or target_for(context), group_id=GROUP,
                                               observed_at=at))

    def capture(self, context, at):
        snapshot = self.snapshot(context.actor, context, at)
        settings = validate({key.removeprefix(GROUP + "."): value
                             for key, value in snapshot.values.items()})
        return {"settings": settings, "configuration_token": snapshot.resolution_token}


def read_perception_values(app, context, at=None, *, role_only=False):
    """Read validated perception settings for trusted internal date projections."""
    service = getattr(app, "perception_settings", None)
    if service is None or context is None:
        return dict(DEFAULTS)
    if not role_only and at is not None and callable(getattr(service, "capture", None)):
        try:
            captured = service.capture(context, at)
            if isinstance(captured, dict) and isinstance(captured.get("settings"), dict):
                return validate_snapshot(captured["settings"])
        except Exception:
            pass
    configuration = getattr(service, "configuration", None)
    if configuration is not None:
        try:
            target = role_target_for(context) if role_only else target_for(context)
            with configuration.store.read_snapshot():
                definitions = configuration.registry.group(GROUP)
                owner = configuration._owners[definitions[0].owner_id]
                snapshot = owner.read(target, definitions)
            return validate({key.removeprefix(GROUP + "."): field.value
                             for key, field in snapshot.fields.items()})
        except Exception:
            pass
    return dict(DEFAULTS)
