"""Concrete expression forms, kept beside the existing review and evidence ledger."""

import json

from companion_v2.execution.models import digest, encode, stamp


PREFIX = "expression.rule-form:"
MERGE_PREFIX = "expression.rule-merge:"
DEDUPE_OP_PREFIX = "expression.rule-dedupe-op:"
LIMITS = {"situation": 240, "pattern": 500, "avoid": 400}
_READ_BATCH_SIZE = 400


def normalize(raw):
    if type(raw) is not dict or set(raw) != {"kind", *LIMITS} or raw["kind"] not in ("style", "grammar"):
        raise ValueError("invalid_expression_form")
    result = {"kind": raw["kind"]}
    for name, maximum in LIMITS.items():
        value = raw[name]
        if type(value) is not str or len(value) > maximum or (name != "avoid" and not value.strip()):
            raise ValueError("invalid_expression_form")
        result[name] = value.strip()
    return result


def read(runtime, signal_id):
    row = runtime.read_one("SELECT value FROM runtime_metadata WHERE key = ?", (PREFIX + digest(signal_id),))
    return json.loads(row["value"]) if row else None


def read_many(runtime, signal_ids):
    """Read stored forms in bounded batches to avoid SQLite parameter limits."""
    keys = {PREFIX + digest(signal_id): signal_id for signal_id in signal_ids}
    if not keys:
        return {}
    key_list = list(keys)
    result = {}
    for start in range(0, len(key_list), _READ_BATCH_SIZE):
        batch = key_list[start:start + _READ_BATCH_SIZE]
        rows = runtime.read_all(
            "SELECT key, value FROM runtime_metadata WHERE key IN (" + ",".join("?" * len(batch)) + ")",
            tuple(batch))
        for row in rows:
            result[keys[row["key"]]] = json.loads(row["value"])
    return result


def read_merge(runtime, signal_id):
    row = runtime.read_one("SELECT value FROM runtime_metadata WHERE key = ?", (MERGE_PREFIX + digest(signal_id),))
    if not row:
        return None
    try:
        value = json.loads(row["value"])
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) and isinstance(value.get("merged_into"), str) else None


def read_merges_many(runtime, signal_ids):
    """Batch-read reversible merge-audit markers keyed by signal_id."""
    keys = {MERGE_PREFIX + digest(signal_id): signal_id for signal_id in signal_ids}
    if not keys:
        return {}
    key_list = list(keys)
    result = {}
    for start in range(0, len(key_list), _READ_BATCH_SIZE):
        batch = key_list[start:start + _READ_BATCH_SIZE]
        rows = runtime.read_all(
            "SELECT key, value FROM runtime_metadata WHERE key IN (" + ",".join("?" * len(batch)) + ")",
            tuple(batch))
        for row in rows:
            try:
                value = json.loads(row["value"])
            except (TypeError, ValueError):
                continue
            if isinstance(value, dict) and isinstance(value.get("merged_into"), str):
                result[keys[row["key"]]] = value
    return result


def write_merge(runtime, signal_id, *, merged_into, previous_state, at, actor, operation_key, connection=None):
    """Record a reversible merge marker without mutating the rule form itself."""
    value = {
        "signal_id": signal_id,
        "merged_into": merged_into,
        "previous_state": previous_state,
        "merged_at": stamp(at),
        "merged_by": actor,
        "operation_key": operation_key,
    }
    key = MERGE_PREFIX + digest(signal_id)
    payload = encode(value)
    if connection is not None:
        connection.execute(
            "INSERT INTO runtime_metadata(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, payload))
    else:
        with runtime.transaction() as active:
            active.execute(
                "INSERT INTO runtime_metadata(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, payload))
    return value


def write(runtime, signal_id, form, *, at, actor, operation_key, legacy=None):
    form = normalize(form)
    previous = read(runtime, signal_id)
    value = {"signal_id": signal_id, "form": form, "revision": (previous or {}).get("revision", 0) + 1,
        "updated_at": stamp(at), "updated_by": actor, "operation_key": operation_key,
        "legacy": (previous or {}).get("legacy", legacy or {})}
    with runtime.transaction() as connection:
        connection.execute("INSERT INTO runtime_metadata(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (PREFIX + digest(signal_id), encode(value)))
    return value


def form_for(runtime, row):
    record = read(runtime, row["signal_id"])
    return record["form"] if record else None


def legacy_counts(record):
    result = {}
    for name in ("use_count", "positive_feedback", "negative_feedback"):
        value = (record or {}).get("legacy", {}).get(name, 0)
        result[name] = max(0, value) if type(value) is int else 0
    return result


FORM_SCHEMA = {"type": "object", "properties": {
    "kind": {"type": "string", "enum": ["style", "grammar"]},
    **{name: {"type": "string", "maxLength": maximum} for name, maximum in LIMITS.items()},
}, "required": ["kind", *LIMITS], "additionalProperties": False}
