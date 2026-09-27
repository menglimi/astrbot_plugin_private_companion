# -*- coding: utf-8 -*-
"""hdsi_experiment 拆分件 part02（机械搬移，行为不变）。

函数 / 常量 / 类逐字搬自 hdsi_experiment.py，仅调整模块级依赖的导入来源。
"""
import hashlib
import time
from types import SimpleNamespace
from typing import Any

try:  # package import
    from .hdsi_experiment_part01 import (
        ACTOR_MAX_COUNT,
        ACTOR_MAX_WINDOWS,
        ACTOR_STATE_KEY,
        ACTOR_TTL_SECONDS,
        EVENT_LEDGER_MAX_EVENTS,
        HDSI_LIFE_TICK_INTERVAL_SECONDS,
        TRIAL_MAX_EVENTS,
        _TRIAL_MODES,
        _actor_has_enabled_route,
        _bounded_text,
        _event_ledger_root,
        _event_origin,
        _event_sender_id,
        _hdsi_activity_projection,
        _hdsi_actor_key,
        _life_phase_for_hour,
        _run_trial_update,
        _safe_metric_float,
        _safe_metric_int,
        _trial_root_and_bucket,
        _window_ref,
    )
except ImportError:  # direct test/import from the plugin directory
    from hdsi_experiment_part01 import (
        ACTOR_MAX_COUNT,
        ACTOR_MAX_WINDOWS,
        ACTOR_STATE_KEY,
        ACTOR_TTL_SECONDS,
        EVENT_LEDGER_MAX_EVENTS,
        HDSI_LIFE_TICK_INTERVAL_SECONDS,
        TRIAL_MAX_EVENTS,
        _TRIAL_MODES,
        _actor_has_enabled_route,
        _bounded_text,
        _event_ledger_root,
        _event_origin,
        _event_sender_id,
        _hdsi_activity_projection,
        _hdsi_actor_key,
        _life_phase_for_hour,
        _run_trial_update,
        _safe_metric_float,
        _safe_metric_int,
        _trial_root_and_bucket,
        _window_ref,
    )


def _event_message_id(plugin: Any, event: Any) -> str:
    """Read a platform message ID when available for retry idempotence."""
    message_id = ""
    getter = getattr(plugin, "_event_message_id", None)
    if callable(getter):
        try:
            message_id = str(getter(event) or "").strip()
        except Exception:
            pass
    return message_id


def _trial_event_digest(plugin: Any, event: Any) -> str:
    message_id = _event_message_id(plugin, event)
    text = str(getattr(event, "message_str", "") or "")
    raw = "|".join((message_id, _event_origin(event), text))
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:20]


async def _record_hdsi_event(plugin: Any, event: Any, event_type: str, **details: Any) -> None:
    mode = str(getattr(event, "private_companion_hdsi_mode", "legacy") or "legacy")
    if mode not in _TRIAL_MODES or not getattr(event, "private_companion_hdsi_chat_route_ready", False):
        return
    digest = _trial_event_digest(plugin, event)
    # Chat hooks are retried and therefore remain idempotent. Autonomous
    # ticks may legitimately repeat in the same window, so they receive a
    # producer supplied tick_id or a per-call nonce instead of being folded
    # into one event forever.
    event_nonce = details.get("tick_id") or details.get("event_id")
    if event_type in {"autonomous_tick", "life_tick", "proactive_event"} and not event_nonce:
        event_nonce = time.time_ns()
    event_id = hashlib.sha256(
        f"{event_type}|{digest}|{event_nonce or ''}".encode("utf-8", errors="replace")
    ).hexdigest()[:24]
    window = _window_ref(event)
    actor_key = _hdsi_actor_key(event)
    item = {
        "event_id": event_id,
        "event_type": _bounded_text(event_type, 32),
        "mode": mode,
        "actor_key": actor_key,
        "scope": _bounded_text(getattr(event, "private_companion_hdsi_scope", ""), 16),
        "window": _bounded_text(window, 220),
        "scope_fingerprint": _bounded_text(getattr(event, "private_companion_hdsi_scope_fingerprint", ""), 64),
        "at": time.time(),
    }
    detail_limits = {
        "input_digest": 64, "response_digest": 64, "content_digest": 64,
        "tick_id": 100, "event_id": 100, "life_phase": 32,
        "activity": 80, "failure_reason": 80,
        "response_chars": 0,
    }
    for key, value in details.items():
        if key not in detail_limits or value in (None, ""):
            continue
        if key.endswith("_chars"):
            item[key] = min(100_000, max(0, _safe_metric_int(value)))
        else:
            item[key] = _bounded_text(value, detail_limits[key])
    def update() -> None:
        state = _event_ledger_root(plugin)
        if state is None:
            return
        ledger, actors = state
        events = ledger["events"]
        if any(isinstance(existing, dict) and existing.get("event_id") == event_id for existing in events[-64:]):
            return
        events.append(item)
        del events[:-EVENT_LEDGER_MAX_EVENTS]
        cutoff = item["at"] - ACTOR_TTL_SECONDS
        for stale_key, stale in list(actors.items()):
            if stale_key == actor_key or not isinstance(stale, dict):
                continue
            if str(stale.get("mode", "") or "") not in _TRIAL_MODES:
                continue
            if _safe_metric_float(stale.get("last_event_at")) < cutoff:
                actors.pop(stale_key, None)
        trial_actor_keys = [
            key for key, value in actors.items()
            if isinstance(value, dict) and str(value.get("mode", "") or "") in _TRIAL_MODES
        ]
        if actor_key not in actors and len(trial_actor_keys) >= ACTOR_MAX_COUNT:
            oldest_key = min(
                trial_actor_keys,
                key=lambda key: _safe_metric_float(actors[key].get("last_event_at")),
            )
            actors.pop(oldest_key, None)
        actor = actors.setdefault(actor_key, {
            "schema_version": 1, "event_count": 0, "last_event_at": 0.0,
            "last_scope": "", "previous_scope": "", "previous_window": "", "windows": [],
            "mode": mode, "persona_id": _bounded_text(getattr(event, "private_companion_hdsi_persona_id", ""), 120),
            "scope_fingerprint": _bounded_text(getattr(event, "private_companion_hdsi_scope_fingerprint", ""), 64),
        })
        if not isinstance(actor, dict):
            actor = {"schema_version": 1, "event_count": 0, "last_event_at": 0.0, "last_scope": "", "previous_scope": "", "previous_window": "", "windows": []}
            actors[actor_key] = actor
        actor["mode"] = mode
        actor["persona_id"] = _bounded_text(
            getattr(event, "private_companion_hdsi_persona_id", "") or actor.get("persona_id"), 120
        )
        actor["scope_fingerprint"] = _bounded_text(
            getattr(event, "private_companion_hdsi_scope_fingerprint", "") or actor.get("scope_fingerprint"), 64
        )
        previous_scope = _bounded_text(actor.get("last_scope"), 16)
        previous_window = _bounded_text(actor.get("windows", [""])[0] if actor.get("windows") else "", 220)
        actor["event_count"] = max(0, _safe_metric_int(actor.get("event_count"))) + 1
        actor["last_event_at"] = item["at"]
        if event_type not in {"life_tick", "autonomous_tick"}:
            actor["last_chat_event_at"] = item["at"]
        actor["previous_scope"] = previous_scope
        actor["previous_window"] = previous_window
        actor["last_scope"] = item["scope"]
        windows = actor.setdefault("windows", [])
        if not isinstance(windows, list):
            windows = []
            actor["windows"] = windows
        if window:
            windows[:] = [window] + [value for value in windows if value != window]
            del windows[ACTOR_MAX_WINDOWS:]
        if event_type in {"life_tick", "autonomous_tick"}:
            phase = _bounded_text(details.get("life_phase"), 32)
            activity = _bounded_text(details.get("activity"), 80)
            if phase:
                actor["life_phase"] = phase
            if activity:
                actor["current_activity"] = activity
            actor["last_life_tick_at"] = item["at"]

    await _run_trial_update(plugin, update)


async def run_hdsi_life_tick(plugin: Any, *, now: float | None = None) -> int:
    """Advance existing HDSI actors without creating messages or model calls.

    This is deliberately a sidecar to the legacy scheduler. It only touches
    actor states that were already created by an opted-in HDSI route, and is
    throttled per actor so a one-minute scheduler cannot grow the ledger.
    """
    check_now = time.time() if now is None else (_safe_metric_float(now) or time.time())
    data = getattr(plugin, "data", None)
    if not isinstance(data, dict):
        return 0
    actors = data.get(ACTOR_STATE_KEY)
    if not isinstance(actors, dict) or not actors:
        return 0
    due: list[tuple[str, dict[str, Any]]] = []
    hour = time.localtime(check_now).tm_hour
    phase = _life_phase_for_hour(hour)
    activity = _hdsi_activity_projection(plugin)
    lock = getattr(plugin, "_data_lock", None)

    def reserve() -> None:
        for actor_key, raw_state in list(actors.items()):
            if not isinstance(raw_state, dict):
                continue
            mode = str(raw_state.get("mode", "") or "")
            if mode not in _TRIAL_MODES:
                continue
            if not _actor_has_enabled_route(plugin, raw_state):
                continue
            has_activity_timestamp = bool(
                raw_state.get("last_chat_event_at") or raw_state.get("last_event_at")
            )
            last_chat = _safe_metric_float(
                raw_state.get("last_chat_event_at") or raw_state.get("last_event_at")
            )
            # Actors persisted by the first trial schema may have an event
            # count but no timestamp. Give those records one migration tick;
            # subsequent ticks are throttled and normal chat events establish
            # a real activity timestamp.
            if last_chat <= 0 and not has_activity_timestamp and _safe_metric_int(raw_state.get("event_count")) > 0:
                last_chat = check_now
                raw_state["last_chat_event_at"] = check_now
            if last_chat <= 0 or check_now - last_chat >= ACTOR_TTL_SECONDS:
                continue
            last = _safe_metric_float(raw_state.get("last_life_tick_at"))
            if last > 0 and check_now - last < HDSI_LIFE_TICK_INTERVAL_SECONDS:
                continue
            raw_state["last_life_tick_at"] = check_now
            raw_state["life_phase"] = phase
            due.append((str(actor_key), dict(raw_state)))

    try:
        if lock is not None:
            async with lock:
                reserve()
        else:
            reserve()
        if due:
            saver = getattr(plugin, "_schedule_data_save", None)
            if callable(saver):
                saver(sections={ACTOR_STATE_KEY}, delay=0.2)
    except Exception:
        return 0

    recorded = 0
    for actor_key, state in due:
        windows = state.get("windows") if isinstance(state.get("windows"), list) else []
        synthetic = SimpleNamespace(
            private_companion_hdsi_mode=str(state.get("mode") or "hdsi_active"),
            private_companion_hdsi_chat_route_ready=True,
            private_companion_hdsi_continuity_actor_id=actor_key,
            private_companion_hdsi_actor_id=actor_key,
            private_companion_hdsi_persona_id=_bounded_text(state.get("persona_id"), 120),
            private_companion_hdsi_scope=_bounded_text(state.get("last_scope"), 16) or "global",
            private_companion_hdsi_scope_fingerprint=_bounded_text(state.get("scope_fingerprint"), 64),
            unified_msg_origin=_bounded_text(windows[0] if windows else "", 220),
            is_private_chat=lambda: _bounded_text(state.get("last_scope"), 16) == "private",
        )
        try:
            await _record_hdsi_event(
                plugin,
                synthetic,
                "life_tick",
                tick_id=f"{int(check_now // HDSI_LIFE_TICK_INTERVAL_SECONDS)}:{actor_key}",
                life_phase=phase,
                activity=activity,
            )
            recorded += 1
        except Exception:
            continue
    return recorded


async def record_hdsi_inbound_event(plugin: Any, event: Any) -> None:
    if getattr(event, "private_companion_hdsi_inbound_recorded", False):
        return
    checker = getattr(plugin, "_event_is_inbound_chat_message", None)
    if callable(checker) and not checker(event):
        return
    await _record_hdsi_event(plugin, event, "inbound_message", input_digest=hashlib.sha256(str(getattr(event, "message_str", "") or "").encode("utf-8", errors="replace")).hexdigest()[:20])
    setattr(event, "private_companion_hdsi_inbound_recorded", True)


async def record_hdsi_outbound_event(plugin: Any, event: Any, response: Any) -> None:
    if getattr(event, "private_companion_hdsi_outbound_recorded", False):
        return
    # AstrBot may emit intermediate chunks/tool-loop responses through the
    # same hook.  Only a final assistant text is a user-visible outbound turn.
    role = str(getattr(response, "role", "assistant") or "assistant").strip().lower()
    if role not in {"assistant", ""} or bool(getattr(response, "is_chunk", False)):
        return
    if getattr(response, "tools_call_args", None) or getattr(response, "tools_call_name", None):
        return
    text = str(getattr(response, "completion_text", "") or "")
    if not text.strip():
        return
    await _record_hdsi_event(
        plugin, event, "outbound_message",
        response_digest=hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:20],
        response_chars=len(text),
    )
    setattr(event, "private_companion_hdsi_outbound_recorded", True)


def _trial_input_id(plugin: Any, event: Any) -> tuple[str, bool]:
    """Return an id and whether it can safely deduplicate retried deliveries."""
    message_id = _event_message_id(plugin, event)
    if message_id:
        return _trial_event_digest(plugin, event), True
    nonce = getattr(event, "private_companion_hdsi_trial_nonce", None)
    if not nonce:
        nonce = str(time.time_ns())
        try:
            setattr(event, "private_companion_hdsi_trial_nonce", nonce)
        except Exception:
            pass
    text = str(getattr(event, "message_str", "") or "")
    raw = "|".join(("", _event_origin(event), text, str(nonce)))
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:20], False


async def record_trial_input(plugin: Any, event: Any) -> None:
    """Record bounded, content-free trial input evidence for later comparison."""
    mode = str(getattr(event, "private_companion_hdsi_mode", "legacy") or "legacy")
    if mode not in _TRIAL_MODES:
        return
    if getattr(event, "private_companion_hdsi_input_recorded", False):
        return
    now = time.time()
    trial_id, idempotent = _trial_input_id(plugin, event)
    duplicate = False
    item = {
        "trial_id": trial_id,
        "mode": mode,
        "scope": _bounded_text(getattr(event, "private_companion_hdsi_scope", ""), 32),
        "scope_fingerprint": _bounded_text(getattr(event, "private_companion_hdsi_scope_fingerprint", ""), 64),
        "actor_id": _bounded_text(getattr(event, "private_companion_hdsi_actor_id", "")),
        "persona_id": _bounded_text(getattr(event, "private_companion_hdsi_persona_id", "")),
        "binding_revision": _bounded_text(getattr(event, "private_companion_hdsi_binding_revision", ""), 64),
        "input_digest": hashlib.sha256(str(getattr(event, "message_str", "") or "").encode("utf-8", errors="replace")).hexdigest()[:20],
        "started_at": now,
    }
    plan = getattr(event, "private_companion_hdsi_response_plan", None)
    if isinstance(plan, dict):
        item["plan_id"] = _bounded_text(plan.get("plan_id"), 32)
        item["decision_schema_version"] = _safe_metric_int(plan.get("schema_version"))
    def update() -> None:
        state = _trial_root_and_bucket(plugin, mode)
        if state is None:
            return
        root, bucket = state
        events = root.setdefault("events", [])
        if not isinstance(events, list):
            events = []
            root["events"] = events
        if idempotent and any(
            isinstance(existing, dict) and existing.get("trial_id") == item["trial_id"]
            for existing in events
        ):
            nonlocal duplicate
            duplicate = True
            return
        events.append(item)
        del events[:-TRIAL_MAX_EVENTS]
        bucket["requests"] += 1
    try:
        await _run_trial_update(plugin, update)
        setattr(event, "private_companion_hdsi_input_recorded", True)
        setattr(event, "private_companion_hdsi_trial_id", item["trial_id"])
        setattr(event, "private_companion_hdsi_trial_started_at", now)
        if duplicate:
            setattr(event, "private_companion_hdsi_trial_duplicate", True)
    except Exception:
        return


async def finalize_trial_response(plugin: Any, event: Any, response: Any) -> None:
    mode = str(getattr(event, "private_companion_hdsi_mode", "legacy") or "legacy")
    started = _safe_metric_float(getattr(event, "private_companion_hdsi_trial_started_at", 0.0))
    if mode not in _TRIAL_MODES or started <= 0:
        return
    if getattr(event, "private_companion_hdsi_response_recorded", False):
        return
    if getattr(event, "private_companion_hdsi_failure_recorded", False):
        return
    if getattr(event, "private_companion_hdsi_trial_duplicate", False):
        return
    trial_id = str(getattr(event, "private_companion_hdsi_trial_id", "") or "")
    if not trial_id:
        return
    completion = str(getattr(response, "completion_text", "") or "")
    elapsed = max(0, int((time.time() - started) * 1000))
    prompt_bytes = max(0, int(getattr(event, "private_companion_hdsi_prompt_bytes", 0) or 0))
    def update() -> None:
        state = _trial_root_and_bucket(plugin, mode)
        if state is None:
            return
        root, bucket = state
        events = root.get("events") if isinstance(root.get("events"), list) else []
        item = next((entry for entry in reversed(events) if isinstance(entry, dict) and entry.get("trial_id") == trial_id), None)
        if not isinstance(item, dict):
            return
        item.update({
            "latency_ms": elapsed,
            "response_chars": len(completion),
            "prompt_bytes": prompt_bytes,
            "outcome": "response" if completion else "empty_response",
        })
        bucket["responses"] += 1
        bucket["latency_ms_total"] += elapsed
        bucket["prompt_bytes"] += prompt_bytes
    try:
        await _run_trial_update(plugin, update)
        setattr(event, "private_companion_hdsi_response_recorded", True)
    except Exception:
        return


async def record_trial_failure(plugin: Any, event: Any, reason: str) -> None:
    mode = str(getattr(event, "private_companion_hdsi_mode", "legacy") or "legacy")
    if mode not in _TRIAL_MODES:
        return
    if getattr(event, "private_companion_hdsi_failure_recorded", False):
        return
    if getattr(event, "private_companion_hdsi_trial_duplicate", False):
        return
    reason_text = _bounded_text(reason or "unknown", 80)
    started = _safe_metric_float(getattr(event, "private_companion_hdsi_trial_started_at", 0.0))
    trial_id = str(getattr(event, "private_companion_hdsi_trial_id", "") or "")
    # A failure belongs to a started trial only.  Error hooks can run for
    # unrelated requests, and counting those would inflate trial metrics.
    if started <= 0 or not trial_id:
        return
    elapsed = max(0, int((time.time() - started) * 1000)) if started > 0 else 0

    def update() -> None:
        state = _trial_root_and_bucket(plugin, mode)
        if state is None:
            return
        root, bucket = state
        events = root.get("events") if isinstance(root.get("events"), list) else []
        item = next((entry for entry in reversed(events) if isinstance(entry, dict) and entry.get("trial_id") == trial_id), None)
        if isinstance(item, dict):
            item.update({"latency_ms": elapsed, "outcome": "failure", "failure_reason": reason_text})
        bucket["failures"] += 1

    await _run_trial_update(plugin, update)
    setattr(event, "private_companion_hdsi_failure_recorded", True)
    setattr(event, "private_companion_hdsi_failure_reason", reason_text)


def _event_group_id(plugin: Any, event: Any) -> str:
    resolver = getattr(plugin, "_extract_group_id_from_event", None)
    if callable(resolver):
        try:
            return str(resolver(event) or "").strip()
        except Exception:
            pass
    return str(getattr(event, "group_id", "") or "").strip()


def _identity_candidates(plugin: Any, event: Any, *, private: bool) -> tuple[str, ...]:
    values: list[str] = []
    raw = _event_sender_id(event) if private else _event_group_id(plugin, event)
    if raw:
        values.append(raw)
    origin = _event_origin(event)
    if origin:
        values.append(origin)
    if private:
        resolver = getattr(plugin, "_private_user_id_for_event", None)
        if callable(resolver) and raw:
            try:
                resolved = resolver(event, raw)
            except Exception:
                resolved = ""
            if resolved:
                values.append(str(resolved).strip())
        canonicalizer = getattr(plugin, "_canonical_private_user_id", None)
        if callable(canonicalizer) and raw:
            try:
                values.append(str(canonicalizer(raw) or "").strip())
            except Exception:
                pass
    else:
        normalizer = getattr(plugin, "_normalize_group_identity_id", None)
        if callable(normalizer):
            try:
                normalized = str(normalizer(raw) or "").strip()
            except Exception:
                normalized = ""
            if normalized:
                values.append(normalized)
    return tuple(dict.fromkeys(item for item in values if item))


def _scope_fingerprint(plugin: Any, event: Any, *, private: bool, subject: str) -> str:
    origin = _event_origin(event)
    platform_getter = getattr(plugin, "_platform_kind_for_event", None)
    try:
        platform = str(platform_getter(event) if callable(platform_getter) else "generic").strip().lower()
    except Exception:
        platform = "generic"
    adapter = str(getattr(event, "adapter_instance_id", "") or "").strip() or origin.split(":", 1)[0]
    self_getter = getattr(plugin, "_event_self_id", None)
    try:
        bot_id = str(self_getter(event) if callable(self_getter) else "").strip()
    except Exception:
        bot_id = ""
    scope = "private" if private else "group"
    payload = "|".join((scope, platform or "generic", adapter, bot_id, subject, origin if not private else ""))
    return hashlib.sha256(payload.encode("utf-8", errors="replace")).hexdigest()[:16]
