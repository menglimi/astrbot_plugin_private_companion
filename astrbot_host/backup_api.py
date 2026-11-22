"""Authenticated whole-installation backup and restore for the pinned V2 profile.

Restoring is deliberately a two-phase operation: the request is staged, and the
host applies it on the next start before the store is opened. See
``companion_v2/storage/backup.py`` for why the live connection is never rebuilt.

Two host-side rules matter as much as the engine's:

* The engine is synchronous and touches gigabytes of disk. Running it inside the
  route's ``async`` body would block AstrBot's event loop for the whole operation
  (no messages, no web requests, no timers), so every call is handed to a worker
  thread (review P1-1).
* Two operations must not interleave. ``_control_lock`` is only held for the cheap
  identity/session check: holding it across a multi-minute snapshot would stall
  message delivery authorization for everyone else. The work itself is serialized
  by ``backup_operation_lock`` — one plain ``threading.Lock`` per controller, held
  by the worker for the entire call.
"""

import asyncio
import json
import logging
from pathlib import Path
import threading

from astrbot.api.web import request
from companion_v2.execution.models import TaskError
from companion_v2.foundation.outcome import Outcome
from companion_v2.storage.backup import (
    PENDING_FILENAME, InstallationBackup, apply_pending, current_schema_version,
    # The startup gate owns the same journal-recovery prologue ``apply_pending``
    # performs. Importing the engine's helpers keeps the protocol in one place
    # instead of parsing ``.restore-*/journal.json`` a second time.
    _discard_journal, _journals, _read_journal, _recover_journal, _tree_has_files,
)
from companion_v2.storage.management import retention_policy

from .management_identity import current_request_username, require_management_operator


logger = logging.getLogger(__name__)
PREFIX = "/astrbot_plugin_private_companion/backup"
MAX_REQUEST_BYTES = 65536
# Restore reasons that mean the installation itself is in an unknown state.  They
# are different from "the restore failed, the original bytes are back": opening a
# runtime after one of these would create an empty database next to the journal.
RESTORE_STARTUP_BLOCKING_REASONS = frozenset({
    "backup_restore_rollback_incomplete",
    "backup_restore_journal_unreadable",
})
RESTORE_STARTUP_ADVICE = (
    "Nothing was deleted. Keep the plugin stopped and fix the underlying filesystem "
    "problem (permissions, free space, or a path that is now a directory), then "
    "restart: the next start retries the same journal-driven rollback before the "
    "database is opened. Do not delete restore-pending.json or the .restore-* "
    "journal; they are the only copy of the previous installation."
)
RESTORE_JOURNAL_ADVICE = (
    "Nothing was deleted. The journal lost its protocol file while it still holds "
    "displaced installation data, so an automatic rollback cannot know what to put "
    "back. Keep the plugin stopped and inspect the .restore-* folder next to the "
    "runtime directory (journal.json and rollback/) before restoring or removing "
    "anything; then restart."
)
_LOCK_TYPE = type(threading.Lock())
_FALLBACK_LOCK = threading.Lock()


def backup_operation_lock(controller) -> threading.Lock:
    """One lock object per controller, created on first use.

    It lives on the controller so two Dashboard requests, a restore preview and a
    snapshot always queue behind the same lock; a module-level lock would serialize
    unrelated installations in one process.
    """
    lock = getattr(controller, "_backup_operation_lock", None)
    if isinstance(lock, _LOCK_TYPE):
        return lock
    if lock is None:
        created = threading.Lock()
        try:
            setattr(controller, "_backup_operation_lock", created)
        except AttributeError:  # controllers with __slots__ still get a shared lock
            return _FALLBACK_LOCK
        return created
    return _FALLBACK_LOCK


class BackupWebAPI:
    def __init__(self, controller):
        self.controller = controller

    def routes(self):
        return [(PREFIX, self.listing, ["GET"], "List completed V2 installation snapshots"),
                (PREFIX + "/create", self.create, ["POST"], "Write a verified snapshot of this installation"),
                (PREFIX + "/delete", self.delete, ["POST"], "Delete one confirmed installation snapshot"),
                (PREFIX + "/inspect", self.inspect, ["GET"], "Verify one snapshot without restoring it"),
                (PREFIX + "/restore/preview", self.preview, ["GET"], "Show what restoring one snapshot would replace"),
                (PREFIX + "/restore", self.restore, ["POST"], "Stage a restore; the host applies it on restart"),
                (PREFIX + "/restore/cancel", self.cancel, ["POST"], "Abandon a staged restore that cannot be applied"),
                (PREFIX + "/quarantine", self.quarantine, ["POST"],
                 "Move one incomplete snapshot pair aside so a backup directory move can proceed"),
                (PREFIX + "/pending", self.pending, ["GET"], "Report a restore waiting for a restart")]

    @staticmethod
    def _query(allowed):
        keys = set(request.query.keys())
        if keys - set(allowed) or any(len(request.query.getlist(key)) != 1 for key in keys):
            raise ValueError()
        return {key: request.query.get(key) for key in allowed if key in request.query}

    @staticmethod
    def _body(raw, allowed, optional=()):
        """Parse a JSON body whose keys are exactly ``allowed`` minus ``optional``.

        The contract stays strict: an unknown key, a duplicate key or a missing
        required key is refused. ``optional`` exists only for ``confirmed_scope``,
        which a page that saw the preview adds and an older page does not send.
        """
        def unique(pairs):
            values = {}
            for key, value in pairs:
                if key in values:
                    raise ValueError()
                values[key] = value
            return values
        arguments = json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
        if type(arguments) is not dict:
            raise ValueError()
        keys = set(arguments)
        if not keys <= set(allowed) or not set(allowed) - set(optional) <= keys:
            raise ValueError()
        return arguments

    async def _run(self, method, arguments):
        """Backups are installation-scoped, so they need an operator but no target."""
        from .web_api import outcome_response
        controller = self.controller
        # Validate identity and configuration on the loop (cheap, no disk). The
        # control lock is released before the work starts: holding it for a
        # multi-minute snapshot would stall every other control API — message
        # delivery authorization included — while doing nothing for safety. A
        # lifecycle change that closes the store mid-snapshot makes the engine fail
        # with a readable reason, which is the outcome we want anyway.
        async with controller._control_lock:
            try:
                if controller._terminated:
                    raise TaskError("staging_host_terminated", "plugin is stopped")
                username = current_request_username(request)
                if type(username) is not str or not username.strip() or username.startswith("api_key:"):
                    raise TaskError("dashboard_identity_required", "sign in to the AstrBot Dashboard")
                session = controller.session or controller.last_session
                if session is None or controller.runtime is None:
                    raise TaskError("staging_not_initialized", "load the explicit V2 profile before managing backups")
                require_management_operator(controller.runtime, username, at=controller.now())
                backup = InstallationBackup(controller.runtime, now=controller.now,
                                            **retention_policy(controller.config))
            except TaskError as error:
                return outcome_response(Outcome.failed(error.code, str(error)))
            except (TypeError, ValueError, KeyError):
                return outcome_response(Outcome.failed("backup_request_invalid", "invalid backup arguments"))
        lock = backup_operation_lock(controller)
        try:
            result = await asyncio.to_thread(self._invoke, lock, backup, method, arguments)
        except asyncio.CancelledError:
            raise
        except Exception:  # a worker failure must not take the route down
            logger.exception("[CompanionV2] 备份操作未完成 method=%s", method)
            result = Outcome.failed("backup_operation_failed", "the backup operation could not be completed")
        try:
            # 还原预览与执行都带上「这一步会影响谁」。还原是整根替换，
            # 多目标下同时替换该安装下所有目标的资料；用户最容易误以为
            # 「只还原我当前看的这个角色」。
            if method in ("preview_restore", "restore") and result.is_success:
                document = result.unwrap()
                if isinstance(document, dict):
                    result = Outcome.success({**document, "scope": self._installation_scope()})
            return outcome_response(result)
        except (TypeError, ValueError):
            return outcome_response(Outcome.failed("backup_operation_failed",
                                                   "the backup operation returned an unexpected result"))

    @staticmethod
    def _invoke(lock, backup, method, arguments):
        with lock:
            return getattr(backup, method)(**arguments)

    def _installation_scope(self):
        """这次还原覆盖的目标与角色数量。按运行档的形状判，不按模式名猜。"""
        settings = getattr(self.controller, "settings", None)
        targets = getattr(settings, "all_targets", None)
        if targets is not None:
            personas = getattr(settings, "personas", ()) or ()
            return {"mode": "multi", "targets": len(targets),
                    "roles": len({(getattr(item, "bot_id", None), getattr(item, "role_id", None)) for item in personas}),
                    "restart_required": True}
        return {"mode": "single", "targets": 1,
                "roles": 1 if getattr(settings, "role_id", None) else 0, "restart_required": True}

    async def listing(self):
        from .web_api import outcome_response
        try:
            self._query([])
        except ValueError:
            return outcome_response(Outcome.failed("backup_request_invalid", "invalid backup query"))
        return await self._run("listing", {})

    async def pending(self):
        from .web_api import outcome_response
        try:
            self._query([])
        except ValueError:
            return outcome_response(Outcome.failed("backup_request_invalid", "invalid backup query"))
        return await self._run("pending", {})

    async def create(self):
        from .web_api import outcome_response
        try:
            raw = await request.body()
            if len(raw) > MAX_REQUEST_BYTES or request.query:
                raise ValueError()
            arguments = self._body(raw, {"note"}) if raw else {"note": ""}
        except (ValueError, UnicodeError, RecursionError):
            return outcome_response(Outcome.failed("backup_request_invalid", "invalid backup note"))
        return await self._run("create", arguments)

    def _snapshot_id(self):
        query = self._query(["snapshot_id"])
        snapshot_id = query.get("snapshot_id", "")
        if not snapshot_id:
            raise ValueError()
        return snapshot_id

    async def inspect(self):
        from .web_api import outcome_response
        try:
            snapshot_id = self._snapshot_id()
        except ValueError:
            return outcome_response(Outcome.failed("backup_request_invalid", "a snapshot id is required"))
        return await self._run("inspect", {"snapshot_id": snapshot_id})

    async def preview(self):
        from .web_api import outcome_response
        try:
            snapshot_id = self._snapshot_id()
        except ValueError:
            return outcome_response(Outcome.failed("backup_request_invalid", "a snapshot id is required"))
        return await self._run("preview_restore", {"snapshot_id": snapshot_id})

    async def restore(self):
        from .web_api import outcome_response
        try:
            raw = await request.body()
            if len(raw) > MAX_REQUEST_BYTES or request.query:
                raise ValueError()
            arguments = self._body(raw, ("snapshot_id", "confirmed_hash", "confirmed_scope"),
                                   optional=("confirmed_scope",))
        except (ValueError, UnicodeError, RecursionError):
            return outcome_response(Outcome.failed("backup_request_invalid", "invalid restore command"))
        return await self._run("restore", arguments)

    async def cancel(self):
        """Abandon a staged restore. The only request that clears the queue."""
        from .web_api import outcome_response
        try:
            raw = await request.body()
            if len(raw) > MAX_REQUEST_BYTES or request.query:
                raise ValueError()
            arguments = self._body(raw, ("confirm",)) if raw else {"confirm": False}
        except (ValueError, UnicodeError, RecursionError):
            return outcome_response(Outcome.failed("backup_request_invalid", "invalid cancel command"))
        return await self._run("cancel_pending", arguments)

    async def delete(self):
        from .storage_api import storage_request_lock
        from .web_api import outcome_response
        try:
            raw = await request.body()
            if len(raw) > MAX_REQUEST_BYTES or request.query:
                raise ValueError()
            arguments = self._body(raw, ("snapshot_id", "confirmed_hash", "confirm"))
        except (ValueError, UnicodeError, RecursionError):
            return outcome_response(Outcome.failed("backup_request_invalid", "invalid delete command"))
        # Resolve the active directory only after any relocation is done.
        async with storage_request_lock(self.controller):
            return await self._run("delete", arguments)

    async def quarantine(self):
        """Move an incomplete snapshot pair aside; nothing is deleted."""
        from .web_api import outcome_response
        try:
            raw = await request.body()
            if len(raw) > MAX_REQUEST_BYTES or request.query:
                raise ValueError()
            arguments = self._body(raw, ("snapshot_id", "confirm"))
        except (ValueError, UnicodeError, RecursionError):
            return outcome_response(Outcome.failed("backup_request_invalid", "invalid quarantine command"))
        return await self._run("quarantine", arguments)


def apply_staged_restore(data_root, *, installation_id: str, backup_directory=None) -> dict:
    """Apply a staged restore before the store is opened. No live connection exists.

    This is called from the host's assembly path before ``StorageRuntime.open``,
    which is the only moment the database file can be replaced without orphaning
    the shared connection. It reads the data root's current schema revision from
    the file itself, because the runtime is not available yet. Errors come back as
    a plain document rather than raising, so a failed restore leaves the original
    data root in place and start-up can continue.
    """
    root = Path(data_root)
    if not (root / PENDING_FILENAME).is_file():
        return {"applied": False, "reason": "no_pending_restore"}
    schema_version = current_schema_version(root)
    if schema_version is None:
        return {"applied": False, "reason": "backup_store_unreadable"}
    return apply_pending(root, installation_id=installation_id, schema_version=schema_version,
                         backup_directory=backup_directory)


# -- startup gate ------------------------------------------------------------
#
# ``apply_staged_restore`` can also report a failure *after* it tried to put the
# previous installation back.  ``backup_restore_rollback_incomplete`` means the
# journal is now the only copy of some files: opening ``StorageRuntime`` here would
# create an empty main database next to that journal (``runtime._open_connection``
# creates whatever is missing), and every later start would see a different
# generation than the one the operator is trying to recover.  The gate below is
# deliberately separate from the engine so the decision is visible at the host
# boundary and testable without a live runtime.


def _journal_diagnostics(data_root) -> list[dict]:
    """Describe every restore journal without touching it."""
    entries: list[dict] = []
    for journal in _journals(Path(data_root)):
        document = _read_journal(journal)
        entries.append({
            "name": journal.root.name,
            "readable": document is not None,
            "phase": document.get("phase") if document is not None else None,
            "snapshot_id": document.get("snapshot_id") if document is not None else None,
            "rollback_files": _tree_has_files(journal.rollback_root),
            "pruned_files": _tree_has_files(journal.pruned_root),
            "superseded_files": _tree_has_files(journal.superseded_root),
        })
    return entries


def _pending_diagnostics(data_root) -> dict | None:
    """Describe the staged request in non-secret terms (never its full content)."""
    path = Path(data_root) / PENDING_FILENAME
    if not path.is_file():
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"present": True, "readable": False}
    if type(document) is not dict:
        return {"present": True, "readable": False}
    return {
        "present": True,
        "readable": True,
        "format": document.get("format") if type(document.get("format")) is str else None,
        "snapshot_id": document.get("snapshot_id") if type(document.get("snapshot_id")) is str else None,
        "restore_scope": (document.get("restore_scope")
                          if type(document.get("restore_scope")) is str else None),
    }


def _recover_uncommitted_journals(data_root: Path) -> tuple[list[str], dict | None]:
    """Finish the journal-driven rollback before anything opens the database.

    ``apply_pending`` already does this for the request it is about to apply, but
    it first needs a readable schema revision.  A crash after the main database was
    displaced makes that read fail (``backup_store_unreadable``) and the old open
    path then created a fresh empty database.  Recovering first is the same
    idempotent operation the engine performs; it only moves journaled files back.

    A journal that still holds displaced data but lost its protocol file is not
    guessed at: it blocks startup and keeps every byte.  A *committed* journal is
    left alone here; its installation is already the new one and ``apply_pending``
    owns the finalize retry.
    """
    root = Path(data_root)
    recovered: list[str] = []
    for journal in _journals(root):
        name = journal.root.name
        document = _read_journal(journal)
        if document is not None and document.get("phase") == "committed":
            continue
        if document is None:
            if (_tree_has_files(journal.rollback_root) or _tree_has_files(journal.pruned_root)
                    or _tree_has_files(journal.superseded_root)):
                return recovered, {
                    "reason": "backup_restore_journal_unreadable",
                    "detail": (f"the restore journal {name} lost its protocol file while it "
                               "still holds displaced installation data"),
                    "journal": name,
                }
            continue
        try:
            _recover_journal(journal, root)
            _discard_journal(journal)
        except Exception as error:  # noqa: BLE001 - every failure must block opening
            reason = getattr(error, "code", None)
            if reason not in RESTORE_STARTUP_BLOCKING_REASONS:
                reason = "backup_restore_rollback_incomplete"
            return recovered, {"reason": reason,
                               "detail": str(error) or type(error).__name__,
                               "journal": name}
        recovered.append(name)
    return recovered, None


def guard_restore_startup(data_root, *, installation_id: str, backup_directory=None) -> tuple[dict, dict]:
    """Apply a staged restore and decide whether the store may be opened.

    Returns ``(restore_result, state)``.  ``state["blocking"]`` is true only when
    the previous installation cannot be put back (or an interrupted journal cannot
    be interpreted).  In that case the caller must raise and leave the data root
    untouched: the staged request and the journal are not cleared and no database
    is created.  A restore that *succeeded* but could not finalize is explicitly
    allowed to proceed — its commit marker is written and the bytes were verified;
    the next start only finishes publication.  A restore that failed but rolled
    back is also allowed to proceed on the original bytes.
    """
    root = Path(data_root)
    recovered, blocked = _recover_uncommitted_journals(root)
    if blocked is None:
        result = apply_staged_restore(root, installation_id=installation_id,
                                      backup_directory=backup_directory)
    else:
        # Do not call apply: a matching request must not be retried while the
        # journal cannot be recovered.
        result = {"applied": False, "reason": blocked["reason"], "detail": blocked["detail"]}
    pending = _pending_diagnostics(root)
    journals = _journal_diagnostics(root)
    reason = result.get("reason")

    if blocked is not None:
        blocking, code, detail, state = True, blocked["reason"], blocked["detail"], "blocked"
    elif result.get("applied") is True:
        finalizing = bool(result.get("pending_finalization_required")
                          or result.get("journal_cleanup_required"))
        blocking, code, detail = False, None, result.get("detail")
        state = "applied_pending_finalization" if finalizing else "applied"
    elif reason in RESTORE_STARTUP_BLOCKING_REASONS:
        blocking, code, detail = True, reason, str(result.get("detail") or reason)
        state = "blocked"
    elif reason == "no_pending_restore":
        blocking, code, detail, state = False, None, None, "clean"
    else:
        # A normal failure (space, unverified snapshot, unreadable request) either
        # never mutated the installation or rolled it back byte for byte.
        blocking, code, detail, state = False, None, result.get("detail"), "not_applied"

    advice = None
    if blocking:
        advice = (RESTORE_JOURNAL_ADVICE if code == "backup_restore_journal_unreadable"
                  else RESTORE_STARTUP_ADVICE)
        base = (detail or "the previous restore could not be rolled back").rstrip().rstrip(".")
        detail = f"{base}. The database was not opened. {advice}"

    return result, {
        "state": state,
        "blocking": blocking,
        "startup_allowed": not blocking,
        "code": code,
        "detail": detail,
        "advice": advice,
        "data_root": str(root),
        "pending": pending,
        "journals": journals,
        "recovered_journals": recovered,
    }
