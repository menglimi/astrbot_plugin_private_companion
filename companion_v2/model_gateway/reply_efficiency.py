"""Role-scoped reply latency projections from persisted conversation receipts."""

from datetime import datetime, timedelta, timezone
from math import ceil, isfinite
import json

from .response_diagnostics import checked_response_diagnostics
from .core_observations import observations_for_role


SAMPLE_LIMIT = 48


def _parse_time(value):
    if type(value) is not str or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _elapsed_ms(start, end):
    start_time, end_time = _parse_time(start), _parse_time(end)
    if start_time is None or end_time is None or end_time < start_time:
        return None
    return round((end_time - start_time).total_seconds() * 1000)


def _percentile(values, percentile):
    ordered = sorted(value for value in values if type(value) is int and value >= 0)
    if not ordered:
        return None
    return ordered[max(0, ceil(len(ordered) * percentile) - 1)]


def _batch_metadata(runtime, role_context, start_at, end_at):
    scope = role_context.scope
    role = role_context.role_binding.role
    context_json = "CASE WHEN json_valid(b.context_json) THEN b.context_json ELSE '{}' END"
    start_dt, end_dt = _parse_time(start_at), _parse_time(end_at)
    if start_dt is not None and end_dt is not None:
        bound_predicate = "AND b.created_at >= ? AND b.created_at <= ?"
        bound_values = ((start_dt - timedelta(days=1)).isoformat(), (end_dt + timedelta(days=1)).isoformat())
    else:
        bound_predicate = "AND julianday(b.created_at) >= julianday(?) AND julianday(b.created_at) <= julianday(?)"
        bound_values = (start_at, end_at)
    rows = runtime.read_all(f"""SELECT b.batch_id, b.created_at, b.generation_task_id, b.state AS batch_state,
            b.part_count, b.contributions_json, f.state AS flow_state, f.reason AS flow_reason,
            f.due_at AS flow_due_at, r.plan_json
        FROM conversation_batches b
        LEFT JOIN conversation_flows f ON f.batch_id = b.batch_id
        LEFT JOIN model_requests r ON r.task_id = b.generation_task_id
        WHERE json_extract({context_json}, '$.role_binding.scope.kind') = 'private'
            AND json_extract({context_json}, '$.role_binding.scope.installation_id') = ?
            AND json_extract({context_json}, '$.role_binding.scope.platform') = ?
            AND json_extract({context_json}, '$.role_binding.scope.bot_id') = ?
            AND json_extract({context_json}, '$.role_binding.scope.account_id') = ?
            AND json_extract({context_json}, '$.role_binding.role.installation_id') = ?
            AND json_extract({context_json}, '$.role_binding.role.kind') = ?
            AND json_extract({context_json}, '$.role_binding.role.subject_id') = ?
            {bound_predicate}
        ORDER BY b.created_at DESC, b.rowid DESC""",
        (scope.installation_id, scope.platform, scope.bot_id, scope.account_id,
         role.installation_id, role.kind.value, role.subject_id, *bound_values))
    if start_dt is None or end_dt is None:
        return rows
    return tuple(row for row in rows if (created_dt := _parse_time(row["created_at"])) is not None
                 and start_dt <= created_dt <= end_dt)


def _grouped_for_batches(runtime, table, identifiers):
    result = {}
    for offset in range(0, len(identifiers), 400):
        group = identifiers[offset:offset + 400]
        if table in ("calls", "task_starts", "model_details"):
            group = tuple(dict.fromkeys(value for value in group if value))
            if not group:
                continue
            placeholders = ",".join("?" for _ in group)
            if table == "calls":
                query = f"""SELECT task_id, COUNT(*) AS model_calls,
                        SUM(CASE WHEN state IN ('reserved', 'dispatched') THEN 1 ELSE 0 END) AS active_model_calls,
                        MIN(CASE WHEN started_at IS NOT NULL THEN started_at END) AS first_model_started_at,
                        CASE WHEN SUM(CASE WHEN state IN ('reserved', 'dispatched') THEN 1 ELSE 0 END) = 0
                            THEN MAX(CASE WHEN finished_at IS NOT NULL THEN finished_at END)
                            ELSE NULL END AS last_model_finished_at
                    FROM model_calls WHERE task_id IN ({placeholders}) GROUP BY task_id"""
            elif table == "task_starts":
                query = f"""SELECT task_id, MIN(observed_at) AS started_at FROM execution_events
                    WHERE task_id IN ({placeholders}) AND state = 'running' GROUP BY task_id"""
            else:
                query = f"""SELECT c.task_id, c.position, c.state, c.started_at, c.finished_at, c.latency_ms,
                        c.provider_json, c.actual_tokens, c.input_tokens, c.output_tokens,
                        f.code AS failure_code, d.summary_json AS response_summary_json
                    FROM model_calls c
                    LEFT JOIN model_call_failures f ON f.call_id = c.call_id
                    LEFT JOIN model_call_diagnostics d ON d.call_id = c.call_id
                    WHERE c.task_id IN ({placeholders}) ORDER BY c.task_id, c.position"""
            for row in runtime.read_all(query, group):
                if table == "model_details":
                    result.setdefault(row["task_id"], []).append(dict(row))
                else:
                    result[row["task_id"]] = dict(row)
            continue
        placeholders = ",".join("?" for _ in group)
        if table == "inputs":
            query = f"""SELECT batch_id, COUNT(*) AS input_count,
                    MIN(CASE WHEN json_valid(event_json) THEN json_extract(event_json, '$.received_at') END)
                        AS first_received_at,
                    MAX(CASE WHEN json_valid(event_json) THEN json_extract(event_json, '$.received_at') END)
                        AS last_received_at
                FROM conversation_inputs WHERE batch_id IN ({placeholders}) GROUP BY batch_id"""
        elif table == "parts":
            query = f"""SELECT batch_id, COUNT(*) AS stored_parts,
                    SUM(CASE WHEN state = 'delivered' THEN 1 ELSE 0 END) AS delivered_parts,
                    SUM(CASE WHEN state = 'failed' THEN 1 ELSE 0 END) AS failed_parts,
                    SUM(CASE WHEN state = 'unknown' THEN 1 ELSE 0 END) AS unknown_parts,
                    SUM(CASE WHEN state IN ('pending', 'dispatched', 'accepted') THEN 1 ELSE 0 END) AS pending_parts,
                    MIN(CASE WHEN dispatched_at IS NOT NULL THEN dispatched_at END) AS first_dispatched_at,
                    MIN(CASE WHEN state = 'delivered' AND delivered_at IS NOT NULL THEN delivered_at END) AS first_delivered_at,
                    MAX(CASE WHEN state = 'delivered' AND delivered_at IS NOT NULL THEN delivered_at END) AS last_delivered_at
                FROM conversation_parts WHERE batch_id IN ({placeholders}) GROUP BY batch_id"""
        elif table == "tools":
                query = f"""SELECT c.batch_id, c.sequence, c.action, c.state, c.created_at, c.finished_at,
                    i.invocation_id, i.state AS extension_state,
                    i.created_at AS extension_created_at, i.finished_at AS extension_finished_at
                FROM conversation_tool_calls c
                LEFT JOIN extension_tool_invocations i
                    ON i.parent_task_id = c.task_id AND i.tool_call_id = c.tool_call_id
                WHERE c.batch_id IN ({placeholders}) ORDER BY c.batch_id, c.sequence"""
        else:
            raise ValueError("unsupported reply-efficiency projection")
        for row in runtime.read_all(query, group):
            if table == "tools":
                result.setdefault(row["batch_id"], []).append(dict(row))
            else:
                result[row["batch_id"]] = dict(row)
    return result


def _laya_for_batches(runtime, identifiers):
    result = {}
    for offset in range(0, len(identifiers), 400):
        group = tuple(dict.fromkeys(value for value in identifiers[offset:offset + 400] if value))
        if not group:
            continue
        placeholders = ",".join("?" for _ in group)
        rows = runtime.read_all(f"""SELECT batch_id, source, status, mode, reason_code, elapsed_ms,
                decision_json, observed_at FROM laya_decision_events
            WHERE source = 'conversation' AND batch_id IN ({placeholders})
            ORDER BY observed_at""", group)
        for row in rows:
            event = dict(row)
            try:
                event["decision"] = json.loads(event.pop("decision_json"))
            except (TypeError, ValueError):
                event["decision"] = None
            result.setdefault(event["batch_id"], []).append(event)
    return result


def _laya_projection(events):
    elapsed = [item["elapsed_ms"] for item in events
               if item["status"] in ("ok", "failed") and type(item.get("elapsed_ms")) is int]
    return {
        "calls": sum(item["status"] in ("ok", "failed") for item in events),
        "succeeded": sum(item["status"] == "ok" for item in events),
        "skipped": sum(item["status"] == "skipped" for item in events),
        "failed": sum(item["status"] == "failed" for item in events),
        "elapsed_ms": sum(elapsed) if elapsed else None,
        "events": list(events), "availability": "observed" if events else "unavailable",
    }


def _outcome(batch, parts):
    delivered = parts.get("delivered_parts", 0) or 0
    failed = parts.get("failed_parts", 0) or 0
    unknown = parts.get("unknown_parts", 0) or 0
    pending = parts.get("pending_parts", 0) or 0
    flow_state = batch.get("flow_state") or "not_started"
    part_count = batch.get("part_count") or 0
    if delivered and (failed or unknown or delivered < part_count):
        return "partial"
    if part_count and delivered >= part_count:
        return "completed"
    if unknown:
        return "unknown"
    if pending or flow_state in ("collecting", "generating", "delivering"):
        return "pending"
    if failed or flow_state in ("blocked", "closed") or (flow_state == "complete" and part_count == 0):
        return "no_reply"
    return "incomplete"


def _project(batch, inputs, parts, calls, task_start, model_details, tool_details, laya_events=()):
    received_at = inputs.get("first_received_at") or batch["created_at"]
    last_input_received_at = inputs.get("last_received_at") or received_at
    first_model_started_at = calls.get("first_model_started_at")
    last_model_finished_at = calls.get("last_model_finished_at")
    first_dispatched_at = parts.get("first_dispatched_at")
    first_delivered_at = parts.get("first_delivered_at")
    last_delivered_at = parts.get("last_delivered_at")
    part_count = batch["part_count"] or 0
    delivered_parts = parts.get("delivered_parts", 0) or 0
    outcome = _outcome(batch, parts)
    completed = outcome == "completed"
    try:
        contribution_snapshot = json.loads(batch.get("contributions_json") or "{}")
    except (TypeError, ValueError):
        contribution_snapshot = {}
    raw_materials = contribution_snapshot.get("materials", []) if isinstance(contribution_snapshot, dict) else []
    materials = [{
        "owner": item.get("owner", "")[:80] if type(item.get("owner")) is str else "",
        "purpose": item.get("purpose", "")[:100] if type(item.get("purpose")) is str else "",
        "state": item.get("state") if item.get("state") in ("ready", "deferred", "unavailable") else "unknown",
    } for item in raw_materials[:32] if isinstance(item, dict)] if isinstance(raw_materials, list) else []
    try:
        request_plan = json.loads(batch.get("plan_json") or "{}")
    except (TypeError, ValueError):
        request_plan = {}
    offered_actions = request_plan.get("tool_actions") if isinstance(request_plan, dict) else None
    offered_tool_count = len(offered_actions) if isinstance(offered_actions, list) else None
    offered_tools = list(dict.fromkeys(
        action[:120] for action in offered_actions if type(action) is str and action
    ))[:32] if isinstance(offered_actions, list) else []
    model_steps = []
    for item in model_details:
        latency = item.get("latency_ms")
        elapsed = (round(latency) if type(latency) in (int, float) and isfinite(latency) and latency >= 0
                   else _elapsed_ms(item.get("started_at"), item.get("finished_at")))
        try:
            provider_record = json.loads(item.get("provider_json") or "{}")
        except (TypeError, ValueError):
            provider_record = {}
        model_steps.append({
            "position": item["position"], "state": item["state"],
            "started_at": item.get("started_at"), "finished_at": item.get("finished_at"),
            "elapsed_ms": elapsed,
            "provider": provider_record.get("provider", "") if isinstance(provider_record, dict) else "",
            "tokens": item.get("actual_tokens"), "input_tokens": item.get("input_tokens"),
            "output_tokens": item.get("output_tokens"), "failure_code": item.get("failure_code"),
            "response_diagnostics": checked_response_diagnostics(item.get("response_summary_json")),
        })
    tool_steps = [{
        "sequence": item["sequence"], "action": item["action"],
        "kind": "extension" if item.get("invocation_id") else "builtin",
        "state": item.get("extension_state") or item["state"],
        "started_at": item.get("extension_created_at") or item.get("created_at"),
        "finished_at": item.get("extension_finished_at") or item.get("finished_at"),
        "elapsed_ms": _elapsed_ms(item.get("created_at"), item.get("finished_at")),
        "extension_started_at": item.get("extension_created_at"),
        "extension_finished_at": item.get("extension_finished_at"),
        "extension_elapsed_ms": _elapsed_ms(item.get("extension_created_at"), item.get("extension_finished_at"))
            if item.get("invocation_id") else None,
    } for item in tool_details]
    laya = _laya_projection(laya_events)
    flow_due_at = batch.get("flow_due_at")
    generation_started_at = task_start.get("started_at")
    return {
        "batch_id": batch["batch_id"],
        "execution_path": "legacy_v2_conversation",
        "measurement_basis": {"delivery": "v2_platform_receipt", "usage": "v2_model_ledger"},
        "batch_state": batch["batch_state"],
        "received_at": received_at,
        "received_time_source": "input_event" if inputs.get("first_received_at") else "batch_created",
        "flow_state": batch.get("flow_state") or "not_started",
        "flow_reason": batch.get("flow_reason") or "",
        "outcome": outcome,
        "input_count": inputs.get("input_count", 0),
        "part_count": part_count,
        "delivered_parts": delivered_parts,
        "failed_parts": parts.get("failed_parts", 0) or 0,
        "unknown_parts": parts.get("unknown_parts", 0) or 0,
        "pending_parts": parts.get("pending_parts", 0) or 0,
        "model_calls": calls.get("model_calls", 0),
        "active_model_calls": calls.get("active_model_calls", 0) or 0,
        "timestamps": {
            "received_at": received_at,
            "last_input_received_at": last_input_received_at,
            "flow_due_at": flow_due_at,
            "generation_started_at": generation_started_at,
            "first_model_started_at": first_model_started_at,
            "last_model_finished_at": last_model_finished_at,
            "first_dispatched_at": first_dispatched_at,
            "first_delivered_at": first_delivered_at,
            "last_delivered_at": last_delivered_at,
        },
        "stages": {
            "pre_model_ms": _elapsed_ms(received_at, first_model_started_at),
            "last_input_to_model_ms": _elapsed_ms(last_input_received_at, first_model_started_at),
            "model_ms": _elapsed_ms(first_model_started_at, last_model_finished_at),
            "post_model_ms": _elapsed_ms(last_model_finished_at, first_dispatched_at),
            "first_reply_ms": _elapsed_ms(received_at, first_delivered_at),
            "delivery_ms": _elapsed_ms(first_dispatched_at, last_delivered_at),
            "end_to_end_ms": _elapsed_ms(received_at, last_delivered_at) if completed else None,
        },
        "trace": {
            "input_wait_ms": _elapsed_ms(last_input_received_at, flow_due_at),
            "queue_wait_ms": _elapsed_ms(flow_due_at, generation_started_at),
            "preparation_ms": _elapsed_ms(generation_started_at, first_model_started_at),
            "materials": materials,
            "offered_tool_count": offered_tool_count,
            "offered_tools": offered_tools,
            "model_calls": model_steps,
            "tool_calls": tool_steps,
            "laya": laya,
        },
        "observed": {
            "received": bool(inputs.get("first_received_at")),
            "model_started": bool(first_model_started_at),
            "model_finished": bool(last_model_finished_at),
            "first_dispatched": bool(first_dispatched_at),
            "first_delivered": bool(first_delivered_at),
            "final_delivered": bool(part_count and delivered_parts >= part_count and last_delivered_at),
        },
    }


def _core_sample(record, laya_events=()):
    requests = record.get("requests", [])
    real_requests = [item for item in requests if item.get("measurement_basis", "provider_response") == "provider_response"]
    delivery = record.get("delivery", {})
    received = record["received_at"]
    first_started = real_requests[0].get("started_at") if real_requests else None
    finished = real_requests[-1].get("finished_at") if real_requests else None
    agent_finished = record.get("generation_finished_at")
    preparation_steps = record.get("preparation_steps", [])

    def measured_total(items, field):
        values = [item.get(field) for item in items]
        return sum(values) if values and all(type(value) in (int, float) and isfinite(value) and value >= 0
                                            for value in values) else None

    preparation_ms = measured_total(preparation_steps, "elapsed_ms")
    first_dispatched, first_returned = delivery.get("first_started_at"), delivery.get("first_returned_at")
    last_returned = delivery.get("last_returned_at")
    state = delivery.get("state", "not_started")
    generation = record.get("generation_state", "prepared")
    failed_sends = delivery.get("failed", 0)
    outcome = ("unknown" if state == "unknown" else "failed" if state == "failed"
               else "partial" if state == "returned" and (generation == "failed" or failed_sends)
               else "completed" if state == "returned" and generation == "completed" else "pending" if generation in ("prepared", "running")
               else "no_reply" if generation in ("failed", "suppressed") or state == "denied" else "incomplete")
    tools = [{**item, "kind": "astrbot_core",
              "elapsed_ms": _elapsed_ms(item.get("started_at"), item.get("finished_at"))}
             for item in record.get("tools", [])]
    model_steps = [{"position": item["position"], "state": item["state"],
        "started_at": item.get("started_at"), "finished_at": item.get("finished_at"),
        "elapsed_ms": item.get("latency_ms"), "ttft_ms": item.get("ttft_ms"), "provider": item.get("provider", ""),
        "tokens": (item.get("usage") or {}).get("tokens"),
        "input_tokens": (item.get("usage") or {}).get("input_tokens"),
        "output_tokens": (item.get("usage") or {}).get("output_tokens"),
        "cached_input_tokens": (item.get("usage") or {}).get("cached_input_tokens"),
        "failure_code": item.get("failure_code"), "response_diagnostics": None,
        "measurement_basis": item.get("measurement_basis", "provider_response"),
        "usage_availability": (item.get("usage") or {}).get("availability", "unavailable"),
        "cache_availability": (item.get("usage") or {}).get("cache_availability", "unavailable")}
        for item in requests]
    return {"batch_id": record["turn_id"], "turn_id": record["turn_id"], "batch_state": generation,
        "execution_path": "astrbot_core", "measurement_basis": {
            "usage": record.get("usage_scope", "unavailable"), "delivery": "native_send_return",
            "first_reply": "unavailable_without_platform_receipt", "request_signature": record.get("signature_scope", "unavailable")},
        "delivery": delivery, "received_at": received, "received_time_source": "native_input",
        "flow_state": generation, "flow_reason": "astrbot_core", "outcome": outcome,
        "input_count": 1, "part_count": delivery.get("attempts", 0), "delivered_parts": 0,
        "send_returned_parts": delivery.get("returned", 0), "failed_parts": failed_sends,
        "unknown_parts": delivery.get("unknown", 0),
        "pending_parts": max(0, delivery.get("attempts", 0) - delivery.get("returned", 0) - delivery.get("unknown", 0) - failed_sends),
        "model_calls": sum(item.get("measurement_basis", "provider_response") == "provider_response" for item in requests),
        "usage_observation_count": len(requests),
        "model_call_count_available": record.get("request_observation_available", False),
        "model_call_count_complete": record.get("request_observation_complete", False),
        "active_model_calls": sum(item["state"] == "started" for item in requests),
        "timestamps": {"received_at": received, "last_input_received_at": received,
            "flow_due_at": None, "waiting_llm_at": record.get("waiting_llm_at"),
            "generation_started_at": record.get("request_hook_at"),
            "first_model_started_at": first_started, "last_model_finished_at": finished,
            "agent_started_at": record.get("agent_started_at"), "agent_finished_at": agent_finished,
            "first_dispatched_at": first_dispatched, "first_delivered_at": None, "last_delivered_at": None,
            "first_send_returned_at": first_returned, "last_send_returned_at": last_returned},
        "stages": {"pre_model_ms": _elapsed_ms(received, first_started), "last_input_to_model_ms": _elapsed_ms(received, first_started),
            "waiting_llm_to_request_hook_ms": _elapsed_ms(record.get("waiting_llm_at"), record.get("request_hook_at")),
            "request_hook_to_agent_ms": _elapsed_ms(record.get("request_hook_at"), record.get("agent_started_at")),
            "model_ms": _elapsed_ms(first_started, finished),
            "model_request_ms": measured_total(real_requests, "latency_ms"),
            "agent_ms": record.get("runner_duration_ms") if record.get("runner_duration_ms") is not None
                else _elapsed_ms(record.get("agent_started_at"), agent_finished),
            "tool_ms": measured_total(tools, "elapsed_ms"), "plugin_preparation_ms": preparation_ms,
            "send_ms": _elapsed_ms(first_dispatched, last_returned),
            "post_model_ms": _elapsed_ms(agent_finished, first_dispatched), "first_reply_ms": None,
            "delivery_ms": None, "end_to_end_ms": None,
            "first_send_returned_ms": _elapsed_ms(received, first_returned),
            "end_to_end_send_returned_ms": _elapsed_ms(received, last_returned) if generation in ("completed", "failed") else None,
            "ttft_ms": record.get("ttft_ms")},
        "trace": {"input_wait_ms": None, "queue_wait_ms": None, "preparation_ms": preparation_ms,
            "preparation_steps": preparation_steps, "materials": record.get("materials", []),
            "offered_tool_count": (record.get("request_hook_signature") or {}).get("tool_count"),
            "offered_tools": record.get("offered_tools", []), "model_calls": model_steps, "tool_calls": tools,
            "laya": _laya_projection(laya_events)},
        "observed": {"received": True, "model_started": bool(first_started), "model_finished": bool(finished),
            "first_dispatched": bool(first_dispatched), "first_delivered": False, "final_delivered": False,
            "send_returned": bool(delivery.get("returned")), "platform_confirmed": False}}


def reply_efficiency_report(runtime, role_context, now, *, core_observations=None):
    """Return a seven-calendar-day, body-free report for one bot and role's private sessions."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    window_start = (now - timedelta(days=6)).replace(hour=0, minute=0, second=0, microsecond=0)
    batches = _batch_metadata(runtime, role_context, window_start.isoformat(), now.isoformat())
    batch_ids = [row["batch_id"] for row in batches]
    task_ids = [row["generation_task_id"] for row in batches]
    inputs_by_batch = _grouped_for_batches(runtime, "inputs", batch_ids)
    parts_by_batch = _grouped_for_batches(runtime, "parts", batch_ids)
    calls_by_task = _grouped_for_batches(runtime, "calls", task_ids)
    task_starts = _grouped_for_batches(runtime, "task_starts", task_ids)
    model_details = _grouped_for_batches(runtime, "model_details", task_ids)
    tools_by_batch = _grouped_for_batches(runtime, "tools", batch_ids)
    core_records = (observations_for_role(runtime, role_context,
        start=window_start.isoformat(), end=now.isoformat(), private_only=True)
        if core_observations is None else core_observations)
    laya_by_batch = _laya_for_batches(runtime, batch_ids + [record["turn_id"] for record in core_records])

    samples = [_project(dict(batch), inputs_by_batch.get(batch["batch_id"], {}),
                        parts_by_batch.get(batch["batch_id"], {}),
                        calls_by_task.get(batch["generation_task_id"], {}),
                        task_starts.get(batch["generation_task_id"], {}),
                        model_details.get(batch["generation_task_id"], ()),
                        tools_by_batch.get(batch["batch_id"], ()),
                        laya_by_batch.get(batch["batch_id"], ())) for batch in batches]
    core_samples = [_core_sample(record, laya_by_batch.get(record["turn_id"], ())) for record in core_records]
    samples.extend(core_samples)
    samples.sort(key=lambda sample: (sample["received_at"], sample["batch_id"]), reverse=True)
    stage_keys = ("end_to_end_ms", "first_reply_ms", "pre_model_ms", "last_input_to_model_ms", "model_ms",
                  "post_model_ms", "delivery_ms")
    stage_values = {key: [sample["stages"][key] for sample in samples] for key in stage_keys}
    summary = {
        "samples": len(samples),
        "completed": sum(sample["outcome"] == "completed" for sample in samples),
        "partial": sum(sample["outcome"] == "partial" for sample in samples),
        "failed": sum(sample["outcome"] == "failed" for sample in samples),
        "unknown": sum(sample["outcome"] == "unknown" for sample in samples),
        "pending": sum(sample["outcome"] == "pending" for sample in samples),
        "no_reply": sum(sample["outcome"] == "no_reply" for sample in samples),
        "incomplete": sum(sample["outcome"] == "incomplete" for sample in samples),
        "median_end_to_end_ms": _percentile(stage_values["end_to_end_ms"], 0.5),
        "p95_end_to_end_ms": _percentile(stage_values["end_to_end_ms"], 0.95),
        "median_first_reply_ms": _percentile(stage_values["first_reply_ms"], 0.5),
        "p95_first_reply_ms": _percentile(stage_values["first_reply_ms"], 0.95),
        "median_pre_model_ms": _percentile(stage_values["pre_model_ms"], 0.5),
        "median_last_input_to_model_ms": _percentile(stage_values["last_input_to_model_ms"], 0.5),
        "median_model_ms": _percentile(stage_values["model_ms"], 0.5),
        "median_post_model_ms": _percentile(stage_values["post_model_ms"], 0.5),
        "median_delivery_ms": _percentile(stage_values["delivery_ms"], 0.5),
        "send_returned_samples": sum(bool(sample.get("send_returned_parts")) for sample in core_samples),
        "median_first_send_returned_ms": _percentile([sample["stages"]["first_send_returned_ms"] for sample in core_samples], 0.5),
        "p95_first_send_returned_ms": _percentile([sample["stages"]["first_send_returned_ms"] for sample in core_samples], 0.95),
        "median_end_to_end_send_returned_ms": _percentile([sample["stages"]["end_to_end_send_returned_ms"] for sample in core_samples], 0.5),
        "p95_end_to_end_send_returned_ms": _percentile([sample["stages"]["end_to_end_send_returned_ms"] for sample in core_samples], 0.95),
        "median_ttft_ms": _percentile([sample["stages"]["ttft_ms"] for sample in core_samples], 0.5),
        "laya_calls": sum(sample["trace"]["laya"]["calls"] for sample in samples),
        "laya_succeeded": sum(sample["trace"]["laya"]["succeeded"] for sample in samples),
        "laya_skipped": sum(sample["trace"]["laya"]["skipped"] for sample in samples),
        "laya_failed": sum(sample["trace"]["laya"]["failed"] for sample in samples),
        "median_laya_ms": _percentile([
            event["elapsed_ms"] for sample in samples for event in sample["trace"]["laya"]["events"]
            if event["status"] in ("ok", "failed") and type(event.get("elapsed_ms")) is int], 0.5),
        "p95_laya_ms": _percentile([
            event["elapsed_ms"] for sample in samples for event in sample["trace"]["laya"]["events"]
            if event["status"] in ("ok", "failed") and type(event.get("elapsed_ms")) is int], 0.95),
    }
    for key in ("model_request_ms", "agent_ms", "tool_ms", "plugin_preparation_ms", "send_ms"):
        summary["median_" + key] = _percentile([sample["stages"][key] for sample in core_samples], 0.5)
    for key in ("waiting_llm_to_request_hook_ms", "request_hook_to_agent_ms"):
        summary["median_" + key] = _percentile([sample["stages"][key] for sample in core_samples], 0.5)
    coverage_keys = ("received", "model_started", "model_finished", "first_dispatched", "first_delivered", "final_delivered")
    coverage = {key: sum(sample["observed"][key] for sample in samples) for key in coverage_keys}
    coverage.update(core_samples=len(core_samples), core_send_returned=sum(sample["observed"]["send_returned"] for sample in core_samples),
                    core_request_observation=sum(sample["model_call_count_available"] for sample in core_samples),
                    core_preparation=sum(bool(sample["trace"]["preparation_steps"]) for sample in core_samples))
    by_day = {}
    cursor = window_start.date()
    while cursor <= now.date():
        by_day[cursor.isoformat()] = []
        cursor += timedelta(days=1)
    for sample in samples:
        by_day.setdefault(sample["received_at"][:10], []).append(sample)
    daily = []
    for day, entries in sorted(by_day.items()):
        values = [entry["stages"]["end_to_end_ms"] for entry in entries]
        day_core = [entry for entry in entries if entry["execution_path"] == "astrbot_core"]
        send_values = [entry["stages"]["end_to_end_send_returned_ms"] for entry in day_core]
        daily.append({"day": day, "samples": len(entries),
                      "completed": sum(entry["outcome"] == "completed" for entry in entries),
                      "failed": sum(entry["outcome"] == "failed" for entry in entries),
                      "median_end_to_end_ms": _percentile(values, 0.5),
                      "p95_end_to_end_ms": _percentile(values, 0.95),
                      "core_samples": len(day_core),
                      "send_returned_samples": sum(bool(entry["send_returned_parts"]) for entry in day_core),
                      "median_end_to_end_send_returned_ms": _percentile(send_values, 0.5),
                      "p95_end_to_end_send_returned_ms": _percentile(send_values, 0.95)})
    return {
        "generated_at": now.isoformat(),
        "window_days": 7,
        "window_start": window_start.isoformat(),
        "sample_limit": SAMPLE_LIMIT,
        "summary": summary,
        "coverage": coverage,
        "recent": samples[:SAMPLE_LIMIT],
        "daily": daily,
        "measurement_basis": {"legacy_v2_conversation": "v2_platform_receipt", "astrbot_core": "native_send_return",
                              "platform_latency_includes_core": False},
        "by_source": {key: {"samples": sum(sample["execution_path"] == key for sample in samples),
            "completed": sum(sample["execution_path"] == key and sample["outcome"] == "completed" for sample in samples)}
            for key in ("astrbot_core", "legacy_v2_conversation")},
    }
