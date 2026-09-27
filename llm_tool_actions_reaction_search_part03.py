# -*- coding: utf-8 -*-
"""LlmToolActionsReactionSearchPart03Mixin。

由 tools/split_mixin_domain.py 从 llm_tool_actions_reaction_search.py 机械抽取（1 个方法 + 0 个模块级名字 + 0 个类级赋值 / 553 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 LlmToolActionsReactionSearchMixin）。
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from .helpers import _path_text, _safe_float, _single_line
from .llm_tool_actions_shared import PHOTO_TOOL_SILENT_SENTINEL, logger
from .persona_config import runtime_persona_setting
from astrbot.api.event import AstrMessageEvent
from typing import Any



class LlmToolActionsReactionSearchPart03Mixin:
    """LlmToolActionsReactionSearchPart03Mixin（从 LlmToolActionsReactionSearchMixin 拆出）。"""


    async def _pc_find_reaction_image_impl(
        self,
        event: AstrMessageEvent,
        query: str = "",
        search_context: str = "",
        meme_only: bool = True,
        send: bool = True,
        caption: str = "",
        low_latency: bool = False,
        internal_attachment: bool = False,
        context: str = "",
        selection_preferences: Any = None,
        selection_signature: str = "",
    ) -> str:
        scope = self._reaction_expression_scope(event)
        preference_snapshot = (
            selection_preferences if isinstance(selection_preferences, dict) else {}
        )
        preference_signature = _single_line(
            selection_signature or preference_snapshot.get("intent_signature"),
            40,
        )
        preference_revision = self._reaction_expression_selection_revision(
            preference_snapshot,
            preference_signature,
        )
        query_text = _single_line(query, 500)
        if not query_text:
            getter = getattr(event, "get_message_str", None)
            query_text = _single_line(
                getter() if callable(getter) else getattr(event, "message_str", ""),
                500,
            )
        if not query_text:
            self._log_reaction_expression_event(
                event,
                stage="lookup",
                decision="miss",
                reason="missing_query",
                scope=scope,
                status="need_query",
                found=False,
                sent=False,
            )
            return json.dumps(
                {
                    "status": "need_query",
                    "success": False,
                    "found": False,
                    "sent": False,
                    "message": "缺少表情包检索需求",
                    "must_not_claim_sent": True,
                },
                ensure_ascii=False,
            )

        def bool_arg(value: Any, default: bool) -> bool:
            if isinstance(value, bool):
                return value
            if value is None:
                return default
            normalized = str(value).strip().lower()
            if normalized in {"1", "true", "yes", "on", "是", "发送"}:
                return True
            if normalized in {"0", "false", "no", "off", "否", "不发送"}:
                return False
            return default

        send_image = bool_arg(send, True)
        meme_filter = bool_arg(meme_only, True)
        visible_caption = self._sanitize_photo_tool_caption(caption, limit=500)
        if send_image and not visible_caption:
            return json.dumps(
                {
                    "status": "missing_visible_caption",
                    "success": False,
                    "found": False,
                    "sent": False,
                    "message": "发送表情包前需要同时提供一条完整的可见正文",
                    "must_not_claim_sent": True,
                    "final_response_instruction": "请保留完整自然文字回复；不要用图片替代正文。",
                },
                ensure_ascii=False,
            )
        if send_image:
            caption = visible_caption
        if not search_context and isinstance(context, str):
            search_context = context
        lookup_context = _single_line(search_context, 1000)
        snapshot_builder = getattr(self, "_build_companion_scene_snapshot", None)
        snapshot_formatter = getattr(self, "_format_companion_scene_snapshot", None)
        if callable(snapshot_builder) and callable(snapshot_formatter):
            try:
                sender_getter = getattr(event, "get_sender_id", None)
                sender_id = self._reaction_expression_event_storage_id(
                    event,
                    sender_getter() if callable(sender_getter) else "",
                )
                users = self.data.get("users") if isinstance(getattr(self, "data", None), dict) and isinstance(self.data.get("users"), dict) else {}
                current_user = users.get(sender_id) if sender_id else None
                if isinstance(current_user, dict):
                    current_user = dict(current_user)
                    current_user.setdefault("user_id", sender_id)
                scene_text = _single_line(
                    snapshot_formatter(
                        snapshot_builder(current_user if isinstance(current_user, dict) else None),
                        purpose="image_search",
                    ),
                    620,
                )
                if scene_text:
                    scene_note = f"Bot当前情境（仅辅助判断回应情绪，不覆盖用户的明确需求）：{scene_text}"
                    lookup_context = _single_line(
                        "；".join(part for part in (lookup_context, scene_note) if part),
                        1000,
                    )
            except Exception as exc:
                self._log_reaction_expression_event(
                    event,
                    stage="degrade",
                    decision="failed",
                    reason="scene_snapshot_failed",
                    scope=scope,
                    error_type=type(exc).__name__,
                )

        # Q6 is an optional, hash-locked local source. A hit wins before the
        # editable reaction library, while every miss preserves its existing
        # lookup, authorization, reservation and delivery behavior.
        owned_lookup_finder = getattr(self, "_find_owned_reaction_asset", None)
        owned_lookup = (
            owned_lookup_finder(
                query_text,
                search_context=lookup_context,
                meme_only=meme_filter,
            )
            if callable(owned_lookup_finder)
            else None
        )
        library = self._reaction_asset_library()
        if owned_lookup is None and (library is None or not library.has_enabled_assets()):
            self._log_reaction_expression_event(
                event,
                stage="lookup",
                decision="miss",
                reason="library_unavailable",
                scope=scope,
                status="unavailable",
                found=False,
                sent=False,
            )
            return json.dumps(
                {
                    "status": "unavailable",
                    "success": False,
                    "found": False,
                    "sent": False,
                    "message": "Private Companion 表情包素材库为空，请先在实验功能页导入并启用素材",
                    "must_not_claim_sent": True,
                },
                ensure_ascii=False,
            )

        embedding_provider = None
        embedding_provider_id = ""
        embedding_query: list[float] = []
        if bool(runtime_persona_setting(self, 'reaction_expression_embedding_enabled', False)):
            try:
                embedding_provider, embedding_provider_id = await self._reaction_embedding_provider()
                if embedding_provider is not None and embedding_provider_id:
                    setattr(self, "_reaction_embedding_active_provider_id", embedding_provider_id)
                    self._schedule_reaction_embedding_backfill(
                        library, embedding_provider, embedding_provider_id
                    )
                    embedding_query = await self._reaction_embedding_vector(
                        embedding_provider,
                        "；".join(part for part in (query_text, lookup_context) if part),
                    )
            except Exception as exc:
                logger.debug(
                    "表情查询向量生成失败，回退关键词: provider=%s error_type=%s",
                    embedding_provider_id or "<auto>",
                    type(exc).__name__,
                )
                embedding_query = []

        lookup_started = time.perf_counter()
        cache_hit = False
        lookup_error_type = ""
        lookup = dict(owned_lookup) if isinstance(owned_lookup, dict) else None
        if lookup is None:
            lookup_revision = self._reaction_expression_lookup_cache_revision(library)
            if embedding_provider_id:
                lookup_revision = f"{lookup_revision}|embedding:{embedding_provider_id}"
            if preference_revision:
                lookup_revision = f"{lookup_revision}|preference:{preference_revision}"
            cache_key = self._reaction_expression_lookup_cache_key(
                library,
                query_text,
                lookup_context,
                meme_filter,
                scope,
                lookup_revision,
            )
            lookup = (
                self._reaction_expression_lookup_cache_get(cache_key)
                if low_latency
                else None
            )
            if isinstance(lookup, dict):
                cache_hit = True
        if lookup is None:
            try:
                find_kwargs = {
                    "context": lookup_context,
                    "scope": scope,
                    "selection_preferences": preference_snapshot,
                    "selection_signature": preference_signature,
                }
                if embedding_query and embedding_provider_id:
                    find_kwargs.update(
                        {
                            "embedding_query": embedding_query,
                            "embedding_provider_id": embedding_provider_id,
                            "embedding_score_threshold": runtime_persona_setting(self, 'reaction_expression_embedding_score_threshold', 0.42),
                            "embedding_weight": runtime_persona_setting(self, 'reaction_expression_embedding_weight', 0.7),
                            "embedding_candidate_limit": runtime_persona_setting(self, 'reaction_expression_embedding_candidate_limit', 1200),
                        }
                    )
                lookup = await asyncio.to_thread(
                    library.find,
                    query_text,
                    **find_kwargs,
                )
                if lookup is None:
                    lookup = {
                        "success": False,
                        "status": "not_found",
                        "message": "素材库中没有足够贴合当前语境的表情包",
                    }
            except Exception as exc:
                lookup_error_type = type(exc).__name__
                logger.warning(
                    "自有表情包素材库检索失败: error_type=%s",
                    lookup_error_type,
                )
                lookup = {
                    "success": False,
                    "status": "error",
                    "message": f"图库检索失败：{_single_line(exc, 160)}",
                }
            if low_latency and isinstance(lookup, dict) and owned_lookup is None:
                self._reaction_expression_lookup_cache_put(cache_key, lookup)
        lookup_latency_ms = round(
            max(0.0, (time.perf_counter() - lookup_started) * 1000.0), 2
        )
        if low_latency:
            self._note_reaction_expression_runtime(
                lookups=0 if cache_hit else 1,
                cache_hits=1 if cache_hit else 0,
                last_reason="cache_hit" if cache_hit else "lookup",
                latency_ms=lookup_latency_ms,
                lookup_elapsed_ms=0.0 if cache_hit else lookup_latency_ms,
            )
        if not isinstance(lookup, dict) or not lookup.get("success"):
            lookup = lookup if isinstance(lookup, dict) else {}
            lookup_status = _single_line(lookup.get("status"), 40) or "not_found"
            self._log_reaction_expression_event(
                event,
                stage="lookup",
                decision="miss",
                reason="lookup_error" if lookup_status == "error" else lookup_status,
                scope=scope,
                status=lookup_status,
                found=False,
                sent=False,
                cache_hit=cache_hit,
                latency_ms=lookup_latency_ms,
                error_type=lookup_error_type,
            )
            return json.dumps(
                {
                    "status": lookup_status,
                    "success": False,
                    "found": False,
                    "sent": False,
                    "message": _single_line(lookup.get("message"), 220) or "图库中没有找到合适的表情包",
                    "need": _single_line(lookup.get("need"), 220),
                    "reason": _single_line(lookup.get("reason"), 220),
                    "cache_hit": cache_hit,
                    "lookup_latency_ms": lookup_latency_ms,
                    "must_not_claim_sent": True,
                },
                ensure_ascii=False,
            )

        image_path = _path_text(lookup.get("path"), 1000)
        if not image_path or not os.path.isfile(image_path):
            self._log_reaction_expression_event(
                event,
                stage="lookup",
                decision="miss",
                reason="missing_file",
                scope=scope,
                status="missing_file",
                found=False,
                sent=False,
                image_id=lookup.get("image_id"),
                confidence=lookup.get("confidence"),
                cache_hit=cache_hit,
                latency_ms=lookup_latency_ms,
                match_basis=self._reaction_expression_match_basis(lookup),
            )
            return json.dumps(
                {
                    "status": "missing_file",
                    "success": False,
                    "found": False,
                    "sent": False,
                    "message": "匹配到的图库图片文件不可用",
                    "cache_hit": cache_hit,
                    "lookup_latency_ms": lookup_latency_ms,
                    "must_not_claim_sent": True,
                },
                ensure_ascii=False,
            )

        self._log_reaction_expression_event(
            event,
            stage="lookup",
            decision="hit",
            reason="matched",
            scope=scope,
            status=_single_line(lookup.get("status"), 40) or "success",
            found=True,
            sent=False,
            image_id=lookup.get("image_id"),
            confidence=lookup.get("confidence"),
            cache_hit=cache_hit,
            latency_ms=lookup_latency_ms,
            match_basis=self._reaction_expression_match_basis(lookup),
        )

        vision_review: dict[str, Any] | None = None
        verify_mode = _single_line(
            runtime_persona_setting(self, "reaction_expression_vision_verify_mode", "embedding"), 20
        ).lower()
        # The automatic tag path prepares the image here (send=False,
        # internal_attachment=True) and delivers it after the text, so it needs
        # the same pre-send check as a direct tool send.
        if (send_image or internal_attachment) and not low_latency and (
            verify_mode == "always"
            or (verify_mode == "embedding" and lookup.get("match_basis") == "embedding")
        ):
            vision_review = await self._reaction_expression_vision_verify(
                event, library, lookup, query_text, lookup_context
            )
            if vision_review is not None and not vision_review.get("fit"):
                # Reservation ownership belongs to ``_pc_reaction_expression_impl``.
                # This helper is also called directly by the public lookup tool,
                # where those variables do not exist.  The caller consumes this
                # miss result and releases its own reservation when applicable.
                self._log_reaction_expression_event(
                    event,
                    stage="lookup",
                    decision="miss",
                    reason="vision_rejected",
                    scope=scope,
                    status="not_found",
                    found=False,
                    sent=False,
                    image_id=lookup.get("image_id"),
                    confidence=lookup.get("confidence"),
                    cache_hit=cache_hit,
                    latency_ms=lookup_latency_ms,
                    match_basis=self._reaction_expression_match_basis(lookup),
                )
                return json.dumps(
                    {
                        "status": "not_found",
                        "success": False,
                        "found": False,
                        "sent": False,
                        "message": "候选表情包经视觉复核后不贴合当前语境，未发送",
                        "image_description": _single_line(vision_review.get("description"), 300),
                        "reason": _single_line(vision_review.get("reason"), 120),
                        "cache_hit": cache_hit,
                        "lookup_latency_ms": lookup_latency_ms,
                        "must_not_claim_sent": True,
                    },
                    ensure_ascii=False,
                )

        sent = False
        delivery: dict[str, Any] = {}
        visible_caption = self._sanitize_photo_tool_caption(caption, limit=120)
        if send_image:
            try:
                delivery = await self._deliver_generated_image_to_event(
                    event,
                    image_path=image_path,
                    caption=visible_caption,
                    reaction_image=True,
                )
            except Exception as exc:
                delivery = {
                    "sent": False,
                    "destination": "error",
                    "message": f"图片发送失败：{_single_line(exc, 180) or '未知错误'}",
                }
            sent = bool(delivery.get("sent"))
            if sent:
                try:
                    setattr(event, "_private_companion_photo_tool_sent", True)
                    setattr(event, "_private_companion_photo_tool_sent_caption", visible_caption)
                except Exception:
                    pass

        tags = [
            _single_line(item, 60)
            for item in lookup.get("tags", [])
            if _single_line(item, 60)
        ]
        need = _single_line(lookup.get("need"), 220) or query_text
        match_reason = _single_line(lookup.get("reason"), 220)
        snapshot_caption = "；".join(
            part
            for part in (
                f"图片画面：{_single_line(lookup.get('description') or lookup.get('image_description'), 200)}"
                if lookup.get("description") or lookup.get("image_description")
                else "",
                f"图库标签：{'、'.join(tags[:8])}" if tags else "",
                f"表达需求：{need}" if need else "",
                f"选图依据：{match_reason}" if match_reason else "",
            )
            if part
        )
        if sent and snapshot_caption:
            try:
                user_id = self._reaction_expression_event_storage_id(event, event.get_sender_id())
            except Exception:
                user_id = ""
            if user_id:
                async with self._data_lock:
                    user = self._reaction_expression_state_owner(event, user_id)
                    if not isinstance(user, dict):
                        return json.dumps(
                            self._reaction_expression_skip_result(
                                "state_unavailable",
                                event=event,
                                scope=scope,
                            ),
                            ensure_ascii=False,
                        )
                    self._remember_recent_photo_share_snapshot(
                        user,
                        caption=snapshot_caption,
                        topic=need,
                        motive=match_reason,
                        reason="reaction_library_image",
                        subject_owner="unknown",
                    )
                    try:
                        self._save_data_sync(sections={"users"})
                    except TypeError:
                        # Keep lightweight hosts/test doubles compatible with
                        # the historical no-argument persistence hook.
                        self._save_data_sync()

        if sent:
            self._mark_reaction_asset_used(lookup.get("image_id"), event=event)
        delivery_uncertain = bool(delivery.get("uncertain"))
        success = bool(image_path and (not send_image or sent))
        if send_image:
            self._log_reaction_expression_event(
                event,
                stage="delivery",
                decision=(
                    "sent"
                    if sent
                    else "uncertain"
                    if delivery_uncertain
                    else "failed"
                ),
                reason=(
                    "delivered"
                    if sent
                    else "delivery_uncertain"
                    if delivery_uncertain
                    else "delivery_failed"
                ),
                scope=scope,
                status=(
                    "success"
                    if sent
                    else "delivery_uncertain"
                    if delivery_uncertain
                    else "delivery_failed"
                ),
                found=True,
                sent=sent,
                image_id=lookup.get("image_id"),
                confidence=lookup.get("confidence"),
                cache_hit=cache_hit,
                latency_ms=lookup_latency_ms,
                delivery=delivery.get("destination"),
                match_basis=self._reaction_expression_match_basis(lookup),
            )
        result_payload = {
            "status": (
                "success"
                if success
                else "delivery_uncertain"
                if delivery_uncertain
                else "delivery_failed"
            ),
            "success": success,
            "found": True,
            "send_requested": send_image,
            "sent": sent,
            "delivery_uncertain": delivery_uncertain,
            "message": (
                _single_line(delivery.get("message"), 220)
                if send_image
                else "已找到图库图片，但按请求未发送"
            ),
            "image_id": _single_line(lookup.get("image_id"), 120),
            "tags": tags,
            "need": need,
            "reason": match_reason,
            "confidence": _safe_float(lookup.get("confidence"), 0.0, 0.0, 1.0),
            "image_description": _single_line(
                (vision_review or {}).get("description")
                or getattr(self, "_reaction_vision_description_cache", {}).get(_single_line(lookup.get("asset_id"), 64))
                or lookup.get("description"),
                300,
            ),
            "delivery": _single_line(delivery.get("destination"), 40),
            "cache_hit": cache_hit,
            "lookup_latency_ms": lookup_latency_ms,
            "must_not_claim_sent": not sent,
            "final_response_instruction": (
                f"完整正文 caption 与图片已一并发送。最终回复不要留空，只输出 {PHOTO_TOOL_SILENT_SENTINEL}。"
                if sent
                else ""
            ),
        }
        if (
            _single_line(lookup.get("source"), 60) != "owned_reaction_assets"
            or internal_attachment
        ):
            result_payload["path"] = image_path
        return json.dumps(result_payload, ensure_ascii=False)
