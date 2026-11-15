"""Small transactional record primitive; authorization belongs to each domain owner."""

import json

from companion_v2.execution.models import TaskError, digest, encode, require, stamp
from companion_v2.foundation.refs import OperationRef, VersionRef
from companion_v2.storage.models import OperationState
from companion_v2.storage.repositories import OperationRepository
from .state_schema import RECORD_TABLES


def owner_key(context):
    # Stable ownership survives binding revisions; access still validates the current binding.
    return digest([context.scope, context.role_binding.role, context.actor.subject])


class VersionedRecords:
    def __init__(self, runtime, domain):
        if domain not in RECORD_TABLES:
            raise ValueError("unregistered record owner")
        self.runtime, self.domain = runtime, domain
        self.operations = OperationRepository(runtime)

    def owner_key(self, context):
        return owner_key(context)

    def operation_key(self, context):
        return self.owner_key(context)

    def get(self, context, identifier, *, storage_key=None):
        row = self.runtime.read_one(
            f"SELECT record_json FROM {self.domain}_records WHERE owner_key = ? AND record_id = ?",
            (storage_key or self.owner_key(context), identifier))
        return json.loads(row["record_json"]) if row else None

    def list(self, context, *, after_id="", limit=100, storage_key=None, kinds=None, active_since=None):
        if type(limit) is not int or not 1 <= limit <= 100 or type(after_id) is not str:
            raise TaskError("record_page_invalid", "invalid record page")
        values, parameters = ["owner_key = ?", "record_id > ?"], [storage_key or self.owner_key(context), after_id]
        if kinds is not None:
            kinds = tuple(kinds)
            if not kinds or any(type(kind) is not str or not kind for kind in kinds):
                raise TaskError("record_page_invalid", "invalid record kind filter")
            values.append("json_extract(record_json, '$.kind') IN (" + ", ".join("?" for _ in kinds) + ")")
            parameters.extend(kinds)
        if active_since is not None:
            # Recurrence and moved one-off events must still go through Schedule.
            values.append("""json_extract(record_json, '$.state') != 'cancelled' AND (
                json_extract(record_json, '$.schedule.frequency') != 'once'
                OR json_extract(record_json, '$.schedule.anchor') >= ?
                OR EXISTS (SELECT 1 FROM json_each(record_json, '$.exceptions') WHERE value >= ?))""")
            parameters.extend((stamp(active_since), stamp(active_since)))
        parameters.append(limit)
        return tuple(json.loads(row["record_json"]) for row in self.runtime.read_all(
            f"SELECT record_json FROM {self.domain}_records WHERE {' AND '.join(values)} ORDER BY record_id LIMIT ?",
            tuple(parameters)))

    def version(self, context, identifier, revision, *, storage_key=None):
        row = self.runtime.read_one(
            f"SELECT record_json FROM {self.domain}_history WHERE owner_key = ? AND record_id = ? AND revision = ?",
            (storage_key or self.owner_key(context), identifier, revision))
        if row is None:
            raise TaskError("record_version_unavailable", "original record version is unavailable")
        return json.loads(row["record_json"])

    def receipt(self, context, operation_id, *, storage_key=None):
        row = self.runtime.read_one(
            f"SELECT result_json FROM {self.domain}_receipts WHERE owner_key = ? AND operation_id = ?",
            (storage_key or self.owner_key(context), operation_id))
        if row is None:
            raise TaskError("operation_unavailable", "original domain operation is unavailable")
        return json.loads(row["result_json"])

    def change(self, context, identifier, *, operation_key, expected_revision, parameters, transform, at,
               storage_key=None):
        if (type(identifier) is not str or not identifier.strip() or len(identifier) > 512
                or type(operation_key) is not str or not operation_key.strip() or len(operation_key) > 512
                or type(expected_revision) is not int or expected_revision < 0):
            raise TaskError("record_command_invalid", "invalid record identity or revision")
        key = storage_key or self.owner_key(context)
        operation_id = self.domain + ":" + digest([self.operation_key(context), operation_key])
        operation = OperationRef(operation_id, self.domain, context.scope, VersionRef(operation_id, 1),
                                 operation_id)
        with self.runtime.transaction() as connection:
            reservation = require(self.operations.reserve(operation,
                parameter_hash=digest([context.key, identifier, expected_revision, parameters]), observed_at=at))
            if reservation.replayed:
                return {**self.receipt(context, operation_id), "replayed": True}
            current = self.get(context, identifier, storage_key=key)
            if (current["revision"] if current else 0) != expected_revision:
                raise TaskError("record_revision_conflict", "record changed; read its current revision")
            if current and current["updated_at"] > stamp(at):
                raise TaskError("record_clock_regression", "record time cannot move backwards")
            record = {**transform(current), "id": identifier, "revision": expected_revision + 1,
                      "updated_at": stamp(at)}
            payload = encode(record)
            if len(payload.encode("utf-8")) > 131072:
                raise TaskError("record_too_large", "record exceeds its storage contract")
            connection.execute(f"""INSERT INTO {self.domain}_records VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(owner_key, record_id) DO UPDATE SET
                revision=excluded.revision, updated_at=excluded.updated_at, record_json=excluded.record_json""",
                (key, identifier, record["revision"], stamp(at), payload))
            connection.execute(f"INSERT INTO {self.domain}_history VALUES (?, ?, ?, ?, ?)",
                               (key, identifier, record["revision"], operation_id, payload))
            # Receipts contain versions, never stale private content.
            receipt = {"operation_id": operation_id, "id": identifier, "revision": record["revision"],
                       "state": record.get("state", "saved"), "replayed": False}
            connection.execute(f"INSERT INTO {self.domain}_receipts VALUES (?, ?, ?, ?)",
                               (operation_id, key, identifier, encode(receipt)))
            require(self.operations.transition(operation_id, expected_state=OperationState.ACCEPTED,
                next_state=OperationState.COMPLETED, observed_at=at))
            return receipt
