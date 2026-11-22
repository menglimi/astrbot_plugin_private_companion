"""Explicit, private-window Screen options with one shared owner.

The live capture itself stays a native extension tool: this group only decides
whether an already adopted original may be interpreted by the model, and whether
that interpretation may become the day's diary material. Nothing here grants a
device call, and nothing here can widen the extension consent.
"""

from companion_v2.configuration.models import ConfigLayer, ConfigTarget, ConfigurationError
from companion_v2.configuration.owners import SparseConfigurationOwner
from companion_v2.configuration.registry import FieldDefinition, ValueKind
from companion_v2.execution.models import require


GROUP = "screen"
OWNER = GROUP
DEFAULTS = {
    # Model-calling capability: off until an operator turns it on for this window.
    "interpretation_enabled": False,
    # Fused diary material instead of V1's separate "yesterday screen diary" block.
    "journal_material_enabled": False,
    "journal_material_max_chars": 700,
}
LIMITS = {"journal_material_max_chars": (200, 1600)}
INTERPRETATION_FIELDS = ("interpretation_enabled", "journal_material_enabled")
INTEGER_FIELDS = ("journal_material_max_chars",)


def target_for(context):
    return ConfigTarget(context.scope.installation_id, ConfigLayer.WINDOW,
                        context.role_binding.role, context.scope)


def validate(values):
    if type(values) is not dict or set(values) != set(DEFAULTS):
        raise ConfigurationError("screen_configuration_invalid", "provide the declared Screen settings")
    if any(type(values[name]) is not bool for name in INTERPRETATION_FIELDS):
        raise ConfigurationError("screen_configuration_invalid", "Screen switches must be boolean")
    for name in INTEGER_FIELDS:
        low, high = LIMITS[name]
        value = values[name]
        if type(value) is not int or not low <= value <= high:
            raise ConfigurationError("screen_configuration_invalid", f"{name} must be an integer from {low} to {high}")
    return values


def configuration_fields():
    return tuple(FieldDefinition(GROUP + "." + name, GROUP, GROUP,
        ValueKind.BOOLEAN if type(value) is bool else ValueKind.INTEGER, value,
        layers=(ConfigLayer.WINDOW,), owner_id=OWNER,
        **({"minimum": LIMITS[name][0], "maximum": LIMITS[name][1]} if name in LIMITS else {}))
        for name, value in DEFAULTS.items())


class ScreenConfigurationOwner(SparseConfigurationOwner):
    owner_id = OWNER

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


class ScreenSettings:
    """Read one window's Screen options without inventing a second scope."""

    def __init__(self, configuration):
        self.configuration = configuration

    def read(self, context, at):
        snapshot = require(self.configuration.read(context.actor, target=target_for(context),
                                                   group_id=GROUP, observed_at=at))
        return validate({key.removeprefix(GROUP + "."): value for key, value in snapshot.values.items()})


__all__ = ["DEFAULTS", "GROUP", "LIMITS", "OWNER", "ScreenConfigurationOwner", "ScreenSettings",
           "configuration_fields", "target_for", "validate"]
