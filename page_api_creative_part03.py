# -*- coding: utf-8 -*-
"""PrivateCompanionPageApiCreativePart03Mixin。

由 tools/split_mixin_domain.py 从 page_api_creative.py 机械抽取（7 个方法 + 0 个模块级名字 + 0 个类级赋值 / 166 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApiCreativeMixin）。
"""
from __future__ import annotations

from .page_api_creative_shared import logger
from .page_api_creative_shared import Any
from .page_api_creative_shared import deepcopy
from .page_api_creative_shared import json
from .page_api_creative_shared import request
from .page_api_creative_shared import secrets
from .page_api_creative_shared import story_legacy_operation
from .page_api_creative_shared import time



class PrivateCompanionPageApiCreativePart03Mixin:
    """PrivateCompanionPageApiCreativePart03Mixin（从 PrivateCompanionPageApiCreativeMixin 拆出）。"""


    @story_legacy_operation("page.creative.outline-update")
    async def update_creative_outline(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        project_id = str(payload.get("id", "")).strip()
        outline_text = str(payload.get("outline", "")).strip()
        if not project_id:
            return self._error("缺少 id")
        try:
            apply_edit = getattr(self.plugin, "_apply_creative_manual_edit", None)
            if not callable(apply_edit):
                return self._error("创作编辑能力不可用")
            result = await apply_edit(project_id, "outline", outline_text, "更新大纲", -1)
            if not result.get("success"):
                return self._error(result.get("error") or "大纲更新失败")
            return self._ok({"project_id": project_id, "message": "大纲已更新"})
        except Exception as exc:
            logger.error("更新创作大纲失败: %s", exc, exc_info=True)
            return self._exception_error("更新创作大纲失败")

    @story_legacy_operation("page.creative.characters-update")
    async def update_creative_characters(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        project_id = str(payload.get("id", "")).strip()
        raw_characters = payload.get("characters")
        if not project_id:
            return self._error("缺少 id")
        if not isinstance(raw_characters, list):
            return self._error("角色必须是数组")
        try:
            characters = [item for item in raw_characters if isinstance(item, dict)]
            apply_edit = getattr(self.plugin, "_apply_creative_manual_edit", None)
            if not callable(apply_edit):
                return self._error("创作编辑能力不可用")
            result = await apply_edit(
                project_id,
                "characters",
                json.dumps(characters, ensure_ascii=False),
                "更新角色表",
                -1,
            )
            if not result.get("success"):
                return self._error(result.get("error") or "角色更新失败")
            return self._ok({"project_id": project_id, "message": "角色已更新"})
        except Exception as exc:
            logger.error("更新创作角色失败: %s", exc, exc_info=True)
            return self._exception_error("更新创作角色失败")

    @story_legacy_operation("page.creative.reanalyze")
    async def reanalyze_creative_project(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        project_id = str(payload.get("id", "")).strip()
        if not project_id:
            return self._error("缺少 id")
        try:
            async with self.plugin._data_lock:
                projects = self.plugin.data.get("creative_projects") if isinstance(self.plugin.data.get("creative_projects"), list) else []
                project = next((p for p in projects if isinstance(p, dict) and p.get("id") == project_id), None)
                if not project:
                    return self._error("作品不存在")
                snapshot = deepcopy(project)
            chunks = snapshot.get("draft_chunks") if isinstance(snapshot.get("draft_chunks"), list) else []
            latest = next((c for c in reversed(chunks) if isinstance(c, dict) and self._single_line(c.get("text"), 80)), None)
            if not latest:
                return self._error("还没有正文片段，无法分析")
            story_bible = snapshot.get("story_bible") if isinstance(snapshot.get("story_bible"), dict) else {}
            outline = "\n".join(snapshot.get("outline", [])) if isinstance(snapshot.get("outline"), list) else ""
            safe_recent_chunks = [c for c in chunks[-5:] if isinstance(c, dict)]
            reviewer = getattr(self.plugin, "_review_creative_chunk", None)
            if not callable(reviewer):
                return self._error("创作审校能力不可用")
            review = await reviewer(snapshot, story_bible, outline, latest.get("text", ""), safe_recent_chunks)
            if not isinstance(review, dict):
                review = {}
            async with self.plugin._data_lock:
                projects = self.plugin.data.get("creative_projects") if isinstance(self.plugin.data.get("creative_projects"), list) else []
                project = next((p for p in projects if isinstance(p, dict) and p.get("id") == project_id), None)
                if not project:
                    return self._error("作品不存在")
                review["id"] = secrets.token_hex(6)
                review["chunk_index"] = len(project.get("draft_chunks") or []) - 1
                review["created_at"] = time.time()
                reviews = project.setdefault("quality_reviews", [])
                if not isinstance(reviews, list):
                    reviews = []
                    project["quality_reviews"] = reviews
                reviews.append(review)
                del reviews[:-20]
                self.plugin._save_data_sync(sections={"creative_projects"})
            return self._ok({"project_id": project_id, "review": review})
        except Exception as exc:
            logger.error("创作项目质量分析失败: %s", exc, exc_info=True)
            return self._exception_error("创作项目质量分析失败")

    @story_legacy_operation("page.creative.memory-rebuild")
    async def rebuild_creative_memory(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        project_id = str(payload.get("id", "")).strip()
        if not project_id:
            return self._error("缺少 id")
        try:
            rebuild = getattr(self.plugin, "_rebuild_creative_memory_from_project", None)
            if not callable(rebuild):
                return self._error("创作记忆重建能力不可用")
            result = await rebuild(project_id)
            if not result.get("success"):
                return self._error(result.get("error") or "重建失败")
            return self._ok(result)
        except Exception as exc:
            logger.error("重建创作记忆失败: %s", exc, exc_info=True)
            return self._exception_error("重建创作记忆失败")

    @story_legacy_operation("page.creative.project-delete")
    async def delete_creative_project(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        project_id = str(payload.get("id", "")).strip()
        if not project_id:
            return self._error("缺少 id")
        try:
            async with self.plugin._data_lock:
                projects = self.plugin.data.get("creative_projects") if isinstance(self.plugin.data.get("creative_projects"), list) else []
                before = len(projects)
                self.plugin.data["creative_projects"] = [
                    p for p in projects if not (isinstance(p, dict) and p.get("id") == project_id)
                ]
                removed = before - len(self.plugin.data["creative_projects"])
                self.plugin._save_data_sync(sections={"creative_projects"})
            return self._ok({"project_id": project_id, "removed": removed})
        except Exception as exc:
            logger.error("删除创作项目失败: %s", exc, exc_info=True)
            return self._exception_error("删除创作项目失败")

    def _segment_from_story_windows(self, snapshot: dict[str, Any]) -> dict[str, Any] | None:
        minutes: list[int] = []
        for key in ("today_events", "proactive_events"):
            items = snapshot.get(key)
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                start, end = self.plugin._parse_window_minutes(str(item.get("window") or ""))
                if start is not None:
                    minutes.append(start)
                if end is not None:
                    minutes.append(end)
        if not minutes:
            return None
        start = min(minutes)
        end = max(minutes)
        return {"window": f"{self.plugin._minutes_to_hhmm(start)}-{self.plugin._minutes_to_hhmm(end)}", "start": start}

    @staticmethod
    def _story_item_axis_start(
        start: int,
        *,
        parent_start: int | None = None,
        parent_end: int | None = None,
    ) -> int:
        if start < 0 or start >= 24 * 60:
            return start
        axis_start = start
        if parent_start is None or parent_start < 0:
            return axis_start
        parent_day_start = (parent_start // (24 * 60)) * (24 * 60)
        axis_start += parent_day_start
        if (
            parent_end is not None
            and parent_end > parent_day_start + 24 * 60
            and axis_start < parent_start
        ):
            axis_start += 24 * 60
        return axis_start
