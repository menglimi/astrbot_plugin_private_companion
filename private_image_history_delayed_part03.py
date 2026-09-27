# -*- coding: utf-8 -*-
"""PrivateImageHistoryDelayedPart03Mixin。

由 tools/split_mixin_domain.py 从 private_image_history_delayed.py 机械抽取（1 个方法 + 0 个模块级名字 + 0 个类级赋值 / 566 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateImageHistoryDelayedMixin）。
"""
from __future__ import annotations

import asyncio
import time
from .conversation_injection_plan import get_conversation_injection_plan
from .conversation_prompt_section import PromptSection, prompt_section
from .helpers import _single_line
from .private_image_shared import _private_image_host, logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.provider import ProviderRequest
from astrbot.core.astr_main_agent import MainAgentBuildConfig, build_main_agent
from typing import Any



class PrivateImageHistoryDelayedPart03Mixin:
    """PrivateImageHistoryDelayedPart03Mixin（从 PrivateImageHistoryDelayedMixin 拆出）。"""


    async def _send_delayed_private_image_only_event(
        self,
        event: AstrMessageEvent,
        user_id: str,
        buffer: dict[str, Any],
    ) -> None:
        feature_checker = getattr(self, "_feature_enabled_or_temp_unlocked", None)
        feature_enabled = (
            feature_checker("enable_private_image_self_recognition")
            if callable(feature_checker)
            else bool(self._private_image_setting("enable_private_image_self_recognition", True))
        )
        if not feature_enabled:
            logger.info(
                "私聊单图处理期间图片转述增强已关闭,但原事件已接管,继续完成本轮回复: user=%s",
                user_id,
            )
        images = buffer.get("images") if isinstance(buffer.get("images"), list) else []
        vision_task = buffer.get("vision_task")
        image_limit = self._private_image_vision_text_limit(len(images))
        vision_text = _single_line(buffer.get("vision_text"), image_limit)
        vision_wait_timed_out = False
        if not vision_text and isinstance(vision_task, asyncio.Task):
            timeout = self._private_image_vision_wait_budget_seconds()
            try:
                if timeout > 0:
                    logger.info("私聊单图等待视觉转述完成: user=%s timeout=%.1fs", user_id, timeout)
                    vision_text = _single_line(await asyncio.wait_for(asyncio.shield(vision_task), timeout=timeout), image_limit)
            except asyncio.TimeoutError:
                vision_wait_timed_out = True
                logger.warning("私聊单图延迟处理时视觉转述仍未完成: user=%s timeout=%.1fs", user_id, timeout)
            except Exception as exc:
                logger.warning("私聊单图延迟视觉转述失败: user=%s error=%s", user_id, _single_line(exc, 120))
        ownership_line = self._private_image_ownership_line(vision_text)
        intent_line = self._private_image_intent_line(vision_text)
        reply_objective = self._private_image_reply_objective(ownership_line, vision_text=vision_text)
        prompt = _single_line(getattr(event, "message_str", ""), 120)
        if not prompt or prompt == "[图片]":
            prompt = (
                "用户刚刚只发了一张图片,没有补充文字。"
                "图片内容已在系统提示的本轮图片视觉摘要中给出；请直接回应那张图,不要说没看到图片。"
                "本轮只回应当前图片和用户发图可能表达的态度/梗/疑问；"
                "但如果最近对话里用户明确规定了这张/下一张图片的回复方式（例如只回复某句话、不要回复其他内容）,必须优先照做。"
                "除此之外,聊天历史只作语气背景,不要续写、答应或安排旧话题。"
                if vision_text
                else (
                    "用户刚刚只发了一张图片,没有补充文字；但当前没有可靠视觉摘要。"
                    "不要描述图片内容、场景、天气、人物、表情或文字，也不要根据聊天历史猜图。"
                    "如果最近对话里用户明确规定了这张/下一张图片的回复方式,必须优先照做；否则只用一句自然短回复说明这边没看清/没识别出来，并请用户补一句想让你看哪里。"
                )
            )
        logger.info(
            "私聊单图准备进入主链: user=%s images=%s has_vision=%s intent=%s ownership=%s objective=%s vision_preview=%s",
            user_id,
            len(images),
            bool(vision_text),
            intent_line or "无",
            ownership_line or "无",
            _single_line(reply_objective, 120),
            _single_line(vision_text, 220),
        )
        raw_image_sources = [str(item) for item in images[:5] if str(item or "").strip()]
        image_items = self._private_image_model_image_items(raw_image_sources)
        model_image_urls = [url for _, url in image_items]
        request_image_refs = self._private_image_sources_for_astrbot_request(raw_image_sources)
        try:
            umo = str(getattr(event, "unified_msg_origin", "") or "")
            framework_context = self._private_image_framework_context()
            framework_event = event
            if umo and framework_context is not None:
                try:
                    from astrbot.core.platform.message_session import MessageSession
                    from .proactive_message_framework_prompt_shared import SyntheticPrivateWakeEvent

                    session = MessageSession.from_str(umo)
                    sender_name = ""
                    try:
                        sender_name = _single_line(event.get_sender_name(), 60)
                    except Exception:
                        sender_name = ""
                    framework_event = SyntheticPrivateWakeEvent(
                        context=framework_context,
                        session=session,
                        message="[图片]",
                        sender_name=sender_name or "PrivateCompanion",
                    )
                    try:
                        selected_provider = event.get_extra("selected_provider")
                        if selected_provider:
                            framework_event.set_extra("selected_provider", selected_provider)
                    except Exception:
                        pass
                    logger.info("私聊单图主链使用合成私聊事件执行: user=%s session=%s", user_id, umo)
                except Exception as exc:
                    framework_event = event
                    logger.info("私聊单图合成私聊事件创建失败,回退原事件: user=%s error=%s", user_id, _single_line(exc, 160))
            elif umo:
                logger.warning(
                    "私聊单图主链未取得 AstrBot 原生 Context,已直接转入视觉摘要兜底: user=%s",
                    user_id,
                )
            setattr(framework_event, "private_companion_deferred_private_image_only_ready", True)
            setattr(framework_event, "private_companion_deferred_private_image_only", False)
            setattr(framework_event, "private_companion_skip_external_token_stats", True)
            setattr(framework_event, "private_companion_delayed_image_vision_text", vision_text)
            setattr(framework_event, "private_companion_delayed_image_sources", list(request_image_refs))
            if vision_text:
                self._route_private_image_caption_with_keyword_router(
                    framework_event, vision_text
                )
            buffered_image_mode = _single_line(buffer.get("image_mode"), 20)
            main_provider_supports_image = self._event_main_provider_supports_image(framework_event)
            has_visual_provider = self._has_private_image_visual_provider(umo)
            has_dynamic_gif_sources = (
                bool(self._private_image_setting("enable_private_image_gif_enhancement", True))
                and self._private_image_sources_include_gif(raw_image_sources)
            )
            resolved_image_mode = self._private_image_delivery_mode(
                has_visual_provider=has_visual_provider,
                main_provider_supports_image=main_provider_supports_image,
                has_dynamic_gif=has_dynamic_gif_sources,
            )
            direct_image_mode = bool(
                request_image_refs
                and buffered_image_mode == "direct"
                and resolved_image_mode == "direct"
            )
            direct_provider_id = ""
            direct_provider_source = "current_main_provider"
            if direct_image_mode:
                try:
                    direct_provider_id = _single_line(framework_event.get_extra("selected_provider"), 160)
                except Exception:
                    direct_provider_id = ""
                if not direct_provider_id:
                    direct_provider_id = "current_main_provider"
                setattr(framework_event, "private_companion_delayed_image_mode", "direct")
            elif request_image_refs:
                setattr(framework_event, "private_companion_delayed_image_mode", "caption" if has_visual_provider else "no_vision")
            if not direct_image_mode and has_visual_provider and not vision_text and images:
                completed_vision = self._completed_private_image_vision_task_text(vision_task)
                if completed_vision:
                    vision_text = _single_line(completed_vision, self._private_image_vision_text_limit(len(images)))
                    logger.info(
                        "私聊单图主链前取到后台视觉摘要: user=%s preview=%s",
                        user_id,
                        _single_line(vision_text, 220),
                    )
                elif not vision_wait_timed_out:
                    vision_text = _single_line(await self._transcribe_private_inbound_images(images, umo=umo), self._private_image_vision_text_limit(len(images)))
                else:
                    logger.warning("私聊单图识图等待已超时,主链不再重复发起视觉转述: user=%s", user_id)
                    setattr(framework_event, "private_companion_delayed_image_mode", "no_vision")
                if vision_text:
                    setattr(framework_event, "private_companion_delayed_image_vision_text", vision_text)
                    self._route_private_image_caption_with_keyword_router(
                        framework_event, vision_text
                    )
                    ownership_line = self._private_image_ownership_line(vision_text)
                    intent_line = self._private_image_intent_line(vision_text)
                    reply_objective = self._private_image_reply_objective(ownership_line, vision_text=vision_text)
            if has_dynamic_gif_sources and request_image_refs:
                logger.info(
                    "私聊单图检测到动态 GIF,已改用抽帧视觉摘要链路: user=%s has_vision=%s",
                    user_id,
                    bool(vision_text),
            )
            conv = None
            if umo:
                getter = getattr(self, "_get_current_conversation_safely", None)
                if callable(getter):
                    conv = await getter(umo, label="private_image_framework_read")
                else:
                    conv_id = await self.context.conversation_manager.get_curr_conversation_id(umo)
                    if conv_id:
                        conv = await self.context.conversation_manager.get_conversation(umo, conv_id)
            config_context = framework_context or self.context
            cfg = config_context.get_config(umo=umo) if umo else config_context.get_config()
            provider_settings = cfg.get("provider_settings", {}) if isinstance(cfg, dict) else {}
            build_cfg = MainAgentBuildConfig(
                tool_call_timeout=int(provider_settings.get("tool_call_timeout", 120) or 120),
                llm_safety_mode=False,
                streaming_response=False,
            )
            # The single-image response is a plugin-owned task even though it
            # runs through AstrBot's framework agent. Keep the custom rule in
            # this task request body; never mutate the main conversation system
            # prompt or the stored conversation configuration.
            prompt_applier = getattr(self, "_apply_task_prompt_override_for_call", None)
            if callable(prompt_applier):
                prompt, _unused_system_prompt = prompt_applier(
                    "private_image_only_framework",
                    prompt,
                    None,
                    flatten_system_prompt=True,
                )
            req = ProviderRequest(
                prompt=prompt,
                conversation=conv,
                session_id=getattr(framework_event, "session_id", None) or umo,
            )
            try:
                selected_model = framework_event.get_extra("selected_model")
            except Exception:
                selected_model = None
            if isinstance(selected_model, str) and selected_model.strip():
                # This path passes an explicit request to build_main_agent, so the
                # framework cannot copy selected_model from the event for us.
                req.model = selected_model.strip()
            previous_selected_provider = ""
            selected_provider_changed = False
            if direct_image_mode:
                req.image_urls = list(request_image_refs)
            await self.inject_humanized_state(framework_event, req)
            boundary_intro = (
                "用户当前只发了一张图片,没有文字补充；但当前没有可靠视觉摘要,本轮也没有把图片直接交给主模型。"
                "你不能看见图片内容,不要猜测画面、天气、地点、人物、表情、截图文字或图片类型。"
                "只允许短句请用户补一句想让你看哪里。\n"
                if not vision_text and not direct_image_mode
                else "用户当前只发了一张图片,没有文字补充。你的当前任务是回应这张图片本身和用户借图表达的态度/梗/疑问。\n"
            )
            boundary_prompt = (
                f"{boundary_intro}"
                "用户没有明确问‘图里是什么/写了什么/有几个人’时，不要逐项描述主体、衣服、背景和文字；"
                "把图当作对方递来的一句话，按人格自然评价、接梗、回应情绪或追问一个重点，最多顺带点出一个最显眼细节。\n"
                "如果最近对话上下文里有用户对本轮图片或下一张图片的明确回复限制,例如“只回复某句话”“不要回复其他内容”,必须优先遵守；这不是旧话题。\n"
                "不要把聊天历史、长期记忆、主动消息、旧 TTS 文本或压缩摘要里的邀约当成当前输入；"
                "不要顺便提下午、五点、放学、出去走走、陪你、到时候叫我等旧约定。"
            )
            boundary_section = prompt_section(
                key="private.image_reply_boundary",
                title="本轮图片回复边界",
                source="private_image",
                content=boundary_prompt,
            )
            recent_group_context = self._format_recent_group_messages_for_private_image_prompt_section(
                user_id
            )
            boundary_children: list[PromptSection] = []
            if str(recent_group_context.content or "").strip():
                boundary_children.append(recent_group_context)
            if boundary_children:
                boundary_section = prompt_section(
                    key=boundary_section.key,
                    title=boundary_section.title,
                    source=boundary_section.source,
                    content=boundary_section.content,
                    children=boundary_children,
                )
            self._register_materialized_private_image_context(
                req,
                section=boundary_section,
                marker="",
                priority=31,
            )
            segmenting_injector = getattr(
                self,
                "inject_llm_controlled_segmenting_instruction",
                None,
            )
            if callable(segmenting_injector):
                try:
                    await segmenting_injector(framework_event, req)
                except Exception as exc:
                    logger.debug(
                        "私聊单图分段说明注入失败，继续生成正文: %s",
                        _single_line(exc, 120),
                    )
            request_plan = get_conversation_injection_plan(req, create=False)
            if request_plan is not None:
                request_plan.render_into(req)
            if direct_image_mode:
                existing = getattr(req, "image_urls", None)
                if not isinstance(existing, list):
                    existing = []
                for image_ref in request_image_refs:
                    if image_ref not in existing:
                        existing.append(image_ref)
                req.image_urls = existing
                logger.info(
                    "私聊单图主链已挂载图片: user=%s provider=%s source=%s images=%s has_vision=%s",
                    user_id,
                    direct_provider_id,
                    direct_provider_source,
                    len(existing),
                    bool(vision_text),
                )
            start = time.time()
            captured_tool_sends = []
            llm_resp = None
            try:
                async def _runner_factory():
                    if framework_context is None:
                        return None
                    built = await build_main_agent(
                        event=framework_event,
                        plugin_context=framework_context,
                        config=build_cfg,
                        req=req,
                    )
                    return built

                capture_runner = getattr(self, "_capture_framework_send_message_calls", None)
                framework_lock = getattr(self, "_framework_agent_lock", None)
                if not isinstance(framework_lock, asyncio.Lock):
                    framework_lock = asyncio.Lock()
                    self._framework_agent_lock = framework_lock
                async with framework_lock:
                    if callable(capture_runner) and umo:
                        result, captured_tool_sends = await capture_runner(
                            target_session=umo,
                            runner_factory=_runner_factory,
                        )
                        if captured_tool_sends:
                            logger.info(
                                "私聊单图主链拦截到框架工具直发: user=%s count=%s",
                                user_id,
                                len(captured_tool_sends),
                            )
                    else:
                        result = await _runner_factory()
                        runner_for_step = getattr(result, "agent_runner", None) if result else None
                        if runner_for_step is not None and hasattr(runner_for_step, "step_until_done"):
                            async for _ in runner_for_step.step_until_done(20):
                                pass
            except Exception as exc:
                if direct_image_mode and self._exception_indicates_image_input_unsupported(exc):
                    logger.warning(
                        "私聊单图主链模型不支持图片输入,已降级为视觉摘要兜底: user=%s provider=%s error=%s",
                        user_id,
                        direct_provider_id,
                        _single_line(exc, 180),
                    )
                    direct_image_mode = False
                    reply = ""
                    reply_source = "image_input_unsupported_fallback"
                    result = None
                elif self._exception_indicates_tool_schema_invalid(exc):
                    logger.warning(
                        "私聊单图主链工具 schema 不兼容,已转入兜底回复: user=%s error=%s",
                        user_id,
                        _single_line(exc, 180),
                    )
                    direct_image_mode = False
                    reply = ""
                    reply_source = "tool_schema_invalid_fallback"
                    result = None
                else:
                    logger.warning(
                        "私聊单图主链异常,已转入人格兜底: user=%s error=%s",
                        user_id,
                        _single_line(exc, 180),
                        exc_info=True,
                    )
                    direct_image_mode = False
                    reply = ""
                    reply_source = "main_chain_exception_fallback"
                    result = None
            finally:
                if selected_provider_changed:
                    try:
                        framework_event.set_extra("selected_provider", previous_selected_provider)
                    except Exception:
                        pass
            runner = getattr(result, "agent_runner", None) if result else None
            if llm_resp is None:
                llm_resp = runner.get_final_llm_resp() if runner else None
            if "reply" not in locals():
                reply = self._private_image_framework_response_text(llm_resp)
                if reply and not str(getattr(llm_resp, "completion_text", "") or "").strip():
                    logger.info(
                        "私聊单图主链 completion_text 为空,已从 result_chain 恢复可见文本: user=%s preview=%s",
                        user_id,
                        _single_line(reply, 180),
                    )
            if "reply_source" not in locals():
                reply_source = "main_chain"
            reply = self._restore_private_image_framework_tts_reply(
                reply,
                framework_event,
            )
            if reply and self._private_image_reply_is_internal_error(reply):
                logger.warning(
                    "私聊单图主链返回内部错误文本,已拦截转入兜底: user=%s preview=%s",
                    user_id,
                    _single_line(reply, 180),
                )
                reply = ""
                reply_source = "internal_error_fallback"
            if reply and direct_image_mode and self._private_image_reply_denies_image_capability(reply):
                logger.warning(
                    "私聊单图主链返回无法看图声明,已转视觉摘要兜底: user=%s provider=%s preview=%s",
                    user_id,
                    direct_provider_id,
                    _single_line(reply, 180),
                )
                reply = ""
                direct_image_mode = False
                reply_source = "image_capability_denial_fallback"
            if not reply and captured_tool_sends:
                captured_text_parts: list[str] = []
                sanitizer = getattr(self, "_sanitize_captured_plain_text", None)
                for call in reversed(captured_tool_sends):
                    messages = getattr(call, "messages", [])
                    if not isinstance(messages, list):
                        continue
                    for item in messages:
                        if not isinstance(item, dict):
                            continue
                        if str(item.get("type") or "").strip().lower() != "plain":
                            continue
                        raw_text = item.get("text")
                        text_value = sanitizer(raw_text) if callable(sanitizer) else _single_line(raw_text, 260)
                        if text_value:
                            captured_text_parts.append(text_value)
                    if captured_text_parts:
                        break
                reply = _single_line("\n".join(captured_text_parts), 500)
                if reply:
                    reply_source = "main_chain_tool_capture"
                    logger.info(
                        "私聊单图主链工具直发文本已转为普通回复: user=%s chars=%s reply_preview=%s",
                        user_id,
                        len(reply),
                        _single_line(reply, 180),
                    )
            if reply and vision_text and self._private_image_reply_ignores_vision_summary(reply):
                logger.info(
                    "私聊单图主链疑似忽略视觉摘要,转入兜底回复: user=%s reply_preview=%s",
                    user_id,
                    _single_line(reply, 180),
                )
                reply = ""
            if reply and self._private_image_reply_drifts_to_stale_context(reply):
                trimmed_reply = self._trim_private_image_stale_context_tail(reply)
                if trimmed_reply and trimmed_reply != reply and not self._private_image_reply_drifts_to_stale_context(trimmed_reply):
                    logger.info(
                        "私聊单图主链回复夹带旧上下文,已裁剪: user=%s before=%s after=%s",
                        user_id,
                        _single_line(reply, 180),
                        _single_line(trimmed_reply, 180),
                    )
                    reply = trimmed_reply
                else:
                    logger.info(
                        "私聊单图主链回复夹带旧上下文,转入兜底回复: user=%s reply_preview=%s",
                        user_id,
                        _single_line(reply, 180),
                    )
                    reply = ""
            if reply:
                reply_preview = reply
                preview_cleaner = getattr(self, "_sanitize_orphan_tts_placeholders", None)
                if callable(preview_cleaner):
                    try:
                        reply_preview = preview_cleaner(reply_preview)
                    except Exception:
                        reply_preview = reply
                logger.info(
                    "私聊单图主链回复生成: user=%s chars=%s intent=%s ownership=%s reply_preview=%s",
                    user_id,
                    len(reply),
                    intent_line or "无",
                    ownership_line or "无",
                    _single_line(reply_preview, 180),
                )
            if not reply:
                if not vision_text and images and has_visual_provider:
                    vision_text = self._completed_private_image_vision_task_text(vision_task)
                    if vision_text:
                        logger.info(
                            "私聊单图兜底前取到后台视觉摘要: user=%s preview=%s",
                            user_id,
                            _single_line(vision_text, 220),
                        )
                    elif not vision_wait_timed_out:
                        vision_text = _single_line(await self._transcribe_private_inbound_images(images, umo=umo), self._private_image_vision_text_limit(len(images)))
                    else:
                        logger.info("私聊单图兜底阶段跳过重复视觉转述: user=%s", user_id)
                    setattr(event, "private_companion_delayed_image_vision_text", vision_text)
                    ownership_line = self._private_image_ownership_line(vision_text)
                    intent_line = self._private_image_intent_line(vision_text)
                    reply_objective = self._private_image_reply_objective(ownership_line, vision_text=vision_text)
                fallback_system_prompt = str(getattr(req, "system_prompt", "") or "").strip()
                reply, reply_source = await self._generate_private_image_fallback_reply(
                    vision_text=vision_text,
                    reply_objective=reply_objective,
                    system_prompt=fallback_system_prompt,
                    user_id=user_id,
                )
                if not vision_text:
                    logger.info(
                        "私聊单图无可靠视觉摘要,已尝试人格兜底回复: user=%s chars=%s reply_preview=%s",
                        user_id,
                        len(reply),
                        _single_line(reply, 180),
                    )
                else:
                    logger.info(
                        "私聊单图兜底回复生成: user=%s chars=%s intent=%s ownership=%s objective=%s reply_preview=%s",
                        user_id,
                        len(reply),
                        intent_line or "无",
                        ownership_line or "无",
                        _single_line(reply_objective, 120),
                        _single_line(reply, 180),
                    )
                if not reply:
                    logger.warning(
                        "私聊单图原生链路与兜底 LLM 均未生成有效回复,不启用本地静态兜底: user=%s images=%s has_vision=%s",
                        user_id,
                        len(images),
                        bool(vision_text),
                    )
                    self._record_llm_usage(
                        provider_id="framework",
                        task="private_image_only_framework",
                        prompt=prompt,
                        completion="",
                        elapsed_ms=int((time.time() - start) * 1000),
                        success=False,
                        resp=llm_resp,
                        budget_exempt=True,
                    )
                    return
                logger.info("私聊单图原生链路回复为空,已使用兜底 LLM 回复: user=%s images=%s", user_id, len(images))
            self._record_llm_usage(
                provider_id="framework",
                task="private_image_only_framework",
                prompt=prompt,
                completion=reply,
                elapsed_ms=int((time.time() - start) * 1000),
                success=True,
                resp=llm_resp,
                budget_exempt=True,
            )
            await self._record_private_image_vision_feedback_target(
                user_id=user_id,
                image_sources=raw_image_sources,
                vision_text=vision_text,
                reply=reply,
                ownership=ownership_line,
                intent=intent_line,
            )
            sent_reply = await self._send_private_image_reply_text(event, reply)
            buffer["delayed_reply_sent"] = bool(sent_reply)
            buffer["delayed_reply_sent_ts"] = _private_image_host._now_ts() if sent_reply else 0.0
            if sent_reply:
                await self._archive_private_image_turn_context(
                    event,
                    user_id=user_id,
                    vision_text=vision_text,
                    reply=sent_reply,
                    image_count=len(images),
                )
            if reply_source == "main_chain":
                logger.info("私聊单图无补充说明,已由原生 LLM 链路回复: user=%s images=%s", user_id, len(images))
            else:
                logger.info(
                    "私聊单图无补充说明,原生链路为空,已由兜底回复发送: user=%s images=%s source=%s",
                    user_id,
                    len(images),
                    reply_source,
                )
        except Exception as exc:
            logger.warning("私聊单图延迟回复失败: user=%s error=%s", user_id, _single_line(exc, 180), exc_info=True)
