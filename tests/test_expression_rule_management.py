"""Rule management keeps the family, the recorded history and the page order.

Four review-page defects are covered here:

* importing a share pack from a rotated conversation window counted capacity in
  that window alone, so a full rule library could be exceeded;
* a rule demoted to ``pending`` by negative feedback showed no injections even
  though the rotation ledger still held them;
* a rule exported without a concrete form came back as a second rule;
* the pending list was ordered by ``signal_id`` and paged by that identifier,
  which is unrelated to creation time, so the first page was an arbitrary
  subset.
"""

import json
import unittest
from datetime import datetime, timedelta, timezone

from astrbot_host.management_identity import dashboard_operator
from companion_v2.execution.models import TaskContext, digest, encode, stamp
from companion_v2.expression import feedback as style_feedback
from companion_v2.expression import rule_details
from companion_v2.expression.configuration import read_settings
from companion_v2.expression.sharing import build_pack
from companion_v2.expression.style_management import ExpressionStyleManagement
from companion_v2.foundation.refs import AudienceRef, RoleOwnerRef
from tests.group_host_support import GroupFixture
from tests.staging_bootstrap_support import wait_for_batch
from tests.test_host_application import HostFixture
from tests.test_management_access import NOW, SCOPE


APPLICABILITY = {"channels": ["private", "group", "proactive", "qzone", "tts"],
                 "relationship_stages": ["any"], "emotion_gates": ["any"],
                 "intent": "any", "keywords": []}
FORM = {"kind": "style", "situation": "彼此已有共识，不需要展开解释时",
        "pattern": "懂的都懂", "avoid": "对方确实在请求解释时不要敷衍"}


def shared_window(at):
    """A share-pack window carries no original evidence."""
    return {"opened_at": at, "closed_at": at, "handles": [], "characters": 0,
            "source_kind": "shared_pack"}


def insert_rule(runtime, *, owner_key, scope_key, signal_id, run_id, created_at, at,
                state="pending", observation="晚间问候时使用轻松短句",
                proposal="用轻松短句问候", applicability=None):
    decided_at = None if state == "pending" else created_at
    decided_by = None if state == "pending" else "operator"
    decision_key = None if state == "pending" else "seed:" + signal_id
    with runtime.transaction() as connection:
        connection.execute("""INSERT INTO expression_style_runs
            (run_id, owner_key, scope_key, sample_json, material_hash, summary, state,
             created_at, decided_at) VALUES (?, ?, ?, ?, ?, '分享规则', 'decided', ?, ?)""",
            (run_id, owner_key, scope_key, encode(shared_window(at)), digest([run_id, signal_id]),
             created_at, created_at))
        connection.execute("""INSERT INTO expression_style_signals
            (signal_id, run_id, owner_key, scope_key, dimension, observation, proposal,
             applicability_json, confidence, evidence_json, state, created_at, decided_at,
             decided_by, decision_key, ordinal)
            VALUES (?, ?, ?, ?, 'diction', ?, ?, ?, 0.8, '[]', ?, ?, ?, ?, ?, 0)""",
            (signal_id, run_id, owner_key, scope_key, observation, proposal,
             encode(applicability or APPLICABILITY), state, created_at, decided_at,
             decided_by, decision_key))


class ExpressionShareFamilyTests(GroupFixture, unittest.IsolatedAsyncioTestCase):
    """Share-pack deduction follows the group family, not one window."""

    def setUp(self):
        super().setUp()
        self.sequence = 0

    async def turn(self, *, text="大家好"):
        self.sequence += 1
        event = self.group_event(identifier="rule-management-" + str(self.sequence), text=text)
        result = await self.controller.receive_claimed(event)
        self.assertTrue(result.is_success, result.failure)
        batch = result.unwrap()
        await wait_for_batch(self.controller.session, batch)
        app = self.controller.session.application
        return TaskContext.restore(json.loads(app.messages.batch(batch)["context_json"]))

    @staticmethod
    def write_rules(app, *, scope, role, prefix, count, at):
        owner = RoleOwnerRef(scope.installation_id, scope.bot_id, role.subject_id)
        runs, signals = [], []
        for index in range(count):
            run_id = f"{prefix}:run:{index:04d}"
            signal_id = f"{prefix}:{index:04d}"
            runs.append((run_id, digest(owner), digest(scope), encode(shared_window(at)),
                         digest([run_id, signal_id]), at, at))
            signals.append((signal_id, run_id, digest(owner), digest(scope),
                            "晚间问候时使用轻松短句", "用轻松短句问候",
                            encode(APPLICABILITY), at, at, "seed:" + signal_id))
        with app.runtime.transaction() as connection:
            connection.executemany("""INSERT INTO expression_style_runs
                (run_id, owner_key, scope_key, sample_json, material_hash, summary, state,
                 created_at, decided_at) VALUES (?, ?, ?, ?, ?, '分享规则', 'decided', ?, ?)""",
                runs)
            connection.executemany("""INSERT INTO expression_style_signals
                (signal_id, run_id, owner_key, scope_key, dimension, observation, proposal,
                 applicability_json, confidence, evidence_json, state, created_at, decided_at,
                 decided_by, decision_key, ordinal)
                VALUES (?, ?, ?, ?, 'diction', ?, ?, ?, 1.0, '[]', 'adopted', ?, ?,
                        'operator', ?, 0)""", signals)

    def rotate(self):
        self.native.conversations["qq-main:GroupMessage:300"] = "conversation-rotated"

    @staticmethod
    def operator(app):
        return dashboard_operator(app.runtime, "tester", at=app.tasks.now())

    async def test_import_capacity_counts_the_rules_of_earlier_windows(self):
        await self.start_host()
        first = await self.turn()
        app = self.controller.session.application
        self.rotate()
        second = await self.turn()
        self.assertNotEqual(digest(first.scope), digest(second.scope))
        limit = read_settings(app.runtime, second).active_signal_limit
        self.write_rules(app, scope=first.scope, role=first.role_binding.role,
                         prefix="family-active", count=limit, at=stamp(NOW))
        pack = build_pack([{"dimension": "diction", "observation": "先确认再补一句",
                            "proposal": "用两段话收尾", "confidence": 0.7}], title="家族容量")
        manager = ExpressionStyleManagement(app)
        preview = manager.preview_import(operator=self.operator(app), context=second,
                                         raw_pack=pack).unwrap()
        self.assertEqual(preview["capacity"], {"active": limit, "limit": limit})
        self.assertEqual((preview["importable_rule_count"], preview["duplicate_rule_count"],
                          preview["capacity_blocked_count"]), (0, 0, 1))
        imported = manager.import_pack(operator=self.operator(app), context=second, raw_pack=pack,
            preview_token=preview["preview_token"],
            operation_key="expression-family-capacity-import").unwrap()
        self.assertEqual((imported["imported_count"], imported["capacity"]["active"]), (0, limit))

    async def test_import_replay_reports_the_family_capacity(self):
        await self.start_host()
        first = await self.turn()
        app = self.controller.session.application
        self.rotate()
        second = await self.turn()
        limit = read_settings(app.runtime, second).active_signal_limit
        self.write_rules(app, scope=first.scope, role=first.role_binding.role,
                         prefix="family-replay", count=limit - 1, at=stamp(NOW))
        pack = build_pack([{"dimension": "diction", "observation": "先确认再补一句",
                            "proposal": "用两段话收尾", "confidence": 0.7}], title="家族回放")
        manager = ExpressionStyleManagement(app)
        preview = manager.preview_import(operator=self.operator(app), context=second,
                                         raw_pack=pack).unwrap()
        self.assertEqual(preview["importable_rule_count"], 1)
        imported = manager.import_pack(operator=self.operator(app), context=second, raw_pack=pack,
            preview_token=preview["preview_token"],
            operation_key="expression-family-capacity-replay").unwrap()
        self.assertEqual(imported["imported_count"], 1)
        replay = manager.import_pack(operator=self.operator(app), context=second, raw_pack=pack,
            preview_token="replay-does-not-need-a-preview",
            operation_key="expression-family-capacity-replay").unwrap()
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["capacity"], {"active": limit, "limit": limit})

    async def test_a_rule_adopted_in_an_earlier_window_is_still_a_duplicate(self):
        await self.start_host()
        first = await self.turn()
        app = self.controller.session.application
        at = stamp(app.tasks.now())
        owner = RoleOwnerRef(first.scope.installation_id, first.scope.bot_id,
                             first.role_binding.role.subject_id)
        signal_id = "family-duplicate:0000"
        insert_rule(app.runtime, owner_key=digest(owner), scope_key=digest(first.scope),
                    signal_id=signal_id, run_id="family-duplicate:run", created_at=at, at=at,
                    state="adopted", observation="不用展开解释", proposal="用默契短句收尾")
        rule_details.write(app.runtime, signal_id, FORM, at=app.tasks.now(),
                           actor="test", operation_key="seed-family-duplicate")
        self.rotate()
        second = await self.turn()
        pack = build_pack([{"dimension": "diction", "observation": "不用展开解释",
                            "proposal": "用默契短句收尾", "confidence": 0.7,
                            "expression": FORM, "applicability": APPLICABILITY}],
                          title="家族去重")
        preview = ExpressionStyleManagement(app).preview_import(
            operator=self.operator(app), context=second, raw_pack=pack).unwrap()
        self.assertEqual((preview["importable_rule_count"], preview["duplicate_rule_count"]),
                         (0, 1))


class ExpressionRuleHistoryTests(HostFixture, unittest.IsolatedAsyncioTestCase):
    """History survives a demotion and a form-less export round trip."""

    def selected_context(self):
        binding = self.identity.resolve_role(SCOPE, observed_at=self.clock.now()).unwrap()
        return TaskContext(self.important, binding, AudienceRef(SCOPE, (self.important.subject,)))

    @staticmethod
    def owner(context):
        return RoleOwnerRef(context.scope.installation_id, context.scope.bot_id,
                            context.role_binding.role.subject_id)

    async def test_a_demoted_rule_keeps_its_recorded_injections(self):
        host = await self.start()
        app = host.application
        context = self.selected_context()
        owner_key, scope_key = digest(self.owner(context)), digest(context.scope)
        at = app.tasks.now()
        signal_id = "history-signal:0000"
        insert_rule(app.runtime, owner_key=owner_key, scope_key=scope_key,
                    signal_id=signal_id, run_id="history-run", created_at=stamp(at), at=stamp(at),
                    state="adopted")
        style = app.expression_style_context

        def deliver(batch_id, delivered_at):
            style_feedback.stage_injection(app.runtime, batch_id=batch_id, owner_key=owner_key,
                scope_key=scope_key, signal_ids=(signal_id,), at=delivered_at - timedelta(seconds=1))
            with app.runtime.transaction() as connection:
                style.confirm_delivery(connection, batch_id=batch_id, context=context,
                                       delivered_at=delivered_at)

        def reject(text, received_at):
            with app.runtime.transaction() as connection:
                return style.consume_feedback(connection, context=context, text=text,
                                              received_at=received_at)

        deliver("history-batch-1", at)
        self.assertEqual(reject("别这么说", at + timedelta(seconds=1))["demoted_rules"], 0)
        deliver("history-batch-2", at + timedelta(minutes=1))
        self.assertEqual(reject("这个语气怪怪的，别这样说",
                                at + timedelta(minutes=1, seconds=1))["demoted_rules"], 1)
        self.assertEqual(app.runtime.read_one(
            "SELECT state FROM expression_style_signals WHERE signal_id = ?", (signal_id,))["state"],
            "pending")

        item = ExpressionStyleManagement(app).list(
            operator=self.core, context=context, state="pending").unwrap()["items"][0]
        # The second confirmed delivery is the most recent injection; both stay
        # visible after expression feedback returns the rule to review.
        self.assertEqual(item["injections"], 2)
        self.assertEqual(datetime.fromisoformat(item["last_injected_at"]),
                         at + timedelta(minutes=1))
        self.assertEqual(item["state"], "pending")

    async def test_a_rule_without_a_form_is_not_duplicated_by_its_own_export(self):
        host = await self.start()
        app = host.application
        manager = ExpressionStyleManagement(app)
        context = self.selected_context()
        at = stamp(app.tasks.now())
        insert_rule(app.runtime, owner_key=digest(self.owner(context)),
                    scope_key=digest(context.scope), signal_id="formless-signal",
                    run_id="formless-run", created_at=at, at=at, state="adopted")
        exported = manager.export_pack(operator=self.core, context=context).unwrap()
        exported_rule = exported["package"]["rule_groups"][0]["rules"][0]
        self.assertNotIn("expression", exported_rule)

        preview = manager.preview_import(operator=self.core, context=context,
                                         raw_pack=exported["package"]).unwrap()
        self.assertEqual((preview["importable_rule_count"], preview["duplicate_rule_count"]),
                         (0, 1))
        imported = manager.import_pack(operator=self.core, context=context,
            raw_pack=exported["package"], preview_token=preview["preview_token"],
            operation_key="formless-reimport").unwrap()
        self.assertEqual(imported["imported_count"], 0)


class ExpressionRulePageTests(HostFixture, unittest.IsolatedAsyncioTestCase):
    """The pending list reads newest first and pages on the composite key."""

    def selected_context(self):
        binding = self.identity.resolve_role(SCOPE, observed_at=self.clock.now()).unwrap()
        return TaskContext(self.important, binding, AudienceRef(SCOPE, (self.important.subject,)))

    async def test_the_list_is_newest_first_and_the_cursor_keeps_the_position(self):
        host = await self.start()
        app = host.application
        context = self.selected_context()
        owner_key, scope_key = digest(RoleOwnerRef(context.scope.installation_id,
            context.scope.bot_id, context.role_binding.role.subject_id)), digest(context.scope)
        window_at = stamp(NOW - timedelta(hours=3))
        older, newer = stamp(NOW - timedelta(hours=2)), stamp(NOW - timedelta(hours=1))
        for signal_id, created_at in (("signal-a", older), ("signal-c", newer),
                                      ("signal-b", newer)):
            insert_rule(app.runtime, owner_key=owner_key, scope_key=scope_key,
                        signal_id=signal_id, run_id="page-run:" + signal_id,
                        created_at=created_at, at=window_at)
        manager = ExpressionStyleManagement(app)

        first = manager.list(operator=self.core, context=context, limit=1).unwrap()
        self.assertEqual([item["signal_id"] for item in first["items"]], ["signal-c"])
        self.assertEqual(first["next_cursor"], newer + "|signal-c")
        second = manager.list(operator=self.core, context=context, limit=1,
                              after_id=first["next_cursor"]).unwrap()
        self.assertEqual([item["signal_id"] for item in second["items"]], ["signal-b"])
        # The cursor carries a UTC offset, so the same instant written in
        # another offset names the same position.
        shifted = datetime.fromisoformat(newer).astimezone(
            timezone(timedelta(hours=8))).isoformat()
        same_page = manager.list(operator=self.core, context=context, limit=1,
                                 after_id=shifted + "|signal-c").unwrap()
        self.assertEqual([item["signal_id"] for item in same_page["items"]], ["signal-b"])
        third = manager.list(operator=self.core, context=context, limit=1,
                             after_id=second["next_cursor"]).unwrap()
        self.assertEqual([item["signal_id"] for item in third["items"]], ["signal-a"])
        self.assertIsNone(third["next_cursor"])

        # A cursor stored before this ordering existed is a bare signal id and
        # cannot name a position in the new key, so it is refused explicitly.
        legacy = manager.list(operator=self.core, context=context, after_id="signal-b")
        self.assertEqual(legacy.failure.code, "expression_query_invalid")


if __name__ == "__main__":
    unittest.main()
