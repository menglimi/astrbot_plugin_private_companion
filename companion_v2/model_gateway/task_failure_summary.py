"""Body-free model failure summary lookup for background tasks."""

from __future__ import annotations

import json

from .response_diagnostics import checked_response_diagnostics


def read_task_failure_summary(runtime, task_id: str) -> dict[str, object]:
    """Return safe, body-free failure metadata for one task's model calls.

    Reads only fixed failure codes, validated HTTP status codes (100..599),
    provider identifiers, and whether more than one model call was attempted
    (e.g. a configured fallback provider). Never returns raw provider text.
    """
    if not isinstance(task_id, str) or not task_id:
        return {}
    rows = runtime.read_all(
        """SELECT c.position, c.provider_json, f.code, d.summary_json
        FROM model_calls c
        LEFT JOIN model_call_failures f USING(call_id)
        LEFT JOIN model_call_diagnostics d USING(call_id)
        WHERE c.task_id = ?
        ORDER BY c.position ASC""",
        (task_id,),
    )
    if not rows:
        return {}
    failed_rows = [row for row in rows if row["code"]]
    target = failed_rows[-1] if failed_rows else rows[-1]
    summary: dict[str, object] = {}
    if target["code"]:
        summary["code"] = target["code"]
    provider = _provider_id(target["provider_json"])
    if provider:
        summary["provider"] = provider
    diagnostics = checked_response_diagnostics(target["summary_json"])
    if isinstance(diagnostics, dict) and diagnostics.get("status_code") is not None:
        summary["http_status"] = diagnostics["status_code"]
    summary["fallback_attempted"] = len(rows) > 1 or any(
        isinstance(row["position"], int) and row["position"] > 0 for row in rows
    )
    return summary


def _provider_id(raw: object) -> str | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None
    value = parsed.get("provider")
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned[:128] if cleaned else None
