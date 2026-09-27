# -*- coding: utf-8 -*-
"""CreativePostgenEditRebuildMixin。

由 tools/split_mixin_domain.py 从 creative.py 机械抽取（3 个方法 + 0 个模块级名字 + 0 个类级赋值 / 283 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 CreativeMixin）。
"""
from __future__ import annotations

from .creative_shared import _persona_provider_id, _render_creative_prompt
from .creative_shared import Any
from .creative_shared import CREATIVE_MAX_REVISION_HISTORY
from .creative_shared import _now_ts
from .creative_shared import _safe_int
from .creative_shared import _single_line
from .creative_shared import json
from .creative_shared import logger
from .creative_shared import prompt_section
from .creative_shared import runtime_persona_setting
from .creative_shared import story_legacy_operation
from .creative_shared import uuid



class CreativePostgenEditRebuildMixin:
    """CreativePostgenEditRebuildMixin（从 CreativeMixin 拆出）。"""


    @story_legacy_operation("creative.chunk.extract")
    async def _post_generation_extract(
        self, project: dict[str, Any], story_bible: dict[str, Any],
        new_chunk_text: str, chunk_index: int,
    ) -> dict[str, Any]:
        manual_outline_ctx = self._creative_manual_outline_context(project)
        character_ctx = self._creative_character_context(project)
        revision_ctx = self._creative_manual_revision_context(project)
        direction_prompt = str(runtime_persona_setting(self, "creative_direction_prompt", "") or "").strip()[:2000]
        companion_memory_ctx = ""
        composer = getattr(self, "_memory_companion_compose_feature_context", None)
        if callable(composer):
            try:
                companion_memory_ctx = await composer(
                    kind="creative_extract",
                    query=(
                        f"私下创作抽取：{_single_line(project.get('title'), 40)}；"
                        "需要长期记住的角色、线索、用户修订、下一步、避雷"
                    ),
                    top_k=4,
                    max_chars=700,
                )
            except Exception as exc:
                logger.debug("创作抽取 我会牢牢记住你 上下文读取失败: %s", _single_line(exc, 120))
        prompt = prompt_section(
            key="background.creative.extract",
            title="私人创作连续性提取",
            source="creative",
            content=f"""
整理一个长期创作项目刚写出的新片段,提取对后续续写最有用的结构化信息。

当前主线：{_single_line(story_bible.get('mainline_direction'), 140)}
未解决线索：{', '.join(_single_line(t, 24) for t in story_bible.get('unresolved_threads', []) if _single_line(t, 24)) or '暂无'}
下一步方向：{_single_line(story_bible.get('next_direction') or project.get('next_hint'), 140)}
故事内时间（写新片段之前）：{_single_line(story_bible.get('story_time'), 60) or '尚未确定'}
人工维护大纲：
{manual_outline_ctx or '暂无人工大纲。'}
角色表：
{character_ctx or '暂无角色表。'}
人工修订约束：
{revision_ctx or '暂无人工修订。'}
用户配置的创作方向：
{direction_prompt or '未指定。'}
我会牢牢记住你 项目参考：
{companion_memory_ctx or '暂无外部项目参考。'}
新片段：{_single_line(new_chunk_text, 420)}

输出 JSON：
{{
  "mainline_direction": "一句话概括现在真正推进到哪条主线",
  "themes_used": ["最多3个主题词"],
  "threads_advanced": ["最多2条推进中的线索"],
  "threads_resolved": ["最多1条已收束线索"],
  "new_threads": ["最多2条新埋下的线索"],
  "important_facts": ["最多3条后续必须记住的事实"],
  "keywords": ["最多6个关键词"],
  "story_time": "写完这个片段后,故事内现在处于什么时刻,10到24字",
  "next_direction": "一句话描述下一段最自然该写什么"
}}
""".strip(),
        )
        text = await self._llm_call(
            _render_creative_prompt(prompt), max_tokens=300,
            provider_id=self._task_provider(
                _persona_provider_id(
                    self, "CREATIVE_REVIEW_PROVIDER_ID", "creative_review_provider_id", "creative"
                ),
                _persona_provider_id(self, "CREATIVE_PROVIDER_ID", "creative_provider_id", "creative"),
                _persona_provider_id(self, "MAI_STYLE_PROVIDER_ID", "mai_style_provider_id", "fast"),
            ),
            task="creative_extract",
        )
        payload = self._extract_json_payload(text or "")
        if not isinstance(payload, dict):
            return {}

        def _limit(values: Any, size: int, width: int) -> list[str]:
            if not isinstance(values, list):
                return []
            result: list[str] = []
            for v in values:
                s = _single_line(v, width)
                if s and s not in result:
                    result.append(s)
                if len(result) >= size:
                    break
            return result

        themes_used = _limit(payload.get("themes_used"), 3, 20)
        threads_advanced = _limit(payload.get("threads_advanced"), 2, 40)
        threads_resolved = _limit(payload.get("threads_resolved"), 1, 40)
        new_threads = _limit(payload.get("new_threads"), 2, 40)
        important_facts = _limit(payload.get("important_facts"), 3, 60)
        keywords = _limit(payload.get("keywords"), 6, 16)
        mainline_direction = _single_line(payload.get("mainline_direction"), 140)
        next_direction = _single_line(payload.get("next_direction"), 140)
        story_time = _single_line(payload.get("story_time"), 60)

        active_themes = [t for t in story_bible.get("active_themes", []) if _single_line(t, 20)]
        unresolved = [t for t in story_bible.get("unresolved_threads", []) if _single_line(t, 40)]
        resolved = [t for t in story_bible.get("resolved_threads", []) if _single_line(t, 40)]
        imp_facts = [t for t in story_bible.get("important_facts", []) if _single_line(t, 60)]
        recent_kw = [t for t in story_bible.get("recent_keywords", []) if _single_line(t, 16)]

        for t in themes_used:
            if t not in active_themes:
                active_themes.append(t)
        for t in threads_resolved:
            if t not in resolved:
                resolved.append(t)
            unresolved = [u for u in unresolved if u != t]
        for t in threads_advanced + new_threads:
            if t and t not in unresolved and t not in resolved:
                unresolved.append(t)
        for k in keywords:
            if k not in recent_kw:
                recent_kw.append(k)
        for f in important_facts:
            if f not in imp_facts:
                imp_facts.append(f)

        if mainline_direction:
            story_bible["mainline_direction"] = mainline_direction
        story_bible["active_themes"] = active_themes[-6:]
        story_bible["resolved_threads"] = resolved[-20:]
        story_bible["unresolved_threads"] = unresolved[-12:]
        story_bible["important_facts"] = imp_facts[-12:]
        if next_direction:
            story_bible["next_direction"] = next_direction
        if story_time:
            story_bible["story_time"] = story_time
        story_bible["recent_keywords"] = recent_kw[-20:]
        story_bible["last_updated_chunk"] = _safe_int(chunk_index, chunk_index, 0)
        return {
            "themes_used": themes_used,
            "threads_advanced": threads_advanced,
            "threads_resolved": threads_resolved,
            "new_threads": new_threads,
            "important_facts": important_facts,
            "keywords": keywords,
            "mainline_direction": mainline_direction,
            "next_direction": next_direction,
            "story_time": story_time,
        }

    @story_legacy_operation("creative.manual-edit")
    async def _apply_creative_manual_edit(
        self, project_id: str, edit_type: str, edit_content: str,
        edit_title: str = "", chunk_index: int = -1,
    ) -> dict[str, Any]:
        async with self._data_lock:
            projects = self._creative_projects()
            project = next((p for p in projects if p.get("id") == project_id), None)
            if not project:
                return {"success": False, "error": "作品不存在"}
            now = _now_ts()
            normalized_type = _single_line(edit_type, 24)
            normalized_title = _single_line(edit_title or normalized_type, 60)
            if normalized_type == "chunk_text":
                chunks = project.get("draft_chunks") if isinstance(project.get("draft_chunks"), list) else []
                if not (0 <= chunk_index < len(chunks)) or not isinstance(chunks[chunk_index], dict):
                    return {"success": False, "error": "片段索引越界"}
            edit_record = {
                "id": uuid.uuid4().hex[:12],
                "type": normalized_type,
                "title": normalized_title,
                "content": _single_line(edit_content, 2000),
                "chunk_index": chunk_index,
                "created_at": now,
            }
            edits = project.setdefault("manual_edits", [])
            if not isinstance(edits, list):
                edits = []
                project["manual_edits"] = edits
            edits.append(edit_record)
            del edits[:-CREATIVE_MAX_REVISION_HISTORY]
            project["last_manual_edit_at"] = now
            project["last_manual_edit_summary"] = _single_line(edit_title or edit_type, 120)
            story_bible = self._get_or_create_story_bible(project)
            pool = self._get_or_create_memory_pool(project)
            if normalized_type == "chunk_text":
                chunks = project.get("draft_chunks") if isinstance(project.get("draft_chunks"), list) else []
                chunks[chunk_index]["text"] = _single_line(edit_content, 5000)
                chunks[chunk_index]["chars"] = len(chunks[chunk_index]["text"])
                chunks[chunk_index]["manually_edited"] = True
                project["current_chars"] = sum(
                    _safe_int(c.get("chars"), 0, 0) for c in chunks if isinstance(c, dict)
                )
            elif normalized_type == "outline":
                lines = [_single_line(l, 100) for l in edit_content.split("\n") if _single_line(l, 100)]
                project["outline"] = lines[:30]
                if lines:
                    story_bible["next_direction"] = lines[0]
                    story_bible["recent_outlines"] = (story_bible.get("recent_outlines") or [])[-5:] + ["\n".join(f"- {line}" for line in lines[:5])]
            elif normalized_type == "premise":
                project["premise"] = _single_line(edit_content, 160)
                story_bible["mainline_direction"] = _single_line(edit_content, 160)
            elif normalized_type == "title":
                project["title"] = _single_line(edit_content, 40)
            elif normalized_type == "characters":
                try:
                    parsed = json.loads(edit_content) if edit_content.strip().startswith(("[", "{")) else []
                    if isinstance(parsed, dict):
                        parsed = parsed.get("characters") if isinstance(parsed.get("characters"), list) else []
                    if isinstance(parsed, list):
                        project["characters"] = [item for item in parsed if isinstance(item, dict)]
                except Exception:
                    pass
            elif normalized_type == "next_hint":
                project["next_hint"] = _single_line(edit_content, 160)
                story_bible["next_direction"] = _single_line(edit_content, 160)
            keywords = self._extract_creative_keywords(f"{normalized_title} {edit_content}", limit=8) or ["人工修订"]
            self._add_memory_entry(
                pool,
                project_id,
                "revision",
                f"{normalized_title or normalized_type}: {_single_line(edit_content, 180)}",
                keywords,
                importance=5,
            )
            self.data["creative_projects"] = projects
            self._save_data_sync(sections={"creative_projects"})
            return {"success": True, "project_id": project_id, "edit_type": normalized_type}

    @story_legacy_operation("creative.memory.rebuild")
    async def _rebuild_creative_memory_from_project(self, project_id: str) -> dict[str, Any]:
        async with self._data_lock:
            projects = self._creative_projects()
            project = next((p for p in projects if p.get("id") == project_id), None)
            if not project:
                return {"success": False, "error": "作品不存在"}
            pool = self._get_or_create_memory_pool(project)
            chunks = project.get("draft_chunks") if isinstance(project.get("draft_chunks"), list) else []
            story_bible = self._get_or_create_story_bible(project)
            pool.clear()
            for chunk in chunks[-12:]:
                if not isinstance(chunk, dict):
                    continue
                text = _single_line(chunk.get("text"), 400)
                if text:
                    self._add_memory_entry(
                        pool, project_id, "scene", text,
                        story_bible.get("recent_keywords", []), importance=3,
                    )
            outline = project.get("outline") if isinstance(project.get("outline"), list) else []
            for line in outline[:12]:
                text = _single_line(line, 120)
                if text:
                    self._add_memory_entry(pool, project_id, "outline", text, self._extract_creative_keywords(text), importance=5)
            for character in self._get_project_characters(project)[:10]:
                name = _single_line(character.get("name"), 32)
                desc = _single_line(character.get("description") or character.get("personality") or character.get("background"), 160)
                if name or desc:
                    self._add_memory_entry(
                        pool,
                        project_id,
                        "character",
                        "｜".join(part for part in (name, desc) if part),
                        self._extract_creative_keywords(f"{name} {desc}"),
                        importance=5,
                    )
            edits = project.get("manual_edits") if isinstance(project.get("manual_edits"), list) else []
            for edit in edits[-CREATIVE_MAX_REVISION_HISTORY:]:
                if not isinstance(edit, dict):
                    continue
                title = _single_line(edit.get("title") or edit.get("type"), 60)
                content = _single_line(edit.get("content"), 180)
                if title or content:
                    self._add_memory_entry(
                        pool,
                        project_id,
                        "revision",
                        f"{title}: {content}",
                        self._extract_creative_keywords(f"{title} {content}") or ["人工修订"],
                        importance=5,
                    )
            if project.get("last_manual_edit_summary"):
                self._add_memory_entry(
                    pool, project_id, "revision",
                    f"最近人工修订: {_single_line(project.get('last_manual_edit_summary'), 120)}",
                    ["人工修订"], importance=5,
                )
            self.data["creative_projects"] = projects
            self._save_data_sync(sections={"creative_projects"})
            return {"success": True, "project_id": project_id, "memory_count": len(pool)}
