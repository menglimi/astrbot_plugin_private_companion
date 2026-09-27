# -*- coding: utf-8 -*-
"""hdsi_experiment 拆分件 part03（机械搬移，行为不变）。

函数 / 常量 / 类逐字搬自 hdsi_experiment.py，仅调整模块级依赖的导入来源。
"""
import asyncio
import json
from typing import Any

try:  # package import
    from .hdsi_experiment_part01 import (
        EXPERIMENT_MODES,
        HDSI_DECISION_SCHEMA_VERSION,
        WINDOW_MODES_KEY,
        _bounded_text,
        _event_is_private,
        _event_sender_id,
        _hdsi_runtime_generation,
        _safe_metric_int,
        _window_ref,
        build_hdsi_decision_snapshot,
        build_hdsi_response_plan,
        format_hdsi_trial_stats,
        get_hdsi_runtime_snapshot,
        hdsi_plan_fence_valid,
        normalize_continuity_scope,
        normalize_hdsi_mode,
        normalize_id_set,
        normalize_window_modes,
    )
except ImportError:  # direct test/import from the plugin directory
    from hdsi_experiment_part01 import (
        EXPERIMENT_MODES,
        HDSI_DECISION_SCHEMA_VERSION,
        WINDOW_MODES_KEY,
        _bounded_text,
        _event_is_private,
        _event_sender_id,
        _hdsi_runtime_generation,
        _safe_metric_int,
        _window_ref,
        build_hdsi_decision_snapshot,
        build_hdsi_response_plan,
        format_hdsi_trial_stats,
        get_hdsi_runtime_snapshot,
        hdsi_plan_fence_valid,
        normalize_continuity_scope,
        normalize_hdsi_mode,
        normalize_id_set,
        normalize_window_modes,
    )
try:  # package import
    from .hdsi_experiment_part02 import (
        _identity_candidates,
        _record_hdsi_event,
        _scope_fingerprint,
        record_trial_input,
    )
except ImportError:  # direct test/import from the plugin directory
    from hdsi_experiment_part02 import (
        _identity_candidates,
        _record_hdsi_event,
        _scope_fingerprint,
        record_trial_input,
    )


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
