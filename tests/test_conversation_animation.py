"""Animation originals through the governed conversation and model loop."""

import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from companion_v2.adapters.astrbot.model_wire import build_wire, tool_alias
from companion_v2.conversation.media.animation import AnimationDescriptor, PromptAnimation
from companion_v2.conversation.media.codec import image_descriptor
from companion_v2.conversation.media.models import PromptImage
from companion_v2.execution.models import TaskError, encode
from companion_v2.identity_policy.personas import RuntimeIdentity
from companion_v2.model_gateway.models import ModelEnvelope
from companion_v2.sources.dependencies import key
from tests.animation_support import animation_bytes
from tests.test_conversation_images import ImageFixture, FakeImage
from tests.test_conversation_quotes import QuoteFixture, quote_raw
from tests.native_adapter_support import completion, final_text


class ConversationAnimationTests(ImageFixture, unittest.IsolatedAsyncioTestCase):
    def animation_event(self, identifier="animation-1", *, count=3, direct=False):
        event = self.image_event(identifier, content=animation_bytes(count=count), with_text=True)
        if direct:
            event.message_obj.message.append(FakeImage(animation_bytes(count=2)))
        return event

    async def test_animation_is_persisted_as_original_with_exact_frame_views_and_wire_metadata(self):
        host = await self.quiet_host()
        captured = await self.capture(self.animation_event())
        batch = host.receive(captured).unwrap()
        event = self.original(host.application, batch)
        self.assertIsInstance(event.images[0], AnimationDescriptor)
        self.assertEqual((event.images[0].frame_count, event.images[0].coverage), (3, "all_frames"))
        request = self.request(host.application, batch)
        self.assertIsInstance(request.images[0], PromptAnimation)
        self.assertEqual(len(request.images[0].frames), 3)
        self.assertEqual([frame.descriptor.position for frame in request.images[0].frames], [0, 1, 2])
        self.assertEqual([frame.descriptor.mime_type for frame in request.images[0].frames], ["image/png"] * 3)
        self.assertEqual(request.images[0].descriptor, event.images[0])
        self.assertEqual(image_descriptor(json.loads(encode(event.images[0]))), event.images[0])
        frame_materials = [json.loads(item.payload) for item in request.context.materials
                           if item.purpose.startswith("input_image:frame:")]
        self.assertEqual([item["frame_index"] for item in frame_materials], [0, 1, 2])
        self.assertEqual([item["start_ms"] for item in frame_materials], [0, 40, 80])

        def image_evidence(payload):
            content = next(m["content"] for m in payload["messages"] if m["role"] == "user")
            materials = json.loads(content[0]["text"])["materials"]
            return next(item["evidence"] for item in materials if item["owner"] == "conversation.image")

        async def respond(payload, _):
            content = next(m["content"] for m in payload["messages"] if m["role"] == "user")
            self.assertEqual(len([part for part in content if part["type"] == "image_url"]), 3)
            self.assertTrue(all(part["image_url"]["url"].startswith("data:image/png;base64,")
                                for part in content if part["type"] == "image_url"))
            return completion(final_text("动画的三帧依次变化。", evidence=image_evidence(payload)))

        self.native.provider.client.state.behavior = respond
        self.advance(host.application, 3)
        self.assertEqual((await self.done(host.application, batch))["state"], "complete")
        self.assertEqual(len(self.native.provider.client.state.calls), 1)
        self.assertEqual(len(self.native.platform.client._api._wsr_api.calls), 1)

    async def test_long_animation_is_sampled_and_is_counted_by_rendered_frame_budget(self):
        host = await self.quiet_host()
        batch = host.receive(await self.capture(self.image_event("long", animation_bytes(count=10)))).unwrap()
        request = self.request(host.application, batch)
        self.assertEqual(request.images[0].descriptor.coverage, "sampled_frames")
        self.assertEqual([f.descriptor.position for f in request.images[0].frames], list(range(4)))
        self.assertEqual(len(request.images[0].frames), 4)
        for index in range(3):
            self.assertEqual(host.receive(await self.capture(self.image_event("long-" + str(index), animation_bytes(count=10)))).unwrap(), batch)
        # Four four-frame views fit; another three-frame view exceeds 16 slots.
        second = host.receive(await self.capture(self.animation_event("next", count=3)))
        self.assertNotEqual(second.unwrap(), batch)

    async def test_animation_and_direct_images_share_retained_byte_and_frame_budgets(self):
        host = await self.quiet_host()
        batch = host.receive(await self.capture(self.animation_event(direct=True))).unwrap()
        self.assertEqual(len(self.request(host.application, batch).images), 2)
        # The two attachment originals expand to five distinct model frames.
        self.assertEqual(sum(len(image.rendered_images) for image in self.request(host.application, batch).images), 5)

    async def test_each_frame_asset_is_revalidated(self):
        host = await self.quiet_host()
        batch = host.receive(await self.capture(self.animation_event())).unwrap()
        before = self.request(host.application, batch)
        event = self.original(host.application, batch)
        frame_event = host.application.messages.images.frame_event(event, event.images[0])
        _, frame_asset = host.application.messages.images.references(frame_event, event.images[0].frames[1].image)
        host.application.messages.images.assets.revoke(frame_asset, expected_governance_revision=1,
                                                       observed_at=self.clock.now()).unwrap()
        with self.assertRaises(TaskError) as caught:
            self.request(host.application, batch)
        self.assertEqual(caught.exception.code, "asset_revoked")
        self.assertEqual(before.images[0].descriptor, event.images[0])

    async def test_frame_revoked_after_generation_prevents_platform_send(self):
        host = await self.setup_host()
        app = host.application
        batch = host.receive(await self.capture(self.animation_event())).unwrap()
        event = self.original(app, batch)
        parent = app.messages.images.frame_event(event, event.images[0])
        _, asset = app.messages.images.references(parent, parent.images[1])
        revoked = False
        async def revoke_after_model(_):
            nonlocal revoked
            if self.native.provider.client.state.calls and not revoked:
                app.messages.images.assets.revoke(asset, expected_governance_revision=1, observed_at=self.clock.now()).unwrap()
                revoked = True
        self.native.before_resolve = revoke_after_model
        await self.done(app, batch)
        self.assertTrue(revoked)
        self.assertEqual(len(self.native.provider.client.state.calls), 1)
        self.assertEqual(self.native.platform.client._api._wsr_api.calls, [])

    async def test_unknown_animation_result_is_not_retried_on_event_replay(self):
        async def timeout(*_):
            raise TimeoutError("fixture unknown animation result")
        self.native.provider.client.state.behavior = timeout
        host = await self.setup_host()
        captured = await self.capture(self.animation_event())
        batch = host.receive(captured).unwrap()
        await self.done(host.application, batch)
        self.assertEqual(host.receive(captured).unwrap(), batch)
        await asyncio.sleep(0)
        self.assertEqual(len(self.native.provider.client.state.calls), 1)
        self.assertEqual(self.native.platform.client._api._wsr_api.calls, [])

    async def test_animation_publication_failure_rolls_back_original_and_frame_rows(self):
        host = await self.quiet_host()
        captured = await self.capture(self.animation_event())
        original = host.application.messages.images.publish
        calls = 0
        def fail_after(*args):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise TaskError("fixture_failure", "frame publication failed")
            return original(*args)
        with patch.object(host.application.messages.images, "publish", side_effect=fail_after):
            self.assertEqual(host.receive(captured).failure.code, "fixture_failure")
        for table in ("conversation_inputs", "conversation_batches", "conversation_image_payloads", "sources", "assets"):
            self.assertEqual(host.application.runtime.read_one(f"SELECT COUNT(*) n FROM {table}", ())["n"], 0, table)

    async def test_restart_reuses_persisted_views_without_decoding_or_downloading(self):
        host = await self.quiet_host()
        captured = await self.capture(self.animation_event())
        batch = host.receive(captured).unwrap()
        before = self.request(host.application, batch)
        await host.application.close()
        self.clock.advance(timedelta(seconds=0.2))
        new = self.host(runtime=self.open_runtime(), event_port=self.bridge.ports, identity_port=self.bridge.ports,
            role_port=self.bridge.ports, providers=(self.native_model.local_provider(),), sender=self.native_sender.local_sender(),
            runtime_identity=RuntimeIdentity("companion", "runtime-2", "astrbot", "host"))
        await self.start(new, 1, 1)
        with patch("astrbot_host.animation.inspect_animation", side_effect=AssertionError("do not decode again")):
            self.assertEqual(self.request(new.application, batch).images, before.images)
            self.assertEqual(new.receive(captured).unwrap(), batch)

    async def test_withdrawal_revokes_animation_and_all_frame_sources(self):
        host = await self.quiet_host()
        captured = await self.capture(self.animation_event())
        batch = host.receive(captured).unwrap()
        app, context = host.application, self.context(host.application, batch)
        image = self.request(app, batch).images[0]
        app.withdraw(context, self.original(app, batch).scoped_key).unwrap()
        for view in image.evidence_images:
            self.assertEqual(app.messages.sources.validate_reference(view.source, observed_at=self.clock.now()).failure.code, "source_revoked")
        self.assertEqual(host.receive(captured).failure.code, "message_withdrawn")

    async def test_missing_frame_or_corrupted_original_prevents_model_request(self):
        host = await self.quiet_host()
        app = host.application
        for index, error in enumerate(("image_original_unavailable", "image_content_changed")):
            batch = host.receive(await self.capture(self.animation_event("broken-" + str(index)))).unwrap()
            image = self.request(app, batch).images[0]
            with app.runtime.transaction() as connection:
                if index == 0:
                    connection.execute("DELETE FROM conversation_image_payloads WHERE source_id = ?", (image.frames[1].source.source_id,))
                else:
                    connection.execute("UPDATE conversation_image_payloads SET content = ? WHERE source_id = ?", (b"broken", image.source.source_id))
            with self.assertRaises(TaskError) as caught:
                self.request(app, batch)
            self.assertEqual(caught.exception.code, error)
            app.cancel(self.context(app, batch), batch).unwrap()

    async def test_tool_continuation_and_confirmed_memory_retain_every_frame_dependency(self):
        payloads = []
        async def respond(payload, _):
            content = next(m["content"] for m in payload["messages"] if m["role"] == "user")
            payloads.append(content)
            if not any(m["role"] == "tool" for m in payload["messages"]):
                return completion(tools=[NS(id="frames-memo", type="function", function=NS(name=tool_alias("memo.list"), arguments='{}'))])
            evidence = json.loads(content[1]["text"])["image_evidence"]
            return completion(final_text("第一帧是红色的。", evidence=evidence))
        self.native.provider.client.state.behavior = respond
        host = await self.setup_host()
        batch = host.receive(await self.capture(self.animation_event())).unwrap()
        app, context = host.application, self.context(host.application, batch)
        self.assertEqual((await self.done(app, batch))["state"], "complete")
        self.assertEqual(len(payloads), 2)
        self.assertEqual(payloads[0][1:], payloads[1][1:])
        materials = [[m for m in json.loads(p[0]["text"])["materials"] if m["owner"] == "conversation.image"] for p in payloads]
        self.assertEqual(materials[0], materials[1])
        images = self.request(app, batch).images
        owner = app.memory.writer.access.sent_sources
        reference = tuple(owner.rows(context))[0]
        owner.materialize(context, reference)
        links = app.runtime.read_all("SELECT input_id, input_lineage, input_revision FROM source_dependencies WHERE dependent_id = ?", (reference.source_id,))
        for view in images[0].evidence_images:
            self.assertIn(key(view.source), [tuple(row) for row in links])
        event = self.original(app, batch)
        parent = app.messages.images.frame_event(event, event.images[0])
        _, asset = app.messages.images.references(parent, parent.images[1])
        app.messages.images.assets.revoke(asset, expected_governance_revision=1, observed_at=self.clock.now()).unwrap()
        with self.assertRaises(TaskError):
            owner.validate(context, reference)
        self.assertEqual(len(self.native.platform.client._api._wsr_api.calls), 1)

    async def test_sampled_animation_fits_default_model_budget_and_reserves_each_frame(self):
        host = await self.setup_host()
        batch = host.receive(await self.capture(self.animation_event(count=10))).unwrap()
        wire = build_wire(ModelEnvelope(self.request(host.application, batch), "task", 100, "fixture"))
        self.assertEqual(wire.image_count, 4)
        self.assertGreater(wire.input_estimate(), 4 * 16384)
        self.assertLess(wire.input_estimate(), self.native_model.maximum_input_estimate)
        self.assertEqual((await self.done(host.application, batch))["state"], "complete")

    async def test_native_preparation_may_not_drop_frame_timing_or_coverage(self):
        host = await self.setup_host()
        original = type(self.native.provider)._prepare_chat_payload
        async def drop_metadata(native, **kwargs):
            content = kwargs["contexts"][2]["content"]
            kwargs["contexts"][2]["content"] = [content[0], *(p for p in content[1:] if p["type"] == "image_url")]
            return await original(native, **kwargs)
        with patch.object(type(self.native.provider), "_prepare_chat_payload", drop_metadata):
            batch = host.receive(await self.capture(self.animation_event())).unwrap()
            await self.done(host.application, batch)
        self.assertEqual(self.native.provider.client.state.calls, [])

    async def test_animation_cannot_be_downgraded_to_a_single_static_prompt_image(self):
        host = await self.quiet_host()
        batch = host.receive(await self.capture(self.animation_event())).unwrap()
        image = self.request(host.application, batch).images[0]
        with self.assertRaises(TaskError):
            PromptImage(image.source, image.parent, image.descriptor, image.data_base64)

    async def test_same_event_cannot_replace_original_timing(self):
        host = await self.quiet_host()
        captured = await self.capture(self.animation_event())
        batch = host.receive(captured).unwrap()
        changed = await self.capture(self.image_event("animation-1", animation_bytes(durations=[40, 50, 60])))
        self.assertEqual(host.receive(changed).failure.code, "message_event_conflict")
        self.assertEqual(self.request(host.application, batch).images[0].descriptor.duration_ms, 120)

    async def test_single_input_frames_cannot_exceed_admission_budget(self):
        too_many = await self.bridge.capture(self.image_event("too-many", animation_bytes(count=10), count=6))
        self.assertFalse(too_many.is_success)
        self.assertEqual(too_many.failure.code, "image_manifest_invalid")


class QuotedAnimationTests(QuoteFixture, unittest.IsolatedAsyncioTestCase):
    async def test_direct_and_quoted_frames_share_one_admission_budget(self):
        self.raw = quote_raw(content=animation_bytes(count=10))
        event = self.quote_event()
        event.message_obj.message.extend(FakeImage(animation_bytes(count=10)) for _ in range(4))
        result = await self.bridge.capture(event)
        self.assertEqual(result.failure.code, "host_image_limit")

    async def test_robot_quote_expands_frames_with_original_sender(self):
        self.raw = quote_raw(content=animation_bytes())
        host = await self.quiet_host()
        batch = host.receive(await self.capture(self.quote_event(direct=True))).unwrap()
        request = self.request(host.application, batch)
        self.assertIsInstance(request.images[0], PromptAnimation)
        self.assertEqual(len(request.images[0].frames), 3)
        frame_materials = [json.loads(m.payload) for m in request.context.materials if m.purpose.startswith("quoted_image:frame:")]
        self.assertEqual([m["sender"] for m in frame_materials], ["100"] * 3)
        self.assertEqual([m["quoted_by"] for m in frame_materials], ["200"] * 3)
        self.assertEqual([m["original_message_id"] for m in frame_materials], ["345"] * 3)

    async def test_known_original_frame_revocation_blocks_quoted_copy(self):
        host = await self.quiet_host()
        app = host.application
        first = host.receive(await self.capture(self.image_event("345", animation_bytes(), with_text=False))).unwrap()
        original = self.original(app, first)
        self.raw = quote_raw(sender="200", text="", content=animation_bytes())
        host.receive(await self.capture(self.quote_event())).unwrap()
        parent = app.messages.images.frame_event(original, original.images[0])
        _, asset = app.messages.images.references(parent, parent.images[1])
        app.messages.images.assets.revoke(asset, expected_governance_revision=1, observed_at=self.clock.now()).unwrap()
        quote = self.original(app, first, position=2)
        with self.assertRaises(TaskError) as caught:
            app.messages.quotes.read(self.context(app, first), quote, self.clock.now())
        self.assertEqual(caught.exception.code, "asset_revoked")
