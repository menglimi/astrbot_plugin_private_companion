from types import SimpleNamespace

from astrbot_plugin_private_companion.hdsi_experiment import (
    build_hdsi_prompt_section,
    mark_hdsi_route,
    resolve_hdsi_binding,
    resolve_hdsi_mode,
)


def private_event(user_id="pilot"):
    return SimpleNamespace(is_private_chat=lambda: True, get_sender_id=lambda: user_id)


def group_event(group_id="group-1", sender_id="member"):
    return SimpleNamespace(is_private_chat=lambda: False, get_sender_id=lambda: sender_id, group_id=group_id)


def plugin(mode="hdsi_active", users=(), groups=(), **extra):
    value = SimpleNamespace(hdsi_experiment_mode=mode, hdsi_experiment_user_ids=users, hdsi_experiment_group_ids=groups)
    for key, item in extra.items():
        setattr(value, key, item)
    return value


def test_unlisted_user_stays_legacy():
    assert resolve_hdsi_mode(plugin(users=["pilot"]), private_event("normal")) == "legacy"


def test_private_alias_uses_storage_identity():
    value = plugin(users=["main"], _private_user_id_for_event=lambda _event, _raw: "main")
    binding = resolve_hdsi_binding(value, private_event("alias"))
    assert binding["mode"] == "hdsi_active"
    assert binding["subject_id"] == "main"


def test_group_route_is_window_scoped_and_member_independent():
    value = plugin(groups=["group-1"])
    assert resolve_hdsi_mode(value, group_event(sender_id="a")) == "hdsi_active"
    assert resolve_hdsi_mode(value, group_event(sender_id="b")) == "hdsi_active"


def test_group_full_umo_binding_is_accepted():
    event = group_event()
    event.unified_msg_origin = "OneBot:GroupMessage:group-1"
    assert resolve_hdsi_mode(plugin(groups=[event.unified_msg_origin]), event) == "hdsi_active"


def test_mark_route_metadata_and_prompt():
    event = private_event()
    assert mark_hdsi_route(plugin(users=["pilot"], hdsi_experiment_binding_revision="r2"), event) == "hdsi_active"
    assert event.private_companion_hdsi_chat_route_ready is True
    assert event.private_companion_hdsi_binding_revision == "r2"
    assert build_hdsi_prompt_section(event, "hdsi_active") is not None


def test_same_sender_different_adapters_have_distinct_fingerprints():
    one, two = private_event("42"), private_event("42")
    one.adapter_instance_id, two.adapter_instance_id = "bot-a", "bot-b"
    value = plugin(users=["42"], _platform_kind_for_event=lambda _event: "qq")
    assert resolve_hdsi_binding(value, one)["scope_fingerprint"] != resolve_hdsi_binding(value, two)["scope_fingerprint"]
