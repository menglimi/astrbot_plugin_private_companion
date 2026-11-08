"""Concrete expression forms, kept beside the existing review and evidence ledger."""

import json

from companion_v2.execution.models import digest, encode, stamp


PREFIX = "expression.rule-form:"
LIMITS = {"situation": 240, "pattern": 500, "avoid": 400}


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
    """Read a whole page of stored forms in one statement.

    A review page holds up to fifty rules and each row needs its own form. Asking
    for them one at a time costs one statement per row, and every statement waits
    on the storage runtime's single write lock, so the page cost grew with the
    number of rules rather than with the page. The missing-key result stays
    ``None`` in the caller, exactly as :func:`read` reports it.
    """
    keys = {PREFIX + digest(signal_id): signal_id for signal_id in signal_ids}
    if not keys:
        return {}
    rows = runtime.read_all(
        "SELECT key, value FROM runtime_metadata WHERE key IN (" + ",".join("?" * len(keys)) + ")",
        tuple(keys))
    return {keys[row["key"]]: json.loads(row["value"]) for row in rows}


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
