"""Original image selection through real local generation, sender and receipt owners."""

import asyncio
import base64
from dataclasses import replace
from datetime import timedelta
import json
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from companion_v2.adapters.astrbot.model_wire import build_wire, tool_alias
from companion_v2.conversation.delivery import DeliveryReceipt, LocalSender
from companion_v2.conversation.flow import ConversationPolicy
from companion_v2.conversation.media.reply import OWNER
from companion_v2.delivery.media import ImageDeliveryEnvelope
from companion_v2.execution.models import TaskError, digest
from companion_v2.identity_policy.personas import RuntimeIdentity
from companion_v2.model_gateway.models import ModelEnvelope
from companion_v2.sources.dependencies import key
from tests.animation_support import animation_bytes
from tests.forward_support import ForwardFixture, node_raw, quoted_forward_raw
from tests.image_delivery_support import (attach_originals, image_reply, offered_images,
                                          intercept_image_delivery_verification)
from tests.native_adapter_support import completion, final_text
from tests.test_conversation_images import FakeImage, image_bytes
from tests.test_conversation_quotes import quote_raw


class ConversationImageDeliveryTests(ForwardFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.native.provider.client.state.behavior = attach_originals

    def sends(self):
        return [params for action, params in self.api.calls if action == "send_private_msg"]

    async def restart(self, host, *, sender=None):
        await host.application.close()
        self.clock.advance(timedelta(seconds=0.2))
        new = self.host(runtime=self.open_runtime(), event_port=self.bridge.ports, identity_port=self.bridge.ports,
            role_port=self.bridge.ports, providers=(self.native_model.local_provider(),), sender=sender or self.native_sender.local_sender(),
            runtime_identity=RuntimeIdentity("companion", "image-delivery-restart", "astrbot", "host"))
        await self.start(new, 1, 1)
        return new

    async def test_text_and_ordered_originals_share_one_dispatch_and_confirmed_receipt(self):
        async def reverse(payload, _):
            return image_reply(payload, handles=list(reversed(offered_images(payload))))
        self.native.provider.client.state.behavior = reverse
        event = self.image_event()
        event.message_obj.message.append(FakeImage(image_bytes("blue")))
        host = await self.setup_host()
        captured = await self.capture(event)
        batch = host.receive(captured).unwrap()
        app, context = host.application, self.context(host.application, batch)
        self.assertEqual((await self.done(app, batch))["state"], "complete")
        self.assertEqual(len(self.sends()), 1)
        message = self.sends()[0]["message"]
        self.assertEqual([p["type"] for p in message], ["text", "image", "image"])
        self.assertEqual([base64.b64decode(p["data"]["file"].removeprefix("base64://")) for p in message[1:]],
                         [image_bytes("blue"), image_bytes()])
        history = app.messages.history(context).unwrap()["items"]
        self.assertEqual(len(history), 1)
        self.assertEqual([i["content_hash"] for i in history[0]["images"]],
                         [captured.images[1].descriptor.content_hash, captured.images[0].descriptor.content_hash])
        part = app.runtime.read_one("SELECT * FROM conversation_parts WHERE batch_id = ?", (batch,))
        self.assertNotEqual(part["payload_hash"], digest(part["text"]))
        self.assertEqual(host.receive(captured).unwrap(), batch)
        self.assertEqual(len(self.sends()), 1)
        record = app.runtime.read_one("SELECT payload_json FROM sources WHERE owner = ?", (OWNER,))
        self.assertNotIn("base64", record[0])
        self.assertNotIn("https://", record[0])

    async def test_user_photo_origin_and_photo_request_guidance_reach_the_model(self):
        host = await self.setup_host()
        event = self.image_event()
        event.message_obj.message[0].text = "来张照片"
        batch = host.receive(await self.capture(event)).unwrap()
        request = self.request(host.application, batch)
        wire = build_wire(ModelEnvelope(request, "photo request", 100, "fixture"))
        image_metadata = [json.loads(item["text"]) for item in wire.messages[-1]["content"]
                          if item.get("type") == "text" and "image_evidence" in item["text"]]
        system_prompt = "\n".join(message["content"] for message in wire.messages
                                  if isinstance(message.get("content"), str))

        self.assertEqual(len(image_metadata), 1)
        self.assertEqual(image_metadata[0]["image_origin"], "user_input")
        self.assertIn("默认不要选择 user_input 图片", system_prompt)
        self.assertIn("用户对角色说“来张照片”“看看照片”", system_prompt)
        self.assertIn("不得把用户手机相册上传的照片冒充成角色照片", system_prompt)
        self.assertIn("sendable_image 只表示原件在技术上可回送", system_prompt)
        self.assertIn("发一张你的照片/自拍/穿搭照", system_prompt)

    async def test_quoted_forward_animation_sends_full_original_and_keeps_memory_dependencies(self):
        body = animation_bytes(count=10)
        self.raw = quoted_forward_raw(nodes=[node_raw(images=(body,))])
        self.native_model = self.make_model(maximum_input_estimate=300000)
        host = await self.setup_host()
        batch = host.receive(await self.capture(self.quote_event())).unwrap()
        app, context = host.application, self.context(host.application, batch)
        self.assertEqual((await self.done(app, batch))["state"], "complete")
        message = self.sends()[0]["message"]
        self.assertEqual(len(message), 2)
        self.assertEqual(base64.b64decode(message[1]["data"]["file"].removeprefix("base64://")), body)
        owner = app.memory.writer.access.sent_sources
        reference = tuple(owner.rows(context))[0]
        owner.materialize(context, reference)
        _, manifest = app.messages.reply_images.read(app.messages.batch(batch), context, self.clock.now())
        links = app.runtime.read_all("SELECT input_id, input_lineage, input_revision FROM source_dependencies WHERE dependent_id = ?", (reference.source_id,))
        self.assertIn(key(manifest), [tuple(r) for r in links])
        app.withdraw(context, self.original(app, batch).scoped_key).unwrap()
        with self.assertRaises(TaskError):
            owner.validate(context, reference)
        self.assertFalse(app.messages.history(context).unwrap()["items"][0]["body_available"])

    async def test_tool_continuation_preserves_sendable_original_handles(self):
        snapshots = []
        async def respond(payload, _):
            snapshots.append(offered_images(payload))
            if not any(m["role"] == "tool" for m in payload["messages"]):
                return completion(tools=[NS(id="before-image", type="function", function=NS(name=tool_alias("memo.list"), arguments='{}'))])
            return image_reply(payload)
        self.native.provider.client.state.behavior = respond
        host = await self.setup_host()
        batch = host.receive(await self.capture(self.image_event())).unwrap()
        self.assertEqual((await self.done(host.application, batch))["state"], "complete")
        self.assertEqual(len(snapshots), 2)
        self.assertEqual(snapshots[0], snapshots[1])
        self.assertEqual(len(self.sends()), 1)

    async def test_unknown_nonimage_frame_duplicate_or_url_handles_never_dispatch(self):
        host = await self.quiet_host()
        batch = host.receive(await self.capture(self.image_event(content=animation_bytes()))).unwrap()
        request = self.request(host.application, batch)
        envelope = ModelEnvelope(request, "合成任务", 100, "config", before_dispatch=lambda: True)
        wire = build_wire(envelope)
        offered = next(iter(wire.reply_images))
        primary = "e_" + digest(request.event.source)
        frame = "e_" + digest(request.images[0].frames[0].source)
        for handles in (["e_invented"], [primary], [frame], [offered, offered], ["https://invalid.example/image.png"],
                        ["C:/untrusted.png"], ["base64://untrusted"], "wrong-shape", [None]):
            with self.subTest(handles=handles):
                async def invalid(payload, _, selected=handles):
                    return image_reply(payload, handles=selected)
                self.native.provider.client.state.behavior = invalid
                response = await self.native_model.generate(envelope)
                self.assertEqual(response.status, "unknown")
                self.assertIsNone(response.draft)
        self.assertEqual(self.sends(), [])

    async def test_sender_without_image_capability_does_not_offer_or_silently_drop_images(self):
        async def forbidden(*_):
            raise AssertionError("no send may occur")
        sender = LocalSender(self.native_sender.binding, forbidden)
        host = await self.quiet_host(sender=sender)
        batch = host.receive(await self.capture(self.image_event())).unwrap()
        request = self.request(host.application, batch)
        self.assertFalse(request.reply_images)
        wire = build_wire(ModelEnvelope(request, "task", 100, "config"))
        self.assertEqual(wire.reply_images, {})
        self.assertNotIn("sendable_image", str(wire.messages))

    async def test_atomic_generation_failure_rolls_back_parts_and_image_manifest(self):
        host = await self.setup_host()
        app = host.application
        publish = app.messages.reply_images.publish
        def fail(*args):
            publish(*args)
            raise TaskError("fixture_failure", "failure after manifest publication")
        with patch.object(app.messages.reply_images, "publish", side_effect=fail):
            batch = host.receive(await self.capture(self.image_event())).unwrap()
            await self.done(app, batch)
        self.assertEqual(app.runtime.read_one("SELECT COUNT(*) n FROM conversation_parts", ())["n"], 0)
        self.assertEqual(app.runtime.read_one("SELECT COUNT(*) n FROM sources WHERE owner = ?", (OWNER,))["n"], 0)
        self.assertEqual(self.sends(), [])

    async def test_asset_revocation_during_sender_role_await_prevents_whole_dispatch(self):
        host = await self.setup_host()
        app = host.application
        batch = host.receive(await self.capture(self.image_event())).unwrap()
        event = self.original(app, batch)
        _, asset = app.messages.images.references(event, event.images[0])
        revoked = False
        async def revoke(_):
            nonlocal revoked
            if not revoked and app.runtime.read_one("SELECT 1 FROM sources WHERE owner = ?", (OWNER,)):
                app.messages.images.assets.revoke(asset, expected_governance_revision=1, observed_at=self.clock.now()).unwrap()
                revoked = True
        reached = intercept_image_delivery_verification(self.bridge, app.runtime, revoke)
        await self.done(app, batch)
        self.assertTrue(reached())
        self.assertTrue(revoked)
        self.assertEqual(self.sends(), [])
        self.assertEqual(app.runtime.read_one("SELECT state FROM conversation_parts WHERE batch_id = ?", (batch,))[0], "failed")

    async def test_changed_image_manifest_blocks_entire_send(self):
        host = await self.setup_host()
        app = host.application
        changed = False
        async def mutate(_):
            nonlocal changed
            record = app.runtime.read_one("SELECT source_id FROM sources WHERE owner = ?", (OWNER,))
            if record and not changed:
                with app.runtime.transaction() as connection:
                    connection.execute("UPDATE sources SET payload_json = ? WHERE source_id = ?", (json.dumps("changed"), record[0]))
                changed = True
        reached = intercept_image_delivery_verification(self.bridge, app.runtime, mutate)
        batch = host.receive(await self.capture(self.image_event())).unwrap()
        await self.done(app, batch)
        self.assertTrue(reached())
        self.assertTrue(changed)
        self.assertEqual(self.sends(), [])

    async def test_restart_sends_pending_last_part_from_original_bytes_without_regeneration(self):
        async def respond(payload, _):
            return image_reply(payload, text="第一段\n这是原图。")
        self.native.provider.client.state.behavior = respond
        host = await self.setup_host(policy=ConversationPolicy(0, 0, 180, 3))
        batch = host.receive(await self.capture(self.image_event())).unwrap()
        await self.until(lambda: host.application.flow.row(batch)["reason"] == "part_spacing")
        self.assertEqual([p["type"] for p in self.sends()[0]["message"]], ["text"])
        new = await self.restart(host)
        with patch("astrbot_host.media.inspect_image", side_effect=AssertionError("no decode on send or recovery")):
            self.advance(new.application, 3)
            self.assertEqual((await self.done(new.application, batch))["state"], "complete")
        self.assertEqual(len(self.native.provider.client.state.calls), 1)
        self.assertEqual([p["type"] for p in self.sends()[1]["message"]], ["text", "image"])
        self.assertEqual(len(self.sends()), 2)

    async def test_unknown_send_survives_restart_and_late_receipt_without_resend(self):
        self.api.error = TimeoutError("synthetic lost ACK")
        host = await self.setup_host()
        captured = await self.capture(self.image_event())
        batch = host.receive(captured).unwrap()
        await self.done(host.application, batch)
        self.assertEqual(len(self.sends()), 1)
        new = await self.restart(host)
        app, context = new.application, self.context(new.application, batch)
        self.assertEqual(new.receive(captured).unwrap(), batch)
        self.advance(app, 1)
        await asyncio.sleep(0.02)
        self.assertEqual(len(self.sends()), 1)
        self.assertEqual(app.messages.history(context).unwrap()["items"], [])
        part = app.runtime.read_one("SELECT * FROM conversation_parts WHERE batch_id = ?", (batch,))
        receipt = DeliveryReceipt(part["part_id"], part["operation_id"], context.key, part["payload_hash"],
            self.native_sender.binding, "delivered", "late-image-ack", "onebot-message:synthetic:456", self.clock.now())
        wrong = replace(receipt, payload_hash=digest(part["text"]))
        self.assertEqual(app.record_receipt(wrong).failure.code, "message_receipt_mismatch")
        app.record_receipt(receipt).unwrap()
        app.reconcile_delivery(context, part["part_id"]).unwrap()
        self.assertEqual((await self.done(app, batch))["state"], "complete")
        self.assertEqual(len(app.messages.history(context).unwrap()["items"][0]["images"]), 1)
        self.assertEqual(len(self.sends()), 1)

    async def test_restart_with_text_only_sender_keeps_pending_images_unsent(self):
        async def respond(payload, _):
            return image_reply(payload, text="第一段\n这是原图。")
        self.native.provider.client.state.behavior = respond
        host = await self.setup_host(policy=ConversationPolicy(0, 0, 180, 3))
        batch = host.receive(await self.capture(self.image_event())).unwrap()
        await self.until(lambda: host.application.flow.row(batch)["reason"] == "part_spacing")
        sender = LocalSender(self.native_sender.binding, self.native_sender.send)
        new = await self.restart(host, sender=sender)
        self.advance(new.application, 3)
        flow = await self.done(new.application, batch)
        self.assertEqual((flow["state"], flow["reason"]), ("blocked", "conversation_task_failed"))
        self.assertEqual(len(self.sends()), 1)
        self.assertEqual(len(self.native.provider.client.state.calls), 1)

    async def test_known_bot_image_reply_can_be_quoted_and_original_withdrawal_propagates(self):
        host = await self.setup_host()
        first = host.receive(await self.capture(self.image_event())).unwrap()
        app = host.application
        await self.done(app, first)
        self.api.result = {"message_id": 457}
        self.native.provider.client.state.behavior = lambda *_: asyncio.sleep(0, result=completion(final_text("这是刚才附上的原图。")))
        self.raw = quote_raw(message_id="456", text="把这张原图附在这里。")
        second = host.receive(await self.capture(self.quote_event(quote_id="456"))).unwrap()
        flow = await self.done(app, second)
        self.assertEqual(flow["state"], "complete", dict(flow))
        app.withdraw(self.context(app, first), self.original(app, first).scoped_key).unwrap()
        self.assertFalse(app.messages.history(self.context(app, second)).unwrap()["items"][-1]["body_available"])

    async def test_missing_image_manifest_never_degrades_to_a_text_only_dispatch(self):
        host = await self.setup_host()
        app = host.application
        removed = False
        async def remove(_):
            nonlocal removed
            if not removed and app.runtime.read_one("SELECT 1 FROM sources WHERE owner = ?", (OWNER,)):
                with app.runtime.transaction() as connection:
                    connection.execute("DELETE FROM source_dependencies WHERE dependent_id IN (SELECT source_id FROM sources WHERE owner = ?)", (OWNER,))
                    connection.execute("DELETE FROM sources WHERE owner = ?", (OWNER,))
                removed = True
        reached = intercept_image_delivery_verification(self.bridge, app.runtime, remove)
        batch = host.receive(await self.capture(self.image_event())).unwrap()
        await self.done(app, batch)
        self.assertTrue(reached())
        self.assertTrue(removed)
        self.assertEqual(self.sends(), [])

    async def test_revoked_animation_frame_blocks_the_original_image_send(self):
        host = await self.setup_host()
        app = host.application
        batch = host.receive(await self.capture(self.image_event(content=animation_bytes()))).unwrap()
        frame = self.request(app, batch).images[0].frames[-1].source
        revoked = False
        async def revoke(_):
            nonlocal revoked
            if not revoked and app.runtime.read_one("SELECT 1 FROM sources WHERE owner = ?", (OWNER,)):
                app.messages.sources.revoke(frame, expected_governance_revision=1, observed_at=self.clock.now()).unwrap()
                revoked = True
        reached = intercept_image_delivery_verification(self.bridge, app.runtime, revoke)
        await self.done(app, batch)
        self.assertTrue(reached())
        self.assertTrue(revoked)
        self.assertEqual(self.sends(), [])

    async def test_stop_during_image_dispatch_accounts_late_receipt_without_resending(self):
        entered = asyncio.Event()
        async def held(action, **params):
            self.api.calls.append((action, params))
            entered.set()
            await asyncio.Event().wait()
        self.api.call_action = held
        host = await self.setup_host()
        batch = host.receive(await self.capture(self.image_event())).unwrap()
        app, context = host.application, self.context(host.application, batch)
        await asyncio.wait_for(entered.wait(), 3)
        await app.close(grace_seconds=0)
        part = app.runtime.read_one("SELECT * FROM conversation_parts WHERE batch_id = ?", (batch,))
        self.assertEqual(part["state"], "unknown")
        receipt = DeliveryReceipt(part["part_id"], part["operation_id"], context.key, part["payload_hash"],
            self.native_sender.binding, "delivered", "late-after-stop", "onebot-message:synthetic:456", self.clock.now())
        app.record_receipt(receipt).unwrap()
        new = await self.restart(host)
        new.application.reconcile_delivery(context, part["part_id"]).unwrap()
        self.advance(new.application, 1)
        self.assertEqual((await self.done(new.application, batch))["state"], "complete")
        self.assertEqual(len(new.application.messages.history(context).unwrap()["items"][0]["images"]), 1)
        self.assertEqual(len(self.sends()), 1)

    async def test_ordinary_reply_without_images_preserves_text_hash_and_manifest_shape(self):
        self.native.provider.client.state.behavior = lambda *_: asyncio.sleep(0, result=completion(final_text("只回复文字。")))
        host = await self.setup_host()
        batch = host.receive(await self.capture(self.image_event())).unwrap()
        await self.done(host.application, batch)
        app = host.application
        row = app.runtime.read_one("SELECT * FROM conversation_parts WHERE batch_id = ?", (batch,))
        self.assertEqual(row["payload_hash"], digest(row["text"]))
        self.assertEqual(app.runtime.read_one("SELECT COUNT(*) n FROM sources WHERE owner = ?", (OWNER,))["n"], 0)
        self.assertNotIn("images", app.messages.history(self.context(app, batch)).unwrap()["items"][0])
