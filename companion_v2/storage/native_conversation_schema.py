"""Optional native conversation observations, independent of V2 reply batches."""

from __future__ import annotations

from companion_v2.storage.native_dedup import (
    DEDUP_INDEX_STATEMENTS,
    DEDUP_TABLE_STATEMENTS,
    ensure_dedup_tables,
)

# The two phases in which an admitted turn is still using its window. They appear
# as literals below and in the query that reads them, because SQLite only applies
# a partial index when it can prove the statement implies the index predicate, and
# a bound parameter cannot be proven. Keep the three spellings identical.
OPEN_TURN_STATES = ("prepared", "running")

TABLE = "conversation_native_observations"
SCHEMA_KEY = "native_conversation.v4"

TABLE_STATEMENTS = (
    *DEDUP_TABLE_STATEMENTS,
    """CREATE TABLE IF NOT EXISTS conversation_native_observations (
        observation_id TEXT PRIMARY KEY,
        context_key TEXT NOT NULL, memory_owner TEXT NOT NULL, conversation_owner TEXT NOT NULL,
        turn_id TEXT NOT NULL, event_id TEXT NOT NULL,
        kind TEXT NOT NULL CHECK(kind IN ('input', 'assistant')),
        delivery_state TEXT NOT NULL CHECK(delivery_state IN ('input_observed', 'output_observed', 'send_returned')),
        context_json TEXT NOT NULL, source_json TEXT NOT NULL,
        content_hash TEXT NOT NULL, observed_at TEXT NOT NULL,
        effective_at TEXT NOT NULL DEFAULT '',
        scope_kind TEXT NOT NULL DEFAULT '',
        role_binding_key TEXT NOT NULL DEFAULT '',
        context_hash TEXT NOT NULL DEFAULT ''
    )""",
    """CREATE TABLE IF NOT EXISTS conversation_native_turns (
        turn_id TEXT PRIMARY KEY, context_key TEXT NOT NULL, context_json TEXT NOT NULL,
        role_instruction TEXT NOT NULL, input_observation_id TEXT,
        observed_at TEXT NOT NULL, generation_state TEXT NOT NULL
            CHECK(generation_state IN ('prepared', 'running', 'completed', 'failed', 'absorbed')),
        phase_at TEXT NOT NULL, last_sent_at TEXT,
        scope_kind TEXT NOT NULL DEFAULT '',
        role_binding_key TEXT NOT NULL DEFAULT '',
        context_hash TEXT NOT NULL DEFAULT ''
    )""",
    """CREATE TABLE IF NOT EXISTS conversation_native_sends (
        observation_id TEXT PRIMARY KEY, turn_id TEXT NOT NULL, sent_at TEXT NOT NULL
    )""",
)

INDEX_STATEMENTS = (
    "CREATE INDEX IF NOT EXISTS conversation_native_owner_idx ON conversation_native_observations(memory_owner, observed_at, observation_id)",
    "CREATE INDEX IF NOT EXISTS conversation_native_input_context_idx ON conversation_native_observations(context_key, observed_at DESC, observation_id) WHERE kind = 'input' AND delivery_state = 'input_observed'",
    "CREATE INDEX IF NOT EXISTS conversation_native_input_role_idx ON conversation_native_observations(json_extract(context_json, '$.role_binding'), observed_at DESC, observation_id) WHERE kind = 'input' AND delivery_state = 'input_observed'",
    # Retained for compatibility with queries ordering by observed_at directly.
    "CREATE INDEX IF NOT EXISTS conversation_native_obs_context_delivery_idx "
    "ON conversation_native_observations(context_key, observed_at) "
    "WHERE delivery_state IN ('input_observed', 'send_returned')",
    # ``private_scope.private_people`` / ``role_private_contexts`` scan recent
    # inputs for one role/bot/installation ordered by observed_at DESC, rowid DESC.
    "CREATE INDEX IF NOT EXISTS conversation_native_obs_role_input_recent_idx "
    "ON conversation_native_observations("
    "json_extract(context_json, '$.role_binding.role.subject_id'), "
    "json_extract(context_json, '$.role_binding.scope.bot_id'), "
    "json_extract(context_json, '$.role_binding.scope.installation_id'), "
    "observed_at) WHERE kind = 'input'",
    "CREATE INDEX IF NOT EXISTS conversation_native_source_idx ON conversation_native_observations(json_extract(source_json, '$.source_id'))",
    # ``recalls.accept`` looks up native input observations by platform event_id.
    "CREATE INDEX IF NOT EXISTS idx_native_obs_event_input "
    "ON conversation_native_observations(event_id) WHERE kind = 'input'",
    "CREATE INDEX IF NOT EXISTS conversation_native_turn_context_idx ON conversation_native_turns(context_key, observed_at DESC, turn_id)",
    # Ascending (context_key, observed_at) has implicit trailing rowid ASC, so
    # backwards scans satisfy ORDER BY observed_at DESC, rowid DESC with no temp
    # B-tree and power both loose-index-scan latest-per-context reads and O(log N)
    # row-value NOT EXISTS probes without shadowing conversation_native_open_turn_idx.
    "CREATE INDEX IF NOT EXISTS conversation_native_turn_context_recent_idx "
    "ON conversation_native_turns(context_key, observed_at)",
    # Latest-turn-per-context probes must prove no newer row exists for the same
    # context, so their cost is the whole table unless the caller can cut the
    # candidate set first. In-flight turns are the per-pass case (presence asks
    # which private windows are still typing on every drive pass), so the open
    # rows get their own index and a finished turn is never read for that answer.
    # The leading expression is the audience kind: callers that also narrow to one
    # scope kind are served by the same entry, which is why a plain kind index is
    # deliberately absent — it would win the planner and lose the state filter.
    # The leading column is the materialized scope_kind so both legacy inline
    # rows and deduplicated ``{"_ctx_ref":...}`` rows use this partial index
    # without evaluating json_extract on every open-turn probe.
    "CREATE INDEX IF NOT EXISTS conversation_native_open_turn_idx ON conversation_native_turns("
    "scope_kind, context_key, observed_at) "
    "WHERE generation_state IN ('prepared', 'running')",
    # ``latest_scope`` probes seven scope fields and then orders by observed_at.
    # Without this index the probe is a full table scan per call, and presence
    # calls it once per active window on every pass.
    "CREATE INDEX IF NOT EXISTS conversation_native_scope_idx ON conversation_native_turns("
    "json_extract(context_json, '$.audience.scope.installation_id'), "
    "json_extract(context_json, '$.audience.scope.platform'), "
    "json_extract(context_json, '$.audience.scope.kind'), "
    "json_extract(context_json, '$.audience.scope.native_scope_id'), "
    "json_extract(context_json, '$.audience.scope.bot_id'), "
    "json_extract(context_json, '$.audience.scope.account_id'), "
    "json_extract(context_json, '$.audience.scope.session_id'), observed_at DESC)",
    *(
        f"""CREATE INDEX IF NOT EXISTS {table}_private_participant_idx ON {table}(
            json_extract(context_json, '$.role_binding.role.subject_id'),
            json_extract(context_json, '$.role_binding.scope.bot_id'),
            json_extract(context_json, '$.role_binding.scope.installation_id'),
            json_extract(context_json, '$.audience.scope.kind'),
            json_extract(context_json, '$.actor.subject.subject_id'))"""
        for table in ("conversation_batches", "conversation_native_turns",
                      "conversation_native_observations")
    ),
    # ``private_scope.private_people`` / ``role_private_contexts`` query
    # conversation_batches by (role, bot, installation) ORDER BY rowid DESC.
    """CREATE INDEX IF NOT EXISTS conversation_batches_role_recent_idx ON conversation_batches(
        json_extract(context_json, '$.role_binding.role.subject_id'),
        json_extract(context_json, '$.role_binding.scope.bot_id'),
        json_extract(context_json, '$.role_binding.scope.installation_id'))""",
    "CREATE INDEX IF NOT EXISTS idx_conversation_batches_group_role "
    "ON conversation_batches(json_extract(context_json, '$.role_binding')) "
    "WHERE json_extract(context_json, '$.audience.scope.kind') = 'group'",
    # ``scope_family._windows`` resolves both group and private scope families
    # across conversation_native_turns and conversation_batches ORDER BY rowid DESC.
    *(
        f"""CREATE INDEX IF NOT EXISTS {table}_scope_family_idx ON {table}(
            json_extract(context_json, '$.role_binding.scope.installation_id'),
            json_extract(context_json, '$.role_binding.scope.platform'),
            json_extract(context_json, '$.role_binding.scope.bot_id'),
            json_extract(context_json, '$.role_binding.scope.account_id'),
            json_extract(context_json, '$.role_binding.scope.native_scope_id'),
            json_extract(context_json, '$.audience.scope.kind'))"""
        for table in ("conversation_native_turns", "conversation_batches")
    ),
)

MATERIALIZED_INDEX_STATEMENTS = (
    *DEDUP_INDEX_STATEMENTS,
    # Materialized covering index for ``native_turns.latest_role_contexts``:
    # ascending observed_at with implicit trailing rowid ASC enables exact
    # backwards scans for ORDER BY observed_at DESC, rowid DESC with zero table
    # page reads and zero temp B-tree, without shadowing conversation_native_open_turn_idx.
    "CREATE INDEX IF NOT EXISTS idx_native_turns_kind_role_obs "
    "ON conversation_native_turns(scope_kind, role_binding_key, observed_at)",
    # Fast index seek on deduplicated context hashes for latest_scope & scope_family.
    "CREATE INDEX IF NOT EXISTS idx_native_turns_context_hash "
    "ON conversation_native_turns(context_hash, observed_at)",
    # Materialized effective_at = COALESCE(sent_at, observed_at) lets
    # ``native_history.rows`` scan backwards in exact (effective_at DESC, rowid DESC)
    # order with zero temp B-tree even when LEFT JOINing conversation_native_sends.
    "CREATE INDEX IF NOT EXISTS idx_native_obs_ctx_effective "
    "ON conversation_native_observations(context_key, effective_at) "
    "WHERE delivery_state IN ('input_observed', 'send_returned')",
    # Materialized (memory_owner, scope_kind, effective_at, observation_id) serves
    # ``native_observations.rows_for_context`` backwards with zero json_extract or temp B-tree.
    "CREATE INDEX IF NOT EXISTS idx_native_obs_owner_kind_effective "
    "ON conversation_native_observations(memory_owner, scope_kind, effective_at, observation_id) "
    "WHERE delivery_state IN ('input_observed', 'send_returned')",
    # Materialized role_binding_key serves group input lookups on both legacy and deduped rows.
    "CREATE INDEX IF NOT EXISTS idx_native_obs_input_role_key "
    "ON conversation_native_observations(role_binding_key, observed_at DESC, observation_id) "
    "WHERE kind = 'input' AND delivery_state = 'input_observed'",
    # Fast index seek on deduplicated context hashes for private_scope & recalls/reset.
    "CREATE INDEX IF NOT EXISTS idx_native_obs_context_hash "
    "ON conversation_native_observations(kind, context_hash, observed_at)",
)

TURN_TRIGGER_STATEMENTS = (
    """CREATE TRIGGER IF NOT EXISTS trg_native_turns_sync_meta_ins
    AFTER INSERT ON conversation_native_turns
    WHEN NEW.scope_kind = '' OR NEW.role_binding_key = ''
    BEGIN
        UPDATE conversation_native_turns
        SET scope_kind = CASE
                WHEN NEW.scope_kind <> '' THEN NEW.scope_kind
                WHEN json_extract(NEW.context_json, '$._ctx_ref') IS NOT NULL THEN COALESCE(
                    (SELECT c.scope_kind FROM conversation_contexts c
                     WHERE c.context_hash = json_extract(NEW.context_json, '$._ctx_ref')),
                    ''
                )
                ELSE COALESCE(json_extract(NEW.context_json, '$.audience.scope.kind'), '')
            END,
            role_binding_key = CASE
                WHEN NEW.role_binding_key <> '' THEN NEW.role_binding_key
                WHEN json_extract(NEW.context_json, '$._ctx_ref') IS NOT NULL THEN COALESCE(
                    (SELECT c.role_binding_key FROM conversation_contexts c
                     WHERE c.context_hash = json_extract(NEW.context_json, '$._ctx_ref')),
                    ''
                )
                ELSE COALESCE(json_extract(NEW.context_json, '$.role_binding'), '')
            END
        WHERE rowid = NEW.rowid;
    END""",
    """CREATE TRIGGER IF NOT EXISTS trg_native_turns_sync_meta_upd
    AFTER UPDATE OF context_json ON conversation_native_turns
    WHEN OLD.context_json <> NEW.context_json AND json_extract(NEW.context_json, '$._ctx_ref') IS NULL
    BEGIN
        UPDATE conversation_native_turns
        SET scope_kind = COALESCE(json_extract(NEW.context_json, '$.audience.scope.kind'), ''),
            role_binding_key = COALESCE(json_extract(NEW.context_json, '$.role_binding'), '')
        WHERE rowid = NEW.rowid;
    END""",
)

OBS_TRIGGER_STATEMENTS_WITH_SENDS = (
    """CREATE TRIGGER IF NOT EXISTS trg_native_obs_sync_meta_ins
    AFTER INSERT ON conversation_native_observations
    WHEN NEW.effective_at = '' OR NEW.scope_kind = '' OR NEW.role_binding_key = ''
    BEGIN
        UPDATE conversation_native_observations
        SET effective_at = CASE
                WHEN NEW.effective_at <> '' THEN NEW.effective_at
                ELSE COALESCE(
                    (SELECT sent_at FROM conversation_native_sends WHERE observation_id = NEW.observation_id),
                    NEW.observed_at
                )
            END,
            scope_kind = CASE
                WHEN NEW.scope_kind <> '' THEN NEW.scope_kind
                WHEN json_extract(NEW.context_json, '$._ctx_ref') IS NOT NULL THEN COALESCE(
                    (SELECT c.scope_kind FROM conversation_contexts c
                     WHERE c.context_hash = json_extract(NEW.context_json, '$._ctx_ref')),
                    ''
                )
                ELSE COALESCE(json_extract(NEW.context_json, '$.audience.scope.kind'), '')
            END,
            role_binding_key = CASE
                WHEN NEW.role_binding_key <> '' THEN NEW.role_binding_key
                WHEN json_extract(NEW.context_json, '$._ctx_ref') IS NOT NULL THEN COALESCE(
                    (SELECT c.role_binding_key FROM conversation_contexts c
                     WHERE c.context_hash = json_extract(NEW.context_json, '$._ctx_ref')),
                    ''
                )
                ELSE COALESCE(json_extract(NEW.context_json, '$.role_binding'), '')
            END
        WHERE rowid = NEW.rowid;
    END""",
    """CREATE TRIGGER IF NOT EXISTS trg_native_obs_sync_meta_upd
    AFTER UPDATE OF context_json, observed_at ON conversation_native_observations
    WHEN OLD.context_json <> NEW.context_json OR OLD.observed_at <> NEW.observed_at
    BEGIN
        UPDATE conversation_native_observations
        SET effective_at = COALESCE(
                (SELECT sent_at FROM conversation_native_sends WHERE observation_id = NEW.observation_id),
                NEW.observed_at
            ),
            scope_kind = CASE
                WHEN json_extract(NEW.context_json, '$._ctx_ref') IS NOT NULL THEN COALESCE(
                    NULLIF(NEW.scope_kind, ''),
                    (SELECT c.scope_kind FROM conversation_contexts c
                     WHERE c.context_hash = json_extract(NEW.context_json, '$._ctx_ref')),
                    ''
                )
                ELSE COALESCE(json_extract(NEW.context_json, '$.audience.scope.kind'), '')
            END,
            role_binding_key = CASE
                WHEN json_extract(NEW.context_json, '$._ctx_ref') IS NOT NULL THEN COALESCE(
                    NULLIF(NEW.role_binding_key, ''),
                    (SELECT c.role_binding_key FROM conversation_contexts c
                     WHERE c.context_hash = json_extract(NEW.context_json, '$._ctx_ref')),
                    ''
                )
                ELSE COALESCE(json_extract(NEW.context_json, '$.role_binding'), '')
            END
        WHERE rowid = NEW.rowid;
    END""",
    """CREATE TRIGGER IF NOT EXISTS trg_native_sends_sync_effective_ins
    AFTER INSERT ON conversation_native_sends
    BEGIN
        UPDATE conversation_native_observations
        SET effective_at = NEW.sent_at
        WHERE observation_id = NEW.observation_id AND effective_at <> NEW.sent_at;
    END""",
    """CREATE TRIGGER IF NOT EXISTS trg_native_sends_sync_effective_upd
    AFTER UPDATE OF sent_at ON conversation_native_sends
    WHEN OLD.sent_at <> NEW.sent_at
    BEGIN
        UPDATE conversation_native_observations
        SET effective_at = NEW.sent_at
        WHERE observation_id = NEW.observation_id;
    END""",
    """CREATE TRIGGER IF NOT EXISTS trg_native_sends_sync_effective_del
    AFTER DELETE ON conversation_native_sends
    BEGIN
        UPDATE conversation_native_observations
        SET effective_at = observed_at
        WHERE observation_id = OLD.observation_id;
    END""",
)

OBS_TRIGGER_STATEMENTS_NO_SENDS = (
    """CREATE TRIGGER IF NOT EXISTS trg_native_obs_sync_meta_ins
    AFTER INSERT ON conversation_native_observations
    WHEN NEW.effective_at = '' OR NEW.scope_kind = '' OR NEW.role_binding_key = ''
    BEGIN
        UPDATE conversation_native_observations
        SET effective_at = CASE
                WHEN NEW.effective_at <> '' THEN NEW.effective_at
                ELSE NEW.observed_at
            END,
            scope_kind = CASE
                WHEN NEW.scope_kind <> '' THEN NEW.scope_kind
                WHEN json_extract(NEW.context_json, '$._ctx_ref') IS NOT NULL THEN COALESCE(
                    (SELECT c.scope_kind FROM conversation_contexts c
                     WHERE c.context_hash = json_extract(NEW.context_json, '$._ctx_ref')),
                    ''
                )
                ELSE COALESCE(json_extract(NEW.context_json, '$.audience.scope.kind'), '')
            END,
            role_binding_key = CASE
                WHEN NEW.role_binding_key <> '' THEN NEW.role_binding_key
                WHEN json_extract(NEW.context_json, '$._ctx_ref') IS NOT NULL THEN COALESCE(
                    (SELECT c.role_binding_key FROM conversation_contexts c
                     WHERE c.context_hash = json_extract(NEW.context_json, '$._ctx_ref')),
                    ''
                )
                ELSE COALESCE(json_extract(NEW.context_json, '$.role_binding'), '')
            END
        WHERE rowid = NEW.rowid;
    END""",
    """CREATE TRIGGER IF NOT EXISTS trg_native_obs_sync_meta_upd
    AFTER UPDATE OF context_json, observed_at ON conversation_native_observations
    WHEN OLD.context_json <> NEW.context_json OR OLD.observed_at <> NEW.observed_at
    BEGIN
        UPDATE conversation_native_observations
        SET effective_at = NEW.observed_at,
            scope_kind = CASE
                WHEN json_extract(NEW.context_json, '$._ctx_ref') IS NOT NULL THEN COALESCE(
                    NULLIF(NEW.scope_kind, ''),
                    (SELECT c.scope_kind FROM conversation_contexts c
                     WHERE c.context_hash = json_extract(NEW.context_json, '$._ctx_ref')),
                    ''
                )
                ELSE COALESCE(json_extract(NEW.context_json, '$.audience.scope.kind'), '')
            END,
            role_binding_key = CASE
                WHEN json_extract(NEW.context_json, '$._ctx_ref') IS NOT NULL THEN COALESCE(
                    NULLIF(NEW.role_binding_key, ''),
                    (SELECT c.role_binding_key FROM conversation_contexts c
                     WHERE c.context_hash = json_extract(NEW.context_json, '$._ctx_ref')),
                    ''
                )
                ELSE COALESCE(json_extract(NEW.context_json, '$.role_binding'), '')
            END
        WHERE rowid = NEW.rowid;
    END""",
)

STATEMENTS = (
    *TABLE_STATEMENTS,
    *INDEX_STATEMENTS,
    *MATERIALIZED_INDEX_STATEMENTS,
    *TURN_TRIGGER_STATEMENTS,
    *OBS_TRIGGER_STATEMENTS_WITH_SENDS,
)


def ensure_native_conversation_migrations(connection) -> bool:
    """Idempotently add materialized columns, dedup tables, backfills, triggers, and indexes.

    Returns True if any schema object or backfill changed so callers may run
    ``PRAGMA optimize`` to refresh planner statistics.
    """
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name IS NOT NULL"
        )
    }
    changed = False
    if "conversation_native_turns" in tables or "conversation_native_observations" in tables:
        if "role_instruction_blobs" not in tables or "conversation_contexts" not in tables:
            changed = True
        ensure_dedup_tables(connection)

    if "conversation_native_turns" in tables:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(conversation_native_turns)")}
        turns_changed = False
        if "scope_kind" not in columns:
            connection.execute(
                "ALTER TABLE conversation_native_turns ADD COLUMN scope_kind TEXT NOT NULL DEFAULT ''"
            )
            turns_changed = True
        if "role_binding_key" not in columns:
            connection.execute(
                "ALTER TABLE conversation_native_turns ADD COLUMN role_binding_key TEXT NOT NULL DEFAULT ''"
            )
            turns_changed = True
        if "context_hash" not in columns:
            connection.execute(
                "ALTER TABLE conversation_native_turns ADD COLUMN context_hash TEXT NOT NULL DEFAULT ''"
            )
            turns_changed = True
        turns_ddl_row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'conversation_native_turns'"
        ).fetchone()
        turns_ddl = (turns_ddl_row[0] or "") if turns_ddl_row is not None else ""
        if turns_ddl and "'absorbed'" not in turns_ddl:
            connection.execute("""CREATE TABLE conversation_native_turns_new (
                turn_id TEXT PRIMARY KEY, context_key TEXT NOT NULL, context_json TEXT NOT NULL,
                role_instruction TEXT NOT NULL, input_observation_id TEXT,
                observed_at TEXT NOT NULL, generation_state TEXT NOT NULL
                    CHECK(generation_state IN ('prepared', 'running', 'completed', 'failed', 'absorbed')),
                phase_at TEXT NOT NULL, last_sent_at TEXT,
                scope_kind TEXT NOT NULL DEFAULT '',
                role_binding_key TEXT NOT NULL DEFAULT '',
                context_hash TEXT NOT NULL DEFAULT ''
            )""")
            connection.execute("""INSERT INTO conversation_native_turns_new (
                rowid, turn_id, context_key, context_json, role_instruction, input_observation_id,
                observed_at, generation_state, phase_at, last_sent_at,
                scope_kind, role_binding_key, context_hash
            ) SELECT
                rowid, turn_id, context_key, context_json, role_instruction, input_observation_id,
                observed_at, generation_state, phase_at, last_sent_at,
                scope_kind, role_binding_key, context_hash
            FROM conversation_native_turns""")
            connection.execute("DROP TABLE conversation_native_turns")
            connection.execute("ALTER TABLE conversation_native_turns_new RENAME TO conversation_native_turns")
            for statement in INDEX_STATEMENTS:
                if "conversation_native_turns" in statement:
                    connection.execute(statement)
            for statement in TURN_TRIGGER_STATEMENTS:
                connection.execute(statement)
            turns_changed = True
        if turns_changed or connection.execute(
            "SELECT 1 FROM conversation_native_turns WHERE scope_kind = '' OR role_binding_key = '' LIMIT 1"
        ).fetchone() is not None:
            connection.execute(
                """UPDATE conversation_native_turns
                SET scope_kind = CASE
                        WHEN scope_kind <> '' THEN scope_kind
                        WHEN json_extract(context_json, '$._ctx_ref') IS NOT NULL THEN COALESCE(
                            (SELECT c.scope_kind FROM conversation_contexts c
                             WHERE c.context_hash = json_extract(context_json, '$._ctx_ref')),
                            ''
                        )
                        ELSE COALESCE(json_extract(context_json, '$.audience.scope.kind'), '')
                    END,
                    role_binding_key = CASE
                        WHEN role_binding_key <> '' THEN role_binding_key
                        WHEN json_extract(context_json, '$._ctx_ref') IS NOT NULL THEN COALESCE(
                            (SELECT c.role_binding_key FROM conversation_contexts c
                             WHERE c.context_hash = json_extract(context_json, '$._ctx_ref')),
                            ''
                        )
                        ELSE COALESCE(json_extract(context_json, '$.role_binding'), '')
                    END
                WHERE scope_kind = '' OR role_binding_key = ''"""
            )
            turns_changed = True
        existing_indexes = {
            row[0]: (row[1] or "")
            for row in connection.execute(
                "SELECT name, sql FROM sqlite_master WHERE type = 'index' AND tbl_name = 'conversation_native_turns'"
            )
        }
        open_idx_sql = existing_indexes.get("conversation_native_open_turn_idx")
        if open_idx_sql is not None and "json_extract" in open_idx_sql:
            connection.execute("DROP INDEX IF EXISTS conversation_native_open_turn_idx")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS conversation_native_open_turn_idx "
                "ON conversation_native_turns(scope_kind, context_key, observed_at) "
                "WHERE generation_state IN ('prepared', 'running')"
            )
            turns_changed = True
        for name, stmt in (
            (
                "idx_native_turns_kind_role_obs",
                "CREATE INDEX IF NOT EXISTS idx_native_turns_kind_role_obs "
                "ON conversation_native_turns(scope_kind, role_binding_key, observed_at)",
            ),
            (
                "idx_native_turns_context_hash",
                "CREATE INDEX IF NOT EXISTS idx_native_turns_context_hash "
                "ON conversation_native_turns(context_hash, observed_at)",
            ),
        ):
            if name not in existing_indexes:
                connection.execute(stmt)
                turns_changed = True
        existing_turn_trg = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'trigger' AND name = 'trg_native_turns_sync_meta_upd'"
        ).fetchone()
        if existing_turn_trg is not None and "_ctx_ref" not in (existing_turn_trg[0] or ""):
            connection.execute("DROP TRIGGER IF EXISTS trg_native_turns_sync_meta_ins")
            connection.execute("DROP TRIGGER IF EXISTS trg_native_turns_sync_meta_upd")
            turns_changed = True
        for statement in TURN_TRIGGER_STATEMENTS:
            connection.execute(statement)
        changed = changed or turns_changed

    if "conversation_native_observations" in tables:
        has_sends = "conversation_native_sends" in tables
        columns = {row[1] for row in connection.execute("PRAGMA table_info(conversation_native_observations)")}
        obs_changed = False
        if "effective_at" not in columns:
            connection.execute(
                "ALTER TABLE conversation_native_observations ADD COLUMN effective_at TEXT NOT NULL DEFAULT ''"
            )
            obs_changed = True
        if "scope_kind" not in columns:
            connection.execute(
                "ALTER TABLE conversation_native_observations ADD COLUMN scope_kind TEXT NOT NULL DEFAULT ''"
            )
            obs_changed = True
        if "role_binding_key" not in columns:
            connection.execute(
                "ALTER TABLE conversation_native_observations ADD COLUMN role_binding_key TEXT NOT NULL DEFAULT ''"
            )
            obs_changed = True
        if "context_hash" not in columns:
            connection.execute(
                "ALTER TABLE conversation_native_observations ADD COLUMN context_hash TEXT NOT NULL DEFAULT ''"
            )
            obs_changed = True
        if obs_changed or connection.execute(
            "SELECT 1 FROM conversation_native_observations "
            "WHERE effective_at = '' OR scope_kind = '' OR role_binding_key = '' LIMIT 1"
        ).fetchone() is not None:
            if has_sends:
                connection.execute(
                    """UPDATE conversation_native_observations
                    SET effective_at = CASE
                            WHEN effective_at <> '' THEN effective_at
                            ELSE COALESCE(
                                (SELECT s.sent_at FROM conversation_native_sends s
                                 WHERE s.observation_id = conversation_native_observations.observation_id),
                                observed_at
                            )
                        END,
                        scope_kind = CASE
                            WHEN scope_kind <> '' THEN scope_kind
                            WHEN json_extract(context_json, '$._ctx_ref') IS NOT NULL THEN COALESCE(
                                (SELECT c.scope_kind FROM conversation_contexts c
                                 WHERE c.context_hash = json_extract(context_json, '$._ctx_ref')),
                                ''
                            )
                            ELSE COALESCE(json_extract(context_json, '$.audience.scope.kind'), '')
                        END,
                        role_binding_key = CASE
                            WHEN role_binding_key <> '' THEN role_binding_key
                            WHEN json_extract(context_json, '$._ctx_ref') IS NOT NULL THEN COALESCE(
                                (SELECT c.role_binding_key FROM conversation_contexts c
                                 WHERE c.context_hash = json_extract(context_json, '$._ctx_ref')),
                                ''
                            )
                            ELSE COALESCE(json_extract(context_json, '$.role_binding'), '')
                        END
                    WHERE effective_at = '' OR scope_kind = '' OR role_binding_key = ''"""
                )
            else:
                connection.execute(
                    """UPDATE conversation_native_observations
                    SET effective_at = CASE WHEN effective_at <> '' THEN effective_at ELSE observed_at END,
                        scope_kind = CASE
                            WHEN scope_kind <> '' THEN scope_kind
                            WHEN json_extract(context_json, '$._ctx_ref') IS NOT NULL THEN COALESCE(
                                (SELECT c.scope_kind FROM conversation_contexts c
                                 WHERE c.context_hash = json_extract(context_json, '$._ctx_ref')),
                                ''
                            )
                            ELSE COALESCE(json_extract(context_json, '$.audience.scope.kind'), '')
                        END,
                        role_binding_key = CASE
                            WHEN role_binding_key <> '' THEN role_binding_key
                            WHEN json_extract(context_json, '$._ctx_ref') IS NOT NULL THEN COALESCE(
                                (SELECT c.role_binding_key FROM conversation_contexts c
                                 WHERE c.context_hash = json_extract(context_json, '$._ctx_ref')),
                                ''
                            )
                            ELSE COALESCE(json_extract(context_json, '$.role_binding'), '')
                        END
                    WHERE effective_at = '' OR scope_kind = '' OR role_binding_key = ''"""
                )
            obs_changed = True
        if has_sends:
            has_sends_trigger = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'trigger' AND name = 'trg_native_sends_sync_effective_ins'"
            ).fetchone() is not None
            if obs_changed or not has_sends_trigger:
                # Ensure any sends recorded before triggers existed are reflected in effective_at.
                updated = connection.execute(
                    """UPDATE conversation_native_observations
                    SET effective_at = (
                        SELECT s.sent_at FROM conversation_native_sends s
                        WHERE s.observation_id = conversation_native_observations.observation_id
                    )
                    WHERE observation_id IN (
                        SELECT s.observation_id FROM conversation_native_sends s
                        JOIN conversation_native_observations n ON n.observation_id = s.observation_id
                        WHERE n.effective_at <> s.sent_at
                    )"""
                ).rowcount
                if updated:
                    obs_changed = True
        existing_indexes = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'conversation_native_observations'"
            )
        }
        for name, stmt in (
            (
                "idx_native_obs_ctx_effective",
                "CREATE INDEX IF NOT EXISTS idx_native_obs_ctx_effective "
                "ON conversation_native_observations(context_key, effective_at) "
                "WHERE delivery_state IN ('input_observed', 'send_returned')",
            ),
            (
                "idx_native_obs_owner_kind_effective",
                "CREATE INDEX IF NOT EXISTS idx_native_obs_owner_kind_effective "
                "ON conversation_native_observations(memory_owner, scope_kind, effective_at, observation_id) "
                "WHERE delivery_state IN ('input_observed', 'send_returned')",
            ),
            (
                "idx_native_obs_event_input",
                "CREATE INDEX IF NOT EXISTS idx_native_obs_event_input "
                "ON conversation_native_observations(event_id) WHERE kind = 'input'",
            ),
            (
                "idx_native_obs_input_role_key",
                "CREATE INDEX IF NOT EXISTS idx_native_obs_input_role_key "
                "ON conversation_native_observations(role_binding_key, observed_at DESC, observation_id) "
                "WHERE kind = 'input' AND delivery_state = 'input_observed'",
            ),
            (
                "idx_native_obs_context_hash",
                "CREATE INDEX IF NOT EXISTS idx_native_obs_context_hash "
                "ON conversation_native_observations(kind, context_hash, observed_at)",
            ),
        ):
            if name not in existing_indexes:
                connection.execute(stmt)
                obs_changed = True
        existing_ins = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'trigger' AND name = 'trg_native_obs_sync_meta_ins'"
        ).fetchone()
        existing_ins_sql = existing_ins[0] or "" if existing_ins is not None else ""
        needs_obs_trg_upgrade = (
            existing_ins is not None
            and ("_ctx_ref" not in existing_ins_sql
                 or (has_sends and "conversation_native_sends" not in existing_ins_sql))
        )
        if needs_obs_trg_upgrade:
            connection.execute("DROP TRIGGER IF EXISTS trg_native_obs_sync_meta_ins")
            connection.execute("DROP TRIGGER IF EXISTS trg_native_obs_sync_meta_upd")
            obs_changed = True
        if has_sends:
            for statement in OBS_TRIGGER_STATEMENTS_WITH_SENDS:
                connection.execute(statement)
        else:
            for statement in OBS_TRIGGER_STATEMENTS_NO_SENDS:
                connection.execute(statement)
        changed = changed or obs_changed
    return changed


def ensure_native_conversation_schema(runtime) -> None:
    """Create and migrate native conversation tables once per runtime."""
    def _initialize(connection) -> None:
        for statement in TABLE_STATEMENTS:
            connection.execute(statement)
        ensure_native_conversation_migrations(connection)
        for statement in INDEX_STATEMENTS:
            connection.execute(statement)
        for statement in MATERIALIZED_INDEX_STATEMENTS:
            connection.execute(statement)

    ensure = getattr(runtime, "ensure_schema", None)
    if callable(ensure):
        ensure(SCHEMA_KEY, _initialize)
        return
    with runtime.transaction() as connection:
        _initialize(connection)
