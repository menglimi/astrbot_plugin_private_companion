# -*- coding: utf-8 -*-
"""hdsi_experiment 拆分件 part01（机械搬移，行为不变）。

函数 / 常量 / 类逐字搬自 hdsi_experiment.py，仅调整模块级依赖的导入来源。
"""
import asyncio
import hashlib
import json
import time
from dataclasses import dataclass
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


def _hdsi_runtime_generation(plugin: Any) -> str:
    """Resolve the host generation used to fence in-flight HDSI plans."""
    for key in ("hdsi_runtime_generation", "_private_companion_runtime_generation", "runtime_generation"):
        value = _bounded_text(getattr(plugin, key, ""), 80)
        if value:
            return value
    return "legacy-instance"


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


def _trial_percentile(values: list[int], percentile: float) -> int:
    if not values:
        return 0
    index = min(len(values) - 1, max(0, int((len(values) - 1) * percentile + 0.9999)))
    return values[index]


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


def normalize_hdsi_mode(value: Any) -> str:
    aliases = {"off": "legacy", "disabled": "legacy", "shadow": "hdsi_shadow", "active": "hdsi_active", "hdsi": "hdsi_active"}
    mode = aliases.get(str(value or "legacy").strip().lower(), str(value or "legacy").strip().lower())
    return mode if mode in EXPERIMENT_MODES else "legacy"


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


def _window_ref(event: Any) -> str:
    origin = _event_origin(event)
    parts = origin.split(":", 2)
    kind = "friendmessage" if _event_is_private(event) else "groupmessage"
    if len(parts) != 3 or not parts[0] or parts[1].lower() != kind or not parts[2]:
        return ""
    return origin


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
