"""Outbound continuations keep their original connection across task boundaries."""

from datetime import timedelta
import json
import unittest
from unittest.mock import patch

from astrbot_host.account_recovery import AccountExecutionGuard
from companion_v2.configuration.registry import FieldRegistry
from companion_v2.execution.models import TaskError
from companion_v2.model_gateway.routing import configuration_fields
from companion_v2.proactive.service import plan_profile
from tests.proactive_support import ContactFixture
from tests.test_agenda_background import AgendaBackgroundFixture
from tests import test_proactive_contacts
from tests.test_role_tool_loop import answered


class Connections:
    def __init__(self, now):
        self.now, self.generation, self.online = now, 1, True

    def acquire(self, key):
        if not self.online:
            raise TaskError("host_account_offline", "account is offline")
        return self.generation

    def assert_current(self, lease):
        if not self.online or lease != self.generation:
            raise TaskError("host_connection_changed", "connection was replaced")


class ProactiveConnectionRecoveryTests(ContactFixture, unittest.IsolatedAsyncioTestCase):
    async def guarded(self, *, two_stage=False):
        if two_stage:
            self.profiles += (plan_profile("primary", max_output_tokens=100),)
            self.registry = FieldRegistry(version="connection-contacts.v1", fields=configuration_fields(self.profiles))
        host = self.host()
        self.guarded_host = host
        app = host.application
        self.connections = Connections(self.clock.now)
        self.guard = AccountExecutionGuard(self.connections, self.runtime)
        app.tasks.bind_execution_guard(self.guard)
        await self.start(host)
        self.settings(app, {"enabled": True, "generation_mode": "two_stage" if two_stage else "single"}).unwrap()
        return app

    async def test_connection_replaced_after_plan_prevents_composition(self):
        app = await self.guarded(two_stage=True)

        async def model(_):
            return answered('{"decision":"send","decision_confidence":0.9,"angle":"聊书","facts":[],"avoid":[]}')

        self.model_behavior = model
        original = app.proactive._run_exact

        async def completed_plan(runner, *args, **kwargs):
            result = await original(runner, *args, **kwargs)
            if runner is app.proactive.plan_runner:
                self.connections.generation += 1
            return result

        with patch.object(app.proactive, "_run_exact", completed_plan):
            result = await self.contact(app)
        self.assertEqual(result.failure.code, "host_connection_changed")
        self.assertEqual([call.original.output_format for call in self.called], ["proactive_plan"])
        self.assertEqual(self.sent, [])

    async def test_connection_replaced_after_first_sent_part_cannot_queue_the_rest(self):
        app = await self.guarded()
        test_proactive_contacts.ProactiveContactTests.presentation_settings(self, app, {
            "enabled": True, "llm_controlled": True, "rules_enabled": False,
            "part_gap_mode": "fixed", "part_gap_ms": 0,
        }).unwrap()

        async def model(_):
            return answered(json.dumps({"decision": "send", "text":
                "第一句。\n<<PRIVATE_COMPANION_SPLIT>>\n第二句。"}, ensure_ascii=False))

        self.model_behavior = model
        original = app.proactive.delivery_confirmed

        def confirmed(delivery_id):
            self.connections.generation += 1
            return original(delivery_id)

        with patch.object(app.proactive, "delivery_confirmed", confirmed):
            task = (await self.contact(app)).unwrap().task
            await self.delivery_state(app, task.task_id, "delivered")
        app.proactive.advance_deliveries()
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(len(app.proactive._delivery_rows(task.task_id)), 1)

    async def test_final_part_releases_completed_logical_lease(self):
        app = await self.guarded()
        task = (await self.contact(app)).unwrap().task
        delivery = await self.delivery_state(app, task.task_id, "delivered")
        await self.until(lambda: self.runtime.read_one("SELECT state FROM execution_tasks WHERE task_id = ?",
                                                     (delivery["task_id"],))[0] == "succeeded")
        self.assertEqual(self.guard._logical_leases, {})
        self.assertEqual(app.tasks._execution_leases, {})

    async def test_legacy_reply_keeps_connection_across_generated_parts(self):
        app = await self.guarded()
        test_proactive_contacts.ProactiveContactTests.presentation_settings(self, app, {
            "enabled": True, "llm_controlled": True, "rules_enabled": False,
            "part_gap_mode": "fixed", "part_gap_ms": 0,
        }).unwrap()

        async def model(_):
            return answered("先休息一会。\n<<PRIVATE_COMPANION_SPLIT>>\n然后再继续。")

        async def send(envelope):
            self.connections.generation += 1
            return self.receipt(envelope)

        self.model_behavior, self.send_behavior = model, send
        batch = self.guarded_host.receive(self.event()).unwrap()
        self.clock.advance(timedelta(seconds=2))
        app.notify()
        await self.done(app, batch)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(app.state, "running")


class QueuedReminderRecoveryTests(AgendaBackgroundFixture, unittest.IsolatedAsyncioTestCase):
    async def test_queued_reminder_waits_offline_then_uses_recovered_connection(self):
        host = self.host()
        app = host.application
        connections = Connections(self.clock.now)
        guard = AccountExecutionGuard(connections, self.runtime)
        app.tasks.bind_execution_guard(guard)
        app.outbound.bind_connection_gate(guard.available)
        app.agenda_background.bind_connection_gate(guard.available)
        with patch.object(app.background, "advance", return_value=False):
            await self.start(host)
            occurrence, _ = self.reminder(app)
            app.agenda_background.advance().unwrap()
            self.assertEqual(self.delivery(occurrence)["state"], "pending")
            connections.online = False
            self.assertEqual(app.outbound.pending(limit=1), ())
            self.assertGreater(app.outbound.next_wake(), self.clock.now())
            self.assertEqual(app.tasks._execution_leases, {})
            connections.generation += 1
            connections.online = True
        app.notify()
        await self.delivered(app, occurrence)
        self.assertEqual(len(self.sent), 1)

    async def test_queued_reminder_expires_while_still_offline(self):
        host = self.host()
        app = host.application
        connections = Connections(self.clock.now)
        guard = AccountExecutionGuard(connections, self.runtime)
        app.tasks.bind_execution_guard(guard)
        app.outbound.bind_connection_gate(guard.available)
        with patch.object(app.background, "advance", return_value=False):
            await self.start(host)
            occurrence, _ = self.reminder(app)
            app.agenda_background.advance().unwrap()
            row = self.delivery(occurrence)
            connections.online = False
            self.clock.advance(timedelta(seconds=app.agenda_background.policy.lateness_seconds + 1))
        app.notify()
        await self.until(lambda: self.runtime.read_one("SELECT state FROM execution_tasks WHERE task_id = ?",
                                                     (row["task_id"],))[0] == "expired")
        self.assertEqual(self.sent, [])
