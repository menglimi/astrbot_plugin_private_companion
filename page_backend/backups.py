from __future__ import annotations

import heapq
import json
import stat as stat_module
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .validation import normalized_backup_name


class MigrationBackupService:
    """Filesystem and projection service for management-page backups."""

    def __init__(
        self,
        root: Path,
        *,
        checksum_matches: Callable[[dict[str, Any], str], bool],
        error_text: Callable[[object, int], str],
    ) -> None:
        self.root = root
        self._checksum_matches = checksum_matches
        self._error_text = error_text

    def resolve(self, backup_id: object) -> Path:
        safe_name = normalized_backup_name(backup_id)
        path = (self.root / safe_name).resolve()
        root = self.root.resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise ValueError("备份路径不合法") from exc
        if not path.exists() or not path.is_file():
            raise ValueError("备份文件不存在")
        return path

    def list_items(self, *, limit: int = 8) -> list[dict[str, Any]]:
        if not self.root.exists():
            return []
        bounded_limit = max(1, limit)
        # O(n log k), rather than sorting every backup (O(n log n)). Stat each
        # entry once and parse only the selected k files.
        newest = heapq.nlargest(
            bounded_limit,
            self._entries(),
            key=lambda entry: entry[0],
        )
        return [self._project(path, stat) for _mtime, path, stat in newest]

    def _entries(self):
        for path in self.root.glob("*.json"):
            try:
                stat = path.stat()
            except OSError:
                continue
            if stat_module.S_ISREG(stat.st_mode):
                yield stat.st_mtime, path, stat

    def _project(self, path: Path, stat: Any) -> dict[str, Any]:
        item: dict[str, Any] = {
            "id": path.name,
            "name": path.name,
            "size": stat.st_size,
            "mtime": int(stat.st_mtime),
            "version": "",
            "exported_at": 0,
            "checksum_ok": False,
        }
        try:
            package = json.loads(path.read_text(encoding="utf-8-sig"))
            item["version"] = str(package.get("version") or "")
            item["exported_at"] = int(float(package.get("exported_at") or 0))
            item["included_sections"] = [
                str(value)
                for value in package.get("included_sections", [])
                if str(value).strip()
            ]
            checksum = str(package.get("checksum") or "")
            item["checksum_ok"] = bool(checksum) and self._checksum_matches(package, checksum)
        except Exception as exc:
            item["error"] = self._error_text(exc, 120)
        return item
