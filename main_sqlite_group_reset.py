# -*- coding: utf-8 -*-
"""sqlite_group_reset。

由 tools/split_main_domain.py 从 main.py 机械抽取（5 个方法 / 242 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPlugin）。
"""
from __future__ import annotations

import asyncio
import sqlite3
import uuid
from .helpers import _now_ts, _single_line
from .migration_scoped_projection import scoped_persona_ref
from copy import deepcopy
from pathlib import Path
from typing import Any

from .logging_util import get_module_logger

logger = get_module_logger(__name__)

class PrivateCompanionPluginSqliteGroupResetMixin:
    """sqlite_group_reset（从 PrivateCompanionPlugin 拆出）。"""

    def _companion_owned_sqlite_path(self, db_path: Any) -> Path | None:
        """Resolve an existing SQLite file only when Companion owns its path."""
        data_dir = str(getattr(self, "data_dir", "") or "").strip()
        if not data_dir or db_path in (None, ""):
            return None
        try:
            data_root = Path(data_dir).resolve(strict=True)
            resolved = Path(str(db_path)).resolve(strict=True)
            resolved.relative_to(data_root)
        except (OSError, RuntimeError, ValueError):
            return None
        if not data_root.is_dir() or not resolved.is_file():
            return None
        return resolved

    def _sqlite_wal_candidate_paths(self) -> list[Path]:
        # AstrBot 4.27.x configures its own database and knowledge-base engines.
        # Only explicitly registered files below this plugin's data directory
        # may be tuned; shared AstrBot or sibling-plugin databases stay untouched.
        candidates: list[Any] = []
        effective_store_text = str(
            getattr(self, "storage_sqlite_effective_path", "") or ""
        ).strip()
        if effective_store_text:
            candidates.append(effective_store_text)
        if str(getattr(self, "storage_backend", "json") or "json").lower() == "sqlite":
            store_manager = getattr(self, "store_manager", None)
            candidates.append(
                getattr(getattr(store_manager, "backend", None), "db_path", None)
            )
        profiles_dir_text = str(getattr(self, "_persona_profiles_dir", "") or "").strip()
        profiles_dir = Path(profiles_dir_text) if profiles_dir_text else None
        if profiles_dir is not None and profiles_dir.is_dir():
            candidates.extend(sorted(profiles_dir.glob("*.db")))
        for owner_name in (
            "req041_migration_coordinator",
            "req041_migration_outbox",
            "req041_relationship_store",
        ):
            candidates.append(getattr(getattr(self, owner_name, None), "path", None))

        seen: set[str] = set()
        paths: list[Path] = []
        for candidate in candidates:
            path = self._companion_owned_sqlite_path(candidate)
            if path is None:
                continue
            canonical = str(path)
            if canonical in seen:
                continue
            seen.add(canonical)
            paths.append(path)
        return paths

    def _apply_sqlite_wal_to_file(self, db_path: Path) -> str:
        owned_path = self._companion_owned_sqlite_path(db_path)
        if owned_path is None:
            raise ValueError("sqlite_path_not_companion_owned")
        conn = sqlite3.connect(str(owned_path), timeout=15.0)
        try:
            conn.execute("PRAGMA busy_timeout=15000")
            mode_row = conn.execute("PRAGMA journal_mode=WAL").fetchone()
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA wal_autocheckpoint=1000")
            conn.execute("PRAGMA temp_store=MEMORY")
            conn.commit()
            return str(mode_row[0] if mode_row else "")
        finally:
            conn.close()

    async def _apply_sqlite_wal_optimizations(self) -> None:
        applied: list[str] = []
        failed: list[str] = []
        for path in self._sqlite_wal_candidate_paths():
            try:
                mode = await asyncio.to_thread(self._apply_sqlite_wal_to_file, path)
                applied.append(f"{path.name}:{mode or 'unknown'}")
            except Exception as exc:
                failed.append(f"{path.name}:{_single_line(exc, 80)}")
        if applied:
            logger.info(
                "插件自有 SQLite WAL 优化已应用: files=%s",
                "，".join(applied),
            )
        if failed:
            logger.warning("SQLite WAL 并发优化部分失败: %s", "；".join(failed))

    async def reset_group_scoped_data(
        self,
        group_id: str,
        *,
        operation_id: str = "",
    ) -> dict[str, Any]:
        """Durably reset a group remotely before removing its legacy/config sources."""
        normalize = getattr(self, "_normalize_group_identity_id", None)
        clean_group = _single_line(normalize(group_id) if callable(normalize) else group_id, 160)
        if not clean_group:
            return {"ok": False, "state": "invalid", "code": "scoped_group_erase_context_invalid"}
        synchronizer = getattr(self, "req041_scoped_projection_sync", None)
        if synchronizer is None:
            # Startup migration runs in the background. A panel action can arrive
            # before it binds the scoped eraser, so wait for that task once instead
            # of reporting an unavailable capability during the normal race window.
            startup_tasks = getattr(self, "_startup_background_tasks", {})
            migration_task = (
                startup_tasks.get("req041_automatic_migration")
                if isinstance(startup_tasks, dict) else None
            )
            if isinstance(migration_task, asyncio.Task) and not migration_task.done():
                try:
                    await asyncio.wait_for(asyncio.shield(migration_task), timeout=20.0)
                except asyncio.TimeoutError:
                    return {
                        "ok": False, "state": "degraded",
                        "code": "scoped_group_erase_initializing",
                    }
                except Exception:
                    pass
            synchronizer = getattr(self, "req041_scoped_projection_sync", None)
            status = getattr(self, "req041_migration_status", None)
            if synchronizer is None and isinstance(status, dict) and status.get("state") == "uninitialized":
                initializer = getattr(self, "_req041_initialize_automatic_migration", None)
                if callable(initializer):
                    try:
                        await asyncio.wait_for(initializer(), timeout=20.0)
                    except asyncio.TimeoutError:
                        return {
                            "ok": False, "state": "degraded",
                            "code": "scoped_group_erase_initializing",
                        }
                    except Exception:
                        pass
                    synchronizer = getattr(self, "req041_scoped_projection_sync", None)
        if synchronizer is None:
            status = getattr(self, "req041_migration_status", None)
            if self._req041_group_remote_cleanup_required():
                logger.warning(
                    "群聊删除因远端分域不可用而保留: group=%s state=%s code=%s memory_bound=%s scoped_required=%s",
                    clean_group,
                    _single_line((status or {}).get("state"), 32),
                    _single_line((status or {}).get("code"), 96),
                    bool((status or {}).get("memory_bound")),
                    bool((status or {}).get("scoped_required")),
                )
                return {"ok": False, "state": "degraded", "code": "scoped_group_erase_unavailable"}
            logger.info(
                "MemoryCompanion 从未绑定，群聊删除仅清理本地分域: group=%s",
                clean_group,
            )
            return {"ok": True, "state": "not_required", "code": "scoped_group_erase_not_required"}

        async with self._data_lock:
            sagas = self._req041_group_reset_sagas_locked()
            persona_reset = self.data.get("_req041_persona_reset_saga")
            if isinstance(persona_reset, dict) and persona_reset.get("state") == "confirmed":
                return {"ok": False, "state": "rejected", "code": "persona_reset_in_progress"}
            clean_operation = _single_line(operation_id, 120)
            saga = sagas.get(clean_operation) if clean_operation else None
            if saga is not None and not isinstance(saga, dict):
                return {"ok": False, "state": "rejected", "code": "group_reset_saga_invalid"}
            current_persona = scoped_persona_ref(self._active_persona_scope())
            if saga is None:
                matches = [
                    item for item in sagas.values()
                    if isinstance(item, dict)
                    and item.get("state") in {"confirmed", "config_pending"}
                    and _single_line(item.get("group_id"), 160) == clean_group
                    and _single_line(item.get("persona_id"), 80) == current_persona
                ]
                if len(matches) > 1:
                    return {"ok": False, "state": "rejected", "code": "group_reset_saga_conflict"}
                saga = matches[0] if matches else None
            if saga is None:
                clean_operation = clean_operation or "req041-group-reset-" + uuid.uuid4().hex
                saga = {
                    "operation_id": clean_operation,
                    "group_id": clean_group,
                    "persona_id": current_persona,
                    "state": "confirmed",
                    "created_at": _now_ts(),
                }
                sagas[clean_operation] = saga
                self._req041_persist_archive_saga_locked(
                    sections={"_req041_group_reset_sagas"},
                )
            else:
                clean_operation = _single_line(saga.get("operation_id"), 120)
                if (
                    not clean_operation or sagas.get(clean_operation) is not saga
                    or _single_line(saga.get("group_id"), 160) != clean_group
                    or saga.get("state") not in {"confirmed", "config_pending"}
                ):
                    return {"ok": False, "state": "rejected", "code": "group_reset_saga_invalid"}
            safe_persona = _single_line(saga.get("persona_id"), 80)

        remote = self._req041_erase_scoped_group_data(
            clean_group, operation_id=clean_operation, persona_id=safe_persona,
        )
        if not remote.get("ok"):
            return {
                "ok": False, "state": "confirmed",
                "code": str(remote.get("code") or "scoped_group_erase_failed")[:120],
                "operation_id": clean_operation,
            }

        async with self._data_lock:
            saga = self._req041_group_reset_sagas_locked().get(clean_operation)
            if not isinstance(saga, dict):
                return {"ok": False, "state": "rejected", "code": "group_reset_saga_missing"}
            local = self._req041_finalize_group_reset_locked(clean_group)
            saga["state"] = "config_pending"
            saga["remote_receipt"] = {
                "code": str(remote.get("code") or "")[:120],
                "count": int(remote.get("count") or 0),
                "namespace_count": int(remote.get("namespace_count") or 0),
            }
            saga["local_result"] = deepcopy(local)
            self._req041_persist_archive_saga_locked(
                sections={
                    "groups",
                    "expression_voice_profile",
                    "_req041_group_reset_sagas",
                },
            )

        config_saved = await self._save_config_if_possible()
        if not config_saved:
            return {
                "ok": False, "state": "config_pending", "code": "group_reset_config_save_failed",
                "operation_id": clean_operation, **local, "scoped_cleanup": remote,
            }
        async with self._data_lock:
            self._req041_group_reset_sagas_locked().pop(clean_operation, None)
            deleted_sections: set[str] = set()
            if not self.data.get("_req041_group_reset_sagas"):
                self.data.pop("_req041_group_reset_sagas", None)
                deleted_sections.add("_req041_group_reset_sagas")
            self._req041_persist_archive_saga_locked(
                sections={"_req041_group_reset_sagas"},
                deleted_sections=deleted_sections,
            )
        return {
            "ok": True, "state": "completed", "code": "group_reset_completed",
            "operation_id": clean_operation, "config_saved": True,
            **local, "scoped_cleanup": remote,
        }
