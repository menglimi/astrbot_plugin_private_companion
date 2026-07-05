# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .json_backend import JsonStoreBackend
from .sqlite_backend import SqliteStoreBackend


def build_store_backend(
    *,
    backend_name: str,
    data_file: str | Path,
    sqlite_path: str | Path,
    ensure_defaults: Callable[[dict[str, Any]], dict[str, Any]],
    new_store: Callable[[], dict[str, Any]],
) -> Any:
    name = str(backend_name or "json").strip().lower()
    if name == "sqlite":
        return SqliteStoreBackend(sqlite_path, ensure_defaults, new_store)
    return JsonStoreBackend(data_file, ensure_defaults, new_store)
