"""Project real AstrBot observations without reserving V2 model calls or budget."""

from datetime import datetime, timedelta, timezone
import json
import re

from companion_v2.execution.models import digest, encode
from companion_v2.conversation.linked_ownership import link_for_scope
from companion_v2.storage.core_reply_telemetry_schema import STATEMENTS, TABLE
from .usage import CORE_CHAIN_KIND, USAGE_FIELDS


def role_key(context):
    scope = context.scope
    return digest([scope.installation_id, scope.platform, scope.bot_id, scope.account_id,
                   context.role_binding.role])


def available(runtime):
    return runtime.read_one("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (TABLE,)) is not None


def _fingerprints_only(value):
    """The telemetry owner accepts structural metadata, never arbitrary JSON."""
    if value is None:
        return
    from .cache_diagnostics import checked_signature
    signature = checked_signature(encode(value))
    if signature is None or set(value) - set(signature.to_record()):
        raise ValueError("core request signature must contain hashes and counts only")


def _validate_record(record):
    if type(record) is not dict or set(record) - {
        "version", "revision", "turn_id", "event_id", "execution_path", "received_at", "waiting_llm_at", "request_hook_at", "observed_at",
        "generation_state", "generation_finished_at", "request_observation_available", "request_observation_complete",
        "tool_requery_observed", "requests", "tools", "ttft_ms", "runner_duration_ms", "usage_scope", "delivery",
        "request_hook_signature", "signature_scope", "agent_started_at", "preparation_steps", "materials", "offered_tools"}:
        raise ValueError("core telemetry records contain only declared metadata")
    for step in record.get("preparation_steps", []):
        if (type(step) is not dict or set(step) - {"name", "state", "started_at", "finished_at", "elapsed_ms"}
                or step.get("name") not in {"context_build", "request_authorization", "prompt_injection", "tool_injection", "send_tool_injection", "perception_injection", "telemetry_capture"}
                or step.get("state") not in {"returned", "failed"}
                or step.get("elapsed_ms") is not None and (type(step["elapsed_ms"]) is not int or step["elapsed_ms"] < 0)):
            raise ValueError("core preparation contains only declared stages and timings")
    for material in record.get("materials", []):
        if (type(material) is not dict or set(material) - {"owner", "purpose", "state", "elapsed_ms", "chars"}
                or material.get("state") not in {"ready", "empty", "unavailable", "not_injected"}
                or any(type(material.get(key)) is not str or re.fullmatch(r"[a-z][a-z0-9_.:-]{0,127}", material[key]) is None
                       for key in ("owner", "purpose"))
                or any(material.get(key) is not None and (type(material[key]) is not int or material[key] < 0)
                       for key in ("elapsed_ms", "chars"))):
            raise ValueError("core materials contain only source names, counts and timings")
    if (type(record.get("offered_tools", [])) is not list
            or len(record.get("offered_tools", [])) > 256
            or any(type(name) is not str or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.:-]{0,127}", name) is None
                   for name in record.get("offered_tools", []))):
        raise ValueError("core offered tools contain function names only")
    _fingerprints_only(record.get("request_hook_signature"))
    request_fields = {"position", "state", "started_at", "finished_at", "latency_ms", "ttft_ms", "provider", "usage",
                      "signature", "signature_scope", "measurement_basis", "failure_code", "media"}
    usage_fields = {"input_tokens", "output_tokens", "tokens", "cached_input_tokens", "cache_creation_tokens",
                    "availability", "cache_availability", "cost_availability"}
    for request in record.get("requests", []):
        if type(request) is not dict or set(request) - request_fields or set(request.get("usage") or {}) - usage_fields:
            raise ValueError("core request metadata must not include prompt or response bodies")
        _fingerprints_only(request.get("signature"))
        media = request.get("media")
        if media is not None:
            flags = {"voice_prompt_present", "reaction_enabled", "reaction_prompt_present", "presentation_present"}
            if (type(media) is not dict or set(media) != flags | {"voice_mode"}
                    or media["voice_mode"] not in {"disabled", "always", "request", "adaptive"}
                    or any(type(media[key]) is not bool for key in flags)):
                raise ValueError("core media diagnostics contain mode and boolean presence flags only")
    for tool in record.get("tools", []):
        if type(tool) is not dict or set(tool) - {"sequence", "action", "state", "started_at", "finished_at"}:
            raise ValueError("core tool timing must not include arguments or output")


def record_observation(runtime, context, record):
    """Revision fences let asynchronous snapshots finish in any order."""
    _validate_record(record)
    with runtime.transaction() as connection:
        for statement in STATEMENTS:
            connection.execute(statement)
        connection.execute("""INSERT INTO astrbot_core_reply_observations
            (turn_id, revision, role_key, context_key, scope_kind, context_json, received_at, observed_at, record_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(turn_id) DO UPDATE SET revision = excluded.revision,
                observed_at = excluded.observed_at, record_json = excluded.record_json
            WHERE excluded.revision > astrbot_core_reply_observations.revision""",
            (record["turn_id"], record["revision"], role_key(context), context.key, context.scope.kind.value,
             encode(context), record["received_at"], record["observed_at"], encode(record)))


def _parse_utc_timestamp(value):
    if type(value) is not str or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _query_observations(runtime, context, *, start=None, end=None, private_only=False):
    if not available(runtime):
        return []
    link = link_for_scope(runtime, context.scope)
    keys = tuple(dict.fromkeys(digest([scope.installation_id, scope.platform, scope.bot_id, scope.account_id,
                                     context.role_binding.role])
                               for scope in (link.scopes if link else (context.scope,))))
    where, values = ["role_key IN (" + ",".join("?" for _ in keys) + ")"], list(keys)
    if link:
        # A role key covers a whole account; the shared view is this conversation's own turns.
        where.append("json_extract(context_json, '$.audience.scope') IN (" + ",".join("json(?)" for _ in link.scopes) + ")")
        values.extend(encode(scope) for scope in link.scopes)
    start_dt = _parse_utc_timestamp(start) if start is not None else None
    end_dt = _parse_utc_timestamp(end) if end is not None else None
    if start is not None:
        if start_dt is not None:
            where.append("received_at >= ?")
            values.append((start_dt - timedelta(days=1)).isoformat())
        else:
            where.append("julianday(received_at) >= julianday(?)")
            values.append(start)
    if end is not None:
        if end_dt is not None:
            where.append("received_at <= ?")
            values.append((end_dt + timedelta(days=1)).isoformat())
        else:
            where.append("julianday(received_at) <= julianday(?)")
            values.append(end)
    if private_only:
        where.append("scope_kind = 'private'")
    result = []
    for row in runtime.read_all(
        "SELECT scope_kind, context_key, context_json, received_at, record_json "
        "FROM astrbot_core_reply_observations WHERE " + " AND ".join(where)
        + " ORDER BY received_at DESC, turn_id DESC",
        tuple(values),
    ):
        row_dt = _parse_utc_timestamp(row["received_at"]) if (start_dt is not None or end_dt is not None) else None
        if start_dt is not None and (row_dt is None or row_dt < start_dt):
            continue
        if end_dt is not None and (row_dt is None or row_dt > end_dt):
            continue
        try:
            record = json.loads(row["record_json"])
            if type(record) is dict and record.get("version") == 1:
                obs = {**record, "context_key": row["context_key"], "context_json": row["context_json"],
                       **({"source_channel": json.loads(row["context_json"])["audience"]["scope"]["platform"]}
                          if link else {})}
                result.append((obs, row["scope_kind"], row_dt))
        except (TypeError, ValueError):
            continue
    return result


def observations_for_role(runtime, context, *, start=None, end=None, private_only=False):
    return [obs for obs, _scope_kind, _row_dt in _query_observations(
        runtime, context, start=start, end=end, private_only=private_only)]


def observations_with_scope_for_role(runtime, context, *, start=None, end=None, private_only=False):
    return _query_observations(runtime, context, start=start, end=end, private_only=private_only)


def request_rows(observations, *, include_signature_json=True):
    """Translate observations into usage projections, never into the old ledger."""
    result = []
    provider_json_cache = {}
    for observation in observations:
        requests = observation.get("requests", [])
        if not requests:
            continue
        audience_scope = json.loads(observation["context_json"])["audience"]["scope"]
        for request in requests:
            usage = request.get("usage") or {}
            at = request.get("finished_at") or request.get("started_at") or observation["received_at"]
            tokens, inputs, output = usage.get("tokens"), usage.get("input_tokens"), usage.get("output_tokens")
            cached, creation = usage.get("cached_input_tokens"), usage.get("cache_creation_tokens")
            reported = type(inputs) is int and type(cached) is int
            real_call = request.get("measurement_basis", "provider_response") == "provider_response"
            latency = request.get("latency_ms")
            state = request.get("state", "unknown")
            provider = request.get("provider", "")
            provider_json = provider_json_cache.get(provider)
            if provider_json is None:
                provider_json = encode({"provider": provider})
                provider_json_cache[provider] = provider_json
            signature = request.get("signature")
            values = dict.fromkeys(USAGE_FIELDS, 0)
            values.update(calls=int(real_call), tokens=tokens or 0, unconfirmed_calls=int(real_call and tokens is None),
                          unknown_calls=int(real_call and state == "unknown"), failures=int(real_call and state == "failed"),
                          active_calls=int(real_call and state == "started"),
                          input_tokens=inputs or 0, output_tokens=output or 0,
                          cache_reported_calls=int(real_call and reported), cache_telemetry_calls=int(real_call and (cached is not None or creation is not None)),
                          cache_input_tokens=(inputs or 0) if reported else 0,
                          cached_input_tokens=(cached or 0) if reported else 0,
                          cache_creation_tokens=creation or 0,
                          cache_other_input_tokens=max(0, inputs - cached - (creation or 0)) if reported else 0,
                          cache_low_calls=int(reported and inputs >= 1000 and cached * 5 < inputs),
                          latency_total_ms=latency or 0, latency_samples=int(latency is not None))
            row = {**values, "day": at[:10], "hour": at[11:13], "kind": CORE_CHAIN_KIND,
                "provider": provider, "provider_json": provider_json,
                "call_id": observation["turn_id"] + ":" + str(request["position"]), "position": request["position"],
                "origin_key": observation["context_key"], "audience_scope_json": audience_scope,
                "signature_json": encode(signature) if (include_signature_json and signature) else None,
                "state": state, "actual_tokens": tokens, "actual_cost": None,
                "input_tokens": inputs, "output_tokens": output, "cached_input_tokens": cached,
                "cache_creation_tokens": creation, "latency_ms": latency,
                "failure_code": request.get("failure_code"), "response_summary_json": None,
                "created_at": observation["received_at"], "started_at": request.get("started_at"), "finished_at": request.get("finished_at"),
                "execution_path": "astrbot_core", "usage_scope": request.get("measurement_basis", "provider_response"),
                "token_availability": usage.get("availability", "unavailable"), "cost_availability": "unavailable",
                "cache_availability": usage.get("cache_availability", "unavailable"),
                "signature_scope": request.get("signature_scope", "unavailable"),
                "aggregate": values}
            if not include_signature_json and signature:
                row["signature"] = signature
            result.append(row)
    return result


def availability_summary(observations):
    requests = [request for item in observations for request in item.get("requests", [])]
    return {"turns": len(observations), "observed_requests": sum(item.get("measurement_basis") == "provider_response" for item in requests),
            "fallback_usage_observations": sum(item.get("measurement_basis") != "provider_response" for item in requests),
            "request_observation_turns": sum(item.get("request_observation_available") is True for item in observations),
            "complete_request_observation_turns": sum(item.get("request_observation_complete") is True for item in observations),
            "partial_request_observation_turns": sum(item.get("tool_requery_observed") is True for item in observations),
            "usage_reported_requests": sum(type((item.get("usage") or {}).get("tokens")) is int for item in requests),
            "cache_reported_requests": sum(type((item.get("usage") or {}).get("cached_input_tokens")) is int for item in requests),
            "cost_reported_requests": 0, "cost_availability": "unavailable",
            "request_count_basis": "observed_provider_attempts_or_explicit_last_response_fallback",
            "platform_delivery_confirmation": "unavailable"}
