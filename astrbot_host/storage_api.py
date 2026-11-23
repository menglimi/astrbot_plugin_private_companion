"""Dashboard inventory, retention and data-location controls for V2 storage.

The inventory walk, the retention scan and the backup-directory copy are plain
filesystem work (``stat``/``hash``/``copy``).  They used to run inside the
``async`` route while holding ``controller._control_lock``, so a large installation
could block AstrBot's event loop and stall message delivery authorization.  The
route now validates cheap state on the loop, drops the control lock, and runs the
same synchronous functions in a worker thread (the pattern the backup routes
already use).  Mutating work also takes ``backup_operation_lock`` so a cleanup or a
directory move can never cross a snapshot/restore operation; one asyncio lock per
controller serializes the storage requests themselves.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
import json
import logging
from pathlib import Path

from astrbot.api.web import request
from companion_v2.execution.models import TaskError
from companion_v2.foundation.outcome import Outcome
from companion_v2.storage.compaction import compact_and_slim_databases, database_health_report
from companion_v2.storage.management import (auto_backup_policy, cleanup_backups, cleanup_plan,
                                             inventory, retention_policy, valid_auto_backup_interval)

from .backup_api import MAX_REQUEST_BYTES, backup_operation_lock
from .management_identity import current_request_username, require_controller_management_operator
from .storage_location import (finish_backup_relocation, relocate_backup_storage,
                               selected_backup_directory, selected_backup_root, selected_root)


logger = logging.getLogger(__name__)
PREFIX = "/astrbot_plugin_private_companion/storage"
_FALLBACK_REQUEST_LOCK = None


def storage_request_lock(controller) -> asyncio.Lock:
    """One asyncio lock per controller, created on first use.

    It serializes status/update requests so two cleanups never scan and delete the
    same pairs at the same time; ``backup_operation_lock`` separately excludes
    backup/restore work while files are being deleted or copied.
    """
    global _FALLBACK_REQUEST_LOCK
    lock = getattr(controller, "_storage_request_lock", None)
    if isinstance(lock, asyncio.Lock):
        return lock
    created = asyncio.Lock()
    try:
        setattr(controller, "_storage_request_lock", created)
    except AttributeError:
        # A controller that cannot carry attributes gets one process-wide fallback
        # instead of a fresh lock per call: over-serializing unrelated controllers is
        # slower, while a lock that exists only for the length of one call serializes
        # nothing at all.
        if _FALLBACK_REQUEST_LOCK is None:
            _FALLBACK_REQUEST_LOCK = asyncio.Lock()
        return _FALLBACK_REQUEST_LOCK
    return created


class StorageWebAPI:
    def __init__(self, controller):
        self.controller = controller

    def routes(self):
        return [(PREFIX, self.status, ["GET"], "Read V2 storage usage and retention"),
                (PREFIX, self.update, ["POST"], "Save V2 storage location and cleanup policy"),
                (f"{PREFIX}/health", self.health, ["GET"], "Inspect V2 SQLite database health and slimming report"),
                (f"{PREFIX}/compact", self.compact, ["POST"], "Run lossless V2 SQLite slimming and compaction")]

    def _operator(self):
        username = current_request_username(request)
        return require_controller_management_operator(self.controller, username)

    def _backup_context(self, selected_path):
        controller = self.controller
        runtime = getattr(controller, "runtime", None)
        installation_id = getattr(runtime, "installation_id", None)
        if type(installation_id) is not str:
            settings = getattr(controller, "settings", None)
            installation_id = getattr(settings, "installation_id", None)
        if type(installation_id) is not str:
            configuration = getattr(controller, "configuration", None)
            scope = getattr(configuration, "scope", None)
            installation_id = getattr(scope, "installation_id", None)
        plugin_root = Path(controller.data_root).resolve()
        if runtime is not None:
            runtime_root = Path(runtime.data_root).resolve()
        else:
            profile_root = getattr(controller, "profile_root", None)
            runtime_root = (Path(profile_root) / "runtime").resolve() if profile_root is not None else plugin_root
        backup_directory = selected_backup_directory(
            runtime_root, selected_path, installation_id, plugin_root=plugin_root)
        backup_root = selected_backup_root(plugin_root, selected_path)
        return runtime_root, installation_id, backup_directory, backup_root

    @staticmethod
    def _body(raw):
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate field")
                result[key] = value
            return result

        return json.loads(raw.decode("utf-8"), object_pairs_hook=unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")))

    def _status(self):
        controller = self.controller
        configuration = controller.config if isinstance(controller.config, dict) else {}
        policy = retention_policy(configuration)
        default_root = Path(getattr(controller, "default_data_root", controller.data_root)).resolve()
        storage = configuration.get("storage", {})
        selected = storage.get("path", "") if isinstance(storage, dict) else ""
        selected_backup = storage.get("backup_path", "") if isinstance(storage, dict) else ""
        desired = selected_root(default_root, selected)
        active = Path(controller.data_root).resolve()
        _, installation_id, backup_directory, backup_root = self._backup_context(selected_backup)
        active_inventory = inventory(active, backup_directory=backup_root or backup_directory)
        desired_inventory = active_inventory if desired == active else inventory(desired)
        plan = cleanup_plan(active, **policy, backup_directory=backup_directory,
                            installation_id=installation_id)
        return {"active_root": str(active), "selected_path": selected,
                "desired_root": str(desired), "restart_required": desired != active,
                "selected_backup_path": selected_backup, "backup_root": str(backup_directory),
                "inventory": active_inventory,
                "destination_free_bytes": desired_inventory["free_bytes"],
                "policy": policy, "auto_backup": auto_backup_policy(configuration), "cleanup_preview": plan}

    # -- worker-thread helpers ------------------------------------------------
    #
    # These are deliberately plain methods: ``asyncio.to_thread`` runs them off the
    # event loop, and ``backup_operation_lock`` (a threading.Lock) excludes the
    # backup/restore worker for the duration of the filesystem mutation.

    def _clean(self, runtime_root, installation_id, backup_directory, policy):
        with backup_operation_lock(self.controller):
            result = cleanup_backups(runtime_root, **policy,
                                     backup_directory=backup_directory,
                                     installation_id=installation_id)
        # The inventory that follows is read-only: never keep a backup/restore
        # waiting for it. The route's asyncio lock still serializes the request.
        return result, self._status()

    def _relocate(self, plugin_root, previous_backup, selected_backup):
        with backup_operation_lock(self.controller):
            return relocate_backup_storage(plugin_root, previous_backup, selected_backup)

    def _finish_relocation(self, transfers):
        with backup_operation_lock(self.controller):
            return finish_backup_relocation(transfers)

    def _active_runtime(self):
        controller = self.controller
        runtime = getattr(controller, "runtime", None)
        if runtime is not None:
            return runtime
        session = getattr(controller, "session", None) or getattr(controller, "last_session", None)
        if session is not None:
            application = getattr(session, "application", None)
            runtime = getattr(application, "runtime", None)
            if runtime is not None:
                return runtime
        raise TaskError("staging_not_initialized", "load the V2 profile first")

    @staticmethod
    def _parse_compact_options(document: dict) -> dict:
        allowed = {
            "action", "ttl_days", "vacuum", "enable_incremental_vacuum",
            "archive_telemetry", "dedup_native", "offload_media",
        }
        if not set(document) <= allowed:
            raise ValueError("unexpected fields")
        options: dict = {}
        if "ttl_days" in document:
            ttl = document["ttl_days"]
            if type(ttl) is not int or not 1 <= ttl <= 3650:
                raise ValueError("invalid ttl_days")
            options["ttl_days"] = ttl
        for key in ("vacuum", "enable_incremental_vacuum", "archive_telemetry", "dedup_native", "offload_media"):
            if key in document:
                if type(document[key]) is not bool:
                    raise ValueError(f"invalid {key}")
                options[key] = document[key]
        return options

    def _health(self, runtime):
        return database_health_report(runtime)

    def _compact(self, runtime, options: dict):
        with backup_operation_lock(self.controller):
            return compact_and_slim_databases(runtime, **options)

    async def health(self):
        from .web_api import outcome_response
        controller = self.controller
        try:
            async with storage_request_lock(controller):
                async with controller._control_lock:
                    self._operator()
                    if getattr(controller, "_terminated", False):
                        raise TaskError("staging_host_terminated", "plugin is stopped")
                    runtime = self._active_runtime()
                report = await asyncio.to_thread(self._health, runtime)
                return {"status": "ok", "data": report}
        except (TaskError, ValueError) as error:
            code = error.code if isinstance(error, TaskError) else "storage_request_invalid"
            return outcome_response(Outcome.failed(code, str(error)))
        except OSError:
            return outcome_response(Outcome.failed("storage_operation_failed",
                                                   "storage health could not be read"))

    async def compact(self):
        from .web_api import outcome_response
        controller = self.controller
        try:
            raw = await request.body()
            if len(raw) > MAX_REQUEST_BYTES or request.query:
                raise ValueError("request too large or query provided")
            document = self._body(raw) if raw and raw.strip() else {}
            if type(document) is not dict:
                raise ValueError("object required")
            options = self._parse_compact_options(document)
            async with storage_request_lock(controller):
                async with controller._control_lock:
                    self._operator()
                    if getattr(controller, "_terminated", False):
                        raise TaskError("staging_host_terminated", "plugin is stopped")
                    runtime = self._active_runtime()
                result = await asyncio.to_thread(self._compact, runtime, options)
                return {"status": "ok", "data": result}
        except TaskError as error:
            return outcome_response(Outcome.failed(error.code, str(error)))
        except (ValueError, UnicodeError, RecursionError, TypeError, KeyError):
            return outcome_response(Outcome.failed("storage_request_invalid", "compaction options are invalid"))
        except OSError:
            return outcome_response(Outcome.failed("storage_operation_failed",
                                                   "database compaction could not complete"))

    async def status(self):
        from .web_api import outcome_response
        controller = self.controller
        try:
            async with storage_request_lock(controller):
                # Identity/session validation is cheap and stays on the loop under
                # the control lock; the control lock is released before any disk
                # walk (it is shared with delivery authorization).
                async with controller._control_lock:
                    self._operator()
                data = await asyncio.to_thread(self._status)
                return {"status": "ok", "data": data}
        except (TaskError, ValueError) as error:
            code = error.code if isinstance(error, TaskError) else "storage_settings_invalid"
            return outcome_response(Outcome.failed(code, str(error)))
        except OSError:
            return outcome_response(Outcome.failed("storage_location_unavailable",
                                                   "the selected V2 data location is not available"))

    async def update(self):
        from .web_api import outcome_response
        controller = self.controller
        try:
            raw = await request.body()
            if len(raw) > MAX_REQUEST_BYTES or request.query:
                raise ValueError("request too large or query provided")
            document = self._body(raw)
            if type(document) is not dict:
                raise ValueError("object required")
            action = document.get("action")
            if action not in ("clean", "save", "cache", "compact"):
                raise ValueError("unexpected action")

            async with storage_request_lock(controller):
                if action == "compact":
                    options = self._parse_compact_options(document)
                    async with controller._control_lock:
                        self._operator()
                        if getattr(controller, "_terminated", False):
                            raise TaskError("staging_host_terminated", "plugin is stopped")
                        runtime = self._active_runtime()
                    result = await asyncio.to_thread(self._compact, runtime, options)
                    return {"status": "ok", "data": {"compact_result": result}}

                if action == "cache":
                    if set(document) != {"action"}:
                        raise ValueError("unexpected fields")
                    async with controller._control_lock:
                        self._operator()
                        if getattr(controller, "_terminated", False):
                            raise TaskError("staging_host_terminated", "plugin is stopped")
                        session = controller.session or controller.last_session
                        if session is None:
                            raise TaskError("staging_not_initialized", "load the V2 profile first")
                        application = session.application
                    # 缓存是进程内的小对象，直接在事件循环里清；丢到工作线程会和
                    # 正在写入的转述/诊断缓存并发修改同一个字典。
                    clear = getattr(application, "clear_caches", None)
                    if not callable(clear):
                        raise TaskError("storage_cache_unavailable", "runtime caches are unavailable")
                    cleared = clear()
                    return {"status": "ok", "data": {"cache_result": cleared}}

                if action == "clean":
                    if set(document) != {"action"}:
                        raise ValueError("unexpected fields")
                    async with controller._control_lock:
                        self._operator()
                        policy = retention_policy(controller.config)
                        storage = (controller.config.get("storage", {})
                                   if isinstance(controller.config, dict) else {})
                        selected_backup = storage.get("backup_path", "") if isinstance(storage, dict) else ""
                        runtime_root, installation_id, backup_directory, _ = self._backup_context(selected_backup)
                    result, data = await asyncio.to_thread(
                        self._clean, runtime_root, installation_id, backup_directory, policy)
                    return {"status": "ok", "data": {**data, "cleanup_result": result}}

                required = {"action", "path", "backup_path", "retention_count", "retention_days"}
                optional = set(auto_backup_policy({}))
                if not required <= set(document) <= required | optional:
                    raise ValueError("unexpected fields")
                schedule = {key: document[key] for key in optional if key in document}
                if ("auto_backup_enabled" in schedule and type(schedule["auto_backup_enabled"]) is not bool
                        or "auto_backup_interval_minutes" in schedule
                        and not valid_auto_backup_interval(schedule["auto_backup_interval_minutes"])):
                    raise ValueError("invalid automatic backup settings")
                selected = document["path"]
                selected_backup = document["backup_path"]
                count, days = document["retention_count"], document["retention_days"]
                if (type(selected) is not str or type(count) is not int or type(days) is not int
                        or type(selected_backup) is not str or len(selected_backup) > 2048
                        or not 1 <= count <= 200 or not 1 <= days <= 3650):
                    raise ValueError("invalid storage settings")
                default_root = Path(getattr(controller, "default_data_root", controller.data_root)).resolve()
                selected_root(default_root, selected)
                async with controller._control_lock:
                    self._operator()
                    save = getattr(controller, "save_plugin_config", None)
                    if not callable(save):
                        raise TaskError("storage_config_save_unavailable",
                                        "plugin configuration cannot be saved")
                    previous = deepcopy(dict(controller.config))
                    previous_storage = previous.get("storage", {})
                    previous_backup = (previous_storage.get("backup_path", "")
                                       if isinstance(previous_storage, dict) else "")
                    runtime_root, installation_id, _, _ = self._backup_context(previous_backup)

                # Copy every snapshot pair before the config points at the new
                # directory. This is the multi-second part; it must not run on the
                # event loop, and it may not cross a backup/restore operation.
                transfers = await asyncio.to_thread(
                    self._relocate, Path(controller.data_root), previous_backup, selected_backup.strip())

                async with controller._control_lock:
                    if getattr(controller, "_terminated", False):
                        raise TaskError("host_terminated", "plugin is stopped")
                    save = getattr(controller, "save_plugin_config", None)
                    if not callable(save):
                        raise TaskError("storage_config_save_unavailable",
                                        "plugin configuration cannot be saved")
                    if deepcopy(dict(controller.config)) != previous:
                        # Another control API changed storage settings while the
                        # copy ran. Never overwrite it with the stale candidate;
                        # the copied destination pairs are harmless duplicates.
                        raise TaskError(
                            "storage_config_changed",
                            "storage settings changed while the backup files were being copied; retry")
                    candidate = deepcopy(previous)
                    storage = candidate.get("storage")
                    if not isinstance(storage, dict):
                        storage = {}
                    candidate["storage"] = {**storage, "path": selected.strip(),
                                            "backup_path": selected_backup.strip(),
                                            "retention_count": count, "retention_days": days, **schedule}
                    try:
                        save(candidate)
                    except Exception as error:
                        raise TaskError("storage_config_save_failed",
                                        "storage settings could not be saved") from error
                    next_backup_directory = selected_backup_directory(
                        runtime_root, selected_backup, installation_id,
                        plugin_root=Path(controller.data_root))
                    if getattr(controller, "runtime", None) is not None:
                        controller.runtime.backup_directory = next_backup_directory

                if transfers:
                    try:
                        # Source pairs are removed only after the config points at
                        # verified destinations; a failure keeps the originals.
                        await asyncio.to_thread(self._finish_relocation, transfers)
                    except OSError:
                        logger.warning("[CompanionV2] 备份目录迁移收尾未完成，源快照保留在旧目录")

                data = await asyncio.to_thread(self._status)
                return {"status": "ok", "data": data}
        except TaskError as error:
            return outcome_response(Outcome.failed(error.code, str(error)))
        except (ValueError, UnicodeError, RecursionError, TypeError, KeyError):
            return outcome_response(Outcome.failed("storage_request_invalid", "storage settings are invalid"))
        except OSError:
            return outcome_response(Outcome.failed("storage_operation_failed",
                                                   "storage files could not be read or updated"))
