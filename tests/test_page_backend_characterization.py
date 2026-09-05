# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi


def _package(exported_at: int, version: str = "6.4.4") -> dict[str, object]:
    payload: dict[str, object] = {
        "version": version,
        "exported_at": exported_at,
        "included_sections": ["settings"],
        "settings": {"enabled": True},
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    payload["checksum_algorithm"] = "sha256"
    payload["checksum"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return payload


def test_migration_backup_listing_contract_orders_by_mtime_and_bounds_reads(tmp_path: Path) -> None:
    backup_dir = tmp_path / "config_backups"
    backup_dir.mkdir()
    api = PrivateCompanionPageApi(SimpleNamespace(data_dir=tmp_path))

    for index in range(5):
        path = backup_dir / f"backup_{index}.json"
        path.write_text(json.dumps(_package(index)), encoding="utf-8")
        os.utime(path, (100 + index, 100 + index))
    (backup_dir / "ignored.txt").write_text("not a backup", encoding="utf-8")

    items = api._list_migration_backup_items(limit=3)

    assert [item["id"] for item in items] == ["backup_4.json", "backup_3.json", "backup_2.json"]
    assert [item["mtime"] for item in items] == [104, 103, 102]
    assert [item["exported_at"] for item in items] == [4, 3, 2]
    assert all(item["checksum_ok"] for item in items)
    assert all(item["included_sections"] == ["settings"] for item in items)


def test_migration_backup_listing_preserves_malformed_item_shape(tmp_path: Path) -> None:
    backup_dir = tmp_path / "config_backups"
    backup_dir.mkdir()
    bad = backup_dir / "bad.json"
    bad.write_text("{bad", encoding="utf-8")

    item = PrivateCompanionPageApi(SimpleNamespace(data_dir=tmp_path))._list_migration_backup_items()[0]

    assert item["id"] == item["name"] == "bad.json"
    assert item["version"] == ""
    assert item["exported_at"] == 0
    assert item["checksum_ok"] is False
    assert isinstance(item["error"], str) and item["error"]


def test_migration_backup_path_validation_contract(tmp_path: Path) -> None:
    backup_dir = tmp_path / "config_backups"
    backup_dir.mkdir()
    expected = backup_dir / "safe.json"
    expected.write_text("{}", encoding="utf-8")
    api = PrivateCompanionPageApi(SimpleNamespace(data_dir=tmp_path))

    assert api._resolve_migration_backup_path("../safe.json") == expected.resolve()
    for invalid in ("", "safe.txt", "missing.json"):
        try:
            api._resolve_migration_backup_path(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected ValueError for {invalid!r}")


def test_backup_listing_stats_each_candidate_once_and_reads_only_limit(tmp_path: Path, monkeypatch) -> None:
    backup_dir = tmp_path / "config_backups"
    backup_dir.mkdir()
    for index in range(40):
        path = backup_dir / f"backup_{index:02}.json"
        path.write_text(json.dumps(_package(index)), encoding="utf-8")
        os.utime(path, (100 + index, 100 + index))

    stat_calls = 0
    read_calls = 0
    original_stat = Path.stat
    original_read_text = Path.read_text

    def counted_stat(path: Path, *args, **kwargs):
        nonlocal stat_calls
        stat_calls += 1
        return original_stat(path, *args, **kwargs)

    def counted_read(path: Path, *args, **kwargs):
        nonlocal read_calls
        read_calls += 1
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", counted_stat)
    monkeypatch.setattr(Path, "read_text", counted_read)
    items = PrivateCompanionPageApi(SimpleNamespace(data_dir=tmp_path))._list_migration_backup_items(limit=5)

    assert len(items) == 5
    assert read_calls == 5
    # One root existence check plus one metadata probe per candidate. The old
    # implementation probed the newest k entries a second time after sorting.
    assert stat_calls <= 40 + 2
