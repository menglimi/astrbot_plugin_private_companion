"""Durable delivery for registered domain owners, using the shared task supervisor.

Domain validation stays with the source owner. A queued envelope is immutable;
only trusted receipts can establish delivery, including after cancellation.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
import json
import logging
from types import MappingProxyType

from companion_v2.execution.models import (
    Lane, SideEffect, TaskContext, TaskDefinition, TaskError, TaskRegistration, TaskResult,
    TaskSpec, digest, encode, require, stamp,
)
from companion_v2.execution.service import outcome
from companion_v2.foundation.refs import OperationRef, VersionRef, normalize_utc
from companion_v2.identity_policy.models import _opaque
from companion_v2.storage.models import OperationState
from companion_v2.storage.repositories import OperationRepository

from .models import DeliveryEnvelope, DeliveryReceipt
from .media import ImageDeliveryContent, ImageDeliveryEnvelope, payload_hash
from .plan import DeliveryContent


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeliveryRequest:
    owner: str
    logical_key: str
    context: TaskContext
    source_ref: str
    payload_hash: str
    not_before: datetime
    deadline_at: datetime
    sequence: int = 0
    part_count: int = 1

    def __post_init__(self):
        for name in ("owner", "logical_key", "source_ref", "payload_hash"):
            _opaque(getattr(self, name), field=name)
        if not isinstance(self.context, TaskContext):
            raise ValueError("delivery requires an original context")
        if (type(self.sequence) is not int or type(self.part_count) is not int
                or not 0 <= self.sequence < self.part_count <= 64):
            raise ValueError("delivery sequence must identify one bounded part")
        for name in ("not_before", "deadline_at"):
            object.__setattr__(self, name, normalize_utc(getattr(self, name), field=name))
        if self.not_before >= self.deadline_at:
            raise ValueError("delivery deadline must follow its earliest start")

    @property
    def delivery_id(self):
        identity = ([self.owner, self.logical_key] if self.part_count == 1
                    else [self.owner, self.logical_key, self.sequence])
        return "outbound-" + digest(identity)

    @classmethod
    def restore(cls, payload):
        data = json.loads(payload)
        return cls(**{**data, "context": TaskContext.restore(data["context"]),
                      "not_before": datetime.fromisoformat(data["not_before"]),
                      "deadline_at": datetime.fromisoformat(data["deadline_at"])})


@dataclass(frozen=True)
class DeliveryOwner:
    name: str
    action: str
    read: object
    history_read: object = None

    def __post_init__(self):
        _opaque(self.name, field="delivery owner")
        _opaque(self.action, field="delivery action")
        if not callable(self.read):
            raise ValueError("delivery owners must revalidate and read their original source")
        if self.history_read is not None and not callable(self.history_read):
            raise ValueError("historical text requires its original owner's read boundary")

    @property
    def kind(self):
        return "delivery." + self.name


class OutboundDelivery:
    def __init__(self, runtime, sender, owners, *, concurrency=2):
        owners = tuple(owners)
        if not owners or len({o.name for o in owners}) != len(owners):
            raise ValueError("delivery owners must be explicit and unique")
        self.runtime, self.sender = runtime, sender
        self.owners = MappingProxyType({o.name: o for o in owners})
        self.on_delivered = None
        self.on_receipt = None
        self.private_account_router = None
        self.tasks = None
        self._connection_available = None
        self._pending_cursor = ("", "")
        self._waiting_for_connections = False
        self.operations = OperationRepository(runtime)
        self.registrations = tuple(TaskRegistration(TaskDefinition(o.kind, "delivery", o.action,
            Lane.BACKGROUND, side_effect=SideEffect.MESSAGE_DELIVERY, binding=sender.binding,
            timeout_seconds=60, max_attempts=3 if o.name == "proactive.contact" else 1, concurrency=concurrency), self.validate) for o in owners)

    def attach(self, tasks):
        if self.tasks is not None or tasks.runtime is not self.runtime:
            raise ValueError("delivery must attach once to its original supervisor")
        self.tasks = tasks

    def bind_connection_gate(self, available):
        """Keep unstarted reminders available for recovery within their deadline."""
        self._connection_available = available

    def contains(self, delivery_id):
        return self.runtime.read_one("SELECT 1 FROM background_deliveries WHERE delivery_id = ?", (delivery_id,)) is not None

    def _row(self, delivery_id):
        row = self.runtime.read_one("SELECT * FROM background_deliveries WHERE delivery_id = ?", (delivery_id,))
        if row is None:
            raise TaskError("delivery_unavailable", "original delivery is unavailable")
        return row

    @outcome
    def validate(self, spec, at):
        row = self._row(spec.request_ref)
        request = DeliveryRequest.restore(row["request_json"])
        owner = self.owners.get(request.owner)
        if (owner is None or spec.kind != owner.kind or spec.task_id != row["task_id"]
                or spec.context.key != row["origin_key"] or spec.context != request.context
                or spec.request_ref != request.delivery_id or spec.idempotency_key != request.delivery_id
                or spec.not_before != request.not_before or spec.deadline_at != request.deadline_at
                or row["sender_json"] != encode(self.sender.binding) or row["state"] == "cancelled"):
            raise TaskError("delivery_owner_changed", "delivery must retain its original owner, source and sender")
        self._content(request, at)
        return True

    def _content(self, request, at):
        content = require(self.owners[request.owner].read(request, at))
        try:
            if isinstance(content, str):
                content = DeliveryContent(content)
            elif isinstance(content, ImageDeliveryContent):
                content = DeliveryContent(content.text, tuple(content.images),
                                          allow_cross_scope_images=content.allow_cross_scope_images)
        except (TypeError, ValueError) as error:
            raise TaskError("delivery_payload_changed", "source returned an unsupported delivery payload") from error
        if not isinstance(content, DeliveryContent):
            raise TaskError("delivery_payload_changed", "source returned an unsupported delivery payload")
        # Voice and poke are best-effort presentation side effects: their owner
        # re-read them from its own sealed record, and they never enter the
        # fingerprint that authorized this payload.
        text, images = content.text, content.images
        if (not isinstance(text, str) or (not text.strip() and not images) or len(text) > 65536
                or (images and not self.sender.supports_images)
                or payload_hash(text, images) != request.payload_hash):
            raise TaskError("delivery_payload_changed", "source no longer provides the original bounded payload")
        if images:
            ImageDeliveryEnvelope(request.delivery_id, request.delivery_id, request.context, request.sequence,
                                  text, request.payload_hash, self.sender.binding, images=images,
                                  voice=content.voice, poke=content.poke,
                                  allow_cross_scope_images=content.allow_cross_scope_images)
        return content

    @outcome
    def queue(self, request: DeliveryRequest):
        with self.runtime.transaction() as connection:
            require(self.tasks.check_generation())
            owner = self.owners.get(request.owner)
            if owner is None:
                raise TaskError("delivery_owner_unregistered", "delivery owner is not installed")
            identifier = request.delivery_id
            prior = connection.execute("SELECT * FROM background_deliveries WHERE delivery_id = ?", (identifier,)).fetchone()
            if prior is not None:
                try:
                    same_request = DeliveryRequest.restore(prior["request_json"]) == request
                except (TypeError, ValueError, KeyError):
                    same_request = False
                if not same_request or prior["sender_json"] != encode(self.sender.binding):
                    raise TaskError("delivery_idempotency_conflict", "an original delivery cannot change its payload or authority")
                return require(self.tasks.read(request.context, prior["task_id"]))
            task_id = "send-" + identifier
            connection.execute("""INSERT INTO background_deliveries
                (delivery_id, owner, origin_key, request_json, payload_hash, sender_json, task_id, state)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')""",
                (identifier, request.owner, request.context.key, encode(request), request.payload_hash,
                 encode(self.sender.binding), task_id))
            return require(self.tasks.submit(TaskSpec(task_id, request.context, owner.kind, identifier,
                identifier, request.not_before, request.deadline_at)))

    def _dispatch(self, run):
        if run.supervisor is not self.tasks:
            raise ValueError("delivery must use its original supervisor")
        with self.runtime.transaction() as connection:
            run.validate()
            row = self._row(run.task.spec.request_ref)
            if row["state"] != "pending" or row["operation_id"] is not None:
                raise TaskError("delivery_already_dispatched", "an original envelope must never be resubmitted")
            request = DeliveryRequest.restore(row["request_json"])
            content = self._content(request, self.tasks.now())
            text, images = content.text, content.images
            run.begin_effect()
            operation = OperationRef("dispatch-" + row["delivery_id"], "delivery", request.context.scope,
                VersionRef(row["delivery_id"], 1), row["delivery_id"])
            require(self.operations.reserve(operation, parameter_hash=digest([
                request.context.key, row["payload_hash"], request.source_ref, self.sender.binding]), observed_at=self.tasks.now()))
            run.record_operation(operation.operation_id)
            connection.execute("""UPDATE background_deliveries SET state = 'dispatched', operation_id = ?,
                dispatched_at = ? WHERE delivery_id = ?""", (operation.operation_id, stamp(self.tasks.now()), row["delivery_id"]))
            envelope = ImageDeliveryEnvelope if images else DeliveryEnvelope
            return envelope(row["delivery_id"], operation.operation_id, request.context, request.sequence,
                text, row["payload_hash"], self.sender.binding, run.validate,
                voice=content.voice, poke=content.poke,
                **({"images": images, "allow_cross_scope_images": content.allow_cross_scope_images}
                   if images else {}))

    def _operation_state(self, row, state):
        current = require(self.operations.get(row["operation_id"]))
        if current.state is not state:
            require(self.operations.transition(current.operation.operation_id, expected_state=current.state,
                next_state=state, observed_at=self.tasks.now()))

    def _unknown(self, identifier):
        with self.runtime.transaction() as connection:
            changed = connection.execute("UPDATE background_deliveries SET state = 'unknown' WHERE delivery_id = ? AND state = 'dispatched'", (identifier,))
            if changed.rowcount:
                self._operation_state(self._row(identifier), OperationState.UNKNOWN)

    async def deliver(self, run):
        if self.private_account_router is not None:
            request = DeliveryRequest.restore(self._row(run.task.spec.request_ref)["request_json"])
            if not self.private_account_router.ready(request, delivery_id=run.task.spec.request_ref):
                # No sender was entered and no effect begun: use the supervisor's
                # established bounded backoff/deadline, keeping this exact outbox.
                return TaskResult("failed", retryable=True)
        envelope = self._dispatch(run)
        try:
            if self.private_account_router is not None:
                request = DeliveryRequest.restore(self._row(envelope.part_id)["request_json"])
                receipt = await self.private_account_router.send(envelope, request, self.sender)
            else:
                receipt = await self.sender.send(envelope)
            if not isinstance(receipt, DeliveryReceipt) or (receipt.part_id, receipt.operation_id) != (envelope.part_id, envelope.operation_id):
                raise TaskError("delivery_receipt_mismatch", "sender returned no receipt for this dispatch")
            require(self.record_receipt(receipt))
        except asyncio.CancelledError:
            self._unknown(envelope.part_id)
            raise
        except Exception as error:
            logger.warning("V2 outbound receipt could not be recorded delivery=%s error_type=%s code=%s",
                           envelope.part_id, type(error).__name__, getattr(error, "code", "delivery_receipt_failed"))
            self._unknown(envelope.part_id)
        return self._result(self._row(envelope.part_id))

    @outcome
    def record_receipt(self, receipt: DeliveryReceipt):
        with self.runtime.transaction() as connection:
            row = self._row(receipt.part_id)
            if self.private_account_router is not None:
                receipt = self.private_account_router.normalize_receipt(receipt)
            if (row["operation_id"] != receipt.operation_id or row["origin_key"] != receipt.origin_key
                    or row["payload_hash"] != receipt.payload_hash or row["sender_json"] != encode(receipt.sender)
                    or row["dispatched_at"] is None):
                raise TaskError("delivery_receipt_mismatch", "receipt does not belong to this original dispatch")
            if receipt.delivered_at is not None and not datetime.fromisoformat(row["dispatched_at"]) <= receipt.delivered_at <= self.tasks.now():
                raise TaskError("delivery_receipt_time", "confirmation time must be within the original dispatch interval")
            prior = connection.execute("SELECT receipt_json FROM background_delivery_receipts WHERE delivery_id = ? AND receipt_ref = ?",
                (receipt.part_id, receipt.receipt_ref)).fetchone()
            if prior is not None:
                if prior["receipt_json"] != encode(receipt):
                    raise TaskError("delivery_receipt_conflict", "receipt identity cannot change its original outcome")
                state = row["state"]
                request = None
            else:
                request = DeliveryRequest.restore(row["request_json"])
                state = "failed" if receipt.status == "not_sent" else receipt.status
                if (row["state"] == "failed" and state not in ("failed", "unknown")
                        or row["state"] == "delivered" and state == "failed"
                        or row["platform_ref"] is not None and receipt.platform_ref not in (None, row["platform_ref"])
                        or row["state"] == "delivered" and state == "delivered" and stamp(receipt.delivered_at) != row["delivered_at"]):
                    raise TaskError("delivery_receipt_conflict", "confirmed original delivery outcome is immutable")
                connection.execute("INSERT INTO background_delivery_receipts VALUES (?, ?, ?, ?)",
                    (receipt.part_id, receipt.receipt_ref, encode(receipt), stamp(self.tasks.now())))
                if row["state"] in ("delivered", "failed") or row["state"] == "accepted" and state == "unknown":
                    state = row["state"]
                else:
                    connection.execute("""UPDATE background_deliveries SET state = ?, receipt_ref = ?,
                        platform_ref = COALESCE(?, platform_ref), delivered_at = ? WHERE delivery_id = ?""",
                        (state, receipt.receipt_ref, receipt.platform_ref,
                         stamp(receipt.delivered_at) if receipt.delivered_at else None, receipt.part_id))
                    self._operation_state(row, OperationState.COMPLETED if state in ("delivered", "failed") else
                        OperationState.UNKNOWN if state == "unknown" else OperationState.ACCEPTED)
                if self.on_receipt is not None:
                    self.on_receipt(request, receipt)
        if state == "delivered" and self.on_delivered is not None:
            self.on_delivered(receipt.part_id)
        return state

    @staticmethod
    def _result(row):
        if row["state"] == "delivered":
            return TaskResult("succeeded", "committed", result_ref=row["delivery_id"],
                receipt_ref=row["receipt_ref"], operation_id=row["operation_id"])
        if row["state"] == "failed":
            return TaskResult("failed", "not_committed", receipt_ref=row["receipt_ref"], operation_id=row["operation_id"])
        return TaskResult("uncertain", "unknown", operation_id=row["operation_id"])

    @outcome
    def reconcile_task(self, context, delivery_id):
        with self.runtime.transaction():
            row = self._row(delivery_id)
            task = require(self.tasks.read(context, row["task_id"]))
            result = self._result(row)
            if result.status == "uncertain":
                raise TaskError("delivery_unresolved", "there is no confirmed result to reconcile")
            if task.state == result.status:
                return task
            return require(self.tasks.reconcile(context, task.task_id, expected_revision=task.revision, result=result))

    @outcome
    def recover_dispatches(self, *, limit=32):
        self._limit(limit)
        with self.runtime.transaction() as connection:
            require(self.tasks.check_generation())
            rows = connection.execute("""SELECT d.delivery_id FROM background_deliveries d
                JOIN execution_tasks t ON t.task_id = d.task_id
                WHERE d.state = 'dispatched' AND t.state = 'uncertain' ORDER BY d.delivery_id LIMIT ?""", (limit,)).fetchall()
            for row in rows:
                self._unknown(row["delivery_id"])
            return tuple(row["delivery_id"] for row in rows)

    def pending(self, *, limit, excluded=()):
        # Called only by the trusted coordinator after generation validation.
        self._limit(limit)
        if len(excluded) > 32:
            raise ValueError("delivery exclusions must be bounded")
        exclusions = "" if not excluded else " AND d.task_id NOT IN (" + ",".join("?" for _ in excluded) + ")"
        scan = 32 if self._connection_available is not None else limit
        cursor = self._pending_cursor if self._connection_available is not None else ("", "")
        rows = self.runtime.read_all("""SELECT d.request_json, d.task_id, d.delivery_id,
            t.next_run_at, t.deadline_at FROM background_deliveries d
            JOIN execution_tasks t ON t.task_id = d.task_id WHERE d.state = 'pending'
            AND t.state IN ('created', 'retry_scheduled') AND t.next_run_at <= ?
            AND (t.next_run_at, d.delivery_id) > (?, ?)
            AND (SELECT COUNT(*) FROM execution_attempts a WHERE a.slot_held = 1 AND a.kind = t.kind)
                < json_extract(t.record_json, '$.definition.concurrency')
            AND (SELECT COUNT(*) FROM execution_attempts a WHERE a.slot_held = 1 AND a.kind = t.kind
                 AND a.scope_key = json_extract(t.record_json, '$.scope_key'))
                < json_extract(t.record_json, '$.definition.per_scope_concurrency')""" + exclusions +
            " ORDER BY t.next_run_at, d.delivery_id LIMIT ?", (stamp(self.tasks.now()), *cursor, *excluded, scan))
        selected = []
        self._waiting_for_connections = False
        for row in rows:
            self._pending_cursor = row["next_run_at"], row["delivery_id"]
            request = DeliveryRequest.restore(row["request_json"])
            if (self._connection_available is not None and request.owner == "agenda.reminder"
                    and request.deadline_at > self.tasks.now()
                    and not self._connection_available(request.context)):
                self._waiting_for_connections = True
                continue
            selected.append((request.context, row["task_id"]))
            if len(selected) >= limit:
                break
        else:
            if len(rows) < scan:
                self._pending_cursor = ("", "")
        return tuple(selected)

    def next_wake(self, *, excluded=()):
        """Earliest queued delivery that can use its original kind/scope slot."""
        if len(excluded) > 32:
            raise ValueError("delivery exclusions must be bounded")
        exclusions = "" if not excluded else " AND d.task_id NOT IN (" + ",".join("?" for _ in excluded) + ")"
        row = self.runtime.read_one("""SELECT MIN(t.next_run_at) AS next_at FROM background_deliveries d
            JOIN execution_tasks t ON t.task_id = d.task_id WHERE d.state = 'pending'
            AND t.state IN ('created', 'retry_scheduled')
            AND (SELECT COUNT(*) FROM execution_attempts a WHERE a.slot_held = 1 AND a.lane = 'background') < ?
            AND (SELECT COUNT(*) FROM execution_attempts a WHERE a.slot_held = 1 AND a.kind = t.kind)
                < json_extract(t.record_json, '$.definition.concurrency')
            AND (SELECT COUNT(*) FROM execution_attempts a WHERE a.slot_held = 1 AND a.kind = t.kind
                 AND a.scope_key = json_extract(t.record_json, '$.scope_key'))
                < json_extract(t.record_json, '$.definition.per_scope_concurrency')""" + exclusions,
            (self.tasks.limits.background_slots, *excluded))
        next_at = datetime.fromisoformat(row["next_at"]) if row and row["next_at"] else None
        if next_at is not None and self._waiting_for_connections:
            return max(next_at, self.tasks.now() + timedelta(seconds=1))
        return next_at

    def cancel_unstarted(self, task_id, *, reason):
        with self.runtime.transaction() as connection:
            # Keep the coordinator's actual cause in the task ledger instead of a
            # generic shutdown label; the row is only queued, never running.
            task = require(self.tasks.discard_queued(task_id, reason="owner_unavailable", code=reason))
            if task.state == "cancelled":
                connection.execute("UPDATE background_deliveries SET state = 'cancelled', reason = ? WHERE task_id = ? AND state = 'pending'",
                    (reason, task_id))

    def settle(self, *, limit=32):
        self._limit(limit)
        with self.runtime.transaction() as connection:
            require(self.tasks.check_generation())
            rows = connection.execute("""SELECT d.delivery_id, t.record_json FROM background_deliveries d
                JOIN execution_tasks t ON t.task_id = d.task_id WHERE d.state = 'pending'
                AND t.state IN ('succeeded', 'failed', 'uncertain', 'cancelled', 'expired', 'dead_letter')
                ORDER BY d.delivery_id LIMIT ?""", (limit,)).fetchall()
            for row in rows:
                connection.execute("UPDATE background_deliveries SET state = 'cancelled', reason = ? WHERE delivery_id = ?",
                    (json.loads(row["record_json"])["reason"], row["delivery_id"]))
            return len(rows)

    @staticmethod
    def _limit(limit):
        if type(limit) is not int or not 1 <= limit <= 32:
            raise ValueError("delivery scans must contain between one and 32 rows")
