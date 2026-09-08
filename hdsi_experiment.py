"""Opt-in HDSI compatibility routing for the legacy companion pipeline."""
from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any

EXPERIMENT_MODES = frozenset({"legacy", "hdsi_shadow", "hdsi_active"})
WINDOW_MODES_KEY = "hdsi_experiment_window_modes"


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

    window = _window_ref(event)
    if not window:
        return "无法识别当前聊天窗口，设置未修改。"
    action = str(action or "状态").strip().lower()
    labels = {"legacy": "旧框架", "hdsi_shadow": "影子观察", "hdsi_active": "HDSI 表达试验"}
    if action in {"状态", "status"}:
        return f"当前窗口：{labels[resolve_hdsi_mode(plugin, event)]}。"
    modes = {"开启": "hdsi_active", "on": "hdsi_active", "关闭": "legacy", "off": "legacy", "观察": "hdsi_shadow"}
    if action not in modes:
        return "HDSI 开启 / HDSI 关闭 / HDSI 状态"
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


def normalize_hdsi_mode(value: Any) -> str:
    aliases = {"off": "legacy", "disabled": "legacy", "shadow": "hdsi_shadow", "active": "hdsi_active", "hdsi": "hdsi_active"}
    mode = aliases.get(str(value or "legacy").strip().lower(), str(value or "legacy").strip().lower())
    return mode if mode in EXPERIMENT_MODES else "legacy"


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
    return {"mode": mode, "scope": scope, "subject_id": subject, "scope_fingerprint": fingerprint, "binding_revision": revision, "actor_id": f"hdsi:{scope}:{fingerprint}:{persona_id}", "persona_id": persona_id}


def resolve_hdsi_mode(plugin: Any, event: Any) -> str:
    return resolve_hdsi_binding(plugin, event)["mode"]


def mark_hdsi_route(plugin: Any, event: Any) -> str:
    binding = resolve_hdsi_binding(plugin, event)
    try:
        setattr(event, "private_companion_hdsi_route_version", "hdsi-compat-v1")
        for key, value in binding.items():
            setattr(event, f"private_companion_hdsi_{key}", value)
        setattr(event, "private_companion_hdsi_chat_route_ready", True)
    except Exception:
        pass
    return binding["mode"]


def build_hdsi_prompt_section(event: Any, mode: str):
    if normalize_hdsi_mode(mode) != "hdsi_active":
        return None
    from .conversation_prompt_section import prompt_section
    audience = "私聊" if _event_is_private(event) else "群聊"
    content = ("当前处于 HDSI 兼容试验的主动表达模式。把本轮消息视为持续性生活剧本中的一个新事件，先承接角色此刻正在进行的活动、注意力和情绪，再自然回应当前对象。保持同一人格和跨窗口连续性，不要因为切换聊天窗口重置状态，不要把尚未发生的计划写成事实，不要凭空添加共同经历。" f"当前受众是{audience}：只使用本受众获准看到的关系和记忆；群聊中收敛私密细节、长度和主动追问，不要把其他窗口的私聊正文带入回复。若上下文不足，采用简短、自然、可继续的回应或等待，不要用解释框架实现感代替事实。")
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
    section = build_hdsi_prompt_section(event, "hdsi_active")
    metadata = {
        key: getattr(event, f"private_companion_hdsi_{key}", "")
        for key in ("scope", "scope_fingerprint", "binding_revision", "actor_id", "persona_id")
    }
    metadata.update(route=mode, shadow_only=mode == "hdsi_shadow")
    if mode == "hdsi_active":
        metadata["placement"] = plugin._place_conversation_prompt_section(
            req, "<!-- private_companion_hdsi_experiment_v1 -->", section, priority=109,
        )
    setattr(req, "_private_companion_hdsi_processed", True)
    recorder = getattr(plugin, "_record_request_prompt_fragment", None)
    if callable(recorder):
        await recorder(
            event, title="HDSI 表达试验", key="experiment.hdsi.compatibility",
            text=section.content, source="hdsi_experiment", mode=mode, priority=109,
            metadata=metadata,
        )


__all__ = [
    "EXPERIMENT_MODES", "apply_hdsi_prompt", "build_hdsi_prompt_section",
    "hdsi_window_command", "mark_hdsi_route", "normalize_hdsi_mode",
    "normalize_id_set", "normalize_window_modes", "resolve_hdsi_binding", "resolve_hdsi_mode",
]
