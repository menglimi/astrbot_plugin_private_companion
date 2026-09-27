# -*- coding: utf-8 -*-
"""bookshelf_remaining 域。

由 tools/split_mixin_domain.py 从 page_api.py 机械抽取（3 个方法 + 0 个模块级名字 + 0 个类级赋值 / 40 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApi）。
"""
from __future__ import annotations

import base64
import re
from pathlib import Path
from typing import Any



class PrivateCompanionPageApiBookshelfRemainingMixin:
    """bookshelf_remaining 域（从 PrivateCompanionPageApi 拆出）。"""


    def _remember_deleted_diary_day(self, date_key: str) -> None:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_key):
            return
        stored = self.plugin.data.get("daily_diary_deleted_days")
        values = stored if isinstance(stored, list) else []
        normalized: list[str] = []
        for value in [*values, date_key]:
            day = self._bookshelf_diary_date_key(value)
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) and day not in normalized:
                normalized.append(day)
        self.plugin.data["daily_diary_deleted_days"] = normalized[-90:]
        try:
            revision = max(0, int(self.plugin.data.get("daily_diary_delete_revision") or 0))
        except (TypeError, ValueError, OverflowError):
            revision = 0
        self.plugin.data["daily_diary_delete_revision"] = revision + 1

    async def _read_file_base64(self, path: Path) -> str:
        import asyncio

        raw = await asyncio.to_thread(path.read_bytes)
        return base64.b64encode(raw).decode("ascii")

    def _archive_item_comment_sample(self, item: dict[str, Any]) -> tuple[Path | None, list[Path], list[int]]:
        cover_path = self._resolve_bookshelf_data_file(item.get("cover_path"))
        pages = item.get("pages") if isinstance(item.get("pages"), list) else []
        page_by_index: dict[int, Path] = {}
        for page in pages:
            if not isinstance(page, dict):
                continue
            page_index = self._int(page.get("index"))
            page_path = self._resolve_bookshelf_data_file(page.get("path"))
            if page_index > 0 and page_path:
                page_by_index[page_index] = page_path
        sampled_pages = [
            self._int(page)
            for page in (item.get("sampled_pages") if isinstance(item.get("sampled_pages"), list) else [])
            if self._int(page) > 0 and self._int(page) in page_by_index
        ][:5]
        if not sampled_pages:
            sampled_pages = sorted(page_by_index)[:5]
        return cover_path, [page_by_index[page] for page in sampled_pages if page in page_by_index], sampled_pages
