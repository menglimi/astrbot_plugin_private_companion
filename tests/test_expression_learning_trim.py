"""Expression learning trims its reference rules to the model input budget.

Production evidence (2026-11-01): a window that had accumulated 128 expression
rules failed every learning run in the local reservation estimate. A batch reads
only the newest 80 rules, and those 80 alone were 1.42× the default 131072-byte
ceiling once the adapter's wire JSON-escaped them a second time (each material
payload is already JSON, and serializing the wire escapes it again). Nothing in
the batch could be dropped to recover, so the same material failed again on every
scan. These tests pin the owner-side answer: the samples stay, the kept rules
stay newest first, only the reference rules are trimmed, and the task says how
many it dropped. All providers and conversations are synthetic.
"""

from dataclasses import replace
from datetime import timedelta
import json
import unittest

from companion_v2.adapters.astrbot.memory_output import response_format
from companion_v2.adapters.astrbot.model_wire import build_wire
from companion_v2.execution.models import ProviderBinding, TaskError, digest, encode, stamp
from companion_v2.expression.style_management import ExpressionStyleManagement
from companion_v2.expression.style_service import MIN_EXISTING_RULES, RULE_READ_LIMIT, trim_reference_rules
from companion_v2.model_gateway.models import Amount, BudgetPolicy, LocalProvider
from companion_v2.role_profile.configuration import (GROUP as ROLE_PROFILE_GROUP,
    RoleProfileConfigurationOwner, configuration_fields as role_profile_fields,
    target_for as role_profile_target_for)
from tests import test_expression_v1_pipeline as pipeline
from tests.test_expression_v1_pipeline import ExpressionFixture
from tests.test_host_application import HostFixture


# ``LocalProvider``'s default, and the host default: the ceiling production runs
# under until an operator raises it.
DEFAULT_INPUT_CEILING = 131072
RULE_COUNT = 40
# A wire-scale reservation (and the seeded replies holding budget from the same
# account) is larger than this fixture's default 100k daily account.
WIRE_BUDGET = BudgetPolicy(*(Amount(200000, 200000) for _ in range(5)))


def reference_rule(index):
    """One synthetic rule long enough that a few dozen of them become a budget."""
    return {"observation": "合成参考规则" + str(index) + "：" + "先把短句收住，再把话头交回去。" * 8,
            "proposal": "合成参考规则" + str(index) + "：" + "短句确认，再说条件，不交代背景。" * 8}


class ExpressionLearningTrimTests(ExpressionFixture, HostFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.expression_profiles()
        self.model_behavior = self.expression_model
        self.learning_ceiling = DEFAULT_INPUT_CEILING
        self.reserved = []

    def budgeted(self, ceiling=None, *, reserve=None):
        """Declare a provider input ceiling, the way the host adapter counts bytes.

        ``ceiling=None`` keeps ``LocalProvider``'s default, which is the ceiling
        production runs under.
        """
        self.learning_ceiling = DEFAULT_INPUT_CEILING if ceiling is None else ceiling
        options = {} if ceiling is None else {"maximum_input_estimate": ceiling}
        self.providers = (LocalProvider(ProviderBinding("model.text", "primary", "1", "model-1"),
                          ("text", "tools"), 100, reserve or self.reserve_within_budget, self.generate,
                          **options),)

    def reserve_within_budget(self, envelope):
        """Reject when the material bytes alone exceed the declared ceiling."""
        size = self.material_size(envelope)
        if size > self.learning_ceiling:
            raise TaskError("host_model_limit", "request exceeds its declared input profile")
        return Amount(size + envelope.max_output_tokens, 1000)

    def reserve_wire_budget(self, envelope):
        """The adapter's own accounting: the JSON-escaped wire against its ceiling."""
        size = self.wire_size(envelope)
        self.reserved.append((envelope.original.output_format, size))
        if size > self.learning_ceiling:
            raise TaskError("host_model_limit", "request exceeds its declared input profile")
        return Amount(size + envelope.max_output_tokens, 1000)

    @staticmethod
    def material_size(envelope):
        # The reference rules, samples and schema travel as material payloads; this
        # is the same quantity the adapter's local estimate is built from.
        return sum(len(item.payload.encode("utf-8")) for item in envelope.original.context.materials)

    @staticmethod
    def wire_size(envelope):
        """The adapter's local estimate: the wire, escaped again, plus its overhead.

        Reuses the adapter's own functions so the test measures what production
        measures, including the second layer of JSON escaping that makes the
        newest 80 rules exceed the default ceiling.
        """
        estimate = build_wire(envelope, model_name="synthetic").input_estimate()
        structured = response_format(envelope.original)
        return estimate if structured is None else estimate + len(encode(structured).encode("utf-8"))

    def seed_rules(self, app, context, count=RULE_COUNT, state_for=None):
        """Store ``count`` rules whose newest-first order is the reverse of creation.

        ``state_for(index)`` names the state of each rule by creation index, so a
        test can place adopted rules at the oldest end of the window.
        """
        state_for = state_for or (lambda index: "rejected")
        owner_key = digest(app.expression_style.samples.role_owner(context))
        scope_key = digest(context.scope)
        start = self.clock.now() - timedelta(minutes=count)
        sample = json.dumps({"opened_at": stamp(start), "closed_at": stamp(start), "handles": [],
                             "characters": 0, "source_kind": "message"})
        rows = []
        with app.runtime.transaction() as connection:
            connection.execute("INSERT INTO expression_style_runs VALUES (?,?,?,?,?,?,?,?,?)",
                               ("run-trim", owner_key, scope_key, sample, "trim-hash", "合成参考规则",
                                "decided", stamp(start), stamp(start)))
            for index in range(count):
                rule = reference_rule(index)
                rows.append("signal-trim-" + str(index))
                connection.execute("""INSERT INTO expression_style_signals
                    (signal_id, run_id, owner_key, scope_key, dimension, observation, proposal,
                     confidence, evidence_json, state, created_at, decided_at, decided_by, decision_key, ordinal)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (rows[-1], "run-trim", owner_key, scope_key, "sentence_rhythm",
                     rule["observation"], rule["proposal"], 0.5, "[]", state_for(index),
                     stamp(start + timedelta(minutes=index)), None, None, None, index))
        return rows

    def write_self_page(self, app, context, **values):
        """Store the role profile the gateway substitutes for the captured persona."""
        owner = RoleProfileConfigurationOwner(app.runtime)
        target = role_profile_target_for(context)
        profile = owner.read(target, role_profile_fields())
        owner.write(target, role_profile_fields(),
                    changes={ROLE_PROFILE_GROUP + "." + name: value for name, value in values.items()},
                    reset=(), expected_revision=profile.revision, observed_at=self.clock.now())

    async def seed_messages(self, host, *, persona="原角色人格提示"):
        """Reuse the reply pipeline's seeding so this suite reaches a real run.

        ``persona`` is the host text the conversation captures; the self page is
        the role profile, which the gateway substitutes before dispatch.
        """
        return await pipeline.ExpressionReplyPipelineTests.seed(self, host, prompt=persona)

    async def learn(self, app, context):
        """Run one learning batch and wait for its owner result."""
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

    def learning_sizes(self):
        return [size for kind, size in self.reserved if kind == "role_expression_learning"]

    async def prepare(self, *, ceiling=None, reserve=None, budget=None, persona="原角色人格提示",
                      self_page=None):
        self.budgeted(ceiling, reserve=reserve)
        host = await self.start(self.host(budget=budget) if budget is not None else None)
        app = host.application
        if self_page is not None:
            self.write_self_page(app, self.selected_context(), **self_page)
        context = await self.seed_messages(host, persona=persona)
        self.configure_expression(app, {"private_learning_enabled": True})
        return app, context

    async def test_oversized_reference_rules_are_trimmed_before_the_request(self):
        # A ceiling that fits a few rules but not the whole window: the batch must
        # succeed, keep the newest rules and say how many it dropped. Every rule
        # here has the lowest value (rejected), so value order is time order and
        # the trim is exactly the prefix step it has always been.
        app, context = await self.prepare(ceiling=30000)
        rows = self.seed_rules(app, context)

        last = await self.learn(app, context)
        self.assertEqual(last["state"], "succeeded", last)
        envelope = self.learning_request()
        material = self.learning_material(envelope)
        kept = [rule["signal_id"] for rule in material["existing_rules"]]
        newest_first = list(reversed(rows))

        self.assertGreaterEqual(len(kept), MIN_EXISTING_RULES)
        self.assertLess(len(kept), len(rows), "the window did not need the whole window to fit")
        self.assertEqual(kept, newest_first[:len(kept)], "a window of equal value keeps the newest prefix")
        self.assertLessEqual(self.material_size(envelope), self.learning_ceiling)
        self.assertEqual(last["trimmed_rules"], len(rows) - len(kept))
        self.assertEqual(last["trim_reason"], "model_input_budget")
        self.assertEqual(last["input_ceiling"], self.learning_ceiling)
        status = app.expression_style.status(context)
        self.assertEqual(status["trimmed_rules"], last["trimmed_rules"],
                         "the page reads the same observation the task recorded")
        self.assertEqual(status["trim_reason"], "model_input_budget")
        calls = app.runtime.read_all("SELECT c.call_id FROM model_calls c "
                                     "JOIN execution_tasks t ON t.task_id = c.task_id WHERE t.kind = ?",
                                     ("role.expression.learn",))
        self.assertEqual(len(calls), 1, "a trimmed batch still sends exactly one request")

    def test_a_rejected_only_window_keeps_the_newest_prefix(self):
        # The old behaviour must survive the value order: when every rule has the
        # same (lowest) value, the step still drops the oldest end.
        rules = [{"signal_id": index, "state": "rejected"} for index in range(RULE_COUNT)]
        kept = trim_reference_rules(rules)
        self.assertGreaterEqual(len(kept), MIN_EXISTING_RULES)
        self.assertLess(len(kept), len(rules))
        self.assertEqual(kept, rules[:len(kept)])

    async def test_an_older_adopted_rule_survives_the_trim(self):
        # Adopted priors are what a new candidate must not duplicate; a newer
        # rejection is not. Adopted rules sit at the oldest end of the window, so
        # a recency-only prefix would drop all of them before the first rejection.
        app, context = await self.prepare(ceiling=30000)
        adopted_from = 8
        rows = self.seed_rules(app, context,
                               state_for=lambda index: "adopted" if index < adopted_from else "rejected")
        states = {signal: "adopted" if index < adopted_from else "rejected"
                  for index, signal in enumerate(rows)}
        adopted = {signal for signal, state in states.items() if state == "adopted"}

        last = await self.learn(app, context)
        self.assertEqual(last["state"], "succeeded", last)
        material = self.learning_material(self.learning_request())
        kept = [rule["signal_id"] for rule in material["existing_rules"]]
        dropped = set(rows) - set(kept)

        self.assertGreaterEqual(len(kept), MIN_EXISTING_RULES)
        self.assertLess(len(kept), len(rows), "the window still needed trimming")
        self.assertFalse(adopted & set(list(reversed(rows))[:len(kept)]),
                         "a recency-only prefix would have dropped every adopted rule")
        self.assertEqual(adopted - set(kept), set(), "every adopted rule survives the trim")
        self.assertEqual({states[signal] for signal in dropped}, {"rejected"},
                         "only lower-value rules are dropped")
        self.assertEqual(kept, [signal for signal in reversed(rows) if signal in set(kept)],
                         "kept rules stay in their stored newest-first order")
        self.assertEqual(last["trimmed_rules"], len(rows) - len(kept))
        self.assertEqual(last["trim_reason"], "model_input_budget")
        self.assertEqual(last["input_ceiling"], self.learning_ceiling)
        calls = app.runtime.read_all("SELECT c.call_id FROM model_calls c "
                                     "JOIN execution_tasks t ON t.task_id = c.task_id WHERE t.kind = ?",
                                     ("role.expression.learn",))
        self.assertEqual(len(calls), 1, "a trimmed batch still sends exactly one request")

    async def test_a_production_window_is_trimmed_under_the_default_ceiling(self):
        # The production window: 128 rules accumulated, the newest RULE_READ_LIMIT
        # of them read, the host default ceiling, and the adapter's own wire
        # accounting. Those 80 read rules alone are more than one ceiling, so the
        # batch must trim, still keep well over the floor, and never fail.
        app, context = await self.prepare(reserve=self.reserve_wire_budget, budget=WIRE_BUDGET)
        rows = self.seed_rules(app, context, 128)

        last = await self.learn(app, context)
        self.assertEqual(last["state"], "succeeded", last)
        envelope = self.learning_request()
        material = self.learning_material(envelope)
        kept = [rule["signal_id"] for rule in material["existing_rules"]]

        sizes = self.learning_sizes()
        self.assertGreater(sizes[0], DEFAULT_INPUT_CEILING, "the whole read window does not fit")
        self.assertLessEqual(sizes[-1], DEFAULT_INPUT_CEILING)
        self.assertGreater(len(kept), MIN_EXISTING_RULES, "one over-budget window must not need the floor")
        self.assertLess(len(kept), RULE_READ_LIMIT)
        self.assertEqual(kept, list(reversed(rows))[:len(kept)],
                         "a window of equal value keeps the newest prefix")
        self.assertLessEqual(self.wire_size(envelope), DEFAULT_INPUT_CEILING)
        self.assertEqual(last["trimmed_rules"], RULE_READ_LIMIT - len(kept))
        self.assertEqual(last["trim_reason"], "model_input_budget")
        self.assertEqual(last["input_ceiling"], DEFAULT_INPUT_CEILING)
        recorded = app.runtime.read_all("SELECT r.task_id FROM model_requests r "
                                        "JOIN execution_tasks t ON t.task_id = r.task_id WHERE t.kind = ?",
                                        ("role.expression.learn",))
        self.assertEqual(len(recorded), 1, "the preflight must not record a request of its own")

    async def test_a_shorter_self_page_does_not_trim_a_fitting_window(self):
        # The preflight must judge the request the gateway dispatches, not the one
        # the conversation captured. Here the host persona is much longer than the
        # role profile that replaces it: judging the captured persona would trim a
        # window that fits as the short self page — a normal window the baseline
        # sent whole. The persona still fits a reply, so the seeded conversation is
        # an ordinary one; only the learning route would have been judged too tight.
        persona = "很长的宿主人格提示。" * 800
        app, context = await self.prepare(reserve=self.reserve_wire_budget, budget=WIRE_BUDGET,
                                          persona=persona, self_page={"name": "阿灯"})
        # Fewer than a full read window, with room to spare: this case is about
        # which role layer the preflight measures, not about a tight margin.
        rows = self.seed_rules(app, context, 30)

        last = await self.learn(app, context)
        self.assertEqual(last["state"], "succeeded", last)
        # Judge the regression first: a baseline that measured the captured
        # persona trims here, and that is the failure worth reporting — not a
        # later premise about which side of the ceiling each request fell on.
        self.assertNotIn("trimmed_rules", last)
        envelope = self.learning_request()
        # The case only discriminates if the captured persona does not fit while
        # the dispatched self page does.
        captured = replace(envelope, original=replace(envelope.original,
            role_instruction=persona, role_guard_instruction=None))
        self.assertGreater(self.wire_size(captured), DEFAULT_INPUT_CEILING)
        self.assertLessEqual(self.wire_size(envelope), DEFAULT_INPUT_CEILING)
        self.assertEqual(envelope.original.host_role_instruction, persona,
                         "the native role guard still checks the captured persona")
        self.assertNotEqual(envelope.original.role_instruction, persona)
        material = self.learning_material(envelope)
        self.assertEqual([rule["signal_id"] for rule in material["existing_rules"]], list(reversed(rows)),
                         "a window that fits the dispatched request is sent unchanged")

    async def test_a_longer_self_page_trims_before_the_dispatch(self):
        # The mirror image: the role profile can also be longer than the captured
        # persona. A preflight on the persona would pass a request the gateway then
        # rejects, which is the failure this whole preflight exists to prevent; the
        # window has to be trimmed before dispatch instead.
        self_page = {"personality": "很长的角色自述。" * 300, "identity": "很长的角色自述。" * 250,
                     "voice_examples": "很长的角色自述。" * 225, "motivations": "很长的角色自述。" * 225,
                     "appearance": "很长的角色自述。" * 225}
        app, context = await self.prepare(reserve=self.reserve_wire_budget, budget=WIRE_BUDGET,
                                          self_page=self_page)
        rows = self.seed_rules(app, context, 30)

        last = await self.learn(app, context)
        self.assertEqual(last["state"], "succeeded", last)
        envelope = self.learning_request()
        # The case only discriminates if the captured persona would have fitted
        # while the dispatched self page does not.
        captured = replace(envelope, original=replace(envelope.original,
            role_instruction="原角色人格提示", role_guard_instruction=None))
        self.assertLessEqual(self.wire_size(captured), DEFAULT_INPUT_CEILING)
        sizes = self.learning_sizes()
        self.assertGreater(sizes[0], DEFAULT_INPUT_CEILING, "the whole dispatched request does not fit")
        self.assertLessEqual(sizes[-1], DEFAULT_INPUT_CEILING)
        material = self.learning_material(envelope)
        kept = [rule["signal_id"] for rule in material["existing_rules"]]
        self.assertGreater(len(kept), MIN_EXISTING_RULES)
        self.assertEqual(kept, list(reversed(rows))[:len(kept)],
                         "a window of equal value keeps the newest prefix")
        self.assertEqual(last["trimmed_rules"], 30 - len(kept))
        self.assertEqual(last["trim_reason"], "model_input_budget")
        self.assertEqual(last["input_ceiling"], DEFAULT_INPUT_CEILING)

    async def test_the_samples_are_never_trimmed(self):
        app, context = await self.prepare(ceiling=30000)
        self.seed_rules(app, context)
        last = await self.learn(app, context)
        self.assertGreater(last["trimmed_rules"], 0)
        material = self.learning_material(self.learning_request())
        self.assertEqual(len(material["samples"]), 6,
                         "the evidence stays complete; only the deduplication priors shrink")

    async def test_material_that_cannot_fit_even_the_floor_fails_by_its_own_code(self):
        # Eight rules are the floor, so a ceiling below that cannot be met: the run
        # must report the profile conflict instead of a generic estimation failure,
        # and it must not trim below the priors a batch needs to deduplicate.
        app, context = await self.prepare(ceiling=6000)
        rows = self.seed_rules(app, context)
        with self.assertLogs("companion_v2.expression.style_service", level="WARNING"):
            last = await self.learn(app, context)
        self.assertEqual(last["state"], "failed", last)
        self.assertEqual(last["code"], "expression_model_profile_exceeded")
        self.assertEqual(last["trimmed_rules"], len(rows) - MIN_EXISTING_RULES,
                         "the floor is the last reference set the run tries")
        self.assertFalse(any(item.original.output_format == "role_expression_learning"
                             for item in self.called), "an unfitting request must never be sent")

    async def test_a_fitting_window_is_sent_unchanged(self):
        app, context = await self.prepare(ceiling=131072)
        rows = self.seed_rules(app, context)
        last = await self.learn(app, context)
        self.assertEqual(last["state"], "succeeded", last)
        material = self.learning_material(self.learning_request())
        self.assertEqual([rule["signal_id"] for rule in material["existing_rules"]], list(reversed(rows)),
                         "a normal window keeps every rule and its order")
        self.assertNotIn("trimmed_rules", last)
        self.assertIsNone(app.expression_style.status(context)["trimmed_rules"])


if __name__ == "__main__":
    unittest.main()
