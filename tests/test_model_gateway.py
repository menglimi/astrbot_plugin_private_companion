from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
import json
import unittest

from companion_v2.configuration.models import ConfigLayer, ConfigTarget
from companion_v2.configuration.registry import FieldRegistry
from companion_v2.configuration.service import ConfigurationService
from companion_v2.conversation.foreground import ModelDraft, PromptMaterial, PromptRequest, SealedForegroundContext
from companion_v2.conversation.models import InboundConversationEvent
from companion_v2.diagnostics.models import CheckStatus, DiagnosticContext, DiagnosticQuery
from companion_v2.diagnostics.service import DiagnosticService
from companion_v2.execution.models import Lane, ProviderBinding, TaskError, TaskRegistration, TaskResult, digest, encode, stamp
from companion_v2.execution.runner import LocalTaskRunner, TaskRun
from companion_v2.foundation.refs import SourceRef, SubjectKind, SubjectRef, VersionRef
from companion_v2.identity_policy.personas import PersonaBinding, PersonaSelector, SelectorKind
from companion_v2.model_gateway.budget import BudgetLedger
from companion_v2.model_gateway.failures import ModelFailure
from companion_v2.model_gateway.models import (Amount, BudgetPolicy, CacheSignature, CapturedModelOutput,
    LocalProvider, ModelTaskProfile, ProviderResponse, is_released)
from companion_v2.model_gateway.perception import capability_instruction
from companion_v2.model_gateway.routing import ModelRouter, configuration_fields
from companion_v2.model_gateway.service import LocalModelGateway
from companion_v2.sources.models import SourceRecord
from companion_v2.sources.repositories import SourceRepository
from tests.test_execution import ExecutionFixture
from tests.test_management_access import INSTALLATION, NOW, ROLE, SCOPE


class ModelFixture(ExecutionFixture):
    def setUp(self):
        super().setUp()
        self.profiles = (
            ModelTaskProfile("fixture.read", Lane.FOREGROUND, "primary", "Task supplement", "fallback", 10),
            ModelTaskProfile("fixture.background", Lane.BACKGROUND, "primary", "Background supplement", "fallback", 10),
        )
        self.registry = FieldRegistry(version="models.v1", fields=tuple(self.registry.fields.values()) + configuration_fields(self.profiles))
        self.configuration = ConfigurationService(self.runtime, self.registry)
        self.called = []
        self.responses = {}
        self.estimate = Amount(30, 100)
        self.budget_policy = BudgetPolicy(*(Amount(100, 1000) for _ in range(4)), background=Amount(30, 300))
        self.providers = tuple(self.provider(name) for name in ("primary", "fallback"))
        self.router = ModelRouter(self.configuration, self.profiles, self.providers)
        self.router.start(expected_generation=0)
        self.ledger = BudgetLedger(self.runtime, self.budget_policy)
        self.gateway = LocalModelGateway(self.router, self.ledger, self.tasks)

    def provider(self, name, reserve=None):
        async def generate(envelope):
            self.called.append((name, envelope))
            response = self.responses.get(name)
            if callable(response):
                return await response(envelope)
            return response or ProviderResponse("generated", ModelDraft("private draft", ()), Amount(15, 40), "receipt-" + name)

        return LocalProvider(ProviderBinding("model.text", name, "1", "local-1"), ("text",), 100,
                             reserve or (lambda envelope: self.estimate), generate)

    def gateway_with(self, *providers):
        self.providers = providers
        self.router = ModelRouter(self.configuration, self.profiles, self.providers)
        self.router.start(expected_generation=1)
        self.gateway = LocalModelGateway(self.router, self.ledger, self.tasks)

    def request(self, context=None):
        context = context or self.context
        identifier = "original-source:" + digest(context)
        source = SourceRef(identifier, "conversation", context.scope, VersionRef(identifier, 1), NOW)
        SourceRepository(self.runtime).publish(SourceRecord(source, context.audience,
            "input-hash", "private material"), observed_at=NOW).unwrap()
        event = InboundConversationEvent("native-event", context.actor.principal, context.scope, source, "text", "input-hash", NOW)
        sealed = SealedForegroundContext((PromptMaterial("conversation", "current_source", "private material", source),), (), ())
        return PromptRequest(event, "Original persona instruction", sealed, "Original reply instruction", context.role_binding)

    def model_run(self, identifier="task-1", *, context=None, kind="fixture.read"):
        claim = self.running(identifier, context=context, kind=kind)
        task = self.tasks.read(context or self.context, identifier).unwrap()
        return TaskRun(self.tasks, claim, task)

    def patch_model(self, *, target=None, kind="fixture.read", reset=(), **changes):
        target = target or self.window
        group = "model." + kind
        snapshot = self.configuration.read(self.core, target=target, group_id=group, observed_at=self.clock.now()).unwrap()
        return self.configuration.patch(self.core, target=target, group_id=group,
            operation=self.operation(target.authorization_scope), expected_edit_token=snapshot.edit_token,
            changes={group + "." + key: value for key, value in changes.items()},
            reset_to_inherit=tuple(group + "." + key for key in reset), observed_at=self.clock.now()).unwrap()

    def accounts(self):
        return self.runtime.read_all("SELECT * FROM model_budget_accounts", ())

    def call_rows(self):
        return self.runtime.read_all("SELECT * FROM model_calls ORDER BY task_id, position", ())

    def stage_plan(self, run):
        plan = self.router.resolve(run, self.request(run.task.spec.context))
        with self.runtime.transaction() as connection:
            self.router.adopt(run, plan)
            connection.execute("INSERT INTO model_requests VALUES (?, ?, ?, ?)",
                               (run.task.task_id, plan.audit["request_hash"], encode(plan.audit), stamp(self.clock.now())))


class ModelGatewayTests(ModelFixture, unittest.IsolatedAsyncioTestCase):
    async def test_captured_provider_overrides_and_independent_checks_keep_separate_rounds(self):
        ceiling = Amount(100_000, 1_000_000)
        self.ledger = BudgetLedger(self.runtime, BudgetPolicy(ceiling, ceiling, ceiling, ceiling, ceiling))
        self.gateway = LocalModelGateway(self.router, self.ledger, self.tasks)
        run = self.model_run(kind="fixture.background")
        request = self.request()
        session = self.gateway.open_session(run, request, independent_providers=("fallback", ""))

        first = await session.generate(session.plan.envelope.original)
        second = await session.generate(session.plan.envelope.original)

        self.assertEqual([name for name, _ in self.called], ["fallback", "primary"])
        self.assertTrue(first.draft)
        self.assertTrue(second.draft)
        rounds = self.runtime.read_all("SELECT round_no FROM model_rounds WHERE task_id=? ORDER BY round_no",
                                       (run.task.task_id,))
        self.assertEqual([row["round_no"] for row in rounds], [0, 1])
        with self.assertRaises(TaskError) as error:
            self.gateway.open_session(run, request)
        self.assertEqual(error.exception.code, "model_request_already_started")

    async def test_proactive_prompt_archive_keeps_only_the_latest_ten_model_wires(self):
        ceiling = Amount(100_000, 1_000_000)
        self.ledger = BudgetLedger(self.runtime, BudgetPolicy(ceiling, ceiling, ceiling, ceiling, ceiling))
        self.gateway = LocalModelGateway(self.router, self.ledger, self.tasks)

        async def archive(envelope):
            snapshot = [
                {"role": "system", "content": "active prompt"},
                {"role": "user", "content": [{"type": "image_url", "image_url": {
                    "url": "data:image/png;base64,private-image"}}]},
            ]
            self.assertIsNotNone(envelope.record_prompt_snapshot)
            envelope.record_prompt_snapshot(snapshot)
            return ProviderResponse("generated", ModelDraft("private draft", ()), Amount(15, 40), "receipt-primary")

        self.responses["primary"] = archive
        request = replace(self.request(), output_format="proactive_contact")
        task_ids = []
        for index in range(11):
            run = self.model_run(f"proactive-prompt-{index}")
            task_ids.append(run.task.task_id)
            completion = await self.gateway.generate(run, request)
            self.tasks.finish(run.claim, TaskResult("succeeded", result_ref=completion.call_id)).unwrap()

        rows = self.runtime.read_all("""SELECT task_id, prompt_hash, messages_json
            FROM proactive_prompt_snapshots ORDER BY snapshot_id""", ())
        self.assertEqual(len(rows), 10)
        self.assertNotIn(task_ids[0], {row["task_id"] for row in rows})
        self.assertEqual(rows[-1]["task_id"], task_ids[-1])
        saved = json.loads(rows[-1]["messages_json"])
        self.assertEqual(saved[0]["content"], "active prompt")
        self.assertEqual(saved[1]["content"][0]["image_url"]["url"], "[inline image payload omitted]")
        self.assertEqual(rows[-1]["prompt_hash"], digest(saved))

    async def test_fully_filtered_catalog_keeps_reasons_without_requiring_tool_capabilities(self):
        run = self.model_run()
        request = self.request()
        withheld = ({"action": "reaction.search", "reason": "reactions_disabled_for_scope"},)

        session = self.gateway.open_session(run, request, tool_filters=withheld)
        await session.generate(request)

        projection = self.gateway.observe(self.context, run.task.task_id).unwrap()
        self.assertEqual(projection["plan"]["tool_actions"], [])
        self.assertEqual(projection["plan"]["tool_filters"], list(withheld))
        self.assertEqual(len(self.called), 1)

    async def test_cache_signature_sink_persists_only_against_the_dispatched_call(self):
        signature = CacheSignature("a" * 64, "b" * 64, ("b" * 64,), "c" * 64, (), (), 0, True)

        async def respond(envelope):
            envelope.record_cache_signature(signature)
            return ProviderResponse("generated", ModelDraft("private draft", ()), Amount(15, 40), "receipt-primary")

        self.responses["primary"] = respond
        run = self.model_run()
        completion = await self.gateway.generate(run, self.request())
        row = self.runtime.read_one("SELECT signature_json FROM model_call_cache_signatures WHERE call_id = ?",
                                    (completion.call_id,))
        self.assertEqual(json.loads(row["signature_json"]), signature.to_record())

    async def test_unset_task_fallback_uses_the_profile_window_provider_after_a_task_override(self):
        profile = replace(self.profiles[0], default_fallback="")
        profiles = (profile, self.profiles[1])
        base_fields = tuple(field for field in self.registry.fields.values()
                            if not field.field_id.startswith("model.fixture."))
        registry = FieldRegistry(version="models.window-fallback.v1",
                                 fields=base_fields + configuration_fields(profiles))
        configuration = ConfigurationService(self.runtime, registry)
        router = ModelRouter(configuration, profiles, self.providers)
        generation = self.runtime.read_one(
            "SELECT generation FROM configuration_consumers WHERE consumer_id = 'model_gateway'", ())
        router.start(expected_generation=generation["generation"])

        before = configuration.read(self.core, target=self.window, group_id=profile.group,
                                    observed_at=self.clock.now()).unwrap()
        self.assertEqual(before.fields[profile.group + ".fallback"].value, "primary")
        self.assertFalse(before.fields[profile.group + ".fallback"].explicit)
        configuration.patch(self.core, target=self.window, group_id=profile.group,
            operation=self.operation(self.window.authorization_scope), expected_edit_token=before.edit_token,
            changes={profile.group + ".provider": "fallback"}, observed_at=self.clock.now()).unwrap()

        plan = router.resolve(self.model_run(), self.request())
        self.assertEqual([provider.binding.provider for provider in plan.providers], ["fallback", "primary"])

    async def test_inherited_role_provider_is_not_replaced_by_host_conversation_model(self):
        role = ConfigTarget(self.context.scope.installation_id, ConfigLayer.ROLE,
                            self.context.role_binding.role, bot_id=self.context.scope.bot_id)
        self.patch_model(target=role, provider="fallback", fallback="primary")
        self.router.default_provider_resolver = lambda context, profile: "primary"

        plan = self.router.resolve(self.model_run(), self.request())

        self.assertEqual([provider.binding.provider for provider in plan.providers], ["fallback", "primary"])
        self.assertEqual(plan.audit["fields"]["model.fixture.read.provider"]["source"]["layer"], "role")

    async def test_actual_local_call_keeps_original_persona_materials_and_prompt_provenance(self):
        run = self.model_run()
        request = self.request()
        completion = await self.gateway.generate(run, request)
        self.assertEqual(completion.draft.text, "private draft")
        self.assertTrue(completion.usage_known)
        name, envelope = self.called[0]
        self.assertEqual(name, "primary")
        self.assertIs(envelope.original, request)
        self.assertEqual(envelope.task_prompt, "Task supplement")
        self.assertEqual(envelope.max_output_tokens, 10)
        self.tasks.finish(run.claim, TaskResult("succeeded", result_ref=completion.call_id)).unwrap()
        projection = self.gateway.observe(self.context, "task-1").unwrap()
        self.assertEqual(projection["plan"]["fields"]["model.fixture.read.prompt"]["source"], "registry_default")
        self.assertEqual(projection["calls"][0]["actual_tokens"], 15)
        self.assertGreaterEqual(projection["calls"][0]["provider_latency_ms"], 0)
        self.assertTrue(all((a["held_tokens"], a["held_cost"], a["spent_tokens"], a["spent_cost"]) == (0, 0, 15, 40) for a in self.accounts()))
        encoded = json.dumps(projection)
        for private in ("private draft", "private material", "Original persona instruction", "Task supplement"):
            self.assertNotIn(private, encoded)

    async def test_configured_refusal_term_retries_the_task_fallback(self):
        self.patch_model(fallback_on_refusal=True, refusal_terms="denied by policy\nrequest blocked")
        self.responses["primary"] = ProviderResponse(
            "generated", ModelDraft("This request was denied by policy.", ()),
            Amount(12, 30), "primary:matched-refusal")

        completion = await self.gateway.generate(self.model_run(), self.request())

        self.assertEqual(completion.draft.text, "private draft")
        self.assertEqual([name for name, _ in self.called], ["primary", "fallback"])
        observed = self.gateway.observe(self.context, "task-1").unwrap()
        self.assertEqual(observed["calls"][0]["failure"]["code"], ModelFailure.REFUSAL.value)
        self.assertNotIn("denied by policy", json.dumps(observed["plan"]))

    async def test_contract_failure_with_known_usage_retries_the_fallback(self):
        # 结构化任务拿到非法 JSON，但用量已由 Provider 确认、也没有已发出的正文：
        # 换备用模型重试一次，不算“未知不可重试”。
        self.responses["primary"] = ProviderResponse("unknown", usage=Amount(12, 30),
            failure=ModelFailure.WIRE_DECODE, receipt_ref="primary:bad-json")

        completion = await self.gateway.generate(self.model_run(), self.request())

        self.assertEqual(completion.draft.text, "private draft")
        self.assertEqual([name for name, _ in self.called], ["primary", "fallback"])
        observed = self.gateway.observe(self.context, "task-1").unwrap()
        self.assertEqual(observed["calls"][0]["failure"]["code"], ModelFailure.WIRE_DECODE.value)

    async def test_contract_failure_without_usage_is_not_retried(self):
        self.responses["primary"] = ProviderResponse("unknown", failure=ModelFailure.WIRE_DECODE)

        with self.assertRaises(TaskError) as raised:
            await self.gateway.generate(self.model_run(), self.request())

        self.assertEqual(raised.exception.code, "model_usage_unknown")
        self.assertEqual([name for name, _ in self.called], ["primary"])

    async def test_configured_refusal_terms_do_not_intercept_when_disabled(self):
        self.patch_model(refusal_terms="cannot help with that")
        self.responses["primary"] = ProviderResponse(
            "generated", ModelDraft("I cannot help with that, but here is context.", ()),
            Amount(12, 30), "primary:normal-draft")

        completion = await self.gateway.generate(self.model_run(), self.request())

        self.assertEqual(completion.draft.text, "I cannot help with that, but here is context.")
        self.assertEqual([name for name, _ in self.called], ["primary"])

    async def test_provider_cache_receipt_details_survive_settlement_and_observation(self):
        usage = Amount(15, 40, input_tokens=12, cached_input_tokens=5,
                       cache_creation_tokens=2, output_tokens=3)
        self.responses["primary"] = ProviderResponse("generated", ModelDraft("private draft", ()),
                                                       usage, "cache-receipt")

        await self.gateway.generate(self.model_run(), self.request())

        row = self.call_rows()[0]
        self.assertEqual((row["input_tokens"], row["cached_input_tokens"],
                          row["cache_creation_tokens"], row["output_tokens"]), (12, 5, 2, 3))
        observed = self.gateway.observe(self.context, "task-1").unwrap()["calls"][0]
        self.assertEqual((observed["input_tokens"], observed["cached_input_tokens"],
                          observed["cache_creation_tokens"], observed["output_tokens"]), (12, 5, 2, 3))

    async def test_cleared_prompt_is_explicit_and_reset_restores_inheritance(self):
        role = ConfigTarget(INSTALLATION, ConfigLayer.ROLE, ROLE, bot_id="bot-a")
        self.patch_model(target=role, prompt="Role supplement")
        self.patch_model(prompt="", fallback="")
        run = self.model_run()
        result = await self.gateway.generate(run, self.request())
        self.assertEqual(self.called[0][1].task_prompt, "")
        field = self.gateway.observe(self.context, "task-1").unwrap()["plan"]["fields"]["model.fixture.read.prompt"]
        self.assertTrue(field["explicit"])
        self.assertEqual(field["source"]["layer"], "window")
        self.tasks.finish(run.claim, TaskResult("succeeded", result_ref=result.call_id)).unwrap()
        self.patch_model(reset=("prompt",))
        run2 = self.model_run("task-2")
        await self.gateway.generate(run2, self.request())
        self.assertEqual(self.called[1][1].task_prompt, "Role supplement")

    async def test_configuration_changes_apply_to_next_task_and_keep_current_plan(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def wait(envelope):
            entered.set()
            await release.wait()
            return ProviderResponse("not_started", usage=Amount(0, 0), receipt_ref="not-started")

        self.responses["primary"] = wait
        run = self.model_run()
        work = asyncio.create_task(self.gateway.generate(run, self.request()))
        await asyncio.wait_for(entered.wait(), 1)
        self.patch_model(prompt="Next task", fallback="", provider="fallback")
        release.set()
        await asyncio.wait_for(work, 1)
        self.assertEqual([name for name, _ in self.called], ["primary", "fallback"])
        self.assertEqual(self.called[1][1].task_prompt, "Task supplement")
        status = self.configuration.application_status(self.important, target=self.window,
                     group_id="model.fixture.read", observed_at=NOW).unwrap()
        self.assertEqual(status.state, "next_task")
        self.tasks.finish(run.claim, TaskResult("succeeded")).unwrap()
        await self.gateway.generate(self.model_run("next"), self.request())
        self.assertEqual(self.called[-1][1].task_prompt, "Next task")
        self.assertEqual(self.configuration.application_status(self.important, target=self.window,
                         group_id="model.fixture.read", observed_at=NOW).unwrap().state, "applied")

    async def test_consumer_replacement_discards_original_result_after_recording_usage(self):
        async def replace_consumer(envelope):
            self.router.start(expected_generation=1)
            return ProviderResponse("generated", ModelDraft("old consumer draft", ()), Amount(16, 45), "old-consumer")

        self.responses["primary"] = replace_consumer
        with self.assertRaises(TaskError) as error:
            await self.gateway.generate(self.model_run(), self.request())
        self.assertEqual(error.exception.code, "consumer_generation_conflict")
        row = self.call_rows()[0]
        self.assertEqual((row["state"], row["actual_tokens"]), ("discarded", 16))
        self.assertEqual(len(self.called), 1)

    async def test_reconciled_usage_is_not_overwritten_by_a_later_incomplete_result(self):
        async def already_accounted(envelope):
            row = self.call_rows()[0]
            self.ledger.reconcile_usage(row["call_id"], self.providers[0].binding,
                                        Amount(13, 50), receipt_ref="accounted", observed_at=NOW)
            return ProviderResponse("generated", ModelDraft("usage arrived earlier", ()))

        self.responses["primary"] = already_accounted
        result = await self.gateway.generate(self.model_run(), self.request())
        self.assertTrue(result.usage_known)
        row = self.call_rows()[0]
        self.assertEqual((row["state"], row["actual_tokens"], row["receipt_ref"]), ("generated", 13, "accounted"))
        self.assertTrue(all(a["spent_tokens"] == 13 and a["held_tokens"] == 0 for a in self.accounts()))

    async def test_confirmed_not_started_fallback_is_bounded_and_zero_is_receipted(self):
        self.responses = {name: ProviderResponse("not_started", usage=Amount(0, 0), receipt_ref=name + ":not-started")
                          for name in ("primary", "fallback")}
        run = self.model_run()
        with self.assertRaisesRegex(TaskError, "did not process"):
            await self.gateway.generate(run, self.request())
        self.assertEqual(len(self.called), 2)
        self.assertTrue(all(a["spent_tokens"] == a["held_tokens"] == 0 for a in self.accounts()))
        with self.assertRaisesRegex(TaskError, "instead of regenerating"):
            await self.gateway.generate(run, self.request())
        self.assertEqual(len(self.called), 2)

    async def test_fallback_receives_its_own_capability_context(self):
        self.providers = (replace(self.providers[0], capabilities=("text", "tools")),
                          replace(self.providers[1], capabilities=("text", "image")))
        self.router = ModelRouter(self.configuration, self.profiles, self.providers)
        self.router.start(expected_generation=1)
        self.gateway = LocalModelGateway(self.router, self.ledger, self.tasks)
        self.responses["primary"] = ProviderResponse("not_started", usage=Amount(0, 0), receipt_ref="first:not-started")
        await self.gateway.generate(self.model_run(), self.request())
        primary, fallback = (envelope for _, envelope in self.called)
        self.assertEqual(primary.provider_capabilities, ("text", "tools"))
        self.assertEqual(fallback.provider_capabilities, ("text", "image"))
        self.assertIn("结构化工具调用", capability_instruction(primary))
        self.assertNotIn("图片输入", capability_instruction(primary))
        self.assertIn("图片输入", capability_instruction(fallback))
        self.assertNotIn("结构化工具调用", capability_instruction(fallback))

    async def test_unknown_usage_survives_reopen_and_receipt_settles_exactly_once(self):
        self.responses["primary"] = ProviderResponse("unknown", failure=ModelFailure.TRANSPORT)
        run = self.model_run()
        with self.assertRaisesRegex(TaskError, "unknown"):
            await self.gateway.generate(run, self.request())
        self.assertEqual(len(self.called), 1)
        self.assertTrue(all(a["held_tokens"] == 30 and a["spent_tokens"] == 0 for a in self.accounts()))
        row = self.call_rows()[0]
        failure_before = self.gateway.observe(self.context, "task-1").unwrap()["calls"][0]["failure"]
        self.assertEqual(failure_before["code"], ModelFailure.TRANSPORT.value)
        other = BudgetLedger(self.open_runtime(), self.budget_policy)
        binding = self.providers[0].binding
        with self.assertRaisesRegex(TaskError, "another provider"):
            other.reconcile_usage(row["call_id"], replace(binding, generation="replacement"), Amount(12, 50), receipt_ref="actual", observed_at=NOW)
        for _ in range(2):
            other.reconcile_usage(row["call_id"], binding, Amount(12, 50), receipt_ref="actual", observed_at=NOW)
        self.assertEqual(self.gateway.observe(self.context, "task-1").unwrap()["calls"][0]["failure"], failure_before)
        self.assertTrue(all((a["held_tokens"], a["spent_tokens"], a["spent_cost"]) == (0, 12, 50) for a in self.accounts()))
        with self.assertRaisesRegex(TaskError, "immutable"):
            other.reconcile_usage(row["call_id"], binding, Amount(13, 50), receipt_ref="changed", observed_at=NOW)

    async def test_success_without_usage_keeps_reservation_and_does_not_guess_cost(self):
        self.responses["primary"] = ProviderResponse("generated", ModelDraft("no usage", ()))
        result = await self.gateway.generate(self.model_run(), self.request())
        self.assertFalse(result.usage_known)
        observed = self.gateway.observe(self.context, "task-1").unwrap()["calls"][0]
        self.assertIsNone(observed["actual_tokens"])
        self.assertIsNone(observed["actual_cost_microusd"])
        self.assertEqual(observed["usage_state"], "unknown")
        self.assertTrue(all(a["held_tokens"] == 30 for a in self.accounts()))

    async def test_unknown_adapter_error_is_redacted_and_does_not_trigger_fallback(self):
        async def broken(envelope):
            raise RuntimeError("private credential")

        self.responses["primary"] = broken
        with self.assertRaises(TaskError) as error:
            await self.gateway.generate(self.model_run(), self.request())
        self.assertEqual(error.exception.code, "model_transport_failed")
        self.assertNotIn("private credential", str(error.exception))
        self.assertEqual([name for name, _ in self.called], ["primary"])
        self.assertEqual(self.call_rows()[0]["state"], "unknown")
        report = self.gateway.observe(self.context, "task-1").unwrap()
        self.assertEqual(report["calls"][0]["failure"]["code"], ModelFailure.ADAPTER.value)
        self.assertNotIn("private credential", json.dumps(report))
        self.assertNotIn("private credential", str(tuple(self.runtime.read_one("SELECT * FROM model_call_failures", ()))))

    async def test_a_local_limit_keeps_its_own_code_instead_of_a_generic_estimation_failure(self):
        def reserve(envelope):
            raise TaskError("host_model_limit_input", "input limit exceeded: estimated=2048 maximum=1024")

        self.gateway_with(self.provider("primary", reserve), self.provider("fallback"))
        run = self.model_run()

        with self.assertLogs("companion_v2.model_gateway.service", level="WARNING") as captured:
            with self.assertRaises(TaskError) as error:
                await self.gateway.generate(run, self.request())

        self.assertEqual(error.exception.code, "host_model_limit_input")
        self.assertIn("estimated=2048", str(error.exception))
        message = "\n".join(captured.output)
        self.assertIn("code=host_model_limit_input", message)
        self.assertIn("error_type=TaskError", message)
        # 数值要出现在 WARNING 里：线上日志级别就足以判断差多少。
        self.assertIn("estimated=2048", message)
        self.assertIn("maximum=1024", message)
        # 越界不静默换 provider，也不写 model_calls：台账里留下的是可执行的那个 code。
        self.assertEqual(self.called, [])
        self.assertEqual(self.call_rows(), ())
        rounds = self.runtime.read_all(
            "SELECT round_no, state, call_id FROM model_rounds WHERE task_id = ?", (run.task.task_id,))
        self.assertEqual([(row["round_no"], row["state"], row["call_id"]) for row in rounds], [(0, "started", None)])

    async def test_an_unexpected_reserve_error_keeps_its_error_type_and_its_message_private(self):
        def reserve(envelope):
            raise ValueError("duplicate offered tool")

        self.gateway_with(self.provider("primary", reserve), self.provider("fallback"))
        run = self.model_run()

        with self.assertLogs("companion_v2.model_gateway.service", level="WARNING") as captured:
            with self.assertRaises(TaskError) as error:
                await self.gateway.generate(run, self.request())

        self.assertEqual(error.exception.code, "model_estimation_failed")
        self.assertIn("ValueError", str(error.exception))
        self.assertNotIn("details omitted", str(error.exception))
        self.assertNotIn("duplicate offered tool", str(error.exception))
        self.assertIn("error_type=ValueError", "\n".join(captured.output))
        self.assertEqual(self.called, [])
        self.assertEqual(self.call_rows(), ())

    async def test_revocation_discards_late_draft_but_records_actual_usage(self):
        async def revoke(envelope):
            self.identity.revoke_binding(self.important.binding_id, expected_revision=self.identity.revision, revoked_at=NOW)
            return ProviderResponse("generated", ModelDraft("must discard", ()), Amount(17, 60), "revoked-receipt")

        self.responses["primary"] = revoke
        with self.assertRaises(TaskError) as error:
            await self.gateway.generate(self.model_run(), self.request())
        self.assertEqual(error.exception.code, "binding_revoked")
        self.assertEqual((self.call_rows()[0]["state"], self.call_rows()[0]["actual_tokens"]), ("discarded", 17))
        self.assertEqual(self.gateway.observe(self.context, "task-1").failure.code, "binding_revoked")
        self.assertEqual(self.runtime.read_one("SELECT code FROM model_call_failures", ())["code"],
                         ModelFailure.GATEWAY_RECHECK.value)

    async def test_original_input_expiry_prevents_result_return(self):
        async def expire(envelope):
            self.inputs_current = False
            return ProviderResponse("generated", ModelDraft("expired source", ()), Amount(10, 30), "expired")

        self.responses["primary"] = expire
        with self.assertRaises(TaskError) as error:
            await self.gateway.generate(self.model_run(), self.request())
        self.assertEqual(error.exception.code, "input_expired")
        self.assertEqual(self.call_rows()[0]["state"], "discarded")

    async def test_same_persona_id_in_two_bots_does_not_share_model_configuration(self):
        target = ConfigTarget(INSTALLATION, ConfigLayer.ROLE, ROLE, bot_id="bot-a")
        self.patch_model(target=target, prompt="Bot A supplement", provider="fallback", fallback="")
        scope_b = replace(SCOPE, bot_id="bot-b", account_id="login-b")
        principal_b = replace(self.important.principal, bot_id="bot-b", account_id="login-b")
        self.identity.bind(binding_id="important-b", principal=principal_b, subject=self.important.subject,
                           valid_from=NOW, expected_revision=self.identity.revision)
        self.personas.set_binding(PersonaBinding("base-b", PersonaSelector(INSTALLATION, SelectorKind.BOT, bot_id="bot-b"),
            ROLE, 0, NOW, "fixture", "admin"), expected_revision=0, observed_at=NOW)
        actor_b = self.identity.resolve_authenticated(principal_b, observed_at=NOW).unwrap()
        context_b = self.context_for(actor_b, scope_b)
        run_a = self.model_run()
        await self.gateway.generate(run_a, self.request())
        run_b = self.model_run("bot-b-task", context=context_b)
        await self.gateway.generate(run_b, self.request(context_b))
        self.assertEqual([(name, envelope.task_prompt) for name, envelope in self.called],
                         [("fallback", "Bot A supplement"), ("primary", "Task supplement")])
        self.assertEqual(len(self.accounts()), 7)
        self.assertEqual(self.gateway.observe(context_b, "task-1").failure.code, "task_unavailable")

    async def test_persona_switch_keeps_window_budget_and_discards_old_persona_result(self):
        role_b = SubjectRef(INSTALLATION, SubjectKind.BOT, "persona-b")
        policy = replace(self.budget_policy, window=Amount(30, 1000))
        self.ledger = BudgetLedger(self.runtime, policy)
        self.gateway = LocalModelGateway(self.router, self.ledger, self.tasks)

        async def change_role(envelope):
            self.identity.bind_role(scope=SCOPE, role=role_b, expected_revision=self.context.role_binding.revision, observed_at=NOW)
            return ProviderResponse("generated", ModelDraft("old role draft", ()))

        self.responses["primary"] = change_role
        run = self.model_run()
        with self.assertRaises(TaskError) as error:
            await self.gateway.generate(run, self.request())
        self.assertEqual(error.exception.code, "role_binding_changed")
        self.assertEqual(self.call_rows()[0]["state"], "discarded")
        self.tasks.stop(run.claim, reason="permission_changed").unwrap()
        next_context = self.context_for(self.important)
        target = ConfigTarget(INSTALLATION, ConfigLayer.ROLE, role_b, bot_id="bot-a")
        self.patch_model(target=target, prompt="Persona B supplement")
        next_run = self.model_run("persona-b-task", context=next_context)
        plan = self.router.resolve(next_run, self.request(next_context))
        self.assertEqual(plan.envelope.task_prompt, "Persona B supplement")
        with self.assertRaises(TaskError) as error:
            await self.gateway.generate(next_run, self.request(next_context))
        self.assertEqual(error.exception.code, "model_budget_exhausted")
        self.assertEqual(len(self.called), 1)

    async def test_budget_checks_actual_overrun_and_does_not_hide_it_as_estimate(self):
        self.responses["primary"] = ProviderResponse("generated", ModelDraft("overrun", ()), Amount(101, 1050), "overrun")
        run = self.model_run()
        await self.gateway.generate(run, self.request())
        self.assertTrue(self.gateway.observe(self.context, "task-1").unwrap()["calls"][0]["over_reservation"])
        self.tasks.finish(run.claim, TaskResult("succeeded")).unwrap()
        with self.assertRaises(TaskError) as error:
            await self.gateway.generate(self.model_run("next"), self.request())
        self.assertEqual(error.exception.code, "model_budget_exhausted")
        self.assertEqual(len(self.called), 1)

    async def test_background_ceiling_leaves_foreground_budget_available(self):
        self.responses["primary"] = ProviderResponse("generated", ModelDraft("usage missing", ()))
        bg = self.model_run("background-1", kind="fixture.background")
        await self.gateway.generate(bg, self.request())
        self.tasks.finish(bg.claim, TaskResult("succeeded")).unwrap()
        with self.assertRaises(TaskError) as error:
            await self.gateway.generate(self.model_run("background-2", kind="fixture.background"), self.request())
        self.assertEqual(error.exception.code, "model_budget_exhausted")
        await self.gateway.generate(self.model_run(), self.request())
        self.assertEqual(len(self.called), 2)

    async def test_budget_wait_survives_ledger_recreation_and_clears_after_a_receipt_or_new_day(self):
        self.responses["primary"] = ProviderResponse("generated", ModelDraft("usage missing", ()))
        first = self.model_run("held-background", kind="fixture.background")
        completion = await self.gateway.generate(first, self.request())
        self.tasks.finish(first.claim, TaskResult("succeeded")).unwrap()
        with self.assertRaises(TaskError) as error:
            await self.gateway.generate(self.model_run("deferred-background", kind="fixture.background"), self.request())
        self.assertEqual(error.exception.code, "model_budget_exhausted")
        ledger = BudgetLedger(self.runtime, self.budget_policy)
        wait = ledger.background_deferral(self.context, "fixture.background", at=self.clock.now())
        self.assertEqual(wait["state"], "deferred")
        self.assertEqual(len(self.called), 1)
        self.assertIsNone(ledger.background_deferral(self.context, "fixture.read", at=self.clock.now()))
        self.assertIsNone(ledger.background_deferral(self.context, "fixture.background",
                                                    at=self.clock.now() + timedelta(days=1)))
        ledger.reconcile_usage(completion.call_id, self.providers[0].binding, Amount(0, 0),
                               receipt_ref="provider-confirmed-no-charge", observed_at=self.clock.now())
        self.assertIsNone(ledger.background_deferral(self.context, "fixture.background", at=self.clock.now()))

    async def test_diagnostic_projection_tracks_model_records_and_observes_usage_unknown(self):
        run = self.model_run()
        diagnostics = DiagnosticService(self.configuration, tasks=self.tasks, models=self.gateway, now=self.clock.now)
        context = DiagnosticContext(self.context.actor, self.context.role_binding, self.context.audience)
        query = DiagnosticQuery(execution=True, execution_task_id="task-1")
        before = diagnostics.inspect(context, query).unwrap()
        await self.gateway.generate(run, self.request())
        self.assertEqual(diagnostics.validated(context, before.report_id).failure.code, "diagnostic_evidence_changed")
        report = diagnostics.inspect(context, query).unwrap()
        details = json.loads(report.results[0].details_json)
        self.assertEqual(details["provider"], "local_test_adapter")
        self.assertEqual(details["sender"], "not_connected")
        self.assertEqual(details["model"]["calls"][0]["actual_tokens"], 15)
        self.assertNotIn("private draft", report.results[0].details_json)
        other = self.context_for(self.core)
        self.assertEqual(self.gateway.observe(other, "task-1").failure.code, "task_unavailable")

    async def test_successful_task_with_missing_usage_is_still_unknown_in_diagnostics(self):
        self.responses["primary"] = ProviderResponse("generated", ModelDraft("no billing receipt", ()))
        run = self.model_run()
        await self.gateway.generate(run, self.request())
        self.tasks.finish(run.claim, TaskResult("succeeded")).unwrap()
        diagnostics = DiagnosticService(self.configuration, tasks=self.tasks, models=self.gateway, now=self.clock.now)
        context = DiagnosticContext(self.context.actor, self.context.role_binding, self.context.audience)
        report = diagnostics.inspect(context, DiagnosticQuery(execution=True, execution_task_id="task-1")).unwrap()
        self.assertEqual(report.results[0].status, CheckStatus.UNKNOWN)

    async def test_cost_ceiling_alone_denies_call_without_leaking_reservations(self):
        policy = replace(self.budget_policy, installation=Amount(100, 99), background=Amount(30, 50))
        gateway = LocalModelGateway(self.router, BudgetLedger(self.runtime, policy), self.tasks)
        with self.assertRaises(TaskError) as error:
            await gateway.generate(self.model_run(), self.request())
        self.assertEqual(error.exception.code, "model_budget_exhausted")
        self.assertEqual(self.accounts(), ())
        self.assertEqual(self.call_rows(), ())
        self.assertEqual(self.called, [])

    async def test_duplicate_fallback_and_excessive_output_are_not_silently_normalized(self):
        run = self.model_run()
        for changes, expected in (({"fallback": "primary"}, "model_fallback_duplicate"),
                                  ({"fallback": "", "max_output_tokens": 101}, "model_output_limit")):
            self.patch_model(**changes)
            with self.assertRaises(TaskError) as error:
                await self.gateway.generate(run, self.request())
            self.assertEqual(error.exception.code, expected)
        self.assertEqual(self.call_rows(), ())
        self.assertEqual(self.called, [])

    async def test_unknown_provider_and_incompatible_capability_fail_before_reservation(self):
        self.patch_model(provider="not-registered")
        run = self.model_run()
        with self.assertRaises(TaskError) as error:
            await self.gateway.generate(run, self.request())
        self.assertEqual(error.exception.code, "model_provider_unavailable")
        self.assertEqual(self.call_rows(), ())
        self.patch_model(provider="primary")
        providers = (replace(self.providers[0], capabilities=("image",)), self.providers[1])
        router = ModelRouter(self.configuration, self.profiles, providers)
        router.start(expected_generation=1)
        gateway = LocalModelGateway(router, self.ledger, self.tasks)
        with self.assertRaises(TaskError) as error:
            await gateway.generate(run, self.request())
        self.assertEqual(error.exception.code, "model_capability_mismatch")
        self.assertEqual(self.called, [])

    async def test_original_context_mismatch_and_consumer_fence_prevent_dispatch(self):
        other = self.context_for(self.core)
        run = self.model_run()
        with self.assertRaises(TaskError) as error:
            await self.gateway.generate(run, self.request(other))
        self.assertEqual(error.exception.code, "model_context_mismatch")
        self.configuration.start_consumer("model_gateway", expected_generation=1)
        with self.assertRaises(TaskError) as error:
            await self.gateway.generate(run, self.request())
        self.assertEqual(error.exception.code, "consumer_generation_conflict")
        self.assertEqual(self.called, [])


class ModelTruncationTests(ModelFixture, unittest.IsolatedAsyncioTestCase):
    """A body cut off by the output ceiling belongs to the format that validates it.

    The transport only states what happened (``length``). Deciding whether a
    partial body is still usable belongs to the workflow that owns the result and
    re-validates a complete contract before writing anything, exactly like the
    private memory owner that has always received its raw body.
    """

    def truncated(self, text='{"items":[{"title":"练琴"'):
        return ProviderResponse("generated", captured_output=CapturedModelOutput(text, "length"),
                                usage=Amount(15, 40), receipt_ref="receipt-primary")

    async def test_a_truncated_body_reaches_the_format_that_validates_it(self):
        self.responses["primary"] = self.truncated()

        completion = await self.gateway.generate(self.model_run(),
            replace(self.request(), output_format="calendar_planning"))

        # The owner re-validates each part, so the raw body arrives as its draft
        # while the classification stays visible on the completion.
        self.assertEqual(completion.draft.text, '{"items":[{"title":"练琴"')
        self.assertEqual(completion.draft.claims, ())
        self.assertEqual(completion.captured_output.finish_reason, "length")
        # The provider was charged for this round; the receipt is not a zero.
        self.assertEqual(self.call_rows()[0]["state"], "generated")

    async def test_a_truncated_reply_is_never_handed_to_the_conversation_contract(self):
        # Half a reply and half a JSON object are both unusable as a delivered
        # message, and only the ceiling is to blame, so the code must say so.
        self.responses["primary"] = self.truncated()

        with self.assertRaises(TaskError) as caught:
            await self.gateway.generate(self.model_run(), self.request())
        self.assertEqual(caught.exception.code, "model_output_truncated")
        self.assertEqual(self.call_rows()[0]["state"], "discarded")

    async def test_an_unusable_capture_still_requires_its_private_memory_owner(self):
        self.responses["primary"] = ProviderResponse("generated",
            captured_output=CapturedModelOutput("", "invalid"), usage=Amount(15, 40), receipt_ref="receipt-primary")

        with self.assertRaises(TaskError) as caught:
            await self.gateway.generate(self.model_run(),
                replace(self.request(), output_format="calendar_planning"))
        self.assertEqual(caught.exception.code, "model_capture_unavailable")

    async def test_a_private_memory_reflection_keeps_its_captured_body(self):
        self.responses["primary"] = self.truncated('{"facts": [')

        completion = await self.gateway.generate(self.model_run(),
            replace(self.request(), output_format="memory_reflection"))

        self.assertEqual(completion.captured_output.text, '{"facts": [')


class ModelBudgetTests(ModelFixture):
    def test_two_connections_compete_for_last_reservation_without_partial_holds(self):
        first = self.model_run()
        other_context = self.context_for(self.core)
        second = self.model_run("task-2", context=other_context)
        for run in (first, second):
            self.stage_plan(run)
        runtime = self.open_runtime()
        supervisor = self.supervisor(runtime)
        supervisor.attach(expected_generation=1).unwrap()
        second = TaskRun(supervisor, second.claim, second.task)
        ledgers = (self.ledger, BudgetLedger(runtime, self.budget_policy))

        def reserve(args):
            ledger, run, call_id = args
            try:
                ledger.reserve(run, call_id=call_id, position=0, provider=self.providers[0].binding, amount=Amount(70, 100))
                return "reserved"
            except TaskError as error:
                return error.code

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = tuple(pool.map(reserve, zip(ledgers, (first, second), ("first-call", "second-call"))))
        self.assertCountEqual(results, ("reserved", "model_budget_exhausted"))
        self.assertEqual(len(self.call_rows()), 1)
        self.assertEqual(len(self.accounts()), 4)
        self.assertTrue(all(a["held_tokens"] == 70 for a in self.accounts()))

    def test_restart_releases_only_never_dispatched_and_preserves_unknown_holds(self):
        first = self.model_run()
        second = self.model_run("task-2", context=self.context_for(self.core))
        for run in (first, second):
            self.stage_plan(run)
            self.ledger.reserve(run, call_id=run.task.task_id, position=0, provider=self.providers[0].binding, amount=Amount(30, 100))
        self.ledger.dispatch(second, "task-2")
        runtime = self.open_runtime()
        supervisor = self.supervisor(runtime)
        supervisor.start(expected_generation=1).unwrap()
        ledger = BudgetLedger(runtime, self.budget_policy)
        self.assertEqual(ledger.recover(supervisor), 2)
        self.assertEqual(ledger.recover(supervisor), 0)
        first_row, second_row = self.call_rows()
        self.assertEqual((first_row["state"], first_row["actual_tokens"]), ("not_started", 0))
        self.assertEqual((second_row["state"], second_row["actual_tokens"]), ("unknown", None))
        self.assertEqual(runtime.read_one("SELECT code FROM model_call_failures WHERE call_id = 'task-2'", ())["code"],
                         ModelFailure.RECOVERY.value)
        with self.assertRaises(TaskError):
            self.ledger.recover(self.tasks)
        ledger.reconcile_usage("task-2", self.providers[0].binding, Amount(10, 15), receipt_ref="after-restart", observed_at=NOW)
        self.assertTrue(all(a["held_tokens"] == 0 for a in self.accounts()))

    def test_expired_reclaim_releases_only_reservations_past_their_task_timeout(self):
        stale = self.model_run("stale-call")
        self.stage_plan(stale)
        self.ledger.reserve(stale, call_id="stale-call", position=0,
                            provider=self.providers[0].binding, amount=Amount(30, 100))
        self.ledger.dispatch(stale, "stale-call")
        old = (NOW - timedelta(seconds=301)).isoformat(timespec="microseconds")
        with self.runtime.transaction() as connection:
            connection.execute("UPDATE model_calls SET started_at = ? WHERE call_id = 'stale-call'", (old,))
        self.tasks.finish(stale.claim, TaskResult("succeeded")).unwrap()
        fresh = self.model_run("fresh-call")
        self.stage_plan(fresh)
        self.ledger.reserve(fresh, call_id="fresh-call", position=0,
                            provider=self.providers[0].binding, amount=Amount(20, 50))
        self.ledger.dispatch(fresh, "fresh-call")

        report = self.ledger.reclaim_expired(self.tasks)

        self.assertEqual((report["released_calls"], report["released_tokens"], report["released_cost_microusd"]),
                         (1, 30, 100))
        rows = {row["call_id"]: row for row in self.call_rows()}
        self.assertEqual(rows["stale-call"]["state"], "dispatched")
        self.assertTrue(rows["stale-call"]["receipt_ref"].startswith("local:released:expired-reservation"))
        self.assertIsNone(rows["fresh-call"]["receipt_ref"])
        self.assertTrue(all((a["held_tokens"], a["held_cost"]) == (20, 50) for a in self.accounts()))
        self.assertEqual(self.ledger.reclaim_expired(self.tasks)["released_calls"], 0)
        audit = self.ledger.reclaim_audit()
        self.assertEqual((audit["released_calls"], audit["released_tokens"]), (1, 30))
        self.assertIn("at", audit)

    def test_reclaim_threshold_follows_the_owning_task_timeout(self):
        registrations = tuple(TaskRegistration(replace(entry.definition, timeout_seconds=400), entry.validate)
                              for entry in self.registrations)
        supervisor = self.supervisor(registrations=registrations)
        supervisor.attach(expected_generation=1).unwrap()
        supervisor.submit(self.spec("slow-call")).unwrap()
        claim = supervisor.claim(Lane.FOREGROUND).unwrap().claim
        supervisor.begin(claim).unwrap()
        run = TaskRun(supervisor, claim, supervisor.read(self.context, "slow-call").unwrap())
        self.stage_plan(run)
        self.ledger.reserve(run, call_id="slow-call", position=0,
                            provider=self.providers[0].binding, amount=Amount(30, 100))
        self.ledger.dispatch(run, "slow-call")
        for age, expected in ((500, 0), (801, 1)):
            with self.runtime.transaction() as connection:
                connection.execute("UPDATE model_calls SET started_at = ? WHERE call_id = 'slow-call'",
                                   ((NOW - timedelta(seconds=age)).isoformat(timespec="microseconds"),))
            self.assertEqual(self.ledger.reclaim_expired(supervisor)["released_calls"], expected)

    def test_release_is_idempotent_and_a_late_receipt_charges_only_once(self):
        run = self.model_run()
        self.stage_plan(run)
        self.ledger.reserve(run, call_id="released-call", position=0,
                            provider=self.providers[0].binding, amount=Amount(30, 100))
        self.ledger.dispatch(run, "released-call")

        released = self.ledger.release("released-call", observed_at=self.clock.now())
        self.assertEqual((released.tokens, released.cost_microusd), (30, 100))
        self.assertTrue(all(a["held_tokens"] == 0 for a in self.accounts()))
        self.assertEqual(self.ledger.release("released-call", observed_at=self.clock.now()), Amount(0, 0))
        self.ledger.settle("released-call", self.providers[0].binding,
                           ProviderResponse("unknown", failure=ModelFailure.TRANSPORT),
                           observed_at=self.clock.now())
        self.assertTrue(is_released(self.call_rows()[0]["receipt_ref"]))
        self.assertTrue(all(a["held_tokens"] == 0 for a in self.accounts()))

        self.ledger.reconcile_usage("released-call", self.providers[0].binding, Amount(9, 21),
                                    receipt_ref="late-receipt", observed_at=self.clock.now())
        self.assertTrue(all((a["held_tokens"], a["spent_tokens"], a["spent_cost"]) == (0, 9, 21)
                            for a in self.accounts()))
        self.assertFalse(is_released(self.call_rows()[0]["receipt_ref"]))
        with self.assertRaises(TaskError):
            self.ledger.reconcile_usage("released-call", self.providers[0].binding, Amount(10, 21),
                                        receipt_ref="changed", observed_at=self.clock.now())

    def test_concurrent_release_of_one_reservation_returns_the_hold_once(self):
        run = self.model_run()
        self.stage_plan(run)
        self.ledger.reserve(run, call_id="shared-call", position=0,
                            provider=self.providers[0].binding, amount=Amount(70, 100))
        self.ledger.dispatch(run, "shared-call")
        other = BudgetLedger(self.open_runtime(), self.budget_policy)

        def release(ledger):
            return ledger.release("shared-call", observed_at=self.clock.now()).tokens

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = tuple(pool.map(release, (self.ledger, other)))
        self.assertEqual(sorted(results), [0, 70])
        self.assertTrue(all(a["held_tokens"] == 0 for a in self.accounts()))

    def test_reclaim_requires_the_current_supervisor(self):
        with self.assertRaises(TaskError) as error:
            self.ledger.reclaim_expired(self.supervisor())
        self.assertEqual(error.exception.code, "supervisor_fenced")

    def test_same_day_policy_change_cannot_reset_spending_or_expand_budget(self):
        run = self.model_run()
        self.stage_plan(run)
        self.ledger.reserve(run, call_id="first", position=0, provider=self.providers[0].binding, amount=Amount(30, 100))
        changed = BudgetLedger(self.runtime, replace(self.budget_policy, installation=Amount(1000, 1000)))
        with self.assertRaises(TaskError) as error:
            changed.reserve(run, call_id="other", position=1, provider=self.providers[1].binding, amount=Amount(30, 100))
        self.assertEqual(error.exception.code, "model_budget_policy_changed")
        self.assertEqual(len(self.call_rows()), 1)


class ModelRunnerTests(ModelFixture, unittest.IsolatedAsyncioTestCase):
    def runner(self):
        self.registrations = tuple(TaskRegistration(replace(e.definition, timeout_seconds=0.08, max_attempts=1), e.validate)
                                   for e in self.registrations)
        self.tasks = self.supervisor()
        self.tasks.attach(expected_generation=1).unwrap()
        self.gateway = LocalModelGateway(self.router, self.ledger, self.tasks)

        async def handler(run):
            result = await self.gateway.generate(run, self.request(run.task.spec.context))
            return TaskResult("succeeded", result_ref=result.call_id)

        runner = LocalTaskRunner(self.tasks, {e.definition.kind: handler for e in self.registrations}, check_interval=0.005)
        self.addAsyncCleanup(runner.close)
        return runner

    async def test_runner_connects_original_task_and_model_receipt(self):
        runner = self.runner()
        self.submit()
        task = (await runner.run_once(Lane.FOREGROUND)).unwrap().task
        self.assertEqual(task.state, "succeeded")
        self.assertEqual(task.result_ref, self.call_rows()[0]["call_id"])
        self.assertEqual(task.attempt_no, 1)

    async def test_timeout_keeps_unknown_usage_and_does_not_dispatch_fallback(self):
        async def stall(envelope):
            await asyncio.Event().wait()

        self.responses["primary"] = stall
        runner = self.runner()
        self.submit()
        task = (await runner.run_once(Lane.FOREGROUND)).unwrap().task
        self.assertEqual(task.state, "expired")
        self.assertEqual(self.call_rows()[0]["state"], "unknown")
        self.assertEqual(self.runtime.read_one("SELECT code FROM model_call_failures", ())["code"],
                         ModelFailure.INTERRUPTED.value)
        self.assertTrue(all(a["held_tokens"] == 30 for a in self.accounts()))
        self.assertEqual(len(self.called), 1)
        self.assertEqual(runner.active_handles, ())

    async def test_cancellation_does_not_release_unknown_usage(self):
        entered = asyncio.Event()

        async def stall(envelope):
            entered.set()
            await asyncio.Event().wait()

        self.responses["primary"] = stall
        runner = self.runner()
        self.submit()
        work = asyncio.create_task(runner.run_once(Lane.FOREGROUND))
        await asyncio.wait_for(entered.wait(), 1)
        current = self.read_task()
        self.tasks.cancel(self.context, current.task_id, expected_revision=current.revision).unwrap()
        result = (await asyncio.wait_for(work, 1)).unwrap().task
        self.assertEqual(result.state, "cancelled")
        self.assertTrue(all(a["held_tokens"] == 30 for a in self.accounts()))
        self.assertEqual(len(self.called), 1)

    async def test_late_receipt_after_timeout_records_usage_but_never_returns_draft(self):
        async def late(envelope):
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return ProviderResponse("generated", ModelDraft("late private draft", ()), Amount(11, 31), "late-receipt")

        self.responses["primary"] = late
        runner = self.runner()
        self.submit()
        result = (await runner.run_once(Lane.FOREGROUND)).unwrap().task
        self.assertEqual(result.state, "expired")
        self.assertIsNone(result.result_ref)
        self.assertEqual((self.call_rows()[0]["state"], self.call_rows()[0]["actual_tokens"]), ("discarded", 11))
        self.assertTrue(all(a["held_tokens"] == 0 and a["spent_tokens"] == 11 for a in self.accounts()))


if __name__ == "__main__":
    unittest.main()
