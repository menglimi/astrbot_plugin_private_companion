from datetime import timedelta
import json
import unittest
from dataclasses import replace
from unittest.mock import patch

from companion_v2.configuration.registry import FieldRegistry
from companion_v2.configuration.models import ConfigLayer, ConfigTarget
from companion_v2.execution.models import TaskSpec, digest, encode
from companion_v2.execution.runner import RunResult
from companion_v2.identity_policy.models import AuthorizationTier
from companion_v2.memory.management import MemoryManagement
from companion_v2.memory.models import AtomRef, owner_key
from companion_v2.memory.reflection import KIND, reflection_profile
from companion_v2.foundation.outcome import Outcome
from companion_v2.model_gateway.routing import configuration_fields
from tests.test_host_application import HostFixture
from tests.test_role_tool_loop import answered


class MemoryReflectionTests(HostFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.profiles += (reflection_profile("primary", max_output_tokens=100),)
        self.registry = FieldRegistry(version="memory-reflection-test.v1", fields=configuration_fields(self.profiles))

    def enable_reflection(self, app, context):
        """把每日反思显式打开，作为调度类用例的前提。

        `memory.reflection_enabled` 的默认值会随产品决定变化（它现在默认开），而调度
        用例验的是「排定后会跑、能暂停」，与默认值无关。写死前提，失败时才真的是调度坏了。
        """
        self.set_reflection(app, context, True)

    def disable_reflection(self, app, context):
        self.set_reflection(app, context, False)

    def set_reflection(self, app, context, enabled):
        # 每次用一个不同的操作者名：self.person() 以名称为 binding_id，
        # 同名再建会撞 "binding_id already exists"。
        operator = self.person("reflection-admin-" + ("on" if enabled else "off"),
                               AuthorizationTier.CORE)
        manager = MemoryManagement(app.memory, operator)
        settings = manager.settings(context).unwrap()
        manager.patch_settings(context, operation_key="reflection-test-" + str(enabled),
            expected_edit_token=settings["edit_token"],
            changes={"memory.reflection_enabled": enabled}).unwrap()

    async def setup_reflection(self, *, prepare=True):
        async def respond(envelope):
            if envelope.original.output_format != "memory_reflection":
                return answered("知道了。")
            source = json.loads(next(m.payload for m in envelope.original.context.materials if m.purpose == "reflection_source"))
            return answered(encode({"summary": "对方明确说自己不喝咖啡，可以保留这个偏好。", "proposals": [
                {"item_id": "preference", "proposal": {"operation": "add", "predicate": "饮用偏好", "object": "不喝咖啡",
                    "representation": "用户自述不喝咖啡。", "single_value": True, "evidence_handles": [source["handle"]]}}]}))
        self.model_behavior = respond
        host = await self.start()
        batch = host.receive(self.event("coffee", text="我平时不喝咖啡")).unwrap()
        self.advance(host.application)
        await self.done(host.application, batch)
        context = self.context(host.application, batch)
        if not prepare:
            return host, context, None
        prepared = host.application.memory_reflection.prepare(context, request_id="daily",
            start=self.clock.now() - timedelta(hours=1), end=self.clock.now(), role_instruction="原角色人格提示").unwrap()
        return host, context, prepared

    async def test_reflection_persists_per_item_receipts_and_does_not_repeat_model(self):
        host, context, prepared = await self.setup_reflection()
        reflection = host.application.memory_reflection
        result = (await reflection.run(context, prepared["batch_id"])).unwrap()
        self.assertEqual(result["state"], "complete", result)
        self.assertEqual(result["receipts"]["preference"]["state"], "persisted")
        before = len(self.called)
        self.assertEqual((await reflection.run(context, prepared["batch_id"])).unwrap(), result)
        self.assertEqual(len(self.called), before)
        next_batch = reflection.prepare(context, request_id="incremental", start=self.clock.now() - timedelta(hours=1),
            end=self.clock.now(), role_instruction="原角色人格提示").unwrap()
        self.assertEqual(next_batch["state"], "empty")

    async def test_reflection_creates_an_anniversary_only_from_user_evidence(self):
        host, context, prepared = await self.setup_reflection()

        async def respond(envelope):
            if envelope.original.output_format != "memory_reflection":
                return answered("知道了。")
            source = json.loads(next(m.payload for m in envelope.original.context.materials
                                     if m.purpose == "reflection_source"))
            return answered(encode({
                "summary": "今天用户明确说起了第一次告白，可以记作互动纪念日。",
                "proposals": [],
                "pending_decisions": [],
                "anniversaries": [{
                    "label": "第一次认真说出喜欢",
                    "reason": "用户原话明确说出今天第一次向角色告白。",
                    "confidence": 0.92,
                    "evidence_handle": source["handle"],
                }],
            }))
        self.model_behavior = respond
        reflection = host.application.memory_reflection
        result = (await reflection.run(context, prepared["batch_id"])).unwrap()
        self.assertEqual(result["state"], "complete", result)
        rows = host.application.runtime.read_all(
            "SELECT label, state FROM relationship_anniversaries", ())
        self.assertEqual([(row["label"], row["state"]) for row in rows],
                         [("第一次认真说出喜欢", "active")])
        first = (await reflection.run(context, prepared["batch_id"])).unwrap()
        self.assertEqual(first["state"], "complete")
        self.assertEqual(len(host.application.runtime.read_all(
            "SELECT * FROM relationship_anniversaries", ())), 1)

    async def test_configured_model_wait_and_fallback_fit_inside_the_reflection_task(self):
        host, context, prepared = await self.setup_reflection()
        app = host.application
        group = "model.memory.reflect"
        target = ConfigTarget(context.scope.installation_id, ConfigLayer.INSTALLATION)
        snapshot = app.configuration.read(self.core, target=target, group_id=group,
                                          observed_at=self.clock.now()).unwrap()
        app.configuration.patch(self.core, target=target, group_id=group,
            expected_edit_token=snapshot.edit_token, changes={group + ".timeout_seconds": 240},
            operation=self.operation(target.authorization_scope,
                                     owner=app.configuration.registry.group(group)[0].owner_id),
            observed_at=self.clock.now()).unwrap()

        result = (await app.memory_reflection.run(context, prepared["batch_id"])).unwrap()

        self.assertEqual(result["state"], "complete")
        row = app.memory_reflection._row(context, prepared["batch_id"])
        task = app.memory_reflection.tasks.read(context, row["task_id"]).unwrap()
        duration = (task.spec.deadline_at - task.spec.not_before).total_seconds()
        self.assertGreaterEqual(duration, 2 * 240)
        definition = app.memory_reflection.tasks.registrations[KIND].definition
        self.assertGreaterEqual(definition.timeout_seconds, duration)
        self.assertEqual(len(self.sent), 1)  # Private reflection does not send its summary.

    async def test_reflection_redacts_summary_and_staged_proposal_before_durable_storage(self):
        host, context, prepared = await self.setup_reflection()
        original = self.model_behavior
        secret = "sk-" + "S" * 28
        async def respond(envelope):
            response = await original(envelope)
            output = json.loads(response.draft.text)
            output["summary"] = "测试 api_key=" + secret
            output["proposals"][0]["proposal"].update(
                predicate="api_key", object=secret, representation="测试 api_key=" + secret,
            )
            return answered(encode(output))
        self.model_behavior = respond
        result = (await host.application.memory_reflection.run(context, prepared["batch_id"])).unwrap()
        self.assertEqual(result["state"], "complete", result)
        stored = host.application.runtime.read_one("SELECT result_json FROM memory_reflections WHERE batch_id = ?",
                                                   (prepared["batch_id"],))["result_json"]
        self.assertNotIn(secret, stored)
        self.assertIn("[REDACTED]", stored)

    async def test_withdrawal_during_model_prevents_all_memory_writes(self):
        host, context, prepared = await self.setup_reflection()
        app = host.application
        previous = self.model_behavior
        async def withdraw(envelope):
            response = await previous(envelope)
            if envelope.original.output_format == "memory_reflection":
                source = next(m.source for m in envelope.original.context.materials if m.purpose == "reflection_source")
                app.memory.writer.sources.revoke(source, expected_governance_revision=1, observed_at=self.clock.now()).unwrap()
            return response
        self.model_behavior = withdraw
        result = (await app.memory_reflection.run(context, prepared["batch_id"])).unwrap()
        self.assertNotEqual(result["state"], "complete")
        self.assertEqual(app.runtime.read_all("SELECT * FROM memory_atoms", ()), ())
        count = len(self.called)
        self.assertFalse((await app.memory_reflection.run(context, prepared["batch_id"])).is_success)
        self.assertEqual(len(self.called), count)

    async def test_new_correction_fences_a_prepared_old_reflection(self):
        host, context, prepared = await self.setup_reflection()
        app = host.application
        from companion_v2.memory.models import Proposal
        event = host.receive(self.event("explicit", text="记住，我现在喝咖啡了")).unwrap()
        source = json.loads(app.runtime.read_one("SELECT event_json FROM conversation_inputs WHERE batch_id = ?", (event,))["event_json"])["source"]
        from companion_v2.memory.codec import source as decode_source
        evidence = app.memory.writer.access.capture(context, decode_source(source))
        app.memory.writer.submit(context, Proposal("add", predicate="饮用偏好", object="喝咖啡", representation="用户现在喝咖啡。",
            evidence=(evidence,), single_value=True), operation_key="explicit").unwrap()
        result = await app.memory_reflection.run(context, prepared["batch_id"])
        self.assertEqual(result.failure.code, "memory_reflection_stale")

    async def test_persisted_daily_schedule_runs_on_host_wake_and_pauses(self):
        host, context, _ = await self.setup_reflection(prepare=False)
        app = host.application
        # 显式声明前提：这个用例验的是「已排定的日程会跑、能暂停」，不是「反思默认开」。
        # 依赖默认值会让它随默认值改动而失败，而失败信息看起来像调度坏了。
        self.enable_reflection(app, context)
        app.memory_schedule.sync_configuration(context, "原角色人格提示")
        schedule = app.memory_schedule.configure(context, schedule_id="daily", expected_revision=0,
            role_instruction="原角色人格提示", zone="UTC", local_time="00:02").unwrap()
        self.advance(app, 119)
        await self.until(lambda: "daily" in app.memory_schedule.results)
        self.assertEqual(app.memory_schedule.results["daily"]["state"], "complete")
        self.assertTrue(app.runtime.read_one("SELECT * FROM memory_schedules WHERE schedule_id = 'daily'", ()))
        paused = app.memory_schedule.configure(context, schedule_id="daily", expected_revision=schedule["revision"],
            role_instruction="原角色人格提示", zone="UTC", local_time="00:02", enabled=False).unwrap()
        self.assertFalse(paused["enabled"])
        # 「暂停这个日程」只停它自己，所以这里断言它自己的行确实停了。
        # 别断言 next_wake() 为 None：本用例手动建了 schedule_id="daily"，
        # 而配置同步还会建一条 id 为 "memory-daily-" + owner_key 的派生日程
        # （scheduling.py:48）——产品只会生成派生那一条，手动那条是本用例自己
        # 用内部 API 造的，两条并存是测试搭出来的场景，不是产品状态。
        row = app.runtime.read_one("SELECT enabled FROM memory_schedules WHERE schedule_id = 'daily'", ())
        self.assertEqual(row["enabled"], 0)
        # 真正决定 next_wake() 的是配置：关掉反思，两条路径都不该再排唤醒。
        self.disable_reflection(app, context)
        app.memory_schedule.sync_configuration(context, "原角色人格提示")
        self.assertIsNone(app.memory_schedule.next_wake())

    async def test_schedule_configuration_token_is_written_in_the_schedule_commit(self):
        host, context, _ = await self.setup_reflection(prepare=False)
        app = host.application
        self.enable_reflection(app, context)

        with patch.object(app.runtime, "transaction", wraps=app.runtime.transaction) as transaction:
            app.memory_schedule.sync_configuration(context, "角色提示")

        self.assertEqual(transaction.call_count, 1)
        row = app.runtime.read_one("SELECT settings_json FROM memory_schedules WHERE schedule_id = ?",
                                   ("memory-daily-" + owner_key(context),))
        self.assertIsNotNone(row)
        saved = json.loads(row["settings_json"])
        self.assertEqual(saved["configuration_token"], app.memory.settings.read(context)[1])

    async def test_schedule_pause_during_provider_response_fences_commit(self):
        host, context, _ = await self.setup_reflection(prepare=False)
        app = host.application
        app.memory_schedule.configure(context, schedule_id="fenced", expected_revision=0, role_instruction="角色提示").unwrap()
        prepared = app.memory_reflection.prepare(context, request_id="fenced", start=self.clock.now() - timedelta(hours=1),
            end=self.clock.now(), role_instruction="角色提示", schedule_ref={"schedule_id": "fenced", "revision": 1}).unwrap()
        previous = self.model_behavior
        async def paused(envelope):
            response = await previous(envelope)
            app.memory_schedule.configure(context, schedule_id="fenced", expected_revision=1, role_instruction="角色提示", enabled=False).unwrap()
            return response
        self.model_behavior = paused
        result = (await app.memory_reflection.run(context, prepared["batch_id"])).unwrap()
        self.assertNotEqual(result["state"], "complete")
        self.assertEqual(app.runtime.read_all("SELECT * FROM memory_atoms", ()), ())

    async def test_known_parse_failure_can_explicitly_retry_but_unknown_cannot(self):
        host, context, prepared = await self.setup_reflection()
        reflection = host.application.memory_reflection
        original = self.model_behavior
        async def invalid(envelope):
            return answered("{bad json")
        self.model_behavior = invalid
        result = (await reflection.run(context, prepared["batch_id"])).unwrap()
        self.assertEqual(result["state"], "failed")
        retry = reflection.retry(context, prepared["batch_id"], request_id="retry-1").unwrap()
        self.model_behavior = original
        self.assertEqual((await reflection.run(context, retry["batch_id"])).unwrap()["state"], "complete")
        with self.runtime.transaction() as connection:
            connection.execute("UPDATE memory_reflections SET state = 'running' WHERE batch_id = ?", (prepared["batch_id"],))
        count = len(self.called)
        self.assertEqual((await reflection.run(context, prepared["batch_id"])).failure.code, "memory_reflection_uncertain")
        self.assertEqual(reflection.retry(context, prepared["batch_id"], request_id="unsafe").failure.code, "memory_reflection_retry_denied")
        self.assertEqual(len(self.called), count)

    async def test_durable_model_output_resumes_without_another_model_call(self):
        host, context, prepared = await self.setup_reflection()
        app = host.application
        with patch.object(app.memory_reflection, "apply", return_value=Outcome.failed("interrupted", "simulated interruption after output persistence")):
            result = (await app.memory_reflection.run(context, prepared["batch_id"])).unwrap()
            self.assertEqual(result["state"], "modelled")
        count = len(self.called)
        await app.close()
        replacement = await self.start(self.host(runtime=self.open_runtime()), task_generation=1, model_generation=1)
        result = (await replacement.application.memory_reflection.run(context, prepared["batch_id"])).unwrap()
        self.assertEqual(result["state"], "complete")
        self.assertEqual(len(self.called), count)
        self.assertEqual(len(self.runtime.read_all("SELECT * FROM memory_atoms", ())), 1)

    async def test_unadmitted_attempt_reports_the_refusal_and_never_continues_the_batch(self):
        """A retained capacity refusal is not a result: the batch must not move."""
        host, context, prepared = await self.setup_reflection()
        app, batch_id = host.application, prepared["batch_id"]
        reflection = app.memory_reflection
        calls = len(self.called)
        now = reflection.tasks.now()
        task_id = "memory-extract-task-" + digest(batch_id)
        with app.runtime.transaction() as connection:
            connection.execute("UPDATE memory_reflections SET task_id = ? WHERE batch_id = ?", (task_id, batch_id))
        reflection.tasks.submit(TaskSpec(task_id, context, KIND, batch_id, batch_id,
                                         now, now + timedelta(minutes=5))).unwrap()
        queued = reflection.tasks.read(context, task_id).unwrap()
        self.assertEqual((queued.state, queued.attempt_no), ("created", 0))
        retained = RunResult(queued, "task_lane_full", refused="task_lane_full")
        with patch.object(reflection, "_after_run") as after, \
                patch.object(reflection.runner, "run", return_value=Outcome.success(retained)):
            outcome = await reflection.run(context, batch_id)
            after.assert_not_called()
        self.assertEqual(outcome.failure.code, "task_lane_full")
        self.assertEqual(len(self.called), calls)
        batch_row = app.runtime.read_one("SELECT state, receipts_json FROM memory_reflections WHERE batch_id = ?", (batch_id,))
        self.assertEqual(batch_row["state"], "prepared")
        self.assertIsNone(json.loads(batch_row["receipts_json"]).get("repair_batch"))
        self.assertEqual(self.tasks_ledger(app, task_id), ("created", 0))

    @staticmethod
    def tasks_ledger(app, task_id):
        row = app.runtime.read_one("SELECT state, attempt_no FROM execution_tasks WHERE task_id = ?", (task_id,))
        return row["state"], row["attempt_no"]

    async def test_daily_reflection_can_accept_a_pending_proposal_without_duplicating_it(self):
        host, context, _ = await self.setup_reflection(prepare=False)
        app = host.application
        from companion_v2.memory.models import Proposal
        evidence = next(iter(app.memory.query._documents(context, "personal", "sources")[1].values()))["evidence"]
        pending = app.memory.writer.submit(context, Proposal("add", predicate="饮用偏好", object="不喝咖啡", representation="用户不喝咖啡。",
            evidence=(evidence,), defer_until_boundary=True), operation_key="pending").unwrap()
        prepared = app.memory_reflection.prepare(context, request_id="pending-day", start=self.clock.now() - timedelta(hours=1),
            end=self.clock.now(), role_instruction="原角色人格提示").unwrap()
        async def decide(envelope):
            return answered(encode({"summary": "明确偏好可以留下。", "proposals": [], "pending_decisions": [
                {"operation_id": pending["operation_id"], "decision": "accept"}]}))
        self.model_behavior = decide
        result = (await app.memory_reflection.run(context, prepared["batch_id"])).unwrap()
        self.assertEqual(result["receipts"]["pending:" + pending["operation_id"]]["state"], "persisted")
        self.assertEqual(len(self.runtime.read_all("SELECT * FROM memory_atoms", ())), 1)
