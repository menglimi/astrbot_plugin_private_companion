# -*- coding: utf-8 -*-
"""PrivateCompanionPageApiBookshelfPart02Mixin。

由 tools/split_mixin_domain.py 从 page_api_bookshelf.py 机械抽取（10 个方法 + 0 个模块级名字 + 0 个类级赋值 / 487 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApiBookshelfMixin）。
"""
from __future__ import annotations

from .page_api_bookshelf_shared import logger
from .page_api_bookshelf_shared import Any
from .page_api_bookshelf_shared import Path
from .page_api_bookshelf_shared import _path_text
from .page_api_bookshelf_shared import deepcopy
from .page_api_bookshelf_shared import re
from .page_api_bookshelf_shared import request
from .page_api_bookshelf_shared import shutil
from .page_api_bookshelf_shared import story_authority_controller
from .page_api_bookshelf_shared import time



class PrivateCompanionPageApiBookshelfPart02Mixin:
    """PrivateCompanionPageApiBookshelfPart02Mixin（从 PrivateCompanionPageApiBookshelfMixin 拆出）。"""


    async def delete_bookshelf_item(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        access_token = (self._bookshelf_request_token(payload))
        if not self._bookshelf_access_token_valid(access_token):
            return self._error(self._bookshelf_access_error()["error"])
        kind = self._single_line(payload.get("kind"), 32)
        item_id = self._single_line(payload.get("id"), 80)
        album_payload_id = self._single_line(payload.get("album_id"), 80)
        title_payload = self._single_line(payload.get("title"), 120)
        date_key = self._single_line(payload.get("date"), 32)
        diary_date_key = self._bookshelf_diary_date_key(date_key)
        diary_entry_key = self._single_line(payload.get("entry_key") or payload.get("diary_key"), 80)
        story_authority_identity: Any | None = None
        if kind == "creative":
            story_authority_identity = (
                story_authority_controller().enter_legacy_operation(
                    "page.bookshelf.creative-delete"
                )
            )
        try:
            async with self.plugin._data_lock:
                changed = False
                changed_sections: set[str]
                if kind == "creative":
                    changed_sections = {"creative_projects"}
                    if not item_id:
                        return self._error("缺少要删除的创作标识")
                    projects = self.plugin.data.get("creative_projects", [])
                    if not isinstance(projects, list):
                        return self._error("创作记录结构异常，已停止删除以避免覆盖原数据")
                    before = len(projects)
                    kept_projects = [
                        item
                        for item in projects
                        if not (isinstance(item, dict) and self._single_line(item.get("id"), 80) == item_id)
                    ]
                    changed = len(kept_projects) != before
                    if changed:
                        self.plugin.data["creative_projects"] = kept_projects
                elif kind == "diary":
                    changed_sections = {
                        "bot_diaries",
                        "daily_diary_deleted_days",
                        "daily_diary_delete_revision",
                    }
                    if not diary_entry_key and not diary_date_key:
                        return self._error("缺少要删除的日记标识")
                    diaries = self.plugin.data.get("bot_diaries", [])
                    storage_type = type(diaries).__name__
                    deleted_dates: set[str] = set()
                    entry_key_occurrences: dict[str, int] = {}

                    def should_delete(item: Any, fallback_date: Any = "") -> bool:
                        candidate_date = self._bookshelf_diary_date_key(
                            (item.get("date") or fallback_date) if isinstance(item, dict) else fallback_date
                        )
                        if diary_entry_key:
                            candidate_entry_key = self._bookshelf_next_diary_entry_key(
                                item,
                                fallback_date,
                                entry_key_occurrences,
                            )
                            matched = candidate_entry_key == diary_entry_key
                        elif diary_date_key == "某天":
                            matched = not candidate_date
                        else:
                            matched = candidate_date == diary_date_key
                        if matched and re.fullmatch(r"\d{4}-\d{2}-\d{2}", candidate_date):
                            deleted_dates.add(candidate_date)
                        return matched

                    if isinstance(diaries, list):
                        kept = [item for item in diaries if not should_delete(item)]
                        changed = len(kept) != len(diaries)
                        if changed:
                            self.plugin.data["bot_diaries"] = kept
                    elif isinstance(diaries, dict):
                        kept_dict: dict[Any, Any] = {}
                        for stored_date, item in diaries.items():
                            if should_delete(item, stored_date):
                                changed = True
                                continue
                            kept_dict[stored_date] = item
                        if changed:
                            self.plugin.data["bot_diaries"] = kept_dict
                    else:
                        return self._error("日记记录结构异常，已停止删除以避免覆盖原数据")
                    if changed:
                        if not deleted_dates and re.fullmatch(r"\d{4}-\d{2}-\d{2}", diary_date_key):
                            deleted_dates.add(diary_date_key)
                        for deleted_date in sorted(deleted_dates):
                            self._remember_deleted_diary_day(deleted_date)
                    remaining = len(self.plugin.data.get("bot_diaries", []))
                    logger.info(
                        "日记删除: changed=%s date=%s entry=%s storage=%s remaining=%s",
                        changed,
                        diary_date_key,
                        diary_entry_key,
                        storage_type,
                        remaining,
                    )
                elif kind == "archive_item":
                    changed_sections = {
                        "bookshelf_items",
                        "reading_archive_integration",
                    }
                    album_id = album_payload_id or item_id.removeprefix("archive-")
                    album_id = album_id.removeprefix("archive-").removeprefix("archive_item:")
                    match_keys = {
                        value
                        for value in {
                            album_id,
                            item_id,
                            item_id.removeprefix("archive-"),
                            f"archive-{album_id}" if album_id else "",
                            f"archive_item:{album_id}" if album_id else "",
                        }
                        if value
                    }
                    items = self.plugin.data.get("bookshelf_items")
                    if not isinstance(items, list):
                        return self._error("夹层记录结构异常，已停止删除以避免覆盖原数据")
                    removed_pages: list[dict[str, Any]] = []
                    removed_album_ids: set[str] = set()
                    kept = []
                    for item in items:
                        if not self._is_bookshelf_archive_item(item):
                            kept.append(item)
                            continue
                        item_values = {
                            self._bookshelf_album_id(item),
                            self._single_line(item.get("id"), 80),
                            self._single_line(item.get("key"), 100),
                        }
                        title_matched = bool(
                            not match_keys
                            and title_payload
                            and self._single_line(item.get("title"), 120) == title_payload
                        )
                        if match_keys.intersection(value for value in item_values if value) or title_matched:
                            removed_album_id = self._bookshelf_album_id(item)
                            if removed_album_id:
                                removed_album_ids.add(removed_album_id)
                            if isinstance(item.get("pages"), list):
                                removed_pages.extend(page for page in item.get("pages", []) if isinstance(page, dict))
                            changed = True
                            continue
                        kept.append(item)
                    self.plugin.data["bookshelf_items"] = kept
                    state = self.plugin.data.get("reading_archive_integration")
                    if isinstance(state, dict):
                        last_album = state.get("last_album")
                        last_values = {
                            self._single_line(last_album.get("id"), 80),
                            self._single_line(last_album.get("album_id"), 80),
                            self._single_line(last_album.get("key"), 100),
                        } if isinstance(last_album, dict) else set()
                        last_title_matched = bool(
                            isinstance(last_album, dict)
                            and not match_keys
                            and title_payload
                            and self._single_line(last_album.get("title"), 120) == title_payload
                        )
                        if isinstance(last_album, dict) and (match_keys.intersection(value for value in last_values if value) or last_title_matched):
                            removed_album_id = self._single_line(last_album.get("id") or last_album.get("album_id"), 80)
                            if removed_album_id:
                                removed_album_ids.add(removed_album_id)
                            state["last_album"] = {}
                            changed = True
                    if not changed and album_id:
                        data_root = Path(str(getattr(self.plugin, "data_dir", ""))).resolve()
                        if (data_root / "bookshelf_pages" / album_id).exists():
                            removed_album_ids.add(album_id)
                            changed = True
                    if changed and (removed_album_ids or title_payload):
                        state = self.plugin.data.setdefault("reading_archive_integration", {})
                        if not isinstance(state, dict):
                            state = {}
                            self.plugin.data["reading_archive_integration"] = state
                        deleted_ids = state.setdefault("deleted_album_ids", [])
                        if not isinstance(deleted_ids, list):
                            deleted_ids = []
                            state["deleted_album_ids"] = deleted_ids
                        for removed_id in sorted(removed_album_ids):
                            if removed_id and removed_id not in deleted_ids:
                                deleted_ids.append(removed_id)
                        del deleted_ids[:-300]
                        deleted_titles = state.setdefault("deleted_titles", [])
                        if not isinstance(deleted_titles, list):
                            deleted_titles = []
                            state["deleted_titles"] = deleted_titles
                        removed_titles = [
                            self._single_line(item.get("title"), 120)
                            for item in items
                            if self._is_bookshelf_archive_item(item)
                            and self._bookshelf_album_id(item) in removed_album_ids
                        ]
                        if title_payload:
                            removed_titles.append(title_payload)
                        for removed_title in removed_titles:
                            if removed_title and removed_title not in deleted_titles:
                                deleted_titles.append(removed_title)
                        del deleted_titles[:-300]
                    self._cleanup_bookshelf_page_files(removed_pages)
                    self._cleanup_bookshelf_album_dirs(removed_album_ids)
                    logger.info(
                        "资料柜夹层移除: changed=%s id=%s album_id=%s title=%s removed=%s",
                        changed,
                        item_id,
                        album_id,
                        title_payload,
                        sorted(removed_album_ids),
                    )
                else:
                    return self._error("不支持的资料柜项目类型")
                if changed:
                    if kind == "archive_item":
                        self._mark_bookshelf_data_changed()
                    self.plugin._save_data_sync(sections=changed_sections)
                data = deepcopy(self.plugin.data)
            return self._ok({"changed": changed, "bookshelf": await self._bookshelf_summary(data, unlocked=True, access_token=access_token)})
        except Exception as exc:
            logger.error(f"删除资料柜项目失败: {exc}", exc_info=True)
            return self._exception_error(str(exc))
        finally:
            if story_authority_identity is not None:
                story_authority_controller().exit_legacy_operation(
                    story_authority_identity
                )

    def _cleanup_bookshelf_page_files(self, pages: list[dict[str, Any]]) -> None:
        data_root = Path(str(getattr(self.plugin, "data_dir", ""))).resolve()
        touched_dirs: set[Path] = set()
        for page in pages:
            path = Path(str(page.get("path") or "")).resolve()
            try:
                path.relative_to(data_root)
            except ValueError:
                continue
            if not path.exists() or not path.is_file():
                continue
            touched_dirs.add(path.parent)
            try:
                path.unlink()
            except Exception:
                pass
        for folder in touched_dirs:
            try:
                folder.relative_to(data_root / "bookshelf_pages")
            except ValueError:
                continue
            try:
                if folder.exists() and not any(folder.iterdir()):
                    shutil.rmtree(folder, ignore_errors=True)
            except Exception:
                pass

    def _cleanup_bookshelf_album_dirs(self, album_ids: set[str]) -> None:
        if not album_ids:
            return
        data_root = Path(str(getattr(self.plugin, "data_dir", ""))).resolve()
        page_root = data_root / "bookshelf_pages"
        for album_id in album_ids:
            safe_id = re.sub(r"[^0-9A-Za-z_.-]+", "_", str(album_id or ""))
            if not safe_id:
                continue
            folder = (page_root / safe_id).resolve()
            try:
                folder.relative_to(page_root.resolve())
            except ValueError:
                continue
            try:
                shutil.rmtree(folder, ignore_errors=True)
            except Exception:
                pass

    async def update_bookshelf_reading_state(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        access_token = (self._bookshelf_request_token(payload))
        if not self._bookshelf_access_token_valid(access_token):
            return self._error(self._bookshelf_access_error()["error"])
        album_id = self._single_line(payload.get("album_id") or payload.get("id"), 32)
        page = max(1, self._int(payload.get("page")))
        total_pages = max(0, self._int(payload.get("total_pages")))
        bookmark = self._single_line(payload.get("bookmark"), 120)
        if not album_id:
            return self._error("缺少 album_id")
        try:
            async with self.plugin._data_lock:
                items = self.plugin.data.get("bookshelf_items")
                if not isinstance(items, list):
                    return self._error("夹层记录结构异常，未写入阅读进度")
                target = next(
                    (
                        item for item in items
                        if self._is_bookshelf_archive_item(item)
                        and self._bookshelf_album_id(item, limit=32) == album_id
                    ),
                    None,
                )
                if target is None:
                    return self._error("没有找到这本资料归档记录")
                safe_total = total_pages or max(0, self._int(target.get("image_count")))
                if safe_total > 0:
                    page = min(page, safe_total)
                target["reading_progress_page"] = page
                target["reading_progress_total"] = safe_total
                target["reading_progress_updated_at"] = time.time()
                target["reading_started_at"] = self._float(target.get("reading_started_at")) or time.time()
                if bookmark:
                    target["reading_bookmark"] = bookmark
                elif "bookmark" in payload:
                    target["reading_bookmark"] = ""
                if safe_total > 0 and page >= safe_total:
                    target["reading_completed_at"] = self._float(target.get("reading_completed_at")) or time.time()
                self._mark_bookshelf_data_changed()
                self.plugin._save_data_sync(sections={"bookshelf_items"})
                data = deepcopy(self.plugin.data)
            return self._ok({"bookshelf": await self._bookshelf_summary(data, unlocked=True, access_token=access_token)})
        except Exception as exc:
            logger.error(f"更新资料柜阅读进度失败: {exc}", exc_info=True)
            return self._exception_error(str(exc))

    async def rate_bookshelf_item(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        access_token = (self._bookshelf_request_token(payload))
        if not self._bookshelf_access_token_valid(access_token):
            return self._error(self._bookshelf_access_error()["error"])
        album_id = self._single_line(payload.get("album_id") or payload.get("id"), 32)
        rating = self._int(payload.get("rating"))
        reason = self._single_line(payload.get("reason"), 160)
        if not album_id:
            return self._error("缺少 album_id")
        if rating < 1 or rating > 10:
            return self._error("评分必须是 1 到 10")
        try:
            async with self.plugin._data_lock:
                items = self.plugin.data.get("bookshelf_items")
                if not isinstance(items, list):
                    return self._error("夹层记录结构异常，未写入评分")
                target: dict[str, Any] | None = None
                for item in items:
                    if not self._is_bookshelf_archive_item(item):
                        continue
                    if self._bookshelf_album_id(item) == album_id:
                        item["user_rating"] = rating
                        item["user_rating_reason"] = reason
                        item["user_rated_ts"] = time.time()
                        target = item
                        break
                state = self.plugin.data.setdefault("reading_archive_integration", {})
                if isinstance(state, dict):
                    last_album = state.get("last_album")
                    if isinstance(last_album, dict) and str(last_album.get("id") or last_album.get("album_id") or "") == album_id:
                        last_album["user_rating"] = rating
                        last_album["user_rating_reason"] = reason
                        last_album["user_rated_ts"] = time.time()
                        if target is None:
                            target = last_album
                if target is None:
                    return self._error("没有找到这条资料归档记录")
                updater = getattr(self.plugin, "_update_reading_archive_preference_profile", None)
                if callable(updater):
                    updater(target)
                self._mark_bookshelf_data_changed()
                self.plugin._save_data_sync(sections={"bookshelf_items"})
                data = deepcopy(self.plugin.data)
            return self._ok({"bookshelf": await self._bookshelf_summary(data, unlocked=True, access_token=access_token)})
        except Exception as exc:
            logger.error(f"保存资料归档评分失败: {exc}", exc_info=True)
            return self._exception_error(str(exc))

    def _normalize_bookshelf_tag_list(self, value: Any, *, limit: int = 8) -> list[str]:
        raw_items: list[Any]
        if isinstance(value, str):
            raw_items = re.split(r"[,，、\s\n\r]+", value)
        elif isinstance(value, list):
            raw_items = value
        else:
            raw_items = []
        tags: list[str] = []
        seen: set[str] = set()
        for raw in raw_items:
            tag = self._single_line(raw, 24)
            if not tag:
                continue
            normalized = tag.casefold()
            if normalized in seen:
                continue
            seen.add(normalized)
            tags.append(tag)
            if len(tags) >= limit:
                break
        return tags

    def _normalize_bookshelf_page_comment(self, value: Any, *, limit: int = 100) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        page_no = self._int(value.get("page"))
        comment_text = self._single_line(value.get("comment"), limit)
        if page_no <= 0 or not comment_text:
            return None
        return {
            "page": page_no,
            "comment": comment_text,
            "raw_page": self._int(value.get("raw_page")),
            "sample_order": self._int(
                value.get("sample_order")
                or value.get("sample_index")
                or value.get("reference_index")
                or value.get("image_index")
            ),
        }

    def _merge_bookshelf_page_comments(self, *sources: Any, limit: int = 24) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        seen: set[tuple[int, str]] = set()
        for source in sources:
            if not isinstance(source, list):
                continue
            for item in source:
                normalized = self._normalize_bookshelf_page_comment(item)
                if not normalized:
                    continue
                key = (normalized["page"], normalized["comment"])
                if key in seen:
                    continue
                seen.add(key)
                merged.append(normalized)
                if len(merged) >= limit:
                    return merged
        return merged

    async def update_bookshelf_item_tags(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        access_token = (self._bookshelf_request_token(payload))
        if not self._bookshelf_access_token_valid(access_token):
            return self._error(self._bookshelf_access_error()["error"])
        album_id = self._single_line(payload.get("album_id") or payload.get("id"), 32)
        liked_tags = self._normalize_bookshelf_tag_list(payload.get("liked_tags"))
        disliked_tags_raw = self._normalize_bookshelf_tag_list(payload.get("disliked_tags"))
        liked_seen = {tag.casefold() for tag in liked_tags}
        disliked_tags = [tag for tag in disliked_tags_raw if tag.casefold() not in liked_seen]
        if not album_id:
            return self._error("缺少 album_id")
        try:
            async with self.plugin._data_lock:
                items = self.plugin.data.get("bookshelf_items")
                if not isinstance(items, list):
                    return self._error("夹层记录结构异常，未写入标签")
                target: dict[str, Any] | None = None
                for item in items:
                    if not self._is_bookshelf_archive_item(item):
                        continue
                    if self._bookshelf_album_id(item) == album_id:
                        item["user_liked_tags"] = liked_tags
                        item["user_disliked_tags"] = disliked_tags
                        item["user_tags_updated_ts"] = time.time()
                        target = item
                        break
                state = self.plugin.data.setdefault("reading_archive_integration", {})
                if isinstance(state, dict):
                    last_album = state.get("last_album")
                    if isinstance(last_album, dict) and str(last_album.get("id") or last_album.get("album_id") or "") == album_id:
                        last_album["user_liked_tags"] = liked_tags
                        last_album["user_disliked_tags"] = disliked_tags
                        last_album["user_tags_updated_ts"] = time.time()
                        if target is None:
                            target = last_album
                if target is None:
                    return self._error("没有找到这条资料归档记录")
                updater = getattr(self.plugin, "_update_reading_archive_preference_profile", None)
                if callable(updater):
                    updater(target)
                self._mark_bookshelf_data_changed()
                self.plugin._save_data_sync(sections={"bookshelf_items"})
                data = deepcopy(self.plugin.data)
            return self._ok({"bookshelf": await self._bookshelf_summary(data, unlocked=True, access_token=access_token)})
        except Exception as exc:
            logger.error(f"保存资料归档标签失败: {exc}", exc_info=True)
            return self._exception_error(str(exc))

    def _resolve_bookshelf_data_file(self, value: Any) -> Path | None:
        path_text = _path_text(value, 1000)
        if not path_text:
            return None
        data_root = Path(str(getattr(self.plugin, "data_dir", ""))).resolve()
        try:
            raw_path = Path(path_text)
            path = raw_path.resolve() if raw_path.is_absolute() else (data_root / raw_path).resolve()
            path.relative_to(data_root)
            if path.exists() and path.is_file():
                return path
        except Exception:
            return None
        return None
