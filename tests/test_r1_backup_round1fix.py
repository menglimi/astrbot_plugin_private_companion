"""Round-1 adversarial review, backup/restore: the fixes required before merge.

Every test in this file fails on the tree that the review rejected, for the reason
named in its docstring, and passes on the fixed tree:

* P1-1  a snapshot used to hold ``StorageRuntime``'s lock while it copied every
        file, and the dashboard route ran the whole thing on the AstrBot event loop;
* P1-2  a 7.0.7 ``installation-backup.v1`` snapshot was listable but unrestorable;
* P2-1  a legacy or unreadable ``restore-pending.json`` blocked every later
        restore with no way out;
* P2-2  a restore deleted the current credential files and rewrote configuration
        with the redacted copy, silently forgetting API keys;
* P2-3  ``listing`` shipped every artifact of every snapshot and statted them all;
* P2-4  one incomplete bundle made a backup-directory move fail forever;
* P2-6  a retried finalize deleted ``companion-v2.sqlite3.replaced``;
* P2-7  v1 snapshot pairs were not carried along by a backup-directory move;
* P3    stale ``.bundle.old-*`` scratch, ``cleanup_all_backups``' data root.

Everything here is synthetic: temporary directories, a fresh SQLite data root per
test, no AstrBot process, no production data and no network.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from companion_v2.storage import backup as backup_module
from companion_v2.storage import management as management_module
from companion_v2.storage.backup import (
    BACKUP_FORMAT, JOURNAL_FORMAT, LEGACY_BACKUP_FORMAT, MAIN_DATABASE, PENDING_FILENAME,
    RESTORE_FORMAT, InstallationBackup, apply_pending,
)
from companion_v2.storage.management import (
    cleanup_all_backups, cleanup_backups, cleanup_plan, copy_backup_snapshots,
)
from companion_v2.storage.runtime import StorageRuntime


NOW = datetime(2026, 10, 27, 6, 0, tzinfo=timezone.utc)
INSTALLATION = "r1-round1fix-installation"
AUX_DATABASE = "semantic-catalog.sqlite3"
CONFIG = "extensions/image/config.json"
CREDENTIAL = "extensions/game/trusted_browsers.json"
BINARY = "content_v2/blob.bin"
SLOW_BINARY = "content_v2/slow-asset.bin"
CURRENT_KEY = "CURRENT-ONLY-KEY"
SNAPSHOT_SECRET = "SNAPSHOT-SECRET-DO-NOT-PACK"


def _put(root: Path, relative: str, body: bytes) -> None:
    path = root.joinpath(*relative.split("/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)


def _make_db(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE marker(value TEXT NOT NULL)")
        connection.execute("INSERT INTO marker VALUES (?)", (value,))
        connection.commit()
    finally:
        connection.close()


def _db_value(path: Path) -> str:
    connection = sqlite3.connect(path)
    try:
        return connection.execute("SELECT value FROM marker").fetchone()[0]
    finally:
        connection.close()


class Installation:
    """One synthetic V2 installation this object owns and cleans up."""

    def __init__(self, *, external: bool = False) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="r1-round1fix-"))
        self.root = self.base / "data"
        self.external = (self.base / "external" / "snapshots") if external else None
        self.runtime = StorageRuntime.open(data_root=self.root, installation_id=INSTALLATION)
        self._closed = False
        try:
            with self.runtime.transaction() as connection:
                connection.execute(
                    "INSERT OR REPLACE INTO runtime_metadata(key, value) VALUES('probe', 'before')")
            _make_db(self.path(AUX_DATABASE), "before")
            _put(self.root, BINARY, b"before-binary")
            _put(self.root, SLOW_BINARY, b"slow-asset" * 256)
            _put(self.root, CONFIG, json.dumps({
                "enabled": True, "synthetic_marker": "before",
                "api_key": SNAPSHOT_SECRET,
                "nested": {"pairing_token": "SNAPSHOT-TOKEN-DO-NOT-PACK"},
            }).encode())
            _put(self.root, CREDENTIAL, b"before-pairing")
        except BaseException:
            self.close()
            raise

    # -- paths ------------------------------------------------------------
    def path(self, relative: str) -> Path:
        return self.root.joinpath(*relative.split("/"))

    def backup(self, *, now: datetime = NOW, directory: Path | None = None) -> InstallationBackup:
        return InstallationBackup(self.runtime, now=lambda: now, directory=directory or self.external,
                                  retention_count=20, retention_days=3650)

    def bundle(self, snapshot_id: str) -> Path:
        return self.backup().directory / f"companion-v2-{snapshot_id}.bundle"

    def payload(self, snapshot_id: str, relative: str) -> Path:
        return self.bundle(snapshot_id) / "payload" / Path(*relative.split("/"))

    def sidecar(self, snapshot_id: str) -> Path:
        return self.backup().directory / f"companion-v2-{snapshot_id}.json"

    # -- content ----------------------------------------------------------
    def create(self, *, now: datetime = NOW, note: str = "round1fix") -> dict:
        return self.backup(now=now).create(note=note).unwrap()

    def mutate(self) -> None:
        with self.runtime.transaction() as connection:
            connection.execute("UPDATE runtime_metadata SET value = 'after' WHERE key = 'probe'")
        connection = sqlite3.connect(self.path(AUX_DATABASE))
        try:
            connection.execute("UPDATE marker SET value = 'after'")
            connection.commit()
        finally:
            connection.close()
        _put(self.root, BINARY, b"after-binary")
        document = json.loads(self.path(CONFIG).read_text())
        document["synthetic_marker"] = "after"
        _put(self.root, CONFIG, json.dumps(document).encode())

    def rotate_credentials(self) -> None:
        document = json.loads(self.path(CONFIG).read_text())
        document["api_key"] = CURRENT_KEY
        document["nested"]["pairing_token"] = "CURRENT-ONLY-TOKEN"
        _put(self.root, CONFIG, json.dumps(document).encode())
        _put(self.root, CREDENTIAL, b"current-pairing")

    def stage(self, manifest: dict, *, confirmed_scope: str = "") -> None:
        self.mutate()
        self.rotate_credentials()
        self.backup().restore(snapshot_id=manifest["snapshot_id"],
                              confirmed_hash=manifest["content_hash"],
                              confirmed_scope=confirmed_scope).unwrap()

    def legacy_pair(self, *, identifier: str = "a" * 32) -> dict:
        """A 7.0.7 snapshot: one main database file plus its sidecar."""
        current = self.create(note="source for a 7.0.7 snapshot")
        directory = self.backup().directory
        database = directory / f"companion-v2-{identifier}.sqlite3"
        shutil.copy2(self.payload(current["snapshot_id"], MAIN_DATABASE), database)
        document = {
            "format": LEGACY_BACKUP_FORMAT, "snapshot_id": identifier,
            "created_at": (NOW - timedelta(days=1)).isoformat(),
            "installation_id": INSTALLATION, "schema_version": current["schema_version"],
            "byte_size": database.stat().st_size,
            "content_hash": hashlib.sha256(database.read_bytes()).hexdigest(),
            "note": "7.0.7 single-database snapshot",
        }
        (directory / f"companion-v2-{identifier}.json").write_text(json.dumps(document))
        return document

    # -- lifecycle --------------------------------------------------------
    def close_runtime(self) -> None:
        try:
            self.runtime.close()
        except Exception:  # pragma: no cover - close is best effort
            pass

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.close_runtime()
        shutil.rmtree(self.base, ignore_errors=True)

    # -- assertions -------------------------------------------------------
    def probe(self) -> str:
        connection = sqlite3.connect(self.path(MAIN_DATABASE))
        try:
            return connection.execute(
                "SELECT value FROM runtime_metadata WHERE key = 'probe'").fetchone()[0]
        finally:
            connection.close()


def _installations(test: unittest.TestCase):
    def make(*, external: bool = False) -> Installation:
        installation = Installation(external=external)
        test.addCleanup(installation.close)
        return installation
    return make


# ---------------------------------------------------------------------------
# P1-1: the runtime lock and the event loop
# ---------------------------------------------------------------------------


class RuntimeLockWindowTests(unittest.TestCase):
    def test_copying_payload_files_does_not_hold_the_runtime_lock(self) -> None:
        """A snapshot used to keep ``read_snapshot()`` open for every artifact.

        The lock is a process-wide ``RLock`` owned by the one live connection, so
        copying a 1 KB image blocked every reader for as long as the copy ran: the
        review measured 5.02 s for a single file. Only the main database needs the
        lock; a payload file is not protected by it in any way.
        """
        installation = _installations(self)(external=True)
        installation.mutate()
        expected_probe = installation.probe()
        in_copy, release = threading.Event(), threading.Event()
        original = backup_module._copy_verified

        def slow_copy(source, destination, **kwargs):
            if Path(source).name == "slow-asset.bin":
                in_copy.set()
                release.wait(20)
            return original(source, destination, **kwargs)

        result: dict = {}

        def run() -> None:
            result["outcome"] = installation.backup().create(note="lock window")

        with patch.object(backup_module, "_copy_verified", new=slow_copy):
            worker = threading.Thread(target=run, daemon=True)
            worker.start()
            try:
                self.assertTrue(in_copy.wait(20), "the payload copy never started")
                started = time.monotonic()
                row = installation.runtime.read_one("SELECT 1 AS ok", ())
                read_seconds = time.monotonic() - started
                started = time.monotonic()
                with installation.runtime.read_snapshot() as connection:
                    connection.execute("SELECT COUNT(*) FROM runtime_metadata").fetchone()
                snapshot_seconds = time.monotonic() - started
                started = time.monotonic()
                with installation.runtime.transaction() as connection:
                    connection.execute(
                        "INSERT OR REPLACE INTO runtime_metadata(key, value) VALUES('lock-probe', 'x')")
                write_seconds = time.monotonic() - started
            finally:
                release.set()
                worker.join(120)
        self.assertEqual(row["ok"], 1)
        for label, seconds in (("read_one", read_seconds), ("read_snapshot", snapshot_seconds),
                               ("transaction", write_seconds)):
            self.assertLess(seconds, 2.0,
                            f"{label} waited {seconds:.2f}s for a payload copy to finish")
        outcome = result.get("outcome")
        self.assertIsNotNone(outcome)
        self.assertTrue(outcome.is_success, outcome.failure)
        manifest = outcome.unwrap()
        # The snapshot is still a consistent, fully verified bundle.
        self.assertTrue(installation.backup().inspect(snapshot_id=manifest["snapshot_id"]).is_success)
        snapshot = installation.payload(manifest["snapshot_id"], MAIN_DATABASE)
        connection = sqlite3.connect(snapshot)
        try:
            self.assertEqual(connection.execute(
                "SELECT value FROM runtime_metadata WHERE key = 'probe'").fetchone()[0], expected_probe)
        finally:
            connection.close()
        self.assertEqual(installation.payload(manifest["snapshot_id"], SLOW_BINARY).read_bytes(),
                         b"slow-asset" * 256)


# ---------------------------------------------------------------------------
# The dashboard route must not run the engine on the event loop
# ---------------------------------------------------------------------------


def _astrbot_stubs():
    """Minimal synthetic seams so ``backup_api`` can be imported without the SDK.

    ``astrbot_host/backup_api.py`` imports ``astrbot.api.web.request`` and resolves
    ``outcome_response`` from ``astrbot_host.web_api`` at call time. Replacing those
    two with small fakes is the whole host surface this route needs, and it keeps
    the test offline and free of the production SDK.
    """
    request = types.SimpleNamespace(username="dashboard-admin", query={}, path="/api/plugin/page")
    astrbot = types.ModuleType("astrbot")
    astrbot_api = types.ModuleType("astrbot.api")
    astrbot_web = types.ModuleType("astrbot.api.web")
    astrbot_web.request = request
    astrbot.api = astrbot_api
    astrbot_api.web = astrbot_web
    host_web_api = types.ModuleType("astrbot_host.web_api")

    def outcome_response(result, *, message=""):
        if result.failure is not None:
            return {"status": "error", "message": result.failure.code,
                    "reason": result.failure.reason, "data": {"code": result.failure.code}}
        return {"status": "ok", "message": message, "data": result.unwrap()}

    host_web_api.outcome_response = outcome_response
    return request, {"astrbot": astrbot, "astrbot.api": astrbot_api,
                     "astrbot.api.web": astrbot_web, "astrbot_host.web_api": host_web_api}


class DashboardRouteTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.request, stubs = _astrbot_stubs()
        cls.enterClassContext(patch.dict(sys.modules, stubs))
        import astrbot_host.backup_api as backup_api
        cls.enterClassContext(patch.object(backup_api, "request", cls.request))
        cls.backup_api = backup_api

    def controller(self, runtime):
        return types.SimpleNamespace(
            _control_lock=asyncio.Lock(), _terminated=False, session=object(),
            last_session=object(), runtime=runtime, config={"storage": {}},
            now=lambda: NOW, settings=None)

    async def test_a_backup_does_not_block_the_event_loop(self) -> None:
        """``_run`` was ``async`` but called the synchronous engine directly.

        A whole-installation snapshot copies gigabytes; running it on the loop stops
        every other AstrBot task (messages, web requests, timers) for the duration.
        The route has to hand the synchronous call to a worker thread.
        """
        from companion_v2.execution.models import Outcome

        installation = _installations(self)(external=True)
        self.request.body = self._body_for({"note": "event loop probe"})
        api = self.backup_api.BackupWebAPI(self.controller(installation.runtime))

        ticks = 0

        async def ticker() -> None:
            nonlocal ticks
            while True:
                started = time.monotonic()
                await asyncio.sleep(0.02)
                if time.monotonic() - started > 0.25:
                    raise AssertionError("the event loop was blocked for more than 250 ms")
                ticks += 1

        original = backup_module._copy_verified

        def slow_copy(source, destination, **kwargs):
            if Path(source).name == "slow-asset.bin":
                time.sleep(0.6)
            return original(source, destination, **kwargs)

        task = asyncio.create_task(ticker())
        try:
            with patch.object(backup_module, "_copy_verified", new=slow_copy):
                response = await api.create()
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self.assertEqual(response["status"], "ok", response)
        self.assertGreater(ticks, 8, f"the event loop only ran {ticks} times during a 0.6s backup")
        from astrbot_host.backup_api import backup_operation_lock
        self.assertFalse(backup_operation_lock(api.controller).locked())

    async def test_delete_route_requires_confirmation_and_dashboard_identity(self) -> None:
        installation = _installations(self)(external=True)
        installation.runtime.backup_directory = installation.external
        document = installation.create()
        snapshot_id, digest = document["snapshot_id"], document["content_hash"]
        api = self.backup_api.BackupWebAPI(self.controller(installation.runtime))
        self.assertIn(self.backup_api.PREFIX + "/delete", [path for path, *_ in api.routes()])
        self.request.body = self._body_for({"snapshot_id": snapshot_id, "confirmed_hash": digest})
        self.assertEqual((await api.delete())["message"], "backup_request_invalid")
        self.request.body = self._body_for({"snapshot_id": snapshot_id, "confirmed_hash": digest, "confirm": True})
        with patch.object(self.request, "username", ""):
            self.assertEqual((await api.delete())["message"], "dashboard_identity_required")
        response = await api.delete()
        self.assertEqual(response["status"], "ok", response)
        self.assertEqual((await api.delete())["message"], "backup_snapshot_missing")

    async def test_a_second_operation_waits_for_the_first(self) -> None:
        """Two dashboard backups must not interleave: the write path shares the store."""
        installation = _installations(self)(external=True)
        self.request.body = self._body_for({"note": "serialized"})
        api = self.backup_api.BackupWebAPI(self.controller(installation.runtime))
        order: list[str] = []
        original = InstallationBackup.create

        def recorded(self, *, note=""):
            order.append("start")
            try:
                return original(self, note=note)
            finally:
                order.append("end")

        with patch.object(InstallationBackup, "create", new=recorded):
            first, second = await asyncio.gather(api.create(), api.create())
        self.assertEqual(first["status"], "ok", first)
        self.assertEqual(second["status"], "ok", second)
        self.assertEqual(order, ["start", "end", "start", "end"])

    async def test_abandoning_a_staged_request_is_an_explicit_route(self) -> None:
        """P2-1: a stuck request needed a way out that is not "delete a file by hand"."""
        installation = _installations(self)(external=True)
        manifest = installation.create()
        (installation.root / PENDING_FILENAME).write_text(
            json.dumps({"format": LEGACY_BACKUP_FORMAT, "snapshot_id": "b" * 32,
                        "content_hash": "0" * 64, "requested_at": NOW.isoformat()}))
        api = self.backup_api.BackupWebAPI(self.controller(installation.runtime))
        self.request.body = self._body_for({})
        refused = await api.cancel()
        self.assertEqual(refused["status"], "error", refused)
        self.assertEqual(refused["data"]["code"], "backup_request_invalid")
        self.assertTrue((installation.root / PENDING_FILENAME).is_file())
        self.request.body = self._body_for({"confirm": True})
        cancelled = await api.cancel()
        self.assertEqual(cancelled["status"], "ok", cancelled)
        self.assertTrue(cancelled["data"]["cancelled"])
        self.assertFalse((installation.root / PENDING_FILENAME).exists())
        # The snapshot the request pointed at is untouched, and the queue is free.
        self.assertTrue(installation.bundle(manifest["snapshot_id"]).is_dir())
        staged = installation.backup().restore(snapshot_id=manifest["snapshot_id"],
                                               confirmed_hash=manifest["content_hash"])
        self.assertTrue(staged.is_success, staged.failure)

    @staticmethod
    def _body_for(document: dict):
        async def body() -> bytes:
            return json.dumps(document).encode()
        return body


# ---------------------------------------------------------------------------
# P1-2: a 7.0.7 snapshot is restorable again, as a main-database-only restore
# ---------------------------------------------------------------------------


class LegacySnapshotTests(unittest.TestCase):
    def test_manual_delete_handles_legacy_pairs_but_refuses_foreign_and_incomplete_pairs(self) -> None:
        installation = _installations(self)(external=True)
        backup = installation.backup()
        document = installation.legacy_pair()
        snapshot_id = document["snapshot_id"]
        sidecar = backup.directory / f"companion-v2-{snapshot_id}.json"
        database = backup.directory / f"companion-v2-{snapshot_id}.sqlite3"
        command = {"snapshot_id": snapshot_id, "confirmed_hash": document["content_hash"], "confirm": True}
        sidecar.write_text(json.dumps({**document, "installation_id": "foreign"}), encoding="utf-8")
        self.assertEqual(backup.delete(**command).failure.code, "backup_snapshot_missing")
        self.assertTrue(database.is_file())
        sidecar.write_text(json.dumps({**document, "byte_size": 1}), encoding="utf-8")
        self.assertEqual(backup.delete(**command).failure.code, "backup_delete_incomplete")
        self.assertTrue(database.is_file())
        sidecar.write_text(json.dumps(document), encoding="utf-8")
        self.assertTrue(backup.delete(**command).is_success)
        self.assertFalse(sidecar.exists())
        self.assertFalse(database.exists())

    def test_a_v1_snapshot_restores_the_main_database_and_says_what_it_skips(self) -> None:
        installation = _installations(self)(external=True)
        legacy = installation.legacy_pair()
        installation.mutate()
        installation.rotate_credentials()
        backup = installation.backup()

        listed = {item["snapshot_id"]: item for item in backup.listing().unwrap()["snapshots"]}
        self.assertIn(legacy["snapshot_id"], listed)
        self.assertTrue(listed[legacy["snapshot_id"]]["restorable"])
        self.assertEqual(listed[legacy["snapshot_id"]]["restore_scope"], "main_database_only")

        preview = backup.preview_restore(snapshot_id=legacy["snapshot_id"]).unwrap()
        self.assertEqual(preview["restore_scope"], "main_database_only")
        self.assertIn("legacy_main_database_only", preview["warnings"])
        self.assertIn("mixed_generation_risk", preview["warnings"])
        self.assertIn(AUX_DATABASE, preview["preserves"])
        self.assertIn("content_v2/", preview["preserves"])

        # Writing the pending request requires the scope to be acknowledged: a plain
        # "restore" must never silently do a partial restore.
        refused = backup.restore(snapshot_id=legacy["snapshot_id"],
                                 confirmed_hash=legacy["content_hash"])
        self.assertFalse(refused.is_success)
        self.assertEqual(refused.failure.code, "backup_legacy_confirmation_required")
        self.assertFalse((installation.root / PENDING_FILENAME).exists())

        staged = backup.restore(snapshot_id=legacy["snapshot_id"],
                                confirmed_hash=legacy["content_hash"],
                                confirmed_scope="main_database_only")
        self.assertTrue(staged.is_success, staged.failure)
        self.assertEqual(staged.unwrap()["restore_scope"], "main_database_only")
        pending = json.loads((installation.root / PENDING_FILENAME).read_text())
        self.assertEqual(pending["restore_scope"], "main_database_only")
        self.assertTrue(pending["safety_snapshot_id"])

        installation.close_runtime()
        applied = apply_pending(installation.root, installation_id=INSTALLATION,
                                schema_version=legacy["schema_version"],
                                backup_directory=installation.external)
        self.assertTrue(applied["applied"], applied)
        self.assertEqual(applied["restore_scope"], "main_database_only")
        # The main database is the snapshot's generation…
        self.assertEqual(installation.probe(), "before")
        # …and the side stores are not rolled back: the operator was told exactly this.
        self.assertEqual(_db_value(installation.path(AUX_DATABASE)), "after")
        self.assertEqual(installation.path(BINARY).read_bytes(), b"after-binary")
        self.assertTrue(installation.path(CREDENTIAL).is_file())
        self.assertTrue((installation.root / "companion-v2.sqlite3.replaced").is_file())
        # The 7.0.7 snapshot itself is never consumed by the restore.
        self.assertTrue((installation.external / f"companion-v2-{legacy['snapshot_id']}.sqlite3").is_file())

    def test_v1_pairs_take_part_in_a_backup_directory_move(self) -> None:
        """P2-7: relocation only enumerated the v2 format, so v1 pairs stayed behind."""
        from astrbot_host.storage_location import (
            _backup_installations, finish_backup_relocation, relocate_backup_storage,
        )

        installation = _installations(self)()
        installation.legacy_pair()
        plugin_root = installation.base / "plugin-data" / "astrbot_plugin_private_companion"
        source = plugin_root / "profiles" / "main" / "runtime" / "backups"
        source.mkdir(parents=True)
        for suffix in (".json", ".sqlite3"):
            shutil.copy2(installation.backup().directory / f"companion-v2-{'a' * 32}{suffix}",
                         source / f"companion-v2-{'a' * 32}{suffix}")
        self.assertEqual(_backup_installations(source), (INSTALLATION,))

        external = installation.base / "moved-snapshots"
        report: dict = {}
        transfers = relocate_backup_storage(plugin_root, "", str(external), report=report)
        self.assertEqual(len(transfers), 1)
        installation_key = hashlib.sha256(INSTALLATION.encode()).hexdigest()
        destination = external / "installations" / installation_key / "backups"
        database = destination / f"companion-v2-{'a' * 32}.sqlite3"
        self.assertTrue(database.is_file())
        self.assertTrue((destination / f"companion-v2-{'a' * 32}.json").is_file())
        self.assertEqual(finish_backup_relocation(transfers), 1)
        self.assertFalse((source / f"companion-v2-{'a' * 32}.sqlite3").exists())
        self.assertFalse(report.get("skipped"))

    def test_retention_keeps_the_staged_v1_snapshot_while_it_is_needed(self) -> None:
        installation = _installations(self)(external=True)
        legacy = installation.legacy_pair()
        installation.backup().restore(snapshot_id=legacy["snapshot_id"],
                                      confirmed_hash=legacy["content_hash"],
                                      confirmed_scope="main_database_only").unwrap()
        cleanup = cleanup_backups(installation.root, retention_count=1, retention_days=1,
                                  now=NOW + timedelta(days=365), backup_directory=installation.external,
                                  installation_id=INSTALLATION)
        self.assertNotIn(legacy["snapshot_id"], cleanup["snapshot_ids"], cleanup)
        self.assertTrue((installation.external / f"companion-v2-{legacy['snapshot_id']}.sqlite3").is_file())
        self.assertTrue((installation.external / f"companion-v2-{legacy['snapshot_id']}.json").is_file())
        # v1 material is visible in the plan, and it is never reported as incomplete.
        plan = cleanup_plan(installation.root, retention_count=1, retention_days=1,
                            now=NOW, backup_directory=installation.external,
                            installation_id=INSTALLATION)
        self.assertEqual(plan["incomplete_ids"], [])


# ---------------------------------------------------------------------------
# P2-1: pending requests can be abandoned, explicitly and safely
# ---------------------------------------------------------------------------


class PendingRequestTests(unittest.TestCase):
    def test_an_unreadable_pending_request_is_reported_and_cancellable(self) -> None:
        installation = _installations(self)()
        pending = installation.root / PENDING_FILENAME
        pending.write_text("{not json")
        state = installation.backup().pending().unwrap()
        self.assertIsNone(state["pending"])
        self.assertTrue(state["cancellable"])
        self.assertEqual(state["problem"]["code"], "backup_pending_unreadable")
        cancelled = installation.backup().cancel_pending(confirm=True).unwrap()
        self.assertTrue(cancelled["cancelled"])
        self.assertEqual(cancelled["abandoned"]["code"], "backup_pending_unreadable")
        self.assertFalse(pending.exists())
        # The installation is untouched and a new restore can be staged.
        self.assertEqual(installation.probe(), "before")
        manifest = installation.create()
        staged = installation.backup().restore(snapshot_id=manifest["snapshot_id"],
                                               confirmed_hash=manifest["content_hash"])
        self.assertTrue(staged.is_success, staged.failure)

    def test_a_legacy_pending_request_is_legible_and_cancellable(self) -> None:
        installation = _installations(self)()
        manifest = installation.create()
        pending = installation.root / PENDING_FILENAME
        pending.write_text(json.dumps({
            "format": LEGACY_BACKUP_FORMAT, "snapshot_id": "c" * 32,
            "content_hash": "0" * 64, "requested_at": NOW.isoformat(),
            "safety_snapshot_id": manifest["snapshot_id"],
        }))
        state = installation.backup().pending().unwrap()
        self.assertEqual(state["problem"]["code"], "backup_pending_legacy")
        self.assertFalse(state["restorable"])
        self.assertTrue(state["cancellable"])
        # A staged request this version cannot apply blocks new restores: say so.
        blocked = installation.backup().restore(snapshot_id=manifest["snapshot_id"],
                                                confirmed_hash=manifest["content_hash"])
        self.assertFalse(blocked.is_success)
        self.assertEqual(blocked.failure.code, "backup_restore_pending")
        self.assertIn("cancel", blocked.failure.reason)
        cancelled = installation.backup().cancel_pending(confirm=True).unwrap()
        self.assertTrue(cancelled["cancelled"])
        self.assertIn("backup_pending_legacy", json.dumps(cancelled["abandoned"]))
        self.assertTrue(installation.bundle(manifest["snapshot_id"]).is_dir())

    def test_cancelling_requires_confirmation_and_takes_a_safety_copy(self) -> None:
        installation = _installations(self)()
        manifest = installation.create()
        (installation.root / PENDING_FILENAME).write_text(json.dumps({
            "format": RESTORE_FORMAT, "snapshot_id": manifest["snapshot_id"],
            "content_hash": manifest["content_hash"], "requested_at": NOW.isoformat(),
        }))
        refused = installation.backup().cancel_pending()
        self.assertFalse(refused.is_success)
        self.assertEqual(refused.failure.code, "backup_cancel_confirmation_required")
        self.assertTrue((installation.root / PENDING_FILENAME).is_file())
        cancelled = installation.backup().cancel_pending(confirm=True).unwrap()
        self.assertTrue(cancelled["cancelled"])
        safety_id = cancelled["safety_snapshot_id"]
        self.assertTrue(safety_id)
        self.assertTrue(installation.bundle(safety_id).is_dir())
        self.assertIsNone(cancelled["safety_failure"])
        self.assertTrue(installation.backup().inspect(snapshot_id=safety_id).is_success)

    def test_cancelling_refuses_while_a_restore_journal_needs_recovery(self) -> None:
        """A prepared journal is the only copy of a half-swapped installation.

        Abandoning the request must not throw it away: the restart has to roll it
        back first, so the cancel is refused with an actionable reason.
        """
        installation = _installations(self)()
        manifest = installation.create()
        (installation.root / PENDING_FILENAME).write_text(json.dumps({
            "format": RESTORE_FORMAT, "snapshot_id": manifest["snapshot_id"],
            "content_hash": manifest["content_hash"], "requested_at": NOW.isoformat(),
        }))
        journal_root = installation.root / ".restore-abcdef12-99999999"
        (journal_root / "rollback").mkdir(parents=True)
        (journal_root / "rollback" / MAIN_DATABASE).write_bytes(b"original-bytes")
        (journal_root / "journal.json").write_text(json.dumps({
            "format": JOURNAL_FORMAT, "phase": "prepared",
            "snapshot_id": manifest["snapshot_id"], "content_hash": manifest["content_hash"],
            "artifacts": [{"path": MAIN_DATABASE, "kind": "sqlite"}],
            "roots": [MAIN_DATABASE],
        }))
        result = installation.backup().cancel_pending(confirm=True)
        self.assertFalse(result.is_success)
        self.assertEqual(result.failure.code, "backup_restore_journal_pending")
        self.assertTrue((installation.root / PENDING_FILENAME).is_file())
        self.assertTrue((journal_root / "rollback" / MAIN_DATABASE).is_file())


# ---------------------------------------------------------------------------
# P2-6: a retried finalize must not delete the previous generation
# ---------------------------------------------------------------------------


class FinalizeRetryTests(unittest.TestCase):
    def test_a_retried_finalize_keeps_the_previous_generation_copy(self) -> None:
        installation = _installations(self)(external=True)
        manifest = installation.create()
        installation.stage(manifest)
        installation.close_runtime()
        live_before = installation.path(MAIN_DATABASE).read_bytes()
        pending = installation.root / PENDING_FILENAME
        original_unlink = Path.unlink
        fired: list[bool] = []

        def fail_pending_unlink(path, *args, **kwargs):
            if path == pending and not fired:
                fired.append(True)
                raise OSError(13, "synthetic pending finalization failure")
            return original_unlink(path, *args, **kwargs)

        kwargs = dict(installation_id=INSTALLATION, schema_version=manifest["schema_version"],
                      backup_directory=installation.external)
        with patch.object(Path, "unlink", new=fail_pending_unlink):
            first = apply_pending(installation.root, **kwargs)
        self.assertTrue(first["applied"], first)
        self.assertTrue(first.get("pending_finalization_required"), first)
        replaced = installation.root / "companion-v2.sqlite3.replaced"
        self.assertTrue(replaced.is_file())
        self.assertEqual(replaced.read_bytes(), live_before)

        second = apply_pending(installation.root, **kwargs)
        self.assertTrue(second["applied"], second)
        self.assertFalse(pending.exists())
        self.assertTrue(replaced.is_file(), "the retry deleted the previous generation")
        self.assertEqual(replaced.read_bytes(), live_before)
        self.assertEqual(second.get("replaced_kept_at"), "companion-v2.sqlite3.replaced")
        self.assertEqual(installation.probe(), "before")


# ---------------------------------------------------------------------------
# P2-2: a restore keeps the credentials the installation has today
# ---------------------------------------------------------------------------


class CredentialPreservationTests(unittest.TestCase):
    def test_restore_keeps_current_credentials_and_reports_them(self) -> None:
        installation = _installations(self)(external=True)
        manifest = installation.create()
        installation.stage(manifest)
        preview = installation.backup().preview_restore(snapshot_id=manifest["snapshot_id"]).unwrap()
        credentials = preview["credentials"]
        self.assertEqual(credentials["policy"], "preserved")
        self.assertIn(CREDENTIAL, credentials["files"])
        self.assertIn("api_key", json.dumps(credentials["keys"]))
        installation.close_runtime()
        applied = apply_pending(installation.root, installation_id=INSTALLATION,
                                schema_version=manifest["schema_version"],
                                backup_directory=installation.external)
        self.assertTrue(applied["applied"], applied)
        preserved = applied["credentials_preserved"]
        self.assertIn(CREDENTIAL, preserved["files"])
        self.assertIn("api_key", json.dumps(preserved["keys"]))
        # The current credential file survives with its current bytes…
        self.assertEqual(installation.path(CREDENTIAL).read_bytes(), b"current-pairing")
        # …and the restored configuration keeps the current secret values while the
        # non-secret state comes from the snapshot.
        document = json.loads(installation.path(CONFIG).read_text())
        self.assertEqual(document["api_key"], CURRENT_KEY)
        self.assertEqual(document["nested"]["pairing_token"], "CURRENT-ONLY-TOKEN")
        self.assertEqual(document["synthetic_marker"], "before")
        # The snapshot itself still never carried the secret.
        packed = b"".join(path.read_bytes() for path in installation.bundle(
            manifest["snapshot_id"]).rglob("*") if path.is_file())
        self.assertNotIn(CURRENT_KEY.encode(), packed)
        self.assertNotIn(SNAPSHOT_SECRET.encode(), packed)

    def test_restore_does_not_invent_credentials_that_do_not_exist(self) -> None:
        installation = _installations(self)(external=True)
        manifest = installation.create()
        installation.mutate()
        installation.path(CREDENTIAL).unlink()
        installation.path(CONFIG).unlink()
        installation.backup().restore(snapshot_id=manifest["snapshot_id"],
                                      confirmed_hash=manifest["content_hash"]).unwrap()
        installation.close_runtime()
        applied = apply_pending(installation.root, installation_id=INSTALLATION,
                                schema_version=manifest["schema_version"],
                                backup_directory=installation.external)
        self.assertTrue(applied["applied"], applied)
        self.assertEqual(applied["credentials_preserved"]["keys"], [])
        document = json.loads(installation.path(CONFIG).read_text())
        self.assertNotIn("api_key", document)


# ---------------------------------------------------------------------------
# P2-3: listing is a summary; inspect keeps verifying everything
# ---------------------------------------------------------------------------


class ListingSummaryTests(unittest.TestCase):
    def test_listing_returns_a_summary_and_never_hashes_the_payload(self) -> None:
        installation = _installations(self)(external=True)
        manifest = installation.create()
        backup = installation.backup()
        listing = backup.listing().unwrap()
        item = listing["snapshots"][0]
        self.assertEqual(set(item), {
            "snapshot_id", "format", "created_at", "note", "byte_size", "artifact_count",
            "installation_id", "schema_version", "content_hash", "restorable", "restore_scope",
        })
        self.assertEqual(item["artifact_count"], manifest["artifact_count"])
        self.assertLess(len(json.dumps(listing)), 2000)
        # Neither a listing nor a second listing may hash a payload file.
        with patch.object(backup_module, "_sha256",
                          side_effect=AssertionError("listing must not hash the payload")):
            first = installation.backup().listing()
            second = installation.backup().listing()
        self.assertTrue(first.is_success, first.failure)
        self.assertTrue(second.is_success, second.failure)
        # A full verification still hashes everything.
        hashed: list[str] = []
        original = backup_module._sha256

        def counting(path):
            hashed.append(str(path))
            return original(path)

        with patch.object(backup_module, "_sha256", new=counting):
            self.assertTrue(backup.inspect(snapshot_id=manifest["snapshot_id"]).is_success)
        self.assertGreaterEqual(len(hashed), 4)

    def test_a_broken_payload_is_listed_and_rejected_by_inspect(self) -> None:
        installation = _installations(self)()
        manifest = installation.create()
        installation.payload(manifest["snapshot_id"], AUX_DATABASE).unlink()
        # Lazy listing: an auxiliary artifact is only verified by inspect/restore.
        self.assertEqual(len(installation.backup().listing().unwrap()["snapshots"]), 1)
        self.assertFalse(installation.backup().inspect(snapshot_id=manifest["snapshot_id"]).is_success)
        # A bundle without its main database cannot be listed at all.
        installation.payload(manifest["snapshot_id"], MAIN_DATABASE).unlink()
        listing = installation.backup().listing().unwrap()
        self.assertEqual(listing["snapshots"], [])
        self.assertEqual([item["snapshot_id"] for item in listing["problems"]],
                         [manifest["snapshot_id"]])


# ---------------------------------------------------------------------------
# P2-4: an incomplete bundle never blocks a backup-directory move
# ---------------------------------------------------------------------------


class IncompleteBundleTests(unittest.TestCase):
    def test_an_incomplete_bundle_is_skipped_with_a_report(self) -> None:
        installation = _installations(self)()
        first = installation.create()
        second = installation.create(now=NOW + timedelta(minutes=1), note="second")
        third = installation.create(now=NOW + timedelta(minutes=2), note="third")
        # Deep damage: an auxiliary artifact is gone. A listing is deliberately too
        # cheap to see it; the copy/retention scan is not.
        installation.payload(first["snapshot_id"], AUX_DATABASE).unlink()
        # Structural damage: the bundle directory is gone, so the listing reports it.
        shutil.rmtree(installation.bundle(third["snapshot_id"]))
        backup = installation.backup()
        listing = backup.listing().unwrap()
        # The snapshot whose bundle is gone cannot be listed at all; the one with a
        # missing auxiliary artifact still is (the listing is a summary, inspect is
        # the authority).
        self.assertEqual([item["snapshot_id"] for item in listing["snapshots"]],
                         [second["snapshot_id"], first["snapshot_id"]])
        self.assertEqual([item["snapshot_id"] for item in listing["problems"]], [third["snapshot_id"]])
        plan = cleanup_plan(installation.root, retention_count=1, retention_days=1,
                            now=NOW + timedelta(days=365), backup_directory=backup.directory,
                            installation_id=INSTALLATION)
        self.assertEqual(sorted(plan["incomplete_ids"]),
                         sorted([first["snapshot_id"], third["snapshot_id"]]))

        destination = installation.base / "moved"
        with self.assertRaises(OSError):
            copy_backup_snapshots(backup.directory, destination, installation_id=INSTALLATION)
        report: dict = {}
        pairs = copy_backup_snapshots(backup.directory, destination, installation_id=INSTALLATION,
                                      skip_incomplete=True, report=report)
        self.assertEqual(len(pairs), 1)
        self.assertEqual(sorted(item["snapshot_id"] for item in report["skipped"]),
                         sorted([first["snapshot_id"], third["snapshot_id"]]))
        self.assertTrue((destination / f"companion-v2-{second['snapshot_id']}.bundle").is_dir())
        self.assertFalse((destination / f"companion-v2-{first['snapshot_id']}.bundle").exists())
        # Skipping is not deleting: both broken pairs stay exactly where they were.
        self.assertTrue(installation.bundle(first["snapshot_id"]).is_dir())
        self.assertTrue(installation.sidecar(first["snapshot_id"]).is_file())
        self.assertTrue(installation.sidecar(third["snapshot_id"]).is_file())

    def test_a_broken_pair_can_be_quarantined_without_losing_a_byte(self) -> None:
        """P2-4: the operator needs a way to stop a broken pair from blocking moves."""
        installation = _installations(self)()
        manifest = installation.create()
        snapshot_id = manifest["snapshot_id"]
        shutil.rmtree(installation.bundle(snapshot_id))
        backup = installation.backup()
        refused = backup.quarantine(snapshot_id=snapshot_id)
        self.assertFalse(refused.is_success)
        self.assertEqual(refused.failure.code, "backup_quarantine_confirmation_required")
        result = backup.quarantine(snapshot_id=snapshot_id, confirm=True).unwrap()
        self.assertEqual(result["reason"], "bundle_missing")
        quarantine = backup.directory / ".incomplete"
        self.assertTrue((quarantine / f"companion-v2-{snapshot_id}.json").is_file())
        self.assertEqual(backup.listing().unwrap()["problems"], [])
        # The pair is now out of the way, so a directory move no longer trips over it.
        self.assertEqual(copy_backup_snapshots(backup.directory, installation.base / "moved",
                                               installation_id=INSTALLATION), ())

    def test_a_snapshot_a_staged_restore_needs_is_never_skipped(self) -> None:
        installation = _installations(self)()
        first = installation.create()
        installation.payload(first["snapshot_id"], AUX_DATABASE).unlink()
        (installation.root / PENDING_FILENAME).write_text(json.dumps({
            "format": RESTORE_FORMAT, "snapshot_id": first["snapshot_id"],
            "content_hash": "0" * 64, "requested_at": NOW.isoformat(),
        }))
        with self.assertRaises(OSError) as caught:
            copy_backup_snapshots(installation.backup().directory, installation.base / "moved",
                                  installation_id=INSTALLATION, skip_incomplete=True,
                                  data_root=installation.root)
        self.assertIn("staged restore", str(caught.exception))


# ---------------------------------------------------------------------------
# P3: the low-risk items this round can close
# ---------------------------------------------------------------------------


class ScratchAndCleanupTests(unittest.TestCase):
    def test_a_stale_displaced_bundle_is_swept(self) -> None:
        """The displaced name had no leading dot, and the sweep only matched ones that did."""
        base = Path(tempfile.mkdtemp(prefix="r1-round1fix-sweep-"))
        self.addCleanup(shutil.rmtree, base, True)
        leftover = base / f"companion-v2-{'b' * 32}.bundle.old-12345678"
        (leftover / "payload").mkdir(parents=True)
        (leftover / "payload" / "stale.bin").write_bytes(b"stale")
        old = time.time() - 7200
        for path in (leftover / "payload" / "stale.bin", leftover / "payload", leftover):
            os.utime(path, (old, old))
        backup_module._sweep_stale_scratch(base)
        self.assertFalse(leftover.exists())

    def test_cleanup_all_backups_uses_the_real_data_root_for_pending_protection(self) -> None:
        """``cleanup_all_backups`` passed ``directory.parent`` as the data root."""
        base = Path(tempfile.mkdtemp(prefix="r1-round1fix-all-"))
        self.addCleanup(shutil.rmtree, base, True)
        root = base / "data"
        directory = root / "manual" / "backups"
        directory.mkdir(parents=True)
        identifiers = {"older": "1" * 32, "newer": "2" * 32}
        for label, identifier in identifiers.items():
            database = directory / f"companion-v2-{identifier}.bundle" / "payload" / MAIN_DATABASE
            database.parent.mkdir(parents=True)
            database.write_bytes(b"x")
            (directory / f"companion-v2-{identifier}.json").write_text(json.dumps({
                "format": BACKUP_FORMAT, "snapshot_id": identifier,
                "created_at": (NOW - timedelta(days=5 if label == "older" else 1)).isoformat(),
                "installation_id": INSTALLATION, "schema_version": "s",
                "byte_size": 1, "content_hash": "fixture", "note": "",
                "artifacts": [{"path": MAIN_DATABASE, "kind": "sqlite", "bytes": 1,
                               "sha256": hashlib.sha256(b"x").hexdigest()}],
                "policy": {"version": 1, "roots": [MAIN_DATABASE], "derived": []},
            }))
        (root / PENDING_FILENAME).write_text(json.dumps({
            "format": RESTORE_FORMAT, "snapshot_id": identifiers["older"],
            "content_hash": "fixture", "requested_at": NOW.isoformat(),
        }))
        result = cleanup_all_backups(root, retention_count=1, retention_days=1,
                                     now=NOW, dry_run=False)
        self.assertEqual(result["removed_count"], 0, result)
        self.assertTrue((directory / f"companion-v2-{identifiers['older']}.bundle").is_dir())


if __name__ == "__main__":
    unittest.main()
