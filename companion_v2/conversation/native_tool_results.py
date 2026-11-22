"""Read settled native operations for domain consumers without a reply batch."""

import json
from dataclasses import replace

from companion_v2.execution.models import TaskError, TaskSpec, encode, digest, require
from .foreground import PromptMaterial
from .messages import source_from_document
from .tool_failures import failure_guidance, retry_disposition_for


PREFIX = "astrbot.core-tool."
PREFIX_END = PREFIX[:-1] + chr(ord(PREFIX[-1]) + 1)


def operation_row(runtime, invocation_id):
    saved = runtime.read_one("SELECT value FROM runtime_metadata WHERE key = ?", (PREFIX + invocation_id,))
    if saved is None:
        return None
    native = json.loads(saved["value"])
    if not native.get("provider_json") or not native.get("task_id"):
        return None
    task = runtime.read_one("SELECT created_at, updated_at FROM execution_tasks WHERE task_id = ?",
                            (native["task_id"],))
    if task is None:
        raise TaskError("external_task_unresolved", "the native operation has no original task")
    descriptor = json.loads(native["provider_json"])
    result = native.get("result") or {}
    return {**native, "execution_path": "astrbot_core", "batch_id": native["turn_id"],
        "parent_task_id": native["turn_id"], "tool_call_id": native["call_id"],
        "descriptor_json": native["provider_json"], "provider_json": encode(descriptor["binding"]),
        "result_source_json": encode(native.get("source")), "result_hash": native.get("source_hash"),
        "operation_id": result.get("operation_id"), "receipt_ref": result.get("receipt_ref"),
        "error_code": result.get("code"), "retry_disposition": result.get("retry_disposition"),
        "created_at": task["created_at"],
        "finished_at": task["updated_at"] if native["state"] in ("succeeded", "failed", "unknown") else None}


def operation_rows(runtime, context, actions, *, after=0, limit=128, newest_first=False, states=()):
    actions = tuple(actions)
    if not actions:
        return ()
    states = tuple(states)
    placeholders = ",".join("?" for _ in actions)
    state_filter = (" AND json_extract(value, '$.state') IN ("
                    + ",".join("?" for _ in states) + ")") if states else ""
    order = "DESC" if newest_first else "ASC"
    rows = runtime.read_all(f"""SELECT rowid AS scan_id, json_extract(value, '$.invocation_id') AS invocation_id
        FROM runtime_metadata WHERE key >= ? AND key < ? AND rowid > ?
        AND json_extract(value, '$.origin_key') = ? AND json_extract(value, '$.action') IN ({placeholders})
        AND json_extract(value, '$.task_id') IS NOT NULL{state_filter} ORDER BY rowid {order} LIMIT ?""",
        (PREFIX, PREFIX_END, after, context.key, *actions, *states, limit))
    return tuple({**operation_row(runtime, row["invocation_id"]), "scan_id": row["scan_id"]} for row in rows)


def snapshot(external, row, context, at):
    current = operation_row(external.runtime, row["invocation_id"])
    if current is None or current != {key: value for key, value in row.items() if key != "scan_id"}:
        raise TaskError("external_invocation_unavailable", "the native operation changed")
    saved = external.runtime.read_one("SELECT value FROM runtime_metadata WHERE key = ?", (PREFIX + row["invocation_id"],))
    native = json.loads(saved["value"]) if saved is not None else {}
    native_result = native.get("result")
    if (current["state"] in ("succeeded", "failed") or native_result is not None) and (
            not isinstance(native_result, dict)
            or (native_result.get("state"), native_result.get("effect"), native_result.get("invocation_id"),
                native_result.get("operation_id"), native_result.get("receipt_ref")) != (
                    current["state"], current["effect"], current["invocation_id"],
                    current["operation_id"], current["receipt_ref"])):
        raise TaskError("external_invocation_unavailable", "the native receipt changed")
    descriptor = external._descriptor(current)
    external.admit(descriptor, context, at)
    if current["origin_key"] != context.key:
        raise TaskError("external_invocation_unavailable", "the native operation belongs to another conversation")
    require(external.tasks.check_context_access(context, action="conversation.reply", observed_at=at))
    task = external.runtime.read_one("SELECT * FROM execution_tasks WHERE task_id = ? AND origin_key = ?",
                                     (current["task_id"], context.key))
    data = json.loads(task["record_json"]) if task else {}
    definition = replace(descriptor.definition(), kind=PREFIX + digest(descriptor), owner="astrbot.core-tool")
    if not task or data.get("definition") != json.loads(encode(definition)):
        raise TaskError("external_task_mismatch", "the native task implementation or provider binding changed")
    spec = TaskSpec.restore(data["spec"])
    if (spec.kind, spec.request_ref, spec.context.key) != (definition.kind, current["invocation_id"], context.key):
        raise TaskError("external_task_mismatch", "the native task belongs to another operation")
    state = current["state"] if current["state"] in ("succeeded", "failed") else "unknown"
    result = {key: current[key] for key in ("invocation_id", "effect", "operation_id", "receipt_ref")}
    result.update(state=state, code=current["error_code"],
                  retry_disposition=current.get("retry_disposition") or
                  retry_disposition_for(current["error_code"], state))
    result.update(failure_guidance(current["error_code"], state, action=current["action"]))
    material = None
    if state == "succeeded":
        if (task["state"], data["effect"], data.get("operation_id"), data.get("result_ref"),
                data.get("receipt_ref")) != (
                "succeeded", current["effect"], current["operation_id"], current["invocation_id"],
                current["receipt_ref"]):
            raise TaskError("external_task_unresolved", "the native task has not accepted its final receipt")
        from .external_tools import _SourceAccess
        reference = source_from_document(json.loads(current["result_source_json"]))
        source = require(external.messages.sources.read(reference, resolved=context.actor,
            capability=descriptor.binding.capability, observed_at=at,
            authorizer=_SourceAccess(external, descriptor, context)))
        if digest(source) != current["result_hash"] or source.audience != context.audience:
            raise TaskError("tool_result_changed", "the native original changed")
        material = PromptMaterial(reference.owner, "tool_source:" + current["tool_call_id"], source.payload, reference)
    return result, material
