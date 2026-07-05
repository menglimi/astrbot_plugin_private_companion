# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from astrbot.api import logger

from .backend_base import StoreBackendBase


class JsonStoreBackend(StoreBackendBase):
    def __init__(
        self,
        data_file: str | Path,
        ensure_defaults: Callable[[dict[str, Any]], dict[str, Any]],
        new_store: Callable[[], dict[str, Any]],
    ) -> None:
        self.data_file = Path(data_file)
        self.ensure_defaults = ensure_defaults
        self.new_store = new_store

    def backend_name(self) -> str:
        return "json"

    def exists(self) -> bool:
        return self.data_file.exists()

    def load_store(self) -> dict[str, Any]:
        if not self.exists():
            return self.new_store()
        try:
            with self.data_file.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return self.new_store()
            return self.ensure_defaults(data)
        except Exception as exc:
            logger.warning(f"[PrivateCompanion] 读取 JSON 数据失败,将使用空数据: {exc}")
            return self.new_store()

    def save_store(self, data: dict[str, Any]) -> None:
        self._atomic_write_data_file_sync(data)

    def save_snapshot(self, data: dict[str, Any]) -> None:
        self._atomic_write_data_file_sync(data)

    def health_check(self) -> dict[str, Any]:
        return {
            "backend": self.backend_name(),
            "path": str(self.data_file),
            "exists": self.exists(),
            "writable": self.data_file.parent.exists(),
        }

    def _atomic_write_data_file_sync(self, data: dict[str, Any]) -> None:
        base = str(self.data_file)
        tmp_file = f"{base}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp"
        try:
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError:
                    pass
            last_exc: Exception | None = None
            for attempt in range(6):
                try:
                    os.replace(tmp_file, base)
                    return
                except PermissionError as exc:
                    last_exc = exc
                    time.sleep(0.05 * (attempt + 1))
                except OSError as exc:
                    last_exc = exc
                    if getattr(exc, "winerror", 0) not in {32, 33, 5}:
                        raise
                    time.sleep(0.05 * (attempt + 1))
            if last_exc:
                raise last_exc
        finally:
            try:
                if os.path.exists(tmp_file):
                    os.remove(tmp_file)
            except Exception:
                pass
