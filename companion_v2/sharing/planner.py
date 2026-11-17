"""Prepare and recheck share candidates without generating empty share openings."""

from __future__ import annotations

from datetime import datetime
import hashlib

from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import RoleOwnerRef
from companion_v2.identity_policy.models import ResolvedIdentity
from companion_v2.identity_policy.ports import IdentityAuthorizationPort
from companion_v2.sources.repositories import AssetRepository, SourceRepository

from .models import ShareCandidate, ShareMaterial


class ShareCatalog:
    """Keeps prepared candidates by identity; it never invents a replacement material."""

    def __init__(self) -> None:
        self._candidates: dict[str, ShareCandidate] = {}

    def remember(self, candidate: ShareCandidate) -> ShareCandidate:
        existing = self._candidates.get(candidate.candidate_id)
        if existing is None:
            self._candidates[candidate.candidate_id] = candidate
            return candidate
        # The preparation time records the first check, not material identity.
        # SharePlanner revalidates current access before consulting this catalog.
        if existing.material != candidate.material or existing.target_scope != candidate.target_scope:
            raise ValueError("candidate_id is already bound to different material")
        return existing

    def candidate(self, candidate_id: str) -> ShareCandidate:
        return self._candidates[candidate_id]


class SharePlanner:
    def __init__(
        self,
        *,
        sources: SourceRepository,
        assets: AssetRepository,
        catalog: ShareCatalog,
    ) -> None:
        self._sources = sources
        self._assets = assets
        self._catalog = catalog

    def prepare(
        self,
        material: ShareMaterial | None,
        *,
        resolved: ResolvedIdentity,
        capability: str,
        observed_at: datetime,
        authorizer: IdentityAuthorizationPort,
        role_owner: RoleOwnerRef | None = None,
    ) -> Outcome[ShareCandidate]:
        if material is None:
            return Outcome.failed("share_material_required", "no material exists for a share candidate")
        readable = self._revalidate(
            material,
            resolved=resolved,
            capability=capability,
            observed_at=observed_at,
            authorizer=authorizer,
            role_owner=role_owner,
        )
        if not readable.is_success:
            return Outcome(failure=readable.failure)
        candidate = ShareCandidate(
            candidate_id=_candidate_id(material),
            material=material,
            target_scope=material.scope,
            prepared_at=observed_at,
        )
        return Outcome.success(self._catalog.remember(candidate))

    def follow_up_material(
        self,
        candidate_id: str,
        *,
        resolved: ResolvedIdentity,
        capability: str,
        observed_at: datetime,
        authorizer: IdentityAuthorizationPort,
        role_owner: RoleOwnerRef | None = None,
    ) -> Outcome[ShareMaterial]:
        try:
            candidate = self._catalog.candidate(candidate_id)
        except KeyError:
            return Outcome.failed("share_candidate_not_found", "share candidate does not exist")
        readable = self._revalidate(
            candidate.material,
            resolved=resolved,
            capability=capability,
            observed_at=observed_at,
            authorizer=authorizer,
            role_owner=role_owner,
        )
        if not readable.is_success:
            return Outcome(failure=readable.failure)
        return Outcome.success(candidate.material)

    def _revalidate(
        self,
        material: ShareMaterial,
        *,
        resolved: ResolvedIdentity,
        capability: str,
        observed_at: datetime,
        authorizer: IdentityAuthorizationPort,
        role_owner: RoleOwnerRef | None,
    ) -> Outcome[bool]:
        if material.role_owner is not None and material.role_owner != role_owner:
            return Outcome.failed("share_role_owner_mismatch", "the material belongs to a different bot/persona")
        source = self._sources.read(
            material.source,
            resolved=resolved,
            capability=capability,
            observed_at=observed_at,
            authorizer=authorizer,
        )
        if not source.is_success:
            return Outcome(failure=source.failure)
        if material.asset is not None:
            asset = self._assets.read(
                material.asset,
                resolved=resolved,
                capability=capability,
                observed_at=observed_at,
                authorizer=authorizer,
            )
            if not asset.is_success:
                return Outcome(failure=asset.failure)
        return Outcome.success(True)


def _candidate_id(material: ShareMaterial) -> str:
    token = "\x1f".join(
        (
            material.material_id,
            material.source.source_id,
            material.source.version.lineage_id,
            str(material.source.version.revision),
            material.asset.asset_id if material.asset is not None else "no-asset",
        )
    ).encode("utf-8")
    return f"share-candidate-{hashlib.sha256(token).hexdigest()[:24]}"
