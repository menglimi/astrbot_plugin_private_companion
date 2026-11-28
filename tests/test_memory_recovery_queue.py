"""Recovery queue progress under schedule changes, restart and accumulated work."""

from dataclasses import replace
from datetime import timedelta
import json
import unittest
from unittest.mock import patch

from companion_v2.execution.models import encode, stamp
from companion_v2.configuration.registry import FieldRegistry
from companion_v2.foundation.outcome import Outcome
from companion_v2.identity_policy.models import AuthorizationTier
from companion_v2.memory.models import Proposal
from companion_v2.memory.reflection import reflection_profile
from companion_v2.memory.reflection_management import ReflectionManagement
from companion_v2.model_gateway.routing import configuration_fields
from tests import test_memory_recovery as support
from tests.test_host_application import HostFixture
from tests.test_role_tool_loop import answered


class MemoryRecoveryQueueTests(HostFixture, unittest.IsolatedAsyncioTestCase):
    setup_reflection = support.MemoryRecoveryTests.setup_reflection
    configure = support.MemoryRecoveryTests.configure
    setup_manual = support.MemoryRecoveryTests.setup_manual

    def setUp(self):
        super().setUp()
        self.profiles += (reflection_profile("primary", max_output_tokens=100),)
        self.registry = FieldRegistry(version="memory-recovery-queue.v1", fields=configuration_fields(self.profiles))

    def prepare(self, app, context, request_id, **changes):
        return app.memory_reflection.prepare(context, request_id=request_id,
            start=self.clock.now() - timedelta(hours=1), end=self.clock.now(),
            role_instruction="原角色人格提示", **changes).unwrap()

    def manifest(self, app, context, batch_id):
        return json.loads(app.memory_reflection._row(context, batch_id)["manifest_json"])

    def pending(self, app, context, identifier):
        evidence = next(iter(app.memory.query._documents(context, "personal", "sources")[1].values()))["evidence"]
        return app.memory.writer.submit(context, Proposal("add", predicate="习惯" + identifier, object="不喝咖啡",
            representation="用户自述不喝咖啡。", evidence=(evidence,), defer_until_boundary=True),
            operation_key="queue-pending-" + identifier).unwrap()

    async def failed_schedule(self):
        host, context, _ = await self.setup_manual(prepare=False)
        app = host.application
        app.memory_schedule.configure(context, schedule_id="repair-day", expected_revision=0,
            role_instruction="原角色人格提示").unwrap()
        prepared = self.prepare(app, context, "scheduled-failure",
            schedule_ref={"schedule_id": "repair-day", "revision": 1})
        original = self.model_behavior
        async def invalid(_):
            return answered("{invalid")
        self.model_behavior = invalid
        result = (await app.memory_reflection.run(context, prepared["batch_id"])).unwrap()
        self.assertEqual(result["state"], "failed")
        self.model_behavior = original
        return host, context, prepared

    async def test_prepared_sources_stay_reserved_across_restart(self):
        host, context, first = await self.setup_manual()
        original = self.manifest(host.application, context, first["batch_id"])["evidence"]
        await host.application.close()
        host = await self.start(self.host(runtime=self.open_runtime()), task_generation=1, model_generation=1)
        app = host.application
        second = self.prepare(app, context, "second-preparation")
        manifest = self.manifest(app, context, second["batch_id"])
        first_sources = {encode(v) for v in self.manifest(app, context, first["batch_id"])["evidence"]}
        self.assertTrue(first_sources.isdisjoint(encode(v) for v in manifest["evidence"]))
        self.assertEqual(manifest["deferred_sources"], len(original))
        self.assertEqual(second["state"], "empty")
        before = len(self.called)
        (await app.memory_reflection.run(context, first["batch_id"])).unwrap()
        self.assertEqual(len(self.called), before + 1)
        self.assertEqual(len(app.runtime.read_all("SELECT * FROM memory_atoms", ())), 1)

    async def test_held_pending_items_do_not_hide_later_candidates(self):
        host, context, _ = await self.setup_manual(prepare=False)
        app = host.application
        for number in range(32):
            self.pending(app, context, str(number))
        first = self.prepare(app, context, "pending-first")
        self.assertEqual(len(self.manifest(app, context, first["batch_id"])["pending"]), 32)
        self.clock.advance(timedelta(milliseconds=1))
        new = self.pending(app, context, "new")
        second = self.prepare(app, context, "pending-second")
        manifest = self.manifest(app, context, second["batch_id"])
        self.assertEqual([v["operation_id"] for v in manifest["pending"]], [new["operation_id"]])
        first_sources = {encode(v) for v in self.manifest(app, context, first["batch_id"])["evidence"]}
        self.assertTrue(first_sources.isdisjoint(encode(v) for v in manifest["evidence"]))

    async def test_format_repair_keeps_its_original_pending_scope(self):
        host, context, _ = await self.setup_manual(prepare=False)
        app = host.application
        original = self.pending(app, context, "original")
        prepared = self.prepare(app, context, "pending-failure")
        async def invalid(_):
            return answered("{invalid")
        self.model_behavior = invalid
        (await app.memory_reflection.run(context, prepared["batch_id"])).unwrap()
        self.clock.advance(timedelta(milliseconds=1))
        new = self.pending(app, context, "unrelated-new")
        repaired = app.memory_reflection.retry(context, prepared["batch_id"], request_id="repair-pending").unwrap()
        manifest = self.manifest(app, context, repaired["batch_id"])
        self.assertEqual([v["operation_id"] for v in manifest["pending"]], [original["operation_id"]])
        other = self.prepare(app, context, "new-work")
        self.assertEqual([v["operation_id"] for v in self.manifest(app, context, other["batch_id"])["pending"]],
                         [new["operation_id"]])

    async def test_enabled_schedule_revision_can_repair_its_old_failure(self):
        host, context, prepared = await self.failed_schedule()
        app = host.application
        app.memory_schedule.configure(context, schedule_id="repair-day", expected_revision=1,
            role_instruction="更新后的角色提示", local_time="22:30").unwrap()
        repaired = app.memory_reflection.retry(context, prepared["batch_id"], request_id="after-update").unwrap()
        result = (await app.memory_reflection.run(context, repaired["batch_id"])).unwrap()
        self.assertEqual(result["state"], "complete", result)
        manifest = self.manifest(app, context, repaired["batch_id"])
        self.assertEqual(manifest["schedule_ref"]["revision"], 2)
        self.assertEqual(manifest["role_instruction"], "更新后的角色提示")
        self.assertEqual(self.manifest(app, context, prepared["batch_id"])["schedule_ref"]["revision"], 1)
        self.assertEqual(len(app.runtime.read_all("SELECT * FROM memory_atoms", ())), 1)

    async def test_paused_schedule_rejects_repair_without_linking_an_unusable_child(self):
        host, context, prepared = await self.failed_schedule()
        app = host.application
        app.memory_schedule.configure(context, schedule_id="repair-day", expected_revision=1,
            role_instruction="原角色人格提示", enabled=False).unwrap()
        before = len(self.called)
        retry = app.memory_reflection.retry(context, prepared["batch_id"], request_id="pause-then-resume")
        self.assertFalse(retry.is_success)
        self.assertEqual(retry.failure.code, "memory_schedule_changed")
        self.assertIsNone(app.memory_reflection.status(context, prepared["batch_id"]).unwrap()["repair_batch"])
        self.assertEqual(len(self.called), before)
        app.memory_schedule.configure(context, schedule_id="repair-day", expected_revision=2,
            role_instruction="原角色人格提示", enabled=True).unwrap()
        repaired = app.memory_reflection.retry(context, prepared["batch_id"], request_id="pause-then-resume").unwrap()
        self.assertEqual((await app.memory_reflection.run(context, repaired["batch_id"])).unwrap()["state"], "complete")

    async def test_schedule_change_after_repair_preparation_still_blocks_execution(self):
        host, context, prepared = await self.failed_schedule()
        app = host.application
        repaired = app.memory_reflection.retry(context, prepared["batch_id"], request_id="about-to-pause").unwrap()
        app.memory_schedule.configure(context, schedule_id="repair-day", expected_revision=1,
            role_instruction="原角色人格提示", enabled=False).unwrap()
        before = len(self.called)
        result = await app.memory_reflection.run(context, repaired["batch_id"])
        self.assertEqual(result.failure.code, "memory_schedule_changed")
        self.assertEqual(app.memory_reflection.status(context, repaired["batch_id"]).unwrap()["state"], "failed")
        self.assertEqual(len(self.called), before)

    async def test_expired_undispatched_task_is_repairable_after_restart(self):
        host, context, prepared = await self.setup_manual()
        app = host.application
        with patch.object(app.memory_reflection.runner, "run", return_value=Outcome.failed("fixture_queue_busy", "not yet admitted")):
            self.assertFalse((await app.memory_reflection.run(context, prepared["batch_id"])).is_success)
        before = len(self.called)
        await app.close()
        self.clock.advance(timedelta(minutes=4))
        host = await self.start(self.host(runtime=self.open_runtime()), task_generation=1, model_generation=1)
        app = host.application
        await app.memory_reflection.run(context, prepared["batch_id"])
        result = app.memory_reflection.status(context, prepared["batch_id"]).unwrap()
        self.assertEqual(result["state"], "failed")
        self.assertTrue(result["retryable"])
        self.assertEqual(len(self.called), before)
        repair = app.memory_reflection.retry(context, prepared["batch_id"], request_id="after-expiry").unwrap()
        self.assertEqual((await app.memory_reflection.run(context, repair["batch_id"])).unwrap()["state"], "complete")

    def seed_queue(self, app, context, template, count=55):
        row = app.memory_reflection._row(context, template["batch_id"])
        with app.runtime.transaction() as connection:
            connection.execute("UPDATE memory_reflections SET state = 'empty' WHERE batch_id = ?", (template["batch_id"],))
            for index in range(count):
                connection.execute("INSERT INTO memory_reflections VALUES (?, ?, ?, ?, ?, 'running', NULL, ?, NULL, ?, ?)",
                    (f"fixture-backlog-{index:03d}", row["owner_key"], row["request_hash"], row["context_json"],
                     row["manifest_json"], row["receipts_json"], row["created_at"], row["updated_at"]))

    async def test_queue_pages_beyond_fifty_and_keeps_position_when_anchor_is_resolved(self):
        host, context, template = await self.setup_manual()
        app = host.application
        self.seed_queue(app, context, template)
        manager = ReflectionManagement(app, self.core)
        first = manager.list(context, limit=50).unwrap()
        self.assertTrue(first["has_more"])
        self.assertEqual(first["next_cursor"], first["items"][-1]["batch_id"])
        with app.runtime.transaction() as connection:
            connection.execute("UPDATE memory_reflections SET state = 'complete' WHERE batch_id = ?", (first["next_cursor"],))
        second = manager.list(context, limit=50, after_id=first["next_cursor"]).unwrap()
        self.assertEqual(len(second["items"]), 5)
        self.assertFalse(second["has_more"])
        self.assertIsNone(second["next_cursor"])
        identifiers = [v["batch_id"] for page in (first, second) for v in page["items"]]
        self.assertEqual(len(set(identifiers)), 55)
        self.assertFalse(manager.list(context, after_id="not-an-authorized-batch").is_success)

    async def test_queue_cursor_cannot_cross_sessions_or_a_revoked_administrator(self):
        host, context, template = await self.setup_manual()
        app = host.application
        self.seed_queue(app, context, template, count=3)
        manager = ReflectionManagement(app, self.core)
        first = manager.list(context, limit=1).unwrap()
        other_scope = replace(context.scope, native_scope_id="other", conversation_id="other", session_id="other")
        self.grant(self.important, other_scope, "text")
        batch = host.receive(self.event("other", scope=other_scope)).unwrap()
        other_context = self.context(app, batch)
        self.assertEqual(manager.list(other_context, after_id=first["next_cursor"]).failure.code, "memory_reflection_unavailable")
        self.policy.set_tier(self.core.subject, AuthorizationTier.STANDARD,
            expected_revision=self.policy.revision, observed_at=self.clock.now())
        self.assertEqual(manager.list(context, after_id=first["next_cursor"]).failure.code, "core_access_required")

    async def test_reauthentication_keeps_original_queue_but_checks_current_permissions(self):
        host, context, prepared = await self.setup_manual()
        app = host.application
        self.grant(self.important, context.scope, "unrelated-capability")
        actor = self.identity.resolve_authenticated(context.actor.principal, observed_at=self.clock.now()).unwrap()
        fresh = replace(context, actor=actor)
        self.assertNotEqual(fresh, context)
        self.assertEqual(fresh.key, context.key)
        manager = ReflectionManagement(app, self.core)
        listing = manager.list(fresh).unwrap()
        self.assertIn(prepared["batch_id"], [v["batch_id"] for v in listing["items"]])
        self.assertEqual(self.prepare(app, fresh, "manual")["batch_id"], prepared["batch_id"])
        result = (await app.memory_reflection.run(fresh, prepared["batch_id"])).unwrap()
        self.assertEqual(result["state"], "complete")

    async def test_reauthentication_does_not_replace_an_unchanged_schedule(self):
        host, context, _ = await self.setup_reflection(prepare=False)
        app = host.application
        self.configure(app, context, reflection_enabled=True)
        app.memory_schedule.sync_configuration(context, "原角色人格提示")
        schedule = dict(app.runtime.read_one("SELECT * FROM memory_schedules", ()))
        prepared = self.prepare(app, context, "before-refresh",
            schedule_ref={"schedule_id": schedule["schedule_id"], "revision": schedule["revision"]})
        self.grant(self.important, context.scope, "unrelated-capability")
        fresh = replace(context, actor=self.identity.resolve_authenticated(context.actor.principal, observed_at=self.clock.now()).unwrap())
        app.memory_schedule.sync_configuration(fresh, "原角色人格提示")
        self.assertEqual(app.runtime.read_one("SELECT revision FROM memory_schedules", ())["revision"], schedule["revision"])
        self.assertEqual((await app.memory_reflection.run(fresh, prepared["batch_id"])).unwrap()["state"], "complete")

    async def test_auto_repair_completion_advances_coverage_without_deferring_itself(self):
        host, context, _ = await self.setup_reflection(prepare=False)
        app = host.application
        app.memory_schedule.configure(context, schedule_id="auto-repair-day", expected_revision=0,
            role_instruction="原角色人格提示").unwrap()
        original, count = self.model_behavior, 0
        async def repair(envelope):
            nonlocal count
            count += 1
            output = json.loads((await original(envelope)).draft.text)
            proposal = {**output["proposals"][0]["proposal"], "predicate": "早餐习惯"}
            if count == 1:
                output["proposals"].append({"item_id": "breakfast", "proposal": {**proposal, "confidence": "bad"}})
            else:
                material = next(m for m in envelope.original.context.materials if m.purpose == "format_repair")
                self.assertEqual(json.loads(material.payload)["allowed_item_ids"], ["breakfast"])
                if count == 2:
                    return answered("{invalid")
                output["proposals"] = [{"item_id": "breakfast", "proposal": proposal}]
            return answered(encode(output))

        self.model_behavior = repair
        schedule = dict(app.runtime.read_one("SELECT * FROM memory_schedules WHERE schedule_id = 'auto-repair-day'", ()))
        result = await app.memory_schedule._run(schedule, self.clock.now())
        self.assertEqual(result["state"], "complete")
        self.assertTrue(result["repair_batch"])
        self.assertEqual(result["deferred_sources"], 0)
        child = app.memory_reflection.status(context, result["repair_batch"]).unwrap()
        self.assertEqual(child["deferred_sources"], 0)
        current = app.runtime.read_one("SELECT * FROM memory_schedules WHERE schedule_id = 'auto-repair-day'", ())
        self.assertEqual(current["covered_until"], self.manifest(app, context, result["batch_id"])["end"])
        self.assertIsNone(current["pending_batch"])
        self.assertEqual(count, 3)
        self.assertEqual(set(result["receipts"]), {"preference", "breakfast"})

    async def test_repair_of_a_detached_window_rechecks_coverage(self):
        host, context, prepared = await self.failed_schedule()
        app = host.application
        batch_id = prepared["batch_id"]
        schedule = app.runtime.read_one("SELECT * FROM memory_schedules WHERE schedule_id = 'repair-day'", ())
        self.assertIsNone(schedule["pending_batch"])
        original_due = schedule["next_at"]
        repair = ReflectionManagement(app, self.core).prepare_retry(
            context, batch_id=batch_id, operation_key="repair-detached").unwrap()
        calls = len(self.called)
        self.assertEqual((await app.memory_reflection.run(context, repair["batch_id"])).unwrap()["state"], "complete")

        schedule = dict(app.runtime.read_one("SELECT * FROM memory_schedules WHERE schedule_id = 'repair-day'", ()))
        self.assertLess(schedule["next_at"], original_due)
        self.assertEqual(schedule["next_at"], stamp(self.clock.now() + timedelta(seconds=5)))
        original_evidence = {encode(item) for item in self.manifest(app, context, batch_id)["evidence"]}

        async def no_new_facts(envelope):
            return answered(encode({"summary": "暂无新的习惯需要记录。", "proposals": []}))

        self.model_behavior = no_new_facts
        self.clock.advance(timedelta(seconds=5))
        result = await app.memory_schedule._run(schedule, self.clock.now())
        self.assertIn(result["state"], ("complete", "empty"))
        current_evidence = self.manifest(app, context, result["batch_id"])["evidence"]
        self.assertTrue(original_evidence.isdisjoint(map(encode, current_evidence)))
        self.assertEqual(result["deferred_sources"], 0)
        covered = app.runtime.read_one("SELECT covered_until FROM memory_schedules WHERE schedule_id = 'repair-day'", ())
        self.assertEqual(covered["covered_until"], stamp(self.clock.now()))
        self.assertEqual(len(self.called), calls + 1 + (result["state"] == "complete"))

    async def test_reauthentication_does_not_restore_a_removed_reflection_permission(self):
        host, context, prepared = await self.setup_manual()
        app = host.application
        self.policy.set_tier(self.important.subject, AuthorizationTier.STANDARD,
            expected_revision=self.policy.revision, observed_at=self.clock.now())
        actor = self.identity.resolve_authenticated(context.actor.principal, observed_at=self.clock.now()).unwrap()
        fresh = replace(context, actor=actor)
        self.assertEqual(fresh.key, context.key)
        before = len(self.called)
        self.assertFalse((await app.memory_reflection.run(fresh, prepared["batch_id"])).is_success)
        self.assertFalse(app.memory_reflection.retry(fresh, prepared["batch_id"], request_id="denied").is_success)
        self.assertEqual(len(self.called), before)
        manager = ReflectionManagement(app, self.core)
        self.assertEqual(manager.list(fresh, batch_id=prepared["batch_id"]).unwrap()["state"], "prepared")

    async def test_rebound_account_cannot_resume_the_original_batch(self):
        host, context, prepared = await self.setup_manual()
        app = host.application
        self.identity.bind(binding_id="replacement-binding", principal=context.actor.principal,
            subject=context.actor.subject, valid_from=self.clock.now(), expected_revision=self.identity.revision)
        fresh = replace(context, actor=self.identity.resolve_authenticated(context.actor.principal, observed_at=self.clock.now()).unwrap())
        self.assertNotEqual(fresh.key, context.key)
        self.assertEqual(ReflectionManagement(app, self.core).list(fresh, batch_id=prepared["batch_id"]).failure.code,
                         "memory_reflection_unavailable")
        before = len(self.called)
        self.assertEqual((await app.memory_reflection.run(fresh, prepared["batch_id"])).failure.code, "memory_reflection_unavailable")
        self.assertEqual(len(self.called), before)


if __name__ == "__main__":
    unittest.main()
