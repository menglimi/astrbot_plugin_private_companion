"""Strictly normalize OneBot peer-history response wrappers.

The installed SDK unwraps the action's data. Injected readers may also keep an
envelope. The caller still validates the exact private peer, message id and
component contract; this helper only removes a single transport wrapper.
"""

from __future__ import annotations

import re


_SIGNED_INT_RE = re.compile(r"^-?\d+$")


def parse_signed_int(value):
    """Return ``int(value)`` when ``value`` is a canonical signed decimal integer, else ``None``."""
    if type(value) is int:
        return value
    if type(value) is str:
        stripped = value.strip()
        if _SIGNED_INT_RE.fullmatch(stripped):
            return int(stripped)
    return None


def _record_message_id_matches(record, expected_message_id: str) -> bool:
    if not isinstance(record, dict):
        return False
    actual = record.get("message_id")
    if type(actual) not in (str, int):
        return False
    return str(actual).strip() == expected_message_id


async def fetch_onebot_history_record(
    call_action,
    action: str,
    *,
    expected_message_id: str,
    primary_params: dict,
    fallback_params: dict | None = None,
    after_call=None,
):
    """Fetch one anchored OneBot history record across NapCat and SnowLuma.

    Calls ``action`` with ``primary_params`` first (NapCat ``message_seq``
    convention).  When a OneBot server such as SnowLuma ignores ``message_seq``
    and defaults ``message_id`` to 0—returning empty results or an unanchored
    message whose ``message_id`` disagrees with ``expected_message_id``—retries
    once with ``fallback_params`` (integer ``message_id`` anchor) and accepts
    only an exact unique match for ``expected_message_id``.
    """
    expected = str(expected_message_id).strip()
    primary_error = None
    try:
        primary_raw = await call_action(action, primary_params)
    except Exception as error:
        if fallback_params is None or type(error).__name__ in ("TaskError", "TimeoutError", "CancelledError"):
            raise
        primary_error = error
        primary_raw = None
    if after_call is not None:
        after_call()
    records = extract_history_messages(primary_raw)
    if isinstance(records, list) and len(records) == 1 and _record_message_id_matches(records[0], expected):
        return records[0], records
    if fallback_params is not None:
        try:
            fallback_raw = await call_action(action, fallback_params)
        except Exception:
            fallback_raw = None
        if after_call is not None:
            after_call()
        fallback_records = extract_history_messages(fallback_raw)
        if isinstance(fallback_records, list):
            if len(fallback_records) == 1 and _record_message_id_matches(fallback_records[0], expected):
                return fallback_records[0], fallback_records
            exact = [item for item in fallback_records if _record_message_id_matches(item, expected)]
            if len(exact) == 1:
                return exact[0], exact
    if primary_error is not None:
        raise primary_error
    if isinstance(records, list) and len(records) == 1 and isinstance(records[0], dict):
        return records[0], records
    return None, records


def extract_history_messages(result):
    """Return one unwrapped ``messages`` list, or ``None`` for an unknown shape.

    Normalize these explicit container forms, without inferring platform support:
    a direct list, ``{"messages": [...]}``, ``{"data": [...]}``, or
    ``{"data": {"messages": [...]}}``.  Ambiguous envelopes are rejected so a
    stale or mixed response cannot silently select a different message list.
    """

    if isinstance(result, list):
        return result
    if not isinstance(result, dict):
        return None
    for depth in range(2):
        if "status" in result and result["status"] != "ok":
            return None
        if "retcode" in result and (
                type(result["retcode"]) not in (int, str) or result["retcode"] not in (0, "0")):
            return None
        if "messages" in result:
            # Even a malformed second payload is ambiguous, not a fallback.
            messages = result["messages"]
            return messages if "data" not in result and isinstance(messages, list) else None
        if depth:
            return None
        result = result.get("data")
        if isinstance(result, list):
            return result
        if not isinstance(result, dict):
            return None
    return None
