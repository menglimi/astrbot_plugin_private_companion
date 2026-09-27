# -*- coding: utf-8 -*-
"""PrivateCompanionPageApiCreativePart02Mixin。

由 tools/split_mixin_domain.py 从 page_api_creative.py 机械抽取（10 个方法 + 0 个模块级名字 + 0 个类级赋值 / 486 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApiCreativeMixin）。
"""
from __future__ import annotations

from .page_api_creative_shared import _render_page_background_prompt, logger
from .page_api_creative_shared import Any
from .page_api_creative_shared import Path
from .page_api_creative_shared import _path_text
from .page_api_creative_shared import _safe_int
from .page_api_creative_shared import asyncio
from .page_api_creative_shared import deepcopy
from .page_api_creative_shared import diagnostic_test_id
from .page_api_creative_shared import mimetypes
from .page_api_creative_shared import quote
from .page_api_creative_shared import request
from .page_api_creative_shared import secrets
from .page_api_creative_shared import send_file
from .page_api_creative_shared import story_legacy_operation
from .page_api_creative_shared import time



class PrivateCompanionPageApiCreativePart02Mixin:
    """PrivateCompanionPageApiCreativePart02Mixin（从 PrivateCompanionPageApiCreativeMixin 拆出）。"""


    async def test_provider(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        key = str(payload.get("key", "")).strip()
        provider_id = self._single_line(payload.get("provider_id"), 160)
        if key and key not in self._allowed_provider_keys():
            return self._error("不允许测试该 Provider 配置项")
        timeout_raw = payload.get("timeout_seconds")
        timeout_seconds = None
        if timeout_raw not in (None, ""):
            timeout_seconds = self._float(timeout_raw, 0.0, 5.0, 600.0)
        request_id = secrets.token_hex(6)
        start = time.time()
        logger.info("[test:%s][type:provider_connection] 开始执行测试", request_id)
        try:
            if key in {"EMBEDDING_PROVIDER_ID", "REACTION_EXPRESSION_EMBEDDING_PROVIDER_ID"}:
                provider = await self._embedding_provider_for_test(provider_id)
                vector_getter = getattr(self.plugin, "_reaction_embedding_vector", None)
                if provider is None or not callable(vector_getter):
                    raise RuntimeError("未找到可用的 Embedding Provider")
                vector = await vector_getter(provider, "开心 安慰 抱抱 表情语义测试")
                text = f"{len(vector)} 维向量" if vector else ""
                step_name = "向量生成"
            elif key in {"PLUGIN_VISION_PROVIDER_ID", "READING_ARCHIVE_VISION_PROVIDER_ID", "WARDROBE_VISION_PROVIDER_ID"}:
                provider = self._visual_provider_for_test(provider_id)
                supports_image = getattr(self.plugin, "_provider_supports_image", None)
                if provider is None:
                    raise RuntimeError("未找到已加载的视觉 Provider")
                if callable(supports_image) and not supports_image(provider):
                    raise RuntimeError("Provider 配置未声明支持图片输入")
                visual_timeout = self._visual_provider_test_timeout(provider_id, key, timeout_seconds)
                visual_prompt = _render_page_background_prompt(
                    key="background.provider_test.vision",
                    title="视觉 Provider 连通性测试",
                    content="这是视觉 Provider 图片输入测试。请观察所附图片，并只回复两个字：正常",
                )
                prompt_applier = getattr(self.plugin, "_apply_task_prompt_override_for_call", None)
                if callable(prompt_applier):
                    visual_prompt, _unused_system_prompt = prompt_applier(
                        "provider_test",
                        visual_prompt,
                        None,
                        flatten_system_prompt=True,
                    )
                call = provider.text_chat(
                    prompt=visual_prompt,
                    image_urls=[self._vision_provider_test_image_data_url()],
                    max_tokens=16,
                )
                try:
                    response = await asyncio.wait_for(call, timeout=visual_timeout) if visual_timeout > 0 else await call
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    raise RuntimeError(self._visual_call_error_text(exc, timeout=visual_timeout)) from exc
                text = str(getattr(response, "completion_text", response) or "").strip()
                step_name = "图片输入"
            else:
                text = await self.plugin._llm_call(
                    _render_page_background_prompt(
                        key="background.provider_test.text",
                        title="文本 Provider 连通性测试",
                        content="请只回复两个字：正常",
                    ),
                    max_tokens=16,
                    provider_id=provider_id,
                    task="provider_test",
                    timeout_key=key,
                    timeout_seconds=timeout_seconds,
                )
                step_name = "模型调用"
            elapsed_ms = int((time.time() - start) * 1000)
            ok = bool(text)
            embedding_test = key in {"EMBEDDING_PROVIDER_ID", "REACTION_EXPRESSION_EMBEDDING_PROVIDER_ID"}
            vision_test = key in {"PLUGIN_VISION_PROVIDER_ID", "READING_ARCHIVE_VISION_PROVIDER_ID", "WARDROBE_VISION_PROVIDER_ID"}
            result = {
                "ok": ok,
                "key": key,
                "provider_id": provider_id,
                "elapsed_ms": elapsed_ms,
                "sample": self._single_line(text, 80),
                "detail": (
                    "Embedding Provider 已返回有效向量"
                    if ok and embedding_test
                    else "视觉 Provider 已接受图片并返回有效内容"
                    if ok and vision_test
                    else "Provider 已返回有效测试内容"
                    if ok
                    else "Provider 调用完成，但返回内容为空"
                ),
                "error": "" if ok else ("Embedding Provider 未返回有效向量" if embedding_test else "Provider 未返回有效内容"),
                "steps": [
                    {
                        "name": step_name,
                        "status": "ok" if ok else "error",
                        "detail": "已收到有效结果" if ok else "响应为空",
                        "elapsed_ms": elapsed_ms,
                    }
                ],
            }
        except Exception as exc:
            result = {
                "ok": False,
                "key": key,
                "provider_id": provider_id,
                "elapsed_ms": int((time.time() - start) * 1000),
                "error": self._safe_test_diagnostic_text(exc, 1600),
                "exception_type": exc.__class__.__name__,
            }
        result["request_id"] = request_id
        result = self._finalize_test_diagnostics(
            "provider_connection",
            result,
            start,
            title="模型 Provider 连接测试",
        )
        result = self._diagnostic_envelope(
            result,
            test_type="provider",
            duration_ms=self._int(result.get("elapsed_ms")),
            test_id=diagnostic_test_id("provider"),
        )
        logger.info(
            "[test:%s][type:provider_connection] 测试结束: status=%s elapsed_ms=%s",
            request_id,
            result.get("test_status"),
            result.get("elapsed_ms"),
        )
        return self._ok(result)

    def _browsing_history_entries(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        news_state = data.get("news_integration") if isinstance(data.get("news_integration"), dict) else {}
        news_digest = news_state.get("last_digest") if isinstance(news_state.get("last_digest"), dict) else {}
        news_digests = news_state.get("digests") if isinstance(news_state.get("digests"), list) else []
        news_items = [item for item in news_digests if isinstance(item, dict)]
        if news_digest and not any(
            self._single_line(item.get("selected_key"), 32) == self._single_line(news_digest.get("selected_key"), 32)
            and self._float(item.get("created_ts")) == self._float(news_digest.get("created_ts"))
            for item in news_items
        ):
            news_items.append(news_digest)
        for news_digest in news_items:
            headline = self._single_line(news_digest.get("headline") or news_digest.get("topic"), 120)
            impression = self._single_line(news_digest.get("impression"), 1000)
            selected_source = self._single_line(news_digest.get("selected_source"), 40)
            selected_link = self._single_line(news_digest.get("selected_link"), 400)
            created_ts = self._float(news_digest.get("created_ts"))
            entries.append(
                {
                    "_ts": created_ts,
                    "source": "news",
                    "source_label": "新闻阅读",
                    "date": self.plugin._format_timestamp_elapsed(created_ts) or "今日新闻",
                    "generated_at": self.plugin._format_timestamp_elapsed(created_ts),
                    "title": headline or "新闻阅读",
                    "query": "",
                    "intro": impression or "这次新闻阅读没有留下明显印象。",
                    "content": "\n\n".join(
                        part
                        for part in (
                            f"新闻见闻：{headline}" if headline else "",
                            impression,
                            f"来源：{selected_source}" if selected_source else "",
                            f"链接：{selected_link}" if selected_link else "",
                        )
                        if part
                    ) or "这次新闻阅读没有留下正文。",
                    "source_title": selected_source,
                    "source_url": selected_link,
                    "tags": ["新闻阅读"],
                }
            )
        web_state = data.get("web_exploration") if isinstance(data.get("web_exploration"), dict) else {}
        web_notes = web_state.get("notes") if isinstance(web_state.get("notes"), list) else []
        web_items = [note for note in web_notes if isinstance(note, dict)]
        last_digest = web_state.get("last_digest") if isinstance(web_state.get("last_digest"), dict) else {}
        if last_digest:
            last_key = "|".join(
                self._single_line(last_digest.get(key), 120)
                for key in ("query", "topic", "created_ts")
            )
            if not any(
                "|".join(
                    self._single_line(item.get(key), 120)
                    for key in ("query", "topic", "created_ts")
                ) == last_key
                for item in web_items
            ):
                web_items.append(last_digest)
        for item in web_items:
            topic = self._single_line(item.get("topic"), 100)
            query = self._single_line(item.get("query"), 100)
            note = self._single_line(
                item.get("note")
                or item.get("summary")
                or item.get("impression")
                or item.get("content"),
                1000,
            )
            source_title = self._single_line(item.get("source_title"), 120)
            source_url = self._single_line(item.get("source_url"), 400)
            created_ts = self._float(item.get("created_ts"))
            result_lines = []
            raw_results = item.get("results") if isinstance(item.get("results"), list) else []
            for result in raw_results[:4]:
                if not isinstance(result, dict):
                    continue
                title = self._single_line(result.get("title"), 100)
                snippet = self._single_line(result.get("snippet"), 180)
                if title and snippet:
                    result_lines.append(f"{title}：{snippet}")
                elif title:
                    result_lines.append(title)
                elif snippet:
                    result_lines.append(snippet)
            result_excerpt = self._single_line("；".join(result_lines), 1000)
            if not note:
                note = result_excerpt
            entries.append(
                {
                    "_ts": created_ts,
                    "source": self._single_line(item.get("source"), 40) or "web_exploration",
                    "source_label": self._single_line(item.get("source_label"), 40) or "主动搜索",
                    "date": self.plugin._format_timestamp_elapsed(created_ts) or "某次搜索",
                    "generated_at": self.plugin._format_timestamp_elapsed(created_ts),
                    "title": topic or query or "主动搜索",
                    "query": query,
                    "intro": note or "这次搜索没有留下明显印象。",
                    "content": "\n\n".join(
                        part
                        for part in (
                            f"搜索词：{query}" if query else "",
                            f"搜索动机：{self._single_line(item.get('reason'), 160)}" if self._single_line(item.get("reason"), 160) else "",
                            f"笔记：{note}" if note else "",
                            f"结果摘录：{result_excerpt}" if result_excerpt and result_excerpt != note else "",
                            f"主要来源：{source_title}" if source_title else "",
                            f"链接：{source_url}" if source_url else "",
                        )
                        if part
                    ) or "这次主动搜索没有留下正文。",
                    "source_title": source_title,
                    "source_url": source_url,
                    "tags": ["主动搜索"],
                }
            )
        entries.sort(key=lambda item: self._float(item.get("_ts")))
        for item in entries:
            item.pop("_ts", None)
        return entries

    def _creative_project_cover_path(self, project: dict[str, Any]) -> Path | None:
        path_text = _path_text(project.get("cover_path"), 1000)
        if not path_text:
            return None
        try:
            path = Path(path_text).resolve()
        except Exception:
            return None
        return path if path.exists() and path.is_file() else None

    def _creative_project_cover_url(self, project: dict[str, Any]) -> str:
        project_id = self._single_line(project.get("id"), 32)
        path = self._creative_project_cover_path(project)
        if not project_id or path is None:
            return ""
        try:
            stat = path.stat()
            version = f"{int(stat.st_mtime)}-{stat.st_size}"
        except OSError:
            version = self._single_line(project.get("cover_generated_at"), 40)
        return f"{self._page_asset_prefix()}/creative/project/cover?id={quote(project_id, safe='')}&v={quote(version, safe='')}"

    def _creative_project_payload(self, project: dict[str, Any]) -> dict[str, Any]:
        chunks = project.get("draft_chunks") if isinstance(project.get("draft_chunks"), list) else []
        chunk_items = []
        for idx, chunk in enumerate(chunks):
            if not isinstance(chunk, dict):
                continue
            chunk_ts = chunk.get("at", 0) or chunk.get("created_at", 0) or chunk.get("created_ts", 0)
            chunk_items.append(
                {
                    "index": idx,
                    "text": self._single_line(chunk.get("text"), 8000),
                    "chars": self._int(chunk.get("chars")),
                    "at": chunk_ts,
                    "created": self.plugin._format_timestamp_elapsed(chunk_ts),
                    "manually_edited": bool(chunk.get("manually_edited")),
                }
            )
        return {
            "id": self._single_line(project.get("id"), 32),
            "title": self._single_line(project.get("title"), 80),
            "work_type": self._single_line(project.get("work_type"), 40) or "短篇小说",
            "premise": self._single_line(project.get("premise"), 500),
            "tone": self._single_line(project.get("tone"), 80),
            "point_of_view": self._single_line(project.get("point_of_view"), 60),
            "source": self._single_line(project.get("source"), 40),
            "source_text": self._single_line(project.get("source_text"), 500),
            "status": self._single_line(project.get("status"), 24),
            "current_chars": self._int(project.get("current_chars")),
            "target_chars": self._int(project.get("target_chars")),
            "next_hint": self._single_line(project.get("next_hint"), 240),
            "outline": project.get("outline") if isinstance(project.get("outline"), list) else [],
            "characters": project.get("characters") if isinstance(project.get("characters"), list) else [],
            "story_bible": project.get("story_bible") if isinstance(project.get("story_bible"), dict) else {},
            "revision_notes": self._limited_list(project.get("revision_notes"), 20),
            "quality_reviews": self._limited_list(project.get("quality_reviews"), 20),
            "manual_edits": self._limited_list(project.get("manual_edits"), 30),
            "creative_memory_pool": self._limited_list(project.get("creative_memory_pool"), 50),
            "last_manual_edit_at": self.plugin._format_timestamp_elapsed(project.get("last_manual_edit_at", 0)),
            "last_manual_edit_summary": self._single_line(project.get("last_manual_edit_summary"), 160),
            "chunks": chunk_items,
            "chunk_count": len(chunk_items),
            "milestones": project.get("disclosed_milestones") if isinstance(project.get("disclosed_milestones"), list) else [],
            "created_at": self.plugin._format_timestamp_elapsed(project.get("created_at", 0)),
            "last_advanced": self.plugin._format_timestamp_elapsed(project.get("last_advanced_at", 0)),
            "next_advance": self.plugin._format_timestamp_elapsed(project.get("next_advance_at", 0)),
            "cover_src": self._creative_project_cover_url(project),
            "cover_status": self._single_line(project.get("cover_generation_status"), 24),
            "cover_error": self._single_line(project.get("cover_generation_error"), 220),
            "cover_backend": self._single_line(project.get("cover_generation_backend"), 80),
            "cover_style": self._single_line(project.get("cover_generation_style"), 40),
            "cover_attempts": self._int(project.get("cover_generation_attempts")),
            "cover_generated_at": self.plugin._format_timestamp_elapsed(project.get("cover_generated_at", 0)),
        }

    async def get_creative_project_cover(self):
        project_id = self._single_line(request.args.get("id"), 32)
        if not project_id:
            return self._error("缺少 id")
        async with self.plugin._data_lock:
            projects = self.plugin.data.get("creative_projects") if isinstance(self.plugin.data.get("creative_projects"), list) else []
            project = next(
                (item for item in projects if isinstance(item, dict) and self._single_line(item.get("id"), 32) == project_id),
                None,
            )
            path = self._creative_project_cover_path(project) if isinstance(project, dict) else None
        if path is None:
            return self._error("作品封面不存在")
        response = await send_file(str(path))
        response.headers["Cache-Control"] = "private, max-age=3600"
        return response

    async def get_creative_project_cover_data(self) -> dict[str, Any]:
        project_id = self._single_line(request.args.get("id"), 32)
        if not project_id:
            return self._error("缺少 id")
        async with self.plugin._data_lock:
            projects = self.plugin.data.get("creative_projects") if isinstance(self.plugin.data.get("creative_projects"), list) else []
            project = next(
                (item for item in projects if isinstance(item, dict) and self._single_line(item.get("id"), 32) == project_id),
                None,
            )
            path = self._creative_project_cover_path(project) if isinstance(project, dict) else None
        if path is None:
            return self._error("作品封面不存在")
        try:
            mime = mimetypes.guess_type(str(path))[0] or "image/jpeg"
            data = await self._read_file_base64(path)
            stat = path.stat()
            return self._ok(
                {
                    "mime": mime,
                    "data_url": f"data:{mime};base64,{data}",
                    "size": stat.st_size,
                    "mtime": int(stat.st_mtime),
                }
            )
        except Exception as exc:
            logger.error("读取创作封面数据失败: %s", exc, exc_info=True)
            return self._exception_error("读取创作封面数据失败")

    async def get_creative_project(self) -> dict[str, Any]:
        project_id = str(request.args.get("id", "")).strip()
        if not project_id:
            return self._error("缺少 id")
        try:
            async with self.plugin._data_lock:
                projects = self.plugin.data.get("creative_projects") if isinstance(self.plugin.data.get("creative_projects"), list) else []
                project = next((p for p in projects if isinstance(p, dict) and p.get("id") == project_id), None)
                if not project:
                    return self._error("作品不存在")
                snapshot = deepcopy(project)
            return self._ok(self._creative_project_payload(snapshot))
        except Exception as exc:
            logger.error("获取创作项目详情失败: %s", exc, exc_info=True)
            return self._exception_error("获取创作项目详情失败")

    @story_legacy_operation("page.creative.project-update")
    async def update_creative_project(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        project_id = str(payload.get("id", "")).strip()
        if not project_id:
            return self._error("缺少 id")
        try:
            changed_notes: list[str] = []
            async with self.plugin._data_lock:
                projects = self.plugin.data.get("creative_projects") if isinstance(self.plugin.data.get("creative_projects"), list) else []
                project = next((p for p in projects if isinstance(p, dict) and p.get("id") == project_id), None)
                if not project:
                    return self._error("作品不存在")
                for key, limit in (
                    ("title", 80),
                    ("work_type", 40),
                    ("premise", 500),
                    ("tone", 80),
                    ("point_of_view", 60),
                    ("next_hint", 240),
                    ("source_text", 500),
                ):
                    if key not in payload:
                        continue
                    value = self._single_line(payload.get(key), limit)
                    if value != self._single_line(project.get(key), limit):
                        project[key] = value
                        changed_notes.append(f"{key}: {value}")
                if "status" in payload:
                    status = self._single_line(payload.get("status"), 24)
                    if status in ("drafting", "finished", "paused") and status != project.get("status"):
                        project["status"] = status
                        changed_notes.append(f"status: {status}")
                if "target_chars" in payload:
                    target_chars = _safe_int(payload.get("target_chars"), 2000, 300, 5200)
                    if target_chars != self._int(project.get("target_chars")):
                        project["target_chars"] = target_chars
                        changed_notes.append(f"target_chars: {target_chars}")
                if changed_notes:
                    story_bible_getter = getattr(self.plugin, "_get_or_create_story_bible", None)
                    if callable(story_bible_getter):
                        story_bible = story_bible_getter(project)
                        if "premise" in payload:
                            story_bible["mainline_direction"] = self._single_line(project.get("premise"), 160)
                        if "next_hint" in payload:
                            story_bible["next_direction"] = self._single_line(project.get("next_hint"), 160)
                    edits = project.setdefault("manual_edits", [])
                    if not isinstance(edits, list):
                        edits = []
                        project["manual_edits"] = edits
                    now = time.time()
                    note = "；".join(changed_notes)
                    edits.append(
                        {
                            "id": secrets.token_hex(6),
                            "type": "project_meta",
                            "title": "更新作品信息",
                            "content": note[:2000],
                            "chunk_index": -1,
                            "created_at": now,
                        }
                    )
                    del edits[:-10]
                    project["last_manual_edit_at"] = now
                    project["last_manual_edit_summary"] = "更新作品信息"
                    pool_getter = getattr(self.plugin, "_get_or_create_memory_pool", None)
                    add_memory = getattr(self.plugin, "_add_memory_entry", None)
                    extract_keywords = getattr(self.plugin, "_extract_creative_keywords", None)
                    if callable(pool_getter) and callable(add_memory):
                        keywords = extract_keywords(note, limit=8) if callable(extract_keywords) else ["作品信息"]
                        add_memory(
                            pool_getter(project),
                            project_id,
                            "revision",
                            f"更新作品信息: {self._single_line(note, 180)}",
                            keywords or ["作品信息"],
                            importance=5,
                        )
                self.plugin._save_data_sync(sections={"creative_projects"})
            return self._ok({"project_id": project_id, "changed": bool(changed_notes), "message": "作品已更新"})
        except Exception as exc:
            logger.error("更新创作项目失败: %s", exc, exc_info=True)
            return self._exception_error("更新创作项目失败")

    @story_legacy_operation("page.creative.chunk-update")
    async def update_creative_chunk(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        project_id = str(payload.get("id", "")).strip()
        chunk_index = _safe_int(payload.get("chunk_index"), -1, -1)
        text = str(payload.get("text", "")).strip()
        if not project_id:
            return self._error("缺少 id")
        if chunk_index < 0:
            return self._error("缺少 chunk_index")
        if not text:
            return self._error("缺少 text")
        try:
            apply_edit = getattr(self.plugin, "_apply_creative_manual_edit", None)
            if not callable(apply_edit):
                return self._error("创作编辑能力不可用")
            result = await apply_edit(project_id, "chunk_text", text, f"修改第{chunk_index + 1}段", chunk_index)
            if not result.get("success"):
                return self._error(result.get("error") or "片段更新失败")
            return self._ok({"project_id": project_id, "chunk_index": chunk_index, "message": "片段已更新"})
        except Exception as exc:
            logger.error("更新创作片段失败: %s", exc, exc_info=True)
            return self._exception_error("更新创作片段失败")
