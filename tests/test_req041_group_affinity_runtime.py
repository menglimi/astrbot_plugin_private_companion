from __future__ import annotations

from pathlib import Path
import json
import tempfile
import unittest

from identity_namespace import NamespaceContext
from relationship_account_store import RelationshipAccountStore
from relationship_affinity_runtime import (
    admit_confirmed_group_affinity,
    normalize_group_allowlist,
    prepare_group_affinity_candidate,
)
from tests.module_source_index import main_source_text, file_family_source_text


EPOCH = "req041-affinity-runtime-test"
PERSON = "person_aaaaaaaaaaaaaaaaaaaaaaaa"


def context(group_id: str = "group@opaque-a") -> NamespaceContext:
    return NamespaceContext(
        kind="group_member",
        persona_id="persona_default",
        identity_id=PERSON,
        group_id=group_id,
        assurance="verified",
        profile_status="active",
        policy_version="req041-v1",
        migration_epoch=EPOCH,
    )


class GroupAffinityRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = RelationshipAccountStore(
            Path(self.tmp.name) / "relationship.sqlite3",
            active_migration_epoch=EPOCH,
            clock=lambda: 1_786_291_200.0,
        )
        private = NamespaceContext(
            kind="private", persona_id="persona_default", identity_id=PERSON,
            group_id="",
            assurance="verified", profile_status="active",
            policy_version="req041-v1", migration_epoch=EPOCH,
        )
        self.store.create_account(private, operation_id="create", actor="migration")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    @staticmethod
    def candidate(**changes):
        values = {
            "raw_group_id": "10001",
            "allowlist": ["10001"],
            "enabled": True,
            "inbound_event_id": "message-1",
            "directed_by": "at_bot",
            "legacy_user_key": "user-1",
            "inbound": True,
            "human_sender": True,
            "forwarded": False,
            "echo": False,
            "historical": False,
        }
        values.update(changes)
        return prepare_group_affinity_candidate(context(), **values)

    def test_allowlist_is_explicit_and_empty_never_means_all(self) -> None:
        self.assertEqual(frozenset({"1", "2", "3"}), normalize_group_allowlist("1, 2；3"))
        self.assertIsNone(self.candidate(allowlist=[]))
        self.assertIsNone(self.candidate(allowlist=["another"]))
        self.assertIsNone(self.candidate(enabled=False))

    def test_candidate_rejects_non_direct_or_untrusted_sources(self) -> None:
        for changes in (
            {"directed_by": "bot_name"},
            {"inbound": False},
            {"human_sender": False},
            {"forwarded": True},
            {"echo": True},
            {"historical": True},
            {"inbound_event_id": ""},
        ):
            with self.subTest(changes=changes):
                self.assertIsNone(self.candidate(**changes))

    def test_only_confirmed_reply_reserves_once_and_does_not_settle_score(self) -> None:
        candidate = self.candidate()
        self.assertIsNotNone(candidate)
        self.assertIsNone(
            admit_confirmed_group_affinity(candidate, self.store, reply_succeeded=False)
        )
        admitted = admit_confirmed_group_affinity(
            candidate, self.store, reply_succeeded=True,
        )
        replay = admit_confirmed_group_affinity(
            candidate, self.store, reply_succeeded=True,
        )
        self.assertEqual(admitted, replay)
        self.assertEqual(1, admitted.admitted_delta)
        private = NamespaceContext(
            kind="private", persona_id="persona_default", identity_id=PERSON,
            group_id="",
            assurance="verified", profile_status="active",
            policy_version="req041-v1", migration_epoch=EPOCH,
        )
        self.assertEqual(0, self.store.account(private)["relationship_score"])

    def test_event_id_is_opaque_stable_and_namespace_bound(self) -> None:
        first = self.candidate()
        replay = self.candidate()
        other_group = prepare_group_affinity_candidate(
            context("group@opaque-b"), raw_group_id="10002", allowlist=["10002"],
            enabled=True, inbound_event_id="message-1", directed_by="at_bot",
            legacy_user_key="user-1", inbound=True, human_sender=True,
            forwarded=False, echo=False, historical=False,
        )
        self.assertEqual(first["event_id"], replay["event_id"])
        self.assertNotEqual(first["event_id"], other_group["event_id"])
        self.assertNotIn("message-1", first["event_id"])
        self.assertNotIn("10001", repr(first["context"]))

    def test_production_wiring_requires_new_read_and_confirmed_delivery(self) -> None:
        root = Path(__file__).resolve().parents[1]
        main_source = main_source_text(root)
        pipeline_source = file_family_source_text(root, "message_pipeline.py")
        self.assertIn('req041_read_generation", "") or "") != "new"', main_source)
        self.assertIn("_reaction_expression_primary_reply_confirmed", main_source)
        self.assertIn("require_segmented_complete=True", main_source)
        self.assertIn("@filter.after_message_sent(priority=-105000)", main_source)
        self.assertIn("_req041_prepare_group_affinity_candidate", pipeline_source)
        self.assertIn('scene_trigger in {"at_bot", "reply_bot"}', pipeline_source)
        self.assertIn("not group_reference_media_with_text", pipeline_source)
        self.assertIn("group_affinity_config_revoked", main_source)

    def test_schema_defaults_keep_production_closed(self) -> None:
        root = Path(__file__).resolve().parents[1]
        schema = json.loads((root / "_conf_schema.json").read_text(encoding="utf-8"))
        relationship = schema["basic_config"]["items"]
        self.assertIs(False, relationship["enable_group_relationship_affinity"]["default"])
        self.assertEqual([], relationship["group_relationship_affinity_allowlist"]["default"])


if __name__ == "__main__":
    unittest.main()
