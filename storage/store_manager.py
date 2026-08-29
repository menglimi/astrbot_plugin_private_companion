# -*- coding: utf-8 -*-
from __future__ import annotations

import time
from collections.abc import Callable, Collection, Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any


from . import cleanup_storage_bytecode_cache
from .factory import build_store_backend
from .json_backend import JsonStoreBackend
from .migration import migrate_json_to_backend_if_needed
from .path_generation import activate_persistence_owner, shared_prepare_lock
from .sqlite_backend import SqliteStoreNotInitializedError
from ..logging_util import get_module_logger

logger = get_module_logger(__name__)

_BOOKSHELF_SECTIONS = (
    "bookshelf_items",
    "reading_archive_integration",
    "bookshelf_secret",
    "bookshelf_store_revision",
)

def _shared_store_lock(path: str | Path):
    return shared_prepare_lock(path)


def _bookshelf_revision(data: Any) -> int:
    if not isinstance(data, dict):
        return 0
    try:
        return max(0, int(data.get("bookshelf_store_revision") or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _bookshelf_state(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    state = data.get("reading_archive_integration")
    return state if isinstance(state, dict) else {}


def _bookshelf_item_identity(item: Any) -> str:
    if not isinstance(item, dict):
        return ""
    item_type = str(item.get("type") or item.get("kind") or "").strip()
    album_id = str(item.get("album_id") or item.get("id") or "").strip()
    key = str(item.get("key") or "").strip()
    if not album_id and key.startswith("archive_item:"):
        album_id = key.split(":", 1)[1].strip()
    if not album_id and key.startswith("archive-"):
        album_id = key[3:].strip()
    if album_id:
        return f"{item_type or 'archive_item'}:{album_id}"
    return key


def _merge_string_history(
    primary: Any, secondary: Any, *, limit: int = 300
) -> list[Any]:
    merged: list[Any] = []
    seen: set[str] = set()
    for source in (primary, secondary):
        if not isinstance(source, list):
            continue
        for value in source:
            marker = str(value).strip()
            if not marker or marker in seen:
                continue
            seen.add(marker)
            merged.append(deepcopy(value))
    return merged[-max(1, limit) :]


def reconcile_bookshelf_payload(
    preferred: dict[str, Any],
    fallback: dict[str, Any],
) -> tuple[dict[str, Any], bool, int]:
    """Keep the newest shelf state and recover legacy items missing from it."""

    if not isinstance(preferred, dict) or not isinstance(fallback, dict):
        return preferred, False, 0
    preferred_revision = _bookshelf_revision(preferred)
    fallback_revision = _bookshelf_revision(fallback)
    if fallback_revision > preferred_revision:
        primary, secondary = fallback, preferred
    else:
        primary, secondary = preferred, fallback

    primary_state_raw = primary.get("reading_archive_integration")
    secondary_state_raw = secondary.get("reading_archive_integration")
    primary_state = (
        deepcopy(primary_state_raw) if isinstance(primary_state_raw, dict) else None
    )
    secondary_state = (
        deepcopy(secondary_state_raw) if isinstance(secondary_state_raw, dict) else None
    )
    if primary_state is not None:
        merged_state: dict[str, Any] | None = deepcopy(secondary_state or {})
        merged_state.update(primary_state)
    elif secondary_state is not None:
        merged_state = deepcopy(secondary_state)
    else:
        merged_state = None

    if preferred_revision == fallback_revision and merged_state is not None:
        deleted_ids = _merge_string_history(
            primary_state.get("deleted_album_ids")
            if primary_state is not None
            else None,
            secondary_state.get("deleted_album_ids")
            if secondary_state is not None
            else None,
        )
        deleted_titles = _merge_string_history(
            primary_state.get("deleted_titles") if primary_state is not None else None,
            secondary_state.get("deleted_titles")
            if secondary_state is not None
            else None,
        )
        if deleted_ids:
            merged_state["deleted_album_ids"] = deleted_ids
        if deleted_titles:
            merged_state["deleted_titles"] = deleted_titles

    primary_items_raw = primary.get("bookshelf_items")
    secondary_items_raw = secondary.get("bookshelf_items")
    primary_items = primary_items_raw if isinstance(primary_items_raw, list) else []
    secondary_items = (
        secondary_items_raw if isinstance(secondary_items_raw, list) else []
    )
    primary_identities = {
        identity
        for raw_item in primary_items
        if (identity := _bookshelf_item_identity(raw_item))
    }
    merged_entries: list[tuple[bool, Any]] = []
    seen_secondary: set[str] = set()
    for raw_item in secondary_items:
        identity = _bookshelf_item_identity(raw_item)
        if identity and (identity in primary_identities or identity in seen_secondary):
            continue
        if identity:
            seen_secondary.add(identity)
        merged_entries.append((True, deepcopy(raw_item)))
    seen_primary: set[str] = set()
    for raw_item in primary_items:
        identity = _bookshelf_item_identity(raw_item)
        if identity and identity in seen_primary:
            continue
        if identity:
            seen_primary.add(identity)
        merged_entries.append((False, deepcopy(raw_item)))
    capacity = max(80, len(primary_items))
    merged_entries = merged_entries[-capacity:]
    merged_items = [item for _from_secondary, item in merged_entries]
    recovered = sum(1 for from_secondary, _item in merged_entries if from_secondary)

    result = deepcopy(preferred)
    if (
        isinstance(preferred.get("bookshelf_items"), list)
        or bool(merged_items)
        or preferred.get("bookshelf_items") is None
    ):
        result["bookshelf_items"] = merged_items
    if merged_state is not None and (
        isinstance(preferred.get("reading_archive_integration"), dict)
        or bool(merged_state)
        or preferred.get("reading_archive_integration") is None
    ):
        result["reading_archive_integration"] = merged_state

    primary_secret = primary.get("bookshelf_secret")
    secondary_secret = secondary.get("bookshelf_secret")
    if isinstance(primary_secret, dict) and primary_secret:
        result["bookshelf_secret"] = deepcopy(primary_secret)
    elif isinstance(secondary_secret, dict) and secondary_secret:
        result["bookshelf_secret"] = deepcopy(secondary_secret)
    if (
        preferred_revision
        or fallback_revision
        or "bookshelf_store_revision" in preferred
        or "bookshelf_store_revision" in fallback
    ):
        result["bookshelf_store_revision"] = max(preferred_revision, fallback_revision)

    changed = any(result.get(key) != preferred.get(key) for key in _BOOKSHELF_SECTIONS)
    return result, changed, recovered


class StoreManager:
    def __init__(
        self,
        *,
        backend_name: str,
        data_file: str | Path,
        sqlite_path: str | Path,
        ensure_defaults: Callable[[dict[str, Any]], dict[str, Any]],
        new_store: Callable[[], dict[str, Any]],
        persistence_owner_token: str = "",
    ) -> None:
        cleanup_storage_bytecode_cache()
        self.backend_name = str(backend_name or "json").strip().lower() or "json"
        self.data_file = Path(data_file)
        self.sqlite_path = Path(sqlite_path)
        self.ensure_defaults = ensure_defaults
        self.new_store = new_store
        self.persistence_owner_token = str(persistence_owner_token or "").strip()
        self.json_backend = JsonStoreBackend(
            self.data_file,
            ensure_defaults,
            new_store,
            persistence_owner_token=self.persistence_owner_token,
        )
        self.sqlite_backend = build_store_backend(
            backend_name="sqlite",
            data_file=self.data_file,
            sqlite_path=self.sqlite_path,
            ensure_defaults=ensure_defaults,
            new_store=new_store,
        )
        self.backend = (
            self.sqlite_backend if self.backend_name == "sqlite" else self.json_backend
        )
        active_path = (
            self.sqlite_path if self.backend_name == "sqlite" else self.data_file
        )
        self._store_lock = _shared_store_lock(active_path)
        self._json_store_lock = _shared_store_lock(self.data_file)
        self._last_json_export_at = 0.0
        self._last_persistence_status: dict[str, Any] = {
            "accepted": None,
            "state": "idle",
            "path": str(active_path),
        }

    def activate_persistence_generation(self) -> dict[str, int]:
        if not self.persistence_owner_token:
            return {}
        return activate_persistence_owner(
            self.persistence_owner_token,
            [self.data_file],
        )

    def persistence_status(self) -> dict[str, Any]:
        return dict(self._last_persistence_status)

    def _record_json_write_status(self) -> bool:
        status = dict(getattr(self.json_backend, "last_write_status", {}) or {})
        self._last_persistence_status = status
        return status.get("accepted") is not False

    @staticmethod
    def _load_optional_store(
        backend: Any, *, strict: bool = False
    ) -> dict[str, Any] | None:
        if backend is None or not backend.exists():
            return None
        try:
            return backend.load_store()
        except SqliteStoreNotInitializedError:
            return None
        except Exception as exc:
            if strict:
                raise
            logger.warning(
                "备用 %s 存储读取失败，跳过夹层恢复: %s",
                backend.backend_name(),
                exc,
            )
            return None

    def _apply_bookshelf_recovery(
        self,
        selected: dict[str, Any],
        fallback: dict[str, Any] | None,
        *,
        fallback_name: str,
        persist: bool,
    ) -> dict[str, Any]:
        if not isinstance(fallback, dict):
            return selected
        deleted_revisions: Mapping[str, int] = {}
        if self.backend.backend_name() == "sqlite":
            deleted_revisions = self.backend.deleted_section_revisions(
                _BOOKSHELF_SECTIONS
            )
        reconciled, changed, recovered = reconcile_bookshelf_payload(selected, fallback)
        if deleted_revisions:
            for key in deleted_revisions:
                if key in selected:
                    reconciled[key] = deepcopy(selected[key])
                else:
                    reconciled.pop(key, None)
            changed = any(
                reconciled.get(key) != selected.get(key) for key in _BOOKSHELF_SECTIONS
            )
        if not changed:
            return selected
        logger.warning(
            "检测到夹层存储来源不完整，已从 %s 保守恢复: items=%s revision=%s",
            fallback_name,
            recovered,
            _bookshelf_revision(reconciled),
        )
        if persist:
            try:
                if self.backend.backend_name() == "sqlite":
                    revision = self.backend.next_revision()
                    changed_sections = {
                        key: (revision, deepcopy(reconciled[key]))
                        for key in _BOOKSHELF_SECTIONS
                        if key in reconciled and key not in deleted_revisions
                    }
                    deleted_sections = {key: revision for key in deleted_revisions}
                    self.backend.save_sections(changed_sections, deleted_sections)
                else:
                    self.backend.save_store(reconciled)
            except Exception as exc:
                logger.warning(
                    "夹层恢复结果暂未写回 %s，本进程继续使用已恢复数据: %s",
                    self.backend.backend_name(),
                    exc,
                )
        return reconciled

    def load_initial_store(self) -> dict[str, Any]:
        with self._store_lock:
            if self.backend.backend_name() == "json":
                selected = self.backend.load_store()
                fallback = self._load_optional_store(self.sqlite_backend)
                return self._apply_bookshelf_recovery(
                    selected,
                    fallback,
                    fallback_name="SQLite",
                    persist=True,
                )
            selected = migrate_json_to_backend_if_needed(
                self.backend, self.json_backend, self.new_store()
            )
            fallback = self._load_optional_store(self.json_backend)
            return self._apply_bookshelf_recovery(
                selected,
                fallback,
                fallback_name="JSON",
                persist=True,
            )

    def load_sections(
        self,
        section_names: Collection[str],
        *,
        backend_name: str | None = None,
        read_only: bool = False,
    ) -> dict[str, Any]:
        """Read exact durable roots; ``read_only`` forbids schema/store writes."""

        names = tuple(
            dict.fromkeys(
                str(name).strip()
                for name in section_names
                if str(name).strip()
            )
        )
        if not names:
            return {}
        selected = str(backend_name or self.backend_name).strip().lower()
        if selected == "json":
            backend = self.json_backend
            lock = self._json_store_lock
        elif selected == "sqlite":
            backend = self.sqlite_backend
            lock = _shared_store_lock(self.sqlite_path)
        else:
            raise ValueError(f"unknown store backend: {selected}")
        with lock:
            if not backend.exists():
                return {}
            try:
                readonly_loader = getattr(backend, "load_store_read_only", None)
                loader = getattr(backend, "load_sections", None)
                if read_only and callable(readonly_loader):
                    # Reuse the backend's immutable/query-only reader.  The
                    # existing file size is also a natural upper bound for
                    # both encoded payload and database bytes, so preflight
                    # does not impose a new startup size limit.
                    database_bytes = max(
                        1,
                        int(backend.db_path.lstat().st_size),
                    )
                    store = readonly_loader(
                        max_payload_bytes=database_bytes,
                        max_database_bytes=database_bytes,
                    )
                    loaded = {
                        name: deepcopy(store[name])
                        for name in names
                        if name in store
                    }
                elif callable(loader):
                    loaded = loader(names)
                else:
                    store = backend.load_store()
                    loaded = {
                        name: deepcopy(store[name])
                        for name in names
                        if name in store
                    }
            except SqliteStoreNotInitializedError:
                return {}
        if not isinstance(loaded, dict):
            raise RuntimeError("store section reader returned a non-object result")
        return {
            name: deepcopy(loaded[name])
            for name in names
            if name in loaded
        }

    def _prepare_store_for_save(self, data: dict[str, Any]) -> dict[str, Any]:
        section_loader = getattr(self.backend, "load_sections", None)
        if callable(section_loader):
            existing = section_loader(_BOOKSHELF_SECTIONS)
        else:
            existing = self._load_optional_store(self.backend, strict=True)
        if not isinstance(existing, dict):
            return data
        reconciled, changed, recovered = reconcile_bookshelf_payload(data, existing)
        if not changed:
            return data
        for key in _BOOKSHELF_SECTIONS:
            if key in reconciled:
                data[key] = deepcopy(reconciled[key])
        logger.warning(
            "拦截到可能清空夹层的旧快照，已保留现有存储内容: items=%s revision=%s",
            recovered,
            _bookshelf_revision(reconciled),
        )
        return data

    def save_store(self, data: dict[str, Any]) -> None:
        if self.backend.backend_name() == "json":
            ticket = self.json_backend.capture_write_ticket()
            with self._store_lock:
                data = self._prepare_store_for_save(data)
                if not data.get("worldbook_entries") and self.json_backend.exists():
                    existing = self.json_backend.load_store()
                    if isinstance(existing, dict) and existing.get("worldbook_entries"):
                        for key in (
                            "worldbook_entries",
                            "worldbook_member_profiles",
                            "worldbook_group_profiles",
                            "worldbook_import_state",
                        ):
                            data[key] = existing.get(key, data.get(key))
            # JSON encoding/fsync is intentionally outside the shared prepare
            # lock.  The backend's reload-stable path lock validates generation
            # and sequence immediately before the atomic replace.
            try:
                self.json_backend.save_store(data, write_ticket=ticket)
            finally:
                self._record_json_write_status()
            return
        with self._store_lock:
            data = self._prepare_store_for_save(data)
            self.backend.save_store(data)

    def save_snapshot(
        self,
        data: dict[str, Any],
        *,
        minimum_revision: int | None = None,
        deleted_sections: Mapping[str, int] | None = None,
        preserve_tombstones: bool = False,
    ) -> int | None:
        if self.backend.backend_name() == "json":
            ticket = self.json_backend.capture_write_ticket()
            with self._store_lock:
                prepared = self._prepare_store_for_save(data)
            try:
                result = self.json_backend.save_snapshot(
                    prepared,
                    minimum_revision=minimum_revision,
                    deleted_sections=deleted_sections,
                    preserve_tombstones=preserve_tombstones,
                    write_ticket=ticket,
                )
            finally:
                self._record_json_write_status()
            return result
        with self._store_lock:
            return self.backend.save_snapshot(
                self._prepare_store_for_save(data),
                minimum_revision=minimum_revision,
                deleted_sections=deleted_sections,
                preserve_tombstones=preserve_tombstones,
            )

    def save_sections(
        self,
        changed_sections: Mapping[str, tuple[int, Any]],
        deleted_sections: Mapping[str, int],
    ) -> Mapping[str, int]:
        with self._store_lock:
            return self.backend.save_sections(changed_sections, deleted_sections)

    def next_revision(self) -> int:
        with self._store_lock:
            return self.backend.next_revision()

    def deleted_section_revisions(
        self,
        section_names: Collection[str],
    ) -> Mapping[str, int]:
        with self._store_lock:
            return self.backend.deleted_section_revisions(section_names)

    def export_current_to_json(
        self,
        data: dict[str, Any],
        *,
        force: bool = False,
        min_interval_seconds: float = 300.0,
    ) -> bool:
        now = time.monotonic()
        with self._json_store_lock:
            if not force and now - self._last_json_export_at < max(1.0, float(min_interval_seconds)):
                return False
            self._last_json_export_at = now
            ticket = self.json_backend.capture_write_ticket()
        try:
            self.json_backend.save_store(deepcopy(data), write_ticket=ticket)
            accepted = self._record_json_write_status()
        except Exception:
            with self._json_store_lock:
                if self._last_json_export_at == now:
                    self._last_json_export_at = 0.0
            raise
        if not accepted:
            with self._json_store_lock:
                if self._last_json_export_at == now:
                    self._last_json_export_at = 0.0
        return accepted

    def health_check(self, *, raise_on_error: bool = False) -> dict[str, Any]:
        return self.backend.health_check(raise_on_error=raise_on_error)
