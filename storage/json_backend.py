# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import threading
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Callable


from .backend_base import StoreBackendBase
from .path_generation import capture_write_ticket, replace_if_ticket_current


class JsonStoreBackend(StoreBackendBase):
    def __init__(
        self,
        data_file: str | Path,
        ensure_defaults: Callable[[dict[str, Any]], dict[str, Any]],
        new_store: Callable[[], dict[str, Any]],
        *,
        persistence_owner_token: str = "",
    ) -> None:
        self.data_file = Path(data_file)
        self.ensure_defaults = ensure_defaults
        self.new_store = new_store
        self.persistence_owner_token = str(persistence_owner_token or "").strip()
        self.last_write_status: dict[str, Any] = {
            "accepted": None,
            "state": "idle",
            "path": str(self.data_file),
        }

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
                raise ValueError("JSON store root must be an object")
            return self.ensure_defaults(data)
        except Exception as exc:
            logger.warning(
                "读取 JSON 数据失败,已保留原文件并中止加载: %s",
                exc,
            )
            raise

    def capture_write_ticket(self) -> dict[str, Any]:
        return capture_write_ticket(self.data_file, self.persistence_owner_token)

    def save_store(
        self,
        data: dict[str, Any],
        *,
        write_ticket: dict[str, Any] | None = None,
    ) -> None:
        self._atomic_write_data_file_sync(data, write_ticket=write_ticket)

    def save_snapshot(
        self,
        data: dict[str, Any],
        *,
        minimum_revision: int | None = None,
        deleted_sections: Mapping[str, int] | None = None,
        preserve_tombstones: bool = False,
        write_ticket: dict[str, Any] | None = None,
    ) -> int | None:
        self._atomic_write_data_file_sync(data, write_ticket=write_ticket)
        return None

    def health_check(self, *, raise_on_error: bool = False) -> dict[str, Any]:
        return {
            "backend": self.backend_name(),
            "path": str(self.data_file),
            "exists": self.exists(),
            "writable": self.data_file.parent.exists(),
        }

    def _atomic_write_data_file_sync(
        self,
        data: dict[str, Any],
        *,
        write_ticket: dict[str, Any] | None = None,
    ) -> bool:
        base = str(self.data_file)
        ticket = write_ticket or self.capture_write_ticket()
        tmp_file = (
            f"{base}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp"
        )
        try:
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError:
                    pass
            accepted = replace_if_ticket_current(tmp_file, base, ticket)
            self.last_write_status = {
                "accepted": accepted,
                "state": "saved" if accepted else "superseded",
                "path": str(self.data_file),
                "owner": str(ticket.get("owner") or ""),
                "generation": int(ticket.get("generation") or 0),
                "sequence": int(ticket.get("sequence") or 0),
            }
            if not accepted:
                logger.info(
                    "JSON persistence skipped because its generation was superseded: path=%s",
                    self.data_file,
                )
            return accepted
        except Exception:
            self.last_write_status = {
                "accepted": False,
                "state": "failed",
                "path": str(self.data_file),
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
