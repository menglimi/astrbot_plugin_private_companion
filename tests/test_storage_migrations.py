from __future__ import annotations

from contextlib import closing
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from companion_v2.storage.runtime import StorageOpenError, StorageRuntime
from companion_v2.storage.schema import (
    MIGRATION_V1_TO_V2_FINGERPRINT, MIGRATION_V1_TO_V2_ID, MIGRATION_V19_TO_V20_ID,
    MIGRATION_V20_TO_V21_ID, MIGRATION_V21_TO_V22_ID, MIGRATION_V30_TO_V31_ID,
    MIGRATION_V30_TO_V31_FINGERPRINT, MIGRATION_V31_TO_V32_ID,
    MIGRATION_V31_TO_V32_FINGERPRINT, SCHEMA_VERSION, SCHEMA_VERSION_V1,
    MIGRATION_V51_TO_V52_FINGERPRINT, MIGRATION_V51_TO_V52_ID, SCHEMA_VERSION_V51, SCHEMA_VERSION_V52,
    MIGRATION_V52_TO_V53_FINGERPRINT, MIGRATION_V52_TO_V53_ID, V53_MIGRATION_STATEMENTS,
    SCHEMA_VERSION_V20, SCHEMA_VERSION_V21, SCHEMA_VERSION_V22, SCHEMA_VERSION_V30,
    SCHEMA_VERSION_V31, SCHEMA_VERSION_V32, V1_SCHEMA_STATEMENTS, V2_MIGRATION_STATEMENTS,
    V2_REQUIRED_TABLES, V21_REQUIRED_TABLES, V22_REQUIRED_TABLES, V30_REQUIRED_TABLES,
    V31_REQUIRED_TABLES, V31_MIGRATION_STATEMENTS, V32_REQUIRED_TABLES, V32_MIGRATION_STATEMENTS,
    V33_REQUIRED_TABLES, V33_MIGRATION_STATEMENTS, MIGRATION_V32_TO_V33_ID, MIGRATION_V32_TO_V33_FINGERPRINT,
    V34_REQUIRED_TABLES, SCHEMA_VERSION_V33, SCHEMA_VERSION_V34, V34_MIGRATION_STATEMENTS,
    MIGRATION_V33_TO_V34_ID, MIGRATION_V33_TO_V34_FINGERPRINT,
    V35_REQUIRED_TABLES, V35_MIGRATION_STATEMENTS,
    MIGRATION_V34_TO_V35_ID, MIGRATION_V34_TO_V35_FINGERPRINT,
    V36_MIGRATION_STATEMENTS, MIGRATION_V35_TO_V36_ID, SCHEMA_VERSION_V35,
    MIGRATION_V54_TO_V55_FINGERPRINT, MIGRATION_V54_TO_V55_ID, SCHEMA_VERSION_V54, SCHEMA_VERSION_V55,
    MIGRATION_V56_TO_V57_FINGERPRINT, MIGRATION_V56_TO_V57_ID, SCHEMA_VERSION_V56, SCHEMA_VERSION_V57,
    MIGRATION_V57_TO_V58_FINGERPRINT, MIGRATION_V57_TO_V58_ID, SCHEMA_VERSION_V58,
    MIGRATION_V58_TO_V59_FINGERPRINT, MIGRATION_V58_TO_V59_ID, SCHEMA_VERSION_V59,
    MIGRATION_V59_TO_V60_FINGERPRINT, MIGRATION_V59_TO_V60_ID, SCHEMA_VERSION_V60,
    MIGRATION_V60_TO_V61_FINGERPRINT, MIGRATION_V60_TO_V61_ID, SCHEMA_VERSION_V61,
    MIGRATION_V61_TO_V62_FINGERPRINT, MIGRATION_V61_TO_V62_ID, SCHEMA_VERSION_V62,
    MIGRATION_V62_TO_V63_FINGERPRINT, MIGRATION_V62_TO_V63_ID, SCHEMA_VERSION_V63,
    MIGRATION_V63_TO_V64_FINGERPRINT, MIGRATION_V63_TO_V64_ID, SCHEMA_VERSION_V64,
    MIGRATION_V64_TO_V65_FINGERPRINT, MIGRATION_V64_TO_V65_ID, SCHEMA_VERSION_V65,
    MIGRATION_V65_TO_V66_FINGERPRINT, MIGRATION_V65_TO_V66_ID, SCHEMA_VERSION_V66,
    MIGRATION_V66_TO_V67_FINGERPRINT, MIGRATION_V66_TO_V67_ID, SCHEMA_VERSION_V67,
)
from tests.execution_support import remove_v21, remove_v22, remove_v31, remove_v32, remove_v33, remove_v34, remove_v35, remove_v36, remove_v52, remove_v53, remove_v55, remove_v57, remove_v58, remove_v59, remove_v60, remove_v61, remove_v62, remove_v63, remove_v64, remove_v65, remove_v66, remove_v67


class StorageMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory(prefix="companion-v2-schema-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "v2-data"
        self.root.mkdir()
        self.database = self.root / StorageRuntime.DATABASE_FILENAME
        with closing(sqlite3.connect(self.database)) as connection:
            for statement in V1_SCHEMA_STATEMENTS:
                connection.execute(statement)
            connection.executemany(
                "INSERT INTO runtime_metadata(key, value) VALUES (?, ?)",
                (("installation_id", "installation-a"), ("schema_version", SCHEMA_VERSION_V1)),
            )
            connection.execute(
                "INSERT INTO inbox(consumer, event_id, payload_hash, created_at) VALUES (?, ?, ?, ?)",
                ("conversation", "existing-event", "existing-hash", "2026-09-21T00:00:00+00:00"),
            )
            connection.execute(
                """INSERT INTO operations VALUES(
                    'existing-op', 'screen', 'installation-a', 'onebot', 'private', 'private-a',
                    'existing-lineage', 1, 'existing-key', 'existing-hash',
                    'unknown', 'unknown', '2026-09-21T00:00:00+00:00', '2026-09-21T00:00:00+00:00'
                )""",
            )
            connection.execute(
                """INSERT INTO outbox VALUES(
                    'existing-outbox', 'existing-op', 'part-a', 'payload-hash', 'unknown',
                    '2026-09-21T00:00:00+00:00'
                )""",
            )
            connection.commit()

    def open_runtime(self) -> StorageRuntime:
        runtime = StorageRuntime.open(data_root=self.root, installation_id="installation-a")
        self.addCleanup(runtime.close)
        return runtime

    def mutate(self, statement: str, parameters: tuple = ()) -> None:
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(statement, parameters)
            connection.commit()

    def version(self) -> str:
        with closing(sqlite3.connect(self.database)) as connection:
            return connection.execute(
                "SELECT value FROM runtime_metadata WHERE key = 'schema_version'"
            ).fetchone()[0]

    def test_empty_data_root_initializes_the_latest_schema(self) -> None:
        fresh_root = Path(self.temporary.name) / "empty-data"
        fresh_root.mkdir()
        runtime = StorageRuntime.open(data_root=fresh_root, installation_id="installation-a")
        self.addCleanup(runtime.close)

        self.assertEqual(runtime.read_one(
            "SELECT value FROM runtime_metadata WHERE key = 'schema_version'", (),
        )["value"], SCHEMA_VERSION)
        columns = {row["name"] for row in runtime.read_all(
            "PRAGMA table_info(conversation_batches)", (),
        )}
        self.assertIn("presentation_action", columns)
        self.assertIsNotNone(runtime.read_one(
            "SELECT * FROM schema_migrations WHERE migration_id = ?", (MIGRATION_V51_TO_V52_ID,),
        ))
        self.assertIsNotNone(runtime.read_one(
            "SELECT * FROM sqlite_master WHERE type = 'table' AND name = 'laya_decision_events'", (),
        ))
        self.assertIsNotNone(runtime.read_one(
            "SELECT * FROM sqlite_master WHERE type = 'table' AND name = 'agenda_open_loop_activity'", (),
        ))
        self.assertIsNotNone(runtime.read_one(
            "SELECT * FROM sqlite_master WHERE type = 'table' AND name = 'proactive_prompt_snapshots'", (),
        ))
        prompt_migration = runtime.read_one(
            "SELECT * FROM schema_migrations WHERE migration_id = ?", (MIGRATION_V63_TO_V64_ID,),
        )
        self.assertEqual((prompt_migration["to_version"], prompt_migration["fingerprint"]),
                         (SCHEMA_VERSION_V64, MIGRATION_V63_TO_V64_FINGERPRINT))
        receipt_migration = runtime.read_one(
            "SELECT * FROM schema_migrations WHERE migration_id = ?", (MIGRATION_V64_TO_V65_ID,),
        )
        self.assertEqual((receipt_migration["to_version"], receipt_migration["fingerprint"]),
                         (SCHEMA_VERSION_V65, MIGRATION_V64_TO_V65_FINGERPRINT))
        anniversary_migration = runtime.read_one(
            "SELECT * FROM schema_migrations WHERE migration_id = ?", (MIGRATION_V65_TO_V66_ID,),
        )
        self.assertEqual((anniversary_migration["to_version"], anniversary_migration["fingerprint"]),
                         (SCHEMA_VERSION_V66, MIGRATION_V65_TO_V66_FINGERPRINT))
        self.assertIsNotNone(runtime.read_one(
            "SELECT * FROM sqlite_master WHERE type = 'table' AND name = 'relationship_anniversaries'", (),
        ))
        migration = runtime.read_one(
            "SELECT * FROM schema_migrations WHERE migration_id = ?", (MIGRATION_V60_TO_V61_ID,),
        )
        self.assertEqual(migration["fingerprint"], MIGRATION_V60_TO_V61_FINGERPRINT)
        self.assertEqual(migration["to_version"], SCHEMA_VERSION_V61)

    def assert_v1_unchanged(self) -> None:
        with closing(sqlite3.connect(self.database)) as connection:
            version = connection.execute(
                "SELECT value FROM runtime_metadata WHERE key = 'schema_version'"
            ).fetchone()[0]
            self.assertEqual(version, SCHEMA_VERSION_V1)
            self.assertIsNone(connection.execute(
                "SELECT name FROM sqlite_master WHERE name = 'schema_migrations'"
            ).fetchone())
            self.assertEqual(connection.execute("SELECT state FROM outbox").fetchone(), ("unknown",))

    def test_nonempty_v1_upgrades_once_and_preserves_unknown_effects(self) -> None:
        runtime = self.open_runtime()
        self.assertEqual(runtime.read_one(
            "SELECT value FROM runtime_metadata WHERE key = ?", ("schema_version",),
        )["value"], SCHEMA_VERSION)
        row = runtime.read_one("SELECT * FROM schema_migrations WHERE migration_id = ?", (MIGRATION_V1_TO_V2_ID,))
        self.assertEqual(row["fingerprint"], MIGRATION_V1_TO_V2_FINGERPRINT)
        tables = {r["name"] for r in runtime.read_all("SELECT name FROM sqlite_master WHERE type = 'table'", ())}
        self.assertTrue(V2_REQUIRED_TABLES.issubset(tables))
        batch_columns = {r["name"] for r in runtime.read_all("PRAGMA table_info(conversation_batches)", ())}
        self.assertIn("presentation_action", batch_columns)
        self.assertIsNotNone(runtime.read_one(
            "SELECT * FROM schema_migrations WHERE migration_id = ?", (MIGRATION_V51_TO_V52_ID,),
        ))


        self.assertEqual(runtime.read_one("SELECT * FROM inbox", ())['event_id'], "existing-event")
        self.assertEqual(runtime.read_one("SELECT * FROM operations", ())['commit_state'], "unknown")
        self.assertEqual(runtime.read_one("SELECT * FROM outbox", ())['state'], "unknown")
        migrations = tuple(tuple(r) for r in runtime.read_all("SELECT * FROM schema_migrations ORDER BY migration_id", ()))
        runtime.close()
        reopened = self.open_runtime()
        self.assertEqual(tuple(tuple(r) for r in reopened.read_all("SELECT * FROM schema_migrations ORDER BY migration_id", ())), migrations)

    def test_v60_upgrade_preserves_laya_events_and_accepts_role_interventions(self) -> None:
        runtime = self.open_runtime()
        with runtime.transaction() as connection:
            connection.execute("""INSERT INTO laya_decision_events
                (event_id, origin_key, source, status, mode, enabled_fields_json, observed_at)
                VALUES (?, ?, 'conversation', 'ok', 'remote', '{}', '2026-10-14T00:00:00+00:00')""",
                ("a" * 32, "role-key"))
        runtime.close()
        with closing(sqlite3.connect(self.database)) as connection:
            remove_v61(connection)
            connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = 'schema_version'",
                               (SCHEMA_VERSION_V60,))
            connection.commit()

        upgraded = self.open_runtime()
        existing = upgraded.read_one("SELECT * FROM laya_decision_events WHERE event_id = ?", ("a" * 32,))
        self.assertEqual(existing["source"], "conversation")
        with upgraded.transaction() as connection:
            connection.execute("""INSERT INTO laya_decision_events
                (event_id, origin_key, source, status, mode, enabled_fields_json, observed_at)
                VALUES (?, ?, 'role_intervention', 'ok', 'remote', '{}', '2026-10-14T00:00:01+00:00')""",
                ("b" * 32, "role-key"))
        new_event = upgraded.read_one("SELECT * FROM laya_decision_events WHERE event_id = ?", ("b" * 32,))
        self.assertEqual(new_event["source"], "role_intervention")
        runtime = upgraded
        self.assertEqual(runtime.read_one("SELECT * FROM inbox", ())["event_id"], "existing-event")
        self.assertEqual(runtime.read_one("SELECT * FROM operations", ())["commit_state"], "unknown")
        self.assertEqual(runtime.read_one("SELECT * FROM outbox", ())["state"], "unknown")
        migrations = tuple(tuple(r) for r in runtime.read_all("SELECT * FROM schema_migrations ORDER BY migration_id", ()))
        runtime.close()
        reopened = self.open_runtime()
        self.assertEqual(tuple(tuple(r) for r in reopened.read_all("SELECT * FROM schema_migrations ORDER BY migration_id", ())), migrations)

    def test_v20_proactive_schema_migrates_to_v21_memory_without_reusing_v20(self) -> None:
        runtime = self.open_runtime()
        runtime.close()
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("PRAGMA foreign_keys = OFF")
            remove_v21(connection)
            connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = 'schema_version'", (SCHEMA_VERSION_V20,))
            connection.commit()
        upgraded = self.open_runtime()
        self.assertEqual(upgraded.read_one("SELECT value FROM runtime_metadata WHERE key = 'schema_version'", ())['value'], SCHEMA_VERSION)
        tables = {row['name'] for row in upgraded.read_all("SELECT name FROM sqlite_master WHERE type = 'table'", ())}
        self.assertTrue(V21_REQUIRED_TABLES.issubset(tables))
        self.assertIsNotNone(upgraded.read_one("SELECT * FROM schema_migrations WHERE migration_id = ?", (MIGRATION_V19_TO_V20_ID,)))
        self.assertIsNotNone(upgraded.read_one("SELECT * FROM schema_migrations WHERE migration_id = ?", (MIGRATION_V20_TO_V21_ID,)))

    def test_v51_storage_migrates_reply_presentation_action(self) -> None:
        runtime = self.open_runtime()
        runtime.close()
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("PRAGMA foreign_keys = OFF")
            remove_v52(connection)
            connection.execute(
                "UPDATE runtime_metadata SET value = ? WHERE key = 'schema_version'",
                (SCHEMA_VERSION_V51,),
            )
            connection.commit()

        upgraded = self.open_runtime()
        self.assertEqual(upgraded.read_one(
            "SELECT value FROM runtime_metadata WHERE key = 'schema_version'", (),
        )["value"], SCHEMA_VERSION)
        columns = {row["name"] for row in upgraded.read_all("PRAGMA table_info(conversation_batches)", ())}
        self.assertIn("presentation_action", columns)
        migration = upgraded.read_one(
            "SELECT * FROM schema_migrations WHERE migration_id = ?", (MIGRATION_V51_TO_V52_ID,),
        )
        self.assertEqual(migration["from_version"], SCHEMA_VERSION_V51)
        self.assertEqual(migration["to_version"], SCHEMA_VERSION_V52)
        self.assertEqual(migration["fingerprint"], MIGRATION_V51_TO_V52_FINGERPRINT)

    def test_v53_upgrade_adds_stream_records_and_rolls_back_on_failure(self):
        from companion_v2.storage.schema import V54_MIGRATION_STATEMENTS, SCHEMA_VERSION_V53, MIGRATION_V53_TO_V54_ID
        from tests.execution_support import remove_v54
        runtime = self.open_runtime()
        runtime.close()
        with closing(sqlite3.connect(self.database)) as connection:
            remove_v54(connection)
            connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = 'schema_version'", (SCHEMA_VERSION_V53,))
            connection.commit()
        with patch("companion_v2.storage.runtime.V54_MIGRATION_STATEMENTS", V54_MIGRATION_STATEMENTS + ("INVALID UPGRADE",)):
            with self.assertRaises(StorageOpenError):
                self.open_runtime()
        self.assertEqual(self.version(), SCHEMA_VERSION_V53)
        upgraded = self.open_runtime()
        self.assertIsNotNone(upgraded.read_one("SELECT * FROM schema_migrations WHERE migration_id = ?", (MIGRATION_V53_TO_V54_ID,)))
        self.assertEqual(upgraded.read_all("SELECT * FROM conversation_reply_streams", ()), ())
        self.assertEqual(upgraded.read_one("SELECT state FROM outbox", ())["state"], "unknown")

    def test_v54_upgrade_persists_timeout_failures_and_preserves_existing_records(self):
        self.open_runtime().close()
        with closing(sqlite3.connect(self.database)) as connection:
            remove_v55(connection)
            connection.execute("""INSERT INTO execution_tasks VALUES
                ('failure-task', 'origin-a', 'fixture.read', 'foreground', 'failure-dedupe', 'request-hash',
                 1, 'failed', 1, 1, 'failure-attempt', '2026-10-10T00:00:00+00:00',
                 '2026-10-10T00:01:00+00:00', '2026-10-10T00:00:00+00:00',
                 '2026-10-10T00:00:01+00:00', '{}')""")
            connection.execute("""INSERT INTO execution_attempts VALUES
                ('failure-attempt', 'failure-task', 1, 1, 'foreground', 'fixture.read',
                 'origin-a', 'scope-a', 0, '2026-10-10T00:01:00+00:00', '{}')""")
            connection.execute("INSERT INTO model_requests VALUES (?, ?, ?, ?)",
                               ('failure-task', 'request-hash', '{}', '2026-10-10T00:00:00+00:00'))
            connection.execute("""INSERT INTO model_calls
                (call_id, task_id, attempt_id, position, state, provider_json, reserved_tokens, reserved_cost)
                VALUES ('failure-call', 'failure-task', 'failure-attempt', 0, 'unknown', '{}', 1, 0)""")
            connection.execute("INSERT INTO model_call_failures VALUES (?, ?, ?)",
                               ('failure-call', 'provider_request_failed', '2026-10-10T00:00:01+00:00'))
            connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = 'schema_version'",
                               (SCHEMA_VERSION_V54,))
            connection.execute("DELETE FROM schema_migrations WHERE migration_id = ?",
                               (MIGRATION_V54_TO_V55_ID,))
            connection.commit()

        upgraded = self.open_runtime()
        migration = upgraded.read_one("SELECT * FROM schema_migrations WHERE migration_id = ?",
                                      (MIGRATION_V54_TO_V55_ID,))
        self.assertEqual((migration["from_version"], migration["to_version"], migration["fingerprint"]),
                         (SCHEMA_VERSION_V54, SCHEMA_VERSION_V55, MIGRATION_V54_TO_V55_FINGERPRINT))
        self.assertEqual(upgraded.read_one("SELECT code FROM model_call_failures WHERE call_id = 'failure-call'", ())["code"],
                         "provider_request_failed")
        with upgraded.transaction() as connection:
            connection.execute("UPDATE model_call_failures SET code = 'provider_wait_timed_out' WHERE call_id = 'failure-call'")
        self.assertEqual(upgraded.read_one("SELECT code FROM model_call_failures WHERE call_id = 'failure-call'", ())["code"],
                         "provider_wait_timed_out")

    def test_v56_upgrade_adds_platform_circuit_table_and_rolls_back_on_failure(self):
        self.open_runtime().close()
        with closing(sqlite3.connect(self.database)) as connection:
            remove_v57(connection)
            connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = 'schema_version'",
                               (SCHEMA_VERSION_V56,))
            connection.commit()

        with patch("companion_v2.storage.runtime.V57_MIGRATION_STATEMENTS",
                   ("CREATE TABLE proactive_platform_circuits (account_key TEXT PRIMARY KEY)", "INVALID UPGRADE")):
            with self.assertRaises(StorageOpenError):
                self.open_runtime()
        self.assertEqual(self.version(), SCHEMA_VERSION_V56)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertIsNone(connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'proactive_platform_circuits'").fetchone())

        upgraded = self.open_runtime()
        migration = upgraded.read_one("SELECT * FROM schema_migrations WHERE migration_id = ?",
                                      (MIGRATION_V56_TO_V57_ID,))
        self.assertEqual((migration["from_version"], migration["to_version"], migration["fingerprint"]),
                         (SCHEMA_VERSION_V56, SCHEMA_VERSION_V57, MIGRATION_V56_TO_V57_FINGERPRINT))
        self.assertIsNotNone(upgraded.read_one(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'proactive_platform_circuits'", ()))

    def test_v57_upgrade_adds_default_applicability_to_existing_expression_rules(self):
        runtime = self.open_runtime()
        runtime.close()
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("""INSERT INTO expression_style_runs
                (run_id, owner_key, scope_key, sample_json, material_hash, summary, state, created_at, decided_at)
                VALUES ('legacy-style-run', 'owner', 'scope', '{\"source_kind\":\"shared_pack\",\"opened_at\":\"2026-10-01T00:00:00+00:00\",\"closed_at\":\"2026-10-01T00:00:00+00:00\",\"handles\":[],\"characters\":0}', 'hash', '旧规则', 'pending', '2026-10-01T00:00:00+00:00', NULL)""")
            connection.execute("""INSERT INTO expression_style_signals
                (signal_id, run_id, owner_key, scope_key, dimension, observation, proposal, confidence,
                 evidence_json, state, created_at, decided_at, decided_by, decision_key, ordinal)
                VALUES ('legacy-style-signal', 'legacy-style-run', 'owner', 'scope', 'diction',
                        '旧观察', '旧规则', 0.5, '[]', 'adopted', '2026-10-01T00:00:00+00:00',
                        '2026-10-01T00:00:00+00:00', 'operator', 'adopt', 0)""")
            remove_v58(connection)
            connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = 'schema_version'",
                               (SCHEMA_VERSION_V57,))
            connection.commit()
        upgraded = self.open_runtime()
        migration = upgraded.read_one("SELECT * FROM schema_migrations WHERE migration_id = ?",
                                      (MIGRATION_V57_TO_V58_ID,))
        self.assertEqual((migration["from_version"], migration["to_version"], migration["fingerprint"]),
                         (SCHEMA_VERSION_V57, SCHEMA_VERSION_V58, MIGRATION_V57_TO_V58_FINGERPRINT))
        rule = upgraded.read_one("SELECT applicability_json FROM expression_style_signals WHERE signal_id = ?",
                                 ("legacy-style-signal",))
        self.assertIn('"intent":"any"', rule["applicability_json"])
        self.assertIn('"relationship_stages":["any"]', rule["applicability_json"])

    def test_v58_upgrade_adds_laya_request_response_and_processing_diagnostics(self):
        runtime = self.open_runtime()
        runtime.close()
        with closing(sqlite3.connect(self.database)) as connection:
            remove_v59(connection)
            connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = 'schema_version'",
                               (SCHEMA_VERSION_V58,))
            connection.commit()

        with patch("companion_v2.storage.runtime.V59_MIGRATION_STATEMENTS",
                   ("ALTER TABLE laya_decision_events ADD COLUMN request_json TEXT", "INVALID UPGRADE")):
            with self.assertRaises(StorageOpenError):
                self.open_runtime()
        self.assertEqual(self.version(), SCHEMA_VERSION_V58)
        with closing(sqlite3.connect(self.database)) as connection:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(laya_decision_events)")}
            self.assertNotIn("request_json", columns)

        upgraded = self.open_runtime()
        migration = upgraded.read_one("SELECT * FROM schema_migrations WHERE migration_id = ?",
                                      (MIGRATION_V58_TO_V59_ID,))
        self.assertEqual((migration["from_version"], migration["to_version"], migration["fingerprint"]),
                         (SCHEMA_VERSION_V58, SCHEMA_VERSION_V59, MIGRATION_V58_TO_V59_FINGERPRINT))
        columns = {row["name"] for row in upgraded.read_all("PRAGMA table_info(laya_decision_events)", ())}
        self.assertTrue({"request_json", "response_json", "processing_json"}.issubset(columns))

    def test_v59_upgrade_adds_bounded_per_window_contact_schedule_diagnostics(self):
        self.open_runtime().close()
        with closing(sqlite3.connect(self.database)) as connection:
            remove_v60(connection)
            connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = 'schema_version'",
                               (SCHEMA_VERSION_V59,))
            connection.commit()

        upgraded = self.open_runtime()
        migration = upgraded.read_one("SELECT * FROM schema_migrations WHERE migration_id = ?",
                                      (MIGRATION_V59_TO_V60_ID,))
        self.assertEqual((migration["from_version"], migration["to_version"], migration["fingerprint"]),
                         (SCHEMA_VERSION_V59, SCHEMA_VERSION_V60, MIGRATION_V59_TO_V60_FINGERPRINT))
        self.assertIsNotNone(upgraded.read_one(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'proactive_schedule_diagnostics'", ()))

    def test_v61_upgrade_adds_indexes_for_bounded_input_sample_queries(self):
        self.open_runtime().close()
        with closing(sqlite3.connect(self.database)) as connection:
            remove_v62(connection)
            connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = 'schema_version'",
                               (SCHEMA_VERSION_V61,))
            connection.commit()

        upgraded = self.open_runtime()
        migration = upgraded.read_one("SELECT * FROM schema_migrations WHERE migration_id = ?",
                                      (MIGRATION_V61_TO_V62_ID,))
        self.assertEqual((migration["from_version"], migration["to_version"], migration["fingerprint"]),
                         (SCHEMA_VERSION_V61, SCHEMA_VERSION_V62, MIGRATION_V61_TO_V62_FINGERPRINT))
        names = {row["name"] for row in upgraded.read_all(
            "SELECT name FROM sqlite_master WHERE type = 'index'", ())}
        self.assertTrue({"conversation_inputs_scope_received_idx", "conversation_batches_role_binding_idx"}
                        .issubset(names))

    def test_v62_upgrade_adds_persistent_open_loop_activity(self):
        self.open_runtime().close()
        with closing(sqlite3.connect(self.database)) as connection:
            remove_v63(connection)
            connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = 'schema_version'",
                               (SCHEMA_VERSION_V62,))
            connection.commit()
        upgraded = self.open_runtime()
        migration = upgraded.read_one("SELECT * FROM schema_migrations WHERE migration_id = ?",
                                      (MIGRATION_V62_TO_V63_ID,))
        self.assertEqual((migration["from_version"], migration["to_version"], migration["fingerprint"]),
                         (SCHEMA_VERSION_V62, SCHEMA_VERSION_V63, MIGRATION_V62_TO_V63_FINGERPRINT))
        columns = {row["name"] for row in upgraded.read_all("PRAGMA table_info(agenda_open_loop_activity)", ())}
        self.assertTrue({"memo_id", "last_context_at", "context_count", "last_followup_at",
                         "followup_count", "updated_at"}.issubset(columns))

    def test_v63_upgrade_adds_proactive_prompt_archive(self):
        self.open_runtime().close()
        with closing(sqlite3.connect(self.database)) as connection:
            remove_v64(connection)
            connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = 'schema_version'",
                               (SCHEMA_VERSION_V63,))
            connection.commit()

        upgraded = self.open_runtime()
        migration = upgraded.read_one("SELECT * FROM schema_migrations WHERE migration_id = ?",
                                      (MIGRATION_V63_TO_V64_ID,))
        self.assertEqual((migration["from_version"], migration["to_version"], migration["fingerprint"]),
                         (SCHEMA_VERSION_V63, SCHEMA_VERSION_V64, MIGRATION_V63_TO_V64_FINGERPRINT))
        columns = {row["name"] for row in upgraded.read_all(
            "PRAGMA table_info(proactive_prompt_snapshots)", ())}
        self.assertTrue({"task_id", "call_id", "provider_id", "model_name", "prompt_hash",
                         "messages_json", "created_at"}.issubset(columns))

    def test_v64_upgrade_adds_nullable_outbox_receipt_reference(self):
        self.open_runtime().close()
        with closing(sqlite3.connect(self.database)) as connection:
            remove_v65(connection)
            connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = 'schema_version'",
                               (SCHEMA_VERSION_V64,))
            connection.commit()

        upgraded = self.open_runtime()
        migration = upgraded.read_one("SELECT * FROM schema_migrations WHERE migration_id = ?",
                                      (MIGRATION_V64_TO_V65_ID,))
        self.assertEqual((migration["from_version"], migration["to_version"], migration["fingerprint"]),
                         (SCHEMA_VERSION_V64, SCHEMA_VERSION_V65, MIGRATION_V64_TO_V65_FINGERPRINT))
        columns = {row["name"] for row in upgraded.read_all("PRAGMA table_info(outbox)", ())}
        self.assertIn("receipt_ref", columns)
        self.assertIsNone(upgraded.read_one("SELECT receipt_ref FROM outbox", ())["receipt_ref"])

    def test_v65_upgrade_adds_auto_relationship_anniversaries(self):
        self.open_runtime().close()
        with closing(sqlite3.connect(self.database)) as connection:
            remove_v66(connection)
            connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = 'schema_version'",
                               (SCHEMA_VERSION_V65,))
            connection.commit()

        upgraded = self.open_runtime()
        migration = upgraded.read_one("SELECT * FROM schema_migrations WHERE migration_id = ?",
                                      (MIGRATION_V65_TO_V66_ID,))
        self.assertEqual((migration["from_version"], migration["to_version"], migration["fingerprint"]),
                         (SCHEMA_VERSION_V65, SCHEMA_VERSION_V66, MIGRATION_V65_TO_V66_FINGERPRINT))
        columns = {row["name"] for row in upgraded.read_all(
            "PRAGMA table_info(relationship_anniversaries)", ())}
        self.assertTrue({"anniversary_id", "owner_key", "label", "month", "day",
                         "confidence", "evidence_json", "source_run_id", "state"}.issubset(columns))
        indexes = {row["name"] for row in upgraded.read_all(
            "SELECT name FROM sqlite_master WHERE type = 'index'", ())}
        self.assertIn("relationship_anniversary_active_day_idx", indexes)

    def test_v66_upgrade_accepts_jev_and_preserves_recorded_events(self) -> None:
        runtime = self.open_runtime()
        with runtime.transaction() as connection:
            connection.execute("""INSERT INTO laya_decision_events
                (event_id, origin_key, source, status, mode, enabled_fields_json, observed_at)
                VALUES (?, ?, 'conversation', 'ok', 'remote', '{}', '2026-10-30T00:00:00+00:00')""",
                ("a" * 32, "role-key"))
        runtime.close()
        with closing(sqlite3.connect(self.database)) as connection:
            remove_v67(connection)
            connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = 'schema_version'",
                               (SCHEMA_VERSION_V66,))
            connection.commit()
            # A v66 root rejects the third backend, which is why the rebuild exists.
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("""INSERT INTO laya_decision_events
                    (event_id, origin_key, source, status, mode, enabled_fields_json, observed_at)
                    VALUES (?, ?, 'conversation', 'ok', 'jev', '{}', '2026-10-30T00:00:01+00:00')""",
                    ("b" * 32, "role-key"))

        upgraded = self.open_runtime()
        migration = upgraded.read_one("SELECT * FROM schema_migrations WHERE migration_id = ?",
                                      (MIGRATION_V66_TO_V67_ID,))
        self.assertEqual((migration["from_version"], migration["to_version"], migration["fingerprint"]),
                         (SCHEMA_VERSION_V66, SCHEMA_VERSION_V67, MIGRATION_V66_TO_V67_FINGERPRINT))
        self.assertEqual(upgraded.read_one("SELECT mode FROM laya_decision_events WHERE event_id = ?",
                                          ("a" * 32,))["mode"], "remote")
        with upgraded.transaction() as connection:
            connection.execute("""INSERT INTO laya_decision_events
                (event_id, origin_key, source, status, mode, enabled_fields_json, observed_at)
                VALUES (?, ?, 'conversation', 'ok', 'jev', '{}', '2026-10-30T00:00:01+00:00')""",
                ("b" * 32, "role-key"))
        self.assertEqual(upgraded.read_one("SELECT mode FROM laya_decision_events WHERE event_id = ?",
                                          ("b" * 32,))["mode"], "jev")
        upgraded.close()
        reopened = self.open_runtime()
        self.assertEqual(reopened.read_one(
            "SELECT count(*) AS n FROM laya_decision_events", ())["n"], 2)

    def test_v52_upgrade_adds_response_diagnostics_and_rolls_back_on_failure(self):
        runtime = self.open_runtime()
        runtime.close()
        with closing(sqlite3.connect(self.database)) as connection:
            remove_v53(connection)
            connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = 'schema_version'",
                               (SCHEMA_VERSION_V52,))
            connection.commit()
        with patch("companion_v2.storage.runtime.V53_MIGRATION_STATEMENTS",
                   V53_MIGRATION_STATEMENTS + ("INVALID UPGRADE",)):
            with self.assertRaises(StorageOpenError):
                self.open_runtime()
        self.assertEqual(self.version(), SCHEMA_VERSION_V52)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertIsNone(connection.execute(
                "SELECT name FROM sqlite_master WHERE name = 'model_call_diagnostics'").fetchone())
        upgraded = self.open_runtime()
        migration = upgraded.read_one("SELECT * FROM schema_migrations WHERE migration_id = ?",
                                      (MIGRATION_V52_TO_V53_ID,))
        self.assertEqual(migration["fingerprint"], MIGRATION_V52_TO_V53_FINGERPRINT)
        self.assertEqual(migration["to_version"], "companion-v2.storage.v53")
        self.assertEqual(upgraded.read_one("SELECT state FROM outbox", ())["state"], "unknown")

    def test_v21_memory_schema_adds_role_growth_tables_and_preserves_memory(self):
        runtime = self.open_runtime()
        runtime.close()
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                """INSERT INTO memory_atoms VALUES(
                    'atom-a', 'owner-a', 'personal', 'self', 'fact-a', 0, 1,
                    'active', '{}', '{}', '{}', '2026-09-23T00:00:00+00:00'
                )"""
            )
            remove_v22(connection)
            connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = 'schema_version'", (SCHEMA_VERSION_V21,))
            connection.commit()
        upgraded = self.open_runtime()
        self.assertEqual(upgraded.read_one("SELECT value FROM runtime_metadata WHERE key = 'schema_version'", ())['value'], SCHEMA_VERSION)
        names = {row['name'] for row in upgraded.read_all("SELECT name FROM sqlite_master WHERE type = 'table'", ())}
        self.assertTrue(V22_REQUIRED_TABLES.issubset(names))
        self.assertEqual(upgraded.read_one("SELECT atom_id FROM memory_atoms WHERE atom_id = 'atom-a'", ())['atom_id'], 'atom-a')
        migration = upgraded.read_one("SELECT * FROM schema_migrations WHERE migration_id = ?", (MIGRATION_V21_TO_V22_ID,))
        self.assertEqual((migration["from_version"], migration["to_version"]), (SCHEMA_VERSION_V21, SCHEMA_VERSION_V22))

    def test_failed_v22_upgrade_rolls_back_and_can_retry(self):
        runtime = self.open_runtime()
        runtime.close()
        with closing(sqlite3.connect(self.database)) as connection:
            remove_v22(connection)
            connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = 'schema_version'", (SCHEMA_VERSION_V21,))
            connection.commit()
        with patch("companion_v2.storage.runtime.V22_MIGRATION_STATEMENTS",
                   ("CREATE TABLE role_growth_temp(id TEXT)", "INVALID UPGRADE")):
            with self.assertRaises(StorageOpenError):
                StorageRuntime.open(data_root=self.root, installation_id="installation-a")
        self.assertEqual(self.version(), SCHEMA_VERSION_V21)
        with closing(sqlite3.connect(self.database)) as connection:
            names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            self.assertNotIn("role_growth_temp", names)
            self.assertNotIn("role_growth_items", names)
            self.assertIsNone(connection.execute("SELECT 1 FROM schema_migrations WHERE migration_id = ?",
                                                 (MIGRATION_V21_TO_V22_ID,)).fetchone())

    def test_v30_schema_migrates_to_v31_growth_progress_reviews(self):
        runtime = self.open_runtime()
        runtime.close()
        with closing(sqlite3.connect(self.database)) as connection:
            remove_v31(connection)
            connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = 'schema_version'", (SCHEMA_VERSION_V30,))
            connection.commit()

        upgraded = self.open_runtime()
        self.assertEqual(upgraded.read_one(
            "SELECT value FROM runtime_metadata WHERE key = 'schema_version'", (),
        )["value"], SCHEMA_VERSION)
        names = {row["name"] for row in upgraded.read_all(
            "SELECT name FROM sqlite_master WHERE type = 'table'", (),
        )}
        self.assertTrue(V31_REQUIRED_TABLES.issubset(names))
        migration = upgraded.read_one(
            "SELECT * FROM schema_migrations WHERE migration_id = ?", (MIGRATION_V30_TO_V31_ID,),
        )
        self.assertEqual(migration["fingerprint"], MIGRATION_V30_TO_V31_FINGERPRINT)

    def test_failed_v31_upgrade_rolls_back_and_can_retry(self):
        runtime = self.open_runtime()
        runtime.close()
        with closing(sqlite3.connect(self.database)) as connection:
            remove_v31(connection)
            connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = 'schema_version'", (SCHEMA_VERSION_V30,))
            connection.commit()
        with patch(
            "companion_v2.storage.runtime.V31_MIGRATION_STATEMENTS",
            (V31_MIGRATION_STATEMENTS[0], "INVALID V31 MIGRATION"),
        ):
            with self.assertRaises(StorageOpenError):
                StorageRuntime.open(data_root=self.root, installation_id="installation-a")
        self.assertEqual(self.version(), SCHEMA_VERSION_V30)
        with closing(sqlite3.connect(self.database)) as connection:
            names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            self.assertNotIn("role_growth_progress_reviews", names)
            self.assertIsNone(connection.execute(
                "SELECT 1 FROM schema_migrations WHERE migration_id = ?", (MIGRATION_V30_TO_V31_ID,),
            ).fetchone())
        self.assertEqual(self.open_runtime().read_one(
            "SELECT value FROM runtime_metadata WHERE key = 'schema_version'", (),
        )["value"], SCHEMA_VERSION)

    def test_v31_schema_migrates_to_v32_voice_plan_and_cooldown(self):
        self.open_runtime().close()
        with closing(sqlite3.connect(self.database)) as connection:
            remove_v32(connection)
            connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = 'schema_version'", (SCHEMA_VERSION_V31,))
            connection.commit()
        upgraded = self.open_runtime()
        self.assertEqual(self.version(), SCHEMA_VERSION)
        names = {row["name"] for row in upgraded.read_all("SELECT name FROM sqlite_master WHERE type = 'table'", ())}
        self.assertTrue(V32_REQUIRED_TABLES.issubset(names))
        migration = upgraded.read_one("SELECT * FROM schema_migrations WHERE migration_id = ?", (MIGRATION_V31_TO_V32_ID,))
        self.assertEqual(migration["fingerprint"], MIGRATION_V31_TO_V32_FINGERPRINT)

    def test_failed_v32_upgrade_rolls_back_and_can_retry(self):
        self.open_runtime().close()
        with closing(sqlite3.connect(self.database)) as connection:
            remove_v32(connection)
            connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = 'schema_version'", (SCHEMA_VERSION_V31,))
            connection.commit()
        with patch("companion_v2.storage.runtime.V32_MIGRATION_STATEMENTS",
                   (V32_MIGRATION_STATEMENTS[0], "INVALID V32 MIGRATION")):
            with self.assertRaises(StorageOpenError):
                StorageRuntime.open(data_root=self.root, installation_id="installation-a")
        self.assertEqual(self.version(), SCHEMA_VERSION_V31)
        with closing(sqlite3.connect(self.database)) as connection:
            names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            self.assertNotIn("conversation_voice_parts", names)
            self.assertIsNone(connection.execute("SELECT 1 FROM schema_migrations WHERE migration_id = ?",
                                                 (MIGRATION_V31_TO_V32_ID,)).fetchone())
        self.assertEqual(self.open_runtime().read_one("SELECT value FROM runtime_metadata WHERE key = 'schema_version'", ())[
            "value"], SCHEMA_VERSION)

    def test_v32_schema_migrates_to_v33_weather_baselines(self):
        self.open_runtime().close()
        with closing(sqlite3.connect(self.database)) as connection:
            remove_v33(connection)
            connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = 'schema_version'", (SCHEMA_VERSION_V32,))
            connection.commit()
        upgraded = self.open_runtime()
        self.assertEqual(self.version(), SCHEMA_VERSION)
        names = {row["name"] for row in upgraded.read_all("SELECT name FROM sqlite_master WHERE type = 'table'", ())}
        self.assertTrue(V33_REQUIRED_TABLES.issubset(names))
        migration = upgraded.read_one("SELECT * FROM schema_migrations WHERE migration_id = ?", (MIGRATION_V32_TO_V33_ID,))
        self.assertEqual(migration["fingerprint"], MIGRATION_V32_TO_V33_FINGERPRINT)

    def test_failed_v33_upgrade_rolls_back_and_can_retry(self):
        self.open_runtime().close()
        with closing(sqlite3.connect(self.database)) as connection:
            remove_v33(connection)
            connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = 'schema_version'", (SCHEMA_VERSION_V32,))
            connection.commit()
        with patch("companion_v2.storage.runtime.V33_MIGRATION_STATEMENTS",
                   (V33_MIGRATION_STATEMENTS[0], "INVALID V33 MIGRATION")):
            with self.assertRaises(StorageOpenError):
                StorageRuntime.open(data_root=self.root, installation_id="installation-a")
        self.assertEqual(self.version(), SCHEMA_VERSION_V32)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertIsNone(connection.execute("SELECT 1 FROM sqlite_master WHERE name = 'proactive_weather_samples'").fetchone())
            self.assertIsNone(connection.execute("SELECT 1 FROM schema_migrations WHERE migration_id = ?",
                                                 (MIGRATION_V32_TO_V33_ID,)).fetchone())
        self.assertEqual(self.open_runtime().read_one("SELECT value FROM runtime_metadata WHERE key = 'schema_version'", ())[
            "value"], SCHEMA_VERSION)

    def test_v33_upgrades_to_v34_official_alert_ledger(self):
        self.open_runtime().close()
        with closing(sqlite3.connect(self.database)) as connection:
            remove_v34(connection)
            connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = 'schema_version'", (SCHEMA_VERSION_V33,))
            connection.commit()
        upgraded = self.open_runtime()
        self.assertEqual(self.version(), SCHEMA_VERSION)
        names = {row["name"] for row in upgraded.read_all("SELECT name FROM sqlite_master WHERE type = 'table'", ())}
        self.assertTrue(V34_REQUIRED_TABLES.issubset(names))
        self.assertEqual(upgraded.read_one("SELECT count(*) AS n FROM proactive_official_alerts", ())["n"], 0)
        self.assertEqual(upgraded.read_one("SELECT state FROM outbox", ())["state"], "unknown")
        migration = upgraded.read_one("SELECT * FROM schema_migrations WHERE migration_id = ?", (MIGRATION_V33_TO_V34_ID,))
        self.assertEqual(migration["fingerprint"], MIGRATION_V33_TO_V34_FINGERPRINT)

    def test_failed_v34_upgrade_rolls_back_and_can_retry(self):
        self.open_runtime().close()
        with closing(sqlite3.connect(self.database)) as connection:
            remove_v34(connection)
            connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = 'schema_version'", (SCHEMA_VERSION_V33,))
            connection.commit()
        with patch("companion_v2.storage.runtime.V34_MIGRATION_STATEMENTS",
                   (V34_MIGRATION_STATEMENTS[0], "INVALID V34 MIGRATION")):
            with self.assertRaises(StorageOpenError):
                StorageRuntime.open(data_root=self.root, installation_id="installation-a")
        self.assertEqual(self.version(), SCHEMA_VERSION_V33)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertIsNone(connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name = 'proactive_official_alert_polls'").fetchone())
            self.assertIsNone(connection.execute(
                "SELECT 1 FROM schema_migrations WHERE migration_id = ?", (MIGRATION_V33_TO_V34_ID,)).fetchone())
        self.assertEqual(self.open_runtime().read_one(
            "SELECT value FROM runtime_metadata WHERE key = 'schema_version'", ())["value"], SCHEMA_VERSION)

    def test_v34_upgrades_to_v35_without_backfilling_old_voice_outcomes(self):
        self.open_runtime().close()
        with closing(sqlite3.connect(self.database)) as connection:
            remove_v35(connection)
            connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = 'schema_version'", (SCHEMA_VERSION_V34,))
            connection.commit()
        upgraded = self.open_runtime()
        names = {row["name"] for row in upgraded.read_all("SELECT name FROM sqlite_master WHERE type = 'table'", ())}
        self.assertTrue(V35_REQUIRED_TABLES.issubset(names))
        self.assertTrue(V34_REQUIRED_TABLES.issubset(names))
        self.assertEqual(upgraded.read_one("SELECT count(*) AS n FROM conversation_voice_diagnostics", ())["n"], 0)
        migration = upgraded.read_one("SELECT * FROM schema_migrations WHERE migration_id = ?", (MIGRATION_V34_TO_V35_ID,))
        self.assertEqual(migration["fingerprint"], MIGRATION_V34_TO_V35_FINGERPRINT)

    def test_failed_v35_upgrade_rolls_back_and_can_retry(self):
        self.open_runtime().close()
        with closing(sqlite3.connect(self.database)) as connection:
            remove_v35(connection)
            connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = 'schema_version'", (SCHEMA_VERSION_V34,))
            connection.commit()
        with patch("companion_v2.storage.runtime.V35_MIGRATION_STATEMENTS",
                   (V35_MIGRATION_STATEMENTS[0], "INVALID V35 MIGRATION")):
            with self.assertRaises(StorageOpenError):
                StorageRuntime.open(data_root=self.root, installation_id="installation-a")
        self.assertEqual(self.version(), SCHEMA_VERSION_V34)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertIsNone(connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name = 'conversation_voice_diagnostics'").fetchone())
            self.assertIsNone(connection.execute("SELECT 1 FROM schema_migrations WHERE migration_id = ?",
                                                 (MIGRATION_V34_TO_V35_ID,)).fetchone())
        self.assertEqual(self.open_runtime().read_one("SELECT value FROM runtime_metadata WHERE key = 'schema_version'", ())[
            "value"], SCHEMA_VERSION)

    def test_failed_v36_upgrade_rolls_back_and_can_retry(self):
        self.open_runtime().close()
        with closing(sqlite3.connect(self.database)) as connection:
            remove_v36(connection)
            connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = 'schema_version'", (SCHEMA_VERSION_V35,))
            connection.commit()
        with patch("companion_v2.storage.runtime.V36_MIGRATION_STATEMENTS",
                   (V36_MIGRATION_STATEMENTS[0], "INVALID V36 MIGRATION")):
            with self.assertRaises(StorageOpenError):
                StorageRuntime.open(data_root=self.root, installation_id="installation-a")
        self.assertEqual(self.version(), SCHEMA_VERSION_V35)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertIsNone(connection.execute("SELECT 1 FROM sqlite_master WHERE name = 'model_call_failures_v36'").fetchone())
            self.assertIsNone(connection.execute("SELECT 1 FROM schema_migrations WHERE migration_id = ?", (MIGRATION_V35_TO_V36_ID,)).fetchone())
        self.assertEqual(self.open_runtime().read_one("SELECT value FROM runtime_metadata WHERE key = 'schema_version'", ())["value"], SCHEMA_VERSION)

    def test_partial_v30_growth_progress_table_fails_closed(self):
        runtime = self.open_runtime()
        runtime.close()
        with closing(sqlite3.connect(self.database)) as connection:
            remove_v31(connection)
            connection.execute("CREATE TABLE role_growth_progress_reviews(review_id TEXT)")
            connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = 'schema_version'", (SCHEMA_VERSION_V30,))
            connection.commit()
        with self.assertRaises(StorageOpenError):
            StorageRuntime.open(data_root=self.root, installation_id="installation-a")

    def test_partial_v22_growth_table_in_v21_fails_closed(self):
        runtime = self.open_runtime()
        runtime.close()
        with closing(sqlite3.connect(self.database)) as connection:
            remove_v22(connection)
            connection.execute("CREATE TABLE role_growth_items(growth_id TEXT)")
            connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = 'schema_version'", (SCHEMA_VERSION_V21,))
            connection.commit()
        with self.assertRaises(StorageOpenError):
            StorageRuntime.open(data_root=self.root, installation_id="installation-a")

    def test_failed_migration_rolls_back_ddl_and_version_then_can_retry(self) -> None:
        with patch(
            "companion_v2.storage.runtime.V2_MIGRATION_STATEMENTS",
            (*V2_MIGRATION_STATEMENTS[:3], "INVALID MIGRATION STATEMENT"),
        ):
            with self.assertRaises(StorageOpenError):
                self.open_runtime()
        self.assert_v1_unchanged()
        self.assertEqual(self.open_runtime().read_one("SELECT count(*) AS n FROM outbox", ())["n"], 1)

    def test_unknown_schema_and_foreign_installation_do_not_migrate(self) -> None:
        with self.assertRaises(StorageOpenError):
            StorageRuntime.open(data_root=self.root, installation_id="installation-other")
        self.assert_v1_unchanged()
        self.mutate("UPDATE runtime_metadata SET value = ? WHERE key = 'schema_version'", ("future-schema",))
        with self.assertRaises(StorageOpenError):
            self.open_runtime()
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute("SELECT value FROM runtime_metadata WHERE key = 'schema_version'").fetchone()[0], "future-schema")
            self.assertIsNone(connection.execute("SELECT name FROM sqlite_master WHERE name = 'schema_migrations'").fetchone())

    def test_partial_v1_and_column_mismatch_fail_before_migration(self) -> None:
        self.mutate("ALTER TABLE inbox RENAME COLUMN payload_hash TO wrong_column")
        with self.assertRaises(StorageOpenError):
            self.open_runtime()
        self.assert_v1_unchanged()

    def test_missing_required_index_is_not_promoted_to_a_valid_schema(self) -> None:
        self.mutate("DROP INDEX sources_scope_idx")
        with self.assertRaises(StorageOpenError):
            self.open_runtime()
        self.assert_v1_unchanged()
        self.mutate("CREATE INDEX sources_scope_idx ON sources(installation_id, platform, scope_kind, native_scope_id)")
        self.mutate("CREATE TABLE capability_installations(extension_id TEXT)")
        with self.assertRaises(StorageOpenError):
            self.open_runtime()
        self.assert_v1_unchanged()

    def test_missing_table_broken_fk_and_tampered_migration_record_fail_closed(self) -> None:
        self.mutate("DELETE FROM operations")
        with self.assertRaises(StorageOpenError):
            self.open_runtime()
        self.assert_v1_unchanged()
        self.mutate("DELETE FROM outbox")
        self.open_runtime().close()
        self.mutate("UPDATE schema_migrations SET fingerprint = 'different' WHERE migration_id = ?", (MIGRATION_V1_TO_V2_ID,))
        with self.assertRaises(StorageOpenError):
            self.open_runtime()
        self.mutate("UPDATE schema_migrations SET fingerprint = ? WHERE migration_id = ?", (MIGRATION_V1_TO_V2_FINGERPRINT, MIGRATION_V1_TO_V2_ID))
        self.mutate("DROP TABLE screen_feedback_anchors")
        with self.assertRaises(StorageOpenError):
            self.open_runtime()
