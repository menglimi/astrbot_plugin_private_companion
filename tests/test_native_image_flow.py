"""Native generation/edit -> governed adoption -> local sender and restart."""

import base64
from dataclasses import replace
import json
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock

from companion_v2.capabilities.models import CapabilityAuthorizationState
from companion_v2.capabilities.repositories import CapabilityRepository
from companion_v2.conversation.delivery import DeliveryReceipt
from companion_v2.conversation.flow import ConversationPolicy
from companion_v2.conversation.media.reply import ImageReplyDraft
from companion_v2.execution.models import TaskError, digest, encode
from companion_v2.model_gateway.models import Amount, ProviderResponse, ToolCall
from tests.native_image_support import NativeImageFixture
from tests.test_conversation_images import image_bytes
from tests.test_host_application import HostFixture
from tests.test_management_access import NOW, SCOPE
from tests.test_role_tool_loop import answered, requested
from astrbot_host.native_extensions.image_native.output_routing import OutputRoutingBackend


class NativeImageFlowTests(NativeImageFixture, HostFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.image_directory = self.root.parent / "image-native-v2"
        self.native_registry = self.create_image_port(self.image_directory, self.clock.now)
        self.providers = tuple(replace(p, capabilities=("text", "tools", "image")) for p in self.providers)
        self.sender = replace(self.sender, supports_images=True)
        self.package = self.native_registry.declarations()[0].descriptor.package
        self.capabilities = CapabilityRepository(self.runtime)
        self.capabilities.register_descriptor(self.package, registered_at=NOW).unwrap()
        self.capabilities.acquire(extension_id=self.package.extension_id, package_id=self.package.package_id,
            package_version=self.package.package_version, expected_revision=0, observed_at=NOW).unwrap()
        self.capabilities.set_role_enabled(extension_id=self.package.extension_id, role=self.window.role,
            bot_id=SCOPE.bot_id, enabled=True, expected_revision=0, observed_at=NOW).unwrap()
        for capability in self.package.capabilities:
            self.consent(capability, CapabilityAuthorizationState.ACTIVE, 0)
        self.model_behavior = self.image_model

    def consent(self, capability, state, revision):
        return self.capabilities.set_authorization(extension_id=self.package.extension_id, capability_id=capability,
            subject=self.important.subject, scope=SCOPE, state=state, expected_revision=revision,
            observed_at=self.clock.now()).unwrap()

    async def image_model(self, envelope):
        if not envelope.tool_history:
            return requested(ToolCall("generate", "image.generate", encode({"prompt": "合成红色图片"})))
        if not envelope.original.images:
            return answered("尚未得到可采用的图片。")
        return self.image_answer(envelope)

    def image_answer(self, envelope, text="这是本次生成的图片。"):
        return ProviderResponse("generated", ImageReplyDraft(
            text, (), images=(envelope.original.images[-1].source,)),
                                Amount(30, 30), "model-image-answer")

    async def image_host(self, **options):
        options.setdefault("policy", ConversationPolicy(0, 0, 180, 0))
        return await self.start(self.host(tool_port=self.native_registry, **options))

    async def restart_images(self, host):
        await host.application.close()
        await self.image_owner.close()
        old_generation = self.image_owner.generation
        self.native_registry = self.create_image_port(self.image_directory, self.clock.now)
        self.assertEqual(self.image_owner.generation, old_generation)
        new = self.host(runtime=self.open_runtime(), tool_port=self.native_registry,
                        policy=ConversationPolicy(0, 0, 180, 0))
        return await self.start(new, 1, 1)

    async def complete_image(self):
        host = await self.image_host()
        batch = host.receive(self.event()).unwrap()
        state = await self.done(host.application, batch)
        self.assertEqual(state["state"], "complete", dict(state))
        return host, batch, self.context(host.application, batch)

    async def test_native_generation_is_adopted_and_sent_once_with_original_receipt(self):
        host, batch, context = await self.complete_image()
        app = host.application
        self.assertEqual(len(self.image_backend.calls), 1)
        self.assertEqual(len(self.called), 2)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(base64.b64decode(self.sent[0].images[0].data_base64), image_bytes("red"))
        self.assertEqual(self.runtime.read_one("SELECT COUNT(*) FROM conversation_inputs", ())[0], 1)
        row = self.runtime.read_one("SELECT * FROM extension_tool_invocations", ())
        self.assertEqual((row["state"], row["effect"]), ("succeeded", "committed"))
        self.assertIsNotNone(row["receipt_ref"])
        history = app.messages.history(context).unwrap()["items"]
        self.assertTrue(history[0]["body_available"])
        self.assertEqual(history[0]["images"][0]["content_hash"], self.sent[0].images[0].descriptor.content_hash)
        sources = app.runtime.read_all("SELECT payload_json FROM sources WHERE owner IN ('image.native','conversation.generated_images')", ())
        self.assertTrue(sources)
        self.assertNotIn("data:image/", str([tuple(r) for r in sources]))
        self.assertEqual(host.receive(self.event()).unwrap(), batch)
        self.assertEqual(len(self.sent), 1)

    async def test_nai_tags_and_character_features_survive_generation_adoption_and_delivery(self):
        requests = []
        output = self.image_directory / "nai-output.png"

        async def generate(_owner, request):
            requests.append(request)
            output.write_bytes(image_bytes("green"))
            return {"handled": True, "image_path": str(output)}

        api = NS(status=lambda: {"call_mode": "direct", "prompt_format": "nai tag模式"},
                 capability_status=lambda owner: {"available": True},
                 generate_for_companion=AsyncMock(side_effect=generate))
        native = NS(get_registered_star=lambda name: NS(activated=True, star_cls=NS(extension_api=api)))
        self.image_owner.backend = OutputRoutingBackend(
            {"enabled": True, "backend": "nai", "operation_routes": {"generate": ["nai"], "edit": []}},
            self.image_directory / "routes", context=native)

        async def model(envelope):
            if not envelope.tool_history:
                self.assertIn("发型发色、瞳色和外貌识别点", envelope.original.instruction)
                return requested(ToolCall("nai-generate", "image.generate", encode({
                    "prompt": "1girl, silver hair, green eyes, fox ears, selfie, garden"})))
            return self.image_answer(envelope)

        self.model_behavior = model
        host, batch, context = await self.complete_image()
        self.assertEqual(len(requests), 1)
        self.assertIn("silver hair, green eyes, fox ears", requests[0]["prompt_text"])
        self.assertNotIn("photorealistic", requests[0]["prompt_text"])
        self.assertEqual(requests[0]["negative"], "")
        self.assertEqual(requests[0]["character_source"], "companion")
        self.assertNotIn("size", requests[0])
        self.assertEqual(base64.b64decode(self.sent[0].images[0].data_base64), image_bytes("green"))
        saved = host.application.messages.generated_images.read_batch(batch, context, self.clock.now())
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0].source.scope, context.scope)

    async def test_edit_uses_same_origin_saved_artifact_and_only_selected_output_is_sent(self):
        async def model(envelope):
            if len(envelope.tool_history) != 1:
                return await self.image_model(envelope)
            native = next(json.loads(m.payload)["native"] for m in envelope.original.context.materials if m.owner == "image.native")
            artifact_id = native["artifacts"][0]["artifact_id"]
            return requested(ToolCall("edit", "image.edit", encode({"prompt": "改为蓝色", "artifact_id": artifact_id})))
        self.model_behavior = model
        await self.complete_image()
        self.assertEqual(len(self.image_backend.calls), 2)
        self.assertEqual(len(self.image_backend.calls[1][1]), 1)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(base64.b64decode(self.sent[0].images[0].data_base64), image_bytes("blue"))
        self.assertEqual(len(self.called[-1].original.images), 2)

    async def test_unselected_generation_stays_saved_without_automatic_image_send(self):
        async def model(envelope):
            return await self.image_model(envelope) if not envelope.tool_history else answered("已保存，暂时不附图。")
        self.model_behavior = model
        host, batch, context = await self.complete_image()
        self.assertEqual(len(host.application.messages.generated_images.read_batch(batch, context, self.clock.now())), 1)
        self.assertFalse(getattr(self.sent[0], "images", ()))

    async def test_unknown_backend_produces_no_image_and_never_replays(self):
        self.image_backend.unknown = True
        host, batch, context = await self.complete_image()
        self.assertEqual(len(self.image_backend.calls), 1)
        self.assertEqual(host.application.messages.generated_images.read_batch(batch, context, self.clock.now()), ())
        self.assertFalse(getattr(self.sent[0], "images", ()))
        row = self.runtime.read_one("SELECT state,effect FROM extension_tool_invocations", ())
        self.assertEqual(tuple(row), ("unknown", "unknown"))
        self.assertEqual(host.receive(self.event()).unwrap(), batch)
        self.assertEqual(len(self.image_backend.calls), 1)

    async def test_revocation_before_final_adoption_prevents_entire_send(self):
        async def model(envelope):
            response = await self.image_model(envelope)
            if envelope.tool_history:
                self.consent("image.generate", CapabilityAuthorizationState.REVOKED, 1)
            return response
        self.model_behavior = model
        host = await self.image_host()
        batch = host.receive(self.event()).unwrap()
        await self.done(host.application, batch)
        self.assertEqual(self.sent, [])
        self.assertEqual(len(self.image_backend.calls), 1)

    async def test_corrupt_original_blocks_adoption_and_history(self):
        host, batch, context = await self.complete_image()
        with self.runtime.transaction() as connection:
            connection.execute("UPDATE conversation_image_payloads SET content = ?", (b"corrupt",))
        with self.assertRaises(TaskError):
            host.application.messages.generated_images.read_batch(batch, context, self.clock.now())
        self.assertFalse(host.application.messages.history(context).unwrap()["items"][0]["body_available"])

    async def test_default_restart_reads_pending_image_and_original_receipt_without_regenerating(self):
        async def model(envelope):
            if not envelope.tool_history:
                return await self.image_model(envelope)
            return self.image_answer(envelope, "第一段\n这是生成图。")
        self.model_behavior = model
        host = await self.image_host(policy=ConversationPolicy(0, 0, 180, 5))
        batch = host.receive(self.event()).unwrap()
        await self.until(lambda: host.application.flow.row(batch)["reason"] == "part_spacing", seconds=8)
        original_receipt = tuple(self.runtime.read_one("SELECT operation_id,receipt_ref FROM extension_tool_invocations", ()))
        self.assertEqual(len(self.sent), 1)
        self.assertFalse(getattr(self.sent[0], "images", ()))
        self.assertEqual(len(self.image_backend.calls), 1)
        new = await self.restart_images(host)
        self.advance(new.application, 5)
        self.assertEqual((await self.done(new.application, batch))["state"], "complete")
        self.assertEqual(len(self.called), 2)
        self.assertEqual(self.image_backend.calls, [])
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(base64.b64decode(self.sent[-1].images[0].data_base64), image_bytes("red"))
        restored = tuple(new.application.runtime.read_one("SELECT operation_id,receipt_ref FROM extension_tool_invocations", ()))
        self.assertEqual(restored, original_receipt)

    async def test_unknown_send_remains_unknown_after_restart_without_resending(self):
        async def send(envelope):
            return self.receipt(envelope, "unknown")
        self.send_behavior = send
        host = await self.image_host()
        batch = host.receive(self.event()).unwrap()
        await self.done(host.application, batch)
        self.assertEqual(len(self.sent), 1)
        new = await self.restart_images(host)
        self.advance(new.application)
        await self.done(new.application, batch)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.image_backend.calls, [])
        self.assertEqual(len(self.called), 2)
        app = new.application
        part = app.runtime.read_one("SELECT * FROM conversation_parts WHERE batch_id = ?", (batch,))
        self.assertEqual(part["state"], "unknown")
        context = self.context(app, batch)
        receipt = DeliveryReceipt(part["part_id"], part["operation_id"], context.key, part["payload_hash"],
            self.sender.binding, "delivered", "late-native-image-ack", "synthetic-native-image", self.clock.now())
        self.assertEqual(app.record_receipt(replace(receipt, payload_hash=digest(part["text"]))).failure.code,
                         "message_receipt_mismatch")
        app.record_receipt(receipt).unwrap()
        app.reconcile_delivery(context, part["part_id"]).unwrap()
        self.assertEqual((await self.done(app, batch))["state"], "complete")
        self.assertEqual(len(app.messages.history(context).unwrap()["items"][0]["images"]), 1)
        self.assertEqual(len(self.sent), 1)


if __name__ == "__main__":
    unittest.main()
