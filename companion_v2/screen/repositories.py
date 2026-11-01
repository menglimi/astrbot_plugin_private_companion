"""Durable Screen references, with governance checked on every material read."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import sqlite3

from companion_v2.capabilities.repositories import CapabilityRepository
from companion_v2.execution.models import digest
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import (
    AssetRef, AudienceRef, ReferenceValidationError, ScopeRef, SourceRef, SubjectRef, VersionRef,
)
from companion_v2.identity_policy.models import AuthorizationTier, ResolvedIdentity
from companion_v2.identity_policy.ports import IdentityAuthorizationPort
from companion_v2.sources.models import AssetRecord, SourceRecord
from companion_v2.sources.repositories import AssetRepository, SourceRepository
from companion_v2.storage.codec import decode_timestamp, encode_timestamp, scope_from_row, scope_values
from companion_v2.storage.runtime import StorageRuntime

from .models import (
    ClockQuality, SCREEN_EXTENSION, SCREEN_PROCESS, SCREEN_READ,
    ScreenAnalysis, ScreenCapture, ScreenCaptureRef, ScreenCoverage,
    ScreenFeedbackAnchor, ScreenFeedbackKind, ScreenFeedbackMaterial,
    ScreenMaterial, ScreenMediaKind,
)
from .originals import ScreenImage, ScreenOriginalStore
from .proactive_access import proactive_allowed


@dataclass(frozen=True)
class PreparedScreenMaterial:
    material: ScreenMaterial
    admission_stamp: str


class _PublicationFailure(Exception):
    def __init__(self, failure) -> None:
        self.failure = failure


class ScreenRepository:
    """Accept originals from a trusted capture adapter; never sample devices here."""

    def __init__(self, runtime: StorageRuntime, *, authorizer: IdentityAuthorizationPort) -> None:
        self._runtime = runtime
        self._authorizer = authorizer
        self._sources = SourceRepository(runtime)
        self._assets = AssetRepository(runtime)
        self._capabilities = CapabilityRepository(runtime)
        self._originals = ScreenOriginalStore(runtime.data_root)

    @property
    def runtime(self) -> StorageRuntime:
        return self._runtime

    def admit_capture(
        self, reference: ScreenCaptureRef, *, role: SubjectRef,
        resolved: ResolvedIdentity, observed_at: datetime,
    ) -> Outcome[str]:
        """Check capture authority before device I/O, then compare the stamp afterward."""
        return self._admission(reference, role, resolved, observed_at)

    def publish_image(
        self, capture: ScreenCapture, image: ScreenImage, *, audience: AudienceRef,
        window_title: str | None, role: SubjectRef, resolved: ResolvedIdentity,
        observed_at: datetime, expected_admission_stamp: str,
    ) -> Outcome[ScreenCapture]:
        reference = capture.reference
        if (capture.media_kind is not ScreenMediaKind.IMAGE
                or reference.asset.content_hash != image.content_hash
                or audience.scope != reference.source.scope
                or resolved.subject not in audience.recipients
                or "image.viewport" not in capture.coverage.units
                or ("window.title" in capture.coverage.units) != bool(
                    isinstance(window_title, str) and window_title.strip())
                or window_title is not None and (not isinstance(window_title, str) or len(window_title) > 1024)
                or capture.received_at > observed_at or capture.created_at > observed_at):
            return Outcome.failed("screen_original_invalid", "image and declared Screen coverage must match")
        admitted = self.admit_capture(reference, role=role, resolved=resolved, observed_at=observed_at)
        if not admitted.is_success:
            return Outcome(failure=admitted.failure)
        if admitted.unwrap() != expected_admission_stamp:
            return Outcome.failed("screen_admission_changed", "capture authority changed during device I/O")
        payload = json.dumps({
            "schema": "screen.image-original.v1", "capture_id": reference.capture_id,
            "device_id": capture.device_id, "mime_type": image.mime_type,
            "byte_count": len(image.content), "image_hash": image.content_hash,
            "window_title": window_title, "coverage": json.loads(_coverage_json(capture.coverage)),
            "captured_at": encode_timestamp(capture.captured_at),
            "received_at": encode_timestamp(capture.received_at),
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        try:
            self._originals.save(image)
        except (OSError, ReferenceValidationError):
            return Outcome.failed("screen_original_write_failed", "screen image bytes could not be retained")
        try:
            with self._runtime.transaction():
                current = self.admit_capture(reference, role=role, resolved=resolved, observed_at=observed_at)
                if not current.is_success:
                    raise _PublicationFailure(current.failure)
                if current.unwrap() != expected_admission_stamp:
                    return Outcome.failed("screen_admission_changed", "capture authority changed during publication")
                for result in (
                    self._sources.publish(SourceRecord(reference.source, audience, _hash(payload), payload), observed_at=observed_at),
                    self._assets.publish(AssetRecord(reference.asset, audience), observed_at=observed_at),
                ):
                    if not result.is_success:
                        raise _PublicationFailure(result.failure)
                result = self.register_capture(capture, role=role, resolved=resolved, observed_at=observed_at)
                if not result.is_success:
                    raise _PublicationFailure(result.failure)
        except _PublicationFailure as error:
            return Outcome(failure=error.failure)
        return Outcome.success(capture)

    def read_image(
        self, reference: ScreenCaptureRef, *, resolved: ResolvedIdentity,
        observed_at: datetime,
    ) -> Outcome[ScreenImage]:
        with self._runtime.read_snapshot():
            material = self.read_capture(reference, resolved=resolved, observed_at=observed_at)
            if not material.is_success:
                return Outcome(failure=material.failure)
            capture = material.unwrap().capture
            source = material.unwrap().source
            return self._read_image_original(capture, source.payload)

    def read_retained_capture(self, reference, *, resolved, observed_at):
        """Open a saved original for review, without renewing its evidence TTL."""
        with self._runtime.read_snapshot():
            row = self._capture_row(reference.capture_id)
            if row is None or not _matches_capture(row, reference):
                return Outcome.failed("screen_capture_not_found", "the retained capture is absent")
            consent = self._capabilities.consent(extension_id=SCREEN_EXTENSION,
                capability_id=SCREEN_READ, subject=resolved.subject, scope=reference.source.scope)
            if not consent.is_success:
                return Outcome(failure=consent.failure)
            access = self._authorizer.authorize(resolved, scope=reference.source.scope,
                capability=SCREEN_READ, observed_at=observed_at)
            if not access.is_success:
                return Outcome(failure=access.failure)
            source = self._runtime.read_one("""SELECT payload_json, content_hash FROM sources
                WHERE source_id = ? AND lineage_id = ? AND source_revision = ?""", _source_key(reference))
            payload = json.loads(source["payload_json"]) if source is not None else None
            if type(payload) is not str or _hash(payload) != source["content_hash"]:
                return Outcome.failed("screen_original_unavailable", "retained original metadata changed")
            provenance = self._sources.validate_archived_provenance(reference.source,
                recipient=resolved.subject, observed_at=observed_at,
                expected_content_hash=source["content_hash"])
            if not provenance.is_success:
                return Outcome(failure=provenance.failure)
            asset = self._assets.read(reference.asset, resolved=resolved, capability=SCREEN_READ,
                observed_at=observed_at, authorizer=self._authorizer)
            if not asset.is_success:
                return Outcome(failure=asset.failure)
            return Outcome.success((_capture_from_row(row, reference), payload))

    def read_retained_image(self, reference, *, resolved, observed_at):
        """Review inert saved bytes; current model reads still use read_image."""
        with self._runtime.read_snapshot():
            retained = self.read_retained_capture(reference, resolved=resolved, observed_at=observed_at)
            if not retained.is_success:
                return Outcome(failure=retained.failure)
            return self._read_image_original(*retained.unwrap())

    def read_retained_analysis(self, reference, analysis_id, *, resolved, observed_at):
        with self._runtime.read_snapshot():
            retained = self.read_retained_capture(reference, resolved=resolved, observed_at=observed_at)
            if not retained.is_success:
                return Outcome(failure=retained.failure)
            row = self._runtime.read_one("SELECT * FROM screen_analyses WHERE analysis_id = ? AND capture_id = ?",
                (analysis_id, reference.capture_id))
            if row is None:
                return Outcome.failed("screen_analysis_not_found", "this capture has no confirmed analysis")
            try:
                analysis = _analysis_from_row(row, reference)
                if not set(analysis.input_units).issubset(retained.unwrap()[0].coverage.units):
                    raise ReferenceValidationError("analysis coverage differs from the retained original")
                return Outcome.success(analysis)
            except (ValueError, TypeError, KeyError):
                return Outcome.failed("screen_analysis_changed", "the saved analysis no longer matches its hash")

    def _read_image_original(self, capture, payload):
        reference = capture.reference
        try:
            metadata = json.loads(payload)
            expected = {
                "schema": "screen.image-original.v1", "capture_id": reference.capture_id,
                "device_id": capture.device_id, "mime_type": metadata["mime_type"],
                "byte_count": metadata["byte_count"], "image_hash": reference.asset.content_hash,
                "window_title": metadata["window_title"],
                "coverage": json.loads(_coverage_json(capture.coverage)),
                "captured_at": encode_timestamp(capture.captured_at),
                "received_at": encode_timestamp(capture.received_at),
            }
            if (metadata != expected
                    or capture.media_kind is not ScreenMediaKind.IMAGE
                    or ("window.title" in capture.coverage.units) != bool(
                        isinstance(metadata["window_title"], str) and metadata["window_title"].strip())):
                raise ReferenceValidationError("screen image manifest differs from its original capture")
            body = self._originals.read(reference.asset.content_hash)
            if len(body) != metadata["byte_count"]:
                raise ReferenceValidationError("screen image length differs from its original manifest")
            return Outcome.success(ScreenImage(metadata["mime_type"], body))
        except (OSError, ValueError, KeyError, TypeError):
            return Outcome.failed("screen_original_unavailable", "the original image is missing or has changed")

    def published_reference(self, capture_id: str, *, scope: ScopeRef) -> Outcome[ScreenCaptureRef]:
        """Resolve only an already published sample; the caller must read it with current authority."""
        with self._runtime.read_snapshot():
            row = self._capture_row(capture_id)
            if row is None:
                return Outcome.failed("screen_capture_not_found", "the original capture is absent")
            source = self._runtime.read_one(
                "SELECT * FROM sources WHERE source_id = ? AND lineage_id = ? AND source_revision = ?",
                (row["source_id"], row["source_lineage_id"], row["source_revision"]),
            )
            asset = self._runtime.read_one(
                "SELECT * FROM assets WHERE asset_id = ? AND lineage_id = ? AND asset_revision = ?",
                (row["asset_id"], row["asset_lineage_id"], row["asset_revision"]),
            )
            if source is None or asset is None or scope_from_row(source) != scope or scope_from_row(asset) != scope:
                return Outcome.failed("screen_capture_reference_mismatch", "capture belongs to another original scope")
            return Outcome.success(ScreenCaptureRef(
                capture_id,
                SourceRef(source["source_id"], source["owner"], scope,
                          VersionRef(source["lineage_id"], source["source_revision"]),
                          decode_timestamp(source["issued_at"]),
                          decode_timestamp(source["expires_at"]) if source["expires_at"] else None),
                AssetRef(asset["asset_id"], asset["owner"], scope,
                         VersionRef(asset["lineage_id"], asset["asset_revision"]), asset["content_hash"]),
            ))

    def register_capture(
        self,
        capture: ScreenCapture,
        *,
        role: SubjectRef,
        resolved: ResolvedIdentity,
        observed_at: datetime,
    ) -> Outcome[ScreenCapture]:
        with self._runtime.transaction() as connection:
            admitted = self._admission(capture.reference, role, resolved, observed_at)
            if not admitted.is_success:
                return Outcome(failure=admitted.failure)
            originals = self._read_originals(capture, resolved=resolved, observed_at=observed_at)
            if not originals.is_success:
                return Outcome(failure=originals.failure)
            row = self._capture_row(capture.reference.capture_id)
            if row is not None:
                if _capture_from_row(row, capture.reference) == capture and _matches_capture(row, capture.reference):
                    return Outcome.success(capture)
                return Outcome.failed("screen_capture_conflict", "capture identity already refers to another sample")
            existing_source = connection.execute(
                """SELECT capture_id FROM screen_captures
                   WHERE source_id = ? AND source_lineage_id = ? AND source_revision = ?""",
                _source_key(capture.reference),
            ).fetchone()
            if existing_source is not None:
                return Outcome.failed("screen_source_already_registered", "source version already has a capture identity")
            connection.execute(
                """
                INSERT INTO screen_captures(
                    capture_id, source_id, source_lineage_id, source_revision,
                    asset_id, asset_lineage_id, asset_revision, device_id, media_kind,
                    coverage_json, captured_at, received_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    capture.reference.capture_id, *_source_key(capture.reference),
                    *_asset_key(capture.reference), capture.device_id, capture.media_kind.value,
                    _coverage_json(capture.coverage), encode_timestamp(capture.captured_at),
                    encode_timestamp(capture.received_at), encode_timestamp(capture.created_at),
                ),
            )
        return Outcome.success(capture)

    def read_capture(
        self,
        reference: ScreenCaptureRef,
        *,
        resolved: ResolvedIdentity,
        observed_at: datetime,
    ) -> Outcome[ScreenMaterial]:
        with self._runtime.read_snapshot():
            row = self._capture_row(reference.capture_id)
            if row is None:
                return Outcome.failed("screen_capture_not_found", "the original capture is absent")
            if not _matches_capture(row, reference):
                return Outcome.failed("screen_capture_reference_mismatch", "capture does not match the original source and asset")
            return self._read_originals(
                _capture_from_row(row, reference), resolved=resolved, observed_at=observed_at,
            )

    def prepare_processing(
        self,
        reference: ScreenCaptureRef,
        *,
        role: SubjectRef,
        resolved: ResolvedIdentity,
        observed_at: datetime,
    ) -> Outcome[PreparedScreenMaterial]:
        with self._runtime.read_snapshot():
            admitted = self._admission(reference, role, resolved, observed_at)
            if not admitted.is_success:
                return Outcome(failure=admitted.failure)
            material = self.read_capture(reference, resolved=resolved, observed_at=observed_at)
            if not material.is_success:
                return Outcome(failure=material.failure)
            return Outcome.success(PreparedScreenMaterial(material.unwrap(), admitted.unwrap()))

    def record_result(
        self,
        analysis: ScreenAnalysis,
        feedback: ScreenFeedbackAnchor,
        *,
        role: SubjectRef,
        resolved: ResolvedIdentity,
        expected_admission_stamp: str,
        observed_at: datetime,
    ) -> Outcome[ScreenFeedbackMaterial]:
        """Commit interpretation and its feedback anchor together after processing."""

        if (
            feedback.capture != analysis.capture or feedback.analysis_id != analysis.analysis_id
            or feedback.analysis_version != analysis.version
        ):
            return Outcome.failed("screen_feedback_analysis_mismatch", "feedback must reference this exact analysis")
        with self._runtime.transaction() as connection:
            prepared = self.prepare_processing(
                analysis.capture, role=role, resolved=resolved, observed_at=observed_at,
            )
            if not prepared.is_success:
                return Outcome(failure=prepared.failure)
            current = prepared.unwrap()
            if current.admission_stamp != expected_admission_stamp:
                return Outcome.failed("screen_admission_changed", "role, package or authorization changed during processing")
            if not set(analysis.input_units).issubset(current.material.capture.coverage.units):
                return Outcome.failed("screen_coverage_mismatch", "analysis claims input outside the supplied material")
            if feedback.kind is ScreenFeedbackKind.PROACTIVE:
                if not proactive_allowed(self._runtime, scope=analysis.capture.source.scope,
                                         role=role, subject=resolved.subject):
                    return Outcome.failed("screen_proactive_disabled", "proactive feedback is not enabled in this scope")
            previous_analysis = self._analysis_row(analysis.analysis_id, analysis.version)
            if previous_analysis is not None:
                if (
                    previous_analysis["capture_id"] != analysis.capture.capture_id
                    or _analysis_from_row(previous_analysis, analysis.capture) != analysis
                ):
                    return Outcome.failed("screen_analysis_conflict", "analysis version already holds another interpretation")
            previous_feedback = self._feedback_row(feedback.feedback_id)
            if previous_feedback is not None:
                if (
                    previous_feedback["capture_id"] != feedback.capture.capture_id
                    or _feedback_from_row(previous_feedback, feedback.capture) != feedback
                ):
                    return Outcome.failed("screen_feedback_conflict", "feedback already refers to another result")
            if previous_analysis is None:
                connection.execute(
                    """
                    INSERT INTO screen_analyses(
                        analysis_id, lineage_id, analysis_revision, capture_id, processor_id,
                        purpose, coverage_json, content_hash, payload_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        analysis.analysis_id, analysis.version.lineage_id, analysis.version.revision,
                        analysis.capture.capture_id, analysis.processor_id, analysis.purpose,
                        json.dumps(analysis.input_units), _hash(analysis.interpretation),
                        json.dumps(analysis.interpretation, ensure_ascii=False), encode_timestamp(analysis.created_at),
                    ),
                )
            if previous_feedback is None:
                connection.execute(
                    """
                    INSERT INTO screen_feedback_anchors(
                        feedback_id, capture_id, analysis_id, analysis_lineage_id,
                        analysis_revision, feedback_kind, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        feedback.feedback_id, feedback.capture.capture_id, feedback.analysis_id,
                        analysis.version.lineage_id, analysis.version.revision, feedback.kind.value,
                        encode_timestamp(feedback.created_at),
                    ),
                )
            return Outcome.success(ScreenFeedbackMaterial(feedback, current.material, analysis))

    def read_feedback(
        self,
        feedback_id: str,
        *,
        capture: ScreenCaptureRef,
        resolved: ResolvedIdentity,
        observed_at: datetime,
    ) -> Outcome[ScreenFeedbackMaterial]:
        """Reopen the original anchor; no lookup of a newer capture is available."""

        with self._runtime.read_snapshot():
            row = self._feedback_row(feedback_id)
            if row is None:
                return Outcome.failed("screen_feedback_not_found", "feedback has no saved result")
            if row["capture_id"] != capture.capture_id:
                return Outcome.failed("screen_feedback_capture_mismatch", "feedback belongs to the original capture")
            material = self.read_capture(capture, resolved=resolved, observed_at=observed_at)
            if not material.is_success:
                return Outcome(failure=material.failure)
            feedback = _feedback_from_row(row, capture)
            analysis = None
            if feedback.analysis_id is not None and feedback.analysis_version is not None:
                analysis_row = self._analysis_row(feedback.analysis_id, feedback.analysis_version)
                if analysis_row is None:
                    return Outcome.failed("screen_analysis_not_found", "feedback interpretation is absent")
                if analysis_row["capture_id"] != capture.capture_id:
                    return Outcome.failed("screen_feedback_analysis_mismatch", "analysis belongs to a different capture")
                analysis = _analysis_from_row(analysis_row, capture)
            return Outcome.success(ScreenFeedbackMaterial(feedback, material.unwrap(), analysis))

    def latest_analysis(
        self,
        *,
        scope: ScopeRef,
        resolved: ResolvedIdentity,
        observed_at: datetime,
    ) -> Outcome[tuple[ScreenFeedbackMaterial, ...]]:
        """Read the newest saved interpretation in one exact private scope."""
        values = scope_values(scope)
        with self._runtime.read_snapshot():
            rows = self._runtime.read_all(
                """SELECT f.feedback_id, f.capture_id
                   FROM screen_feedback_anchors AS f
                   JOIN screen_captures AS c ON c.capture_id = f.capture_id
                   JOIN sources AS s ON s.source_id = c.source_id
                     AND s.lineage_id = c.source_lineage_id
                     AND s.source_revision = c.source_revision
                   WHERE f.analysis_id IS NOT NULL
                     AND s.installation_id = ? AND s.platform = ? AND s.scope_kind = ?
                     AND s.native_scope_id = ? AND s.bot_id = ? AND s.account_id = ?
                     AND s.conversation_id = ? AND s.session_id = ?
                   ORDER BY f.created_at DESC, f.feedback_id DESC LIMIT 12""",
                values,
            )
            for row in rows:
                reference = self.published_reference(row["capture_id"], scope=scope)
                if not reference.is_success:
                    return Outcome(failure=reference.failure)
                material = self.read_feedback(
                    row["feedback_id"], capture=reference.unwrap(),
                    resolved=resolved, observed_at=observed_at,
                )
                if not material.is_success:
                    return Outcome(failure=material.failure)
                if material.unwrap().analysis is not None:
                    return Outcome.success((material.unwrap(),))
        return Outcome.success(())

    def _read_originals(
        self, capture: ScreenCapture, *, resolved: ResolvedIdentity, observed_at: datetime,
    ) -> Outcome[ScreenMaterial]:
        reference = capture.reference
        consent = self._capabilities.consent(
            extension_id=SCREEN_EXTENSION, capability_id=SCREEN_READ,
            subject=resolved.subject, scope=reference.source.scope,
        )
        if not consent.is_success:
            return Outcome(failure=consent.failure)
        source = self._sources.read(
            reference.source, resolved=resolved, capability=SCREEN_READ,
            observed_at=observed_at, authorizer=self._authorizer,
        )
        if not source.is_success:
            return Outcome(failure=source.failure)
        asset = self._assets.read(
            reference.asset, resolved=resolved, capability=SCREEN_READ,
            observed_at=observed_at, authorizer=self._authorizer,
        )
        if not asset.is_success:
            return Outcome(failure=asset.failure)
        return Outcome.success(ScreenMaterial(capture, source.unwrap(), asset.unwrap()))

    def _admission(
        self, reference: ScreenCaptureRef, role: SubjectRef,
        resolved: ResolvedIdentity, observed_at: datetime,
    ) -> Outcome[str]:
        role_selection = self._capabilities.require_role_enabled(extension_id=SCREEN_EXTENSION, role=role,
                                                                 bot_id=reference.source.scope.bot_id)
        if not role_selection.is_success:
            return Outcome(failure=role_selection.failure)
        stamps: list[object] = []
        for capability in (SCREEN_READ, SCREEN_PROCESS):
            consent = self._capabilities.authorize(
                extension_id=SCREEN_EXTENSION, capability_id=capability,
                subject=resolved.subject, scope=reference.source.scope,
            )
            if not consent.is_success:
                return Outcome(failure=consent.failure)
            access = self._authorizer.authorize(
                resolved, scope=reference.source.scope, capability=capability, observed_at=observed_at,
            )
            if (not access.is_success and access.failure.code in
                    ("authorization_denied", "scope_not_granted")):
                # Consent is the user's explicit authorization in this exact
                # scope; the identity grant is only its mirror. An upgrade can
                # leave an active consent without that mirror, which used to
                # surface as "the Dashboard identity has no permission" on the
                # proactive Screen test. Restore the mirror instead of blaming
                # the operator, and never resurrect an explicitly revoked grant
                # (its id already exists, so issuance fails closed).
                repaired = self._mirror_consented_grant(
                    resolved, reference.source.scope, capability, observed_at)
                if repaired is not None and repaired.is_success:
                    access = repaired
            if not access.is_success:
                return Outcome(failure=access.failure)
            identity = access.unwrap()
            stamps.append((
                capability, identity.subject.kind.value, identity.subject.subject_id,
                identity.binding_id, identity.binding_revision, identity.grant_id, identity.grant_revision,
                consent.unwrap().revision,
            ))
        installation = self._capabilities.installation(extension_id=SCREEN_EXTENSION).unwrap()
        stamps.append((installation.revision, role.subject_id, role_selection.unwrap().revision))
        return Outcome.success(_hash(json.dumps(stamps, ensure_ascii=False)))

    def _mirror_consented_grant(self, resolved, scope, capability, observed_at):
        """Re-issue the identity grant that an active consent already implies."""
        issuer = getattr(self._authorizer, "issue_grant", None)
        revision = getattr(self._authorizer, "revision", None)
        if not callable(issuer) or type(revision) is not int:
            return None
        try:
            grant_id = "screen-access:mirror:" + digest([
                resolved.subject.kind.value, resolved.subject.subject_id,
                scope.installation_id, scope.platform, scope.kind.value, scope.native_scope_id,
                scope.bot_id, scope.account_id, scope.conversation_id, scope.session_id, capability])
            issuer(grant_id=grant_id, subject=resolved.subject, scope=scope,
                   capability=capability, tier=AuthorizationTier.STANDARD,
                   valid_from=observed_at, expected_revision=revision)
        except Exception:  # existing (possibly revoked) grant, or a concurrent policy write
            return None
        return self._authorizer.authorize(resolved, scope=scope, capability=capability,
                                          observed_at=observed_at)

    def _capture_row(self, capture_id: str) -> sqlite3.Row | None:
        return self._runtime.read_one("SELECT * FROM screen_captures WHERE capture_id = ?", (capture_id,))

    def _analysis_row(self, analysis_id: str, version: VersionRef) -> sqlite3.Row | None:
        return self._runtime.read_one(
            "SELECT * FROM screen_analyses WHERE analysis_id = ? AND lineage_id = ? AND analysis_revision = ?",
            (analysis_id, version.lineage_id, version.revision),
        )

    def _feedback_row(self, feedback_id: str) -> sqlite3.Row | None:
        return self._runtime.read_one("SELECT * FROM screen_feedback_anchors WHERE feedback_id = ?", (feedback_id,))


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _source_key(reference: ScreenCaptureRef) -> tuple[str, str, int]:
    return reference.source.source_id, reference.source.version.lineage_id, reference.source.version.revision


def _asset_key(reference: ScreenCaptureRef) -> tuple[str, str, int]:
    return reference.asset.asset_id, reference.asset.version.lineage_id, reference.asset.version.revision


def _matches_capture(row: sqlite3.Row, reference: ScreenCaptureRef) -> bool:
    return (
        (row["source_id"], row["source_lineage_id"], row["source_revision"]) == _source_key(reference)
        and (row["asset_id"], row["asset_lineage_id"], row["asset_revision"]) == _asset_key(reference)
    )


def _coverage_json(coverage: ScreenCoverage) -> str:
    return json.dumps({
        "surface": coverage.surface, "units": coverage.units, "gaps": coverage.gaps,
        "clock_quality": coverage.clock_quality.value,
    }, ensure_ascii=False, sort_keys=True)


def _capture_from_row(row: sqlite3.Row, reference: ScreenCaptureRef) -> ScreenCapture:
    coverage = json.loads(row["coverage_json"])
    return ScreenCapture(
        reference=reference, device_id=row["device_id"], media_kind=ScreenMediaKind(row["media_kind"]),
        coverage=ScreenCoverage(
            surface=coverage["surface"], units=tuple(coverage["units"]), gaps=tuple(coverage["gaps"]),
            clock_quality=ClockQuality(coverage["clock_quality"]),
        ),
        captured_at=decode_timestamp(row["captured_at"]), received_at=decode_timestamp(row["received_at"]),
        created_at=decode_timestamp(row["created_at"]),
    )


def _analysis_from_row(row: sqlite3.Row, capture: ScreenCaptureRef) -> ScreenAnalysis:
    interpretation = json.loads(row["payload_json"])
    if _hash(interpretation) != row["content_hash"]:
        raise ReferenceValidationError("screen analysis content hash does not match")
    return ScreenAnalysis(
        analysis_id=row["analysis_id"], version=VersionRef(row["lineage_id"], row["analysis_revision"]),
        capture=capture, processor_id=row["processor_id"], purpose=row["purpose"],
        input_units=tuple(json.loads(row["coverage_json"])), interpretation=interpretation,
        created_at=decode_timestamp(row["created_at"]),
    )


def _feedback_from_row(row: sqlite3.Row, capture: ScreenCaptureRef) -> ScreenFeedbackAnchor:
    version = None
    if row["analysis_id"] is not None:
        version = VersionRef(row["analysis_lineage_id"], row["analysis_revision"])
    return ScreenFeedbackAnchor(
        feedback_id=row["feedback_id"], capture=capture, kind=ScreenFeedbackKind(row["feedback_kind"]),
        created_at=decode_timestamp(row["created_at"]), analysis_id=row["analysis_id"], analysis_version=version,
    )
