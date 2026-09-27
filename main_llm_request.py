# -*- coding: utf-8 -*-
"""LLM 请求钩子域。

由 tools/split_main_domain_v2.py 从 main.py 机械抽取（11 个方法 / 521 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPlugin）。
"""
from __future__ import annotations
from typing import Any

from .conversation_injection_plan import PLACEMENT_DYNAMIC_SYSTEM, get_conversation_injection_plan
from .conversation_prompt_section import prompt_section
from .helpers import _safe_int, _single_line
from .main_shared import _multi_persona_event_context
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest
from astrbot.core.provider.entities import LLMResponse

from .logging_util import get_module_logger

logger = get_module_logger(__name__)

class PrivateCompanionPluginLlmRequestMixin:
    """LLM 请求钩子域（从 PrivateCompanionPlugin 拆出）。"""

    @filter.on_llm_tool_respond()
    @_multi_persona_event_context
    async def capture_future_task_result(
        self,
        event: AstrMessageEvent,
        tool: Any,
        tool_args: dict[str, Any] | None,
        tool_result: Any,
        *args,
        **kwargs,
    ):
        """记录官方定时与创作读取工具的真实结果，供响应阶段可靠校验。"""
        if self is None or event is None:
            return
        await self._record_official_llm_timer_tool_result(event, tool, tool_result)
        if self._record_future_task_result(event, tool, tool_args, tool_result):
            logger.info(
                "已记录本轮 future_task 成功: action=%s session=%s",
                _single_line((tool_args or {}).get("action"), 20) or "unknown",
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            )
        if self._record_creative_work_tool_result(event, tool, tool_args, tool_result):
            logger.info(
                "已记录本轮创作读取工具结果: action=%s status=%s inventory_complete=%s session=%s",
                _single_line((tool_args or {}).get("action") if isinstance(tool_args, dict) else "", 20) or "get",
                _single_line(getattr(event, "private_companion_creative_work_tool_status", ""), 24) or "unknown",
                bool(getattr(event, "private_companion_bookshelf_inventory_complete", False)),
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
            )

    def _llm_request_provider_settings_for_event(self, event: AstrMessageEvent | None) -> dict[str, Any]:
        umo = str(getattr(event, "unified_msg_origin", "") or "")
        resolver = getattr(self, "_astrbot_provider_settings_for_umo", None)
        if callable(resolver):
            try:
                return dict(resolver(umo) or {})
            except Exception:
                pass
        try:
            cfg = self.context.get_config(umo=umo) if umo else self.context.get_config()
        except TypeError:
            try:
                cfg = self.context.get_config(umo) if umo else self.context.get_config()
            except Exception:
                cfg = {}
        except Exception:
            cfg = {}
        settings = cfg.get("provider_settings", {}) if isinstance(cfg, dict) else {}
        return dict(settings or {}) if isinstance(settings, dict) else {}

    def _llm_request_provider_identity_parts(self, event: AstrMessageEvent | None, req: ProviderRequest | None) -> list[str]:
        parts: list[str] = []

        def add(value: Any) -> None:
            text = _single_line(value, 200)
            if text and text not in parts:
                parts.append(text)

        if req is not None:
            for key in ("provider_id", "llm_provider_id", "chat_provider_id", "model"):
                add(getattr(req, key, ""))
        settings = self._llm_request_provider_settings_for_event(event)
        for key in (
            "default_provider_id",
            "default_llm_provider_id",
            "provider_id",
            "model",
            "api_base",
            "base_url",
        ):
            add(settings.get(key))
        context = getattr(self, "context", None)
        get_using = getattr(context, "get_using_provider", None)
        if callable(get_using):
            umo = str(getattr(event, "unified_msg_origin", "") or "")
            provider = None
            try:
                provider = get_using(umo=umo) if umo else get_using()
            except TypeError:
                try:
                    provider = get_using(umo) if umo else get_using(None)
                except Exception:
                    provider = None
            except Exception:
                provider = None
            if provider is not None:
                try:
                    meta = provider.meta()
                    if isinstance(meta, dict):
                        for key in ("id", "model", "type"):
                            add(meta.get(key))
                    else:
                        for key in ("id", "model", "type"):
                            add(getattr(meta, key, ""))
                except Exception:
                    pass
                config = getattr(provider, "provider_config", None) or getattr(provider, "config", None) or {}
                if isinstance(config, dict):
                    for key in ("id", "provider_id", "provider", "model", "api_base", "base_url"):
                        add(config.get(key))
        return parts

    def _llm_request_uses_gemini_family_provider(self, event: AstrMessageEvent | None, req: ProviderRequest | None) -> bool:
        identity = " ".join(self._llm_request_provider_identity_parts(event, req)).lower()
        return any(
            marker in identity
            for marker in (
                "gemini",
                "generativelanguage.googleapis.com",
                "googleapis.com/v1beta/openai",
            )
        )

    def _llm_request_uses_deepseek_family_provider(self, event: AstrMessageEvent | None, req: ProviderRequest | None) -> bool:
        identity = " ".join(self._llm_request_provider_identity_parts(event, req)).lower()
        return "deepseek" in identity

    def _llm_request_uses_deepseek_openai_compatible_provider(
        self,
        event: AstrMessageEvent | None,
        req: ProviderRequest | None,
    ) -> bool:
        """Limit history cleanup to DeepSeek's OpenAI-compatible endpoint."""
        identity = " ".join(self._llm_request_provider_identity_parts(event, req)).lower()
        return "deepseek" in identity

    def _append_deepseek_tool_protocol_guard(self, event: AstrMessageEvent, req: ProviderRequest) -> bool:
        if getattr(req, "func_tool", None) is None:
            return False
        if not self._llm_request_uses_deepseek_family_provider(event, req):
            return False
        marker = "<!-- private_companion_tool_protocol_v1 -->"
        current_prompt = str(getattr(req, "system_prompt", "") or "")
        if marker in current_prompt:
            return False
        instruction = (
            "当前模型兼容接口会严格核对每个 tool_call_id 与工具结果。"
            "需要使用多个工具时，请按顺序逐个调用：每条 assistant 消息只发起一个工具调用，"
            "拿到该工具结果后再决定是否调用下一个；不要并行或批量发起 tool_calls。"
            "回复当前会话的普通文字时，直接输出最终回复，不要调用 send_message_to_user；"
            "确需使用该工具发送媒体或主动消息时，plain 文本不得为空，调用同一轮不要额外输出可见正文。"
        )
        section = prompt_section(
            key="tools.deepseek_protocol",
            title="工具调用协议",
            source="tools",
            content=instruction,
        )
        self._materialize_conversation_system_block(
            req,
            section=section,
            marker=marker,
            priority=10,
            placement=PLACEMENT_DYNAMIC_SYSTEM,
        )
        return True

    def _append_passive_reply_tool_boundary(self, event: AstrMessageEvent, req: ProviderRequest) -> list[str]:
        """Guide ordinary replies without removing AstrBot's media sender.

        Plain text stays on the final assistant-response path so it cannot be
        delivered twice. The official sender remains available for real files,
        images, records and videos that the current turn needs to deliver.
        """
        if req is None:
            return []
        if callable(getattr(self, "_event_requires_direct_same_session_tool_delivery", None)):
            if self._event_requires_direct_same_session_tool_delivery(event):
                return []
        if str(getattr(event, "_private_companion_external_proactive_source", "") or ""):
            return []
        umo = _single_line(getattr(event, "unified_msg_origin", ""), 240)
        if not umo or not any(marker in umo for marker in (":GroupMessage:", ":FriendMessage:")):
            return []

        try:
            setattr(event, "_private_companion_passive_reply_tool_boundary", True)
            setattr(event, "_private_companion_passive_reply_request", req)
        except Exception:
            pass
        marker = "<!-- private_companion_passive_reply_tool_boundary_v1 -->"
        plan = get_conversation_injection_plan(req, create=False)
        # Agent startup repeats this hook after the request plan is frozen.
        if plan is not None and (plan.frozen or plan.contains_marker(marker)):
            return []
        prompt = str(getattr(req, "system_prompt", "") or "")
        instruction = (
            "这是普通私聊或群聊的被动回复。请直接输出一次最终正文；"
            "普通文字不要调用 `send_message_to_user`，同一正文也不要在工具调用后再次输出。"
            "只有本轮确实需要投递已经存在、且带有真实 path 或 url 的图片、音频、视频或文件时，"
            "才可使用该官方工具；messages 至少包含一个非 plain 媒体组件。"
            "媒体消息中的 plain 只写必要附言，工具调用后不要重复输出附言或发送结果。"
            "不要猜测文件路径，也不要把该工具当成生成、搜索或读取文件的能力。"
            "需要跨会话主动发送时，使用 PrivateCompanion 专用发送工具；官方 Cron 任务不受此边界影响。"
        )
        section = prompt_section(
            key="tools.passive_reply_boundary",
            title="当前会话回复边界",
            source="tools",
            content=instruction,
        )
        if marker not in prompt and hasattr(req, "system_prompt"):
            materializer = getattr(self, "_materialize_conversation_system_block", None)
            if callable(materializer):
                materializer(
                    req,
                    section=section,
                    marker=marker,
                    priority=10,
                    placement=PLACEMENT_DYNAMIC_SYSTEM,
                )
            else:
                plan = get_conversation_injection_plan(req)
                if plan is not None:
                    plan.materialize_system_block(
                        req,
                        section=section,
                        marker=marker,
                        priority=10,
                        placement=PLACEMENT_DYNAMIC_SYSTEM,
                    )
            if self._tool_set_has_named_tool(getattr(req, "func_tool", None), "send_message_to_user"):
                logger.info(
                    "已约束被动回复的 send_message_to_user 仅用于媒体投递: session=%s",
                    umo,
                )
        return []

    @filter.on_llm_request(priority=-240000)
    @_multi_persona_event_context
    async def flush_conversation_injection_plan(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        *args,
        **kwargs,
    ):
        """Render all registered plugin-owned conversation blocks once before provider cleanup."""
        if self is None or req is None or not bool(getattr(self, "enabled", False)):
            return
        plan = get_conversation_injection_plan(req, create=False)
        if plan is None:
            return
        try:
            plan.render_into(req)
        except Exception as exc:
            logger.error(
                "主对话注入计划最终渲染失败: session=%s error=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                _single_line(exc, 180),
            )

    @filter.on_llm_request(priority=-260000)
    @_multi_persona_event_context
    async def finalize_conversation_injection_plan(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        *args,
        **kwargs,
    ):
        """Freeze a privacy-safe manifest after all plugin-owned request changes."""
        if self is None or req is None or not bool(getattr(self, "enabled", False)):
            return
        plan = get_conversation_injection_plan(req, create=False)
        if plan is None:
            return
        try:
            plan.render_into(req)
            setattr(
                req,
                "_private_companion_conversation_injection_manifest",
                plan.manifest(),
            )
            plan.freeze()
        except Exception as exc:
            logger.error(
                "主对话注入计划冻结失败: session=%s error=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120)
                or "unknown",
                _single_line(exc, 180),
            )

    @filter.on_llm_response()
    @_multi_persona_event_context
    async def capture_llm_timer_directive(self, event: AstrMessageEvent, resp: LLMResponse, *args, **kwargs):
        """LLM 回复后捕获定时/状态指令，并做私聊回复审校。"""
        release_now = False
        try:
            if self is None or not self.enabled:
                release_now = True
                return
            if bool(getattr(event, "private_companion_proactive_framework", False)):
                return
            if self._proactive_only_blocks_passive_event(event, "enable_llm_timer_scheduling"):
                release_now = True
                return
            if not bool(getattr(event, "is_private_chat", lambda: False)()):
                return
            original_text = str(resp.completion_text or "").strip()
            if not original_text:
                if bool(getattr(event, "_private_companion_plaintext_photo_sent", False)):
                    self._stop_passive_input_status_loop(event)
                    release_now = True
                    return
                self._stop_passive_input_status_loop(event)
                self._record_passive_no_reply(
                    event,
                    source="主链回复",
                    reason="LLM 返回空回复",
                    level="warn",
                )
                release_now = True
                return
            try:
                user_id = str(event.get_sender_id())
            except Exception:
                self._stop_passive_input_status_loop(event)
                release_now = True
                return
            resolver = getattr(self, "_private_user_id_for_event", None)
            if callable(resolver):
                user_id = resolver(event, user_id)
            raw_users = self.data.get("users", {})
            current_user = raw_users.get(user_id) if isinstance(raw_users, dict) else None
            if not isinstance(current_user, dict):
                self._stop_passive_input_status_loop(event)
                release_now = True
                return
            working_text = original_text
            reply_image_count = _safe_int(getattr(event, "private_companion_reply_image_count", 1), 1, 1, 5)
            reply_image_vision = _single_line(
                getattr(event, "private_companion_reply_image_vision_text", ""),
                self._private_image_vision_text_limit(reply_image_count),
            )
            reply_image_user_text = _single_line(
                getattr(event, "private_companion_reply_image_user_text", "") or current_user.get("last_user_message"),
                260,
            )
            if (
                reply_image_vision
                and bool(getattr(event, "private_companion_reply_image_content_question", False))
                and self._private_image_reply_misses_content_question(working_text)
            ):
                corrected = self._private_image_content_answer_from_vision(
                    reply_image_vision,
                    user_text=reply_image_user_text,
                )
                if corrected:
                    logger.info(
                        "私聊引用图片回复疑似被历史话题污染,已按视觉摘要纠偏: user=%s before=%s after=%s",
                        user_id,
                        _single_line(working_text, 120),
                        _single_line(corrected, 160),
                    )
                    working_text = corrected
                    resp.completion_text = corrected
            if self.enable_llm_timer_scheduling and "<timer" in original_text.lower():
                cleaned_text, payloads = self._extract_timer_directives(original_text)
                if cleaned_text != original_text:
                    working_text = cleaned_text
                    resp.completion_text = working_text
                if payloads:
                    timer_source_text = _single_line(current_user.get("last_user_message"), 260) or working_text
                    await self._schedule_llm_timer_after_response_dedup(
                        event,
                        resp,
                        user_id,
                        payloads[-1],
                        source_text=timer_source_text,
                        visible_text=working_text,
                        trigger_message_id=self._event_message_id(event),
                        trigger_umo=str(getattr(event, "unified_msg_origin", "") or ""),
                    )

            inbound_text = _single_line(current_user.get("last_user_message"), 260)
            sanitized_elapsed_text = self._sanitize_unverified_repeat_elapsed_claim(
                inbound_text,
                working_text,
                current_user,
            )
            sanitized_elapsed_text = self._sanitize_robotic_topic_choice_after_repeat_correction(
                inbound_text,
                sanitized_elapsed_text,
            )
            if sanitized_elapsed_text != working_text:
                logger.info(
                    "已清理重复纠正后的生硬回复: user=%s before=%s after=%s",
                    user_id,
                    _single_line(working_text, 120),
                    _single_line(sanitized_elapsed_text, 120),
                )
                working_text = sanitized_elapsed_text
                resp.completion_text = working_text
            music_album_context = getattr(event, "private_companion_reply_music_album_context", None)
            silence_decision = await self._decide_smart_silence(
                inbound_text=inbound_text,
                response_text=working_text,
                user=current_user,
                session_kind="private",
            )
            if str(silence_decision.get("decision") or "") == "silent":
                setattr(event, "_private_companion_smart_silence_drop", True)
                setattr(event, "_private_companion_smart_silence_reason", _single_line(silence_decision.get("reason"), 120))
                resp.completion_text = ""
                async with self._data_lock:
                    current = self._get_user(user_id)
                    stats = current.setdefault("postprocess_stats", {})
                    if not isinstance(stats, dict):
                        stats = {}
                        current["postprocess_stats"] = stats
                    stats["smart_silence"] = _safe_int(stats.get("smart_silence"), 0, 0) + 1
                    stats["last_smart_silence_at"] = self._environment_now().strftime("%Y-%m-%d %H:%M")
                    self._save_data_sync(sections={"users"})
                logger.info(
                    "智能沉默已取消本轮私聊回复: user=%s reason=%s inbound=%s reply=%s",
                    user_id,
                    _single_line(silence_decision.get("reason"), 120),
                    _single_line(inbound_text, 120),
                    _single_line(working_text, 140),
                )
                self._record_passive_no_reply(
                    event,
                    source="智能沉默",
                    reason=_single_line(silence_decision.get("reason"), 120) or "用户边界语义触发静默",
                    detail=inbound_text,
                    reply_preview=working_text,
                    level="info",
                )
                release_now = True
                return
            reviewed_text = await self._review_and_rewrite_response(
                current_user,
                inbound_text,
                working_text,
                music_album_context=music_album_context if isinstance(music_album_context, dict) else None,
                creative_context=str(getattr(event, "private_companion_creative_reply_context", "") or ""),
                review_event=event,
            )
            if self._passive_response_review_enabled() and self._is_response_review_drop_marker(reviewed_text):
                setattr(event, "_private_companion_response_review_drop", True)
                resp.completion_text = ""
                async with self._data_lock:
                    current = self._get_user(user_id)
                    stats = current.setdefault("postprocess_stats", {})
                    if not isinstance(stats, dict):
                        stats = {}
                        current["postprocess_stats"] = stats
                    stats["duplicate_dropped"] = _safe_int(stats.get("duplicate_dropped"), 0, 0) + 1
                    stats["last_duplicate_dropped_at"] = self._environment_now().strftime("%Y-%m-%d %H:%M")
                    self._save_data_sync(sections={"users"})
                logger.info(
                    "回复复核已取消重复私聊回复: user=%s inbound=%s reply=%s",
                    user_id,
                    _single_line(inbound_text, 120),
                    _single_line(working_text, 160),
                )
                self._record_passive_no_reply(
                    event,
                    source="回复复核去重",
                    reason="最终回复与上一条 Bot 消息重复",
                    detail=inbound_text,
                    reply_preview=working_text,
                    level="info",
                )
                release_now = True
                return
            if reviewed_text != working_text:
                resp.completion_text = reviewed_text
                working_text = reviewed_text
                async with self._data_lock:
                    current = self._get_user(user_id)
                    stats = current.setdefault("postprocess_stats", {})
                    if not isinstance(stats, dict):
                        stats = {}
                        current["postprocess_stats"] = stats
                    stats["rewritten"] = _safe_int(stats.get("rewritten"), 0, 0) + 1
                    stats["last_rewritten_at"] = self._environment_now().strftime("%Y-%m-%d %H:%M")
                    self._save_data_sync(sections={"users"})

            async with self._data_lock:
                live_user_for_duplicate = self._get_user(user_id)
            if self._passive_response_review_enabled() and self._effective_passive_review_strength() != "lenient":
                should_drop_duplicate, duplicate_reason = self._should_drop_duplicate_reply_text(live_user_for_duplicate, inbound_text, working_text)
            else:
                should_drop_duplicate, duplicate_reason = False, ""
            if should_drop_duplicate:
                setattr(event, "_private_companion_response_review_drop", True)
                resp.completion_text = ""
                async with self._data_lock:
                    current = self._get_user(user_id)
                    stats = current.setdefault("postprocess_stats", {})
                    if not isinstance(stats, dict):
                        stats = {}
                        current["postprocess_stats"] = stats
                    stats["duplicate_dropped"] = _safe_int(stats.get("duplicate_dropped"), 0, 0) + 1
                    stats["last_duplicate_dropped_at"] = self._environment_now().strftime("%Y-%m-%d %H:%M")
                    self._save_data_sync(sections={"users"})
                logger.info(
                    "发送前去重已取消重复私聊回复: user=%s reason=%s inbound=%s reply=%s",
                    user_id,
                    _single_line(duplicate_reason, 120),
                    _single_line(inbound_text, 120),
                    _single_line(working_text, 160),
                )
                self._record_passive_no_reply(
                    event,
                    source="回复复核去重",
                    reason=duplicate_reason or "最终回复与上一条 Bot 消息重复",
                    detail=inbound_text,
                    reply_preview=working_text,
                    level="info",
                )
                release_now = True
                return

            if working_text != original_text:
                self._schedule_reply_interception_forward(
                    "rewrite",
                    source="私聊回复处理",
                    reason="回复在发送前经过纠偏、清理或复核改写",
                    source_session=_single_line(getattr(event, "unified_msg_origin", ""), 180),
                    inbound=inbound_text,
                    before=original_text,
                    after=working_text,
                )
        except Exception:
            release_now = True
            raise
        finally:
            pass
