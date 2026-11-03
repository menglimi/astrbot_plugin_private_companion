"""QQ presentation uses fake ports and never touches a platform account."""

import asyncio
from dataclasses import replace
from datetime import timedelta
import json
import unittest
from unittest.mock import patch

from companion_v2.adapters.astrbot.presence import OneBotPresencePort
from companion_v2.conversation.flow import ConversationPolicy
from companion_v2.delivery.presence import PresenceCoordinator
from companion_v2.execution.models import TaskError, encode, stamp
from companion_v2.foundation.refs import AudienceRef
from companion_v2.agenda.models import Schedule
from tests.native_adapter_support import NativeFixture, FakeReverseApi
from tests.test_management_access import NOW
from tests.test_role_state import StateFixture, calendar_data, state_data
from tests.test_role_tool_loop import answered


class FakePresence:
    def __init__(self):
        self.pulses, self.statuses, self.modes = [], [], []
        self.typing_result = self.status_result = {"state": "accepted"}
        self.before_status = None

    async def typing(self, context, *, before_dispatch):
        before_dispatch()
        self.pulses.append(context)
        return self.typing_result

    async def custom_status(self, context, wording, *, before_dispatch):
        if self.before_status:
            self.before_status()
        try:
            before_dispatch()
        except TaskError as error:
            return {"state": "not_started", "code": error.code}
        self.statuses.append((context, wording))
        return self.status_result

    async def online_status(self, context, mode, *, before_dispatch):
        if self.before_status:
            self.before_status()
        try:
            before_dispatch()
        except TaskError as error:
            return {"state": "not_started", "code": error.code}
        self.modes.append((context, mode))
        return self.status_result


class PresenceTests(StateFixture, unittest.IsolatedAsyncioTestCase):
    async def test_real_reply_restores_online_without_reapplying_old_away_after_finish(self):
        port = FakePresence()
        host, app, context, manager = self.setup_state(presence_port=port)
        self.configure(manager, enabled=True, online_status_enabled=True)
        app.role_state.write(context, data=state_data(online_mode="away", sleep="待休息"),
                             expected_revision=0, operation_key="old-away")
        await self.start(host)
        app.presence.bind(context)
        await self.until(lambda: bool(port.modes))
        self.advance(app, 1)
        app.presence.begin_native_reply(context, "actual-reply")
        await app.presence._sync_status()
        app.presence.finish_native_reply(context, "actual-reply")
        await app.presence._sync_status()
        self.assertEqual([mode for _, mode in port.modes], ["online"])
        self.assertEqual(app.role_state.read(context)["values"]["sleep"], "待休息")
        self.advance(app, 1)
        app.role_state.write(context, data=state_data(online_mode="away", sleep="已入睡"),
                             expected_revision=1, operation_key="new-absence")
        await app.presence._sync_status()
        self.assertEqual([mode for _, mode in port.modes], ["online"])

    async def test_returned_native_reply_restores_online_after_coordinator_restart_once(self):
        from companion_v2.conversation.native_turns import NativeConversationTurns
        port = FakePresence()
        host, app, context, manager = self.setup_state(presence_port=port)
        self.configure(manager, enabled=True, online_status_enabled=True)
        # Away/busy are normalized to online immediately. Use an actual
        # remaining absence to exercise recovery after durable reply intake.
        app.role_state.write(context, data=state_data(online_mode="invisible"),
                             expected_revision=0, operation_key="old-invisible")
        await self.start(host)
        app.presence.bind(context)
        await self.until(lambda: bool(port.modes))
        self.advance(app, 1)
        turns = NativeConversationTurns(app.runtime)
        turns.mark({"context": context, "turn_id": "durable-reply", "instruction": "聊天"},
                   "completed", at=self.clock.now())
        turns.sent("durable-reply", self.clock.now())
        self.advance(app, 1)
        turns.mark({"context": context, "turn_id": "interrupted-reply", "instruction": "聊天"},
                   "failed", at=self.clock.now())
        await app.presence.close()
        next_port = FakePresence()
        next_port.status_result = {"state": "unknown"}
        coordinator = PresenceCoordinator(app, next_port)
        self.addAsyncCleanup(coordinator.close)
        coordinator.bind(context)
        await coordinator._sync_status()
        await coordinator._sync_status()
        self.assertEqual([mode for _, mode in next_port.modes], ["online"])
        retry_port = FakePresence()
        restarted = PresenceCoordinator(app, retry_port)
        self.addAsyncCleanup(restarted.close)
        restarted.bind(context)
        await restarted._sync_status()
        self.assertEqual(retry_port.modes, [])

    async def test_reply_recovery_respects_online_opt_in_and_operator_calibration(self):
        port = FakePresence()
        host, app, context, manager = self.setup_state(presence_port=port)
        self.configure(manager, enabled=True, presence_enabled=True)
        app.role_state.write(context, data=state_data(online_mode="away", presence="休息中"),
                             expected_revision=0, operation_key="old-away")
        await self.start(host)
        self.advance(app, 1)
        app.presence.begin_native_reply(context, "actual-reply")
        await app.presence._sync_status()
        self.assertEqual(port.modes, [])
        self.configure(manager, online_status_enabled=True)
        await app.presence._sync_status()
        app.role_state.write(context, data=state_data(online_mode="invisible"),
            expected_revision=1, operation_key="manual-invisible", operator=self.core, reason="human_edit")
        await app.presence._sync_status()
        self.assertEqual([mode for _, mode in port.modes], ["online", "invisible"])

    async def test_one_typing_worker_survives_input_merge_model_wait_and_part_gap(self):
        port = FakePresence()
        host, app, context, manager = self.setup_state(presence_port=port,
            policy=ConversationPolicy(part_gap_seconds=3))
        self.configure(manager, typing_enabled=True, typing_refresh_seconds=1)
        gate = asyncio.Event()
        async def respond(envelope):
            await gate.wait()
            return answered("第一段\n第二段")
        self.model_behavior = respond
        await self.start(host)
        batch = host.receive(self.event()).unwrap()
        await self.until(lambda: len(port.pulses) == 1)
        key = app.presence.route(context)
        worker = app.presence.typing[key][2]
        self.assertEqual(app.presence.results_for(context), {"typing:" + key:
            {"state": "accepted", "recorded_at": stamp(self.clock.now()), "batch_id": batch}})
        diagnostic = manager.status(self.core).unwrap()["presentation_diagnostics"]["typing"]
        self.assertEqual(diagnostic["state"], "refreshing")
        self.assertEqual(diagnostic["batch_id"], batch)
        rebound = replace(context, role_binding=replace(context.role_binding, revision=context.role_binding.revision + 1))
        self.assertEqual(app.presence.results_for(rebound), {})
        self.assertEqual(host.receive(self.event("continued")).unwrap(), batch)
        app.presence.advance()
        self.assertIs(app.presence.typing[key][2], worker)
        self.advance(app)
        await self.until(lambda: bool(self.called))
        self.advance(app, 91)
        await self.until(lambda: len(port.pulses) >= 2)
        self.assertIs(app.presence.typing[key][2], worker)
        gate.set()
        await self.until(lambda: len(self.sent) == 1)
        self.assertIs(app.presence.typing[key][2], worker)
        self.assertEqual(app.flow.row(batch)["state"], "delivering")
        self.advance(app, 4)
        self.assertEqual((await self.done(app, batch))["state"], "complete")
        app.presence.advance()
        await self.until(worker.done)
        app.presence.advance()
        self.assertFalse(any(handle.startswith("typing:") for handle in app.presence.active_handles))
        self.assertEqual(manager.status(self.core).unwrap()["presentation_diagnostics"]["typing"]["state"], "idle")
        self.assertEqual(port.statuses, [])

    async def test_typing_failure_does_not_spin_and_revoke_and_close_clean_up(self):
        port = FakePresence()
        port.typing_result = {"state": "unknown"}
        host, app, context, manager = self.setup_state(presence_port=port)
        self.configure(manager, typing_enabled=True, typing_refresh_seconds=1)
        await self.start(host)
        batch = host.receive(self.event()).unwrap()
        await self.until(lambda: len(port.pulses) == 1)
        for _ in range(5):
            app.presence.advance()
            await asyncio.sleep(0)
        self.assertEqual(len(port.pulses), 1)
        self.assertEqual(manager.status(self.core).unwrap()["presentation_diagnostics"]["typing"]["state"], "suppressed")
        app.cancel(context, batch).unwrap()
        app.presence.advance()
        port.typing_result = {"state": "accepted"}
        host.receive(self.event("new-turn")).unwrap()
        await self.until(lambda: len(port.pulses) == 2)
        worker = next(iter(app.presence.typing.values()))[2]
        self.configure(manager, typing_enabled=False)
        app.presence.advance()
        await self.until(worker.done)
        await app.close()
        self.assertEqual(app.presence.active_handles, ())

    async def current_plan(self, *, result=None):
        port = FakePresence()
        if result:
            port.status_result = result
        host, app, context, manager = self.setup_state(presence_port=port)
        self.configure(manager, calendar_enabled=True, presence_enabled=True)
        data = calendar_data(schedule=json.loads(encode(Schedule(NOW, "Asia/Shanghai"))), duration_minutes=30)
        self.event_command(app, context, data=data)
        await self.start(host)
        await self.until(lambda: bool(port.statuses))
        return port, app, context, manager, data

    async def test_public_status_follows_schedule_deduplicates_and_never_uses_private_title(self):
        port, app, context, manager, data = await self.current_plan()
        self.assertEqual([text for _, text in port.statuses], ["阅读中"])
        for _ in range(3):
            await app.presence._sync_status()
        self.assertEqual(len(port.statuses), 1)
        self.event_command(app, context, action="revise", revision=1, data=data | {"title": "更加私密的标题"})
        await app.presence._sync_status()
        self.assertEqual(len(port.statuses), 1)
        self.event_command(app, context, action="revise", revision=2, data=data | {"presence": "散步中"})
        await app.presence._sync_status()
        self.assertEqual([text for _, text in port.statuses], ["阅读中", "散步中"])
        self.advance(app, 1900)
        await app.presence._sync_status()
        self.assertEqual(len(port.statuses), 2)  # No unsolicited clearing of manual account status.

    async def test_unknown_ack_is_persistent_and_is_not_repeated_after_restart(self):
        port, app, context, _, _ = await self.current_plan(result={"state": "unknown"})
        await app.presence.close()
        next_port = FakePresence()
        coordinator = PresenceCoordinator(app, next_port)
        self.addAsyncCleanup(coordinator.close)
        coordinator.bind(context)
        await coordinator._sync_status()
        self.assertEqual(next_port.statuses, [])
        receipt = coordinator.results_for(context)[coordinator.account(context)]
        self.assertEqual(receipt["state"], "unknown")
        self.assertEqual(receipt["recorded_at"], stamp(NOW))
        self.assertEqual(receipt["desired"]["wording"], "阅读中")
        rebound = replace(context, role_binding=replace(context.role_binding, revision=context.role_binding.revision + 1))
        self.assertEqual(coordinator.results_for(rebound), {})
        row = self.runtime.read_one("SELECT state FROM platform_presence_operations", ())
        self.assertEqual(row["state"], "unknown")


    async def test_same_role_windows_share_one_presence_owner(self):
        port = FakePresence()
        _, app, context, _ = self.setup_state(presence_port=port)
        other_scope = replace(context.scope, native_scope_id="other-native",
                              conversation_id="other-conversation", session_id="other-session")
        other = replace(context,
            role_binding=replace(context.role_binding, scope=other_scope),
            audience=AudienceRef(other_scope, (context.actor.subject,)))
        coordinator = app.presence
        coordinator.bind(context)
        coordinator.bind(other)
        with patch.object(coordinator, "_status_available", return_value=True):
            owners = coordinator._candidates()[coordinator.account(context)]
        self.assertEqual(len(owners), 1)
        self.assertEqual(owners[0].key, other.key)

    async def test_changed_plan_during_native_preflight_does_not_send_old_wording(self):
        port, app, context, _, data = await self.current_plan()
        self.event_command(app, context, action="revise", revision=1, data=data | {"presence": "散步中"})
        port.before_status = lambda: self.event_command(app, context, action="cancel", revision=2)
        await app.presence._sync_status()
        self.assertEqual([wording for _, wording in port.statuses], ["阅读中"])

    async def test_overlapping_role_plans_do_not_arbitrarily_publish_one(self):
        port = FakePresence()
        host, app, context, manager = self.setup_state(presence_port=port)
        self.configure(manager, calendar_enabled=True, presence_enabled=True)
        data = calendar_data(schedule=json.loads(encode(Schedule(NOW, "Asia/Shanghai"))))
        self.event_command(app, context, identifier="reading", key="reading", data=data)
        self.event_command(app, context, identifier="walk", key="walk", data=data | {"presence": "散步中"})
        await self.start(host)
        await app.presence._sync_status()
        self.assertIsNone(app.presence.desired(context))
        self.assertEqual(port.statuses, [])

    async def test_online_modes_require_opt_in_and_change_only_on_explicit_state(self):
        port = FakePresence()
        host, app, context, manager = self.setup_state(presence_port=port)
        self.configure(manager, enabled=True)
        app.role_state.write(context, data=state_data(online_mode="online"),
                             expected_revision=0, operation_key="online")
        await self.start(host)
        app.presence.bind(context)
        await app.presence._sync_status()
        self.assertEqual(port.modes, [])
        self.configure(manager, online_status_enabled=True)
        self.assertTrue(app.presence._status_available(context))
        for revision, mode in enumerate(("online", "away", "busy", "invisible"), 1):
            if revision > 1:
                app.role_state.write(context, data=state_data(online_mode=mode),
                                     expected_revision=revision - 1, operation_key="mode-" + mode)
            await app.presence._sync_status()
            await app.presence._sync_status()
        self.assertEqual([mode for _, mode in port.modes], ["online", "invisible"])
        app.role_state.write(context, data=state_data(), expected_revision=4, operation_key="no-mode")
        await app.presence._sync_status()
        self.assertEqual(len(port.modes), 2)

    async def test_schedule_wording_precedes_online_mode_then_mode_resumes(self):
        port = FakePresence()
        host, app, context, manager = self.setup_state(presence_port=port)
        self.configure(manager, enabled=True, calendar_enabled=True, presence_enabled=True,
                       online_status_enabled=True)
        app.role_state.write(context, data=state_data(online_mode="busy", presence="专注中"),
                             expected_revision=0, operation_key="state")
        data = calendar_data(schedule=json.loads(encode(Schedule(NOW, "Asia/Shanghai"))), duration_minutes=30)
        self.event_command(app, context, data=data)
        await self.start(host)
        await self.until(lambda: bool(port.statuses))
        self.assertEqual([wording for _, wording in port.statuses], ["阅读中"])
        self.assertEqual(port.modes, [])
        self.event_command(app, context, action="cancel", revision=1)
        await app.presence._sync_status()
        self.assertEqual([mode for _, mode in port.modes], ["online"])

    async def test_unknown_online_ack_persists_without_resend_after_restart(self):
        port = FakePresence()
        port.status_result = {"state": "unknown"}
        host, app, context, manager = self.setup_state(presence_port=port)
        self.configure(manager, enabled=True, online_status_enabled=True)
        app.role_state.write(context, data=state_data(online_mode="away"),
                             expected_revision=0, operation_key="away")
        await self.start(host)
        await self.until(lambda: bool(port.modes))
        await app.presence.close()
        next_port = FakePresence()
        coordinator = PresenceCoordinator(app, next_port)
        self.addAsyncCleanup(coordinator.close)
        coordinator.bind(context)
        await coordinator._sync_status()
        self.assertEqual(next_port.modes, [])
        receipt = coordinator.results_for(context)[coordinator.account(context)]
        self.assertEqual((receipt["state"], receipt["desired"]["mode"]), ("unknown", "online"))

    async def test_role_simulation_never_dispatches_away_or_busy(self):
        port = FakePresence()
        host, app, context, manager = self.setup_state(presence_port=port)
        self.configure(manager, enabled=True, online_status_enabled=True)
        await self.start(host)
        app.presence.bind(context)
        for revision, mode in enumerate(("away", "busy"), 1):
            app.role_state.write(context, data=state_data(online_mode=mode),
                                 expected_revision=revision - 1, operation_key="blocked-" + mode)
            await app.presence._sync_status()
        self.assertEqual([mode for _, mode in port.modes], ["online"])
        self.assertNotIn("away", [mode for _, mode in port.modes])
        self.assertNotIn("busy", [mode for _, mode in port.modes])


class NativePresenceTests(NativeFixture, unittest.IsolatedAsyncioTestCase):
    async def setup_port(self):
        await self.envelope()
        self.grant(self.native_context.actor, self.native_context.scope, "platform.typing")
        self.grant(self.native_context.actor, self.native_context.scope, "platform.presence")
        return OneBotPresencePort(self.bridge, reverse_api_type=FakeReverseApi,
                                 authorize=lambda: None, now=self.clock.now)

    async def test_pinned_onebot_actions_and_parameters_without_toggling(self):
        port = await self.setup_port()
        self.assertEqual((await port.typing(self.native_context))["state"], "accepted")
        self.assertEqual((await port.custom_status(self.native_context, "阅读中"))["state"], "accepted")
        for mode in ("online", "away", "busy", "invisible"):
            self.assertEqual((await port.online_status(self.native_context, mode))["state"], "accepted")
        calls = self.native.platform.client._api._wsr_api.calls
        self.assertEqual(calls, [("set_input_status", {"self_id": "100", "user_id": 200, "event_type": 1}),
            ("set_diy_online_status", {"self_id": "100", "face_id": 21, "face_type": 1, "wording": "阅读中"}),
            *[("set_online_status", {"self_id": "100", "status": status, "ext_status": 0, "battery_status": 0})
              for status in (10, 30, 50, 40)]])
        self.assertEqual((await port.online_status(self.native_context, "sleeping"))["state"], "not_started")
        self.assertEqual(len(calls), 6)

    async def test_napcat_plain_status_ack_is_accepted(self):
        port = await self.setup_port()
        self.native.platform.client._api._wsr_api.result = "set status success"
        self.assertEqual((await port.custom_status(self.native_context, "陪伴中"))["state"], "accepted")
        self.native.platform.client._api._wsr_api.result = "set online status success"
        self.assertEqual((await port.online_status(self.native_context, "away"))["state"], "accepted")

    async def test_revoked_grant_and_wrong_account_never_dispatch(self):
        port = await self.setup_port()
        # Persona resolution uses the startup snapshot. Permission revocation
        # remains live and must not depend on resolving the persona again.
        for grant in self.identity.snapshot().grants:
            if grant.capability == "platform.typing":
                self.identity.revoke_grant(grant.grant_id, revoked_at=NOW, expected_revision=self.identity.revision)
        self.assertEqual((await port.typing(self.native_context))["state"], "not_started")
        altered = replace(self.native_context, actor=replace(self.native_context.actor,
            principal=replace(self.native_context.actor.principal, account_id="101")))
        self.assertEqual((await port.custom_status(altered, "休息中"))["state"], "not_started")
        self.assertEqual(self.native.platform.client._api._wsr_api.calls, [])

    async def test_transport_exception_is_unknown_without_alternate_api_attempt(self):
        port = await self.setup_port()
        api = self.native.platform.client._api._wsr_api
        api.error = TimeoutError()
        self.assertEqual((await port.typing(self.native_context))["state"], "unknown")
        self.assertEqual(len(api.calls), 1)
        self.assertEqual((await port.custom_status(self.native_context, "超过八个字的公开状态文案"))["state"], "not_started")
        self.assertEqual(len(api.calls), 1)


if __name__ == "__main__":
    unittest.main()
