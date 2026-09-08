from __future__ import annotations

from types import SimpleNamespace

from astrbot_plugin_private_companion.hdsi_experiment import (
    build_hdsi_prompt_section,
    mark_hdsi_route,
    resolve_hdsi_binding,
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


def _plugin(mode: str = "hdsi_active", users=(), groups=(), **extra):
    plugin = SimpleNamespace(
        hdsi_experiment_mode=mode,
        hdsi_experiment_user_ids=users,
        hdsi_experiment_group_ids=groups,
    )
    for key, value in extra.items():
        setattr(plugin, key, value)
    return plugin


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


def test_private_route_reuses_canonical_storage_identity() -> None:
    plugin = _plugin(
        users=["main-user"],
        _private_user_id_for_event=lambda _event, _raw: "main-user",
        _canonical_private_user_id=lambda value: "main-user" if value == "alias" else value,
    )
    binding = resolve_hdsi_binding(plugin, _private_event("alias"))
    assert binding["mode"] == "hdsi_active"
    assert binding["subject_id"] == "main-user"
    assert binding["scope"] == "private"
    assert len(binding["scope_fingerprint"]) == 16


def test_group_binding_accepts_full_umo_without_member_switching() -> None:
    event = SimpleNamespace(
        is_private_chat=lambda: False,
        get_sender_id=lambda: "member-a",
        group_id="group-1",
        unified_msg_origin="OneBot:GroupMessage:group-1",
    )
    plugin = _plugin(groups=["OneBot:GroupMessage:group-1"])
    assert resolve_hdsi_mode(plugin, event) == "hdsi_active"
    event.get_sender_id = lambda: "member-b"
    assert resolve_hdsi_mode(plugin, event) == "hdsi_active"


def test_same_sender_id_on_different_adapters_gets_distinct_scope_fingerprint() -> None:
    def platform(_event):
        return "qq"

    first = _private_event("42")
    second = _private_event("42")
    first.adapter_instance_id = "bot-a"
    second.adapter_instance_id = "bot-b"
    plugin = _plugin(users=["42"], _platform_kind_for_event=platform)
    assert resolve_hdsi_binding(plugin, first)["scope_fingerprint"] != resolve_hdsi_binding(plugin, second)["scope_fingerprint"]


def test_mark_route_exposes_binding_metadata() -> None:
    event = _private_event("pilot")
    plugin = _plugin(users=["pilot"], hdsi_experiment_binding_revision="rev-2")
    assert mark_hdsi_route(plugin, event) == "hdsi_active"
    assert event.private_companion_hdsi_chat_route_ready is True
    assert event.private_companion_hdsi_binding_revision == "rev-2"
    assert event.private_companion_hdsi_actor_id.startswith("hdsi:private:")
