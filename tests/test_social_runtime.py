"""End-to-end invariants for the role-owned social runtime."""

from __future__ import annotations

import json
import unittest

from companion_v2.conversation.messages import source_from_document
from companion_v2.execution.models import TaskError
from companion_v2.foundation.refs import RoleOwnerRef
from companion_v2.role_work import RoleActivity, RoleActivityState
from companion_v2.role_work.journal import JournalContext, JournalDraft, JournalKey, JournalKind
from tests.test_host_application import HostFixture
from tests.test_management_access import NOW, ROLE, SCOPE


class SocialRuntimeTests(HostFixture, unittest.IsolatedAsyncioTestCase):
    async def test_attention_receipt_and_delivery_clear_the_same_thread(self):
        host = await self.start()
        app = host.application
        batch = host.receive(self.event(text="明天有考试，可以帮我记住吗？")).unwrap()
        context = self.context(app, batch)
        thread = app.social.thread_for(context)
        self.assertEqual(thread["owed_reply"], 1)
        self.assertEqual(app.social.proactive_hold(context, self.clock.now())[0], "social_reply_owed")
        self.advance(app)
        self.assertEqual((await self.done(app, batch))["state"], "complete")
        self.assertEqual(app.social.thread_for(context)["owed_reply"], 0)
        self.assertIsNone(app.social.proactive_hold(context, self.clock.now()))

        quiet = host.receive(self.event("quiet", text="嗯")).unwrap()
        self.assertEqual(app.flow.row(quiet)["state"], "closed", {
            "thread": app.social.thread_for(context), "receipts": [dict(row) for row in app.runtime.read_all(
                "SELECT action, score, reason FROM social_attention_receipts", ())]})
        self.assertEqual(len(self.sent), 2)
        receipt = app.runtime.read_one("""SELECT action FROM social_attention_receipts
            ORDER BY recorded_at DESC, rowid DESC LIMIT 1""", ())
        self.assertEqual(receipt["action"], "observe")
        self.assertEqual(app.social.thread_for(context)["owed_reply"], 0)
        self.assertTrue(any(item.get("attention", {}).get("action") == "observe" for item in
            app.social.timeline(context, thread["thread_id"])["items"]))

    async def test_timeline_pages_and_withdrawal_hides_source_body(self):
        host = await self.start()
        app = host.application
        batch = host.receive(self.event(text="我明天在图书馆考试，能帮我吗？")).unwrap()
        context = self.context(app, batch)
        thread_id = app.social.thread_for(context)["thread_id"]
        self.advance(app)
        await self.done(app, batch)
        page = app.social.timeline(context, thread_id, limit=1)
        self.assertTrue(page["next_cursor"])
        second = app.social.timeline(context, thread_id, cursor=page["next_cursor"], limit=1)
        self.assertNotEqual(page["items"][0]["event_id"], second["items"][0]["event_id"])
        events, cursor = [], None
        while True:
            current = app.social.timeline(context, thread_id, cursor=cursor, limit=1)
            events.extend(current["items"])
            cursor = current["next_cursor"]
            if cursor is None:
                break
        inbound = next(item for item in events if item["kind"] == "inbound")
        self.assertIn("图书馆", inbound["text"])
        event_key = app.runtime.read_one("SELECT event_key FROM conversation_inputs WHERE batch_id = ?", (batch,))[0]
        app.withdraw(context, event_key).unwrap()
        hidden = app.social.timeline(context, thread_id)
        self.assertFalse(any("图书馆" in item.get("text", "") for item in hidden["items"]))
        with self.assertRaises(ValueError):
            app.social.timeline(context, thread_id, cursor="not-json")

    async def test_role_life_requires_explicit_public_selection_and_current_revision(self):
        host = await self.start()
        app = host.application
        activity = app.role_life.add_activity(RoleActivity("social-reading", ROLE.subject_id, SCOPE, NOW,
            RoleActivityState.ACTIVE, description="在书桌读完一章"))
        batch = host.receive(self.event(text="你今天在忙什么？")).unwrap()
        context = self.context(app, batch)
        thread = app.social.thread_for(context)
        source = app.runtime.read_one("SELECT * FROM social_semantic_refs WHERE origin_kind = 'life'", ())
        self.assertEqual(app.social._project(source, context)["text"], "在书桌读完一章")
        page = app.social.timeline(context, thread["thread_id"])
        life = next((item for item in page["items"] if item["kind"] == "life"), None)
        self.assertIsNotNone(life, {
            "events": page["items"], "refs": [dict(row) for row in app.runtime.read_all(
                "SELECT origin_kind, origin_id, origin_thread, subject_id FROM social_semantic_refs", ())]})
        self.assertEqual(life["text"], "在书桌读完一章")
        self.assertEqual(app.social.broadcast(context), [])
        with self.assertRaises(TaskError):
            app.social.publish_life(context, activity.activity_id, expected_revision=2)
        published = app.social.publish_life(context, activity.activity_id, expected_revision=1)
        self.assertEqual(app.social.broadcast(context)[0]["semantic_id"], published["semantic_id"])
        app.role_life.transition_activity(activity.activity_id, owner=activity.owner, expected_revision=1,
            state=RoleActivityState.PAUSED, operation_id="social-pause")
        self.assertEqual(app.social.broadcast(context), [])
        with self.assertRaises(TaskError):
            app.social.broadcast_reference(context, published["semantic_id"])

    async def test_journal_episode_returns_to_its_private_thread_only(self):
        host = await self.start()
        app = host.application
        event = self.event(text="今天在图书馆读书")
        batch = host.receive(event).unwrap()
        context = self.context(app, batch)
        owner = RoleOwnerRef(context.scope.installation_id, context.scope.bot_id,
                             context.role_binding.role.subject_id)
        key = JournalKey(owner, JournalKind.DIARY, "2026-09-21")
        saved = json.loads(app.runtime.read_one("SELECT event_json FROM conversation_inputs WHERE batch_id = ?",
                                                (batch,))["event_json"])
        draft = JournalDraft(JournalContext(key, context.scope, "Asia/Shanghai"),
                             body="今天在图书馆读完一章。", summary="在图书馆读书",
                             sources=(source_from_document(saved["source"]),))
        app.role_journal.save(draft, expected_revision=0, operation_id="social-diary")
        thread = app.social.thread_for(context)
        timeline = app.social.timeline(context, thread["thread_id"])
        self.assertTrue(any(item.get("kind") == "journal" and item.get("text") == "在图书馆读书"
                            for item in timeline["items"]))
        self.assertEqual(app.social.broadcast(context), [])

    async def test_due_rank_uses_wait_and_salience_without_changing_per_route_order(self):
        host = await self.start()
        app = host.application
        first = host.receive(self.event(text="路过")).unwrap()
        second = host.receive(self.event("important", text="我生病了，能帮我吗？", prompt="另一段角色提示")).unwrap()
        context = self.context(app, first)
        ranked = app.social.order_due_batches((first, second), self.clock.now())
        self.assertEqual(ranked[0], second)
        self.assertEqual(app.social.thread_for(context)["owed_reply"], 2)

    async def test_withdrawn_question_no_longer_owes_a_reply(self):
        host = await self.start()
        app = host.application
        batch = host.receive(self.event(text="明天考试，能帮我吗？")).unwrap()
        context = self.context(app, batch)
        self.assertEqual(app.social.thread_for(context)["owed_reply"], 1)
        event_key = app.runtime.read_one("SELECT event_key FROM conversation_inputs WHERE batch_id = ?", (batch,))[0]
        app.withdraw(context, event_key).unwrap()
        self.assertEqual(app.social.thread_for(context)["owed_reply"], 0)
        self.assertIsNone(app.social.proactive_hold(context, self.clock.now()))
        self.assertEqual(app.social.timeline(context, app.social.thread_for(context)["thread_id"])["unresolved"], [])


if __name__ == "__main__":
    unittest.main()
