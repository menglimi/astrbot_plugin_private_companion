"""Role preferences with window-only typing controls."""

from dataclasses import replace

from companion_v2.configuration.models import ConfigLayer, ConfigTarget, ConfigurationError, EffectiveValue
from companion_v2.configuration.owners import OwnerSnapshot, OwnerWrite, SparseConfigurationOwner
from companion_v2.agenda.planning_config import DEFAULTS as PLANNING_DEFAULTS

WINDOW_FIELDS = frozenset({"typing_enabled", "typing_refresh_seconds"})
PRESENCE_FIELDS = frozenset({"presence_enabled", "online_status_enabled"})
ROLE_ONLY_FIELDS = frozenset({"enabled", "maintenance_grant_scope", "calendar_enabled",
    *PLANNING_DEFAULTS, *PRESENCE_FIELDS})


def physical(field):
    name = field.field_id.rsplit(".", 1)[-1]
    return name not in WINDOW_FIELDS


def role_preference(name):
    return name not in WINDOW_FIELDS


class PhysicalSettingsOwner(SparseConfigurationOwner):
    def is_shared(self, field):
        return physical(field)

    @staticmethod
    def role_target(target):
        return ConfigTarget(target.installation_id, ConfigLayer.ROLE, target.role, bot_id=target.bot_id)

    def role_fields(self, fields):
        return tuple(replace(field, layers=(ConfigLayer.ROLE,)) for field in fields if self.is_shared(field))

    @staticmethod
    def window_fields(fields):
        return tuple(replace(field, layers=(ConfigLayer.WINDOW,)) for field in fields
                     if field.field_id.rsplit(".", 1)[-1] not in ROLE_ONLY_FIELDS)

    def read(self, target, fields):
        if target.layer is ConfigLayer.ROLE:
            role = SparseConfigurationOwner.read(self, target, self.role_fields(fields))
            values = {field.field_id: EffectiveValue(field.default, None, False, 0) for field in fields}
            values.update(role.fields)
            return OwnerSnapshot(role.revision, role.dependencies, values, role.lifecycle_token)

        local_fields = self.window_fields(fields)
        local = SparseConfigurationOwner.read(self, target, local_fields)
        role = SparseConfigurationOwner.read(self, self.role_target(target), self.role_fields(fields))
        values = {}
        for field in self.role_fields(fields):
            value = role.fields[field.field_id]
            local_value = local.fields.get(field.field_id)
            if (field.field_id.rsplit(".", 1)[-1] not in ROLE_ONLY_FIELDS
                    and local_value is not None and local_value.source is not None):
                value = local_value
            values[field.field_id] = replace(value, explicit=value.source == target)
        values.update({key: value for key, value in local.fields.items() if key not in values})
        return OwnerSnapshot(local.revision + role.revision,
            tuple(dict.fromkeys((*local.dependencies, *role.dependencies))), values)

    def write(self, target, fields, *, changes, reset, expected_revision, observed_at):
        with self.runtime.transaction():
            if target.layer is ConfigLayer.ROLE:
                role_fields = self.role_fields(fields)
                role_ids = {field.field_id for field in role_fields}
                if (set(changes) | set(reset)) - role_ids:
                    raise ConfigurationError("configuration_scope_not_supported",
                        "field is not available as a role default")
                current = SparseConfigurationOwner.read(self, target, role_fields)
                if current.revision != expected_revision:
                    raise ConfigurationError("configuration_revision_conflict", "role defaults changed")
                return SparseConfigurationOwner.write(self, target, role_fields, changes=changes, reset=reset,
                    expected_revision=expected_revision, observed_at=observed_at)

            role = SparseConfigurationOwner.read(self, self.role_target(target), self.role_fields(fields))
            local_fields = self.window_fields(fields)
            local_ids = {field.field_id for field in local_fields}
            if (set(changes) | set(reset)) - local_ids:
                raise ConfigurationError("configuration_scope_not_supported",
                    "role preferences cannot be written at a window scope")
            local = SparseConfigurationOwner.read(self, target, local_fields)
            if local.revision + role.revision != expected_revision:
                raise ConfigurationError("configuration_revision_conflict", "role defaults or local overrides changed")
            return SparseConfigurationOwner.write(self, target, local_fields, changes=changes, reset=reset,
                expected_revision=local.revision, observed_at=observed_at)
