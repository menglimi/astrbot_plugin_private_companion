"""Durable planning feedback and new observations of the remaining local day."""

from datetime import datetime, timedelta
import json

from companion_v2.execution.models import encode, stamp


REVIEW_HOURS = (0, 6, 12, 18)
RETRY_MINUTES = (5, 15, 30)


def result_key(planner, context):
    return "calendar.last-result.v1:" + planner.app.calendar.records.role_key(context)


def save_result(planner, context, result):
    key = result_key(planner, context)
    row = planner.runtime.read_one("SELECT value FROM runtime_metadata WHERE key = ?", (key,))
    previous = json.loads(row["value"]) if row else None
    if previous and {k: v for k, v in previous.items() if k != "observed_at"} == result:
        return previous
    result = {**result, "observed_at": stamp(planner.tasks.now())}
    with planner.runtime.transaction() as connection:
        connection.execute("""INSERT INTO runtime_metadata(key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value""", (key, encode(result)))
        if result.get("task_id"):
            connection.execute("""INSERT INTO runtime_metadata(key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
                ("calendar.task-result.v1:" + result["task_id"], encode(result)))
    return result


def stored_result(planner, context):
    row = planner.runtime.read_one("SELECT value FROM runtime_metadata WHERE key = ?", (result_key(planner, context),))
    return json.loads(row["value"]) if row else None


def task_result(planner, context, identifier):
    if identifier is None:
        return None
    row = planner.runtime.read_one("SELECT state, updated_at, record_json FROM execution_tasks WHERE task_id = ?", (identifier,))
    if row is None:
        return None
    data = json.loads(row["record_json"])
    operation = planner.operations.get(identifier)
    operation_state = operation.unwrap().state.value if operation.is_success else None
    saved = planner.runtime.read_one("SELECT value FROM runtime_metadata WHERE key = ?",
                                     ("calendar.task-result.v1:" + identifier,))
    last = json.loads(saved["value"]) if saved else stored_result(planner, context)
    report = last if last and last.get("task_id") == identifier else {
        "task_id": identifier, "state": row["state"], "code": data.get("reason"), "observed_at": row["updated_at"]}
    if report.get("code") in ("worker_error", "model_usage_unknown"):
        failure = planner.runtime.read_one("""SELECT f.code FROM model_calls c
            JOIN model_call_failures f USING(call_id) WHERE c.task_id = ? ORDER BY c.position DESC LIMIT 1""", (identifier,))
        if failure is not None:
            report = {**report, "code": failure["code"]}
    return {**report, "effect": data.get("effect", "none"),
            "committed": operation_state == "completed", "operation_unknown": operation_state == "unknown"}


def needs_review(result):
    return (result is not None and result["state"] in ("failed", "expired", "cancelled", "dead_letter")
            and result["effect"] in ("none", "not_committed")
            and not result["committed"] and not result["operation_unknown"])


def daily_observation(planner, context, slot, local):
    """A new daypart re-plans only future time; never release or replay an old slot.

    A terminal task with no local effect can be superseded by a later observation.
    Committed/uncertain effects and intentional empty plans remain final. Missed
    dayparts are not caught up, and each daypart has its own durable marker.
    """
    result = task_result(planner, context, planner._automatic_task_id(context, slot))
    for hour in REVIEW_HOURS:
        if hour > local.hour:
            break
        candidate = slot + ":review:" + str(hour)
        identifier = planner._automatic_task_id(context, candidate)
        if identifier is not None:
            result = task_result(planner, context, identifier)
    if not needs_review(result):
        return result, None, None
    observed_at = datetime.fromisoformat(result["observed_at"]).astimezone(local.tzinfo)
    # A model can corrupt one JSON response and then succeed immediately on a
    # retry.  Give the failed automatic slot short backoff attempts before the
    # sparse daypart review, instead of leaving the role without a plan for up
    # to six hours.
    for minutes in RETRY_MINUTES:
        if local >= observed_at + timedelta(hours=1):
            break
        candidate = slot + ":retry:" + str(minutes)
        if planner.attempted(context, candidate, automatic=True):
            continue
        ready_at = max(local, observed_at + timedelta(minutes=minutes))
        return result, candidate if ready_at <= local else None, ready_at
    earliest = observed_at + timedelta(hours=1)
    midnight = (local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    for index, hour in enumerate(REVIEW_HOURS):
        start = local.replace(hour=hour, minute=0, second=0, microsecond=0)
        end = (local.replace(hour=REVIEW_HOURS[index + 1], minute=0, second=0, microsecond=0)
               if index + 1 < len(REVIEW_HOURS) else midnight)
        if end <= local:
            continue  # Missed dayparts are never caught up.
        candidate = slot + ":review:" + str(hour)
        if planner.attempted(context, candidate, automatic=True):
            continue
        ready_at = max(local, start, earliest)
        if ready_at < end:
            return result, candidate if ready_at == local else None, ready_at
    return result, None, None
