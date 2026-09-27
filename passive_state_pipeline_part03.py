# -*- coding: utf-8 -*-
"""passive_state_pipeline 阶段 3（原 inject_humanized_state 行 1282-1576）。

由 tmp/refactor/psp_split.py 机械提取，控制流与副作用保持原样。
"""
from __future__ import annotations

from typing import Any

from .passive_state_pipeline_shared import _PASSIVE_STAGE_STOP, _psp_host


async def _passive_state_stage_3(self: Any, ctx: Any):
    buffered_image_from_handoff = getattr(ctx, "buffered_image_from_handoff", None)
    buffered_image_mode = getattr(ctx, "buffered_image_mode", None)
    buffered_image_vision = getattr(ctx, "buffered_image_vision", None)
    buffered_images = getattr(ctx, "buffered_images", None)
    buffered_images_include_gif = getattr(ctx, "buffered_images_include_gif", None)
    current_user = getattr(ctx, "current_user", None)
    event = getattr(ctx, "event", None)
    exc = getattr(ctx, "exc", None)
    existing = getattr(ctx, "existing", None)
    inbound_text = getattr(ctx, "inbound_text", None)
    index = getattr(ctx, "index", None)
    is_private_chat = getattr(ctx, "is_private_chat", None)
    item = getattr(ctx, "item", None)
    lightweight_passive = getattr(ctx, "lightweight_passive", None)
    log_bookshelf_secret_skip = getattr(ctx, "log_bookshelf_secret_skip", None)
    marker = getattr(ctx, "marker", None)
    place_sections = getattr(ctx, "place_sections", None)
    prompt_surface = getattr(ctx, "prompt_surface", None)
    req = getattr(ctx, "req", None)
    section = getattr(ctx, "section", None)
    state = getattr(ctx, "state", None)
    state_changed = getattr(ctx, "state_changed", None)
    state_update_reason = getattr(ctx, "state_update_reason", None)
    user = getattr(ctx, "user", None)
    user_id = getattr(ctx, "user_id", None)
    if buffered_images:
        direct_image_mounted = False
        if (
            not buffered_image_from_handoff
            and buffered_image_mode == "direct"
            and self._event_main_provider_supports_image(event)
            and not buffered_images_include_gif
        ):
            image_refs: list[str] = []
            for image_ref in buffered_images[:5]:
                for request_ref in self._private_image_sources_for_astrbot_request([image_ref]):
                    if request_ref not in image_refs:
                        image_refs.append(request_ref)
            if not image_refs:
                _psp_host.logger.info(
                    "私聊延迟图片无模型可读源,跳过直接挂图: user=%s images=%s",
                    user_id,
                    len(buffered_images),
                )
                buffered_image_mode = (
                    "caption"
                    if self._has_private_image_visual_provider(str(getattr(event, "unified_msg_origin", "") or ""))
                    else "no_vision"
                )
            else:
                existing = getattr(req, "image_urls", None)
                if not isinstance(existing, list):
                    existing = []
                for image_ref in image_refs:
                    if image_ref not in existing:
                        existing.append(image_ref)
                req.image_urls = existing
                _psp_host.logger.info(
                    "私聊延迟图片已挂回视觉主模型: user=%s images=%s mounted=%s",
                    user_id,
                    len(buffered_images),
                    len(image_refs),
                )
                try:
                    await self._refresh_default_persona_prompt(str(getattr(event, "unified_msg_origin", "") or ""))
                except Exception as exc:
                    _psp_host.logger.debug("图片直挂刷新人格缓存失败: %s", exc)
                direct_role_section = self._private_image_direct_role_appearance_prompt_section()
                if direct_role_section.content:
                    prompt_surface.add(direct_role_section, priority=55)
                direct_image_mounted = True
        if not direct_image_mounted and buffered_image_vision:
            intent_line = self._private_image_intent_line(buffered_image_vision)
            ownership_line = self._private_image_ownership_line(buffered_image_vision)
            reply_objective = self._private_image_reply_objective(ownership_line, vision_text=buffered_image_vision, user_text=inbound_text)
            _psp_host.logger.info(
                "私聊延迟图片已注入视觉摘要: user=%s chars=%s intent=%s ownership=%s objective=%s preview=%s",
                user_id,
                len(buffered_image_vision),
                intent_line or "无",
                ownership_line or "无",
                _psp_host._single_line(reply_objective, 120),
                _psp_host._single_line(buffered_image_vision, 220),
            )
            image_context_intro = (
                "用户刚刚只发了一张图片,没有继续补充文字。"
                if bool(getattr(event, "private_companion_deferred_private_image_only_ready", False))
                else "用户刚刚先单独发了一张图片,随后补充了文字。"
            )
            prompt_surface.add(
                _psp_host._deferred_private_image_prompt_section(
                    key="image.vision",
                    content=(
                        f"{image_context_intro}下面是这张图的视觉摘要；请按摘要理解当前图片，不要说没看到图。"
                        "只回应本轮图片和用户文字，不要提模型、插件或路径。"
                        "如果最近对话里用户明确规定了这张/下一张图片的回复方式（例如只回复某句话、不要回复其他内容）,必须优先照做。\n"
                        f"{self._private_image_identity_disambiguation_instruction()}\n"
                        f"{reply_objective}\n"
                        f"{buffered_image_vision}"
                    ),
                ),
                priority=55,
            )
            await self._memory_companion_record_image_observation(
                event,
                content=buffered_image_vision,
                image_count=len(buffered_images),
                source="current_private_image",
                user_id=user_id,
                user_name=_psp_host._single_line(current_user.get("nickname") or current_user.get("display_name") or user_id, 80),
            )
        else:
            image_context_intro = (
                "用户刚刚只发了一张图片,没有继续补充文字。"
                if bool(getattr(event, "private_companion_deferred_private_image_only_ready", False))
                else "用户刚刚先单独发了一张图片,随后补充了文字。"
            )
            prompt_surface.add(
                _psp_host._deferred_private_image_prompt_section(
                    key="image.fallback",
                    content=(
                        f"{image_context_intro}图片已暂存，但暂无可靠视觉摘要；"
                        "如果用户问图片内容，请自然说暂时没看清，不要编造画面。\n"
                        + "\n".join(f"- {path}" for path in buffered_images)
                    ),
                ),
                priority=55,
            )
    elif bool(getattr(event, "private_companion_deferred_private_image_only_ready", False)):
        if buffered_image_vision:
            prompt_surface.add(
                _psp_host._deferred_private_image_prompt_section(
                    key="image.only.vision",
                    content=(
                        "用户只发了一张图片。下面是这张图的视觉摘要；请自然接住图片内容或表达意图，不要提处理过程。"
                        "如果最近对话里用户明确规定了这张/下一张图片的回复方式（例如只回复某句话、不要回复其他内容）,必须优先照做。\n"
                        f"{buffered_image_vision}"
                    ),
                ),
                priority=55,
            )
            await self._memory_companion_record_image_observation(
                event,
                content=buffered_image_vision,
                image_count=max(1, len(buffered_images)),
                source="current_private_image",
                user_id=user_id,
                user_name=_psp_host._single_line(current_user.get("nickname") or current_user.get("display_name") or user_id, 80),
            )
        else:
            prompt_surface.add(
                _psp_host._deferred_private_image_prompt_section(
                    key="image.only.fallback",
                    content="用户只发了一张图片，但当前没有可靠图片内容；请自然表示暂时没看清，可以请用户补一句，不要编造画面。",
                ),
                priority=55,
            )
    reply_image_sources: list[str] = []
    reply_image_prompt_anchor = ""
    skip_reply_image_for_forward_context = bool(getattr(event, "private_companion_forward_context_injected", False))
    if skip_reply_image_for_forward_context:
        _psp_host.logger.info("本轮已注入合并消息上下文,跳过引用图片重复视觉: user=%s", user_id)
    if (
        not skip_reply_image_for_forward_context
        and not buffered_images
        and not bool(getattr(event, "private_companion_deferred_private_image_only_ready", False))
    ):
        reply_image_sources = await self._find_reply_image_sources_for_event(event)
        if reply_image_sources:
            reply_image_limit = self._private_image_vision_text_limit(len(reply_image_sources))
            reply_image_vision = _psp_host._single_line(
                getattr(event, "private_companion_reply_image_vision_text", ""),
                reply_image_limit,
            )
            if not reply_image_vision:
                reply_image_vision = _psp_host._single_line(
                    await self._transcribe_private_inbound_images(
                        reply_image_sources,
                        umo=str(getattr(event, "unified_msg_origin", "") or ""),
                        user_text=inbound_text,
                        force_contextual=self._private_image_user_has_specific_vision_request(inbound_text),
                    ),
                    reply_image_limit,
                )
            if reply_image_vision:
                intent_line = self._private_image_intent_line(reply_image_vision)
                ownership_line = self._private_image_ownership_line(reply_image_vision)
                reply_objective = self._private_image_reply_objective(ownership_line, vision_text=reply_image_vision, user_text=inbound_text)
                _psp_host.logger.info(
                    "私聊引用图片已注入视觉摘要: user=%s images=%s intent=%s ownership=%s objective=%s preview=%s",
                    user_id,
                    len(reply_image_sources),
                    intent_line or "无",
                    ownership_line or "无",
                    _psp_host._single_line(reply_objective, 120),
                    _psp_host._single_line(reply_image_vision, 220),
                )
                reply_image_prompt_anchor = (
                    f"用户本轮是在问被引用图片：{inbound_text or '（空）'}。\n"
                    "下面摘要只属于这一次被引用的图片；请把它作为当前问题的主要依据。\n"
                    f"{reply_objective}\n"
                    f"{reply_image_vision}"
                )
                prompt_surface.add(
                    _psp_host._reply_private_image_prompt_section(
                        key="image.reply.vision",
                        content=(
                            f"用户这轮引用/回复了一张图片,并发送文字：{inbound_text or '（空）'}。\n"
                            "下面是被引用图片的视觉摘要；请优先回答用户当前文字针对这张图提出的问题。\n"
                            f"{self._private_image_identity_disambiguation_instruction()}\n"
                            f"{reply_objective}\n"
                            f"{reply_image_vision}"
                        ),
                    ),
                    priority=55,
                )
                await self._memory_companion_record_image_observation(
                    event,
                    content=reply_image_vision,
                    image_count=len(reply_image_sources),
                    source="reply_image",
                    user_id=user_id,
                    user_name=_psp_host._single_line(current_user.get("nickname") or current_user.get("display_name") or user_id, 80),
                )
                image_keys = self._private_image_cache_image_keys(reply_image_sources)
                if image_keys:
                    try:
                        async with self._data_lock:
                            user = self._get_user(user_id)
                            user["last_private_image_vision_feedback_target"] = {
                                "ts": _psp_host._now_ts(),
                                "image_keys": image_keys,
                                "vision_text": _psp_host._single_line(reply_image_vision, reply_image_limit),
                                "reply": "",
                                "ownership": ownership_line,
                                "intent": intent_line,
                                "source": "reply_image",
                            }
                            self._save_data_sync(sections={"users"})
                    except Exception as exc:
                        _psp_host.logger.debug("私聊引用图片视觉反馈目标记录失败: %s", exc)
                try:
                    setattr(event, "private_companion_reply_image_vision_text", _psp_host._single_line(reply_image_vision, reply_image_limit))
                    setattr(event, "private_companion_reply_image_count", len(reply_image_sources))
                    setattr(event, "private_companion_reply_image_user_text", inbound_text)
                    setattr(
                        event,
                        "private_companion_reply_image_content_question",
                        self._private_image_user_asks_content(inbound_text),
                    )
                except Exception:
                    pass
            else:
                prompt_surface.add(
                    _psp_host._reply_private_image_prompt_section(
                        key="image.reply.fallback",
                        content=(
                            f"用户这轮引用/回复了一张图片,并发送文字：{inbound_text or '（空）'}。"
                            "当前未能拿到可用视觉摘要；如果用户问图片内容，请自然说明暂时没看清，不要编造。"
                        ),
                    ),
                    priority=55,
                )
    if reply_image_prompt_anchor:
        prompt_surface.add(
            _psp_host.prompt_section(
                key="image.reply.anchor",
                title="当前引用图片锚点",
                source="private_image",
                content=reply_image_prompt_anchor,
            ),
            priority=4,
        )
    if lightweight_passive:
        log_bookshelf_secret_skip("lightweight_passive", current_user, inbound_text)
    if not lightweight_passive:
        collected_contexts = await self._collect_private_passive_prompt_contexts(
            event,
            req,
            inbound_text=inbound_text,
            current_user=current_user,
            is_private_chat=is_private_chat,
        )
        creative_reply_context = next(
            (
                _psp_host.render_prompt_sections(
                    [section],
                    mode="body_only",
                ).strip()
                for item in collected_contexts
                for section in item.sections
                if section.key == "creative.hidden"
            ),
            "",
        )
        if creative_reply_context:
            setattr(event, "private_companion_creative_reply_context", creative_reply_context)
        self._add_collected_prompt_contexts(prompt_surface, collected_contexts)
    static_fragment_keys = {"reply.style"}
    static_sections, dynamic_sections = prompt_surface.partition_sections(
        lambda fragment: fragment.normalized_key() in static_fragment_keys
    )
    injection_sections = (*static_sections, *dynamic_sections)
    injection = _psp_host.render_prompt_sections(injection_sections)
    static_marker = "<!-- private_companion_static_v1 -->"
    marker = "<!-- private_companion_state_v1 -->"
    if self._request_has_managed_prompt_marker(req, marker):
        log_bookshelf_secret_skip("state_marker_already_present", current_user, inbound_text)
        await self._append_conditional_tool_instructions_to_request(event, req)
        return _PASSIVE_STAGE_STOP
    if not injection_sections:
        _psp_host.logger.debug("被动状态提示词片段为空,跳过状态 marker 注入")
        log_bookshelf_secret_skip("empty_passive_injection", current_user, inbound_text)
        await self._append_conditional_tool_instructions_to_request(event, req)
        return _PASSIVE_STAGE_STOP
    static_placement = ""
    dynamic_placement = ""
    conversation_plan = _psp_host.get_conversation_injection_plan(req)
    if conversation_plan is None:
        raise RuntimeError("conversation injection plan is unavailable")
    _snapshot = locals()
    for _k in ['conversation_plan', 'dynamic_placement', 'dynamic_sections', 'injection', 'injection_sections', 'marker', 'section', 'static_marker', 'static_placement', 'static_sections']:
        if _k in _snapshot:
            setattr(ctx, _k, _snapshot[_k])
