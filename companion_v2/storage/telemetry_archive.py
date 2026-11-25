"""Cold telemetry archiving and daily pre-aggregation for long-lived stores.

Moves historical cold rows out of the hot primary SQLite database into a
dedicated ``telemetry-archive.sqlite3`` sidecar while preserving 100% of
historical usage, pricing/FX recomputation, provider inventory, and cache
diagnostics via compact per-day rows in ``core_reply_daily_rollups``.

Specifically:

1. ``astrbot_core_reply_observations`` rows older than ``ttl_days`` (default 14)
   are durably written to ``telemetry-archive.sqlite3``, folded into pricing-
   independent ``core_reply_daily_rollups`` (collapsing per-turn request records
   into compact ``(day, hour, provider)`` aggregates), and deleted from the live
   table atomically.
2. ``laya_decision_events`` rows older than ``ttl_days`` have their heavyweight
   ``request_json`` (up to 128 KB) and ``response_json`` (up to 64 KB) payloads
   archived to ``telemetry-archive.sqlite3`` and nulled in place, keeping all
   decision indexes, counters, and CHECK constraints intact.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any

from companion_v2.execution.models import encode
from companion_v2.storage.core_reply_telemetry_schema import (
    ROLLUPS_TABLE,
    TABLE as CORE_OBS_TABLE,
    ensure_core_reply_telemetry_schema,
)
from companion_v2.storage.native_dedup import resolve_context_json
from companion_v2.storage.runtime import table_exists
from companion_v2.storage.sqlite_tuning import active_sqlite_tuning_profile, apply_sidecar_pragmas

DEFAULT_TELEMETRY_TTL_DAYS = 14
ARCHIVE_FILENAME = "telemetry-archive.sqlite3"

_ARCHIVE_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS archived_core_reply_observations (
        turn_id TEXT PRIMARY KEY,
        revision INTEGER NOT NULL,
        role_key TEXT NOT NULL,
        context_key TEXT NOT NULL,
        scope_kind TEXT NOT NULL,
        context_json TEXT NOT NULL,
        received_at TEXT NOT NULL,
        observed_at TEXT NOT NULL,
        record_json TEXT NOT NULL,
        archived_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_archived_core_role_received "
    "ON archived_core_reply_observations(role_key, received_at DESC, turn_id DESC)",
    """CREATE TABLE IF NOT EXISTS archived_laya_decision_payloads (
        event_id TEXT PRIMARY KEY,
        observed_at TEXT NOT NULL,
        origin_key TEXT NOT NULL DEFAULT '',
        scope_key TEXT NOT NULL DEFAULT '',
        source TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT '',
        mode TEXT NOT NULL DEFAULT '',
        request_json TEXT,
        response_json TEXT,
        archived_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_archived_laya_observed "
    "ON archived_laya_decision_payloads(observed_at DESC)",
)


class TelemetryArchiveStore:
    """Sidecar SQLite store for archived cold telemetry payloads."""

    BUSY_TIMEOUT_MS = 5000

    def __init__(self, data_root: str | Path, *, profile=None) -> None:
        self.root = Path(data_root)
        self.path = self.root / ARCHIVE_FILENAME
        self._explicit_profile = profile
        self._initialized = False

    @property
    def _profile(self):
        return self._explicit_profile or active_sqlite_tuning_profile()

    def ensure_initialized(self) -> None:
        if self._initialized and self.path.exists():
            return
        self.root.mkdir(parents=True, exist_ok=True)
        is_new = not self.path.exists()
        with self.db(enable_wal=True, set_incremental_vacuum=is_new) as db:
            for statement in _ARCHIVE_STATEMENTS:
                db.execute(statement)
        self._initialized = True

    @contextmanager
    def db(
        self,
        *,
        read_only: bool = False,
        enable_wal: bool = False,
        set_incremental_vacuum: bool = False,
    ):
        self.root.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            str(self.path),
            timeout=self.BUSY_TIMEOUT_MS / 1000.0,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        try:
            if set_incremental_vacuum and not read_only:
                try:
                    connection.execute("PRAGMA auto_vacuum = INCREMENTAL")
                except sqlite3.Error:
                    pass
            apply_sidecar_pragmas(
                connection,
                busy_timeout_ms=self.BUSY_TIMEOUT_MS,
                enable_wal=enable_wal,
                read_only=read_only,
                profile=self._profile,
            )
            connection.execute("BEGIN" if read_only else "BEGIN IMMEDIATE")
            yield connection
            connection.execute("COMMIT")
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def archive_core_rows(self, rows: list[dict[str, Any]], *, archived_at: str) -> set[str]:
        """Durably persist *rows* into the archive and return the set of newly inserted turn_ids."""
        if not rows:
            return set()
        self.ensure_initialized()
        newly_inserted: set[str] = set()
        with self.db() as db:
            for row in rows:
                cursor = db.execute(
                    """INSERT OR IGNORE INTO archived_core_reply_observations
                       (turn_id, revision, role_key, context_key, scope_kind,
                        context_json, received_at, observed_at, record_json, archived_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        row["turn_id"],
                        row["revision"],
                        row["role_key"],
                        row["context_key"],
                        row["scope_kind"],
                        row["context_json"],
                        row["received_at"],
                        row["observed_at"],
                        row["record_json"],
                        archived_at,
                    ),
                )
                if cursor.rowcount > 0:
                    newly_inserted.add(row["turn_id"])
        return newly_inserted

    def archive_laya_payloads(self, rows: list[dict[str, Any]], *, archived_at: str) -> int:
        if not rows:
            return 0
        self.ensure_initialized()
        inserted = 0
        with self.db() as db:
            for row in rows:
                cursor = db.execute(
                    """INSERT OR IGNORE INTO archived_laya_decision_payloads
                       (event_id, observed_at, origin_key, scope_key, source, status, mode,
                        request_json, response_json, archived_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        row["event_id"],
                        row["observed_at"],
                        row.get("origin_key") or "",
                        row.get("scope_key") or "",
                        row.get("source") or "",
                        row.get("status") or "",
                        row.get("mode") or "",
                        row.get("request_json"),
                        row.get("response_json"),
                        archived_at,
                    ),
                )
                inserted += int(cursor.rowcount or 0)
        return inserted

    def core_observation(self, turn_id: str) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        with self.db(read_only=True) as db:
            row = db.execute(
                "SELECT * FROM archived_core_reply_observations WHERE turn_id = ?",
                (turn_id,),
            ).fetchone()
            return dict(row) if row is not None else None

    def list_core_observations(self, role_key: str, *, limit: int = 100) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        with self.db(read_only=True) as db:
            rows = db.execute(
                """SELECT * FROM archived_core_reply_observations
                   WHERE role_key = ?
                   ORDER BY received_at DESC, turn_id DESC LIMIT ?""",
                (role_key, max(1, int(limit))),
            ).fetchall()
            return [dict(r) for r in rows]

    def laya_decision_payload(self, event_id: str) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        with self.db(read_only=True) as db:
            row = db.execute(
                "SELECT * FROM archived_laya_decision_payloads WHERE event_id = ?",
                (event_id,),
            ).fetchone()
            return dict(row) if row is not None else None

    def stats(self) -> dict[str, int | str | bool]:
        if not self.path.exists():
            return {
                "exists": False,
                "path": str(self.path),
                "size_bytes": 0,
                "core_rows": 0,
                "laya_rows": 0,
            }
        try:
            with self.db(read_only=True) as db:
                core_rows = int(
                    db.execute("SELECT COUNT(*) FROM archived_core_reply_observations").fetchone()[0]
                )
                laya_rows = int(
                    db.execute("SELECT COUNT(*) FROM archived_laya_decision_payloads").fetchone()[0]
                )
        except sqlite3.Error:
            core_rows, laya_rows = 0, 0
        size_bytes = 0
        for candidate in (
            self.path,
            self.path.with_name(self.path.name + "-wal"),
            self.path.with_name(self.path.name + "-shm"),
        ):
            try:
                if candidate.exists():
                    size_bytes += candidate.stat().st_size
            except OSError:
                pass
        return {
            "exists": True,
            "path": str(self.path),
            "size_bytes": size_bytes,
            "core_rows": core_rows,
            "laya_rows": laya_rows,
        }


# ---------------------------------------------------------------------------
# Pricing-independent daily rollup helpers
# ---------------------------------------------------------------------------

def _sum_opt_int(a: int | None, b: int | None) -> int | None:
    if a is None:
        return b
    if b is None:
        return a
    return int(a) + int(b)


def _collapse_pricing_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse per-call pricing rows into compact additive ``(day, hour, provider, measurable)`` buckets.

    Preserves all token counters, call/request counts, latency totals, and
    provider metadata needed for exact pricing/FX re-evaluation at any future time.
    """
    from companion_v2.model_gateway.usage import CORE_CHAIN_KIND, USAGE_FIELDS

    buckets: dict[tuple[str, str, str, bool], dict[str, Any]] = {}
    seen_turns_per_bucket: dict[tuple[str, str, str, bool], set[str]] = {}

    for row in rows:
        day = str(row.get("day") or "")
        hour = str(row.get("hour") or "00")
        provider = row.get("provider") if type(row.get("provider")) is str else ""
        measurable = (
            row.get("actual_tokens") is not None
            or row.get("input_tokens") is not None
            or row.get("output_tokens") is not None
        )
        bkey = (day, hour, provider, measurable)
        row_calls = int(row.get("_rollup_calls") or 1)
        if "_rollup_requests" in row:
            row_requests = int(row["_rollup_requests"] or 0)
        else:
            turn_id = str(row.get("call_id") or "").rsplit(":", 1)[0]
            seen = seen_turns_per_bucket.setdefault(bkey, set())
            if turn_id and turn_id not in seen:
                seen.add(turn_id)
                row_requests = 1
            elif not turn_id:
                row_requests = 1
            else:
                row_requests = 0

        bucket = buckets.get(bkey)
        if bucket is None:
            agg = {field: int((row.get("aggregate") or {}).get(field) or 0) for field in USAGE_FIELDS}
            created_at = str(row.get("created_at") or f"{day}T{hour}:00:00+00:00")
            buckets[bkey] = {
                **agg,
                "day": day,
                "hour": hour,
                "kind": row.get("kind") or CORE_CHAIN_KIND,
                "provider": provider,
                "provider_json": row.get("provider_json") or encode({"provider": provider}),
                "call_id": f"rollup:{day}:{hour}:{provider}:{'m' if measurable else 'u'}",
                "position": 0,
                "origin_key": "",
                "state": row.get("state") or "completed",
                "actual_tokens": row.get("actual_tokens"),
                "actual_cost": None,
                "input_tokens": row.get("input_tokens"),
                "output_tokens": row.get("output_tokens"),
                "cached_input_tokens": row.get("cached_input_tokens"),
                "cache_creation_tokens": row.get("cache_creation_tokens"),
                "latency_ms": None,
                "failure_code": None,
                "response_summary_json": None,
                "created_at": created_at,
                "started_at": created_at,
                "finished_at": created_at,
                "execution_path": "astrbot_core",
                "usage_scope": row.get("usage_scope") or "provider_response",
                "token_availability": row.get("token_availability") or ("reported" if measurable else "unavailable"),
                "cost_availability": "unavailable",
                "cache_availability": row.get("cache_availability") or "unavailable",
                "signature_scope": "unavailable",
                "aggregate": agg,
                "_is_rollup": True,
                "_rollup_calls": row_calls,
                "_rollup_requests": row_requests,
            }
        else:
            agg = bucket["aggregate"]
            src_agg = row.get("aggregate") or {}
            for field in USAGE_FIELDS:
                added = int(src_agg.get(field) or 0)
                agg[field] = int(agg.get(field) or 0) + added
                bucket[field] = agg[field]
            bucket["actual_tokens"] = _sum_opt_int(bucket["actual_tokens"], row.get("actual_tokens"))
            bucket["input_tokens"] = _sum_opt_int(bucket["input_tokens"], row.get("input_tokens"))
            bucket["output_tokens"] = _sum_opt_int(bucket["output_tokens"], row.get("output_tokens"))
            bucket["cached_input_tokens"] = _sum_opt_int(
                bucket["cached_input_tokens"], row.get("cached_input_tokens")
            )
            bucket["cache_creation_tokens"] = _sum_opt_int(
                bucket["cache_creation_tokens"], row.get("cache_creation_tokens")
            )
            bucket["_rollup_calls"] += row_calls
            bucket["_rollup_requests"] += row_requests

    return [buckets[key] for key in sorted(buckets)]


def _merge_cache_daily(
    target: dict[str, dict[str, dict[str, Any]]],
    source: dict[str, dict[str, dict[str, Any]]],
) -> dict[str, dict[str, dict[str, Any]]]:
    merged: dict[str, dict[str, dict[str, Any]]] = {
        key: {day: dict(vals) for day, vals in days.items()}
        for key, days in target.items()
    }
    for key, days in source.items():
        dst_days = merged.setdefault(key, {})
        for day, src_vals in days.items():
            dst_vals = dst_days.get(day)
            if dst_vals is None:
                dst_days[day] = dict(src_vals)
            else:
                for field, val in src_vals.items():
                    if field == "day":
                        continue
                    if isinstance(val, (int, float)):
                        dst_vals[field] = (dst_vals.get(field) or 0) + val
    return merged


def _build_day_rollup_delta(observations: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute a pricing-independent daily rollup payload for a batch of observations."""
    from companion_v2.model_gateway.core_observations import request_rows
    from companion_v2.model_gateway.maintenance_cache import (
        CacheDiagnosticAccumulator,
        _availability_counts_for_observations,
    )

    avail = list(_availability_counts_for_observations(observations))
    raw_rows = request_rows(observations, include_signature_json=False)
    sorted_rows = sorted(
        raw_rows,
        key=lambda r: (r["finished_at"] or r["started_at"] or r["created_at"], r["position"]),
    )
    cache_diag = CacheDiagnosticAccumulator()
    for row in sorted_rows:
        cache_diag.add_row(row, track_sort_key=True)
    cache_daily = {
        key: {day: dict(vals) for day, vals in days.items()}
        for key, days in cache_diag.daily.items()
        if days
    }
    pricing_rows = _collapse_pricing_rows(raw_rows)
    core_days = sorted({row["day"] for row in raw_rows if row.get("day")})

    provider_totals: dict[str, dict[str, int]] = {}
    turns_seen: dict[str, set[str]] = {}
    for obs in observations:
        tid = str(obs.get("turn_id") or "")
        for req in obs.get("requests") or ():
            if not isinstance(req, dict):
                continue
            pid = str(req.get("provider") or "").strip()
            if not pid:
                continue
            usage = req.get("usage") if isinstance(req.get("usage"), dict) else {}
            is_new_turn = tid not in turns_seen.setdefault(pid, set())
            if tid:
                turns_seen[pid].add(tid)
            entry = provider_totals.setdefault(
                pid,
                {
                    "calls": 0,
                    "requests": 0,
                    "total_tokens": 0,
                    "input_tokens": 0,
                    "cached_input_tokens": 0,
                    "cache_creation_tokens": 0,
                    "output_tokens": 0,
                },
            )
            entry["calls"] += 1
            entry["requests"] += 1 if is_new_turn else 0
            entry["total_tokens"] += int(usage.get("tokens") or 0)
            entry["input_tokens"] += int(usage.get("input_tokens") or 0)
            entry["cached_input_tokens"] += int(usage.get("cached_input_tokens") or 0)
            entry["cache_creation_tokens"] += int(usage.get("cache_creation_tokens") or 0)
            entry["output_tokens"] += int(usage.get("output_tokens") or 0)

    return {
        "version": 1,
        "core_days": core_days,
        "availability_counts": avail,
        "pricing_rows": pricing_rows,
        "provider_totals": provider_totals,
        "cache_daily": cache_daily,
    }


def _merge_day_rollups(existing: dict[str, Any] | None, delta: dict[str, Any]) -> dict[str, Any]:
    if not existing:
        return delta
    merged_days = sorted(set(existing.get("core_days") or ()) | set(delta.get("core_days") or ()))
    left_avail = existing.get("availability_counts") or [0] * 8
    right_avail = delta.get("availability_counts") or [0] * 8
    merged_avail = [int(a) + int(b) for a, b in zip(left_avail, right_avail)]
    merged_pricing = _collapse_pricing_rows(
        list(existing.get("pricing_rows") or ()) + list(delta.get("pricing_rows") or ())
    )
    merged_providers: dict[str, dict[str, int]] = {
        pid: dict(vals) for pid, vals in (existing.get("provider_totals") or {}).items()
    }
    for pid, vals in (delta.get("provider_totals") or {}).items():
        dst = merged_providers.setdefault(
            pid,
            {
                "calls": 0,
                "requests": 0,
                "total_tokens": 0,
                "input_tokens": 0,
                "cached_input_tokens": 0,
                "cache_creation_tokens": 0,
                "output_tokens": 0,
            },
        )
        for field, count in vals.items():
            dst[field] = int(dst.get(field) or 0) + int(count or 0)
    merged_cache_daily = _merge_cache_daily(
        existing.get("cache_daily") or {},
        delta.get("cache_daily") or {},
    )
    return {
        "version": 1,
        "core_days": merged_days,
        "availability_counts": merged_avail,
        "pricing_rows": merged_pricing,
        "provider_totals": merged_providers,
        "cache_daily": merged_cache_daily,
    }


def rollups_available(runtime: Any) -> bool:
    return table_exists(runtime, ROLLUPS_TABLE)


def read_core_rollups_watermark(
    runtime: Any,
    *,
    role_key_value: str | None = None,
    before_day: str | None = None,
) -> tuple[int, int, str]:
    """O(1)/index-backed watermark for ``core_reply_daily_rollups``."""
    if not rollups_available(runtime):
        return (0, 0, "")
    where: list[str] = []
    params: list[Any] = []
    if role_key_value is not None:
        where.append("role_key = ?")
        params.append(role_key_value)
    if before_day is not None:
        where.append("day <= ?")
        params.append(before_day)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    try:
        row = runtime.read_one(
            f"SELECT COUNT(*), COALESCE(SUM(archived_row_count), 0), COALESCE(MAX(updated_at), '') "
            f"FROM {ROLLUPS_TABLE}{clause}",
            tuple(params),
        )
    except sqlite3.OperationalError:
        return (0, 0, "")
    if row is None:
        return (0, 0, "")
    return (int(row[0]), int(row[1]), str(row[2] or ""))


def load_core_daily_rollups(
    runtime: Any,
    *,
    role_key_value: str | None = None,
    before_day: str | None = None,
) -> list[dict[str, Any]]:
    """Load parsed rollup payloads from ``core_reply_daily_rollups``."""
    if not rollups_available(runtime):
        return []
    where: list[str] = []
    params: list[Any] = []
    if role_key_value is not None:
        where.append("role_key = ?")
        params.append(role_key_value)
    if before_day is not None:
        where.append("day <= ?")
        params.append(before_day)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    try:
        rows = runtime.read_all(
            f"SELECT role_key, day, archived_row_count, rollup_json, updated_at "
            f"FROM {ROLLUPS_TABLE}{clause} ORDER BY day ASC, role_key ASC",
            tuple(params),
        )
    except sqlite3.OperationalError:
        return []
    parsed: list[dict[str, Any]] = []
    for row in rows:
        try:
            payload = json.loads(row["rollup_json"])
        except (TypeError, ValueError):
            continue
        if type(payload) is dict:
            parsed.append({
                "role_key": row["role_key"],
                "day": row["day"],
                "archived_row_count": int(row["archived_row_count"] or 0),
                "updated_at": row["updated_at"],
                "payload": payload,
            })
    return parsed


def archive_cold_telemetry(
    runtime: Any,
    *,
    ttl_days: int = DEFAULT_TELEMETRY_TTL_DAYS,
    now: datetime | None = None,
    batch_size: int = 256,
) -> dict[str, Any]:
    """Archive cold core-reply telemetry and Laya decision payloads into ``telemetry-archive.sqlite3``.

    Safe to call repeatedly (idempotent) and bounded per transaction so low-spec
    servers never stall on long write locks.
    """
    effective_ttl = max(1, min(3650, int(ttl_days)))
    chunk = max(16, min(500, int(batch_size)))
    now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    cutoff_dt = now_utc - timedelta(days=effective_ttl)
    cutoff_iso = cutoff_dt.isoformat()
    archived_at = now_utc.isoformat()

    archive_store = TelemetryArchiveStore(runtime.data_root)
    stats: dict[str, Any] = {
        "ttl_days": effective_ttl,
        "cutoff_iso": cutoff_iso,
        "archive_path": str(archive_store.path),
        "core_rows_archived": 0,
        "core_rollup_days_updated": 0,
        "laya_payloads_archived": 0,
        "core_bytes_reclaimed_estimate": 0,
        "laya_bytes_reclaimed_estimate": 0,
    }
    updated_rollup_keys: set[tuple[str, str]] = set()

    # 1. Archive cold astrbot_core_reply_observations into telemetry-archive.sqlite3
    #    after rolling up pricing-independent daily summaries into core_reply_daily_rollups.
    if table_exists(runtime, CORE_OBS_TABLE):
        ensure_core_reply_telemetry_schema(runtime)
        while True:
            cold_rows = runtime.read_all(
                f"""SELECT rowid, turn_id, revision, role_key, context_key, scope_kind,
                           context_json, received_at, observed_at, record_json
                    FROM {CORE_OBS_TABLE}
                    WHERE received_at < ?
                    ORDER BY received_at ASC, rowid ASC
                    LIMIT ?""",
                (cutoff_iso, chunk),
            )
            if not cold_rows:
                break

            resolved_rows: list[dict[str, Any]] = []
            for row in cold_rows:
                resolved_ctx = resolve_context_json(runtime, row["context_json"])
                resolved_rows.append({
                    "rowid": int(row["rowid"]),
                    "turn_id": row["turn_id"],
                    "revision": row["revision"],
                    "role_key": row["role_key"],
                    "context_key": row["context_key"],
                    "scope_kind": row["scope_kind"],
                    "context_json": resolved_ctx,
                    "received_at": row["received_at"],
                    "observed_at": row["observed_at"],
                    "record_json": row["record_json"],
                    "raw_bytes": len(str(row["record_json"] or "").encode("utf-8"))
                    + len(str(row["context_json"] or "").encode("utf-8")),
                })

            archive_store.archive_core_rows(resolved_rows, archived_at=archived_at)

            turn_ids = [item["turn_id"] for item in resolved_rows]
            marks = ",".join("?" for _ in turn_ids)

            with runtime.transaction() as connection:
                # Re-verify which rows still exist in the primary table inside the
                # same atomic transaction that updates rollups and deletes them.
                # This ensures retries after a sidecar-committed/primary-rolled-back
                # crash never silently drop rollup stats, while concurrent callers
                # never double-count.
                live_ids = {
                    row["turn_id"]
                    for row in connection.execute(
                        f"SELECT turn_id FROM {CORE_OBS_TABLE} WHERE turn_id IN ({marks})",
                        tuple(turn_ids),
                    ).fetchall()
                }
                live_items = [item for item in resolved_rows if item["turn_id"] in live_ids]
                if not live_items:
                    continue

                by_role_day: dict[tuple[str, str], list[dict[str, Any]]] = {}
                max_rowid_by_role_day: dict[tuple[str, str], int] = {}
                for item in live_items:
                    day = str(item["received_at"] or "")[:10]
                    if len(day) != 10:
                        continue
                    try:
                        record = json.loads(item["record_json"])
                    except (TypeError, ValueError):
                        continue
                    if type(record) is not dict or record.get("version") != 1:
                        continue
                    obs = {
                        **record,
                        "context_key": item["context_key"],
                        "context_json": item["context_json"],
                    }
                    rd_key = (item["role_key"], day)
                    by_role_day.setdefault(rd_key, []).append(obs)
                    max_rowid_by_role_day[rd_key] = max(max_rowid_by_role_day.get(rd_key, 0), item["rowid"])

                deltas = {
                    rd_key: (_build_day_rollup_delta(obs_list), len(obs_list), max_rowid_by_role_day[rd_key])
                    for rd_key, obs_list in by_role_day.items()
                }

                for (rk, day), (delta_payload, added_count, max_rid) in deltas.items():
                    existing_row = connection.execute(
                        f"SELECT source_row_count, max_rowid, archived_row_count, rollup_json "
                        f"FROM {ROLLUPS_TABLE} WHERE role_key = ? AND day = ?",
                        (rk, day),
                    ).fetchone()
                    existing_payload = None
                    prev_source = 0
                    prev_max_rid = 0
                    prev_archived = 0
                    if existing_row is not None:
                        prev_source = int(existing_row["source_row_count"] or 0)
                        prev_max_rid = int(existing_row["max_rowid"] or 0)
                        prev_archived = int(existing_row["archived_row_count"] or 0)
                        try:
                            existing_payload = json.loads(existing_row["rollup_json"])
                        except (TypeError, ValueError):
                            existing_payload = None
                    merged_payload = _merge_day_rollups(existing_payload, delta_payload)
                    connection.execute(
                        f"""INSERT INTO {ROLLUPS_TABLE}
                            (role_key, day, source_row_count, max_rowid, archived_row_count, rollup_json, updated_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(role_key, day) DO UPDATE SET
                                source_row_count = excluded.source_row_count,
                                max_rowid = excluded.max_rowid,
                                archived_row_count = excluded.archived_row_count,
                                rollup_json = excluded.rollup_json,
                                updated_at = excluded.updated_at""",
                        (
                            rk,
                            day,
                            prev_source + added_count,
                            max(prev_max_rid, max_rid),
                            prev_archived + added_count,
                            encode(merged_payload),
                            archived_at,
                        ),
                    )
                    updated_rollup_keys.add((rk, day))

                live_turn_ids = [item["turn_id"] for item in live_items]
                live_marks = ",".join("?" for _ in live_turn_ids)
                deleted = connection.execute(
                    f"DELETE FROM {CORE_OBS_TABLE} WHERE turn_id IN ({live_marks})",
                    tuple(live_turn_ids),
                ).rowcount
                stats["core_rows_archived"] += int(deleted or 0)
                stats["core_bytes_reclaimed_estimate"] += sum(item["raw_bytes"] for item in live_items)

    stats["core_rollup_days_updated"] = len(updated_rollup_keys)

    # 2. Archive heavyweight cold laya_decision_events request_json / response_json payloads
    #    and NULL them in place so live counters, outcomes, and indexes stay intact.
    if table_exists(runtime, "laya_decision_events"):
        cols = {
            row[1]
            for row in runtime.read_all("PRAGMA table_info(laya_decision_events)", ())
        }
        has_scope_key = "scope_key" in cols
        scope_expr = "scope_key" if has_scope_key else "'' AS scope_key"
        while True:
            cold_laya = runtime.read_all(
                f"""SELECT event_id, observed_at, origin_key, {scope_expr}, source, status, mode,
                           request_json, response_json
                    FROM laya_decision_events
                    WHERE observed_at < ?
                      AND (request_json IS NOT NULL OR response_json IS NOT NULL)
                    ORDER BY rowid ASC
                    LIMIT ?""",
                (cutoff_iso, chunk),
            )
            if not cold_laya:
                break
            payload_rows = [dict(row) for row in cold_laya]
            reclaimed_bytes = sum(
                len(str(r.get("request_json") or "").encode("utf-8"))
                + len(str(r.get("response_json") or "").encode("utf-8"))
                for r in payload_rows
            )
            archive_store.archive_laya_payloads(payload_rows, archived_at=archived_at)
            event_ids = [r["event_id"] for r in payload_rows]
            marks = ",".join("?" for _ in event_ids)
            with runtime.transaction() as connection:
                updated = connection.execute(
                    f"""UPDATE laya_decision_events
                        SET request_json = NULL, response_json = NULL
                        WHERE event_id IN ({marks})""",
                    tuple(event_ids),
                ).rowcount
                stats["laya_payloads_archived"] += int(updated or 0)
                stats["laya_bytes_reclaimed_estimate"] += reclaimed_bytes

    return stats
