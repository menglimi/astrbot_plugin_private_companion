"""The optional planning call finishes before the writing call claims its slot."""

import json
import unittest
from unittest.mock import patch

from companion_v2.configuration.registry import FieldRegistry
from companion_v2.execution.models import TERMINAL
from companion_v2.model_gateway.routing import configuration_fields
from companion_v2.proactive.service import persona_judge_profile, plan_profile
from tests.contact_schedule_support import ContactScheduleFixture
from tests.proactive_support import ContactFixture
from tests.test_role_tool_loop import answered


class TwoStageContactTests(ContactFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.profiles += (plan_profile("primary", max_output_tokens=100),
                          persona_judge_profile("primary", max_output_tokens=100))
        self.registry = FieldRegistry(version="contact-two-stage.v1", fields=configuration_fields(self.profiles))

    async def test_plan_then_write_and_switch_back_to_single_call(self):
        app = await self.enabled()
        self.settings(app, {"generation_mode": "two_stage"}).unwrap()

        async def staged(envelope):
            if envelope.original.output_format == "proactive_plan":
                return answered(json.dumps({"decision": "send", "decision_confidence": 0.85,
                    "angle": "分享一个新的读书念头", "facts": [], "avoid": ["不接自己上一句"]},
                    ensure_ascii=False))
            return answered('{"text":"刚想起一个有趣的读书问题，想听听你的看法。"}')

        self.model_behavior = staged
        task = (await self.contact(app, self.opportunity("two-stage-send"))).unwrap().task
        first_delivery = await self.delivery_state(app, task.task_id, "delivered")
        await self.until(lambda: app.runtime.read_one(
            "SELECT state FROM execution_tasks WHERE task_id = ?", (first_delivery["task_id"],))[0] in TERMINAL)
        self.assertEqual([call.original.output_format for call in self.called],
                         ["proactive_plan", "proactive_contact"])
        self.assertIn("contact_brief", [item.purpose for item in self.called[1].original.context.materials])
        self.assertIn("新回合", self.called[1].original.instruction)
        self.assertEqual([message.text for message in self.sent], ["刚想起一个有趣的读书问题，想听听你的看法。"])
        observation = app.proactive.observe(self.contact_context(), task.task_id).unwrap()
        self.assertEqual((observation["generation_mode"], observation["plan_decision"]),
                         ("two_stage", "send"))
        self.assertEqual(observation["stage_metrics"]["model"]["provider_calls"], 2)
        self.assertEqual(set(observation["stage_metrics"]["model"]["stages"]), {"plan", "write"})
        usage = app.proactive.budget.usage_summary(self.contact_context(), self.preferences(app), self.clock.now())
        self.assertEqual(usage["decisions"]["session"]["used"], 1)
        self.assertEqual(usage["model"]["session"]["calls"], 2)

        self.settings(app, {"generation_mode": "single"}).unwrap()
        self.model_behavior = lambda _: self._single_reply()
        second = (await self.contact(app, self.opportunity("switch-back"))).unwrap().task
        self.assertEqual(len(self.called), 3)
        self.assertEqual(app.proactive.observe(self.contact_context(), second.task_id).unwrap()["generation_mode"],
                         "single")

    @staticmethod
    async def _single_reply():
        return answered('{"decision":"send","text":"换个轻松的话题，今天有个小发现。"}')

    async def test_plan_skip_does_not_call_writer(self):
        app = await self.enabled()
        self.settings(app, {"generation_mode": "two_stage"}).unwrap()

        async def skip(_):
            return answered('{"decision":"skip","decision_confidence":0.93,"skip_reason":"repetitive"}')

        self.model_behavior = skip
        task = (await self.contact(app, self.opportunity("two-stage-skip"))).unwrap().task
        observation = app.proactive.observe(self.contact_context(), task.task_id).unwrap()
        self.assertEqual(len(self.called), 1)
        self.assertEqual(self.sent, [])
        self.assertEqual((observation["skipped"], observation["model_skip_reason"]), (True, "repetitive"))
        self.assertEqual(observation["stage_metrics"]["model"]["provider_calls"], 1)
        self.assertEqual(set(observation["stage_metrics"]["model"]["stages"]), {"plan"})
        usage = app.proactive.budget.usage_summary(self.contact_context(), self.preferences(app), self.clock.now())
        self.assertEqual((usage["decisions"]["session"]["used"], usage["model"]["session"]["calls"]),
                         (1, 1))

    async def test_writer_keeps_voice_and_poke_intents(self):
        app = await self.enabled()
        self.settings(app, {"generation_mode": "two_stage", "voice_action_enabled": True,
                            "poke_action_enabled": True}).unwrap()

        async def staged(envelope):
            if envelope.original.output_format == "proactive_plan":
                self.assertNotIn("voice_intent", envelope.original.instruction)
                return answered('{"decision":"send","decision_confidence":0.9,'
                                '"angle":"轻松聊书","facts":[],"avoid":[]}')
            self.assertIn("voice_intent", envelope.original.instruction)
            self.assertIn("poke_intent", envelope.original.instruction)
            return answered('{"text":"想听听你最近读的书。","voice_intent":true,'
                            '"voice_text":"想听听你最近读的书。","poke_intent":true}')

        self.model_behavior = staged
        task = (await self.contact(app, self.opportunity("two-stage-voice-poke"))).unwrap().task
        await self.delivery_state(app, task.task_id, "delivered")
        saved = json.loads(self.runtime.read_one(
            "SELECT record_json FROM proactive_contacts WHERE task_id = ?", (task.task_id,))["record_json"])
        self.assertEqual(saved["voice"]["speech"], "想听听你最近读的书。")
        self.assertEqual(saved["poke"], {"count": 1})

    async def test_invalid_plan_stops_before_writing(self):
        app = await self.enabled()
        self.settings(app, {"generation_mode": "two_stage"}).unwrap()

        async def invalid(_):
            return answered('{"decision":"send","text":"已经写好的半句话"}')

        self.model_behavior = invalid
        result = await self.contact(app, self.opportunity("invalid-plan"))
        self.assertEqual(result.failure.code, "contact_plan_invalid")
        self.assertEqual(len(self.called), 1)
        self.assertEqual(self.sent, [])

    async def test_synthetic_diagnostic_keeps_its_premise_and_hides_test_language(self):
        app = await self.enabled()
        self.settings(app, {"generation_mode": "two_stage"}).unwrap()
        opportunity = self.opportunity("synthetic-two-stage")
        intent = self.intent(opportunity, summary="演练前提：角色刚画完一张画，想分享颜色选择")
        diagnostic = {"_diagnostic": {"route_id": "synthetic_event", "purpose": "chat"}}

        async def staged(envelope):
            instruction = envelope.original.instruction
            self.assertIn("合成情境演练", instruction)
            if envelope.original.output_format == "proactive_plan":
                return answered('{"decision":"send","decision_confidence":0.9,'
                                '"angle":"聊颜色选择","facts":[],"avoid":[]}')
            self.assertIn("QQ 的 text", instruction)
            return answered('{"text":"刚画完这张画，我还在想用哪种颜色更好。"}')

        self.model_behavior = staged
        with patch.object(app.contact_schedule, "validate_diagnostic_origin"):
            result = await app.proactive.contact(self.contact_context(), opportunity, (intent,),
                role_instruction="这是原角色人格，保持自然。", selection_seed="synthetic-two-stage",
                administrator_test=True, diagnostic_origin=diagnostic)
            self.assertIsNone(result.failure, result.failure)
            task = result.unwrap().task
            await self.delivery_state(app, task.task_id, "delivered")
        self.assertEqual(len(self.called), 2)
        self.assertIn("刚画完这张画", self.sent[0].text)


class TwoStageJudgedContactTests(ContactScheduleFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.profiles += (plan_profile("primary", max_output_tokens=100),
                          persona_judge_profile("primary", max_output_tokens=100))
        self.registry = FieldRegistry(version="contact-two-stage-judge.v1",
                                      fields=configuration_fields(self.profiles))

    async def test_persona_review_still_runs_after_two_stage_writing(self):
        host, _ = await self.scheduled()
        app = host.application
        self.settings(app, {"generation_mode": "two_stage",
                            "enable_llm_proactive_persona_judge": True}).unwrap()

        async def staged(envelope):
            if envelope.original.output_format == "proactive_plan":
                return answered('{"decision":"send","decision_confidence":0.9,'
                                '"angle":"分享一个小念头","facts":[],"avoid":[]}')
            if any(item.purpose == "proactive_draft" for item in envelope.original.context.materials):
                return answered('{"decision":"send","score":90,"reason":"符合当前人格"}')
            return answered('{"text":"刚想起一件有趣的小事，想和你说说。"}')

        self.model_behavior = staged
        self.advance(app, 60)
        app.contact_schedule.advance()
        await self.automatic_delivery(app)
        task_id = self.contacts(app)[-1]["task_id"]
        observation = app.proactive.observe(self.contact_context(), task_id).unwrap()
        self.assertEqual(observation["stage_metrics"]["model"]["provider_calls"], 3)
        self.assertEqual(set(observation["stage_metrics"]["model"]["stages"]),
                         {"plan", "write", "judge"})
        self.assertTrue(observation["judge_allowed"])


if __name__ == "__main__":
    unittest.main()
