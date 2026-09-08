import asyncio
import inspect
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from astrbot_plugin_private_companion.hdsi_experiment import (
    apply_hdsi_prompt,
    build_hdsi_prompt_section,
    hdsi_window_command,
    mark_hdsi_route,
    normalize_window_modes,
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


def window_event(*, group=False, adapter="bot-a", subject="pilot"):
    event = group_event(subject) if group else private_event(subject)
    kind = "GroupMessage" if group else "FriendMessage"
    event.unified_msg_origin = f"{adapter}:{kind}:{subject}"
    return event


def command_plugin(**extra):
    return plugin(
        mode="legacy", enabled=True,
        config={"reactive_poke_config": {"hdsi_experiment_window_modes": "{}"}},
        _save_config_if_possible=AsyncMock(return_value=True),
        _can_manage_private_companion=lambda _event: True,
        _can_manage_group_companion=lambda _event: True,
        _event_is_inbound_chat_message=lambda _event: True,
        **extra,
    )


def test_one_command_enables_only_current_window_and_survives_reload():
    host, event = command_plugin(), window_event()
    assert "下一条消息生效" in asyncio.run(hdsi_window_command(host, event, "开启"))
    assert resolve_hdsi_mode(host, window_event()) == "hdsi_active"
    assert resolve_hdsi_mode(host, window_event(adapter="bot-b")) == "legacy"
    assert resolve_hdsi_mode(host, window_event(group=True)) == "legacy"
    assert host.hdsi_experiment_mode == "legacy"
    persisted = host.config["reactive_poke_config"]["hdsi_experiment_window_modes"]
    reloaded = plugin(mode="legacy", hdsi_experiment_window_modes=normalize_window_modes(persisted))
    assert resolve_hdsi_mode(reloaded, window_event()) == "hdsi_active"


def test_disable_overrides_old_allowlist_without_disabling_other_windows():
    host, event = command_plugin(), window_event()
    host.hdsi_experiment_mode = "hdsi_active"
    host.hdsi_experiment_user_ids = ["pilot", "other"]
    asyncio.run(hdsi_window_command(host, event, "关闭"))
    assert resolve_hdsi_mode(host, event) == "legacy"
    assert resolve_hdsi_mode(host, window_event(subject="other")) == "hdsi_active"


def test_group_command_affects_every_member_only_in_that_group():
    host, event = command_plugin(), window_event(group=True)
    asyncio.run(hdsi_window_command(host, event, "开启"))
    event.get_sender_id = lambda: "another-member"
    assert resolve_hdsi_mode(host, event) == "hdsi_active"
    assert resolve_hdsi_mode(host, window_event(group=True, subject="other")) == "legacy"


@pytest.mark.parametrize("group", [False, True])
def test_unauthorized_command_does_not_save(group):
    host, event = command_plugin(), window_event(group=group)
    host._can_manage_private_companion = host._can_manage_group_companion = lambda _event: False
    assert "管理权限" in asyncio.run(hdsi_window_command(host, event, "开启"))
    host._save_config_if_possible.assert_not_awaited()
    assert resolve_hdsi_mode(host, event) == "legacy"


@pytest.mark.parametrize("failure", [False, OSError("save failed")])
def test_failed_save_preserves_runtime_and_configuration(failure):
    host, event = command_plugin(), window_event()
    if isinstance(failure, Exception):
        host._save_config_if_possible.side_effect = failure
    else:
        host._save_config_if_possible.return_value = failure
    assert "保存失败" in asyncio.run(hdsi_window_command(host, event, "开启"))
    assert host.config["reactive_poke_config"]["hdsi_experiment_window_modes"] == "{}"
    assert resolve_hdsi_mode(host, event) == "legacy"


def test_unknown_action_and_missing_window_do_not_modify_configuration():
    host = command_plugin()
    asyncio.run(hdsi_window_command(host, window_event(), "typo"))
    asyncio.run(hdsi_window_command(host, private_event(), "开启"))
    host._save_config_if_possible.assert_not_awaited()


def test_simultaneous_window_switches_do_not_overwrite_each_other():
    host = command_plugin()

    async def slow_save():
        await asyncio.sleep(0)
        return True

    host._save_config_if_possible = slow_save

    async def switch():
        await asyncio.gather(
            hdsi_window_command(host, window_event(subject="a"), "开启"),
            hdsi_window_command(host, window_event(subject="b"), "观察"),
        )

    asyncio.run(switch())
    saved = json.loads(host.config["reactive_poke_config"]["hdsi_experiment_window_modes"])
    assert saved == {"bot-a:FriendMessage:a": "hdsi_active", "bot-a:FriendMessage:b": "hdsi_shadow"}


def prompt_host():
    from astrbot_plugin_private_companion.main import PrivateCompanionPlugin

    host = command_plugin()
    host._proactive_only_blocks_passive_event = lambda _event: False
    host._normalize_passive_injection_position = lambda value: value
    host._record_request_prompt_fragment = AsyncMock()
    host._place_conversation_prompt_section = PrivateCompanionPlugin._place_conversation_prompt_section.__get__(host)
    return host


def request():
    return SimpleNamespace(system_prompt="persona", prompt="hello", extra_user_content_parts=[])


def test_active_command_reaches_real_prompt_hook_and_placement_once():
    from astrbot_plugin_private_companion.main import PrivateCompanionPlugin

    host, event, req = prompt_host(), window_event(), request()
    asyncio.run(hdsi_window_command(host, event, "开启"))
    mark_hdsi_route(host, event)
    hook = inspect.unwrap(PrivateCompanionPlugin.inject_hdsi_experiment_prompt)
    asyncio.run(hook(host, event, req))
    asyncio.run(hook(host, event, req))
    assert "HDSI" in str(req.extra_user_content_parts)
    assert req.system_prompt == "persona"
    host._record_request_prompt_fragment.assert_awaited_once()


def test_shadow_records_once_per_request_without_changing_prompts():
    host, event, req = prompt_host(), window_event(), request()
    asyncio.run(hdsi_window_command(host, event, "观察"))
    mark_hdsi_route(host, event)
    asyncio.run(apply_hdsi_prompt(host, event, req))
    asyncio.run(apply_hdsi_prompt(host, event, req))
    assert vars(req) == {"system_prompt": "persona", "prompt": "hello", "extra_user_content_parts": [], "_private_companion_hdsi_processed": True}
    host._record_request_prompt_fragment.assert_awaited_once()


@pytest.mark.parametrize("blocked_by", ["disabled", "proactive", "not_inbound", "proactive_only", "unmarked", "turned_off"])
def test_prompt_hook_respects_existing_gates_and_switch_off(blocked_by):
    host, event, req = prompt_host(), window_event(), request()
    asyncio.run(hdsi_window_command(host, event, "开启"))
    mark_hdsi_route(host, event)
    if blocked_by == "disabled":
        host.enabled = False
    elif blocked_by == "proactive":
        event.private_companion_proactive_framework = True
    elif blocked_by == "not_inbound":
        host._event_is_inbound_chat_message = lambda _event: False
    elif blocked_by == "proactive_only":
        host._proactive_only_blocks_passive_event = lambda _event: True
    elif blocked_by == "unmarked":
        event.private_companion_hdsi_chat_route_ready = False
    else:
        asyncio.run(hdsi_window_command(host, event, "关闭"))
    asyncio.run(apply_hdsi_prompt(host, event, req))
    assert vars(req) == vars(request())
    host._record_request_prompt_fragment.assert_not_awaited()
