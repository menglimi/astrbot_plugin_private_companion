from __future__ import annotations

from contextlib import closing
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

from companion_v2.agenda.models import AgendaTarget
from companion_v2.agenda.service import AgendaService
from companion_v2.identity_policy.models import AuthorizationTier
from companion_v2.identity_policy.repository import PersistentIdentityDirectory
from companion_v2.storage.runtime import StorageOpenError, StorageRuntime
from companion_v2.storage.schema import (
    migration_count,
    MIGRATION_V4_TO_V5_ID, MIGRATION_V4_TO_V5_FINGERPRINT,
    MIGRATION_V5_TO_V6_ID, MIGRATION_V5_TO_V6_FINGERPRINT, SCHEMA_VERSION, SCHEMA_VERSION_V4, SCHEMA_VERSION_V5,
    V5_MIGRATION_STATEMENTS, V6_INDEX_STATEMENTS,
)
from tests import test_object_access_storage as fixture


class ScopeStorageTests(unittest.TestCase):
    def setUp(self):
        fixture.ObjectAccessStorageTests.setUp(self)
        with closing(sqlite3.connect(self.database)) as connection:
            for statement in V5_MIGRATION_STATEMENTS:
                connection.execute(statement)
            connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = 'schema_version'", (SCHEMA_VERSION_V5,))
            connection.execute("INSERT INTO schema_migrations VALUES (?, ?, ?, ?, ?)", (
                MIGRATION_V4_TO_V5_ID, SCHEMA_VERSION_V4, SCHEMA_VERSION_V5, MIGRATION_V4_TO_V5_FINGERPRINT, fixture.NOW.isoformat(),
            ))
            connection.execute("INSERT INTO access_objects VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (
                "agenda.memo:memo-old", "human", fixture.USER.subject_id, fixture.INSTALLATION,
                "onebot", "private", "window-a", "memo",
            ))
            connection.execute("INSERT INTO object_access_shares VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (
                "agenda.memo:memo-old", "human", "shared-person", "memo.read", "revoked", 2,
                fixture.NOW.isoformat(), None, fixture.NOW.isoformat(),
            ))
            connection.execute("""INSERT INTO operations VALUES (
                'unknown-op', 'conversation', ?, 'onebot', 'private', 'window-a',
                'unknown-op', 1, 'original-key', 'original-hash', 'unknown', 'unknown', ?, ?)
            """, (fixture.INSTALLATION, fixture.NOW.isoformat(), fixture.NOW.isoformat()))
            connection.execute("INSERT INTO outbox VALUES ('unknown-outbox', 'unknown-op', 'part-1', 'hash', 'unknown', ?)",
                               (fixture.NOW.isoformat(),))
            connection.execute("""INSERT INTO sources VALUES (
                'old-source', 'old-source', 1, 'conversation', ?, 'onebot', 'private', 'window-a',
                ?, NULL, '[]', 'original-hash', 'old body', 'revoked', 3, ?, ?)
            """, (fixture.INSTALLATION, fixture.NOW.isoformat(), fixture.NOW.isoformat(), fixture.NOW.isoformat()))
            connection.commit()
        self.old_rows = self.rows()

    open_runtime = fixture.ObjectAccessStorageTests.open_runtime
    mutate = fixture.ObjectAccessStorageTests.mutate

    def rows(self):
        with closing(sqlite3.connect(self.database)) as connection:
            return {table: connection.execute('SELECT * FROM "' + table + '"').fetchall()
                    for table in ("agenda_memos", "agenda_reminders", "agenda_occurrences", "outbox")}

    def assert_unchanged(self):
        self.assertEqual(self.rows(), self.old_rows)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute("SELECT value FROM runtime_metadata WHERE key = 'schema_version'").fetchone()[0], SCHEMA_VERSION_V5)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0], 4)
            columns = {row[1] for row in connection.execute("PRAGMA table_info(role_bindings)")}
            self.assertNotIn("bot_id", columns)
            self.assertEqual(connection.execute("SELECT name FROM sqlite_master WHERE name LIKE 'scope_v6_%'").fetchall(), [])

    def test_nonempty_v5_upgrade_preserves_owner_revisions_revocation_and_unknown_effects(self):
        runtime = self.open_runtime()
        self.assertEqual(self.rows(), self.old_rows)
        self.assertEqual(runtime.read_one("SELECT value FROM runtime_metadata WHERE key = 'schema_version'", ())["value"], SCHEMA_VERSION)
        self.assertEqual(runtime.read_one("SELECT state FROM sources", ())["state"], "revoked")
        self.assertEqual(runtime.read_one("SELECT governance_revision FROM sources", ())["governance_revision"], 3)
        self.assertEqual(tuple(runtime.read_one("SELECT bot_id, account_id, conversation_id, session_id FROM sources", ())), ("",) * 4)
        self.assertEqual(tuple(runtime.read_one("SELECT state, commit_state FROM operations", ())), ("unknown", "unknown"))
        self.assertEqual(tuple(runtime.read_one("SELECT state, revision FROM object_access_shares", ())), ("revoked", 2))
        runtime.close()
        reopened = self.open_runtime()
        self.assertEqual(len(reopened.read_all("SELECT * FROM schema_migrations", ())), migration_count())
        self.assertEqual(self.rows(), self.old_rows)

    def test_unresolved_legacy_identity_role_and_memo_are_not_adopted_by_new_route(self):
        runtime = self.open_runtime()
        identity = PersistentIdentityDirectory(runtime)
        resolved_scope = replace(fixture.SCOPE, bot_id="bot-a", account_id="login-a", conversation_id="conversation-a", session_id="session-a")
        principal = replace(fixture.PRINCIPAL, bot_id="bot-a", account_id="login-a")
        self.assertEqual(identity.resolve_authenticated(principal, observed_at=fixture.NOW).failure.code, "unbound_identity")
        self.assertIsNone(identity.role_binding(resolved_scope))
        self.assertEqual(identity.role_binding(fixture.SCOPE).revision, 7)
        identity.bind(binding_id="explicit-new-binding", principal=principal, subject=fixture.USER,
                      valid_from=fixture.NOW, expected_revision=identity.revision)
        identity.bind_role(scope=resolved_scope, role=self.target.role, expected_revision=0)
        service = AgendaService(runtime)
        service.policy.set_tier(fixture.USER, AuthorizationTier.IMPORTANT,
                                expected_revision=service.policy.revision, observed_at=fixture.NOW)
        actor = identity.resolve_authenticated(principal, observed_at=fixture.NOW).unwrap()
        target = AgendaTarget(resolved_scope, self.target.role, fixture.USER)
        self.assertEqual(service.list_memos(actor, target=target, observed_at=fixture.NOW).unwrap(), ())
        self.assertEqual(service.read_memo(actor, target=target, memo_id="memo-old", observed_at=fixture.NOW).failure.code,
                         "agenda_object_not_found")
        self.assertEqual(service.repository.memo("memo-old").target, self.target)
        self.assertEqual(service.repository.memo("memo-old").revision, 9)

    def test_failure_after_rebuilding_tables_rolls_back_everything_and_retries(self):
        with patch("companion_v2.storage.runtime.V6_INDEX_STATEMENTS", (*V6_INDEX_STATEMENTS, "INVALID SCOPE MIGRATION")):
            with self.assertRaises(StorageOpenError):
                self.open_runtime()
        self.assert_unchanged()
        self.assertEqual(self.open_runtime().read_one("SELECT COUNT(*) AS n FROM outbox", ())["n"], 1)

    def test_foreign_keys_are_reenabled_after_upgrade(self):
        runtime = self.open_runtime()
        self.assertEqual(runtime.read_one("PRAGMA foreign_keys", ())[0], 1)
        with self.assertRaises(sqlite3.IntegrityError):
            with runtime.transaction() as connection:
                connection.execute("INSERT INTO outbox VALUES ('broken', 'missing-op', 'part', 'hash', 'pending', ?)",
                                   (fixture.NOW.isoformat(),))
        self.assertEqual(runtime.read_all("PRAGMA foreign_key_check", ()), ())

    def test_broken_old_reference_is_quarantined_before_migration(self):
        self.mutate("UPDATE outbox SET operation_id = 'missing-operation'")
        runtime = self.open_runtime()
        try:
            self.assertEqual(runtime.read_all("PRAGMA foreign_key_check", ()), ())
            self.assertEqual(runtime.read_one("SELECT COUNT(*) AS n FROM outbox", ())["n"], 0)
            self.assertEqual(runtime.read_one(
                "SELECT value FROM runtime_metadata WHERE key = 'schema_version'", ())["value"], SCHEMA_VERSION)
            quarantined = sorted(Path(self.database).parent.glob("*.fk-repair-*.jsonl"))
            self.assertEqual(len(quarantined), 1, quarantined)
            record = json.loads(quarantined[0].read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual((record["table"], record["row"]["operation_id"]), ("outbox", "missing-operation"))
        finally:
            runtime.close()

    def test_partial_upgrade_is_not_silently_adopted(self):
        self.mutate("ALTER TABLE role_bindings ADD COLUMN bot_id TEXT NOT NULL DEFAULT ''")
        with self.assertRaises(StorageOpenError):
            self.open_runtime()

    def test_unknown_fingerprint_or_missing_scope_index_is_rejected(self):
        runtime = self.open_runtime()
        runtime.close()
        self.mutate("UPDATE schema_migrations SET fingerprint = 'untrusted' WHERE migration_id = ?", (MIGRATION_V5_TO_V6_ID,))
        with self.assertRaises(StorageOpenError):
            self.open_runtime()
        self.mutate("UPDATE schema_migrations SET fingerprint = ? WHERE migration_id = ?",
                    (MIGRATION_V5_TO_V6_FINGERPRINT, MIGRATION_V5_TO_V6_ID))
        self.mutate("DROP INDEX sources_scope_idx")
        with self.assertRaises(StorageOpenError):
            self.open_runtime()


if __name__ == "__main__":
    unittest.main()
