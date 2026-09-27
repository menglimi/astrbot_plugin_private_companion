# -*- coding: utf-8 -*-
"""reaction_library 域。

由 tools/split_mixin_domain.py 从 page_api.py 机械抽取（13 个方法 + 0 个模块级名字 + 0 个类级赋值 / 419 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApi）。
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from .helpers import _safe_int
from .reaction_asset_library import get_reaction_asset_library
from .page_api_shared import _page_api_host, _page_api_host_request as request
from typing import Any

from .logging_util import get_module_logger

logger = get_module_logger(__name__)



class PrivateCompanionPageApiReactionLibraryMixin:
    """reaction_library 域（从 PrivateCompanionPageApi 拆出）。"""


    def _reaction_library(self):
        library = get_reaction_asset_library(self.plugin)
        if library is None:
            raise RuntimeError("插件数据目录尚未初始化")
        return library

    def _reaction_library_analysis_lock(self) -> asyncio.Lock:
        lock = getattr(self.plugin, "_reaction_library_analysis_lock", None)
        if not isinstance(lock, asyncio.Lock):
            lock = asyncio.Lock()
            setattr(self.plugin, "_reaction_library_analysis_lock", lock)
        return lock

    @staticmethod
    def _parse_reaction_library_analysis(text: Any, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        raw = str(text or "").strip()
        if not raw:
            return []
        fenced = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", raw, flags=re.IGNORECASE)
        if fenced:
            raw = fenced.group(1).strip()
        candidates = [raw]
        array_start, array_end = raw.find("["), raw.rfind("]")
        if array_start >= 0 and array_end > array_start:
            candidates.append(raw[array_start : array_end + 1])
        parsed: Any = None
        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
                break
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        if isinstance(parsed, dict):
            parsed = parsed.get("items") or parsed.get("results") or parsed.get("images")
        if not isinstance(parsed, list):
            return []
        results: list[dict[str, Any]] = []
        used_indexes: set[int] = set()
        for row in parsed:
            if not isinstance(row, dict):
                continue
            index = _safe_int(row.get("image_index") or row.get("index"), 0, 0)
            if index < 1 or index > len(items) or index in used_indexes:
                continue
            used_indexes.add(index)
            item = items[index - 1]
            results.append(
                {
                    "id": item.get("id"),
                    "name": row.get("name"),
                    "description": row.get("description"),
                    "visible_text": row.get("visible_text") or row.get("text"),
                    "tags": row.get("tags"),
                    "emotions": row.get("emotions"),
                    "intents": row.get("intents") or row.get("purposes"),
                }
            )
        return results

    def _reaction_library_analysis_provider_candidates(self) -> list[tuple[str, str, str]]:
        """Prefer the plugin-owned vision card for reaction asset metadata."""
        candidates_getter = getattr(self.plugin, "_private_image_visual_provider_candidates", None)
        inherited = candidates_getter("") if callable(candidates_getter) else []
        inherited_rows = inherited if isinstance(inherited, list) else []
        configured_id = self._single_line(getattr(self.plugin, "plugin_vision_provider_id", ""), 160)
        fallback_getter = getattr(self.plugin, "_model_fallback_provider_id", None)
        configured_fallback_id = (
            self._single_line(
                fallback_getter("PLUGIN_VISION_PROVIDER_ID", configured_id),
                160,
            )
            if configured_id and callable(fallback_getter)
            else ""
        )

        ordered: list[tuple[str, str, str]] = []
        seen: set[str] = set()

        def append(provider_id: Any, source: Any, prompt: Any = "") -> None:
            clean_id = self._single_line(provider_id, 160)
            if not clean_id or clean_id in seen:
                return
            seen.add(clean_id)
            ordered.append(
                (
                    clean_id,
                    self._single_line(source, 80),
                    str(prompt or "").strip(),
                )
            )

        append(configured_id, "plugin_vision")
        append(configured_fallback_id, "plugin_vision_fallback")
        for row in inherited_rows:
            if not isinstance(row, (list, tuple)) or not row:
                continue
            append(
                row[0],
                row[1] if len(row) > 1 else "",
                row[2] if len(row) > 2 else "",
            )
        return ordered

    async def _call_reaction_library_analysis_provider(
        self,
        items: list[dict[str, Any]],
        image_urls: list[str],
    ) -> tuple[list[dict[str, Any]], str, str]:
        provider_getter = getattr(self.plugin, "_private_image_provider_by_id", None)
        supports_image = getattr(self.plugin, "_provider_supports_image", None)
        cooldown_check = getattr(self.plugin, "_private_image_provider_in_failure_cooldown", None)
        candidate_rows = self._reaction_library_analysis_provider_candidates()
        prompt = self._reaction_library_analysis_prompt(items)
        last_error = "未配置可用的视觉模型"
        seen: set[str] = set()
        primary_visual_id = next(
            (
                self._single_line(row[0], 160)
                for row in candidate_rows
                if isinstance(row, (list, tuple)) and len(row) >= 2 and row[1] == "plugin_vision"
            ),
            "",
        )
        fallback_visual_id = next(
            (
                self._single_line(row[0], 160)
                for row in candidate_rows
                if isinstance(row, (list, tuple)) and len(row) >= 2 and row[1] == "plugin_vision_fallback"
            ),
            "",
        )
        configured_visual_id = self._single_line(
            getattr(self.plugin, "plugin_vision_provider_id", ""),
            160,
        )
        prompt_applier = getattr(self.plugin, "_apply_task_prompt_override_for_call", None)
        if callable(prompt_applier):
            prompt, _unused_system_prompt = prompt_applier(
                "reaction_library_analysis",
                prompt,
                None,
                flatten_system_prompt=True,
            )
        visual_key_getter = getattr(self.plugin, "_private_image_visual_provider_card_key", None)
        visual_provider_key = (
            "PLUGIN_VISION_PROVIDER_ID"
            if configured_visual_id
            else visual_key_getter()
            if callable(visual_key_getter)
            else "PLUGIN_VISION_PROVIDER_ID"
        )
        for row in candidate_rows:
            if not isinstance(row, (list, tuple)) or not row:
                continue
            provider_id = self._single_line(row[0], 160)
            provider_source = self._single_line(row[1] if len(row) > 1 else "", 80)
            if not provider_id or provider_id in seen:
                continue
            seen.add(provider_id)
            if callable(cooldown_check) and cooldown_check(provider_id, provider_source):
                continue
            provider = provider_getter(provider_id) if callable(provider_getter) else None
            if provider is None or (callable(supports_image) and not supports_image(provider)):
                continue
            token_skip_getter = getattr(self.plugin, "_model_token_limit_should_skip_primary", None)
            if callable(token_skip_getter) and token_skip_getter(
                task="reaction_library_analysis",
                provider_id=provider_id,
                primary_provider_id=primary_visual_id,
                fallback_provider_id=fallback_visual_id,
                provider_key=visual_provider_key,
                prompt=prompt,
                max_tokens=1200,
                image_count=len(image_urls),
            ):
                recorder = getattr(self.plugin, "_record_llm_usage", None)
                if callable(recorder):
                    recorder(
                        provider_id=provider_id,
                        task="reaction_library_analysis",
                        prompt=prompt,
                        completion="",
                        elapsed_ms=0,
                        success=False,
                        error="model_token_limit_exceeded",
                        budget_exempt=False,
                    )
                last_error = "主视觉模型预估超出 Token 上限，已切换备用模型"
                continue
            budget_check = getattr(self.plugin, "_can_run_llm_task", None)
            if callable(budget_check) and not budget_check(provider_id, task="reaction_library_analysis"):
                last_error = "视觉模型调用预算已达到限制"
                continue
            started = time.time()
            result: Any = None
            completion = ""
            try:
                timeout_getter = getattr(self.plugin, "_private_image_provider_timeout_seconds", None)
                timeout = float(timeout_getter(provider_id, provider_source)) if callable(timeout_getter) else 30.0
                request_call = provider.text_chat(prompt=prompt, image_urls=image_urls)
                result = await asyncio.wait_for(request_call, timeout=timeout) if timeout > 0 else await request_call
                completion = str(getattr(result, "completion_text", result) or "").strip()
                parsed = self._parse_reaction_library_analysis(completion, items)
                if not parsed:
                    raise ValueError("视觉模型未返回可解析的素材 JSON")
                recorder = getattr(self.plugin, "_record_llm_usage", None)
                if callable(recorder):
                    recorder(
                        provider_id=provider_id,
                        task="reaction_library_analysis",
                        prompt=prompt,
                        completion=completion,
                        elapsed_ms=int((time.time() - started) * 1000),
                        success=True,
                        resp=result,
                        budget_exempt=False,
                    )
                success_notifier = getattr(self.plugin, "_note_private_image_visual_provider_success", None)
                if callable(success_notifier):
                    success_notifier(provider_id, provider_source, scope="reaction_library", chars=len(completion))
                return parsed, provider_id, ""
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                last_error = self._visual_call_error_text(exc, timeout=timeout)
                recorder = getattr(self.plugin, "_record_llm_usage", None)
                if callable(recorder):
                    recorder(
                        provider_id=provider_id,
                        task="reaction_library_analysis",
                        prompt=prompt,
                        completion=completion,
                        elapsed_ms=int((time.time() - started) * 1000),
                        success=False,
                        error=last_error,
                        resp=result,
                        budget_exempt=False,
                    )
                logger.info(
                    "表情包自动识别尝试下一个视觉模型: provider=%s images=%s exception=%s error=%s",
                    provider_id,
                    len(image_urls),
                    exc.__class__.__name__,
                    last_error,
                )
        return [], "", last_error

    async def _run_reaction_library_analysis_queue(self) -> None:
        library = self._reaction_library()
        async with self._reaction_library_analysis_lock():
            while True:
                items = await asyncio.to_thread(
                    library.analysis_candidates,
                    statuses=("pending", "running"),
                    limit=4,
                )
                if not items:
                    return
                ids = [str(item.get("id") or "") for item in items if item.get("id")]
                await asyncio.to_thread(library.mark_analysis_running, ids)
                images = await asyncio.gather(
                    *(asyncio.to_thread(library.get_analysis_image_data, item_id) for item_id in ids)
                )
                usable_items: list[dict[str, Any]] = []
                image_urls: list[str] = []
                unavailable: list[str] = []
                for item, image in zip(items, images):
                    if isinstance(image, dict) and image.get("data_url"):
                        usable_items.append(item)
                        image_urls.append(str(image["data_url"]))
                    else:
                        unavailable.append(str(item.get("id") or ""))
                if unavailable:
                    await asyncio.to_thread(library.mark_analysis_failed, unavailable, "图片文件不存在或无法读取")
                if not usable_items:
                    continue
                results, provider_id, error = await self._call_reaction_library_analysis_provider(
                    usable_items,
                    image_urls,
                )
                if not results:
                    await asyncio.to_thread(
                        library.mark_analysis_failed,
                        [str(item.get("id") or "") for item in usable_items],
                        error,
                    )
                    continue
                applied = await asyncio.to_thread(
                    library.apply_analysis_results,
                    results,
                    provider_id=provider_id,
                )
                completed_ids = set(applied.get("ids") or [])
                missing_ids = [
                    str(item.get("id") or "")
                    for item in usable_items
                    if str(item.get("id") or "") not in completed_ids
                ]
                if missing_ids:
                    await asyncio.to_thread(library.mark_analysis_failed, missing_ids, "视觉模型遗漏了这张图片")

    def _schedule_reaction_library_analysis(self) -> asyncio.Task[Any] | None:
        current = getattr(self.plugin, "_reaction_library_analysis_task", None)
        if isinstance(current, asyncio.Task) and not current.done():
            return current
        operation = self._run_reaction_library_analysis_queue()
        tracker = getattr(self.plugin, "_create_lifecycle_background_task", None)
        if callable(tracker):
            task = tracker(operation, label="reaction_library_analysis")
        else:
            task = asyncio.create_task(operation, name="private_companion_reaction_library_analysis")
        if isinstance(task, asyncio.Task):
            setattr(self.plugin, "_reaction_library_analysis_task", task)

            def restart_if_queue_refilled(finished: asyncio.Task[Any]) -> None:
                if getattr(self.plugin, "_reaction_library_analysis_task", None) is finished:
                    setattr(self.plugin, "_reaction_library_analysis_task", None)
                if finished.cancelled():
                    return
                try:
                    if finished.exception() is not None:
                        return
                    pending = _safe_int(self._reaction_library().summary().get("analysis_pending"), 0, 0)
                except Exception:
                    return
                if pending:
                    self._schedule_reaction_library_analysis()

            task.add_done_callback(restart_if_queue_refilled)
        return task

    async def list_reaction_library(self) -> dict[str, Any]:
        try:
            data = await asyncio.to_thread(
                self._reaction_library().list_items,
                query=self._single_line(request.args.get("q"), 160),
                status=self._single_line(request.args.get("status"), 20) or "all",
                scope=self._single_line(request.args.get("scope"), 20) or "all",
                analysis=self._single_line(request.args.get("analysis"), 20) or "all",
                page=_safe_int(request.args.get("page"), 1, 1),
                page_size=_safe_int(request.args.get("page_size"), 48, 1, 120),
            )
            if _safe_int(data.get("summary", {}).get("analysis_pending"), 0, 0) > 0:
                self._schedule_reaction_library_analysis()
            return self._ok(data)
        except Exception as exc:
            logger.error("读取表情包素材库失败: %s", exc, exc_info=True)
            return self._error(str(exc))

    async def import_reaction_library(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        files = payload.get("files") if isinstance(payload, dict) else None
        if not isinstance(files, list) or not files:
            return self._error("请选择图片或 ZIP 文件")
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        try:
            result = await asyncio.to_thread(
                self._reaction_library().import_base64_payloads,
                files,
                metadata=metadata,
            )
            result["message"] = (
                f"已导入 {result.get('imported', 0)} 张，跳过 {len(result.get('duplicates', []))} 张重复素材"
            )
            if _safe_int(result.get("analysis_queued"), 0, 0) > 0:
                self._schedule_reaction_library_analysis()
                result["message"] += f"；{result.get('analysis_queued', 0)} 张已进入自动识别"
            return self._ok(result)
        except Exception as exc:
            logger.error("导入表情包失败: %s", exc, exc_info=True)
            return self._error(str(exc))

    async def analyze_reaction_library(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        ids = payload.get("ids") if isinstance(payload.get("ids"), list) else []
        if not ids:
            return self._error("没有选择要识别的表情包")
        try:
            result = await asyncio.to_thread(
                self._reaction_library().queue_analysis,
                ids,
                include_complete=bool(payload.get("force", True)),
            )
            if result.get("queued"):
                self._schedule_reaction_library_analysis()
            result["message"] = f"已将 {result.get('queued', 0)} 张素材加入识别队列"
            return self._ok(result)
        except Exception as exc:
            logger.error("表情包自动识别排队失败: %s", exc, exc_info=True)
            return self._error(str(exc))

    async def update_reaction_library(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        ids = payload.get("ids") if isinstance(payload.get("ids"), list) else []
        changes = payload.get("changes") if isinstance(payload.get("changes"), dict) else {}
        if not ids:
            return self._error("没有选择表情包")
        if not changes:
            return self._error("没有可保存的修改")
        try:
            result = await asyncio.to_thread(self._reaction_library().update_items, ids, changes)
            return self._ok(result)
        except Exception as exc:
            logger.error("更新表情包失败: %s", exc, exc_info=True)
            return self._error(str(exc))

    async def delete_reaction_library(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        ids = payload.get("ids") if isinstance(payload.get("ids"), list) else []
        if not ids:
            return self._error("没有选择表情包")
        if payload.get("confirm") is not True:
            return self._error("删除需要 confirm=true")
        try:
            return self._ok(await asyncio.to_thread(self._reaction_library().delete_items, ids))
        except Exception as exc:
            logger.error("删除表情包失败: %s", exc, exc_info=True)
            return self._error(str(exc))

    async def rescan_reaction_library(self) -> dict[str, Any]:
        try:
            result = await asyncio.to_thread(self._reaction_library().rescan)
            if _safe_int(result.get("analysis_queued"), 0, 0) > 0:
                self._schedule_reaction_library_analysis()
            duplicate_count = len(result.get("duplicates", [])) if isinstance(result.get("duplicates"), list) else 0
            if duplicate_count:
                result["message"] = f"已重建索引；发现 {duplicate_count} 个重复文件，未重复导入"
            return self._ok(result)
        except Exception as exc:
            logger.error("重建表情包索引失败: %s", exc, exc_info=True)
            return self._error(str(exc))
