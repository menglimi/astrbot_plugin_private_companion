"""Forward-compatible additive schema columns must not hide data."""
import sqlite3
import unittest

from companion_v2.storage.runtime import (
    FORWARD_COMPATIBLE_SCHEMA_VERSIONS, StorageOpenError, _validate_table_shapes,
)

STATEMENTS = ("CREATE TABLE sample (id TEXT PRIMARY KEY, value TEXT NOT NULL)",)
TABLES = frozenset({"sample"})


class ForwardSchemaTests(unittest.TestCase):
    def test_appended_defaulted_column_is_accepted_for_newer_data_root(self):
        connection = sqlite3.connect(":memory:")
        self.addCleanup(connection.close)
        connection.execute(STATEMENTS[0])
        connection.execute("ALTER TABLE sample ADD COLUMN occurred_at TEXT NOT NULL DEFAULT ''")
        _validate_table_shapes(connection, STATEMENTS, TABLES, allow_extra=True)

    def test_extra_column_is_still_rejected_for_the_current_schema(self):
        connection = sqlite3.connect(":memory:")
        self.addCleanup(connection.close)
        connection.execute(STATEMENTS[0])
        connection.execute("ALTER TABLE sample ADD COLUMN occurred_at TEXT NOT NULL DEFAULT ''")
        with self.assertRaises(StorageOpenError):
            _validate_table_shapes(connection, STATEMENTS, TABLES)

    def test_v69_and_v70_are_recognized_as_additive_forward_schemas(self):
        self.assertIn("companion-v2.storage.v69", FORWARD_COMPATIBLE_SCHEMA_VERSIONS)
        self.assertIn("companion-v2.storage.v70", FORWARD_COMPATIBLE_SCHEMA_VERSIONS)


if __name__ == "__main__":
    unittest.main()
