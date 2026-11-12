"""The role's wardrobe: how much of it may reach a generation, and how.

Wardrobe items are data, not configuration. V1 kept the item list inside the
config document with an ``invisible`` flag, which meant an installation's clothes
lived in a settings blob. V2 keeps the items as registered rows
(``companion_v2/references``) and leaves only switches and ceilings here — a list
of items is not a scalar, and the configuration registry cannot hold one.

Both switches default to off, and the two ceilings default to the V1 values, so an
installation that never edits this group behaves as it did before.
"""

from __future__ import annotations

from companion_v2.configuration.models import ConfigLayer, ConfigTarget, ConfigurationError
from companion_v2.configuration.owners import SparseConfigurationOwner
from companion_v2.configuration.registry import FieldDefinition, ValueKind
from companion_v2.execution.models import require


GROUP = "wardrobe"
OWNER = GROUP
LAYER = (ConfigLayer.ROLE,)

# V1 bounded the operator's own words at 800 characters and never truncated them.
MAX_TENDENCY = 800
MAX_IMAGE_PROMPT = 800

DEFAULTS = {
    # Off until an operator turns the wardrobe on for this role.
    "enabled": False,
    # The operator's own note about how the role tends to dress.
    "tendency": "",
    # Whether the wardrobe may be described in a prompt at all.
    "prompt_enabled": False,
    # How many items one prompt may name.
    "prompt_max_items": 12,
    # How many pictures one outfit request may take in.
    "image_max_count": 3,
    # How the role should be dressed, in the operator's words.
    "image_prompt": "",
}
LIMITS = {"prompt_max_items": (1, 24), "image_max_count": (1, 8)}
BOOLEAN_FIELDS = ("enabled", "prompt_enabled")
INTEGER_FIELDS = tuple(LIMITS)
TEXT_FIELDS = {"tendency": MAX_TENDENCY, "image_prompt": MAX_IMAGE_PROMPT}


def target_for(context):
    return ConfigTarget(context.scope.installation_id, ConfigLayer.ROLE,
                        context.role_binding.role, bot_id=context.scope.bot_id)


def validate(values):
    if type(values) is not dict or set(values) != set(DEFAULTS):
        raise ConfigurationError("wardrobe_configuration_invalid",
                                 "provide the declared wardrobe settings")
    if any(type(values[name]) is not bool for name in BOOLEAN_FIELDS):
        raise ConfigurationError("wardrobe_configuration_invalid", "wardrobe switches must be boolean")
    for name, maximum in TEXT_FIELDS.items():
        value = values[name]
        if type(value) is not str:
            raise ConfigurationError("wardrobe_configuration_invalid", f"{name} must be text")
        # Refused, never truncated: a silently shortened note would look saved.
        if len(value) > maximum:
            raise ConfigurationError("wardrobe_configuration_invalid",
                                     f"{name} exceeds {maximum} characters")
    for name in INTEGER_FIELDS:
        low, high = LIMITS[name]
        value = values[name]
        if type(value) is not int or isinstance(value, bool) or not low <= value <= high:
            raise ConfigurationError("wardrobe_configuration_invalid",
                                     f"{name} must be an integer from {low} to {high}")
    return values


def configuration_fields():
    definitions = []
    for name, value in DEFAULTS.items():
        if name in TEXT_FIELDS:
            kind = ValueKind.TEXT
        elif type(value) is bool:
            kind = ValueKind.BOOLEAN
        else:
            kind = ValueKind.INTEGER
        bounds = ({"minimum": LIMITS[name][0], "maximum": LIMITS[name][1]} if name in LIMITS
                  else {"maximum": TEXT_FIELDS[name]} if name in TEXT_FIELDS else {})
        definitions.append(FieldDefinition(GROUP + "." + name, GROUP, GROUP, kind, value,
                                          layers=LAYER, owner_id=OWNER, **bounds))
    return tuple(definitions)


class WardrobeConfigurationOwner(SparseConfigurationOwner):
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


class WardrobeSettings:
    """Read one role's wardrobe options without inventing a second scope."""

    def __init__(self, configuration, *, target_resolver=None):
        self.configuration = configuration
        self.target_resolver = target_resolver

    def target(self, context):
        return self.target_resolver(context) if self.target_resolver else target_for(context)

    def read(self, context, at):
        snapshot = require(self.configuration.read(context.actor, target=target_for(context),
                                                  group_id=GROUP, observed_at=at))
        target = self.target(context)
        if target != target_for(context):
            # Authorize the original actor above; share only role material.
            owner = WardrobeConfigurationOwner(self.configuration.runtime)
            with self.configuration.runtime.read_snapshot():
                shared = owner.read(target, configuration_fields())
            return validate({key.removeprefix(GROUP + '.'): item.value
                             for key, item in shared.fields.items()})
        return validate({key.removeprefix(GROUP + "."): value for key, value in snapshot.values.items()})


__all__ = ["DEFAULTS", "GROUP", "LAYER", "LIMITS", "MAX_IMAGE_PROMPT", "MAX_TENDENCY", "OWNER",
           "WardrobeConfigurationOwner", "WardrobeSettings", "configuration_fields", "target_for",
           "validate"]
