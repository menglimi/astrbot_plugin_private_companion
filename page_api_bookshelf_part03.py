# -*- coding: utf-8 -*-
"""PrivateCompanionPageApiBookshelfPart03Mixin。

由 tools/split_mixin_domain.py 从 page_api_bookshelf.py 机械抽取（3 个方法 + 0 个模块级名字 + 0 个类级赋值 / 154 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApiBookshelfMixin）。
"""
from __future__ import annotations

from .page_api_bookshelf_shared import logger
from .page_api_bookshelf_shared import Any
from .page_api_bookshelf_shared import Path
from .page_api_bookshelf_shared import _path_text
from .page_api_bookshelf_shared import deepcopy
from .page_api_bookshelf_shared import request
from .page_api_bookshelf_shared import time



class PrivateCompanionPageApiBookshelfPart03Mixin:
    """PrivateCompanionPageApiBookshelfPart03Mixin（从 PrivateCompanionPageApiBookshelfMixin 拆出）。"""


    async def update_bookshelf_item_comments(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        access_token = (self._bookshelf_request_token(payload))
        if not self._bookshelf_access_token_valid(access_token):
            return self._error(self._bookshelf_access_error()["error"])
        album_id = self._single_line(payload.get("album_id") or payload.get("id"), 32)
        if not album_id:
            return self._error("缺少 album_id")
        try:
            async with self.plugin._data_lock:
                items = self.plugin.data.get("bookshelf_items")
                if not isinstance(items, list):
                    return self._error("夹层记录结构异常，未读取或覆盖原数据")
                target = next(
                    (
                        item
                        for item in items
                        if self._is_bookshelf_archive_item(item)
                        and self._bookshelf_album_id(item) == album_id
                    ),
                    None,
                )
                if target is None:
                    state = self.plugin.data.get("reading_archive_integration") if isinstance(self.plugin.data.get("reading_archive_integration"), dict) else {}
                    last_album = state.get("last_album") if isinstance(state.get("last_album"), dict) else None
                    if last_album and str(last_album.get("id") or last_album.get("album_id") or "") == album_id:
                        target = last_album
                if target is None:
                    return self._error("没有找到这条资料归档记录")
                target_snapshot = deepcopy(target)
            cover_path, page_paths, sampled_pages = self._archive_item_comment_sample(target_snapshot)
            if not page_paths:
                return self._error("没有找到可用于重读的本地图片")
            vision = getattr(self.plugin, "_call_reading_archive_vision", None)
            if not callable(vision):
                return self._error("当前插件版本不支持让 Bot 重读")
            vision_result = await vision(cover_path, target_snapshot, page_paths=page_paths, sampled_pages=sampled_pages)
            if not isinstance(vision_result, dict) or not (vision_result.get("impression") or vision_result.get("page_comments")):
                return self._error("这次没有生成新的读后感或批注")
            updates: dict[str, Any] = {
                "comments_updated_ts": time.time(),
                "sampled_pages": sampled_pages,
            }
            impression = self._single_line(vision_result.get("impression"), 600)
            if impression:
                updates["impression"] = impression
                updates["reading_impression"] = impression
            rating = self._int(vision_result.get("rating"))
            if 1 <= rating <= 10:
                updates["rating"] = rating
            rating_reason = self._single_line(vision_result.get("rating_reason"), 160)
            if rating_reason:
                updates["rating_reason"] = rating_reason
            preference_tags = self._normalize_bookshelf_tag_list(vision_result.get("preference_tags"))
            if preference_tags:
                updates["preference_tags"] = preference_tags
            page_comments = vision_result.get("page_comments") if isinstance(vision_result.get("page_comments"), list) else []
            normalized_comments: list[dict[str, Any]] = []
            for comment in page_comments[:8]:
                normalized = self._normalize_bookshelf_page_comment(comment, limit=80)
                if normalized:
                    normalized_comments.append(normalized)
            if normalized_comments:
                existing_comments = target_snapshot.get("page_comments") if isinstance(target_snapshot.get("page_comments"), list) else []
                previous_comments = (
                    target_snapshot.get("page_comments_previous")
                    if isinstance(target_snapshot.get("page_comments_previous"), list)
                    else []
                )
                updates["page_comments"] = self._merge_bookshelf_page_comments(
                    existing_comments,
                    normalized_comments,
                    previous_comments,
                    limit=24,
                )
                updates["page_comments_previous"] = existing_comments[:12]
            async with self.plugin._data_lock:
                items = self.plugin.data.get("bookshelf_items")
                if not isinstance(items, list):
                    return self._error("夹层记录结构异常，未写入批注")
                written = False
                for item in items:
                    if not self._is_bookshelf_archive_item(item):
                        continue
                    if self._bookshelf_album_id(item) == album_id:
                        item.update(updates)
                        target = item
                        written = True
                        break
                state = self.plugin.data.setdefault("reading_archive_integration", {})
                if isinstance(state, dict):
                    last_album = state.get("last_album")
                    if isinstance(last_album, dict) and str(last_album.get("id") or last_album.get("album_id") or "") == album_id:
                        last_album.update(updates)
                        if not written:
                            target = last_album
                            written = True
                if not written:
                    return self._error("没有找到可写回的资料归档记录")
                updater = getattr(self.plugin, "_update_reading_archive_preference_profile", None)
                if callable(updater):
                    updater(target)
                self._mark_bookshelf_data_changed()
                self.plugin._save_data_sync(sections={"bookshelf_items"})
                data = deepcopy(self.plugin.data)
            return self._ok({"message": "Bot 已重新读过并更新读后感", "bookshelf": await self._bookshelf_summary(data, unlocked=True, access_token=access_token)})
        except Exception as exc:
            logger.error(f"更新资料归档批注失败: {exc}", exc_info=True)
            return self._exception_error(str(exc))

    def _reading_archive_summary(self, data: dict[str, Any]) -> dict[str, Any]:
        # The public package exposes no external or page-level reading source.
        state = {}
        album = {}
        available = False
        return {
            "enabled": bool(available and getattr(self.plugin, "enable_reading_archive_integration", False)),
            "boredom_read_enabled": bool(
                available and getattr(self.plugin, "enable_reading_archive_boredom_read", False)
            ),
            "ask_recommendation_enabled": bool(available and getattr(self.plugin, "enable_reading_archive_ask_recommendation", False)),
            "available": available,
            "last_read_at": "",
            "last_status": "disabled",
            "last_keyword": "",
            "last_album": {
                "id": self._single_line(album.get("id"), 32),
                "title": self._single_line(album.get("title"), 100),
                "impression": self._single_line(album.get("impression"), 160),
                "rating": self._int(album.get("rating")),
                "user_rating": self._int(album.get("user_rating")),
            },
        }

    def _bookshelf_cover_url(
        self,
        album_id: str,
        item: dict[str, Any],
        page_items: list[dict[str, Any]],
        data_root: Path,
        *,
        access_token: str = "",
    ) -> str:
        cover_path = _path_text(item.get("cover_path"), 1000)
        if cover_path:
            return self._bookshelf_image_url(
                album_id,
                data_root=data_root,
                cover=True,
                path_value=cover_path,
                access_token=access_token,
            )
        first_page = page_items[0] if page_items else {}
        if isinstance(first_page, dict):
            return self._single_line(first_page.get("src"), 500)
        return ""
