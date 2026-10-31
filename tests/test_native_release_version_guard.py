import json
import tempfile
import unittest
from pathlib import Path

from tools.build_v2_release import (
    NATIVE_CAPABILITY_BASELINE,
    NATIVE_HASH_FILES,
    NATIVE_HASH_GLOBS,
    ROOT,
    VERSION,
    native_artifact_hashes,
    native_artifact_source_files,
    native_baseline_document,
    refresh_native_baseline,
    verify_native_baseline,
)


class NativeReleaseVersionGuardTests(unittest.TestCase):
    def source_tree(self, root):
        names = {name for group in NATIVE_HASH_FILES.values() for name in group}
        names.update(directory + "/package.py" for directory in NATIVE_HASH_GLOBS.values())
        for name in names:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"VALUE = 1\n")

    def test_baseline_tracks_all_seven_runtime_artifact_hashes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.source_tree(root)
            hashes = native_artifact_hashes(native_artifact_source_files(root))
            self.assertEqual(set(hashes), {"content", "game", "image", "live", "reality", "screen", "together"})
            baseline = native_baseline_document("2.1.1", hashes)
            verify_native_baseline("2.1.1", hashes, baseline)

    def test_repository_baseline_matches_release_sources(self):
        baseline = json.loads((ROOT / NATIVE_CAPABILITY_BASELINE).read_text(encoding="utf-8"))
        hashes = native_artifact_hashes(native_artifact_source_files(ROOT))
        verify_native_baseline(VERSION, hashes, baseline)

    def test_hash_change_requires_version_advance_before_baseline_refresh(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.source_tree(root)
            baseline = refresh_native_baseline(root, "2.1.1")
            screen = root / NATIVE_HASH_FILES["screen"][0]
            screen.write_bytes(b"VALUE = 2\n")
            current = native_artifact_hashes(native_artifact_source_files(root))

            with self.assertRaisesRegex(ValueError, "without a package version bump"):
                verify_native_baseline("2.1.1", current, baseline)
            with self.assertRaisesRegex(ValueError, "must advance"):
                refresh_native_baseline(root, "2.1.1")

            refreshed = refresh_native_baseline(root, "2.1.2")
            self.assertEqual(json.loads((root / NATIVE_CAPABILITY_BASELINE).read_text(encoding="utf-8")), refreshed)
            verify_native_baseline("2.1.2", current, refreshed)

    def test_image_hash_change_requires_version_advance_before_baseline_refresh(self):
        # 7.0.7 -> 7.0.8 shipped image source changes after the previous baseline was
        # pinned. Keep the guard proven for image itself, not only for a generic file.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.source_tree(root)
            baseline = refresh_native_baseline(root, "7.0.8")
            image = root / "astrbot_host/native_extensions/image_native/package.py"
            image.write_bytes(b"VALUE = 2\n")
            current = native_artifact_hashes(native_artifact_source_files(root))
            self.assertNotEqual(current["image"], baseline["artifact_hashes"]["image"])

            with self.assertRaisesRegex(ValueError, "without a package version bump: image"):
                verify_native_baseline("7.0.8", current, baseline)
            with self.assertRaisesRegex(ValueError, "must advance"):
                refresh_native_baseline(root, "7.0.8")

            refreshed = refresh_native_baseline(root, "7.0.9")
            self.assertEqual(refreshed["package_version"], "7.0.9")
            verify_native_baseline("7.0.9", current, refreshed)

    def test_build_requires_baseline_refresh_after_release_version_bump(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.source_tree(root)
            baseline = refresh_native_baseline(root, "2.1.1")
            hashes = native_artifact_hashes(native_artifact_source_files(root))
            with self.assertRaisesRegex(ValueError, "baseline version is stale"):
                verify_native_baseline("2.1.2", hashes, baseline)

    def test_letter_hotfix_advances_without_allowing_downgrades_or_unversioned_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.source_tree(root)
            refresh_native_baseline(root, "7.0.4")
            screen = root / NATIVE_HASH_FILES["screen"][0]
            screen.write_bytes(b"VALUE = 2\n")
            refreshed = refresh_native_baseline(root, "7.0.4a")
            self.assertEqual(refreshed["package_version"], "7.0.4a")
            verify_native_baseline("7.0.4a", native_artifact_hashes(native_artifact_source_files(root)), refreshed)
            with self.assertRaisesRegex(ValueError, "must advance"):
                refresh_native_baseline(root, "7.0.4")
            screen.write_bytes(b"VALUE = 3\n")
            with self.assertRaisesRegex(ValueError, "must advance"):
                refresh_native_baseline(root, "7.0.4a")
            self.assertEqual(refresh_native_baseline(root, "7.0.4b")["package_version"], "7.0.4b")
            self.assertEqual(refresh_native_baseline(root, "7.0.5")["package_version"], "7.0.5")


if __name__ == "__main__":
    unittest.main()
