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
from companion_v2.conversation.scope_family import group_identity_scope, scope_family_keys
from companion_v2.conversation.style_context import MAX_POOL_SCAN
from companion_v2.execution.models import TaskContext, digest, encode, stamp
from companion_v2.expression import rule_details, style_rotation
from companion_v2.expression.configuration import read_settings
from companion_v2.expression.sharing import build_pack
from companion_v2.expression.style_evidence import sample_document
from companion_v2.expression.style_management import ExpressionStyleManagement
from companion_v2.expression.window_scope import rule_scope_counts
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
DIALOGUE_SIGNAL = "expression-signal:group-dialogue"
DIALOGUE_RUN = "expression-run:group-dialogue"
REVOKED_SIGNAL = "expression-signal:approved-then-withdrawn"
REVOKED_RUN = "expression-run:approved-then-withdrawn"
RESTORE_SIGNAL = "expression-share-signal:restore-budget"
RESTORE_RUN = "expression-share-import:restore-budget"
DIALOGUE_FORM = {"kind": "style", "situation": "群里接话时", "pattern": "懂的都懂",
                 "avoid": "对方明确要求解释时不要敷衍"}


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

    def dialogue_window(self, app, context):
        """A conversation-input window whose samples this exact group window holds."""
        at = app.tasks.now()
        policy = app.expression_style._policy(context, at)
        samples = app.expression_samples.collect(
            context, at, sample_limit=policy.sample_limit, sample_chars=policy.sample_chars,
            source_kind="conversation_input", review_enabled=policy.sample_review_enabled)
        handles = [item.handle for item in samples.samples[:2]]
        self.assertTrue(handles, "the group turn produced no reviewable samples")
        return {**sample_document(samples), "context_key": context.key}, handles

    def adopt(self, app, context, signal_id):
        manager = ExpressionStyleManagement(app)
        page = manager.list(operator=self.operator(app), context=context, state="pending")
        self.assertTrue(page.is_success, page.failure)
        item = next(item for item in page.unwrap()["items"] if item["signal_id"] == signal_id)
        done = manager.command(operator=self.operator(app), context=context, signal_id=signal_id,
                               action="adopt", view_token=item["view_token"],
                               operation_key="adopt-" + signal_id)
        self.assertTrue(done.is_success, done.failure)
        return done.unwrap()

    @staticmethod
    def reject_samples(app, context, handles):
        """Withdraw the approval that admitted these samples (the operator's reject)."""
        reviews, at = app.expression_samples.reviews, stamp(app.tasks.now())
        for handle in handles:
            value = reviews.read(context, handle) or {
                "handle": handle, "content_hash": digest(handle), "state": "pending",
                "revision": 1, "seen_at": at, "decided_at": None, "decided_by": None,
                "decision_key": None}
            reviews.write(context, {**value, "state": "rejected", "revision": value["revision"] + 1,
                                    "decided_at": at, "decided_by": "tester",
                                    "decision_key": "reject:" + handle})

    async def test_a_rotated_group_dialogue_rule_is_listed_but_not_counted(self):
        await self.start_host()
        first = await self.turn()
        app = self.controller.session.application
        window, handles = self.dialogue_window(app, first)
        self.write_rule(app, scope=first.scope, role=first.role_binding.role,
                        signal_id=DIALOGUE_SIGNAL, run_id=DIALOGUE_RUN, window=window,
                        evidence=handles, state="pending")
        rule_details.write(app.runtime, DIALOGUE_SIGNAL, DIALOGUE_FORM, at=app.tasks.now(),
                           actor="learning_model", operation_key=DIALOGUE_RUN)
        self.adopt(app, first, DIALOGUE_SIGNAL)

        manager = ExpressionStyleManagement(app)
        before = manager.list(operator=self.operator(app), context=first, state="adopted")
        self.assertTrue(before.is_success, before.failure)
        before_item = before.unwrap()["items"][0]
        self.assertIsNone(before_item["unavailable_reason"])
        self.assertEqual(before.unwrap()["capacity"]["active"], 1,
                         "a dialogue rule counts while its own window is current")
        before_capture = app.expression_style_context.capture(first, app.tasks.now())
        self.assertEqual([item["signal_id"] for item in before_capture["references"]],
                         [DIALOGUE_SIGNAL])

        # AstrBot rotates the group session's conversation id.
        self.native.conversations["qq-main:GroupMessage:300"] = "conversation-rotated"
        second = await self.turn()
        self.assertNotEqual(digest(first.scope), digest(second.scope))

        after = manager.list(operator=self.operator(app), context=second, state="adopted")
        self.assertTrue(after.is_success, after.failure)
        data = after.unwrap()
        self.assertEqual([item["signal_id"] for item in data["items"]], [DIALOGUE_SIGNAL],
                         "the review page still lists the rotated rule")
        self.assertEqual(data["items"][0]["unavailable_reason"], "expression_rule_window_rotated",
                         "a rotated window is not unreadable evidence")
        self.assertEqual(data["items"][0]["expression"], DIALOGUE_FORM,
                         "the reviewed rule stays visible even though it no longer injects")
        self.assertEqual(data["items"][0]["actions"], ["retire"])
        self.assertFalse(data["items"][0]["can_revise"])
        self.assertIs(data["items"][0]["injectable"], False,
                      "the item answers the same question as the capacity")
        self.assertEqual(data["counts"].get("adopted"), 0,
                         "the enabled tab counts only rules this window can inject")
        self.assertEqual(data["rotated_rules"], 1,
                         "the page can still say how many rules are window-bound")
        self.assertEqual(data["capacity"]["active"], 0,
                         "a rule that cannot be injected must not occupy the family budget")
        self.assertEqual(app.expression_style._active_signal_count(second), 0,
                         "the learning gate must not count an unusable rule")
        self.assertEqual(app.expression_style.status(second)["active"], 0)
        self.assertEqual(app.expression_style_context.capture(second, app.tasks.now())["references"], [])

    async def test_damaged_evidence_keeps_the_unreadable_reason(self):
        """Rotation is a separate reason; genuinely damaged evidence is not.

        A window that moved on is a normal lifecycle event, while an unreadable
        stored sample is a data failure. Both keep the rule listable, but only
        the latter may say the original evidence is unreadable.
        """
        await self.start_host()
        first = await self.turn()
        app = self.controller.session.application
        window, handles = self.dialogue_window(app, first)
        self.write_rule(app, scope=first.scope, role=first.role_binding.role,
                        signal_id="expression-signal:damaged", run_id="expression-run:damaged",
                        window=window, evidence=handles)
        with app.runtime.transaction() as connection:
            connection.execute("UPDATE expression_style_runs SET sample_json = '{' WHERE run_id = ?",
                               ("expression-run:damaged",))

        self.native.conversations["qq-main:GroupMessage:300"] = "conversation-rotated"
        second = await self.turn()
        self.assertNotEqual(digest(first.scope), digest(second.scope))

        page = ExpressionStyleManagement(app).list(operator=self.operator(app), context=second,
                                                  state="adopted")
        self.assertTrue(page.is_success, page.failure)
        data = page.unwrap()
        item = next(item for item in data["items"] if item["signal_id"] == "expression-signal:damaged")
        self.assertEqual(item["unavailable_reason"], "expression_sample_changed",
                         "damaged stored evidence stays an unreadable-evidence failure")
        self.assertIsNone(item["expression"], "unreadable evidence keeps withholding the rule body")
        self.assertEqual(item["actions"], ["retire"])
        self.assertIs(item["injectable"], False)

    async def test_a_withdrawn_sample_approval_stops_the_rule_counting_or_injecting(self):
        """A rejected sample retires the rule's influence, so it stops counting.

        Reply injection already drops a ``conversation_input`` rule whose sample
        approval was revoked (``reviews.allows_handles``). The capacity, the
        state tabs and the learning budget have to read the same set, otherwise
        the page promises an enabled rule that can never be used.
        """
        await self.start_host()
        first = await self.turn()
        app = self.controller.session.application
        window, handles = self.dialogue_window(app, first)
        self.write_rule(app, scope=first.scope, role=first.role_binding.role,
                        signal_id=REVOKED_SIGNAL, run_id=REVOKED_RUN, window=window,
                        evidence=handles, state="pending")
        rule_details.write(app.runtime, REVOKED_SIGNAL, DIALOGUE_FORM, at=app.tasks.now(),
                           actor="learning_model", operation_key=REVOKED_RUN)
        self.adopt(app, first, REVOKED_SIGNAL)
        manager = ExpressionStyleManagement(app)

        before = manager.list(operator=self.operator(app), context=first, state="adopted").unwrap()
        self.assertEqual(before["capacity"]["active"], 1)
        self.assertIs(before["items"][0]["injectable"], True)
        self.assertEqual([item["signal_id"] for item in
                          app.expression_style_context.capture(first, app.tasks.now())["references"]],
                         [REVOKED_SIGNAL], "the approved rule is injected while its approval holds")

        self.reject_samples(app, first, handles)

        after = manager.list(operator=self.operator(app), context=first, state="adopted").unwrap()
        item = after["items"][0]
        self.assertEqual([entry["signal_id"] for entry in after["items"]], [REVOKED_SIGNAL],
                         "the review page still lists the rule it must let the operator retire")
        self.assertEqual(item["unavailable_reason"], "expression_rule_review_revoked",
                         "a withdrawn approval is its own reason, not a rotated window")
        self.assertIs(item["injectable"], False)
        self.assertEqual(item["expression"], DIALOGUE_FORM,
                         "the reviewed rule stays visible: only its approval was withdrawn")
        self.assertEqual(item["actions"], ["retire"])
        self.assertEqual(after["counts"].get("adopted"), 0,
                         "the enabled tab counts only rules injection can really use")
        self.assertEqual(after["rotated_rules"], 0, "the window itself did not move")
        self.assertEqual(after["capacity"]["active"], 0,
                         "a rule injection dropped must free its capacity slot")
        self.assertEqual(app.expression_style._active_signal_count(first), 0,
                         "the learning budget must not count a rule that can never inject")
        self.assertEqual(app.expression_style.status(first)["active"], 0)
        self.assertEqual(app.expression_style_context.capture(first, app.tasks.now())["references"], [],
                         "injection already dropped the rule; the count now agrees")

    async def test_capacity_equals_the_rules_reply_injection_really_uses(self):
        """Mixed family: one injectable, one rotated, one with a withdrawn approval.

        The capacity is pinned to the injected set itself rather than to a
        hand-counted number, so any future precondition the two sides disagree
        on turns this test red.
        """
        await self.start_host()
        first = await self.turn()
        app = self.controller.session.application
        # A self-contained rule follows the group through the rotation.
        window, handles = shared_window(app.tasks.now().isoformat())
        self.write_rule(app, scope=first.scope, role=first.role_binding.role,
                        signal_id=SHARED_SIGNAL, run_id=SHARED_RUN, window=window, evidence=handles)
        # A dialogue rule stays bound to the window that captured it.
        window, handles = self.dialogue_window(app, first)
        self.write_rule(app, scope=first.scope, role=first.role_binding.role,
                        signal_id=DIALOGUE_SIGNAL, run_id=DIALOGUE_RUN, window=window,
                        evidence=handles)

        self.native.conversations["qq-main:GroupMessage:300"] = "conversation-rotated"
        second = await self.turn()
        self.assertNotEqual(digest(first.scope), digest(second.scope))

        # A rule of the new window whose samples were withdrawn after approval.
        window, handles = self.dialogue_window(app, second)
        self.write_rule(app, scope=second.scope, role=second.role_binding.role,
                        signal_id=REVOKED_SIGNAL, run_id=REVOKED_RUN, window=window,
                        evidence=handles)
        self.reject_samples(app, second, handles)

        manager = ExpressionStyleManagement(app)
        page = manager.list(operator=self.operator(app), context=second, state="adopted").unwrap()
        injected = [item["signal_id"] for item in
                    app.expression_style_context.capture(second, app.tasks.now())["references"]]
        self.assertEqual(injected, [SHARED_SIGNAL],
                         "only the self-contained rule can still be injected here")
        self.assertEqual(page["capacity"]["active"], len(injected),
                         "capacity is exactly the injected set, not a guess")
        self.assertEqual(page["capacity"]["active"], 1)
        self.assertEqual([item["signal_id"] for item in page["items"] if item["injectable"]],
                         injected, "the item-level answer matches the injected set")
        reasons = {item["signal_id"]: item["unavailable_reason"] for item in page["items"]}
        self.assertEqual(reasons, {SHARED_SIGNAL: None,
                                   DIALOGUE_SIGNAL: "expression_rule_window_rotated",
                                   REVOKED_SIGNAL: "expression_rule_review_revoked"},
                         "each unusable rule keeps its own reason")
        self.assertEqual(page["counts"].get("adopted"), 1)
        self.assertEqual(page["counts"].get("pending", 0) + page["counts"].get("adopted", 0),
                         page["capacity"]["active"],
                         "the state tabs count the same injectable set as the capacity")
        self.assertEqual(page["rotated_rules"], 1)
        self.assertEqual(app.expression_style._active_signal_count(second), len(injected))
        self.assertEqual(app.expression_style.status(second)["active"], len(injected))

        # The import budget is the same number, so an import cannot oversell.
        pack = build_pack([{"dimension": "diction", "observation": "轮换后更短地接话",
                            "proposal": "先接住再补一句"}], title="可注入容量")
        preview = manager.preview_import(operator=self.operator(app), context=second,
                                         raw_pack=pack).unwrap()
        self.assertEqual(preview["capacity"], page["capacity"])

    async def test_the_injectable_set_is_not_just_the_window(self):
        """Reverse control: the window alone still counts the withdrawn rule.

        The same rows are counted twice, once with only the stored window (the
        old judgement) and once with the source catalogue reply injection uses.
        A predicate that fell back to the window alone would report one active
        rule here while injection uses none.
        """
        await self.start_host()
        first = await self.turn()
        app = self.controller.session.application
        window, handles = self.dialogue_window(app, first)
        self.write_rule(app, scope=first.scope, role=first.role_binding.role,
                        signal_id=REVOKED_SIGNAL, run_id=REVOKED_RUN, window=window,
                        evidence=handles)
        self.reject_samples(app, first, handles)

        owner = RoleOwnerRef(first.scope.installation_id, first.scope.bot_id,
                             first.role_binding.role.subject_id)
        keys = scope_family_keys(app.runtime, first)
        window_only, rotated = rule_scope_counts(app.runtime, first, digest(owner), keys)
        injectable, injectable_rotated = rule_scope_counts(
            app.runtime, first, digest(owner), keys, samples=app.expression_samples,
            at=app.tasks.now())
        self.assertEqual((sum(window_only.values()), rotated), (1, 0),
                         "a window-only judgement still holds this rule")
        self.assertEqual((sum(injectable.values()), injectable_rotated), (0, 0),
                         "the approval check injection already applies must decide the count")

    async def test_unreadable_evidence_keeps_the_body_but_damage_does_not(self):
        """Two different failures: the archive is gone, or the record is broken.

        Injection is decided by the window and the approval, not by the archived
        text, so a rule whose cited messages are gone still holds its slot and
        keeps its reviewed form. A damaged run record hides the body instead:
        nothing verifiable is left to show.
        """
        await self.start_host()
        first = await self.turn()
        app = self.controller.session.application
        window, _ = self.dialogue_window(app, first)
        missing = "input:" + "0" * 64
        self.write_rule(app, scope=first.scope, role=first.role_binding.role,
                        signal_id=DIALOGUE_SIGNAL, run_id=DIALOGUE_RUN,
                        window={**window, "handles": [missing]}, evidence=[missing])

        page = ExpressionStyleManagement(app).list(operator=self.operator(app), context=first,
                                                   state="adopted")
        self.assertTrue(page.is_success, page.failure)
        data = page.unwrap()
        item = data["items"][0]
        self.assertEqual(item["unavailable_reason"], "expression_sample_changed")
        self.assertEqual(item["evidence"], [], "the cited text cannot be shown")
        self.assertIs(item["injectable"], True,
                      "a missing archive does not change what this window can inject")
        self.assertEqual(item["observation"], "旧版群规则",
                         "unreadable evidence keeps the reviewed body")
        self.assertEqual(data["capacity"]["active"], 1)
        self.assertEqual(data["counts"].get("adopted"), 1)

    async def test_a_rotated_group_dialogue_backlog_does_not_block_the_budget(self):
        await self.start_host()
        first = await self.turn()
        app = self.controller.session.application
        service = app.expression_style
        limit = service._policy(first, app.tasks.now()).active_signal_limit
        window, handles = self.dialogue_window(app, first)
        self.write_rules(app, scope=first.scope, role=first.role_binding.role,
                         prefix="rotated-dialogue", count=limit, window=window, evidence=handles)

        self.native.conversations["qq-main:GroupMessage:300"] = "conversation-rotated"
        second = await self.turn()
        self.assertNotEqual(digest(first.scope), digest(second.scope))

        self.assertEqual(service._active_signal_count(second), 0,
                         "a rotated window's dialogue rules are not injectable there")
        self.assertEqual(service.status(second)["active"], 0)
        service.next_checks.clear()
        self.assertFalse(service._scan(second, "", app.tasks.now()))
        self.assertNotEqual(service.results.get(service._work_key(second), {}).get("state"),
                            "capacity_reached",
                            "unusable rules must not close the learning budget")

        # A rule this window can actually use must be restorable even though the
        # family still holds the previous window's unusable dialogue backlog.
        window, handles = shared_window(app.tasks.now().isoformat())
        self.write_rule(app, scope=second.scope, role=second.role_binding.role,
                        signal_id=RESTORE_SIGNAL, run_id=RESTORE_RUN, window=window,
                        evidence=handles, state="retired")
        manager = ExpressionStyleManagement(app)
        retired = manager.list(operator=self.operator(app), context=second, state="retired")
        self.assertTrue(retired.is_success, retired.failure)
        item = next(item for item in retired.unwrap()["items"] if item["signal_id"] == RESTORE_SIGNAL)
        restored = manager.command(operator=self.operator(app), context=second,
                                   signal_id=RESTORE_SIGNAL, action="restore",
                                   view_token=item["view_token"],
                                   operation_key="restore-rotated-budget")
        self.assertTrue(restored.is_success, restored.failure)
        self.assertEqual(restored.unwrap()["state"], "adopted")

    async def test_import_capacity_ignores_a_rotated_group_dialogue_rule(self):
        """A rule the new window cannot inject must not fill the import budget."""
        await self.start_host()
        first = await self.turn()
        app = self.controller.session.application
        window, handles = self.dialogue_window(app, first)
        self.write_rule(app, scope=first.scope, role=first.role_binding.role,
                        signal_id=DIALOGUE_SIGNAL, run_id=DIALOGUE_RUN, window=window,
                        evidence=handles)

        self.native.conversations["qq-main:GroupMessage:300"] = "conversation-rotated"
        second = await self.turn()
        self.assertNotEqual(digest(first.scope), digest(second.scope))
        limit = read_settings(app.runtime, second).active_signal_limit
        pack = build_pack([{"dimension": "diction", "observation": "旧版群规则",
                            "proposal": PROPOSAL}], title="导入容量")
        manager = ExpressionStyleManagement(app)

        preview = manager.preview_import(operator=self.operator(app), context=second,
                                         raw_pack=pack).unwrap()
        self.assertEqual(preview["capacity"], {"active": 0, "limit": limit},
                         "a rotated dialogue rule must not spend the import budget")
        self.assertEqual((preview["importable_rule_count"], preview["duplicate_rule_count"],
                          preview["capacity_blocked_count"]), (1, 0, 0))

        imported = manager.import_pack(operator=self.operator(app), context=second, raw_pack=pack,
            preview_token=preview["preview_token"],
            operation_key="import-rotated-dialogue-capacity").unwrap()
        self.assertEqual((imported["imported_count"], imported["capacity"]),
                         (1, {"active": 1, "limit": limit}),
                         "the imported self-contained rule takes the freed slot")

    async def test_import_capacity_counts_usable_family_rules_and_replays_them(self):
        """The import budget is the family's injectable set, not this window's rows.

        A share-pack rule imported in an earlier window is still injectable after
        AstrBot rotates the conversation, so it has to spend one slot. A rotated
        dialogue rule in the same family does not.
        """
        await self.start_host()
        first = await self.turn()
        app = self.controller.session.application
        window, handles = shared_window(app.tasks.now().isoformat())
        self.write_rule(app, scope=first.scope, role=first.role_binding.role,
                        signal_id=SHARED_SIGNAL, run_id=SHARED_RUN, window=window,
                        evidence=handles)
        window, handles = self.dialogue_window(app, first)
        self.write_rule(app, scope=first.scope, role=first.role_binding.role,
                        signal_id=DIALOGUE_SIGNAL, run_id=DIALOGUE_RUN, window=window,
                        evidence=handles)

        self.native.conversations["qq-main:GroupMessage:300"] = "conversation-rotated"
        second = await self.turn()
        self.assertNotEqual(digest(first.scope), digest(second.scope))
        limit = read_settings(app.runtime, second).active_signal_limit
        pack = build_pack([{"dimension": "diction", "observation": "轮换后更短地接话",
                            "proposal": "先接住再补一句"}], title="家族导入容量")
        manager = ExpressionStyleManagement(app)

        preview = manager.preview_import(operator=self.operator(app), context=second,
                                         raw_pack=pack).unwrap()
        self.assertEqual(preview["capacity"], {"active": 1, "limit": limit},
                         "the earlier window's share-pack rule still injects here")
        self.assertEqual(preview["importable_rule_count"], 1)

        imported = manager.import_pack(operator=self.operator(app), context=second, raw_pack=pack,
            preview_token=preview["preview_token"],
            operation_key="import-family-injectable-capacity").unwrap()
        self.assertEqual((imported["imported_count"], imported["capacity"]),
                         (1, {"active": 2, "limit": limit}))

        replayed = manager.import_pack(operator=self.operator(app), context=second, raw_pack=pack,
            preview_token="stale-but-the-same-operation-replays",
            operation_key="import-family-injectable-capacity").unwrap()
        self.assertTrue(replayed["replayed"])
        self.assertEqual(replayed["capacity"], {"active": 2, "limit": limit},
                         "replay reports the same family + injectable capacity")

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

    async def test_a_rotated_group_rule_takes_negative_feedback_and_returns_to_review(self):
        await self.start_host()
        first = await self.turn()
        app = self.controller.session.application
        at = app.tasks.now().isoformat()
        # One rule came from the V1 group import and one from an imported share
        # pack: both are self-contained, so both survive the rotation.
        window, handles = legacy_window(at)
        self.write_rule(app, scope=group_identity_scope(first.scope), role=first.role_binding.role,
                        signal_id=LEGACY_SIGNAL, run_id=LEGACY_RUN, window=window, evidence=handles)
        window, handles = shared_window(at)
        self.write_rule(app, scope=first.scope, role=first.role_binding.role,
                        signal_id=SHARED_SIGNAL, run_id=SHARED_RUN, window=window, evidence=handles)

        self.native.conversations["qq-main:GroupMessage:300"] = "conversation-rotated"
        second = await self.turn()
        self.assertNotEqual(digest(first.scope), digest(second.scope))

        style = app.expression_style_context
        delivered_at = app.tasks.now()
        for attempt in (1, 2):
            captured_at = delivered_at + timedelta(seconds=attempt * 10)
            snapshot = style.capture(second, captured_at)
            self.assertEqual(sorted(item["signal_id"] for item in snapshot["references"]),
                             sorted([LEGACY_SIGNAL, SHARED_SIGNAL]))
            batch = f"rotated-group-feedback-{attempt}"
            style.contributions(snapshot, second, captured_at, batch_id=batch)
            with app.runtime.transaction() as connection:
                style.confirm_delivery(connection, batch_id=batch, context=second,
                                       delivered_at=captured_at + timedelta(seconds=1))
            with app.runtime.transaction() as connection:
                consumed = style.consume_feedback(connection, context=second, text="别这么说",
                                                  received_at=captured_at + timedelta(seconds=2))
            self.assertEqual((consumed["signal"], consumed["updated_rules"], consumed["demoted_rules"]),
                             ("negative", 2, 0 if attempt == 1 else 2))

        # Each rule keeps one counter set inside the family, so the review page
        # shows two complaints per rule rather than one set per window.
        page = ExpressionStyleManagement(app).list(
            operator=self.operator(app), context=second, state="pending")
        self.assertTrue(page.is_success, page.failure)
        items = page.unwrap()["items"]
        self.assertEqual(sorted(item["signal_id"] for item in items),
                         sorted([LEGACY_SIGNAL, SHARED_SIGNAL]))
        self.assertEqual([item["negative_feedback"] for item in items], [2, 2])
        self.assertTrue(all("退回复审" in item["review_reason"] for item in items))

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


    async def test_a_rotated_group_window_counts_the_family_budget(self):
        await self.start_host()
        first = await self.turn()
        app = self.controller.session.application
        service = app.expression_style
        limit = service._policy(first, app.tasks.now()).active_signal_limit
        # Rules the group already holds under its V1 identity scope: the synthetic
        # scope no conversation window ever occupies, but the same group and role.
        window, handles = shared_window(app.tasks.now().isoformat())
        self.write_rules(app, scope=group_identity_scope(first.scope),
                         role=first.role_binding.role, prefix="family-budget",
                         count=limit, window=window, evidence=handles)
        self.assertEqual(service._active_signal_count(first), limit,
                         "the synthetic V1 identity scope belongs to the group family")

        # AstrBot rotates the group session's conversation id.
        self.native.conversations["qq-main:GroupMessage:300"] = "conversation-rotated"
        second = await self.turn()
        self.assertNotEqual(digest(first.scope), digest(second.scope))
        self.assertEqual(service._active_signal_count(second), limit,
                         "a rotated group window must count the family's rules")
        service.next_checks.clear()
        self.assertFalse(service._scan(second, "", app.tasks.now()))
        self.assertEqual(service.results[service._work_key(second)]["state"], "capacity_reached",
                         "a full family budget must gate the rotated group window too")


if __name__ == "__main__":
    unittest.main()
