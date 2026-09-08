"""Opt-in HDSI compatibility routing for the legacy companion pipeline.

The default route is deliberately inert. This module only resolves an
explicit experiment binding and provides a bounded prompt overlay; it does
not own legacy data, delivery, or permissions.
"""
from __future__ import annotations

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


def _event_group_id(plugin: Any, event: Any) -> str:
    resolver = getattr(plugin, "_extract_group_id_from_event", None)
    if callable(resolver):
        try:
            return str(resolver(event) or "").strip()
        except Exception:
            pass
    return str(getattr(event, "group_id", "") or "").strip()


def resolve_hdsi_mode(plugin: Any, event: Any) -> str:
    """Resolve the narrowest explicit binding without widening scope."""
    mode = normalize_hdsi_mode(getattr(plugin, "hdsi_experiment_mode", "legacy"))
    if mode == "legacy":
        return mode
    if _event_is_private(event):
        user_ids = normalize_id_set(getattr(plugin, "hdsi_experiment_user_ids", ()))
        return mode if _event_sender_id(event) in user_ids else "legacy"
    group_id = _event_group_id(plugin, event)
    group_ids = normalize_id_set(getattr(plugin, "hdsi_experiment_group_ids", ()))
    return mode if group_id and group_id in group_ids else "legacy"


def mark_hdsi_route(plugin: Any, event: Any) -> str:
    mode = resolve_hdsi_mode(plugin, event)
    try:
        setattr(event, "private_companion_hdsi_mode", mode)
        setattr(event, "private_companion_hdsi_route_version", "hdsi-compat-v1")
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
    "normalize_hdsi_mode",
    "normalize_id_set",
    "resolve_hdsi_mode",
]
