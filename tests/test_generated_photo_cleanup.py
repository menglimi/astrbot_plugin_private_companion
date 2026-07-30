# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

from astrbot_plugin_private_companion.proactive_message import ProactiveMessageMixin


class GeneratedPhotoCleanupHarness(ProactiveMessageMixin):
    enable_generated_photo_cleanup = True
    generated_photo_retention_days = 30
    generated_photo_max_mb = 512

    def __init__(self, data_dir: str) -> None:
        self.data_dir = data_dir
        self._last_generated_photo_cleanup_ts = 0.0


class GeneratedPhotoCleanupTests(unittest.IsolatedAsyncioTestCase):
    def _write_file(self, root: Path, name: str, size: int, age_seconds: int = 0) -> Path:
        path = root / name
        path.write_bytes(b"x" * size)
        timestamp = time.time() - age_seconds
        os.utime(path, (timestamp, timestamp))
        return path

    def test_cleanup_applies_age_and_capacity_without_touching_other_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "generated_photos"
            root.mkdir()
            harness = GeneratedPhotoCleanupHarness(temp_dir)
            harness.generated_photo_retention_days = 2
            harness.generated_photo_max_mb = 1

            expired = self._write_file(root, "expired.png", 400_000, age_seconds=3 * 86400)
            oldest = self._write_file(root, "oldest.jpg", 700_000, age_seconds=3600)
            newest = self._write_file(root, "newest.webp", 700_000, age_seconds=60)
            unrelated = self._write_file(root, "notes.txt", 32, age_seconds=10 * 86400)

            result = harness._cleanup_generated_photo_files()

            self.assertFalse(expired.exists())
            self.assertFalse(oldest.exists())
            self.assertTrue(newest.exists())
            self.assertTrue(unrelated.exists())
            self.assertEqual(result["removed_by_age"], 1)
            self.assertEqual(result["removed_by_size"], 1)

    def test_cleanup_protects_current_output_and_removes_stale_partial(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "generated_photos"
            root.mkdir()
            harness = GeneratedPhotoCleanupHarness(temp_dir)
            harness.generated_photo_retention_days = 0
            harness.generated_photo_max_mb = 1

            protected = self._write_file(root, "current.png", 1_200_000, age_seconds=3600)
            removable = self._write_file(root, "later.png", 200_000, age_seconds=60)
            partial = self._write_file(root, "download.png.part", 100, age_seconds=7200)

            result = harness._cleanup_generated_photo_files(protected_path=protected)

            self.assertTrue(protected.exists())
            self.assertFalse(removable.exists())
            self.assertFalse(partial.exists())
            self.assertEqual(result["removed_by_size"], 1)
            self.assertEqual(result["removed_partial"], 1)

    async def test_disabled_cleanup_and_interval_guard_leave_files_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "generated_photos"
            root.mkdir()
            harness = GeneratedPhotoCleanupHarness(temp_dir)
            expired = self._write_file(root, "expired.png", 100, age_seconds=40 * 86400)

            harness.enable_generated_photo_cleanup = False
            self.assertEqual(await harness._maybe_cleanup_generated_photos(force=True), {})
            self.assertTrue(expired.exists())

            harness.enable_generated_photo_cleanup = True
            harness._last_generated_photo_cleanup_ts = time.time()
            self.assertEqual(await harness._maybe_cleanup_generated_photos(), {})
            self.assertTrue(expired.exists())

    def test_zero_limits_keep_images_but_remove_stale_partial(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "generated_photos"
            root.mkdir()
            harness = GeneratedPhotoCleanupHarness(temp_dir)
            harness.generated_photo_retention_days = 0
            harness.generated_photo_max_mb = 0
            image = self._write_file(root, "archive.png", 100, age_seconds=400 * 86400)
            partial = self._write_file(root, "abandoned.webp.part", 100, age_seconds=7200)

            result = harness._cleanup_generated_photo_files()

            self.assertTrue(image.exists())
            self.assertFalse(partial.exists())
            self.assertEqual(result["removed_by_age"], 0)
            self.assertEqual(result["removed_by_size"], 0)
            self.assertEqual(result["removed_partial"], 1)


if __name__ == "__main__":
    unittest.main()
