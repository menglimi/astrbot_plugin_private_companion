"""Current-governance reads for V2 source, evidence, and asset records."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import json
import sqlite3

from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import (
    AssetRef,
    AudienceRef,
    ReferenceValidationError,
    ScopeRef,
    SourceRef,
    SubjectKind,
    SubjectRef,
    VersionRef,
    normalize_utc,
)
from companion_v2.execution.models import digest
from companion_v2.identity_policy.models import ResolvedIdentity
from companion_v2.identity_policy.ports import IdentityAuthorizationPort
from companion_v2.storage.codec import (SCOPE_COLUMNS, decode_timestamp, encode_timestamp,
                                        scope_from_row, scope_values)
from companion_v2.storage.repositories import NamespaceRepository
from companion_v2.storage.runtime import StorageRuntime

from .models import AssetRecord, EvidenceRecord, GovernanceState, SourceRecord


class SourceRepository:
    def __init__(self, runtime: StorageRuntime) -> None:
        self._runtime = runtime
        self._namespaces = NamespaceRepository(runtime)

    def validate_reference(self, reference: SourceRef, *, observed_at: datetime) -> Outcome[SourceRef]:
        """Trusted control-plane evidence check; returns no content or read authority."""
        observed_at = normalize_utc(observed_at, field="observed_at")
        _ensure_runtime_scope(self._runtime, reference.scope)
        row = self._runtime.read_one(
            """SELECT source_id, lineage_id, source_revision, owner, installation_id, platform, scope_kind,
                      native_scope_id, bot_id, account_id, conversation_id, session_id, issued_at, expires_at, state
               FROM sources WHERE source_id = ? AND lineage_id = ? AND source_revision = ?""", _source_key(reference),
        )
        if row is None:
            return Outcome.failed("source_not_found", "exact evidence version does not exist")
        stored = SourceRef(row["source_id"], row["owner"], scope_from_row(row),
                           VersionRef(row["lineage_id"], row["source_revision"]), decode_timestamp(row["issued_at"]),
                           decode_timestamp(row["expires_at"]) if row["expires_at"] else None)
        if stored != reference:
            return Outcome.failed("source_reference_mismatch", "evidence identity does not match its original")
        if row["state"] != GovernanceState.ACTIVE.value:
            return Outcome.failed("source_revoked", "evidence was revoked")
        if stored.is_expired_at(observed_at):
            return Outcome.failed("source_expired", "evidence expired")
        if observed_at < stored.issued_at:
            return Outcome.failed("source_not_active", "evidence is not available at this time")
        from companion_v2.execution.models import TaskError
        from .dependencies import validate
        try:
            validate(self._runtime, reference, observed_at)
        except TaskError as error:
            return Outcome.failed(error.code, str(error))
        return Outcome.success(stored)

    def validate_reference_for_audience(
        self,
        reference: SourceRef,
        *,
        recipient: SubjectRef,
        observed_at: datetime,
        expected_content_hash: str | None = None,
    ) -> Outcome[SourceRef]:
        """Check current source governance and audience without returning its payload.

        This control-plane check is useful when a domain stores an interpretation of
        a source but must not read or project the source body itself. It does not
        grant source read access. When supplied, expected_content_hash must match
        the immutable content identity recorded by the original producer.
        """
        if not isinstance(reference, SourceRef):
            return Outcome.failed("source_reference_invalid", "source reference must be explicit")
        if not isinstance(recipient, SubjectRef) or recipient.installation_id != reference.scope.installation_id:
            return Outcome.failed("source_audience_denied", "recipient does not belong to the source installation")
        with self._runtime.read_snapshot():
            current = self.validate_reference(reference, observed_at=observed_at)
            if not current.is_success:
                return Outcome(failure=current.failure)
            row = self._runtime.read_one(
                """SELECT recipients_json, content_hash FROM sources
                   WHERE source_id = ? AND lineage_id = ? AND source_revision = ?""",
                _source_key(reference),
            )
            if row is None:
                return Outcome.failed("source_not_found", "exact evidence version does not exist")
            audience = _decode_audience(reference.scope, row["recipients_json"])
            if recipient not in audience.recipients:
                return Outcome.failed("source_audience_denied", "source is not visible to the current subject")
            if expected_content_hash is not None and row["content_hash"] != expected_content_hash:
                return Outcome.failed("source_content_changed", "source no longer matches the recorded content hash")
            return current

    def validate_archived_provenance(
        self, reference: SourceRef, *, recipient: SubjectRef, observed_at: datetime,
        expected_content_hash: str,
    ) -> Outcome[SourceRef]:
        """Check immutable attribution metadata for a receipt-backed archive.

        This returns no source payload and grants no current evidence lease.
        Producers must independently verify the sealed archive and its trusted
        delivery receipt. A past source's TTL and live producer settings do not
        rewrite its attribution; current revocation, scope, audience and hash do.
        Normal source reads and sends still require current owner governance.
        """
        if not isinstance(reference, SourceRef) or not isinstance(recipient, SubjectRef):
            return Outcome.failed("source_reference_invalid", "archived provenance needs explicit identities")
        _ensure_runtime_scope(self._runtime, reference.scope)
        normalize_utc(observed_at, field="observed_at")
        with self._runtime.read_snapshot():
            columns = ", ".join((*SCOPE_COLUMNS, "source_id", "owner", "lineage_id", "source_revision",
                                 "issued_at", "expires_at", "state", "recipients_json", "content_hash"))
            row = self._runtime.read_one(f"""SELECT {columns} FROM sources
                WHERE source_id = ? AND lineage_id = ? AND source_revision = ?""", _source_key(reference))
            if row is None:
                return Outcome.failed("source_not_found", "original attribution source is unavailable")
            stored = SourceRef(row["source_id"], row["owner"], scope_from_row(row),
                VersionRef(row["lineage_id"], row["source_revision"]), decode_timestamp(row["issued_at"]),
                decode_timestamp(row["expires_at"]) if row["expires_at"] else None)
            if stored != reference:
                return Outcome.failed("source_reference_mismatch", "archived attribution identity changed")
            if row["state"] != GovernanceState.ACTIVE.value:
                return Outcome.failed("source_revoked", "original attribution was revoked")
            if observed_at < stored.issued_at:
                return Outcome.failed("source_not_active", "original attribution is not yet available")
            if recipient not in _decode_audience(reference.scope, row["recipients_json"]).recipients:
                return Outcome.failed("source_audience_denied", "original attribution belongs to another audience")
            if row["content_hash"] != expected_content_hash:
                return Outcome.failed("source_content_changed", "original attribution content changed")
            return Outcome.success(stored)

    def prepare_message(self, record: SourceRecord) -> SourceRecord:
        """Prepare visible message text before any durable publication."""
        from .privacy import prepare_message
        return prepare_message(self._runtime, record)

    def publish(self, record: SourceRecord, *, observed_at: datetime) -> Outcome[SourceRecord]:
        _ensure_runtime_scope(self._runtime, record.reference.scope)
        self._namespaces.ensure(
            owner=record.reference.owner,
            scope=record.reference.scope,
            created_at=observed_at,
        )
        with self._runtime.transaction() as connection:
            from .governance import mark_publication
            mark_publication(self._runtime, record.reference.owner)
            existing = connection.execute(
                """
                SELECT * FROM sources
                WHERE source_id = ? AND lineage_id = ? AND source_revision = ?
                """,
                _source_key(record.reference),
            ).fetchone()
            if existing is not None:
                restored = _source_from_row(existing)
                if restored == record:
                    return Outcome.success(restored)
                return Outcome.failed(
                    "source_version_conflict",
                    "source version is already bound to different content or governance",
                )
            connection.execute(
                """
                INSERT INTO sources(
                    source_id, lineage_id, source_revision, owner, installation_id, platform,
                    scope_kind, native_scope_id, bot_id, account_id, conversation_id, session_id, issued_at, expires_at, recipients_json,
                    content_hash, payload_json, state, governance_revision, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    *_source_key(record.reference),
                    record.reference.owner,
                    *scope_values(record.reference.scope),
                    encode_timestamp(record.reference.issued_at),
                    encode_timestamp(record.reference.expires_at)
                    if record.reference.expires_at is not None
                    else None,
                    _encode_audience(record.audience),
                    record.content_hash,
                    json.dumps(record.payload, ensure_ascii=False),
                    record.state.value,
                    record.governance_revision,
                    encode_timestamp(observed_at),
                    encode_timestamp(observed_at),
                ),
            )
            from .dependencies import invalidate_batch
            invalidate_batch(self._runtime, source_key=_source_key(record.reference))
        return Outcome.success(record)

    def revoke(
        self,
        reference: SourceRef,
        *,
        expected_governance_revision: int,
        observed_at: datetime,
    ) -> Outcome[SourceRecord]:
        _ensure_runtime_scope(self._runtime, reference.scope)
        if expected_governance_revision < 1:
            raise ReferenceValidationError("expected_governance_revision must be positive")
        with self._runtime.transaction() as connection:
            row = connection.execute(
                """
                SELECT * FROM sources
                WHERE source_id = ? AND lineage_id = ? AND source_revision = ?
                """,
                _source_key(reference),
            ).fetchone()
            if row is None:
                return Outcome.failed("source_not_found", "source version does not exist")
            current = _source_from_row(row)
            if current.reference != reference:
                return Outcome.failed("source_reference_mismatch", "source identity does not match")
            if current.governance_revision != expected_governance_revision:
                return Outcome.failed(
                    "source_governance_conflict",
                    "source governance revision no longer matches",
                )
            if current.state is GovernanceState.REVOKED:
                return Outcome.success(current)
            from .dependencies import revoke_dependants
            revoke_dependants(self._runtime, reference, observed_at)
            updated_revision = current.governance_revision + 1
            connection.execute(
                """
                UPDATE sources
                SET state = ?, governance_revision = ?, updated_at = ?
                WHERE source_id = ? AND lineage_id = ? AND source_revision = ?
                  AND governance_revision = ?
                """,
                (
                    GovernanceState.REVOKED.value,
                    updated_revision,
                    encode_timestamp(observed_at),
                    *_source_key(reference),
                    expected_governance_revision,
                ),
            )
            from .dependencies import invalidate_batch
            invalidate_batch(self._runtime, clear_all=True)
        return Outcome.success(
            replace(
                current,
                state=GovernanceState.REVOKED,
                governance_revision=updated_revision,
            )
        )

    def read(
        self,
        reference: SourceRef,
        *,
        resolved: ResolvedIdentity,
        capability: str,
        observed_at: datetime,
        authorizer: IdentityAuthorizationPort,
    ) -> Outcome[SourceRecord]:
        _ensure_runtime_scope(self._runtime, reference.scope)
        checked = self.validate_reference(reference, observed_at=observed_at)
        if not checked.is_success:
            return Outcome(failure=checked.failure)
        access = authorizer.authorize(
            resolved,
            scope=reference.scope,
            capability=capability,
            observed_at=observed_at,
        )
        if not access.is_success:
            return Outcome(failure=access.failure)
        row = self._runtime.read_one(
            """
            SELECT * FROM sources
            WHERE source_id = ? AND lineage_id = ? AND source_revision = ?
            """,
            _source_key(reference),
        )
        if row is None:
            return Outcome.failed("source_not_found", "source version does not exist")
        record = _source_from_row(row)
        if record.reference != reference:
            return Outcome.failed("source_reference_mismatch", "source identity does not match")
        if resolved.subject not in record.audience.recipients:
            return Outcome.failed(
                "source_audience_denied",
                "source is not visible to the current subject",
            )
        if record.state is GovernanceState.REVOKED:
            return Outcome.failed("source_revoked", "source is no longer readable")
        if record.reference.is_expired_at(observed_at):
            return Outcome.failed("source_expired", "source is no longer current")
        return Outcome.success(record)

    def list_readable_current(
        self,
        scope: ScopeRef,
        *,
        owners: tuple[str, ...],
        expected_recipients: tuple[SubjectRef, ...],
        resolved: ResolvedIdentity,
        capability: str,
        observed_at: datetime,
        authorizer: IdentityAuthorizationPort,
        limit: int = 128,
    ) -> tuple[SourceRecord, ...]:
        """List bounded current sources after rechecking read governance.

        The inventory query returns identifiers only. Payloads are read through
        ``read`` so source-owner attestations, identity grants, and audience
        checks remain in force for every returned record.
        """
        observed_at = normalize_utc(observed_at, field="observed_at")
        _ensure_runtime_scope(self._runtime, scope)
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 256:
            raise ValueError("source inventory limit must be between 1 and 256")
        selected_owners = tuple(dict.fromkeys(owner for owner in owners
                                              if isinstance(owner, str) and owner.strip()))
        if not selected_owners:
            return ()
        recipient_set = set(expected_recipients)
        if not recipient_set or resolved.subject not in recipient_set:
            return ()
        columns = ", ".join(SCOPE_COLUMNS)
        owner_slots = ", ".join("?" for _ in selected_owners)
        rows = self._runtime.read_all(
            f"""SELECT source_id, lineage_id, source_revision, owner, {columns}, issued_at, expires_at
                FROM sources
                WHERE installation_id = ? AND platform = ? AND scope_kind = ? AND native_scope_id = ?
                  AND bot_id = ? AND account_id = ? AND conversation_id = ? AND session_id = ?
                  AND owner IN ({owner_slots}) AND state = 'active' AND issued_at <= ?
                  AND (expires_at IS NULL OR expires_at > ?)
                ORDER BY issued_at DESC, source_id DESC LIMIT ?""",
            (*scope_values(scope), *selected_owners, encode_timestamp(observed_at),
             encode_timestamp(observed_at), limit),
        )
        readable = []
        for row in rows:
            try:
                reference = SourceRef(row["source_id"], row["owner"], scope_from_row(row),
                    VersionRef(row["lineage_id"], int(row["source_revision"])),
                    decode_timestamp(row["issued_at"]),
                    decode_timestamp(row["expires_at"]) if row["expires_at"] else None)
                result = self.read(reference, resolved=resolved, capability=capability,
                                   observed_at=observed_at, authorizer=authorizer)
                if not result.is_success:
                    continue
                record = result.unwrap()
                if (record.reference.scope != scope
                        or not recipient_set.issubset(set(record.audience.recipients))
                        or digest(record.payload) != record.content_hash):
                    continue
                readable.append(record)
            except (KeyError, TypeError, ValueError, UnicodeError):
                continue
        return tuple(readable)


class EvidenceRepository:
    def __init__(self, runtime: StorageRuntime, sources: SourceRepository) -> None:
        self._runtime = runtime
        self._sources = sources

    def publish(self, record: EvidenceRecord) -> Outcome[EvidenceRecord]:
        _ensure_runtime_scope(self._runtime, record.source.scope)
        with self._runtime.transaction() as connection:
            source = connection.execute(
                """
                SELECT state FROM sources
                WHERE source_id = ? AND lineage_id = ? AND source_revision = ?
                """,
                _source_key(record.source),
            ).fetchone()
            if source is None:
                return Outcome.failed("source_not_found", "evidence source does not exist")
            if source["state"] != GovernanceState.ACTIVE.value:
                return Outcome.failed("source_revoked", "evidence source is no longer writable")
            existing = connection.execute(
                """
                SELECT * FROM evidence
                WHERE evidence_id = ? AND lineage_id = ? AND evidence_revision = ?
                """,
                (record.evidence_id, record.version.lineage_id, record.version.revision),
            ).fetchone()
            if existing is not None:
                restored = _evidence_from_row(existing, record.source)
                if restored == record:
                    return Outcome.success(restored)
                return Outcome.failed(
                    "evidence_version_conflict",
                    "evidence version is already bound to different content or governance",
                )
            connection.execute(
                """
                INSERT INTO evidence(
                    evidence_id, source_id, source_lineage_id, source_revision, lineage_id,
                    evidence_revision, observed_at, content_hash, payload_json, state,
                    governance_revision
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.evidence_id,
                    *_source_key(record.source),
                    record.version.lineage_id,
                    record.version.revision,
                    encode_timestamp(record.observed_at),
                    record.content_hash,
                    json.dumps(record.payload, ensure_ascii=False),
                    record.state.value,
                    record.governance_revision,
                ),
            )
        return Outcome.success(record)

    def read(
        self,
        *,
        evidence_id: str,
        version: VersionRef,
        source: SourceRef,
        resolved: ResolvedIdentity,
        capability: str,
        observed_at: datetime,
        authorizer: IdentityAuthorizationPort,
    ) -> Outcome[EvidenceRecord]:
        readable_source = self._sources.read(
            source,
            resolved=resolved,
            capability=capability,
            observed_at=observed_at,
            authorizer=authorizer,
        )
        if not readable_source.is_success:
            return Outcome(failure=readable_source.failure)
        row = self._runtime.read_one(
            """
            SELECT * FROM evidence
            WHERE evidence_id = ? AND lineage_id = ? AND evidence_revision = ?
            """,
            (evidence_id, version.lineage_id, version.revision),
        )
        if row is None:
            return Outcome.failed("evidence_not_found", "evidence version does not exist")
        if (
            row["source_id"],
            row["source_lineage_id"],
            int(row["source_revision"]),
        ) != _source_key(source):
            return Outcome.failed("evidence_source_mismatch", "evidence belongs to another source")
        record = _evidence_from_row(row, source)
        if record.state is GovernanceState.REVOKED:
            return Outcome.failed("evidence_revoked", "evidence is no longer readable")
        return Outcome.success(record)


class AssetRepository:
    def __init__(self, runtime: StorageRuntime) -> None:
        self._runtime = runtime
        self._namespaces = NamespaceRepository(runtime)

    def publish(self, record: AssetRecord, *, observed_at: datetime) -> Outcome[AssetRecord]:
        _ensure_runtime_scope(self._runtime, record.reference.scope)
        self._namespaces.ensure(
            owner=record.reference.owner,
            scope=record.reference.scope,
            created_at=observed_at,
        )
        with self._runtime.transaction() as connection:
            existing = connection.execute(
                """
                SELECT * FROM assets
                WHERE asset_id = ? AND lineage_id = ? AND asset_revision = ?
                """,
                _asset_key(record.reference),
            ).fetchone()
            if existing is not None:
                restored = _asset_from_row(existing)
                if restored == record:
                    return Outcome.success(restored)
                return Outcome.failed(
                    "asset_version_conflict",
                    "asset version is already bound to different metadata or governance",
                )
            connection.execute(
                """
                INSERT INTO assets(
                    asset_id, lineage_id, asset_revision, owner, installation_id, platform,
                    scope_kind, native_scope_id, bot_id, account_id, conversation_id, session_id, recipients_json, content_hash, state,
                    governance_revision, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    *_asset_key(record.reference),
                    record.reference.owner,
                    *scope_values(record.reference.scope),
                    _encode_audience(record.audience),
                    record.reference.content_hash,
                    record.state.value,
                    record.governance_revision,
                    encode_timestamp(observed_at),
                    encode_timestamp(observed_at),
                ),
            )
        return Outcome.success(record)

    def revoke(
        self,
        reference: AssetRef,
        *,
        expected_governance_revision: int,
        observed_at: datetime,
    ) -> Outcome[AssetRecord]:
        _ensure_runtime_scope(self._runtime, reference.scope)
        if expected_governance_revision < 1:
            raise ReferenceValidationError("expected_governance_revision must be positive")
        with self._runtime.transaction() as connection:
            row = connection.execute(
                """
                SELECT * FROM assets
                WHERE asset_id = ? AND lineage_id = ? AND asset_revision = ?
                """,
                _asset_key(reference),
            ).fetchone()
            if row is None:
                return Outcome.failed("asset_not_found", "asset version does not exist")
            current = _asset_from_row(row)
            if current.reference != reference:
                return Outcome.failed("asset_reference_mismatch", "asset identity does not match")
            if current.governance_revision != expected_governance_revision:
                return Outcome.failed(
                    "asset_governance_conflict",
                    "asset governance revision no longer matches",
                )
            if current.state is GovernanceState.REVOKED:
                return Outcome.success(current)
            updated_revision = current.governance_revision + 1
            connection.execute(
                """
                UPDATE assets
                SET state = ?, governance_revision = ?, updated_at = ?
                WHERE asset_id = ? AND lineage_id = ? AND asset_revision = ?
                  AND governance_revision = ?
                """,
                (
                    GovernanceState.REVOKED.value,
                    updated_revision,
                    encode_timestamp(observed_at),
                    *_asset_key(reference),
                    expected_governance_revision,
                ),
            )
        return Outcome.success(
            replace(
                current,
                state=GovernanceState.REVOKED,
                governance_revision=updated_revision,
            )
        )

    def read(
        self,
        reference: AssetRef,
        *,
        resolved: ResolvedIdentity,
        capability: str,
        observed_at: datetime,
        authorizer: IdentityAuthorizationPort,
    ) -> Outcome[AssetRecord]:
        _ensure_runtime_scope(self._runtime, reference.scope)
        access = authorizer.authorize(
            resolved,
            scope=reference.scope,
            capability=capability,
            observed_at=observed_at,
        )
        if not access.is_success:
            return Outcome(failure=access.failure)
        row = self._runtime.read_one(
            """
            SELECT * FROM assets
            WHERE asset_id = ? AND lineage_id = ? AND asset_revision = ?
            """,
            _asset_key(reference),
        )
        if row is None:
            return Outcome.failed("asset_not_found", "asset version does not exist")
        record = _asset_from_row(row)
        if record.reference != reference:
            return Outcome.failed("asset_reference_mismatch", "asset identity does not match")
        if resolved.subject not in record.audience.recipients:
            return Outcome.failed(
                "asset_audience_denied",
                "asset is not visible to the current subject",
            )
        if record.state is GovernanceState.REVOKED:
            return Outcome.failed("asset_revoked", "asset is no longer readable")
        return Outcome.success(record)


def _ensure_runtime_scope(runtime: StorageRuntime, scope: ScopeRef) -> None:
    if scope.installation_id != runtime.installation_id:
        raise ReferenceValidationError("reference scope belongs to another installation")


def _source_key(reference: SourceRef) -> tuple[str, str, int]:
    return (
        reference.source_id,
        reference.version.lineage_id,
        reference.version.revision,
    )


def _asset_key(reference: AssetRef) -> tuple[str, str, int]:
    return (
        reference.asset_id,
        reference.version.lineage_id,
        reference.version.revision,
    )


def _encode_audience(audience: AudienceRef) -> str:
    values = [
        {"kind": item.kind.value, "subject_id": item.subject_id}
        for item in audience.recipients
    ]
    return json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _decode_audience(scope: ScopeRef, encoded: str) -> AudienceRef:
    raw = json.loads(encoded)
    if not isinstance(raw, list):
        raise ReferenceValidationError("stored audience must be a list")
    recipients = tuple(
        SubjectRef(
            installation_id=scope.installation_id,
            kind=SubjectKind(item["kind"]),
            subject_id=item["subject_id"],
        )
        for item in raw
    )
    return AudienceRef(scope=scope, recipients=recipients)


def _source_from_row(row: sqlite3.Row) -> SourceRecord:
    scope = scope_from_row(row)
    reference = SourceRef(
        source_id=row["source_id"],
        owner=row["owner"],
        scope=scope,
        version=VersionRef(
            lineage_id=row["lineage_id"],
            revision=int(row["source_revision"]),
        ),
        issued_at=decode_timestamp(row["issued_at"]),
        expires_at=decode_timestamp(row["expires_at"]) if row["expires_at"] else None,
    )
    return SourceRecord(
        reference=reference,
        audience=_decode_audience(scope, row["recipients_json"]),
        content_hash=row["content_hash"],
        payload=json.loads(row["payload_json"]),
        state=GovernanceState(row["state"]),
        governance_revision=int(row["governance_revision"]),
    )


def _evidence_from_row(row: sqlite3.Row, source: SourceRef) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=row["evidence_id"],
        source=source,
        version=VersionRef(
            lineage_id=row["lineage_id"],
            revision=int(row["evidence_revision"]),
        ),
        observed_at=decode_timestamp(row["observed_at"]),
        content_hash=row["content_hash"],
        payload=json.loads(row["payload_json"]),
        state=GovernanceState(row["state"]),
        governance_revision=int(row["governance_revision"]),
    )


def _asset_from_row(row: sqlite3.Row) -> AssetRecord:
    scope = scope_from_row(row)
    reference = AssetRef(
        asset_id=row["asset_id"],
        owner=row["owner"],
        scope=scope,
        version=VersionRef(
            lineage_id=row["lineage_id"],
            revision=int(row["asset_revision"]),
        ),
        content_hash=row["content_hash"],
    )
    return AssetRecord(
        reference=reference,
        audience=_decode_audience(scope, row["recipients_json"]),
        state=GovernanceState(row["state"]),
        governance_revision=int(row["governance_revision"]),
    )
