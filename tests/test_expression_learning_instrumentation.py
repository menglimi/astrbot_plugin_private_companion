"""One expression-learning run must be judgeable from its logs alone.

Before this instrumentation a successful batch left no trace of what it read or
produced: a run that proposed zero candidates, one that lost every candidate to
deduplication and one that never reached the model looked identical in the log.
The quality question ("did learning get worse?") therefore had no measurement.

These tests pin the four structured INFO lines of one run (assembly, model
return, parse result, commit), the parse counters in the branches that actually
drop something, the failure line for a round the output ceiling cut, and the
warning for a worker that failed before its model round. They also pin the
privacy rule: counts, enums and durations only, never the sampled text, the
prompt body or the persona. The behavioural assertions compare the run's
observable result with what it was before the instrumentation: same result
document, same number of model calls, same stored rows.
"""

from dataclasses import replace
from datetime import timedelta
import json
import unittest
from unittest.mock import patch

from companion_v2.configuration.models import ConfigLayer, ConfigTarget
from companion_v2.configuration.registry import FieldRegistry
from companion_v2.execution.models import TaskError, digest, stamp
from companion_v2.expression.style_management import ExpressionStyleManagement
from companion_v2.expression.style_service import KIND, LEARN_LEASE_SECONDS
from companion_v2.model_gateway.models import Amount, CapturedModelOutput, ProviderResponse
from companion_v2.model_gateway.routing import configuration_fields
from companion_v2.storage.models import OperationState
from companion_v2.storage.repositories import OperationRepository
from tests import test_expression_v1_pipeline as pipeline
from tests.test_expression_v1_pipeline import ExpressionFixture, RULE
from tests.test_host_application import HostFixture
from tests.test_role_tool_loop import answered


LOGGER = "companion_v2.expression.style_service"
USER_MARKER = "嗯哼"
PERSONA_MARKER = "原角色人格提示"


def lines_with(logs, marker):
    return [record.getMessage() for record in logs.records if marker in record.getMessage()]


def fields(line):
    """Parse one structured log line into its ``key=value`` pairs."""
    return {key: value for part in line.split() if "=" in part for key, _, value in (part.partition("="),)}


class LearningInstrumentationTests(ExpressionFixture, HostFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.expression_profiles()
        # A round refuses an answer wider than the count its own dispatched schema
        # declared. The shared fixture's 100-token learning profile only pays for
        # one candidate, while the dedupe and capacity cases each script a
        # two-candidate answer, so raise just this class's learning ceiling to a
        # value that declares two (the registry feeds the profiles, so it is
        # rebuilt with them).
        self.profiles = tuple(replace(profile, max_output_tokens=1600)
                              if profile.kind == "role.expression.learn" else profile
                              for profile in self.profiles)
        self.registry = FieldRegistry(version="expression-pipeline.v1",
                                      fields=configuration_fields(self.profiles))
        # The route clamps the ask to what the selected provider can emit, so the
        # provider has to pay for the same two candidates.
        self.providers = tuple(
            replace(provider, maximum_output_tokens=1600,
                    reserve=lambda envelope: Amount(envelope.max_output_tokens + 1024, 1000))
            for provider in self.providers)
        self.model_behavior = self.expression_model

    async def prepare(self):
        """Seed a real private window with learned material and enable learning."""
        host = await self.start()
        app = host.application
        context = await pipeline.ExpressionReplyPipelineTests.seed(self, host, prompt=PERSONA_MARKER)
        self.configure_expression(app, {"private_learning_enabled": True})
        # Keep the driver's own discovery pass off this test: the round under test
        # is the one this test asks for, not a scan-started one.
        app.expression_style.next_checks[context.key] = self.clock.now() + timedelta(days=1)
        return app, context

    async def learn(self, app, context):
        service = app.expression_style
        work_key = service._work_key(context)
        started = ExpressionStyleManagement(app).learn(operator=self.core, context=context).unwrap()
        self.assertIn(started["state"], ("queued", "running"))
        await self.until(lambda: (service.results.get(work_key) or {}).get("state") in ("succeeded", "failed"))
        return service.results[work_key]

    def learning_request(self):
        return next(item for item in reversed(self.called)
                    if item.original.output_format == "role_expression_learning")

    def learning_material(self, envelope):
        return json.loads(next(item for item in envelope.original.context.materials
                               if item.purpose == "role_expression_samples").payload)

    def model_calls(self, app):
        return app.runtime.read_all("SELECT c.call_id FROM model_calls c "
                                    "JOIN execution_tasks t ON t.task_id = c.task_id WHERE t.kind = ?",
                                    ("role.expression.learn",))

    def stored_signals(self, app):
        return app.runtime.read_all("SELECT signal_id, state FROM expression_style_signals "
                                    "ORDER BY signal_id", ())

    def scripted(self, *changes, summary="可复用的短句节奏"):
        """Answer one learning round with one candidate per change to the fixture rule.

        Evidence handles come from the material this round actually sent, so every
        candidate passes the evidence contract without inventing handles.
        """
        async def answer(envelope):
            handles = [item["handle"] for item in self.learning_material(envelope)["samples"][:2]]
            signals = [{**RULE, "evidence_handles": handles, **change} for change in changes]
            return answered(json.dumps({"summary": summary, "signals": signals}, ensure_ascii=False))
        return answer

    def save_task_ceiling(self, app, context, value):
        """Save this window's own output ceiling for the learning task."""
        profile = app.router.profiles[KIND]
        target = ConfigTarget(context.scope.installation_id, ConfigLayer.WINDOW,
                              context.role_binding.role, context.scope)
        snapshot = app.configuration.read(self.core, target=target, group_id=profile.group,
                                          observed_at=self.clock.now()).unwrap()
        return app.configuration.patch(self.core, target=target, group_id=profile.group,
            expected_edit_token=snapshot.edit_token,
            changes={profile.group + ".max_output_tokens": value},
            operation=self.operation(target.authorization_scope, owner="configuration"),
            observed_at=self.clock.now()).unwrap()

    def metadata(self, app, prefix):
        return app.runtime.read_all("SELECT value FROM runtime_metadata WHERE key LIKE ?",
                                    (prefix + "%",))

    def instruction_for(self, app, context):
        return app.state_maintenance.bindings[context.key][1]

    def seed_active_rules(self, app, context, count):
        """Store ``count`` pending rules so only the rest of the budget can fit.

        The rows carry no expression-form record, exactly like the rules written
        before form storage existed: this batch only needs them to occupy the
        active budget and to appear in the reference list.
        """
        owner_key = digest(app.expression_style.samples.role_owner(context))
        scope_key = digest(context.scope)
        start = self.clock.now() - timedelta(minutes=count)
        sample = json.dumps({"opened_at": stamp(start), "closed_at": stamp(start), "handles": [],
                             "characters": 0, "source_kind": "message"})
        with app.runtime.transaction() as connection:
            connection.execute("INSERT INTO expression_style_runs VALUES (?,?,?,?,?,?,?,?,?)",
                               ("run-capacity", owner_key, scope_key, sample, "capacity-hash", "容量",
                                "pending", stamp(start), None))
            for index in range(count):
                connection.execute("""INSERT INTO expression_style_signals
                    (signal_id, run_id, owner_key, scope_key, dimension, observation, proposal,
                     confidence, evidence_json, state, created_at, decided_at, decided_by, decision_key, ordinal)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    ("signal-capacity-" + str(index), "run-capacity", owner_key, scope_key,
                     "sentence_rhythm", "容量参考规则" + str(index), "容量参考规则" + str(index) + "的用法",
                     0.5, "[]", "pending", stamp(start + timedelta(minutes=index)), None, None, None, index))

    async def test_a_successful_run_logs_assembly_model_parse_and_commit_counts(self):
        app, context = await self.prepare()
        with self.assertLogs(LOGGER, level="INFO") as logs:
            last = await self.learn(app, context)

        material = self.learning_material(self.learning_request())
        sample_window = material["sample_window"]
        self.assertEqual((last["state"], last["code"]), ("succeeded", "owner_result"), last)
        self.assertEqual(set(last), {"state", "task_id", "code"})
        self.assertTrue(last["task_id"].startswith("expression-learn:"))
        self.assertEqual(len(self.model_calls(app)), 1, "a normal run still spends exactly one model call")
        inserted = self.stored_signals(app)

        [assembled] = lines_with(logs, "表达学习组装完成")
        assembly = fields(assembled)
        self.assertEqual(int(assembly["samples"]), len(material["samples"]))
        self.assertEqual(int(assembly["sample_chars"]), sample_window["characters"])
        self.assertEqual(int(assembly["existing_read"]), 0)
        self.assertEqual(int(assembly["existing_sent"]), 0)
        self.assertGreater(int(assembly["input_est_tokens"]), 0,
                           "the log must carry the gateway's own input estimate")
        self.assertEqual(int(assembly["output_ceiling"]), 1600)
        self.assertEqual(int(assembly["declared"]), 2)
        self.assertEqual(int(assembly["trimmed"]), 0)

        [returned] = lines_with(logs, "表达学习模型返回")
        model_return = fields(returned)
        self.assertGreater(int(model_return["raw_chars"]), 0)
        self.assertGreater(int(model_return["visible_tokens"]), 0)
        self.assertEqual(model_return["finish_reason"], "stop")
        self.assertEqual(model_return["truncated"], "no")
        # Neither retry exists in this build, so the two fields must say so
        # instead of claiming "no retry happened".
        self.assertEqual(model_return["degraded"], "unknown")
        self.assertEqual(model_return["ceiling_raised"], "unknown")
        self.assertGreaterEqual(int(model_return["wait_ms"]), 0)

        [parsed] = lines_with(logs, "表达学习解析结果")
        parsed = fields(parsed)
        self.assertEqual(int(parsed["proposed"]), 1)
        self.assertEqual(int(parsed["dedup_signature"]), 0)
        self.assertEqual(int(parsed["dedup_form"]), 0)
        self.assertEqual(int(parsed["capacity_dropped"]), 0)
        self.assertEqual(int(parsed["inserted"]), 1)
        self.assertEqual(int(parsed["pending"]), 1)
        self.assertEqual(int(parsed["adopted"]), 0)
        self.assertEqual(len(inserted), 1)
        self.assertEqual(inserted[0]["state"], "pending")

        [committed] = lines_with(logs, "表达学习提交 ")
        commit = fields(committed)
        self.assertEqual(commit["committed"], "yes")
        # The field names checkpoint presence, not progress: the scheduler wrote
        # it before this run, so "advanced" would claim a change never measured.
        self.assertEqual(commit["checkpoint"], "present")
        self.assertEqual(commit["quota"], "accounted")
        for name in ("elapsed_assemble_ms", "elapsed_wait_ms", "elapsed_parse_ms",
                     "elapsed_commit_ms", "elapsed_total_ms"):
            self.assertGreaterEqual(int(commit[name]), 0, name)

        text = "\n".join(logs.output)
        self.assertNotIn(USER_MARKER, text, "the sampled messages must never reach the log")
        self.assertNotIn(PERSONA_MARKER, text, "the prompt body must never reach the log")
        self.assertNotIn(sample_window["opened_at"], text)

    async def test_a_truncated_round_logs_the_failing_step_code_and_retry(self):
        app, context = await self.prepare()

        async def truncated(envelope):
            return ProviderResponse("generated",
                captured_output=CapturedModelOutput('{"summary": "被截断的候选', "length"),
                usage=Amount(15, 40), receipt_ref="truncated-usage")

        self.model_behavior = truncated
        with self.assertLogs(LOGGER, level="INFO") as logs:
            last = await self.learn(app, context)

        self.assertEqual(last["state"], "failed", last)
        self.assertEqual(last["code"], "model_output_truncated")
        # A cut-off round now spends one more, smaller call before it gives up
        # (#456), so the failed result also carries the degraded-retry record.
        self.assertEqual(set(last), {"state", "task_id", "code", "truncation_retry"})
        self.assertEqual(len(self.model_calls(app)), 2,
                         "the merged build degrades the ask once, then stops")
        self.assertFalse(self.stored_signals(app), "a cut answer must store no candidate")
        self.assertEqual(OperationRepository(app.runtime).get(last["task_id"]).unwrap().state,
                         OperationState.ACCEPTED, "the failed round still owns its material batch")

        assembled = lines_with(logs, "表达学习组装完成")
        self.assertGreaterEqual(len(assembled), 1)
        self.assertTrue(all(int(fields(line)["input_est_tokens"]) > 0 for line in assembled))
        self.assertEqual(lines_with(logs, "表达学习模型返回"), [],
                         "a round the ceiling cut returns no completion")
        self.assertEqual(lines_with(logs, "表达学习解析结果"), [])
        self.assertEqual(lines_with(logs, "表达学习提交 "), [])

        failed_lines = lines_with(logs, "表达学习本轮未提交")
        self.assertGreaterEqual(len(failed_lines), 1)
        failure = fields(failed_lines[0])
        self.assertEqual(failure["stage"], "model")
        self.assertEqual(failure["code"], "model_output_truncated")
        # The first cut round hands over to an immediate smaller ask, not to the
        # next lease: ``run`` retries right away with the degraded count.
        self.assertEqual(failure["retry"], "now")
        self.assertEqual(failure["epoch"], "0")
        self.assertEqual(failure["truncated"], "yes")
        # The failing model wait is measured on the same clock as a successful
        # one: a round cut at the ceiling is exactly the one whose cost matters.
        self.assertNotEqual(failure["elapsed_wait_ms"], "unavailable")
        self.assertGreaterEqual(int(failure["elapsed_wait_ms"]), 0)
        self.assertEqual(failure["elapsed_assemble_ms"] != "unavailable", True)

        text = "\n".join(logs.output)
        self.assertNotIn(USER_MARKER, text, "the sampled messages must never reach the log")
        self.assertNotIn(PERSONA_MARKER, text, "the prompt body must never reach the log")

    async def test_a_repeated_candidate_is_counted_by_the_dedupe_that_dropped_it(self):
        """A duplicate has to move a counter, or the parse line cannot explain a loss."""
        app, context = await self.prepare()
        # Same expression form (kind/situation/pattern), different observation:
        # the second candidate is a duplicate of the first by form, not by
        # signature, so exactly one candidate may be stored.
        first = {"observation": "回应计划时常用短句收束"}
        second = {"observation": "回应计划时先确认，再用短句收束"}
        self.model_behavior = self.scripted(first, second)
        with self.assertLogs(LOGGER, level="INFO") as logs:
            last = await self.learn(app, context)

        self.assertEqual((last["state"], last["code"]), ("succeeded", "owner_result"), last)
        self.assertEqual(len(self.model_calls(app)), 1)
        self.assertEqual(len(self.stored_signals(app)), 1, "the repeated form must not be stored twice")

        [parsed] = lines_with(logs, "表达学习解析结果")
        counters = fields(parsed)
        self.assertEqual(int(counters["proposed"]), 2)
        self.assertEqual(int(counters["dedup_form"]), 1)
        self.assertEqual(int(counters["dedup_signature"]), 0)
        self.assertEqual(int(counters["capacity_dropped"]), 0)
        self.assertEqual(int(counters["inserted"]), 1)
        self.assertEqual(int(counters["pending"]), 1)

    async def test_a_candidate_beyond_the_remaining_budget_is_counted_as_dropped(self):
        """When only one slot is left, the extra candidate must be visible as dropped."""
        app, context = await self.prepare()
        # The budget has twelve slots; eleven occupied leaves room for one.
        self.configure_expression(app, {"active_signal_limit": 12})
        self.seed_active_rules(app, context, 11)
        self.model_behavior = self.scripted(
            {"observation": "预算内的新短句节奏", "proposal": "预算内先确认，再收束",
             "expression": {**RULE["expression"], "pattern": "预算内先用短句确认"}},
            {"observation": "预算外的新短句节奏", "proposal": "预算外先确认，再收束",
             "expression": {**RULE["expression"], "pattern": "预算外先接一句补充"}})
        with self.assertLogs(LOGGER, level="INFO") as logs:
            last = await self.learn(app, context)

        self.assertEqual((last["state"], last["code"]), ("succeeded", "owner_result"), last)
        self.assertEqual(len(self.stored_signals(app)), 12, "the seeded rules plus the one that fit")

        [assembled] = lines_with(logs, "表达学习组装完成")
        self.assertEqual(int(fields(assembled)["existing_sent"]), 11)
        [parsed] = lines_with(logs, "表达学习解析结果")
        counters = fields(parsed)
        self.assertEqual(int(counters["proposed"]), 2)
        self.assertEqual(int(counters["dedup_form"]), 0)
        self.assertEqual(int(counters["dedup_signature"]), 0)
        self.assertEqual(int(counters["capacity_dropped"]), 1)
        self.assertEqual(int(counters["inserted"]), 1)

    async def test_a_worker_that_fails_before_its_model_round_says_so(self):
        """A pre-model failure must not disappear into the generic background error."""
        app, context = await self.prepare()
        service = app.expression_style
        work_key = service._work_key(context)
        real_prepare = service.prepare
        calls = {"count": 0}

        def fail_after_admission(*arguments, **options):
            calls["count"] += 1
            if calls["count"] == 1:
                return real_prepare(*arguments, **options)
            raise TaskError("expression_sample_changed", "sampled writing changed or was withdrawn")

        with self.assertLogs(LOGGER, level="INFO") as logs:
            with patch.object(service, "prepare", fail_after_admission):
                started = ExpressionStyleManagement(app).learn(operator=self.core, context=context).unwrap()
                self.assertIn(started["state"], ("queued", "running"))
                await self.until(lambda: (service.results.get(work_key) or {}).get("state") == "failed")

        self.assertEqual((service.results[work_key]["state"], service.results[work_key]["code"]),
                         ("failed", "expression_sample_changed"))
        self.assertEqual(len(self.model_calls(app)), 0, "the round failed before any model call")
        self.assertFalse(self.stored_signals(app))
        self.assertEqual(lines_with(logs, "表达学习模型返回"), [])
        self.assertEqual(lines_with(logs, "表达学习本轮未提交"), [])

        [failed] = lines_with(logs, "表达学习工作线程失败")
        failure = fields(failed)
        self.assertEqual(failure["stage"], "assemble")
        self.assertEqual(failure["code"], "expression_sample_changed")
        self.assertEqual(failure["retry"], "next_scan")

    async def test_a_failed_round_keeps_the_same_error_contract(self):
        """The instrumentation must not change what a failure object looks like."""
        app, context = await self.prepare()

        async def unavailable(envelope):
            raise TaskError("model_estimation_failed", "local reservation estimation failed")

        self.model_behavior = unavailable
        with self.assertLogs(LOGGER, level="INFO"):
            last = await self.learn(app, context)

        self.assertEqual((last["state"], last["code"]), ("failed", "model_estimation_failed"))
        self.assertEqual(set(last), {"state", "task_id", "code"})
        self.assertFalse(self.stored_signals(app))

    async def test_a_ceiling_above_the_provider_is_refused_at_preflight_with_both_numbers(self):
        """A saved ceiling the provider cannot pay for is a configuration fact.

        The route refuses the plan before anything is sent, so the round never
        entered assembly's own raise points by growing too long: reporting it as
        ``assemble`` sent the operator looking for an over-long prompt. The line
        has to name the phase that really refused it and both sides of the
        comparison, and the phase it did enter still has to be measured.
        """
        app, context = await self.prepare()
        self.save_task_ceiling(app, context, 8192)
        with self.assertLogs(LOGGER, level="INFO") as logs:
            last = await self.learn(app, context)

        self.assertEqual((last["state"], last["code"]), ("failed", "model_output_limit"), last)
        self.assertEqual(self.model_calls(app), (),
                         "a refused plan must not spend a model call")

        [line] = lines_with(logs, "表达学习本轮未提交")
        failure = fields(line)
        self.assertEqual(failure["stage"], "preflight", "the refusal is a route comparison")
        self.assertEqual(failure["retry"], "no", "the next attempt would be refused identically")
        self.assertEqual(failure["reason"], "cannot_change")
        self.assertEqual(failure["wait"], "none")
        # The phase this round did enter is measured even though it raised;
        # ``unavailable`` is reserved for the phases it never entered.
        self.assertNotEqual(failure["elapsed_assemble_ms"], "unavailable")
        self.assertGreaterEqual(int(failure["elapsed_assemble_ms"]), 0)
        for name in ("elapsed_wait_ms", "elapsed_parse_ms", "elapsed_commit_ms"):
            self.assertEqual(failure[name], "unavailable", name)

        [refusal] = lines_with(logs, "表达学习上限配置与模型能力不匹配")
        numbers = fields(refusal)
        self.assertEqual(numbers["stage"], "preflight")
        self.assertEqual(numbers["code"], "model_output_limit")
        self.assertEqual(numbers["saved_max_output_tokens"], "8192")
        self.assertEqual(numbers["provider_max_output_tokens"], "1600",
                         "the provider capability this route declares")
        self.assertEqual(numbers["adopted_max_output_tokens"], "8192")
        self.assertEqual(numbers["adopted_basis"], "explicit",
                         "the operator saved this value, so editing it is the fix")

    async def test_a_refused_batch_is_not_retried_by_the_next_scan(self):
        """The scheduler must not spend the next epoch on a comparison that failed."""
        app, context = await self.prepare()
        self.save_task_ceiling(app, context, 8192)
        service = app.expression_style
        work_key = service._work_key(context)
        first = await self.learn(app, context)
        self.assertEqual(first["code"], "model_output_limit", first)
        self.assertEqual(self.metadata(app, "role.expression.learn.lease.v1:"), (),
                         "no retry epoch may be opened for a deterministic refusal")
        self.assertEqual(len(self.metadata(app, "role.expression.learn.stop.v1:")), 1,
                         "the refusal is remembered for this material signature")

        # Past the lease *and* past the window's rescan interval, so the scan is
        # really allowed to look at this window again.
        self.clock.advance(timedelta(hours=2))
        with self.assertLogs(LOGGER, level="INFO") as logs:
            started = service._scan(context, self.instruction_for(app, context), self.clock.now())
            self.assertTrue(started, "the scan still reports the recorded refusal")
            await self.until(lambda: (service.results.get(work_key) or {}).get("retry") == "no")

        report = service.results[work_key]
        self.assertEqual((report["state"], report["code"]), ("failed", "model_output_limit"))
        self.assertEqual(self.model_calls(app), (), "a stopped signature spends no model call")
        self.assertEqual(self.metadata(app, "role.expression.learn.lease.v1:"), (),
                         "the retry epoch was never advanced")
        [skip] = lines_with(logs, "表达学习跳过同一批确定性失败素材")
        skipped = fields(skip)
        self.assertEqual(skipped["code"], "model_output_limit")
        self.assertEqual(skipped["retry"], "no")
        self.assertEqual(skipped["reason"], "cannot_change")

    async def test_an_explicit_retry_clears_the_recorded_refusal(self):
        """Editing the value then asking for the batch must not stay refused."""
        app, context = await self.prepare()
        self.save_task_ceiling(app, context, 8192)
        first = await self.learn(app, context)
        self.assertEqual(first["code"], "model_output_limit", first)

        # The operator lowered the saved ceiling and clicked "learn now". The
        # previous result still says "failed", so this test waits for the batch to
        # really run instead of for a state that is already true.
        self.save_task_ceiling(app, context, 1600)
        self.clock.advance(timedelta(seconds=LEARN_LEASE_SECONDS * 4))
        service = app.expression_style
        with self.assertLogs(LOGGER, level="INFO") as logs:
            ExpressionStyleManagement(app).learn(operator=self.core, context=context).unwrap()
            await self.until(lambda: len(self.model_calls(app)) == 1)
            await self.until(lambda: (service.results.get(service._work_key(context)) or {})
                             .get("state") == "succeeded")
        retried = service.results[service._work_key(context)]

        self.assertEqual((retried["state"], retried["code"]), ("succeeded", "owner_result"), retried)
        self.assertEqual(len(lines_with(logs, "表达学习按手动请求清除确定性失败记录")), 1)
        self.assertEqual(self.metadata(app, "role.expression.learn.stop.v1:"), ())


if __name__ == "__main__":
    unittest.main()
