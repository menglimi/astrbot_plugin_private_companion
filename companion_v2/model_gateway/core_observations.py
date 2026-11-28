"""Project real AstrBot observations without reserving V2 model calls or budget."""

from datetime import datetime, timedelta, timezone
import json
import re
import sqlite3

from companion_v2.execution.models import digest, encode
from companion_v2.storage.core_reply_telemetry_schema import (
    OBSERVED_MODEL_CATALOG_TABLE,
    OBSERVED_TURN_USAGE_TABLE,
    TABLE,
    ensure_core_reply_telemetry_schema,
)
from companion_v2.storage.native_dedup import resolve_context_json, store_context_json
from companion_v2.storage.runtime import table_exists
from .usage import CORE_CHAIN_KIND, USAGE_FIELDS


_SCHEMA_KEY = "astrbot_core_reply_observations.v1"
_OBSERVED_CATALOG_WATERMARK_KEY = "observed_model_catalog.watermark.v1"
_CATALOG_BACKFILL_BATCH_SIZE = 500


def role_key(context):
    scope = context.scope
    return digest([scope.installation_id, scope.platform, scope.bot_id, scope.account_id,
                   context.role_binding.role])


def available(runtime):
    return table_exists(runtime, TABLE)


def _fingerprints_only(value):
    """The telemetry owner accepts structural metadata, never arbitrary JSON."""
    if value is None:
        return
    from .cache_diagnostics import checked_signature
    signature = checked_signature(encode(value))
    if signature is None or set(value) - set(signature.to_record()):
        raise ValueError("core request signature must contain hashes and counts only")


def _validate_record(record):
    if type(record) is not dict or set(record) - {
        "version", "revision", "turn_id", "event_id", "execution_path", "received_at", "waiting_llm_at", "request_hook_at", "observed_at",
        "generation_state", "generation_finished_at", "request_observation_available", "request_observation_complete",
        "tool_requery_observed", "requests", "tools", "ttft_ms", "runner_duration_ms", "usage_scope", "delivery",
        "request_hook_signature", "signature_scope", "agent_started_at", "preparation_steps", "materials", "offered_tools"}:
        raise ValueError("core telemetry records contain only declared metadata")
    for step in record.get("preparation_steps", []):
        if (type(step) is not dict or set(step) - {"name", "state", "started_at", "finished_at", "elapsed_ms"}
                or step.get("name") not in {"context_build", "request_authorization", "prompt_injection", "tool_injection", "send_tool_injection", "perception_injection", "telemetry_capture"}
                or step.get("state") not in {"returned", "failed"}
                or step.get("elapsed_ms") is not None and (type(step["elapsed_ms"]) is not int or step["elapsed_ms"] < 0)):
            raise ValueError("core preparation contains only declared stages and timings")
    for material in record.get("materials", []):
        if (type(material) is not dict or set(material) - {"owner", "purpose", "state", "elapsed_ms", "chars"}
                or material.get("state") not in {"ready", "empty", "unavailable", "not_injected"}
                or any(type(material.get(key)) is not str or re.fullmatch(r"[a-z][a-z0-9_.:-]{0,127}", material[key]) is None
                       for key in ("owner", "purpose"))
                or any(material.get(key) is not None and (type(material[key]) is not int or material[key] < 0)
                       for key in ("elapsed_ms", "chars"))):
            raise ValueError("core materials contain only source names, counts and timings")
    if (type(record.get("offered_tools", [])) is not list
            or len(record.get("offered_tools", [])) > 256
            or any(type(name) is not str or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.:-]{0,127}", name) is None
                   for name in record.get("offered_tools", []))):
        raise ValueError("core offered tools contain function names only")
    _fingerprints_only(record.get("request_hook_signature"))
    request_fields = {"position", "state", "started_at", "finished_at", "latency_ms", "ttft_ms", "provider", "usage",
                      "signature", "signature_scope", "measurement_basis", "failure_code", "media"}
    usage_fields = {"input_tokens", "output_tokens", "tokens", "cached_input_tokens", "cache_creation_tokens",
                    "availability", "cache_availability", "cost_availability"}
    for request in record.get("requests", []):
        if type(request) is not dict or set(request) - request_fields or set(request.get("usage") or {}) - usage_fields:
            raise ValueError("core request metadata must not include prompt or response bodies")
        _fingerprints_only(request.get("signature"))
        media = request.get("media")
        if media is not None:
            flags = {"voice_prompt_present", "reaction_enabled", "reaction_prompt_present", "presentation_present"}
            if (type(media) is not dict or set(media) != flags | {"voice_mode"}
                    or media["voice_mode"] not in {"disabled", "always", "request", "adaptive"}
                    or any(type(media[key]) is not bool for key in flags)):
                raise ValueError("core media diagnostics contain mode and boolean presence flags only")
    for tool in record.get("tools", []):
        if type(tool) is not dict or set(tool) - {"sequence", "action", "state", "started_at", "finished_at"}:
            raise ValueError("core tool timing must not include arguments or output")


def _ensure_schema(runtime):
    ensure_core_reply_telemetry_schema(runtime)


def _extract_turn_provider_usage(record):
    """Aggregate per-provider call and token counters for one observation turn."""
    totals = {}
    if not isinstance(record, dict):
        return totals
    for req in record.get("requests") or ():
        if not isinstance(req, dict):
            continue
        pid = str(req.get("provider") or "").strip()
        if not pid:
            continue
        usage = req.get("usage") if isinstance(req.get("usage"), dict) else {}
        entry = totals.setdefault(
            pid,
            {
                "calls": 0,
                "total_tokens": 0,
                "input_tokens": 0,
                "cached_input_tokens": 0,
                "cache_creation_tokens": 0,
                "output_tokens": 0,
            },
        )
        entry["calls"] += 1
        entry["total_tokens"] += int(usage.get("tokens") or 0)
        entry["input_tokens"] += int(usage.get("input_tokens") or 0)
        entry["cached_input_tokens"] += int(usage.get("cached_input_tokens") or 0)
        entry["cache_creation_tokens"] += int(usage.get("cache_creation_tokens") or 0)
        entry["output_tokens"] += int(usage.get("output_tokens") or 0)
    return totals


def _apply_catalog_delta(
    connection,
    role_key_value,
    provider_id,
    *,
    calls_delta,
    requests_delta,
    total_tokens_delta,
    input_tokens_delta,
    cached_input_tokens_delta,
    cache_creation_tokens_delta,
    output_tokens_delta,
    observed_at="",
):
    if (
        calls_delta == 0
        and requests_delta == 0
        and total_tokens_delta == 0
        and input_tokens_delta == 0
        and cached_input_tokens_delta == 0
        and cache_creation_tokens_delta == 0
        and output_tokens_delta == 0
    ):
        return
    existing = connection.execute(
        f"""SELECT calls, requests, total_tokens, input_tokens,
                   cached_input_tokens, cache_creation_tokens, output_tokens,
                   last_observed_at
            FROM {OBSERVED_MODEL_CATALOG_TABLE}
            WHERE role_key = ? AND provider_id = ?""",
        (role_key_value, provider_id),
    ).fetchone()
    if existing is None:
        if calls_delta <= 0 and requests_delta <= 0:
            return
        connection.execute(
            f"""INSERT INTO {OBSERVED_MODEL_CATALOG_TABLE}
                (role_key, provider_id, calls, requests, total_tokens, input_tokens,
                 cached_input_tokens, cache_creation_tokens, output_tokens, last_observed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                role_key_value,
                provider_id,
                max(0, calls_delta),
                max(0, requests_delta),
                max(0, total_tokens_delta),
                max(0, input_tokens_delta),
                max(0, cached_input_tokens_delta),
                max(0, cache_creation_tokens_delta),
                max(0, output_tokens_delta),
                str(observed_at or ""),
            ),
        )
        return
    next_calls = max(0, int(existing["calls"] or 0) + calls_delta)
    next_requests = max(0, int(existing["requests"] or 0) + requests_delta)
    if next_calls == 0 and next_requests == 0:
        connection.execute(
            f"DELETE FROM {OBSERVED_MODEL_CATALOG_TABLE} WHERE role_key = ? AND provider_id = ?",
            (role_key_value, provider_id),
        )
        return
    prev_obs = str(existing["last_observed_at"] or "")
    next_obs = str(observed_at or "")
    if prev_obs and (not next_obs or prev_obs > next_obs):
        next_obs = prev_obs
    connection.execute(
        f"""UPDATE {OBSERVED_MODEL_CATALOG_TABLE}
            SET calls = ?, requests = ?, total_tokens = ?, input_tokens = ?,
                cached_input_tokens = ?, cache_creation_tokens = ?, output_tokens = ?,
                last_observed_at = ?
            WHERE role_key = ? AND provider_id = ?""",
        (
            next_calls,
            next_requests,
            max(0, int(existing["total_tokens"] or 0) + total_tokens_delta),
            max(0, int(existing["input_tokens"] or 0) + input_tokens_delta),
            max(0, int(existing["cached_input_tokens"] or 0) + cached_input_tokens_delta),
            max(0, int(existing["cache_creation_tokens"] or 0) + cache_creation_tokens_delta),
            max(0, int(existing["output_tokens"] or 0) + output_tokens_delta),
            next_obs,
            role_key_value,
            provider_id,
        ),
    )


def _sync_turn_observed_catalog(connection, *, turn_id, role_key_value, revision, observed_at, record):
    """Idempotently replace one turn's usage rows and update ``observed_model_catalog``."""
    prior_rows = connection.execute(
        f"""SELECT role_key, provider_id, calls, total_tokens, input_tokens,
                   cached_input_tokens, cache_creation_tokens, output_tokens
            FROM {OBSERVED_TURN_USAGE_TABLE}
            WHERE turn_id = ?""",
        (turn_id,),
    ).fetchall()
    prior_by_key = {
        (row["role_key"], row["provider_id"]): {
            "calls": int(row["calls"] or 0),
            "requests": 1,
            "total_tokens": int(row["total_tokens"] or 0),
            "input_tokens": int(row["input_tokens"] or 0),
            "cached_input_tokens": int(row["cached_input_tokens"] or 0),
            "cache_creation_tokens": int(row["cache_creation_tokens"] or 0),
            "output_tokens": int(row["output_tokens"] or 0),
        }
        for row in prior_rows
    }
    new_usage = _extract_turn_provider_usage(record)
    new_by_key = {
        (role_key_value, pid): {**vals, "requests": 1}
        for pid, vals in new_usage.items()
    }
    if prior_by_key == new_by_key:
        if new_by_key:
            connection.execute(
                f"UPDATE {OBSERVED_TURN_USAGE_TABLE} SET revision = ?, observed_at = ? WHERE turn_id = ?",
                (int(revision or 0), str(observed_at or ""), turn_id),
            )
        return
    if prior_rows:
        connection.execute(
            f"DELETE FROM {OBSERVED_TURN_USAGE_TABLE} WHERE turn_id = ?",
            (turn_id,),
        )
    for pid, vals in new_usage.items():
        connection.execute(
            f"""INSERT INTO {OBSERVED_TURN_USAGE_TABLE}
                (turn_id, role_key, revision, provider_id, calls, total_tokens,
                 input_tokens, cached_input_tokens, cache_creation_tokens, output_tokens, observed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                turn_id,
                role_key_value,
                int(revision or 0),
                pid,
                vals["calls"],
                vals["total_tokens"],
                vals["input_tokens"],
                vals["cached_input_tokens"],
                vals["cache_creation_tokens"],
                vals["output_tokens"],
                str(observed_at or ""),
            ),
        )
    for key in set(prior_by_key) | set(new_by_key):
        old = prior_by_key.get(key)
        cur = new_by_key.get(key)
        rk, pid = key
        _apply_catalog_delta(
            connection,
            rk,
            pid,
            calls_delta=(cur["calls"] if cur else 0) - (old["calls"] if old else 0),
            requests_delta=(cur["requests"] if cur else 0) - (old["requests"] if old else 0),
            total_tokens_delta=(cur["total_tokens"] if cur else 0) - (old["total_tokens"] if old else 0),
            input_tokens_delta=(cur["input_tokens"] if cur else 0) - (old["input_tokens"] if old else 0),
            cached_input_tokens_delta=(cur["cached_input_tokens"] if cur else 0)
            - (old["cached_input_tokens"] if old else 0),
            cache_creation_tokens_delta=(cur["cache_creation_tokens"] if cur else 0)
            - (old["cache_creation_tokens"] if old else 0),
            output_tokens_delta=(cur["output_tokens"] if cur else 0) - (old["output_tokens"] if old else 0),
            observed_at=observed_at if cur else "",
        )


def _read_catalog_watermark_in_tx(connection):
    try:
        row = connection.execute(
            "SELECT value FROM runtime_metadata WHERE key = ?",
            (_OBSERVED_CATALOG_WATERMARK_KEY,),
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    if row is None:
        return None
    try:
        parsed = json.loads(row["value"])
    except (TypeError, ValueError):
        return None
    return parsed if type(parsed) is dict else None


def _write_catalog_watermark_in_tx(connection, watermark):
    connection.execute(
        """INSERT INTO runtime_metadata(key, value) VALUES (?, ?)
           ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
        (_OBSERVED_CATALOG_WATERMARK_KEY, encode(watermark)),
    )


def _read_live_obs_watermark(connection):
    row = connection.execute(
        f"SELECT COUNT(*), COALESCE(MAX(rowid), 0), COALESCE(SUM(revision), 0) FROM {TABLE}"
    ).fetchone()
    if row is None:
        return (0, 0, 0)
    return (int(row[0] or 0), int(row[1] or 0), int(row[2] or 0))


def remove_turns_from_observed_catalog(connection, turn_ids, *, deleted_revision_sum=None):
    """Subtract archived/deleted observations from ``observed_model_catalog`` inside the delete transaction."""
    ids = [str(tid) for tid in turn_ids if tid]
    if not ids:
        return
    has_tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN (?, ?)",
            (OBSERVED_MODEL_CATALOG_TABLE, OBSERVED_TURN_USAGE_TABLE),
        ).fetchall()
    }
    if OBSERVED_MODEL_CATALOG_TABLE not in has_tables or OBSERVED_TURN_USAGE_TABLE not in has_tables:
        return
    marks = ",".join("?" for _ in ids)
    rows = connection.execute(
        f"""SELECT role_key, provider_id,
                   COUNT(*) AS requests,
                   SUM(calls) AS calls,
                   SUM(total_tokens) AS total_tokens,
                   SUM(input_tokens) AS input_tokens,
                   SUM(cached_input_tokens) AS cached_input_tokens,
                   SUM(cache_creation_tokens) AS cache_creation_tokens,
                   SUM(output_tokens) AS output_tokens
            FROM {OBSERVED_TURN_USAGE_TABLE}
            WHERE turn_id IN ({marks})
            GROUP BY role_key, provider_id""",
        tuple(ids),
    ).fetchall()
    for row in rows:
        _apply_catalog_delta(
            connection,
            row["role_key"],
            row["provider_id"],
            calls_delta=-int(row["calls"] or 0),
            requests_delta=-int(row["requests"] or 0),
            total_tokens_delta=-int(row["total_tokens"] or 0),
            input_tokens_delta=-int(row["input_tokens"] or 0),
            cached_input_tokens_delta=-int(row["cached_input_tokens"] or 0),
            cache_creation_tokens_delta=-int(row["cache_creation_tokens"] or 0),
            output_tokens_delta=-int(row["output_tokens"] or 0),
        )
    connection.execute(
        f"DELETE FROM {OBSERVED_TURN_USAGE_TABLE} WHERE turn_id IN ({marks})",
        tuple(ids),
    )
    wm = _read_catalog_watermark_in_tx(connection)
    if wm and wm.get("backfilled") is True:
        max_row = connection.execute(f"SELECT COALESCE(MAX(rowid), 0) FROM {TABLE}").fetchone()
        next_max_rowid = int(max_row[0] or 0) if max_row is not None else 0
        if deleted_revision_sum is not None:
            next_count = max(0, int(wm.get("row_count") or 0) - len(ids))
            next_rev_sum = max(0, int(wm.get("revision_sum") or 0) - int(deleted_revision_sum))
        else:
            next_count, next_max_rowid, next_rev_sum = _read_live_obs_watermark(connection)
        _write_catalog_watermark_in_tx(
            connection,
            {
                "version": 1,
                "backfilled": True,
                "row_count": next_count,
                "max_rowid": next_max_rowid,
                "revision_sum": next_rev_sum,
            },
        )


def sync_observed_model_catalog(runtime, *, batch_size=_CATALOG_BACKFILL_BATCH_SIZE):
    """Ensure ``observed_model_catalog`` matches live ``astrbot_core_reply_observations``.

    On the steady-state path this performs a single covering-index watermark check
    (< 1 ms) and returns immediately without touching ``record_json`` blobs.
    On first upgrade over a large legacy table it performs bounded, resumable
    chunked backfill so write locks stay short.
    """
    if not available(runtime):
        return False
    _ensure_schema(runtime)
    live_row = runtime.read_one(
        f"SELECT COUNT(*), COALESCE(MAX(rowid), 0), COALESCE(SUM(revision), 0) FROM {TABLE}",
        (),
    )
    live_count = int(live_row[0] or 0) if live_row is not None else 0
    live_max_rowid = int(live_row[1] or 0) if live_row is not None else 0
    live_rev_sum = int(live_row[2] or 0) if live_row is not None else 0

    wm_row = runtime.read_one(
        "SELECT value FROM runtime_metadata WHERE key = ?",
        (_OBSERVED_CATALOG_WATERMARK_KEY,),
    )
    wm = None
    if wm_row is not None:
        try:
            parsed = json.loads(wm_row["value"])
            if type(parsed) is dict:
                wm = parsed
        except (TypeError, ValueError):
            wm = None

    if (
        wm is not None
        and wm.get("backfilled") is True
        and int(wm.get("row_count") or 0) == live_count
        and int(wm.get("max_rowid") or 0) == live_max_rowid
        and int(wm.get("revision_sum") or 0) == live_rev_sum
    ):
        return True

    chunk = max(16, min(2000, int(batch_size)))

    # Detect out-of-band raw SQL updates/deletes vs purely append-only raw inserts / first backfill.
    need_reset = False
    incremental_catchup = False
    prev_count = 0
    prev_rev = 0
    cursor_rowid = 0
    if wm is not None and wm.get("backfilled") is True:
        prev_count = int(wm.get("row_count") or 0)
        prev_max = int(wm.get("max_rowid") or 0)
        prev_rev = int(wm.get("revision_sum") or 0)
        if live_max_rowid > prev_max and live_count >= prev_count and live_rev_sum >= prev_rev:
            # Try incremental catch-up for newly appended rows first.
            cursor_rowid = prev_max
            incremental_catchup = True
        else:
            need_reset = True
    elif wm is not None:
        cursor_rowid = max(0, int(wm.get("cursor_rowid") or 0))

    if need_reset:
        with runtime.transaction() as connection:
            connection.execute(f"DELETE FROM {OBSERVED_MODEL_CATALOG_TABLE}")
            connection.execute(f"DELETE FROM {OBSERVED_TURN_USAGE_TABLE}")
            _write_catalog_watermark_in_tx(
                connection,
                {"version": 1, "backfilled": False, "cursor_rowid": 0},
            )
        cursor_rowid = 0

    def _run_chunked_scan(start_rowid):
        cur = start_rowid
        scanned_rows = 0
        scanned_rev_sum = 0
        while True:
            rows = runtime.read_all(
                f"""SELECT rowid, turn_id, revision, role_key, observed_at, record_json
                    FROM {TABLE}
                    WHERE rowid > ?
                    ORDER BY rowid ASC
                    LIMIT ?""",
                (cur, chunk),
            )
            if not rows:
                break
            last_rid = cur
            with runtime.transaction() as connection:
                for row in rows:
                    last_rid = int(row["rowid"])
                    scanned_rows += 1
                    scanned_rev_sum += int(row["revision"] or 0)
                    try:
                        record = json.loads(row["record_json"])
                    except (TypeError, ValueError):
                        continue
                    if not isinstance(record, dict):
                        continue
                    _sync_turn_observed_catalog(
                        connection,
                        turn_id=row["turn_id"],
                        role_key_value=row["role_key"],
                        revision=row["revision"],
                        observed_at=row["observed_at"],
                        record=record,
                    )
                _write_catalog_watermark_in_tx(
                    connection,
                    {"version": 1, "backfilled": False, "cursor_rowid": last_rid},
                )
            cur = last_rid
        return scanned_rows, scanned_rev_sum

    scanned_rows, scanned_rev_sum = _run_chunked_scan(cursor_rowid)

    # Verify incremental catch-up wasn't skewed by out-of-band DELETE+INSERT or
    # in-place UPDATE; any row-count/revision conservation mismatch, orphan turn
    # usage row, or pre-cursor revision drift triggers a clean full rebuild once.
    with runtime.transaction() as connection:
        final_count, final_max_rowid, final_rev_sum = _read_live_obs_watermark(connection)
        if not need_reset and incremental_catchup:
            stale_detected = (
                prev_count + scanned_rows != final_count
                or prev_rev + scanned_rev_sum != final_rev_sum
            )
            if not stale_detected:
                orphan_row = connection.execute(
                    f"""SELECT 1 FROM {OBSERVED_TURN_USAGE_TABLE} u
                        WHERE NOT EXISTS (
                            SELECT 1 FROM {TABLE} o WHERE o.turn_id = u.turn_id
                        ) LIMIT 1"""
                ).fetchone()
                stale_detected = orphan_row is not None
            if not stale_detected:
                recheck_rev = connection.execute(
                    f"""SELECT 1 FROM {TABLE} o
                        JOIN {OBSERVED_TURN_USAGE_TABLE} u ON u.turn_id = o.turn_id
                        WHERE o.rowid <= ? AND o.revision != u.revision
                        LIMIT 1""",
                    (cursor_rowid,),
                ).fetchone()
                stale_detected = recheck_rev is not None
            if stale_detected:
                connection.execute(f"DELETE FROM {OBSERVED_MODEL_CATALOG_TABLE}")
                connection.execute(f"DELETE FROM {OBSERVED_TURN_USAGE_TABLE}")
                _write_catalog_watermark_in_tx(
                    connection,
                    {"version": 1, "backfilled": False, "cursor_rowid": 0},
                )
                need_reset = True
        if not need_reset:
            _write_catalog_watermark_in_tx(
                connection,
                {
                    "version": 1,
                    "backfilled": True,
                    "row_count": final_count,
                    "max_rowid": final_max_rowid,
                    "revision_sum": final_rev_sum,
                },
            )
            return True

    # Full rebuild fallback after in-place out-of-band UPDATE detection.
    _run_chunked_scan(0)
    with runtime.transaction() as connection:
        final_count, final_max_rowid, final_rev_sum = _read_live_obs_watermark(connection)
        _write_catalog_watermark_in_tx(
            connection,
            {
                "version": 1,
                "backfilled": True,
                "row_count": final_count,
                "max_rowid": final_max_rowid,
                "revision_sum": final_rev_sum,
            },
        )
    return True


def _query_observed_catalog_fallback(runtime, *, role_key_value=None):
    """Read-only fallback when the database cannot be migrated/written."""
    obs_sql = (
        f"SELECT turn_id, record_json FROM {TABLE} "
        + ("WHERE role_key = ?" if role_key_value is not None else "")
    )
    params = (role_key_value,) if role_key_value is not None else ()
    agg = {}
    turns_seen = {}
    for obs_row in runtime.read_all(obs_sql, params):
        try:
            record = json.loads(obs_row["record_json"])
        except (TypeError, ValueError):
            continue
        if not isinstance(record, dict):
            continue
        tid = str(obs_row["turn_id"] or "")
        for pid, vals in _extract_turn_provider_usage(record).items():
            entry = agg.setdefault(
                pid,
                {
                    "provider_id": pid,
                    "calls": 0,
                    "requests": 0,
                    "total_tokens": 0,
                    "input_tokens": 0,
                    "cached_input_tokens": 0,
                    "cache_creation_tokens": 0,
                    "output_tokens": 0,
                },
            )
            is_new_turn = tid not in turns_seen.setdefault(pid, set())
            if tid:
                turns_seen[pid].add(tid)
            entry["calls"] += vals["calls"]
            entry["requests"] += 1 if is_new_turn else 0
            entry["total_tokens"] += vals["total_tokens"]
            entry["input_tokens"] += vals["input_tokens"]
            entry["cached_input_tokens"] += vals["cached_input_tokens"]
            entry["cache_creation_tokens"] += vals["cache_creation_tokens"]
            entry["output_tokens"] += vals["output_tokens"]
    return list(agg.values())


def query_observed_model_catalog(runtime, *, role_key_value=None):
    """Return per-provider live usage totals in O(distinct providers) without ``json_each``."""
    if not available(runtime):
        return []
    try:
        sync_observed_model_catalog(runtime)
        if role_key_value is not None:
            rows = runtime.read_all(
                f"""SELECT provider_id, calls, requests, total_tokens, input_tokens,
                           cached_input_tokens, cache_creation_tokens, output_tokens
                    FROM {OBSERVED_MODEL_CATALOG_TABLE}
                    WHERE role_key = ?""",
                (role_key_value,),
            )
        else:
            rows = runtime.read_all(
                f"""SELECT provider_id,
                           SUM(calls) AS calls,
                           SUM(requests) AS requests,
                           SUM(total_tokens) AS total_tokens,
                           SUM(input_tokens) AS input_tokens,
                           SUM(cached_input_tokens) AS cached_input_tokens,
                           SUM(cache_creation_tokens) AS cache_creation_tokens,
                           SUM(output_tokens) AS output_tokens
                    FROM {OBSERVED_MODEL_CATALOG_TABLE}
                    GROUP BY provider_id""",
                (),
            )
    except sqlite3.OperationalError:
        return _query_observed_catalog_fallback(runtime, role_key_value=role_key_value)
    return [
        {
            "provider_id": row["provider_id"],
            "calls": int(row["calls"] or 0),
            "requests": int(row["requests"] or 0),
            "total_tokens": int(row["total_tokens"] or 0),
            "input_tokens": int(row["input_tokens"] or 0),
            "cached_input_tokens": int(row["cached_input_tokens"] or 0),
            "cache_creation_tokens": int(row["cache_creation_tokens"] or 0),
            "output_tokens": int(row["output_tokens"] or 0),
        }
        for row in rows
    ]


def record_observation(runtime, context, record):
    """Revision fences let asynchronous snapshots finish in any order."""
    _validate_record(record)
    _ensure_schema(runtime)
    raw_ctx = encode(context)
    scope_kind = context.scope.kind.value
    role_binding_key = encode(context.role_binding)
    rk = role_key(context)
    turn_id = record["turn_id"]
    new_rev = int(record["revision"])
    with runtime.transaction() as connection:
        existing = connection.execute(
            f"SELECT rowid, revision FROM {TABLE} WHERE turn_id = ?",
            (turn_id,),
        ).fetchone()
        if existing is not None and int(existing["revision"]) >= new_rev:
            return
        stored_ctx, ctx_hash = store_context_json(
            connection,
            raw_ctx,
            context_key=context.key,
            scope_kind=scope_kind,
            role_binding_key=role_binding_key,
            created_at=record["received_at"],
        )
        cur = connection.execute(
            f"""INSERT INTO {TABLE}
            (turn_id, revision, role_key, context_key, scope_kind, context_json, received_at, observed_at, record_json, context_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(turn_id) DO UPDATE SET revision = excluded.revision,
                observed_at = excluded.observed_at, record_json = excluded.record_json
            WHERE excluded.revision > {TABLE}.revision""",
            (
                turn_id,
                new_rev,
                rk,
                context.key,
                scope_kind,
                stored_ctx,
                record["received_at"],
                record["observed_at"],
                encode(record),
                ctx_hash,
            ),
        )
        _sync_turn_observed_catalog(
            connection,
            turn_id=turn_id,
            role_key_value=rk,
            revision=new_rev,
            observed_at=record["observed_at"],
            record=record,
        )
        wm = _read_catalog_watermark_in_tx(connection)
        if wm is not None and wm.get("backfilled") is True:
            new_rowid = int(existing["rowid"]) if existing is not None else int(cur.lastrowid or 0)
            old_rev = int(existing["revision"]) if existing is not None else 0
            _write_catalog_watermark_in_tx(
                connection,
                {
                    "version": 1,
                    "backfilled": True,
                    "row_count": int(wm.get("row_count") or 0) + (0 if existing is not None else 1),
                    "max_rowid": max(int(wm.get("max_rowid") or 0), new_rowid),
                    "revision_sum": int(wm.get("revision_sum") or 0) + (new_rev - old_rev),
                },
            )
        elif wm is None and existing is None:
            # Fresh empty store: if this is the sole observation row, mark catalog synced immediately.
            other = connection.execute(
                f"SELECT 1 FROM {TABLE} WHERE turn_id != ? LIMIT 1",
                (turn_id,),
            ).fetchone()
            if other is None:
                _write_catalog_watermark_in_tx(
                    connection,
                    {
                        "version": 1,
                        "backfilled": True,
                        "row_count": 1,
                        "max_rowid": int(cur.lastrowid or 0),
                        "revision_sum": new_rev,
                    },
                )


def _parse_utc_timestamp(value):
    if type(value) is not str or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _query_observations(runtime, context, *, start=None, end=None, private_only=False):
    if not available(runtime):
        return []
    where, values = ["role_key = ?"], [role_key(context)]
    start_dt = _parse_utc_timestamp(start) if start is not None else None
    end_dt = _parse_utc_timestamp(end) if end is not None else None
    if start is not None:
        if start_dt is not None:
            where.append("received_at >= ?")
            values.append((start_dt - timedelta(days=1)).isoformat())
        else:
            where.append("julianday(received_at) >= julianday(?)")
            values.append(start)
    if end is not None:
        if end_dt is not None:
            where.append("received_at <= ?")
            values.append((end_dt + timedelta(days=1)).isoformat())
        else:
            where.append("julianday(received_at) <= julianday(?)")
            values.append(end)
    if private_only:
        where.append("scope_kind = 'private'")
    result = []
    for row in runtime.read_all(
        "SELECT scope_kind, context_key, context_json, received_at, record_json "
        "FROM astrbot_core_reply_observations WHERE " + " AND ".join(where)
        + " ORDER BY received_at DESC, turn_id DESC",
        tuple(values),
    ):
        row_dt = _parse_utc_timestamp(row["received_at"]) if (start_dt is not None or end_dt is not None) else None
        if start_dt is not None and (row_dt is None or row_dt < start_dt):
            continue
        if end_dt is not None and (row_dt is None or row_dt > end_dt):
            continue
        try:
            record = json.loads(row["record_json"])
            if type(record) is dict and record.get("version") == 1:
                obs = {
                    **record,
                    "context_key": row["context_key"],
                    "context_json": resolve_context_json(runtime, row["context_json"]),
                }
                result.append((obs, row["scope_kind"], row_dt))
        except (TypeError, ValueError):
            continue
    return result


def observations_for_role(runtime, context, *, start=None, end=None, private_only=False):
    return [obs for obs, _scope_kind, _row_dt in _query_observations(
        runtime, context, start=start, end=end, private_only=private_only)]


def observations_with_scope_for_role(runtime, context, *, start=None, end=None, private_only=False):
    return _query_observations(runtime, context, start=start, end=end, private_only=private_only)


def request_rows(observations, *, include_signature_json=True):
    """Translate observations into usage projections, never into the old ledger."""
    result = []
    provider_json_cache = {}
    context_scope_cache = {}
    for observation in observations:
        requests = observation.get("requests", [])
        if not requests:
            continue
        context_key = observation.get("context_key")
        if context_key is not None and context_key in context_scope_cache:
            audience_scope = context_scope_cache[context_key]
        else:
            audience_scope = json.loads(observation["context_json"])["audience"]["scope"]
            if context_key is not None:
                context_scope_cache[context_key] = audience_scope
        for request in requests:
            usage = request.get("usage") or {}
            at = request.get("finished_at") or request.get("started_at") or observation["received_at"]
            tokens, inputs, output = usage.get("tokens"), usage.get("input_tokens"), usage.get("output_tokens")
            cached, creation = usage.get("cached_input_tokens"), usage.get("cache_creation_tokens")
            reported = type(inputs) is int and type(cached) is int
            real_call = request.get("measurement_basis", "provider_response") == "provider_response"
            latency = request.get("latency_ms")
            state = request.get("state", "unknown")
            provider = request.get("provider", "")
            if type(provider) is str:
                provider_json = provider_json_cache.get(provider)
                if provider_json is None:
                    provider_json = encode({"provider": provider})
                    provider_json_cache[provider] = provider_json
            else:
                provider_json = encode({"provider": provider})
            signature = request.get("signature")
            values = dict.fromkeys(USAGE_FIELDS, 0)
            values.update(calls=int(real_call), tokens=tokens or 0, unconfirmed_calls=int(real_call and tokens is None),
                          unknown_calls=int(real_call and state == "unknown"), failures=int(real_call and state == "failed"),
                          active_calls=int(real_call and state == "started"),
                          input_tokens=inputs or 0, output_tokens=output or 0,
                          cache_reported_calls=int(real_call and reported), cache_telemetry_calls=int(real_call and (cached is not None or creation is not None)),
                          cache_input_tokens=(inputs or 0) if reported else 0,
                          cached_input_tokens=(cached or 0) if reported else 0,
                          cache_creation_tokens=creation or 0,
                          cache_other_input_tokens=max(0, inputs - cached - (creation or 0)) if reported else 0,
                          cache_low_calls=int(reported and inputs >= 1000 and cached * 5 < inputs),
                          latency_total_ms=latency or 0, latency_samples=int(latency is not None))
            row = {**values, "day": at[:10], "hour": at[11:13], "kind": CORE_CHAIN_KIND,
                "provider": provider, "provider_json": provider_json,
                "call_id": observation["turn_id"] + ":" + str(request["position"]), "position": request["position"],
                "origin_key": observation["context_key"], "audience_scope_json": audience_scope,
                "signature_json": encode(signature) if (include_signature_json and signature) else None,
                "state": state, "actual_tokens": tokens, "actual_cost": None,
                "input_tokens": inputs, "output_tokens": output, "cached_input_tokens": cached,
                "cache_creation_tokens": creation, "latency_ms": latency,
                "failure_code": request.get("failure_code"), "response_summary_json": None,
                "created_at": observation["received_at"], "started_at": request.get("started_at"), "finished_at": request.get("finished_at"),
                "execution_path": "astrbot_core", "usage_scope": request.get("measurement_basis", "provider_response"),
                "token_availability": usage.get("availability", "unavailable"), "cost_availability": "unavailable",
                "cache_availability": usage.get("cache_availability", "unavailable"),
                "signature_scope": request.get("signature_scope", "unavailable"),
                "aggregate": values}
            if not include_signature_json and signature:
                row["signature"] = signature
            result.append(row)
    return result


def availability_summary(observations):
    requests = [request for item in observations for request in item.get("requests", [])]
    return {"turns": len(observations), "observed_requests": sum(item.get("measurement_basis") == "provider_response" for item in requests),
            "fallback_usage_observations": sum(item.get("measurement_basis") != "provider_response" for item in requests),
            "request_observation_turns": sum(item.get("request_observation_available") is True for item in observations),
            "complete_request_observation_turns": sum(item.get("request_observation_complete") is True for item in observations),
            "partial_request_observation_turns": sum(item.get("tool_requery_observed") is True for item in observations),
            "usage_reported_requests": sum(type((item.get("usage") or {}).get("tokens")) is int for item in requests),
            "cache_reported_requests": sum(type((item.get("usage") or {}).get("cached_input_tokens")) is int for item in requests),
            "cost_reported_requests": 0, "cost_availability": "unavailable",
            "request_count_basis": "observed_provider_attempts_or_explicit_last_response_fallback",
            "platform_delivery_confirmation": "unavailable"}
