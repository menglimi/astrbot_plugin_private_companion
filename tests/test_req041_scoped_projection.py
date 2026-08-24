from __future__ import annotations

from copy import deepcopy
import unittest

from migration_scoped_projection import ScopedProjectionSynchronizer
from authoritative_private_memory import AuthoritativePrivateMemoryStore
from expression_scope_ownership import bind_expression_item, bind_expression_profile
from unified_person_registry import UnifiedPersonRegistry


def _identity(subject: str = "10001") -> dict[str, str]:
    return {
        "companion_instance_id": "astrbot_plugin_private_companion",
        "bot_account_id": "onebot:bot-1",
        "adapter_instance_id": "onebot:default",
        "subject_namespace": "onebot:user",
        "platform_subject_id": subject,
    }


class _Remote:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str, str], dict] = {}
        self.group_erase_calls: list[tuple[str, str]] = []
        self.persona_erase_calls: list[tuple[str, str]] = []

    @staticmethod
    def _key(context, kind: str, record_id: str) -> tuple[str, str, str]:
        return context.cache_scope(), kind, record_id

    def read(self, context, *, record_kind: str, record_id: str):
        row = self.rows.get(self._key(context, record_kind, record_id))
        return {"ok": True, "code": "found" if row else "not_found", "record": deepcopy(row)}

    def list_records(self, context, *, record_kind: str, limit: int = 100):
        scope = context.cache_scope()
        records = [deepcopy(row) for (stored_scope, kind, _), row in self.rows.items() if stored_scope == scope and kind == record_kind]
        return {"ok": True, "code": "listed", "records": records[:limit]}

    def upsert(self, context, *, record_kind: str, record_id: str, revision: int, payload: dict, event_id: str):
        key = self._key(context, record_kind, record_id)
        row = self.rows.get(key)
        expected = int(row.get("revision") or 0) + 1 if row else 1
        if revision != expected:
            return {"ok": False, "code": "revision_gap"}
        self.rows[key] = {
            "record_id": record_id, "record_kind": record_kind, "revision": revision,
            "payload": deepcopy(payload), "event_id": event_id,
        }
        return {"ok": True, "code": "updated" if row else "created"}

    def tombstone(self, context, *, record_kind: str, record_id: str, revision: int, event_id: str):
        key = self._key(context, record_kind, record_id)
        row = self.rows.get(key)
        if not row or revision != int(row["revision"]) + 1:
            return {"ok": False, "code": "revision_gap"}
        self.rows.pop(key)
        return {"ok": True, "code": "tombstoned"}

    def tombstone_identity_scopes(self, context, *, operation_id: str, reason_code: str):
        removed = 0
        scopes: set[str] = set()
        for key in list(self.rows):
            scope, _kind, _record_id = key
            if scope == context.cache_scope() or (
                context.identity_id and context.identity_id in str(self.rows[key].get("identity_id") or "")
            ):
                self.rows.pop(key)
                removed += 1
                scopes.add(scope)
        return {
            "ok": True, "state": "ready", "code": "identity_scopes_tombstoned",
            "count": removed, "namespace_count": len(scopes), "reason_code": reason_code,
        }

    def erase_group_scopes(self, context, *, operation_id: str, reason_code: str):
        self.group_erase_calls.append((context.cache_scope(), operation_id))
        return {
            "ok": True, "state": "ready", "code": "group_scopes_erased",
            "count": 2, "namespace_count": 2, "reason_code": reason_code,
        }

    def erase_persona_scopes(self, context, *, operation_id: str, reason_code: str):
        self.persona_erase_calls.append((context.persona_id, operation_id))
        return {
            "ok": True, "state": "ready", "code": "persona_scopes_erased",
            "count": 4, "namespace_count": 4, "reason_code": reason_code,
        }


class ScopedProjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.remote = _Remote()
        self.sync = ScopedProjectionSynchronizer(
            read=self.remote.read, list_records=self.remote.list_records,
            upsert=self.remote.upsert, tombstone=self.remote.tombstone,
            tombstone_identity_scopes=self.remote.tombstone_identity_scopes,
            erase_group_scopes=self.remote.erase_group_scopes,
            erase_persona_scopes=self.remote.erase_persona_scopes,
            migration_epoch="epoch-1", policy_version="req041-v1",
        )
        self.snapshot: dict = {}
        created = UnifiedPersonRegistry(self.snapshot).create_or_link(
            _identity(),
            profile={"display_name": "private-name", "preferred_address": "private-name"},
            operation_id="create-a",
        )
        self.person_id = created["person_id"]
        self.snapshot["users"] = {
            "10001": {
                "user_id": "10001", "identity_subject_id": "10001", "unified_person_id": self.person_id,
                "nickname": "private-name", "style": "private-style",
                "companion_memory": {"items": [{"text": "private-sentinel"}]},
                "dialogue_episodes": [{"summary": "private-episode"}],
                "expression_profile": {
                    "learned_rules": [{"id": "approved", "style": "private-rule"}],
                    "pending_rules": [{"id": "pending", "style": "candidate"}],
                    "samples": ["private-evidence"],
                },
                "relationship_role": "owner", "relationship_score": 99,
            }
        }
        self.snapshot["groups"] = {
            "group-a": {
                "group_id": "group-a", "recent_messages": [{"text": "group-a-sentinel"}],
                "recent_bot_replies": [{"text": "group-a-bot-sentinel"}],
                "group_episodes": [{"summary": "group-a-episode"}],
                "expression_profile": {"learned_rules": [{"id": "ga", "style": "group-a-rule"}]},
                "members": {"10001": {"name": "A-in-group-a", "count": 3, "recent_phrases": ["ga-phrase"]}},
            },
            "group-b": {
                "group_id": "group-b", "recent_messages": [{"text": "group-b-sentinel"}],
                "members": {"10001": {"name": "A-in-group-b", "count": 1, "recent_phrases": ["gb-phrase"]}},
            },
        }

    def test_builds_private_group_shared_and_group_member_without_privilege_projection(self) -> None:
        records, contexts = self.sync.build_records(self.snapshot)
        self.assertTrue(any(item.context.kind == "private" for item in records))
        group_scopes = {item.context.group_id for item in records if item.context.kind == "group_shared"}
        self.assertEqual(2, len(group_scopes))
        member_scopes = {item.context.group_id for item in records if item.context.kind == "group_member"}
        self.assertEqual(group_scopes, member_scopes)
        serialized = str([item.payload for item in records])
        self.assertIn("private-sentinel", serialized)
        self.assertIn("group-a-sentinel", serialized)
        self.assertIn("group-a-bot-sentinel", serialized)
        self.assertIn("group-b-sentinel", serialized)
        self.assertNotIn("relationship_role", serialized)
        self.assertNotIn("relationship_score", serialized)
        self.assertTrue(all(context.persona_id == "default" for context in contexts))

    def test_sync_is_idempotent_updates_and_tombstones_removed_legacy_fields(self) -> None:
        first = self.sync.sync_snapshot(self.snapshot)
        self.assertTrue(first["ok"])
        self.assertGreater(first["created"], 0)
        second = self.sync.sync_snapshot(deepcopy(self.snapshot))
        self.assertTrue(second["ok"])
        self.assertEqual(0, second["created"])
        self.assertEqual(0, second["updated"])
        self.assertEqual(first["records"], second["unchanged"])
        changed = deepcopy(self.snapshot)
        changed["users"]["10001"]["companion_memory"]["items"][0]["text"] = "private-updated"
        third = self.sync.sync_snapshot(changed)
        self.assertEqual(1, third["updated"])
        changed["users"]["10001"].pop("dialogue_episodes")
        fourth = self.sync.sync_snapshot(changed)
        self.assertEqual(1, fourth["cleared"])
        changed["users"]["10001"]["dialogue_episodes"] = [{"summary": "restored"}]
        fifth = self.sync.sync_snapshot(changed)
        self.assertEqual(1, fifth["updated"])

    def test_persona_scopes_are_physically_distinct_and_global_rules_are_not_inferred(self) -> None:
        default_records, _ = self.sync.build_records(self.snapshot, source_scope="default")
        persona_records, _ = self.sync.build_records(self.snapshot, source_scope="persona:custom")
        self.assertNotEqual(default_records[0].context.persona_id, persona_records[0].context.persona_id)
        self.assertFalse(any(item.context.kind == "persona_global" for item in default_records + persona_records))

    def test_explicit_admin_global_rules_project_only_to_current_persona(self) -> None:
        context = self.sync._context(kind="persona_global", persona_id="default")
        rule = bind_expression_item(
            {"id": "global-approved", "style": "persona-global-rule", "evidence_count": 1},
            context, approval_state="approved", approved_by="administrator",
        )
        self.snapshot["_req041_persona_expression_profile"] = bind_expression_profile(
            {"learned_rules": [rule]}, context,
        )
        records, contexts = self.sync.build_records(self.snapshot)
        global_records = [item for item in records if item.context.kind == "persona_global"]
        self.assertEqual(1, len(global_records))
        self.assertEqual("approved", global_records[0].payload["approval_state"])
        self.assertEqual("administrator", global_records[0].payload["approved_by"])
        self.assertFalse(any(item.record_kind == "evidence" for item in global_records))
        self.assertTrue(any(item.kind == "persona_global" for item in contexts))

        other_records, _ = self.sync.build_records(
            {key: deepcopy(value) for key, value in self.snapshot.items() if key != "_req041_persona_expression_profile"},
            source_scope="persona:other",
        )
        self.assertFalse(any(item.context.kind == "persona_global" for item in other_records))

    def test_authoritative_person_private_memory_wins_over_multiple_linked_legacy_rows(self) -> None:
        linked = UnifiedPersonRegistry(self.snapshot).link_identity(
            self.person_id,
            _identity("20002"),
            operation_id="link-second-private-identity",
        )
        self.assertTrue(linked["ok"])
        self.snapshot["users"]["20002"] = {
            "user_id": "20002",
            "identity_subject_id": "20002",
            "unified_person_id": self.person_id,
            "companion_memory": {"items": [{"text": "ambiguous-second-row"}]},
        }
        AuthoritativePrivateMemoryStore(self.snapshot).commit(
            self.person_id,
            {"companion_memory": {"items": [{"text": "canonical-person-memory"}]}},
            expected_revision=0,
            operation_id="canonical-private-memory",
        )
        records, _ = self.sync.build_records(self.snapshot)
        serialized = str([record.payload for record in records])
        self.assertIn("canonical-person-memory", serialized)
        self.assertNotIn("private-sentinel", serialized)
        self.assertNotIn("ambiguous-second-row", serialized)
        memory_records = [
            record for record in records
            if record.context.kind == "private" and record.record_kind == "memory"
        ]
        self.assertTrue(memory_records)
        self.assertTrue(all(record.payload["source_revision"] == 1 for record in memory_records))

    def test_rejected_and_revoked_rules_are_audited_but_never_projected_for_runtime(self) -> None:
        profile = self.snapshot["users"]["10001"]["expression_profile"]
        profile["rejected_rules"] = [{"id": "rejected", "style": "must-not-run"}]
        profile["revoked_rules"] = [{"id": "revoked", "style": "must-not-run"}]
        records, _ = self.sync.build_records(self.snapshot)
        archived = [
            item for item in records
            if item.record_kind == "rule" and item.payload.get("approval_state") in {"rejected", "revoked"}
        ]
        self.assertEqual({"rejected", "revoked"}, {item.payload["approval_state"] for item in archived})
        self.assertTrue(all(item.payload["source_revision"] >= 1 for item in archived))
        self.assertTrue(self.sync.sync_snapshot(self.snapshot)["ok"])
        private = next(item.context for item in records if item.context.kind == "private")
        expression = self.sync.read_projection(private)["fields"]["expression_profile"]
        self.assertEqual(["approved"], [item["id"] for item in expression["learned_rules"]])
        self.assertEqual(["pending"], [item["id"] for item in expression["pending_rules"]])
        self.assertNotIn("rejected_rules", expression)
        self.assertNotIn("revoked_rules", expression)

    def test_persona_switch_reads_only_the_selected_ready_namespace(self) -> None:
        main_snapshot = deepcopy(self.snapshot)
        alt_snapshot = deepcopy(self.snapshot)
        main_snapshot["users"]["10001"]["nickname"] = "main-persona-name"
        alt_snapshot["users"]["10001"]["nickname"] = "alt-persona-name"
        UnifiedPersonRegistry(main_snapshot).update_identity_profile_facts(
            self.person_id, {"preferred_address": "main-persona-name"},
            operation_id="main-persona-profile",
        )
        UnifiedPersonRegistry(alt_snapshot).update_identity_profile_facts(
            self.person_id, {"preferred_address": "alt-persona-name"},
            operation_id="alt-persona-profile",
        )
        self.assertTrue(self.sync.sync_snapshot(main_snapshot, source_scope="default")["ok"])
        self.assertTrue(self.sync.sync_snapshot(alt_snapshot, source_scope="persona:alt")["ok"])
        main_records, _ = self.sync.build_records(main_snapshot, source_scope="default")
        alt_records, _ = self.sync.build_records(alt_snapshot, source_scope="persona:alt")
        main_context = next(item.context for item in main_records if item.context.kind == "private")
        alt_context = next(item.context for item in alt_records if item.context.kind == "private")
        self.assertNotEqual(main_context.persona_id, alt_context.persona_id)
        self.assertEqual(
            "main-persona-name", self.sync.read_projection(main_context)["fields"]["nickname"],
        )
        self.assertEqual(
            "alt-persona-name", self.sync.read_projection(alt_context)["fields"]["nickname"],
        )

    def test_read_projection_only_opens_after_reconciliation_and_preserves_group_isolation(self) -> None:
        records, _ = self.sync.build_records(self.snapshot)
        private = next(item.context for item in records if item.context.kind == "private")
        group_contexts = {
            item.context.group_id: item.context for item in records if item.context.kind == "group_shared"
        }
        self.assertFalse(self.sync.read_projection(private)["ok"])
        self.sync.sync_snapshot(self.snapshot)
        private_view = self.sync.read_projection(private)
        self.assertTrue(private_view["ok"])
        self.assertEqual("private-name", private_view["fields"]["nickname"])
        expression = private_view["fields"]["expression_profile"]
        self.assertEqual("private-rule", expression["learned_rules"][0]["style"])
        binding = expression["learned_rules"][0]["scope_binding"]
        self.assertEqual("approved", binding["approval_state"])
        self.assertEqual("legacy_migration", binding["approved_by"])
        self.assertGreaterEqual(expression["scope_revision"], 1)
        self.assertEqual("approved", expression["samples"][0]["scope_binding"]["approval_state"])
        self.assertNotIn("10001", str(binding))
        observed = {
            context.group_id: self.sync.read_projection(context)["fields"]["recent_messages"][0]["text"]
            for context in group_contexts.values()
        }
        self.assertEqual({"group-a-sentinel", "group-b-sentinel"}, set(observed.values()))
        self.sync.mark_dirty()
        self.assertEqual("scoped_projection_not_reconciled", self.sync.read_projection(private)["code"])

    def test_unlinked_user_is_denied_and_ambiguous_rows_only_project_canonical_facts(self) -> None:
        unlinked = {"users": {"10001": deepcopy(self.snapshot["users"]["10001"])}}
        records, _ = self.sync.build_records(unlinked)
        self.assertEqual([], records)
        duplicated = deepcopy(self.snapshot)
        duplicated["users"]["duplicate"] = deepcopy(duplicated["users"]["10001"])
        records, _ = self.sync.build_records(duplicated)
        private = [item for item in records if item.context.kind == "private"]
        self.assertEqual(["profile_fact"], [item.record_kind for item in private])
        serialized = str([item.payload for item in private])
        self.assertIn("private-name", serialized)
        self.assertNotIn("private-sentinel", serialized)
        self.assertNotIn("private-rule", serialized)

    def test_person_profile_fact_overrides_legacy_name_without_copying_privilege(self) -> None:
        registry = UnifiedPersonRegistry(self.snapshot)
        updated = registry.update_identity_profile_facts(
            self.person_id,
            {"preferred_address": "canonical-name", "style": "canonical-style"},
            operation_id="canonical-profile",
        )
        self.assertTrue(updated["ok"])
        records, _ = self.sync.build_records(self.snapshot)
        profile = next(
            item for item in records
            if item.context.kind == "private" and item.record_kind == "profile_fact"
        )
        self.assertEqual("canonical-name", profile.payload["content"]["nickname"])
        self.assertEqual("canonical-style", profile.payload["content"]["style"])
        self.assertEqual(2, profile.payload["content"]["profile_fact_revision"])
        self.assertNotIn("relationship_role", repr(profile.payload))
        self.assertNotIn("relationship_score", repr(profile.payload))

    def test_archive_invalidates_ready_cache_before_remote_delete(self) -> None:
        records, _ = self.sync.build_records(self.snapshot)
        private = next(item.context for item in records if item.context.kind == "private")
        self.assertTrue(self.sync.sync_snapshot(self.snapshot)["ok"])
        self.assertTrue(self.sync.read_projection(private)["ok"])
        result = self.sync.archive_identity_scopes(
            private, operation_id="archive-1", reason_code="person_archive",
        )
        self.assertTrue(result["ok"])
        self.assertEqual("scoped_projection_not_reconciled", self.sync.read_projection(private)["code"])

    def test_group_reset_invalidates_cache_and_pending_saga_blocks_reprojection(self) -> None:
        records, _ = self.sync.build_records(self.snapshot)
        shared = next(item.context for item in records if item.context.kind == "group_shared")
        self.assertTrue(self.sync.sync_snapshot(self.snapshot)["ok"])
        self.assertTrue(self.sync.read_projection(shared)["ok"])
        result = self.sync.erase_group_scopes(
            shared, operation_id="group-reset-1", reason_code="group_reset",
        )
        self.assertTrue(result["ok"])
        self.assertEqual("scoped_projection_not_reconciled", self.sync.read_projection(shared)["code"])
        self.assertEqual(1, len(self.remote.group_erase_calls))

        pending = deepcopy(self.snapshot)
        pending["_req041_group_reset_sagas"] = {
            "group-reset-1": {
                "state": "confirmed", "group_id": "group-a", "persona_id": "default",
            }
        }
        pending_records, _ = self.sync.build_records(pending)
        serialized = str([item.payload for item in pending_records])
        self.assertNotIn("group-a-sentinel", serialized)
        self.assertIn("group-b-sentinel", serialized)

    def test_persona_reset_invalidates_cache_and_confirmed_saga_blocks_whole_snapshot(self) -> None:
        records, _ = self.sync.build_records(self.snapshot)
        private = next(item.context for item in records if item.context.kind == "private")
        self.assertTrue(self.sync.sync_snapshot(self.snapshot)["ok"])
        persona = self.sync._context(kind="persona_global", persona_id="default")
        result = self.sync.erase_persona_scopes(
            persona, operation_id="persona-reset-1", reason_code="persona_reset",
        )
        self.assertTrue(result["ok"])
        self.assertEqual("scoped_projection_not_reconciled", self.sync.read_projection(private)["code"])
        self.assertEqual([("default", "persona-reset-1")], self.remote.persona_erase_calls)

        pending = deepcopy(self.snapshot)
        pending["_req041_persona_reset_saga"] = {
            "state": "confirmed", "persona_id": "default", "operation_id": "persona-reset-1",
        }
        pending_records, pending_contexts = self.sync.build_records(pending)
        self.assertEqual([], pending_records)
        self.assertEqual([], pending_contexts)


if __name__ == "__main__":
    unittest.main()
