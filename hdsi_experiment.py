"""Opt-in HDSI compatibility routing for the legacy companion pipeline."""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

EXPERIMENT_MODES = frozenset({"legacy", "hdsi_shadow", "hdsi_active"})
WINDOW_MODES_KEY = "hdsi_experiment_window_modes"
TRIAL_STATE_KEY = "hdsi_trial_observations"
EVENT_LEDGER_KEY = "hdsi_event_ledger"
ACTOR_STATE_KEY = "hdsi_actor_state"
TRIAL_MAX_EVENTS = 200
EVENT_LEDGER_MAX_EVENTS = 320
ACTOR_MAX_WINDOWS = 24
ACTOR_MAX_COUNT = 128
ACTOR_TTL_SECONDS = 30 * 24 * 60 * 60
HDSI_LIFE_TICK_INTERVAL_SECONDS = 15 * 60
_TRIAL_MODES = frozenset({"hdsi_shadow", "hdsi_active"})
HDSI_DECISION_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class DecisionSnapshot:
    """Bounded, content-free state read used to derive one HDSI turn plan."""

    schema_version: int
    actor_id: str
    persona_id: str
    continuity_scope: str
    scope: str
    window: str
    event_count: int
    current_activity: str
    life_phase: str
    previous_scope: str
    previous_window: str
    runtime_generation: str
    binding_revision: str
    captured_at: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "actor_id": self.actor_id,
            "persona_id": self.persona_id,
            "continuity_scope": self.continuity_scope,
            "scope": self.scope,
            "window": self.window,
            "event_count": self.event_count,
            "current_activity": self.current_activity,
            "life_phase": self.life_phase,
            "previous_scope": self.previous_scope,
            "previous_window": self.previous_window,
            "runtime_generation": self.runtime_generation,
            "binding_revision": self.binding_revision,
            "captured_at": self.captured_at,
        }


@dataclass(frozen=True)
class ResponsePlan:
    """A small HDSI decision contract; the legacy model remains the executor."""

    schema_version: int
    plan_id: str
    action: str
    audience: str
    attention: str
    continuity: str
    privacy: str
    max_chars: int
    snapshot: dict[str, Any]
    runtime_generation: str
    binding_revision: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "plan_id": self.plan_id,
            "action": self.action,
            "audience": self.audience,
            "attention": self.attention,
            "continuity": self.continuity,
            "privacy": self.privacy,
            "max_chars": self.max_chars,
            "snapshot": dict(self.snapshot),
            "runtime_generation": self.runtime_generation,
            "binding_revision": self.binding_revision,
        }


def _trial_root_and_bucket(plugin: Any, mode: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Return normalized trial storage objects; caller serializes access."""
    data = getattr(plugin, "data", None)
    if not isinstance(data, dict):
        return None
    root = data.setdefault(
        TRIAL_STATE_KEY,
        {"schema_version": 1, "events": [], "metrics": {}},
    )
    if not isinstance(root, dict):
        root = {"schema_version": 1, "events": [], "metrics": {}}
        data[TRIAL_STATE_KEY] = root
    root.setdefault("schema_version", 1)
    events = root.setdefault("events", [])
    if not isinstance(events, list):
        root["events"] = events = []
    metrics = root.setdefault("metrics", {})
    if not isinstance(metrics, dict):
        root["metrics"] = metrics = {}
    bucket = metrics.setdefault(
        mode,
        {"requests": 0, "responses": 0, "failures": 0, "latency_ms_total": 0, "prompt_bytes": 0},
    )
    if not isinstance(bucket, dict):
        metrics[mode] = bucket = {
            "requests": 0, "responses": 0, "failures": 0,
            "latency_ms_total": 0, "prompt_bytes": 0,
        }
    for key in ("requests", "responses", "failures", "latency_ms_total", "prompt_bytes"):
        try:
            bucket[key] = max(0, int(bucket.get(key, 0) or 0))
        except (TypeError, ValueError, OverflowError):
            bucket[key] = 0
    return root, bucket


def _bounded_text(value: Any, limit: int = 160) -> str:
    return str(value or "").strip()[:limit]


def _hdsi_actor_key(event: Any) -> str:
    """Resolve the shared actor key selected by the trial continuity scope."""
    configured = _bounded_text(getattr(event, "private_companion_hdsi_continuity_actor_id", ""), 160)
    if configured:
        return configured
    persona = _bounded_text(
        getattr(event, "private_companion_hdsi_persona_id", "")
        or getattr(event, "persona_id", "")
        or "default",
        120,
    )
    return f"hdsi:global:{persona or 'default'}"


def _event_ledger_root(plugin: Any) -> tuple[dict[str, Any], dict[str, Any]] | None:
    data = getattr(plugin, "data", None)
    if not isinstance(data, dict):
        return None
    ledger = data.setdefault(EVENT_LEDGER_KEY, {"schema_version": 1, "events": []})
    if not isinstance(ledger, dict):
        ledger = {"schema_version": 1, "events": []}
        data[EVENT_LEDGER_KEY] = ledger
    ledger.setdefault("schema_version", 1)
    events = ledger.setdefault("events", [])
    if not isinstance(events, list):
        events = []
        ledger["events"] = events
    actors = data.setdefault(ACTOR_STATE_KEY, {})
    if not isinstance(actors, dict):
        actors = {}
        data[ACTOR_STATE_KEY] = actors
    return ledger, actors


def get_hdsi_actor_projection(plugin: Any, event: Any) -> dict[str, Any]:
    """Return a small, content-free continuity projection for prompt assembly."""
    if str(getattr(event, "private_companion_hdsi_mode", "legacy")) not in _TRIAL_MODES:
        return {}
    data = getattr(plugin, "data", None)
    actors = data.get(ACTOR_STATE_KEY) if isinstance(data, dict) else None
    state = actors.get(_hdsi_actor_key(event)) if isinstance(actors, dict) else None
    if not isinstance(state, dict):
        return {"event_count": 0, "last_scope": "", "last_event_at": 0.0}
    return {
        "event_count": max(0, _safe_metric_int(state.get("event_count"))),
        "last_scope": _bounded_text(state.get("last_scope"), 16),
        "previous_scope": _bounded_text(state.get("previous_scope"), 16),
        "previous_window": _bounded_text(state.get("previous_window"), 220),
        "window_count": len(state.get("windows")) if isinstance(state.get("windows"), list) else 0,
        "last_event_at": _safe_metric_float(state.get("last_event_at")),
        "life_phase": _bounded_text(state.get("life_phase"), 32),
        "current_activity": _bounded_text(state.get("current_activity"), 80),
        "last_life_tick_at": _safe_metric_float(state.get("last_life_tick_at")),
    }


def get_hdsi_runtime_snapshot(plugin: Any, event: Any) -> str:
    """Render the bounded actor projection as prompt-safe continuity text."""
    projection = get_hdsi_actor_projection(plugin, event)
    if not projection:
        return "角色状态：保持当前人格的连续性。"
    count = projection.get("event_count", 0)
    scope = "私聊" if _event_is_private(event) else "群聊"
    activity = _hdsi_activity_projection(plugin) or _bounded_text(
        projection.get("current_activity"), 80
    )
    text = f"角色状态：统一生活账本已记录约{count}个事件，当前注意力在{scope}窗口。"
    if activity:
        text += f"角色当前活动：{activity}。"
    life_phase = _bounded_text(projection.get("life_phase"), 32)
    if life_phase:
        text += f"生活阶段：{life_phase}。"
    previous = projection.get("previous_scope", "")
    previous_window = projection.get("previous_window", "")
    current_window = _window_ref(event)
    if (previous and previous != ("private" if _event_is_private(event) else "group")) or (
        previous_window and current_window and previous_window != current_window
    ):
        text += "角色刚从另一个聊天窗口切换过来，保持同一注意力和情绪惯性。"
    return text


def _hdsi_runtime_generation(plugin: Any) -> str:
    """Resolve the host generation used to fence in-flight HDSI plans."""
    for key in ("hdsi_runtime_generation", "_private_companion_runtime_generation", "runtime_generation"):
        value = _bounded_text(getattr(plugin, key, ""), 80)
        if value:
            return value
    return "legacy-instance"


def build_hdsi_decision_snapshot(plugin: Any, event: Any) -> dict[str, Any] | None:
    """Capture only the actor facts needed by the active compatibility planner."""
    mode = str(getattr(event, "private_companion_hdsi_mode", "legacy") or "legacy")
    if mode != "hdsi_active" or not getattr(event, "private_companion_hdsi_chat_route_ready", False):
        return None
    projection = get_hdsi_actor_projection(plugin, event)
    snapshot = DecisionSnapshot(
        schema_version=HDSI_DECISION_SCHEMA_VERSION,
        actor_id=_bounded_text(getattr(event, "private_companion_hdsi_continuity_actor_id", "") or _hdsi_actor_key(event), 180),
        persona_id=_bounded_text(getattr(event, "private_companion_hdsi_persona_id", "") or "default", 120),
        continuity_scope=_bounded_text(getattr(event, "private_companion_hdsi_continuity_scope", "") or "global", 16),
        scope="private" if _event_is_private(event) else "group",
        window=_window_ref(event),
        event_count=max(0, _safe_metric_int(projection.get("event_count", 0))),
        current_activity=_bounded_text(_hdsi_activity_projection(plugin) or projection.get("current_activity", ""), 80),
        life_phase=_bounded_text(projection.get("life_phase", ""), 32),
        previous_scope=_bounded_text(projection.get("previous_scope", ""), 16),
        previous_window=_bounded_text(projection.get("previous_window", ""), 220),
        runtime_generation=_hdsi_runtime_generation(plugin),
        binding_revision=_bounded_text(getattr(event, "private_companion_hdsi_binding_revision", "1") or "1", 64),
        captured_at=time.time(),
    )
    return snapshot.as_dict()


def build_hdsi_response_plan(plugin: Any, event: Any, snapshot: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Derive a deterministic, bounded response plan without an extra model call."""
    if str(getattr(event, "private_companion_hdsi_mode", "legacy") or "legacy") != "hdsi_active":
        return None
    snapshot = snapshot or build_hdsi_decision_snapshot(plugin, event)
    if not isinstance(snapshot, dict):
        return None
    audience = "private" if snapshot.get("scope") == "private" else "group"
    continuity = "continue_current_life"
    if snapshot.get("previous_window") and snapshot.get("previous_window") != snapshot.get("window"):
        continuity = "resume_attention_after_window_switch"
    privacy = "private_relationship_allowed" if audience == "private" else "public_context_only"
    attention = _bounded_text(snapshot.get("current_activity"), 80) or "当前对话"
    max_chars = 900 if audience == "private" else 360
    seed = "|".join((str(snapshot.get("actor_id", "")), str(snapshot.get("window", "")), str(snapshot.get("event_count", 0)), str(snapshot.get("runtime_generation", "")), str(snapshot.get("binding_revision", ""))))
    plan = ResponsePlan(
        schema_version=HDSI_DECISION_SCHEMA_VERSION,
        plan_id=hashlib.sha256(seed.encode("utf-8", errors="replace")).hexdigest()[:24],
        action="respond",
        audience=audience,
        attention=attention,
        continuity=continuity,
        privacy=privacy,
        max_chars=max_chars,
        snapshot=snapshot,
        runtime_generation=_bounded_text(snapshot.get("runtime_generation"), 80),
        binding_revision=_bounded_text(snapshot.get("binding_revision"), 64),
    )
    return plan.as_dict()


def hdsi_plan_fence_valid(plugin: Any, event: Any, plan: dict[str, Any] | None) -> bool:
    """Reject stale plans after reload, persona rebinding, or configuration changes."""
    if not isinstance(plan, dict):
        return False
    expected_generation = _bounded_text(plan.get("runtime_generation"), 80)
    current_generation = _hdsi_runtime_generation(plugin)
    event_generation = _bounded_text(getattr(event, "private_companion_hdsi_runtime_generation", ""), 80)
    expected_revision = _bounded_text(plan.get("binding_revision"), 64)
    current_revision = _bounded_text(getattr(plugin, "hdsi_experiment_binding_revision", "1") or "1", 64)
    event_revision = _bounded_text(getattr(event, "private_companion_hdsi_binding_revision", ""), 64)
    return bool(
        expected_generation
        and expected_generation == current_generation
        and (not event_generation or event_generation == current_generation)
        and expected_revision == current_revision
        and (not event_revision or event_revision == current_revision)
    )


def _hdsi_activity_projection(plugin: Any) -> str:
    """Read a short bot-owned activity label without copying schedule content."""
    data = getattr(plugin, "data", None)
    if not isinstance(data, dict):
        return ""
    for section_name in ("daily_state", "daily_plan", "proactive_runtime"):
        section = data.get(section_name)
        if not isinstance(section, dict):
            continue
        for key in ("current_activity", "activity", "current_focus", "focus"):
            value = _bounded_text(section.get(key), 80)
            if value:
                return value
    return ""


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


def _life_phase_for_hour(hour: int) -> str:
    """Use a stable, low-cost phase label for continuity prompts."""
    if hour < 6:
        return "深夜休息"
    if hour < 9:
        return "晨间准备"
    if hour < 12:
        return "上午进行中"
    if hour < 14:
        return "午间缓冲"
    if hour < 18:
        return "下午进行中"
    if hour < 23:
        return "晚间生活"
    return "夜间收束"


def _actor_has_enabled_route(plugin: Any, state: dict[str, Any]) -> bool:
    """Check whether an actor still has at least one HDSI-enabled route."""
    base_mode = normalize_hdsi_mode(getattr(plugin, "hdsi_experiment_mode", "legacy"))
    if base_mode in _TRIAL_MODES:
        return True
    configured = getattr(plugin, WINDOW_MODES_KEY, {})
    if isinstance(configured, str):
        configured = normalize_window_modes(configured)
    if not isinstance(configured, dict):
        return False
    windows = state.get("windows") if isinstance(state.get("windows"), list) else []
    return any(configured.get(window) in _TRIAL_MODES for window in windows)


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


async def record_hdsi_proactive_event(
    plugin: Any, event: Any, event_type: str = "autonomous_tick", **details: Any
) -> None:
    """Record an autonomous-life transition using the same actor ledger.

    Proactive producers can call this before delivery. It does not inject a
    prompt or send anything; it only opts an already-enabled trial route into
    the bounded continuity ledger. Existing producers remain fully compatible.
    """
    if not getattr(event, "private_companion_hdsi_chat_route_ready", False):
        try:
            mark_hdsi_route(plugin, event)
        except Exception:
            return
    await _record_hdsi_event(plugin, event, event_type, **details)


async def _run_trial_update(plugin: Any, update: Any) -> None:
    """Run a best-effort observer update under the plugin data lock."""
    try:
        lock = getattr(plugin, "_data_lock", None)
        if lock is not None:
            async with lock:
                update()
        else:
            update()
        scheduler = getattr(plugin, "_schedule_data_save", None)
        if callable(scheduler):
            scheduler(
                sections={TRIAL_STATE_KEY, EVENT_LEDGER_KEY, ACTOR_STATE_KEY},
                delay=0.2,
            )
    except asyncio.CancelledError:
        raise
    except Exception:
        # Trial telemetry must never affect the legacy reply path.
        return


def normalize_window_modes(value: Any) -> dict[str, str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return {}
    if not isinstance(value, dict):
        return {}
    return {
        key: mode for key, mode in value.items()
        if isinstance(key, str) and key and isinstance(mode, str) and mode in EXPERIMENT_MODES
    }


def _window_ref(event: Any) -> str:
    origin = _event_origin(event)
    parts = origin.split(":", 2)
    kind = "friendmessage" if _event_is_private(event) else "groupmessage"
    if len(parts) != 3 or not parts[0] or parts[1].lower() != kind or not parts[2]:
        return ""
    return origin


async def hdsi_window_command(plugin: Any, event: Any, action: str = "状态") -> str:
    """Persist a current-window override without changing any other binding."""
    from .helpers import _flat_get, _set_into_config

    action = str(action or "状态").strip().lower()
    window = _window_ref(event)
    if action in {"统计", "试验统计", "metrics", "metric", "report"}:
        if not window:
            return "无法识别当前聊天窗口，统计未读取。"
        checker = getattr(
            plugin,
            "_can_manage_private_companion"
            if _event_is_private(event)
            else "_can_manage_group_companion",
            None,
        )
        if not callable(checker) or not checker(event):
            return "需要当前窗口的陪伴管理权限才能查看试验统计。"
        inbound = getattr(plugin, "_event_is_inbound_chat_message", None)
        if not callable(inbound) or not inbound(event):
            return "仅支持在入站聊天中查看试验统计。"
        return format_hdsi_trial_stats(plugin)

    if not window:
        return "无法识别当前聊天窗口，设置未修改。"
    labels = {"legacy": "旧框架", "hdsi_shadow": "影子观察", "hdsi_active": "HDSI 表达试验"}
    if action in {"状态", "status"}:
        return f"当前窗口：{labels[resolve_hdsi_mode(plugin, event)]}。"
    modes = {"开启": "hdsi_active", "on": "hdsi_active", "关闭": "legacy", "off": "legacy", "观察": "hdsi_shadow"}
    if action not in modes:
        return "HDSI 开启 / HDSI 关闭 / HDSI 观察 / HDSI 状态 / HDSI 统计"
    checker = getattr(plugin, "_can_manage_private_companion" if _event_is_private(event) else "_can_manage_group_companion", None)
    if not callable(checker) or not checker(event):
        return "需要当前窗口的陪伴管理权限才能切换。"
    inbound = getattr(plugin, "_event_is_inbound_chat_message", None)
    if not callable(inbound) or not inbound(event):
        return "仅支持在入站聊天中切换，设置未修改。"

    lock = getattr(plugin, "_hdsi_window_config_lock", None)
    if lock is None:
        lock = asyncio.Lock()
        plugin._hdsi_window_config_lock = lock
    async with lock:
        config = getattr(plugin, "config", None)
        if config is None:
            return "配置暂不可用，设置未修改。"
        old_value = _flat_get(config, WINDOW_MODES_KEY, "{}")
        windows = normalize_window_modes(old_value)
        windows[window] = modes[action]
        encoded = json.dumps(windows, ensure_ascii=False, sort_keys=True)
        if not _set_into_config(config, WINDOW_MODES_KEY, encoded):
            return "配置无法写入，设置未修改。"
        try:
            saved = await plugin._save_config_if_possible()
        except asyncio.CancelledError:
            _set_into_config(config, WINDOW_MODES_KEY, old_value)
            raise
        except Exception:
            saved = False
        if not saved:
            _set_into_config(config, WINDOW_MODES_KEY, old_value)
            return "保存失败，当前窗口仍使用原设置。"
        plugin.hdsi_experiment_window_modes = windows
    scope = "当前私聊" if _event_is_private(event) else "当前整个群聊"
    return f"{scope}已切换为{labels[modes[action]]}，下一条消息生效。"


def format_hdsi_trial_stats(plugin: Any) -> str:
    """Return content-free aggregate metrics for administrators.

    The command intentionally exposes only counters and resource totals. Trial
    events contain digests rather than message text, and this formatter does
    not include identities or fingerprints.
    """
    data = getattr(plugin, "data", None)
    root = data.get(TRIAL_STATE_KEY) if isinstance(data, dict) else None
    metrics = root.get("metrics") if isinstance(root, dict) else None
    if not isinstance(metrics, dict):
        return "HDSI 试验统计：暂无数据。"

    labels = {"hdsi_active": "主动表达", "hdsi_shadow": "影子观察"}
    events = root.get("events") if isinstance(root, dict) and isinstance(root.get("events"), list) else []
    lines = ["HDSI 试验统计（仅聚合指标）："]
    any_data = False
    total_requests = total_responses = total_failures = 0
    for mode in ("hdsi_active", "hdsi_shadow"):
        bucket = metrics.get(mode)
        if not isinstance(bucket, dict):
            continue
        requests = max(0, _safe_metric_int(bucket.get("requests")))
        responses = max(0, _safe_metric_int(bucket.get("responses")))
        failures = max(0, _safe_metric_int(bucket.get("failures")))
        latency_total = max(0, _safe_metric_int(bucket.get("latency_ms_total")))
        prompt_bytes = max(0, _safe_metric_int(bucket.get("prompt_bytes")))
        latencies = sorted(
            _safe_metric_int(item.get("latency_ms"))
            for item in events
            if isinstance(item, dict)
            and item.get("mode") == mode
            and _safe_metric_int(item.get("latency_ms")) > 0
        )
        p95_latency = _trial_percentile(latencies, 0.95)
        if requests or responses or failures or latency_total or prompt_bytes:
            any_data = True
        avg_latency = f"{latency_total / responses:.0f}ms" if responses else "-"
        lines.append(
            f"{labels[mode]}：请求 {requests}，响应 {responses}，失败 {failures}，"
            f"平均延迟 {avg_latency}，P95 {p95_latency}ms，提示词 {prompt_bytes}B。"
        )
        total_requests += requests
        total_responses += responses
        total_failures += failures
    if not any_data:
        return "HDSI 试验统计：暂无数据。"
    lines.append(
        f"合计：请求 {total_requests}，响应 {total_responses}，失败 {total_failures}。"
    )
    return "\n".join(lines)


def _safe_metric_int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _safe_metric_float(value: Any) -> float:
    try:
        result = float(value or 0.0)
        return result if result >= 0 else 0.0
    except (TypeError, ValueError, OverflowError):
        return 0.0


def _trial_percentile(values: list[int], percentile: float) -> int:
    if not values:
        return 0
    index = min(len(values) - 1, max(0, int((len(values) - 1) * percentile + 0.9999)))
    return values[index]


def normalize_hdsi_mode(value: Any) -> str:
    aliases = {"off": "legacy", "disabled": "legacy", "shadow": "hdsi_shadow", "active": "hdsi_active", "hdsi": "hdsi_active"}
    mode = aliases.get(str(value or "legacy").strip().lower(), str(value or "legacy").strip().lower())
    return mode if mode in EXPERIMENT_MODES else "legacy"


def normalize_continuity_scope(value: Any) -> str:
    value = str(value or "global").strip().lower()
    value = {"window": "session", "per_user": "user", "all": "global"}.get(value, value)
    return value if value in {"session", "user", "global"} else "global"


def normalize_id_set(value: Any) -> frozenset[str]:
    if isinstance(value, str):
        values = value.replace("\r", "\n").replace(",", "\n").replace("，", "\n").split("\n")
    elif isinstance(value, (list, tuple, set, frozenset)):
        values = value
    else:
        values = ()
    return frozenset(str(item).strip() for item in values if str(item).strip())


def _event_is_private(event: Any) -> bool:
    checker = getattr(event, "is_private_chat", None)
    try:
        return bool(checker()) if callable(checker) else False
    except Exception:
        return False


def _event_sender_id(event: Any) -> str:
    try:
        return str(event.get_sender_id() or "").strip()
    except Exception:
        return str(getattr(event, "sender_id", "") or "").strip()


def _event_origin(event: Any) -> str:
    return str(getattr(event, "unified_msg_origin", "") or "").strip()


def _trial_event_digest(plugin: Any, event: Any) -> str:
    message_id = _event_message_id(plugin, event)
    text = str(getattr(event, "message_str", "") or "")
    raw = "|".join((message_id, _event_origin(event), text))
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:20]


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


def resolve_hdsi_binding(plugin: Any, event: Any) -> dict[str, str]:
    mode = normalize_hdsi_mode(getattr(plugin, "hdsi_experiment_mode", "legacy"))
    private = _event_is_private(event)
    candidates = _identity_candidates(plugin, event, private=private)
    configured = normalize_id_set(getattr(plugin, "hdsi_experiment_user_ids" if private else "hdsi_experiment_group_ids", ()))
    matched = next((candidate for candidate in candidates if candidate in configured), "") if mode != "legacy" else ""
    if not matched:
        mode = "legacy"
    windows = getattr(plugin, WINDOW_MODES_KEY, {})
    if isinstance(windows, str):
        windows = normalize_window_modes(windows)
    window = _window_ref(event)
    override = windows.get(window) if isinstance(windows, dict) else None
    if isinstance(override, str) and override in EXPERIMENT_MODES:
        mode = override
    scope = "private" if private else "group"
    subject = matched or (candidates[0] if candidates else "")
    if private and candidates:
        resolver = getattr(plugin, "_private_user_id_for_event", None)
        if callable(resolver):
            try:
                stable = str(resolver(event, _event_sender_id(event)) or "").strip()
            except Exception:
                stable = ""
            if stable:
                subject = stable
        if subject == _event_sender_id(event):
            canonicalizer = getattr(plugin, "_canonical_private_user_id", None)
            if callable(canonicalizer):
                try:
                    subject = str(canonicalizer(subject) or subject).strip()
                except Exception:
                    pass
    revision = str(getattr(plugin, "hdsi_experiment_binding_revision", "1") or "1").strip()
    persona_id = str(getattr(event, "persona_id", "") or getattr(event, "private_companion_persona_id", "") or getattr(plugin, "default_persona_id", "") or "default").strip()
    fingerprint = _scope_fingerprint(plugin, event, private=private, subject=subject)
    continuity = normalize_continuity_scope(getattr(plugin, "hdsi_experiment_continuity_scope", "global"))
    platform_getter = getattr(plugin, "_platform_kind_for_event", None)
    try:
        continuity_platform = _bounded_text(
            platform_getter(event) if callable(platform_getter) else "generic", 24
        ).lower() or "generic"
    except Exception:
        continuity_platform = "generic"
    platform_prefix = "" if continuity_platform == "generic" else f"{continuity_platform}:"
    if continuity == "session":
        continuity_actor = f"hdsi:session:{platform_prefix}{fingerprint}:{persona_id}"
    elif continuity == "user":
        # Group routing is bound to the group window, so its normal subject is
        # the group ID. User continuity deliberately switches to the sender
        # identity to share state with that person's private window.
        user_subject = _event_sender_id(event) if not private else subject
        resolver = getattr(plugin, "_private_user_id_for_event", None)
        if callable(resolver) and user_subject:
            try:
                user_subject = str(resolver(event, user_subject) or user_subject).strip()
            except Exception:
                pass
        user_subject = user_subject or subject or fingerprint
        normalized_prefix = "" if user_subject.lower().startswith(f"{continuity_platform}:") else platform_prefix
        continuity_actor = f"hdsi:user:{normalized_prefix}{user_subject}:{persona_id}"
    else:
        # Global HDSI is one actor with several windows. Persona IDs still
        # partition actors when multi-persona mode is enabled.
        self_getter = getattr(plugin, "_event_self_id", None)
        try:
            bot_id = _bounded_text(self_getter(event) if callable(self_getter) else "", 80)
        except Exception:
            bot_id = ""
        bot_suffix = f":bot:{bot_id}" if bot_id else ""
        continuity_actor = f"hdsi:global:{platform_prefix}{persona_id}{bot_suffix}"
    return {
        "mode": mode, "scope": scope, "subject_id": subject,
        "scope_fingerprint": fingerprint, "binding_revision": revision,
        "actor_id": f"hdsi:{scope}:{fingerprint}:{persona_id}",
        "continuity_actor_id": continuity_actor,
        "continuity_scope": continuity, "persona_id": persona_id,
    }


def resolve_hdsi_mode(plugin: Any, event: Any) -> str:
    return resolve_hdsi_binding(plugin, event)["mode"]


def mark_hdsi_route(plugin: Any, event: Any) -> str:
    binding = resolve_hdsi_binding(plugin, event)
    try:
        setattr(event, "private_companion_hdsi_route_version", "hdsi-compat-v1")
        for key, value in binding.items():
            setattr(event, f"private_companion_hdsi_{key}", value)
        setattr(event, "private_companion_hdsi_runtime_generation", _hdsi_runtime_generation(plugin))
        setattr(event, "private_companion_hdsi_chat_route_ready", True)
    except Exception:
        pass
    return binding["mode"]


def build_hdsi_prompt_section(event: Any, mode: str, plan: dict[str, Any] | None = None):
    if normalize_hdsi_mode(mode) != "hdsi_active":
        return None
    from .conversation_prompt_section import prompt_section
    audience = "私聊" if _event_is_private(event) else "群聊"
    plugin = getattr(event, "_private_companion_plugin", None)
    continuity = ""
    if plugin is not None:
        # The runtime projection is generated from the bounded event ledger;
        # it never includes message text or private-window history.
        continuity = get_hdsi_runtime_snapshot(plugin, event)
    plan_hint = ""
    if isinstance(plan, dict):
        plan_hint = (
            f"本轮决策计划：注意力在{_bounded_text(plan.get('attention'), 80)}，"
            f"连续性策略为{_bounded_text(plan.get('continuity'), 48)}，"
            f"表达上限约{max(120, _safe_metric_int(plan.get('max_chars')))}字；"
            "计划只提供方向，不能覆盖事实、权限或当前对话。"
        )
    content = ("当前处于 HDSI 兼容试验的主动表达模式。把本轮消息视为持续性生活剧本中的一个新事件，先承接角色此刻正在进行的活动、注意力和情绪，再自然回应当前对象。保持同一人格和跨窗口连续性，不要因为切换聊天窗口重置状态，不要把尚未发生的计划写成事实，不要凭空添加共同经历。" f"当前受众是{audience}：只使用本受众获准看到的关系和记忆；群聊中收敛私密细节、长度和主动追问，不要把其他窗口的私聊正文带入回复。若上下文不足，采用简短、自然、可继续的回应或等待，不要用解释框架实现感代替事实。{continuity}{plan_hint}")
    return prompt_section(key="experiment.hdsi.compatibility", title="HDSI 试验表达约束", source="hdsi_experiment", content=content)


async def apply_hdsi_prompt(plugin: Any, event: Any, req: Any) -> None:
    """Apply the optional overlay after chat routing, using the existing plan."""
    if not getattr(plugin, "enabled", False):
        return
    if not getattr(event, "private_companion_hdsi_chat_route_ready", False):
        return
    if getattr(event, "private_companion_proactive_framework", False):
        return
    if getattr(req, "_private_companion_hdsi_processed", False):
        return
    mode = getattr(event, "private_companion_hdsi_mode", "legacy")
    if mode not in {"hdsi_active", "hdsi_shadow"} or mode != resolve_hdsi_mode(plugin, event):
        return
    checker = getattr(plugin, "_event_is_inbound_chat_message", None)
    if not callable(checker) or not checker(event):
        return
    blocker = getattr(plugin, "_proactive_only_blocks_passive_event", None)
    if callable(blocker) and blocker(event):
        return
    # A reload or binding update invalidates an in-flight compatibility plan;
    # the legacy request continues without the HDSI overlay.
    route_generation = _bounded_text(getattr(event, "private_companion_hdsi_runtime_generation", ""), 80)
    if route_generation and route_generation != _hdsi_runtime_generation(plugin):
        return
    setattr(event, "_private_companion_plugin", plugin)
    snapshot = build_hdsi_decision_snapshot(plugin, event) if mode == "hdsi_active" else None
    plan = build_hdsi_response_plan(plugin, event, snapshot) if mode == "hdsi_active" else None
    if mode == "hdsi_active" and not hdsi_plan_fence_valid(plugin, event, plan):
        return
    await record_trial_input(plugin, event)
    setattr(event, "private_companion_hdsi_decision_snapshot", snapshot)
    setattr(event, "private_companion_hdsi_response_plan", plan)
    if mode == "hdsi_active":
        setattr(req, "_private_companion_hdsi_response_plan", plan)
    section = build_hdsi_prompt_section(event, "hdsi_active", plan=plan)
    prompt_bytes = 0
    if mode == "hdsi_active" and section is not None:
        try:
            prompt_bytes = len(str(getattr(section, "content", "") or "").encode("utf-8"))
        except Exception:
            prompt_bytes = 0
    setattr(event, "private_companion_hdsi_prompt_bytes", prompt_bytes)
    metadata = {
        key: getattr(event, f"private_companion_hdsi_{key}", "")
        for key in ("scope", "scope_fingerprint", "binding_revision", "actor_id", "persona_id")
    }
    metadata.update(route=mode, shadow_only=mode == "hdsi_shadow")
    if isinstance(plan, dict):
        metadata.update({
            "plan_id": plan.get("plan_id", ""),
            "decision_schema_version": plan.get("schema_version", HDSI_DECISION_SCHEMA_VERSION),
            "continuity_strategy": plan.get("continuity", ""),
            "audience": plan.get("audience", ""),
        })
    if mode == "hdsi_active" and section is not None:
        metadata["placement"] = plugin._place_conversation_prompt_section(
            req, "<!-- private_companion_hdsi_experiment_v1 -->", section, priority=109,
        )
    setattr(req, "_private_companion_hdsi_processed", True)
    recorder = getattr(plugin, "_record_request_prompt_fragment", None)
    if callable(recorder) and section is not None:
        await recorder(
            event, title="HDSI 表达试验", key="experiment.hdsi.compatibility",
            text=section.content, source="hdsi_experiment", mode=mode, priority=109,
            metadata=metadata,
        )


__all__ = [
    "EXPERIMENT_MODES", "DecisionSnapshot", "ResponsePlan", "HDSI_DECISION_SCHEMA_VERSION",
    "apply_hdsi_prompt", "build_hdsi_prompt_section", "build_hdsi_decision_snapshot",
    "build_hdsi_response_plan", "hdsi_plan_fence_valid",
    "finalize_trial_response", "format_hdsi_trial_stats", "record_hdsi_proactive_event",
    "record_trial_failure", "record_trial_input",
    "hdsi_window_command", "mark_hdsi_route", "normalize_hdsi_mode",
    "normalize_continuity_scope", "normalize_id_set", "normalize_window_modes",
    "record_hdsi_inbound_event", "record_hdsi_outbound_event",
    "resolve_hdsi_binding", "resolve_hdsi_mode", "get_hdsi_actor_projection",
    "get_hdsi_runtime_snapshot", "run_hdsi_life_tick",
]
