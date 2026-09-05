from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from astrbot_plugin_private_companion.storage.store_manager import StoreManager


def _new_store() -> dict:
    return {"users": {}, "bookshelf_items": [], "bookshelf_store_revision": 0}


def _defaults(data: dict) -> dict:
    result = dict(data)
    result.setdefault("users", {})
    result.setdefault("bookshelf_items", [])
    result.setdefault("bookshelf_store_revision", 0)
    return result


def _manager(root: Path) -> StoreManager:
    return StoreManager(
        backend_name="sqlite",
        data_file=root / "companions.json",
        sqlite_path=root / "companions.db",
        ensure_defaults=_defaults,
        new_store=_new_store,
    )


def test_bookshelf_recovery_does_not_return_undurable_state(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    manager.sqlite_backend.save_store(
        {"users": {}, "bookshelf_items": [{"album_id": "old"}], "bookshelf_store_revision": 1}
    )
    manager.json_backend.save_store(
        {"users": {}, "bookshelf_items": [{"album_id": "new"}], "bookshelf_store_revision": 2}
    )

    with patch.object(manager.sqlite_backend, "save_sections", side_effect=OSError("read-only")):
        # A successful return here claims that the recovered snapshot is usable,
        # although the only authoritative backend still contains the old data.
        with pytest.raises(RuntimeError, match="not durable"):
            manager.load_initial_store()
