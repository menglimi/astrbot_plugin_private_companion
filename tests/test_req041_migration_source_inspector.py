from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from migration_source_inspector import (
    MigrationSourceInspectionError,
    inspect_migration_sources,
)

FIXTURE = (
    Path(__file__).parent / "fixtures" / "req041" / "companion-v6.0.8-sanitized.json"
)


class MigrationSourceInspectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _copy_fixture(self, name: str = "companions.json") -> Path:
        target = self.data_dir / name
        shutil.copyfile(FIXTURE, target)
        return target

    def _sqlite_from_fixture(
        self,
        *,
        schema_version: int = 1,
        name: str | None = None,
        tombstones: tuple[str, ...] = (),
        include_write_guards: bool = True,
    ) -> Path:
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        target = self.data_dir / (name or f"companions-v{schema_version}.db")
        connection = sqlite3.connect(target)
        try:
            if schema_version == 1:
                connection.execute(
                    "CREATE TABLE store_sections ("
                    "section_name TEXT PRIMARY KEY,payload_json TEXT NOT NULL,updated_at REAL NOT NULL,"
                    "checksum TEXT DEFAULT '',schema_version INTEGER DEFAULT 1)"
                )
                connection.executemany(
                    "INSERT INTO store_sections VALUES(?,?,0,'',1)",
                    [
                        (key, json.dumps(value, ensure_ascii=False))
                        for key, value in payload.items()
                    ],
                )
            else:
                write_guards = (
                    ",CONSTRAINT store_sections_schema_v2 CHECK(schema_version=2)"
                    ",CONSTRAINT store_sections_positive_revision CHECK(revision>0)"
                    ",CONSTRAINT store_sections_deleted_flag CHECK(is_deleted IN (0,1))"
                    if include_write_guards
                    else ""
                )
                connection.execute(
                    "CREATE TABLE store_sections ("
                    "section_name TEXT NOT NULL PRIMARY KEY,payload_json TEXT NOT NULL,updated_at REAL NOT NULL,"
                    "checksum TEXT NOT NULL DEFAULT '',schema_version INTEGER NOT NULL DEFAULT 2,"
                    "revision INTEGER NOT NULL DEFAULT 0,is_deleted INTEGER NOT NULL DEFAULT 0"
                    f"{write_guards})"
                )
                connection.executemany(
                    "INSERT INTO store_sections VALUES(?,?,0,'',2,1,0)",
                    [
                        (key, json.dumps(value, ensure_ascii=False))
                        for key, value in payload.items()
                    ],
                )
                connection.executemany(
                    "INSERT INTO store_sections VALUES(?,?,0,'',2,2,1)",
                    [(section_name, "null") for section_name in tombstones],
                )
                connection.execute(
                    "CREATE TABLE store_meta ("
                    "meta_key TEXT NOT NULL PRIMARY KEY,meta_value TEXT NOT NULL)"
                )
                connection.executemany(
                    "INSERT INTO store_meta VALUES(?,?)",
                    (
                        ("initialized", "1"),
                        ("next_revision", "3" if tombstones else "2"),
                    ),
                )
                connection.execute("PRAGMA user_version=2")
            connection.commit()
        finally:
            connection.close()
        return target

    def test_detects_sanitized_v608_json_without_recording_user_values(self) -> None:
        source = self._copy_fixture()
        inventory = inspect_migration_sources(self.data_dir, [source])
        self.assertEqual("req041.source_inventory.v1", inventory["schema"])
        self.assertEqual(1, inventory["store_version"])
        self.assertEqual({"json": 1, "sqlite": 0}, inventory["formats"])
        self.assertTrue(inventory["all_have_unified_person"])
        encoded = json.dumps(inventory, ensure_ascii=False)
        for private_value in (
            "10001",
            "20001",
            "Fixture Owner",
            "fixture-private-sentinel",
        ):
            self.assertNotIn(private_value, encoded)

    def test_json_and_sqlite_share_store_version_but_have_distinct_contract_fingerprints(
        self,
    ) -> None:
        json_source = self._copy_fixture()
        json_inventory = inspect_migration_sources(self.data_dir, [json_source])
        sqlite_source = self._sqlite_from_fixture()
        sqlite_inventory = inspect_migration_sources(self.data_dir, [sqlite_source])
        self.assertEqual(1, sqlite_inventory["store_version"])
        self.assertEqual([1], sqlite_inventory["section_schema_versions"])
        self.assertEqual({"json": 0, "sqlite": 1}, sqlite_inventory["formats"])
        self.assertNotEqual(
            json_inventory["source_schema_version"],
            sqlite_inventory["source_schema_version"],
        )

    def test_sqlite_v1_and_v2_normalize_to_the_same_inventory_contract(self) -> None:
        v1_inventory = inspect_migration_sources(
            self.data_dir,
            [self._sqlite_from_fixture(schema_version=1)],
        )
        v2_inventory = inspect_migration_sources(
            self.data_dir,
            [self._sqlite_from_fixture(schema_version=2)],
        )
        self.assertEqual([1], v1_inventory["section_schema_versions"])
        self.assertEqual([1], v2_inventory["section_schema_versions"])
        self.assertEqual(v1_inventory["fingerprint"], v2_inventory["fingerprint"])
        self.assertEqual(
            v1_inventory["source_schema_version"],
            v2_inventory["source_schema_version"],
        )

    def test_sqlite_v2_tombstones_do_not_affect_inventory(self) -> None:
        baseline = inspect_migration_sources(
            self.data_dir,
            [self._sqlite_from_fixture(schema_version=2, name="baseline.db")],
        )
        with_tombstone = inspect_migration_sources(
            self.data_dir,
            [
                self._sqlite_from_fixture(
                    schema_version=2,
                    name="with-tombstone.db",
                    tombstones=("retired_section",),
                )
            ],
        )
        for key in (
            "fingerprint",
            "source_schema_version",
            "section_count_min",
            "section_count_max",
            "all_have_unified_person",
            "all_have_persona_lifecycle",
        ):
            self.assertEqual(baseline[key], with_tombstone[key])

    def test_accepts_established_internal_underscore_section(self) -> None:
        sqlite_source = self._sqlite_from_fixture()
        connection = sqlite3.connect(sqlite_source)
        try:
            connection.execute(
                "INSERT INTO store_sections VALUES(?,?,0,'',1)",
                ("_req041_memory_scope_state", json.dumps({})),
            )
            connection.commit()
        finally:
            connection.close()
        inventory = inspect_migration_sources(self.data_dir, [sqlite_source])
        self.assertEqual(6, inventory["section_count_min"])

    def test_rejects_invalid_json_root_missing_sections_and_unsupported_version(
        self,
    ) -> None:
        cases = (
            ("broken.json", "{", "migration_source_json_invalid"),
            ("list.json", "[]", "migration_source_root_invalid"),
            (
                "missing.json",
                '{"version":1,"users":{}}',
                "migration_source_required_section_missing",
            ),
            (
                "future.json",
                '{"version":2,"users":{},"groups":{}}',
                "migration_source_store_version_unsupported",
            ),
        )
        for name, content, error in cases:
            with self.subTest(name=name):
                path = self.data_dir / name
                path.write_text(content, encoding="utf-8")
                with self.assertRaisesRegex(MigrationSourceInspectionError, error):
                    inspect_migration_sources(self.data_dir, [path])

    def test_rejects_sqlite_without_contract_bad_columns_or_invalid_section_json(
        self,
    ) -> None:
        missing = self.data_dir / "missing.db"
        connection = sqlite3.connect(missing)
        connection.execute("CREATE TABLE unrelated(value TEXT)")
        connection.commit()
        connection.close()
        with self.assertRaisesRegex(
            MigrationSourceInspectionError, "migration_source_sqlite_contract_missing"
        ):
            inspect_migration_sources(self.data_dir, [missing])

        bad_columns = self.data_dir / "bad-columns.db"
        connection = sqlite3.connect(bad_columns)
        connection.execute(
            "CREATE TABLE store_sections(section_name TEXT,payload_json TEXT)"
        )
        connection.commit()
        connection.close()
        with self.assertRaisesRegex(
            MigrationSourceInspectionError, "migration_source_sqlite_contract_invalid"
        ):
            inspect_migration_sources(self.data_dir, [bad_columns])

        invalid_payload = self._sqlite_from_fixture(name="invalid-payload.db")
        connection = sqlite3.connect(invalid_payload)
        connection.execute(
            "UPDATE store_sections SET payload_json='{' WHERE section_name='users'"
        )
        connection.commit()
        connection.close()
        with self.assertRaisesRegex(
            MigrationSourceInspectionError, "migration_source_section_json_invalid"
        ):
            inspect_migration_sources(self.data_dir, [invalid_payload])

    def test_rejects_partial_v2_contract_and_invalid_tombstone(self) -> None:
        partial_v2 = self.data_dir / "partial-v2.db"
        connection = sqlite3.connect(partial_v2)
        connection.execute(
            "CREATE TABLE store_sections ("
            "section_name TEXT PRIMARY KEY,payload_json TEXT NOT NULL,updated_at REAL NOT NULL,"
            "checksum TEXT DEFAULT '',schema_version INTEGER DEFAULT 2,revision INTEGER DEFAULT 0)"
        )
        connection.commit()
        connection.close()
        with self.assertRaisesRegex(
            MigrationSourceInspectionError, "migration_source_sqlite_contract_invalid"
        ):
            inspect_migration_sources(self.data_dir, [partial_v2])

        invalid_flag = self._sqlite_from_fixture(
            schema_version=2, name="invalid-flag.db"
        )
        connection = sqlite3.connect(invalid_flag)
        connection.execute("PRAGMA ignore_check_constraints=ON")
        connection.execute(
            "UPDATE store_sections SET is_deleted=2 WHERE section_name='users'"
        )
        connection.commit()
        connection.close()
        with self.assertRaisesRegex(
            MigrationSourceInspectionError, "migration_source_tombstone_invalid"
        ):
            inspect_migration_sources(self.data_dir, [invalid_flag])

        invalid_payload = self._sqlite_from_fixture(
            schema_version=2,
            name="invalid-tombstone-payload.db",
            tombstones=("retired_section",),
        )
        connection = sqlite3.connect(invalid_payload)
        connection.execute(
            "UPDATE store_sections SET payload_json='{}' WHERE section_name='retired_section'"
        )
        connection.commit()
        connection.close()
        with self.assertRaisesRegex(
            MigrationSourceInspectionError, "migration_source_tombstone_invalid"
        ):
            inspect_migration_sources(self.data_dir, [invalid_payload])

    def test_rejects_schema_marker_and_physical_contract_mismatch(self) -> None:
        v1_with_v2_marker = self._sqlite_from_fixture(
            schema_version=1,
            name="v1-with-v2-marker.db",
        )
        connection = sqlite3.connect(v1_with_v2_marker)
        connection.execute("PRAGMA user_version=2")
        connection.commit()
        connection.close()
        with self.assertRaisesRegex(
            MigrationSourceInspectionError,
            "migration_source_sqlite_contract_invalid",
        ):
            inspect_migration_sources(self.data_dir, [v1_with_v2_marker])

        v2_with_v1_marker = self._sqlite_from_fixture(
            schema_version=2,
            name="v2-with-v1-marker.db",
        )
        connection = sqlite3.connect(v2_with_v1_marker)
        connection.execute("PRAGMA user_version=1")
        connection.commit()
        connection.close()
        with self.assertRaisesRegex(
            MigrationSourceInspectionError,
            "migration_source_sqlite_contract_invalid",
        ):
            inspect_migration_sources(self.data_dir, [v2_with_v1_marker])

    def test_sqlite_v2_requires_valid_meta_primary_key_and_revision_contract(
        self,
    ) -> None:
        missing_meta = self._sqlite_from_fixture(
            schema_version=2,
            name="missing-meta.db",
        )
        connection = sqlite3.connect(missing_meta)
        connection.execute("DROP TABLE store_meta")
        connection.commit()
        connection.close()
        with self.assertRaisesRegex(
            MigrationSourceInspectionError,
            "migration_source_sqlite_contract_invalid",
        ):
            inspect_migration_sources(self.data_dir, [missing_meta])

        stale_meta = self._sqlite_from_fixture(
            schema_version=2,
            name="stale-meta.db",
        )
        connection = sqlite3.connect(stale_meta)
        connection.execute(
            "UPDATE store_meta SET meta_value='1' WHERE meta_key='next_revision'"
        )
        connection.commit()
        connection.close()
        with self.assertRaisesRegex(
            MigrationSourceInspectionError,
            "migration_source_sqlite_contract_invalid",
        ):
            inspect_migration_sources(self.data_dir, [stale_meta])

        invalid_revision = self._sqlite_from_fixture(
            schema_version=2,
            name="invalid-revision.db",
        )
        connection = sqlite3.connect(invalid_revision)
        connection.execute(
            "UPDATE store_sections SET revision=1.5 WHERE section_name='users'"
        )
        connection.commit()
        connection.close()
        with self.assertRaisesRegex(
            MigrationSourceInspectionError,
            "migration_source_revision_invalid",
        ):
            inspect_migration_sources(self.data_dir, [invalid_revision])

    def test_sqlite_v2_rejects_unexpected_extensions_and_missing_guards(self) -> None:
        unexpected_table = self._sqlite_from_fixture(
            schema_version=2,
            name="unexpected-table.db",
        )
        connection = sqlite3.connect(unexpected_table)
        connection.execute("CREATE TABLE unrelated(value TEXT)")
        connection.commit()
        connection.close()
        with self.assertRaisesRegex(
            MigrationSourceInspectionError,
            "migration_source_sqlite_contract_invalid",
        ):
            inspect_migration_sources(self.data_dir, [unexpected_table])

        extra_meta = self._sqlite_from_fixture(
            schema_version=2,
            name="extra-meta.db",
        )
        connection = sqlite3.connect(extra_meta)
        connection.execute("INSERT INTO store_meta VALUES('future_key','1')")
        connection.commit()
        connection.close()
        with self.assertRaisesRegex(
            MigrationSourceInspectionError,
            "migration_source_sqlite_contract_invalid",
        ):
            inspect_migration_sources(self.data_dir, [extra_meta])

        missing_guards = self._sqlite_from_fixture(
            schema_version=2,
            name="missing-guards.db",
            include_write_guards=False,
        )
        with self.assertRaisesRegex(
            MigrationSourceInspectionError,
            "migration_source_sqlite_contract_invalid",
        ):
            inspect_migration_sources(self.data_dir, [missing_guards])

    def test_sqlite_v2_validates_checksum_and_bounds_physical_rows(self) -> None:
        invalid_checksum = self._sqlite_from_fixture(
            schema_version=2,
            name="invalid-checksum.db",
        )
        connection = sqlite3.connect(invalid_checksum)
        connection.execute(
            "UPDATE store_sections SET checksum=? WHERE section_name='users'",
            ("0" * 64,),
        )
        connection.commit()
        connection.close()
        with self.assertRaisesRegex(
            MigrationSourceInspectionError,
            "migration_source_checksum_invalid",
        ):
            inspect_migration_sources(self.data_dir, [invalid_checksum])

        excessive_rows = self._sqlite_from_fixture(
            schema_version=2,
            name="excessive-rows.db",
        )
        connection = sqlite3.connect(excessive_rows)
        connection.executemany(
            "INSERT INTO store_sections VALUES(?,?,0,'',2,1,1)",
            [(f"retired_{index:04d}", "null") for index in range(513)],
        )
        connection.commit()
        connection.close()
        with self.assertRaisesRegex(
            MigrationSourceInspectionError,
            "migration_source_section_set_invalid",
        ):
            inspect_migration_sources(self.data_dir, [excessive_rows])

    def test_sqlite_v2_legacy_empty_tombstone_checksum_is_supported(self) -> None:
        source = self._sqlite_from_fixture(
            schema_version=2,
            name="legacy-tombstone.db",
            tombstones=("retired_section",),
        )

        inventory = inspect_migration_sources(self.data_dir, [source])

        self.assertEqual({"json": 0, "sqlite": 1}, inventory["formats"])
        self.assertEqual([1], inventory["section_schema_versions"])

    def test_rejects_symlink_and_path_escape(self) -> None:
        outside = self.data_dir.parent / f"{self.data_dir.name}-outside.json"
        shutil.copyfile(FIXTURE, outside)
        self.addCleanup(outside.unlink, missing_ok=True)
        link = self.data_dir / "linked.json"
        link.symlink_to(outside)
        with self.assertRaisesRegex(
            MigrationSourceInspectionError, "migration_source_file_invalid"
        ):
            inspect_migration_sources(self.data_dir, [link])
        with self.assertRaisesRegex(
            MigrationSourceInspectionError, "migration_source_path_invalid"
        ):
            inspect_migration_sources(self.data_dir, [outside])


if __name__ == "__main__":
    unittest.main()
