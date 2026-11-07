"""Durable private output checkpoints and explicit, bounded repair attempts."""

import json
from datetime import datetime, timedelta

from companion_v2.execution.models import TERMINAL, TaskContext, TaskError, digest, encode, require, stamp

from . import codec
from .models import owner_key
from .reflection_output import OutputProblem, parse_output, proposal_for_repair, redact_output, validate_output


# A batch stays ``running`` only while a worker owns it. After a process restart
# no worker does: the model call that was in flight is unknown and must not be
# replayed, but the batch cannot keep the dashboard "running" forever either.
ABANDONED_RUNNING_AFTER = timedelta(hours=2)


def abandon_stale_running(reflection, context, batch_id, at, *, older_than=ABANDONED_RUNNING_AFTER):
    """Settle one interrupted ``running`` batch that no worker can still own.

    Returns ``True`` only when this call changed the row. A batch updated within
    ``older_than`` is left alone: a fresh process is not the only writer, and a
    live worker owns its own row.
    """
    with reflection.runtime.transaction() as connection:
        row = connection.execute("SELECT * FROM memory_reflections WHERE batch_id = ? AND owner_key = ?",
                                 (batch_id, owner_key(context))).fetchone()
        if (row is None or row["state"] != "running"
                or TaskContext.restore(json.loads(row["context_json"])).key != context.key):
            return False
        try:
            updated = datetime.fromisoformat(row["updated_at"])
        except (TypeError, ValueError):
            updated = None
        if updated is not None and at - updated < older_than:
            return False
        result = json.loads(row["result_json"] or '{"summary":null}')
        result.setdefault("issues", []).append({"code": "memory_reflection_abandoned", "path": "$"})
        connection.execute("""UPDATE memory_reflections SET state = 'failed', result_json = ?, updated_at = ?
            WHERE batch_id = ? AND state = 'running'""",
            (encode(result), stamp(at), batch_id))
    return True


REPAIRABLE = frozenset({"invalid_json", "duplicate_json_field", "nonfinite_json_number", "invalid_shape",
    "invalid_summary", "invalid_proposals", "invalid_decisions", "invalid_proposal", "invalid_details",
    "invalid_qualifiers", "invalid_item_id", "duplicate_item_id", "related_item_unresolved", "invalid_decision",
    "invalid_anniversaries", "unknown_evidence", "unknown_target", "ambiguous_target", "unexpected_repair_item",
    "conflicting_target", "overlapping_pending_decision", "output_invalid", "output_length", "output_oversized",
    "missing_repair_item"})
STALE_INPUTS = frozenset({"memory_reflection_stale", "memory_settings_changed", "memory_pending_changed",
    "memory_schedule_changed", "source_revoked", "source_expired", "memory_evidence_forgotten",
    "memory_revision_conflict", "memory_evidence_changed"})


def quarantine_stale(reflection, context, batch_id, code):
    if code not in STALE_INPUTS:
        return
    with reflection.runtime.transaction() as connection:
        row = connection.execute("SELECT * FROM memory_reflections WHERE batch_id = ? AND owner_key = ?",
                                 (batch_id, owner_key(context))).fetchone()
        if (row is None or TaskContext.restore(json.loads(row["context_json"])).key != context.key
                or row["state"] not in ("selecting", "prepared", "received", "modelled")):
            return
        result = json.loads(row["result_json"] or '{"summary":null}')
        result.setdefault("issues", []).append({"code": code, "path": "$"})
        receipts = json.loads(row["receipts_json"])["items"]
        if row["state"] == "modelled":
            manifest = json.loads(row["manifest_json"])
            for entry in result["proposals"]:
                if entry["item_id"] not in receipts:
                    result.setdefault("rejected", []).append({"item_id": entry["item_id"], "kind": "proposal",
                        "raw": proposal_for_repair(entry["proposal"], manifest), "problem": {"code": code, "path": "proposal"}})
            for decision in result.get("pending_decisions", []):
                identifier = "pending:" + decision["operation_id"]
                if identifier not in receipts:
                    result.setdefault("rejected", []).append({"item_id": identifier, "kind": "decision",
                        "raw": decision, "problem": {"code": code, "path": "pending_decisions"}})
        connection.execute("UPDATE memory_reflections SET state = ?, result_json = ?, updated_at = ? WHERE batch_id = ?",
                           ("partial" if receipts else "failed", encode(result), stamp(reflection.tasks.now()), batch_id))


def reconcile_prepared(reflection, context, batch_id):
    """A stopped task that never dispatched must not leave an unresumable prepared row."""
    with reflection.runtime.transaction() as connection:
        row = reflection._row(context, batch_id)
        if row["state"] != "prepared" or row["task_id"] is None:
            return row
        task = connection.execute("""SELECT t.state, a.slot_held FROM execution_tasks t
            LEFT JOIN execution_attempts a ON a.attempt_id = t.attempt_id WHERE t.task_id = ?""",
            (row["task_id"],)).fetchone()
        if task is None or task["state"] not in TERMINAL or task["slot_held"]:
            return row
        calls = connection.execute("SELECT state FROM model_calls WHERE task_id = ?", (row["task_id"],)).fetchall()
        unknown = task["state"] == "uncertain" or any(call["state"] not in ("not_started", "discarded") for call in calls)
        result = {"summary": None, "issues": [{"code": "memory_reflection_uncertain" if unknown else "memory_task_not_started",
                                              "path": "$"}]}
        connection.execute("UPDATE memory_reflections SET state = ?, result_json = ?, updated_at = ? WHERE batch_id = ?",
                           ("running" if unknown else "failed", encode(result), stamp(reflection.tasks.now()), batch_id))
        return reflection._row(context, batch_id)


def ensure_checkpoint_stopped(reflection, row):
    """Saved output is not permission to race a worker still holding its slot."""
    if row["task_id"] is None:
        return  # Compatibility with older locally staged results.
    task = reflection.runtime.read_one("SELECT state FROM execution_tasks WHERE task_id = ?", (row["task_id"],))
    held = reflection.runtime.read_one("SELECT 1 FROM execution_attempts WHERE task_id = ? AND slot_held = 1 LIMIT 1",
                                       (row["task_id"],))
    if task is None or task["state"] not in TERMINAL or held:
        raise TaskError("memory_task_worker_not_stopped", "the original worker has not been confirmed stopped")


def capture(reflection, run, completion):
    raw = completion.captured_output
    if raw is not None:
        body, reason = raw.text, raw.finish_reason
    elif completion.draft is not None:
        body, reason = completion.draft.text, "stop"
    else:
        body, reason = "", "invalid"
    if len(body) > 65536:
        body, reason = body[:65536], "oversized"
    record = {"raw_output": {"text": redact_output(body), "finish_reason": reason, "call_id": completion.call_id},
              "summary": None, "proposals": [], "pending_decisions": [], "rejected": [], "issues": []}
    context, batch_id = run.task.spec.context, run.task.spec.request_ref
    with reflection.runtime.transaction() as connection:
        run.validate()
        run.begin_effect()
        row = reflection._row(context, batch_id)
        reflection.memory.writer._reserve(context, batch_id, digest([row["request_hash"], record]))
        run.record_operation(batch_id)
        connection.execute("UPDATE memory_reflections SET state = 'received', result_json = ?, updated_at = ? WHERE batch_id = ?",
                           (encode(record), stamp(reflection.tasks.now()), batch_id))


def consume(reflection, context, batch_id):
    with reflection.runtime.transaction() as connection:
        row = reflection._row(context, batch_id)
        if row["state"] != "received":
            raise TaskError("memory_reflection_not_received", "no unparsed captured output is available")
        manifest = reflection._validate_inputs(context, row)
        record = json.loads(row["result_json"])
        try:
            output = parse_output(record["raw_output"])
            parsed = validate_output(reflection, context, manifest, output)
            record.update(parsed)
            state = "modelled"
        except OutputProblem as error:
            record["issues"] = [{"code": error.code, "path": error.path}]
            state = "failed"
        connection.execute("UPDATE memory_reflections SET state = ?, result_json = ?, updated_at = ? WHERE batch_id = ?",
                           (state, encode(record), stamp(reflection.tasks.now()), batch_id))
    if state == "modelled":
        return require(reflection.apply(context, batch_id))
    return require(reflection.status(context, batch_id))


def repair_allowed(row):
    if row["state"] not in ("failed", "partial") or not row["result_json"]:
        return False
    result = json.loads(row["result_json"])
    codes = [v["code"] for v in result.get("issues", [])]
    codes += [v["problem"]["code"] for v in result.get("rejected", [])]
    return bool(codes) and all(code in REPAIRABLE for code in codes)


def repair_material(row):
    """Never resend already-applied candidates as items to be executed again."""
    result = json.loads(row["result_json"] or "{}")
    rejected = result.get("rejected", [])
    if rejected:
        return {"items": rejected, "issues": result.get("issues", []),
                "instruction": "只修复这些原编号的待处理项；已保存项不在此次范围内。缺失信息回查本批次来源，不猜编号或版本。"}
    return {"previous_output": result.get("raw_output", {}).get("text", ""),
            "issues": result.get("issues", []),
            "instruction": "根据原始来源修正格式；不延续截断内容的猜测。保持简短，不编造事实、人物或来源。"}


def finish_parent(reflection, context, batch_id):
    """A completed repair resolves only its linked parent; receipts remain intact."""
    row = reflection._row(context, batch_id)
    if row["state"] not in ("complete", "empty"):
        return
    manifest = json.loads(row["manifest_json"])
    parent_id = manifest.get("retry_of")
    if not parent_id:
        return
    with reflection.runtime.transaction() as connection:
        parent = reflection._row(context, parent_id)
        receipts = json.loads(parent["receipts_json"])
        if receipts.get("repair_batch") != batch_id:
            raise TaskError("memory_repair_conflict", "the parent owns another repair attempt")
        receipts["items"].update(json.loads(row["receipts_json"])["items"])
        receipts["sequence"] = reflection._sequence(context)
        result = json.loads(parent["result_json"] or "{}")
        result["resolved_by"] = batch_id
        result["summary"] = json.loads(row["result_json"] or "{}").get("summary", result.get("summary"))
        connection.execute("UPDATE memory_reflections SET state = 'complete', result_json = ?, receipts_json = ?, updated_at = ? WHERE batch_id = ?",
                           (encode(result), encode(receipts), stamp(reflection.tasks.now()), parent_id))
        if connection.execute("SELECT 1 FROM operations WHERE operation_id = ?", (parent_id,)).fetchone():
            reflection.memory.writer._complete(parent_id)
    finish_parent(reflection, context, parent_id)


def current_schedule(reflection, context, reference, *, exact=True):
    row = reflection.runtime.read_one("SELECT * FROM memory_schedules WHERE schedule_id = ? AND owner_key = ?",
                                      (reference["schedule_id"], owner_key(context)))
    if (row is None or not row["enabled"] or (exact and row["revision"] != reference["revision"])
            or TaskContext.restore(json.loads(row["context_json"])).key != context.key):
        raise TaskError("memory_schedule_changed", "the reflection schedule was paused, replaced or rebound")
    return row
