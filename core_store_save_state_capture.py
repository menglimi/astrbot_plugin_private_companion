# -*- coding: utf-8 -*-
"""CoreStoreSaveStateCaptureMixin。

由 tools/split_mixin_domain.py 从 core_store.py 机械抽取（18 个方法 + 0 个模块级名字 + 0 个类级赋值 / 410 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 CoreStoreMixin）。
"""
from __future__ import annotations

from .core_store_shared import logger
from .core_store_shared import Any
from .core_store_shared import Collection
from .core_store_shared import _single_line
from .core_store_shared import asyncio
from .core_store_shared import deepcopy
from .core_store_shared import time



class CoreStoreSaveStateCaptureMixin:
    """CoreStoreSaveStateCaptureMixin（从 CoreStoreMixin 拆出）。"""


    def _current_data_save_write_generation(self, persona_id: str = "") -> int:
        if persona_id:
            generations = getattr(self, "_persona_data_save_write_generation", None)
            if not isinstance(generations, dict):
                generations = {}
                self._persona_data_save_write_generation = generations
            return max(0, int(generations.get(persona_id, 0) or 0))
        generation = getattr(self, "_data_save_write_generation", 0)
        if not isinstance(generation, int) or isinstance(generation, bool):
            generation = 0
            self._data_save_write_generation = generation
        return max(0, generation)

    def _advance_data_save_write_generation(self, persona_id: str = "") -> int:
        generation = self._current_data_save_write_generation(persona_id) + 1
        if persona_id:
            self._persona_data_save_write_generation[persona_id] = generation
        else:
            self._data_save_write_generation = generation
        return generation

    def _default_data_for_save(self) -> dict[str, Any]:
        """Return the primary store independently of the event persona context."""

        data = getattr(self, "_data_default", None)
        if type(data) is dict:
            return data
        fallback = getattr(self, "data", None)
        return fallback if type(fallback) is dict else {}

    def _ensure_default_save_state(self) -> None:
        legacy_dirty = getattr(self, "_data_save_dirty", None) is True
        if not isinstance(getattr(self, "_data_save_dirty", None), dict):
            self._data_save_dirty = {}
        if not isinstance(getattr(self, "_data_save_deleted", None), dict):
            self._data_save_deleted = {}
        if not isinstance(getattr(self, "_data_save_dirty_since", None), dict):
            self._data_save_dirty_since = {}
        if not isinstance(getattr(self, "_data_save_section_revisions", None), dict):
            self._data_save_section_revisions = {}
        if not isinstance(getattr(self, "_data_save_full_revision", None), int):
            self._data_save_full_revision = 0
        if not isinstance(getattr(self, "_data_save_full_scope", None), str):
            self._data_save_full_scope = ""
        if not isinstance(getattr(self, "_data_save_revision", None), int):
            seed = 1
            manager = getattr(self, "store_manager", None)
            next_revision = getattr(manager, "next_revision", None)
            if callable(next_revision):
                try:
                    seed = max(1, int(next_revision()))
                except Exception:
                    seed = 1
            self._data_save_revision = seed - 1
        if not isinstance(getattr(self, "_data_save_write_generation", None), int):
            self._data_save_write_generation = 0
        if legacy_dirty and not self._data_save_dirty and not self._data_save_deleted:
            revision = self._next_data_save_revision()
            now = time.monotonic()
            live_data = self._default_data_for_save()
            for section in live_data if isinstance(live_data, dict) else ():
                self._data_save_dirty[str(section)] = revision
                self._data_save_dirty_since[str(section)] = now
                self._data_save_section_revisions[str(section)] = revision
            self._data_save_full_revision = revision
            self._data_save_full_since = now
            self._data_save_full_scope = "startup_maintenance"

    def _refresh_data_save_revision_from_manager(self) -> int:
        manager = getattr(self, "store_manager", None)
        next_revision = getattr(manager, "next_revision", None)
        if not callable(next_revision):
            return max(0, int(getattr(self, "_data_save_revision", 0) or 0))
        try:
            persisted = max(0, int(next_revision()) - 1)
        except Exception:
            return max(0, int(getattr(self, "_data_save_revision", 0) or 0))
        current = max(0, int(getattr(self, "_data_save_revision", 0) or 0))
        self._data_save_revision = max(current, persisted)
        return self._data_save_revision

    def _ensure_persona_save_state(self) -> None:
        legacy_dirty = getattr(self, "_persona_data_save_dirty", None)
        legacy_personas = set(legacy_dirty) if isinstance(legacy_dirty, set) else set()
        for name in (
            "_persona_data_save_dirty",
            "_persona_data_save_deleted",
            "_persona_data_save_dirty_since",
            "_persona_data_save_full_revision",
            "_persona_data_save_full_scope",
            "_persona_data_save_revision",
            "_persona_data_save_section_revisions",
        ):
            if not isinstance(getattr(self, name, None), dict):
                setattr(self, name, {})
        if not isinstance(
            getattr(self, "_persona_data_save_write_generation", None), dict
        ):
            self._persona_data_save_write_generation = {}
        if not isinstance(getattr(self, "_persona_data_save_tasks", None), dict):
            self._persona_data_save_tasks = {}
        if not isinstance(getattr(self, "_persona_data_save_full_scope", None), dict):
            self._persona_data_save_full_scope = {}
        for persona_id in legacy_personas:
            if persona_id in self._persona_data_save_dirty or persona_id in self._persona_data_save_deleted:
                continue
            revision = max(1, int(self._persona_data_save_revision.get(persona_id, 0) or 0) + 1)
            self._persona_data_save_revision[persona_id] = revision
            profile = self._persona_data_for_save(str(persona_id))
            dirty = self._persona_data_save_dirty.setdefault(str(persona_id), {})
            since = self._persona_data_save_dirty_since.setdefault(str(persona_id), {})
            section_revisions = self._persona_data_save_section_revisions.setdefault(str(persona_id), {})
            now = time.monotonic()
            for section in profile if isinstance(profile, dict) else ():
                dirty[str(section)] = revision
                since[str(section)] = now
                section_revisions[str(section)] = revision
            self._persona_data_save_full_revision[str(persona_id)] = revision
            self._persona_data_save_full_scope[str(persona_id)] = "startup_maintenance"

    def _next_data_save_revision(self, persona_id: str = "") -> int:
        if persona_id:
            self._ensure_persona_save_state()
            current = max(0, int(self._persona_data_save_revision.get(persona_id, 0) or 0))
            revision = current + 1
            self._persona_data_save_revision[persona_id] = revision
            return revision
        self._ensure_default_save_state()
        revision = max(0, int(self._data_save_revision or 0)) + 1
        self._data_save_revision = revision
        return revision

    @staticmethod
    def _expand_bookshelf_save_sections(
        live_data: dict[str, Any],
        changed: set[str],
        deleted: set[str],
        already_deleted: dict[str, int],
    ) -> None:
        bookshelf_sections = {
            "bookshelf_items",
            "bookshelf_secret",
            "bookshelf_store_revision",
            "reading_archive_integration",
        }
        if not (bookshelf_sections & (changed | deleted)):
            return
        for section in bookshelf_sections:
            if section in live_data and section not in deleted and section not in already_deleted:
                changed.add(section)

    def _mark_default_data_dirty(
        self,
        *,
        sections: Collection[str] | None,
        deleted_sections: Collection[str],
        full_scope: str | None = None,
    ) -> bool:
        self._ensure_default_save_state()
        changed, deleted, full_scope = self._validate_save_request(
            sections, deleted_sections, full_scope
        )
        full = changed is None
        if changed is None:
            changed = {str(name) for name in self._default_data_for_save()}
        if changed & deleted:
            raise ValueError("changed and deleted sections must be disjoint")
        if not changed and not deleted and not full:
            return False
        self._expand_bookshelf_save_sections(
            self._default_data_for_save(),
            changed,
            deleted,
            self._data_save_deleted,
        )
        revision = self._next_data_save_revision()
        now = time.monotonic()
        for section in changed:
            self._data_save_dirty[section] = revision
            self._data_save_deleted.pop(section, None)
            self._data_save_dirty_since.setdefault(section, now)
            self._data_save_section_revisions[section] = revision
        for section in deleted:
            self._data_save_deleted[section] = revision
            self._data_save_dirty.pop(section, None)
            self._data_save_dirty_since.setdefault(section, now)
            self._data_save_section_revisions[section] = revision
        if full:
            self._data_save_full_revision = revision
            self._data_save_full_since = now
            self._data_save_full_scope = str(full_scope)
        return True

    def _mark_persona_data_dirty(
        self,
        persona_id: str,
        *,
        sections: Collection[str] | None,
        deleted_sections: Collection[str],
        full_scope: str | None = None,
    ) -> bool:
        self._ensure_persona_save_state()
        live_data = self._persona_data_for_save(persona_id)
        changed, deleted, full_scope = self._validate_save_request(
            sections, deleted_sections, full_scope
        )
        full = changed is None
        if changed is None:
            changed = {str(name) for name in live_data}
        if changed & deleted:
            raise ValueError("changed and deleted sections must be disjoint")
        if not changed and not deleted and not full:
            return False
        dirty = self._persona_data_save_dirty.setdefault(persona_id, {})
        removed = self._persona_data_save_deleted.setdefault(persona_id, {})
        dirty_since = self._persona_data_save_dirty_since.setdefault(persona_id, {})
        section_revisions = self._persona_data_save_section_revisions.setdefault(persona_id, {})
        self._expand_bookshelf_save_sections(live_data, changed, deleted, removed)
        revision = self._next_data_save_revision(persona_id)
        now = time.monotonic()
        for section in changed:
            dirty[section] = revision
            removed.pop(section, None)
            dirty_since.setdefault(section, now)
            section_revisions[section] = revision
        for section in deleted:
            removed[section] = revision
            dirty.pop(section, None)
            dirty_since.setdefault(section, now)
            section_revisions[section] = revision
        if full:
            self._persona_data_save_full_revision[persona_id] = revision
            self._persona_data_save_full_scope[persona_id] = str(full_scope)
        return True

    def _default_data_save_is_dirty(self) -> bool:
        dirty = getattr(self, "_data_save_dirty", None)
        if not isinstance(dirty, dict):
            return bool(dirty)
        return bool(
            dirty
            or getattr(self, "_data_save_deleted", {})
            or getattr(self, "_data_save_full_revision", 0)
        )

    def _persona_data_save_is_dirty(self, persona_id: str) -> bool:
        self._ensure_persona_save_state()
        return bool(
            self._persona_data_save_dirty.get(persona_id)
            or self._persona_data_save_deleted.get(persona_id)
            or self._persona_data_save_full_revision.get(persona_id, 0)
        )

    def _dirty_persona_ids(self) -> set[str]:
        self._ensure_persona_save_state()
        return {
            str(persona_id)
            for source in (
                self._persona_data_save_dirty,
                self._persona_data_save_deleted,
                self._persona_data_save_full_revision,
            )
            for persona_id, value in source.items()
            if value
        }

    def _capture_data_save_batch(
        self,
        live_data: dict[str, Any],
        dirty: dict[str, int],
        deleted: dict[str, int],
        full_revision: int,
        section_revisions: dict[str, int],
        *,
        full_snapshot: bool,
    ) -> dict[str, Any]:
        captured_revisions = dict(dirty)
        captured_deleted = dict(deleted)
        payloads = {
            section: deepcopy(live_data[section])
            for section in captured_revisions
            if section in live_data and section not in captured_deleted
        }
        missing = {
            section: revision
            for section, revision in captured_revisions.items()
            if section not in live_data and section not in captured_deleted
        }
        readonly_context: dict[str, Any] = {}
        dependency_revisions: dict[str, int] = {}
        if "proactive_candidate_pool" in payloads and "users" not in payloads:
            users = live_data.get("users")
            if isinstance(users, dict):
                readonly_context["users"] = deepcopy(users)
                dependency_revisions["users"] = int(section_revisions.get("users", 0) or 0)
        return {
            "changed_revisions": {
                section: revision
                for section, revision in captured_revisions.items()
                if section in payloads
            },
            "deleted_revisions": captured_deleted,
            "missing_revisions": missing,
            "payloads": payloads,
            "readonly_context": readonly_context,
            "dependency_revisions": dependency_revisions,
            "full_revision": int(full_revision or 0),
            "full_snapshot": deepcopy(live_data) if full_snapshot else None,
        }

    def _capture_default_data_save_batch(
        self,
        *,
        force_full: bool = False,
    ) -> dict[str, Any]:
        self._ensure_default_save_state()
        write_generation = self._current_data_save_write_generation()
        manager = getattr(self, "store_manager", None)
        manager_backend = str(getattr(manager, "backend_name", "") or "").lower()
        full_compatibility_save = bool(self._data_save_full_revision)
        full_scope = str(getattr(self, "_data_save_full_scope", "") or "").strip()
        # A full-scope request marks every live section dirty, but SQLite can
        # still persist that capture incrementally.  Shutdown is the only
        # general path that forces a compatibility full snapshot so removed
        # roots are reconciled without inferring deletes during ordinary
        # writes.  An explicit reset is also a complete replacement by
        # definition and must become durable before returning.
        full_replacement = bool(
            full_compatibility_save
            and (force_full or full_scope == "explicit_reset")
        )
        incremental = bool(
            manager_backend == "sqlite"
            and callable(getattr(manager, "save_sections", None))
            and not full_replacement
        )
        batch = self._capture_data_save_batch(
            self._default_data_for_save(),
            self._data_save_dirty,
            self._data_save_deleted,
            self._data_save_full_revision,
            self._data_save_section_revisions,
            full_snapshot=not incremental,
        )
        batch["incremental"] = incremental
        batch["full_replacement"] = full_replacement
        batch["preserve_tombstones"] = full_replacement
        batch["write_generation"] = write_generation
        return batch

    async def _flush_default_data_save_on_terminate(self) -> None:
        """Drain SQLite default-store revisions without replacing persisted tombstones."""
        manager = getattr(self, "store_manager", None)
        if not (
            str(getattr(manager, "backend_name", "") or "").lower() == "sqlite"
            and callable(getattr(manager, "save_sections", None))
        ):
            return

        current = asyncio.current_task()
        task = getattr(self, "_data_save_task", None)
        if isinstance(task, asyncio.Task) and not task.done() and task is not current:
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                # A cancelled scheduled writer leaves its revisions dirty for this
                # final pass to retry.
                pass
            except Exception as exc:
                logger.debug(
                    "Waiting for the default incremental writer "
                    "during shutdown failed: %s",
                    _single_line(exc, 160),
                )

        self._ensure_default_save_state()
        while self._default_data_save_is_dirty():
            # ``sections=None`` is the compatibility full-replacement path.
            # Preserve that meaning during shutdown so a section removed from
            # the live store is removed by the full snapshot, while ordinary
            # partial batches still require explicit tombstones.
            batch = self._capture_default_data_save_batch(force_full=True)
            result = await asyncio.to_thread(
                self._write_default_data_save_batch_sync,
                batch,
            )
            self._finish_default_data_save_batch(batch, result)
            if self._default_data_save_is_dirty():
                await asyncio.sleep(0)

    def _capture_persona_data_save_batch(self, persona_id: str) -> dict[str, Any]:
        self._ensure_persona_save_state()
        write_generation = self._current_data_save_write_generation(persona_id)
        batch = self._capture_data_save_batch(
            self._persona_data_for_save(persona_id),
            self._persona_data_save_dirty.setdefault(persona_id, {}),
            self._persona_data_save_deleted.setdefault(persona_id, {}),
            int(self._persona_data_save_full_revision.get(persona_id, 0) or 0),
            self._persona_data_save_section_revisions.setdefault(persona_id, {}),
            full_snapshot=True,
        )
        batch["write_generation"] = write_generation
        return batch

    def _prepare_dirty_save_payloads_sync(
        self,
        payloads: dict[str, Any],
        readonly_context: dict[str, Any],
    ) -> tuple[dict[str, Any], int, int, dict[str, int], set[str]]:
        prepared = payloads
        original_payloads = deepcopy(payloads)
        control_changed = self._sanitize_store_control_tags_inplace(prepared)
        repeat_changed = self._sanitize_proactive_candidate_repeat_counts_inplace(prepared)
        readonly_names: set[str] = set()
        for section, value in readonly_context.items():
            if section not in prepared:
                prepared[section] = value
                readonly_names.add(section)
        compacted = self._compact_store_history_inplace(prepared)
        for section in readonly_names:
            prepared.pop(section, None)
        cleaned_sections = {
            section
            for section, value in prepared.items()
            if section not in original_payloads or original_payloads[section] != value
        }
        return prepared, control_changed, repeat_changed, compacted, cleaned_sections
