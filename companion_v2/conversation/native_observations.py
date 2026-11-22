"""Governed raw native chat sources without a V2 generation or delivery batch.

``send_returned`` records AstrBot's send callback returning. It does not create
a platform delivery receipt or prove a recipient saw the text. Generated output
stays ``output_observed`` until that callback, and is excluded from recall.
"""

from datetime import datetime, timedelta
import json
import logging

from companion_v2.execution.models import TaskContext, TaskError, digest, encode, require, stamp
from companion_v2.foundation.refs import ScopeKind, SourceRef, VersionRef
from companion_v2.memory.models import owner_key
from companion_v2.sources.models import SourceRecord
from companion_v2.sources.privacy import prepare_message
from companion_v2.sources.repositories import SourceRepository
from companion_v2.storage.native_conversation_schema import (
    TABLE,
    ensure_native_conversation_schema,
)
from companion_v2.storage.native_dedup import (
    resolve_context_json,
    resolve_row_context,
    resolve_rows_context,
    store_context_json,
)
from companion_v2.storage.runtime import table_exists

from .recalls import CONSUMER as RECALL_CONSUMER, MessageRecall


INPUT_OWNER = "conversation.native_input"
ASSISTANT_OWNER = "conversation.native_assistant"
TOGETHER_INPUT_OWNER = "conversation.together_input"
TOGETHER_ASSISTANT_OWNER = "conversation.together_assistant"
TOGETHER_OWNERS = frozenset({TOGETHER_INPUT_OWNER, TOGETHER_ASSISTANT_OWNER})
REALITY_ASSISTANT_OWNER = "conversation.reality_assistant"
NATIVE_OWNERS = frozenset({INPUT_OWNER, ASSISTANT_OWNER, REALITY_ASSISTANT_OWNER}) | TOGETHER_OWNERS
NATIVE_FEEDS = frozenset({"native_human", "native_reply"})
logger = logging.getLogger(__name__)


def conversation_owner(context):
    return json.dumps([context.scope.installation_id, context.scope.bot_id,
                       context.role_binding.role.subject_id, context.actor.subject.subject_id],
                      ensure_ascii=False, separators=(",", ":"))


def observations_available(runtime):
    return table_exists(runtime, TABLE)


def inbound_event(row, runtime=None):
    """Adapt an observed input for domain owners, without creating a reply batch."""
    from .messages import source_from_document
    from .models import InboundConversationEvent

    original = TaskContext.restore(json.loads(resolve_context_json(runtime, row["context_json"])))
    return InboundConversationEvent(row["event_id"], original.actor.principal, original.scope,
        source_from_document(json.loads(row["source_json"])), "text", row["content_hash"],
        datetime.fromisoformat(row["observed_at"]))


def input_rows_for_context(runtime, context, *, start=None, end=None, after_sequence=0, limit=512):
    """Original member inputs in the exact current private window or group binding."""
    if not observations_available(runtime):
        return ()
    clauses = ["n.kind = 'input'", "n.delivery_state = 'input_observed'"]
    values = []
    if context.scope.kind is ScopeKind.PRIVATE:
        clauses.append("n.context_key = ?")
        values.append(context.key)
    elif context.scope.kind is ScopeKind.GROUP:
        clauses.append("n.role_binding_key = ?")
        values.append(encode(context.role_binding))
    else:
        return ()
    if start is not None:
        clauses.append("n.observed_at >= ?")
        values.append(start)
    if end is not None:
        clauses.append("n.observed_at <= ?")
        values.append(end)
    if after_sequence > 0:
        clauses.append("s.rowid > ?")
        values.append(after_sequence)
    values.append(limit)
    rows = runtime.read_all("""SELECT n.*, s.rowid AS input_sequence
        FROM conversation_native_observations n JOIN sources s
        ON s.source_id = json_extract(n.source_json, '$.source_id')
          AND s.lineage_id = json_extract(n.source_json, '$.version.lineage_id')
          AND s.source_revision = json_extract(n.source_json, '$.version.revision')
        WHERE """ + " AND ".join(clauses) + " ORDER BY n.observed_at DESC, s.rowid DESC LIMIT ?", tuple(values))
    return resolve_rows_context(runtime, rows)


def observation_row(runtime, identifier):
    if not observations_available(runtime):
        return None
    row = runtime.read_one("SELECT * FROM conversation_native_observations WHERE observation_id = ?", (identifier,))
    return resolve_row_context(runtime, row)


def source_row(runtime, reference):
    if reference.owner not in NATIVE_OWNERS or not observations_available(runtime):
        return None
    row = runtime.read_one("""SELECT * FROM conversation_native_observations
        WHERE json_extract(source_json, '$.source_id') = ?""", (reference.source_id,))
    return resolve_row_context(runtime, row)


def validate_observation(runtime, row, context, reference, *, original=None, recalled_keys=None):
    from companion_v2.memory.codec import source

    if original is None:
        original = TaskContext.restore(json.loads(resolve_context_json(runtime, row["context_json"])))
    if (source(json.loads(row["source_json"])) != reference
            or original.scope != reference.scope or owner_key(original) != owner_key(context)
            or original.scope.kind is not context.scope.kind
            or original.role_binding.role != context.role_binding.role
            or original.actor.subject != context.actor.subject
            or row["memory_owner"] != owner_key(original)
            or row["context_key"] != original.key):
        raise TaskError("memory_source_scope", "native observation belongs to another authenticated context")
    if row["kind"] == "input":
        marker = MessageRecall.from_scope(original.scope, row["event_id"],
                                           original.actor.principal.native_account_id)
        if recalled_keys is not None:
            is_recalled = marker.key in recalled_keys
        else:
            is_recalled = runtime.read_one(
                "SELECT 1 FROM inbox WHERE consumer = ? AND event_id = ?",
                (RECALL_CONSUMER, marker.key),
            ) is not None
        if is_recalled:
            raise TaskError("message_withdrawn", "the native input was withdrawn")
        return original.actor.subject.subject_id, "human_message"
    return (original.role_binding.role.subject_id,
            "bot_message" if row["delivery_state"] == "send_returned" else "observation")


def rows_for_context(runtime, context, *, start=None, end=None, limit=None):
    if context.scope.kind is not ScopeKind.PRIVATE or not observations_available(runtime):
        return ()
    has_sends = table_exists(runtime, "conversation_native_sends")
    where = ["n.memory_owner = ?", "n.scope_kind = 'private'",
             "n.delivery_state IN ('input_observed', 'send_returned')"]
    values = [owner_key(context)]
    if start is not None:
        where.append("n.effective_at >= ?")
        values.append(start)
    if end is not None:
        where.append("n.effective_at < ?")
        values.append(end)
    query = ("SELECT n.*, s.sent_at FROM conversation_native_observations n LEFT JOIN conversation_native_sends s USING(observation_id) "
             if has_sends else "SELECT n.*, NULL AS sent_at FROM conversation_native_observations n ")
    query += "WHERE " + " AND ".join(where) + " ORDER BY n.effective_at DESC, n.observation_id DESC"
    if limit is not None:
        query += " LIMIT ?"
        values.append(limit)
    return resolve_rows_context(runtime, runtime.read_all(query, tuple(values)))


class NativeConversationObservations:
    def __init__(self, messages):
        self.messages, self.runtime, self.tasks = messages, messages.runtime, messages.tasks
        self.sources = SourceRepository(self.runtime)
        ensure_native_conversation_schema(self.runtime)

    def _awareness_enabled(self, context):
        owner = getattr(self.messages, "cross_window", None)
        try:
            return owner is not None and owner.enabled(context)
        except Exception:
            return False

    def _peer_count(self, context, at):
        """Distinct other speakers in a short group window; metadata only."""
        if context.scope.kind is not ScopeKind.GROUP:
            return 0
        owner = getattr(self.messages, "cross_window", None)
        try:
            minutes = owner.peer_minutes(context) if owner is not None else 5
        except Exception:
            minutes = 5
        try:
            rows = input_rows_for_context(self.runtime, context, start=stamp(at - timedelta(minutes=minutes)),
                                          end=stamp(at), limit=64)
        except Exception:
            return 0
        current = context.actor.principal.native_account_id
        accounts = set()
        for row in rows:
            try:
                original = TaskContext.restore(json.loads(row["context_json"]))
            except Exception:
                continue
            account = original.actor.principal.native_account_id
            if account and account != current:
                accounts.add(account)
        return min(64, len(accounts))

    def capture_input(self, context, native_input, *, turn_id):
        if (context.scope != native_input.route.scope or context.actor.principal != native_input.principal
                or context.role_binding.role != native_input.role or context.audience != native_input.recipients):
            raise TaskError("native_observation_context_changed", "native input must keep its verified identity and route")
        self.messages.recalls.check(context.scope, native_input.event_id,
                                   context.actor.principal.native_account_id)
        from .native_turns import NativeConversationTurns
        if not native_input.text.strip():
            NativeConversationTurns(self.runtime).admit(context, native_input, turn_id, None)
            return None
        identifier = "core-input-" + digest([context.scope, context.role_binding.role,
                                             context.actor.subject, native_input.event_id])
        reference = self._record(context, identifier, turn_id, native_input.event_id, native_input.text,
                                 native_input.at, kind="input", state="input_observed",
                                 awareness_enabled=self._awareness_enabled(context),
                                 peer_count=self._peer_count(context, native_input.at),
                                 quote_sender_ids=tuple(
                                     quote.sender_id for quote in getattr(native_input, "quotes", ())))
        NativeConversationTurns(self.runtime).admit(context, native_input, turn_id, reference)
        return reference

    def capture_output(self, context, *, turn_id, event_id, text, at, state="output_observed"):
        if state not in ("output_observed", "send_returned"):
            raise ValueError("native assistant observation must preserve its real send stage")
        if type(text) is not str or not text.strip():
            return None
        identifier = "core-output-" + digest([turn_id, text])
        return self._record(context, identifier, turn_id, event_id, text, at, kind="assistant", state=state,
                            awareness_enabled=self._awareness_enabled(context))

    def capture_together(self, context, *, observation_id, event_id, text, at, kind):
        """A real room input or successful WebSocket send, with its own source owner."""
        if kind not in ("input", "assistant") or context.scope.kind is not ScopeKind.PRIVATE:
            raise ValueError("together observations require one verified private participant")
        if type(text) is not str or not text.strip():
            return None
        owner = TOGETHER_INPUT_OWNER if kind == "input" else TOGETHER_ASSISTANT_OWNER
        return self._record(context, observation_id, event_id, event_id, text, at, kind=kind,
            state="input_observed" if kind == "input" else "send_returned", source_owner=owner)

    def capture_reality(self, context, *, observation_id, event_id, text, at):
        """Actual local playback or chat send; never a fabricated core reply receipt."""
        if context.scope.kind is not ScopeKind.PRIVATE:
            raise ValueError("reality observations require their original private participant")
        if type(text) is not str or not text.strip():
            return None
        return self._record(context, observation_id, event_id, event_id, text, at, kind="assistant",
            state="send_returned", source_owner=REALITY_ASSISTANT_OWNER)

    def _record(self, context, identifier, turn_id, event_id, text, at, *, kind, state,
                source_owner=None, awareness_enabled=False, peer_count=0, quote_sender_ids=()):
        require(self.tasks.check_context_access(context, action="conversation.reply", observed_at=self.tasks.now()))
        with self.runtime.transaction() as connection:
            existing = connection.execute("SELECT * FROM conversation_native_observations WHERE observation_id = ?",
                                          (identifier,)).fetchone()
            issued_at = at if existing is None else datetime.fromisoformat(existing["observed_at"])
            owner = source_owner or (INPUT_OWNER if kind == "input" else ASSISTANT_OWNER)
            core_reply = owner in (INPUT_OWNER, ASSISTANT_OWNER)
            reference = SourceRef(identifier, owner,
                                  context.scope, VersionRef(identifier, 1), issued_at)
            record = prepare_message(self.runtime, SourceRecord(reference, context.audience, digest(text), text))
            require(self.sources.publish(record, observed_at=self.tasks.now()))
            if existing is not None:
                from companion_v2.memory.codec import source
                if (source(json.loads(existing["source_json"])) != reference
                        or existing["content_hash"] != record.content_hash or existing["kind"] != kind):
                    raise TaskError("native_observation_conflict", "the original native observation changed")
                if existing["delivery_state"] == state or existing["delivery_state"] == "send_returned":
                    if core_reply and kind == "assistant" and state == "send_returned":
                        from .native_turns import NativeConversationTurns
                        NativeConversationTurns(self.runtime).sent(turn_id, at, observation_id=reference.source_id)
                    return reference
                connection.execute("UPDATE conversation_native_observations SET delivery_state = ? WHERE observation_id = ?",
                                   (state, identifier))
            else:
                stamped_issued = stamp(issued_at)
                scope_kind = context.scope.kind.value
                role_binding_key = encode(context.role_binding)
                stored_ctx, ctx_hash = store_context_json(
                    connection,
                    encode(context),
                    context_key=context.key,
                    scope_kind=scope_kind,
                    role_binding_key=role_binding_key,
                    created_at=stamped_issued,
                )
                connection.execute("""INSERT INTO conversation_native_observations
                    (observation_id, context_key, memory_owner, conversation_owner, turn_id, event_id,
                     kind, delivery_state, context_json, source_json, content_hash, observed_at,
                     effective_at, scope_kind, role_binding_key, context_hash)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (identifier, context.key, owner_key(context), conversation_owner(context), turn_id, event_id,
                     kind, state, stored_ctx, encode(reference), record.content_hash, stamped_issued,
                     stamped_issued, scope_kind, role_binding_key, ctx_hash))
            if context.scope.kind is ScopeKind.PRIVATE and state in ("input_observed", "send_returned"):
                connection.execute("""INSERT INTO memory_recall_changes(owner, kind, object_id, token)
                    VALUES (?, ?, ?, ?)""", (conversation_owner(context),
                    "native_human" if kind == "input" else "native_reply", identifier,
                    digest([reference, state, record.content_hash])))
            if kind == "input" and existing is None:
                self._observe_input(connection, context, inbound_event({
                    "event_id": event_id, "context_json": encode(context), "source_json": encode(reference),
                    "content_hash": record.content_hash, "observed_at": stamp(issued_at)}), record.payload,
                    awareness_enabled=awareness_enabled, peer_count=peer_count,
                    quote_sender_ids=quote_sender_ids)
            elif core_reply and kind == "assistant" and state == "send_returned":
                from .native_turns import NativeConversationTurns
                NativeConversationTurns(self.runtime).sent(turn_id, at, observation_id=reference.source_id)
                owner = getattr(self.messages, "expression_style_context", None)
                if owner is not None:
                    self._optional_observer(connection, "expression_sent", lambda: owner.confirm_delivery(
                        connection, batch_id=turn_id, context=context, delivered_at=at))
                awareness = getattr(self.messages, "cross_window", None)
                if awareness_enabled and awareness is not None:
                    self._optional_observer(connection, "cross_window_reply", lambda: awareness.record_reply(
                        connection, context, at, text=record.payload,
                        event_key=reference.source_id, enabled=True))
            return reference

    @staticmethod
    def _optional_observer(connection, name, observe):
        """A derived owner cannot discard an otherwise readable native source."""
        savepoint = "native_observer_" + name
        connection.execute("SAVEPOINT " + savepoint)
        try:
            observe()
            connection.execute("RELEASE SAVEPOINT " + savepoint)
        except Exception as error:
            connection.execute("ROLLBACK TO SAVEPOINT " + savepoint)
            connection.execute("RELEASE SAVEPOINT " + savepoint)
            logger.warning("[CompanionV2] 核心聊天衍生观察降级 owner=%s error_type=%s", name, type(error).__name__)

    def _observe_input(self, connection, context, event, text, *, awareness_enabled=False, peer_count=0,
                       quote_sender_ids=()):
        for name in ("acquaintance", "habits"):
            owner = getattr(self.messages, name, None)
            if owner is not None:
                def observe(owner=owner):
                    observer = owner.intake_observer(context)
                    if observer is not None:
                        observer(context, event, connection)
                self._optional_observer(connection, name, observe)
        expression = getattr(self.messages, "expression_style_context", None)
        if expression is not None:
            self._optional_observer(connection, "expression_feedback", lambda: expression.consume_feedback(
                connection, context=context, text=text, received_at=event.received_at))
        if context.scope.kind is ScopeKind.GROUP:
            def safety():
                from companion_v2.memory.group.access import owner_key as group_owner
                from companion_v2.memory.group.hits import evaluate_message, persist_hits
                persist_hits(connection, evaluate_message(owner_key=group_owner(context),
                    member_id=event.principal.native_account_id, message_id=event.event_id,
                    message_text=text, occurred_at=event.received_at, direction="inbound"), created_at=self.tasks.now())
            self._optional_observer(connection, "group_safety", safety)
            network = getattr(self.messages, "group_network", None)
            if network is not None:
                self._optional_observer(connection, "group_network", lambda: network.observe(
                    context, event, connection, text=text))
        awareness = getattr(self.messages, "cross_window", None)
        if awareness_enabled and awareness is not None:
            self._optional_observer(connection, "cross_window", lambda: awareness.record_input(
                connection, context, event.received_at, text, event.event_id, enabled=True,
                source=event.source, peer_count=peer_count, quote_sender_ids=quote_sender_ids))
