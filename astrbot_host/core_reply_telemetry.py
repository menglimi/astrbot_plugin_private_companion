"""Passive, asynchronous measurements of AstrBot's own reply runner."""

from collections.abc import Mapping
from datetime import datetime, timezone
from dataclasses import replace
from functools import wraps
from math import isfinite
import copy
import asyncio
import json
import logging
import re
import threading
import time

from companion_v2.execution.models import TaskContext, digest, stamp
from companion_v2.model_gateway.cache_diagnostics import signature_for_payload
from companion_v2.model_gateway.core_observations import record_observation
from .core_reply import CORE_REPLY_CONTEXT_EXTRA


CORE_REPLY_TELEMETRY_EXTRA = "_companion_v2_core_reply_telemetry"
logger = logging.getLogger(__name__)


def _passive(function):
    @wraps(function)
    def observed(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except Exception as error:
            logger.warning("[CompanionV2] 核心回复统计暂不可用 error_type=%s", type(error).__name__)
            return None
    return observed


def _value(value, name, default=None):
    return value.get(name, default) if isinstance(value, Mapping) else getattr(value, name, default)


def _number(value):
    return value if type(value) is int and value >= 0 else None


def _seconds_ms(value):
    return round(value * 1000) if type(value) in (int, float) and isfinite(value) and value >= 0 else None


def _usage(response):
    """SDK default cache zero is not proof of an explicit provider cache report."""
    usage = _value(response, "usage")
    raw = _value(response, "raw_completion")
    raw_usage = _value(raw, "usage") or _value(raw, "usage_metadata")
    other = _number(_value(usage, "input_other"))
    sdk_cached = _number(_value(usage, "input_cached"))
    output = _number(_value(usage, "output"))
    inputs = other + sdk_cached if other is not None and sdk_cached is not None else None
    cached = creation = None
    for detail in ("prompt_tokens_details", "input_tokens_details"):
        candidate = _number(_value(_value(raw_usage, detail), "cached_tokens"))
        if candidate is not None:
            cached = candidate
            break
    if cached is None:
        cached = _number(_value(raw_usage, "cache_read_input_tokens"))
    if cached is None:
        cached = _number(_value(raw_usage, "prompt_cache_hit_tokens"))
    if cached is None:
        cached = _number(_value(raw_usage, "cached_content_token_count"))
    creation = _number(_value(raw_usage, "cache_creation_input_tokens"))
    if creation is None:
        creation = _number(_value(raw_usage, "cache_creation_tokens"))
    if creation is None:
        breakdown = _value(raw_usage, "cache_creation")
        parts = [_number(_value(breakdown, key)) for key in
                 ("ephemeral_5m_input_tokens", "ephemeral_1h_input_tokens")]
        if any(part is not None for part in parts):
            creation = sum(part or 0 for part in parts)
    raw_input = _number(_value(raw_usage, "prompt_tokens"))
    if raw_input is None:
        raw_input = _number(_value(raw_usage, "prompt_token_count"))
    if raw_input is None:
        raw_input = _number(_value(raw_usage, "input_tokens"))
        if raw_input is not None and (_value(raw_usage, "cache_read_input_tokens") is not None or creation is not None):
            raw_input += (cached or 0) + (creation or 0)
    raw_output = _number(_value(raw_usage, "completion_tokens"))
    if raw_output is None:
        raw_output = _number(_value(raw_usage, "output_tokens"))
    if raw_output is None:
        raw_output = _number(_value(raw_usage, "candidates_token_count"))
        reasoning = _number(_value(raw_usage, "thoughts_token_count"))
        if raw_output is not None:
            raw_output += reasoning or 0
    if raw_input is not None:
        inputs = raw_input
    if raw_output is not None:
        output = raw_output
    explicit = raw_input is not None or raw_output is not None
    reported = explicit or (inputs is not None and output is not None and inputs + output > 0)
    if not reported:
        inputs = output = None
    cache_basis = "provider_report" if cached is not None or creation is not None else "unavailable"
    if cached is None and sdk_cached is not None and sdk_cached > 0:
        cached, cache_basis = sdk_cached, "sdk_positive_report"
    if inputs is not None and (cached is not None and cached > inputs
            or creation is not None and (cached or 0) + creation > inputs):
        cached = creation = None
        cache_basis = "unavailable"
    return {"input_tokens": inputs, "output_tokens": output,
            "tokens": inputs + output if inputs is not None and output is not None else None,
            "cached_input_tokens": cached, "cache_creation_tokens": creation,
            "availability": "reported" if reported else "unavailable",
            "cache_availability": cache_basis, "cost_availability": "unavailable"}


def _tools(toolset):
    return [{"type": "function", "function": {
        "name": _value(tool, "name", ""), "description": _value(tool, "description", ""),
        "parameters": _value(tool, "parameters", {})}}
        for tool in (_value(toolset, "tools", []) or []) if _value(tool, "active", True)]


def _signature(model, messages, toolset):
    signature = signature_for_payload(model, messages, _tools(toolset))
    if not signature.context_observed:
        ordinary = [message for message in messages if isinstance(message, Mapping) and message.get("role") != "system"]
        current_user = next((index for index in range(len(ordinary) - 1, -1, -1) if ordinary[index].get("role") == "user"), None)
        history = [message for index, message in enumerate(ordinary) if index != current_user]
        texts = []
        for message in history:
            content = message.get("content")
            if type(content) is str:
                text = content
            else:
                text = "".join(_value(part, "text", "") or _value(part, "think", "") or "" for part in (content or []))
            if message.get("tool_calls"):
                text += json.dumps(message["tool_calls"], ensure_ascii=False)
            texts.append(text)
        hashes = [digest(message) for message in history]
        chars, byte_count = sum(len(text) for text in texts), sum(len(text.encode("utf-8", errors="replace")) for text in texts)
        estimate = sum(max(1, int(sum("一" <= character <= "鿿" for character in text) * 0.35 + len(text) * 0.25)) for text in texts if text)
        signature = replace(signature, context_observed=True, context_hashes=(),
            context_sequence_hash=digest(hashes), context_multiset_hash=digest(sorted(hashes)),
            context_material_count=len(hashes), context_chars=chars, context_bytes=byte_count,
            context_est_tokens=estimate, message_est_tokens=max(0, signature.message_est_tokens - estimate))
    elif signature.context_hashes:
        # Persist compact sequence/multiset digests and counts only; omit the
        # per-material SHA-256 array (up to 256 hashes / ~17 KB per signature).
        signature = replace(signature, context_hashes=())
    return signature.to_record()


def _request_signature(req):
    messages = list(_value(req, "contexts", []) or [])
    system = _value(req, "system_prompt", "")
    if system:
        messages.insert(0, {"role": "system", "content": system})
    content = [{"type": "text", "text": _value(req, "prompt", "") or ""}]
    content += [{"type": "image_url"} for _ in (_value(req, "image_urls", []) or [])]
    content += [{"type": "input_audio"} for _ in (_value(req, "audio_urls", []) or [])]
    for part in (_value(req, "extra_user_content_parts", []) or []):
        part_type = _value(part, "type", "")
        content.append({"type": part_type, "text": _value(part, "text", "")} if part_type == "text" else {"type": part_type})
    messages.append({"role": "user", "content": content})
    return _signature(_value(req, "model"), messages, _value(req, "func_tool"))


def _runner_signature(runner, include_model):
    # Read the post-compaction runner messages. No second sanitization call and
    # no mutation of the request, provider, toolset or response occurs here.
    messages = []
    for message in runner.run_context.messages:
        dump = getattr(message, "model_dump", None)
        messages.append(dump() if callable(dump) else message)
    model = _value(runner.req, "model") if include_model else None
    if not model:
        getter = getattr(runner.provider, "get_model", None)
        model = getter() if callable(getter) else None
    return _signature(model, messages, _value(runner.req, "func_tool"))


def _provider(runner):
    provider = getattr(runner, "provider", None)
    config = _value(provider, "provider_config", {})
    identifier = _value(config, "id", "")
    return identifier if type(identifier) is str else ""


def _state(event):
    state = event.get_extra(CORE_REPLY_TELEMETRY_EXTRA)
    return state if isinstance(state, dict) else None


def _at(state):
    return stamp(state["now"]())


def _snapshot(state):
    submit = state.get("submit")
    if not callable(submit):
        return
    slot = None
    lock = state.get("_flush_lock")
    if lock is None:
        lock = threading.Lock()
        state["_flush_lock"] = lock
    try:
        record = state["record"]
        record["revision"] += 1
        record["observed_at"] = _at(state)
        frozen = copy.deepcopy(record)
        with lock:
            pending = state.get("_pending_flush")
            if pending is not None and not pending["started"]:
                pending["record"] = frozen
                return
            slot = {"record": frozen, "started": False}
            state["_pending_flush"] = slot

        def _persist():
            with lock:
                slot["started"] = True
                payload = slot["record"]
                if state.get("_pending_flush") is slot:
                    state["_pending_flush"] = None
            record_observation(state["runtime"], state["context"], payload)

        submit(_persist)
    except Exception as error:
        if slot is not None:
            with lock:
                if state.get("_pending_flush") is slot:
                    state["_pending_flush"] = None
        logger.warning("[CompanionV2] 核心回复统计暂不可用 error_type=%s", type(error).__name__)


@_passive
def observe_core_waiting_llm_request(event, *, now=None):
    """Mark AstrBot's wait-for-LLM hook without storing request content."""
    core = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
    if not isinstance(core, dict) or not isinstance(core.get("prepared"), Mapping):
        return None
    existing = core.get("_waiting_llm_at")
    if isinstance(existing, str) and existing:
        return existing
    clock = now or (lambda: datetime.now(timezone.utc))
    observed = stamp(clock())
    core["_waiting_llm_at"] = observed
    return observed


def prepare_core_reply_telemetry(event, req, *, application, submit=None, now=None):
    """Called after V2 prompt/tool injection. Persistence never runs inline."""
    core = event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
    prepared = core.get("prepared") if isinstance(core, dict) else None
    context = prepared.get("context") if isinstance(prepared, Mapping) else None
    if not isinstance(context, TaskContext):
        return None
    existing = _state(event)
    if existing is not None:
        return existing
    clock = now or (lambda: datetime.now(timezone.utc))
    moment = clock()
    native = prepared.get("native_input")
    received = getattr(native, "at", None) or moment
    record = {"version": 1, "revision": 0, "turn_id": prepared.get("turn_id") or "core-reply:" + digest([context.key, prepared.get("event_id")]),
        "event_id": prepared.get("event_id", ""), "execution_path": "astrbot_core",
        "received_at": stamp(received), "waiting_llm_at": core.get("_waiting_llm_at"),
        "request_hook_at": stamp(moment), "observed_at": stamp(moment),
        "generation_state": "prepared", "generation_finished_at": None, "agent_started_at": None,
        "request_observation_available": False, "requests": [], "tools": [],
        "request_observation_complete": None, "tool_requery_observed": False,
        "ttft_ms": None, "runner_duration_ms": None, "usage_scope": "unavailable",
        "delivery": {"state": "not_started", "attempts": 0, "returned": 0, "unknown": 0, "failed": 0,
                     "first_started_at": None, "first_returned_at": None, "last_returned_at": None,
                     "platform_confirmed": False, "measurement_basis": "native_send_return"}}
    record["preparation_steps"] = copy.deepcopy(list(prepared.get("preparation_steps", ()))
        + list(core.get("_preparation_steps", ())))
    materials = list(prepared.get("context_materials", ()))
    system_prompt = _value(req, "system_prompt", "") or ""
    instruction = prepared.get("prompt_instruction", "")
    stable_instruction = prepared.get("stable_prompt_instruction", "")
    background_injected = bool(instruction and instruction in system_prompt) or (
        core.get("_perception_background_injected") is True)
    stable_injected = bool(stable_instruction and stable_instruction in system_prompt)
    injected_purposes = set()
    stable_purposes = {"expression_preferences", "task_prompt", "role_profile"}
    if stable_injected:
        injected_purposes.update(stable_purposes)
    if background_injected:
        injected_purposes.update(item["purpose"] for item in materials
            if item["purpose"] not in stable_purposes | {"perception"})
    if core.get("_perception_material_injected") is True:
        injected_purposes.add("perception")
    record["materials"] = [{**item, "state": "not_injected" if item["state"] == "ready"
        and item["purpose"] not in injected_purposes else item["state"]} for item in materials]
    record["offered_tools"] = [name for tool in (_value(_value(req, "func_tool"), "tools", ()) or ())
        if _value(tool, "active", True) and isinstance(name := _value(tool, "name"), str)
        and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.:-]{0,127}", name)][:256]
    try:
        record["request_hook_signature"] = _request_signature(req)
    except Exception:
        record["request_hook_signature"] = None
    record["signature_scope"] = "provider_request_hook"
    state = {"record": record, "context": context, "runtime": application.runtime,
             "submit": submit, "now": clock, "runner": None, "response_ids": set(),
             "_flush_lock": threading.Lock(), "_pending_flush": None}
    def preparation_observer(step):
        record["preparation_steps"].append(copy.deepcopy(step))
    core["_preparation_observer"] = preparation_observer
    event.set_extra(CORE_REPLY_TELEMETRY_EXTRA, state)
    return state


@_passive
def observe_core_reply_suppressed(event, prepared, *, application, submit=None, now=None):
    """Record an actual pre-model silence without inventing a request or send."""
    context = prepared.get("context")
    if not isinstance(context, TaskContext) or application is None:
        return None
    clock = now or (lambda: datetime.now(timezone.utc))
    moment = clock()
    received = getattr(prepared.get("native_input"), "at", None) or moment
    record = {"version": 1, "revision": 0, "turn_id": prepared["turn_id"],
        "event_id": prepared.get("event_id", ""), "execution_path": "astrbot_core",
        "received_at": stamp(received), "request_hook_at": None, "observed_at": stamp(moment),
        "generation_state": "suppressed", "generation_finished_at": stamp(moment),
        "request_observation_available": True, "request_observation_complete": True,
        "tool_requery_observed": False, "requests": [], "tools": [],
        "ttft_ms": None, "runner_duration_ms": None, "usage_scope": "not_requested",
        "request_hook_signature": None, "signature_scope": "not_requested",
        "delivery": {"state": "not_started", "attempts": 0, "returned": 0, "unknown": 0, "failed": 0,
            "first_started_at": None, "first_returned_at": None, "last_returned_at": None,
            "platform_confirmed": False, "measurement_basis": "native_send_return"}}
    record["preparation_steps"] = copy.deepcopy(list(prepared.get("preparation_steps", ())))
    record["materials"] = [{**item, "state": "not_injected" if item["state"] == "ready" else item["state"]}
                           for item in prepared.get("context_materials", ())]
    record["offered_tools"] = []
    state = {"record": record, "context": context, "runtime": application.runtime, "submit": submit, "now": clock}
    _snapshot(state)
    return record


def _active_runner(event, run_context):
    from .core_reply_tool_dispatch import active_core_runner
    return active_core_runner(event, run_context)


def _restore_runner(state):
    runner = state.get("runner")
    if runner is None:
        return
    for name, (had_local, old_local, original, wrapper) in state.get("runner_methods", {}).items():
        try:
            if getattr(runner, name, None) is wrapper:
                if had_local:
                    setattr(runner, name, old_local)
                else:
                    delattr(runner, name)
        except Exception:
            pass
    state["runner"] = None


@_passive
def observe_core_agent_begin(event, run_context):
    state = _state(event)
    if state is None or state.get("runner") is not None:
        return
    runner = _active_runner(event, run_context)
    if runner is None:
        return
    state["record"]["agent_started_at"] = state["record"].get("agent_started_at") or _at(state)
    state["record"]["generation_state"] = "running"
    if not callable(getattr(runner, "_iter_llm_responses", None)) or not callable(getattr(runner, "step", None)):
        return
    methods = {}
    state["runner"], state["runner_methods"] = runner, methods
    original_iter, original_step = runner._iter_llm_responses, runner.step
    original_resolve = getattr(runner, "_resolve_tool_exec", None)
    from .core_reply_tool_dispatch import observe_core_tool_dispatch_begin
    dispatch = observe_core_tool_dispatch_begin(event, run_context)

    async def observed_iter(*args, **kwargs):
        request = None
        started = time.monotonic()
        try:
            try:
                from .core_reply_media_prompt import core_media_request_diagnostics
                media = core_media_request_diagnostics(event, runner.run_context)
            except Exception:
                media = None
            request = {"position": len(state["record"]["requests"]) + 1, "state": "started",
                       "started_at": _at(state), "finished_at": None, "latency_ms": None,
                       "ttft_ms": None, "provider": _provider(runner), "usage": _usage(None),
                       "signature": None, "signature_scope": "runner_pre_provider", "measurement_basis": "provider_response"}
            if media is not None:
                request["media"] = media
            try:
                request["signature"] = _runner_signature(runner, kwargs.get("include_model", True))
            except Exception:
                request["signature_scope"] = "unavailable"
            state["record"]["requests"].append(request)
            state["record"]["request_observation_available"] = True
            state["record"]["generation_state"] = "running"
        except Exception:
            request = None
        try:
            async for response in original_iter(*args, **kwargs):
                try:
                    if request is not None:
                        if getattr(response, "is_chunk", False):
                            if request["ttft_ms"] is None:
                                request["ttft_ms"] = _seconds_ms(time.monotonic() - started)
                        else:
                            request.update(state="failed" if getattr(response, "role", None) == "err" else "returned",
                                           finished_at=_at(state), latency_ms=_seconds_ms(time.monotonic() - started), usage=_usage(response))
                            state["response_ids"].add(id(response))
                except Exception:
                    pass
                yield response
        except BaseException as error:
            closing_finished_response = isinstance(error, GeneratorExit) and request is not None and request["state"] in ("returned", "failed")
            try:
                if request is not None and not closing_finished_response:
                    request.update(state="unknown", failure_code=type(error).__name__, finished_at=_at(state),
                                   latency_ms=_seconds_ms(time.monotonic() - started))
            except Exception:
                pass
            if isinstance(error, (asyncio.CancelledError, GeneratorExit)) and not closing_finished_response:
                try:
                    _observe_done(state, runner, None, failed=True)
                except Exception:
                    pass
                finally:
                    _restore_runner(state)
            raise
        finally:
            try:
                if request is not None and request["state"] == "started":
                    request.update(state="unknown", finished_at=_at(state), latency_ms=_seconds_ms(time.monotonic() - started))
            except Exception:
                pass

    async def observed_resolve(*args, **kwargs):
        before = args[0] if args else kwargs.get("llm_resp")
        started = time.monotonic()
        try:
            at = _at(state)
        except Exception:
            at = state["record"]["request_hook_at"]
        state["record"]["tool_requery_observed"] = True
        try:
            result = await original_resolve(*args, **kwargs)
            try:
                response = result[0]
                if response is not before:
                    state["record"]["requests"].append({"position": len(state["record"]["requests"]) + 1,
                        "state": "failed" if getattr(response, "role", None) == "err" else "returned",
                        "started_at": at, "finished_at": _at(state), "latency_ms": _seconds_ms(time.monotonic() - started),
                        "provider": _provider(runner), "usage": _usage(response), "signature": None,
                        "signature_scope": "unavailable", "measurement_basis": "last_tool_requery_only"})
            except Exception:
                pass
            return result
        except asyncio.CancelledError:
            try:
                _observe_done(state, runner, None, failed=True)
            except Exception:
                pass
            finally:
                _restore_runner(state)
            raise

    async def observed_step(*args, **kwargs):
        failed = False
        try:
            async for item in original_step(*args, **kwargs):
                yield item
        except BaseException:
            failed = True
            raise
        finally:
            try:
                done = runner.done()
            except Exception:
                done = False
            if failed or done:
                try:
                    if state["record"]["generation_finished_at"] is None:
                        _observe_done(state, runner, getattr(runner, "final_llm_resp", None), failed=failed)
                except Exception:
                    pass
                finally:
                    _restore_runner(state)

    def dispatch_finished(current, failed):
        try:
            if state["record"]["generation_finished_at"] is None:
                _observe_done(state, current, getattr(current, "final_llm_resp", None), failed=failed)
        finally:
            _restore_runner(state)

    try:
        wrappers = [("_iter_llm_responses", original_iter, observed_iter)]
        if dispatch is not None:
            dispatch["finished"].append(dispatch_finished)
        else:
            wrappers.append(("step", original_step, observed_step))
        if callable(original_resolve):
            wrappers.append(("_resolve_tool_exec", original_resolve, observed_resolve))
        for name, original, wrapper in wrappers:
            local = getattr(runner, "__dict__", {})
            methods[name] = (name in local, local.get(name), original, wrapper)
            setattr(runner, name, wrapper)
    except Exception:
        _restore_runner(state)


def _observe_done(state, runner, response, *, failed=False):
    record = state["record"]
    record["generation_finished_at"] = _at(state)
    record["generation_state"] = "failed" if failed or getattr(response, "role", None) == "err" else "completed"
    stats = getattr(runner, "stats", None)
    if stats is not None:
        duration = getattr(stats, "duration", None)
        record["runner_duration_ms"] = _seconds_ms(duration)
        ttft = getattr(stats, "time_to_first_token", None)
        record["ttft_ms"] = _seconds_ms(ttft) if ttft else None
    if not record["requests"]:
        usage = _usage(response)
        basis = "last_response_only"
        aggregate = getattr(stats, "token_usage", None)
        if aggregate is not None and any((_value(aggregate, field, 0) or 0) > 0 for field in ("input_other", "input_cached", "output")):
            usage = _usage({"usage": aggregate})
            basis = "runner_aggregate"
        record["requests"].append({"position": 1, "state": "failed" if record["generation_state"] == "failed" else "returned",
            "provider": _provider(runner), "started_at": record["request_hook_at"], "finished_at": record["generation_finished_at"],
            "latency_ms": record["runner_duration_ms"], "usage": usage,
            "signature": record["request_hook_signature"], "signature_scope": "provider_request_hook", "measurement_basis": basis})
    elif not record["request_observation_available"]:
        aggregate = getattr(stats, "token_usage", None)
        if aggregate is not None and any((_value(aggregate, field, 0) or 0) > 0 for field in ("input_other", "input_cached", "output")):
            record["requests"][0].update(usage=_usage({"usage": aggregate}), measurement_basis="runner_aggregate",
                provider=_provider(runner), finished_at=record["generation_finished_at"], latency_ms=record["runner_duration_ms"])
    record["request_observation_complete"] = bool(record["request_observation_available"] and not record["tool_requery_observed"])
    record["usage_scope"] = ("provider_responses_partial" if record["tool_requery_observed"] else "provider_responses"
                             if record["request_observation_available"] else record["requests"][0]["measurement_basis"])
    _snapshot(state)


@_passive
def observe_core_agent_done(event, run_context, response):
    state = _state(event)
    if state is None:
        return
    runner = _active_runner(event, run_context) or state.get("runner")
    try:
        _observe_done(state, runner, response)
    except Exception as error:
        logger.warning("[CompanionV2] 核心回复用量暂不可用 error_type=%s", type(error).__name__)
    finally:
        _restore_runner(state)


@_passive
def observe_core_llm_response(event, response):
    """Fallback only: a response hook alone cannot claim multi-round usage."""
    state = _state(event)
    if state is None or state.get("runner") is not None or state["record"]["requests"]:
        return
    try:
        _observe_done(state, None, response)
    except Exception:
        pass


@_passive
def observe_core_tool_started(event, tool):
    state = _state(event)
    if state is None:
        return
    name = re.sub(r"[^a-zA-Z0-9_.:-]", "", _value(tool, "name", ""))[:120]
    state["record"]["tools"].append({"sequence": len(state["record"]["tools"]) + 1,
        "action": name, "state": "started", "started_at": _at(state), "finished_at": None})


@_passive
def observe_core_tool_finished(event, tool):
    state = _state(event)
    if state is None:
        return
    name = re.sub(r"[^a-zA-Z0-9_.:-]", "", _value(tool, "name", ""))[:120]
    item = next((item for item in state["record"]["tools"] if item["action"] == name and item["finished_at"] is None), None)
    if item is not None:
        item.update(state="returned", finished_at=_at(state))


@_passive
def observe_core_reply_delivery(event, stage="returned"):
    state = _state(event)
    if state is None:
        return
    delivery = state["record"]["delivery"]
    if stage == "stream_started":
        # A native stream may buffer before calling event.send. Preserve any
        # earlier real send and replace this provisional time on that call.
        state["_stream_first_started_at"] = delivery["first_started_at"]
        delivery["attempts"] += 1
        delivery["first_started_at"] = delivery["first_started_at"] or _at(state)
        stage = "started"
    elif stage == "started":
        delivery["attempts"] += 1
        if "_stream_first_started_at" in state:
            delivery["first_started_at"] = state.pop("_stream_first_started_at") or _at(state)
        else:
            delivery["first_started_at"] = delivery["first_started_at"] or _at(state)
    elif stage == "returned":
        delivery["returned"] += 1
        delivery["first_returned_at"] = delivery["first_returned_at"] or _at(state)
        delivery["last_returned_at"] = _at(state)
    elif stage == "unknown":
        delivery["unknown"] += 1
    elif stage == "failed":
        delivery["failed"] += 1
    if delivery["state"] not in ("unknown", "denied"):
        delivery["state"] = stage


@_passive
def finish_core_reply_telemetry(event, delivery=None):
    """Snapshot a delivery phase. It does not end or replay the native runner."""
    state = _state(event)
    if state is None:
        return
    state.pop("_stream_first_started_at", None)
    source = delivery if isinstance(delivery, Mapping) else event.get_extra(CORE_REPLY_CONTEXT_EXTRA)
    if isinstance(source, Mapping):
        observed = state["record"]["delivery"]
        observed["attempts"] = source.get("send_attempts", observed["attempts"])
        observed["returned"] = source.get("send_returns", observed["returned"])
        observed["unknown"] = source.get("unknown_sends", source.get("send_unknown", observed["unknown"]))
        observed["failed"] = source.get("send_failed", observed["failed"])
        if not observed["returned"]:
            observed["first_returned_at"] = observed["last_returned_at"] = None
        if source.get("denial_notified") or source.get("send_state") == "denied":
            observed["state"] = "denied"
        elif observed["unknown"]:
            observed["state"] = "unknown"
        elif observed["returned"]:
            observed["state"] = "returned"
            observed["first_returned_at"] = observed["first_returned_at"] or _at(state)
            observed["last_returned_at"] = observed["last_returned_at"] or _at(state)
        elif observed["failed"]:
            observed["state"] = "failed"
    _snapshot(state)
