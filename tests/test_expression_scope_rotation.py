"""Expression rules learned before a conversation rotation stay reviewable."""

import json
import unittest
from dataclasses import replace

from astrbot_host.management_identity import dashboard_operator
from companion_v2.conversation.native_turns import NativeConversationTurns
from companion_v2.execution.models import TaskContext, TaskError, digest, stamp
from companion_v2.expression.style_management import ExpressionStyleManagement
from companion_v2.foundation.refs import (AudienceRef, RoleOwnerRef, ScopeRef, SubjectKind,
                                          SubjectRef)
from companion_v2.identity_policy.models import RoleBinding
from tests.multi_host_support import MultiFixture, multi_config


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


if __name__ == "__main__":
    unittest.main()
