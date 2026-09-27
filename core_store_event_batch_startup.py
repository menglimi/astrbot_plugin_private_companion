# -*- coding: utf-8 -*-
"""CoreStoreEventBatchStartupMixin。

由 tools/split_mixin_domain.py 从 core_store.py 机械抽取（19 个方法 + 0 个模块级名字 + 0 个类级赋值 / 607 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 CoreStoreMixin）。
"""
from __future__ import annotations

from .core_store_shared import _EVENT_DATA_SAVE_BATCH, _EVENT_DATA_SAVE_BATCH_ATTR, logger
from .core_store_shared import Any
from .core_store_shared import Collection
from .core_store_shared import Mapping
from .core_store_shared import Path
from .core_store_shared import STORY_MIGRATION_COMMIT_KEY
from .core_store_shared import StoreManager
from .core_store_shared import StoryAuthorityError
from .core_store_shared import _ensure_config_parent_dir
from .core_store_shared import _single_line
from .core_store_shared import _today_key
from .core_store_shared import asyncio
from .core_store_shared import datetime
from .core_store_shared import deepcopy
from .core_store_shared import json
from .core_store_shared import os
from .core_store_shared import _core_store_host
from .core_store_shared import shutil
from .core_store_shared import sqlite3
from .core_store_shared import story_startup_sync_operation
from .core_store_shared import uuid



class CoreStoreEventBatchStartupMixin:
    """CoreStoreEventBatchStartupMixin（从 CoreStoreMixin 拆出）。"""


    def _begin_event_data_save_batch(self, event: Any) -> tuple[Any, dict[str, Any]] | None:
        """Begin or resume one persistence batch for a managed message event."""
        current = _EVENT_DATA_SAVE_BATCH.get()
        if (
            isinstance(current, dict)
            and current.get("owner") is self
            and not bool(current.get("closed"))
        ):
            return None

        # Message filters run in separate persona contexts. Keep the batch on
        # the event so an early guard and the final handler can share it.
        event_batch = getattr(event, _EVENT_DATA_SAVE_BATCH_ATTR, None)
        if (
            isinstance(event_batch, dict)
            and event_batch.get("owner") is self
            and not bool(event_batch.get("closed"))
        ):
            token = _EVENT_DATA_SAVE_BATCH.set(event_batch)
            return token, event_batch

        sections: set[str] = set()
        deleted_sections: set[str] = set()
        batch = {
            "owner": self,
            "event": event,
            "sections": sections,
            "deleted_sections": deleted_sections,
            "delay": 1.5,
            "closed": False,
        }
        try:
            setattr(event, _EVENT_DATA_SAVE_BATCH_ATTR, batch)
            setattr(event, "_private_companion_pending_save_sections", sections)
        except Exception:
            pass
        token = _EVENT_DATA_SAVE_BATCH.set(batch)
        return token, batch

    def _suspend_event_data_save_batch(
        self,
        handle: tuple[Any, dict[str, Any]] | None,
    ) -> None:
        """Detach the current task from an open event batch without flushing it."""
        if handle is None:
            return
        token, batch = handle
        if bool(batch.get("closed")):
            return
        try:
            _EVENT_DATA_SAVE_BATCH.reset(token)
        except (LookupError, RuntimeError, ValueError):
            # A defensive fallback for handlers that changed the context
            # before returning; the event-owned batch remains authoritative.
            if _EVENT_DATA_SAVE_BATCH.get() is batch:
                _EVENT_DATA_SAVE_BATCH.set(None)

    def _collect_event_data_save_request(
        self,
        *,
        sections: set[str] | None,
        deleted_sections: set[str],
        full_scope: str | None,
        delay: float,
    ) -> bool:
        """Merge an incremental request into the active message-event batch."""
        batch = _EVENT_DATA_SAVE_BATCH.get()
        if (
            not isinstance(batch, dict)
            or batch.get("owner") is not self
            or bool(batch.get("closed"))
            or full_scope is not None
            or sections is None
        ):
            return False
        changed = batch["sections"]
        deleted = batch["deleted_sections"]
        for section in sections:
            deleted.discard(section)
            changed.add(section)
        for section in deleted_sections:
            changed.discard(section)
            deleted.add(section)
        batch["delay"] = min(float(batch.get("delay", 1.5)), max(0.0, float(delay)))
        return True

    def _finish_event_data_save_batch(
        self,
        handle: tuple[Any, dict[str, Any]] | None,
    ) -> None:
        """Close an event batch and submit its final section union once."""
        if handle is None:
            return
        token, batch = handle
        if bool(batch.get("closed")):
            return
        # Child tasks inherit ContextVar values. Mark this shared batch closed
        # before resetting the parent context so later child writes cannot be
        # absorbed into a request that has already been submitted.
        batch["closed"] = True
        if _EVENT_DATA_SAVE_BATCH.get() is batch:
            try:
                _EVENT_DATA_SAVE_BATCH.reset(token)
            except (LookupError, RuntimeError, ValueError):
                _EVENT_DATA_SAVE_BATCH.set(None)
            if _EVENT_DATA_SAVE_BATCH.get() is batch:
                _EVENT_DATA_SAVE_BATCH.set(None)
        else:
            try:
                _EVENT_DATA_SAVE_BATCH.reset(token)
            except (LookupError, RuntimeError, ValueError):
                pass
        self._close_event_data_save_batch(batch)

    def _close_event_data_save_batch(self, batch: dict[str, Any]) -> None:
        """Remove event markers and submit the captured section union."""
        event = batch.get("event")
        try:
            if getattr(event, _EVENT_DATA_SAVE_BATCH_ATTR, None) is batch:
                delattr(event, _EVENT_DATA_SAVE_BATCH_ATTR)
            delattr(event, "_private_companion_pending_save_sections")
        except Exception:
            pass
        sections = set(batch.get("sections") or ())
        deleted_sections = set(batch.get("deleted_sections") or ())
        if not sections and not deleted_sections:
            return
        self._schedule_data_save(
            sections=sections,
            deleted_sections=deleted_sections,
            delay=float(batch.get("delay", 1.5)),
        )

    @staticmethod
    def _backup_store_switch_target(backend: str, path: Path) -> Path | None:
        if not path.exists():
            return None
        suffix = f".before-switch-{datetime.now().strftime('%Y%m%dT%H%M%S%f')}-{uuid.uuid4().hex[:12]}.bak"
        backup_path = path.with_name(path.name + suffix)
        if backend == "sqlite":
            source = sqlite3.connect(str(path), timeout=15.0)
            destination = sqlite3.connect(str(backup_path), timeout=15.0)
            try:
                source.backup(destination)
                destination.commit()
            finally:
                destination.close()
                source.close()
        else:
            shutil.copy2(path, backup_path)
        return backup_path

    @staticmethod
    def _restore_store_switch_target(
        backend: str,
        path: Path,
        backup_path: Path | None,
    ) -> None:
        for sidecar in (Path(str(path) + "-wal"), Path(str(path) + "-shm")):
            sidecar.unlink(missing_ok=True)
        if backup_path is None:
            path.unlink(missing_ok=True)
            return
        if backend == "sqlite":
            source = sqlite3.connect(str(backup_path), timeout=15.0)
            destination = sqlite3.connect(str(path), timeout=15.0)
            try:
                source.backup(destination)
                destination.commit()
            finally:
                destination.close()
                source.close()
        else:
            shutil.copy2(backup_path, path)

    def _storage_backend_state_path(self) -> Path:
        data_dir = getattr(self, "data_dir", "")
        if not data_dir:
            manager = getattr(self, "store_manager", None)
            data_file = getattr(manager, "data_file", "")
            data_dir = Path(data_file).parent if data_file else Path(getattr(self, "data_file", ".")).parent
        return Path(data_dir) / ".storage-backend-state.json"

    def _read_storage_backend_state(self) -> dict[str, str] | None:
        try:
            with self._storage_backend_state_path().open("r", encoding="utf-8") as stream:
                state = json.load(stream)
        except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError):
            return None
        if not isinstance(state, dict):
            return None
        backend = str(state.get("backend") or "").strip().lower()
        sqlite_path = str(state.get("sqlite_path") or "").strip()
        if backend not in {"json", "sqlite"}:
            return None
        return {"backend": backend, "sqlite_path": sqlite_path}

    def _write_storage_backend_state(self, backend: str, sqlite_path: str) -> None:
        path = self._storage_backend_state_path()
        temporary = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with temporary.open("w", encoding="utf-8") as stream:
                json.dump(
                    {"backend": backend, "sqlite_path": sqlite_path},
                    stream,
                    ensure_ascii=False,
                )
                stream.flush()
                try:
                    os.fsync(stream.fileno())
                except OSError:
                    pass
            os.replace(temporary, path)
        except OSError as exc:
            logger.warning(
                "后端状态标记写入失败，不影响当前存储: %s",
                _single_line(exc, 160),
            )
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def _assert_primary_store_startup_safe(self, manager: Any) -> None:
        """Reject ambiguous empty startup while preserving true first install."""

        json_backend = getattr(manager, "json_backend", None)
        sqlite_backend = getattr(manager, "sqlite_backend", None)
        json_exists = getattr(json_backend, "exists", None)
        sqlite_exists = getattr(sqlite_backend, "exists", None)
        if not callable(json_exists) or not callable(sqlite_exists):
            # Lightweight test/downgrade managers are validated by their own
            # loader.  Production StoreManager always exposes both backends.
            return
        if bool(json_exists()) or bool(sqlite_exists()):
            return

        data_dir = Path(getattr(self, "data_dir", "") or self._storage_backend_state_path().parent)
        evidence: list[str] = []
        marker = self._storage_backend_state_path()
        if marker.exists() or marker.is_symlink():
            evidence.append(marker.name)
        try:
            for entry in data_dir.iterdir():
                if entry == marker:
                    continue
                name = entry.name
                if not name or name.startswith(".nfs"):
                    continue
                evidence.append(name)
                if len(evidence) >= 8:
                    break
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise RuntimeError(
                "无法确认陪伴数据目录是否为首次安装；为避免空状态覆盖，已中止启动"
            ) from exc
        if evidence:
            raise RuntimeError(
                "陪伴主状态 JSON 与 SQLite 同时缺失，但检测到既有安装证据 "
                f"({', '.join(sorted(set(evidence)))}); 请恢复数据文件或显式清空数据目录后重试"
            )

    def _legacy_json_is_newer_than_sqlite(self, sqlite_path: Path) -> bool:
        """Detect a pre-marker JSON->SQLite switch without trusting a stale mirror."""
        try:
            json_mtime = Path(self.data_file).stat().st_mtime_ns
            sqlite_mtime = sqlite_path.stat().st_mtime_ns
        except OSError:
            return False
        return json_mtime - sqlite_mtime > 1_000_000_000

    def _preflight_story_handoff_store_managers(
        self,
        *managers: Any,
        extra_sections: Collection[Mapping[str, Any]] = (),
    ) -> None:
        """Validate every plausible startup source before any store write."""

        candidates: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        try:
            for manager in managers:
                if manager is None:
                    continue
                backend = str(getattr(manager, "backend_name", "") or "").lower()
                path = str(
                    getattr(
                        manager,
                        "sqlite_path" if backend == "sqlite" else "data_file",
                        "",
                    )
                    or ""
                )
                identity = (backend, path)
                if identity not in seen:
                    candidates.append(
                        manager.load_sections(
                            (STORY_MIGRATION_COMMIT_KEY,),
                            read_only=True,
                        )
                    )
                    seen.add(identity)
                # An uninitialized SQLite target imports the JSON source during
                # load_initial_store. Validate that source before migration writes.
                if backend == "sqlite":
                    json_path = str(getattr(manager, "data_file", "") or "")
                    json_identity = ("json", json_path)
                    if json_identity not in seen:
                        candidates.append(
                            manager.load_sections(
                                (STORY_MIGRATION_COMMIT_KEY,),
                                backend_name="json",
                                read_only=True,
                            )
                        )
                        seen.add(json_identity)
            candidates.extend(dict(value) for value in extra_sections)
            _core_store_host.preflight_story_handoff_sections(*candidates)
        except StoryAuthorityError:
            raise
        except Exception:
            controller = _core_store_host.story_authority_controller()
            controller.block("story_handoff_marker_preflight_unavailable")
            raise StoryAuthorityError(
                "story_handoff_marker_preflight_unavailable"
            ) from None

    @staticmethod
    def _restore_committed_story_roots(
        data: dict[str, Any],
        baseline: dict[str, tuple[bool, Any]],
    ) -> None:
        for section, (present, value) in baseline.items():
            if present:
                data[section] = value
            else:
                data.pop(section, None)

    @story_startup_sync_operation("store.manager.rebuild")
    def _rebuild_store_manager(self, *, reload_data: bool = False) -> None:
        backend = str(getattr(self, "storage_backend", "json") or "json").strip().lower() or "json"
        if backend not in {"json", "sqlite"}:
            backend = "json"
        default_sqlite_path = os.path.join(self.data_dir, "companions.db")
        configured_sqlite_path = str(getattr(self, "storage_sqlite_path", "") or "").strip()
        sqlite_path = configured_sqlite_path or default_sqlite_path
        if backend == "sqlite" and configured_sqlite_path:
            configured_path = Path(configured_sqlite_path)
            invalid_reason = ""
            try:
                if configured_path.exists() and configured_path.is_dir():
                    invalid_reason = "配置路径是目录，不是 SQLite 数据文件"
                elif configured_path.parent.exists() and not configured_path.parent.is_dir():
                    invalid_reason = "配置路径的父级不是目录"
                elif not configured_path.parent.exists():
                    configured_path.parent.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                invalid_reason = f"配置路径父级不可创建: {_single_line(exc, 160)}"
            if invalid_reason:
                sqlite_path = default_sqlite_path
                logger.warning(
                    "SQLite 数据文件路径无效，已回退默认路径: configured=%s reason=%s fallback=%s",
                    configured_sqlite_path,
                    invalid_reason,
                    default_sqlite_path,
                )
        previous_effective_path = str(
            getattr(self, "storage_sqlite_effective_path", "") or ""
        )
        previous_manager = getattr(self, "store_manager", None)
        previous_data = deepcopy(getattr(self, "data", {})) if reload_data else None
        previous_manager_backend = str(
            getattr(previous_manager, "backend_name", "") or ""
        ).lower()
        previous_manager_sqlite_path = str(
            getattr(previous_manager, "sqlite_path", "") or ""
        )
        previous_configured_backend = str(
            getattr(
                self,
                "_storage_backend_applied",
                previous_manager_backend or "json",
            )
        )
        if hasattr(self, "_storage_sqlite_path_applied"):
            previous_configured_sqlite_path = str(
                getattr(self, "_storage_sqlite_path_applied", "") or ""
            )
        else:
            previous_configured_sqlite_path = (
                ""
                if previous_manager_sqlite_path
                and Path(previous_manager_sqlite_path).resolve()
                == Path(default_sqlite_path).resolve()
                else previous_manager_sqlite_path
            )
        migration_source_backend = ""
        migration_source_sqlite_path = ""
        if not reload_data:
            state = self._read_storage_backend_state()
            if state is not None:
                state_backend = state["backend"]
                state_sqlite_path = state["sqlite_path"]
                switched_path = (
                    backend == "sqlite"
                    and state_backend == "sqlite"
                    and state_sqlite_path
                    and Path(state_sqlite_path).resolve() != Path(sqlite_path).resolve()
                )
                if state_backend != backend or switched_path:
                    migration_source_backend = state_backend
                    migration_source_sqlite_path = state_sqlite_path
            elif backend == "sqlite" and self._legacy_json_is_newer_than_sqlite(Path(sqlite_path)):
                migration_source_backend = "json"
        same_store = bool(
            reload_data
            and previous_manager is not None
            and previous_manager_backend == backend
            and (
                backend != "sqlite"
                or Path(previous_manager_sqlite_path).resolve()
                == Path(sqlite_path).resolve()
            )
        )
        target_path = Path(sqlite_path if backend == "sqlite" else self.data_file)
        target_backup: Path | None = None
        target_write_started = False
        try:
            next_manager = StoreManager(
                backend_name=backend,
                data_file=self.data_file,
                sqlite_path=sqlite_path,
                ensure_defaults=self._ensure_store_defaults,
                new_store=self._new_store,
                persistence_owner_token=str(
                    getattr(self, "_persistence_owner_token", "") or ""
                ),
            )
            source_manager = None
            if not reload_data and migration_source_backend:
                source_sqlite_path = migration_source_sqlite_path or default_sqlite_path
                source_manager = StoreManager(
                    backend_name=migration_source_backend,
                    data_file=self.data_file,
                    sqlite_path=source_sqlite_path,
                    ensure_defaults=self._ensure_store_defaults,
                    new_store=self._new_store,
                    persistence_owner_token=str(
                        getattr(self, "_persistence_owner_token", "") or ""
                    ),
                )
            self._preflight_story_handoff_store_managers(
                next_manager,
                source_manager,
                extra_sections=(previous_data,) if isinstance(previous_data, dict) else (),
            )
            if source_manager is not None:
                source_backend = (
                    source_manager.sqlite_backend
                    if migration_source_backend == "sqlite"
                    else source_manager.json_backend
                )
                if source_backend.exists():
                    source_data = source_backend.load_store()
                    target_backup = self._backup_store_switch_target(backend, target_path)
                    target_write_started = True
                    with self._data_save_io_lock():
                        with next_manager._store_lock:
                            next_manager.backend.save_store(deepcopy(source_data))
                        self._advance_data_save_write_generation()
                    logger.info(
                        "已从 %s 后端迁移到 %s 后端",
                        migration_source_backend,
                        backend,
                    )
            elif reload_data and not same_store and isinstance(previous_data, dict):
                # A backend change must carry the current authority forward before
                # reading the target, otherwise switching away from SQLite falls
                # back to an obsolete JSON mirror.
                target_backup = self._backup_store_switch_target(backend, target_path)
                target_write_started = True
                with self._data_save_io_lock():
                    with next_manager._store_lock:
                        next_manager.backend.save_store(deepcopy(previous_data))
                    self._advance_data_save_write_generation()
                loaded = next_manager.backend.load_store()
                if not isinstance(loaded, dict):
                    raise RuntimeError("Storage switch target returned a non-object store")
            elif reload_data:
                loaded = next_manager.load_initial_store()
            else:
                loaded = None
        except Exception:
            if target_write_started:
                try:
                    self._restore_store_switch_target(
                        backend,
                        target_path,
                        target_backup,
                    )
                except Exception as restore_exc:
                    logger.error(
                        "Failed to restore storage switch target: %s",
                        _single_line(restore_exc, 200),
                    )
            if reload_data and previous_manager is not None:
                self.storage_backend = previous_configured_backend
                self.storage_sqlite_path = previous_configured_sqlite_path
                self.storage_sqlite_effective_path = previous_effective_path
                self.store_manager = previous_manager
                if isinstance(previous_data, dict):
                    self.data = previous_data
            raise

        self.storage_backend = backend
        self.storage_sqlite_effective_path = sqlite_path
        self._storage_backend_applied = backend
        self._storage_sqlite_path_applied = configured_sqlite_path
        self.store_manager = next_manager
        if reload_data and isinstance(loaded, dict):
            self.data = loaded
            self._refresh_data_save_revision_from_manager()
            self._write_storage_backend_state(backend, sqlite_path)

    async def _save_config_if_possible(self) -> bool:
        for method_name in ("save_config", "save", "save_conf"):
            save = getattr(self.config, method_name, None)
            if not callable(save):
                continue
            try:
                _ensure_config_parent_dir(self.config, logger=logger)
                result = save()
                if asyncio.iscoroutine(result) or hasattr(result, "__await__"):
                    await result
                return True
            except TypeError:
                continue
            except FileNotFoundError as exc:
                if _ensure_config_parent_dir(self.config, error=exc, logger=logger):
                    try:
                        result = save()
                        if asyncio.iscoroutine(result) or hasattr(result, "__await__"):
                            await result
                        return True
                    except Exception as retry_exc:
                        logger.warning("自动保存配置重试失败: %s", _single_line(retry_exc, 120))
                        return False
                logger.warning("自动保存配置失败: %s", _single_line(exc, 120))
                return False
            except Exception as exc:
                logger.warning("自动保存配置失败: %s", _single_line(exc, 120))
                return False
        logger.warning("当前配置对象没有可用保存方法，本次修改未落盘")
        return False

    def _set_runtime_bool_config(self, key: str, value: bool) -> None:
        setattr(self, key, bool(value))
        try:
            self.config[key] = bool(value)
        except Exception:
            setter = getattr(self.config, "set", None)
            if callable(setter):
                try:
                    setter(key, bool(value))
                except Exception:
                    pass

    async def _startup_prepare_today(self):
        try:
            if bool(getattr(self, "enable_multi_persona_mode", False)):
                persona_ids = getattr(self, "_configured_multi_persona_ids", lambda: [])()
                if not persona_ids:
                    primary_getter = getattr(self, "_primary_persona_id", None)
                    try:
                        primary = primary_getter() if callable(primary_getter) else ""
                    except Exception:
                        primary = ""
                    primary = primary or getattr(self, "plugin_specific_persona_id", "")
                    persona_ids = [primary]
                for persona_id in persona_ids:
                    token = getattr(self, "_activate_persona_id", lambda _pid: None)(persona_id)
                    if token is None:
                        logger.info(
                            "跳过尚未创建独立配置的人格启动任务: persona=%s",
                            _single_line(persona_id, 96),
                        )
                        continue
                    try:
                        try:
                            await self._ensure_daily_state()
                            await self._ensure_daily_plan()
                            await self._ensure_daily_diary()
                            await self._maybe_settle_skill_growth()
                        except Exception as exc:
                            logger.warning(
                                "启动初始化人格失败，继续处理其他人格: persona=%s error=%s",
                                _single_line(persona_id, 96),
                                _single_line(exc, 160),
                            )
                    finally:
                        if token is not None:
                            getattr(self, "_deactivate_persona_for_event", lambda _token: None)(token)
                if persona_ids and any(str(item or "").strip() for item in persona_ids):
                    return
            await self._ensure_daily_state()
            await self._ensure_daily_plan()
            # 启动只做一次普通维护检查；是否到达配置的日记时间由统一入口判断。
            # 手动“刷新日记”仍可显式传 force=True，不应让重启变成隐式强制生成。
            await self._ensure_daily_diary()
            await self._maybe_settle_skill_growth()
        except Exception as e:
            logger.warning(f"启动时生成今日日志失败: {e}", exc_info=True)

    def _has_today_diary(self) -> bool:
        diaries = self.data.get("bot_diaries", [])
        if not isinstance(diaries, list):
            return False
        return any(
            isinstance(diary, dict) and diary.get("date") == _today_key()
            for diary in diaries
        )
