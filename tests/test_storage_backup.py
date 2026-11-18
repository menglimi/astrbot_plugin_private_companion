"""Whole-installation backup and restore over a real V2 SQLite data root."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from companion_v2.foundation.outcome import Outcome
from companion_v2.storage import backup as module
from companion_v2.storage.backup import (
    BACKUP_FORMAT, InstallationBackup, PENDING_FILENAME, apply_pending, current_schema_version,
)


NOW = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
INSTALLATION_ID = "installation-backup-alpha"


def _open(temporary: str):
    from companion_v2.storage.runtime import StorageRuntime
    return StorageRuntime.open(data_root=Path(temporary) / "data", installation_id=INSTALLATION_ID)


def _payload(backup: InstallationBackup, snapshot_id: str, relative: str = "companion-v2.sqlite3") -> Path:
    return backup.directory / f"companion-v2-{snapshot_id}.bundle" / "payload" / Path(relative)


class BackupTests(unittest.TestCase):
    def test_readonly_probe_failure_closes_connection_before_trying_fallback(self) -> None:
        readonly, fallback = Mock(), Mock()
        readonly.execute.side_effect = sqlite3.DatabaseError("test read-only probe failure")

        def connect(*args, **kwargs):
            if kwargs.get("uri"):
                return readonly
            readonly.close.assert_called_once_with()
            return fallback

        with TemporaryDirectory() as temporary, patch.object(module.sqlite3, "connect", side_effect=connect):
            self.assertIs(module._open_source_database(Path(temporary) / "source.sqlite3"), fallback)

    def test_a_snapshot_carries_rows_committed_after_the_database_file_starts(self) -> None:
        # The reason this module uses the backup API instead of copying the database
        # file: a row committed through the live connection must appear in the
        # snapshot, and the snapshot must be a self-contained single file.
        with TemporaryDirectory(prefix="companion-v2-backup-", ignore_cleanup_errors=True) as temporary:
            runtime = _open(temporary)
            try:
                with runtime.transaction() as connection:
                    connection.execute(
                        "INSERT INTO runtime_metadata(key, value) VALUES (?, ?)", ("probe", "committed"))
                backup = InstallationBackup(runtime, now=lambda: NOW)
                manifest = backup.create(note="commit probe").unwrap()
                snapshot = _payload(backup, manifest["snapshot_id"])
                self.assertTrue(snapshot.is_file())
                # The snapshot stands alone: no sidecar of its own to lose.
                self.assertFalse(Path(str(snapshot) + "-wal").exists())
                self.assertFalse(Path(str(snapshot) + "-shm").exists())
                restored = sqlite3.connect(str(snapshot))
                try:
                    row = restored.execute("SELECT value FROM runtime_metadata WHERE key = 'probe'").fetchone()
                finally:
                    restored.close()
                self.assertEqual(row[0], "committed")
                self.assertGreater(manifest["byte_size"], 0)
            finally:
                runtime.close()

    def test_snapshot_is_refused_when_it_belongs_to_another_installation(self) -> None:
        with TemporaryDirectory(prefix="companion-v2-backup-", ignore_cleanup_errors=True) as temporary:
            runtime = _open(temporary)
            try:
                manifest = InstallationBackup(runtime, now=lambda: NOW).create().unwrap()
                result = _verify_as(InstallationBackup(runtime, now=lambda: NOW),
                                    manifest["snapshot_id"], installation_id="installation-other")
                self.assertFalse(result.is_success)
                self.assertEqual(result.failure.code, "backup_snapshot_foreign")
            finally:
                runtime.close()

    def test_restore_is_staged_and_leaves_the_data_root_untouched_until_restart(self) -> None:
        with TemporaryDirectory(prefix="companion-v2-backup-", ignore_cleanup_errors=True) as temporary:
            runtime = _open(temporary)
            try:
                backup = InstallationBackup(runtime, now=lambda: NOW)
                manifest = backup.create(note="before change").unwrap()
                with runtime.transaction() as connection:
                    connection.execute(
                        "INSERT INTO runtime_metadata(key, value) VALUES (?, ?)", ("later", "written after snapshot"))
                preview = backup.preview_restore(snapshot_id=manifest["snapshot_id"])
                self.assertTrue(preview.is_success)
                self.assertEqual(preview.unwrap()["effect"], "requires_restart")
                staged = backup.restore(snapshot_id=manifest["snapshot_id"],
                                        confirmed_hash=manifest["content_hash"])
                self.assertTrue(staged.is_success)
                pending = backup.pending().unwrap()["pending"]
                self.assertEqual(pending["snapshot_id"], manifest["snapshot_id"])
                # The running store still sees the row written after the snapshot.
                row = runtime.read_one("SELECT value FROM runtime_metadata WHERE key = 'later'", ())
                self.assertEqual(row["value"], "written after snapshot")
                # A second restore cannot be queued behind the first.
                second = backup.restore(snapshot_id=manifest["snapshot_id"],
                                        confirmed_hash=manifest["content_hash"])
                self.assertEqual(second.failure.code, "backup_restore_pending")
            finally:
                runtime.close()

    def test_delete_only_confirmed_complete_snapshot_and_never_live_data(self) -> None:
        with TemporaryDirectory() as temporary:
            runtime = _open(temporary)
            try:
                backup = InstallationBackup(runtime, now=lambda: NOW)
                snapshot = backup.create(note="target").unwrap()
                other = backup.create(note="keep").unwrap()
                command = {"snapshot_id": snapshot["snapshot_id"], "confirmed_hash": snapshot["content_hash"]}
                self.assertEqual(backup.delete(**command).failure.code, "backup_delete_confirmation_required")
                self.assertEqual(backup.delete(snapshot_id="../outside", confirmed_hash="0" * 64,
                                               confirm=True).failure.code, "backup_snapshot_id_invalid")
                self.assertEqual(backup.delete(snapshot_id=command["snapshot_id"], confirmed_hash="0" * 64,
                                               confirm=True).failure.code, "backup_confirmation_mismatch")
                self.assertTrue(backup.restore(**command).is_success)
                self.assertEqual(backup.delete(**command, confirm=True).failure.code, "backup_restore_pending")
                self.assertTrue(backup.cancel_pending(confirm=True).is_success)
                self.assertTrue(backup.delete(**command, confirm=True).is_success)
                self.assertFalse(_payload(backup, snapshot["snapshot_id"]).exists())
                self.assertTrue(_payload(backup, other["snapshot_id"]).is_file())
                self.assertIsNotNone(runtime.read_one("SELECT value FROM runtime_metadata WHERE key='schema_version'", ()))
            finally:
                runtime.close()

    def test_confirmation_digest_must_match_the_snapshot_bytes(self) -> None:
        with TemporaryDirectory(prefix="companion-v2-backup-", ignore_cleanup_errors=True) as temporary:
            runtime = _open(temporary)
            try:
                backup = InstallationBackup(runtime, now=lambda: NOW)
                manifest = backup.create().unwrap()
                result = backup.restore(snapshot_id=manifest["snapshot_id"], confirmed_hash="0" * 64)
                self.assertEqual(result.failure.code, "backup_confirmation_mismatch")
                self.assertFalse(backup.pending_path.exists())
            finally:
                runtime.close()

    def test_apply_pending_swaps_the_database_only_with_no_live_connection(self) -> None:
        with TemporaryDirectory(prefix="companion-v2-backup-", ignore_cleanup_errors=True) as temporary:
            runtime = _open(temporary)
            manifest = None
            schema_version = None
            try:
                # A row that predates the snapshot, so the swap can be shown to keep it.
                with runtime.transaction() as connection:
                    connection.execute(
                        "INSERT INTO runtime_metadata(key, value) VALUES (?, ?)", ("probe", "committed"))
                backup = InstallationBackup(runtime, now=lambda: NOW)
                manifest = backup.create(note="restore target").unwrap()
                schema_version = manifest["schema_version"]
                # A row written after the snapshot, which the swap must roll back.
                with runtime.transaction() as connection:
                    connection.execute(
                        "INSERT INTO runtime_metadata(key, value) VALUES (?, ?)", ("discard", "me"))
                self.assertTrue(backup.restore(snapshot_id=manifest["snapshot_id"],
                                               confirmed_hash=manifest["content_hash"]).is_success)
            finally:
                # Close first: the whole point is that no connection exists during the swap.
                runtime.close()
            outcome = apply_pending(Path(temporary) / "data", installation_id=INSTALLATION_ID,
                                    schema_version=schema_version)
            self.assertTrue(outcome["applied"])
            reopened = _open(temporary)
            try:
                self.assertIsNone(reopened.read_one("SELECT value FROM runtime_metadata WHERE key = 'discard'", ()))
                probe = reopened.read_one("SELECT value FROM runtime_metadata WHERE key = 'probe'", ())
                self.assertEqual(probe["value"], "committed")
                self.assertFalse((Path(temporary) / "data" / PENDING_FILENAME).exists())
                self.assertTrue((Path(temporary) / "data" / "companion-v2.sqlite3.replaced").exists())
            finally:
                reopened.close()
            self.assertEqual(outcome["snapshot_id"], manifest["snapshot_id"])

    def test_restore_can_read_a_snapshot_from_the_configured_external_directory(self) -> None:
        with TemporaryDirectory(prefix="companion-v2-backup-external-", ignore_cleanup_errors=True) as temporary:
            runtime = _open(temporary)
            external = Path(temporary) / "other-disk" / "snapshots"
            try:
                with runtime.transaction() as connection:
                    connection.execute("INSERT INTO runtime_metadata(key, value) VALUES (?, ?)",
                                       ("probe", "before"))
                backup = InstallationBackup(runtime, now=lambda: NOW, directory=external)
                manifest = backup.create(note="外置磁盘快照").unwrap()
                with runtime.transaction() as connection:
                    connection.execute("UPDATE runtime_metadata SET value = ? WHERE key = ?",
                                       ("after", "probe"))
                self.assertTrue(backup.restore(snapshot_id=manifest["snapshot_id"],
                                               confirmed_hash=manifest["content_hash"]).is_success)
                schema_version = manifest["schema_version"]
            finally:
                runtime.close()

            outcome = apply_pending(Path(temporary) / "data", installation_id=INSTALLATION_ID,
                                    schema_version=schema_version, backup_directory=external)
            self.assertTrue(outcome["applied"])
            reopened = _open(temporary)
            try:
                self.assertEqual(reopened.read_one("SELECT value FROM runtime_metadata WHERE key = 'probe'", ())[
                    "value"], "before")
            finally:
                reopened.close()

    def test_apply_pending_reports_absence_instead_of_raising(self) -> None:
        with TemporaryDirectory(prefix="companion-v2-backup-", ignore_cleanup_errors=True) as temporary:
            runtime = _open(temporary)
            try:
                outcome = apply_pending(runtime.data_root, installation_id=INSTALLATION_ID,
                                        schema_version="companion-v2.storage.v40")
                self.assertEqual(outcome, {"applied": False, "reason": "no_pending_restore"})
            finally:
                runtime.close()

    def test_a_malformed_staged_request_does_not_break_start_up(self) -> None:
        # Start-up calls apply_pending before opening the store, so a corrupt request
        # has to come back as a document rather than as an exception.
        with TemporaryDirectory(prefix="companion-v2-backup-", ignore_cleanup_errors=True) as temporary:
            runtime = _open(temporary)
            try:
                backup = InstallationBackup(runtime, now=lambda: NOW)
                backup.pending_path.write_text("{not json", encoding="utf-8")
                outcome = apply_pending(runtime.data_root, installation_id=INSTALLATION_ID,
                                        schema_version="companion-v2.storage.v40")
                self.assertFalse(outcome["applied"])
                self.assertEqual(outcome["reason"], "backup_pending_unreadable")
                self.assertTrue(runtime.data_root.joinpath("companion-v2.sqlite3").is_file())
            finally:
                runtime.close()

    def test_current_schema_version_reads_the_file_without_opening_it_for_writing(self) -> None:
        with TemporaryDirectory(prefix="companion-v2-backup-", ignore_cleanup_errors=True) as temporary:
            runtime = _open(temporary)
            stored = None
            try:
                root = runtime.data_root
                stored = runtime.read_one(
                    "SELECT value FROM runtime_metadata WHERE key = 'schema_version'", ())["value"]
                version = current_schema_version(root)
                self.assertEqual(version, stored)
                self.assertIsNone(current_schema_version(Path(temporary) / "absent"))
            finally:
                runtime.close()
            # Probe a database file copied away from any runtime. The property that
            # matters is that verification never rewrites the candidate it inspects:
            # a staged snapshot must still match its confirmed digest afterwards.
            isolated = Path(temporary) / "isolated"
            isolated.mkdir()
            candidate = isolated / "companion-v2.sqlite3"
            shutil.copy2(Path(temporary) / "data" / "companion-v2.sqlite3", candidate)
            before = hashlib.sha256(candidate.read_bytes()).hexdigest()
            self.assertEqual(current_schema_version(isolated), stored)
            # SQLite may create empty WAL/shm sidecars in a writable directory; that is
            # normal. What must not happen is a modified database file.
            self.assertEqual(hashlib.sha256(candidate.read_bytes()).hexdigest(), before)

    def test_listing_skips_a_snapshot_whose_bundle_was_pruned(self) -> None:
        with TemporaryDirectory(prefix="companion-v2-backup-", ignore_cleanup_errors=True) as temporary:
            runtime = _open(temporary)
            try:
                backup = InstallationBackup(runtime, now=lambda: NOW)
                manifest = backup.create().unwrap()
                self.assertEqual(len(backup.listing().unwrap()["snapshots"]), 1)
                # A bundle without its main database is an interrupted snapshot.
                _payload(backup, manifest["snapshot_id"]).unlink()
                self.assertEqual(list(backup.listing().unwrap()["snapshots"]), [])
            finally:
                runtime.close()

    def test_a_tampered_snapshot_fails_inspection(self) -> None:
        with TemporaryDirectory(prefix="companion-v2-backup-", ignore_cleanup_errors=True) as temporary:
            runtime = _open(temporary)
            try:
                backup = InstallationBackup(runtime, now=lambda: NOW)
                manifest = backup.create().unwrap()
                snapshot = _payload(backup, manifest["snapshot_id"])
                # Corrupt the SQLite header. Flipping an interior page would still be
                # readable and quick_check might not notice; the header is definitive.
                with snapshot.open("r+b") as handle:
                    handle.seek(0)
                    handle.write(b"NOT-A-DATABASE!!" + b"\x00" * 48)
                # The digest check fires first, which is the intended ordering: a
                # snapshot is never opened for inspection before it is authenticated.
                result = backup.inspect(snapshot_id=manifest["snapshot_id"])
                self.assertFalse(result.is_success)
                self.assertIn(result.failure.code,
                              ("backup_snapshot_changed", "backup_snapshot_corrupt", "backup_snapshot_unreadable"))
            finally:
                runtime.close()

    def test_manifest_is_plain_json_the_dashboard_can_render(self) -> None:
        with TemporaryDirectory(prefix="companion-v2-backup-", ignore_cleanup_errors=True) as temporary:
            runtime = _open(temporary)
            try:
                backup = InstallationBackup(runtime, now=lambda: NOW)
                manifest = backup.create(note="每周备份").unwrap()
                sidecar = backup.directory / f"companion-v2-{manifest['snapshot_id']}.json"
                document = json.loads(sidecar.read_text(encoding="utf-8"))
                self.assertEqual(document["format"], BACKUP_FORMAT)
                self.assertEqual(document["note"], "每周备份")
                self.assertEqual(document["installation_id"], INSTALLATION_ID)
                for field in ("snapshot_id", "created_at", "schema_version", "byte_size", "content_hash"):
                    self.assertIn(field, document)
                # The whole installation is described, not only the main database.
                paths = {item["path"] for item in document["artifacts"]}
                self.assertIn("companion-v2.sqlite3", paths)
                self.assertEqual(document["artifact_count"], len(document["artifacts"]))
                self.assertEqual(document["byte_size"], sum(item["bytes"] for item in document["artifacts"]))
            finally:
                runtime.close()


def _verify_as(backup: InstallationBackup, snapshot_id: str, *, installation_id: str):
    """Verify a snapshot against a different claimed installation identity."""
    document = module._read_snapshot_manifest(backup.directory, snapshot_id)
    try:
        module._verify_bundle(document, directory=backup.directory, installation_id=installation_id,
                              schema_version=backup._schema_version())
    except module.BackupError as error:
        return Outcome.failed(error.code, str(error))
    return Outcome.success({})


if __name__ == "__main__":
    unittest.main()
