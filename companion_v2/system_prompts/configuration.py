"""Role-scoped overrides for the fixed system prompt blocks."""

from __future__ import annotations

from companion_v2.configuration.models import (
    ApplyMode, ConfigLayer, ConfigTarget, ConfigurationError,
)
from companion_v2.configuration.owners import SparseConfigurationOwner
from companion_v2.configuration.registry import FieldDefinition, ValueKind

from .catalog import FIELD_DEFAULTS


GROUP = "system.prompts"
OWNER = "system.prompts.settings"
MAX_PROMPT_CHARS = 8000


def configuration_fields():
    return tuple(
        FieldDefinition(
            f"{GROUP}.{key}", GROUP, GROUP, ValueKind.TEXT, default,
            layers=(ConfigLayer.ROLE,), apply_mode=ApplyMode.IMMEDIATE,
            owner_id=OWNER, core_only=True,
        )
        for key, default in FIELD_DEFAULTS.items()
    )


def target_for(context):
    return ConfigTarget(
        context.scope.installation_id,
        ConfigLayer.ROLE,
        context.role_binding.role,
        bot_id=context.scope.bot_id,
    )


def validate_values(values):
    if type(values) is not dict or set(values) != set(FIELD_DEFAULTS):
        raise ConfigurationError("system_prompt_configuration_invalid",
                                 "system prompt fields are incomplete")
    for key, value in values.items():
        if type(value) is not str or len(value) > MAX_PROMPT_CHARS:
            raise ConfigurationError(
                "system_prompt_configuration_invalid",
                f"{key} must be text no longer than {MAX_PROMPT_CHARS} characters",
            )
    return values


class RoleSystemPromptConfigurationOwner(SparseConfigurationOwner):
    """Validate every write path with the same bounded text contract."""

    owner_id = OWNER

    @staticmethod
    def _values(snapshot, fields):
        prefix = GROUP + "."
        return {
            field.field_id.removeprefix(prefix): snapshot.fields[field.field_id].value
            for field in fields
        }

    def read(self, target, fields):
        snapshot = super().read(target, fields)
        validate_values(self._values(snapshot, fields))
        return snapshot

    def preview_set(self, target, fields, *, changes):
        current = super().read(target, fields)
        values = self._values(current, fields)
        prefix = GROUP + "."
        values.update({name.removeprefix(prefix): value for name, value in changes.items()})
        validate_values(values)
        return super().preview_set(target, fields, changes=changes)

    def write(self, target, fields, *, changes, reset, expected_revision, observed_at):
        current = super().read(target, fields)
        values = self._values(current, fields)
        prefix = GROUP + "."
        values.update({name.removeprefix(prefix): value for name, value in changes.items()})
        for name in reset:
            short_name = name.removeprefix(prefix)
            values[short_name] = FIELD_DEFAULTS.get(short_name, "")
        validate_values(values)
        return super().write(
            target, fields, changes=changes, reset=reset,
            expected_revision=expected_revision, observed_at=observed_at,
        )
