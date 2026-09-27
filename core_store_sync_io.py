# -*- coding: utf-8 -*-
"""CoreStoreSyncIoMixin。

由 tools/split_mixin_domain.py 从 core_store.py 机械抽取（14 个方法 + 0 个模块级名字 + 0 个类级赋值 / 534 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 CoreStoreMixin）。
"""
from __future__ import annotations

from .core_store_shared import _DURABLE_SECTION_NAMES, _FULL_SAVE_SCOPES, logger
from .core_store_shared import Any
from .core_store_shared import Collection
from .core_store_shared import Mapping
from .core_store_shared import Path
from .core_store_shared import STORY_MIGRATION_COMMIT_KEY
from .core_store_shared import _single_line
from .core_store_shared import asyncio
from .core_store_shared import capture_write_ticket
from .core_store_shared import deepcopy
from .core_store_shared import inspect
from .core_store_shared import json
from .core_store_shared import os
from .core_store_shared import _core_store_host
from .core_store_shared import replace_if_ticket_current
from .core_store_shared import threading
from .core_store_shared import uuid



class CoreStoreSyncIoMixin:
    """CoreStoreSyncIoMixin（从 CoreStoreMixin 拆出）。"""


    def _load_data_sync(self) -> dict[str, Any]:
        manager = getattr(self, "store_manager", None)
        if manager is not None:
            try:
                self._assert_primary_store_startup_safe(manager)
                self._preflight_story_handoff_store_managers(manager)
                data = manager.load_initial_store()
                _core_store_host.preflight_story_handoff_sections(data)
                manager_backend = str(
                    getattr(manager, "backend_name", "") or ""
                ).lower()
                persisted_bookshelf_tombstones: dict[str, int] = {}
                deleted_revisions = getattr(manager, "deleted_section_revisions", None)
                if manager_backend == "sqlite" and callable(deleted_revisions):
                    persisted_bookshelf_tombstones = dict(
                        deleted_revisions(
                            {
                                "bookshelf_items",
                                "bookshelf_secret",
                                "bookshelf_store_revision",
                                "reading_archive_integration",
                            }
                        )
                    )
                before_maintenance = deepcopy(data)
                story_baseline = {
                    section: (section in data, deepcopy(data.get(section)))
                    for section in ("creative_projects", "creative_memory_pool")
                }
                changed = self._sanitize_store_control_tags_inplace(data)
                repeat_changed = self._sanitize_proactive_candidate_repeat_counts_inplace(data)
                compacted = self._compact_store_history_inplace(data)
                bookshelf_recovered = self._recover_bookshelf_after_load(data)
                if _core_store_host.story_authority_controller().authority_state() == "committed":
                    self._restore_committed_story_roots(data, story_baseline)
                if changed:
                    logger.warning("启动读取数据时清理非标准控制标签: fields=%s", changed)
                if repeat_changed:
                    logger.warning("启动读取数据时压缩主动候选重复计数: items=%s", repeat_changed)
                if compacted:
                    logger.info("启动读取数据时压缩历史存储: %s", compacted)
                self._ensure_primary_store_ownership(data)
                self._persist_startup_maintenance_sync(
                    manager,
                    before_maintenance,
                    data,
                    persisted_bookshelf_tombstones,
                )
                self._write_storage_backend_state(
                    manager.backend_name,
                    str(getattr(manager, "sqlite_path", "") or ""),
                )
                return data
            except Exception as exc:
                logger.error(
                    "StoreManager 读取失败，为避免用空数据覆盖原存储，已中止加载: %s",
                    _single_line(exc, 200),
                )
                raise
        if not os.path.exists(self.data_file):
            data = self._new_store()
            self._ensure_primary_store_ownership(data)
            return data
        try:
            with open(self.data_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("数据文件根节点必须是 JSON 对象")
            data = self._ensure_store_defaults(data)
            _core_store_host.preflight_story_handoff_sections(data)
            story_baseline = {
                section: (section in data, deepcopy(data.get(section)))
                for section in ("creative_projects", "creative_memory_pool")
            }
            changed = self._sanitize_store_control_tags_inplace(data)
            repeat_changed = self._sanitize_proactive_candidate_repeat_counts_inplace(data)
            compacted = self._compact_store_history_inplace(data)
            self._recover_bookshelf_after_load(data)
            if _core_store_host.story_authority_controller().authority_state() == "committed":
                self._restore_committed_story_roots(data, story_baseline)
            if changed:
                logger.warning("启动读取 JSON 时清理非标准控制标签: fields=%s", changed)
            if repeat_changed:
                logger.warning("启动读取 JSON 时压缩主动候选重复计数: items=%s", repeat_changed)
            if compacted:
                logger.info("启动读取 JSON 时压缩历史存储: %s", compacted)
            ownership_changed = self._ensure_primary_store_ownership(data)
            if ownership_changed:
                self._write_data_snapshot_sync(deepcopy(data))
            return data
        except Exception as exc:
            logger.error(
                "读取已有 JSON 数据失败，为避免覆盖原文件，已中止加载: %s",
                _single_line(exc, 200),
            )
            raise

    def _read_story_migration_commit_persisted_sync(
        self,
    ) -> tuple[bool, Any]:
        """Read only the durable Story marker from the active primary store."""

        manager = getattr(self, "store_manager", None)
        if manager is not None:
            sections = manager.load_sections((STORY_MIGRATION_COMMIT_KEY,))
        else:
            path = Path(str(getattr(self, "data_file", "") or ""))
            if not path.is_file():
                return False, None
            with path.open("r", encoding="utf-8") as stream:
                root = json.load(stream)
            if type(root) is not dict:
                raise RuntimeError("Story marker store root is not an object")
            sections = root
        if STORY_MIGRATION_COMMIT_KEY not in sections:
            return False, None
        return True, deepcopy(sections[STORY_MIGRATION_COMMIT_KEY])

    def _save_story_migration_commit_confirmed_sync(
        self,
        marker: Mapping[str, Any],
    ) -> None:
        """Synchronously persist and read back the exact marker section."""

        expected = deepcopy(dict(marker))
        data = getattr(self, "_data_default", None)
        if type(data) is not dict:
            data = getattr(self, "data", None)
        if (
            type(data) is not dict
            or data.get(STORY_MIGRATION_COMMIT_KEY) != expected
        ):
            raise RuntimeError("Story marker memory baseline changed before save")
        tracked_maps = (
            "_data_save_dirty",
            "_data_save_deleted",
            "_data_save_dirty_since",
            "_data_save_section_revisions",
        )
        tracking = {
            name: (
                STORY_MIGRATION_COMMIT_KEY in value,
                deepcopy(value.get(STORY_MIGRATION_COMMIT_KEY)),
            )
            for name in tracked_maps
            if isinstance((value := getattr(self, name, None)), dict)
        }

        def clear_confirmed_marker_tracking() -> None:
            for name in ("_data_save_dirty", "_data_save_deleted", "_data_save_dirty_since"):
                value = getattr(self, name, None)
                if isinstance(value, dict):
                    value.pop(STORY_MIGRATION_COMMIT_KEY, None)

        def restore_marker_tracking() -> None:
            for name, (present, value) in tracking.items():
                current = getattr(self, name, None)
                if not isinstance(current, dict):
                    continue
                if present:
                    current[STORY_MIGRATION_COMMIT_KEY] = value
                else:
                    current.pop(STORY_MIGRATION_COMMIT_KEY, None)

        last_error: BaseException | None = None
        for _attempt in range(3):
            try:
                self._save_data_now_sync(sections={STORY_MIGRATION_COMMIT_KEY})
            except BaseException as exc:
                last_error = exc
            try:
                present, persisted = self._read_story_migration_commit_persisted_sync()
            except BaseException as exc:
                last_error = exc
                continue
            if present and persisted == expected:
                clear_confirmed_marker_tracking()
                return
        restore_marker_tracking()
        if last_error is not None:
            raise RuntimeError("Story marker durable save failed") from last_error
        raise RuntimeError("Story marker durable readback mismatch")

    def _save_data_sync(
        self,
        *,
        sections: Collection[str] | None = None,
        deleted_sections: Collection[str] = (),
        full_scope: str | None = None,
    ):
        sections, deleted_sections, full_scope = self._validate_save_request(
            sections, deleted_sections, full_scope
        )
        if self._collect_event_data_save_request(
            sections=sections,
            deleted_sections=deleted_sections,
            full_scope=full_scope,
            delay=0.35,
        ):
            return
        scoped_scheduler = getattr(self, "_req041_schedule_scoped_sync", None)
        if callable(scoped_scheduler):
            scoped_scheduler()
        group_observation_save = bool(getattr(self, "_group_observation_dirty", False))
        if group_observation_save:
            self._group_observation_dirty = False
        active_persona = str(getattr(self, "_active_persona_scope", lambda: "")() or "")
        primary_getter = getattr(self, "_primary_persona_id", None)
        primary = str(primary_getter() if callable(primary_getter) else "").strip()
        if (
            bool(getattr(self, "enable_multi_persona_mode", False))
            and active_persona
            and active_persona != primary
        ):
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                self._ensure_persona_save_state()
                pending_dirty = dict(
                    self._persona_data_save_dirty.get(active_persona, {})
                )
                pending_deleted = dict(
                    self._persona_data_save_deleted.get(active_persona, {})
                )
                pending_full = bool(
                    self._persona_data_save_full_revision.get(active_persona, 0)
                )
                pending_full_scope = str(
                    self._persona_data_save_full_scope.get(active_persona, "") or ""
                )
                if self._mark_persona_data_dirty(
                    active_persona,
                    sections=sections,
                    deleted_sections=deleted_sections,
                    full_scope=full_scope,
                ):
                    batch = self._capture_persona_data_save_batch(active_persona)
                    result = self._write_persona_data_save_batch_sync(
                        active_persona,
                        batch,
                        advance_generation=True,
                    )
                    self._finish_persona_data_save_batch(
                        active_persona,
                        batch,
                        result,
                    )
                    if not result.get("superseded") and (
                        pending_dirty or pending_deleted or pending_full
                    ):
                        self._mark_persona_data_dirty(
                            active_persona,
                            sections=None if pending_full else set(pending_dirty),
                            deleted_sections=set(pending_deleted),
                            full_scope=pending_full_scope if pending_full else None,
                        )
                return
            self._schedule_data_save(
                sections=sections,
                deleted_sections=deleted_sections,
                full_scope=full_scope,
                delay=15.0 if group_observation_save else 0.35,
            )
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            self._save_data_now_sync(
                sections=sections,
                deleted_sections=deleted_sections,
                full_scope=full_scope,
            )
            return
        self._schedule_data_save(
            sections=sections,
            deleted_sections=deleted_sections,
            full_scope=full_scope,
            delay=0.35,
        )

    def _save_data_now_sync(
        self,
        *,
        sections: Collection[str] | None = None,
        deleted_sections: Collection[str] = (),
        full_scope: str | None = None,
    ) -> None:
        sections, deleted_sections, full_scope = self._validate_save_request(
            sections, deleted_sections, full_scope
        )
        self._ensure_default_save_state()
        pending_dirty = dict(self._data_save_dirty)
        pending_deleted = dict(self._data_save_deleted)
        pending_full = bool(self._data_save_full_revision)
        pending_full_scope = str(getattr(self, "_data_save_full_scope", "") or "")
        if not self._mark_default_data_dirty(
            sections=sections,
            deleted_sections=deleted_sections,
            full_scope=full_scope,
        ):
            return
        batch = self._capture_default_data_save_batch()
        result = self._write_default_data_save_batch_sync(
            batch,
            advance_generation=True,
        )
        self._finish_default_data_save_batch(batch, result)
        if not result.get("superseded") and (
            pending_dirty or pending_deleted or pending_full
        ):
            self._mark_default_data_dirty(
                sections=None if pending_full else set(pending_dirty),
                deleted_sections=set(pending_deleted),
                full_scope=pending_full_scope if pending_full else None,
            )

    def _write_data_snapshot_sync(
        self,
        data: dict[str, Any],
        *,
        advance_generation: bool = True,
    ) -> int:
        manager = getattr(self, "store_manager", None)
        if manager is None and not data.get("worldbook_entries") and os.path.exists(self.data_file):
            try:
                with open(self.data_file, "r", encoding="utf-8") as f:
                    existing = json.load(f)
                if isinstance(existing, dict) and existing.get("worldbook_entries"):
                    for key in (
                        "worldbook_entries",
                        "worldbook_member_profiles",
                        "worldbook_group_profiles",
                        "worldbook_import_state",
                    ):
                        data[key] = existing.get(key, data.get(key))
            except Exception:
                pass
        changed = self._sanitize_store_control_tags_inplace(data)
        self._sanitize_proactive_candidate_repeat_counts_inplace(data)
        self._compact_store_history_inplace(data)
        if manager is not None:
            with self._data_save_io_lock():
                if getattr(self, "storage_backend", "json") == "sqlite":
                    manager.save_snapshot(data)
                    try:
                        mirror = deepcopy(data)
                        self._strip_ephemeral_group_transcripts_inplace(mirror)
                        manager.export_current_to_json(mirror)
                    except Exception as exc:
                        logger.debug(
                            "SQLite 快照镜像 JSON 写出失败: %s",
                            _single_line(exc, 160),
                        )
                else:
                    # The primary JSON is a restart-compatible projection. Keep
                    # the full in-memory window for the live process, but write
                    # only its bounded tail. Secondary persona SQLite stores
                    # never pass through this primary projection path.
                    mirror = deepcopy(data)
                    self._strip_ephemeral_group_transcripts_inplace(mirror)
                    manager.save_snapshot(mirror)
                status_getter = getattr(manager, "persistence_status", None)
                status = (
                    {"accepted": True, "state": "saved", "backend": "sqlite"}
                    if getattr(self, "storage_backend", "json") == "sqlite"
                    else status_getter() if callable(status_getter) else {}
                )
                self._last_persistence_write_status = dict(status or {})
                accepted = status.get("accepted") is not False
                if accepted:
                    self._refresh_data_save_revision_from_manager()
                    if advance_generation:
                        self._advance_data_save_write_generation()
            return changed
        with self._data_save_io_lock():
            mirror = deepcopy(data)
            self._strip_ephemeral_group_transcripts_inplace(mirror)
            accepted = self._atomic_write_data_file_sync(mirror)
            if accepted and advance_generation:
                self._advance_data_save_write_generation()
        return changed

    def _invoke_data_snapshot_writer_sync(
        self,
        snapshot: dict[str, Any],
        *,
        advance_generation: bool,
    ) -> int:
        """Invoke an overridable snapshot writer with legacy signature support."""
        writer = self._write_data_snapshot_sync
        try:
            parameters = inspect.signature(writer).parameters.values()
            accepts_generation = any(
                parameter.name == "advance_generation"
                or parameter.kind is inspect.Parameter.VAR_KEYWORD
                for parameter in parameters
            )
        except (TypeError, ValueError):
            accepts_generation = False
        if accepts_generation:
            return writer(snapshot, advance_generation=advance_generation)
        return writer(snapshot)

    def _atomic_write_data_file_sync(self, data: dict[str, Any]) -> bool:
        base = self.data_file
        ticket = capture_write_ticket(
            base,
            str(getattr(self, "_persistence_owner_token", "") or ""),
        )
        tmp_file = f"{base}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp"
        try:
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError:
                    pass
            accepted = replace_if_ticket_current(tmp_file, base, ticket)
            self._last_persistence_write_status = {
                "accepted": accepted,
                "state": "saved" if accepted else "superseded",
                "path": str(base),
                "owner": str(ticket.get("owner") or ""),
                "generation": int(ticket.get("generation") or 0),
                "sequence": int(ticket.get("sequence") or 0),
            }
            return accepted
        except Exception:
            self._last_persistence_write_status = {
                "accepted": False,
                "state": "failed",
                "path": str(base),
                "owner": str(ticket.get("owner") or ""),
                "generation": int(ticket.get("generation") or 0),
                "sequence": int(ticket.get("sequence") or 0),
            }
            raise
        finally:
            try:
                if os.path.exists(tmp_file):
                    os.remove(tmp_file)
            except Exception:
                pass

    def _persona_data_for_save(self, persona_id: str) -> dict[str, Any]:
        primary_getter = getattr(self, "_primary_persona_id", None)
        primary = str(primary_getter() if callable(primary_getter) else "").strip()
        if str(persona_id or "").strip() == primary:
            return getattr(self, "_data_default", {})
        profiles = getattr(self, "_persona_data_profiles", {})
        if isinstance(profiles, dict):
            profile = profiles.get(persona_id)
            if isinstance(profile, dict):
                return profile
        ensure_profile = getattr(self, "_ensure_persona_profile", None)
        if callable(ensure_profile):
            profile = ensure_profile(persona_id)
            if isinstance(profile, dict):
                return profile
        return {}

    def _write_persona_data_snapshot_sync(self, persona_id: str, data: dict[str, Any]) -> int:
        primary_getter = getattr(self, "_primary_persona_id", None)
        primary = str(primary_getter() if callable(primary_getter) else "").strip()
        if str(persona_id or "").strip() == primary:
            return self._write_data_snapshot_sync(data)
        changed = self._sanitize_store_control_tags_inplace(data)
        self._sanitize_proactive_candidate_repeat_counts_inplace(data)
        self._compact_store_history_inplace(data)
        saver = getattr(self, "_save_persona_profile_sync", None)
        if not callable(saver):
            raise RuntimeError("persona profile saver is unavailable")
        with self._data_save_io_lock(persona_id):
            saver(persona_id, data)
            self._advance_data_save_write_generation(persona_id)
        return changed

    @staticmethod
    def _save_section_names(values: Collection[str] | None) -> set[str] | None:
        if values is None:
            return None
        if isinstance(values, str):
            values = (values,)
        return {str(value).strip() for value in values if str(value).strip()}

    @classmethod
    def _validate_save_request(
        cls,
        sections: Collection[str] | None,
        deleted_sections: Collection[str],
        full_scope: str | None,
    ) -> tuple[set[str] | None, set[str], str | None]:
        """Validate the explicit section or full-save contract."""
        normalized_sections = cls._save_section_names(sections)
        normalized_deleted = cls._save_section_names(deleted_sections) or set()
        normalized_scope = str(full_scope or "").strip() or None
        if normalized_scope is not None and normalized_scope not in _FULL_SAVE_SCOPES:
            raise ValueError(f"unknown full save scope: {normalized_scope}")
        if normalized_sections is None:
            if normalized_scope is None:
                raise ValueError(
                    "sections must be explicit unless an allowlisted full_scope is provided"
                )
            if normalized_deleted:
                raise ValueError("full_scope cannot be combined with deleted_sections")
        elif normalized_scope is not None:
            raise ValueError("sections and full_scope are mutually exclusive")
        unknown = (
            (normalized_sections or set()) | normalized_deleted
        ) - _DURABLE_SECTION_NAMES
        if unknown:
            raise ValueError(
                "unknown durable sections: " + ", ".join(sorted(unknown))
            )
        overlap = (normalized_sections or set()) & normalized_deleted
        if overlap:
            raise ValueError(
                "changed and deleted sections must be disjoint: "
                + ", ".join(sorted(overlap))
            )
        return normalized_sections, normalized_deleted, normalized_scope

    def _save_is_stopping(self) -> bool:
        stop_event = getattr(self, "_stop_event", None)
        return bool(
            stop_event is not None
            and callable(getattr(stop_event, "is_set", None))
            and stop_event.is_set()
        )

    def _data_save_io_lock(self, persona_id: str = "") -> threading.RLock:
        if not persona_id:
            lock = getattr(self, "_data_save_io_lock_instance", None)
            if lock is None:
                lock = threading.RLock()
                self._data_save_io_lock_instance = lock
            return lock
        locks = getattr(self, "_persona_data_save_io_locks", None)
        if not isinstance(locks, dict):
            locks = {}
            self._persona_data_save_io_locks = locks
        lock = locks.get(persona_id)
        if lock is None:
            lock = threading.RLock()
            locks[persona_id] = lock
        return lock
