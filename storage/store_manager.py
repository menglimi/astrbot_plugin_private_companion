# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

from . import cleanup_storage_bytecode_cache
from .factory import build_store_backend
from .json_backend import JsonStoreBackend
from .migration import migrate_json_to_backend_if_needed


class StoreManager:
    def __init__(
        self,
        *,
        backend_name: str,
        data_file: str | Path,
        sqlite_path: str | Path,
        ensure_defaults: Callable[[dict[str, Any]], dict[str, Any]],
        new_store: Callable[[], dict[str, Any]],
    ) -> None:
        cleanup_storage_bytecode_cache()
        self.backend_name = str(backend_name or "json").strip().lower() or "json"
        self.data_file = Path(data_file)
        self.sqlite_path = Path(sqlite_path)
        self.ensure_defaults = ensure_defaults
        self.new_store = new_store
        self.json_backend = JsonStoreBackend(self.data_file, ensure_defaults, new_store)
        self.backend = build_store_backend(
            backend_name=self.backend_name,
            data_file=self.data_file,
            sqlite_path=self.sqlite_path,
            ensure_defaults=ensure_defaults,
            new_store=new_store,
        )

    def load_initial_store(self) -> dict[str, Any]:
        if self.backend.backend_name() == "json":
            return self.backend.load_store()
        return migrate_json_to_backend_if_needed(self.backend, self.json_backend, self.new_store())

    def save_store(self, data: dict[str, Any]) -> None:
        if self.backend.backend_name() == "json" and not data.get("worldbook_entries") and self.json_backend.exists():
            existing = self.json_backend.load_store()
            if isinstance(existing, dict) and existing.get("worldbook_entries"):
                for key in (
                    "worldbook_entries",
                    "worldbook_member_profiles",
                    "worldbook_group_profiles",
                    "worldbook_import_state",
                ):
                    data[key] = existing.get(key, data.get(key))
        self.backend.save_store(data)

    def save_snapshot(self, data: dict[str, Any]) -> None:
        self.backend.save_snapshot(data)

    def export_current_to_json(self, data: dict[str, Any]) -> None:
        self.json_backend.save_store(deepcopy(data))

    def health_check(self) -> dict[str, Any]:
        return self.backend.health_check()
