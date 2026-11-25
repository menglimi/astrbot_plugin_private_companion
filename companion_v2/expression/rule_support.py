"""Additional, source-bound observations of an existing expression form.

The original run and its evidence remain immutable. Later observations keep
only handles and their exact source window; raw conversation text is reread
under the current authorization and sample-review policy when displayed.
"""

import json

from companion_v2.execution.models import digest, encode


PREFIX = "expression.rule-support:"
MAX_RECENT_OBSERVATIONS = 64


def _key(signal_id):
    return PREFIX + digest(signal_id)


def observations(runtime, signal_id, *, connection=None):
    if connection is not None:
        row = connection.execute("SELECT value FROM runtime_metadata WHERE key = ?", (_key(signal_id),)).fetchone()
    else:
        row = runtime.read_one("SELECT value FROM runtime_metadata WHERE key = ?", (_key(signal_id),))
    if row is None:
        return ()
    try:
        values = json.loads(row["value"])
        if type(values) is not list:
            return ()
        for item in values:
            if (type(item) is not dict or set(item) != {"window", "handles"}
                    or type(item["window"]) is not dict or type(item["handles"]) is not list
                    or set(item["window"]) != {"opened_at", "closed_at", "source_kind", "context_key"}
                    or any(type(value) is not str for value in item["window"].values())
                    or item["window"]["source_kind"] != "conversation_input"
                    or any(type(handle) is not str for handle in item["handles"])):
                return ()
        return tuple(values)
    except (TypeError, ValueError):
        return ()


def _write_entries(runtime, signal_id, entries, *, connection=None):
    payload = encode(entries[-MAX_RECENT_OBSERVATIONS:])
    if connection is not None:
        connection.execute("INSERT INTO runtime_metadata(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (_key(signal_id), payload))
    else:
        with runtime.transaction() as active:
            active.execute("INSERT INTO runtime_metadata(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (_key(signal_id), payload))


def record(runtime, signal_id, *, window, handles, original_handles, connection=None):
    """Keep genuinely new cited handles without changing the adopted rule."""
    if (type(window) is not dict or window.get("source_kind") != "conversation_input"
            or type(window.get("handles")) is not list or type(handles) not in (list, tuple)
            or any(handle not in window["handles"] for handle in handles)
            or any(type(window.get(name)) is not str for name in ("opened_at", "closed_at", "source_kind", "context_key"))):
        return False
    previous = observations(runtime, signal_id, connection=connection)
    known = set(original_handles)
    known.update(handle for item in previous for handle in item["handles"])
    added = sorted(set(handles) - known)
    if not added:
        return False
    entry = {"window": {name: window[name] for name in
             ("opened_at", "closed_at", "source_kind", "context_key")}, "handles": added}
    _write_entries(runtime, signal_id, (*previous, entry), connection=connection)
    return True


def merge_from(runtime, target_signal_id, source_signal_id, *, source_window=None, source_handles=(),
               target_original_handles=(), connection=None):
    """Transfer conversation-input support observations from a merged duplicate into the survivor."""
    added_any = False
    if source_window is not None and source_handles:
        if record(runtime, target_signal_id, window=source_window, handles=source_handles,
                  original_handles=target_original_handles, connection=connection):
            added_any = True
    extra_entries = observations(runtime, source_signal_id, connection=connection)
    if not extra_entries:
        return added_any
    previous = observations(runtime, target_signal_id, connection=connection)
    known = set(target_original_handles)
    known.update(handle for item in previous for handle in item["handles"])
    merged = list(previous)
    for item in extra_entries:
        added = sorted(set(item["handles"]) - known)
        if not added:
            continue
        known.update(added)
        merged.append({"window": dict(item["window"]), "handles": added})
        added_any = True
    if added_any and len(merged) > len(previous):
        _write_entries(runtime, target_signal_id, tuple(merged), connection=connection)
    return added_any
