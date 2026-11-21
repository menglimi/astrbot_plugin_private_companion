"""Governance of a role's shared simulation and its private input ancestry."""

from companion_v2.execution.models import TaskError, require
from companion_v2.identity_policy.private_domain import source_ref
from companion_v2.sources.dependencies import is_unguarded_checked, key
from companion_v2.storage.codec import decode_timestamp, scope_from_row
from companion_v2.foundation.refs import SourceRef, VersionRef


class StateEvidence:
    def __init__(self, service):
        self.service = service

    def shared(self, context, sources):
        return all(self.service.records.shared_source(context, source_ref(s) if isinstance(s, dict) else s)
                   for s in sources)

    def validate(self, context, sources, *, at=None, control=False, control_validated=False):
        at = at or self.service.tasks.now()
        repository = self.service.access.sources
        pending = [source_ref(s) if isinstance(s, dict) else s for s in sources]
        root_keys = {key(reference) for reference in pending} if control_validated else ()
        seen = set()
        while pending:
            reference = pending.pop()
            ref_key = key(reference)
            if ref_key in seen:
                continue
            seen.add(ref_key)
            if not (control_validated and ref_key in root_keys
                    and is_unguarded_checked(self.service.runtime, reference, at)):
                require(repository.validate_reference(reference, observed_at=at))
            from companion_v2.conversation.native_observations import NATIVE_OWNERS, source_row, validate_observation
            if reference.owner in NATIVE_OWNERS:
                original = source_row(self.service.runtime, reference)
                if original is None:
                    raise TaskError("state_source_unavailable", "the native input observation is unavailable")
                validate_observation(self.service.runtime, original, context, reference)
            if control or self.service.records.shared_source(context, reference):
                continue
            if reference.scope != context.scope:
                raise TaskError("state_source_private", "this body description retains its original private audience")
            require(repository.validate_reference_for_audience(reference, recipient=context.actor.subject, observed_at=at))
            rows = self.service.runtime.read_all("""SELECT s.* FROM source_dependencies d JOIN sources s
                ON s.source_id = d.input_id AND s.lineage_id = d.input_lineage AND s.source_revision = d.input_revision
                WHERE d.dependent_id = ? AND d.dependent_lineage = ? AND d.dependent_revision = ?""", ref_key)
            pending.extend(SourceRef(r["source_id"], r["owner"], scope_from_row(r),
                VersionRef(r["lineage_id"], r["source_revision"]), decode_timestamp(r["issued_at"]),
                decode_timestamp(r["expires_at"]) if r["expires_at"] else None) for r in rows)

    def visible(self, context, sources, *, at=None, control_validated=False):
        try:
            self.validate(context, sources, at=at, control_validated=control_validated)
            return True
        except TaskError:
            return False
