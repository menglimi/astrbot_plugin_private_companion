from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest

from identity_namespace import NamespaceContext
from relationship_account_store import (
    RelationshipAccessDenied,
    RelationshipAccountStore,
    RelationshipConflict,
    RelationshipStoreError,
)
from relationship_event_policy import build_group_interaction_proof


EPOCH = "req041-20260810-001"
POLICY = "req041-v1"
IDENTITY = "person_aaaaaaaaaaaaaaaaaaaaaaaa"


def _context(*, kind: str = "private", group_id: str = "", **changes: str) -> NamespaceContext:
    values = {
        "kind": kind,
        "identity_id": IDENTITY,
        "group_id": group_id,
        "assurance": "verified",
        "profile_status": "active",
        "policy_version": POLICY,
        "migration_epoch": EPOCH,
    }
    values.update(changes)
    return NamespaceContext(**values)


class RelationshipAccountStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "relationship.sqlite3"
        self.now = [1_786_291_200.0]
        self.store = RelationshipAccountStore(
            self.path, active_migration_epoch=EPOCH, clock=lambda: self.now[0]
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _create(self, *, role: str = "friend", mode: str = "normal", score: int = 0):
        return self.store.create_account(
            _context(), operation_id="create-account", actor="administrator",
            relationship_role=role, relationship_mode=mode, score=score,
        )

    @staticmethod
    def _group_proof(context: NamespaceContext, event_id: str, **changes):
        values = {
            "event_id": event_id,
            "directed_by": "at_bot",
            "inbound": True,
            "human_sender": True,
            "bot_reply_succeeded": True,
            "forwarded": False,
            "echo": False,
            "historical": False,
        }
        values.update(changes)
        return build_group_interaction_proof(context, **values)

    def test_account_is_identity_global_but_group_summary_is_low_sensitive(self) -> None:
        self._create(score=200)
        private = self.store.summary(_context())
        group_a = self.store.summary(_context(kind="group_member", group_id="group-a"))
        group_b = self.store.summary(_context(kind="group_member", group_id="group-b"))
        self.assertEqual(private["revision"], group_a["revision"])
        self.assertEqual(group_a, group_b)
        self.assertEqual(200, private["score"])
        self.assertNotIn("score", group_a)
        self.assertNotIn("sources", group_a)
        self.assertNotIn("ledger", group_a)
        with self.assertRaisesRegex(RelationshipAccessDenied, "relationship_detail_private_only"):
            self.store.account(_context(kind="group_member", group_id="group-a"))
        with self.assertRaisesRegex(RelationshipAccessDenied, "relationship_audit_private_only"):
            self.store.audit_events(_context(kind="group_member", group_id="group-a"))

    def test_owner_normal_bypasses_ordinary_positive_stage_cap(self) -> None:
        self._create(role="owner", score=899)
        result = self.store.apply_event(
            _context(), event_id="owner-positive", actor="private_pipeline",
            reason_code="inbound", delta=4, positive_stage_cap_key="close",
        )
        self.assertTrue(result.applied)
        # Official v2 ledger dampens positive events once score >= 600.
        self.assertEqual(902, result.score)
        self.assertEqual(2, result.account_revision)

    def test_friend_remains_bounded_by_positive_stage_cap(self) -> None:
        self._create(score=899)
        result = self.store.apply_event(
            _context(), event_id="friend-positive", actor="private_pipeline",
            reason_code="inbound", delta=4, positive_stage_cap_key="close",
        )
        self.assertFalse(result.applied)
        self.assertEqual("positive_stage_cap", result.code)
        self.assertEqual(899, result.score)
        self.assertEqual(1, result.account_revision)

    def test_owner_exclusive_freezes_automatic_settlement(self) -> None:
        self._create(role="owner", mode="owner_exclusive", score=600)
        result = self.store.apply_event(
            _context(), event_id="exclusive-positive", actor="private_pipeline",
            reason_code="support", delta=4,
        )
        self.assertFalse(result.applied)
        self.assertEqual("owner_exclusive_frozen", result.code)
        self.assertEqual(600, result.score)

    def test_ordinary_group_and_group_violation_never_change_global_account(self) -> None:
        self._create(score=300)
        group = _context(kind="group_member", group_id="group-a")
        ordinary = self.store.apply_event(
            group, event_id="group-ordinary", actor="group_pipeline", reason_code="group_inbound", delta=4,
        )
        violation = self.store.apply_event(
            group, event_id="group-violation", actor="group_pipeline",
            reason_code="relationship_violation", delta=-12,
        )
        self.assertFalse(ordinary.applied)
        self.assertFalse(violation.applied)
        self.assertEqual("group_global_settlement_disabled", ordinary.code)
        self.assertEqual(300, self.store.account(_context())["relationship_score"])

    def test_explicit_group_affinity_is_weighted_and_budgeted_per_group(self) -> None:
        self._create()
        group_a = _context(kind="group_member", group_id="group-a")
        group_b = _context(kind="group_member", group_id="group-b")
        first = self.store.apply_event(
            group_a, event_id="group-a-1", actor="group_pipeline", reason_code="direct_group_interaction",
            delta=4, weight=1.0, allow_group_affinity=True,
            group_interaction_proof=self._group_proof(group_a, "group-a-1"),
        )
        second = self.store.apply_event(
            group_a, event_id="group-a-2", actor="group_pipeline", reason_code="direct_group_interaction",
            delta=8, weight=1.0, allow_group_affinity=True,
            group_interaction_proof=self._group_proof(group_a, "group-a-2"),
        )
        third = self.store.apply_event(
            group_b, event_id="group-b-1", actor="group_pipeline", reason_code="direct_group_interaction",
            delta=4, weight=1.0, allow_group_affinity=True,
            group_interaction_proof=self._group_proof(group_b, "group-b-1"),
        )
        self.assertEqual((1, 0, 1), (first.applied_delta, second.applied_delta, third.applied_delta))
        self.assertEqual("group_affinity_budget_exhausted", second.code)
        self.assertEqual(2, self.store.account(_context())["relationship_score"])

    def test_group_proof_is_bound_to_event_and_rejects_forward_echo_or_failed_reply(self) -> None:
        self._create()
        group = _context(kind="group_member", group_id="group-a")
        cases = (
            ("missing", None, "group_interaction_proof_invalid"),
            ("wrong-binding", self._group_proof(group, "another-event"), "group_interaction_proof_binding_mismatch"),
            ("forward", self._group_proof(group, "forward", forwarded=True), "group_interaction_source_denied"),
            ("echo", self._group_proof(group, "echo", echo=True), "group_interaction_source_denied"),
            ("failed", self._group_proof(group, "failed", bot_reply_succeeded=False), "group_interaction_proof_incomplete"),
        )
        for event_id, proof, expected in cases:
            with self.subTest(event_id=event_id):
                result = self.store.apply_event(
                    group, event_id=event_id, actor="group_pipeline",
                    reason_code="direct_group_interaction", delta=4,
                    allow_group_affinity=True, group_interaction_proof=proof,
                )
                self.assertFalse(result.applied)
                self.assertEqual(expected, result.code)
        self.assertEqual(0, self.store.account(_context())["relationship_score"])

    def test_group_admission_reserves_budget_without_changing_account(self) -> None:
        self._create(score=20)
        group = _context(kind="group_member", group_id="group-a")
        proof = self._group_proof(group, "reserve-only")
        first = self.store.admit_group_event(
            group, event_id="reserve-only", delta=4,
            allow_group_affinity=True, group_interaction_proof=proof,
        )
        replay = self.store.admit_group_event(
            group, event_id="reserve-only", delta=4,
            allow_group_affinity=True, group_interaction_proof=proof,
        )
        self.assertEqual(first, replay)
        self.assertEqual(1, first.admitted_delta)
        self.assertEqual("group_affinity_admitted", first.code)
        self.assertEqual(20, self.store.account(_context())["relationship_score"])
        self.assertEqual([], self.store.audit_events(_context()))
        with self.assertRaisesRegex(RelationshipConflict, "group_affinity_admission_conflict"):
            self.store.admit_group_event(
                group, event_id="reserve-only", delta=-4,
                allow_group_affinity=True,
                group_interaction_proof=self._group_proof(group, "reserve-only"),
            )

    def test_group_absolute_budgets_stop_sign_churn_and_cross_group_spam(self) -> None:
        self._create()
        group_a = _context(kind="group_member", group_id="group-a")
        group_b = _context(kind="group_member", group_id="group-b")

        def settle(context, event_id, delta, **budgets):
            return self.store.apply_event(
                context, event_id=event_id, actor="group_pipeline",
                reason_code="direct_group_interaction", delta=delta,
                allow_group_affinity=True,
                group_interaction_proof=self._group_proof(context, event_id),
                **budgets,
            )

        first = settle(group_a, "churn-positive", 4, group_window_absolute_cap=4)
        negative = settle(group_a, "churn-negative", -4, group_window_absolute_cap=1)
        self.assertEqual(1, first.applied_delta)
        self.assertEqual(0, negative.applied_delta)

        self.now[0] += 1900
        second_group = settle(
            group_b, "cross-group-1", 4,
            group_person_daily_absolute_cap=2,
        )
        self.now[0] += 1900
        exhausted = settle(
            group_a, "cross-group-2", 4,
            group_person_daily_absolute_cap=2,
        )
        self.assertEqual(1, second_group.applied_delta)
        self.assertEqual(0, exhausted.applied_delta)
        self.assertEqual("group_affinity_budget_exhausted", exhausted.code)

    def test_group_scope_budget_is_atomic_across_identities(self) -> None:
        identities = [IDENTITY, "person_bbbbbbbbbbbbbbbbbbbbbbbb"]
        self._create()
        self.store.create_account(
            _context(identity_id=identities[1]), operation_id="create-account-b",
            actor="administrator",
        )

        def settle(index: int) -> int:
            identity = identities[index % 2]
            context = _context(
                kind="group_member", group_id="group-shared", identity_id=identity
            )
            event_id = f"group-concurrent-{index}"
            local = RelationshipAccountStore(
                self.path, active_migration_epoch=EPOCH, clock=lambda: self.now[0]
            )
            return local.apply_event(
                context, event_id=event_id, actor="group_pipeline",
                reason_code="direct_group_interaction", delta=4,
                allow_group_affinity=True,
                group_interaction_proof=self._group_proof(context, event_id),
                group_window_absolute_cap=20,
                group_person_daily_absolute_cap=20,
                group_scope_daily_absolute_cap=3,
            ).applied_delta

        with ThreadPoolExecutor(max_workers=8) as pool:
            deltas = list(pool.map(settle, range(20)))
        self.assertEqual(3, sum(abs(value) for value in deltas))
        audits = []
        for identity in identities:
            audits.extend(self.store.audit_events(_context(identity_id=identity), limit=100))
        self.assertEqual(20, len(audits))

    def test_event_is_idempotent_across_restart_and_payload_conflict_fails(self) -> None:
        self._create()
        first = self.store.apply_event(
            _context(), event_id="stable-event", actor="private_pipeline", reason_code="support", delta=2,
        )
        reopened = RelationshipAccountStore(
            self.path, active_migration_epoch=EPOCH, clock=lambda: 1_786_291_260.0
        )
        duplicate = reopened.apply_event(
            _context(), event_id="stable-event", actor="private_pipeline", reason_code="support", delta=2,
        )
        self.assertEqual(first, duplicate)
        with self.assertRaisesRegex(RelationshipConflict, "relationship_event_conflict"):
            reopened.apply_event(
                _context(), event_id="stable-event", actor="private_pipeline", reason_code="support", delta=3,
            )

    def test_legacy_event_replay_requires_exact_before_after_and_is_idempotent(self) -> None:
        self._create(score=10)
        kwargs = {
            "event_id": "legacy-event-1",
            "reason_code": "inbound",
            "requested_delta": 4,
            "applied_delta": 2,
            "score_before": 10,
            "score_after": 12,
            "relationship_role": "friend",
            "relationship_mode": "normal",
            "positive_stage_cap_key": "close",
            "daily_totals": {"day": "2026-08-10", "positive": 2, "negative": 0},
            "last_effective_at": 1_700_000_000,
        }
        first = self.store.replay_legacy_event(_context(), **kwargs)
        replay = self.store.replay_legacy_event(_context(), **kwargs)
        account = self.store.account(_context())
        self.assertEqual(first, replay)
        self.assertEqual(12, account["relationship_score"])
        self.assertEqual(2, account["revision"])
        self.assertEqual(kwargs["daily_totals"], account["relationship_daily_totals"])
        self.assertEqual("migration_replay", account["relationship_ledger"][-1]["source"])
        with self.assertRaisesRegex(RelationshipConflict, "relationship_legacy_event_precondition_failed"):
            self.store.replay_legacy_event(
                _context(),
                **{**kwargs, "event_id": "legacy-event-2", "score_before": 10, "score_after": 11, "applied_delta": 1},
            )

    def test_schedule_adjustment_legacy_event_is_replayable(self) -> None:
        self._create(score=20)
        kwargs = {
            "event_id": "legacy-schedule-adjustment-1",
            "reason_code": "schedule_adjustment",
            "requested_delta": 2,
            "applied_delta": 2,
            "score_before": 20,
            "score_after": 22,
            "relationship_role": "friend",
            "relationship_mode": "normal",
            "positive_stage_cap_key": "close",
            "daily_totals": {"day": "2026-08-10", "positive": 2, "negative": 0},
            "last_effective_at": 1_700_000_000,
        }
        first = self.store.replay_legacy_event(_context(), **kwargs)
        replay = self.store.replay_legacy_event(_context(), **kwargs)
        self.assertEqual(first, replay)
        self.assertEqual(22, self.store.account(_context())["relationship_score"])

    def test_legacy_event_replay_accepts_pipeline_reason_contract(self) -> None:
        for index, reason in enumerate(("schedule_adjustment", "food_feedback"), start=1):
            with self.subTest(reason=reason):
                store = RelationshipAccountStore(
                    self.path.parent / f"relationship-{reason}.db",
                    active_migration_epoch=EPOCH,
                    clock=lambda: 1_700_000_000.0,
                )
                store.create_account(
                    _context(),
                    operation_id=f"create-pipeline-{index}",
                    actor="administrator",
                    relationship_role="friend",
                    score=10,
                )
                store.replay_legacy_event(
                    _context(),
                    event_id=f"legacy-pipeline-{index}",
                    reason_code=reason,
                    requested_delta=1,
                    applied_delta=1,
                    score_before=10,
                    score_after=11,
                    relationship_role="friend",
                    relationship_mode="normal",
                    positive_stage_cap_key="close",
                    daily_totals={"day": "2026-08-27", "positive": 1, "negative": 0},
                    last_effective_at=1_700_000_000,
                )
                account = store.account(_context())
                self.assertEqual(
                    reason,
                    account["relationship_ledger"][-1]["reason_code"],
                )

    def test_legacy_event_replay_still_rejects_unknown_reason(self) -> None:
        self._create(score=10)
        with self.assertRaisesRegex(RelationshipStoreError, "relationship_legacy_event_invalid"):
            self.store.replay_legacy_event(
                _context(),
                event_id="legacy-unknown-reason",
                reason_code="future_unregistered_reason",
                requested_delta=1,
                applied_delta=1,
                score_before=10,
                score_after=11,
                relationship_role="friend",
                relationship_mode="normal",
                positive_stage_cap_key="close",
                daily_totals={"day": "2026-08-27", "positive": 1, "negative": 0},
                last_effective_at=1_700_000_000,
            )

    def test_legacy_snapshot_replay_preserves_owner_mode_runtime_and_revision(self) -> None:
        self._create(score=12)
        kwargs = {
            "operation_id": "legacy-snapshot-1",
            "relationship_role": "owner",
            "relationship_mode": "normal",
            "score": 777,
            "positive_stage_cap_key": "close",
            "daily_totals": {"day": "2026-08-10", "positive": 7, "negative": -2},
            "last_effective_at": 1_700_000_100,
        }
        first = self.store.replay_legacy_snapshot(_context(), **kwargs)
        replay = self.store.replay_legacy_snapshot(_context(), **kwargs)
        self.assertEqual(first, replay)
        self.assertEqual("owner", first["relationship_role"])
        self.assertEqual("normal", first["relationship_mode"])
        self.assertEqual(777, first["relationship_score"])
        self.assertEqual(2, first["revision"])
        self.assertEqual(kwargs["daily_totals"], first["relationship_daily_totals"])

    def test_pending_and_cross_identity_contexts_fail_closed(self) -> None:
        pending = _context(kind="pending", assurance="unverified")
        with self.assertRaisesRegex(RelationshipAccessDenied, "namespace_pending_denied"):
            self.store.create_account(pending, operation_id="pending", actor="administrator")
        self._create()
        with self.assertRaisesRegex(Exception, "relationship_account_missing"):
            self.store.account(_context(identity_id="person_bbbbbbbbbbbbbbbbbbbbbbbb"))

    def test_stale_epoch_is_rejected_by_context_and_on_restart(self) -> None:
        self._create()
        with self.assertRaisesRegex(RelationshipAccessDenied, "relationship_migration_epoch_stale"):
            self.store.account(_context(migration_epoch="old-epoch"))
        with self.assertRaisesRegex(RelationshipConflict, "relationship_store_epoch_mismatch"):
            RelationshipAccountStore(self.path, active_migration_epoch="other-epoch")

    def test_role_and_mode_changes_require_admin_and_expected_revision(self) -> None:
        self._create()
        with self.assertRaisesRegex(RelationshipAccessDenied, "relationship_account_admin_required"):
            self.store.configure_account(
                _context(), operation_id="promote", actor="system", expected_revision=1,
                relationship_role="owner",
            )
        changed = self.store.configure_account(
            _context(), operation_id="promote", actor="administrator", expected_revision=1,
            relationship_role="owner",
        )
        self.assertEqual("owner", changed["relationship_role"])
        self.assertEqual("normal", changed["relationship_mode"])
        self.assertEqual(2, changed["revision"])
        with self.assertRaisesRegex(RelationshipConflict, "relationship_revision_conflict"):
            self.store.configure_account(
                _context(), operation_id="stale-change", actor="administrator", expected_revision=1,
                relationship_mode="owner_exclusive",
            )

    def test_owner_two_mode_transition_preserves_score_and_resumes_from_frozen_value(self) -> None:
        self._create(role="owner", mode="normal", score=800)
        frozen = self.store.configure_account(
            _context(), operation_id="freeze-owner", actor="administrator",
            expected_revision=1, relationship_mode="owner_exclusive",
        )
        self.assertEqual(("owner", "owner_exclusive", 800), (
            frozen["relationship_role"], frozen["relationship_mode"],
            frozen["relationship_score"],
        ))
        blocked = self.store.apply_event(
            _context(), event_id="while-frozen", actor="private_pipeline",
            reason_code="support", delta=4,
        )
        self.assertEqual("owner_exclusive_frozen", blocked.code)
        resumed = self.store.configure_account(
            _context(), operation_id="resume-owner", actor="administrator",
            expected_revision=2, relationship_mode="normal",
        )
        self.assertEqual(("owner", "normal", 800), (
            resumed["relationship_role"], resumed["relationship_mode"],
            resumed["relationship_score"],
        ))
        applied = self.store.apply_event(
            _context(), event_id="after-resume", actor="private_pipeline",
            reason_code="support", delta=4, positive_daily_cap=120,
            positive_stage_cap_key="close",
        )
        # High-score damping still applies, but the ordinary user's stage cap
        # does not: owner normal continues upward from the frozen value.
        self.assertEqual(803, applied.score)
        with self.assertRaisesRegex(RelationshipStoreError, "relationship_account_mode_invalid"):
            self.store.configure_account(
                _context(), operation_id="third-mode", actor="administrator",
                expected_revision=4, relationship_mode="dynamic_owner",
            )

    def test_person_archive_purges_relationship_account_and_prevents_resurrection(self) -> None:
        self._create(score=100)
        self.store.apply_event(
            _context(), event_id="before-archive", actor="private_pipeline",
            reason_code="support", delta=2,
        )
        group = _context(kind="group_member", group_id="group-a")
        self.store.admit_group_event(
            group, event_id="admission-before-archive", delta=4,
            allow_group_affinity=True,
            group_interaction_proof=self._group_proof(group, "admission-before-archive"),
        )
        result = self.store.tombstone_account(
            _context(), operation_id="archive-account-1", reason_code="person_archive",
        )
        self.assertEqual("relationship_account_tombstoned", result["code"])
        self.assertEqual(1, result["event_count"])
        self.assertEqual(1, result["admission_count"])
        self.assertGreaterEqual(result["change_count"], 1)
        self.assertEqual(result, self.store.tombstone_account(
            _context(), operation_id="archive-account-1", reason_code="person_archive",
        ))
        with self.assertRaisesRegex(Exception, "relationship_account_missing"):
            self.store.account(_context())
        with self.assertRaisesRegex(RelationshipConflict, "relationship_account_tombstoned"):
            self.store.create_account(
                _context(), operation_id="recreate-after-archive", actor="administrator", score=100,
            )
        with self.assertRaisesRegex(RelationshipConflict, "relationship_account_tombstoned"):
            self.store.tombstone_account(
                _context(), operation_id="archive-account-2", reason_code="person_archive",
            )

    def test_concurrent_distinct_events_are_settled_atomically(self) -> None:
        self._create()

        def settle(index: int) -> int:
            local = RelationshipAccountStore(
                self.path, active_migration_epoch=EPOCH, clock=lambda: 1_786_291_200.0 + index
            )
            return local.apply_event(
                _context(), event_id=f"concurrent-{index}", actor="private_pipeline",
                reason_code="support", delta=1, positive_daily_cap=120,
            ).applied_delta

        with ThreadPoolExecutor(max_workers=8) as pool:
            deltas = list(pool.map(settle, range(20)))
        account = self.store.account(_context())
        self.assertEqual(20, sum(deltas))
        self.assertEqual(20, account["relationship_score"])
        self.assertEqual(21, account["revision"])

    def test_audit_records_redacted_source_scope_and_no_conversation_content(self) -> None:
        self._create()
        group = _context(kind="group_member", group_id="secret-group-id")
        self.store.apply_event(
            group, event_id="audit-event", actor="group_pipeline", reason_code="group_inbound", delta=1,
        )
        audit = self.store.audit_events(_context())[0]
        self.assertEqual("group_member", audit["source_kind"])
        self.assertNotIn("secret-group-id", str(audit))
        self.assertNotIn("content", audit)
        self.assertNotIn("message", audit)


if __name__ == "__main__":
    unittest.main()
