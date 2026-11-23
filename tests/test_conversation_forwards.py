"""Forward originals through private conversation, delivery, memory and recovery."""

import asyncio
import base64
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import json
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from companion_v2.adapters.astrbot.forwards import freeze_nodes
from companion_v2.adapters.astrbot.model_wire import tool_alias
from companion_v2.conversation.forwards.models import (MAX_DEPTH, MAX_FORWARDS, MAX_NODES, ForwardConversationEvent,
                                                       ForwardIntakePolicy, ForwardNodeDescriptor, NODE_OWNER)
from companion_v2.conversation.forwards.service import forward_reference, node_reference
from companion_v2.execution.models import TaskError
from companion_v2.foundation.refs import SubjectKind, SubjectRef
from companion_v2.identity_policy.personas import RuntimeIdentity
from companion_v2.sources.dependencies import key
from tests.animation_support import animation_bytes
from tests.forward_support import ForwardFixture, FakeForward, node_raw
from tests.native_adapter_support import completion, final_text
from tests.test_conversation_images import FakeImage, image_bytes
from tests.test_conversation_quotes import FakeReply


class ConversationForwardTests(ForwardFixture, unittest.IsolatedAsyncioTestCase):
    async def test_forward_reading_can_be_disabled_in_the_sealed_reply_snapshot(self):
        host = await self.quiet_host()
        batch = host.receive(await self.capture(self.forward_event())).unwrap()
        app = host.application
        row = app.messages.batch(batch)
        context = self.context(app, batch)
        reply_snapshot = app.reply_settings.capture(context, self.clock.now())
        reply_snapshot["settings"]["forward_message_adaptation_enabled"] = False
        saved = {"reply_settings": reply_snapshot}
        with app.runtime.transaction() as connection:
            connection.execute("UPDATE conversation_batches SET contributions_json = ? WHERE batch_id = ?",
                               (json.dumps(saved, ensure_ascii=False), batch))
        with patch.object(app.messages.forwards, "read_all", side_effect=AssertionError("disabled forwards were read")):
            request = self.request(app, batch)
        self.assertFalse(any(material.owner == NODE_OWNER for material in request.context.materials))
        self.assertNotIn("显示身份未经认证", request.instruction)

    async def test_node_bodies_attribution_and_audience_are_separate_from_current_user(self):
        self.forward_raw = {"messages": [node_raw("这是记录中的承诺", sender="200"), node_raw(sender="100", at=None)]}
        host = await self.quiet_host()
        batch = host.receive(await self.capture(self.forward_event())).unwrap()
        app = host.application
        request, event = self.request(app, batch), self.original(app, batch)
        self.assertIsInstance(event, ForwardConversationEvent)
        self.assertNotIn("承诺", request.context.materials[0].payload)
        nodes = [json.loads(m.payload) for m in request.context.materials if m.owner == NODE_OWNER]
        self.assertEqual([n["displayed_sender"] for n in nodes], ["200", "100"])
        self.assertEqual([n["forwarded_by"] for n in nodes], ["200", "200"])
        self.assertEqual([n["attribution"] for n in nodes], ["forward_record_claim"] * 2)
        self.assertEqual([n["original_audience"] for n in nodes], ["unknown"] * 2)
        self.assertIsNone(nodes[1]["displayed_time_unix"])
        self.assertIn("显示身份未经认证", request.instruction)
        journal = app.messages.journal_inputs(self.context(app, batch))
        self.assertNotIn("承诺", journal.items[0].source.payload)
        for material in request.context.materials:
            if material.source and material.source.owner in (NODE_OWNER, "conversation.forward"):
                row = app.messages._read_source(material.source, self.context(app, batch), "text", self.clock.now())
                self.assertEqual(row.audience, self.context(app, batch).audience)

    async def test_forward_only_text_uses_normal_generation_and_confirmed_delivery(self):
        self.forward_raw = {"messages": [node_raw()]}
        async def respond(payload, _):
            materials = json.loads(next(m["content"] for m in payload["messages"] if m["role"] == "user"))["materials"]
            evidence = next(m["evidence"] for m in materials if m["owner"] == NODE_OWNER)
            return completion(final_text("转发记录里有一句话。", evidence=evidence))
        self.native.provider.client.state.behavior = respond
        host = await self.setup_host()
        batch = host.receive(await self.capture(self.forward_event(with_text=False))).unwrap()
        self.assertEqual((await self.done(host.application, batch))["state"], "complete")
        self.assertEqual(len(self.native.provider.client.state.calls), 1)
        self.assertEqual(len([a for a, _ in self.api.calls if a == "send_private_msg"]), 1)

    async def test_direct_quote_nested_static_and_animation_images_keep_component_order(self):
        red, green, blue = image_bytes(), image_bytes("green"), image_bytes("blue")
        animation = animation_bytes(count=2)
        self.forward_raw = {"messages": [node_raw(images=(green,), nested=[node_raw(images=(animation,))]) ]}
        self.forward_raw["messages"][0]["message"].append({"type": "image", "data": {"file": FakeImage(blue).file}})
        event = self.forward_event()
        event.message_obj.message = [FakeImage(red), FakeForward(), FakeReply(), FakeImage(blue)]
        host = await self.quiet_host()
        batch = host.receive(await self.capture(event)).unwrap()
        request = self.request(host.application, batch)
        self.assertEqual([base64.b64decode(i.data_base64) for i in request.images], [red, green, animation, blue, red, blue])
        frames = [json.loads(m.payload) for m in request.context.materials if m.purpose.startswith("forwarded_image:frame:")]
        self.assertEqual([m["node_path"] for m in frames], [[0, 2, 0]] * 2)
        self.assertEqual([m["forwarded_by"] for m in frames], ["200"] * 2)

    async def test_publication_failure_rolls_back_nodes_sources_and_all_images(self):
        host = await self.quiet_host()
        captured = await self.capture(self.forward_event())
        publish = host.application.messages.images.publish
        def fail(*args):
            publish(*args)
            raise TaskError("fixture_failure", "forward image publication failed")
        with patch.object(host.application.messages.images, "publish", side_effect=fail):
            self.assertEqual(host.receive(captured).failure.code, "fixture_failure")
        for table in ("conversation_inputs", "conversation_batches", "conversation_image_payloads", "sources", "assets"):
            self.assertEqual(host.application.runtime.read_one(f"SELECT COUNT(*) n FROM {table}", ())["n"], 0, table)

    async def test_redacted_node_text_and_display_name_keep_replay_conflicts(self):
        self.forward_raw = {"messages": [node_raw("password=synthetic-first-secret", name="token=synthetic-name-secret")]}
        host = await self.quiet_host()
        captured = await self.capture(self.forward_event())
        batch = host.receive(captured).unwrap()
        for name, text in (("token=synthetic-other-name", "password=synthetic-first-secret"),
                           ("token=synthetic-name-secret", "password=synthetic-other-text")):
            self.forward_raw = {"messages": [node_raw(text, name=name)]}
            self.assertEqual(host.receive(await self.capture(self.forward_event())).failure.code, "message_event_conflict")
        for table, column in (("sources", "payload_json"), ("conversation_inputs", "event_json")):
            stored = " ".join(row[column] for row in host.application.runtime.read_all(f"SELECT {column} FROM {table}", ()))
            self.assertNotIn("synthetic-first-secret", stored)
            self.assertNotIn("synthetic-name-secret", stored)
        self.assertIn("[REDACTED]", str(self.request(host.application, batch).context.materials))

    async def test_event_replay_cannot_change_node_order_or_resource(self):
        host = await self.quiet_host()
        captured = await self.capture(self.forward_event())
        batch = host.receive(captured).unwrap()
        self.assertEqual(host.receive(captured).unwrap(), batch)
        self.forward_raw["messages"].reverse()
        self.assertEqual(host.receive(await self.capture(self.forward_event())).failure.code, "message_event_conflict")
        self.forward_raw["messages"].reverse()
        self.assertEqual(host.receive(await self.capture(self.forward_event(resource="different"))).failure.code, "message_event_conflict")

    async def test_outer_withdrawal_revokes_all_nodes_animation_and_frame_dependencies(self):
        self.forward_raw = {"messages": [node_raw(nested=[node_raw(images=(animation_bytes(),))])]}
        host = await self.quiet_host()
        captured = await self.capture(self.forward_event())
        batch = host.receive(captured).unwrap()
        app = host.application
        request = self.request(app, batch)
        app.withdraw(self.context(app, batch), self.original(app, batch).scoped_key).unwrap()
        for reference in request.context.evidence_sources:
            if reference.owner.startswith("conversation."):
                self.assertEqual(app.messages.sources.validate_reference(reference, observed_at=self.clock.now()).failure.code, "source_revoked")
        self.assertEqual(host.receive(captured).failure.code, "message_withdrawn")

    async def test_node_body_change_blocks_request(self):
        host = await self.quiet_host()
        batch = host.receive(await self.capture(self.forward_event())).unwrap()
        app = host.application
        node = next(m.source for m in self.request(app, batch).context.materials if m.owner == NODE_OWNER)
        with app.runtime.transaction() as connection:
            connection.execute("UPDATE sources SET payload_json = ? WHERE source_id = ?", (json.dumps("changed"), node.source_id))
        with self.assertRaises(TaskError) as caught:
            self.request(app, batch)
        self.assertEqual(caught.exception.code, "forward_content_changed")

    async def test_another_participant_or_role_cannot_read_forward_nodes(self):
        host = await self.quiet_host()
        batch = host.receive(await self.capture(self.forward_event())).unwrap()
        app, context = host.application, self.context(host.application, batch)
        event = self.original(app, batch)
        role = SubjectRef(event.scope.installation_id, SubjectKind.BOT, "another-role")
        for other in (replace(context, actor=self.standard), replace(context, role_binding=replace(context.role_binding, role=role))):
            with self.assertRaises(TaskError):
                app.messages.forwards.read(other, event, self.clock.now())

    async def test_unknown_model_result_is_not_replayed(self):
        async def timeout(*_):
            raise TimeoutError("synthetic unknown forward reply")
        self.native.provider.client.state.behavior = timeout
        host = await self.setup_host()
        captured = await self.capture(self.forward_event())
        batch = host.receive(captured).unwrap()
        await self.done(host.application, batch)
        self.assertEqual(host.receive(captured).unwrap(), batch)
        await asyncio.sleep(0)
        self.assertEqual(len(self.native.provider.client.state.calls), 1)
        self.assertEqual(len([a for a, _ in self.api.calls if a == "send_private_msg"]), 0)

    async def test_frame_revocation_after_generation_blocks_send(self):
        self.forward_raw = {"messages": [node_raw(images=(animation_bytes(),))]}
        host = await self.setup_host()
        batch = host.receive(await self.capture(self.forward_event())).unwrap()
        app, event = host.application, self.original(host.application, batch)
        forward, node = event.forwards[0], event.forwards[0].nodes[0]
        parent = app.messages.forwards.image_event(event, forward, node)
        frame_parent = app.messages.images.frame_event(parent, node.images[0])
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
        self.assertEqual(len(self.native.provider.client.state.calls), 1)
        self.assertEqual(len([a for a, _ in self.api.calls if a == "send_private_msg"]), 0)

    async def test_tool_continuation_and_confirmed_memory_keep_node_and_frame_sources(self):
        self.forward_raw = {"messages": [node_raw(images=(animation_bytes(),))]}
        frames = []
        async def respond(payload, _):
            content = next(m["content"] for m in payload["messages"] if m["role"] == "user")
            frames.append(content[1:])
            if not any(m["role"] == "tool" for m in payload["messages"]):
                return completion(tools=[NS(id="forward-memo", type="function", function=NS(name=tool_alias("memo.list"), arguments='{}'))])
            evidence = json.loads(content[1]["text"])["image_evidence"]
            return completion(final_text("记录图片的首帧是红色。", evidence=evidence))
        self.native.provider.client.state.behavior = respond
        host = await self.setup_host()
        batch = host.receive(await self.capture(self.forward_event())).unwrap()
        app, context = host.application, self.context(host.application, batch)
        self.assertEqual((await self.done(app, batch))["state"], "complete")
        self.assertEqual(len(frames), 2)
        self.assertEqual(frames[0], frames[1])
        request = self.request(app, batch)
        owner = app.memory.writer.access.sent_sources
        reference = tuple(owner.rows(context))[0]
        owner.materialize(context, reference)
        links = app.runtime.read_all("SELECT input_id, input_lineage, input_revision FROM source_dependencies WHERE dependent_id = ?", (reference.source_id,))
        for material in request.context.materials:
            if material.source and material.owner.startswith("conversation."):
                self.assertIn(key(material.source), [tuple(row) for row in links])
        app.withdraw(context, self.original(app, batch).scoped_key).unwrap()
        with self.assertRaises(TaskError):
            owner.validate(context, reference)

    async def test_restart_restores_original_nodes_without_any_platform_read_or_decode(self):
        host = await self.quiet_host()
        captured = await self.capture(self.forward_event())
        batch = host.receive(captured).unwrap()
        before = self.request(host.application, batch)
        count = len(self.api.calls)
        await host.application.close()
        self.clock.advance(timedelta(seconds=0.2))
        new = self.host(runtime=self.open_runtime(), event_port=self.bridge.ports, identity_port=self.bridge.ports,
            role_port=self.bridge.ports, providers=(self.native_model.local_provider(),), sender=self.native_sender.local_sender(),
            runtime_identity=RuntimeIdentity("companion", "runtime-2", "astrbot", "host"))
        await self.start(new, 1, 1)
        with patch("astrbot_host.media.inspect_image", side_effect=AssertionError("never decode during recovery")):
            after = self.request(new.application, batch)
            self.assertEqual(after.images, before.images)
            self.assertEqual(after.context.materials, before.context.materials)
            self.assertEqual(new.receive(captured).unwrap(), batch)
        self.assertEqual(len(self.api.calls), count)

    async def test_node_and_text_merge_budgets_start_another_batch_without_dropping_records(self):
        host = await self.quiet_host()
        self.forward_raw = {"messages": [node_raw()] * 20}
        first = host.receive(await self.capture(self.forward_event())).unwrap()
        second = host.receive(await self.capture(self.forward_event("679"))).unwrap()
        self.assertNotEqual(first, second)
        self.assertEqual(len([m for m in self.request(host.application, first).context.materials if m.owner == NODE_OWNER]), 20)
        self.forward_raw = {"messages": [node_raw("x" * 33000)]}
        third = host.receive(await self.capture(self.forward_event("680"))).unwrap()
        fourth = host.receive(await self.capture(self.forward_event("681"))).unwrap()
        self.assertNotEqual(third, fourth)

    async def test_mixed_media_frame_budget_survives_restart_before_another_input(self):
        host = await self.quiet_host()
        self.forward_raw = {"messages": [node_raw(images=(animation_bytes(count=10),))]}
        batches = []
        for identifier in ("678", "679"):
            event = self.forward_event(identifier)
            event.message_obj.message = [FakeImage(), FakeForward(), FakeReply()]
            batches.append(host.receive(await self.capture(event)).unwrap())
        self.assertEqual(batches[0], batches[1])
        batch = batches[0]
        before = self.request(host.application, batch)
        self.assertEqual(len(before.images), 6)
        reads = len(self.api.calls)
        await host.application.close()
        self.clock.advance(timedelta(seconds=0.2))
        new = self.host(runtime=self.open_runtime(), event_port=self.bridge.ports, identity_port=self.bridge.ports,
            role_port=self.bridge.ports, providers=(self.native_model.local_provider(),), sender=self.native_sender.local_sender(),
            runtime_identity=RuntimeIdentity("companion", "runtime-mixed-media", "astrbot", "host"))
        await self.start(new, 1, 1)
        with patch("astrbot_host.media.inspect_image", side_effect=AssertionError("recovery may not decode again")):
            self.assertEqual(self.request(new.application, batch).images, before.images)
        self.assertEqual(len(self.api.calls), reads)
        third = new.receive(await self.capture(self.forward_event("680", with_text=False))).unwrap()
        self.assertEqual(third, batch)
        images = self.request(new.application, batch).images
        self.assertEqual(len(images), 7)
        self.assertEqual(sum(len(i.frames) if hasattr(i, "frames") else 1 for i in images), 16)
        # Eight originals would fit, but a seventeenth model view needs a new turn.
        fourth = new.receive(await self.capture(self.image_event("681"))).unwrap()
        self.assertNotEqual(fourth, batch)
        self.assertEqual(len(self.request(new.application, fourth).images), 1)

    async def test_displayed_message_id_cannot_reuse_a_real_user_source(self):
        host = await self.quiet_host()
        original = host.receive(await self.capture(self.image_event("345"))).unwrap()
        self.forward_raw = {"messages": [node_raw("自定义节点可伪造文字", sender="200")]}
        batch = host.receive(await self.capture(self.forward_event())).unwrap()
        app = host.application
        events = app.runtime.read_all("SELECT event_json FROM conversation_inputs WHERE batch_id = ? ORDER BY position", (batch,))
        forward_event = self.original(app, batch, position=len(events))
        root = forward_reference(forward_event.source, forward_event.forwards[0].position)
        node = node_reference(root, (0,))
        links = app.runtime.read_all("SELECT input_id, input_lineage, input_revision FROM source_dependencies WHERE dependent_id = ?", (node.source_id,))
        self.assertEqual([tuple(row) for row in links], [key(root)])
        self.assertNotEqual(node, self.original(app, original).source)


class ForwardIntakePolicyTests(unittest.TestCase):
    """New-input settings remain inside structural bounds; saved records stay readable."""

    def test_defaults_equal_the_structural_constants(self):
        policy = ForwardIntakePolicy()
        self.assertEqual((policy.max_forwards, policy.max_nodes, policy.max_depth),
                         (MAX_FORWARDS, 32, MAX_DEPTH))
        self.assertTrue(policy.parse_nested)
        self.assertEqual(policy.max_path_length, 2 * MAX_DEPTH - 1)

    def test_node_limit_can_keep_the_v1_range_and_nested_expansion_is_configurable(self):
        widened = ForwardIntakePolicy(max_nodes=300, parse_nested=False)
        self.assertEqual(widened.max_nodes, 300)
        self.assertFalse(widened.parse_nested)
        with self.assertRaises(TaskError):
            ForwardIntakePolicy(max_nodes=301)

    def test_operator_may_narrow_other_structural_ceilings(self):
        narrowed = ForwardIntakePolicy(max_forwards=1, max_nodes=4, max_depth=2)
        self.assertEqual(narrowed.max_path_length, 3)
        for kwargs, ceiling in (({"max_forwards": MAX_FORWARDS + 1}, MAX_FORWARDS),
                                ({"max_nodes": MAX_NODES + 1}, MAX_NODES),
                                ({"max_depth": MAX_DEPTH + 1}, MAX_DEPTH)):
            with self.subTest(**kwargs):
                with self.assertRaises(TaskError) as raised:
                    ForwardIntakePolicy(**kwargs)
                self.assertEqual(raised.exception.code, "forward_intake_invalid")

    def test_rejects_nonpositive_and_noninteger_values(self):
        for kwargs in ({"max_nodes": 0}, {"max_forwards": -1}, {"max_depth": 0},
                       {"max_nodes": True}, {"max_nodes": "8"}, {"max_nodes": 2.0}):
            with self.subTest(**kwargs):
                with self.assertRaises(TaskError):
                    ForwardIntakePolicy(**kwargs)

    def test_a_narrowed_node_ceiling_rejects_before_any_download(self):
        """The capture boundary refuses an oversized tree, and stored data is unaffected."""
        nodes = tuple(node_raw("第 %d 条" % index) for index in range(5))
        # Inside the structural ceiling: accepted with a policy that allows five.
        accepted = freeze_nodes(list(nodes), "0" * 64, intake=ForwardIntakePolicy(max_nodes=8))
        self.assertEqual(len(accepted), 5)
        with self.assertRaises(TaskError) as raised:
            freeze_nodes(list(nodes), "0" * 64, intake=ForwardIntakePolicy(max_nodes=4))
        self.assertEqual(raised.exception.code, "host_forward_limit")
        # The structural constants still bound stored descriptors, so a narrowed
        # operator setting can never invalidate an already persisted forward.
        descriptor = ForwardNodeDescriptor((0,), "0" * 64, "300", "记录中的昵称", 1700000000, "1" * 64, "2" * 64, 4)
        self.assertEqual(descriptor.path, (0,))

    def test_nested_expansion_can_be_disabled_without_losing_the_parent_node(self):
        tree = [node_raw("外层", nested=[node_raw("内部节点")])]
        nodes = freeze_nodes(deepcopy(tree), "0" * 64, intake=ForwardIntakePolicy(parse_nested=False))
        self.assertEqual(len(nodes), 1)
        self.assertIn("嵌套转发未展开", nodes[0].text)

    def test_a_narrowed_depth_ceiling_stops_nested_expansion(self):
        # `freeze_nodes` reads an expanded nested forward from `data["content"]` as a list.
        inner = [node_raw("更深一层")]
        tree = [node_raw("外层", nested=inner)]
        accepted = freeze_nodes(deepcopy(tree), "0" * 64, intake=ForwardIntakePolicy(max_depth=4))
        self.assertEqual(len(accepted), 2)
        with self.assertRaises(TaskError) as raised:
            freeze_nodes(deepcopy(tree), "0" * 64, intake=ForwardIntakePolicy(max_depth=1))
        self.assertEqual(raised.exception.code, "host_forward_limit")
