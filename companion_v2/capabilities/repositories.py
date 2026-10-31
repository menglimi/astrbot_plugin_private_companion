"""Transactional capability lifecycle and one-source furniture projection."""

from __future__ import annotations

from datetime import datetime
import json
import math
import sqlite3

from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import (
    ReferenceValidationError,
    ScopeRef,
    SubjectKind,
    SubjectRef,
)
from companion_v2.storage.codec import decode_timestamp, encode_timestamp, scope_from_row, scope_values
from companion_v2.storage.runtime import StorageRuntime

from .models import (
    CapabilityAuthorization,
    CapabilityAuthorizationState,
    CapabilityDesiredState,
    CapabilityInstallation,
    CapabilityPackageDescriptor,
    CapabilityProjection,
    CapabilityRoleSelection,
    CapabilitySettings,
    FurnitureAvailability,
    FurnitureDescriptor,
    FurnitureProjection,
)


def _opaque(value: str, *, field: str) -> None:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ReferenceValidationError(f"{field} must be a non-blank opaque string")


def _expected_revision(value: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ReferenceValidationError("expected_revision must be a non-negative integer")


class CapabilityRepository:
    """Internal control-plane persistence, not a user-facing management API.

    A future host installer must authenticate the administrator and attest actual
    package effects before using these primitives. No method loads or downloads code.
    """

    def __init__(self, runtime: StorageRuntime) -> None:
        self._runtime = runtime

    def register_descriptor(
        self,
        descriptor: CapabilityPackageDescriptor,
        *,
        registered_at: datetime,
    ) -> Outcome[CapabilityPackageDescriptor]:
        with self._runtime.transaction() as connection:
            marker = connection.execute("SELECT value FROM runtime_metadata WHERE key = ?",
                ("capability.native_owner:" + descriptor.extension_id,)).fetchone()
            migrated = json.loads(marker["value"]) if marker is not None else {}
            peers = connection.execute(
                "SELECT * FROM capability_descriptors WHERE extension_id = ? OR package_id = ?",
                (descriptor.extension_id, descriptor.package_id),
            ).fetchall()
            for row in peers:
                peer = _descriptor_from_row(row)
                if (migrated.get("package_id") == descriptor.package_id
                        and migrated.get("previous_package_id") == peer.package_id
                        and peer.extension_id == descriptor.extension_id):
                    continue
                if (
                    peer.extension_id != descriptor.extension_id
                    or peer.package_id != descriptor.package_id
                    or peer.publisher_id != descriptor.publisher_id
                    or set(peer.dataset_owners) != set(descriptor.dataset_owners)
                    or {item.furniture_id for item in peer.furniture}
                    != {item.furniture_id for item in descriptor.furniture}
                ):
                    return Outcome.failed(
                        "capability_identity_conflict",
                        "package, publisher, dataset or furniture identity needs an explicit migration",
                    )
            existing = connection.execute(
                """
                SELECT * FROM capability_descriptors
                WHERE extension_id = ? AND package_id = ? AND package_version = ?
                """,
                (descriptor.extension_id, descriptor.package_id, descriptor.package_version),
            ).fetchone()
            if existing is not None:
                restored = _descriptor_from_row(existing)
                if restored == descriptor:
                    return Outcome.success(restored)
                return Outcome.failed(
                    "capability_descriptor_conflict",
                    "the package identity is already bound to a different descriptor",
                )
            connection.execute(
                """
                INSERT INTO capability_descriptors(
                    extension_id, package_id, package_version, publisher_id,
                    artifact_hash, descriptor_json, registered_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    descriptor.extension_id,
                    descriptor.package_id,
                    descriptor.package_version,
                    descriptor.publisher_id,
                    descriptor.artifact_hash,
                    _descriptor_json(descriptor),
                    encode_timestamp(registered_at),
                ),
            )
        return Outcome.success(descriptor)

    def descriptor(
        self,
        *,
        extension_id: str,
        package_id: str,
        package_version: str,
    ) -> Outcome[CapabilityPackageDescriptor]:
        row = self._runtime.read_one(
            """
            SELECT * FROM capability_descriptors
            WHERE extension_id = ? AND package_id = ? AND package_version = ?
            """,
            (extension_id, package_id, package_version),
        )
        if row is None:
            return Outcome.failed("capability_package_not_found", "the fixed package is not in the trusted catalog")
        return Outcome.success(_descriptor_from_row(row))

    def descriptors(self, *, extension_id: str) -> tuple[CapabilityPackageDescriptor, ...]:
        rows = self._runtime.read_all(
            """
            SELECT * FROM capability_descriptors
            WHERE extension_id = ?
            ORDER BY package_id, package_version, artifact_hash
            """,
            (extension_id,),
        )
        return tuple(_descriptor_from_row(row) for row in rows)

    def acquire(
        self,
        *,
        extension_id: str,
        package_id: str,
        package_version: str,
        expected_revision: int,
        observed_at: datetime,
    ) -> Outcome[CapabilityInstallation]:
        _opaque(extension_id, field="extension_id")
        _expected_revision(expected_revision)
        descriptor = self.descriptor(
            extension_id=extension_id,
            package_id=package_id,
            package_version=package_version,
        )
        if not descriptor.is_success:
            return Outcome(failure=descriptor.failure)
        selected = descriptor.unwrap()
        with self._runtime.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM capability_installations WHERE extension_id = ?",
                (extension_id,),
            ).fetchone()
            if row is None:
                if expected_revision != 0:
                    return Outcome.failed("capability_revision_conflict", "installation does not exist")
                timestamp = encode_timestamp(observed_at)
                connection.execute(
                    """
                    INSERT INTO capability_installations(
                        extension_id, package_id, package_version, artifact_hash,
                        desired_state, revision, retained_dataset_owners_json,
                        acquired_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        extension_id,
                        selected.package_id,
                        selected.package_version,
                        selected.artifact_hash,
                        CapabilityDesiredState.ACTIVE.value,
                        1,
                        json.dumps(selected.dataset_owners, ensure_ascii=False),
                        timestamp,
                        timestamp,
                    ),
                )
            else:
                current = _installation_from_row(row)
                if current.revision != expected_revision:
                    return Outcome.failed("capability_revision_conflict", "installation changed before acquisition")
                if current.desired_state is CapabilityDesiredState.DISCARD_REQUESTED:
                    return Outcome.failed(
                        "capability_removal_pending",
                        "an explicit discard is pending and cannot be silently reactivated",
                    )
                same_package = (
                    current.package_id == selected.package_id
                    and current.package_version == selected.package_version
                    and current.artifact_hash == selected.artifact_hash
                )
                if same_package and current.desired_state in (
                    CapabilityDesiredState.ACTIVE, CapabilityDesiredState.PAUSED,
                ):
                    return Outcome.success(current)
                if (
                    not same_package
                    and current.desired_state is not CapabilityDesiredState.DISCARDED
                ):
                    return Outcome.failed(
                        "capability_update_requires_explicit_plan",
                        "changing the fixed package requires an explicit update",
                    )
                owners = _merge_owners(current.retained_dataset_owners, selected.dataset_owners)
                next_revision = current.revision + 1
                connection.execute(
                    """
                    UPDATE capability_installations
                    SET package_id = ?, package_version = ?, artifact_hash = ?,
                        desired_state = ?, revision = ?, retained_dataset_owners_json = ?,
                        updated_at = ?
                    WHERE extension_id = ? AND revision = ?
                    """,
                    (
                        selected.package_id,
                        selected.package_version,
                        selected.artifact_hash,
                        CapabilityDesiredState.ACTIVE.value,
                        next_revision,
                        json.dumps(owners, ensure_ascii=False),
                        encode_timestamp(observed_at),
                        extension_id,
                        current.revision,
                    ),
                )
            updated = connection.execute(
                "SELECT * FROM capability_installations WHERE extension_id = ?",
                (extension_id,),
            ).fetchone()
        assert updated is not None
        return Outcome.success(_installation_from_row(updated))

    def update(
        self,
        *,
        extension_id: str,
        package_id: str,
        package_version: str,
        expected_revision: int,
        observed_at: datetime,
    ) -> Outcome[CapabilityInstallation]:
        _expected_revision(expected_revision)
        descriptor = self.descriptor(
            extension_id=extension_id,
            package_id=package_id,
            package_version=package_version,
        )
        if not descriptor.is_success:
            return Outcome(failure=descriptor.failure)
        selected = descriptor.unwrap()
        with self._runtime.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM capability_installations WHERE extension_id = ?",
                (extension_id,),
            ).fetchone()
            if row is None:
                return Outcome.failed("capability_not_installed", "the extension has not been acquired")
            current = _installation_from_row(row)
            if current.revision != expected_revision:
                return Outcome.failed("capability_revision_conflict", "installation changed before this update")
            if current.desired_state in (
                CapabilityDesiredState.DISCARD_REQUESTED,
                CapabilityDesiredState.DISCARDED,
            ):
                return Outcome.failed("capability_not_admissible", "discarded capabilities cannot be updated")
            owners = _merge_owners(current.retained_dataset_owners, selected.dataset_owners)
            if (
                current.package_id == selected.package_id
                and current.package_version == selected.package_version
                and current.artifact_hash == selected.artifact_hash
            ):
                return Outcome.success(current)
            next_revision = current.revision + 1
            connection.execute(
                """
                UPDATE capability_installations
                SET package_id = ?, package_version = ?, artifact_hash = ?,
                    revision = ?, retained_dataset_owners_json = ?, updated_at = ?
                WHERE extension_id = ? AND revision = ?
                """,
                (
                    selected.package_id,
                    selected.package_version,
                    selected.artifact_hash,
                    next_revision,
                    json.dumps(owners, ensure_ascii=False),
                    encode_timestamp(observed_at),
                    extension_id,
                    expected_revision,
                ),
            )
            updated = connection.execute(
                "SELECT * FROM capability_installations WHERE extension_id = ?",
                (extension_id,),
            ).fetchone()
        assert updated is not None
        return Outcome.success(_installation_from_row(updated))

    def installation(self, *, extension_id: str) -> Outcome[CapabilityInstallation]:
        row = self._runtime.read_one(
            "SELECT * FROM capability_installations WHERE extension_id = ?",
            (extension_id,),
        )
        if row is None:
            return Outcome.failed("capability_not_installed", "the extension has not been acquired")
        return Outcome.success(_installation_from_row(row))

    def pause(
        self,
        *,
        extension_id: str,
        expected_revision: int,
        observed_at: datetime,
    ) -> Outcome[CapabilityInstallation]:
        return self._transition(
            extension_id=extension_id,
            expected_revision=expected_revision,
            observed_at=observed_at,
            expected=(CapabilityDesiredState.ACTIVE,),
            next_state=CapabilityDesiredState.PAUSED,
            conflict_code="capability_not_active",
        )

    def resume(
        self,
        *,
        extension_id: str,
        expected_revision: int,
        observed_at: datetime,
    ) -> Outcome[CapabilityInstallation]:
        return self._transition(
            extension_id=extension_id,
            expected_revision=expected_revision,
            observed_at=observed_at,
            expected=(CapabilityDesiredState.PAUSED,),
            next_state=CapabilityDesiredState.ACTIVE,
            conflict_code="capability_not_paused",
        )

    def request_discard(
        self,
        *,
        extension_id: str,
        expected_revision: int,
        observed_at: datetime,
    ) -> Outcome[CapabilityInstallation]:
        return self._transition(
            extension_id=extension_id,
            expected_revision=expected_revision,
            observed_at=observed_at,
            expected=(CapabilityDesiredState.ACTIVE, CapabilityDesiredState.PAUSED),
            next_state=CapabilityDesiredState.DISCARD_REQUESTED,
            conflict_code="capability_not_discardable",
        )

    def complete_discard(
        self,
        *,
        extension_id: str,
        expected_revision: int,
        observed_at: datetime,
    ) -> Outcome[CapabilityInstallation]:
        return self._transition(
            extension_id=extension_id,
            expected_revision=expected_revision,
            observed_at=observed_at,
            expected=(CapabilityDesiredState.DISCARD_REQUESTED,),
            next_state=CapabilityDesiredState.DISCARDED,
            conflict_code="capability_discard_not_pending",
        )

    def set_role_enabled(
        self,
        *,
        extension_id: str,
        role: SubjectRef,
        enabled: bool,
        expected_revision: int,
        observed_at: datetime,
        bot_id: str | None = None,
    ) -> Outcome[CapabilityRoleSelection]:
        if role.kind is not SubjectKind.BOT:
            raise ReferenceValidationError("role must be a bot subject")
        if role.installation_id != self._runtime.installation_id:
            raise ReferenceValidationError("role belongs to another installation")
        if not isinstance(enabled, bool):
            raise ReferenceValidationError("enabled must be boolean")
        if bot_id is not None:
            _opaque(bot_id, field="bot_id")
        _expected_revision(expected_revision)
        with self._runtime.transaction() as connection:
            installed = self._configurable_installation(extension_id)
            if not installed.is_success:
                return Outcome(failure=installed.failure)
            row = connection.execute(
                """
                SELECT * FROM capability_role_selections
                WHERE extension_id = ? AND role_kind = ? AND role_id = ? AND bot_id = ?
                """,
                (extension_id, role.kind.value, role.subject_id, bot_id or ""),
            ).fetchone()
            if row is None:
                if expected_revision != 0:
                    return Outcome.failed("capability_role_revision_conflict", "role selection does not exist")
                revision = 1
                connection.execute(
                    """
                    INSERT INTO capability_role_selections(
                        extension_id, role_kind, role_id, bot_id, enabled, revision, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        extension_id,
                        role.kind.value,
                        role.subject_id,
                        bot_id or "",
                        int(enabled),
                        revision,
                        encode_timestamp(observed_at),
                    ),
                )
            else:
                current_revision = int(row["revision"])
                if current_revision != expected_revision:
                    return Outcome.failed("capability_role_revision_conflict", "role selection changed")
                revision = current_revision + 1
                connection.execute(
                    """
                    UPDATE capability_role_selections
                    SET enabled = ?, revision = ?, updated_at = ?
                    WHERE extension_id = ? AND role_kind = ? AND role_id = ? AND bot_id = ? AND revision = ?
                    """,
                    (
                        int(enabled),
                        revision,
                        encode_timestamp(observed_at),
                        extension_id,
                        role.kind.value,
                        role.subject_id,
                        bot_id or "",
                        expected_revision,
                    ),
                )
            updated = connection.execute(
                """
                SELECT * FROM capability_role_selections
                WHERE extension_id = ? AND role_kind = ? AND role_id = ? AND bot_id = ?
                """,
                (extension_id, role.kind.value, role.subject_id, bot_id or ""),
            ).fetchone()
        assert updated is not None
        return Outcome.success(_role_from_row(updated, installation_id=self._runtime.installation_id))

    def set_authorization(
        self,
        *,
        extension_id: str,
        capability_id: str,
        subject: SubjectRef,
        scope: ScopeRef,
        state: CapabilityAuthorizationState,
        expected_revision: int,
        observed_at: datetime,
    ) -> Outcome[CapabilityAuthorization]:
        if subject.kind is not SubjectKind.HUMAN:
            raise ReferenceValidationError("capability authorization requires a human subject")
        if subject.installation_id != self._runtime.installation_id:
            raise ReferenceValidationError("authorization subject belongs to another installation")
        if scope.installation_id != self._runtime.installation_id:
            raise ReferenceValidationError("authorization scope belongs to another installation")
        if subject.installation_id != scope.installation_id:
            raise ReferenceValidationError("authorization subject and scope must share installation")
        _opaque(capability_id, field="capability_id")
        _expected_revision(expected_revision)
        if not isinstance(state, CapabilityAuthorizationState):
            raise ReferenceValidationError("state must be a CapabilityAuthorizationState")
        values = (
            extension_id,
            capability_id,
            subject.kind.value,
            subject.subject_id,
            *scope_values(scope),
        )
        with self._runtime.transaction() as connection:
            installed = self.installation(extension_id=extension_id)
            if not installed.is_success:
                return Outcome(failure=installed.failure)
            if state is CapabilityAuthorizationState.ACTIVE:
                configurable = self._configurable_installation(extension_id)
                if not configurable.is_success:
                    return Outcome(failure=configurable.failure)
                descriptor = self._current_descriptor(extension_id)
                if descriptor is None or capability_id not in descriptor.capabilities:
                    return Outcome.failed("capability_not_provided", "the package does not provide this capability")
            row = connection.execute(
                """
                SELECT * FROM capability_authorizations
                WHERE extension_id = ? AND capability_id = ? AND subject_kind = ? AND subject_id = ?
                  AND installation_id = ? AND platform = ? AND scope_kind = ? AND native_scope_id = ? AND bot_id = ? AND account_id = ? AND conversation_id = ? AND session_id = ?
                """,
                values,
            ).fetchone()
            if row is None:
                if expected_revision != 0:
                    return Outcome.failed("capability_authorization_revision_conflict", "authorization does not exist")
                revision = 1
                connection.execute(
                    """
                    INSERT INTO capability_authorizations(
                        extension_id, capability_id, subject_kind, subject_id,
                        installation_id, platform, scope_kind, native_scope_id, bot_id, account_id, conversation_id, session_id,
                        state, revision, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (*values, state.value, revision, encode_timestamp(observed_at)),
                )
            else:
                current_revision = int(row["revision"])
                if current_revision != expected_revision:
                    return Outcome.failed("capability_authorization_revision_conflict", "authorization changed")
                revision = current_revision + 1
                connection.execute(
                    """
                    UPDATE capability_authorizations
                    SET state = ?, revision = ?, updated_at = ?
                    WHERE extension_id = ? AND capability_id = ? AND subject_kind = ? AND subject_id = ?
                      AND installation_id = ? AND platform = ? AND scope_kind = ? AND native_scope_id = ? AND bot_id = ? AND account_id = ? AND conversation_id = ? AND session_id = ?
                      AND revision = ?
                    """,
                    (state.value, revision, encode_timestamp(observed_at), *values, expected_revision),
                )
            updated = connection.execute(
                """
                SELECT * FROM capability_authorizations
                WHERE extension_id = ? AND capability_id = ? AND subject_kind = ? AND subject_id = ?
                  AND installation_id = ? AND platform = ? AND scope_kind = ? AND native_scope_id = ? AND bot_id = ? AND account_id = ? AND conversation_id = ? AND session_id = ?
                """,
                values,
            ).fetchone()
        assert updated is not None
        return Outcome.success(_authorization_from_row(updated))

    def authorize(
        self,
        *,
        extension_id: str,
        capability_id: str,
        subject: SubjectRef,
        scope: ScopeRef,
    ) -> Outcome[CapabilityAuthorization]:
        with self._runtime.read_snapshot():
            installed = self._admissible_installation(extension_id)
            if not installed.is_success:
                return Outcome(failure=installed.failure)
            descriptor = self._current_descriptor(extension_id)
            if descriptor is None or capability_id not in descriptor.capabilities:
                return Outcome.failed("capability_not_provided", "the active package does not provide this capability")
            return self.consent(
                extension_id=extension_id, capability_id=capability_id, subject=subject, scope=scope,
            )

    def consent(
        self,
        *,
        extension_id: str,
        capability_id: str,
        subject: SubjectRef,
        scope: ScopeRef,
    ) -> Outcome[CapabilityAuthorization]:
        """Read retained consent without enabling an absent or paused provider."""

        if subject.installation_id != self._runtime.installation_id or scope.installation_id != self._runtime.installation_id:
            return Outcome.failed("capability_scope_mismatch", "capability access crosses installation")
        row = self._runtime.read_one(
            """
            SELECT * FROM capability_authorizations
            WHERE extension_id = ? AND capability_id = ? AND subject_kind = ? AND subject_id = ?
              AND installation_id = ? AND platform = ? AND scope_kind = ? AND native_scope_id = ? AND bot_id = ? AND account_id = ? AND conversation_id = ? AND session_id = ?
            """,
            (
                extension_id,
                capability_id,
                subject.kind.value,
                subject.subject_id,
                *scope_values(scope),
            ),
        )
        if row is None:
            return Outcome.failed("capability_consent_missing", "the extension has no consent in this exact scope")
        authorization = _authorization_from_row(row)
        if authorization.state is CapabilityAuthorizationState.REVOKED:
            return Outcome.failed("capability_consent_revoked", "extension consent was revoked")
        return Outcome.success(authorization)

    def latest_route_authorization(
        self,
        *,
        extension_id: str,
        capability_id: str,
        subject: SubjectRef,
        scope: ScopeRef,
    ) -> CapabilityAuthorization | None:
        """Read the latest retained choice on this route, including revocation.

        This is only a default-provisioning input, never permission to invoke in
        another conversation. Normal authorization still requires an exact scope.
        """
        if subject.installation_id != self._runtime.installation_id or scope.installation_id != self._runtime.installation_id:
            raise ReferenceValidationError("capability choice crosses installation")
        row = self._runtime.read_one(
            """
            SELECT * FROM capability_authorizations
            WHERE extension_id = ? AND capability_id = ? AND subject_kind = ? AND subject_id = ?
              AND installation_id = ? AND platform = ? AND scope_kind = ? AND native_scope_id = ?
              AND bot_id = ? AND account_id = ? AND session_id = ?
            ORDER BY updated_at DESC, revision DESC, state DESC
            LIMIT 1
            """,
            (extension_id, capability_id, subject.kind.value, subject.subject_id,
             *scope_values(scope)[:6], scope.session_id or ""),
        )
        return _authorization_from_row(row) if row is not None else None

    def set_settings(
        self,
        *,
        extension_id: str,
        scope: ScopeRef,
        proactive_enabled: bool,
        thought_weight: float,
        expected_revision: int,
        observed_at: datetime,
    ) -> Outcome[CapabilitySettings]:
        if scope.installation_id != self._runtime.installation_id:
            raise ReferenceValidationError("settings scope belongs to another installation")
        if not isinstance(proactive_enabled, bool):
            raise ReferenceValidationError("proactive_enabled must be boolean")
        if isinstance(thought_weight, bool) or not math.isfinite(thought_weight) or thought_weight <= 0:
            raise ReferenceValidationError("thought_weight must be a positive finite number")
        _expected_revision(expected_revision)
        values = (extension_id, *scope_values(scope))
        with self._runtime.transaction() as connection:
            installed = self._configurable_installation(extension_id)
            if not installed.is_success:
                return Outcome(failure=installed.failure)
            row = connection.execute(
                """
                SELECT * FROM capability_settings
                WHERE extension_id = ? AND installation_id = ? AND platform = ?
                  AND scope_kind = ? AND native_scope_id = ? AND bot_id = ? AND account_id = ? AND conversation_id = ? AND session_id = ?
                """,
                values,
            ).fetchone()
            if row is None:
                if expected_revision != 0:
                    return Outcome.failed("capability_settings_revision_conflict", "settings do not exist")
                revision = 1
                connection.execute(
                    """
                    INSERT INTO capability_settings(
                        extension_id, installation_id, platform, scope_kind, native_scope_id, bot_id, account_id, conversation_id, session_id,
                        proactive_enabled, thought_weight, revision, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (*values, int(proactive_enabled), thought_weight, revision, encode_timestamp(observed_at)),
                )
            else:
                current_revision = int(row["revision"])
                if current_revision != expected_revision:
                    return Outcome.failed("capability_settings_revision_conflict", "settings changed")
                revision = current_revision + 1
                connection.execute(
                    """
                    UPDATE capability_settings
                    SET proactive_enabled = ?, thought_weight = ?, revision = ?, updated_at = ?
                    WHERE extension_id = ? AND installation_id = ? AND platform = ?
                      AND scope_kind = ? AND native_scope_id = ? AND bot_id = ? AND account_id = ? AND conversation_id = ? AND session_id = ? AND revision = ?
                    """,
                    (
                        int(proactive_enabled),
                        thought_weight,
                        revision,
                        encode_timestamp(observed_at),
                        *values,
                        expected_revision,
                    ),
                )
            updated = connection.execute(
                """
                SELECT * FROM capability_settings
                WHERE extension_id = ? AND installation_id = ? AND platform = ?
                  AND scope_kind = ? AND native_scope_id = ? AND bot_id = ? AND account_id = ? AND conversation_id = ? AND session_id = ?
                """,
                values,
            ).fetchone()
        assert updated is not None
        return Outcome.success(_settings_from_row(updated))

    def projection(
        self,
        *,
        extension_id: str,
        role: SubjectRef | None = None,
        scope: ScopeRef | None = None,
    ) -> Outcome[CapabilityProjection]:
        with self._runtime.read_snapshot():
            return self._projection(extension_id=extension_id, role=role, scope=scope)

    def _projection(
        self,
        *,
        extension_id: str,
        role: SubjectRef | None,
        scope: ScopeRef | None,
    ) -> Outcome[CapabilityProjection]:
        installation = self.installation(extension_id=extension_id)
        if not installation.is_success:
            return Outcome(failure=installation.failure)
        current = installation.unwrap()
        descriptor = self._current_descriptor(extension_id)
        if descriptor is None:
            return Outcome.failed("capability_descriptor_missing", "installation points to a missing package descriptor")
        role_selection: CapabilityRoleSelection | None = None
        if role is not None:
            if role.kind is not SubjectKind.BOT:
                raise ReferenceValidationError("role must be a bot subject")
            if role.installation_id != self._runtime.installation_id:
                raise ReferenceValidationError("role belongs to another installation")
            row = self._runtime.read_one(
                """
                SELECT * FROM capability_role_selections
                WHERE extension_id = ? AND role_kind = ? AND role_id = ? AND bot_id = ?
                """,
                (extension_id, role.kind.value, role.subject_id, (scope.bot_id or "") if scope is not None else ""),
            )
            if row is not None:
                role_selection = _role_from_row(row, installation_id=self._runtime.installation_id)
        settings: CapabilitySettings | None = None
        if scope is not None:
            if scope.installation_id != self._runtime.installation_id:
                raise ReferenceValidationError("settings scope belongs to another installation")
            row = self._runtime.read_one(
                """
                SELECT * FROM capability_settings
                WHERE extension_id = ? AND installation_id = ? AND platform = ?
                  AND scope_kind = ? AND native_scope_id = ? AND bot_id = ? AND account_id = ? AND conversation_id = ? AND session_id = ?
                """,
                (extension_id, *scope_values(scope)),
            )
            if row is not None:
                settings = _settings_from_row(row)
        if current.desired_state is CapabilityDesiredState.DISCARDED:
            furniture: tuple[FurnitureProjection, ...] = ()
        else:
            availability = {
                CapabilityDesiredState.ACTIVE: FurnitureAvailability.ACQUIRED,
                CapabilityDesiredState.PAUSED: FurnitureAvailability.PAUSED,
                CapabilityDesiredState.DISCARD_REQUESTED: FurnitureAvailability.REMOVING,
            }[current.desired_state]
            furniture = tuple(
                FurnitureProjection(item, availability, current.revision)
                for item in descriptor.furniture
            )
        return Outcome.success(
            CapabilityProjection(
                installation=current,
                furniture=furniture,
                role_selection=role_selection,
                settings=settings,
            )
        )

    def projections(self) -> Outcome[tuple[CapabilityProjection, ...]]:
        with self._runtime.read_snapshot():
            rows = self._runtime.read_all(
                "SELECT extension_id FROM capability_installations ORDER BY extension_id", (),
            )
            result: list[CapabilityProjection] = []
            for row in rows:
                projection = self.projection(extension_id=row["extension_id"])
                if not projection.is_success:
                    return Outcome(failure=projection.failure)
                result.append(projection.unwrap())
            return Outcome.success(tuple(result))

    def role_selection(self, *, extension_id: str, role: SubjectRef, bot_id: str | None = None) -> Outcome[CapabilityRoleSelection]:
        """Read one role selection without requiring an installed package."""

        if role.kind is not SubjectKind.BOT:
            raise ReferenceValidationError("role must be a bot subject")
        if role.installation_id != self._runtime.installation_id:
            raise ReferenceValidationError("role belongs to another installation")
        if bot_id is not None:
            _opaque(bot_id, field="bot_id")
        row = self._runtime.read_one(
            """
            SELECT * FROM capability_role_selections
            WHERE extension_id = ? AND role_kind = ? AND role_id = ? AND bot_id = ?
            """,
            (extension_id, role.kind.value, role.subject_id, bot_id or ""),
        )
        if row is None:
            return Outcome.failed("capability_role_not_enabled", "the capability has no selection for this role")
        return Outcome.success(_role_from_row(row, installation_id=self._runtime.installation_id))

    def require_role_enabled(self, *, extension_id: str, role: SubjectRef, bot_id: str | None = None) -> Outcome[CapabilityRoleSelection]:
        with self._runtime.read_snapshot():
            return self._require_role_enabled(extension_id=extension_id, role=role, bot_id=bot_id)

    def _require_role_enabled(self, *, extension_id: str, role: SubjectRef, bot_id: str | None) -> Outcome[CapabilityRoleSelection]:
        if role.kind is not SubjectKind.BOT:
            raise ReferenceValidationError("role must be a bot subject")
        if role.installation_id != self._runtime.installation_id:
            raise ReferenceValidationError("role belongs to another installation")
        admissible = self._admissible_installation(extension_id)
        if not admissible.is_success:
            return Outcome(failure=admissible.failure)
        row = self._runtime.read_one(
            """
            SELECT * FROM capability_role_selections
            WHERE extension_id = ? AND role_kind = ? AND role_id = ? AND bot_id = ?
            """,
            (extension_id, role.kind.value, role.subject_id, bot_id or ""),
        )
        if row is None or not bool(row["enabled"]):
            return Outcome.failed("capability_role_not_enabled", "the capability is not enabled for this role")
        return Outcome.success(_role_from_row(row, installation_id=self._runtime.installation_id))

    def _current_descriptor(self, extension_id: str) -> CapabilityPackageDescriptor | None:
        row = self._runtime.read_one(
            """
            SELECT d.* FROM capability_descriptors d
            JOIN capability_installations i
              ON i.extension_id = d.extension_id
             AND i.package_id = d.package_id
             AND i.package_version = d.package_version
            WHERE i.extension_id = ?
            """,
            (extension_id,),
        )
        return None if row is None else _descriptor_from_row(row)

    def _admissible_installation(self, extension_id: str) -> Outcome[CapabilityInstallation]:
        installation = self.installation(extension_id=extension_id)
        if not installation.is_success:
            return Outcome(failure=installation.failure)
        current = installation.unwrap()
        if current.desired_state is not CapabilityDesiredState.ACTIVE:
            return Outcome.failed("capability_not_admissible", "the capability is not active for new configuration")
        return Outcome.success(current)

    def _configurable_installation(self, extension_id: str) -> Outcome[CapabilityInstallation]:
        installation = self.installation(extension_id=extension_id)
        if not installation.is_success:
            return Outcome(failure=installation.failure)
        if installation.unwrap().desired_state not in (
            CapabilityDesiredState.ACTIVE, CapabilityDesiredState.PAUSED,
        ):
            return Outcome.failed("capability_not_admissible", "discard is pending or complete")
        return installation

    def _transition(
        self,
        *,
        extension_id: str,
        expected_revision: int,
        observed_at: datetime,
        expected: tuple[CapabilityDesiredState, ...],
        next_state: CapabilityDesiredState,
        conflict_code: str,
    ) -> Outcome[CapabilityInstallation]:
        _expected_revision(expected_revision)
        with self._runtime.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM capability_installations WHERE extension_id = ?",
                (extension_id,),
            ).fetchone()
            if row is None:
                return Outcome.failed("capability_not_installed", "the extension has not been acquired")
            current = _installation_from_row(row)
            if current.revision != expected_revision:
                return Outcome.failed("capability_revision_conflict", "installation changed before this lifecycle action")
            if current.desired_state not in expected:
                return Outcome.failed(conflict_code, "the lifecycle action is not valid for the current state")
            next_revision = current.revision + 1
            connection.execute(
                """
                UPDATE capability_installations
                SET desired_state = ?, revision = ?, updated_at = ?
                WHERE extension_id = ? AND revision = ?
                """,
                (
                    next_state.value,
                    next_revision,
                    encode_timestamp(observed_at),
                    extension_id,
                    expected_revision,
                ),
            )
            updated = connection.execute(
                "SELECT * FROM capability_installations WHERE extension_id = ?",
                (extension_id,),
            ).fetchone()
        assert updated is not None
        return Outcome.success(_installation_from_row(updated))


def _descriptor_json(descriptor: CapabilityPackageDescriptor) -> str:
    return json.dumps(
        {
            "capabilities": descriptor.capabilities,
            "dataset_owners": descriptor.dataset_owners,
            "furniture": [
                {
                    "furniture_id": item.furniture_id,
                    "extension_id": item.extension_id,
                    "display_name": item.display_name,
                    "panel_id": item.panel_id,
                    "styles": item.styles,
                    "default_style": item.default_style,
                }
                for item in descriptor.furniture
            ],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _descriptor_from_row(row: sqlite3.Row) -> CapabilityPackageDescriptor:
    payload = json.loads(row["descriptor_json"])
    furniture = tuple(
        FurnitureDescriptor(
            furniture_id=item["furniture_id"],
            extension_id=item["extension_id"],
            display_name=item["display_name"],
            panel_id=item["panel_id"],
            styles=tuple(item["styles"]),
            default_style=item["default_style"],
        )
        for item in payload["furniture"]
    )
    descriptor = CapabilityPackageDescriptor(
        extension_id=row["extension_id"],
        package_id=row["package_id"],
        publisher_id=row["publisher_id"],
        package_version=row["package_version"],
        artifact_hash=row["artifact_hash"],
        capabilities=tuple(payload["capabilities"]),
        dataset_owners=tuple(payload["dataset_owners"]),
        furniture=furniture,
    )
    if _descriptor_json(descriptor) != row["descriptor_json"]:
        raise ReferenceValidationError("capability descriptor record is not canonical")
    return descriptor


def _installation_from_row(row: sqlite3.Row) -> CapabilityInstallation:
    return CapabilityInstallation(
        extension_id=row["extension_id"],
        package_id=row["package_id"],
        package_version=row["package_version"],
        artifact_hash=row["artifact_hash"],
        desired_state=CapabilityDesiredState(row["desired_state"]),
        revision=int(row["revision"]),
        retained_dataset_owners=tuple(json.loads(row["retained_dataset_owners_json"])),
        acquired_at=decode_timestamp(row["acquired_at"]),
        updated_at=decode_timestamp(row["updated_at"]),
    )


def _role_from_row(row: sqlite3.Row, *, installation_id: str) -> CapabilityRoleSelection:
    return CapabilityRoleSelection(
        extension_id=row["extension_id"],
        role=SubjectRef(
            installation_id=installation_id,
            kind=SubjectKind(row["role_kind"]),
            subject_id=row["role_id"],
        ),
        enabled=bool(row["enabled"]),
        revision=int(row["revision"]),
        updated_at=decode_timestamp(row["updated_at"]),
        bot_id=row["bot_id"] or None,
    )


def _authorization_from_row(row: sqlite3.Row) -> CapabilityAuthorization:
    return CapabilityAuthorization(
        extension_id=row["extension_id"],
        capability_id=row["capability_id"],
        subject=SubjectRef(
            installation_id=row["installation_id"],
            kind=SubjectKind(row["subject_kind"]),
            subject_id=row["subject_id"],
        ),
        scope=scope_from_row(row),
        state=CapabilityAuthorizationState(row["state"]),
        revision=int(row["revision"]),
        updated_at=decode_timestamp(row["updated_at"]),
    )


def _settings_from_row(row: sqlite3.Row) -> CapabilitySettings:
    return CapabilitySettings(
        extension_id=row["extension_id"],
        scope=scope_from_row(row),
        proactive_enabled=bool(row["proactive_enabled"]),
        thought_weight=float(row["thought_weight"]),
        revision=int(row["revision"]),
        updated_at=decode_timestamp(row["updated_at"]),
    )


def _merge_owners(first: tuple[str, ...], second: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys((*first, *second)))
