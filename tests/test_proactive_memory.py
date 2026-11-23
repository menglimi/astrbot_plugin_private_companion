"""Proactive contacts must read the same memory the reply path reads.

The reply path seals a memory snapshot per batch; a proactive message has no
batch, so these tests pin the batch-free entry point, the optional wiring, the
fail-open behaviour and the inherited budgets on real synthetic storage.
"""

import json
from datetime import timedelta
import unittest
from unittest.mock import patch

from companion_v2.conversation.models import InboundConversationEvent
from companion_v2.execution.models import digest
from companion_v2.foundation.refs import SourceRef, VersionRef
from companion_v2.memory import codec
from companion_v2.memory.management import MemoryManagement
from companion_v2.memory.models import Proposal
from companion_v2.proactive.service import ProactiveContacts
from companion_v2.sources.models import SourceRecord
from tests.proactive_support import ContactFixture
from tests.test_management_access import NOW, SCOPE
from tests.test_role_tool_loop import answered


class ProactiveMemoryTests(ContactFixture, unittest.IsolatedAsyncioTestCase):
    async def enabled_with_memory(self):
        app = await self.enabled()
        self.assertIsNotNone(app.proactive.memory_context)
        return app

    def input_event(self, app, identifier, text):
        """One authentic private input whose source body is the given text."""
        identifier_hash = digest([SCOPE.key, identifier])
        reference = SourceRef("source-" + identifier_hash, "conversation.host_input", SCOPE,
                              VersionRef(identifier_hash, 1), NOW)
        app.messages.sources.publish(SourceRecord(reference, self.contact_context().audience,
            digest(text), text), observed_at=NOW).unwrap()
        return InboundConversationEvent(identifier, self.important.principal, SCOPE, reference,
                                        "text", digest(text), NOW)

    def accepted_input(self, app, identifier, text):
        """Admit one input batch without driving a reply for it.

        The contact path only needs the recorded input, its source and its
        owner; a sealed reply would add unrelated model material to this test.
        """
        return app.messages.accept(self.contact_context(), self.input_event(app, identifier, text),
                                   role_instruction="原角色人格提示").unwrap()

    def source_of(self, app, batch):
        row = app.runtime.read_one(
            "SELECT event_json FROM conversation_inputs WHERE batch_id = ? ORDER BY event_key LIMIT 1", (batch,))
        return codec.source(json.loads(row["event_json"])["source"])

    def remember(self, app, batch, *, predicate, representation, key):
        """Stage one fact through the same governed writer a reply turn uses."""
        context = self.context(app, batch)
        evidence = app.memory.writer.access.capture(context, self.source_of(app, batch))
        return app.memory.writer.submit(context, Proposal(
            "add", predicate=predicate, object="进行中", representation=representation,
            evidence=(evidence,), single_value=True,
        ), operation_key=key).unwrap()

    async def seed_diet_memory(self, app):
        """One accepted private input becomes the evidence for a stored fact.

        The source body itself mentions the topic, so the contact's own recall
        intent shares its terms with the fact under test.
        """
        batch = self.accepted_input(app, "diet", "用户说最近在减肥，晚饭只吃沙拉。")
        saved = self.remember(app, batch, predicate="近期饮食",
                              representation="用户最近在减肥，晚饭只吃沙拉。", key="diet-memory")
        self.assertEqual(saved["state"], "persisted")
        return batch, saved

    def memory_materials(self, envelope):
        return tuple(item for item in envelope.original.context.materials if item.owner == "memory")

    def contact_envelope(self):
        return [item for item in self.called if item.original.output_format == "proactive_contact"][-1]

    def budget(self, app, context, **changes):
        """Narrow the relationship's own memory budget through its real owner."""
        manager = MemoryManagement(app.memory, self.core)
        settings = manager.settings(context).unwrap()
        manager.patch_settings(context, operation_key="proactive-memory-budget",
            expected_edit_token=settings["edit_token"],
            changes={"memory." + key: value for key, value in changes.items()}).unwrap()

    async def test_wired_memory_reaches_the_proactive_request(self):
        app = await self.enabled_with_memory()
        _, saved = await self.seed_diet_memory(app)
        self.assertEqual((await self.contact(app)).unwrap().task.state, "succeeded")
        materials = self.memory_materials(self.contact_envelope())
        self.assertTrue(materials, "a stored fact must reach the proactive request")
        facts = [json.loads(item.payload) for item in materials
                 if item.purpose in ("current_memory", "pinned_memory")]
        self.assertIn(saved["atom_id"], [item["atom_id"] for item in facts])
        self.assertTrue(any("减肥" in item.get("representation", "") for item in facts))

    async def test_wired_memory_adds_its_reading_rule_to_the_instruction(self):
        app = await self.enabled_with_memory()
        await self.seed_diet_memory(app)
        self.assertEqual((await self.contact(app)).unwrap().task.state, "succeeded")
        self.assertIn("owner 为 memory 的材料", self.contact_envelope().original.user_instruction)
        self.assertNotIn("owner 为 memory 的材料", self.contact_envelope().original.instruction)

    async def test_absent_memory_leaves_proactive_generation_unchanged(self):
        """A host that never wires memory keeps today's materials and prompt."""
        with patch.object(ProactiveContacts, "attach_memory", lambda self, memory: None):
            app = await self.enabled()
        self.assertIsNone(app.proactive.memory_context)
        # The reply path is a different owner and keeps its own memory wiring.
        self.assertIsNotNone(app.messages.memory_context)
        self.assertEqual((await self.contact(app)).unwrap().task.state, "succeeded")
        request = self.contact_envelope().original
        self.assertEqual(self.memory_materials(self.contact_envelope()), ())
        self.assertNotIn("owner 为 memory 的材料", request.instruction)
        self.assertNotIn("owner 为 memory 的材料", request.user_instruction)
        # The pre-existing contact materials keep their own order and purposes.
        self.assertEqual([item.purpose for item in request.context.materials],
                         ["contact_intent", "communication_state"])

    async def test_failing_memory_never_withholds_the_proactive_message(self):
        app = await self.enabled_with_memory()

        class FailingContext:
            def contact_capture(self, *args, **kwargs):
                raise RuntimeError("memory storage is unavailable")

            def contact_contributions(self, *args, **kwargs):
                raise AssertionError("contributions must not run after a failed capture")

        app.proactive.memory_context = FailingContext()
        result = await self.contact(app)
        task = result.unwrap().task
        self.assertEqual(task.state, "succeeded")
        await self.delivery_state(app, task.task_id, "delivered")
        self.assertEqual(self.sent[0].text, "想起你上次提到的书，读到哪里了？")
        self.assertEqual(self.memory_materials(self.contact_envelope()), ())

    async def test_memory_contribution_respects_the_configured_budget(self):
        app = await self.enabled_with_memory()
        batch, _ = await self.seed_diet_memory(app)
        context = self.context(app, batch)
        # Four more recalled facts, all sharing the contact's recall terms.
        for number in range(4):
            self.remember(app, batch, predicate="减肥阶段" + str(number),
                          representation="用户减肥第 " + str(number) + " 阶段的记录。",
                          key="diet-memory-" + str(number))
        self.budget(app, context, context_items=1, context_chars=2048)
        self.assertEqual((await self.contact(app)).unwrap().task.state, "succeeded")
        materials = self.memory_materials(self.contact_envelope())
        recall = [item for item in materials if item.purpose in ("current_memory", "pinned_memory")]
        # Five matching facts exist and all of them fit inside context_chars, so
        # only the configured item count can explain a single recalled fact.
        self.assertEqual(len(recall), 1)
        self.assertLessEqual(sum(len(item.payload) for item in recall), 2048)


class ContactSnapshotTests(ContactFixture, unittest.IsolatedAsyncioTestCase):
    """The batch-free snapshot keeps the batch path's identity and budget rules."""

    async def live(self):
        app = await self.enabled()
        self.assertIsNotNone(app.proactive.memory_context)
        return app

    def input_event(self, app, identifier, text):
        identifier_hash = digest([SCOPE.key, identifier])
        reference = SourceRef("source-" + identifier_hash, "conversation.host_input", SCOPE,
                              VersionRef(identifier_hash, 1), NOW)
        app.messages.sources.publish(SourceRecord(reference, self.contact_context().audience,
            digest(text), text), observed_at=NOW).unwrap()
        return InboundConversationEvent(identifier, self.important.principal, SCOPE, reference,
                                        "text", digest(text), NOW)

    def source_of(self, app, batch):
        row = app.runtime.read_one(
            "SELECT event_json FROM conversation_inputs WHERE batch_id = ? ORDER BY event_key LIMIT 1", (batch,))
        return codec.source(json.loads(row["event_json"])["source"])

    async def seeded(self):
        app = await self.live()
        batch = app.messages.accept(self.contact_context(), self.input_event(app, "diet", "用户说最近在减肥。"),
                                    role_instruction="原角色人格提示").unwrap()
        context = self.context(app, batch)
        evidence = app.memory.writer.access.capture(context, self.source_of(app, batch))
        saved = app.memory.writer.submit(context, Proposal(
            "add", predicate="近期饮食", object="进行中", representation="用户最近在减肥。",
            evidence=(evidence,), single_value=True,
        ), operation_key="contact-snapshot").unwrap()
        return app, context, batch, saved

    async def test_contact_capture_uses_its_own_session_and_no_input_handles(self):
        app, context, batch, saved = await self.seeded()
        snapshot = app.memory.context.contact_capture(
            context, "memory-contact-fixture", (self.source_of(app, batch),), self.clock.now() + timedelta(minutes=5))
        self.assertEqual((snapshot["state"], snapshot["session_id"]), ("ready", "memory-contact-fixture"))
        self.assertEqual([item["atom_id"] for item in snapshot["references"]], [saved["atom_id"]])
        self.assertTrue(snapshot["handles"], "the snapshot still seals its original source handles")
        purposes = [item.purpose for item in app.memory.context.contact_contributions(snapshot, context)]
        # A proactive generation is not a turn and has no memory tools, so the
        # reply path's queryable input handles stay out of it.
        self.assertNotIn("current_input_handles", purposes)

    async def test_contact_capture_rejects_a_key_outside_its_own_namespace(self):
        app, context, _, _ = await self.seeded()
        for key in ("", "fixture-batch", "memory:" + "x" * 600):
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    app.memory.context.contact_capture(context, key, (), self.clock.now() + timedelta(minutes=5))


if __name__ == "__main__":
    unittest.main()
