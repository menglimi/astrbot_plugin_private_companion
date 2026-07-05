# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any


class StoreBackendBase:
    def backend_name(self) -> str:
        raise NotImplementedError

    def load_store(self) -> dict[str, Any]:
        raise NotImplementedError

    def save_store(self, data: dict[str, Any]) -> None:
        raise NotImplementedError

    def save_snapshot(self, data: dict[str, Any]) -> None:
        self.save_store(data)

    def exists(self) -> bool:
        raise NotImplementedError

    def initialize_empty_store(self, default_data: dict[str, Any]) -> None:
        self.save_store(default_data)

    def health_check(self) -> dict[str, Any]:
        raise NotImplementedError

    def export_store(self) -> dict[str, Any]:
        return self.load_store()

    def import_store(self, data: dict[str, Any], mode: str = "replace") -> None:
        self.save_store(data)

    def close(self) -> None:
        return None
