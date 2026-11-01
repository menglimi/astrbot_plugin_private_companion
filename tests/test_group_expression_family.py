"""Old V1 group rules belong to their group, not to one conversation window.

The V1 group import wrote adopted rules under a synthetic group identity scope
(``conversation_id = session_id = group id``). A group therefore owns every
window of itself and that identity scope, while another group, another Bot
account or another role owns none of them.

Schema note: ``write_rule`` and ``write_rules`` insert directly into
``expression_style_runs`` (run_id, owner_key, scope_key, sample_json,
material_hash, summary, state, created_at, decided_at) and
``expression_style_signals`` (signal_id, run_id, owner_key, scope_key,
dimension, observation, proposal, applicability_json, confidence,
evidence_json, state, created_at, decided_at, decided_by, decision_key,
ordinal). Keep both column lists in step with
``companion_v2/storage/expression_style_schema.py``.
"""

import json
import unittest
from datetime import timedelta

from astrbot_host.management_identity import dashboard_operator
from companion_v2.conversation.scope_family import group_identity_scope
from companion_v2.conversation.style_context import MAX_POOL_SCAN
from companion_v2.execution.models import TaskContext, digest, encode
from companion_v2.expression import style_rotation
from companion_v2.expression.style_management import ExpressionStyleManagement
from companion_v2.foundation.refs import RoleOwnerRef, SubjectKind, SubjectRef
from tests.group_host_support import GroupFixture
from tests.staging_bootstrap_support import wait_for_batch


PROPOSAL = "用群里说惯的短句收尾"
EVIDENCE = "就像平常在群里那样，一句话说完。"
GROUP_APPLICABILITY = {"channels": ["group"], "relationship_stages": ["any"],
                       "emotion_gates": ["any"], "intent": "any", "keywords": []}
LEGACY_SIGNAL = "legacy-expression-rule:group-family"
LEGACY_RUN = "legacy-expression:group-family"
SHARED_SIGNAL = "expression-share-signal:group-family"
SHARED_RUN = "expression-share-import:group-family"


def legacy_window(at):
    """The window shape a V1 group import stored next to its rules."""
    handle = LEGACY_SIGNAL + ":evidence:0"
    return {"opened_at": at, "closed_at": at, "handles": [handle], "characters": len(EVIDENCE),
            "source_kind": "legacy_expression",
            "legacy": {"source_kind": "group", "source_id": "v1-group-source", "samples": [],
                       "evidence": {handle: EVIDENCE}, "rules": {}}}, [handle]


def shared_window(at):
    """An imported share pack: no original evidence, only the reusable form."""
    return {"opened_at": at, "closed_at": at, "handles": [], "characters": 0,
            "source_kind": "shared_pack"}, []


class GroupExpressionFamilyTests(GroupFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.sequence = 0

    async def turn(self, *, target="group-a", text="大家好"):
        self.sequence += 1
        event = self.group_event(target=target, identifier="family-" + str(self.sequence), text=text)
        result = await self.controller.receive_claimed(event)
        self.assertTrue(result.is_success, result.failure)
        batch = result.unwrap()
        await wait_for_batch(self.controller.session, batch)
        app = self.controller.session.application
        return TaskContext.restore(json.loads(app.messages.batch(batch)["context_json"]))

    @staticmethod
    def write_rule(app, *, scope, role, signal_id, run_id, window, evidence, state="adopted"):
        at = app.tasks.now().isoformat()
        owner = RoleOwnerRef(scope.installation_id, scope.bot_id, role.subject_id)
        with app.runtime.transaction() as connection:
            connection.execute("""INSERT INTO expression_style_runs
                (run_id, owner_key, scope_key, sample_json, material_hash, summary, state,
                 created_at, decided_at) VALUES (?, ?, ?, ?, ?, '旧版表达', 'decided', ?, ?)""",
                (run_id, digest(owner), digest(scope), encode(window), digest([run_id, signal_id]),
                 at, at))
            connection.execute("""INSERT INTO expression_style_signals
                (signal_id, run_id, owner_key, scope_key, dimension, observation, proposal,
                 applicability_json, confidence, evidence_json, state, created_at, decided_at,
                 decided_by, decision_key, ordinal)
                VALUES (?, ?, ?, ?, 'diction', '旧版群规则', ?, ?, 1.0, ?, ?, ?, ?,
                        'legacy_migration', ?, 0)""",
                (signal_id, run_id, digest(owner), digest(scope), PROPOSAL,
                 encode(GROUP_APPLICABILITY), encode(evidence), state, at, at, "legacy:" + signal_id))

    @staticmethod
    def write_rules(app, *, scope, role, prefix, count, window, evidence, state="adopted"):
        """Adopted backlog of one scope, inserted in a single transaction."""
        at = app.tasks.now().isoformat()
        owner = RoleOwnerRef(scope.installation_id, scope.bot_id, role.subject_id)
        scope_key = digest(scope)
        runs, signals = [], []
        for index in range(count):
            run_id = f"{prefix}:run:{index:04d}"
            signal_id = f"{prefix}:{index:04d}"
            runs.append((run_id, digest(owner), scope_key, encode(window),
                         digest([run_id, signal_id]), at, at))
            signals.append((signal_id, run_id, digest(owner), scope_key, PROPOSAL,
                            encode(GROUP_APPLICABILITY), encode(evidence), state, at, at,
                            "legacy:" + signal_id))
        with app.runtime.transaction() as connection:
            connection.executemany("""INSERT INTO expression_style_runs
                (run_id, owner_key, scope_key, sample_json, material_hash, summary, state,
                 created_at, decided_at) VALUES (?, ?, ?, ?, ?, '旧版表达', 'decided', ?, ?)""", runs)
            connection.executemany("""INSERT INTO expression_style_signals
                (signal_id, run_id, owner_key, scope_key, dimension, observation, proposal,
                 applicability_json, confidence, evidence_json, state, created_at, decided_at,
                 decided_by, decision_key, ordinal)
                VALUES (?, ?, ?, ?, 'diction', '旧版群规则', ?, ?, 1.0, ?, ?, ?, ?,
                        'legacy_migration', ?, 0)""", signals)

    @staticmethod
    def operator(app):
        return dashboard_operator(app.runtime, "tester", at=app.tasks.now())

    async def test_a_legacy_group_rule_is_listed_injected_and_counted(self):
        await self.start_host()
        context = await self.turn()
        app = self.controller.session.application
        legacy = group_identity_scope(context.scope)
        self.assertIsNotNone(legacy)
        self.assertNotEqual(digest(legacy), digest(context.scope))
        window, handles = legacy_window(app.tasks.now().isoformat())
        self.write_rule(app, scope=legacy, role=context.role_binding.role,
                        signal_id=LEGACY_SIGNAL, run_id=LEGACY_RUN, window=window, evidence=handles)

        page = ExpressionStyleManagement(app).list(
            operator=self.operator(app), context=context, state="adopted")
        self.assertTrue(page.is_success, page.failure)
        items = page.unwrap()["items"]
        self.assertEqual([item["signal_id"] for item in items], [LEGACY_SIGNAL])
        self.assertIsNone(items[0]["unavailable_reason"])
        self.assertEqual(items[0]["source_kind"], "legacy_expression")
        self.assertEqual(items[0]["injections"], 0)
        self.assertEqual(items[0]["actions"], ["retire"])

        at = app.tasks.now()
        style = app.expression_style_context
        snapshot = style.capture(context, at)
        self.assertEqual(snapshot["state"], "ready", snapshot)
        self.assertEqual([item["signal_id"] for item in snapshot["references"]], [LEGACY_SIGNAL])
        materials = [item for item in style.contributions(snapshot, context, at, batch_id="family-batch")
                     if item.purpose == "expression_style_rule"]
        self.assertEqual(len(materials), 1)
        self.assertEqual(json.loads(materials[0].payload)["signal_id"], LEGACY_SIGNAL)
        self.assertEqual(json.loads(materials[0].payload)["basis"], "adopted_legacy_expression")

        # Only a confirmed delivery counts, and it counts under the scope the
        # rule belongs to, not the window that happened to carry it.
        with app.runtime.transaction() as connection:
            style.confirm_delivery(connection, batch_id="family-batch", context=context,
                                  delivered_at=at + timedelta(seconds=1))
        owner_key = digest(RoleOwnerRef(context.scope.installation_id, context.scope.bot_id,
                                       context.role_binding.role.subject_id))
        self.assertEqual(style_rotation.usage(app.runtime, owner_key, digest(legacy))[LEGACY_SIGNAL][0], 1)
        counted = ExpressionStyleManagement(app).list(
            operator=self.operator(app), context=context, state="adopted")
        self.assertEqual(counted.unwrap()["items"][0]["injections"], 1)

    async def test_another_group_or_role_never_sees_the_rule(self):
        await self.start_host()
        context = await self.turn()
        app = self.controller.session.application
        legacy = group_identity_scope(context.scope)
        window, handles = legacy_window(app.tasks.now().isoformat())
        self.write_rule(app, scope=legacy, role=context.role_binding.role,
                        signal_id=LEGACY_SIGNAL, run_id=LEGACY_RUN, window=window, evidence=handles)
        # The same rule id under another role of the same group is a different
        # owner and must not surface on this role's page either.
        self.write_rule(app, scope=legacy, role=SubjectRef(context.scope.installation_id,
                        SubjectKind.BOT, "role-b"), signal_id="legacy-expression-rule:other-role",
                        run_id="legacy-expression:other-role", window=window, evidence=handles)

        manager = ExpressionStyleManagement(app)
        own = manager.list(operator=self.operator(app), context=context, state="adopted").unwrap()
        self.assertEqual([item["signal_id"] for item in own["items"]], [LEGACY_SIGNAL])
        # group-b is another native group on the same Bot account; group-c
        # reuses this group id on another platform, Bot account and persona.
        for target in ("group-b", "group-c"):
            other = await self.turn(target=target)
            self.assertNotEqual(other.scope, context.scope)
            page = manager.list(operator=self.operator(app), context=other, state="adopted")
            self.assertTrue(page.is_success, page.failure)
            self.assertEqual(page.unwrap()["items"], [])
            self.assertEqual(app.expression_style_context.capture(other, app.tasks.now())["references"], [])

    async def test_another_group_backlog_cannot_crowd_out_the_pool(self):
        await self.start_host()
        context = await self.turn()
        app = self.controller.session.application
        # A second group of the same role keeps more adopted rules than the
        # bounded pool scan. Reading every adopted rule of the owner and then
        # filtering by family in Python would let that backlog truncate this
        # group's own rule out of the pool; the pool must be read per scope.
        other = await self.turn(target="group-b")
        self.assertNotEqual(digest(other.scope), digest(context.scope))
        at = app.tasks.now().isoformat()
        window, handles = shared_window(at)
        self.write_rules(app, scope=group_identity_scope(other.scope),
                         role=context.role_binding.role, prefix="backlog-rule",
                         count=MAX_POOL_SCAN + 8, window=window, evidence=handles)
        legacy = group_identity_scope(context.scope)
        window, handles = legacy_window(at)
        self.write_rule(app, scope=legacy, role=context.role_binding.role,
                        signal_id=LEGACY_SIGNAL, run_id=LEGACY_RUN, window=window, evidence=handles)

        style = app.expression_style_context
        now = app.tasks.now()
        snapshot = style.capture(context, now)
        self.assertEqual(snapshot["state"], "ready", snapshot)
        self.assertEqual([item["signal_id"] for item in snapshot["references"]], [LEGACY_SIGNAL])
        injected = [json.loads(item.payload)["signal_id"] for item in
                    style.contributions(snapshot, context, now, batch_id="crowded-pool")
                    if item.purpose == "expression_style_rule"]
        self.assertEqual(injected, [LEGACY_SIGNAL])
        # The other group's backlog stays out of this group's review page too.
        page = ExpressionStyleManagement(app).list(
            operator=self.operator(app), context=context, state="adopted")
        self.assertTrue(page.is_success, page.failure)
        self.assertEqual([item["signal_id"] for item in page.unwrap()["items"]], [LEGACY_SIGNAL])

    async def test_a_rotated_group_window_keeps_the_group_rules(self):
        await self.start_host()
        first = await self.turn()
        app = self.controller.session.application
        at = app.tasks.now().isoformat()
        legacy = group_identity_scope(first.scope)
        window, handles = legacy_window(at)
        self.write_rule(app, scope=legacy, role=first.role_binding.role,
                        signal_id=LEGACY_SIGNAL, run_id=LEGACY_RUN, window=window, evidence=handles)
        # A share pack imported in the first window is self-contained too, so it
        # also belongs to the group rather than to that one window.
        window, handles = shared_window(at)
        self.write_rule(app, scope=first.scope, role=first.role_binding.role,
                        signal_id=SHARED_SIGNAL, run_id=SHARED_RUN, window=window, evidence=handles)

        # AstrBot rotates the group session's conversation id.
        self.native.conversations["qq-main:GroupMessage:300"] = "conversation-rotated"
        second = await self.turn()
        self.assertNotEqual(digest(first.scope), digest(second.scope))

        page = ExpressionStyleManagement(app).list(
            operator=self.operator(app), context=second, state="adopted")
        self.assertTrue(page.is_success, page.failure)
        self.assertEqual(sorted(item["signal_id"] for item in page.unwrap()["items"]),
                         sorted([LEGACY_SIGNAL, SHARED_SIGNAL]))
        snapshot = app.expression_style_context.capture(second, app.tasks.now())
        self.assertEqual(sorted(item["signal_id"] for item in snapshot["references"]),
                         sorted([LEGACY_SIGNAL, SHARED_SIGNAL]))


if __name__ == "__main__":
    unittest.main()
