"""Proactive reactions share the owned catalog and confirmed delivery accounting."""

from dataclasses import replace
import json
import unittest
from unittest.mock import Mock, patch

from companion_v2.configuration.models import ConfigLayer, ConfigTarget
from companion_v2.delivery.media import ImageDeliveryEnvelope, image_manifest, manifest_hash
from companion_v2.delivery.service import DeliveryRequest
from tests.proactive_support import ContactFixture
from tests.test_role_tool_loop import answered
from tests.test_semantic_integration import SemanticPort, animation, picture


class ProactiveReactionTests(ContactFixture, unittest.IsolatedAsyncioTestCase):
    async def enabled_reactions(self, *, proactive=True, return_host=False):
        self.sender = replace(self.sender, supports_images=True)
        host = self.host()
        self.semantic = host.application.attach_semantics(SemanticPort())
        await self.start(host)
        app = host.application
        self.settings(app, {"enabled": True}).unwrap()
        self.semantic.store.configure({"reactions_proactive_enabled": proactive,
                                       "reactions_trigger_probability": 1.0,
                                       "reactions_cooldown_seconds": 0}, 0)
        from companion_v2.conversation.media.output_settings import GROUP as IMAGE_OUTPUT_GROUP
        context = self.contact_context()
        target = ConfigTarget(context.scope.installation_id, ConfigLayer.ROLE,
                              context.role_binding.role, bot_id=context.scope.bot_id)
        snapshot = app.configuration.read(self.core, target=target, group_id=IMAGE_OUTPUT_GROUP,
                                          observed_at=self.clock.now()).unwrap()
        app.configuration.patch(self.core, target=target, group_id=IMAGE_OUTPUT_GROUP,
            expected_edit_token=snapshot.edit_token,
            changes={IMAGE_OUTPUT_GROUP + ".proactive_probability": 1.0,
                     IMAGE_OUTPUT_GROUP + ".proactive_max_daily": 1},
            operation=self.operation(target.authorization_scope, owner=IMAGE_OUTPUT_GROUP),
            observed_at=self.clock.now()).unwrap()
        return (app, host) if return_host else app

    def import_reaction(self, image=None, *, label="安慰"):
        context = self.contact_context()
        return self.semantic.reactions.import_image(self.semantic.ensure_reaction_catalog(context), image or picture(),
            {"name": label, "description": label, "intents": label})

    def choose_reaction(self, intent="安慰"):
        async def respond(_envelope):
            return answered(json.dumps({"decision": "send", "text": "我在这里。",
                                        "reaction_intent": intent}, ensure_ascii=False))
        self.model_behavior = respond

    def uses(self):
        with self.semantic.store.db() as db:
            return db.execute("SELECT COUNT(*) FROM uses").fetchone()[0]

    async def test_default_off_preserves_text_only_contact(self):
        app = await self.enabled_reactions(proactive=False)
        self.import_reaction()
        self.choose_reaction()
        task = (await self.contact(app)).unwrap().task
        await self.delivery_state(app, task.task_id, "delivered")
        self.assertNotIn("reaction_intent", self.called[0].original.instruction)
        self.assertNotIn("reaction_intent", self.called[0].original.user_instruction)
        self.assertNotIsInstance(self.sent[0], ImageDeliveryEnvelope)
        self.assertEqual(self.uses(), 0)

    async def test_original_image_and_history_are_bound_to_confirmed_contact(self):
        app = await self.enabled_reactions()
        original = picture()
        item = self.import_reaction(original)
        self.choose_reaction()
        task = (await self.contact(app)).unwrap().task
        await self.until(lambda: self.uses() == 1)
        row = await self.delivery_state(app, task.task_id, "delivered")
        text, envelope = self.sent
        self.assertNotIsInstance(text, ImageDeliveryEnvelope)
        self.assertEqual(text.text, "我在这里。")
        self.assertIsInstance(envelope, ImageDeliveryEnvelope)
        self.assertEqual(envelope.text, "")
        self.assertIn("reaction_intent", self.called[0].original.user_instruction)
        self.assertIn("主动检查图库表情", self.called[0].original.user_instruction)
        self.assertIn("不需要等用户明确要求图片", self.called[0].original.user_instruction)
        self.assertIn("惊喜又开心地回应对方", self.called[0].original.user_instruction)
        self.assertEqual(envelope.images[0].descriptor.content_hash, item["id"])
        self.assertEqual(envelope.images[0].data_base64, __import__("base64").b64encode(original.content).decode("ascii"))
        contact = self.runtime.read_one("SELECT * FROM proactive_contacts WHERE task_id=?", (task.task_id,))
        record = json.loads(contact["record_json"])
        manifest = image_manifest(envelope.images[0])
        self.assertEqual(record["reaction"]["manifest"], manifest)
        self.assertEqual(contact["payload_hash"], manifest_hash(text.text, (manifest,)))
        self.assertEqual(row["payload_hash"], envelope.payload_hash)
        history = app.background_history.recent(self.contact_context()).unwrap()
        self.assertIn(text.text, [part["text"] for part in history])
        self.assertEqual(app.record_receipt(self.receipt(envelope)).unwrap(), "delivered")
        self.assertEqual(self.uses(), 1)

    async def test_confirmed_image_diagnostic_delivers_original_when_daily_auto_images_are_off(self):
        app = await self.enabled_reactions(proactive=False)
        context = self.contact_context()
        self.settings(app, {"enabled": True, "automatic_enabled": False}).unwrap()
        self.semantic.store.configure({"reactions_proactive_enabled": False,
                                       "reactions_trigger_probability": 0.0,
                                       "reactions_cooldown_seconds": 3600}, 1)
        from companion_v2.conversation.media.output_settings import GROUP as IMAGE_OUTPUT_GROUP
        target = ConfigTarget(context.scope.installation_id, ConfigLayer.ROLE, context.role_binding.role,
                              bot_id=context.scope.bot_id)
        snapshot = app.configuration.read(self.core, target=target, group_id=IMAGE_OUTPUT_GROUP,
                                          observed_at=self.clock.now()).unwrap()
        app.configuration.patch(self.core, target=target, group_id=IMAGE_OUTPUT_GROUP,
            expected_edit_token=snapshot.edit_token,
            changes={IMAGE_OUTPUT_GROUP + ".proactive_probability": 0.0,
                     IMAGE_OUTPUT_GROUP + ".proactive_max_daily": 0},
            operation=self.operation(target.authorization_scope, owner=IMAGE_OUTPUT_GROUP),
            observed_at=self.clock.now()).unwrap()
        original = picture()
        diagnostic_intent = "适合轻松、友好、自然日常互动的表情图片；不适合严肃、悲伤、冲突或边界语境。"
        item = self.import_reaction(original, label=diagnostic_intent)
        opportunity = self.opportunity("image-diagnostic")
        source = self.source(app, "image-diagnostic-source")
        intent = self.intent(opportunity, purpose="chat", sources=(source,))
        diagnostic_origin = {"_diagnostic": {"route_id": "conversation_scheduler", "purpose": "chat",
            "batch_id": "diagnostic-batch", "event_key": "diagnostic-event", "event_hash": "event-hash",
            "role_hash": "role-hash"}}
        app.contact_schedule.validate_diagnostic_origin = Mock()
        async def send_image_diagnostic(_envelope):
            return answered(json.dumps({
                "decision": "send", "text": "刚想找你说件轻松的小事。"}, ensure_ascii=False))
        self.model_behavior = send_image_diagnostic
        result = await app.proactive.contact(context, opportunity, (intent,),
            role_instruction="这是原角色人格，保持自然。", selection_seed="image-diagnostic",
            administrator_test=True, diagnostic_source_backed=True,
            diagnostic_origin=diagnostic_origin,
            diagnostic_image_intent=diagnostic_intent)
        self.assertIsNone(result.failure, result.failure)
        task = result.unwrap().task
        await self.until(lambda: self.uses() == 1)
        await self.delivery_state(app, task.task_id, "delivered")
        observation = app.proactive.observe(context, task.task_id).unwrap()
        self.assertEqual(observation["stage_metrics"]["image_attachment"],
                         {"state": "passed", "attached": True})
        text, envelope = self.sent
        self.assertNotIsInstance(text, ImageDeliveryEnvelope)
        self.assertIsInstance(envelope, ImageDeliveryEnvelope)
        self.assertEqual(envelope.text, "")
        self.assertEqual(envelope.images[0].descriptor.content_hash, item["id"])
        self.assertEqual(envelope.images[0].data_base64,
                         __import__("base64").b64encode(original.content).decode("ascii"))
        self.assertEqual(self.uses(), 1)

    async def test_animated_original_is_sent_as_gif(self):
        app = await self.enabled_reactions()
        original = animation()
        self.import_reaction(original, label="回应问候")
        self.choose_reaction("回应问候")
        task = (await self.contact(app)).unwrap().task
        await self.until(lambda: self.uses() == 1)
        envelope = self.sent[-1]
        self.assertIsInstance(envelope, ImageDeliveryEnvelope)
        self.assertEqual(envelope.text, "")
        self.assertEqual(envelope.images[0].descriptor.mime_type, "image/gif")
        self.assertEqual(envelope.images[0].data_base64,
                         __import__("base64").b64encode(original.content).decode("ascii"))

    async def test_catalog_change_blocks_queued_image_and_text_as_one_payload(self):
        app = await self.enabled_reactions()
        item = self.import_reaction()
        self.choose_reaction()
        with patch.object(app.background, "advance", return_value=False):
            task = (await self.contact(app)).unwrap().task
            row = app.proactive._delivery_row(task.task_id)
            request = DeliveryRequest.restore(row["request_json"])
            self.semantic.store.put(item["owner"], "reaction", item["id"], item["payload"],
                                    expected=item["revision"], enabled=False)
            self.assertFalse(app.proactive.delivery_text(request, self.clock.now()).is_success)
        app.notify()
        await self.delivery_state(app, task.task_id, "cancelled")
        self.assertEqual((self.sent, self.uses()), ([], 0))

    async def test_unknown_then_late_receipt_after_restart_accounts_once(self):
        app = await self.enabled_reactions()
        self.import_reaction()
        self.choose_reaction()
        async def unknown(envelope):
            status = "unknown" if isinstance(envelope, ImageDeliveryEnvelope) else "delivered"
            return self.receipt(envelope, status)
        self.send_behavior = unknown
        task = (await self.contact(app)).unwrap().task
        await self.delivery_state(app, task.task_id, "unknown")
        envelope = self.sent[-1]
        self.assertIsInstance(envelope, ImageDeliveryEnvelope)
        self.assertEqual(self.uses(), 0)
        await app.close()
        host = self.host(runtime=self.open_runtime())
        self.semantic = host.application.attach_semantics(SemanticPort())
        restarted = (await self.start(host, task_generation=1, model_generation=1)).application
        self.assertEqual(restarted.record_receipt(self.receipt(envelope)).unwrap(), "delivered")
        self.assertEqual(restarted.record_receipt(self.receipt(envelope)).unwrap(), "delivered")
        self.assertEqual((len(self.sent), self.uses()), (2, 1))

    async def test_restart_repairs_accounting_after_confirmed_receipt(self):
        app = await self.enabled_reactions()
        self.import_reaction()
        self.choose_reaction()
        with patch.object(self.semantic.reactions, "mark_contact_sent", return_value=False):
            task = (await self.contact(app)).unwrap().task

            def image_delivered():
                rows = app.proactive._delivery_rows(task.task_id)
                return len(rows) == 2 and rows[-1]["state"] == "delivered"

            await self.until(image_delivered)
            self.assertIsInstance(self.sent[-1], ImageDeliveryEnvelope)
            self.assertEqual(self.uses(), 0)
            await app.close()
        host = self.host(runtime=self.open_runtime())
        self.semantic = host.application.attach_semantics(SemanticPort())
        await self.start(host, task_generation=1, model_generation=1)
        self.assertEqual(self.uses(), 1)

    async def test_judged_contact_attaches_reaction_after_judge_approves(self):
        from companion_v2.configuration.registry import FieldRegistry
        from companion_v2.model_gateway.routing import configuration_fields as model_fields
        from companion_v2.proactive.service import persona_judge_profile

        self.sender = replace(self.sender, supports_images=True)
        self.profiles += (persona_judge_profile("primary", max_output_tokens=100),)
        self.registry = FieldRegistry(version="contact-test.v1", fields=model_fields(self.profiles))
        host = self.host()
        self.semantic = host.application.attach_semantics(SemanticPort())
        await self.start(host)
        app = host.application
        self.settings(app, {"enabled": True, "enable_llm_proactive_persona_judge": True,
                            "proactive_persona_judge_threshold": 60}).unwrap()
        self.semantic.store.configure({"reactions_proactive_enabled": True,
                                       "reactions_trigger_probability": 1.0,
                                       "reactions_cooldown_seconds": 0}, 0)
        from companion_v2.conversation.media.output_settings import GROUP as IMAGE_OUTPUT_GROUP
        context = self.contact_context()
        target = ConfigTarget(context.scope.installation_id, ConfigLayer.ROLE,
                              context.role_binding.role, bot_id=context.scope.bot_id)
        snapshot = app.configuration.read(self.core, target=target, group_id=IMAGE_OUTPUT_GROUP,
                                          observed_at=self.clock.now()).unwrap()
        app.configuration.patch(self.core, target=target, group_id=IMAGE_OUTPUT_GROUP,
            expected_edit_token=snapshot.edit_token,
            changes={IMAGE_OUTPUT_GROUP + ".proactive_probability": 1.0,
                     IMAGE_OUTPUT_GROUP + ".proactive_max_daily": 2},
            operation=self.operation(target.authorization_scope, owner=IMAGE_OUTPUT_GROUP),
            observed_at=self.clock.now()).unwrap()
        original = picture()
        item = self.import_reaction(original, label="安慰")

        async def respond(envelope):
            if "主动消息复核" in envelope.task_prompt or "点评一条已写好的主动消息" in envelope.task_prompt or "score" in envelope.original.instruction:
                return answered('{"decision":"send","score":92,"reason":"语气自然"}')
            return answered(json.dumps({"decision": "send", "text": "我在这里陪你。",
                                        "reaction_intent": "安慰"}, ensure_ascii=False))
        self.model_behavior = respond

        opportunity = self.opportunity("judged-reaction")
        source = self.source(app, "judged-reaction-source")
        intent = self.intent(opportunity, purpose="chat", sources=(source,))
        diagnostic_origin = {"_diagnostic": {"route_id": "conversation_scheduler", "purpose": "chat",
            "batch_id": "judged-batch", "event_key": "judged-event", "event_hash": "event-hash",
            "role_hash": "role-hash"}}
        app.contact_schedule.validate_diagnostic_origin = Mock()
        app.contact_schedule._origin = Mock(return_value=(None, {"role_instruction": "原角色"}, None))
        task = (await app.proactive.contact(context, opportunity, (intent,),
            role_instruction="原角色", selection_seed="judged-reaction",
            administrator_test=True, diagnostic_source_backed=True,
            diagnostic_origin=diagnostic_origin)).unwrap().task
        row = await self.delivery_state(app, task.task_id, "delivered")
        await self.until(lambda: self.uses() == 1)
        envelope = self.sent[0]
        self.assertIsInstance(envelope, ImageDeliveryEnvelope)
        self.assertEqual(envelope.images[0].descriptor.content_hash, item["id"])
        contact = self.runtime.read_one("SELECT * FROM proactive_contacts WHERE task_id=?", (task.task_id,))
        record = json.loads(contact["record_json"])
        self.assertEqual(record["judge"]["decision"], "send")
        self.assertNotIn("pending_reaction_intent", record)
        self.assertEqual(record["reaction"]["manifest"], image_manifest(envelope.images[0]))
        self.assertEqual(contact["payload_hash"], row["payload_hash"])
        observation = app.proactive.observe(context, task.task_id).unwrap()
        self.assertEqual(observation["stage_metrics"]["media"]["reaction"], "attached")
        self.assertEqual(observation["stage_metrics"]["media"]["voice"], "not_selected")


if __name__ == "__main__":
    unittest.main()
