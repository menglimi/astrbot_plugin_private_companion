"""Opt-in HDSI compatibility routing for the legacy companion pipeline.

The default route is deliberately inert. This module only resolves an
explicit experiment binding and provides a bounded prompt overlay; it does
not own legacy data, delivery, or permissions.
"""
from __future__ import annotations

import hashlib
from typing import Any

EXPERIMENT_MODES = frozenset({"legacy", "hdsi_shadow", "hdsi_active"})


def normalize_hdsi_mode(value: Any) -> str:
    mode = str(value or "legacy").strip().lower()
    aliases = {
        "off": "legacy",
        "disabled": "legacy",
        "shadow": "hdsi_shadow",
        "active": "hdsi_active",
        "hdsi": "hdsi_active",
    }
    mode = aliases.get(mode, mode)
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


def _identity_candidates(plugin: Any, event: Any, *, private: bool) -> tuple[str, ...]:
    """Return bounded raw/canonical candidates without changing permissions."""
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


def _event_group_id(plugin: Any, event: Any) -> str:
    resolver = getattr(plugin, "_extract_group_id_from_event", None)
    if callable(resolver):
        try:
            return str(resolver(event) or "").strip()
        except Exception:
            pass
    return str(getattr(event, "group_id", "") or "").strip()


def _scope_fingerprint(plugin: Any, event: Any, *, private: bool, subject: str) -> str:
    """Build a diagnostic/storage partition hint; never grants a route."""
    origin = _event_origin(event)
    platform_getter = getattr(plugin, "_platform_kind_for_event", None)
    try:
        platform = str(platform_getter(event) if callable(platform_getter) else "generic").strip().lower()
    except Exception:
        platform = "generic"
    adapter = str(getattr(event, "adapter_instance_id", "") or "").strip()
    if not adapter and origin:
        adapter = origin.split(":", 1)[0]
    self_getter = getattr(plugin, "_event_self_id", None)
    try:
        bot_id = str(self_getter(event) if callable(self_getter) else "").strip()
    except Exception:
        bot_id = ""
    scope = "private" if private else "group"
    payload = "|".join((scope, platform or "generic", adapter, bot_id, subject, origin if not private else ""))
    return hashlib.sha256(payload.encode("utf-8", errors="replace")).hexdigest()[:16]


def resolve_hdsi_binding(plugin: Any, event: Any) -> dict[str, str]:
    """Resolve an explicit binding and return stable, inspectable metadata."""
    mode = normalize_hdsi_mode(getattr(plugin, "hdsi_experiment_mode", "legacy"))
    private = _event_is_private(event)
    candidates = _identity_candidates(plugin, event, private=private)
    configured = normalize_id_set(
        getattr(plugin, "hdsi_experiment_user_ids" if private else "hdsi_experiment_group_ids", ())
    )
    matched = next((candidate for candidate in candidates if candidate in configured), "") if mode != "legacy" else ""
    if not matched:
        mode = "legacy"
    scope = "private" if private else "group"
    # Keep the actor stable when a platform alias changes. Prefer the storage
    # resolver/canonical alias for private users; route matching still uses the
    # complete candidate set above.
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
    persona_id = str(
        getattr(event, "persona_id", "")
        or getattr(event, "private_companion_persona_id", "")
        or getattr(plugin, "default_persona_id", "")
        or "default"
    ).strip()
    fingerprint = _scope_fingerprint(plugin, event, private=private, subject=subject)
    return {
        "mode": mode,
        "scope": scope,
        "subject_id": subject,
        "scope_fingerprint": fingerprint,
        "binding_revision": revision,
        "actor_id": f"hdsi:{scope}:{fingerprint}:{persona_id}",
        "persona_id": persona_id,
    }


def resolve_hdsi_mode(plugin: Any, event: Any) -> str:
    """Resolve the narrowest explicit binding without widening scope."""
    return resolve_hdsi_binding(plugin, event)["mode"]


def mark_hdsi_route(plugin: Any, event: Any) -> str:
    binding = resolve_hdsi_binding(plugin, event)
    mode = binding["mode"]
    try:
        setattr(event, "private_companion_hdsi_mode", mode)
        setattr(event, "private_companion_hdsi_route_version", "hdsi-compat-v1")
        for key, value in binding.items():
            setattr(event, f"private_companion_hdsi_{key}", value)
        # Only the private/group message entry points set this marker. The LLM
        # hook must not infer a trial route for timers, tools, or proactive jobs.
        setattr(event, "private_companion_hdsi_chat_route_ready", True)
    except Exception:
        pass
    return mode


def build_hdsi_prompt_section(event: Any, mode: str):
    """Return a small prompt overlay for explicitly active experiment events."""
    if normalize_hdsi_mode(mode) != "hdsi_active":
        return None
    from .conversation_prompt_section import prompt_section

    is_private = _event_is_private(event)
    audience = "私聊" if is_private else "群聊"
    content = (
        "当前处于 HDSI 兼容试验的主动表达模式。把本轮消息视为持续性生活剧本中的一个新事件，"
        "先承接角色此刻正在进行的活动、注意力和情绪，再自然回应当前对象。保持同一人格和跨窗口连续性，"
        "不要因为切换聊天窗口重置状态，不要把尚未发生的计划写成事实，不要凭空添加共同经历。"
        f"当前受众是{audience}：只使用本受众获准看到的关系和记忆；群聊中收敛私密细节、长度和主动追问，"
        "不要把其他窗口的私聊正文带入回复。若上下文不足，采用简短、自然、可继续的回应或等待，不要用解释框架实现感代替事实。"
    )
    return prompt_section(
        key="experiment.hdsi.compatibility",
        title="HDSI 试验表达约束",
        source="hdsi_experiment",
        content=content,
    )


__all__ = [
    "EXPERIMENT_MODES",
    "build_hdsi_prompt_section",
    "mark_hdsi_route",
    "resolve_hdsi_binding",
    "normalize_hdsi_mode",
    "normalize_id_set",
    "resolve_hdsi_mode",
]
