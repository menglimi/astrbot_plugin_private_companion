"""Atomic sparse patches and explicit runtime adoption, with no external effects."""

from __future__ import annotations

from datetime import datetime
import json
from typing import Mapping

from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import OperationRef
from companion_v2.identity_policy.models import AuthorizationTier, ResolvedIdentity
from companion_v2.identity_policy.object_access import ObjectAccessPolicy
from companion_v2.identity_policy.repository import PersistentIdentityDirectory
from companion_v2.identity_policy.personas import PersonaDirectory
from companion_v2.storage.codec import encode_timestamp
from companion_v2.storage.models import OperationState
from companion_v2.storage.repositories import OperationRepository
from companion_v2.storage.runtime import StorageRuntime

from .models import (
    ApplicationStatus, ApplyMode, ConfigLayer, ConfigTarget, ConfigurationError,
    ConfigurationChanges, ConfigurationPreview, ConfigurationReceipt, ConfigurationSnapshot, encode, fingerprint,
)
from .owners import ConfigurationOwner, SparseConfigurationOwner
from .registry import FieldRegistry


def _require(result: Outcome):
    if result.failure is not None:
        raise ConfigurationError(result.failure.code, result.failure.reason)
    return result.unwrap()


class ConfigurationService:
    OWNER = "configuration"

    def __init__(
        self, runtime: StorageRuntime, registry: FieldRegistry, *,
        store: StorageRuntime | None = None, owners: tuple[ConfigurationOwner, ...] = (),
    ) -> None:
        # ``runtime`` owns identity/authorization; ``store`` owns configuration
        # rows and configuration operation receipts.  They are the same runtime
        # unless the host explicitly splits the configuration database.
        self.runtime = runtime
        self.store = (store or runtime).configuration_store
        self.registry = registry
        self.identity = PersistentIdentityDirectory(runtime)
        self.policy = ObjectAccessPolicy(runtime)
        self.operations = OperationRepository(self.store)
        self._active_generations: dict[str, int] = {}
        self._owners: dict[str, ConfigurationOwner] = {self.OWNER: SparseConfigurationOwner(self.store)}
        for owner in owners:
            if owner.owner_id in self._owners:
                raise ValueError("configuration owners must be unique")
            # Capability owners keep their existing ``capability_settings``
            # table on the main runtime; sparse configuration owners must use
            # the configuration store.  ``store_independent`` is the explicit
            # opt-out for that second table.
            if getattr(owner, "store_independent", False):
                valid = owner.runtime is self.runtime
            else:
                valid = getattr(owner, "store", None) is self.store
            if not valid:
                raise ValueError(
                    f"configuration owner {owner.owner_id!r} must share its declared storage runtime")
            self._owners[owner.owner_id] = owner
        missing = sorted({field.owner_id for field in registry.fields.values()
                          if field.owner_id not in self._owners})
        if missing:
            raise ValueError(f"every field requires its registered configuration owner: missing={missing}")

    def _authorize(self, actor: ResolvedIdentity, target: ConfigTarget, capability: str, now: datetime,
                   *, core_only: bool = False):
        if target.installation_id != self.runtime.installation_id:
            raise ConfigurationError("scope_installation_mismatch", "configuration belongs to another installation")
        access = _require(self.policy.scope_access(
            actor, scope=target.authorization_scope, action=capability, observed_at=now,
            allow_private_owner=True, core_only=core_only,
            owner=target.subject if target.layer is ConfigLayer.RELATIONSHIP else None,
        ))
        if target.subject is not None:
            if access.tier is not AuthorizationTier.CORE and target.subject != actor.subject:
                raise ConfigurationError("configuration_subject_mismatch", "relationship preferences belong to another subject")
            if target.layer is ConfigLayer.WINDOW:
                route = PersonaDirectory(self.runtime).route(target.scope)
                if route is None or route.user != target.subject:
                    raise ConfigurationError("configuration_subject_mismatch", "window must retain its registered relationship subject")
        binding_revision = 0
        if target.layer is ConfigLayer.WINDOW:
            result = self.identity.resolve_role(target.scope, observed_at=now)
            binding = result.value
            if access.tier is not AuthorizationTier.CORE:
                binding = _require(result)
            if access.tier is not AuthorizationTier.CORE and binding.role != target.role:
                raise ConfigurationError("role_binding_changed", "window is not bound to the requested role")
            binding_revision = binding.revision if binding else 0
        return access, binding_revision

    def _snapshot(
        self, actor: ResolvedIdentity, target: ConfigTarget, group_id: str, now: datetime,
        capability: str = "configuration.read",
    ) -> ConfigurationSnapshot:
        definitions = self.registry.group(group_id)
        access, binding_revision = self._authorize(actor, target, capability, now, core_only=definitions[0].core_only)
        if target.layer not in definitions[0].layers:
            raise ConfigurationError("configuration_scope_not_supported", "group is not available at this configuration layer")
        owner = self._owners[definitions[0].owner_id]
        owned = owner.read(target, definitions)
        layers = target.chain()
        placeholders = ",".join("?" for _ in layers)
        sequence = self.store.read_one(
            f"""SELECT COALESCE(MAX(sequence), 0) AS n FROM configuration_changes
                WHERE target_key IN ({placeholders}) AND group_id = ? AND changed = 1""",
            (*(layer.key for layer in layers), group_id),
        )["n"]
        effective = owned.fields
        resolution = fingerprint({
            "schema": self.registry.fingerprint, "target": target.key, "group": group_id,
            "role_binding_revision": binding_revision,
            "owner_lifecycle": owned.lifecycle_token,
            "fields": {key: [item.value, item.source.key if item.source else None, item.source_revision]
                       for key, item in effective.items()},
        })
        edit_token = fingerprint({
            "resolution": resolution, "revisions": owned.dependencies,
            # A reset removes the sparse value row. The group's change sequence
            # still detects set/reset cycles, including changes in its parents.
            "group_changes": sequence,
            "actor_binding": [actor.binding_id, actor.binding_revision],
            # Authorization is rechecked inside every write/replay transaction.
            # Global policy counters also advance for unrelated users and newly
            # registered memories/reminders; those must not expire this editor.
            "access_tier": access.tier.value,
        })
        return ConfigurationSnapshot(
            target, group_id, self.registry.version, owned.revision, sequence,
            edit_token, resolution, effective, definitions[0].consumer_id, definitions[0].apply_mode,
        )

    def read(
        self, actor: ResolvedIdentity, *, target: ConfigTarget, group_id: str, observed_at: datetime,
    ) -> Outcome[ConfigurationSnapshot]:
        try:
            with self.store.read_snapshot():
                result = self._snapshot(actor, target, group_id, observed_at)
            return Outcome.success(result)
        except ConfigurationError as error:
            return Outcome.failed(error.code, str(error))

    def patch(
        self, actor: ResolvedIdentity, *, target: ConfigTarget, group_id: str,
        operation: OperationRef, expected_edit_token: str, changes: Mapping[str, object],
        reset_to_inherit: tuple[str, ...] = (), observed_at: datetime,
    ) -> Outcome[ConfigurationReceipt]:
        try:
            with self.store.transaction() as connection:
                snapshot = self._snapshot(actor, target, group_id, observed_at, "configuration.write")
                definitions = self.registry.group(group_id)
                owner = self._owners[definitions[0].owner_id]
                if operation.scope != target.authorization_scope:
                    raise ConfigurationError("operation_scope_mismatch", "operation is not owned by this configuration target")
                if operation.owner != owner.owner_id:
                    # An owner rename must not turn an already accepted
                    # operation into an idempotency conflict.  The alias is
                    # valid only when the exact old operation is already in
                    # the ledger; callers cannot mint new operations under a
                    # retired owner.
                    legacy_ids = tuple(getattr(owner, "legacy_owner_ids", ()))
                    existing = self.operations.get(operation.operation_id)
                    if (operation.owner not in legacy_ids or not existing.is_success
                            or existing.unwrap().operation != operation):
                        raise ConfigurationError("operation_scope_mismatch", "operation is not owned by this configuration target")
                normalized = self._normalize_patch(target, definitions, changes, reset_to_inherit)
                parameter_hash = fingerprint({
                    "target": target.document(), "group": group_id, "schema": self.registry.fingerprint,
                    "actor": [actor.principal.host_id, actor.principal.platform, actor.principal.native_account_id,
                              actor.binding_id, actor.binding_revision],
                    "set": normalized, "reset": sorted(reset_to_inherit), "expected": expected_edit_token,
                })
                reserved = _require(self.operations.reserve(operation, parameter_hash=parameter_hash, observed_at=observed_at))
                if reserved.replayed:
                    row = connection.execute("SELECT result_json FROM configuration_changes WHERE operation_id = ?", (reserved.operation.operation_id,)).fetchone()
                    if row is None:
                        raise ConfigurationError("configuration_operation_incomplete", "original configuration result is unavailable")
                    return Outcome.success(self._receipt(json.loads(row["result_json"]), replayed=True))
                if snapshot.edit_token != expected_edit_token:
                    raise ConfigurationError("configuration_revision_conflict", "configuration, inheritance, binding, or policy changed")
                timestamp = encode_timestamp(observed_at)
                connection.execute(
                    "INSERT OR IGNORE INTO configuration_targets VALUES (?, ?, 0, ?)",
                    (target.key, encode(target.document()), timestamp),
                )
                written = owner.write(
                    target, definitions, changes=normalized, reset=reset_to_inherit,
                    expected_revision=snapshot.revision, observed_at=observed_at,
                )
                result = {"operation_id": operation.operation_id, "target_key": target.key,
                          "revision": written.revision, "changed_fields": written.changed_fields}
                connection.execute(
                    """INSERT INTO configuration_changes(operation_id, target_key, group_id, changed, result_json, created_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (operation.operation_id, target.key, group_id, int(bool(written.changed_fields)), encode(result), timestamp),
                )
                _require(self.operations.transition(
                    operation.operation_id, expected_state=OperationState.ACCEPTED,
                    next_state=OperationState.COMPLETED, observed_at=observed_at,
                ))
            return Outcome.success(self._receipt(result, replayed=False))
        except ConfigurationError as error:
            return Outcome.failed(error.code, str(error))

    @staticmethod
    def _normalize_patch(target, definitions, changes, reset_to_inherit=()):
        fields = {field.field_id: field for field in definitions}
        if len(set(reset_to_inherit)) != len(reset_to_inherit) or set(changes).intersection(reset_to_inherit):
            raise ConfigurationError("invalid_configuration_patch", "set and reset must be disjoint and unique")
        normalized = {}
        for key in set(changes).union(reset_to_inherit):
            field = fields.get(key)
            if field is None:
                raise ConfigurationError("unknown_configuration_field", "field is not registered in the requested group")
            if target.layer not in field.layers:
                raise ConfigurationError("configuration_scope_not_supported", "field does not support this configuration layer")
            if key in changes:
                normalized[key] = field.validate(changes[key])
        return normalized

    def preview_changes(self, actor: ResolvedIdentity, *, target: ConfigTarget, group_id: str,
                        changes: Mapping[str, object], observed_at: datetime) -> Outcome[ConfigurationPreview]:
        """Preview explicit values through the same owner and permissions as patch."""
        try:
            with self.store.read_snapshot():
                snapshot = self._snapshot(actor, target, group_id, observed_at, "configuration.write")
                definitions = self.registry.group(group_id)
                normalized = self._normalize_patch(target, definitions, changes)
                owner = self._owners[definitions[0].owner_id]
                preview = owner.preview_set(target, definitions, changes=normalized)
                return Outcome.success(ConfigurationPreview(snapshot, normalized, preview, owner.supports_reset))
        except ConfigurationError as error:
            return Outcome.failed(error.code, str(error))

    def changes_since(
        self, actor: ResolvedIdentity, *, target: ConfigTarget, group_id: str,
        after: int, limit: int = 50, observed_at: datetime,
    ) -> Outcome[ConfigurationChanges]:
        """Bounded invalidation feed; re-read current values rather than replay old patches."""
        try:
            if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 100:
                raise ConfigurationError("invalid_configuration_cursor", "expected a nonnegative cursor and limit 1..100")
            with self.store.read_snapshot():
                self._snapshot(actor, target, group_id, observed_at)
                layers = target.chain()
                placeholders = ",".join("?" for _ in layers)
                rows = self.store.read_all(
                    f"""SELECT sequence FROM configuration_changes
                        WHERE target_key IN ({placeholders}) AND group_id = ? AND changed = 1 AND sequence > ?
                        ORDER BY sequence LIMIT ?""",
                    (*(layer.key for layer in layers), group_id, after, limit + 1),
                )
                sequences = tuple(row["sequence"] for row in rows[:limit])
                return Outcome.success(ConfigurationChanges(
                    target, group_id, sequences, sequences[-1] if sequences else after, len(rows) > limit,
                ))
        except ConfigurationError as error:
            return Outcome.failed(error.code, str(error))

    @staticmethod
    def _receipt(data: dict, *, replayed: bool) -> ConfigurationReceipt:
        return ConfigurationReceipt(data["operation_id"], data["target_key"], data["revision"],
            tuple(data["changed_fields"]), replayed)

    def start_consumer(self, consumer_id: str, *, expected_generation: int) -> int:
        """Trusted lifecycle hook; a new process must acquire a fresh generation."""
        if consumer_id not in {field.consumer_id for field in self.registry.fields.values()}:
            raise ConfigurationError("unknown_configuration_consumer", "consumer is not registered")
        with self.store.transaction() as connection:
            row = connection.execute("SELECT generation FROM configuration_consumers WHERE consumer_id = ?", (consumer_id,)).fetchone()
            generation = row["generation"] if row else 0
            if type(expected_generation) is not int or expected_generation != generation:
                raise ConfigurationError("consumer_generation_conflict", "consumer generation changed")
            sequence = connection.execute("SELECT COALESCE(MAX(sequence), 0) FROM configuration_changes").fetchone()[0]
            connection.execute(
                """INSERT INTO configuration_consumers VALUES (?, ?, ?)
                   ON CONFLICT(consumer_id) DO UPDATE SET generation = excluded.generation, started_sequence = excluded.started_sequence""",
                (consumer_id, generation + 1, sequence),
            )
        self._active_generations[consumer_id] = generation + 1
        return generation + 1

    def receipt(
        self, actor: ResolvedIdentity, *, target: ConfigTarget, operation_id: str, observed_at: datetime,
    ) -> Outcome[ConfigurationReceipt]:
        try:
            with self.store.read_snapshot():
                self._authorize(actor, target, "configuration.write", observed_at)
                row = self.store.read_one(
                    "SELECT result_json, group_id FROM configuration_changes WHERE operation_id = ? AND target_key = ?",
                    (operation_id, target.key),
                )
                if row is None:
                    raise ConfigurationError("configuration_operation_not_found", "no configuration receipt in this target")
                self._authorize(actor, target, "configuration.write", observed_at,
                                core_only=self.registry.group(row["group_id"])[0].core_only)
                return Outcome.success(self._receipt(json.loads(row["result_json"]), replayed=True))
        except ConfigurationError as error:
            return Outcome.failed(error.code, str(error))

    def report_application(
        self, actor: ResolvedIdentity, *, snapshot: ConfigurationSnapshot, generation: int,
        succeeded: bool, observed_at: datetime,
    ) -> Outcome[ApplicationStatus]:
        """Trusted consumer attestation after local adoption; never a page write API."""
        try:
            with self.store.transaction() as connection:
                current = self._snapshot(actor, snapshot.target, snapshot.group_id, observed_at)
                if (current.edit_token != snapshot.edit_token or current.values != snapshot.values
                        or current.resolution_token != snapshot.resolution_token):
                    raise ConfigurationError("configuration_revision_conflict", "consumer observed an obsolete configuration")
                consumer = connection.execute("SELECT * FROM configuration_consumers WHERE consumer_id = ?", (current.consumer_id,)).fetchone()
                if (consumer is None or type(generation) is not int or consumer["generation"] != generation
                        or self._active_generations.get(current.consumer_id) != generation):
                    raise ConfigurationError("consumer_generation_conflict", "application report belongs to another runtime generation")
                if type(succeeded) is not bool:
                    raise ConfigurationError("invalid_application_report", "consumer outcome must be explicit")
                if succeeded and current.apply_mode is ApplyMode.RESTART and current.change_sequence > consumer["started_sequence"]:
                    raise ConfigurationError("configuration_restart_required", "consumer must restart before adopting this group")
                previous = self._status(current)
                adopted = current.values if succeeded else dict(previous.adopted_values)
                state = "applied" if succeeded else "failed"
                connection.execute(
                    """INSERT INTO configuration_applications VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(target_key, group_id, consumer_id) DO UPDATE SET
                       generation = excluded.generation, resolution_token = excluded.resolution_token,
                       state = excluded.state, adopted_values_json = excluded.adopted_values_json, updated_at = excluded.updated_at""",
                    (current.target.key, current.group_id, current.consumer_id, generation,
                     current.resolution_token, state, encode(adopted), encode_timestamp(observed_at)),
                )
                return Outcome.success(self._status(current))
        except ConfigurationError as error:
            return Outcome.failed(error.code, str(error))

    def _status(self, snapshot: ConfigurationSnapshot) -> ApplicationStatus:
        consumer = self.store.read_one("SELECT generation FROM configuration_consumers WHERE consumer_id = ?", (snapshot.consumer_id,))
        if consumer is None or self._active_generations.get(snapshot.consumer_id) != consumer["generation"]:
            return ApplicationStatus("consumer_not_started", snapshot.consumer_id, None, {})
        row = self.store.read_one(
            "SELECT * FROM configuration_applications WHERE target_key = ? AND group_id = ? AND consumer_id = ?",
            (snapshot.target.key, snapshot.group_id, snapshot.consumer_id),
        )
        current_generation = row is not None and row["generation"] == consumer["generation"]
        values = json.loads(row["adopted_values_json"]) if current_generation else {}
        if current_generation and row["resolution_token"] == snapshot.resolution_token:
            state = row["state"]
        else:
            state = {ApplyMode.IMMEDIATE: "pending", ApplyMode.NEXT_TASK: "next_task", ApplyMode.RESTART: "restart_required"}[snapshot.apply_mode]
        return ApplicationStatus(state, snapshot.consumer_id, consumer["generation"], values)

    def application_status(
        self, actor: ResolvedIdentity, *, target: ConfigTarget, group_id: str, observed_at: datetime,
    ) -> Outcome[ApplicationStatus]:
        try:
            with self.store.read_snapshot():
                return Outcome.success(self._status(self._snapshot(actor, target, group_id, observed_at)))
        except ConfigurationError as error:
            return Outcome.failed(error.code, str(error))
