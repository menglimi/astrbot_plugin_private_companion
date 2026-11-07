"""Native turn origins for background domains, separate from model telemetry.

These rows describe AstrBot's real run and send callback. They do not create V2
reply batches or platform receipts, and do not run a model or send a message.
"""

from datetime import datetime
import json

from companion_v2.execution.models import TaskContext, TaskError, digest, encode, stamp
from companion_v2.storage.native_conversation_schema import STATEMENTS

from .messages import source_from_document
from .models import InboundConversationEvent
from .reset import source_current


TABLE = "conversation_native_turns"


def turns_available(runtime):
    return runtime.read_one("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (TABLE,)) is not None


def project(row):
    if row is None:
        return None
    return {**dict(row), "batch_id": row["turn_id"], "origin_key": row["context_key"],
            "created_at": row["observed_at"], "execution_path": "astrbot_core",
            "flow_state": "complete" if row["generation_state"] == "completed" else row["generation_state"],
            "state": "ready" if row["generation_state"] == "completed" else row["generation_state"]}


def latest(runtime, context_key):
    if not turns_available(runtime):
        return None
    return project(runtime.read_one("""SELECT * FROM conversation_native_turns WHERE context_key = ?
        ORDER BY observed_at DESC, rowid DESC LIMIT 1""", (context_key,)))


def latest_scope(runtime, scope):
    if not turns_available(runtime):
        return None
    return project(runtime.read_one("""SELECT * FROM conversation_native_turns
        WHERE json_extract(context_json, '$.audience.scope.installation_id') = ?
          AND json_extract(context_json, '$.audience.scope.platform') = ?
          AND json_extract(context_json, '$.audience.scope.kind') = ?
          AND json_extract(context_json, '$.audience.scope.native_scope_id') = ?
          AND json_extract(context_json, '$.audience.scope.bot_id') = ?
          AND json_extract(context_json, '$.audience.scope.account_id') = ?
          AND json_extract(context_json, '$.audience.scope.session_id') = ?
        ORDER BY observed_at DESC, rowid DESC LIMIT 1""", (scope.installation_id, scope.platform,
        scope.kind.value, scope.native_scope_id, scope.bot_id, scope.account_id, scope.session_id)))


def latest_input(runtime, context_key):
    """Newest completed turn for this context that still has an input.

    A text-less native message (image, sticker, video) is admitted as a turn
    with ``input_observation_id = NULL``. Automatic contact must not stay frozen
    on that turn until the user happens to send text again, so it anchors to the
    newest turn that can still prove an authenticated input.
    """
    if not turns_available(runtime):
        return None
    return project(runtime.read_one("""SELECT * FROM conversation_native_turns
        WHERE context_key = ? AND input_observation_id IS NOT NULL
          AND input_observation_id <> ''
          AND generation_state = 'completed' AND trim(role_instruction) <> ''
        ORDER BY observed_at DESC, rowid DESC LIMIT 1""", (context_key,)))


def context_rows(runtime, *, kind=None):
    if not turns_available(runtime):
        return ()
    where = "WHERE json_extract(n.context_json, '$.audience.scope.kind') = ? AND " if kind else "WHERE "
    rows = runtime.read_all("SELECT n.* FROM conversation_native_turns n " + where + """
        NOT EXISTS (SELECT 1 FROM conversation_native_turns newer
            WHERE newer.context_key = n.context_key
            AND (newer.observed_at > n.observed_at OR newer.observed_at = n.observed_at AND newer.rowid > n.rowid))
        ORDER BY n.observed_at DESC, n.rowid DESC""", (kind,) if kind else ())
    return tuple(project(row) for row in rows)


def foreground_busy(runtime, context):
    row = latest_scope(runtime, context.scope)
    return row is not None and row["generation_state"] in ("prepared", "running")


def input_event(messages, row, at):
    from .native_observations import validate_observation

    context = TaskContext.restore(json.loads(row["context_json"]))
    original = messages.runtime.read_one("SELECT * FROM conversation_native_observations WHERE observation_id = ?",
                                         (row["input_observation_id"],))
    if original is None or original["kind"] != "input" or original["turn_id"] != row["turn_id"]:
        raise TaskError("native_turn_input_unavailable", "the native turn has no original authenticated input")
    reference = source_from_document(json.loads(original["source_json"]))
    validate_observation(messages.runtime, original, context, reference)
    if not source_current(messages.runtime, reference):
        raise TaskError("conversation_context_reset", "the native turn precedes this window's reset")
    record = messages._read_source(reference, context, "text", at)
    if record.content_hash != original["content_hash"] or reference.issued_at > at:
        raise TaskError("native_turn_input_changed", "the native input no longer matches its source")
    return InboundConversationEvent(original["event_id"], context.actor.principal, context.scope, reference,
                                    "text", record.content_hash, datetime.fromisoformat(original["observed_at"]))


class NativeConversationTurns:
    def __init__(self, runtime):
        self.runtime = runtime
        with runtime.transaction() as connection:
            for statement in STATEMENTS:
                connection.execute(statement)

    def interrupt_open(self, *, at):
        """Close runs whose host runner disappeared; never retry model or send."""
        with self.runtime.transaction() as connection:
            return connection.execute("""UPDATE conversation_native_turns
                SET generation_state = 'failed', phase_at = ?
                WHERE generation_state IN ('prepared', 'running')""", (stamp(at),)).rowcount

    def admit(self, context, native_input, turn_id, input_reference):
        at = stamp(native_input.at)
        with self.runtime.transaction() as connection:
            existing = connection.execute("SELECT * FROM conversation_native_turns WHERE turn_id = ?", (turn_id,)).fetchone()
            if existing is not None and (existing["context_key"] != context.key
                    or existing["role_instruction"] != native_input.instruction):
                raise TaskError("native_turn_conflict", "the admitted native turn changed")
            connection.execute("""INSERT INTO conversation_native_turns
                (turn_id, context_key, context_json, role_instruction, input_observation_id,
                 observed_at, generation_state, phase_at, last_sent_at)
                VALUES (?, ?, ?, ?, ?, ?, 'prepared', ?, NULL)
                ON CONFLICT(turn_id) DO UPDATE SET input_observation_id = excluded.input_observation_id""",
                (turn_id, context.key, encode(context), native_input.instruction,
                 input_reference.source_id if input_reference is not None else None, at, at))

    def mark(self, prepared, state, *, at):
        if state not in ("prepared", "running", "completed", "failed"):
            raise ValueError("invalid native generation phase")
        context, turn_id = prepared["context"], prepared["turn_id"]
        observed = getattr(prepared.get("native_input"), "at", at)
        with self.runtime.transaction() as connection:
            current = connection.execute("SELECT * FROM conversation_native_turns WHERE turn_id = ?", (turn_id,)).fetchone()
            if current is not None and (current["context_key"] != context.key
                    or current["role_instruction"] != prepared["instruction"]):
                raise TaskError("native_turn_conflict", "the admitted native turn changed")
            if current is not None and (current["phase_at"] > stamp(at)
                    or current["generation_state"] in ("completed", "failed") and state in ("prepared", "running")):
                return
            connection.execute("""INSERT INTO conversation_native_turns
                (turn_id, context_key, context_json, role_instruction, input_observation_id,
                 observed_at, generation_state, phase_at, last_sent_at)
                VALUES (?, ?, ?, ?, NULL, ?, ?, ?, NULL)
                ON CONFLICT(turn_id) DO UPDATE SET generation_state = excluded.generation_state, phase_at = excluded.phase_at""",
                (turn_id, context.key, encode(context), prepared["instruction"], stamp(observed), state, stamp(at)))

    def sent(self, turn_id, observed_at, *, observation_id=None):
        with self.runtime.transaction() as connection:
            if observation_id is not None:
                connection.execute("""INSERT INTO conversation_native_sends(observation_id, turn_id, sent_at)
                    VALUES (?, ?, ?) ON CONFLICT(observation_id) DO NOTHING""", (observation_id, turn_id, stamp(observed_at)))
            connection.execute("""UPDATE conversation_native_turns SET last_sent_at = MAX(COALESCE(last_sent_at, ''), ?),
                generation_state = CASE WHEN generation_state = 'prepared' THEN 'completed' ELSE generation_state END
                WHERE turn_id = ?""", (stamp(observed_at), turn_id))

    def latest(self, context_key):
        return latest(self.runtime, context_key)

    def contexts(self, *, kind=None):
        return context_rows(self.runtime, kind=kind)
