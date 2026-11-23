"""Idempotent private-route game authorization carryover across conversation switches."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import logging

from companion_v2.capabilities.models import CapabilityAuthorizationState, CapabilityDesiredState
from companion_v2.capabilities.repositories import CapabilityRepository
from companion_v2.execution.models import TaskContext, digest
from companion_v2.foundation.refs import ScopeKind, SubjectKind
from companion_v2.games.native.access import GROUPS
from companion_v2.identity_policy.models import AuthorizationTier, GrantState
from companion_v2.identity_policy.repository import PersistentIdentityDirectory
from companion_v2.storage.runtime import StorageRuntime


logger = logging.getLogger(__name__)

GAME_EXTENSION = "game"
GAME_CAPABILITIES = tuple(sorted(action for actions in GROUPS.values() for action in actions))


def ensure_game_access(
    runtime: StorageRuntime,
    context: TaskContext,
    *,
    observed_at: datetime,
) -> bool:
    """Carry forward existing private-route game authorizations when conversation_id changes.

    When AstrBot rotates ``conversation_id`` (e.g. ``/new``, persona switch, or restart),
    each game capability that already has a decision on the same private route is carried
    forward with its latest state (``ACTIVE`` stays ``ACTIVE``, ``REVOKED`` stays ``REVOKED``).
    Explicit role disables and explicit consent or grant revocations are never overwritten,
    and unconfigured games are never silently enabled for non-core users.
    """
    extracted = _extract_private_identity(context)
    if extracted is None:
        return False
    role, scope, subject = extracted
    try:
        with runtime.read_snapshot():
            if not _needs_provisioning(runtime, role, scope, subject, observed_at):
                return False
        with runtime.transaction():
            return _provision_in_transaction(runtime, role, scope, subject, observed_at)
    except Exception as error:
        logger.warning("[CompanionV2] 游戏能力授权续期失败 error_type=%s", type(error).__name__)
        return False


def _extract_private_identity(context):
    scope = getattr(context, "scope", None)
    role = getattr(getattr(context, "role_binding", None), "role", None)
    subject = getattr(getattr(context, "actor", None), "subject", None)
    if (
        scope is None
        or scope.kind is not ScopeKind.PRIVATE
        or role is None
        or subject is None
        or getattr(subject, "kind", None) is not SubjectKind.HUMAN
    ):
        return None
    return role, scope, subject


def _needs_provisioning(runtime, role, scope, subject, observed_at) -> bool:
    repository = CapabilityRepository(runtime)
    projected = repository.projection(extension_id=GAME_EXTENSION, role=role, scope=scope)
    if not projected.is_success:
        return False
    projection = projected.unwrap()
    if projection.installation.desired_state is not CapabilityDesiredState.ACTIVE:
        return False
    if projection.role_selection is not None and not projection.role_selection.enabled:
        return False
    has_active = False
    for capability in GAME_CAPABILITIES:
        consent = repository.consent(
            extension_id=GAME_EXTENSION,
            capability_id=capability,
            subject=subject,
            scope=scope,
        )
        if consent.is_success:
            has_active = True
            continue
        if consent.failure.code != "capability_consent_missing":
            continue
        previous = repository.latest_route_authorization(
            extension_id=GAME_EXTENSION,
            capability_id=capability,
            subject=subject,
            scope=scope,
        )
        if previous is not None:
            return True
    if not has_active:
        return False
    if projection.role_selection is None:
        return True
    return _needs_invocation_grant(runtime, subject, scope, observed_at)


def _provision_in_transaction(runtime, role, scope, subject, observed_at) -> bool:
    repository = CapabilityRepository(runtime)
    projected = repository.projection(extension_id=GAME_EXTENSION, role=role, scope=scope)
    if not projected.is_success:
        return False
    projection = projected.unwrap()
    if projection.installation.desired_state is not CapabilityDesiredState.ACTIVE:
        return False
    if projection.role_selection is not None and not projection.role_selection.enabled:
        return False
    changed = False
    for capability in GAME_CAPABILITIES:
        consent = repository.consent(
            extension_id=GAME_EXTENSION,
            capability_id=capability,
            subject=subject,
            scope=scope,
        )
        if consent.is_success or consent.failure.code != "capability_consent_missing":
            continue
        previous = repository.latest_route_authorization(
            extension_id=GAME_EXTENSION,
            capability_id=capability,
            subject=subject,
            scope=scope,
        )
        if previous is None:
            continue
        result = repository.set_authorization(
            extension_id=GAME_EXTENSION,
            capability_id=capability,
            subject=subject,
            scope=scope,
            state=previous.state,
            expected_revision=0,
            observed_at=observed_at,
        )
        if result.is_success:
            changed = True
        else:
            logger.warning(
                "[CompanionV2] 游戏能力授权续期失败 capability=%s code=%s",
                capability,
                result.failure.code,
            )
    has_active = any(
        repository.authorize(
            extension_id=GAME_EXTENSION,
            capability_id=capability,
            subject=subject,
            scope=scope,
        ).is_success
        for capability in GAME_CAPABILITIES
    )
    if has_active:
        if projection.role_selection is None:
            result = repository.set_role_enabled(
                extension_id=GAME_EXTENSION,
                role=role,
                bot_id=scope.bot_id,
                enabled=True,
                expected_revision=0,
                observed_at=observed_at,
            )
            if result.is_success:
                changed = True
        if _ensure_invocation_grant(runtime, subject, scope, observed_at):
            changed = True
    return changed


def _needs_invocation_grant(runtime, subject, scope, observed_at) -> bool:
    identities = PersistentIdentityDirectory(runtime)
    grants = tuple(
        grant
        for grant in identities.grants_for_subject(subject)
        if grant.capability == "extension.tool.invoke"
        and replace(grant.scope, conversation_id=scope.conversation_id) == scope
    )
    if any(grant.scope == scope for grant in grants):
        return False
    latest = max(
        grants,
        key=lambda grant: (grant.revoked_at or grant.valid_from, grant.revision, grant.grant_id),
        default=None,
    )
    if latest is not None and (
        latest.state is not GrantState.ACTIVE
        or latest.valid_from > observed_at
        or (latest.expires_at is not None and latest.expires_at <= observed_at)
    ):
        return False
    return True


def _ensure_invocation_grant(runtime, subject, scope, observed_at) -> bool:
    """Carry ``extension.tool.invoke`` forward across private conversation switches."""
    identities = PersistentIdentityDirectory(runtime)
    grants = tuple(
        grant
        for grant in identities.grants_for_subject(subject)
        if grant.capability == "extension.tool.invoke"
        and replace(grant.scope, conversation_id=scope.conversation_id) == scope
    )
    if any(grant.scope == scope for grant in grants):
        return False
    latest = max(
        grants,
        key=lambda grant: (grant.revoked_at or grant.valid_from, grant.revision, grant.grant_id),
        default=None,
    )
    if latest is not None and (
        latest.state is not GrantState.ACTIVE
        or latest.valid_from > observed_at
        or (latest.expires_at is not None and latest.expires_at <= observed_at)
    ):
        return False
    identities.issue_grant(
        grant_id="game-access:" + digest([subject, scope]),
        subject=subject,
        scope=scope,
        capability="extension.tool.invoke",
        tier=AuthorizationTier.STANDARD,
        valid_from=observed_at,
        expires_at=latest.expires_at if latest is not None else None,
        expected_revision=identities.revision,
    )
    return True


__all__ = ["GAME_CAPABILITIES", "GAME_EXTENSION", "ensure_game_access"]
