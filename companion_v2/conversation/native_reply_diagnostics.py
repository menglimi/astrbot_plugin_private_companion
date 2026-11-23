"""Window-scoped reply diagnostics from observed AstrBot requests and sends."""

import json

from companion_v2.execution.models import digest
from companion_v2.model_gateway.core_observations import available


_IN_CHUNK_SIZE = 400


def recent(runtime, scope, role, *, limit):
    if limit <= 0 or not available(runtime):
        return []
    from companion_v2.storage.native_dedup import matching_context_hashes

    rk = digest([scope.installation_id, scope.platform, scope.bot_id, scope.account_id, role])
    scope_kind = scope.kind.value
    scope_params = (scope.native_scope_id, scope.session_id, scope.conversation_id)
    # Two-phase covering-index lookup split into pure indexed queries (avoiding
    # `OR json_extract(...)` which prevents SQLite from using either index and
    # avoiding reading multi-hundred-KB `record_json` payloads for non-top-K rows).
    with runtime.read_snapshot():
        hashes = matching_context_hashes(
            runtime,
            """json_extract(context_json, '$.role_binding.scope.native_scope_id') = ?
               AND json_extract(context_json, '$.role_binding.scope.session_id') = ?
               AND json_extract(context_json, '$.role_binding.scope.conversation_id') = ?""",
            scope_params,
        )
        candidates: dict[str, str] = {}
        if hashes:
            for start in range(0, len(hashes), _IN_CHUNK_SIZE):
                chunk = hashes[start : start + _IN_CHUNK_SIZE]
                marks = ",".join("?" for _ in chunk)
                for row in runtime.read_all(
                    f"""SELECT turn_id, received_at FROM astrbot_core_reply_observations
                        WHERE role_key = ? AND scope_kind = ? AND context_hash IN ({marks})
                        ORDER BY received_at DESC, turn_id DESC LIMIT ?""",
                    (rk, scope_kind, *chunk, limit),
                ):
                    candidates[row["turn_id"]] = row["received_at"]
        for row in runtime.read_all(
            """SELECT turn_id, received_at FROM astrbot_core_reply_observations
               WHERE role_key = ? AND scope_kind = ?
                 AND json_extract(context_json, '$.role_binding.scope.native_scope_id') = ?
                 AND json_extract(context_json, '$.role_binding.scope.session_id') = ?
                 AND json_extract(context_json, '$.role_binding.scope.conversation_id') = ?
               ORDER BY received_at DESC, turn_id DESC LIMIT ?""",
            (rk, scope_kind, *scope_params, limit),
        ):
            candidates[row["turn_id"]] = row["received_at"]
        if not candidates:
            return []
        ordered_ids = [
            turn_id
            for turn_id, _ in sorted(
                candidates.items(), key=lambda item: (item[1], item[0]), reverse=True
            )[:limit]
        ]
        by_id: dict[str, str] = {}
        for start in range(0, len(ordered_ids), _IN_CHUNK_SIZE):
            chunk = ordered_ids[start : start + _IN_CHUNK_SIZE]
            marks = ",".join("?" for _ in chunk)
            for row in runtime.read_all(
                f"SELECT turn_id, record_json FROM astrbot_core_reply_observations WHERE turn_id IN ({marks})",
                tuple(chunk),
            ):
                by_id[row["turn_id"]] = row["record_json"]
    result = []
    for turn_id in ordered_ids:
        raw_record = by_id.get(turn_id)
        if raw_record is None:
            continue
        record = json.loads(raw_record)
        delivery = record.get("delivery", {})
        returned = delivery.get("returned", 0)
        state = record.get("generation_state", "awaiting_request")
        requests = record.get("requests", [])
        result.append({"batch_id": record["turn_id"], "created_at": record["received_at"],
            "execution_path": "astrbot_core", "state": "complete" if state == "completed" and returned else
                "closed" if state == "suppressed" else "generating" if state == "running" else state,
            "reason": state, "turn_type": "astrbot.core.reply", "current_inputs": 1, "parts": [],
            "calls": [{"position": item["position"], "state": item["state"],
                "actual_tokens": (item.get("usage") or {}).get("tokens"), "latency_ms": item.get("latency_ms"),
                "provider": item.get("provider"),
                "failure_code": item.get("failure_code"), "response_diagnostics": None} for item in requests],
            "response_type": "native_reply" if returned else "no_reply",
            "injected_materials": record.get("materials", []), "group_history_items": None,
            "offered_tools": record.get("offered_tools", []), "filtered_tools": [],
            "rounds": [{"round_no": index, "state": item["state"], "tool_actions": []}
                       for index, item in enumerate(requests)],
            "tool_calls": record.get("tools", []), "send_returns": returned,
            "delivery_state": delivery.get("state", "not_started"), "platform_confirmed": False})
    return result
