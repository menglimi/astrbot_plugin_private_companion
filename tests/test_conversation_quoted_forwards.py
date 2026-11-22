"""Quoted forwards retain original governance through reply, memory and recovery."""

import asyncio
import base64
from dataclasses import asdict
from datetime import timedelta
from functools import partial
import json
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from astrbot_host.media import load_native_image
from companion_v2.adapters.astrbot.forwards import read_private_forwards
from companion_v2.adapters.astrbot.model_wire import tool_alias
from companion_v2.adapters.astrbot.quotes import read_private_quote
from companion_v2.conversation.quotes.forward_models import ForwardQuoteDescriptor
from companion_v2.conversation.quotes.service import prepare_quote, quote_reference
from companion_v2.execution.models import TaskError, digest
from companion_v2.identity_policy.personas import RuntimeIdentity
from companion_v2.sources.dependencies import key
from companion_v2.sources.models import SourceRecord
from astrbot_host.audio.loader import load_native_audio
from astrbot_host.video.loader import load_native_video
from tests.animation_support import animation_bytes
from tests.audio_support import audio_bytes
from tests.forward_support import FakeForward, ForwardFixture, node_raw, quoted_forward_raw
from tests.native_adapter_support import FakeReverseApi, completion, final_text
from tests.test_conversation_images import FakeImage, image_bytes
from tests.test_conversation_quotes import FakeReply
from tests.video_support import video_bytes


class ConversationQuotedForwardTests(ForwardFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.raw = quoted_forward_raw()

    async def admit_original(self, host, *, animation=False):
        self.forward_raw = {"messages": [node_raw(images=(animation_bytes() if animation else image_bytes(),))]}
        captured = await self.capture(self.forward_event("345", with_text=False))
        batch = host.receive(captured).unwrap()
        self.forward_message_ids.discard("345")
        self.raw = quoted_forward_raw(sender="200", nodes=self.forward_raw["messages"])
        return batch, self.original(host.application, batch)

    async def restart(self, host):
        await host.application.close()
        self.clock.advance(timedelta(seconds=0.2))
        new = self.host(runtime=self.open_runtime(), event_port=self.bridge.ports, identity_port=self.bridge.ports,
            role_port=self.bridge.ports, providers=(self.native_model.local_provider(),), sender=self.native_sender.local_sender(),
            runtime_identity=RuntimeIdentity("companion", "runtime-quoted-forward", "astrbot", "host"))
        await self.start(new, 1, 1)
        return new

    async def test_quoted_and_forward_node_audio_video_survive_restart_with_claim_boundaries(self):
        voice, clip = audio_bytes(), video_bytes()
        voice_data = {"file": "base64://" + base64.b64encode(voice).decode("ascii"), "url": ""}
        video_data = {"file": "base64://" + base64.b64encode(clip).decode("ascii"), "url": ""}
        node = node_raw("节点文字", sender="300")
        node["message"].extend(({"type": "record", "data": voice_data}, {"type": "video", "data": video_data}))
        self.raw = quoted_forward_raw(nodes=[node])
        self.raw["message"].extend(({"type": "record", "data": voice_data}, {"type": "video", "data": video_data}))
        self.bridge.quote_loader = partial(read_private_quote, reverse_api_type=FakeReverseApi,
            image_loader=load_native_image, audio_loader=load_native_audio, video_loader=load_native_video)

        captured = await self.capture(self.quote_event())
        self.assertEqual(len(captured.quotes[0].audios), 1)
        self.assertEqual(len(captured.quotes[0].videos), 1)
        self.assertEqual(len(captured.quotes[0].forwards[0].nodes[0].audios), 1)
        self.assertEqual(len(captured.quotes[0].forwards[0].nodes[0].videos), 1)
        host = await self.quiet_host()
        batch = host.receive(captured).unwrap()
        request = self.request(host.application, batch)
        self.assertEqual(len(request.audios), 2)
        self.assertEqual(len(request.videos), 2)
        audio_records = [json.loads(m.payload) for m in request.context.materials
                         if m.purpose.startswith(("quoted_audio:", "forwarded_audio:"))]
        self.assertEqual({item["attribution"] for item in audio_records}, {"sending_account_only", "forward_record_claim"})
        node_audio = next(item for item in audio_records if item["attribution"] == "forward_record_claim")
        self.assertEqual((node_audio["sender_claim"], node_audio["forwarded_by"], node_audio["quoted_by"]),
                         ("300", "100", "200"))
        self.assertTrue(all(video.parent.owner in ("conversation.quote", "conversation.forward_node")
                            for video in request.videos))
        self.assertIn("speaker_identity", str(request.context.materials))

        restored = await self.restart(host)
        replay = self.request(restored.application, batch)
        self.assertEqual(replay.audios, request.audios)
        self.assertEqual(replay.videos, request.videos)
        self.assertEqual(len([action for action, _ in self.api.calls if action == "get_friend_msg_history"]), 1)

    async def test_direct_forward_node_media_reaches_model_request(self):
        voice, clip = audio_bytes(), video_bytes()
        node = node_raw("节点资料", sender="300")
        node["message"].extend((
            {"type": "record", "data": {"file": "base64://" + base64.b64encode(voice).decode("ascii")}},
            {"type": "video", "data": {"file": "base64://" + base64.b64encode(clip).decode("ascii")}},
        ))
        self.forward_raw = {"messages": [node]}
        self.bridge.forward_loader = partial(read_private_forwards, reverse_api_type=FakeReverseApi,
            image_loader=load_native_image, audio_loader=load_native_audio, video_loader=load_native_video)
        host = await self.quiet_host()
        batch = host.receive(await self.capture(self.forward_event(with_text=False))).unwrap()
        request = self.request(host.application, batch)
        self.assertEqual((len(request.audios), len(request.videos)), (1, 1))
        audio_record = next(json.loads(m.payload) for m in request.context.materials if m.purpose.startswith("forwarded_audio:"))
        self.assertEqual(audio_record["attribution"], "forward_record_claim")
        self.assertEqual(audio_record["sender_claim"], "300")

    async def test_three_levels_of_attribution_and_historical_times_reach_prompt(self):
        self.raw = quoted_forward_raw(nodes=[node_raw(sender="100", at=1600000000)])
        host = await self.quiet_host()
        batch = host.receive(await self.capture(self.quote_event())).unwrap()
        app, context = host.application, self.context(host.application, batch)
        request, event = self.request(app, batch), self.original(app, batch)
        self.assertIsInstance(event.quotes[0], ForwardQuoteDescriptor)
        quote = json.loads(next(m.payload for m in request.context.materials if m.owner == "conversation.quote"))
        node = json.loads(next(m.payload for m in request.context.materials if m.owner == "conversation.forward_node"))
        self.assertEqual((quote["sender"], quote["quoted_by"], quote["sent_at_unix"]), ("100", "200", 1700000000))
        self.assertEqual((node["forwarded_by"], node["quoted_by"], node["displayed_sender"], node["displayed_time_unix"]),
                         ("100", "200", "100", 1600000000))
        self.assertEqual(node["original_message_id"], "345")
        self.assertEqual(node["attribution"], "forward_record_claim")
        self.assertIn("当前引用者没有因此成为原转发者", request.instruction)
        self.assertIn("显示身份未经认证", request.instruction)
        self.assertNotIn("记录中的一句话", app.messages.journal_inputs(context).items[0].source.payload)
        self.assertEqual(event.principal.native_account_id, "200")

    async def test_mixed_component_order_and_animation_frame_attribution(self):
        red, green, blue, animation = image_bytes(), image_bytes("green"), image_bytes("blue"), animation_bytes(count=2)
        nodes = [node_raw(images=(green,), nested=[node_raw(images=(animation,))])]
        nodes[0]["message"].append({"type": "image", "data": {"file": FakeImage(blue).file}})
        self.raw = quoted_forward_raw(direct=True, nodes=nodes)
        event = self.forward_event()
        event.message_obj.message = [FakeImage(red), FakeReply(), FakeForward(), FakeImage(blue)]
        host = await self.quiet_host()
        batch = host.receive(await self.capture(event)).unwrap()
        request = self.request(host.application, batch)
        self.assertEqual([base64.b64decode(i.data_base64) for i in request.images], [red, red, green, animation, blue, red, blue])
        frames = [json.loads(m.payload) for m in request.context.materials if m.purpose.startswith("forwarded_image:frame:")]
        self.assertEqual([m["node_path"] for m in frames], [[0, 2, 0]] * 2)
        self.assertEqual([(m["forwarded_by"], m["quoted_by"], m["original_message_id"]) for m in frames], [("100", "200", "345")] * 2)

    async def test_publication_failure_rolls_back_quote_nodes_images_and_dependencies(self):
        host = await self.quiet_host()
        app = host.application
        captured = await self.capture(self.quote_event(direct=True))
        publish = app.messages.forwards.publish
        def fail(*args):
            publish(*args)
            raise TaskError("fixture_failure", "quoted forward publication failed")
        with patch.object(app.messages.forwards, "publish", side_effect=fail):
            self.assertEqual(host.receive(captured).failure.code, "fixture_failure")
        for table in ("sources", "source_dependencies", "assets", "conversation_image_payloads", "conversation_inputs", "conversation_batches"):
            self.assertEqual(app.runtime.read_one(f"SELECT COUNT(*) n FROM {table}", ())["n"], 0, table)

    async def test_known_original_links_all_materials_and_withdrawal_blocks_reimport(self):
        host = await self.quiet_host()
        batch, original = await self.admit_original(host, animation=True)
        app, context = host.application, self.context(host.application, batch)
        materials, _, _ = app.messages.forwards.read(context, original, self.clock.now())
        host.receive(await self.capture(self.quote_event())).unwrap()
        quoted = self.original(app, batch, position=2)
        source = quote_reference(quoted.source, quoted.quotes[0].position)
        links = app.runtime.read_all("SELECT input_id, input_lineage, input_revision FROM source_dependencies WHERE dependent_id = ?", (source.source_id,))
        for reference in (original.source, *(m.source for m in materials)):
            self.assertIn(key(reference), [tuple(r) for r in links])
        app.messages.quotes.read(context, quoted, self.clock.now())
        app.withdraw(context, original.scoped_key).unwrap()
        self.assertEqual(app.messages.sources.validate_reference(source, observed_at=self.clock.now()).failure.code, "source_revoked")
        self.assertEqual(host.receive(await self.capture(self.quote_event("reimport"))).failure.code, "source_revoked")

    async def test_known_original_node_changes_or_missing_cards_cannot_be_reimported(self):
        host = await self.quiet_host()
        await self.admit_original(host)
        self.raw["message"][0]["data"]["content"][0]["message"][0]["data"]["text"] = "替换了旧节点"
        self.assertEqual(host.receive(await self.capture(self.quote_event())).failure.code, "quote_original_changed")
        self.raw["message"] = [{"type": "text", "data": {"text": "\n[合并转发 1]\n"}}]
        self.assertEqual(host.receive(await self.capture(self.quote_event("missing"))).failure.code, "quote_original_changed")

    async def test_known_original_frame_revocation_blocks_quote_and_reimport(self):
        host = await self.quiet_host()
        batch, original = await self.admit_original(host, animation=True)
        app, context = host.application, self.context(host.application, batch)
        host.receive(await self.capture(self.quote_event())).unwrap()
        quote = self.original(app, batch, position=2)
        forward, node = original.forwards[0], original.forwards[0].nodes[0]
        parent = app.messages.forwards.image_event(original, forward, node)
        frame_parent = app.messages.images.frame_event(parent, node.images[0])
        _, asset = app.messages.images.references(frame_parent, frame_parent.images[1])
        app.messages.images.assets.revoke(asset, expected_governance_revision=1, observed_at=self.clock.now()).unwrap()
        with self.assertRaises(TaskError) as caught:
            app.messages.quotes.read(context, quote, self.clock.now())
        self.assertEqual(caught.exception.code, "asset_revoked")
        self.assertEqual(host.receive(await self.capture(self.quote_event("reimport"))).failure.code, "asset_revoked")

    async def test_known_original_in_previous_host_session_is_not_reowned(self):
        host = await self.quiet_host()
        await self.admit_original(host)
        self.native.cid = "new-session"
        self.assertEqual(host.receive(await self.capture(self.quote_event())).failure.code, "quote_original_scope_mismatch")

    async def test_redacted_node_changes_still_conflict_with_same_input(self):
        self.raw = quoted_forward_raw(nodes=[node_raw("password=synthetic-quoted-secret", name="token=synthetic-name")])
        host = await self.quiet_host()
        captured = await self.capture(self.quote_event())
        batch = host.receive(captured).unwrap()
        self.assertEqual(host.receive(captured).unwrap(), batch)
        self.raw["message"][0]["data"]["content"][0]["sender"]["nickname"] = "token=synthetic-other-name"
        self.assertEqual(host.receive(await self.capture(self.quote_event())).failure.code, "message_event_conflict")
        for table, column in (("sources", "payload_json"), ("conversation_inputs", "event_json")):
            stored = " ".join(row[column] for row in host.application.runtime.read_all(f"SELECT {column} FROM {table}", ()))
            self.assertNotIn("synthetic-quoted-secret", stored)
            self.assertNotIn("synthetic-name", stored)

    async def test_tool_continuation_and_confirmed_memory_preserve_quoted_node_sources(self):
        self.raw = quoted_forward_raw(nodes=[node_raw(images=(animation_bytes(),))])
        snapshots = []
        async def respond(payload, _):
            content = next(m["content"] for m in payload["messages"] if m["role"] == "user")
            materials = json.loads(content[0]["text"])["materials"]
            snapshots.append((content[1:], [m for m in materials if m["evidence"] is not None
                                           and m["owner"].startswith("conversation.")]))
            if not any(m["role"] == "tool" for m in payload["messages"]):
                return completion(tools=[NS(id="quoted-record-memo", type="function",
                    function=NS(name=tool_alias("memo.list"), arguments='{}'))])
            evidence = json.loads(content[1]["text"])["image_evidence"]
            return completion(final_text("这份引用记录的首帧是一片红色。", evidence=evidence))
        self.native.provider.client.state.behavior = respond
        host = await self.setup_host()
        batch = host.receive(await self.capture(self.quote_event())).unwrap()
        app, context = host.application, self.context(host.application, batch)
        self.assertEqual((await self.done(app, batch))["state"], "complete")
        self.assertEqual(len(snapshots), 2)
        self.assertEqual(snapshots[0], snapshots[1])
        owner = app.memory.writer.access.sent_sources
        reference = tuple(owner.rows(context))[0]
        owner.materialize(context, reference)
        links = app.runtime.read_all("SELECT input_id, input_lineage, input_revision FROM source_dependencies WHERE dependent_id = ?", (reference.source_id,))
        for material in self.request(app, batch).context.materials:
            if material.source and material.owner.startswith("conversation."):
                self.assertIn(key(material.source), [tuple(row) for row in links])
        app.withdraw(context, self.original(app, batch).scoped_key).unwrap()
        with self.assertRaises(TaskError):
            owner.validate(context, reference)
        self.assertEqual([a for a, _ in self.api.calls].count("send_private_msg"), 1)

    async def test_quote_frame_revocation_after_generation_blocks_delivery(self):
        self.raw = quoted_forward_raw(nodes=[node_raw(images=(animation_bytes(),))])
        host = await self.setup_host()
        batch = host.receive(await self.capture(self.quote_event())).unwrap()
        app, event = host.application, self.original(host.application, batch)
        parent = app.messages.quotes.forward_event(event, event.quotes[0])
        forward, node = parent.forwards[0], parent.forwards[0].nodes[0]
        image_parent = app.messages.forwards.image_event(parent, forward, node)
        frame_parent = app.messages.images.frame_event(image_parent, node.images[0])
        _, asset = app.messages.images.references(frame_parent, frame_parent.images[1])
        revoked = False
        async def after(_):
            nonlocal revoked
            if self.native.provider.client.state.calls and not revoked:
                app.messages.images.assets.revoke(asset, expected_governance_revision=1, observed_at=self.clock.now()).unwrap()
                revoked = True
        self.native.before_resolve = after
        await self.done(app, batch)
        self.assertTrue(revoked)
        self.assertEqual([a for a, _ in self.api.calls].count("send_private_msg"), 0)

    async def test_unknown_model_result_is_not_replayed(self):
        async def timeout(*_):
            raise TimeoutError("synthetic quoted record result unknown")
        self.native.provider.client.state.behavior = timeout
        host = await self.setup_host()
        captured = await self.capture(self.quote_event())
        batch = host.receive(captured).unwrap()
        await self.done(host.application, batch)
        self.assertEqual(host.receive(captured).unwrap(), batch)
        await asyncio.sleep(0)
        self.assertEqual(len(self.native.provider.client.state.calls), 1)
        self.assertEqual([a for a, _ in self.api.calls].count("send_private_msg"), 0)

    async def test_restart_restores_all_originals_and_shared_sixteen_frame_budget(self):
        self.raw = quoted_forward_raw(nodes=[node_raw(images=(animation_bytes(count=10),))])
        host = await self.quiet_host()
        first = host.receive(await self.capture(self.quote_event())).unwrap()
        second = host.receive(await self.capture(self.quote_event("second"))).unwrap()
        self.assertEqual(first, second)
        before = self.request(host.application, first)
        reads = len(self.api.calls)
        new = await self.restart(host)
        with patch("astrbot_host.media.inspect_image", side_effect=AssertionError("recovery may not decode")):
            after = self.request(new.application, first)
            self.assertEqual((after.images, after.context.materials), (before.images, before.context.materials))
        self.assertEqual(len(self.api.calls), reads)
        self.forward_raw = {"messages": [node_raw(images=(animation_bytes(count=10),))]}
        for identifier in ("678", "679"):
            self.assertEqual(new.receive(await self.capture(self.forward_event(identifier))).unwrap(), first)
        images = self.request(new.application, first).images
        self.assertEqual(sum(i.descriptor.rendered_count for i in images), 16)
        self.assertNotEqual(new.receive(await self.capture(self.image_event())).unwrap(), first)

    async def assert_restart_budget_boundary(self, nodes, cards=1):
        self.raw = quoted_forward_raw(nodes=nodes, cards=cards)
        self.forward_raw = {"messages": nodes}
        host = await self.quiet_host()
        first = host.receive(await self.capture(self.quote_event())).unwrap()
        new = await self.restart(host)
        second = new.receive(await self.capture(self.forward_event())).unwrap()
        self.assertNotEqual(first, second)

    async def test_card_budget_includes_pre_restart_quoted_forwards(self):
        await self.assert_restart_budget_boundary([node_raw()], cards=4)

    async def test_node_budget_includes_pre_restart_quoted_forwards(self):
        await self.assert_restart_budget_boundary([node_raw()] * 20)

    async def test_text_budget_includes_pre_restart_quoted_forwards(self):
        await self.assert_restart_budget_boundary([node_raw("x" * 33000)])

    async def test_ordinary_quote_shape_and_hash_remain_unchanged(self):
        self.raw["message"] = [{"type": "text", "data": {"text": "普通引用原文"}}]
        captured = await self.capture(self.quote_event())
        host = await self.quiet_host()
        batch = host.receive(captured).unwrap()
        app, event = host.application, self.original(host.application, batch)
        item, quote = captured.quotes[0], event.quotes[0]
        self.assertNotIn("forwards", asdict(quote))
        fingerprint = digest([item.position, item.message_id, item.sender_id, item.sent_at, item.text, ()])
        expected = app.messages.sources.prepare_message(SourceRecord(quote_reference(event.source, item.position),
            self.context(app, batch).audience, fingerprint, item.text))
        self.assertEqual(quote.content_hash, expected.content_hash)
        self.assertEqual(prepare_quote(app.messages.sources, event.source, self.context(app, batch).audience, item)[0], quote)
