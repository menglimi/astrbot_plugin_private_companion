# -*- coding: utf-8 -*-
"""PrivateCompanionPageApiBookshelfPart04Mixin。

由 tools/split_mixin_domain.py 从 page_api_bookshelf.py 机械抽取（1 个方法 + 0 个模块级名字 + 0 个类级赋值 / 418 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApiBookshelfMixin）。
"""
from __future__ import annotations

from .page_api_bookshelf_shared import logger
from .page_api_bookshelf_shared import Any
from .page_api_bookshelf_shared import Path
from .page_api_bookshelf_shared import _text_similarity
from .page_api_bookshelf_shared import time



class PrivateCompanionPageApiBookshelfPart04Mixin:
    """PrivateCompanionPageApiBookshelfPart04Mixin（从 PrivateCompanionPageApiBookshelfMixin 拆出）。"""


    async def _bookshelf_summary(self, data: dict[str, Any], *, unlocked: bool, access_token: str = "") -> dict[str, Any]:
        if unlocked and False:
            recoverer = getattr(self.plugin, "_recover_bookshelf_items_from_local_pages_inplace", None)
            if callable(recoverer):
                try:
                    recoverer(data)
                except Exception as exc:
                    logger.debug("夹层响应内本地书页恢复失败: %s", self._single_line(exc, 160))
        projects = data.get("creative_projects") if isinstance(data.get("creative_projects"), list) else []
        diaries = self._bookshelf_diary_entries(data.get("bot_diaries"))
        shelf_items = data.get("bookshelf_items") if isinstance(data.get("bookshelf_items"), list) else []
        archive_state = data.get("reading_archive_integration") if isinstance(data.get("reading_archive_integration"), dict) else {}
        deleted_archive_ids = self._bookshelf_deleted_album_ids(archive_state)
        archive_items = [
            item
            for item in shelf_items
            if self._is_bookshelf_archive_item(item)
            and not self._is_deleted_bookshelf_archive_item(item, archive_state)
        ]
        secret_state = data.get("bookshelf_secret") if isinstance(data.get("bookshelf_secret"), dict) else {}
        reason_sanitizer = getattr(self.plugin, "_sanitize_bookshelf_password_reason", None)
        password_hint = ""
        if callable(reason_sanitizer):
            try:
                password_hint = reason_sanitizer(secret_state.get("reason"))
            except Exception:
                password_hint = ""
        else:
            password_hint = self._single_line(secret_state.get("reason"), 80)
        if not password_hint:
            password_hint = "提示会在通过“陪伴 输出夹层密码”生成后显示。"
        last_album = {}
        last_album_id = self._bookshelf_album_id(last_album)
        if (
            last_album
            and last_album_id
            and not self._is_deleted_bookshelf_archive_item(
                {**last_album, "type": last_album.get("type") or "archive_item"},
                archive_state,
            )
            and not any(self._bookshelf_album_id(item) == last_album_id for item in archive_items)
        ):
            archive_items.append(
                {
                    "type": "archive_item",
                    "title": last_album.get("title"),
                    "album_id": last_album_id,
                    "description": last_album.get("description") or last_album.get("intro") or last_album.get("summary"),
                    "keyword": last_album.get("keyword"),
                    "author": last_album.get("author"),
                    "tags": last_album.get("tags"),
                    "photo_count": last_album.get("photo_count"),
                    "impression": last_album.get("impression"),
                    "reading_impression": last_album.get("reading_impression") or last_album.get("impression"),
                    "vision": last_album.get("vision"),
                    "rating": last_album.get("rating"),
                    "rating_reason": last_album.get("rating_reason"),
                    "user_rating": last_album.get("user_rating"),
                    "user_rating_reason": last_album.get("user_rating_reason"),
                    "user_rated_ts": last_album.get("user_rated_ts"),
                    "preference_tags": last_album.get("preference_tags") if isinstance(last_album.get("preference_tags"), list) else [],
                    "user_liked_tags": last_album.get("user_liked_tags") if isinstance(last_album.get("user_liked_tags"), list) else [],
                    "user_disliked_tags": last_album.get("user_disliked_tags") if isinstance(last_album.get("user_disliked_tags"), list) else [],
                    "user_tags_updated_ts": last_album.get("user_tags_updated_ts"),
                    "page_comments": last_album.get("page_comments") if isinstance(last_album.get("page_comments"), list) else [],
                    "image_count": last_album.get("image_count"),
                    "pages": last_album.get("pages") if isinstance(last_album.get("pages"), list) else [],
                    "sampled_pages": last_album.get("sampled_pages") if isinstance(last_album.get("sampled_pages"), list) else [],
                    "created_ts": last_album.get("created_ts"),
                }
            )
        data_root = Path(str(getattr(self.plugin, "data_dir", ""))).resolve()
        covers_root = data_root / "reading_archive_covers"
        if unlocked and False:
            pages_root = data_root / "bookshelf_pages"
            known_archive_ids = {
                self._bookshelf_album_id(item)
                for item in archive_items
                if self._bookshelf_album_id(item)
            }
            preference_history = []
            profile = archive_state.get("preference_profile") if isinstance(archive_state.get("preference_profile"), dict) else {}
            if isinstance(profile.get("history"), list):
                preference_history = [item for item in profile.get("history", []) if isinstance(item, dict)]
            history_by_album: dict[str, dict[str, Any]] = {}
            for item in preference_history:
                album_id = self._single_line(item.get("album_id") or item.get("id"), 80)
                if album_id:
                    history_by_album[album_id] = {**history_by_album.get(album_id, {}), **item}
            try:
                orphan_dirs = [
                    path
                    for path in pages_root.iterdir()
                    if path.is_dir()
                    and self._single_line(path.name, 80)
                    and self._single_line(path.name, 80) not in known_archive_ids
                    and self._single_line(path.name, 80) not in deleted_archive_ids
                ] if pages_root.exists() else []
            except Exception:
                orphan_dirs = []
            for path in orphan_dirs:
                album_id = self._single_line(path.name, 80)
                try:
                    page_files = sorted(
                        file
                        for file in path.iterdir()
                        if file.is_file() and file.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".gif"}
                    )
                except Exception as exc:
                    logger.debug(
                        "跳过无法读取的夹层目录: album=%s error=%s",
                        album_id,
                        self._single_line(exc, 120),
                    )
                    continue
                if not album_id or not page_files:
                    continue
                meta = history_by_album.get(album_id, {})
                created_ts = self._float(meta.get("created_ts")) or max((file.stat().st_mtime for file in page_files), default=0.0)
                cover_path = covers_root / f"{album_id}.jpg"
                archive_items.append(
                    {
                        "type": "archive_item",
                        "album_id": album_id,
                        "title": meta.get("title") or f"资料归档 {album_id}",
                        "description": meta.get("reason") or "",
                        "tags": meta.get("terms") if isinstance(meta.get("terms"), list) else [],
                        "rating": meta.get("bot_rating") or meta.get("rating"),
                        "user_rating": meta.get("user_rating"),
                        "rating_reason": meta.get("reason") or "",
                        "user_rating_reason": meta.get("reason") or "",
                        "cover_path": str(cover_path) if cover_path.exists() else "",
                        "pages": [
                            {
                                "index": index + 1,
                                "path": str(file),
                                "name": file.name,
                            }
                            for index, file in enumerate(page_files)
                        ],
                        "image_count": len(page_files),
                        "created_ts": created_ts,
                        "source": "bookshelf_orphan_recovered",
                        "locked": True,
                    }
                )
        public_books = []
        browsing_entries = self._browsing_history_entries(data)
        for item in [project for project in projects if isinstance(project, dict)][-12:]:
            chunks = item.get("draft_chunks") if isinstance(item.get("draft_chunks"), list) else []
            full_text = "\n\n".join(
                self._single_line(chunk.get("text"), 2000)
                for chunk in chunks
                if isinstance(chunk, dict) and self._single_line(chunk.get("text"), 2000)
            )
            chunk_entries = [
                {
                    "index": index + 1,
                    "text": self._single_line(chunk.get("text"), 2000),
                    "created": self.plugin._format_timestamp_elapsed(chunk.get("created_ts", 0) or chunk.get("created_at", 0)),
                }
                for index, chunk in enumerate(chunks)
                if isinstance(chunk, dict) and self._single_line(chunk.get("text"), 2000)
            ]
            status = self._single_line(item.get("status"), 24)
            progress = f"{self._int(item.get('current_chars'))}/{self._int(item.get('target_chars')) or '-'} 字"
            public_books.append(
                {
                    "id": self._single_line(item.get("id"), 32) or f"creative-{len(public_books)}",
                    "kind": "creative",
                    "category": self._single_line(item.get("work_type"), 30) or "创作",
                    "work_type": self._single_line(item.get("work_type"), 30) or "短篇小说",
                    "title": self._single_line(item.get("title"), 60) or "未定标题",
                    "intro": self._single_line(item.get("premise"), 240) or "这本书还没整理出简介。",
                    "status": status,
                    "tone": self._single_line(item.get("tone"), 40),
                    "point_of_view": self._single_line(item.get("point_of_view"), 40) or "第三人称有限视角",
                    "progress": progress,
                    "content": full_text or self._single_line(chunks[-1].get("text") if chunks else "", 2000) or "这本书还没有正文。",
                    "chunks": chunk_entries,
                    "outline_count": len(item.get("outline") or []) if isinstance(item.get("outline"), list) else 0,
                    "character_count": len(item.get("characters") or []) if isinstance(item.get("characters"), list) else 0,
                    "review_count": len(item.get("quality_reviews") or []) if isinstance(item.get("quality_reviews"), list) else 0,
                    "manual_edit_count": len(item.get("manual_edits") or []) if isinstance(item.get("manual_edits"), list) else 0,
                    "has_story_bible": bool(item.get("story_bible")) if isinstance(item.get("story_bible"), dict) else False,
                    "last_manual_edit_summary": self._single_line(item.get("last_manual_edit_summary"), 120),
                    "created": self.plugin._format_timestamp_elapsed(item.get("created_at", 0)),
                    "cover_src": self._creative_project_cover_url(item),
                    "cover_status": self._single_line(item.get("cover_generation_status"), 24),
                }
            )
        if browsing_entries:
            latest = browsing_entries[-1]
            public_books.append(
                {
                    "id": "browsing-history-main",
                    "kind": "browsing",
                    "category": "浏览记录",
                    "title": "浏览记录",
                    "intro": f"这里收着 {len(browsing_entries)} 条新闻阅读和主动搜索记录。打开后可以选择记录。",
                    "content": self._single_line(latest.get("content"), 2000) or self._single_line(latest.get("intro"), 1200),
                    "entries": browsing_entries,
                    "created": self._single_line(latest.get("generated_at") or latest.get("date"), 32),
                    "progress": f"{len(browsing_entries)} 条记录",
                    "tags": ["新闻阅读", "主动搜索"],
                }
            )
        locked_count = (1 if diaries else 0) + len(archive_items)
        secret_books: list[dict[str, Any]] = []
        if unlocked:
            diary_entries = []
            for item in [entry for entry in diaries if isinstance(entry, dict)][-60:]:
                body = self.plugin._polish_diary_text(item.get("body"), field="body")
                summary = self.plugin._polish_diary_text(item.get("summary"), field="summary")
                share_seed = self.plugin._polish_diary_text(item.get("share_seed"), field="share")
                content = body or "\n\n".join(part for part in (summary, share_seed) if part)
                diary_entries.append(
                    {
                        "entry_key": self._single_line(item.get("entry_key"), 80),
                        "date": self._single_line(item.get("date"), 24) or "某天",
                        "generated_at": self._single_line(item.get("generated_at"), 32),
                        "title": f"{self._single_line(item.get('date'), 24) or '某天'}",
                        "intro": summary or "这一天没有留下摘要。",
                        "content": content or "这一天的日记暂时没有写出正文。",
                        "tags": [self._single_line(tag, 24) for tag in item.get("tags", [])[:8] if self._single_line(tag, 24)]
                        if isinstance(item.get("tags"), list)
                        else [],
                    }
                )
            if diary_entries:
                secret_books.append(
                    {
                        "id": "diary-main",
                        "kind": "diary",
                        "category": "日记",
                        "title": "日记本",
                        "intro": f"这里收着 {len(diary_entries)} 天的日记。打开后可以选择日期。",
                        "content": diary_entries[-1].get("content") or "这本日记暂时没有可读内容。",
                        "entries": diary_entries,
                        "created": diary_entries[-1].get("generated_at", ""),
                    }
            )
            recent_archive_items = sorted(
                archive_items,
                key=lambda item: self._float(item.get("created_ts") or item.get("created_at") or item.get("ts")),
                reverse=True,
            )[:80]
            for item in recent_archive_items:
                album_id = self._bookshelf_album_id(item, limit=32)
                pages = item.get("pages") if isinstance(item.get("pages"), list) else []
                reading_impression = self._single_line(item.get("reading_impression") or item.get("impression"), 1000)
                vision_impression = self._single_line(item.get("vision"), 1000)
                show_vision_impression = bool(
                    vision_impression
                    and (
                        not reading_impression
                        or _text_similarity(vision_impression, reading_impression) < 0.72
                    )
                )
                bot_rating = self._int(item.get("rating"))
                user_rating = self._int(item.get("user_rating"))
                rating_reason = self._single_line(item.get("rating_reason"), 180)
                user_rating_reason = self._single_line(item.get("user_rating_reason"), 180)
                album_description = self._single_line(
                    item.get("description")
                    or item.get("intro")
                    or item.get("summary")
                    or item.get("desc"),
                    600,
                )
                if not album_description:
                    detail_parts = []
                    author_text = self._single_line(item.get("author"), 40)
                    photo_count = self._int(item.get("photo_count")) or self._int(item.get("image_count"))
                    tag_text = "、".join(
                        self._single_line(tag, 24)
                        for tag in (item.get("tags") if isinstance(item.get("tags"), list) else [])[:6]
                        if self._single_line(tag, 24)
                    )
                    if author_text:
                        detail_parts.append(f"作者：{author_text}")
                    if photo_count:
                        detail_parts.append(f"页数：{photo_count}")
                    if tag_text:
                        detail_parts.append(f"标签：{tag_text}")
                    album_description = "；".join(detail_parts) or "这条阅读记录暂时没有整理出明确简介。"
                page_comment_map: dict[int, list[str]] = {}
                raw_comments = self._merge_bookshelf_page_comments(
                    item.get("page_comments") if isinstance(item.get("page_comments"), list) else [],
                    item.get("page_comments_previous") if isinstance(item.get("page_comments_previous"), list) else [],
                    limit=32,
                )
                for comment_item in raw_comments:
                    if not isinstance(comment_item, dict):
                        continue
                    page_no = self._int(comment_item.get("page"))
                    comment_text = self._single_line(comment_item.get("comment"), 100)
                    if page_no > 0 and comment_text:
                        page_comments = page_comment_map.setdefault(page_no, [])
                        if comment_text not in page_comments:
                            page_comments.append(comment_text)
                reading_progress_page = max(0, self._int(item.get("reading_progress_page")))
                reading_progress_total = max(0, self._int(item.get("reading_progress_total"))) or len(pages)
                reading_progress_updated_at = self._float(item.get("reading_progress_updated_at"))
                reading_bookmark = self._single_line(item.get("reading_bookmark"), 120)
                reading_completed_at = self._float(item.get("reading_completed_at"))
                page_items = []
                for page in pages:
                    if not isinstance(page, dict):
                        continue
                    index = self._int(page.get("index"))
                    if index <= 0:
                        continue
                    page_src = self._bookshelf_image_url(
                        album_id,
                        data_root=data_root,
                        page_index=index,
                        path_value=page.get("path"),
                        access_token=access_token,
                    )
                    page_items.append(
                        {
                            "index": index,
                            "src": page_src,
                            "comment": "\n".join(page_comment_map.get(index, [])),
                        }
                    )
                cover_src = ""
                if album_id:
                    cover_src = self._bookshelf_cover_url(album_id, item, page_items, data_root, access_token=access_token)
                secret_books.append(
                    {
                        "id": f"archive-{album_id or len(secret_books)}",
                        "kind": "archive_item",
                        "category": "资料归档",
                        "album_id": album_id,
                        "title": self._single_line(item.get("title"), 100) or "未命名阅读记录",
                        "intro": self._single_line(album_description, 600),
                        "reading_impression": reading_impression or vision_impression,
                        "rating": bot_rating,
                        "rating_reason": rating_reason,
                        "user_rating": user_rating,
                        "user_rating_reason": user_rating_reason,
                        "user_rated": bool(user_rating),
                        "author": self._single_line(item.get("author"), 40),
                        "progress": (
                            "已读完"
                            if reading_completed_at
                            else f"读至 {min(max(1, reading_progress_page), max(1, reading_progress_total))}/{max(1, reading_progress_total)} 页"
                            if reading_progress_page > 0
                            else f"{len(page_items) or self._int(item.get('image_count')) or self._int(item.get('photo_count'))} 页"
                        ),
                        "created": self.plugin._format_timestamp_elapsed(item.get("created_ts", 0)),
                        "content": "\n\n".join(
                            part
                            for part in (
                                f"读后感：{reading_impression}" if reading_impression else "",
                                f"Bot 评分：{bot_rating}/10" if bot_rating else "",
                                f"用户评分：{user_rating}/10" if user_rating else "",
                                f"评分理由：{user_rating_reason or rating_reason}" if (user_rating_reason or rating_reason) else "",
                                f"画面记录：{vision_impression}" if show_vision_impression else "",
                                f"关键词：{self._single_line(item.get('keyword'), 80)}" if self._single_line(item.get("keyword"), 80) else "",
                            )
                            if part
                        ) or "这本只留下了一点很含糊的阅读印象。",
                        "tags": [self._single_line(tag, 24) for tag in item.get("tags", [])[:8] if self._single_line(tag, 24)]
                        if isinstance(item.get("tags"), list)
                        else [],
                        "preference_tags": [
                            self._single_line(tag, 24)
                            for tag in (item.get("preference_tags") if isinstance(item.get("preference_tags"), list) else [])[:8]
                            if self._single_line(tag, 24)
                        ],
                        "user_liked_tags": [
                            self._single_line(tag, 24)
                            for tag in (item.get("user_liked_tags") if isinstance(item.get("user_liked_tags"), list) else [])[:8]
                            if self._single_line(tag, 24)
                        ],
                        "user_disliked_tags": [
                            self._single_line(tag, 24)
                            for tag in (item.get("user_disliked_tags") if isinstance(item.get("user_disliked_tags"), list) else [])[:8]
                            if self._single_line(tag, 24)
                        ],
                        "user_tags_updated": bool(item.get("user_tags_updated_ts")),
                        "cover_src": cover_src,
                        "pages": page_items,
                        "reading_progress_page": reading_progress_page,
                        "reading_progress_total": reading_progress_total,
                        "reading_progress_updated_at": reading_progress_updated_at,
                        "reading_bookmark": reading_bookmark,
                        "reading_completed_at": reading_completed_at,
                        "page_comment_count": sum(len(comments) for comments in page_comment_map.values()),
                        "page_comments": [
                            {"page": page, "comment": comment}
                            for page, comments in sorted(page_comment_map.items())
                            for comment in comments
                        ],
                    }
                )
        return {
            "unlocked": unlocked,
            "access_token": access_token if unlocked and self._bookshelf_access_token_valid(access_token) else "",
            "access_expires_in": int(max(0, self._bookshelf_access_token_expires_at(access_token) - time.time()))
            if unlocked and access_token
            else 0,
            "access_expires_at": int(self._bookshelf_access_token_expires_at(access_token))
            if unlocked and access_token
            else 0,
            "public_count": len(public_books),
            "secret_count": locked_count,
            "diary_count": 1 if diaries else 0,
            "archive_item_count": len(archive_items),
            "reading_now_count": sum(1 for item in archive_items if self._int(item.get("reading_progress_page")) > 0 and not self._float(item.get("reading_completed_at"))),
            "memo_notes": self._memo_notes_payload(data),
            "password_hint": password_hint,
            "public_books": public_books,
            "secret_books": secret_books,
        }
