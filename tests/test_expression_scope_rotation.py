"""Expression rules learned before a conversation rotation stay readable and counted."""

import json
import unittest
from dataclasses import replace

from astrbot_host.management_identity import dashboard_operator
from companion_v2.conversation.native_turns import NativeConversationTurns
from companion_v2.execution.models import TaskContext, TaskError, digest, stamp
from companion_v2.expression import rule_details
from companion_v2.expression.style_management import ExpressionStyleManagement
from companion_v2.foundation.refs import (AudienceRef, RoleOwnerRef, ScopeRef, SubjectKind,
                                          SubjectRef)
from companion_v2.identity_policy.models import RoleBinding
from tests import test_expression_v1_pipeline as pipeline
from tests.multi_host_support import MultiFixture, multi_config
from tests.test_expression_v1_pipeline import RULE, ExpressionFixture
from tests.test_host_application import HostFixture
from tests.test_management_access import SCOPE


class ExpressionScopeRotationTests(MultiFixture, unittest.IsolatedAsyncioTestCase):
    async def test_rules_from_the_previous_conversation_remain_readable(self):
        self.controller = self.make_controller(multi_config(delivery_mode="onebot_private"))
        app = await self.start_host()
        first, _ = await self.accepted(text="第一条")
        self.native.conversations["qq-main"] = "conversation-b"
        second, _ = await self.accepted(identifier="event-2", text="第二条")
        self.assertNotEqual(digest(first.scope), digest(second.scope))

        owner = RoleOwnerRef(second.scope.installation_id, second.scope.bot_id,
                             second.role_binding.role.subject_id)
        owner_key, old_scope = digest(owner), digest(first.scope)
        at = stamp(app.tasks.now())
        window = json.dumps({"opened_at": at, "closed_at": at, "handles": [], "characters": 0,
                             "source_kind": "shared_pack"})
        with app.runtime.transaction() as connection:
            connection.execute("INSERT INTO expression_style_runs VALUES (?,?,?,?,?,?,?,?,?)",
                               ("run-old", owner_key, old_scope, window, "hash", "旧记录", "decided", at, at))
            connection.execute("""INSERT INTO expression_style_signals
                (signal_id, run_id, owner_key, scope_key, dimension, observation, proposal, confidence,
                 evidence_json, state, created_at, decided_at, decided_by, decision_key, ordinal)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("sig-old", "run-old", owner_key, old_scope, "diction", "旧观察", "旧提案", 0.5,
                 "[]", "pending", at, None, None, None, 0))

        management = ExpressionStyleManagement(app)
        row = management._row("sig-old", owner, second)
        self.assertEqual(row["signal_id"], "sig-old")
        operator = dashboard_operator(app.runtime, "tester", at=app.tasks.now())
        result = management.list(operator=operator, context=second, state="pending")
        self.assertTrue(result.is_success, result.failure)
        self.assertIn("sig-old", [item["signal_id"] for item in result.unwrap()["items"]])


class ExpressionWindowIdentityTests(MultiFixture, unittest.IsolatedAsyncioTestCase):
    """The source check re-derives one window, not one identity snapshot.

    ``policy_revision`` is installation-wide and advances on every unrelated
    bind or grant, so a snapshot written before such a change must still count
    as the same conversation. Anything that actually changed the window — the
    participant, the conversation, or the role — must keep failing.
    """

    async def start_window(self):
        self.controller = self.make_controller(multi_config(delivery_mode="onebot_private"))
        app = await self.start_host()
        context, _ = await self.accepted(text="我不喝咖啡")
        return app, context

    @staticmethod
    def with_policy_revision(context, revision):
        return replace(context, actor=replace(context.actor, policy_revision=revision))

    async def test_the_window_key_ignores_only_the_policy_revision(self):
        _, context = await self.start_window()
        drifted = self.with_policy_revision(context, context.actor.policy_revision + 7)

        self.assertTrue(drifted.same_window(context))
        self.assertEqual(drifted.key, context.key)
        self.assertNotEqual(drifted, context)
        self.assertFalse(context.same_window(object()))

    async def test_a_policy_revision_change_does_not_move_the_window(self):
        app, context = await self.start_window()
        drifted = self.with_policy_revision(context, context.actor.policy_revision + 1)
        self.assertNotEqual(drifted, context)
        live_context, instruction, guard = app.state_maintenance.bindings[context.key]
        self.assertTrue(drifted.same_window(live_context))
        # The live binding still owns this window, so its guard is returned
        # instead of a false "the original role is no longer bound".
        self.assertIs(app.expression_style._bound(drifted, instruction), guard)

    async def test_a_new_participant_is_still_another_window(self):
        app, context = await self.start_window()
        scope = context.scope
        stranger_subject = SubjectRef(scope.installation_id, SubjectKind.HUMAN, "onebot-user:999")
        stranger = replace(context, actor=replace(context.actor, subject=stranger_subject),
                           audience=AudienceRef(scope, (stranger_subject,)))
        self.assertNotEqual(stranger.key, context.key)
        instruction = app.state_maintenance.bindings[context.key][1]

        with self.assertRaises(TaskError) as caught:
            app.expression_style._bound(stranger, instruction)
        self.assertEqual(caught.exception.code, "expression_binding_changed")

    async def test_a_rotated_conversation_is_still_another_window(self):
        app, context = await self.start_window()
        scope = replace(context.scope, conversation_id="conversation-rotated",
                        session_id="session-rotated")
        rotated = TaskContext(context.actor,
                              RoleBinding(scope, context.role_binding.role, context.role_binding.revision),
                              AudienceRef(scope, context.audience.recipients))
        self.assertNotEqual(rotated.key, context.key)
        instruction = app.state_maintenance.bindings[context.key][1]

        with self.assertRaises(TaskError) as caught:
            app.expression_style._bound(rotated, instruction)
        self.assertEqual(caught.exception.code, "expression_binding_changed")

    async def test_another_role_in_the_same_conversation_is_still_another_window(self):
        app, context = await self.start_window()
        role = context.role_binding.role
        other_role = SubjectRef(role.installation_id, role.kind, "role-someone-else")
        other = TaskContext(context.actor,
                            RoleBinding(context.scope, other_role, context.role_binding.revision),
                            context.audience)
        self.assertNotEqual(other.key, context.key)
        instruction = app.state_maintenance.bindings[context.key][1]

        with self.assertRaises(TaskError) as caught:
            app.expression_style._bound(other, instruction)
        self.assertEqual(caught.exception.code, "expression_binding_changed")

    async def test_a_changed_role_instruction_is_still_another_binding(self):
        app, context = await self.start_window()
        instruction = app.state_maintenance.bindings[context.key][1]

        with self.assertRaises(TaskError) as caught:
            app.expression_style._bound(context, instruction + "\n这是另一个角色设定。")
        self.assertEqual(caught.exception.code, "expression_binding_changed")

    async def test_a_stored_native_turn_kept_an_older_policy_revision(self):
        app, context = await self.start_window()
        instruction = app.state_maintenance.bindings[context.key][1]
        NativeConversationTurns(app.runtime).mark(
            {"context": context, "turn_id": "turn-a", "instruction": instruction}, "completed",
            at=app.tasks.now())
        app.state_maintenance.bindings.pop(context.key)
        drifted = self.with_policy_revision(context, context.actor.policy_revision + 1)

        self.assertIsNone(app.expression_style._bound(drifted, instruction))

    async def test_a_stored_private_batch_kept_an_older_policy_revision(self):
        app, context = await self.start_window()
        instruction = app.state_maintenance.bindings[context.key][1]
        app.state_maintenance.bindings.pop(context.key)
        drifted = self.with_policy_revision(context, context.actor.policy_revision + 1)

        self.assertIsNone(app.expression_style._bound(drifted, instruction))


class ExpressionLearningFamilyQuotaTests(ExpressionFixture, HostFixture, unittest.IsolatedAsyncioTestCase):
    """Learning counts capacity and priors by the scope family, not by one window.

    AstrBot rotates ``conversation_id``/``session_id`` often, and the review page,
    the capacity gate and injection already read one participant's rules across
    those rotations (``conversation.scope_family``). Learning has to count the same
    set: a rotated window must start "full" when its family is full, must see what
    the family already learned instead of learning it again, and must never pull in
    another participant's or another role's rules.
    """

    def setUp(self):
        super().setUp()
        self.expression_profiles()
        self.model_behavior = self.expression_model

    @staticmethod
    def rotated(scope, conversation):
        """The same participant's window after AstrBot rotated its conversation."""
        return replace(scope, conversation_id=conversation, session_id="session-" + conversation)

    async def open_window(self, host, conversation, *, count=6):
        """Deliver ``count`` learnable messages in one private conversation window."""
        app = host.application
        scope = SCOPE if conversation is None else self.rotated(SCOPE, conversation)
        if conversation is not None:
            # The fixture grants one scope at setUp; a rotated window authenticates
            # the same participant on its own route, exactly as the host would.
            self.grant(self.important, scope, "text")
        batch = None
        for index in range(count):
            batch = host.receive(self.event(f"{conversation or 'window-a'}-{index}",
                text="嗯哼，那就这么定啦。" + str(index), scope=scope)).unwrap()
        self.advance(app)
        await self.done(app, batch)
        return self.context(app, batch)

    @staticmethod
    def seed_rules(app, context, *, count, state="adopted", proposal="上一窗口已学的规则",
                   observation="上一窗口的观察"):
        """Rules a previous window of the same participant already learned."""
        owner_key = digest(app.expression_style.samples.role_owner(context))
        scope_key = digest(context.scope)
        at = stamp(app.tasks.now())
        window = json.dumps({"opened_at": at, "closed_at": at, "handles": [],
                             "characters": 0, "source_kind": "conversation_input"})
        rows = []
        with app.runtime.transaction() as connection:
            connection.execute("INSERT INTO expression_style_runs VALUES (?,?,?,?,?,?,?,?,?)",
                ("run-family", owner_key, scope_key, window, "family-hash", "上一窗口已学", "decided", at, at))
            for index in range(count):
                signal_id = "signal-family-" + str(index)
                rows.append(signal_id)
                connection.execute("""INSERT INTO expression_style_signals
                    (signal_id, run_id, owner_key, scope_key, dimension, observation, proposal,
                     confidence, evidence_json, state, created_at, decided_at, decided_by,
                     decision_key, ordinal)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (signal_id, "run-family", owner_key, scope_key, "diction", observation,
                     proposal + str(index), 0.6, "[]", state, at, at, "dashboard:test",
                     "family:" + signal_id, index))
        return rows

    @staticmethod
    def learning_material(called):
        """The learning batch's material, exactly as the model receives it."""
        envelope = next(item for item in reversed(called)
                        if item.original.output_format == "role_expression_learning")
        return json.loads(next(item.payload for item in envelope.original.context.materials
                               if item.purpose == "role_expression_samples"))

    async def test_a_rotated_private_window_counts_the_family_budget(self):
        host = await self.start()
        app = host.application
        first = await pipeline.ExpressionReplyPipelineTests.seed(self, host)
        self.configure_expression(app, {"private_learning_enabled": True, "active_signal_limit": 12})
        second = await self.open_window(host, "conversation-b")
        self.assertNotEqual(digest(first.scope), digest(second.scope))
        service = app.expression_style
        limit = service._policy(second, app.tasks.now()).active_signal_limit
        self.assertEqual(limit, 12)
        self.seed_rules(app, first, count=limit)

        self.assertEqual(service._active_signal_count(first), limit)
        self.assertEqual(service._active_signal_count(second), limit,
                         "a rotated window must count the rules its family already holds")
        service.next_checks.clear()
        self.assertFalse(service._scan(second, "", app.tasks.now()))
        self.assertEqual(service.results[service._work_key(second)]["state"], "capacity_reached",
                         "a full family budget must gate the rotated window too")

    async def test_a_rotated_private_window_reads_the_family_priors(self):
        host = await self.start()
        app = host.application
        first = await pipeline.ExpressionReplyPipelineTests.seed(self, host)
        self.configure_expression(app, {"private_learning_enabled": True})
        (row,) = self.seed_rules(app, first, count=1)
        second = await self.open_window(host, "conversation-b")

        instruction = app.state_maintenance.bindings[second.key][1]
        job = app.expression_style.prepare(second, instruction, app.tasks.now())
        self.assertIsNotNone(job, "the rotated window must still prepare a batch")
        self.assertIn(row, [item["signal_id"] for item in job["existing_rules"]],
                      "deduplication priors come from the family, not only this window")

        await self.learn_now(app, second)
        material = self.learning_material(self.called)
        self.assertIn(row, [item["signal_id"] for item in material["existing_rules"]],
                      "the model must see the rules its owner already has")

    async def test_a_rotated_private_window_does_not_relearn_a_family_rule(self):
        host = await self.start()
        app = host.application
        first = await pipeline.ExpressionReplyPipelineTests.seed(self, host)
        self.configure_expression(app, {"private_learning_enabled": True})
        (row,) = self.seed_rules(app, first, count=1, state="rejected",
                                 observation=RULE["observation"], proposal=RULE["proposal"])
        rule_details.write(app.runtime, row, RULE["expression"], at=app.tasks.now(),
                           actor="learning_model", operation_key="run-family")
        second = await self.open_window(host, "conversation-b")

        await self.learn_now(app, second)

        page = ExpressionStyleManagement(app).list(operator=self.core, context=second, state="pending")
        self.assertTrue(page.is_success, page.failure)
        self.assertEqual(page.unwrap()["items"], [],
                         "the family already decided this exact form; it must not be learned twice")

    async def test_a_rotated_private_window_page_counts_the_family(self):
        host = await self.start()
        app = host.application
        first = await pipeline.ExpressionReplyPipelineTests.seed(self, host)
        self.configure_expression(app, {"private_learning_enabled": True})
        second = await self.open_window(host, "conversation-b")
        self.seed_rules(app, first, count=3)

        # ``status()`` is what the page reads: it must report the family's active
        # count, not the rotated window's own (empty) one.
        self.assertEqual(app.expression_style.status(second)["active"], 3,
                         "the page reads the family's active count, not this window's")

    async def test_another_participant_or_role_is_not_in_the_family(self):
        host = await self.start()
        app = host.application
        first = await pipeline.ExpressionReplyPipelineTests.seed(self, host)
        self.configure_expression(app, {"private_learning_enabled": True})
        self.seed_rules(app, first, count=3)
        service = app.expression_style
        self.assertEqual(service._active_signal_count(first), 3)

        stranger_scope = replace(first.scope, native_scope_id="participant-9999",
                                 conversation_id="conversation-stranger",
                                 session_id="session-stranger")
        stranger_subject = SubjectRef(first.scope.installation_id, SubjectKind.HUMAN,
                                      "onebot-user:9999")
        stranger = TaskContext(replace(first.actor, subject=stranger_subject),
            RoleBinding(stranger_scope, first.role_binding.role, first.role_binding.revision),
            AudienceRef(stranger_scope, (stranger_subject,)))
        self.assertEqual(service._active_signal_count(stranger), 0,
                         "one participant's rules must never count for another's window")

        other_role = TaskContext(first.actor,
            RoleBinding(first.scope, SubjectRef(first.scope.installation_id, SubjectKind.BOT,
                                                "role-someone-else"), first.role_binding.revision),
            first.audience)
        self.assertEqual(service._active_signal_count(other_role), 0,
                         "the same window under another role owns none of these rules")


if __name__ == "__main__":
    unittest.main()
