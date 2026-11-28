"""Presentation inspection never dispatches, renews receipts or crosses contexts."""

from dataclasses import replace
from datetime import timedelta
import json
import unittest

from companion_v2.agenda.models import Schedule
from companion_v2.delivery.presence import PresenceCoordinator
from companion_v2.execution.models import TaskError, encode, stamp
from companion_v2.identity_policy.personas import ConversationRoute
from tests import test_role_presence as support
from tests.test_management_access import NOW
from tests.test_role_state import StateFixture, calendar_data, state_data


class PresenceDiagnosticTests(StateFixture, unittest.IsolatedAsyncioTestCase):
    current_plan = support.PresenceTests.current_plan

    async def test_read_only_conditions_distinguish_disabled_paused_idle_and_revoked(self):
        port = support.FakePresence()
        host, app, context, manager = self.setup_state(presence_port=port)
        inspect = lambda: manager.status(self.core).unwrap()["presentation_diagnostics"]
        self.assertEqual(inspect()["typing"]["state"], "disabled")
        self.configure(manager, typing_enabled=True, presence_enabled=True, typing_refresh_seconds=7)
        self.assertEqual(inspect()["typing"]["state"], "paused")
        await self.start(host)
        changes = self.runtime.read_one("SELECT total_changes() AS n", ())["n"]
        contexts = dict(app.presence.contexts)
        for _ in range(3):
            diagnostic = inspect()
            self.assertEqual(diagnostic["typing"]["state"], "idle")
            self.assertEqual(diagnostic["typing"]["refresh_seconds"], 7)
            self.assertEqual(diagnostic["custom_status"]["reason"], "presence_no_public_wording")
        self.assertEqual(self.runtime.read_one("SELECT total_changes() AS n", ())["n"], changes)
        self.assertEqual(app.presence.contexts, contexts)
        for grant in self.identity.snapshot().grants:
            if grant.capability == "platform.typing":
                self.identity.revoke_grant(grant.grant_id, revoked_at=NOW, expected_revision=self.identity.revision)
        self.assertEqual(inspect()["typing"]["state"], "blocked")
        self.assertEqual((port.pulses, port.statuses, self.called, self.sent), ([], [], [], []))
        self.assertIsNone(app.presence.latest_status(context))

    async def test_public_intent_and_plan_conflict_inspection_do_not_schedule_requests(self):
        port = support.FakePresence()
        host, app, context, manager = self.setup_state(presence_port=port)
        self.configure(manager, calendar_enabled=True, presence_enabled=True)
        await self.start(host)
        app.presence.bind(context)
        data = calendar_data(schedule=json.loads(encode(Schedule(NOW, "Asia/Shanghai"))))
        self.event_command(app, context, data=data)
        diagnostic = manager.status(self.core).unwrap()["presentation_diagnostics"]["custom_status"]
        self.assertEqual(diagnostic["state"], "waiting")
        self.assertEqual(diagnostic["desired"]["wording"], "阅读中")
        self.assertNotIn(data["title"], encode(diagnostic))
        self.assertNotIn(data["detail"], encode(diagnostic))
        self.event_command(app, context, identifier="other", key="other", data=data | {"presence": "散步中"})
        diagnostic = manager.status(self.core).unwrap()["presentation_diagnostics"]["custom_status"]
        self.assertEqual(diagnostic["reason"], "presence_plan_conflict")
        self.assertIsNone(diagnostic["desired"])
        self.assertEqual(diagnostic["state"], "blocked")
        self.assertIsNone(app.presence.latest_status(context))
        self.assertEqual(port.statuses, [])

    async def test_online_mode_diagnostic_tracks_selected_intent_and_recorded_request(self):
        port = support.FakePresence()
        host, app, context, manager = self.setup_state(presence_port=port)
        self.configure(manager, enabled=True, presence_enabled=True, online_status_enabled=True)
        app.role_state.write(context, data=state_data(online_mode="busy", presence="专注中"),
                             expected_revision=0, operation_key="busy")
        await self.start(host)
        await self.until(lambda: bool(port.statuses))
        # Default presence_priority='custom_first': custom_status wins and online_status reports shadowed_by.
        diagnostics = manager.status(self.core).unwrap()["presentation_diagnostics"]
        self.assertEqual(diagnostics["priority"], "custom_first")
        self.assertEqual(diagnostics["effective_channel"], "custom_status")
        self.assertEqual(diagnostics["custom_status"]["state"], "recorded")
        self.assertEqual(diagnostics["custom_status"]["desired"]["wording"], "专注中")
        self.assertIsNone(diagnostics["custom_status"]["shadowed_by"])
        self.assertEqual(diagnostics["online_status"]["state"], "idle")
        self.assertIsNone(diagnostics["online_status"]["desired"])
        self.assertIsNone(diagnostics["online_status"]["reason"])
        self.assertEqual(diagnostics["online_status"]["shadowed_by"], "custom_status")

        # Switching to online_first lets online_status win and shadows custom_status.
        self.configure(manager, presence_priority="online_first")
        await app.presence._sync_status()
        diagnostics = manager.status(self.core).unwrap()["presentation_diagnostics"]
        self.assertEqual(diagnostics["priority"], "online_first")
        self.assertEqual(diagnostics["effective_channel"], "online_status")
        self.assertEqual(diagnostics["online_status"]["state"], "recorded")
        self.assertEqual(diagnostics["online_status"]["desired"]["mode"], "online")
        self.assertEqual(diagnostics["custom_status"]["state"], "idle")
        self.assertIsNone(diagnostics["custom_status"]["desired"])
        self.assertIsNone(diagnostics["custom_status"]["reason"])
        self.assertEqual(diagnostics["custom_status"]["shadowed_by"], "online_status")

        # Clearing online_mode lets custom_status take over cleanly even under online_first.
        app.role_state.write(context, data=state_data(presence="画画中"),
                             expected_revision=1, operation_key="wording")
        diagnostics = manager.status(self.core).unwrap()["presentation_diagnostics"]
        self.assertEqual(diagnostics["effective_channel"], "custom_status")
        self.assertEqual(diagnostics["custom_status"]["state"], "waiting")
        self.assertEqual(diagnostics["online_status"]["state"], "idle")
        self.assertEqual(diagnostics["online_status"]["shadowed_by"], "custom_status")
        self.assertIsNone(diagnostics["online_status"]["reason"])
        self.assertEqual([w for _, w in port.statuses], ["专注中"])

    async def test_original_receipt_is_readable_before_restart_binding_without_resend_or_retimestamp(self):
        port, app, context, _, _ = await self.current_plan(result={"state": "unknown", "code": "presence_ack_unknown"})
        await app.presence.close()
        saved = app.presence.results_for(context)[app.presence.account(context)]
        self.clock.advance(timedelta(minutes=5))
        next_port = support.FakePresence()
        coordinator = PresenceCoordinator(app, next_port)
        self.addAsyncCleanup(coordinator.close)
        self.assertEqual(coordinator.results_for(context), {coordinator.account(context): saved})
        self.assertEqual(coordinator.contexts, {})
        self.assertEqual(coordinator.active_handles, ())
        rebound = replace(context, role_binding=replace(context.role_binding, revision=context.role_binding.revision + 1))
        self.assertEqual(coordinator.results_for(rebound), {})
        coordinator.bind(context)
        await coordinator._sync_status()
        self.assertEqual(coordinator.results_for(context)[coordinator.account(context)], saved)
        self.assertEqual(saved["recorded_at"], stamp(NOW))
        self.assertEqual(saved["operation_state"], "unknown")
        self.assertEqual(next_port.statuses, [])

    async def test_legacy_incomplete_operation_uses_original_start_record_time(self):
        _, app, context, _, _ = await self.current_plan()
        await app.presence.close()
        with self.runtime.transaction() as connection:
            connection.execute("UPDATE platform_presence_operations SET state = 'started', result_json = ?", (encode({"state": "unknown"}),))
        coordinator = PresenceCoordinator(app, support.FakePresence())
        self.addAsyncCleanup(coordinator.close)
        result = coordinator.results_for(context)[coordinator.account(context)]
        self.assertEqual(result["state"], "unknown")
        self.assertEqual(result["operation_state"], "started")
        self.assertEqual(result["recorded_at"], stamp(NOW))

    async def test_another_window_busy_does_not_renew_original_recipient_typing(self):
        port = support.FakePresence()
        host, app, context, manager = self.setup_state(presence_port=port)
        self.configure(manager, typing_enabled=True)
        other_scope = replace(context.scope, native_scope_id="other-native", conversation_id="other", session_id="other")
        self.personas.register_route(ConversationRoute(other_scope, user=self.important.subject), observed_at=NOW)
        self.grant(self.important, other_scope, "text")
        await self.start(host)
        batch = host.receive(self.event()).unwrap()
        host.receive(self.event("other", scope=other_scope)).unwrap()
        app.cancel(context, batch).unwrap()
        self.assertTrue(app.state_maintenance.foreground_busy(context))
        self.assertIsNone(app.presence.active_turn(context))
        self.assertEqual(manager.status(self.core).unwrap()["presentation_diagnostics"]["typing"]["state"], "idle")
        with self.assertRaises(TaskError) as error:
            await app.presence._pulse(context)
        self.assertEqual(error.exception.code, "typing_turn_closed")
        self.assertEqual(port.pulses, [])


if __name__ == "__main__":
    unittest.main()
