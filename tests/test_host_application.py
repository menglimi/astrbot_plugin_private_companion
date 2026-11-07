from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import time
import unittest
from unittest.mock import patch

from companion_v2.adapters.astrbot.application import AstrBotCompanionHost
from companion_v2.configuration.registry import FieldRegistry
from companion_v2.conversation.delivery import DeliveryReceipt, LocalSender
from companion_v2.conversation.flow import ConversationPolicy
from companion_v2.conversation.messages import REPLY_KIND
from companion_v2.conversation.segmentation import SegmentationPolicy
from companion_v2.conversation.reply_settings import configuration_fields as reply_configuration_fields
from companion_v2.execution.models import Lane, ProviderBinding, SupervisorLimits, TaskContext, TaskError, digest
from companion_v2.foundation.clock import DeterministicClock
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import AudienceRef, SubjectKind, SubjectRef
from companion_v2.identity_policy.personas import ConversationRoute, PersonaBinding, PersonaSelector, RuntimeIdentity, SelectorKind
from companion_v2.model_gateway.models import Amount, BudgetPolicy, LocalProvider, ModelTaskProfile
from companion_v2.model_gateway.routing import configuration_fields
from tests.test_management_access import INSTALLATION, ManagementFixture, NOW, SCOPE
from tests.test_role_tool_loop import answered, call, requested


class HostPorts:
    def event_id(self, event): return event["id"]
    def scope(self, event): return event["scope"]
    def audience(self, event): return AudienceRef(event["scope"], (event["actor"].subject,))
    def payload(self, event): return event["text"]
    def payload_hash(self, event): return digest(event["text"])
    def received_at(self, event): return event["at"]
    def conversation_route(self, event): return ConversationRoute(event["scope"], user=event["actor"].subject)
    def authenticated_principal(self, event): return Outcome.success(event["actor"].principal)
    def role_instruction(self, event, context): return event["prompt"]


class HostFixture(ManagementFixture):
    # Real-time state changes produced by the running driver are polled, so the
    # budget must absorb a loaded machine. It bounds a hang, it does not measure
    # how fast the behaviour under test is.
    wait_seconds = 20

    def setUp(self):
        super().setUp()
        self.clock = DeterministicClock.at(NOW)
        self.grant(self.important, SCOPE, "text")
        self.called, self.sent = [], []
        self.model_behavior = self.send_behavior = None
        self.receipt_now = self.clock.now
        self.profiles = (ModelTaskProfile(REPLY_KIND, Lane.FOREGROUND, "primary", "补充任务提示", max_output_tokens=100),)
        self.registry = FieldRegistry(version="host.v1", fields=configuration_fields(self.profiles))
        self.providers = (LocalProvider(ProviderBinding("model.text", "primary", "1", "model-1"),
                          ("text", "tools"), 100, lambda envelope: Amount(100, 100), self.generate),)
        self.sender = LocalSender(ProviderBinding("message.delivery", "local", "1", "sender-1"), self.send)
        self.ports = HostPorts()
        self.limits = SupervisorLimits(foreground_slots=4, background_slots=1, queued_per_lane=64, retained_tasks=256)

    def host(self, *, runtime=None, policy=None, now=None, **changes):
        options = dict(event_port=self.ports, identity_port=self.ports, role_port=self.ports,
            runtime_identity=RuntimeIdentity("companion", "runtime-1", "astrbot", "host"),
            registry=self.registry, profiles=self.profiles, providers=self.providers,
            budget=BudgetPolicy(*(Amount(100000, 100000) for _ in range(5))), sender=self.sender,
            limits=self.limits, policy=policy or ConversationPolicy(part_gap_seconds=0),
            now=now or self.clock.now, split=lambda text: tuple(text.splitlines(keepends=True)),
            segmentation=SegmentationPolicy(enabled=False))
        options.update(changes)
        # Synthetic lifecycle tests explicitly use no punctuation cleanup and
        # fixed spacing. Product defaults have their own V1 preset regression.
        registry = options["registry"]
        reply_fields = tuple(replace(field, default={"part_gap_mode": "fixed", "content_cleanup_enabled": False}.get(
            field.field_id.removeprefix("conversation.presentation."), field.default))
            for field in reply_configuration_fields(options["segmentation"], self.providers[0].default_streaming,
                options["policy"].part_gap_seconds, options.get("voice_policy"))
            if field.field_id not in registry.fields)
        options["registry"] = FieldRegistry(version=registry.version,
            fields=(*registry.fields.values(), *reply_fields))
        host = AstrBotCompanionHost(runtime or self.runtime, **options)
        self.addAsyncCleanup(host.application.close)
        return host

    async def start(self, host=None, task_generation=0, model_generation=0):
        host = host or self.host()
        (await host.application.start(expected_task_generation=task_generation, expected_model_generation=model_generation)).unwrap()
        return host

    def event(self, identifier="event-1", **changes):
        return dict(id=identifier, scope=SCOPE, actor=self.important, text="输入 " + identifier,
                    at=self.clock.now(), prompt="原角色人格提示") | changes

    async def generate(self, envelope):
        self.called.append(envelope)
        return await self.model_behavior(envelope) if self.model_behavior else answered("第一段\n第二段")

    def receipt(self, envelope, status="delivered"):
        return DeliveryReceipt(envelope.part_id, envelope.operation_id, envelope.context.key, envelope.payload_hash,
            envelope.sender, status, status + ":" + envelope.part_id,
            "platform:" + envelope.part_id if status in ("delivered", "accepted") else None,
            self.receipt_now() if status == "delivered" else None)

    async def send(self, envelope):
        self.sent.append(envelope)
        return await self.send_behavior(envelope) if self.send_behavior else self.receipt(envelope)

    def context(self, app, batch):
        return TaskContext.restore(json.loads(app.messages.batch(batch)["context_json"]))

    async def until(self, predicate, *, seconds=None):
        """Wait for a real-time state change without depending on machine load.

        The driver loop wakes on its own event and its own wall-clock timeouts, so
        this can only poll: the state it waits for is produced by the running
        application, not by the clock this fixture advances. A tight deadline then
        measures the machine, not the behaviour under test, and a shared runner
        with other suites on it starves the loop long enough to fail a passing
        test. The deadline is therefore generous by default, and a test that
        genuinely needs a tighter bound can pass one.
        """
        deadline = time.monotonic() + (self.wait_seconds if seconds is None else seconds)

        async def wait():
            while not predicate():
                if time.monotonic() >= deadline:
                    raise TimeoutError("predicate did not become true before its deadline")
                await asyncio.sleep(0.005)
        await asyncio.wait_for(wait(), max(0.1, deadline - time.monotonic()))

    async def done(self, app, batch, *, seconds=None):
        await self.until(lambda: app.flow.row(batch)["state"] in ("complete", "blocked", "closed") or app.state == "faulted",
                         seconds=seconds)
        self.assertEqual(app.state, "running", app.failure_code)
        return app.flow.row(batch)

    def advance(self, app, seconds=1):
        self.clock.advance(timedelta(seconds=seconds))
        app.notify()


class HostApplicationTests(HostFixture, unittest.IsolatedAsyncioTestCase):
    async def test_contact_schedule_is_not_rescanned_on_every_driver_wake(self):
        host = self.host()
        app = host.application
        with patch.object(app, "CONTACT_SCHEDULE_INTERVAL_SECONDS", 60.0), patch.object(
                app.contact_schedule, "advance_async", wraps=app.contact_schedule.advance_async) as advance:
            await self.start(host)
            await self.until(lambda: advance.call_count == 1)
            for _ in range(5):
                app.notify()
                await asyncio.sleep(0.01)
            self.assertEqual(advance.call_count, 1)

    async def test_relationship_memory_service_is_assembled_as_the_access_boundary(self):
        host = await self.start()
        self.assertIs(host.application.relationship_memories.runtime, host.application.runtime)
        self.assertTrue(hasattr(host.application.relationship_memories, "adopted_for_relationship"))

    async def test_host_ingress_merges_once_and_delivers_only_confirmed_history(self):
        host = await self.start()
        first = self.event()
        batch = host.receive(first).unwrap()
        app = host.application
        self.advance(app, 0.4)
        self.assertEqual(host.receive(self.event("event-2")).unwrap(), batch)
        due = app.flow.row(batch)["due_at"]
        self.assertEqual(host.receive(first).unwrap(), batch)
        self.assertEqual(app.flow.row(batch)["due_at"], due)
        self.advance(app)
        self.assertEqual((await self.done(app, batch))["state"], "complete")
        self.assertEqual(len(self.called), 1)
        original = self.called[0].original
        self.assertEqual(original.role_instruction, "原角色人格提示")
        self.assertEqual([m.payload for m in original.context.materials if m.owner == "conversation.host_input"],
                         ["输入 event-1", "输入 event-2"])
        self.assertEqual([e.text for e in self.sent], ["第一段", "第二段"])
        self.assertEqual(len(app.messages.history(self.context(app, batch)).unwrap()["items"]), 2)
        self.assertEqual(host.receive(first).unwrap(), batch)
        await asyncio.sleep(0.01)
        self.assertEqual(len(self.called), 1)

    async def test_wall_clock_timer_requires_no_manual_seal_or_worker_calls(self):
        current = lambda: datetime.now(timezone.utc)
        self.receipt_now = current
        host = await self.start(self.host(policy=ConversationPolicy(0.01, 0.05, 180, 0), now=current))
        batch = host.receive(self.event(at=current())).unwrap()
        self.assertEqual((await self.done(host.application, batch))["state"], "complete")
        self.assertEqual(len(self.sent), 2)

    async def test_bounded_debounce_does_not_extend_forever(self):
        host = await self.start(self.host(policy=ConversationPolicy(0.6, 1.5, 180, 0)))
        app = host.application
        batch = host.receive(self.event()).unwrap()
        for i in range(3):
            self.advance(app, 0.4)
            self.assertEqual(host.receive(self.event(str(i))).unwrap(), batch)
        self.assertEqual(app.flow.row(batch)["due_at"], (NOW + timedelta(seconds=1.5)).isoformat(timespec="microseconds"))
        self.advance(app, 0.3)
        self.assertEqual((await self.done(app, batch))["state"], "complete")
        self.assertEqual(sum(m.owner == "conversation.host_input" for m in self.called[0].original.context.materials), 4)

    async def test_full_batch_and_prompt_change_start_ordered_successors(self):
        host = await self.start()
        app = host.application
        batch = host.receive(self.event()).unwrap()
        for i in range(1, 32):
            self.assertEqual(host.receive(self.event(str(i))).unwrap(), batch)
        next_batch = host.receive(self.event("overflow")).unwrap()
        third = host.receive(self.event("changed", prompt="另一份原角色提示")).unwrap()
        self.assertEqual(len({batch, next_batch, third}), 3)
        self.advance(app)
        # The third batch waits for a full 32-input batch and another successor,
        # including their governed generation and six ordered delivery parts.
        self.assertEqual((await self.done(app, third, seconds=10))["state"], "complete")
        self.assertEqual([sum(m.owner == "conversation.host_input" for m in e.original.context.materials) for e in self.called], [32, 1, 1])
        self.assertEqual(self.called[-1].original.role_instruction, "另一份原角色提示")

    async def test_input_during_generation_does_not_restart_it_and_other_routes_can_progress(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def model(envelope):
            if envelope.original.event.event_id == "first":
                entered.set()
                await release.wait()
            return answered(envelope.original.event.event_id)
        self.model_behavior = model
        host = await self.start(self.host(policy=ConversationPolicy(0, 0, 180, 0)))
        app = host.application
        first = host.receive(self.event("first")).unwrap()
        await asyncio.wait_for(entered.wait(), 2)
        second = host.receive(self.event("second")).unwrap()
        other_scope = replace(SCOPE, native_scope_id="other", conversation_id="other", session_id="other")
        self.grant(self.important, other_scope, "text")
        other = host.receive(self.event("other", scope=other_scope)).unwrap()
        await self.done(app, other)
        self.assertEqual([e.original.event.event_id for e in self.called], ["first", "other"])
        release.set()
        await self.done(app, second)
        self.assertEqual([e.text for e in self.sent], ["other", "first", "second"])
        self.assertEqual(app.flow.row(first)["state"], "complete")

    async def test_two_bots_same_native_message_keep_separate_role_and_delivery(self):
        scope = replace(SCOPE, bot_id="bot-b", account_id="login-b")
        principal = replace(self.important.principal, bot_id="bot-b", account_id="login-b")
        self.identity.bind(binding_id="bot-b-user", principal=principal, subject=self.important.subject,
                           valid_from=NOW, expected_revision=self.identity.revision)
        actor = self.identity.resolve_authenticated(principal, observed_at=NOW).unwrap()
        self.personas.set_binding(PersonaBinding("bot-b-role", PersonaSelector(INSTALLATION, SelectorKind.BOT, bot_id="bot-b"),
            SubjectRef(INSTALLATION, SubjectKind.BOT, "persona-b"), 0, NOW, "fixture", "admin"), expected_revision=0, observed_at=NOW)
        self.grant(actor, scope, "text")
        host = await self.start()
        app = host.application
        a = host.receive(self.event()).unwrap()
        b = host.receive(self.event(scope=scope, actor=actor, prompt="Bot B 的人格")).unwrap()
        self.assertNotEqual(a, b)
        self.assertFalse(app.cancel(self.context(app, b), a).is_success)
        self.advance(app)
        await self.done(app, a)
        await self.done(app, b)
        self.assertEqual({e.original.role_instruction for e in self.called}, {"原角色人格提示", "Bot B 的人格"})
        self.assertEqual({e.context.scope.bot_id for e in self.sent}, {"bot-a", "bot-b"})

    async def test_revocation_before_debounce_blocks_generation(self):
        host = await self.start()
        app = host.application
        batch = host.receive(self.event()).unwrap()
        self.identity.revoke_grant("grant-1", expected_revision=self.identity.revision, revoked_at=NOW)
        self.advance(app)
        self.assertEqual((await self.done(app, batch))["state"], "blocked")
        self.assertEqual(self.called, [])
        self.assertEqual(self.sent, [])

    async def test_persona_switch_invalidates_old_batch_without_blocking_new_persona(self):
        host = await self.start()
        app = host.application
        old = host.receive(self.event()).unwrap()
        self.advance(app, 0.1)
        self.personas.set_binding(replace(self.personas.binding("base"), revision=2,
            role=SubjectRef(INSTALLATION, SubjectKind.BOT, "persona-b")), expected_revision=1, observed_at=self.clock.now())
        new = host.receive(self.event("new", prompt="新角色人格")).unwrap()
        self.advance(app)
        await self.done(app, new)
        self.assertEqual(app.flow.row(old)["reason"], "role_binding_changed")
        self.assertEqual([e.original.role_instruction for e in self.called], ["新角色人格"])

    async def test_withdraw_removes_only_that_input_before_seal(self):
        host = await self.start()
        app = host.application
        batch = host.receive(self.event()).unwrap()
        host.receive(self.event("keep")).unwrap()
        event_key = app.runtime.read_one("SELECT event_key FROM conversation_inputs WHERE batch_id = ? AND position = 1", (batch,))["event_key"]
        app.withdraw(self.context(app, batch), event_key).unwrap()
        self.advance(app)
        await self.done(app, batch)
        self.assertEqual([m.payload for m in self.called[0].original.context.materials if m.owner == "conversation.host_input"], ["输入 keep"])
        self.assertEqual(host.receive(self.event(at=NOW)).failure.code, "message_withdrawn")

    async def test_cancel_while_spacing_retains_first_history_and_discards_queue_entry(self):
        host = await self.start(self.host(policy=ConversationPolicy(0, 0, 180, 5)))
        app = host.application
        batch = host.receive(self.event()).unwrap()
        await self.until(lambda: app.flow.row(batch)["reason"] == "part_spacing")
        app.cancel(self.context(app, batch), batch).unwrap()
        self.advance(app, 10)
        await asyncio.sleep(0.02)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(app.flow.row(batch)["state"], "closed")
        self.assertEqual(len(app.messages.history(self.context(app, batch)).unwrap()["items"]), 1)
        self.assertEqual(app.runtime.read_one("SELECT COUNT(*) n FROM execution_tasks WHERE state = 'created'", ())["n"], 0)

    async def test_startup_cas_is_atomic_when_model_consumer_conflicts(self):
        host = self.host()
        result = await host.application.start(expected_task_generation=0, expected_model_generation=7)
        self.assertEqual(result.failure.code, "consumer_generation_conflict")
        self.assertIsNone(self.runtime.read_one("SELECT * FROM task_supervisor", ()))
        self.assertIsNone(host.application.tasks.generation)
        await self.start(host)
        self.assertEqual((await host.application.start(expected_task_generation=1, expected_model_generation=1)).failure.code,
                         "host_already_started")

    async def test_new_supervisor_fences_old_intake_and_driver(self):
        old = await self.start()
        batch = old.receive(self.event()).unwrap()
        due = old.application.flow.row(batch)["due_at"]
        new = await self.start(self.host(runtime=self.open_runtime()), 1, 1)
        self.assertEqual(old.receive(self.event()).failure.code, "supervisor_fenced")
        with self.assertRaises(TaskError):
            old.application.flow.defer(batch)
        self.assertEqual(new.application.flow.row(batch)["due_at"], due)
        old.application.notify()
        await self.until(lambda: old.application.state == "faulted")
        batch = new.receive(self.event()).unwrap()
        self.advance(new.application)
        await self.done(new.application, batch)
        self.assertEqual(len(self.called), 1)

    async def test_failed_admission_rolls_back_published_source(self):
        host = await self.start()
        before = self.runtime.read_one("SELECT COUNT(*) n FROM sources", ())["n"]
        with patch.object(host.application.flow, "accept", return_value=Outcome.failed("message_capacity", "full")):
            self.assertEqual(host.receive(self.event()).failure.code, "message_capacity")
        self.assertEqual(self.runtime.read_one("SELECT COUNT(*) n FROM sources", ())["n"], before)
        self.assertEqual(self.runtime.read_one("SELECT COUNT(*) n FROM conversation_inputs", ())["n"], 0)

    async def test_idle_waits_for_events_and_closed_host_rejects_intake(self):
        host = self.host()
        self.assertEqual(host.receive(self.event()).failure.code, "host_not_running")
        with patch.object(host.application.flow, "advance", wraps=host.application.flow.advance) as advance:
            await self.start(host)
            await self.until(lambda: advance.call_count == 1)
            await asyncio.sleep(0.02)
            self.assertEqual(advance.call_count, 1)
        self.assertEqual((await host.application.close()).state, "stopped")
        self.assertEqual(host.receive(self.event()).failure.code, "host_not_running")
        self.assertEqual((await host.application.close()).state, "stopped")

    async def test_role_tools_are_assembled_and_return_to_original_role(self):
        async def model(envelope):
            return requested(call(title="实际便签", body="只是便签")) if not envelope.tool_history else answered("已经保存到便签")
        self.model_behavior = model
        host = await self.start()
        batch = host.receive(self.event()).unwrap()
        self.advance(host.application)
        await self.done(host.application, batch)
        self.assertEqual(len(self.called), 2)
        self.assertEqual({e.original.role_instruction for e in self.called}, {"原角色人格提示"})
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.runtime.read_one("SELECT state FROM conversation_tool_calls", ())["state"], "succeeded")


if __name__ == "__main__":
    unittest.main()
