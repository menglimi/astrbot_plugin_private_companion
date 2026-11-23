"""Native turn origins for background domains, separate from model telemetry.

These rows describe AstrBot's real run and send callback. They do not create V2
reply batches or platform receipts, and do not run a model or send a message.
"""

from datetime import datetime
import json

from companion_v2.execution.models import TaskContext, TaskError, digest, encode, stamp
from companion_v2.storage.native_conversation_schema import (
    OPEN_TURN_STATES,
    ensure_native_conversation_schema,
)
from companion_v2.storage.native_dedup import (
    matching_context_hashes,
    resolve_context_json,
    resolve_role_instruction,
    resolve_rows_context,
    role_instruction_matches,
    store_context_json,
    store_role_instruction,
)
from companion_v2.storage.runtime import table_exists

from .messages import source_from_document
from .models import InboundConversationEvent
from .reset import source_current


TABLE = "conversation_native_turns"


def turns_available(runtime):
    return table_exists(runtime, TABLE)


def project(row, runtime=None):
    if row is None:
        return None
    data = dict(row)
    if "context_json" in data:
        data["context_json"] = resolve_context_json(runtime, data["context_json"])
    if "role_instruction" in data:
        data["role_instruction"] = resolve_role_instruction(runtime, data["role_instruction"])
    return {**data, "batch_id": row["turn_id"], "origin_key": row["context_key"],
            "created_at": row["observed_at"], "execution_path": "astrbot_core",
            "flow_state": "complete" if row["generation_state"] == "completed" else row["generation_state"],
            "state": "ready" if row["generation_state"] == "completed" else row["generation_state"]}


def latest(runtime, context_key):
    if not turns_available(runtime):
        return None
    return project(runtime.read_one("""SELECT * FROM conversation_native_turns WHERE context_key = ?
        ORDER BY observed_at DESC, rowid DESC LIMIT 1""", (context_key,)), runtime=runtime)


def latest_scope(runtime, scope):
    if not turns_available(runtime):
        return None
    scope_params = (
        scope.installation_id,
        scope.platform,
        scope.kind.value,
        scope.native_scope_id,
        scope.bot_id,
        scope.account_id,
        scope.session_id,
    )
    hashes = matching_context_hashes(
        runtime,
        """json_extract(context_json, '$.audience.scope.installation_id') = ?
           AND json_extract(context_json, '$.audience.scope.platform') = ?
           AND json_extract(context_json, '$.audience.scope.kind') = ?
           AND json_extract(context_json, '$.audience.scope.native_scope_id') = ?
           AND json_extract(context_json, '$.audience.scope.bot_id') = ?
           AND json_extract(context_json, '$.audience.scope.account_id') = ?
           AND json_extract(context_json, '$.audience.scope.session_id') = ?""",
        scope_params,
    )
    if hashes:
        best_row = None
        for start in range(0, len(hashes), 400):
            chunk = hashes[start:start + 400]
            marks = ",".join("?" for _ in chunk)
            candidate = runtime.read_one(
                f"""SELECT rowid AS _rid, * FROM conversation_native_turns
                    WHERE context_hash IN ({marks})
                    ORDER BY observed_at DESC, rowid DESC LIMIT 1""",
                chunk,
            )
            if candidate is not None and (
                best_row is None
                or (candidate["observed_at"], candidate["_rid"]) > (best_row["observed_at"], best_row["_rid"])
            ):
                best_row = candidate
        if best_row is not None:
            data = dict(best_row)
            data.pop("_rid", None)
            return project(data, runtime=runtime)
    return project(runtime.read_one("""SELECT * FROM conversation_native_turns
        WHERE json_extract(context_json, '$.audience.scope.installation_id') = ?
          AND json_extract(context_json, '$.audience.scope.platform') = ?
          AND json_extract(context_json, '$.audience.scope.kind') = ?
          AND json_extract(context_json, '$.audience.scope.native_scope_id') = ?
          AND json_extract(context_json, '$.audience.scope.bot_id') = ?
          AND json_extract(context_json, '$.audience.scope.account_id') = ?
          AND json_extract(context_json, '$.audience.scope.session_id') = ?
        ORDER BY observed_at DESC, rowid DESC LIMIT 1""", scope_params), runtime=runtime)


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
        ORDER BY observed_at DESC, rowid DESC LIMIT 1""", (context_key,)), runtime=runtime)


def context_rows(runtime, *, kind=None, open_only=False):
    """The newest turn of every context, optionally only while it is still open.

    ``open_only`` exists because a caller that acts only on in-flight turns must
    not pay for a latest-per-context answer over the whole table: restricting the
    outer scan to the open phases leaves a handful of rows, and
    ``conversation_native_open_turn_idx`` serves that filter without reading a
    finished turn. The predicate is the same question in both spellings — a row is
    the newest of its context exactly when no newer row shares its ``context_key``
    — so filtering first and filtering after the probe return the same turns.

    The phases are written as literals on purpose: SQLite only applies a partial
    index when the statement provably implies the index predicate, and a bound
    parameter cannot be proven. ``OPEN_TURN_STATES`` is the single source for both
    spellings.
    """
    if not turns_available(runtime):
        return ()
    if open_only:
        phases = ", ".join(f"'{state}'" for state in OPEN_TURN_STATES)
        where = f"WHERE n.generation_state IN ({phases}) AND "
        parameters: tuple[object, ...] = ()
        if kind:
            where += "n.scope_kind = ? AND "
            parameters = (kind,)
        rows = runtime.read_all("SELECT n.* FROM conversation_native_turns n " + where + """
            NOT EXISTS (SELECT 1 FROM conversation_native_turns newer
                WHERE newer.context_key = n.context_key
                AND (newer.observed_at, newer.rowid) > (n.observed_at, n.rowid))
            ORDER BY n.observed_at DESC, n.rowid DESC""", parameters)
        resolved = resolve_rows_context(runtime, rows, resolve_role=True)
        return tuple(project(row, runtime=runtime) for row in resolved)
    # Full latest-turn-per-context reads (e.g. expression review over groups)
    # jump across distinct context_keys via a loose index scan on
    # conversation_native_turn_context_recent_idx instead of scanning the full
    # table and its large role_instruction/context_json blobs.
    kind_filter = ""
    parameters = ()
    if kind:
        kind_filter = " AND n.scope_kind = ?"
        parameters = (kind,)
    rows = runtime.read_all("""WITH RECURSIVE keys(k) AS (
            SELECT MIN(context_key) FROM conversation_native_turns
            UNION ALL
            SELECT (SELECT MIN(t.context_key) FROM conversation_native_turns t WHERE t.context_key > keys.k)
            FROM keys WHERE keys.k IS NOT NULL
        )
        SELECT n.* FROM conversation_native_turns n
        WHERE n.rowid IN (
            SELECT (SELECT t.rowid FROM conversation_native_turns t
                    WHERE t.context_key = keys.k
                    ORDER BY t.observed_at DESC, t.rowid DESC LIMIT 1)
            FROM keys WHERE keys.k IS NOT NULL
        )""" + kind_filter + " ORDER BY n.observed_at DESC, n.rowid DESC", parameters)
    resolved = resolve_rows_context(runtime, rows, resolve_role=True)
    return tuple(project(row, runtime=runtime) for row in resolved)


def latest_role_contexts(runtime, *, kind, limit=8):
    """Newest ``context_json`` per distinct role binding of one scope kind.

    Uses a loose index scan over the materialized covering index
    ``idx_native_turns_kind_role_obs(scope_kind, role_binding_key, observed_at)``
    so group and private discovery never scan table pages or large
    ``role_instruction`` blobs, and never shadow ``conversation_native_open_turn_idx``.
    """
    if not turns_available(runtime) or limit <= 0:
        return ()
    rows = runtime.read_all("""WITH RECURSIVE roles(rb) AS (
            SELECT MIN(role_binding_key)
            FROM conversation_native_turns
            WHERE scope_kind = ? AND role_binding_key > ''
            UNION ALL
            SELECT (
                SELECT MIN(t.role_binding_key)
                FROM conversation_native_turns t
                WHERE t.scope_kind = ?
                  AND t.role_binding_key > roles.rb
            )
            FROM roles WHERE roles.rb IS NOT NULL
        ),
        top_roles(rid, obs) AS (
            SELECT (
                SELECT t.rowid FROM conversation_native_turns t
                WHERE t.scope_kind = ?
                  AND t.role_binding_key = roles.rb
                ORDER BY t.observed_at DESC, t.rowid DESC LIMIT 1
            ) AS rid,
            (
                SELECT t.observed_at FROM conversation_native_turns t
                WHERE t.scope_kind = ?
                  AND t.role_binding_key = roles.rb
                ORDER BY t.observed_at DESC, t.rowid DESC LIMIT 1
            ) AS obs
            FROM roles WHERE roles.rb IS NOT NULL
            ORDER BY obs DESC, rid DESC LIMIT ?
        )
        SELECT n.context_json
        FROM top_roles l
        JOIN conversation_native_turns n ON n.rowid = l.rid
        ORDER BY l.obs DESC, l.rid DESC""", (kind, kind, kind, kind, limit))
    return resolve_rows_context(runtime, rows)


def foreground_busy(runtime, context):
    row = latest_scope(runtime, context.scope)
    return row is not None and row["generation_state"] in ("prepared", "running")


def input_event(messages, row, at):
    from .native_observations import validate_observation

    context = TaskContext.restore(json.loads(resolve_context_json(messages.runtime, row["context_json"])))
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
        ensure_native_conversation_schema(runtime)

    def interrupt_open(self, *, at):
        """Close runs whose host runner disappeared; never retry model or send."""
        with self.runtime.transaction() as connection:
            return connection.execute("""UPDATE conversation_native_turns
                SET generation_state = 'failed', phase_at = ?
                WHERE generation_state IN ('prepared', 'running')""", (stamp(at),)).rowcount

    def admit(self, context, native_input, turn_id, input_reference):
        at = stamp(native_input.at)
        scope_kind = context.scope.kind.value
        role_binding_key = encode(context.role_binding)
        raw_context_json = encode(context)
        with self.runtime.transaction() as connection:
            existing = connection.execute("SELECT * FROM conversation_native_turns WHERE turn_id = ?", (turn_id,)).fetchone()
            if existing is not None and (existing["context_key"] != context.key
                    or not role_instruction_matches(connection, existing["role_instruction"], native_input.instruction)):
                raise TaskError("native_turn_conflict", "the admitted native turn changed")
            stored_ctx, ctx_hash = store_context_json(
                connection,
                raw_context_json,
                context_key=context.key,
                scope_kind=scope_kind,
                role_binding_key=role_binding_key,
                created_at=at,
            )
            stored_role = store_role_instruction(connection, native_input.instruction, created_at=at)
            connection.execute("""INSERT INTO conversation_native_turns
                (turn_id, context_key, context_json, role_instruction, input_observation_id,
                 observed_at, generation_state, phase_at, last_sent_at, scope_kind, role_binding_key, context_hash)
                VALUES (?, ?, ?, ?, ?, ?, 'prepared', ?, NULL, ?, ?, ?)
                ON CONFLICT(turn_id) DO UPDATE SET input_observation_id = excluded.input_observation_id""",
                (turn_id, context.key, stored_ctx, stored_role,
                 input_reference.source_id if input_reference is not None else None, at, at,
                 scope_kind, role_binding_key, ctx_hash))

    def mark(self, prepared, state, *, at):
        if state not in ("prepared", "running", "completed", "failed"):
            raise ValueError("invalid native generation phase")
        context, turn_id = prepared["context"], prepared["turn_id"]
        observed = getattr(prepared.get("native_input"), "at", at)
        stamped_observed = stamp(observed)
        stamped_at = stamp(at)
        scope_kind = context.scope.kind.value
        role_binding_key = encode(context.role_binding)
        raw_context_json = encode(context)
        with self.runtime.transaction() as connection:
            current = connection.execute("SELECT * FROM conversation_native_turns WHERE turn_id = ?", (turn_id,)).fetchone()
            if current is not None and (current["context_key"] != context.key
                    or not role_instruction_matches(connection, current["role_instruction"], prepared["instruction"])):
                raise TaskError("native_turn_conflict", "the admitted native turn changed")
            if current is not None and (current["phase_at"] > stamped_at
                    or current["generation_state"] in ("completed", "failed") and state in ("prepared", "running")):
                return
            stored_ctx, ctx_hash = store_context_json(
                connection,
                raw_context_json,
                context_key=context.key,
                scope_kind=scope_kind,
                role_binding_key=role_binding_key,
                created_at=stamped_observed,
            )
            stored_role = store_role_instruction(connection, prepared["instruction"], created_at=stamped_observed)
            connection.execute("""INSERT INTO conversation_native_turns
                (turn_id, context_key, context_json, role_instruction, input_observation_id,
                 observed_at, generation_state, phase_at, last_sent_at, scope_kind, role_binding_key, context_hash)
                VALUES (?, ?, ?, ?, NULL, ?, ?, ?, NULL, ?, ?, ?)
                ON CONFLICT(turn_id) DO UPDATE SET generation_state = excluded.generation_state, phase_at = excluded.phase_at""",
                (turn_id, context.key, stored_ctx, stored_role, stamped_observed, state, stamped_at,
                 scope_kind, role_binding_key, ctx_hash))

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

    def contexts(self, *, kind=None, open_only=False):
        return context_rows(self.runtime, kind=kind, open_only=open_only)
