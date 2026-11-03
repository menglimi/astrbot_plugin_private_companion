from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from companion_v2.execution.models import digest, stamp
from companion_v2.conversation.style_context import ConversationExpressionStyleContext
from companion_v2.expression import feedback, style_rotation
from companion_v2.foundation.refs import RoleOwnerRef, ScopeKind, ScopeRef
from companion_v2.storage.runtime import StorageRuntime
from tests.test_management_access import INSTALLATION, ROLE, SCOPE


NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)


class ExpressionFeedbackTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="companion-v2-expression-feedback-")
        self.addCleanup(self.temporary.cleanup)
        self.runtime = StorageRuntime.open(data_root=Path(self.temporary.name) / "data",
                                           installation_id=INSTALLATION)
        self.addCleanup(self.runtime.close)
        self.context = SimpleNamespace(scope=SCOPE, role_binding=SimpleNamespace(role=ROLE))
        owner = RoleOwnerRef(SCOPE.installation_id, SCOPE.bot_id, ROLE.subject_id)
        self.owner_key, self.scope_key = digest(owner), digest(SCOPE)
        catalog = SimpleNamespace(runtime=self.runtime, role_owner=lambda _context: owner)
        self.style_context = ConversationExpressionStyleContext(catalog)
        self.signal_id = "expression-run:feedback:signal:0"
        self.write_rule(self.scope_key, run_id="expression-run:feedback", signal_id=self.signal_id)

    def write_rule(self, scope_key, *, run_id, signal_id):
        with self.runtime.transaction() as connection:
            connection.execute("""INSERT INTO expression_style_runs
                (run_id, owner_key, scope_key, sample_json, material_hash, summary, state, created_at, decided_at)
                VALUES (?, ?, ?, '{}', 'sample-hash', '表达测试', 'pending', ?, NULL)""",
                (run_id, self.owner_key, scope_key, stamp(NOW)))
            connection.execute("""INSERT INTO expression_style_signals
                (signal_id, run_id, owner_key, scope_key, dimension, observation, proposal, confidence,
                 evidence_json, state, created_at, decided_at, decided_by, decision_key, ordinal)
                VALUES (?, ?, ?, ?, 'diction', '晚间问候时使用轻松短句', '用轻松短句问候', 0.8,
                        '[]', 'adopted', ?, ?, 'operator', 'adopted', 0)""",
                (signal_id, run_id, self.owner_key, scope_key, stamp(NOW), stamp(NOW)))

    def record(self, connection, owner, scope, signal_ids, at):
        style_rotation.record(self.runtime, owner_key=owner, scope_key=scope,
                              signal_ids=signal_ids, at=at, connection=connection)

    def deliver(self, batch_id, at):
        feedback.stage_injection(self.runtime, batch_id=batch_id, owner_key=self.owner_key,
            scope_key=self.scope_key, signal_ids=(self.signal_id,), at=at - timedelta(seconds=1))
        with self.runtime.transaction() as connection:
            self.style_context.confirm_delivery(connection, batch_id=batch_id, context=self.context,
                                                delivered_at=at)

    def consume(self, text, at, *, owner_key=None, scope_key=None, channel="private"):
        with self.runtime.transaction() as connection:
            return feedback.consume_feedback(connection, owner_key=owner_key or self.owner_key,
                scope_key=scope_key or self.scope_key, channel=channel, text=text, received_at=at)

    def test_only_a_confirmed_delivery_starts_feedback_and_rotation(self):
        feedback.stage_injection(self.runtime, batch_id="reply-unsent", owner_key=self.owner_key,
            scope_key=self.scope_key, signal_ids=(self.signal_id,), at=NOW)
        self.assertEqual(style_rotation.usage(self.runtime, self.owner_key, self.scope_key), {})
        self.assertEqual(self.consume("别这么说", NOW + timedelta(seconds=1)), {})
        self.assertEqual(feedback.usage(self.runtime, self.owner_key, self.scope_key), {})

        self.deliver("reply-sent", NOW)
        self.assertEqual(style_rotation.usage(self.runtime, self.owner_key, self.scope_key)[self.signal_id][0], 1)
        self.assertEqual(self.consume("今天先聊点别的", NOW + timedelta(seconds=1)), {})
        self.assertEqual(feedback.usage(self.runtime, self.owner_key, self.scope_key), {})

    def test_two_clear_negative_feedbacks_return_rule_to_review(self):
        self.deliver("reply-one", NOW)
        first = self.consume("别这么说", NOW + timedelta(seconds=1))
        self.assertEqual((first["signal"], first["updated_rules"], first["demoted_rules"]),
                         ("negative", 1, 0))
        self.assertEqual(self.runtime.read_one("SELECT state FROM expression_style_signals WHERE signal_id = ?",
                                               (self.signal_id,))["state"], "adopted")

        self.deliver("reply-two", NOW + timedelta(minutes=1))
        second = self.consume("这个语气怪怪的，别这样说", NOW + timedelta(minutes=1, seconds=1))
        self.assertEqual((second["signal"], second["updated_rules"], second["demoted_rules"]),
                         ("negative", 1, 1))
        self.assertEqual(self.runtime.read_one("SELECT state FROM expression_style_signals WHERE signal_id = ?",
                                               (self.signal_id,))["state"], "pending")
        usage = feedback.usage(self.runtime, self.owner_key, self.scope_key)[self.signal_id]
        self.assertEqual((usage["negative_count"], usage["positive_count"]), (2, 0))
        self.assertIn("退回复审", usage["review_reason"])

    def test_duplicate_receipts_do_not_count_the_same_rule_twice(self):
        self.deliver("reply-segmented", NOW)
        with self.runtime.transaction() as connection:
            feedback.confirm_delivery(connection, batch_id="reply-segmented", owner_key=self.owner_key,
                scope_key=self.scope_key, delivered_at=NOW + timedelta(seconds=1),
                rotation_record=self.record)
        self.assertEqual(style_rotation.usage(self.runtime, self.owner_key, self.scope_key)[self.signal_id][0], 1)

    def test_feedback_is_scoped_to_the_same_role_and_conversation(self):
        self.deliver("reply-scoped", NOW)
        self.assertEqual(self.consume("别这么说", NOW + timedelta(seconds=1), scope_key="other-scope"), {})
        self.assertEqual(feedback.usage(self.runtime, self.owner_key, self.scope_key), {})

    def test_a_rule_outside_the_conversation_family_never_takes_its_feedback(self):
        # Another participant's rule is a different relationship; a delivery
        # receipt that names it anyway must not turn this conversation's
        # complaint into its review history.
        other = ScopeRef(INSTALLATION, "onebot", ScopeKind.PRIVATE, "native-b", "bot-a", "login-a",
                         "conversation-b", "session-b")
        other_key = digest(other)
        other_signal = "expression-run:feedback:signal:foreign"
        self.write_rule(other_key, run_id="expression-run:feedback-foreign", signal_id=other_signal)

        feedback.stage_injection(self.runtime, batch_id="reply-family", owner_key=self.owner_key,
            scope_key=self.scope_key, signal_ids=(self.signal_id, other_signal), at=NOW)
        with self.runtime.transaction() as connection:
            feedback.confirm_delivery(connection, batch_id="reply-family", owner_key=self.owner_key,
                scope_key=self.scope_key, family_scopes=lambda foreign: (self.scope_key,),
                delivered_at=NOW + timedelta(seconds=1), rotation_record=self.record)
        consumed = self.consume("别这么说", NOW + timedelta(seconds=2))
        self.assertEqual((consumed["signal"], consumed["updated_rules"], consumed["demoted_rules"]),
                         ("negative", 1, 0))
        self.assertEqual(feedback.usage(self.runtime, self.owner_key, other_key), {})
        self.assertEqual(self.runtime.read_one(
            "SELECT state FROM expression_style_signals WHERE signal_id = ?", (other_signal,))["state"],
            "adopted")
        self.assertEqual(feedback.usage(self.runtime, self.owner_key, self.scope_key)
                         [self.signal_id]["negative_count"], 1)

    def test_a_batch_inside_one_window_reads_no_conversation_family(self):
        # Injection already paid the family read for this turn; an ordinary
        # delivery of rules learned in the answered window must not scan the
        # conversation tables again.
        resolved = []

        def family_scopes(foreign):
            resolved.append(foreign)
            return (self.scope_key,)

        feedback.stage_injection(self.runtime, batch_id="reply-local", owner_key=self.owner_key,
            scope_key=self.scope_key, signal_ids=(self.signal_id,), at=NOW)
        with self.runtime.transaction() as connection:
            feedback.confirm_delivery(connection, batch_id="reply-local", owner_key=self.owner_key,
                scope_key=self.scope_key, family_scopes=family_scopes,
                delivered_at=NOW + timedelta(seconds=1), rotation_record=self.record)
        self.assertEqual(resolved, [])
        self.assertEqual(self.consume("别这么说", NOW + timedelta(seconds=2))["updated_rules"], 1)

    def test_a_family_read_failure_keeps_the_delivery_receipt_and_rotation(self):
        # The receipt is written in the caller's transaction together with the
        # rotation record, so a family read that escaped would roll back a
        # delivered reply's accounting. The failure is contained inside the
        # receipt instead: the answered window's rule is still taken, the
        # rotation record written next to it survives, and a rule from another
        # conversation stays out of this feedback window (fail-closed).
        local_signal = "expression-run:feedback:signal:local"
        foreign_scope = digest(ScopeRef(INSTALLATION, "onebot", ScopeKind.PRIVATE, "native-b",
                                        "bot-a", "login-a", "conversation-b", "session-b"))
        foreign_signal = "expression-run:feedback:signal:foreign"
        self.write_rule(self.scope_key, run_id="expression-run:feedback-local", signal_id=local_signal)
        self.write_rule(foreign_scope, run_id="expression-run:feedback-foreign",
                        signal_id=foreign_signal)

        calls = []

        def broken_family(foreign):
            calls.append(foreign)
            raise RuntimeError("conversation table unavailable during delivery")

        at = NOW + timedelta(minutes=1)
        feedback.stage_injection(self.runtime, batch_id="reply-broken-family", owner_key=self.owner_key,
            scope_key=self.scope_key, signal_ids=(local_signal, foreign_signal), at=at)
        with self.runtime.transaction() as connection:
            with self.assertLogs("companion_v2.expression.feedback", level="WARNING") as captured:
                feedback.confirm_delivery(connection, batch_id="reply-broken-family",
                    owner_key=self.owner_key, scope_key=self.scope_key,
                    family_scopes=broken_family, delivered_at=at, rotation_record=self.record)

        # The callback really ran and the failure reached the log as its type.
        self.assertEqual(calls, [{foreign_scope}])
        self.assertTrue(any("RuntimeError" in line and "family unavailable" in line
                            for line in captured.output), captured.output)
        # The rotation record of the same transaction survived, under each
        # rule's own scope.
        self.assertEqual(style_rotation.usage(self.runtime, self.owner_key,
                         self.scope_key)[local_signal][0], 1)
        self.assertEqual(style_rotation.usage(self.runtime, self.owner_key,
                         foreign_scope)[foreign_signal][0], 1)
        # Only the answered window's rule entered the feedback window ...
        consumed = self.consume("别这么说", at + timedelta(seconds=1))
        self.assertEqual((consumed["signal"], consumed["updated_rules"], consumed["demoted_rules"]),
                         ("negative", 1, 0))
        self.assertEqual(set(feedback.usage(self.runtime, self.owner_key, self.scope_key)),
                         {local_signal})
        self.assertEqual(feedback.usage(self.runtime, self.owner_key, self.scope_key)
                         [local_signal]["negative_count"], 1)
        # ... so the other conversation's rule neither hears this feedback nor
        # leaves its admitted state.
        self.assertEqual(feedback.usage(self.runtime, self.owner_key, foreign_scope), {})
        self.assertEqual(self.runtime.read_one(
            "SELECT state FROM expression_style_signals WHERE signal_id = ?", (foreign_signal,))["state"],
            "adopted")

    def test_classifier_requires_a_direct_expression_reference(self):
        self.assertEqual(feedback.classify("这个语气很自然，我喜欢"), "positive")
        self.assertEqual(feedback.classify("别学我这样说"), "negative")
        self.assertEqual(feedback.classify("哈哈，今天挺开心"), "")


if __name__ == "__main__":
    unittest.main()
