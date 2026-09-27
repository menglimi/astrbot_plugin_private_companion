# -*- coding: utf-8 -*-
"""util_small 域。

由 tools/split_mixin_domain.py 从 page_api.py 机械抽取（10 个方法 + 0 个模块级名字 + 0 个类级赋值 / 80 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApi）。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from .page_api_shared import _page_api_host, _page_api_host_request as request
from typing import Any



class PrivateCompanionPageApiUtilSmallMixin:
    """util_small 域（从 PrivateCompanionPageApi 拆出）。"""


    async def _to_thread_sqlite_inspect(self, func: Any, path: Path) -> dict[str, Any]:
        try:
            import asyncio

            return await asyncio.to_thread(func, path)
        except Exception:
            return func(path)

    @staticmethod
    def _int(value: Any, default: int = 0, minimum: int | None = None, maximum: int | None = None) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = default
        if minimum is not None:
            parsed = max(minimum, parsed)
        if maximum is not None:
            parsed = min(maximum, parsed)
        return parsed

    @staticmethod
    def _float(value: Any, default: float = 0.0, minimum: float | None = None, maximum: float | None = None) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            parsed = default
        if minimum is not None:
            parsed = max(minimum, parsed)
        if maximum is not None:
            parsed = min(maximum, parsed)
        return parsed

    @staticmethod
    def _sqlite_like_pattern(token: str) -> str:
        escaped = str(token).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return f"%{escaped}%"

    @staticmethod
    def _json_dict(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        if not isinstance(value, str) or not value.strip():
            return {}
        try:
            parsed = json.loads(value)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _json_list(value: Any) -> list[Any]:
        if isinstance(value, list):
            return value
        if not isinstance(value, str) or not value.strip():
            return []
        try:
            parsed = json.loads(value)
        except Exception:
            return []
        return parsed if isinstance(parsed, list) else []

    @staticmethod
    def _coerce_float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _sqlite_table_exists(conn: sqlite3.Connection, table: str) -> bool:
        row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (table,)).fetchone()
        return row is not None

    @staticmethod
    def _query_int(name: str, default: int, minimum: int, maximum: int) -> int:
        raw = request.args.get(name, default)
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = default
        return max(minimum, min(maximum, value))

    @staticmethod
    def _clamp_int(raw: Any, default: int, minimum: int, maximum: int) -> int:
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = default
        return max(minimum, min(maximum, value))
