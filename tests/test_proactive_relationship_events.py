"""Explicit private statements may seed a purpose candidate; the contact model decides to skip or send."""

import json
import unittest
from datetime import timedelta

from companion_v2.agenda.models import AgendaTarget
from companion_v2.conversation.flow import ConversationPolicy
from companion_v2.execution.models import stamp
from companion_v2.foundation.refs import OperationRef, VersionRef
from companion_v2.storage.repositories import OperationRepository
from tests.contact_schedule_support import ContactScheduleFixture
from tests.test_role_tool_loop import answered


class RelationshipContactEventTests(ContactScheduleFixture, unittest.IsolatedAsyncioTestCase):
    def _relationship_settings(self, app, purpose):
        values = {"enabled": True, "automatic_enabled": True, "idle_seconds": 60,
                  "chat.enabled": False}
        values.update({name + ".enabled": name == purpose
                       for name in ("affection", "care", "play", "repair", "invitation")})
        return self.settings(app, values).unwrap()

    async def test_explicit_feeling_creates_care_contact_from_original_private_turn(self):
        host = await self.start(self.host(policy=ConversationPolicy(0, 0, 180, 0)))
        app = host.application
        self._relationship_settings(app, "care")
        batch = host.receive(self.event(text="我最近很累，今天有点难过。", prompt="原角色人格提示")).unwrap()
        await self.done(app, batch)

        self.advance(app, 60)
        row = app.runtime.read_one("""SELECT f.sequence, b.* FROM conversation_flows f
            JOIN conversation_batches b ON b.batch_id = f.batch_id WHERE f.route_key = ?""",
            (self.contact_context().scope.key,))
        plan = app.contact_schedule._plan(row, self.clock.now())
        self.assertIsNotNone(plan)
        await app.contact_schedule._run(plan)
        await self.automatic_delivery(app)

        contact = self.contacts(app)[0]
        record = json.loads(contact["record_json"])
        self.assertEqual(record["purpose"], "care")
        materials = self.contact_calls()[0].original.context.materials
        self.assertTrue(any(item.purpose == "recent_private_dialogue" and "很累" in item.payload
                            for item in materials))
        status = app.proactive.observe(self.context(app, batch), contact["task_id"]).unwrap()
        self.assertEqual(status["delivery"], "delivered")

    async def _assert_explicit_relationship_contact(self, text, purpose):
        host = await self.start(self.host(policy=ConversationPolicy(0, 0, 180, 0)))
        app = host.application
        app.contact_schedule.advance = lambda: None
        self._relationship_settings(app, purpose)
        batch = host.receive(self.event("relationship-" + purpose, text=text)).unwrap()
        await self.done(app, batch)
        self.advance(app, 60)
        row = app.runtime.read_one("""SELECT f.sequence, b.* FROM conversation_flows f
            JOIN conversation_batches b ON b.batch_id = f.batch_id WHERE f.route_key = ?""",
            (self.contact_context().scope.key,))
        plan = app.contact_schedule._plan(row, self.clock.now())
        self.assertIsNotNone(plan)
        self.assertEqual([intent.purpose for intent in plan[2]], [purpose])
        await app.contact_schedule._run(plan)
        await self.automatic_delivery(app)

        contact = self.contacts(app)[0]
        self.assertEqual(json.loads(contact["record_json"])["purpose"], purpose)
        dialogue = [item.payload for item in self.contact_calls()[-1].original.context.materials
                    if item.purpose == "recent_private_dialogue"]
        self.assertTrue(dialogue)
        self.assertTrue(any(text_part in " ".join(dialogue) for text_part in text.split()), dialogue)
        self.assertEqual(app.proactive.observe(self.context(app, batch), contact["task_id"])
                         .unwrap()["delivery"], "delivered")

    async def test_explicit_affection_reaches_the_contact_model_and_delivery(self):
        await self._assert_explicit_relationship_contact("我今天有点想你。", "affection")

    async def test_neutral_private_text_can_reach_enabled_relationship_prompt(self):
        host = await self.start(self.host(policy=ConversationPolicy(0, 0, 180, 0)))
        app = host.application
        app.contact_schedule.advance = lambda: None
        self.settings(app, {"enabled": True, "automatic_enabled": True, "idle_seconds": 60,
            "chat.enabled": False, "affection.enabled": False, "care.enabled": True,
            "play.enabled": False, "repair.enabled": False, "invitation.enabled": False}).unwrap()
        batch = host.receive(self.event("relationship-soft-candidate", text="准备睡觉咯")).unwrap()
        await self.done(app, batch)

        self.advance(app, 60)
        row = app.runtime.read_one("""SELECT f.sequence, b.* FROM conversation_flows f
            JOIN conversation_batches b ON b.batch_id = f.batch_id WHERE f.route_key = ?
            ORDER BY f.sequence DESC LIMIT 1""", (self.contact_context().scope.key,))
        plan = app.contact_schedule._plan(row, self.clock.now())

        self.assertIsNotNone(plan)
        self.assertEqual([intent.purpose for intent in plan[2]], ["care"])
        self.assertEqual(plan[4]["relationship_event"]["mode"], "slot")
        await app.contact_schedule._run(plan)
        await self.automatic_delivery(app)

        contact = self.contacts(app)[0]
        self.assertEqual(json.loads(contact["record_json"])["purpose"], "care")
        self.assertEqual(plan[2][0].thought.evidence.sources, ())
        dialogue = [item.payload for item in self.contact_calls()[-1].original.context.materials
                    if item.purpose == "recent_private_dialogue"]
        self.assertTrue(any("准备睡觉咯" in item for item in dialogue))

    async def test_relationship_contact_uses_a_later_slot_without_a_new_user_message(self):
        host = await self.start(self.host(policy=ConversationPolicy(0, 0, 180, 0)))
        app = host.application
        app.contact_schedule.advance = lambda: None
        self.settings(app, {"enabled": True, "automatic_enabled": True, "idle_seconds": 60,
            "chat.enabled": False, "affection.enabled": True, "care.enabled": False,
            "play.enabled": False, "repair.enabled": False, "invitation.enabled": False}).unwrap()
        batch = host.receive(self.event("old-private-turn", text="今天先这样吧")).unwrap()
        await self.done(app, batch)

        self.advance(app, 3660)
        row = app.runtime.read_one("""SELECT f.sequence, b.* FROM conversation_flows f
            JOIN conversation_batches b ON b.batch_id = f.batch_id WHERE f.route_key = ?
            ORDER BY f.sequence DESC LIMIT 1""", (self.contact_context().scope.key,))
        plan = app.contact_schedule._plan(row, self.clock.now())

        self.assertIsNotNone(plan)
        self.assertEqual([intent.purpose for intent in plan[2]], ["affection"])
        self.assertEqual(plan[2][0].thought.evidence.sources, ())
        self.assertEqual(plan[4]["relationship_event"]["mode"], "slot")
        await app.contact_schedule._run(plan)
        await self.automatic_delivery(app)

        contact = self.contacts(app)[0]
        self.assertEqual(json.loads(contact["record_json"])["purpose"], "affection")
        self.assertFalse(any(item.purpose == "contact_evidence"
                             for item in self.contact_calls()[-1].original.context.materials))

    async def test_relationship_and_chat_candidates_share_a_normal_slot_without_keyword_cues(self):
        host = await self.start(self.host(policy=ConversationPolicy(0, 0, 180, 0)))
        app = host.application
        app.contact_schedule.advance = lambda: None
        self.settings(app, {"enabled": True, "automatic_enabled": True, "idle_seconds": 60,
            "chat.enabled": True, "affection.enabled": False, "care.enabled": True,
            "play.enabled": False, "repair.enabled": False, "invitation.enabled": False}).unwrap()
        batch = host.receive(self.event("neutral-relationship-slot", text="准备睡觉咯")).unwrap()
        await self.done(app, batch)

        self.advance(app, 60)
        row = app.runtime.read_one("""SELECT f.sequence, b.* FROM conversation_flows f
            JOIN conversation_batches b ON b.batch_id = f.batch_id WHERE f.route_key = ?
            ORDER BY f.sequence DESC LIMIT 1""", (self.contact_context().scope.key,))
        plan = app.contact_schedule._plan(row, self.clock.now())

        self.assertIsNotNone(plan)
        # One slot is one opening: rotate to exactly one enabled purpose instead
        # of paying for chat + every enabled relationship purpose at once.
        self.assertEqual(len(plan[2]), 1)
        purpose = plan[2][0].purpose
        self.assertIn(purpose, {"chat", "care"})
        self.assertTrue(all(intent.thought.opportunity == plan[1] for intent in plan[2]))
        self.assertTrue(plan[1].opportunity_id.startswith("automatic-"))
        if purpose == "care":
            self.assertEqual(plan[4]["relationship_event"]["mode"], "slot")
            self.assertIn("关心", plan[2][0].thought.summary)
        else:
            self.assertNotIn("relationship_event", plan[4])
            self.assertIn("角色自己的想法", plan[2][0].thought.summary)
            self.assertIn("不要求依赖最近某条消息", plan[2][0].thought.summary)

    async def test_relationship_diagnostic_uses_real_neutral_turn_and_leaves_fit_to_prompt(self):
        host = await self.start(self.host(policy=ConversationPolicy(0, 0, 180, 0)))
        app = host.application
        self.settings(app, {"enabled": True, "automatic_enabled": True,
                            "idle_seconds": 60, "chat.enabled": False}).unwrap()
        batch = host.receive(self.event("relationship-diagnostic", text="准备睡觉咯")).unwrap()
        await self.done(app, batch)

        context = self.contact_context()
        at = self.clock.now()
        event = app.contact_schedule._origin(context, batch, at)[2]
        preferences = app.contact_schedule.contacts.preferences.read(context, observed_at=at).unwrap()
        producer = app.contact_schedule.relationship_events

        self.assertIsNotNone(producer.select(context, preferences, event, at, purpose="affection"))
        candidate = await app.contact_schedule.diagnostic_candidate(context, "affection", at=at)

        self.assertIsNotNone(candidate)
        self.assertEqual(candidate["route"]["id"], "relationship_event")
        self.assertEqual(candidate["intents"][0].purpose, "affection")
        self.assertEqual(candidate["intents"][0].thought.evidence.sources, (event.source,))

    async def test_explicit_play_reaches_the_contact_model_and_delivery(self):
        await self._assert_explicit_relationship_contact("今天一起玩游戏吧。", "play")

    async def test_explicit_repair_reaches_the_contact_model_and_delivery(self):
        await self._assert_explicit_relationship_contact("你刚才那句话让我不舒服。", "repair")

    async def test_explicit_invitation_reaches_the_contact_model_and_delivery(self):
        await self._assert_explicit_relationship_contact("下次一起去看电影吧。", "invitation")

    async def test_common_interest_is_not_forced_into_an_invitation(self):
        host = await self.start(self.host(policy=ConversationPolicy(0, 0, 180, 0)))
        app = host.application
        app.contact_schedule.advance = lambda: None
        self.settings(app, {"enabled": True, "automatic_enabled": True, "idle_seconds": 60,
            "chat.enabled": False, "affection.enabled": False, "care.enabled": False,
            "play.enabled": False, "repair.enabled": False, "invitation.enabled": True}).unwrap()

        async def skip(_envelope):
            return answered('{"decision":"skip","text":"","skip_reason":"purpose_mismatch"}')

        batch = host.receive(self.event("shared-interest", text="我喜欢看电影，最近看了三部。")).unwrap()
        await self.done(app, batch)
        self.sent.clear()
        self.model_behavior = skip
        self.advance(app, 60)
        row = app.runtime.read_one("""SELECT f.sequence, b.* FROM conversation_flows f
            JOIN conversation_batches b ON b.batch_id = f.batch_id WHERE f.route_key = ?
            ORDER BY f.sequence DESC LIMIT 1""", (self.contact_context().scope.key,))
        plan = app.contact_schedule._plan(row, self.clock.now())
        self.assertIsNotNone(plan)
        self.assertEqual([intent.purpose for intent in plan[2]], ["invitation"])
        await app.contact_schedule._run(plan)

        record = json.loads(self.contacts(app)[0]["record_json"])
        self.assertEqual((record["purpose"], record["decision"]), ("invitation", "skip"))
        self.assertEqual(self.sent, [])

    async def test_explicitly_rejected_invitation_can_be_skipped_without_a_delivery(self):
        host = await self.start(self.host(policy=ConversationPolicy(0, 0, 180, 0)))
        app = host.application
        app.contact_schedule.advance = lambda: None
        self._relationship_settings(app, "invitation")

        async def skip(_envelope):
            return answered('{"decision":"skip","text":"","skip_reason":"respect_user_boundary"}')

        batch = host.receive(self.event("declined-invitation", text="我不想和你一起去看电影，别约我。")).unwrap()
        await self.done(app, batch)
        self.sent.clear()
        self.model_behavior = skip
        self.advance(app, 60)
        row = app.runtime.read_one("""SELECT f.sequence, b.* FROM conversation_flows f
            JOIN conversation_batches b ON b.batch_id = f.batch_id WHERE f.route_key = ?
            ORDER BY f.sequence DESC LIMIT 1""", (self.contact_context().scope.key,))
        plan = app.contact_schedule._plan(row, self.clock.now())
        self.assertIsNotNone(plan)
        self.assertEqual([intent.purpose for intent in plan[2]], ["invitation"])
        result = await app.contact_schedule._run(plan)
        self.assertTrue(result.is_success)
        self.assertEqual(len(self.contacts(app)), 1, getattr(self, "contact_error", ""))
        record = json.loads(self.contacts(app)[0]["record_json"])
        self.assertEqual((record["purpose"], record["decision"]), ("invitation", "skip"))
        self.assertEqual(self.sent, [])


class FollowupContactEventTests(ContactScheduleFixture, unittest.IsolatedAsyncioTestCase):
    async def _start_with_delivered_contact(self):
        host = await self.start(self.host(policy=ConversationPolicy(0, 0, 180, 0)))
        app = host.application
        app.contact_schedule.advance = lambda: None
        self.settings(app, {"enabled": True, "automatic_enabled": True, "idle_seconds": 60,
            "chat.enabled": False, "contact_followup_after_minutes": 10,
            "contact_followup_cooldown_minutes": 30, "event_max_age_seconds": 60,
            "max_contacts_24h": 20}).unwrap()
        batch = host.receive(self.event("followup-origin", text="今天先读了几页小说。")).unwrap()
        await self.done(app, batch)
        context = self.context(app, batch)
        opportunity = self.opportunity("prior-care")
        source = self.source(app, "prior-care-source")
        task = (await self.contact(app, opportunity,
            (self.intent(opportunity, purpose="care", sources=(source,)),), context=context)).unwrap().task
        await self.delivery_state(app, task.task_id, "delivered")
        return host, app, context, batch

    def _latest_row(self, app, context):
        return app.runtime.read_one("""SELECT f.sequence, b.* FROM conversation_flows f
            JOIN conversation_batches b ON b.batch_id = f.batch_id WHERE f.route_key = ?
            ORDER BY f.sequence DESC LIMIT 1""",
            (context.scope.key,))

    async def test_followup_waits_then_composes_and_delivers_and_respects_cooldown(self):
        _host, app, context, batch = await self._start_with_delivered_contact()
        self.clock.advance(timedelta(minutes=9))
        self.assertIsNone(app.contact_schedule._plan(self._latest_row(app, context), self.clock.now()))

        self.clock.advance(timedelta(minutes=1))
        plan = app.contact_schedule._plan(self._latest_row(app, context), self.clock.now())
        self.assertIsNotNone(plan)
        self.assertIn("followup_event", plan[4])
        self.assertEqual([intent.purpose for intent in plan[2]], ["followup"])
        self.assertEqual(plan[1].expires_at, self.clock.now() + timedelta(minutes=5))
        await app.contact_schedule._run(plan)
        await self.automatic_delivery(app, count=2)
        followup = self.contacts(app)[-1]
        self.assertEqual(json.loads(followup["record_json"])["purpose"], "followup")
        self.assertEqual(app.proactive.observe(context, followup["task_id"]).unwrap()["delivery"], "delivered")

    async def test_followup_expires_with_its_slot_instead_of_the_material_freshness_window(self):
        _host, app, context, _batch = await self._start_with_delivered_contact()
        self.clock.advance(timedelta(minutes=10))
        first = app.contact_schedule._plan(self._latest_row(app, context), self.clock.now())
        self.assertIsNotNone(first)

        self.clock.advance(timedelta(minutes=6))
        self.assertIsNone(app.contact_schedule._plan(self._latest_row(app, context), self.clock.now()))


    async def test_recent_followup_receipt_blocks_a_second_followup_during_cooldown(self):
        _host, app, context, _batch = await self._start_with_delivered_contact()
        self.clock.advance(timedelta(minutes=10))
        operation_id = "followup-cooldown-marker-operation"
        operation = OperationRepository(app.runtime).reserve(OperationRef(
            operation_id, "proactive.contact", context.scope, VersionRef(operation_id, 1), operation_id),
            parameter_hash="followup-cooldown-marker", observed_at=self.clock.now()).unwrap()
        with app.runtime.transaction() as connection:
            connection.execute("""INSERT INTO proactive_contacts(contact_id, task_id, origin_key, record_json, body,
                payload_hash, operation_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                ("followup-cooldown-marker", "followup-cooldown-marker-task", context.key,
                 json.dumps({"purpose": "followup"}), "marker", "marker-hash",
                 operation.operation.operation_id, self.clock.now().isoformat()))

        self.clock.advance(timedelta(minutes=10))
        self.assertIsNone(app.contact_schedule._plan(self._latest_row(app, context), self.clock.now()))

    async def test_a_new_user_input_supersedes_an_unanswered_followup(self):
        host, app, context, _batch = await self._start_with_delivered_contact()
        self.clock.advance(timedelta(minutes=9))
        batch = host.receive(self.event("followup-new-input", text="我回来继续聊了。")).unwrap()
        await self.done(app, batch)
        self.clock.advance(timedelta(minutes=1))
        self.assertIsNone(app.contact_schedule._plan(self._latest_row(app, context), self.clock.now()))
        self.assertEqual(len(self.contacts(app)), 1)


class OpenLoopContactEventTests(ContactScheduleFixture, unittest.IsolatedAsyncioTestCase):
    def _latest_row(self, app, context):
        return app.runtime.read_one("""SELECT f.sequence, b.* FROM conversation_flows f
            JOIN conversation_batches b ON b.batch_id = f.batch_id WHERE f.route_key = ?
            ORDER BY f.sequence DESC LIMIT 1""", (context.scope.key,))

    async def test_open_loop_uses_its_agenda_expiry_not_material_freshness(self):
        host = await self.start(self.host(policy=ConversationPolicy(0, 0, 180, 0)))
        app = host.application
        app.contact_schedule.advance = lambda: None
        self.settings(app, {"enabled": True, "automatic_enabled": True, "idle_seconds": 60,
            "chat.enabled": False, "contact_followup_after_minutes": 10,
            "event_max_age_seconds": 60}).unwrap()
        for capability in ("memo.create", "memo.list", "memo.read"):
            self.grant(self.important, self.contact_context().scope, capability)
        context = self.contact_context()
        target = AgendaTarget(context.scope, context.role_binding.role, context.actor.subject)
        memo_id = "long-lived-open-loop"
        app.agenda.create_memo(context.actor, target=target, memo_id=memo_id, title="继续读那本书",
            body="之后聊聊读到哪里", purpose="open_loop", operation=self.operation(target.scope, owner="agenda"),
            expected_role_revision=context.role_binding.revision, observed_at=self.clock.now()).unwrap()
        batch = host.receive(self.event("open-loop-origin", text="今天先这样吧")).unwrap()
        await self.done(app, batch)

        self.clock.advance(timedelta(minutes=10))
        row = self._latest_row(app, context)
        plan = app.contact_schedule._plan(row, self.clock.now())
        memo = app.agenda.read_memo(context.actor, target=target, memo_id=memo_id,
            observed_at=self.clock.now()).unwrap()

        self.assertIsNotNone(plan)
        self.assertIn("open_loop_event", plan[4])
        self.assertEqual(plan[1].expires_at, memo.expires_at)

    async def test_delivered_open_loop_records_the_receipt_time_for_reply_review(self):
        host = await self.start(self.host(policy=ConversationPolicy(0, 0, 180, 0)))
        app = host.application
        app.contact_schedule.advance = lambda: None
        self.settings(app, {"enabled": True, "automatic_enabled": True, "idle_seconds": 60,
            "chat.enabled": False, "contact_followup_after_minutes": 10}).unwrap()
        for capability in ("memo.create", "memo.list", "memo.read"):
            self.grant(self.important, self.contact_context().scope, capability)
        context = self.contact_context()
        target = AgendaTarget(context.scope, context.role_binding.role, context.actor.subject)
        memo_id = "delivered-open-loop"
        app.agenda.create_memo(context.actor, target=target, memo_id=memo_id, title="继续读那本书",
            body="之后聊聊读到哪里", purpose="open_loop", operation=self.operation(target.scope, owner="agenda"),
            expected_role_revision=context.role_binding.revision, observed_at=self.clock.now()).unwrap()
        batch = host.receive(self.event("open-loop-delivery-origin", text="今天先这样吧")).unwrap()
        await self.done(app, batch)

        self.clock.advance(timedelta(minutes=10))
        plan = app.contact_schedule._plan(self._latest_row(app, context), self.clock.now())
        self.assertIsNotNone(plan)

        async def delayed_receipt(envelope):
            self.clock.advance(timedelta(seconds=7))
            return self.receipt(envelope)
        self.send_behavior = delayed_receipt
        await app.contact_schedule._run(plan)
        await self.automatic_delivery(app)

        contact = self.contacts(app)[0]
        delivery = app.proactive._delivery_row(contact["task_id"])
        memo = app.agenda.read_memo(context.actor, target=target, memo_id=memo_id,
            observed_at=self.clock.now()).unwrap()
        self.assertEqual(memo.followup_count, 1)
        self.assertIsNotNone(memo.last_followup_at)
        self.assertEqual(stamp(memo.last_followup_at), delivery["delivered_at"])

    async def test_skipped_open_loop_releases_followup_claim_for_later_revision(self):
        host = await self.start(self.host(policy=ConversationPolicy(0, 0, 180, 0)))
        app = host.application
        app.contact_schedule.advance = lambda: None
        self.settings(app, {"enabled": True, "automatic_enabled": True, "idle_seconds": 60,
            "chat.enabled": False, "contact_followup_after_minutes": 10}).unwrap()
        for capability in ("memo.create", "memo.list", "memo.read"):
            self.grant(self.important, self.contact_context().scope, capability)
        context = self.contact_context()
        target = AgendaTarget(context.scope, context.role_binding.role, context.actor.subject)
        memo_id = "skip-release-open-loop"
        app.agenda.create_memo(context.actor, target=target, memo_id=memo_id, title="周末去书店",
            body="看看那本画集", purpose="open_loop", operation=self.operation(target.scope, owner="agenda"),
            expected_role_revision=context.role_binding.revision, observed_at=self.clock.now()).unwrap()
        batch = host.receive(self.event("open-loop-skip-origin", text="先忙会儿")).unwrap()
        await self.done(app, batch)
        with app.runtime.transaction() as connection:
            connection.execute("DELETE FROM agenda_open_loop_activity WHERE memo_id = ?", (memo_id,))

        async def skip(_envelope):
            return answered('{"decision":"skip","text":"","skip_reason":"no_natural_opening"}')
        self.model_behavior = skip
        self.clock.advance(timedelta(minutes=10))
        plan = app.contact_schedule._plan(self._latest_row(app, context), self.clock.now())
        self.assertIsNotNone(plan)
        result = await app.contact_schedule._run(plan)
        self.assertTrue(result.is_success, getattr(result, "failure", None))
        memo = app.agenda.read_memo(context.actor, target=target, memo_id=memo_id,
            observed_at=self.clock.now()).unwrap()
        self.assertEqual(memo.followup_count, 0)
