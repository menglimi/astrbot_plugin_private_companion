"""Selection continuation through the real local host, with synthetic providers."""

from dataclasses import replace
from datetime import timedelta
import json
import unittest

from companion_v2.configuration.registry import FieldRegistry
from companion_v2.execution.models import encode, stamp
from companion_v2.identity_policy.models import AuthorizationTier
from companion_v2.memory import codec
from companion_v2.memory.models import Proposal
from companion_v2.memory.reflection import reflection_profile
from companion_v2.memory.reflection_management import ReflectionManagement
from companion_v2.model_gateway.routing import configuration_fields
from tests import test_memory_recovery as support
from tests import test_memory_startup_recovery as startup
from tests.test_host_application import HostFixture
from tests.test_role_tool_loop import answered


class MemorySelectionHostTests(HostFixture, unittest.IsolatedAsyncioTestCase):
    setup_reflection = support.MemoryRecoveryTests.setup_reflection
    setup_manual = support.MemoryRecoveryTests.setup_manual
    configure = support.MemoryRecoveryTests.configure
    restart = startup.MemoryStartupRecoveryTests.restart
    recovered = startup.MemoryStartupRecoveryTests.recovered

    def setUp(self):
        super().setUp()
        self.profiles += (reflection_profile("primary", max_output_tokens=100),)
        self.registry = FieldRegistry(version="memory-selection-test.v1", fields=configuration_fields(self.profiles))

    def row(self, app, batch_id):
        return app.runtime.read_one("SELECT * FROM memory_reflections WHERE batch_id = ?", (batch_id,))

    def manifest(self, app, batch_id):
        return json.loads(self.row(app, batch_id)["manifest_json"])

    def schedule(self, app):
        return dict(app.runtime.read_one("SELECT * FROM memory_schedules WHERE schedule_id = 'selection-day'", ()))

    def configure_day(self, app, context):
        app.memory_schedule.configure(context, schedule_id="selection-day", expected_revision=0,
            role_instruction="原角色人格提示", zone="UTC", local_time="00:02").unwrap()

    def prepare(self, app, context, request_id="selection"):
        return app.memory_reflection.prepare(context, request_id=request_id,
            start=self.clock.now() - timedelta(hours=1), end=self.clock.now(), role_instruction="原角色人格提示").unwrap()

    async def cold(self):
        host, context, _ = await self.setup_reflection(prepare=False)
        for number in range(4):
            batch = host.receive(self.event("extra-" + str(number))).unwrap()
        self.advance(host.application)
        await self.done(host.application, batch)
        host.application.memory.query.recall_index.sync_items = 1
        return host.application, context

    def add_facts(self, app, context, count=7):
        event = json.loads(app.runtime.read_one("SELECT event_json FROM conversation_inputs ORDER BY rowid LIMIT 1", ())[0])
        evidence = app.memory.writer.access.capture(context, codec.source(event["source"]))
        for number in range(count):
            app.memory.writer.submit(context, Proposal("add", predicate="咖啡习惯" + str(number), object="不喝咖啡",
                representation="用户说不喝咖啡", evidence=(evidence,)), operation_key="fact-" + str(number)).unwrap()

    async def test_daily_wakes_finish_cold_selection_before_one_model_call(self):
        app, context = await self.cold()
        self.configure_day(app, context)
        calls = len(self.called)
        self.advance(app, 118)
        await self.until(lambda: "selection-day" in app.memory_schedule.results)
        first = app.memory_schedule.results["selection-day"]
        self.assertEqual(first["state"], "selecting")
        self.assertEqual(len(self.called), calls)
        self.assertIsNone(self.row(app, first["batch_id"])["task_id"])
        for _ in range(20):
            result = app.memory_schedule.results.pop("selection-day")
            if result["state"] == "complete":
                break
            self.assertEqual(self.schedule(app)["pending_batch"], first["batch_id"])
            self.advance(app)
            await self.until(lambda: "selection-day" in app.memory_schedule.results)
        self.assertEqual(result["state"], "complete")
        self.assertEqual(len(self.called), calls + 1)
        self.assertIsNone(self.schedule(app)["pending_batch"])
        self.assertEqual(self.schedule(app)["covered_until"], self.manifest(app, first["batch_id"])["end"])

    async def test_restart_resumes_same_daily_selection_before_tomorrow(self):
        app, context = await self.cold()
        self.configure_day(app, context)
        first = await app.memory_schedule._run(self.schedule(app), self.clock.now())
        self.assertEqual(first["state"], "selecting")
        calls = len(self.called)
        app = await self.restart(app)
        await self.recovered(app)
        self.assertEqual(len(self.called), calls)
        self.assertEqual(self.schedule(app)["pending_batch"], first["batch_id"])
        self.assertEqual(self.schedule(app)["next_at"], stamp(self.clock.now() + timedelta(seconds=1)))
        self.advance(app)
        await self.until(lambda: self.row(app, first["batch_id"])["state"] == "complete")
        self.assertEqual(len(self.called), calls + 1)

    async def test_schedule_pause_during_selection_prevents_dispatch(self):
        app, context = await self.cold()
        self.configure_day(app, context)
        first = await app.memory_schedule._run(self.schedule(app), self.clock.now())
        calls = len(self.called)
        app.memory_schedule.configure(context, schedule_id="selection-day", expected_revision=1,
            role_instruction="原角色人格提示", enabled=False).unwrap()
        result = await app.memory_reflection.run(context, first["batch_id"])
        self.assertEqual(result.failure.code, "memory_schedule_changed")
        self.assertEqual(self.row(app, first["batch_id"])["state"], "failed")
        self.assertEqual(len(self.called), calls)

    async def test_revoked_permission_during_selection_prevents_dispatch(self):
        app, context = await self.cold()
        first = self.prepare(app, context)
        self.assertEqual(first["state"], "selecting")
        self.policy.set_tier(self.important.subject, AuthorizationTier.STANDARD,
            expected_revision=self.policy.revision, observed_at=self.clock.now())
        context = replace(context, actor=self.identity.resolve_authenticated(context.actor.principal, observed_at=self.clock.now()).unwrap())
        calls = len(self.called)
        self.assertFalse((await app.memory_reflection.run(context, first["batch_id"])).is_success)
        self.assertIsNone(self.row(app, first["batch_id"])["task_id"])
        self.assertEqual(len(self.called), calls)

    async def test_auto_repair_selection_retains_schedule_and_resumes_after_restart(self):
        host, context, _ = await self.setup_reflection(prepare=False)
        app = host.application
        self.add_facts(app, context)
        self.configure_day(app, context)
        original, calls = self.model_behavior, len(self.called)
        async def invalidate(envelope):
            app.memory.query.index.clear()
            app.memory.query.fact_index.sync_items = 1
            return answered("{invalid")
        self.model_behavior = invalidate
        result = await app.memory_schedule._run(self.schedule(app), self.clock.now())
        self.assertEqual(result["state"], "failed")
        child = result["repair_batch"]
        self.assertEqual(self.row(app, child)["state"], "selecting")
        self.assertEqual(self.schedule(app)["pending_batch"], result["batch_id"])
        self.assertEqual(len(self.called), calls + 1)
        self.assertEqual(self.manifest(app, child)["evidence"], self.manifest(app, result["batch_id"])["evidence"])
        self.model_behavior = original
        app = await self.restart(app)
        await self.recovered(app)
        self.assertEqual(len(self.called), calls + 1)
        self.assertEqual(self.schedule(app)["pending_batch"], result["batch_id"])
        self.advance(app)
        await self.until(lambda: self.row(app, result["batch_id"])["state"] == "complete")
        self.assertEqual(len(self.called), calls + 2)
        await app.memory_reflection.run(context, result["batch_id"])
        self.assertEqual(len(self.called), calls + 2)

    async def test_explicit_repair_keeps_original_materials_while_preparing_facts(self):
        host, context, _ = await self.setup_manual(prepare=False)
        app = host.application
        self.add_facts(app, context)
        first = self.prepare(app, context)
        original = self.model_behavior
        async def invalid(envelope):
            return answered("{invalid")
        self.model_behavior = invalid
        await app.memory_reflection.run(context, first["batch_id"])
        self.model_behavior = original
        app.memory.query.index.clear()
        app.memory.query.fact_index.sync_items = 1
        manager = ReflectionManagement(app, self.core)
        repair = manager.prepare_retry(context, batch_id=first["batch_id"], operation_key="repair").unwrap()
        self.assertEqual(repair["state"], "selecting")
        self.assertEqual(manager.prepare_retry(context, batch_id=repair["batch_id"], operation_key="continue").unwrap()["batch_id"], repair["batch_id"])
        calls = len(self.called)
        for _ in range(20):
            result = (await app.memory_reflection.run(context, first["batch_id"])).unwrap()
            if result["state"] == "complete":
                break
            self.assertEqual(len(self.called), calls)
        self.assertEqual(result["state"], "complete")
        self.assertEqual(len(self.called), calls + 1)
        self.assertEqual(self.manifest(app, repair["batch_id"])["evidence"], self.manifest(app, first["batch_id"])["evidence"])

    async def test_arrival_during_model_does_not_advance_covered_window(self):
        host, context, _ = await self.setup_reflection(prepare=False)
        app = host.application
        self.configure_day(app, context)
        before = self.schedule(app)["covered_until"]
        original = self.model_behavior
        async def late(envelope):
            response = await original(envelope)
            host.receive(self.event("late", at=self.clock.now() - timedelta(seconds=1))).unwrap()
            return response
        self.model_behavior = late
        result = await app.memory_schedule._run(self.schedule(app), self.clock.now())
        self.assertEqual(result["state"], "complete")
        self.assertTrue(result["continuation_needed"])
        self.assertEqual(self.schedule(app)["covered_until"], before)
        self.assertEqual(self.schedule(app)["next_at"], stamp(self.clock.now() + timedelta(seconds=5)))

    async def test_repair_with_unknown_source_coverage_keeps_daily_window_open(self):
        app, context = await self.cold()
        self.configure_day(app, context)
        first = await app.memory_schedule._run(self.schedule(app), self.clock.now())
        before = self.schedule(app)["covered_until"]
        self.assertIsNone(first["remaining_sources"])
        with app.runtime.transaction() as db:
            db.execute("UPDATE memory_reflections SET state = 'failed', result_json = ? WHERE batch_id = ?",
                       (encode({"summary": None, "issues": [{"code": "memory_settings_changed", "path": "$"}]}), first["batch_id"]))
        repair = app.memory_reflection.retry(context, first["batch_id"], request_id="repair-selection").unwrap()
        async def no_op(envelope):
            return answered(encode({"summary": "本批材料不足", "proposals": [], "pending_decisions": []}))
        self.model_behavior = no_op
        await app.memory_reflection.run(context, repair["batch_id"])
        result = app.memory_reflection.status(context, first["batch_id"]).unwrap()
        self.assertEqual(result["state"], "complete")
        app.memory_schedule._settle(self.schedule(app), first["batch_id"], result, self.clock.now())
        self.assertEqual(self.schedule(app)["covered_until"], before)
        self.assertEqual(self.schedule(app)["next_at"], stamp(self.clock.now() + timedelta(seconds=5)))
