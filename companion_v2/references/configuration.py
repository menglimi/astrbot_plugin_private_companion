"""Whether a role may use registered reference material at all.

The group decides what the role may put into a generation; it never says where a
picture lives. Reference material is a registered asset (``companion_v2/references``),
so nothing here is a file path — a path in configuration text would bypass the
ownership and visibility checks the asset layer exists to enforce.

Both switches default to off, so an installation that never touches them behaves
exactly as it did before this group existed.
"""

from __future__ import annotations

from companion_v2.configuration.models import ConfigLayer, ConfigTarget, ConfigurationError
from companion_v2.foundation.refs import RoleOwnerRef
from companion_v2.configuration.owners import SparseConfigurationOwner
from companion_v2.configuration.registry import FieldDefinition, ValueKind


GROUP = "photo.reference"
OWNER = GROUP
LAYER = (ConfigLayer.ROLE,)

DEFAULTS = {
    # Off until an operator turns reference material on for this role.
    "enabled": False,
    # Group exposure is a separate opt-in; private use does not depend on it.
    "group_enabled": False,
    # The daily-outfit path stays separate: agreeing to reference pictures is not
    # agreement to a picture every day.
    "daily_outfit_enabled": False,
    # How often the same outfit may come round again.
    "daily_outfit_rotation_days": 14,
}
LIMITS = {"daily_outfit_rotation_days": (1, 365)}
BOOLEAN_FIELDS = ("enabled", "group_enabled", "daily_outfit_enabled")
INTEGER_FIELDS = tuple(LIMITS)


def target_for(context):
    return ConfigTarget(context.scope.installation_id, ConfigLayer.ROLE,
                        context.role_binding.role, bot_id=context.scope.bot_id)


def validate(values):
    if type(values) is not dict or set(values) != set(DEFAULTS):
        raise ConfigurationError("photo_reference_configuration_invalid",
                                 "provide the declared reference settings")
    if any(type(values[name]) is not bool for name in BOOLEAN_FIELDS):
        raise ConfigurationError("photo_reference_configuration_invalid",
                                 "reference switches must be boolean")
    for name in INTEGER_FIELDS:
        low, high = LIMITS[name]
        value = values[name]
        if type(value) is not int or isinstance(value, bool) or not low <= value <= high:
            raise ConfigurationError("photo_reference_configuration_invalid",
                                     f"{name} must be an integer from {low} to {high}")
    return values


def configuration_fields():
    return tuple(FieldDefinition(GROUP + "." + name, GROUP, GROUP,
        ValueKind.BOOLEAN if type(value) is bool else ValueKind.INTEGER, value,
        layers=LAYER, owner_id=OWNER,
        **({"minimum": LIMITS[name][0], "maximum": LIMITS[name][1]} if name in LIMITS else {}))
        for name, value in DEFAULTS.items())


class ReferenceConfigurationOwner(SparseConfigurationOwner):
    owner_id = OWNER

    def preview_set(self, target, fields, *, changes):
        result = super().preview_set(target, fields, changes=changes)
        validate({key.removeprefix(GROUP + "."): item.value for key, item in result.items()})
        return result

    def write(self, target, fields, *, changes, reset, expected_revision, observed_at):
        with self.runtime.transaction():
            result = super().write(target, fields, changes=changes, reset=reset,
                                   expected_revision=expected_revision, observed_at=observed_at)
            validate({key.removeprefix(GROUP + "."): item.value
                      for key, item in self.read(target, fields).fields.items()})
            return result


class ReferenceSettings:
    """Read the role-owned switches after the reference service authorizes use."""

    def __init__(self, owner, *, owner_resolver=None):
        if not isinstance(owner, ReferenceConfigurationOwner):
            raise TypeError("reference settings require their registered configuration owner")
        self.owner = owner
        self.owner_resolver = owner_resolver

    def role_owner(self, context):
        original = RoleOwnerRef(context.scope.installation_id, context.scope.bot_id,
                                context.role_binding.role.subject_id)
        selected = self.owner_resolver(context) if self.owner_resolver is not None else original
        if (not isinstance(selected, RoleOwnerRef)
                or selected.installation_id != original.installation_id
                or selected.persona_id != original.persona_id):
            raise ConfigurationError('reference_owner_invalid', 'reference material must retain its installation and role')
        return selected

    def target(self, context):
        owner = self.role_owner(context)
        return ConfigTarget(owner.installation_id, ConfigLayer.ROLE,
                            context.role_binding.role, bot_id=owner.bot_id)

    def read(self, context, at):
        with self.owner.runtime.read_snapshot():
            snapshot = self.owner.read(self.target(context), configuration_fields())
        return validate({key.removeprefix(GROUP + "."): item.value
                         for key, item in snapshot.fields.items()})


__all__ = ["DEFAULTS", "GROUP", "LAYER", "LIMITS", "OWNER", "ReferenceConfigurationOwner",
           "ReferenceSettings", "configuration_fields", "target_for", "validate"]
