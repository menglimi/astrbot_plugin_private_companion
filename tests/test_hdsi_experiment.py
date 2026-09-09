import asyncio
import inspect
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from astrbot_plugin_private_companion.hdsi_experiment import (
    apply_hdsi_prompt,
    build_hdsi_prompt_section,
    finalize_trial_response,
    format_hdsi_trial_stats,
    hdsi_window_command,
    mark_hdsi_route,
    normalize_window_modes,
    record_trial_failure,
    record_trial_input,
    record_hdsi_proactive_event,
    record_hdsi_inbound_event,
    record_hdsi_outbound_event,
    get_hdsi_runtime_snapshot,
    resolve_hdsi_binding,
    resolve_hdsi_mode,
    run_hdsi_life_tick,
    build_hdsi_decision_snapshot,
    build_hdsi_response_plan,
    hdsi_plan_fence_valid,
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


def test_global_continuity_uses_one_actor_across_windows():
    value = plugin(
        mode="hdsi_active",
        users=["pilot"],
        hdsi_experiment_continuity_scope="global",
    )
    first = window_event(subject="pilot", adapter="bot-a")
    second = window_event(subject="pilot", adapter="bot-b")
    first_binding = resolve_hdsi_binding(value, first)
    second_binding = resolve_hdsi_binding(value, second)
    assert first_binding["continuity_scope"] == "global"
    assert first_binding["continuity_actor_id"] == second_binding["continuity_actor_id"]
    assert first_binding["actor_id"] != second_binding["actor_id"]


@pytest.mark.parametrize("scope, same", [("user", True), ("session", False)])
def test_continuity_scope_controls_actor_merge(scope, same):
    value = plugin(mode="hdsi_active", users=["pilot"], hdsi_experiment_continuity_scope=scope)
    first = window_event(subject="pilot", adapter="bot-a")
    second = window_event(subject="pilot", adapter="bot-b")
    left = resolve_hdsi_binding(value, first)["continuity_actor_id"]
    right = resolve_hdsi_binding(value, second)["continuity_actor_id"]
    assert (left == right) is same


def test_user_continuity_uses_group_sender_not_group_id():
    value = plugin(mode="hdsi_active", users=["pilot"], groups=["group-1"], hdsi_experiment_continuity_scope="user")
    private = window_event(subject="pilot")
    group = window_event(group=True)
    group.get_sender_id = lambda: "pilot"
    assert resolve_hdsi_binding(value, private)["continuity_actor_id"] == resolve_hdsi_binding(value, group)["continuity_actor_id"]


def test_proactive_event_uses_same_global_actor_ledger():
    host = plugin(mode="hdsi_active", users=["pilot"])
    host.data = {}
    host._data_lock = asyncio.Lock()
    host._schedule_data_save = lambda **_kwargs: None
    event = window_event(subject="pilot")
    asyncio.run(record_hdsi_proactive_event(host, event, "autonomous_tick", activity="reading"))
    asyncio.run(record_hdsi_proactive_event(host, event, "autonomous_tick", activity="walking"))
    assert host.data["hdsi_event_ledger"]["events"][0]["event_type"] == "autonomous_tick"
    assert host.data["hdsi_actor_state"]["hdsi:global:default"]["event_count"] == 2


def test_global_actor_ledger_survives_window_switch():
    host = plugin(mode="hdsi_active", users=["pilot"], hdsi_experiment_continuity_scope="global")
    host.data = {}
    first, second = window_event(subject="pilot", adapter="bot-a"), window_event(subject="pilot", adapter="bot-b")
    mark_hdsi_route(host, first)
    mark_hdsi_route(host, second)

    async def run():
        await record_hdsi_inbound_event(host, first)
        await record_hdsi_inbound_event(host, second)

    asyncio.run(run())
    actor = host.data["hdsi_actor_state"][first.private_companion_hdsi_continuity_actor_id]
    assert actor["event_count"] == 2
    assert len(actor["windows"]) == 2
    second._private_companion_plugin = host
    snapshot = get_hdsi_runtime_snapshot(host, second)
    assert "统一生活账本已记录约2个事件" in snapshot
    assert "另一个聊天窗口" in snapshot


def test_runtime_snapshot_uses_bot_activity_projection_only():
    host = plugin(mode="hdsi_active", users=["pilot"])
    host.data = {"daily_state": {"current_activity": "整理房间"}}
    event = window_event(subject="pilot")
    mark_hdsi_route(host, event)
    snapshot = get_hdsi_runtime_snapshot(host, event)
    assert "整理房间" in snapshot
    assert "message_str" not in snapshot


def test_active_plan_is_structured_bounded_and_fenced():
    host = plugin(mode="hdsi_active", users=["pilot"], hdsi_experiment_binding_revision="rev-7")
    host.hdsi_runtime_generation = "generation-a"
    host.data = {"hdsi_actor_state": {"hdsi:global:default": {
        "event_count": 4, "last_scope": "private", "previous_scope": "group",
        "previous_window": "bot:GroupMessage:g", "windows": ["bot:GroupMessage:g"],
        "current_activity": "看书", "life_phase": "晚间生活",
    }}}
    event = window_event(subject="pilot")
    mark_hdsi_route(host, event)
    snapshot = build_hdsi_decision_snapshot(host, event)
    plan = build_hdsi_response_plan(host, event, snapshot)
    assert snapshot["event_count"] == 4
    assert snapshot["runtime_generation"] == "generation-a"
    assert plan["action"] == "respond"
    assert plan["continuity"] == "resume_attention_after_window_switch"
    assert plan["privacy"] == "private_relationship_allowed"
    assert len(plan["plan_id"]) == 24
    assert hdsi_plan_fence_valid(host, event, plan)
    host.hdsi_runtime_generation = "generation-b"
    assert not hdsi_plan_fence_valid(host, event, plan)


def test_group_plan_limits_public_audience_and_stale_binding_is_rejected():
    host = plugin(mode="hdsi_active", groups=["g"], hdsi_experiment_binding_revision="rev-1")
    event = window_event(group=True, subject="g")
    mark_hdsi_route(host, event)
    plan = build_hdsi_response_plan(host, event)
    assert plan["audience"] == "group"
    assert plan["privacy"] == "public_context_only"
    assert plan["max_chars"] < 500
    host.hdsi_experiment_binding_revision = "rev-2"
    assert not hdsi_plan_fence_valid(host, event, plan)


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


def test_trial_stats_command_is_aggregate_and_permission_gated():
    host, event = command_plugin(), window_event()
    host.data = {
        "hdsi_trial_observations": {
            "schema_version": 1,
            "events": [{"input_digest": "secret-digest"}],
            "metrics": {
                "hdsi_active": {
                    "requests": 3,
                    "responses": 2,
                    "failures": 1,
                    "latency_ms_total": 2400,
                    "prompt_bytes": 1200,
                }
            },
        }
    }
    result = asyncio.run(hdsi_window_command(host, event, "试验统计"))
    assert "请求 3" in result
    assert "平均延迟 1200ms" in result
    assert "secret-digest" not in result

    host._can_manage_private_companion = lambda _event: False
    denied = asyncio.run(hdsi_window_command(host, event, "统计"))
    assert "管理权限" in denied


def test_trial_stats_without_data_is_explicit():
    host = command_plugin()
    host.data = {}
    assert format_hdsi_trial_stats(host) == "HDSI 试验统计：暂无数据。"


def test_trial_observer_records_bounded_content_free_lifecycle():
    host = plugin(mode="hdsi_active", users=["pilot"])
    host.data = {}
    host._data_lock = asyncio.Lock()
    saves = []
    host._schedule_data_save = lambda **kwargs: saves.append(kwargs)

    async def run():
        for index in range(205):
            event = window_event(subject="pilot")
            event.message_str = f"message-{index}"
            mark_hdsi_route(host, event)
            await record_trial_input(host, event)
        response_event = window_event(subject="pilot")
        response_event.message_str = "response input"
        mark_hdsi_route(host, response_event)
        await record_trial_input(host, response_event)
        response_event.private_companion_hdsi_prompt_bytes = 321
        await finalize_trial_response(
            host, response_event, SimpleNamespace(completion_text="done"),
        )
        failure_event = window_event(subject="pilot")
        failure_event.message_str = "failure input"
        mark_hdsi_route(host, failure_event)
        await record_trial_input(host, failure_event)
        await record_trial_failure(host, failure_event, "provider_timeout")

    asyncio.run(run())
    state = host.data["hdsi_trial_observations"]
    assert len(state["events"]) == 200
    metrics = state["metrics"]["hdsi_active"]
    assert metrics["requests"] == 207
    assert metrics["responses"] == 1
    assert metrics["failures"] == 1
    assert metrics["prompt_bytes"] == 321
    assert state["events"][-2]["outcome"] == "response"
    assert state["events"][-1]["failure_reason"] == "provider_timeout"
    assert all(set(item).isdisjoint({"message_str", "completion_text"}) for item in state["events"])
    assert all(
        {"hdsi_trial_observations", "hdsi_event_ledger", "hdsi_actor_state"}
        <= set(call.get("sections") or ())
        for call in saves
    )


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
    assert req._private_companion_hdsi_response_plan["action"] == "respond"
    assert event.private_companion_hdsi_decision_snapshot["schema_version"] == 1
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


def test_trial_observer_persists_response_metrics_and_prompt_bytes():
    from astrbot_plugin_private_companion.hdsi_experiment import TRIAL_STATE_KEY

    class Host:
        def __init__(self):
            self.data = {}
            self._data_lock = asyncio.Lock()
            self.saves = []

        def _schedule_data_save(self, **kwargs):
            self.saves.append(kwargs)

    host = Host()
    event = SimpleNamespace(private_companion_hdsi_mode="hdsi_active", message_str="hello")
    asyncio.run(record_trial_input(host, event))
    event.private_companion_hdsi_prompt_bytes = 321
    asyncio.run(finalize_trial_response(host, event, SimpleNamespace(completion_text="reply")))
    state = host.data[TRIAL_STATE_KEY]
    assert state["metrics"]["hdsi_active"] == {
        "requests": 1, "responses": 1, "failures": 0,
        "latency_ms_total": state["metrics"]["hdsi_active"]["latency_ms_total"],
        "prompt_bytes": 321,
    }
    assert state["events"][0]["prompt_bytes"] == 321
    assert host.saves


def test_trial_observer_bounds_events_and_failure_is_idempotent():
    class Host:
        def __init__(self):
            self.data = {}
            self._data_lock = asyncio.Lock()

        def _schedule_data_save(self, **kwargs):
            return None

    host = Host()
    async def collect():
        for index in range(201):
            await record_trial_input(host, SimpleNamespace(private_companion_hdsi_mode="hdsi_shadow", message_str=str(index)))
        failed = SimpleNamespace(private_companion_hdsi_mode="hdsi_shadow", message_str="failure")
        await record_trial_input(host, failed)
        await record_trial_failure(host, failed, "provider_error")
        await record_trial_failure(host, failed, "provider_error")
    asyncio.run(collect())
    state = host.data["hdsi_trial_observations"]
    assert len(state["events"]) == 200
    assert state["metrics"]["hdsi_shadow"]["failures"] == 1


def test_trial_input_retries_deduplicate_by_message_id_but_repeated_text_does_not():
    class Host:
        def __init__(self):
            self.data = {}
            self._data_lock = asyncio.Lock()
            self._event_message_id = lambda event: getattr(event, "message_id", "")

        def _schedule_data_save(self, **kwargs):
            return None

    host = Host()

    async def collect():
        first = SimpleNamespace(private_companion_hdsi_mode="hdsi_active", message_str="same", message_id="m-1")
        retry = SimpleNamespace(private_companion_hdsi_mode="hdsi_active", message_str="same", message_id="m-1")
        await record_trial_input(host, first)
        await record_trial_failure(host, first, "provider_error")
        await record_trial_input(host, retry)
        await record_trial_failure(host, retry, "provider_error")
        await record_trial_input(host, SimpleNamespace(private_companion_hdsi_mode="hdsi_active", message_str="same"))
        await record_trial_input(host, SimpleNamespace(private_companion_hdsi_mode="hdsi_active", message_str="same"))

    asyncio.run(collect())
    state = host.data["hdsi_trial_observations"]
    assert state["metrics"]["hdsi_active"]["requests"] == 3
    assert state["metrics"]["hdsi_active"]["failures"] == 1
    assert len(state["events"]) == 3


def test_orphan_trial_failure_is_ignored():
    class Host:
        def __init__(self):
            self.data = {}
            self._data_lock = asyncio.Lock()

        def _schedule_data_save(self, **kwargs):
            raise AssertionError("orphan failures must not schedule trial persistence")

    host = Host()
    event = SimpleNamespace(private_companion_hdsi_mode="hdsi_active")
    asyncio.run(record_trial_failure(host, event, "provider_error"))
    assert "hdsi_trial_observations" not in host.data


def test_outbound_ledger_ignores_intermediate_and_empty_responses():
    host = plugin(mode="hdsi_active", users=["pilot"])
    host.data = {}
    host._data_lock = asyncio.Lock()
    host._schedule_data_save = lambda **kwargs: None
    event = window_event(subject="pilot")
    mark_hdsi_route(host, event)

    async def run():
        await record_hdsi_outbound_event(host, event, SimpleNamespace(role="assistant", is_chunk=True, completion_text="part"))
        await record_hdsi_outbound_event(host, event, SimpleNamespace(role="tool", completion_text="tool"))
        await record_hdsi_outbound_event(host, event, SimpleNamespace(role="assistant", completion_text="tool prelude", tools_call_args=[{}]))
        await record_hdsi_outbound_event(host, event, SimpleNamespace(role="assistant", completion_text=""))
        assert not getattr(event, "private_companion_hdsi_outbound_recorded", False)
        await record_hdsi_outbound_event(host, event, SimpleNamespace(role="assistant", completion_text="final"))
        await record_hdsi_outbound_event(host, event, SimpleNamespace(role="assistant", completion_text="duplicate"))

    asyncio.run(run())
    events = host.data["hdsi_event_ledger"]["events"]
    assert [item["event_type"] for item in events] == ["outbound_message"]
    assert events[0]["response_chars"] == len("final")


def test_legacy_route_does_not_create_trial_state():
    host = plugin(mode="legacy")
    host.enabled = True
    host.data = {}
    event = window_event(subject="pilot")
    mark_hdsi_route(host, event)
    asyncio.run(apply_hdsi_prompt(host, event, request()))
    assert "hdsi_trial_observations" not in host.data


def test_hdsi_life_tick_advances_existing_actor_only_and_is_throttled():
    host = plugin(mode="hdsi_active")
    host.data = {
        "hdsi_actor_state": {
            "hdsi:global:default": {
                "schema_version": 1,
                "mode": "hdsi_active",
                "persona_id": "default",
                "last_scope": "private",
                "scope_fingerprint": "fp",
                "windows": ["bot:FriendMessage:pilot"],
                "event_count": 2,
                "last_life_tick_at": 0,
            },
            "legacy-actor": {"mode": "legacy", "event_count": 9},
        }
    }
    host._data_lock = asyncio.Lock()
    host._schedule_data_save = lambda **kwargs: None
    first = asyncio.run(run_hdsi_life_tick(host, now=1_700_000_000))
    second = asyncio.run(run_hdsi_life_tick(host, now=1_700_000_030))
    assert first == 1
    assert second == 0
    actor = host.data["hdsi_actor_state"]["hdsi:global:default"]
    assert actor["life_phase"]
    assert actor["event_count"] == 3
    assert host.data["hdsi_actor_state"]["legacy-actor"]["event_count"] == 9
    assert host.data["hdsi_event_ledger"]["events"][-1]["event_type"] == "life_tick"


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
