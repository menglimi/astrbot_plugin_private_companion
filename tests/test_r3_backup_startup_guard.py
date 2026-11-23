"""Round-3: a failed restore rollback is a startup gate, not a fresh empty store.

Round-1 added ``apply_staged_restore`` to the multi-target open path but ignored
``backup_restore_rollback_incomplete``.  ``StorageRuntime.open`` creates the main
database when the file is missing, so the host could silently start on an empty
store while the real installation sat in the restore journal.  These tests use
synthetic directories only and inject every failure locally:

* rollback that cannot put the original file back  -> the store must not open;
* a journal whose protocol file is gone             -> the store must not open;
* an orphan prepared journal (crash mid-swap)       -> roll the original back first;
* a committed restore whose finalize failed         -> opening is allowed;
* a normal restore failure that rolled back         -> opening is allowed;
* pending request + missing database                -> never create an empty one;
* a blocked start whose cause is then fixed          -> the next start finishes it;
* the mapping-save open path (the only one that bypasses ``_create_storage``)
  runs the same gate instead of forking a second installation;
* the multi controller maps the blocked state to a readable ``HostControlError``
  and keeps ``last_restore`` / ``restore_state`` for diagnosis, without echoing
  the staged request's digest.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

SDK_AVAILABLE = importlib.util.find_spec("astrbot") is not None

# These tests import the host adapter, which needs the AstrBot SDK.  They are
# skipped (not stubbed) in the SDK-free environment: another suite installs its own
# ``astrbot`` stubs at collection time, and a second stub object would leave the
# already-imported ``backup_api.request`` bound to the first one.  The gate logic
# itself only touches the engine and temporary directories; the SDK is a host
# wiring requirement, not a network requirement.
from companion_v2.storage.backup import (
    JOURNAL_FORMAT, MAIN_DATABASE, PENDING_FILENAME, RESTORE_FORMAT,
)

INSTALLATION = "r3-startup-guard-installation"
SNAPSHOT_ID = "a" * 32
CONTENT_HASH = "b" * 64


def _write_pending(root: Path, *, snapshot_id: str = SNAPSHOT_ID) -> Path:
    path = root / PENDING_FILENAME
    path.write_text(json.dumps({
        "format": RESTORE_FORMAT, "snapshot_id": snapshot_id, "content_hash": CONTENT_HASH,
        "installation_id": INSTALLATION, "requested_at": "2026-10-27T09:00:00+00:00",
    }), encoding="utf-8")
    return path


def _write_journal(root: Path, *, name: str = ".restore-aaaaaaaa-11111111",
                   phase: str = "prepared", snapshot_id: str = SNAPSHOT_ID,
                   original: bytes | None = b"original-bytes") -> Path:
    journal = root / name
    journal.mkdir(parents=True)
    if original is not None:
        (journal / "rollback").mkdir()
        (journal / "rollback" / MAIN_DATABASE).write_bytes(original)
    (journal / "journal.json").write_text(json.dumps({
        "format": JOURNAL_FORMAT, "phase": phase,
        "snapshot_id": snapshot_id, "content_hash": CONTENT_HASH,
        "installation_id": INSTALLATION,
        "artifacts": [{"path": MAIN_DATABASE, "kind": "sqlite"}],
        "roots": [MAIN_DATABASE],
    }), encoding="utf-8")
    return journal


def _make_original_database(root: Path) -> bytes:
    """Create a real V2 database with a marker row, then return its bytes."""
    from companion_v2.storage.runtime import StorageRuntime

    runtime = StorageRuntime.open(data_root=root, installation_id=INSTALLATION)
    try:
        with runtime.transaction() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO runtime_metadata(key, value) VALUES('r3-marker', 'original')")
    finally:
        runtime.close()
    return (root / MAIN_DATABASE).read_bytes()


class _StopBeforeOpening(Exception):
    """Sentinel: the gate was passed, so the test stops the real store from opening."""


def _mapping_controller(*, profile_root: Path, data_root: Path, settings) -> types.SimpleNamespace:
    """The slice of the controller that ``_apply_reviewed_bindings`` touches.

    Parsing a real Dashboard candidate needs the AstrBot host, so the two methods
    that reach out to it are stubbed. Everything from the mapping decision down to
    ``StorageRuntime.open`` — including the startup gate — is the real code.
    """
    async def probe(candidate_settings, *, include_conversations=False,
                    allow_unavailable_model=False, allow_unavailable_accounts=False):
        return {}, None

    def resolve(candidate):
        return settings, profile_root, False

    return types.SimpleNamespace(
        _lifecycle_lock=asyncio.Lock(), _control_lock=asyncio.Lock(),
        _terminated=False, _accepting=False, config={}, runtime=None, session=None,
        last_session=None, restore_blocked=False, restore_state=None, last_restore=None,
        save_plugin_config=lambda candidate: None, _idle=lambda: None,
        _probe=probe, _resolve_settings=resolve, settings=settings,
        profile_root=profile_root, data_root=data_root,
        web_api=types.SimpleNamespace(_registered_routes=[]),
        unregister_routes=lambda: None, register_routes=lambda: None,
        refresh_settings=lambda: None, last_failure=None)


@unittest.skipUnless(SDK_AVAILABLE, "the host adapter needs the AstrBot SDK")
class RestoreStartupGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from astrbot_host import backup_api
        cls.backup_api = backup_api

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="r3-startup-gate-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "runtime"
        self.root.mkdir(parents=True)

    def test_rollback_incomplete_blocks_startup_and_preserves_the_scene(self):
        """The exact round-1 gap: a failed rollback must never open an empty store."""
        pending = _write_pending(self.root)
        journal = _write_journal(self.root)
        # The live path is a directory, so the journaled original cannot be moved
        # back.  Recovery must stop and report, not delete or "work around" it.
        (self.root / MAIN_DATABASE).mkdir()

        result, state = self.backup_api.guard_restore_startup(self.root, installation_id=INSTALLATION)

        self.assertFalse(result["applied"], result)
        self.assertTrue(state["blocking"], state)
        self.assertEqual(state["code"], "backup_restore_rollback_incomplete")
        self.assertIn("nothing", state["detail"].lower())
        self.assertTrue(pending.is_file(), "the staged request was cleared")
        self.assertTrue((journal / "journal.json").is_file(), "the journal was cleared")
        self.assertEqual((journal / "rollback" / MAIN_DATABASE).read_bytes(), b"original-bytes")
        self.assertFalse((self.root / MAIN_DATABASE).is_file())
        self.assertEqual([item["name"] for item in state["journals"]], [journal.name])
        self.assertFalse(state["startup_allowed"])

    def test_an_unreadable_journal_with_displaced_data_blocks_startup(self):
        journal = self.root / ".restore-bbbbbbbb-22222222"
        (journal / "rollback").mkdir(parents=True)
        (journal / "rollback" / MAIN_DATABASE).write_bytes(b"only-copy")
        result, state = self.backup_api.guard_restore_startup(self.root, installation_id=INSTALLATION)
        self.assertFalse(result["applied"])
        self.assertTrue(state["blocking"], state)
        self.assertEqual(state["code"], "backup_restore_journal_unreadable")
        self.assertTrue((journal / "rollback" / MAIN_DATABASE).is_file())
        self.assertFalse((self.root / MAIN_DATABASE).exists())

    def test_an_orphan_prepared_journal_is_rolled_back_before_opening(self):
        """A crash mid-swap leaves the original only inside the journal.

        The old open path returned ``backup_store_unreadable`` and then created a
        fresh empty database.  The gate rolls the original back first.
        """
        journal = _write_journal(self.root, original=b"original-bytes")
        result, state = self.backup_api.guard_restore_startup(self.root, installation_id=INSTALLATION)
        self.assertFalse(result["applied"], result)
        self.assertFalse(state["blocking"], state)
        self.assertIn(journal.name, state["recovered_journals"])
        self.assertEqual((self.root / MAIN_DATABASE).read_bytes(), b"original-bytes")
        self.assertFalse(journal.exists())
        self.assertEqual(state["state"], "clean")

    def test_pending_with_a_missing_database_never_creates_an_empty_one(self):
        """Recover the original first; the staged restore may still fail safely."""
        original = _make_original_database(self.root)
        path = self.root / MAIN_DATABASE
        journal = _write_journal(self.root, original=original)
        path.unlink()  # simulate a crash after the original was displaced
        _write_pending(self.root)

        result, state = self.backup_api.guard_restore_startup(self.root, installation_id=INSTALLATION)

        self.assertFalse(state["blocking"], state)
        self.assertTrue(path.is_file(), "the original database was not put back")
        self.assertEqual(path.read_bytes(), original)
        # apply then fails on the missing snapshot, but the installation is intact.
        self.assertFalse(result["applied"], result)
        self.assertIn(result["reason"], {"backup_snapshot_missing", "backup_pending_invalid"})
        self.assertIn(journal.name, state["recovered_journals"])

    def test_applied_restore_with_a_failed_finalize_may_continue(self):
        applied = {"applied": True, "snapshot_id": SNAPSHOT_ID,
                   "pending_finalization_required": True, "journal_cleanup_required": True}
        with patch.object(self.backup_api, "apply_staged_restore", lambda *a, **k: dict(applied)):
            result, state = self.backup_api.guard_restore_startup(self.root, installation_id=INSTALLATION)
        self.assertEqual(result, applied)
        self.assertFalse(state["blocking"], state)
        self.assertEqual(state["state"], "applied_pending_finalization")
        self.assertTrue(state["startup_allowed"])

    def test_a_restore_failure_that_rolled_back_may_continue(self):
        rolled_back = {"applied": False, "reason": "backup_restore_space_insufficient",
                       "detail": "no room", "rolled_back": True}
        with patch.object(self.backup_api, "apply_staged_restore", lambda *a, **k: dict(rolled_back)):
            result, state = self.backup_api.guard_restore_startup(self.root, installation_id=INSTALLATION)
        self.assertEqual(result, rolled_back)
        self.assertFalse(state["blocking"], state)
        self.assertEqual(state["state"], "not_applied")

    def test_the_next_start_finishes_the_rollback_once_the_obstacle_is_gone(self):
        """The refusal is not a dead end: its advice ("fix the path, restart") is true.

        The same bytes are on disk, only the rename that could not be performed is
        retried; the staged request is still there and is the engine's to clear.
        """
        original = _make_original_database(self.root)
        live = self.root / MAIN_DATABASE
        live.unlink()
        live.mkdir()  # the rename target is a directory: the rollback cannot proceed
        journal = _write_journal(self.root, original=original)
        pending = _write_pending(self.root)

        _result, blocked = self.backup_api.guard_restore_startup(self.root, installation_id=INSTALLATION)
        self.assertTrue(blocked["blocking"], blocked)
        self.assertEqual((journal / "rollback" / MAIN_DATABASE).read_bytes(), original)

        live.rmdir()  # the operator removes the thing that blocked the rename
        result, state = self.backup_api.guard_restore_startup(self.root, installation_id=INSTALLATION)

        self.assertFalse(state["blocking"], state)
        self.assertEqual(state["state"], "not_applied")
        self.assertIn(journal.name, state["recovered_journals"])
        self.assertEqual(live.read_bytes(), original, "the journaled original was not put back")
        self.assertFalse(journal.exists())
        self.assertTrue(pending.is_file(), "the gate must not clear a staged request")
        # No snapshot was ever written for this request, so the restore is refused —
        # but it is refused on the recovered installation, not on a fresh empty one.
        self.assertFalse(result["applied"], result)
        self.assertEqual(result["reason"], "backup_snapshot_missing")

    def test_diagnostics_are_a_whitelist_and_never_echo_the_request(self):
        _write_pending(self.root)
        _result, state = self.backup_api.guard_restore_startup(self.root, installation_id=INSTALLATION)
        self.assertEqual(set(state["pending"]),
                         {"present", "readable", "format", "snapshot_id", "restore_scope"})
        self.assertTrue(state["pending"]["present"])
        self.assertEqual(state["pending"]["snapshot_id"], SNAPSHOT_ID)
        # The request carries a digest and (in production) an installation identity:
        # neither may be copied into a status document.
        rendered = json.dumps(state)
        self.assertNotIn(CONTENT_HASH, rendered)
        self.assertNotIn(INSTALLATION, rendered)

    def test_an_empty_stray_restore_directory_neither_blocks_nor_gets_deleted(self):
        stray = self.root / ".restore-cccccccc-33333333"
        (stray / "rollback").mkdir(parents=True)
        result, state = self.backup_api.guard_restore_startup(self.root, installation_id=INSTALLATION)
        self.assertFalse(state["blocking"], state)
        self.assertEqual(state["state"], "clean")
        self.assertEqual(result["reason"], "no_pending_restore")
        self.assertEqual([item["name"] for item in state["journals"]], [stray.name])
        self.assertFalse(state["journals"][0]["readable"])
        self.assertTrue((stray / "rollback").is_dir(), "a directory we cannot interpret was deleted")


@unittest.skipUnless(SDK_AVAILABLE, "the full host controller needs the AstrBot SDK")
class MultiControllerStartupGateTests(unittest.TestCase):
    def setUp(self):
        from astrbot_host import multi_controller

        self.multi_controller = multi_controller
        temporary = tempfile.TemporaryDirectory(prefix="r3-controller-gate-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.profile_root = self.base / "profile"
        self.data_root = self.base / "plugin"
        self.data_root.mkdir()
        self.settings = types.SimpleNamespace(installation_id=INSTALLATION)
        self.controller = types.SimpleNamespace(
            _terminated=False, settings_error=None, settings=self.settings,
            context=None, native_types=None, authority=None, last_restore=None,
            restore_state=None, restore_blocked=False,
            profile_root=self.profile_root, data_root=self.data_root, config={},
            now=lambda: None, _configured_platform_name=lambda platform_id: "",
            _configured_private_session=lambda *args: None, _parked_binding=lambda: None)
        self.controller._settings = lambda: self.settings

    def _create(self):
        return self.multi_controller.AstrBotMultiController._create_storage(self.controller)

    @staticmethod
    def _fake_authority(*args, **kwargs):
        return types.SimpleNamespace()

    def test_blocked_state_raises_a_readable_host_error_and_opens_nothing(self):
        blocked = {"applied": False, "reason": "backup_restore_rollback_incomplete",
                   "detail": "synthetic rollback failure", "journal": ".restore-deadbeef-12345678"}
        state = {"state": "blocked", "blocking": True,
                 "code": "backup_restore_rollback_incomplete",
                 "detail": "synthetic rollback failure; nothing was deleted",
                 "startup_allowed": False, "journals": [], "pending": True}
        with patch.object(self.multi_controller, "guard_restore_startup",
                          lambda *a, **k: (dict(blocked), dict(state))), \
             patch.object(self.multi_controller, "MultiAuthority", self._fake_authority):
            with self.assertRaises(self.multi_controller.HostControlError) as caught:
                self._create()

        self.assertEqual(caught.exception.code, "backup_restore_rollback_incomplete")
        self.assertIn("nothing was deleted", str(caught.exception))
        self.assertTrue(self.controller.restore_blocked)
        self.assertEqual(self.controller.restore_state, state)
        self.assertEqual(self.controller.last_restore, blocked)
        self.assertFalse((self.profile_root / "runtime" / MAIN_DATABASE).is_file(),
                         "the controller opened (and created) a database")

    def test_applied_finalize_pending_still_opens_the_store(self):
        applied = {"applied": True, "snapshot_id": SNAPSHOT_ID,
                   "pending_finalization_required": True}
        state = {"state": "applied_pending_finalization", "blocking": False,
                 "code": None, "detail": None, "startup_allowed": True,
                 "journals": [], "pending": True}
        with patch.object(self.multi_controller, "guard_restore_startup",
                          lambda *a, **k: (dict(applied), dict(state))), \
             patch.object(self.multi_controller, "MultiAuthority", self._fake_authority):
            runtime, authority, last_restore = self._create()
        try:
            self.assertEqual(last_restore, applied)
            self.assertFalse(self.controller.restore_blocked)
            self.assertIsNotNone(runtime)
            self.assertTrue((self.profile_root / "runtime" / MAIN_DATABASE).is_file())
        finally:
            runtime.close()

    def _mapping_stub(self):
        return _mapping_controller(profile_root=self.profile_root, data_root=self.data_root,
                                   settings=self.settings)

    def test_saving_a_mapping_cannot_bypass_a_blocked_startup_gate(self):
        """``_apply_reviewed_bindings`` opens the store without ``_create_storage``.

        It must not create the database the startup gate refused to create, even if
        the mapping is otherwise valid — a save is not a way to start on empty data.
        """
        runtime_root = self.profile_root / "runtime"
        runtime_root.mkdir(parents=True)
        (runtime_root / MAIN_DATABASE).write_bytes(b"live")
        stub = self._mapping_stub()
        stub.restore_blocked = True
        opened = []

        def fake_open(*, data_root, installation_id):
            opened.append(Path(data_root))
            raise _StopBeforeOpening()

        with patch.object(self.multi_controller.StorageRuntime, "open", new=fake_open):
            with self.assertRaises(self.multi_controller.TaskError) as caught:
                asyncio.run(self.multi_controller.AstrBotMultiController._apply_reviewed_bindings(stub, {}))
        self.assertEqual(caught.exception.code, "backup_restore_startup_blocked")
        self.assertEqual(opened, [])
        self.assertIsNone(stub.runtime)
        self.assertEqual((runtime_root / MAIN_DATABASE).read_bytes(), b"live")

    def test_saving_a_mapping_recovers_a_crash_journal_before_opening(self):
        """The bypass path must apply the journal instead of opening beside it."""
        runtime_root = self.profile_root / "runtime"
        runtime_root.mkdir(parents=True)
        original = _make_original_database(runtime_root)
        journal = _write_journal(runtime_root, original=original)
        live = runtime_root / MAIN_DATABASE
        live.write_bytes(b"half-swapped-by-the-crashed-restore")
        stub = self._mapping_stub()
        opened = []

        def fake_open(*, data_root, installation_id):
            opened.append(Path(data_root))
            raise _StopBeforeOpening()

        with patch.object(self.multi_controller.StorageRuntime, "open", new=fake_open):
            with self.assertRaises(_StopBeforeOpening):
                asyncio.run(self.multi_controller.AstrBotMultiController._apply_reviewed_bindings(stub, {}))
        self.assertEqual(opened, [runtime_root])
        self.assertEqual(live.read_bytes(), original, "the crashed restore's original was not put back")
        self.assertFalse(journal.exists())
        self.assertIn(journal.name, stub.restore_state["recovered_journals"])
        self.assertFalse(stub.restore_blocked)


if __name__ == "__main__":
    unittest.main()
