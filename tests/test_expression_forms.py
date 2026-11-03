"""Concrete learned forms reach replies, survive editing, and respect review decisions."""

import json
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from companion_v2.expression import learning
from companion_v2.expression import style_rotation
from companion_v2.expression.configuration import read_settings
from companion_v2.expression.sharing import build_pack, normalize_pack
from companion_v2.execution.models import digest, stamp
from companion_v2.expression.style_management import ExpressionStyleManagement
from companion_v2.conversation.foreground import PromptMaterial, PromptRequest, SealedForegroundContext
from companion_v2.conversation.generation import _filter_expression_style_materials
from companion_v2.conversation.messages import ConversationMessages
from companion_v2.conversation.style_context import (ConversationExpressionStyleContext,
    EXPRESSION_STYLE_INSTRUCTION, SNAPSHOT_VERSION)
from tests.test_expression_v1_pipeline import ExpressionFixture, materials, RULE
from tests import test_expression_v1_pipeline as pipeline
from tests.test_host_application import HostFixture
from tests.test_role_tool_loop import answered


FORM = {"kind": "style", "situation": "彼此已有共识，不需要展开解释时",
        "pattern": "懂的都懂", "avoid": "对方确实在请求解释时不要敷衍"}


class ExpressionFormTests(ExpressionFixture, HostFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.expression_profiles()
        self.model_behavior = self.model

    async def model(self, envelope):
        if envelope.original.output_format == "role_expression_learning":
            source = json.loads(materials(envelope, "role_expression_samples")[0].payload)
            return answered(json.dumps({"summary": "同一情境中的短表达", "signals": [{**RULE,
                "expression": FORM, "evidence_handles": [item["handle"] for item in source["samples"][:2]]}]},
                ensure_ascii=False))
        return await self.expression_model(envelope)

    async def test_reviewed_learning_uses_concrete_form_and_never_resurrects_a_retired_rule(self):
        host = await self.start()
        app = host.application
        context = await pipeline.ExpressionReplyPipelineTests.seed(self, host)
        self.configure_expression(app, {"private_learning_enabled": True, "style_review_enabled": False})
        manager = await self.learn_now(app, context)
        candidate = manager.list(operator=self.core, context=context).unwrap()["items"][0]
        # Draft review is independent from accepting newly learned expressions.
        self.assertEqual(manager.list(operator=self.core, context=context, state="adopted").unwrap()["items"], [])
        manager.command(operator=self.core, context=context, signal_id=candidate["signal_id"], action="adopt",
            view_token=candidate["view_token"], operation_key="accept-concrete-form").unwrap()
        active = manager.list(operator=self.core, context=context, state="adopted").unwrap()["items"][0]
        self.assertEqual(active["expression"], FORM)
        await pipeline.ExpressionReplyPipelineTests.reply(self, host, "concrete-form-reply")
        request = next(item for item in reversed(self.called) if item.original.output_format == "conversation")
        self.assertEqual(json.loads(materials(request, "expression_style_rule")[0].payload)["expression"], FORM)
        usage = style_rotation.usage(app.runtime, digest(app.expression_samples.role_owner(context)),
                                     digest(context.scope))
        self.assertEqual(usage[active["signal_id"]][0], 1)
        active = manager.list(operator=self.core, context=context, state="adopted").unwrap()["items"][0]
        manager.command(operator=self.core, context=context, signal_id=active["signal_id"], action="retire",
            view_token=active["view_token"], operation_key="stop-old-form").unwrap()
        await self.learn_now(app, context)
        self.assertEqual(manager.list(operator=self.core, context=context, state="adopted").unwrap()["items"], [])
        self.assertEqual(manager.list(operator=self.core, context=context).unwrap()["items"], [])

    async def test_new_samples_do_not_duplicate_a_form_when_only_the_notes_change(self):
        host = await self.start()
        app = host.application
        context = await pipeline.ExpressionReplyPipelineTests.seed(self, host)
        self.configure_expression(app, {"private_learning_enabled": True})
        manager = await self.learn_now(app, context)
        original = manager.list(operator=self.core, context=context).unwrap()["items"][0]

        self.configure_expression(app, {"private_learning_enabled": False})
        batch = host.receive(self.event("fresh-expression-sample", text="嗯哼，新样本里也这样接话。")).unwrap()
        self.advance(app)
        await self.done(app, batch)
        self.configure_expression(app, {"private_learning_enabled": True})

        async def reworded_model(envelope):
            if envelope.original.output_format == "role_expression_learning":
                source = json.loads(materials(envelope, "role_expression_samples")[0].payload)
                self.assertEqual(source["existing_rules"][0]["signal_id"], original["signal_id"])
                return answered(json.dumps({"summary": "更多写法上的观察", "signals": [{**RULE,
                    "observation": "说完重点后留一个轻短的尾音",
                    "proposal": "在无需解释时轻巧收尾，不要每句照用",
                    "expression": FORM,
                    "evidence_handles": [item["handle"] for item in source["samples"][:2]]}]},
                    ensure_ascii=False))
            return await self.expression_model(envelope)

        self.model_behavior = reworded_model
        await self.learn_now(app, context)
        latest = manager.list(operator=self.core, context=context).unwrap()["items"]
        self.assertEqual([item["signal_id"] for item in latest], [original["signal_id"]])
        self.assertGreater(len(latest[0]["evidence"]), len(original["evidence"]))
        support = app.runtime.read_all("SELECT value FROM runtime_metadata "
            "WHERE key LIKE 'expression.rule-support:%'", ())
        self.assertEqual(len(support), 1)
        self.assertNotIn("嗯哼", support[0]["value"], "additional evidence stores handles, not raw writing")
        newest = next(item for item in manager.list_samples(operator=self.core, context=context).unwrap()["items"]
                      if item["handle"] in json.loads(support[0]["value"])[0]["handles"])
        manager.sample_command(operator=self.core, context=context, handle=newest["handle"],
            action="reject", view_token=newest["view_token"], operation_key="withdraw-new-support").unwrap()
        remaining = manager.list(operator=self.core, context=context).unwrap()["items"][0]
        self.assertEqual(len(remaining["evidence"]), len(original["evidence"]))

    async def test_reply_uses_laya_selected_rule_after_request_is_rebuilt(self):
        host = await self.start()
        app = host.application
        context = await pipeline.ExpressionReplyPipelineTests.seed(self, host)
        self.configure_expression(app, {"private_learning_enabled": False,
                                        "style_review_enabled": False})
        rules = [{**RULE, "observation": f"观察{index}", "proposal": f"用法{index}",
            "expression": {**FORM, "pattern": f"说法{index}"},
            "applicability": {"channels": ["private"], "intent": intent}}
            for index, intent in enumerate(("comfort", "play", "question"))]
        manager = ExpressionStyleManagement(app)
        pack = build_pack(rules, title="多情境表达")
        preview = manager.preview_import(operator=self.core, context=context, raw_pack=pack).unwrap()
        manager.import_pack(operator=self.core, context=context, raw_pack=pack,
            preview_token=preview["preview_token"], operation_key="seed-multiple-forms").unwrap()
        pending = manager.list(operator=self.core, context=context).unwrap()["items"]
        self.assertEqual(len(pending), 3)
        for item in pending:
            manager.command(operator=self.core, context=context, signal_id=item["signal_id"],
                action="adopt", view_token=item["view_token"],
                operation_key="adopt-" + item["signal_id"]).unwrap()

        async def answer_decision(_run, _request):
            return {"decision": {"action": "reply", "reply_intent": "answer", "reply_style": "normal"}}

        with patch.object(app.generator, "_laya_decision", side_effect=answer_decision):
            await pipeline.ExpressionReplyPipelineTests.reply(self, host, "question-form-reply")
        sent = next(item for item in reversed(self.called) if item.original.output_format == "conversation")
        forms = [json.loads(item.payload)["expression"]["pattern"]
                 for item in materials(sent, "expression_style_rule")]
        self.assertEqual(forms, ["说法2"])
        used = {item["expression"]["pattern"]: item["injections"] for item in
            manager.list(operator=self.core, context=context, state="adopted").unwrap()["items"]}
        self.assertEqual(used, {"说法0": 0, "说法1": 0, "说法2": 1})

    async def test_wording_review_only_sees_the_rules_this_round_carried(self):
        host = await self.start()
        app = host.application
        context = await pipeline.ExpressionReplyPipelineTests.seed(self, host)
        self.configure_expression(app, {"private_learning_enabled": False,
                                        "style_review_enabled": True})
        rules = [{**RULE, "observation": f"观察{index}", "proposal": f"用法{index}",
            "expression": {**FORM, "pattern": f"说法{index}"},
            "applicability": {"channels": ["private"], "intent": intent}}
            for index, intent in enumerate(("comfort", "play", "question"))]
        manager = ExpressionStyleManagement(app)
        pack = build_pack(rules, title="多情境表达")
        preview = manager.preview_import(operator=self.core, context=context, raw_pack=pack).unwrap()
        manager.import_pack(operator=self.core, context=context, raw_pack=pack,
            preview_token=preview["preview_token"], operation_key="seed-review-forms").unwrap()
        for item in manager.list(operator=self.core, context=context).unwrap()["items"]:
            manager.command(operator=self.core, context=context, signal_id=item["signal_id"],
                action="adopt", view_token=item["view_token"],
                operation_key="adopt-" + item["signal_id"]).unwrap()

        async def answer_decision(_run, _request):
            return {"decision": {"action": "reply", "reply_intent": "answer", "reply_style": "normal"}}

        with patch.object(app.generator, "_laya_decision", side_effect=answer_decision):
            await pipeline.ExpressionReplyPipelineTests.reply(self, host, "reviewed-form-reply")
        sent = next(item for item in reversed(self.called) if item.original.output_format == "conversation")
        self.assertEqual([json.loads(item.payload)["expression"]["pattern"]
                          for item in materials(sent, "expression_style_rule")], ["说法2"])
        reviews = [item for item in self.called if item.original.output_format == "role_expression_review"]
        self.assertTrue(reviews, "a selected rule enables the wording review")
        reviewed = json.loads(materials(reviews[-1], "expression_review_input")[0].payload)["rules"]
        self.assertEqual([json.loads(rule)["expression"]["pattern"] for rule in reviewed], ["说法2"],
                         "the review must not receive rules the reply model never saw")

    async def test_editor_preserves_review_state_evidence_and_rejects_stale_edits(self):
        host = await self.start()
        app = host.application
        context = await pipeline.ExpressionReplyPipelineTests.seed(self, host)
        identifier = self.adopt_pack(app, context)
        manager = ExpressionStyleManagement(app)
        before = manager.list(operator=self.core, context=context, state="adopted").unwrap()["items"][0]
        changes = {"dimension": "diction", "expression": FORM, "proposal": "在默契情境下自然接话，不每轮都说",
                   "applicability": before["applicability"]}
        arguments = dict(operator=self.core, context=context, signal_id=identifier, view_token=before["view_token"],
                         operation_key="edit-form", changes=changes)
        manager.revise(**arguments).unwrap()
        self.assertTrue(manager.revise(**arguments).unwrap()["replayed"])
        after = manager.list(operator=self.core, context=context, state="adopted").unwrap()["items"][0]
        self.assertEqual(after["expression"], FORM)
        self.assertEqual(after["evidence"], before["evidence"])
        self.assertEqual(manager.revise(**{**arguments, "operation_key": "stale-edit"}).failure.code,
                         "expression_view_stale")
        pack = manager.export_pack(operator=self.core, context=context).unwrap()["package"]
        self.assertEqual(pack["rule_groups"][0]["rules"][0]["pattern"], FORM["pattern"])
        self.assertEqual(pack["rule_groups"][0]["rules"][0]["expression"], FORM)
        await pipeline.ExpressionReplyPipelineTests.reply(self, host, "edited-form-reply")
        request = next(item for item in reversed(self.called) if item.original.output_format == "conversation")
        self.assertIn(FORM["pattern"], materials(request, "expression_style_rule")[0].payload)

    async def test_export_round_trip_keeps_the_learned_note_and_is_recognised_as_duplicate(self):
        host = await self.start()
        app = host.application
        context = await pipeline.ExpressionReplyPipelineTests.seed(self, host)
        self.configure_expression(app, {"private_learning_enabled": True, "style_review_enabled": False})
        manager = await self.learn_now(app, context)
        learned = manager.list(operator=self.core, context=context).unwrap()["items"][0]
        manager.command(operator=self.core, context=context, signal_id=learned["signal_id"], action="adopt",
            view_token=learned["view_token"], operation_key="adopt-round-trip").unwrap()

        package = manager.export_pack(operator=self.core, context=context).unwrap()["package"]
        reimported = normalize_pack(json.loads(json.dumps(package)))["signals"][0]
        self.assertEqual(reimported["observation"], learned["observation"],
                         "the exported note must survive a round trip instead of becoming the situation")
        self.assertEqual(reimported["expression"], FORM)
        preview = manager.preview_import(operator=self.core, context=context, raw_pack=package).unwrap()
        self.assertEqual((preview["importable_rule_count"], preview["duplicate_rule_count"]), (0, 1))

    async def test_import_merges_the_same_form_with_reworded_notes(self):
        host = await self.start()
        app = host.application
        context = await pipeline.ExpressionReplyPipelineTests.seed(self, host)
        self.configure_expression(app, {"private_learning_enabled": True, "style_review_enabled": False})
        manager = await self.learn_now(app, context)
        learned = manager.list(operator=self.core, context=context).unwrap()["items"][0]
        self.assertEqual(learned["state"], "pending")
        same_form = build_pack([{**RULE, "observation": "同样的情境换一种说法记下来",
                                 "proposal": "同样自然化用，不照抄", "expression": FORM,
                                 "applicability": {"channels": ["private"]}}], title="同形不同注")
        preview = manager.preview_import(operator=self.core, context=context, raw_pack=same_form).unwrap()
        self.assertEqual((preview["importable_rule_count"], preview["duplicate_rule_count"]), (0, 1),
                         "the same situation and pattern is one rule even when the notes differ")

        # A dormant decision is not a live duplicate: after retiring the rule the
        # same pack may be imported again, which is how an operator recovers a rule
        # whose original evidence is gone.
        manager.command(operator=self.core, context=context, signal_id=learned["signal_id"], action="adopt",
            view_token=learned["view_token"], operation_key="adopt-before-retire").unwrap()
        active = manager.list(operator=self.core, context=context, state="adopted").unwrap()["items"][0]
        manager.command(operator=self.core, context=context, signal_id=active["signal_id"], action="retire",
            view_token=active["view_token"], operation_key="retire-before-reimport").unwrap()
        again = manager.preview_import(operator=self.core, context=context, raw_pack=same_form).unwrap()
        self.assertEqual((again["importable_rule_count"], again["duplicate_rule_count"]), (1, 0))

    async def test_withdrawn_sample_leaves_the_rotation_pool(self):
        host = await self.start()
        app = host.application
        context = await pipeline.ExpressionReplyPipelineTests.seed(self, host)
        self.configure_expression(app, {"private_learning_enabled": True, "style_review_enabled": False})
        manager = await self.learn_now(app, context)
        learned = manager.list(operator=self.core, context=context).unwrap()["items"][0]
        manager.command(operator=self.core, context=context, signal_id=learned["signal_id"], action="adopt",
            view_token=learned["view_token"], operation_key="adopt-before-withdrawal").unwrap()
        policy = read_settings(app.runtime, context)
        self.assertEqual(len(app.expression_style_context.capture(
            context, self.clock.now(), policy=policy)["references"]), 1)
        for sample in manager.list_samples(operator=self.core, context=context).unwrap()["items"]:
            manager.sample_command(operator=self.core, context=context, handle=sample["handle"],
                action="reject", view_token=sample["view_token"],
                operation_key="withdraw-" + sample["handle"]).unwrap()
        self.assertEqual(app.expression_style_context.capture(
            context, self.clock.now(), policy=policy)["references"], [],
            "a withdrawn sample must not keep occupying a rotation candidate slot")

    async def test_editing_a_rule_into_another_live_rule_is_rejected(self):
        host = await self.start()
        app = host.application
        context = await pipeline.ExpressionReplyPipelineTests.seed(self, host)
        self.configure_expression(app, {"private_learning_enabled": False, "style_review_enabled": False})
        manager = ExpressionStyleManagement(app)
        rules = [{**RULE, "observation": f"观察{index}", "proposal": f"用法{index}",
                  "expression": {**FORM, "pattern": f"说法{index}"},
                  "applicability": {"channels": ["private"]}} for index in range(2)]
        pack = build_pack(rules, title="两条不同说法")
        preview = manager.preview_import(operator=self.core, context=context, raw_pack=pack).unwrap()
        manager.import_pack(operator=self.core, context=context, raw_pack=pack,
            preview_token=preview["preview_token"], operation_key="seed-edit-duplicate").unwrap()
        items = manager.list(operator=self.core, context=context).unwrap()["items"]
        for item in items:
            manager.command(operator=self.core, context=context, signal_id=item["signal_id"],
                action="adopt", view_token=item["view_token"],
                operation_key="adopt-" + item["signal_id"]).unwrap()
        active = manager.list(operator=self.core, context=context, state="adopted").unwrap()["items"]
        first, second = active[0], active[1]
        result = manager.revise(operator=self.core, context=context, signal_id=first["signal_id"],
            view_token=first["view_token"], operation_key="duplicate-edit",
            changes={"dimension": first["dimension"], "expression": second["expression"],
                     "proposal": first["proposal"], "applicability": first["applicability"]})
        self.assertEqual(result.failure.code, "expression_duplicate_rule")


class ExpressionFormContractTests(unittest.TestCase):
    def test_semantic_selection_reaches_a_matching_rule_behind_unrelated_rules(self):
        at = datetime.now(timezone.utc)
        context = SimpleNamespace(key="exact-conversation",
            scope=SimpleNamespace(kind=SimpleNamespace(value="private")))
        snapshot = {"version": SNAPSHOT_VERSION, "context_key": context.key, "captured_at": stamp(at),
            "state": "ready", "references": [{"signal_id": str(index), "run_id": "run"}
                for index in range(3)], "truncated": False, "reason": None, "rules_per_turn": 2,
            "payload_chars": 4000, "injected": [], "channel": "private",
            "application_enabled": True, "learning_mode": "balanced", "style_review_enabled": False}
        style = ConversationExpressionStyleContext(SimpleNamespace(runtime=None))
        intents = ("comfort", "play", "question")

        def material(reference, _context, _at, memo=None):
            return json.dumps({"signal_id": reference["signal_id"],
                "applicability": learning.normalize_applicability({"channels": ["private"],
                    "intent": intents[int(reference["signal_id"])]})})

        with patch.object(style, "_material", side_effect=material):
            candidates = style.contributions(snapshot, context, at, candidate_pool=True)
        rules = tuple(PromptMaterial(item.owner, item.purpose, item.payload) for item in candidates)
        self.assertEqual(len([item for item in rules if item.purpose == "expression_style_rule"]), 3)
        event = SimpleNamespace(scope=context.scope, runtime_scope=None)
        binding = SimpleNamespace(scope=context.scope)
        request = PromptRequest(event, "", SealedForegroundContext(rules, (), ()),
            "Reply\n" + EXPRESSION_STYLE_INSTRUCTION, binding)
        selected = _filter_expression_style_materials(request,
            {"reply_intent": "answer", "reply_style": "normal"}, max_rules=2)
        matched = [json.loads(item.payload)["signal_id"] for item in selected.context.materials
                   if item.purpose == "expression_style_rule"]
        self.assertEqual(matched, ["2"])
        summary = next(json.loads(item.payload) for item in selected.context.materials
                       if item.purpose == "expression_style_context")
        self.assertEqual(summary["rules_injected"], 1)

        next_info = {**summary, "rules_available": 2}
        next_materials = tuple(replace(item, payload=json.dumps(next_info))
            if item.purpose == "expression_style_context" else item for item in selected.context.materials)
        refreshed = replace(selected, context=replace(selected.context, materials=next_materials))
        pool_info = {**next_info, "rules_injected": 0}
        pool_materials = tuple(replace(item, payload=json.dumps(pool_info))
            if item.purpose == "expression_style_context" else item for item in next_materials)
        owner_projection = replace(selected, context=replace(selected.context, materials=pool_materials))
        owner = SimpleNamespace(optional_guidance=ConversationMessages.optional_guidance,
            owned=lambda *_: None, _request=lambda *_args, **_kwargs: owner_projection,
            tasks=SimpleNamespace(now=lambda: at))
        self.assertTrue(ConversationMessages.validate_optional_refresh(
            owner, context, "batch", selected, refreshed))

    def test_different_phrases_in_one_situation_remain_distinct(self):
        arguments = (RULE["dimension"], RULE["observation"], RULE["proposal"], None)
        first = learning.candidate_signature(*arguments, FORM)
        self.assertNotEqual(first, learning.candidate_signature(*arguments, {**FORM, "pattern": "有内味了"}))
        self.assertEqual(first, learning.candidate_signature(*arguments, {**FORM, "pattern": " 懂的都懂 "}))

    def test_same_form_is_identical_despite_reworded_observation_or_advice(self):
        original = learning.form_identity(FORM)
        self.assertEqual(original, learning.form_identity({**FORM, "situation": " 彼此已有共识，不需要展开解释时 "}))
        self.assertNotEqual(original, learning.form_identity({**FORM, "pattern": "有内味了"}))
        self.assertNotEqual(original, learning.form_identity({**FORM, "situation": "第一次请求详细说明时"}))
        self.assertNotEqual(original, learning.form_identity({**FORM, "kind": "grammar"}))

    def test_concrete_form_needs_original_evidence_and_is_required_for_new_candidates(self):
        signal = {**RULE, "expression": FORM, "evidence_handles": ["input:real"]}
        self.assertEqual(learning.parse_output({"summary": "", "signals": [signal]},
            allowed_evidence={"input:real"}).signals[0].expression, FORM)
        with self.assertRaises(ValueError):
            learning.parse_output({"summary": "", "signals": [signal]}, allowed_evidence={"input:other"})
        old_candidate = {key: value for key, value in signal.items() if key != "expression"}
        with self.assertRaisesRegex(ValueError, "invalid_signal"):
            learning.parse_output({"summary": "", "signals": [old_candidate]}, allowed_evidence={"input:real"})
