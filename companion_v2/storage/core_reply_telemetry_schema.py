"""Optional body-free telemetry for replies executed by AstrBot itself."""

from __future__ import annotations

from companion_v2.storage.native_dedup import (
    DEDUP_INDEX_STATEMENTS,
    DEDUP_TABLE_STATEMENTS,
    ensure_dedup_tables,
)

TABLE = "astrbot_core_reply_observations"
ROLLUPS_TABLE = "core_reply_daily_rollups"
SCHEMA_KEY = "astrbot_core_reply_observations.v2"

STATEMENTS = (
    *DEDUP_TABLE_STATEMENTS,
    *DEDUP_INDEX_STATEMENTS,
    """CREATE TABLE IF NOT EXISTS astrbot_core_reply_observations (
        turn_id TEXT PRIMARY KEY, revision INTEGER NOT NULL,
        role_key TEXT NOT NULL, context_key TEXT NOT NULL, scope_kind TEXT NOT NULL,
        context_json TEXT NOT NULL, received_at TEXT NOT NULL,
        observed_at TEXT NOT NULL, record_json TEXT NOT NULL,
        context_hash TEXT NOT NULL DEFAULT ''
    )""",
    "CREATE INDEX IF NOT EXISTS astrbot_core_reply_role_idx "
    "ON astrbot_core_reply_observations(role_key, received_at, turn_id)",
    "CREATE INDEX IF NOT EXISTS idx_core_reply_role_watermark "
    "ON astrbot_core_reply_observations(role_key, received_at, revision, observed_at)",
    "CREATE INDEX IF NOT EXISTS idx_core_reply_scope_lookup "
    "ON astrbot_core_reply_observations("
    "role_key, scope_kind, "
    "json_extract(context_json, '$.role_binding.scope.native_scope_id'), "
    "json_extract(context_json, '$.role_binding.scope.session_id'), "
    "json_extract(context_json, '$.role_binding.scope.conversation_id'), "
    "received_at DESC, turn_id DESC)",
    "CREATE INDEX IF NOT EXISTS idx_core_reply_context_hash "
    "ON astrbot_core_reply_observations(role_key, context_hash, received_at)",
    "CREATE INDEX IF NOT EXISTS idx_core_reply_role_scope_ctxhash "
    "ON astrbot_core_reply_observations("
    "role_key, scope_kind, context_hash, received_at DESC, turn_id DESC)",
    """CREATE TABLE IF NOT EXISTS core_reply_daily_rollups (
        role_key TEXT NOT NULL,
        day TEXT NOT NULL,
        source_row_count INTEGER NOT NULL DEFAULT 0,
        max_rowid INTEGER NOT NULL DEFAULT 0,
        archived_row_count INTEGER NOT NULL DEFAULT 0,
        rollup_json TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (role_key, day)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_core_reply_rollups_day "
    "ON core_reply_daily_rollups(day)",
)


def ensure_core_reply_telemetry_migrations(connection) -> bool:
    """Idempotently add ``context_hash`` and ``core_reply_daily_rollups`` to existing stores."""
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name IS NOT NULL"
        )
    }
    changed = False
    if TABLE in tables:
        ensure_dedup_tables(connection)
        existing_indexes = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND name IS NOT NULL"
            )
        }
        columns = {row[1] for row in connection.execute(f"PRAGMA table_info({TABLE})")}
        if "context_hash" not in columns:
            connection.execute(
                f"ALTER TABLE {TABLE} ADD COLUMN context_hash TEXT NOT NULL DEFAULT ''"
            )
            changed = True
        for index_name, statement in (
            (
                "idx_core_reply_context_hash",
                "CREATE INDEX IF NOT EXISTS idx_core_reply_context_hash "
                "ON astrbot_core_reply_observations(role_key, context_hash, received_at)",
            ),
            (
                "idx_core_reply_role_scope_ctxhash",
                "CREATE INDEX IF NOT EXISTS idx_core_reply_role_scope_ctxhash "
                "ON astrbot_core_reply_observations("
                "role_key, scope_kind, context_hash, received_at DESC, turn_id DESC)",
            ),
        ):
            if index_name not in existing_indexes:
                connection.execute(statement)
                changed = True
        if ROLLUPS_TABLE not in tables:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS core_reply_daily_rollups (
                    role_key TEXT NOT NULL,
                    day TEXT NOT NULL,
                    source_row_count INTEGER NOT NULL DEFAULT 0,
                    max_rowid INTEGER NOT NULL DEFAULT 0,
                    archived_row_count INTEGER NOT NULL DEFAULT 0,
                    rollup_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (role_key, day)
                )"""
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_core_reply_rollups_day "
                "ON core_reply_daily_rollups(day)"
            )
            changed = True
    return changed


def ensure_core_reply_telemetry_schema(runtime) -> None:
    """Create and migrate core-reply telemetry & daily rollup tables once per runtime."""
    def _init(connection) -> None:
        for statement in STATEMENTS:
            connection.execute(statement)
        ensure_core_reply_telemetry_migrations(connection)

    ensure = getattr(runtime, "ensure_schema", None)
    if callable(ensure):
        ensure(SCHEMA_KEY, _init)
        return
    with runtime.transaction() as connection:
        _init(connection)
