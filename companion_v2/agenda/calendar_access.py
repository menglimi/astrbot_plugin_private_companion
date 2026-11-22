"""Role-wide calendar opt-in for the currently bound private participant."""

from companion_v2.execution.models import TaskError, digest, require
from companion_v2.foundation.refs import ScopeKind, SubjectKind, SourceRef
from companion_v2.identity_policy.private_domain import source_ref
from companion_v2.identity_policy.models import GrantState
from companion_v2.identity_policy.private_domain import PrivateDomainAccess
from companion_v2.identity_policy.repository import PersistentIdentityDirectory
from companion_v2.sources.dependencies import active_validation_batch


CALENDAR_ACTIONS = frozenset({"calendar.read", "calendar.write"})


def role_grant_prefix(installation_id, bot_id, role):
    return "calendar-role:" + digest([installation_id, bot_id, role]) + ":"


def revoke_role_calendar_grants(identity, *, installation_id, bot_id, role, revoked_at):
    prefix = role_grant_prefix(installation_id, bot_id, role)
    for grant in identity.snapshot().grants:
        if grant.grant_id.startswith(prefix) and grant.state is GrantState.ACTIVE:
            identity.revoke_grant(grant.grant_id, revoked_at=revoked_at,
                                  expected_revision=identity.revision)


class CalendarAccess(PrivateDomainAccess):
    def __init__(self, tasks, role_state):
        super().__init__(tasks)
        self.role_state = role_state

    def check(self, context, action, *, operator=None, at=None):
        at = at or self.tasks.now()
        if operator is not None or action not in CALENDAR_ACTIONS:
            return super().check(context, action, operator=operator, at=at)
        if (context.scope.kind is not ScopeKind.PRIVATE
                or context.actor.subject.kind is not SubjectKind.HUMAN
                or context.audience.recipients != (context.actor.subject,)):
            raise TaskError("private_domain_audience", "select one current private participant")
        require(self.tasks.check_context_access(context, action="conversation.reply", observed_at=at))
        settings, _token = self.role_state.settings(context)
        if not settings["calendar_enabled"]:
            raise TaskError("calendar_disabled", "enable role calendar access in state settings")

    def evidence(self, context, sources, *, at=None, limit=8):
        batch = active_validation_batch(self.runtime, at)
        if at is None:
            at = batch.at if batch is not None else self.tasks.now()
        extras = (batch.extras if batch is not None and not getattr(self.runtime, "_write_depth", 0)
                  else None)
        if not isinstance(sources, (tuple, list)) or len(sources) > limit:
            raise TaskError("domain_sources_invalid", "source dependency budget exceeded")
        for item in sources:
            reference = source_ref(item) if isinstance(item, dict) else item
            if not isinstance(reference, SourceRef) or reference.scope.kind is not ScopeKind.PRIVATE:
                raise TaskError("domain_source_scope", "calendar evidence must come from a private conversation")
            scope = reference.scope
            if (scope.installation_id != context.scope.installation_id
                    or scope.bot_id != context.scope.bot_id):
                raise TaskError("calendar_source_role_mismatch", "source belongs to another role")
            role_key = ("calendar.role", scope, reference.issued_at, context.role_binding.role)
            if extras is not None and role_key in extras:
                role_ok = extras[role_key]
            else:
                binding = self.tasks.identity.resolve_role(scope, observed_at=reference.issued_at)
                role_ok = binding.is_success and binding.unwrap().role == context.role_binding.role
                if extras is not None:
                    extras[role_key] = role_ok
            if not role_ok:
                raise TaskError("calendar_source_role_mismatch", "source was not created under this role")
            source_key = ("calendar.source", reference, context.actor.subject, at)
            if extras is not None and source_key in extras:
                failure = extras[source_key]
                if failure is not None:
                    raise TaskError(failure[0], failure[1])
            else:
                outcome = self.sources.validate_reference_for_audience(
                    reference, recipient=context.actor.subject, observed_at=at)
                if extras is not None:
                    extras[source_key] = (
                        (outcome.failure.code, outcome.failure.reason) if outcome.failure is not None else None
                    )
                require(outcome)
