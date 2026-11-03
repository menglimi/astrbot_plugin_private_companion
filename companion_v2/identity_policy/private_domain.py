"""Current private-participant and source checks shared by calendar and role state."""

from datetime import datetime

from companion_v2.execution.models import TaskError, require
from companion_v2.foundation.refs import ScopeKind, ScopeRef, SourceRef, SubjectKind, VersionRef
from companion_v2.sources.repositories import SourceRepository


def source_ref(data):
    return SourceRef(data["source_id"], data["owner"], ScopeRef.restore(data["scope"]),
        VersionRef(**data["version"]), datetime.fromisoformat(data["issued_at"]),
        datetime.fromisoformat(data["expires_at"]) if data["expires_at"] else None)


def as_source_ref(item):
    """Accept persisted mappings and already materialized source references."""
    return item if isinstance(item, SourceRef) else source_ref(item)


class PrivateDomainAccess:
    def __init__(self, tasks):
        self.tasks, self.runtime = tasks, tasks.runtime
        self.sources = SourceRepository(self.runtime)

    def check(self, context, action, *, operator=None, at=None):
        at = at or self.tasks.now()
        if (context.scope.kind is not ScopeKind.PRIVATE or context.actor.subject.kind is not SubjectKind.HUMAN
                or context.audience.recipients != (context.actor.subject,)):
            raise TaskError("private_domain_audience", "select one current private participant")
        # A manager remains bound to this participant, but reply revocation must
        # not hide the role's state or calendar from the core administrator.
        if operator is not None:
            require(self.tasks.check_context_binding(context, observed_at=at))
        else:
            require(self.tasks.check_context_access(context, action=action, observed_at=at))
        if operator is None:
            require(self.tasks.identity.authorize(context.actor, scope=context.scope, capability=action, observed_at=at))
        if operator is not None:
            require(self.tasks.policy.scope_access(operator, scope=context.scope, action="role.life.manage",
                                                  observed_at=at, core_only=True))

    def evidence(self, context, sources, *, at=None, limit=8):
        at = at or self.tasks.now()
        if not isinstance(sources, (tuple, list)) or len(sources) > limit:
            raise TaskError("domain_sources_invalid", "source dependency budget exceeded")
        for item in sources:
            ref = as_source_ref(item)
            if not isinstance(ref, SourceRef) or ref.scope != context.scope:
                raise TaskError("domain_source_scope", "source belongs to another conversation")
            require(self.sources.validate_reference_for_audience(ref, recipient=context.actor.subject, observed_at=at))
