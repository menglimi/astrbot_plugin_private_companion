# -*- coding: utf-8 -*-
"""passive_state_pipeline 阶段 2（原 inject_humanized_state 行 1052-1281）。

由 tmp/refactor/psp_split.py 机械提取，控制流与副作用保持原样。
"""
from __future__ import annotations

from typing import Any

from .passive_state_pipeline_shared import _psp_host


async def _passive_state_stage_2(self: Any, ctx: Any):
    combined_text = getattr(ctx, "combined_text", None)
    current_user = getattr(ctx, "current_user", None)
    event = getattr(ctx, "event", None)
    exc = getattr(ctx, "exc", None)
    existing = getattr(ctx, "existing", None)
    inbound_text = getattr(ctx, "inbound_text", None)
    index = getattr(ctx, "index", None)
    is_private_chat = getattr(ctx, "is_private_chat", None)
    lightweight_passive = getattr(ctx, "lightweight_passive", None)
    log_bookshelf_secret_skip = getattr(ctx, "log_bookshelf_secret_skip", None)
    marker = getattr(ctx, "marker", None)
    place_sections = getattr(ctx, "place_sections", None)
    prompt_surface = getattr(ctx, "prompt_surface", None)
    recall_section = getattr(ctx, "recall_section", None)
    req = getattr(ctx, "req", None)
    rest_backlog_prompt = getattr(ctx, "rest_backlog_prompt", None)
    section = getattr(ctx, "section", None)
    state = getattr(ctx, "state", None)
    state_changed = getattr(ctx, "state_changed", None)
    state_update_reason = getattr(ctx, "state_update_reason", None)
    user = getattr(ctx, "user", None)
    user_id = getattr(ctx, "user_id", None)
    if rest_backlog_prompt:
        prompt_surface.add(
            _psp_host.prompt_section(
                key="rest.backlog",
                title="休息期间消息承接",
                source="daily_state",
                content=rest_backlog_prompt,
            ),
            priority=25,
        )
    busy_delay = _psp_host._safe_float(getattr(event, "private_companion_busy_reply_delay_seconds", 0.0), 0.0)
    if busy_delay > 0:
        busy_schedule = _psp_host._single_line(
            getattr(event, "private_companion_busy_reply_schedule", ""),
            180,
        )
        busy_reply_boundary = (
            "当前日程正在专注处理事情，这轮消息已经自然晚了一点才看到。回复可以比平时更短、更聚焦，但必须完整回答用户真正问的内容。\n"
            "不要汇报延迟秒数，不要说系统排队、闸门、后台或提示词，也不要每次都机械道歉；除非用户追问，通常不必主动解释为什么晚回。"
            + (f"\n当前忙碌片段：{busy_schedule}" if busy_schedule else "")
        )
        prompt_surface.add(
            _psp_host.prompt_section(
                key="busy.reply_boundary",
                title="忙碌中的回复节奏",
                source="daily_state",
                content=busy_reply_boundary,
            ),
            priority=31,
        )
    try:
        sleeping, _sleep_runtime, _sleep_item, sleep_schedule_text = self._rest_reply_sleep_context()
    except Exception:
        sleeping, sleep_schedule_text = False, ""
    if sleeping:
        sleep_reply_boundary = (
            "当前处于睡眠/休息延续。用户如果只是短句叫醒、查岗、例行检查、确认在不在，回复要短、迷糊、低打扰，通常 1 句即可。\n"
            "不要因为记忆里有相似旧话题就展开长段回忆、梦境、解释或连续追问；旧记忆只做语气底色，不要新编具体梦境内容。\n"
            "如果用户没有明确提出新请求，回复后应自然收住，表现为可以睡回去。"
            + (f"\n当前休息片段：{_psp_host._single_line(sleep_schedule_text, 160)}" if sleep_schedule_text else "")
        )
        prompt_surface.add(
            _psp_host.prompt_section(
                key="rest.sleep_reply_boundary",
                title="休息中被叫醒回复边界",
                source="daily_state",
                content=sleep_reply_boundary,
            ),
            priority=32,
        )
    is_wake_event = bool(getattr(event, "is_wake", False)) or bool(
        getattr(event, "is_at_or_wake_command", False)
    )
    group_share_section = self._format_recent_group_share_snapshot_for_reply_prompt_section(
        current_user,
        inbound_text,
        event_umo=_psp_host._single_line(getattr(event, "unified_msg_origin", ""), 180),
    )
    if group_share_section is not None and group_share_section.content:
        prompt_surface.add(group_share_section, priority=44)
    if not is_wake_event:
        proactive_sections = await self._format_proactive_reply_prompt_sections(event)
        for index, section in enumerate(proactive_sections or []):
            if isinstance(section, _psp_host.PromptSection) and section.content:
                prompt_surface.add(section, priority=45 + index)
    short_reaction_section = self._format_short_reaction_prompt_section(
        current_user,
        inbound_text,
    )
    if short_reaction_section is not None and short_reaction_section.content:
        prompt_surface.add(short_reaction_section, priority=48)
    if _psp_host.re.search(r"(说过|讲过|提过|聊过|发过|说了|讲了|提了).{0,4}(啦|了|呀|啊)?$", inbound_text):
        prompt_surface.add(
            _psp_host.prompt_section(
                key="turn.repeat_correction_boundary",
                title="用户纠正重复话题",
                source="conversation",
                content=(
                "用户是在提醒你刚才/前面已经说过。回复只需要短短认一下，不要编造“几小时前/几分钟前”等具体时间差，"
                "也不要把回复写成“你希望我换个话题还是继续聊”的选项题。更自然的做法是：承认自己刚才没接稳，然后收住或自己轻轻换一个具体小切口。"
                ),
            ),
            priority=49,
        )
    if not bool(
        getattr(event, "private_companion_reply_chain_context_injected", False)
    ):
        try:
            reply_chain_section = await self._format_reply_chain_context_prompt_section(event)
        except Exception as exc:
            _psp_host.logger.debug("引用链上下文读取失败: %s", _psp_host._single_line(exc, 120))
            reply_chain_section = None
        if reply_chain_section is not None and reply_chain_section.content:
            prompt_surface.add(reply_chain_section, priority=54)
    private_image_enhancement_enabled_for_request = self._feature_enabled_or_temp_unlocked(
        "enable_private_image_self_recognition"
    )
    buffered_image_context = (
        self._take_buffered_private_image_context_for_event(event)
        if private_image_enhancement_enabled_for_request
        else {}
    )
    buffered_image_from_handoff = bool(
        isinstance(buffered_image_context, dict)
        and buffered_image_context.get("from_handoff")
    )
    buffered_images = (
        [str(item) for item in buffered_image_context.get("images", []) if str(item or "").strip()]
        if isinstance(buffered_image_context, dict)
        else []
    )
    buffered_image_vision = ""
    delayed_image_sources = getattr(event, "private_companion_delayed_image_sources", [])
    if not buffered_images and isinstance(delayed_image_sources, list):
        buffered_images = [str(item) for item in delayed_image_sources[:5] if str(item or "").strip()]
    buffered_image_vision_limit = self._private_image_vision_text_limit(len(buffered_images))
    if isinstance(buffered_image_context, dict):
        buffered_image_vision = _psp_host._single_line(buffered_image_context.get("vision_text"), buffered_image_vision_limit)
    delayed_image_vision = _psp_host._single_line(
        getattr(event, "private_companion_delayed_image_vision_text", ""),
        buffered_image_vision_limit,
    )
    if delayed_image_vision and not buffered_image_vision:
        buffered_image_vision = delayed_image_vision
    buffered_image_mode = _psp_host._single_line(buffered_image_context.get("image_mode"), 20) if isinstance(buffered_image_context, dict) else ""
    delayed_image_mode = _psp_host._single_line(getattr(event, "private_companion_delayed_image_mode", ""), 20)
    if not buffered_image_mode and delayed_image_mode:
        buffered_image_mode = delayed_image_mode
    vision_task = buffered_image_context.get("vision_task") if isinstance(buffered_image_context, dict) else None
    if not buffered_image_vision and isinstance(vision_task, _psp_host.asyncio.Task):
        vision_wait_timeout = self._private_image_vision_wait_budget_seconds()
        try:
            if vision_wait_timeout > 0:
                buffered_image_vision = _psp_host._single_line(await _psp_host.asyncio.wait_for(_psp_host.asyncio.shield(vision_task), timeout=vision_wait_timeout), buffered_image_vision_limit)
        except _psp_host.asyncio.TimeoutError:
            _psp_host.logger.warning("私聊图片视觉转述仍在进行,本轮先注入路径兜底: timeout=%.1fs", vision_wait_timeout)
        except Exception as exc:
            _psp_host.logger.warning("私聊图片视觉转述获取失败: %s", _psp_host._single_line(exc, 120))
    buffered_images_include_gif = (
        bool(_psp_host.runtime_persona_setting(self, "enable_private_image_gif_enhancement", True))
        and self._private_image_sources_include_gif(buffered_images)
        if buffered_images
        else False
    )
    if (
        buffered_images
        and not buffered_image_vision
        and buffered_image_mode != "no_vision"
        and (
            buffered_images_include_gif
            or (
                buffered_image_mode == "direct"
                and (
                    buffered_image_from_handoff
                    or not self._event_main_provider_supports_image(event)
                )
            )
        )
    ):
        buffered_image_vision = _psp_host._single_line(
            await self._transcribe_private_inbound_images(
                buffered_images,
                umo=str(getattr(event, "unified_msg_origin", "") or ""),
            ),
            buffered_image_vision_limit,
        )
    combined_text = ""
    private_buffer_key = self._semantic_buffer_key(f"private:{user_id}", user_id)
    private_buffer_snapshot = self._semantic_buffer_active_snapshot(private_buffer_key, force=True)
    if (
        not private_image_enhancement_enabled_for_request
        and isinstance(private_buffer_snapshot, dict)
        and _psp_host._single_line(private_buffer_snapshot.get("kind"), 40) == "image"
    ):
        buffers = getattr(self, "_semantic_message_buffers", None)
        if isinstance(buffers, dict):
            buffers.pop(private_buffer_key, None)
        private_buffer_snapshot = {}
    private_buffer_active = bool(private_buffer_snapshot)
    if not lightweight_passive or buffered_images or private_buffer_active:
        combined_text = await self._consume_semantic_message_buffer_for_event(event, private_chat=True)
    if combined_text:
        prompt_surface.add(
            _psp_host._turn_continuation_prompt_section(
                combined_text,
                private_chat=True,
            ),
            priority=50,
        )
        inbound_text = _psp_host._single_line(combined_text.replace("\n", " "), 260)
    if self._user_asks_recalled_messages(inbound_text):
        recall_section = self._format_recalled_messages_for_natural_query_prompt_section(
            event,
            limit=5,
        )
        if recall_section.content:
            prompt_surface.add(recall_section, priority=52)
    if self._feature_enabled_or_temp_unlocked("enable_food_menu_recommendation"):
        food_section = self._format_food_menu_reply_prompt_section(
            inbound_text,
            limit=3,
            user=current_user,
        )
    else:
        food_section = None
    meal_section = self._format_meal_care_reply_prompt_section(
        current_user,
        inbound_text,
    )
    if meal_section.content:
        prompt_surface.add(meal_section, priority=55)
    if food_section is not None and food_section.content:
        prompt_surface.add(food_section, priority=53)
    if (
        buffered_images
        and buffered_image_vision
        and buffered_image_mode != "no_vision"
        and self._private_image_user_has_specific_vision_request(inbound_text)
    ):
        contextual_vision = _psp_host._single_line(
            await self._transcribe_private_inbound_images(
                buffered_images,
                umo=str(getattr(event, "unified_msg_origin", "") or ""),
                user_text=inbound_text,
                force_contextual=True,
            ),
            buffered_image_vision_limit,
        )
        if contextual_vision:
            buffered_image_vision = contextual_vision
    _snapshot = locals()
    for _k in ['buffered_image_from_handoff', 'buffered_image_mode', 'buffered_image_vision', 'buffered_images', 'buffered_images_include_gif', 'exc', 'inbound_text', 'index', 'item', 'section']:
        if _k in _snapshot:
            setattr(ctx, _k, _snapshot[_k])
