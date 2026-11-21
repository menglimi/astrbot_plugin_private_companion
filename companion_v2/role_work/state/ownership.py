"""One role body, with window receipts and conservative legacy provenance."""

import json

from companion_v2.execution.models import TaskError, digest
from companion_v2.foundation.refs import RoleOwnerRef
from companion_v2.storage.versioned_records import VersionedRecords, owner_key


def role_owner(context):
    return RoleOwnerRef(context.scope.installation_id, context.scope.bot_id, context.role_binding.role.subject_id)


def role_schedule_key(context):
    return digest(["role-state-maintenance.v1", role_owner(context)])


class RoleStateRecords(VersionedRecords):
    def __init__(self, runtime):
        super().__init__(runtime, "role_state")
        self.legacy = VersionedRecords(runtime, "role_state")

    def owner_key(self, context):
        return digest(["role-body.v1", role_owner(context)])

    def operation_key(self, context):
        # The same dashboard operation key in another window is a different request.
        return owner_key(context)

    def storage(self, context):
        return "role" if super().get(context, "current") else "window"

    def get(self, context, identifier, *, storage_key=None):
        if storage_key is not None:
            return super().get(context, identifier, storage_key=storage_key)
        return super().get(context, identifier) or self.legacy.get(context, identifier)

    def version(self, context, identifier, revision, *, storage=None):
        if storage == "window" or storage is None and self.storage(context) == "window":
            return self.legacy.version(context, identifier, revision)
        return super().version(context, identifier, revision)

    def receipt(self, context, operation_id):
        try:
            return super().receipt(context, operation_id)
        except TaskError as error:
            if error.code != "operation_unavailable":
                raise
            return self.legacy.receipt(context, operation_id)

    def change(self, context, identifier, **options):
        with self.runtime.transaction() as connection:
            if super().get(context, identifier) is None:
                previous = self.legacy.get(context, identifier)
                if previous is not None:
                    # Adoption happens only on an explicit successful write. Other
                    # windows' old rows and all original receipts remain untouched.
                    key, old = self.owner_key(context), owner_key(context)
                    connection.execute("""INSERT INTO role_state_records
                        SELECT ?, record_id, revision, updated_at, record_json FROM role_state_records
                        WHERE owner_key = ? AND record_id = ?""", (key, old, identifier))
                    connection.execute("""INSERT INTO role_state_history
                        SELECT ?, record_id, revision, operation_id, record_json FROM role_state_history
                        WHERE owner_key = ? AND record_id = ?""", (key, old, identifier))
            return super().change(context, identifier, **options)

    def shared_source(self, context, reference):
        """A certificate grants only the body's projection, never raw source text."""
        if reference.owner not in {"role.state", "role.state.cycle"}:
            return False
        from companion_v2.identity_policy.private_domain import source_ref
        current = super().get(context, "current")
        if current is not None and any(
            reference in tuple(source_ref(s) for s in current.get(field, ()))
            for field in ("sources", "cycle_sources")
        ):
            return True
        rows = self.runtime.read_all("""SELECT record_json FROM role_state_history
            WHERE owner_key = ? AND record_id = 'current'
              AND (json_extract(record_json, '$.sources[0].source_id') = ?
                OR json_extract(record_json, '$.cycle_sources[0].source_id') = ?)
            ORDER BY revision DESC LIMIT 1""",
            (self.owner_key(context), reference.source_id, reference.source_id))
        if not rows:
            return False
        record = json.loads(rows[0]["record_json"])
        return any(reference in tuple(source_ref(s) for s in record.get(field, ()))
                   for field in ("sources", "cycle_sources"))
