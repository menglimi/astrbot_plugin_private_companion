"""Content-addressed deduplication for native conversation contexts and role prompts.

``conversation_native_turns`` repeats multi-kilobyte ``role_instruction`` prompts
on every turn, and ``conversation_native_turns``, ``conversation_native_observations``,
and ``astrbot_core_reply_observations`` repeat the same ~1 KB ``TaskContext`` JSON
across thousands of rows.

This module stores each distinct payload once in ``role_instruction_blobs`` or
``conversation_contexts`` keyed by SHA-256 and replaces the inline column with a
compact pointer:

* ``role_instruction`` -> ``"blob:<64-hex-sha256>"`` (69 bytes, satisfies
  ``trim(role_instruction) <> ''``).
* ``context_json`` -> ``'{"_ctx_ref":"<64-hex-sha256>"}'`` (79 bytes, strictly
  valid JSON so existing ``json_valid`` / ``json_extract`` expressions never
  raise malformed-JSON errors).

Because SHA-256 blobs are immutable, reads are backed by bounded in-process LRU
caches so steady-state resolution performs zero extra SQL queries. Legacy rows
storing inline strings continue to work transparently with zero migration required.
"""

from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timezone
import hashlib
import json
import sqlite3
import threading
from typing import Any

from companion_v2.storage.runtime import table_exists

ROLE_BLOB_PREFIX = "blob:"
ROLE_BLOB_MIN_BYTES = 128

CONTEXT_REF_KEY = "_ctx_ref"
CONTEXT_REF_PREFIX = '{"_ctx_ref":"'
CONTEXT_REF_SUFFIX = '"}'
CONTEXT_REF_LEN = len(CONTEXT_REF_PREFIX) + 64 + len(CONTEXT_REF_SUFFIX)

_HEX_CHARS = frozenset("0123456789abcdef")

_ROLE_CACHE_MAX = 512
_CONTEXT_CACHE_MAX = 2048
_UNHASHED_PER_RUNTIME_MAX = 32
_CACHE_LOCK = threading.Lock()
_ROLE_BLOB_CACHE: OrderedDict[str, str] = OrderedDict()
_CONTEXT_CACHE: OrderedDict[str, str] = OrderedDict()
_UNHASHED_CACHE_GENERATION = 0
_UNHASHED_ALLOWED_TABLES = frozenset((
    "conversation_native_turns",
    "conversation_native_observations",
    "astrbot_core_reply_observations",
))

DEDUP_TABLE_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS role_instruction_blobs (
        sha256 TEXT PRIMARY KEY,
        content TEXT NOT NULL,
        byte_size INTEGER NOT NULL,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS conversation_contexts (
        context_hash TEXT PRIMARY KEY,
        context_key TEXT NOT NULL,
        scope_kind TEXT NOT NULL DEFAULT '',
        role_binding_key TEXT NOT NULL DEFAULT '',
        context_json TEXT NOT NULL,
        byte_size INTEGER NOT NULL,
        created_at TEXT NOT NULL
    )""",
)

DEDUP_INDEX_STATEMENTS = (
    "CREATE INDEX IF NOT EXISTS idx_conversation_contexts_key "
    "ON conversation_contexts(context_key)",
    "CREATE INDEX IF NOT EXISTS idx_conversation_contexts_kind_role "
    "ON conversation_contexts(scope_kind, role_binding_key)",
)


def clear_dedup_caches() -> None:
    """Clear process-wide immutable blob caches (primarily for testing)."""
    global _UNHASHED_CACHE_GENERATION
    with _CACHE_LOCK:
        _ROLE_BLOB_CACHE.clear()
        _CONTEXT_CACHE.clear()
        _UNHASHED_CACHE_GENERATION += 1


def _cache_get(cache: OrderedDict[str, str], key: str) -> str | None:
    with _CACHE_LOCK:
        value = cache.get(key)
        if value is not None:
            cache.move_to_end(key)
        return value


def _cache_put(cache: OrderedDict[str, str], key: str, value: str, max_entries: int) -> None:
    with _CACHE_LOCK:
        cache[key] = value
        cache.move_to_end(key)
        while len(cache) > max_entries:
            cache.popitem(last=False)


def _is_sha256_hex(value: str) -> bool:
    return len(value) == 64 and all(ch in _HEX_CHARS for ch in value)


def _sha256_hex(text: str) -> tuple[str, int]:
    raw = text.encode("utf-8")
    return hashlib.sha256(raw).hexdigest(), len(raw)


def _read_one(runtime_or_conn: Any, sql: str, params: tuple[Any, ...]) -> Any:
    if runtime_or_conn is None:
        return None
    if isinstance(runtime_or_conn, sqlite3.Connection):
        return runtime_or_conn.execute(sql, params).fetchone()
    reader = getattr(runtime_or_conn, "read_one", None)
    if callable(reader):
        return reader(sql, params)
    return None


def _read_all(runtime_or_conn: Any, sql: str, params: tuple[Any, ...]) -> list[Any]:
    if runtime_or_conn is None:
        return []
    if isinstance(runtime_or_conn, sqlite3.Connection):
        return list(runtime_or_conn.execute(sql, params).fetchall())
    reader = getattr(runtime_or_conn, "read_all", None)
    if callable(reader):
        return list(reader(sql, params))
    return []


def _has_table(runtime_or_conn: Any, table_name: str) -> bool:
    if runtime_or_conn is None:
        return False
    if isinstance(runtime_or_conn, sqlite3.Connection):
        row = runtime_or_conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table_name,),
        ).fetchone()
        return row is not None
    return table_exists(runtime_or_conn, table_name)


def ensure_dedup_tables(connection: sqlite3.Connection) -> None:
    """Idempotently create dedup lookup tables and indexes on an open connection."""
    for statement in DEDUP_TABLE_STATEMENTS:
        connection.execute(statement)
    for statement in DEDUP_INDEX_STATEMENTS:
        connection.execute(statement)


# ---------------------------------------------------------------------------
# Role instruction blob helpers
# ---------------------------------------------------------------------------

def extract_role_blob_hash(value: Any) -> str | None:
    if type(value) is not str or not value.startswith(ROLE_BLOB_PREFIX):
        return None
    candidate = value[len(ROLE_BLOB_PREFIX):]
    return candidate if _is_sha256_hex(candidate) else None


def is_role_blob_ref(value: Any) -> bool:
    return extract_role_blob_hash(value) is not None


def store_role_instruction(connection: sqlite3.Connection, text: str, *, created_at: str) -> str:
    """Store large role instructions once in ``role_instruction_blobs`` and return a pointer."""
    if type(text) is not str:
        return text
    if is_role_blob_ref(text):
        return text
    digest_hex, byte_size = _sha256_hex(text)
    if byte_size < ROLE_BLOB_MIN_BYTES and not text.startswith(ROLE_BLOB_PREFIX):
        return text
    connection.execute(
        """INSERT OR IGNORE INTO role_instruction_blobs(sha256, content, byte_size, created_at)
           VALUES (?, ?, ?, ?)""",
        (digest_hex, text, byte_size, created_at),
    )
    _cache_put(_ROLE_BLOB_CACHE, digest_hex, text, _ROLE_CACHE_MAX)
    return ROLE_BLOB_PREFIX + digest_hex


def resolve_role_instruction(runtime_or_conn: Any, raw: Any) -> Any:
    """Resolve a ``blob:<sha256>`` reference back to full prompt text, or pass raw strings through."""
    digest_hex = extract_role_blob_hash(raw)
    if digest_hex is None:
        return raw
    cached = _cache_get(_ROLE_BLOB_CACHE, digest_hex)
    if cached is not None:
        return cached
    if not _has_table(runtime_or_conn, "role_instruction_blobs"):
        return raw
    try:
        row = _read_one(
            runtime_or_conn,
            "SELECT content FROM role_instruction_blobs WHERE sha256 = ?",
            (digest_hex,),
        )
    except sqlite3.OperationalError:
        return raw
    if row is None:
        return raw
    content = row["content"] if hasattr(row, "keys") else row[0]
    if type(content) is str:
        _cache_put(_ROLE_BLOB_CACHE, digest_hex, content, _ROLE_CACHE_MAX)
        return content
    return raw


def role_instruction_matches(runtime_or_conn: Any, stored: Any, expected: str) -> bool:
    """Compare a stored role_instruction (raw or blob ref) against expected prompt text."""
    if stored == expected:
        return True
    digest_hex = extract_role_blob_hash(stored)
    if digest_hex is None or type(expected) is not str:
        return False
    expected_hex, _ = _sha256_hex(expected)
    if digest_hex == expected_hex:
        return True
    return resolve_role_instruction(runtime_or_conn, stored) == expected


# ---------------------------------------------------------------------------
# Conversation context deduplication helpers
# ---------------------------------------------------------------------------

def make_context_ref(context_hash: str) -> str:
    return CONTEXT_REF_PREFIX + context_hash + CONTEXT_REF_SUFFIX


def extract_context_hash(value: Any) -> str | None:
    if (
        type(value) is not str
        or len(value) != CONTEXT_REF_LEN
        or not value.startswith(CONTEXT_REF_PREFIX)
        or not value.endswith(CONTEXT_REF_SUFFIX)
    ):
        return None
    candidate = value[len(CONTEXT_REF_PREFIX):-len(CONTEXT_REF_SUFFIX)]
    return candidate if _is_sha256_hex(candidate) else None


def is_context_ref(value: Any) -> bool:
    return extract_context_hash(value) is not None


def _extract_context_meta(context_json: str) -> tuple[str, str]:
    try:
        parsed = json.loads(context_json)
    except (TypeError, ValueError):
        return "", ""
    if type(parsed) is not dict:
        return "", ""
    audience = parsed.get("audience")
    scope = audience.get("scope") if type(audience) is dict else None
    scope_kind = str(scope.get("kind") or "") if type(scope) is dict else ""
    role_binding = parsed.get("role_binding")
    role_binding_key = (
        json.dumps(role_binding, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        if role_binding is not None
        else ""
    )
    return scope_kind, role_binding_key


def store_context_json(
    connection: sqlite3.Connection,
    context_json: str,
    *,
    context_key: str,
    scope_kind: str = "",
    role_binding_key: str = "",
    created_at: str,
) -> tuple[str, str]:
    """Store full context JSON once in ``conversation_contexts`` and return ``(ref_json, hash)``."""
    existing_hash = extract_context_hash(context_json)
    if existing_hash is not None:
        return context_json, existing_hash
    digest_hex, byte_size = _sha256_hex(context_json)
    if not scope_kind or not role_binding_key:
        parsed_kind, parsed_rb = _extract_context_meta(context_json)
        if not scope_kind:
            scope_kind = parsed_kind
        if not role_binding_key:
            role_binding_key = parsed_rb
    connection.execute(
        """INSERT OR IGNORE INTO conversation_contexts
           (context_hash, context_key, scope_kind, role_binding_key, context_json, byte_size, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (digest_hex, context_key, scope_kind, role_binding_key, context_json, byte_size, created_at),
    )
    _cache_put(_CONTEXT_CACHE, digest_hex, context_json, _CONTEXT_CACHE_MAX)
    return make_context_ref(digest_hex), digest_hex


def _batch_warm_context_cache(runtime_or_conn: Any, hashes: list[str]) -> None:
    if not hashes or not _has_table(runtime_or_conn, "conversation_contexts"):
        return
    missing = [h for h in dict.fromkeys(hashes) if _cache_get(_CONTEXT_CACHE, h) is None]
    if not missing:
        return
    chunk_size = 200
    for start in range(0, len(missing), chunk_size):
        chunk = missing[start:start + chunk_size]
        marks = ",".join("?" for _ in chunk)
        try:
            rows = _read_all(
                runtime_or_conn,
                f"SELECT context_hash, context_json FROM conversation_contexts WHERE context_hash IN ({marks})",
                tuple(chunk),
            )
        except sqlite3.OperationalError:
            return
        for row in rows:
            ch = row["context_hash"] if hasattr(row, "keys") else row[0]
            cj = row["context_json"] if hasattr(row, "keys") else row[1]
            if type(ch) is str and type(cj) is str:
                _cache_put(_CONTEXT_CACHE, ch, cj, _CONTEXT_CACHE_MAX)


def resolve_context_json(runtime_or_conn: Any, raw: Any) -> Any:
    """Resolve a ``{"_ctx_ref":"<sha256>"}`` pointer back to the full TaskContext JSON."""
    digest_hex = extract_context_hash(raw)
    if digest_hex is None:
        return raw
    cached = _cache_get(_CONTEXT_CACHE, digest_hex)
    if cached is not None:
        return cached
    if not _has_table(runtime_or_conn, "conversation_contexts"):
        return raw
    try:
        row = _read_one(
            runtime_or_conn,
            "SELECT context_json FROM conversation_contexts WHERE context_hash = ?",
            (digest_hex,),
        )
    except sqlite3.OperationalError:
        return raw
    if row is None:
        return raw
    content = row["context_json"] if hasattr(row, "keys") else row[0]
    if type(content) is str:
        _cache_put(_CONTEXT_CACHE, digest_hex, content, _CONTEXT_CACHE_MAX)
        return content
    return raw


def resolve_row_context(runtime_or_conn: Any, row: Any, *, resolve_role: bool = False) -> Any:
    """Return a row with ``context_json`` (and optionally ``role_instruction``) resolved."""
    if row is None:
        return None
    keys = row.keys() if hasattr(row, "keys") else ()
    raw_ctx = row["context_json"] if "context_json" in keys else None
    ctx_hash = extract_context_hash(raw_ctx) if raw_ctx is not None else None
    role_hash = (
        extract_role_blob_hash(row["role_instruction"])
        if (resolve_role and "role_instruction" in keys)
        else None
    )
    if ctx_hash is None and role_hash is None:
        return row
    resolved = dict(row)
    if ctx_hash is not None:
        resolved["context_json"] = resolve_context_json(runtime_or_conn, raw_ctx)
    if role_hash is not None:
        resolved["role_instruction"] = resolve_role_instruction(runtime_or_conn, row["role_instruction"])
    return resolved


def resolve_rows_context(runtime_or_conn: Any, rows: Any, *, resolve_role: bool = False) -> tuple[Any, ...]:
    """Batch-resolve ``context_json`` (and optionally ``role_instruction``) across a row sequence."""
    items = tuple(rows)
    if not items:
        return ()
    ctx_hashes = [h for r in items if (h := extract_context_hash(r["context_json"])) is not None]
    if ctx_hashes:
        _batch_warm_context_cache(runtime_or_conn, ctx_hashes)
    if resolve_role:
        role_hashes = [h for r in items if (h := extract_role_blob_hash(r["role_instruction"])) is not None]
        if role_hashes and _has_table(runtime_or_conn, "role_instruction_blobs"):
            missing = [h for h in dict.fromkeys(role_hashes) if _cache_get(_ROLE_BLOB_CACHE, h) is None]
            for start in range(0, len(missing), 200):
                chunk = missing[start:start + 200]
                marks = ",".join("?" for _ in chunk)
                try:
                    fetched = _read_all(
                        runtime_or_conn,
                        f"SELECT sha256, content FROM role_instruction_blobs WHERE sha256 IN ({marks})",
                        tuple(chunk),
                    )
                except sqlite3.OperationalError:
                    break
                for blob_row in fetched:
                    bh = blob_row["sha256"] if hasattr(blob_row, "keys") else blob_row[0]
                    bc = blob_row["content"] if hasattr(blob_row, "keys") else blob_row[1]
                    if type(bh) is str and type(bc) is str:
                        _cache_put(_ROLE_BLOB_CACHE, bh, bc, _ROLE_CACHE_MAX)
    return tuple(resolve_row_context(runtime_or_conn, row, resolve_role=resolve_role) for row in items)


def matching_context_hashes(
    runtime_or_conn: Any,
    where_sql: str,
    parameters: tuple[Any, ...] = (),
) -> tuple[str, ...]:
    """Return all ``context_hash`` values in ``conversation_contexts`` matching ``where_sql``.

    Because ``conversation_contexts`` holds only one row per distinct window
    context (typically dozens of rows), evaluating ``json_extract(context_json, ...)``
    filters against it takes microseconds and avoids scanning large tables.
    """
    if not _has_table(runtime_or_conn, "conversation_contexts"):
        return ()
    try:
        rows = _read_all(
            runtime_or_conn,
            f"SELECT context_hash FROM conversation_contexts WHERE {where_sql}",
            parameters,
        )
    except sqlite3.OperationalError:
        return ()
    return tuple(
        (row["context_hash"] if hasattr(row, "keys") else row[0])
        for row in rows
        if (row["context_hash"] if hasattr(row, "keys") else row[0])
    )


def has_unhashed_context_rows(
    runtime_or_conn: Any,
    table: str,
    *,
    kind: str | None = None,
) -> bool:
    """Return whether *table* still contains legacy rows without ``context_hash``.

    Uses O(log N) covering-index probes (``idx_native_turns_context_hash`` /
    ``idx_native_obs_context_hash``) cached per table write epoch so callers can
    skip full-table ``json_extract(context_json, ...)`` fallback scans once all
    historical rows have been deduplicated. Fails open (returns ``True``) when
    the dedup schema or ``context_hash`` column is absent.
    """
    if table not in _UNHASHED_ALLOWED_TABLES:
        raise ValueError(f"unsupported table for context_hash probe: {table}")
    if runtime_or_conn is None or not _has_table(runtime_or_conn, "conversation_contexts"):
        return True
    if not _has_table(runtime_or_conn, table):
        return False

    epoch_fn = getattr(runtime_or_conn, "table_write_epoch_snapshot", None)
    epoch_pair: tuple[int, int] | None = None
    instance_cache: dict[tuple[str, str], tuple[int, int, int, bool]] | None = None
    cache_key = (table, kind or "")
    cache_gen = 0
    if callable(epoch_fn):
        try:
            snap = epoch_fn((frozenset({table}),))
            if isinstance(snap, tuple) and len(snap) >= 2:
                epoch_pair = (int(snap[0]), int(snap[1]))
                existing_cache = getattr(runtime_or_conn, "_unhashed_context_cache", None)
                if isinstance(existing_cache, dict):
                    instance_cache = existing_cache
                else:
                    created_cache: dict[tuple[str, str], tuple[int, int, int, bool]] = {}
                    try:
                        setattr(runtime_or_conn, "_unhashed_context_cache", created_cache)
                        instance_cache = created_cache
                    except (AttributeError, TypeError):
                        instance_cache = None
                if instance_cache is not None:
                    with _CACHE_LOCK:
                        cache_gen = _UNHASHED_CACHE_GENERATION
                        cached = instance_cache.get(cache_key)
                        if (
                            cached is not None
                            and cached[0] == cache_gen
                            and cached[1] == epoch_pair[0]
                            and cached[2] == epoch_pair[1]
                        ):
                            return cached[3]
        except Exception:
            epoch_pair = None
            instance_cache = None

    try:
        if table == "conversation_native_observations" and kind is not None:
            found = (
                _read_one(
                    runtime_or_conn,
                    "SELECT 1 FROM conversation_native_observations WHERE kind = ? AND context_hash = '' LIMIT 1",
                    (kind,),
                )
                is not None
                or _read_one(
                    runtime_or_conn,
                    "SELECT 1 FROM conversation_native_observations WHERE kind = ? AND context_hash IS NULL LIMIT 1",
                    (kind,),
                )
                is not None
            )
        else:
            found = (
                _read_one(
                    runtime_or_conn,
                    f"SELECT 1 FROM {table} WHERE context_hash = '' LIMIT 1",
                    (),
                )
                is not None
                or _read_one(
                    runtime_or_conn,
                    f"SELECT 1 FROM {table} WHERE context_hash IS NULL LIMIT 1",
                    (),
                )
                is not None
            )
    except sqlite3.OperationalError:
        return True

    if instance_cache is not None and epoch_pair is not None:
        with _CACHE_LOCK:
            if _UNHASHED_CACHE_GENERATION == cache_gen:
                if len(instance_cache) >= _UNHASHED_PER_RUNTIME_MAX and cache_key not in instance_cache:
                    instance_cache.clear()
                instance_cache[cache_key] = (cache_gen, epoch_pair[0], epoch_pair[1], found)
    return found


def dedup_native_storage(
    runtime: Any,
    *,
    batch_size: int = 256,
    now: str | None = None,
) -> dict[str, int]:
    """Losslessly deduplicate existing native turn, observation, and core telemetry rows.

    Processes rows in bounded transactions so low-spec hosts do not experience long
    write locks. Safe to call repeatedly (idempotent).
    """
    created_at = now or datetime.now(timezone.utc).isoformat()
    chunk = max(16, int(batch_size))
    stats = {
        "role_blobs_migrated": 0,
        "turn_contexts_migrated": 0,
        "obs_contexts_migrated": 0,
        "core_contexts_migrated": 0,
        "bytes_saved_estimate": 0,
    }

    with runtime.transaction() as connection:
        ensure_dedup_tables(connection)
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name IS NOT NULL"
            )
        }
        if "conversation_native_turns" in tables or "conversation_native_observations" in tables:
            from companion_v2.storage.native_conversation_schema import ensure_native_conversation_migrations
            ensure_native_conversation_migrations(connection)
        if "astrbot_core_reply_observations" in tables:
            from companion_v2.storage.core_reply_telemetry_schema import ensure_core_reply_telemetry_migrations
            ensure_core_reply_telemetry_migrations(connection)

    # 1. Deduplicate conversation_native_turns (role_instruction & context_json).
    if "conversation_native_turns" in tables:
        last_rowid = 0
        while True:
            with runtime.transaction() as connection:
                cols = {r[1] for r in connection.execute("PRAGMA table_info(conversation_native_turns)")}
                has_ctx_hash = "context_hash" in cols
                rows = connection.execute(
                    """SELECT rowid, context_key, context_json, role_instruction, scope_kind, role_binding_key
                       FROM conversation_native_turns
                       WHERE rowid > ?
                         AND (
                           (length(CAST(role_instruction AS BLOB)) >= ? AND role_instruction NOT LIKE 'blob:%')
                           OR (context_json NOT LIKE '{"_ctx_ref":"%' AND length(context_json) > ?)
                         )
                       ORDER BY rowid ASC
                       LIMIT ?""",
                    (last_rowid, ROLE_BLOB_MIN_BYTES, CONTEXT_REF_LEN, chunk),
                ).fetchall()
                if not rows:
                    break
                for row in rows:
                    rowid = row["rowid"]
                    last_rowid = rowid
                    raw_role = row["role_instruction"]
                    raw_ctx = row["context_json"]
                    new_role = raw_role
                    new_ctx = raw_ctx
                    ctx_hash = extract_context_hash(raw_ctx) or ""
                    if type(raw_role) is str and not is_role_blob_ref(raw_role):
                        encoded_role = raw_role.encode("utf-8")
                        if len(encoded_role) >= ROLE_BLOB_MIN_BYTES or raw_role.startswith(ROLE_BLOB_PREFIX):
                            new_role = store_role_instruction(connection, raw_role, created_at=created_at)
                            stats["role_blobs_migrated"] += 1
                            stats["bytes_saved_estimate"] += max(0, len(encoded_role) - len(new_role))
                    if type(raw_ctx) is str and not is_context_ref(raw_ctx) and len(raw_ctx) > CONTEXT_REF_LEN:
                        new_ctx, ctx_hash = store_context_json(
                            connection,
                            raw_ctx,
                            context_key=row["context_key"],
                            scope_kind=row["scope_kind"],
                            role_binding_key=row["role_binding_key"],
                            created_at=created_at,
                        )
                        stats["turn_contexts_migrated"] += 1
                        stats["bytes_saved_estimate"] += max(0, len(raw_ctx.encode("utf-8")) - len(new_ctx))
                    if new_role != raw_role or new_ctx != raw_ctx:
                        if has_ctx_hash:
                            connection.execute(
                                """UPDATE conversation_native_turns
                                   SET role_instruction = ?, context_json = ?, context_hash = ?
                                   WHERE rowid = ?""",
                                (new_role, new_ctx, ctx_hash, rowid),
                            )
                        else:
                            connection.execute(
                                """UPDATE conversation_native_turns
                                   SET role_instruction = ?, context_json = ?
                                   WHERE rowid = ?""",
                                (new_role, new_ctx, rowid),
                            )

    # 2. Deduplicate conversation_native_observations.context_json.
    if "conversation_native_observations" in tables:
        last_rowid = 0
        while True:
            with runtime.transaction() as connection:
                cols = {r[1] for r in connection.execute("PRAGMA table_info(conversation_native_observations)")}
                has_ctx_hash = "context_hash" in cols
                has_rb = "role_binding_key" in cols
                rb_select = ", role_binding_key" if has_rb else ", '' AS role_binding_key"
                rows = connection.execute(
                    f"""SELECT rowid, context_key, context_json, scope_kind{rb_select}
                        FROM conversation_native_observations
                        WHERE rowid > ?
                          AND context_json NOT LIKE '{CONTEXT_REF_PREFIX}%'
                          AND length(context_json) > ?
                        ORDER BY rowid ASC
                        LIMIT ?""",
                    (last_rowid, CONTEXT_REF_LEN, chunk),
                ).fetchall()
                if not rows:
                    break
                for row in rows:
                    rowid = row["rowid"]
                    last_rowid = rowid
                    raw_ctx = row["context_json"]
                    new_ctx, ctx_hash = store_context_json(
                        connection,
                        raw_ctx,
                        context_key=row["context_key"],
                        scope_kind=row["scope_kind"],
                        role_binding_key=row["role_binding_key"],
                        created_at=created_at,
                    )
                    stats["obs_contexts_migrated"] += 1
                    stats["bytes_saved_estimate"] += max(0, len(raw_ctx.encode("utf-8")) - len(new_ctx))
                    if has_ctx_hash:
                        connection.execute(
                            """UPDATE conversation_native_observations
                               SET context_json = ?, context_hash = ?
                               WHERE rowid = ?""",
                            (new_ctx, ctx_hash, rowid),
                        )
                    else:
                        connection.execute(
                            """UPDATE conversation_native_observations
                               SET context_json = ?
                               WHERE rowid = ?""",
                            (new_ctx, rowid),
                        )

    # 3. Deduplicate astrbot_core_reply_observations.context_json.
    if "astrbot_core_reply_observations" in tables:
        last_rowid = 0
        while True:
            with runtime.transaction() as connection:
                cols = {r[1] for r in connection.execute("PRAGMA table_info(astrbot_core_reply_observations)")}
                has_ctx_hash = "context_hash" in cols
                rows = connection.execute(
                    f"""SELECT rowid, context_key, scope_kind, context_json
                        FROM astrbot_core_reply_observations
                        WHERE rowid > ?
                          AND context_json NOT LIKE '{CONTEXT_REF_PREFIX}%'
                          AND length(context_json) > ?
                        ORDER BY rowid ASC
                        LIMIT ?""",
                    (last_rowid, CONTEXT_REF_LEN, chunk),
                ).fetchall()
                if not rows:
                    break
                for row in rows:
                    rowid = row["rowid"]
                    last_rowid = rowid
                    raw_ctx = row["context_json"]
                    new_ctx, ctx_hash = store_context_json(
                        connection,
                        raw_ctx,
                        context_key=row["context_key"],
                        scope_kind=row["scope_kind"],
                        created_at=created_at,
                    )
                    stats["core_contexts_migrated"] += 1
                    stats["bytes_saved_estimate"] += max(0, len(raw_ctx.encode("utf-8")) - len(new_ctx))
                    if has_ctx_hash:
                        connection.execute(
                            """UPDATE astrbot_core_reply_observations
                               SET context_json = ?, context_hash = ?
                               WHERE rowid = ?""",
                            (new_ctx, ctx_hash, rowid),
                        )
                    else:
                        connection.execute(
                            """UPDATE astrbot_core_reply_observations
                               SET context_json = ?
                               WHERE rowid = ?""",
                            (new_ctx, rowid),
                        )

    return stats
