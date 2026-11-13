"""Map G6 scope preferences to the shared configuration entry point, without copies."""

from __future__ import annotations

from datetime import datetime
from typing import Mapping

from companion_v2.configuration.models import (
    ApplyMode, ConfigLayer, ConfigTarget, ConfigurationError, EffectiveValue, Scalar, fingerprint,
)
from companion_v2.configuration.owners import OwnerSnapshot, OwnerWrite
from companion_v2.configuration.registry import FieldDefinition, ValueKind
from companion_v2.storage.runtime import StorageRuntime

from .repositories import CapabilityRepository


class CapabilityConfigurationOwner:
    """Existing capability_settings stays authoritative and has no inheritance sentinel."""

    supports_reset = False
    store_independent = True

    def __init__(self, runtime: StorageRuntime, *, extension_id: str) -> None:
        if not isinstance(extension_id, str) or not extension_id or extension_id != extension_id.strip():
            raise ValueError("extension identity is required")
        self.runtime = runtime
        self.extension_id = extension_id
        self.owner_id = f"capability.settings.{extension_id}"
        self.group_id = f"capability.{extension_id}.preferences"
        self.enabled_key = f"{self.group_id}.proactive_enabled"
        self.weight_key = f"{self.group_id}.thought_weight"
        self.fields = (
            FieldDefinition(self.enabled_key, self.group_id, self.owner_id, ValueKind.BOOLEAN, False,
                            layers=(ConfigLayer.SCOPE,), apply_mode=ApplyMode.NEXT_TASK, owner_id=self.owner_id),
            FieldDefinition(self.weight_key, self.group_id, self.owner_id, ValueKind.WEIGHT, 1.0,
                            layers=(ConfigLayer.SCOPE,), apply_mode=ApplyMode.NEXT_TASK, owner_id=self.owner_id,
                            decimal_places=None),
        )
        self.repository = CapabilityRepository(runtime)

    def _validate(self, target: ConfigTarget, fields: tuple[FieldDefinition, ...]) -> None:
        if target.layer is not ConfigLayer.SCOPE or target.scope is None:
            raise ConfigurationError("configuration_scope_not_supported", "capability preferences belong to an exact scope")
        if tuple(sorted(fields, key=lambda f: f.field_id)) != tuple(sorted(self.fields, key=lambda f: f.field_id)):
            raise ConfigurationError("configuration_owner_mismatch", "capability fields do not match their owner")

    def read(self, target: ConfigTarget, fields: tuple[FieldDefinition, ...]) -> OwnerSnapshot:
        self._validate(target, fields)
        result = self.repository.projection(extension_id=self.extension_id, scope=target.scope)
        if result.failure is not None:
            raise ConfigurationError(result.failure.code, result.failure.reason)
        projection = result.unwrap()
        settings = projection.settings
        revision = settings.revision if settings else 0
        values = {
            self.enabled_key: settings.proactive_enabled if settings else False,
            self.weight_key: settings.thought_weight if settings else 1.0,
        }
        effective = {field.field_id: EffectiveValue(
            field.validate(values[field.field_id]), target if settings else None, settings is not None, revision,
        ) for field in fields}
        return OwnerSnapshot(
            revision, ((self.owner_id, revision),), effective,
            lifecycle_token=fingerprint([projection.installation.revision, projection.installation.desired_state.value]),
        )

    def write(
        self, target: ConfigTarget, fields: tuple[FieldDefinition, ...], *,
        changes: Mapping[str, Scalar], reset: tuple[str, ...],
        expected_revision: int, observed_at: datetime,
    ) -> OwnerWrite:
        self._validate(target, fields)
        if reset:
            raise ConfigurationError("configuration_inheritance_not_supported", "capability scope preferences do not inherit")
        current = self.read(target, fields)
        if current.revision != expected_revision:
            raise ConfigurationError("configuration_revision_conflict", "capability preferences changed")
        values = {key: field.value for key, field in current.fields.items()}
        changed = tuple(sorted(key for key, value in changes.items() if not current.fields[key].explicit or values[key] != value))
        if not changed:
            return OwnerWrite(current.revision, ())
        values.update(changes)
        result = self.repository.set_settings(
            extension_id=self.extension_id, scope=target.scope,
            proactive_enabled=values[self.enabled_key], thought_weight=values[self.weight_key],
            expected_revision=expected_revision, observed_at=observed_at,
        )
        if result.failure is not None:
            raise ConfigurationError(result.failure.code, result.failure.reason)
        # The existing record is a complete pair, so its first creation makes both explicit.
        if current.revision == 0:
            changed = tuple(sorted(values))
        return OwnerWrite(result.unwrap().revision, changed)

    def preview_set(self, target: ConfigTarget, fields: tuple[FieldDefinition, ...], *,
                    changes: Mapping[str, Scalar]) -> Mapping[str, EffectiveValue]:
        current = self.read(target, fields)
        values = {key: field.value for key, field in current.fields.items()}
        changed = any(not current.fields[key].explicit or values[key] != value for key, value in changes.items())
        if not changed:
            return current.fields
        values.update(changes)
        # The first settings record makes the entire stored pair explicit.
        return {key: EffectiveValue(value, target, True, current.revision + 1) for key, value in values.items()}
