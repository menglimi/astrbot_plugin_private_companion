import sqlite3
from pathlib import Path
import tempfile
import unittest

from companion_v2.storage.image_payloads import (
    MARKER_PREFIX,
    is_marker,
    load_image,
    migrate_image_payloads,
    store_image,
)


class ImagePayloadFilesTests(unittest.TestCase):
    def test_file_marker_round_trip_and_legacy_blob_fallback(self):
        with tempfile.TemporaryDirectory() as root:
            payload = b"\x89PNG\r\n\x1a\n" + b"x" * 32
            marker = store_image(Path(root), payload, mime_type="image/png")
            self.assertTrue(is_marker(marker))
            self.assertEqual(load_image(Path(root), marker), payload)
            self.assertEqual(load_image(Path(root), payload), payload)

    def test_migration_rewrites_legacy_blob_and_is_resumable(self):
        with tempfile.TemporaryDirectory() as root:
            connection = sqlite3.connect(":memory:")
            connection.execute(
                "CREATE TABLE conversation_image_payloads ("
                "parent_id TEXT, parent_lineage TEXT, parent_revision INTEGER, position INTEGER, content BLOB)"
            )
            payload = b"\x89PNG\r\n\x1a\nlegacy"
            connection.execute(
                "INSERT INTO conversation_image_payloads VALUES ('p', 'l', 1, 0, ?)", (payload,)
            )
            result = migrate_image_payloads(Path(root), connection)
            self.assertEqual(result["migrated"], 1)
            marker = connection.execute(
                "SELECT content FROM conversation_image_payloads"
            ).fetchone()[0]
            self.assertTrue(marker.startswith(MARKER_PREFIX))
            self.assertEqual(load_image(Path(root), marker), payload)
            self.assertEqual(migrate_image_payloads(Path(root), connection)["migrated"], 0)
            connection.close()


if __name__ == "__main__":
    unittest.main()
