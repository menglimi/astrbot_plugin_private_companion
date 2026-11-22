"""Bookshelf options for one role.

The group decides what the archive may do, never where a work's text lives: assets stay
with the provider that supplies them. A window never carries its own ceiling, so every
field shares role scope like ``content.authoring``.
"""

from companion_v2.configuration.models import ConfigLayer, ConfigTarget, ConfigurationError
from companion_v2.configuration.owners import SparseConfigurationOwner
from companion_v2.configuration.registry import FieldDefinition, ValueKind
from companion_v2.execution.models import require


GROUP = "library"
OWNER = GROUP
LAYER = (ConfigLayer.ROLE,)

DEFAULTS = {
    # Off until an operator turns the shelf on for this role.
    "enabled": False,
    # Reviews are written from what was actually read; the switch governs whether the
    # role may record them at all, not whether evidence is required (it always is).
    "reviews_enabled": True,
    # A provider-supplied asset is read through the read registry, so the role decides
    # how much of one work may enter a single turn.
    "reading_excerpt_chars": 1200,
    # Manual entries are the operator's own list; a provider entry always outranks them.
    "manual_entries_enabled": True,
    # Bounds the catalog the shelf will list in one page.
    "max_entries_per_page": 64,
    # C2-3a: a rating may only steer taste once enough of them exist, so the
    # minimum is what stops a single opinion from looking like a preference.
    "rating_enabled": False,
    "preference_influence_enabled": False,
    "preference_min_ratings": 5,
    "preference_max_terms": 8,
    # C2-3b: the shelf speaks rarely, and a wall-clock gate is what makes a restart
    # unable to bypass the interval (the cursor is stored, not just held in memory).
    "min_interval_hours": 18,
    "share_probability": 0.18,
    "ask_probability": 0.16,
}
LIMITS = {"reading_excerpt_chars": (200, 8000), "max_entries_per_page": (1, 200),
          "preference_min_ratings": (1, 64), "preference_max_terms": (1, 32),
          "min_interval_hours": (1, 720)}
PROBABILITY_FIELDS = ("share_probability", "ask_probability")
BOOLEAN_FIELDS = ("enabled", "reviews_enabled", "manual_entries_enabled", "rating_enabled",
                  "preference_influence_enabled")
INTEGER_FIELDS = tuple(LIMITS)


def target_for(context):
    return ConfigTarget(context.scope.installation_id, ConfigLayer.ROLE,
                        context.role_binding.role, bot_id=context.scope.bot_id)


def validate(values):
    if type(values) is not dict or set(values) != set(DEFAULTS):
        raise ConfigurationError("library_configuration_invalid", "provide the declared bookshelf settings")
    if any(type(values[name]) is not bool for name in BOOLEAN_FIELDS):
        raise ConfigurationError("library_configuration_invalid", "bookshelf switches must be boolean")
    for name in INTEGER_FIELDS:
        low, high = LIMITS[name]
        value = values[name]
        if type(value) is not int or isinstance(value, bool) or not low <= value <= high:
            raise ConfigurationError("library_configuration_invalid",
                                     f"{name} must be an integer from {low} to {high}")
    for name in PROBABILITY_FIELDS:
        value = values[name]
        if type(value) not in (int, float) or isinstance(value, bool) or not 0.0 <= float(value) <= 1.0:
            raise ConfigurationError("library_configuration_invalid",
                                     f"{name} must be a probability from 0 to 1")
    return values


def configuration_fields():
    def kind_of(name, value):
        if name in PROBABILITY_FIELDS:
            return ValueKind.PROBABILITY
        return ValueKind.BOOLEAN if type(value) is bool else ValueKind.INTEGER

    return tuple(FieldDefinition(GROUP + "." + name, GROUP, GROUP,
        kind_of(name, value), value,
        layers=LAYER, owner_id=OWNER,
        **({"minimum": LIMITS[name][0], "maximum": LIMITS[name][1]} if name in LIMITS else {}))
        for name, value in DEFAULTS.items())


class LibraryConfigurationOwner(SparseConfigurationOwner):
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


class LibrarySettings:
    """Read one role's bookshelf options without inventing a second scope."""

    def __init__(self, configuration):
        self.configuration = configuration

    def read(self, context, at):
        snapshot = require(self.configuration.read(context.actor, target=target_for(context),
                                                   group_id=GROUP, observed_at=at))
        return validate({key.removeprefix(GROUP + "."): value for key, value in snapshot.values.items()})


__all__ = ["DEFAULTS", "GROUP", "LAYER", "LIMITS", "LibraryConfigurationOwner", "LibrarySettings",
           "OWNER", "configuration_fields", "target_for", "validate"]
