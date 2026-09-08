from __future__ import annotations

from types import SimpleNamespace

from astrbot_plugin_private_companion.hdsi_experiment import (
    build_hdsi_prompt_section,
    resolve_hdsi_mode,
)


def _private_event(user_id: str):
    return SimpleNamespace(is_private_chat=lambda: True, get_sender_id=lambda: user_id)


def _group_event(group_id: str, sender_id: str = "member"):
    return SimpleNamespace(
        is_private_chat=lambda: False,
        get_sender_id=lambda: sender_id,
        group_id=group_id,
    )


def _plugin(mode: str = "hdsi_active", users=(), groups=()):
    return SimpleNamespace(
        hdsi_experiment_mode=mode,
        hdsi_experiment_user_ids=users,
        hdsi_experiment_group_ids=groups,
    )


def test_default_route_stays_legacy_for_unlisted_user() -> None:
    assert resolve_hdsi_mode(_plugin(users=["pilot"]), _private_event("normal")) == "legacy"


def test_opted_in_private_user_gets_active_route() -> None:
    assert resolve_hdsi_mode(_plugin(users=["pilot"]), _private_event("pilot")) == "hdsi_active"


def test_group_route_requires_group_binding() -> None:
    assert resolve_hdsi_mode(_plugin(users=["pilot"]), _group_event("group-1", "pilot")) == "legacy"
    assert resolve_hdsi_mode(_plugin(groups=["group-1"]), _group_event("group-1")) == "hdsi_active"


def test_shadow_route_does_not_build_active_prompt() -> None:
    assert build_hdsi_prompt_section(_private_event("pilot"), "hdsi_shadow") is None


def test_active_prompt_is_bounded_and_audience_aware() -> None:
    section = build_hdsi_prompt_section(_group_event("group-1"), "hdsi_active")
    assert section is not None
    assert "群聊" in str(section.content)
    assert len(str(section.content)) < 600
