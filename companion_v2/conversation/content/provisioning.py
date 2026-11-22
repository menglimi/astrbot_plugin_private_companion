"""Idempotent default access provisioning and readiness status for native Content."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import logging

from companion_v2.capabilities.models import CapabilityAuthorizationState, CapabilityDesiredState
from companion_v2.capabilities.privacy import is_core
from companion_v2.capabilities.repositories import CapabilityRepository
from companion_v2.conversation.content.authoring_service import effective_settings
from companion_v2.execution.models import digest
from companion_v2.foundation.refs import ScopeKind, SubjectKind
from companion_v2.identity_policy.models import AuthorizationTier, GrantState
from companion_v2.identity_policy.repository import PersistentIdentityDirectory
from companion_v2.storage.runtime import StorageRuntime


logger = logging.getLogger(__name__)

CONTENT_EXTENSION = "content"
CONTENT_CAPABILITIES = (
    "content.work.create",
    "content.work.save_candidate",
    "content.work.adopt",
    "content.work.retract",
    "content.work.list",
    "content.work.describe",
    "content.work.read",
)
_READ_REQUIRED = frozenset(("content.work.read", "content.work.list"))
_CONTINUE_REQUIRED = frozenset(("content.work.describe", "content.work.save_candidate"))


def ensure_content_access(
    runtime: StorageRuntime,
    context,
    *,
    observed_at: datetime,
    log: logging.Logger | None = None,
) -> bool:
    """Idempotently provision role selection and per-scope consent for Content.

    Provisioning applies only when all of the following hold:
    - the target context is a private conversation with a resolved human subject
      and bot role;
    - the subject has ``core`` access tier;
    - ``content.authoring.enabled`` is effectively true for the role;
    - the native ``content`` package is installed and ``active``.

    Explicit role disables (``enabled=False``) and explicit consent or grant
    revocations are never overwritten. When AstrBot rotates ``conversation_id``
    on the same private route, the latest route choice is carried forward so
    active authorizations continue seamlessly while revocations remain intact.
    """
    active_logger = log or logger
    extracted = _extract_private_identity(context)
    if extracted is None:
        return False
    role, scope, subject = extracted
    try:
        with runtime.read_snapshot():
            if not _needs_provisioning(runtime, context, role, scope, subject, observed_at):
                return False
        with runtime.transaction():
            return _provision_in_transaction(
                runtime, context, role, scope, subject, observed_at, active_logger
            )
    except Exception as error:
        active_logger.warning(
            "[CompanionV2] 创作能力授权补齐失败 error_type=%s", type(error).__name__
        )
        return False


def content_authorization_status(runtime: StorageRuntime) -> dict[str, object]:
    """Read honest role-selection and private-session readiness for Content."""
    with runtime.read_snapshot():
        role_rows = runtime.read_all(
            """
            SELECT role_id, bot_id
            FROM capability_role_selections
            WHERE extension_id = ? AND enabled = 1
            """,
            (CONTENT_EXTENSION,),
        )
        role_enabled_count = len(role_rows)
        if role_enabled_count == 0:
            return {
                "ready": False,
                "role_enabled_count": 0,
                "authorized_scope_count": 0,
                "issue": "capability_role_not_enabled",
            }
        enabled_bots = {str(row["bot_id"] or "") for row in role_rows}
        auth_rows = runtime.read_all(
            """
            SELECT capability_id, subject_kind, subject_id, platform,
                   native_scope_id, bot_id, account_id, conversation_id, session_id
            FROM capability_authorizations
            WHERE extension_id = ?
              AND installation_id = ?
              AND scope_kind = ?
              AND state = ?
            """,
            (
                CONTENT_EXTENSION,
                runtime.installation_id,
                ScopeKind.PRIVATE.value,
                CapabilityAuthorizationState.ACTIVE.value,
            ),
        )
        by_scope: dict[tuple[str, ...], set[str]] = {}
        for row in auth_rows:
            bot_id = str(row["bot_id"] or "")
            if "" not in enabled_bots and bot_id not in enabled_bots:
                continue
            scope_key = (
                str(row["subject_kind"]),
                str(row["subject_id"]),
                str(row["platform"]),
                str(row["native_scope_id"]),
                bot_id,
                str(row["account_id"] or ""),
                str(row["conversation_id"] or ""),
                str(row["session_id"] or ""),
            )
            by_scope.setdefault(scope_key, set()).add(str(row["capability_id"]))
        authorized_scope_count = sum(
            1 for capabilities in by_scope.values() if _authoring_capable(capabilities)
        )
        if authorized_scope_count == 0:
            return {
                "ready": False,
                "role_enabled_count": role_enabled_count,
                "authorized_scope_count": 0,
                "issue": "capability_consent_missing",
            }
        return {
            "ready": True,
            "role_enabled_count": role_enabled_count,
            "authorized_scope_count": authorized_scope_count,
            "issue": None,
        }


def _authoring_capable(capabilities: set[str]) -> bool:
    if not _READ_REQUIRED.issubset(capabilities):
        return False
    return "content.work.create" in capabilities or _CONTINUE_REQUIRED.issubset(capabilities)


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


def _needs_provisioning(runtime, context, role, scope, subject, observed_at) -> bool:
    if not is_core(runtime, subject):
        return False
    if not effective_settings(runtime, context).enabled:
        return False
    repository = CapabilityRepository(runtime)
    projected = repository.projection(extension_id=CONTENT_EXTENSION, role=role, scope=scope)
    if not projected.is_success:
        return False
    projection = projected.unwrap()
    if projection.installation.desired_state is not CapabilityDesiredState.ACTIVE:
        return False
    if projection.role_selection is not None and not projection.role_selection.enabled:
        return False
    if projection.role_selection is None:
        return True
    has_active = False
    for capability in CONTENT_CAPABILITIES:
        consent = repository.consent(
            extension_id=CONTENT_EXTENSION,
            capability_id=capability,
            subject=subject,
            scope=scope,
        )
        if consent.is_success:
            has_active = True
            continue
        if consent.failure.code == "capability_consent_missing":
            return True
    return has_active and _needs_invocation_grant(runtime, subject, scope, observed_at)


def _provision_in_transaction(runtime, context, role, scope, subject, observed_at, active_logger) -> bool:
    if not is_core(runtime, subject):
        return False
    if not effective_settings(runtime, context).enabled:
        return False
    repository = CapabilityRepository(runtime)
    projected = repository.projection(extension_id=CONTENT_EXTENSION, role=role, scope=scope)
    if not projected.is_success:
        active_logger.debug(
            "[CompanionV2] 创作能力投影不可用 code=%s role=%s",
            projected.failure.code,
            role.subject_id,
        )
        return False
    projection = projected.unwrap()
    if projection.installation.desired_state is not CapabilityDesiredState.ACTIVE:
        return False
    changed = False
    if projection.role_selection is not None:
        if not projection.role_selection.enabled:
            active_logger.debug(
                "[CompanionV2] 创作能力未对角色启用 role=%s bot=%s",
                role.subject_id,
                scope.bot_id,
            )
            return False
    else:
        result = repository.set_role_enabled(
            extension_id=CONTENT_EXTENSION,
            role=role,
            bot_id=scope.bot_id,
            enabled=True,
            expected_revision=0,
            observed_at=observed_at,
        )
        if not result.is_success:
            active_logger.warning(
                "[CompanionV2] 创作能力角色启用失败 code=%s", result.failure.code
            )
            return False
        changed = True
    for capability in CONTENT_CAPABILITIES:
        consent = repository.consent(
            extension_id=CONTENT_EXTENSION,
            capability_id=capability,
            subject=subject,
            scope=scope,
        )
        if consent.is_success or consent.failure.code != "capability_consent_missing":
            continue
        previous = repository.latest_route_authorization(
            extension_id=CONTENT_EXTENSION,
            capability_id=capability,
            subject=subject,
            scope=scope,
        )
        state = previous.state if previous is not None else CapabilityAuthorizationState.ACTIVE
        result = repository.set_authorization(
            extension_id=CONTENT_EXTENSION,
            capability_id=capability,
            subject=subject,
            scope=scope,
            state=state,
            expected_revision=0,
            observed_at=observed_at,
        )
        if result.is_success:
            changed = True
        else:
            active_logger.warning(
                "[CompanionV2] 创作能力授权失败 capability=%s code=%s",
                capability,
                result.failure.code,
            )
    if any(
        repository.authorize(
            extension_id=CONTENT_EXTENSION,
            capability_id=capability,
            subject=subject,
            scope=scope,
        ).is_success
        for capability in CONTENT_CAPABILITIES
    ):
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
    """Ensure ``extension.tool.invoke`` carries across conversation switches."""
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
        grant_id="content-access:" + digest([subject, scope]),
        subject=subject,
        scope=scope,
        capability="extension.tool.invoke",
        tier=AuthorizationTier.STANDARD,
        valid_from=observed_at,
        expires_at=latest.expires_at if latest is not None else None,
        expected_revision=identities.revision,
    )
    return True


__all__ = [
    "CONTENT_CAPABILITIES",
    "CONTENT_EXTENSION",
    "content_authorization_status",
    "ensure_content_access",
]
