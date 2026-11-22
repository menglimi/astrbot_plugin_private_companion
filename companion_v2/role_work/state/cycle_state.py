"""Cycle provenance is independent of subsequent conversation-driven feelings."""

from companion_v2.execution.models import TaskError, digest, encode, require
from companion_v2.foundation.refs import SourceRef, VersionRef
from companion_v2.identity_policy.private_domain import source_ref
from companion_v2.sources.dependencies import link
from companion_v2.sources.models import SourceRecord
from . import cycle
from .ownership import role_owner


class CycleState:
    def __init__(self, service):
        self.service = service

    @staticmethod
    def seed(context):
        return role_owner(context)

    def previous(self, context, record, *, at=None):
        if not record or not record.get("cycle_clock"):
            return None, ()
        # Legacy records lack separate evidence: never invent a more permissive ancestry.
        sources = record.get("cycle_sources", record["sources"])
        try:
            # Carry the one existing clock internally even when this window may
            # not consume its private basis. Projection checks audience separately.
            self.service.evidence.validate(context, sources, at=at, control=True)
        except TaskError:
            return None, ()
        clock = dict(record["cycle_clock"])
        if "decision_origin" not in clock and record["reason"] == "cycle_alignment":
            clock["decision_origin"] = "manual"
        return clock, tuple(source_ref(s) for s in sources)

    def save(self, context, current, assertion, settings, at, *, operation_key, sources, alignment):
        previous, prior_sources = self.previous(context, current, at=at)
        clock = cycle.evolve(previous, assertion, settings, at, alignment=alignment, seed=self.seed(context))
        if clock is None:
            return None, (), False
        if alignment is None and clock == previous:
            return clock, prior_sources, self.service.records.storage(context) == "role" or bool(
                current and current.get("cycle_shared", False))
        # Explicit alignment is its own authenticated basis. Merely carrying a clock or
        # adopting configuration never makes it dependent on today's jokes or feelings.
        changed_decision = previous is None or previous["applicability"] != clock["applicability"]
        dependencies = () if alignment is not None else tuple(dict.fromkeys(
            (*prior_sources, *(sources if changed_decision else ()))))
        self.service.evidence.validate(context, dependencies, at=at, control=True)
        shared = self.service.records.storage(context) == "role" or self.service.evidence.shared(context, dependencies)
        from .service import bound_state_dependencies
        link_dependencies = bound_state_dependencies(self.service.runtime, dependencies, at=at)
        identifier = "role-cycle-" + digest([context.key, operation_key])
        source = SourceRef(identifier, "role.state.cycle", context.scope, VersionRef(identifier, 1), at)
        payload = encode(clock)
        require(self.service.access.sources.publish(SourceRecord(source, context.audience, digest(payload), payload), observed_at=at))
        link(self.service.runtime, source, link_dependencies, at)
        return clock, (source,), shared

    def preview(self, context, settings, at):
        previous, sources = self.previous(context, self.service.records.get(context, "current"), at=at)
        if (not settings["cycle_enabled"] or previous is None
                or self.service.records.storage(context) != "role"
                and not self.service.evidence.visible(context, sources, at=at)):
            return None, ()
        clock = cycle.evolve(previous, None, settings, at, seed=self.seed(context))
        return cycle.project(clock, at, discomfort_enabled=settings["cycle_discomfort_enabled"]), sources
