"""Bounded, source-governed evidence for one role's self-review window.

Self-review only inspects the role's own observable behaviour inside one exact
private conversation. Evidence therefore comes from two owners that already
prove the behaviour happened:

* confirmed replies -- a part with a matching delivery receipt, so the role
  really did say it in this conversation;
* completed life/journal receipts -- an operation that really completed.

Neither source is republished as an independent fact. Each entry carries an
opaque handle so the model can cite what it looked at without ever receiving an
internal identifier, and a later read resolves those handles back to the
original references so current grants and source governance are rechecked.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import json

from companion_v2.execution.models import TaskContext, TaskError, digest, require, stamp
from companion_v2.foundation.refs import ScopeKind, SourceRef, SubjectKind
from companion_v2.memory.activity_receipts import TABLES
from companion_v2.storage.models import OperationState


READ_ACTION = "role.self_review.read"
REPLY_LIMIT = 24
RECEIPT_LIMIT = 8
MAX_EVIDENCE = 24
MAX_STATEMENT_CHARS = 1200
MAX_WINDOW_HOURS = 72


@dataclass(frozen=True)
class ReviewEvidence:
    handle: str
    kind: str
    payload: str


@dataclass(frozen=True)
class ReviewWindow:
    opened_at: datetime
    closed_at: datetime
    evidence: tuple[ReviewEvidence, ...]

    @property
    def material_hash(self):
        return digest([{"handle": item.handle, "kind": item.kind, "payload": item.payload}
                       for item in self.evidence])


def evidence_handles(window):
    return {item.handle for item in window.evidence}


def window_document(window):
    return {"opened_at": stamp(window.opened_at), "closed_at": stamp(window.closed_at),
            "handles": [item.handle for item in window.evidence],
            "kinds": sorted({item.kind for item in window.evidence})}


def _trim(value, maximum=MAX_STATEMENT_CHARS):
    value = value.strip()
    return value if len(value) <= maximum else value[:maximum]


class SelfReviewEvidenceCatalog:
    """The read boundary for one role's own recent behaviour."""

    def __init__(self, runtime, receipts, *, window_hours=24):
        if not 1 <= window_hours <= MAX_WINDOW_HOURS:
            raise ValueError("self-review window must stay inside its bounded range")
        # The conversation owner is attached after construction: the catalog is
        # needed to build the conversation owner in the first place.
        self.runtime, self.receipts = runtime, receipts
        self.messages = None
        self.window = timedelta(hours=window_hours)

    def attach(self, messages):
        if messages.runtime is not self.runtime:
            raise ValueError("self-review evidence must share its conversation runtime")
        self.messages = messages

    def _owner(self):
        if self.messages is None:
            raise TaskError("self_review_owner_unavailable", "the conversation owner is not attached")
        return self.messages

    @staticmethod
    def _authorize(context, at):
        if (context.scope.kind is not ScopeKind.PRIVATE
                or context.audience.recipients != (context.actor.subject,)
                or context.actor.subject.kind is not SubjectKind.HUMAN):
            raise TaskError("self_review_private_audience_required",
                            "self-review needs the current participant's exact private audience")

    def collect(self, context, at):
        """Read the window once; every entry is rechecked against its original receipt."""
        self._authorize(context, at)
        opened_at = at - self.window
        receipts = []
        with self.runtime.read_snapshot():
            self._owner().authorize(context, at)
            replies = tuple(self._reply_rows(context, opened_at, at))
            for handle, stream, operation_id in self._receipt_references(context):
                try:
                    receipts.append(self._receipt(context, handle, stream, operation_id, at))
                except TaskError:
                    continue
        # A confirmed reply is read back through the memory owner, which
        # publishes the quote as a durable source before returning it. That
        # write cannot run inside the read snapshot above, so replies are
        # resolved after the snapshot closes; each one rechecks its own receipt.
        evidence = []
        for row in replies:
            try:
                evidence.append(self._reply(context, row, at))
            except TaskError:
                continue
        return ReviewWindow(opened_at, at, tuple((evidence + receipts)[:MAX_EVIDENCE]))

    def _reply_rows(self, context, opened_at, at):
        rows = self.runtime.read_all("""SELECT h.part_id, h.history_sequence, h.delivered_at, b.context_json
            FROM conversation_sent_history h
            JOIN conversation_parts p ON p.part_id = h.part_id
            JOIN conversation_batches b ON b.batch_id = h.batch_id
            JOIN conversation_delivery_receipts r ON r.part_id = h.part_id AND r.receipt_ref = h.receipt_ref
            WHERE p.state = 'delivered' AND p.delivered_at > ? AND p.delivered_at <= ?
            AND json_extract(b.context_json, '$.actor.subject.subject_id') = ?
            AND json_extract(b.context_json, '$.role_binding.role.subject_id') = ?
            AND json_extract(b.context_json, '$.role_binding.scope.bot_id') = ?
            AND json_extract(b.context_json, '$.audience.scope.account_id') = ?
            ORDER BY h.history_sequence DESC LIMIT ?""",
            (stamp(opened_at), stamp(at), context.actor.subject.subject_id,
             context.role_binding.role.subject_id, context.scope.bot_id,
             context.scope.account_id, REPLY_LIMIT))
        from companion_v2.conversation.native_history import rows as native_rows
        native = tuple({**dict(row), "part_id": row["observation_id"], "native": True}
                       for row in native_rows(self.runtime, context, start=stamp(opened_at), end=stamp(at),
                                              kind="assistant", limit=REPLY_LIMIT))
        return (*rows, *native)

    @staticmethod
    def reply_handle(context, part_id):
        return "reply:" + digest([context.key, part_id])

    @staticmethod
    def receipt_handle(context, stream, operation_id):
        return "receipt:" + digest([context.key, stream, operation_id])

    @staticmethod
    def _original(row):
        return TaskContext.restore(json.loads(row["context_json"]))

    def _confirmed_text(self, row, original):
        """Reuse the memory owner's own delivery proof instead of re-deriving it."""
        owner = getattr(self._owner(), "confirmed_reply_sources", None)
        if owner is None:
            raise TaskError("self_review_reply_owner_unavailable",
                            "the confirmed reply owner is not attached")
        # The owner stamps its reference at delivery time; building one from the
        # review timestamp makes every receipt finally look like another reply.
        reference = owner.reference(row)
        return owner.materialize(original, reference).payload

    def _reply(self, context, row, at):
        if isinstance(row, dict) and row.get("native"):
            from companion_v2.conversation.native_history import body, reference
            item = body(self._owner(), reference(row), context, at)
            return ReviewEvidence(self.reply_handle(context, row["part_id"]), "reply",
                _trim("角色在本会话经 AstrBot 发送返回的一段回复（未获平台送达确认）：" + item["text"]))
        original = self._original(row)
        text = self._confirmed_text(row, original)
        return ReviewEvidence(self.reply_handle(context, row["part_id"]), "reply",
                              _trim("角色在本会话确认送达的一段回复：" + text))

    def _receipt_references(self, context):
        """Completed life/journal operations owned by this role, newest first."""
        owner_key = digest(self.receipts.life.owner(context))
        seen = []
        for stream, table in TABLES.items():
            for row in self.runtime.read_all(f"""SELECT operation_id FROM {table}
                    WHERE owner_key = ? ORDER BY rowid DESC LIMIT ?""",
                    (owner_key, RECEIPT_LIMIT)):
                operation = require(self.receipts.life.life.operations.get(row["operation_id"]))
                if operation.state is not OperationState.COMPLETED or operation.operation.scope != context.scope:
                    continue
                seen.append((self.receipt_handle(context, stream, row["operation_id"]),
                             stream, row["operation_id"]))
        return seen[:RECEIPT_LIMIT]

    def _receipt(self, context, handle, stream, operation_id, at):
        experience = self.receipts.read(context, stream, operation_id, at)
        if experience is None:
            raise TaskError("self_review_receipt_unavailable", "receipt did not record a completion")
        for source in experience.sources:
            if not isinstance(source, SourceRef) or source.scope != context.scope:
                raise TaskError("self_review_source_scope", "receipt evidence belongs to another scope")
        return ReviewEvidence(handle, "receipt", _trim(
            f"角色自己的生活回执：{experience.title}。" + " ".join(experience.statements)))

    def resolve(self, window, context, at):
        """Reproduce exactly the reviewed handles under current grants and sources.

        Raises instead of degrading: a stale review must be discarded rather than
        silently reviewed against different material than the operator saw.
        """
        return self.resolve_handles(evidence_handles(window), window.opened_at, window.closed_at, context, at)

    def resolve_handles(self, handles, opened_at, closed_at, context, at):
        """Recheck a named set of handles inside one closed window.

        Used both for a whole review window and for the subset an accepted item
        actually cites, so accepting one item never depends on unrelated evidence
        that has since been withdrawn.
        """
        self.describe_handles(handles, opened_at, closed_at, context, at)
        return True

    def describe_handles(self, handles, opened_at, closed_at, context, at):
        """Return only cited, currently readable evidence for an authorized reviewer."""
        self._authorize(context, at)
        requested = tuple(handles)
        unresolved = set(requested)
        if not unresolved or len(unresolved) != len(requested):
            raise TaskError("self_review_evidence_unavailable", "this review has no evidence to recheck")
        descriptions = {}
        with self.runtime.read_snapshot():
            self._owner().authorize(context, at)
            replies = tuple(self._reply_rows(context, opened_at, closed_at))
            for handle, stream, operation_id in self._receipt_references(context):
                if handle not in unresolved:
                    continue
                item = self._receipt(context, handle, stream, operation_id, at)
                descriptions[handle] = item.payload
                unresolved.discard(handle)
        # Confirmed replies publish their quote through the memory owner, so
        # they are resolved after the read snapshot (see collect()).
        for row in replies:
            handle = self.reply_handle(context, row["part_id"])
            if handle not in unresolved:
                continue
            item = self._reply(context, row, at)
            descriptions[handle] = item.payload
            unresolved.discard(handle)
        if unresolved:
            raise TaskError("self_review_evidence_changed",
                            "original self-review evidence is no longer available")
        return [descriptions[handle] for handle in requested]

    def catalogue(self, window):
        """Deterministic handle, kind and text for the prompt."""
        return [{"handle": item.handle, "kind": item.kind, "text": item.payload}
                for item in window.evidence]
