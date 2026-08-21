from __future__ import annotations

import ast
import copy
from pathlib import Path
import tempfile
import unittest
from typing import Any

from migration_coordinator import MigrationCoordinator
from migration_dual_write import MigrationDualWriteProducer
from migration_outbox import MigrationOutbox, StaleMigrationEpoch
from persona_config import runtime_persona_setting
from relationship_ledger import apply_relationship_event, migrate_legacy_relationship_score
from unified_person_registry import UnifiedPersonRegistry


EPOCH_POLICY = "req041-v1"
ROOT = Path(__file__).resolve().parents[1]


def _load_relationship_writer():
    tree = ast.parse((ROOT / "core_store.py").read_text(encoding="utf-8"))
    owner = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "CoreStoreMixin")
    method = next(node for node in owner.body if isinstance(node, ast.FunctionDef) and node.name == "_apply_relationship_event")
    module = ast.Module(body=[copy.deepcopy(method)], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {
        "Any": Any,
        "apply_relationship_event": apply_relationship_event,
        "migrate_legacy_relationship_score": migrate_legacy_relationship_score,
        "runtime_persona_setting": runtime_persona_setting,
        "logger": type("Logger", (), {"warning": staticmethod(lambda *_args, **_kwargs: None)})(),
        "_single_line": lambda value, limit=160: " ".join(str(value or "").split())[:limit],
    }
    exec(compile(module, str(ROOT / "core_store.py"), "exec"), namespace)
    return namespace["_apply_relationship_event"]


APPLY_LIVE_RELATIONSHIP = _load_relationship_writer()


def _identity(subject: str = "10001") -> dict[str, str]:
    return {
        "companion_instance_id": "astrbot_plugin_private_companion",
        "bot_account_id": "onebot:bot-1",
        "adapter_instance_id": "onebot:default",
        "subject_namespace": "onebot:user",
        "platform_subject_id": subject,
    }


def _result(event_key: str = "a" * 24, *, before: int = 10, after: int = 12) -> dict:
    return {
        "changed": True,
        "code": "applied",
        "score": after,
        "delta": after - before,
        "entry": {
            "event_key": event_key,
            "reason_code": "inbound",
            "delta": after - before,
            "score_before": before,
            "score_after": after,
            "created_at": 1_700_000_000,
        },
    }


class MigrationDualWriteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name)
        source = self.data_dir / "companions.json"
        source.write_text('{"users":{}}', encoding="utf-8")
        self.coordinator = MigrationCoordinator(self.data_dir)
        status = self.coordinator.start_or_resume(
            source_files=[source],
            policy_version=EPOCH_POLICY,
            source_schema_version="legacy-effective",
            target_schema_version="req041-v1",
            companion_version="6.1.1",
            memory_version="1.7.2",
            reserve_bytes=0,
        )
        self.coordinator.capture_compatibility({})
        self.coordinator.transition("S3", checkpoint="outbox_active")
        self.coordinator.transition("S4", checkpoint="backfill_active")
        self.epoch = status["migration_epoch"]
        self.outbox = MigrationOutbox(self.data_dir / "outbox.db")
        self.outbox.begin_epoch(self.epoch, policy_version=EPOCH_POLICY)
        self.producer = MigrationDualWriteProducer(
            outbox=self.outbox,
            coordinator=self.coordinator,
            migration_epoch=self.epoch,
            policy_version=EPOCH_POLICY,
        )
        self.store: dict = {}
        self.registry = UnifiedPersonRegistry(self.store)
        created = self.registry.create_or_link(_identity(), operation_id="fixture-create")
        self.person_id = created["person_id"]
        self.user = {
            "user_id": "10001",
            "unified_person_id": self.person_id,
            "relationship_role": "friend",
            "relationship_mode": "normal",
            "relationship_score": 12,
            "relationship_positive_stage_cap_key": "close",
            "relationship_daily_totals": {"day": "2026-08-10", "positive": 2, "negative": 0},
            "relationship_last_effective_at": 1_700_000_000,
        }

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_changed_legacy_event_enqueues_redacted_revisioned_payload(self) -> None:
        first = self.producer.emit_relationship(
            registry=self.registry,
            user=self.user,
            requested_delta=2,
            reason_code="inbound",
            result=_result(),
            source_revision=1,
        )
        replay = self.producer.emit_relationship(
            registry=self.registry,
            user=self.user,
            requested_delta=2,
            reason_code="inbound",
            result=_result(),
            source_revision=1,
        )
        item = self.outbox.pending(self.epoch)[0]
        self.assertEqual({"status": "enqueued", "source_revision": 1}, first)
        self.assertEqual({"status": "duplicate", "source_revision": 1}, replay)
        self.assertEqual("relationship_legacy_event", item.payload["operation"])
        self.assertEqual(self.person_id, item.payload["identity_ref"])
        self.assertEqual(2, item.payload["applied_delta"])
        self.assertEqual({"day": "2026-08-10", "positive": 2, "negative": 0}, item.payload["daily_totals"])
        self.assertNotIn("10001", str(item.payload))
        self.assertNotIn("created_at", item.payload)

    def test_successful_enqueue_notifies_replay_scheduler_once(self) -> None:
        calls: list[str] = []
        producer = MigrationDualWriteProducer(
            outbox=self.outbox, coordinator=self.coordinator,
            migration_epoch=self.epoch, policy_version=EPOCH_POLICY,
            on_enqueued=lambda: calls.append("scheduled"),
        )
        producer.emit_relationship(
            registry=self.registry, user=self.user, requested_delta=2,
            reason_code="inbound", result=_result(), source_revision=1,
        )
        producer.emit_relationship(
            registry=self.registry, user=self.user, requested_delta=2,
            reason_code="inbound", result=_result(), source_revision=1,
        )
        self.assertEqual(["scheduled"], calls)

    def test_runtime_totals_are_preserved_exactly_and_invalid_values_fail(self) -> None:
        user = {
            **self.user,
            "relationship_daily_totals": {"day": "2026-08-10", "positive": 2, "negative": -132},
        }
        self.producer.emit_relationship(
            registry=self.registry, user=user, requested_delta=-12,
            reason_code="boundary_violation",
            result=_result(before=12, after=0), source_revision=1,
        )
        self.assertEqual(-132, self.outbox.pending(self.epoch)[0].payload["daily_totals"]["negative"])
        invalid = {
            **self.user,
            "relationship_daily_totals": {"day": "2026-08-10", "positive": 999, "negative": 0},
        }
        with self.assertRaisesRegex(Exception, "dual_write_relationship_runtime_invalid"):
            self.producer.emit_relationship_snapshot(
                registry=self.registry, user=invalid,
                reason_code="administrator_relationship_update", source_revision=2,
            )

    def test_two_distinct_legacy_events_receive_contiguous_revisions(self) -> None:
        self.producer.emit_relationship(
            registry=self.registry, user=self.user, requested_delta=2,
            reason_code="inbound", result=_result("a" * 24), source_revision=1,
        )
        second = self.producer.emit_relationship(
            registry=self.registry, user=self.user, requested_delta=-1,
            reason_code="boundary_violation", result=_result("b" * 24, before=12, after=11), source_revision=2,
        )
        self.assertEqual(2, second["source_revision"])
        self.assertEqual([1, 2], [item.source_revision for item in self.outbox.pending(self.epoch)])

    def test_unlinked_user_is_pending_and_not_enqueued(self) -> None:
        result = self.producer.emit_relationship(
            registry=self.registry,
            user={"user_id": "unlinked-user"},
            requested_delta=2,
            reason_code="inbound",
            result=_result(),
            source_revision=1,
        )
        self.assertEqual("relationship_identity_pending", result["code"])
        self.assertEqual([], self.outbox.pending(self.epoch))
        self.assertEqual(1, self.coordinator.pending_summary()["total"])

    def test_group_observation_cannot_write_global_relationship_stream(self) -> None:
        user = {**self.user, "observation_only": True, "profile_origin": "group_observation"}
        result = self.producer.emit_relationship(
            registry=self.registry, user=user, requested_delta=2,
            reason_code="inbound", result=_result(), source_revision=1,
        )
        self.assertEqual("group_observation_relationship_denied", result["code"])
        self.assertEqual([], self.outbox.pending(self.epoch))

    def test_corrupt_user_to_person_pointer_cannot_write_other_relationship(self) -> None:
        user = {**self.user, "user_id": "different-user"}
        result = self.producer.emit_relationship(
            registry=self.registry, user=user, requested_delta=2,
            reason_code="inbound", result=_result(), source_revision=1,
        )
        self.assertEqual("relationship_identity_subject_mismatch", result["code"])
        self.assertEqual([], self.outbox.pending(self.epoch))

    def test_relationship_snapshot_captures_manual_role_mode_score_without_ledger_body(self) -> None:
        user = {
            **self.user,
            "relationship_role": "owner",
            "relationship_mode": "normal",
            "relationship_score": 777,
            "relationship_positive_stage_cap_key": "close",
            "relationship_ledger": [{
                "event_key": "c" * 24,
                "reason_code": "administrator_manual_relationship_adjustment",
                "delta": 765,
                "score_after": 777,
                "created_at": 1_700_000_000,
            }],
        }
        emitted = self.producer.emit_relationship_snapshot(
            registry=self.registry,
            user=user,
            reason_code="administrator_relationship_update",
            source_revision=1,
        )
        payload = self.outbox.pending(self.epoch)[0].payload
        self.assertEqual({"status": "enqueued", "source_revision": 1}, emitted)
        self.assertEqual("relationship_legacy_snapshot", payload["operation"])
        self.assertEqual("owner", payload["relationship_role"])
        self.assertEqual(777, payload["relationship_score"])
        self.assertNotIn("relationship_ledger", payload)
        self.assertNotIn("created_at", payload)

    def test_fail_closed_pauses_cutover_but_does_not_reopen_closed_epoch(self) -> None:
        self.outbox.set_epoch_state(self.epoch, "verified", checkpoint="closed")
        with self.assertRaises(StaleMigrationEpoch):
            self.producer.emit_relationship(
                registry=self.registry, user=self.user, requested_delta=2,
                reason_code="inbound", result=_result(), source_revision=1,
            )
        self.producer.fail_closed("relationship_dual_write_failed")
        self.assertEqual("paused", self.coordinator.status()["state"])
        self.assertEqual("verified", self.outbox.epoch_status(self.epoch)["state"])

    def test_identity_create_event_contains_only_stable_refs_and_revision(self) -> None:
        second_store: dict = {}
        second_registry = UnifiedPersonRegistry(second_store)
        created = second_registry.create_or_link(
            _identity("sensitive-subject-id"),
            operation_id="raw-admin-operation",
        )
        emitted = self.producer.emit_identity_change(
            registry=second_registry,
            result=created,
            action="create",
            operation_id="raw-admin-operation",
        )
        item = self.outbox.pending(self.epoch)[0]
        self.assertEqual({"status": "enqueued", "source_revision": 1}, emitted)
        self.assertEqual("identity_create", item.payload["operation"])
        self.assertEqual(1, item.payload["projection_revision"])
        self.assertNotIn("sensitive-subject-id", str(item.payload))
        self.assertNotIn("raw-admin-operation", item.event_id)
        registered = self.coordinator.identity_status(created["person_id"])
        self.assertEqual("verified", registered["assurance"])
        self.assertEqual("legacy", registered["read_generation"])

    def test_identity_link_replay_is_idempotent(self) -> None:
        linked = self.registry.link_identity(
            self.person_id,
            _identity("secondary-subject"),
            operation_id="link-operation",
        )
        first = self.producer.emit_identity_change(
            registry=self.registry, result=linked, action="link", operation_id="link-operation"
        )
        replay = self.producer.emit_identity_change(
            registry=self.registry, result=linked, action="link", operation_id="link-operation"
        )
        self.assertEqual(1, first["source_revision"])
        self.assertEqual("duplicate", replay["status"])
        self.assertEqual(1, self.outbox.stream_revision(f"identity:{self.person_id}", self.epoch))
        self.assertEqual("explicit_linked", self.outbox.pending(self.epoch)[0].payload["identity_assurance"])

    def test_identity_unlink_event_and_tombstone_are_atomic(self) -> None:
        secondary = _identity("secondary-subject")
        self.registry.link_identity(self.person_id, secondary, operation_id="link-first")
        unlinked = self.registry.unlink_identity(
            self.person_id,
            secondary,
            operation_id="unlink-operation",
            dry_run=False,
        )
        emitted = self.producer.emit_identity_change(
            registry=self.registry,
            result=unlinked,
            action="unlink",
            operation_id="unlink-operation",
        )
        item = self.outbox.pending(self.epoch)[0]
        tombstone = self.outbox.tombstone(
            f"identity-link:{unlinked['identity_key']}", self.epoch
        )
        self.assertEqual({"status": "enqueued", "source_revision": 1}, emitted)
        self.assertEqual("identity_unlink", item.payload["operation"])
        self.assertEqual(1, tombstone["revision"])
        self.assertEqual("identity_unlink", tombstone["reason_code"])

    def test_live_legacy_relationship_authority_emits_after_success(self) -> None:
        producer = self.producer
        registry = self.registry

        class Host:
            enable_custom_relationship_stage_policy = True
            enable_p4_b_legacy_score_isolation = False
            req041_dual_write_producer = producer
            req041_migration_status = {"state": "active"}
            saved = False
            _apply_relationship_event = APPLY_LIVE_RELATIONSHIP

            @staticmethod
            def _active_unified_person_registry():
                return registry

            @staticmethod
            def _unified_persona_domain():
                return ""

            def _schedule_data_save(self, *_args, **_kwargs):
                self.saved = True

        host = Host()
        user = {
            **self.user,
            "relationship_score": 10,
            "relationship_score_schema_version": 2,
            "relationship_ledger": [],
        }
        result = host._apply_relationship_event(
            user, 2, reason_code="inbound", event_id="raw-message-id", now=1_700_000_000
        )
        self.assertTrue(result["changed"])
        self.assertEqual("enqueued", result["req041_dual_write"])
        self.assertTrue(host.saved)
        self.assertEqual(12, user["relationship_score"])
        self.assertEqual(1, len(self.outbox.pending(self.epoch)))

    def test_dual_write_failure_does_not_rollback_legacy_event(self) -> None:
        producer = self.producer
        registry = self.registry
        self.outbox.set_epoch_state(self.epoch, "verified", checkpoint="closed")

        class Host:
            enable_custom_relationship_stage_policy = True
            enable_p4_b_legacy_score_isolation = False
            req041_dual_write_producer = producer
            req041_migration_status = {"state": "active"}
            saved = False
            _apply_relationship_event = APPLY_LIVE_RELATIONSHIP

            @staticmethod
            def _active_unified_person_registry():
                return registry

            @staticmethod
            def _unified_persona_domain():
                return ""

            def _schedule_data_save(self, *_args, **_kwargs):
                self.saved = True

        host = Host()
        user = {
            **self.user,
            "relationship_score": 10,
            "relationship_score_schema_version": 2,
            "relationship_ledger": [],
        }
        result = host._apply_relationship_event(
            user, 2, reason_code="inbound", event_id="event-after-close", now=1_700_000_000
        )
        self.assertTrue(result["changed"])
        self.assertEqual("failed", result["req041_dual_write"])
        self.assertEqual(12, user["relationship_score"])
        self.assertTrue(host.saved)
        self.assertEqual("paused", self.coordinator.status()["state"])
        self.assertEqual("paused", host.req041_migration_status["state"])

    def test_all_live_identity_entrypoints_call_dual_write_helper(self) -> None:
        main_source = (ROOT / "main.py").read_text(encoding="utf-8")
        page_source = (ROOT / "page_api_users_groups.py").read_text(encoding="utf-8")
        self.assertIn("self._req041_emit_identity_dual_write(", main_source)
        self.assertGreaterEqual(page_source.count("_req041_emit_identity_dual_write"), 2)


if __name__ == "__main__":
    unittest.main()
