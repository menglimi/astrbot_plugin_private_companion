"""Configuration owners that share selected fields across a role's windows."""

from dataclasses import replace

from .models import ConfigLayer, ConfigTarget, ConfigurationError, EffectiveValue
from .owners import OwnerSnapshot, OwnerWrite, SparseConfigurationOwner


class RoleSharedSettingsOwner(SparseConfigurationOwner):
    """Share selected preferences across windows while retaining legacy fallbacks."""

    def is_shared(self, field):
        raise NotImplementedError

    def role_value_overrides_window(self, field):
        return False

    def role_explicit_value_overrides_window(self, field):
        return False

    def adopt_window_value_on_write(self, field, changed_fields):
        return True

    def window_write_role_default(self, field):
        """Whether a legacy window editor routes shared-field writes to the role."""
        return True

    def window_reset_role_default(self, field):
        """Whether a window editor's inherit action clears the role override."""
        return False

    def role_target(self, target):
        return ConfigTarget(target.installation_id, ConfigLayer.ROLE, target.role, bot_id=target.bot_id)

    def window_fields(self, fields):
        return tuple(replace(field, layers=(ConfigLayer.WINDOW,)) for field in fields)

    def role_fields(self, fields):
        return tuple(replace(field, layers=(ConfigLayer.ROLE,)) for field in fields if self.is_shared(field))

    def read(self, target, fields):
        if target.layer is ConfigLayer.ROLE:
            role_fields = self.role_fields(fields)
            role = super().read(target, role_fields)
            values = {field.field_id: EffectiveValue(field.default, None, False, 0) for field in fields}
            values.update(role.fields)
            return OwnerSnapshot(role.revision, role.dependencies, values, role.lifecycle_token)
        local = super().read(target, self.window_fields(fields))
        role = super().read(self.role_target(target), self.role_fields(fields))
        values = dict(local.fields)
        for field in self.role_fields(fields):
            key = field.field_id
            value = role.fields[key]
            # Some role settings are authoritative even at their default, so an old
            # window value cannot split their behavior across private conversations.
            if (self.role_value_overrides_window(field) or
                    (value.source is not None and (local.fields[key].source is None or
                                                   self.role_explicit_value_overrides_window(field)))):
                values[key] = replace(value, explicit=value.source == target)
        return OwnerSnapshot(local.revision + role.revision,
            tuple(dict.fromkeys((*local.dependencies, *role.dependencies))), values)

    def write(self, target, fields, *, changes, reset, expected_revision, observed_at):
        with self.store.transaction():
            if target.layer is ConfigLayer.ROLE:
                role_fields = self.role_fields(fields)
                role_ids = {field.field_id for field in role_fields}
                if (set(changes) | set(reset)) - role_ids:
                    raise ConfigurationError("configuration_scope_not_supported",
                                             "field is not a role-shared preference")
                current = super().read(target, role_fields)
                if current.revision != expected_revision:
                    raise ConfigurationError("configuration_revision_conflict",
                                             "role settings changed in another editor")
                return super().write(target, role_fields, changes=changes, reset=reset,
                                     expected_revision=expected_revision, observed_at=observed_at)
            current = self.read(target, fields)
            if current.revision != expected_revision:
                raise ConfigurationError("configuration_revision_conflict", "role settings changed in another window")
            role_target = self.role_target(target)
            role_fields = self.role_fields(fields)
            role_ids = {field.field_id for field in role_fields}
            local_fields = self.window_fields(fields)
            local = super().read(target, local_fields)
            role = super().read(role_target, role_fields)
            role_changes = {key: value for key, value in changes.items()
                            if key in role_ids and self.window_write_role_default(
                                next(field for field in role_fields if field.field_id == key))}
            role_resets = {key for key in reset if key in role_ids and self.window_write_role_default(
                next(field for field in role_fields if field.field_id == key))}
            role_inherit_resets = {key for key in role_resets if self.window_reset_role_default(
                next(field for field in role_fields if field.field_id == key))}
            # Adopt this window's pre-existing value only once, before the role
            # has an explicit value. Other windows can never overwrite it later.
            for field in role_fields:
                if (self.window_write_role_default(field) and role.fields[field.field_id].source is None
                        and self.adopt_window_value_on_write(field, frozenset((*changes, *reset)))):
                    role_changes.setdefault(field.field_id, current.fields[field.field_id].value)
                if field.field_id in role_resets - role_inherit_resets:
                    role_changes[field.field_id] = field.default
            saved_role = (super().write(role_target, role_fields, changes=role_changes, reset=(),
                expected_revision=role.revision, observed_at=observed_at) if role_changes else
                OwnerWrite(role.revision, ()))
            if role_inherit_resets:
                reset_role = super().write(role_target, role_fields, changes={}, reset=tuple(role_inherit_resets),
                    expected_revision=saved_role.revision, observed_at=observed_at)
                saved_role = OwnerWrite(reset_role.revision,
                    tuple(sorted(set(saved_role.changed_fields) | set(reset_role.changed_fields))))
            local_changes = {key: value for key, value in changes.items() if key not in role_changes}
            local_resets = tuple(key for key in reset if key not in role_resets)
            saved_window = (super().write(target, local_fields, changes=local_changes, reset=local_resets,
                expected_revision=local.revision, observed_at=observed_at) if local_changes or local_resets else
                OwnerWrite(local.revision, ()))
            return OwnerWrite(saved_role.revision + saved_window.revision,
                tuple(sorted(set(saved_role.changed_fields) | set(saved_window.changed_fields))))
