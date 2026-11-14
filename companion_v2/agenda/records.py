"""Role-owned plans alongside conversation-owned reports and commitments."""

from datetime import datetime
import json

from companion_v2.execution.models import TaskError, digest
from companion_v2.foundation.refs import RoleOwnerRef
from companion_v2.identity_policy.personas import PersonaDirectory
from companion_v2.storage.codec import scope_from_row
from companion_v2.storage.versioned_records import VersionedRecords, owner_key as conversation_owner_key


def calendar_role_owner(context):
    return RoleOwnerRef(context.scope.installation_id, context.scope.bot_id,
                        context.role_binding.role.subject_id)


class CalendarRecords(VersionedRecords):
    def __init__(self, runtime, identity):
        super().__init__(runtime, "calendar")
        self.identity = identity
        self._legacy_cache = {}

    def role_key(self, context):
        return digest(["calendar-role.v1", calendar_role_owner(context)])

    def conversation_key(self, context):
        return conversation_owner_key(context)

    def _legacy_role_plans(self, context):
        role_key = self.role_key(context)
        if role_key in self._legacy_cache:
            return self._legacy_cache[role_key]
        rows = self.runtime.read_all("""SELECT r.owner_key, r.record_id, r.revision, r.record_json,
                o.installation_id, o.platform, o.scope_kind, o.native_scope_id, o.bot_id,
                o.account_id, o.conversation_id, o.session_id
            FROM calendar_records AS r
            JOIN calendar_history AS h ON h.owner_key = r.owner_key
                AND h.record_id = r.record_id AND h.revision = r.revision
            JOIN operations AS o ON o.operation_id = h.operation_id
            WHERE r.owner_key != ? AND json_extract(r.record_json, '$.kind') = 'role_plan'
            ORDER BY r.record_id""", (role_key,))
        personas = PersonaDirectory(self.runtime)
        matches = []
        for row in rows:
            try:
                scope = scope_from_row(row)
                if (scope.installation_id != context.scope.installation_id
                        or scope.bot_id != context.scope.bot_id):
                    continue
                record = json.loads(row["record_json"])
                if record.get("reminder_id") and not record.get("reminder_context_key"):
                    continue
                binding = self.identity.resolve_role(
                    scope, observed_at=datetime.fromisoformat(record["updated_at"]))
                if not binding.is_success or binding.unwrap().role != context.role_binding.role:
                    continue
                route = personas.route(scope)
                if route is None or route.user is None:
                    continue
                if row["owner_key"] != digest([scope, binding.unwrap().role, route.user]):
                    continue
                matches.append({"owner_key": row["owner_key"], "record": record})
            except (AttributeError, KeyError, TypeError, ValueError):
                continue
        self._legacy_cache[role_key] = tuple(matches)
        return self._legacy_cache[role_key]

    def _move_to_role(self, local_key, role_key, identifier):
        with self.runtime.transaction() as connection:
            connection.execute("""INSERT OR IGNORE INTO calendar_records
                SELECT ?, record_id, revision, updated_at, record_json FROM calendar_records
                WHERE owner_key = ? AND record_id = ?""", (role_key, local_key, identifier))
            connection.execute("""INSERT OR IGNORE INTO calendar_history
                SELECT ?, record_id, revision, operation_id, record_json FROM calendar_history
                WHERE owner_key = ? AND record_id = ?""", (role_key, local_key, identifier))
            connection.execute("""INSERT OR IGNORE INTO calendar_receipts
                SELECT operation_id, ?, record_id, result_json FROM calendar_receipts
                WHERE owner_key = ? AND record_id = ?""", (role_key, local_key, identifier))
            connection.execute("DELETE FROM calendar_history WHERE owner_key = ? AND record_id = ?",
                               (local_key, identifier))
            connection.execute("DELETE FROM calendar_receipts WHERE owner_key = ? AND record_id = ?",
                               (local_key, identifier))
            connection.execute("DELETE FROM calendar_records WHERE owner_key = ? AND record_id = ?",
                               (local_key, identifier))

    def adopt_legacy_role_plans(self, context):
        role_key = self.role_key(context)
        for item in self._legacy_role_plans(context):
            identifier = item["record"]["id"]
            if super().get(context, identifier, storage_key=role_key) is None:
                self._move_to_role(item["owner_key"], role_key, identifier)
        self._legacy_cache.pop(role_key, None)

    def get(self, context, identifier, *, storage_key=None):
        if storage_key is not None:
            return super().get(context, identifier, storage_key=storage_key)
        shared = super().get(context, identifier, storage_key=self.role_key(context))
        if shared is not None:
            return shared
        legacy = next((item["record"] for item in self._legacy_role_plans(context)
                       if item["record"]["id"] == identifier), None)
        return legacy or super().get(context, identifier, storage_key=self.conversation_key(context))

    def list(self, context, *, after_id="", limit=100, kinds=None, active_since=None):
        if type(limit) is not int or not 1 <= limit <= 100 or type(after_id) is not str:
            raise TaskError("record_page_invalid", "invalid record page")
        if kinds is not None:
            kinds = tuple(kinds)
            if not kinds or any(type(kind) is not str or not kind for kind in kinds):
                raise TaskError("record_page_invalid", "invalid record kind filter")
        role_key, local_key = self.role_key(context), self.conversation_key(context)
        shared = super().list(context, after_id=after_id, limit=limit, storage_key=role_key, kinds=kinds,
                              active_since=active_since)
        local = super().list(context, after_id=after_id, limit=limit, storage_key=local_key, kinds=kinds,
                             active_since=active_since)
        kind_set = set(kinds) if kinds is not None else None
        records = {item["id"]: item for item in shared}
        for item in self._legacy_role_plans(context):
            record = item["record"]
            if active_since is not None:
                schedule = record["schedule"]
                if record["state"] == "cancelled" or (schedule["frequency"] == "once" and
                        all(datetime.fromisoformat(value) < active_since for value in
                            (schedule["anchor"], *record["exceptions"].values()) if value)):
                    continue
            if record["id"] > after_id and (kind_set is None or record.get("kind") in kind_set):
                records.setdefault(record["id"], record)
        for record in local:
            records.setdefault(record["id"], record)
        return tuple(records[key] for key in sorted(records)[:limit])

    def shared_role_plans(self, context, *, limit=100):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise TaskError("record_page_invalid", "invalid shared role-plan page")
        role_key = self.role_key(context)
        records = {item["id"]: item for item in super().list(context, limit=limit,
            storage_key=role_key, kinds=("role_plan",))}
        for item in self._legacy_role_plans(context):
            record = item["record"]
            if record["id"]:
                records.setdefault(record["id"], record)
        ordered = tuple(records[key] for key in sorted(records))
        return ordered[:limit], len(ordered) > limit

    def version(self, context, identifier, revision, *, storage_key=None):
        if storage_key is not None:
            return super().version(context, identifier, revision, storage_key=storage_key)
        if super().get(context, identifier, storage_key=self.role_key(context)) is not None:
            return super().version(context, identifier, revision, storage_key=self.role_key(context))
        legacy = next((item["owner_key"] for item in self._legacy_role_plans(context)
                       if item["record"]["id"] == identifier), None)
        for key in (self.role_key(context), legacy, self.conversation_key(context)):
            if key is None:
                continue
            try:
                return super().version(context, identifier, revision, storage_key=key)
            except TaskError as error:
                if error.code != "record_version_unavailable":
                    raise
        raise TaskError("record_version_unavailable", "original record version is unavailable")

    def receipt(self, context, operation_id, *, storage_key=None):
        if storage_key is not None:
            return super().receipt(context, operation_id, storage_key=storage_key)
        keys = (self.role_key(context), *(item["owner_key"] for item in self._legacy_role_plans(context)),
                self.conversation_key(context))
        for key in dict.fromkeys(keys):
            try:
                return super().receipt(context, operation_id, storage_key=key)
            except TaskError as error:
                if error.code != "operation_unavailable":
                    raise
        raise TaskError("operation_unavailable", "original domain operation is unavailable")

    def storage_key_for_change(self, context, identifier, kind):
        self.adopt_legacy_role_plans(context)
        if kind != "role_plan":
            return self.conversation_key(context)
        role_key, local_key = self.role_key(context), self.conversation_key(context)
        current = super().get(context, identifier, storage_key=role_key)
        if current is not None:
            return role_key
        legacy = super().get(context, identifier, storage_key=local_key)
        if legacy is not None and legacy.get("kind") == "role_plan":
            if legacy.get("reminder_id") and not legacy.get("reminder_context_key"):
                return local_key
            self._move_to_role(local_key, role_key, identifier)
        return role_key

    def storage_key_for(self, context, identifier):
        if super().get(context, identifier, storage_key=self.role_key(context)) is not None:
            return self.role_key(context)
        return self.conversation_key(context)
