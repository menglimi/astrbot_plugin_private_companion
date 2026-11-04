import asyncio
from dataclasses import replace
from datetime import timedelta
import json
import unittest
from unittest.mock import patch

from companion_v2.agenda.models import AgendaTarget, Schedule
from companion_v2.configuration.models import ConfigLayer, ConfigTarget
from companion_v2.conversation.delivery import DeliveryReceipt
from companion_v2.conversation.reply_settings import GROUP as PRESENTATION_GROUP
from companion_v2.conversation.text_processing import TEXT_DEFAULTS
from companion_v2.conversation.foreground import BackgroundPromptTrigger
from companion_v2.delivery.service import DeliveryRequest
from companion_v2.execution.models import TaskError, digest
from companion_v2.foundation.refs import SubjectKind, SubjectRef
from companion_v2.identity_policy.personas import ConversationRoute
from companion_v2.identity_policy.models import AuthorizationTier
from companion_v2.proactive.configuration import GROUP, target_for
from companion_v2.proactive.prompts import contact_output, history_round_materials
from companion_v2.proactive.service import CONTACT_KIND
from companion_v2.model_gateway.models import Amount, BudgetPolicy
from companion_v2.adapters.astrbot.model_wire import build_wire
from tests.proactive_support import ContactFixture
from tests.test_management_access import INSTALLATION, NOW, ROLE, SCOPE
from tests.test_role_tool_loop import answered


class ProactiveContactTests(ContactFixture, unittest.IsolatedAsyncioTestCase):
    def presentation_settings(self, app, changes):
        context = self.contact_context()
        target = ConfigTarget(INSTALLATION, ConfigLayer.WINDOW, ROLE, context.scope)
        snapshot = app.configuration.read(self.important, target=target, group_id=PRESENTATION_GROUP,
                                          observed_at=self.clock.now()).unwrap()
        return app.configuration.patch(self.important, target=target, group_id=PRESENTATION_GROUP,
            expected_edit_token=snapshot.edit_token,
            changes={PRESENTATION_GROUP + "." + key: value for key, value in changes.items()},
            operation=self.operation(context.scope, owner=PRESENTATION_GROUP), observed_at=self.clock.now())

    async def test_skip_is_counted_but_does_not_consume_the_daily_contact_allowance(self):
        app = await self.enabled()
        self.settings(app, {"max_contacts_24h": 1}).unwrap()

        async def skip(_envelope):
            return answered('{"decision":"skip","text":"","skip_reason":"repetitive"}')

        self.model_behavior = skip
        skipped = (await self.contact(app, self.opportunity("skip-does-not-send"))).unwrap().task
        skipped_observation = app.proactive.observe(self.contact_context(), skipped.task_id).unwrap()
        self.assertTrue(skipped_observation["skipped"])
        self.assertEqual(skipped_observation["stage_metrics"]["model"]["provider_calls"], 1)
        self.assertEqual(skipped_observation["stage_metrics"]["delivery"]["expected_parts"], 0)

        usage = app.proactive.budget.usage_summary(self.contact_context(), self.preferences(app), self.clock.now())
        self.assertEqual(usage["decisions"]["session"]["used"], 1)
        self.assertEqual((usage["decisions"]["session"]["calls"], usage["decisions"]["session"]["pending"]), (1, 0))
        self.assertEqual(usage["deliveries"]["session"]["used"], 0)
        self.assertEqual(usage["deliveries"]["session"]["delivered"], 0)
        self.assertEqual(usage["model"]["session"]["calls"], 1)

        self.model_behavior = None
        sent = (await self.contact(app, self.opportunity("after-model-skip"))).unwrap().task
        await self.delivery_state(app, sent.task_id, "delivered")
        sent_observation = app.proactive.observe(self.contact_context(), sent.task_id).unwrap()
        self.assertEqual(sent_observation["stage_metrics"]["delivery"]["delivered_parts"], 1)

        usage = app.proactive.budget.usage_summary(self.contact_context(), self.preferences(app), self.clock.now())
        self.assertEqual(usage["decisions"]["session"]["used"], 2)
        self.assertEqual(usage["deliveries"]["session"]["used"], 1)
        self.assertEqual(usage["deliveries"]["session"]["occupied"], 1)
        self.assertEqual(usage["deliveries"]["session"]["delivered"], 1)
        self.assertEqual(usage["model"]["session"]["calls"], 2)

    async def test_selected_relationship_candidate_is_the_only_saved_and_prompted_origin(self):
        app = await self.enabled()
        self.settings(app, {"chat.enabled": True, "care.enabled": True}).unwrap()
        opportunity = self.opportunity("mixed-chat-relationship-candidates")
        role_instruction = "這是原角色人格，保持自然。"
        intents = (
            self.intent(opportunity, purpose="chat", identifier="chat-candidate",
                        summary="角色有一个独立的日常念头。"),
            self.intent(opportunity, purpose="care", identifier="care-candidate",
                        summary="这是一次关系表达机会。"),
        )
        origin = {
            "batch_id": "original-batch",
            "event_key": "original-event",
            "event_hash": "original-hash",
            "role_hash": digest(role_instruction),
            "relationship_event": {"mode": "slot", "purposes": ["care"]},
        }

        with patch.object(app.proactive.schedule, "validate_origin", return_value=None):
            task = (await app.proactive.contact(self.contact_context(), opportunity, intents,
                role_instruction=role_instruction, selection_seed="mixed-candidate-seed",
                automatic_origin=origin)).unwrap().task

        row = app.runtime.read_one("SELECT record_json FROM proactive_contacts WHERE task_id = ?",
                                   (task.task_id,))
        record = json.loads(row["record_json"])
        selected_purpose = record["purpose"]
        relationship_instruction = "这是主动表达方向，不代表用户说过特定关键词或当前有同等意愿。"
        if selected_purpose == "chat":
            self.assertNotIn("relationship_event", record["automatic_origin"])
            self.assertNotIn(relationship_instruction, self.called[-1].original.instruction)
        else:
            self.assertEqual(selected_purpose, "care")
            self.assertEqual(record["automatic_origin"]["relationship_event"]["purposes"], ["care"])
            self.assertIn(relationship_instruction, self.called[-1].original.instruction)

    async def test_proactive_contact_uses_saved_segmentation_and_delivers_ordered_parts(self):
        app = await self.enabled()
        self.presentation_settings(app, {"enabled": True, "llm_controlled": True,
            "rules_enabled": False, "part_gap_ms": 0, **TEXT_DEFAULTS,
            "content_cleanup_enabled": True, "content_cleanup_words": json.dumps(["\n"], ensure_ascii=False)}).unwrap()
        marker = "<<PRIVATE_COMPANION_SPLIT>>"
        async def segmented(envelope):
            return answered(json.dumps({"decision": "send", "text": f"第一句。\n{marker}\n第二句。"},
                                       ensure_ascii=False))
        self.model_behavior = segmented

        task = (await self.contact(app)).unwrap().task
        await self.until(lambda: len(self.sent) == 2)

        self.assertIn(marker, self.called[0].original.instruction)
        self.assertEqual([message.text for message in self.sent], ["第一句。", "第二句。"])
        self.assertEqual([message.sequence for message in self.sent], [0, 1])
        rows = app.proactive._delivery_rows(task.task_id)
        requests = [DeliveryRequest.restore(row["request_json"]) for row in rows]
        self.assertEqual([(request.sequence, request.part_count) for request in requests], [(0, 2), (1, 2)])
        self.assertEqual([row["state"] for row in rows], ["delivered", "delivered"])
        self.assertEqual(app.proactive.observe(self.contact_context(), task.task_id).unwrap()["delivery"], "delivered")
        self.assertEqual(app.background_history.interaction(self.contact_context(), self.clock.now())[
            "contacts_since_last_input"], 1)

    async def test_one_history_round_keeps_all_confirmed_proactive_segments(self):
        app = await self.enabled()
        self.settings(app, {"generation_history_limit": 1, "dedup_enabled": False}).unwrap()
        self.presentation_settings(app, {"enabled": True, "llm_controlled": True,
            "rules_enabled": False, "part_gap_ms": 0, **TEXT_DEFAULTS,
            "content_cleanup_enabled": True, "content_cleanup_words": json.dumps(["\n"], ensure_ascii=False)}).unwrap()
        marker = "<<PRIVATE_COMPANION_SPLIT>>"

        async def segmented(_envelope):
            return answered(json.dumps({"decision": "send", "text": f"第一段。\n{marker}\n第二段。"},
                                       ensure_ascii=False))

        self.model_behavior = segmented
        first = (await self.contact(app, self.opportunity("segmented-history-source"))).unwrap().task
        await self.until(lambda: len(self.sent) == 2)
        await self.delivery_state(app, first.task_id, "delivered")

        captured = app.background_history.capture(self.contact_context(), self.clock.now(),
            action="proactive.contact", limit=1)
        self.assertEqual(len(captured["references"]), 2)
        captured_items = app.background_history.read(captured, self.contact_context(), self.clock.now(),
            action="proactive.contact", round_metadata=True)
        self.assertEqual([item["text"] for item in captured_items], ["第一段。", "第二段。"])
        history = history_round_materials(captured_items)
        self.assertEqual(len(history), 1)
        self.assertEqual([part["text"] for part in history[0]["messages"]], ["第一段。", "第二段。"])
        self.assertEqual(history[0]["message_parts"],
                         {"received": [1, 2], "expected": 2, "complete": True})

    async def test_proactive_split_trims_blank_edges_when_cleanup_is_disabled(self):
        app = await self.enabled()
        self.presentation_settings(app, {"enabled": True, "llm_controlled": True,
            "part_gap_mode": "fixed", "part_gap_ms": 0, "content_cleanup_enabled": False}).unwrap()
        marker = "<<PRIVATE_COMPANION_SPLIT>>"

        async def segmented(_envelope):
            return answered(json.dumps({"decision": "send", "text":
                f"\n怎么会不理你呢。\n\n{marker}\n\n下午好呀～\n\n"}, ensure_ascii=False))

        self.model_behavior = segmented
        task = (await self.contact(app)).unwrap().task
        await self.until(lambda: len(self.sent) == 2)
        self.assertEqual([message.text for message in self.sent], ["怎么会不理你呢。", "下午好呀～"])
        rows = app.proactive._delivery_rows(task.task_id)
        requests = [DeliveryRequest.restore(row["request_json"]) for row in rows]
        for request, message in zip(requests, self.sent):
            self.assertEqual(app.proactive.delivery_text(request, self.clock.now()).unwrap(), message.text)

    async def test_future_segment_is_queued_before_its_spacing_window_and_delivered_once_due(self):
        app = await self.enabled()
        self.presentation_settings(app, {"enabled": True, "llm_controlled": True,
            "rules_enabled": False, "part_gap_mode": "fixed", "part_gap_ms": 1000,
            **TEXT_DEFAULTS, "content_cleanup_enabled": True,
            "content_cleanup_words": json.dumps(["\n"], ensure_ascii=False)}).unwrap()
        marker = "<<PRIVATE_COMPANION_SPLIT>>"

        async def segmented(_envelope):
            return answered(json.dumps({"decision": "send", "text": f"第一句。\n{marker}\n第二句。"},
                                       ensure_ascii=False))

        self.model_behavior = segmented
        task = (await self.contact(app)).unwrap().task
        await self.until(lambda: len(app.proactive._delivery_rows(task.task_id)) >= 1
                         and app.proactive._delivery_rows(task.task_id)[0]["state"] == "delivered")

        rows = app.proactive._delivery_rows(task.task_id)
        self.assertEqual(len(rows), 2)
        second = DeliveryRequest.restore(rows[1]["request_json"])
        self.assertEqual(second.sequence, 1)
        self.assertGreater(second.not_before, self.clock.now())
        self.assertEqual(app.proactive.delivery_text(second, self.clock.now()).unwrap(), "第二句。")

        self.clock.advance(timedelta(seconds=1))
        await self.until(lambda: len(self.sent) == 2)
        self.assertEqual([message.sequence for message in self.sent], [0, 1])
        self.assertEqual(app.proactive.observe(self.contact_context(), task.task_id).unwrap()["delivery"],
                         "delivered")

    async def test_legacy_single_part_receipt_keeps_the_original_hash_without_queuing_again(self):
        app = await self.enabled()
        task = (await self.contact(app)).unwrap().task
        delivered = await self.delivery_state(app, task.task_id, "delivered")
        request = DeliveryRequest.restore(delivered["request_json"])
        row = app.runtime.read_one("SELECT * FROM proactive_contacts WHERE contact_id = ?", (request.source_ref,))
        data = json.loads(row["record_json"])
        self.assertEqual(len(data["parts"]), 1)
        del data["parts"]
        with app.runtime.transaction() as connection:
            connection.execute("UPDATE proactive_contacts SET record_json = ? WHERE contact_id = ?",
                               (json.dumps(data, ensure_ascii=False), row["contact_id"]))
        restored = app.proactive._part_request(request.source_ref, request.context, data, 0, request.not_before)
        self.assertEqual(restored, request)
        with patch("companion_v2.proactive.service.logger.warning") as warning:
            self.assertFalse(app.proactive.delivery_confirmed(request.delivery_id))
        warning.assert_not_called()
        self.assertEqual(len(app.proactive._delivery_rows(task.task_id)), 1)
        self.assertEqual(len(self.sent), 1)
        data["expires_at"] = request.deadline_at.isoformat()
        other = self.other_window_context("legacy-other-window")
        with self.assertRaises(TaskError):
            app.proactive._part_request(request.source_ref, other, data, 0, request.not_before)

    async def test_expired_segmented_contacts_are_not_scanned_or_replayed(self):
        app = await self.enabled()
        self.presentation_settings(app, {"enabled": True, "llm_controlled": True,
            "rules_enabled": False, "part_gap_ms": 0}).unwrap()
        async def segmented(envelope):
            return answered(json.dumps({"decision": "send", "text":
                "第一句话。<<PRIVATE_COMPANION_SPLIT>>第二句话。"}, ensure_ascii=False))
        self.model_behavior = segmented
        with patch.object(app.proactive, "delivery_confirmed", return_value=False):
            task = (await self.contact(app)).unwrap().task
            await self.delivery_state(app, task.task_id, "delivered")
            self.clock.advance(timedelta(minutes=6))
            self.assertEqual(app.proactive.advance_deliveries(), 0)
        row = app.proactive._delivery_rows(task.task_id)[0]
        self.assertFalse(app.proactive.delivery_confirmed(row["delivery_id"]))
        self.assertEqual(len(app.proactive._delivery_rows(task.task_id)), 1)
        self.assertEqual(len(self.sent), 1)

    async def test_restart_queues_next_contact_part_after_confirmed_first_receipt(self):
        app = await self.enabled()
        self.presentation_settings(app, {"enabled": True, "llm_controlled": True,
            "rules_enabled": False, "part_gap_ms": 0}).unwrap()
        marker = "<<PRIVATE_COMPANION_SPLIT>>"
        async def segmented(envelope):
            return answered(json.dumps({"decision": "send", "text": f"第一句。\n{marker}\n第二句。"},
                                       ensure_ascii=False))
        self.model_behavior = segmented
        app.proactive.delivery_confirmed = lambda _: False

        task = (await self.contact(app)).unwrap().task
        await self.until(lambda: len(self.sent) == 1 and app.proactive._delivery_rows(task.task_id)[0]["state"] == "delivered")
        await app.close()

        restarted = (await self.start(self.host(runtime=self.open_runtime()), task_generation=1,
                                      model_generation=1)).application
        await self.until(lambda: len(self.sent) == 2)
        self.assertEqual((len(self.called), len(restarted.proactive._delivery_rows(task.task_id))), (1, 2))
        self.assertEqual([message.text for message in self.sent], ["第一句。", "第二句。"])

    async def test_full_delivery_queue_retries_the_next_part_without_replaying_the_first(self):
        from companion_v2.foundation.outcome import Outcome

        app = await self.enabled()
        self.presentation_settings(app, {"enabled": True, "llm_controlled": True,
            "rules_enabled": False, "part_gap_ms": 0}).unwrap()
        marker = "<<PRIVATE_COMPANION_SPLIT>>"
        async def segmented(envelope):
            return answered(json.dumps({"decision": "send", "text": f"第一句。\n{marker}\n第二句。"},
                                       ensure_ascii=False))
        self.model_behavior = segmented
        queue = app.outbound.queue
        attempted = []

        def temporarily_full(request):
            attempted.append(request.sequence)
            if request.sequence == 1 and attempted.count(1) == 1:
                return Outcome.failed("task_queue_full", "temporary capacity limit")
            return queue(request)

        with patch.object(app.outbound, "queue", side_effect=temporarily_full):
            task = (await self.contact(app)).unwrap().task
            await self.until(lambda: len(self.sent) == 2)

        self.assertGreaterEqual(attempted.count(1), 2)
        self.assertEqual([message.text for message in self.sent], ["第一句。", "第二句。"])
        self.assertEqual(len(app.proactive._delivery_rows(task.task_id)), 2)

    async def test_contact_uses_persona_evidence_shared_model_delivery_and_independent_receipts(self):
        app = await self.enabled()
        source = self.source(app)
        opportunity = self.opportunity()
        context = self.contact_context()
        result = (await self.contact(app, opportunity, (self.intent(opportunity, sources=(source,)),))).unwrap()
        task = result.task
        self.assertEqual(task.state, "succeeded")
        self.assertEqual(task.effect, "committed")
        row = await self.delivery_state(app, task.task_id, "delivered")
        self.assertEqual(len(self.called), 1)
        self.assertEqual(len(self.sent), 1)
        prompt = self.called[0].original
        self.assertIsInstance(prompt.event, BackgroundPromptTrigger)
        self.assertEqual(prompt.role_instruction, "这是原角色人格，保持自然。")
        self.assertEqual(prompt.output_format, "proactive_contact")
        self.assertEqual(build_wire(self.called[0]).output_format, "proactive_contact")
        self.assertEqual(prompt.context.evidence_sources, (source,))
        evidence = [item.payload for item in prompt.context.materials if item.purpose == "contact_evidence"]
        self.assertEqual(len(evidence), 1)
        self.assertIn("用户先前说过", evidence[0])
        self.assertIn("未回应", prompt.instruction)
        self.assertEqual(self.sent[0].context, context)
        self.assertEqual(self.sent[0].text, "想起你上次提到的书，读到哪里了？")
        self.assertEqual(self.runtime.read_one("SELECT COUNT(*) FROM sources", ())[0], 1)
        self.assertNotIn(self.sent[0].text, row["request_json"])
        status = app.proactive.observe(context, task.task_id).unwrap()
        self.assertEqual((status["delivery"], status["receipt_ref"]), ("delivered", row["receipt_ref"]))
        adoption = app.configuration.application_status(self.important, target=target_for(context), group_id=GROUP, observed_at=NOW).unwrap()
        self.assertEqual(adoption.state, "applied")

    async def test_disabled_by_default_and_missing_profile_do_not_call_foreground_provider(self):
        app = (await self.start()).application
        self.assertEqual((await self.contact(app)).failure.code, "no_feasible_thought")
        self.assertEqual(self.called, [])
        adoption = app.configuration.application_status(self.important, target=target_for(self.contact_context()),
            group_id=GROUP, observed_at=NOW).unwrap()
        self.assertEqual(adoption.state, "applied")
        await app.close()
        missing = (await self.start(self.host(profiles=self.profiles[:-1]), task_generation=1, model_generation=1)).application
        self.settings(missing, {"enabled": True}).unwrap()
        self.assertEqual((await self.contact(missing)).failure.code, "model_task_unregistered")
        self.assertEqual(self.called, [])

    async def test_skip_is_successful_model_decision_without_draft_or_delivery(self):
        app = await self.enabled()
        async def skip(envelope):
            return answered('{"decision":"skip","text":"","skip_reason":"no_natural_opening"}')
        self.model_behavior = skip
        task = (await self.contact(app)).unwrap().task
        self.assertEqual(task.state, "succeeded")
        observation = app.proactive.observe(self.contact_context(), task.task_id).unwrap()
        self.assertTrue(observation["skipped"])
        self.assertEqual((observation["skip_reason"], observation["model_skip_reason"]),
                         ("model_decision", "no_natural_opening"))
        record = self.runtime.read_one("SELECT record_json, body FROM proactive_contacts", ())
        self.assertEqual((json.loads(record["record_json"])["decision"], record["body"]), ("skip", ""))
        self.assertEqual(self.runtime.read_one("SELECT COUNT(*) FROM background_deliveries", ())[0], 0)
        self.assertEqual(self.sent, [])
        replay = (await self.contact(app)).unwrap().task
        self.assertEqual(replay.task_id, task.task_id)
        self.assertEqual(len(self.called), 1)

    async def test_low_confidence_send_is_saved_as_skip_without_delivery(self):
        app = await self.enabled()
        self.settings(app, {"silence_min_confidence": 0.8}).unwrap()
        async def uncertain_send(envelope):
            return answered('{"decision":"send","text":"想说一句","decision_confidence":0.62}')
        self.model_behavior = uncertain_send
        task = (await self.contact(app)).unwrap().task
        record = json.loads(self.runtime.read_one(
            "SELECT record_json, body FROM proactive_contacts WHERE task_id = ?", (task.task_id,))["record_json"])
        self.assertEqual(record["decision"], "skip")
        self.assertEqual(record["decision_confidence"], 0.62)
        self.assertEqual(record["silence_reason"], "below_confidence_threshold")
        self.assertEqual(app.proactive.observe(self.contact_context(), task.task_id).unwrap()["skip_reason"],
                         "low_confidence")
        self.assertEqual(self.runtime.read_one("SELECT COUNT(*) FROM background_deliveries", ())[0], 0)
        self.assertEqual(self.sent, [])

    def test_contact_output_rejects_invalid_decision_confidence(self):
        for confidence in ("0.8", -0.01, 1.01):
            with self.subTest(confidence=confidence):
                with self.assertRaises(ValueError):
                    contact_output(json.dumps({
                        "decision": "send", "text": "正文", "decision_confidence": confidence
                    }))

    def test_contact_output_accepts_only_bounded_skip_reason_categories(self):
        valid = '{"decision":"skip","text":"","skip_reason":"insufficient_context"}'
        self.assertEqual(contact_output(valid), (None, None))
        for value in ("private explanation", [], 4):
            with self.subTest(value=value), self.assertRaises(ValueError):
                contact_output(json.dumps({"decision": "skip", "text": "", "skip_reason": value}))
        with self.assertRaises(ValueError):
            contact_output('{"decision":"send","text":"正文","skip_reason":"other"}')

    async def test_replay_keeps_original_and_changed_opportunity_inputs_are_rejected(self):
        app = await self.enabled()
        opportunity = self.opportunity()
        intent = self.intent(opportunity)
        task = (await self.contact(app, opportunity, (intent,))).unwrap().task
        await self.delivery_state(app, task.task_id, "delivered")
        self.assertEqual((await self.contact(app, opportunity, (intent,))).unwrap().task.task_id, task.task_id)
        changed = replace(intent, thought=replace(intent.thought, summary="重写原机会"))
        self.assertEqual((await self.contact(app, opportunity, (changed,))).failure.code, "contact_request_conflict")
        self.assertEqual((len(self.called), len(self.sent)), (1, 1))

    async def test_committed_skip_survives_task_fencing_after_local_receipt(self):
        app = await self.enabled()
        async def skip(envelope):
            return answered('{"decision":"skip","text":""}')
        self.model_behavior = skip
        def changed_after_commit():
            self.settings(app, {"note": "提交之后调整偏好"}).unwrap()
            app.notify()
        with patch.object(app.proactive, "notify", side_effect=changed_after_commit):
            task = (await self.contact(app)).unwrap().task
        self.assertNotEqual(task.state, "succeeded")
        self.assertTrue(app.proactive.observe(self.contact_context(), task.task_id).unwrap()["skipped"])
        self.assertEqual(self.sent, [])

    async def test_single_eligible_thought_does_not_use_weight_or_turn_weight_into_probability(self):
        app = await self.enabled()
        self.settings(app, {"news.enabled": False, "news.weight": 1000000.0, "chat.weight": 0.000001}).unwrap()
        opportunity = self.opportunity()
        intents = (self.intent(opportunity, purpose="news", identifier="news"), self.intent(opportunity, identifier="chat"))
        task = (await self.contact(app, opportunity, intents)).unwrap().task
        record = json.loads(self.runtime.read_one("SELECT record_json FROM proactive_contacts WHERE task_id = ?", (task.task_id,))[0])
        self.assertEqual((record["thought_id"], record["feasible_count"], record["weight_used"]), ("chat", 1, False))
        await self.delivery_state(app, task.task_id, "delivered")

    async def test_future_expired_or_wrong_window_opportunities_do_not_generate(self):
        app = await self.enabled()
        opportunity = self.opportunity()
        for item in (replace(opportunity, opened_at=NOW + timedelta(seconds=1)),
                     replace(opportunity, opened_at=NOW - timedelta(minutes=1), expires_at=NOW),
                     replace(opportunity, scope=replace(SCOPE, session_id="another"))):
            self.assertFalse((await self.contact(app, item)).is_success)
        self.assertEqual(self.called, [])

    async def test_source_read_grant_and_actual_audience_remain_required(self):
        app = await self.enabled()
        source = self.source(app, recipients=(self.core.subject,))
        opportunity = self.opportunity()
        self.assertEqual((await self.contact(app, opportunity, (self.intent(opportunity, sources=(source,)),))).failure.code,
                         "no_feasible_thought")
        visible = self.source(app, "visible")
        row = self.runtime.read_one("SELECT grant_id FROM identity_grants WHERE capability = 'text'", ())
        self.identity.revoke_grant(row[0], expected_revision=self.identity.revision, revoked_at=NOW)
        self.assertEqual((await self.contact(app, opportunity, (self.intent(opportunity, sources=(visible,)),))).failure.code,
                         "no_feasible_thought")
        self.assertEqual(self.called, [])

    async def test_changed_preferences_during_generation_discard_output_but_keep_usage(self):
        app = await self.enabled()
        async def change(envelope):
            self.settings(app, {"note": "现在只想安静一些"}).unwrap()
            return answered('{"decision":"send","text":"旧偏好下的文本"}')
        self.model_behavior = change
        task = (await self.contact(app)).unwrap().task
        self.assertNotEqual(task.state, "succeeded")
        row = self.runtime.read_one("SELECT state, actual_tokens, actual_cost FROM model_calls", ())
        self.assertEqual(row["state"], "discarded")
        self.assertEqual((row["actual_tokens"], row["actual_cost"]), (15, 40))
        self.assertEqual(self.runtime.read_one("SELECT COUNT(*) FROM model_call_accounts", ())[0] > 0, True)
        self.assertEqual(self.runtime.read_one("SELECT COUNT(*) FROM proactive_contacts", ())[0], 0)
        self.assertEqual(self.sent, [])

    async def test_revoked_source_during_generation_discards_old_draft(self):
        app = await self.enabled()
        source = self.source(app)
        opportunity = self.opportunity()
        async def revoke(envelope):
            app.proactive.sources.revoke(source, expected_governance_revision=1, observed_at=NOW).unwrap()
            return answered('{"decision":"send","text":"已经失效的来源"}')
        self.model_behavior = revoke
        result = await self.contact(app, opportunity, (self.intent(opportunity, sources=(source,)),))
        self.assertNotEqual(result.unwrap().task.state, "succeeded")
        self.assertEqual(self.sent, [])

    async def test_quiet_hours_starting_during_generation_prevent_delivery(self):
        app = await self.enabled()
        self.settings(app, {"quiet_enabled": True, "quiet_timezone": "UTC", "quiet_start": "00:01", "quiet_end": "00:02"}).unwrap()
        async def advance(envelope):
            self.clock.advance(timedelta(minutes=1))
            return answered('{"decision":"send","text":"进入免打扰后的旧稿"}')
        self.model_behavior = advance
        result = await self.contact(app)
        self.assertNotEqual(result.unwrap().task.state, "succeeded")
        self.assertEqual(self.sent, [])

    async def test_pause_at_actual_sender_boundary_prevents_wire_effect(self):
        app = await self.enabled()
        wire_calls = []
        async def checked(envelope):
            self.settings(app, {"pause_until": (NOW + timedelta(hours=1)).isoformat()}).unwrap()
            try:
                envelope.before_dispatch()
            except TaskError:
                return self.receipt(envelope, "not_sent")
            wire_calls.append(envelope)
            return self.receipt(envelope)
        self.send_behavior = checked
        task = (await self.contact(app)).unwrap().task
        await self.delivery_state(app, task.task_id, "failed")
        self.assertEqual(wire_calls, [])

    async def test_two_eventchecker_refusals_pause_contact_generation_for_the_account(self):
        app = await self.enabled()
        context = self.contact_context()
        now = self.clock.now()
        for index in range(2):
            payload = f"eventchecker refusal {index}"
            request = DeliveryRequest(GROUP, f"eventchecker-{index}", context,
                f"eventchecker-source-{index}", digest(payload), now, now + timedelta(minutes=5))
            receipt = DeliveryReceipt(request.delivery_id, f"eventchecker-operation-{index}", context.key,
                request.payload_hash, self.sender.binding, "not_sent", f"eventchecker-receipt-{index}",
                failure_code="qq_eventchecker_1200")
            app.outbound.on_receipt(request, receipt)
            if index == 0:
                app.outbound.on_receipt(request, replace(receipt, status="unknown",
                    receipt_ref="eventchecker-unknown-receipt", failure_code=None))

        generated_before_pause = len(self.called)
        paused = await self.contact(app, self.opportunity("eventchecker-paused"))

        self.assertEqual(paused.failure.code, "proactive_platform_cooldown")
        self.assertEqual(len(self.called), generated_before_pause)
        expected_until = now + timedelta(hours=2)
        self.assertEqual(app.proactive.platform_circuit.cooldown_until(
            self.contact_context(), at=self.clock.now()), expected_until)
        other_window = self.other_window_context()
        paused_other_window = await self.contact(app, self.opportunity(
            "eventchecker-cross-window", scope=other_window.scope), context=other_window)
        self.assertEqual(paused_other_window.failure.code, "proactive_platform_cooldown")
        self.clock.advance(timedelta(hours=2))
        self.assertIsNone(app.proactive.platform_circuit.cooldown_until(
            self.contact_context(), at=self.clock.now()))

    async def test_unknown_and_other_not_sent_receipts_do_not_trip_platform_circuit(self):
        app = await self.enabled()

        async def refused(envelope):
            return replace(self.receipt(envelope, "not_sent"), failure_code="authorization_revoked")

        self.send_behavior = refused
        task = (await self.contact(app)).unwrap().task
        await self.delivery_state(app, task.task_id, "failed")

        self.assertIsNone(app.proactive.platform_circuit.cooldown_until(
            self.contact_context(), at=self.clock.now()))
        self.assertEqual(len(self.called), 1)

    async def test_queued_draft_is_cancelled_on_pause_and_does_not_return_when_pause_expires(self):
        app = await self.enabled()
        opportunity = self.opportunity()
        intent = self.intent(opportunity)
        with patch.object(app.background, "advance", return_value=False):
            task = (await self.contact(app, opportunity, (intent,))).unwrap().task
            self.settings(app, {"pause_until": (NOW + timedelta(seconds=1)).isoformat()}).unwrap()
        app.notify()
        await self.delivery_state(app, task.task_id, "cancelled")
        self.clock.advance(timedelta(seconds=2))
        app.notify()
        self.assertEqual((await self.contact(app, opportunity, (intent,))).unwrap().task.task_id, task.task_id)
        self.assertEqual(self.sent, [])
        self.assertEqual(len(self.called), 1)

    async def test_unknown_is_not_replayed_after_restart_and_late_receipt_still_counts(self):
        app = await self.enabled()
        async def unknown(envelope):
            return self.receipt(envelope, "unknown")
        self.send_behavior = unknown
        opportunity = self.opportunity()
        task = (await self.contact(app, opportunity)).unwrap().task
        await self.delivery_state(app, task.task_id, "unknown")
        envelope = self.sent[0]
        await app.close()
        new = (await self.start(self.host(runtime=self.open_runtime()), task_generation=1, model_generation=1)).application
        self.settings(new, {"enabled": False}).unwrap()
        self.assertEqual((await self.contact(new, opportunity)).unwrap().task.task_id, task.task_id)
        self.assertEqual((len(self.called), len(self.sent)), (1, 1))
        self.assertEqual(new.record_receipt(self.receipt(envelope)).unwrap(), "delivered")
        self.assertEqual(new.proactive.observe(self.contact_context(), task.task_id).unwrap()["delivery"], "delivered")

    async def test_queued_draft_survives_restart_without_another_model_call(self):
        app = await self.enabled()
        with patch.object(app.background, "advance", return_value=False):
            task = (await self.contact(app)).unwrap().task
            original = app.proactive._delivery_row(task.task_id)["request_json"]
            await app.close()
        new = (await self.start(self.host(runtime=self.open_runtime()), task_generation=1, model_generation=1)).application
        row = await self.delivery_state(new, task.task_id, "delivered")
        self.assertEqual(row["request_json"], original)
        self.assertEqual((len(self.called), len(self.sent)), (1, 1))

    async def test_restart_does_not_extend_draft_deadline(self):
        app = await self.enabled()
        with patch.object(app.background, "advance", return_value=False):
            task = (await self.contact(app)).unwrap().task
            await app.close()
        self.clock.advance(timedelta(minutes=6))
        new = (await self.start(self.host(runtime=self.open_runtime()), task_generation=1, model_generation=1)).application
        await self.delivery_state(new, task.task_id, "cancelled")
        self.assertEqual(self.sent, [])

    async def test_role_rebind_cancels_original_queued_contact(self):
        app = await self.enabled()
        with patch.object(app.background, "advance", return_value=False):
            task = (await self.contact(app)).unwrap().task
            binding = self.identity.resolve_role(SCOPE, observed_at=NOW).unwrap()
            self.identity.bind_role(scope=SCOPE, role=SubjectRef(INSTALLATION, SubjectKind.BOT, "new-persona"),
                expected_revision=binding.revision, observed_at=NOW)
        app.notify()
        await self.delivery_state(app, task.task_id, "cancelled")
        self.assertEqual(self.sent, [])

    async def test_delivery_payload_or_logical_key_cannot_be_replaced(self):
        app = await self.enabled()
        with patch.object(app.background, "advance", return_value=False):
            task = (await self.contact(app)).unwrap().task
            request = DeliveryRequest.restore(app.proactive._delivery_row(task.task_id)["request_json"])
            for forged in (replace(request, payload_hash="other"), replace(request, logical_key="another"),
                           replace(request, deadline_at=NOW + timedelta(hours=1))):
                self.assertFalse(app.outbound.queue(forged).is_success)
        self.assertEqual(self.runtime.read_one("SELECT COUNT(*) FROM background_deliveries", ())[0], 1)

    async def test_explicit_cancellation_stops_queued_delivery_and_observation_is_authorized(self):
        app = await self.enabled()
        context = self.contact_context()
        with patch.object(app.background, "advance", return_value=False):
            task = (await self.contact(app)).unwrap().task
            result = app.proactive.cancel(context, task.task_id, expected_revision=task.revision).unwrap()
            self.assertEqual(result["delivery"], "cancelled")
        self.assertFalse(app.proactive.observe(replace(context, actor=self.core), task.task_id).is_success)
        self.assertEqual(self.sent, [])

    async def test_pause_does_not_suppress_previously_accepted_agenda_reminder(self):
        app = await self.enabled()
        self.settings(app, {"pause_until": (NOW + timedelta(hours=1)).isoformat(), "reminder.enabled": False}).unwrap()
        context = self.contact_context()
        app.agenda.create_reminder(self.important, target=AgendaTarget(SCOPE, ROLE, self.important.subject),
            reminder_id="promised", message="按原约定提醒喝水", schedule=Schedule(NOW, "UTC"),
            operation=self.operation(SCOPE, owner="agenda"), expected_role_revision=context.role_binding.revision,
            observed_at=NOW).unwrap()
        app.notify()
        await self.until(lambda: len(self.sent) == 1)
        self.assertEqual(self.sent[0].text, "按原约定提醒喝水")
        self.assertEqual(self.called, [])

    async def test_invalid_model_contract_keeps_usage_and_cannot_be_sent(self):
        app = await self.enabled()
        async def invalid(envelope):
            return answered('{"decision":"send","text":""}')
        self.model_behavior = invalid
        result = await self.contact(app)
        self.assertNotEqual(result.unwrap().task.state, "succeeded")
        self.assertEqual(self.runtime.read_one("SELECT state FROM model_calls", ())[0], "discarded")
        self.assertEqual(self.sent, [])

    async def test_an_answer_that_is_not_json_is_skipped_instead_of_failing_the_task(self):
        """One prose answer must not become ``unhandled_exception``.

        The JSON decode had no guard, so a model that answered prose raised a
        ValueError the supervisor could only report as an unhandled task failure —
        the same word it uses for a crash, with no hint about the answer. The round
        now records a skip and the log names the malformed answer without quoting
        it.
        """
        app = await self.enabled()
        async def prose(_envelope):
            return answered("这句不是 JSON，只是一句随手写的话。")
        self.model_behavior = prose
        with self.assertLogs("companion_v2.proactive.service", level="WARNING") as logs:
            task = (await self.contact(app)).unwrap().task

        self.assertEqual(task.state, "succeeded", "a malformed answer is not a task failure")
        self.assertEqual(self.sent, [])
        [warning] = [record.getMessage() for record in logs.records
                     if "不是合法 JSON" in record.getMessage()]
        self.assertIn("raw_chars=", warning)
        self.assertNotIn("随手写的话", warning, "the model body must never reach the log")
        document = json.loads(self.runtime.read_one(
            "SELECT record_json FROM proactive_contacts", ())["record_json"])
        self.assertEqual(document["decision"], "skip")
        self.assertEqual(document["model_skip_reason"], "other")

    async def test_shutdown_waits_for_stubborn_model_and_discards_late_output(self):
        entered, release = asyncio.Event(), asyncio.Event()
        self.addCleanup(release.set)
        async def stubborn(envelope):
            entered.set()
            while not release.is_set():
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    pass
            return answered('{"decision":"send","text":"停机之后的旧结果"}')
        self.model_behavior = stubborn
        app = await self.enabled(owns_runtime=True)
        invocation = asyncio.create_task(self.contact(app))
        await asyncio.wait_for(entered.wait(), 2)
        report = await app.close(grace_seconds=0)
        self.assertEqual((report.state, report.storage_closed), ("draining", False))
        self.assertTrue(report.pending_handles)
        release.set()
        await asyncio.gather(invocation, return_exceptions=True)
        await self.until(lambda: not app.active_handles)
        self.assertEqual(self.sent, [])
        self.assertEqual(self.runtime.read_one("SELECT state FROM model_calls", ())[0], "discarded")
        self.assertTrue((await app.close(grace_seconds=0)).storage_closed)

    async def test_delivery_capacity_failure_rolls_back_draft_and_local_operation_together(self):
        app = await self.enabled()
        async def full(envelope):
            return answered('{"decision":"send","text":"准备好的文本"}')
        self.model_behavior = full
        from companion_v2.foundation.outcome import Outcome
        with patch.object(app.outbound, "queue", return_value=Outcome.failed("task_queue_full", "no delivery capacity")):
            task = (await self.contact(app)).unwrap().task
        self.assertNotEqual(task.state, "succeeded")
        self.assertEqual(self.runtime.read_one("SELECT COUNT(*) FROM proactive_contacts", ())[0], 0)
        self.assertEqual(self.runtime.read_one("SELECT COUNT(*) FROM operations WHERE operation_id LIKE 'save-contact-%'", ())[0], 0)
        self.assertEqual(self.runtime.read_one("SELECT state FROM model_calls", ())[0], "discarded")

    async def test_exhausted_budget_does_not_dispatch_a_model_or_sender(self):
        app = await self.enabled(budget=BudgetPolicy(*(Amount(0, 0) for _ in range(5))))
        task = (await self.contact(app)).unwrap().task
        self.assertNotEqual(task.state, "succeeded")
        self.assertEqual((self.called, self.sent), ([], []))

    async def test_model_decision_calls_are_counted_without_a_separate_hard_limit(self):
        app = await self.enabled()
        self.settings(app, {"max_contacts_24h": 1}).unwrap()

        async def skip(_envelope):
            return answered('{"decision":"skip","text":""}')

        self.model_behavior = skip
        task = (await self.contact(app)).unwrap().task
        self.assertEqual(task.state, "succeeded")
        preferences = self.preferences(app)
        usage = app.proactive.budget.attempt_usage(self.contact_context(), preferences, self.clock.now())
        self.assertEqual(usage["session"]["used"], 1)
        self.assertNotIn("relationship", usage)
        self.assertNotIn("limit", usage["session"])

        self.model_behavior = None
        sent = (await self.contact(app, self.opportunity("after-counted-skip"))).unwrap().task
        await self.delivery_state(app, sent.task_id, "delivered")
        self.assertEqual(len(self.sent), 1)

        for state in ("failed", "cancelled", "expired", "dead_letter"):
            with app.runtime.transaction() as connection:
                connection.execute("UPDATE execution_tasks SET state = ? WHERE task_id = ?", (state, task.task_id))
            usage = app.proactive.budget.attempt_usage(self.contact_context(), preferences, self.clock.now())
            self.assertEqual(usage["session"]["used"], 2, state)
            self.assertNotIn("relationship", usage)

    async def test_administrator_diagnostic_surfaces_local_model_budget_deferral(self):
        app = await self.enabled(budget=BudgetPolicy(*(Amount(1, 100_000) for _ in range(5))))

        result = await self.contact(app, administrator_test=True)

        self.assertEqual(result.failure.code, "model_budget_exhausted")
        self.assertEqual((self.called, self.sent), ([], []))

    async def test_local_laya_events_are_not_provider_cost_or_contact_quota_usage(self):
        app = await self.enabled()
        context = self.contact_context()
        preferences = self.preferences(app)
        with app.runtime.transaction() as connection:
            connection.execute("""INSERT INTO laya_decision_events
                (event_id, origin_key, batch_id, source, status, mode, elapsed_ms,
                 enabled_fields_json, observed_at)
                VALUES (?, ?, ?, 'conversation', 'ok', 'local', 8, '{}', ?)""",
                ("a" * 32, context.key, "local-laya-test", self.clock.now().isoformat()))

        usage = app.proactive.budget.usage_summary(context, preferences, self.clock.now())
        self.assertEqual(usage["model"]["session"]["calls"], 0)
        self.assertIsNone(usage["model"]["session"]["cost_microusd"])
        self.assertEqual(usage["decisions"]["session"]["used"], 0)
        self.assertEqual(usage["deliveries"]["session"]["used"], 0)

    async def test_administrator_delivery_tests_bypass_contact_windows_and_do_not_consume_daily_limits(self):
        app = await self.enabled()
        self.settings(app, {"max_contacts_24h": 0,
            "pause_until": (NOW + timedelta(hours=1)).isoformat(), "quiet_enabled": True,
            "quiet_start": "00:00", "quiet_end": "00:00"}).unwrap()

        first = (await self.contact(app, self.opportunity("diagnostic-first"), administrator_test=True)).unwrap().task
        await self.delivery_state(app, first.task_id, "delivered")
        self.assertEqual(len(self.sent), 1)

        preferences = self.preferences(app)
        self.assertTrue(app.proactive._duplicate(self.contact_context(), self.sent[0].text, preferences,
            self.clock.now(), purpose="chat", contact_id="ordinary-probe"))
        self.assertFalse(app.proactive._duplicate(self.contact_context(), self.sent[0].text, preferences,
            self.clock.now(), purpose="chat", contact_id="test-probe", administrator_test=True))
        usage = app.proactive.budget.attempt_usage(self.contact_context(), preferences, self.clock.now())
        self.assertEqual((usage["session"]["used"], usage["session"]["date"],
                          usage["session"]["timezone"]), (0, "2026-09-21", "Asia/Shanghai"))
        self.assertNotIn("relationship", usage)
        self.assertEqual(preferences.reason("chat"), "proactive_paused")

        self.settings(app, {"max_contacts_24h": 1}).unwrap()
        ordinary = await self.contact(app, self.opportunity("ordinary-during-pause"))
        self.assertEqual(ordinary.failure.code, "no_feasible_thought")

        self.settings(app, {"pause_until": "", "quiet_enabled": False}).unwrap()
        preferences = self.preferences(app)
        app.proactive.budget.contacts(self.contact_context(), preferences, self.clock.now())
        usage = app.proactive.budget.attempt_usage(self.contact_context(), preferences, self.clock.now())
        self.assertEqual((usage["session"]["used"], usage["session"]["date"],
                          usage["session"]["timezone"]), (0, "2026-09-21", "Asia/Shanghai"))

    async def test_administrator_delivery_test_omits_history_memory_and_repeat_guidance(self):
        app = await self.enabled()
        self.settings(app, {"play.enabled": True}).unwrap()

        class HistoryProbe:
            def capture(self, *_args, **_kwargs):
                raise AssertionError("a manual diagnostic must not read private conversation history")

            def interaction(self, *_args, **_kwargs):
                raise AssertionError("a manual diagnostic must not read private interaction state")

            def latest_reply(self, *_args, **_kwargs):
                raise AssertionError("a manual diagnostic must not read the last reply")

        class MemoryProbe:
            def contact_capture(self, *_args, **_kwargs):
                raise AssertionError("a manual diagnostic must not recall private memory")

        app.proactive.history = HistoryProbe()
        app.proactive.memory_context = MemoryProbe()
        opportunity = self.opportunity("diagnostic-isolated")
        intent = self.intent(opportunity, purpose="play", summary="一次轻松互动测试")
        task = (await self.contact(app, opportunity, (intent,), administrator_test=True)).unwrap().task

        self.assertEqual(task.state, "succeeded")
        prompt = self.called[0].original
        self.assertEqual(intent.purpose, "play")
        purposes = {item.purpose for item in prompt.context.materials}
        self.assertNotIn("communication_state", purposes)
        self.assertNotIn("dedup_candidate", purposes)
        self.assertNotIn("last_confirmed_reply", purposes)
        self.assertNotIn("只用标为 dedup_candidate", prompt.instruction)
        self.assertIn("不要把缺少这些材料判为重复", prompt.instruction)
        self.assertIn("除程序负责的授权和安全检查外，请选择 send", prompt.instruction)

    async def test_confirmed_administrator_test_does_not_enable_the_saved_global_default(self):
        app = await self.enabled()
        global_target = target_for(self.contact_context()).chain()[0]
        self.settings(app, {"enabled": False, "automatic_enabled": True}, target=global_target, actor=self.core).unwrap()
        ordinary = await self.contact(app, self.opportunity("ordinary-master-off"))
        self.assertEqual(ordinary.failure.code, "no_feasible_thought")
        task = (await self.contact(app, self.opportunity("confirmed-master-off"),
                                   administrator_test=True)).unwrap().task
        await self.delivery_state(app, task.task_id, "delivered")
        self.assertEqual(len(self.sent), 1)
        public = app.configuration.read(self.core, target=global_target, group_id=GROUP,
                                        observed_at=self.clock.now()).unwrap()
        self.assertFalse(public.values[GROUP + ".enabled"])
        self.assertEqual(self.preferences(app).reason("chat"), "proactive_disabled")
        self.settings(app, {"chat.enabled": False}).unwrap()
        purpose_off = await self.contact(app, self.opportunity("test-purpose-off"), administrator_test=True)
        self.assertEqual(purpose_off.failure.code, "no_feasible_thought")
        self.settings(app, {"chat.enabled": True}).unwrap()
        self.grant(self.important, self.contact_context().scope, GROUP)
        self.policy.set_tier(self.important.subject, AuthorizationTier.STANDARD,
            expected_revision=self.policy.revision, observed_at=self.clock.now())
        self.assertTrue(app.tasks.check_context_access(self.contact_context(), action=GROUP,
                                                      observed_at=self.clock.now()).is_success)
        for grant in self.identity.snapshot().grants:
            if grant.capability == GROUP and grant.scope == self.contact_context().scope:
                self.identity.revoke_grant(grant.grant_id,
                    expected_revision=self.identity.revision, revoked_at=self.clock.now())
        denied = await self.contact(app, self.opportunity("test-revoked"), administrator_test=True)
        self.assertEqual(denied.failure.code, "authorization_revoked")
        self.assertEqual(len(self.sent), 1)

    async def test_accepted_receipt_is_not_reported_as_delivered(self):
        app = await self.enabled()
        async def accepted(envelope):
            return self.receipt(envelope, "accepted")
        self.send_behavior = accepted
        task = (await self.contact(app)).unwrap().task
        await self.delivery_state(app, task.task_id, "accepted")
        self.assertEqual(app.proactive.observe(self.contact_context(), task.task_id).unwrap()["delivery"], "accepted")
        app.record_receipt(self.receipt(self.sent[0])).unwrap()
        self.assertEqual(app.proactive.observe(self.contact_context(), task.task_id).unwrap()["delivery"], "delivered")

    async def test_temporary_pause_expiry_allows_a_new_opportunity_without_rewriting_settings(self):
        app = await self.enabled()
        self.settings(app, {"pause_until": (NOW + timedelta(seconds=1)).isoformat()}).unwrap()
        self.assertEqual((await self.contact(app)).failure.code, "no_feasible_thought")
        self.clock.advance(timedelta(seconds=1))
        task = (await self.contact(app, self.opportunity("after-pause"))).unwrap().task
        await self.delivery_state(app, task.task_id, "delivered")
        self.assertEqual(len(self.called), 1)

    async def test_role_state_reaches_contact_intent_and_pacing_settings(self):
        app = await self.enabled()
        context = self.contact_context()
        with patch.object(app.role_state, "read", return_value={
            "state": "active",
            "values": {"energy": 25, "sleep": "昨晚熬夜有点困", "mood": "微倦但平静"},
            "sleep_execution": {"state": "awake", "awake_minutes": 10},
        }):
            self.presentation_settings(app, {"enabled": True, "llm_controlled": True,
                "rules_enabled": False, "part_gap_mode": "log", "part_gap_min_ms": 1500,
                "part_gap_max_ms": 5000, **TEXT_DEFAULTS}).unwrap()
            task = (await self.contact(app, self.opportunity("with-role-state"))).unwrap().task
            await self.delivery_state(app, task.task_id, "delivered")
        intent_material = next(m for m in self.called[-1].original.context.materials
                               if m.purpose == "contact_intent")
        payload = json.loads(intent_material.payload)
        self.assertEqual(payload["role_state"]["energy"], 25)
        self.assertEqual(payload["role_state"]["sleep"], "昨晚熬夜有点困")
        row = app.runtime.read_one("SELECT record_json FROM proactive_contacts WHERE task_id = ?", (task.task_id,))
        seg = json.loads(row["record_json"])["segmentation_settings"]
        self.assertEqual(seg.get("role_energy"), 25)
        self.assertTrue(seg.get("role_sleepy"))

    async def test_awake_sleep_prose_does_not_force_sleepy_contact_pacing(self):
        app = await self.enabled()
        with patch.object(app.role_state, "read", return_value={
            "state": "active", "values": {"energy": 85, "sleep": "睡得很好，不困了",
                "conditions": [{"kind": "other", "text": "打算睡前再看一章"}]},
            "sleep_execution": {"state": "awake", "awake_minutes": 120},
        }):
            task = (await self.contact(app, self.opportunity("awake-prose"))).unwrap().task
        row = app.runtime.read_one("SELECT record_json FROM proactive_contacts WHERE task_id = ?", (task.task_id,))
        self.assertFalse(json.loads(row["record_json"])["segmentation_settings"].get("role_sleepy", False))
        intent = next(item for item in self.called[-1].original.context.materials if item.purpose == "contact_intent")
        self.assertEqual(json.loads(intent.payload)["role_state"]["sleep"], "睡得很好，不困了")
