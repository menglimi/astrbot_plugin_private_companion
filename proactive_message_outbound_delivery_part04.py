# -*- coding: utf-8 -*-
"""ProactiveMessageOutboundDeliveryPart04Mixin。

由 tools/split_mixin_domain.py 从 proactive_message_outbound_delivery.py 机械抽取（10 个方法 + 0 个模块级名字 + 0 个类级赋值 / 565 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveMessageOutboundDeliveryMixin）。
"""
from __future__ import annotations

from .proactive_message_outbound_delivery_shared import _now_ts, logger
from .proactive_message_outbound_delivery_shared import Any
from .proactive_message_outbound_delivery_shared import AssistantMessageSegment
from .proactive_message_outbound_delivery_shared import Image
from .proactive_message_outbound_delivery_shared import Record
from .proactive_message_outbound_delivery_shared import UserMessageSegment
from .proactive_message_outbound_delivery_shared import _ProactiveSendOutcome
from .proactive_message_outbound_delivery_shared import _format_history_media_marker
from .proactive_message_outbound_delivery_shared import _normalize_photo_subject_owner
from .proactive_message_outbound_delivery_shared import _photo_subject_owner_prompt_label
from .proactive_message_outbound_delivery_shared import _single_line
from .proactive_message_outbound_delivery_shared import _strip_outbound_control_blocks
from .proactive_message_outbound_delivery_shared import _today_key
from .proactive_message_outbound_delivery_shared import asyncio
from .proactive_message_outbound_delivery_shared import collect_proactive_delivery
from .proactive_message_outbound_delivery_shared import json
from .proactive_message_outbound_delivery_shared import re
from .proactive_message_outbound_delivery_shared import runtime_persona_setting
from .proactive_message_outbound_delivery_shared import sanitize_llm_segment_control_tokens



class ProactiveMessageOutboundDeliveryPart04Mixin:
    """ProactiveMessageOutboundDeliveryPart04Mixin（从 ProactiveMessageOutboundDeliveryMixin 拆出）。"""


    @collect_proactive_delivery
    async def _send_proactive_message_chain(
        self,
        umo: str,
        text: str,
        image_path: str = "",
        *,
        extra_components: list[Any] | None = None,
        quote_message_id: str = "",
        disable_segmenting: bool = False,
    ) -> _ProactiveSendOutcome:
        if not await self._proactive_persona_delivery_allowed(umo):
            return _ProactiveSendOutcome(False, False, note="主动投递人格已变化，已取消发送")
        # Recheck at the final delivery boundary. A realtime call may start
        # after a proactive candidate was planned but before it is sent.
        busy_context_getter = getattr(self, "_busy_reply_proactive_block_context", None)
        if callable(busy_context_getter):
            try:
                busy_context = busy_context_getter({}, now=_now_ts())
            except TypeError:
                busy_context = busy_context_getter({}, now=_now_ts(), umo=umo)
            except Exception:
                busy_context = {}
            if isinstance(busy_context, dict) and busy_context.get("kind") == "external_realtime":
                logger.info(
                    "实时共同活动期间在最终发送边界取消主动消息: umo=%s",
                    _single_line(umo, 140),
                )
                return _ProactiveSendOutcome(False, False, note="实时共同活动期间已取消主动消息")
        trigger_message_id = _single_line(quote_message_id, 120)
        placeholder_cleaner = getattr(self, "_sanitize_orphan_tts_placeholders", None)
        if callable(placeholder_cleaner):
            cleaned_text = placeholder_cleaner(text)
            if cleaned_text != text:
                logger.warning(
                    "主动发送前清理孤儿 TTS 占位符: umo=%s before=%s after=%s",
                    _single_line(umo, 120),
                    _single_line(text, 120),
                    _single_line(cleaned_text, 120),
                )
                text = cleaned_text
        reaction_pending: dict[str, Any] | None = None
        reaction_delivery_mode = self._normalize_reaction_expression_delivery_mode(
            runtime_persona_setting(self, "reaction_expression_delivery_mode", "separate_after")
        )
        has_existing_media = bool(
            image_path
            or extra_components
            or (text and self._contains_inline_image_tag(text))
        )
        if has_existing_media:
            self._clear_proactive_reaction_intent(umo)
        else:
            reaction_component, reaction_pending = await self._prepare_proactive_reaction_attachment(
                umo,
                text,
            )
            if reaction_component is not None:
                try:
                    object.__setattr__(
                        reaction_component,
                        "_private_companion_reaction_expression",
                        True,
                    )
                except Exception:
                    pass
                if isinstance(reaction_pending, dict) and reaction_pending.get("sticker_only"):
                    try:
                        reaction_sent = bool(
                            await self._send_chain_components(
                                umo,
                                [reaction_component],
                            )
                        )
                    except Exception as exc:
                        reaction_sent = False
                        logger.warning(
                            "主动纯表情投递失败: umo=%s error_type=%s",
                            _single_line(umo, 140),
                            type(exc).__name__,
                        )
                    await self._settle_proactive_reaction_attachment(
                        reaction_pending,
                        sent=reaction_sent,
                        reason="delivered" if reaction_sent else "delivery_failed",
                    )
                    return _ProactiveSendOutcome(
                        delivered=reaction_sent,
                        complete=reaction_sent,
                        extra_components_delivered=1 if reaction_sent else 0,
                        note="" if reaction_sent else "主动纯表情未送达",
                    )
                if isinstance(reaction_pending, dict):
                    reaction_pending["delivery_mode"] = reaction_delivery_mode
                if reaction_delivery_mode == "separate_before":
                    try:
                        reaction_sent = bool(
                            await self._send_chain_components(
                                umo,
                                [reaction_component],
                            )
                        )
                    except Exception as exc:
                        reaction_sent = False
                        logger.warning(
                            "主动表情先行发送失败，继续发送正文: "
                            "umo=%s error_type=%s",
                            _single_line(umo, 140),
                            type(exc).__name__,
                        )
                    await self._settle_proactive_reaction_attachment(
                        reaction_pending,
                        sent=reaction_sent,
                        reason="delivered" if reaction_sent else "delivery_failed",
                    )
                    reaction_pending = None
                else:
                    extra_components = [reaction_component]
        if has_existing_media or image_path or extra_components:
            try:
                outcome = await self._send_media_proactive_chain(
                    umo,
                    text,
                    image_path,
                    extra_components=extra_components,
                    quote_message_id=quote_message_id,
                    disable_segmenting=disable_segmenting,
                    media_delivery_mode=(
                        reaction_delivery_mode
                        if reaction_pending is not None
                        else "separate_after"
                    ),
                    require_complete_text_before_media=bool(
                        reaction_pending is not None
                        and reaction_delivery_mode == "separate_after"
                    ),
                )
            except Exception:
                await self._settle_proactive_reaction_attachment(
                    reaction_pending,
                    sent=False,
                    reason=(
                        "primary_not_delivered"
                        if reaction_pending is not None
                        and reaction_delivery_mode == "separate_after"
                        else "delivery_failed"
                    ),
                )
                raise
            if reaction_pending is not None:
                reaction_sent = bool(outcome.extra_components_delivered)
                settlement_reason = (
                    "delivered"
                    if reaction_sent
                    else "primary_not_delivered"
                    if reaction_delivery_mode == "separate_after"
                    and not outcome.primary_complete
                    else "delivery_failed"
                )
                await self._settle_proactive_reaction_attachment(
                    reaction_pending,
                    sent=reaction_sent,
                    reason=settlement_reason,
                )
            return outcome
        if text:
            await self._maybe_send_input_status(umo, text)
        splitter = getattr(self, "_split_llm_controlled_text_for_event", None)
        if (
            callable(splitter)
            and bool(runtime_persona_setting(self, "enable_llm_controlled_segmenting", False))
            and not disable_segmenting
            and self._segmented_platform_allows(umo=umo)
            and self._segmented_scope_allows_umo(umo)
        ):
            segments = splitter(None, text, umo=umo)
        else:
            segments = self._split_proactive_text(
                text,
                umo=umo,
                image_path="",
                extra_components=None,
                disable_segmenting=(
                    disable_segmenting
                    or not self._segmented_platform_allows(umo=umo)
                    or not self._segmented_scope_allows_umo(umo)
                ),
            )
        if len(segments) > 1:
            logger.info(
                "主动文本已分段: umo=%s segments=%s lengths=%s",
                _single_line(umo, 140),
                len(segments),
                [len(segment) for segment in segments],
            )
        if len(segments) <= 1:
            outbound_text = segments[0] if segments else text
            if not str(outbound_text or "").strip():
                return _ProactiveSendOutcome(False, False, note="主动正文为空")
            if quote_message_id and self._quote_skip_reason_for_short_reply(outbound_text):
                quote_message_id = ""
            recalled_message_id = self._should_cancel_reply_for_recalled_message_ids(trigger_message_id)
            if recalled_message_id:
                logger.info("触发消息已撤回，取消主动消息发送: umo=%s message_id=%s", umo, recalled_message_id)
                return _ProactiveSendOutcome(False, False, note="触发消息已撤回")
            sent = await self._send_chain_components(
                umo,
                self._with_optional_reply(
                    [
                        self._proactive_plain_segment_component(outbound_text, full_text=text, index=0, count=1)
                    ],
                    quote_message_id,
                ),
            )
            return _ProactiveSendOutcome(
                delivered=bool(sent),
                complete=bool(sent),
                delivered_text=outbound_text if sent else "",
                note="" if sent else "主动发送组件被取消或清空",
            )
        recalled_message_id = self._should_cancel_reply_for_recalled_message_ids(trigger_message_id)
        if recalled_message_id:
            logger.info("触发消息已撤回，取消主动合并分段发送: umo=%s message_id=%s", umo, recalled_message_id)
            return _ProactiveSendOutcome(False, False, note="触发消息已撤回")
        if await self._send_segmented_proactive_forward_message(umo, segments, source="proactive_text"):
            return _ProactiveSendOutcome(True, True, delivered_text="\n".join(segments).strip())
        delivered_segments: list[str] = []
        complete = True
        for index, segment in enumerate(segments):
            if index == 0 and quote_message_id and self._quote_skip_reason_for_short_reply(segment):
                quote_message_id = ""
            recalled_message_id = self._should_cancel_reply_for_recalled_message_ids(trigger_message_id)
            if recalled_message_id:
                logger.info("触发消息已撤回，停止主动消息分段发送: umo=%s message_id=%s index=%s", umo, recalled_message_id, index + 1)
                return _ProactiveSendOutcome(
                    bool(delivered_segments),
                    False,
                    delivered_text="\n".join(delivered_segments).strip(),
                    note=f"第 {index + 1} 段发送前触发消息已撤回",
                )
            segment_comp = self._proactive_plain_segment_component(segment, full_text=text, index=index, count=len(segments))
            chain = self._with_optional_reply([segment_comp], quote_message_id) if index == 0 else [segment_comp]
            try:
                sent = await self._send_chain_components(umo, chain)
            except Exception as exc:
                if not delivered_segments:
                    raise
                logger.warning(
                    "主动文本部分送达后后续分段失败，不再整条重试: umo=%s index=%s error=%s",
                    _single_line(umo, 140),
                    index + 1,
                    _single_line(exc, 180),
                )
                return _ProactiveSendOutcome(
                    True,
                    False,
                    delivered_text="\n".join(delivered_segments).strip(),
                    note=f"第 {index + 1} 段发送失败：{_single_line(exc, 160)}",
                )
            if sent:
                delivered_segments.append(segment)
            else:
                complete = False
            quote_message_id = ""
            if index < len(segments) - 1:
                try:
                    interval = await self._calc_segmented_proactive_interval(segment, umo=umo)
                except TypeError:
                    interval = await self._calc_segmented_proactive_interval(segment)
                await asyncio.sleep(interval)
        delivered_text = "\n".join(delivered_segments).strip()
        return _ProactiveSendOutcome(
            delivered=bool(delivered_text),
            complete=bool(delivered_text and complete),
            delivered_text=delivered_text,
            note="" if complete else "部分分段被发送钩子取消或清空",
        )

    def _build_outbound_result(
        self,
        text: str,
        image_path: str = "",
        extra_components: list[Any] | None = None,
    ) -> Any:
        chain = self._build_outbound_chain(text, image_path, extra_components=extra_components)
        return self._build_result_from_chain(chain)

    def _build_proactive_archive_user_prompt(
        self,
        *,
        reason: str,
        action: str,
        motive: str = "",
        action_summary: str = "",
    ) -> str:
        return ""

    @staticmethod
    def _proactive_component_is_image(component: Any) -> bool:
        return isinstance(component, Image) or bool(
            getattr(component, "_private_companion_reaction_expression", False)
        )

    @staticmethod
    def _proactive_components_contain_image(components: list[Any] | None) -> bool:
        return any(
            ProactiveMessageOutboundDeliveryPart04Mixin._proactive_component_is_image(component)
            for component in (components or [])
        )

    def _build_actual_proactive_delivery_summary(
        self,
        *,
        text: str,
        image_path: str = "",
        extra_components: list[Any] | None = None,
        original_summary: str = "",
    ) -> str:
        parts: list[str] = []
        visible_text = self._visible_text_without_tts_reading(text, limit=320)
        if visible_text:
            parts.append(f"文字消息：{visible_text}")

        image_count = int(bool(image_path)) + sum(
            1
            for component in (extra_components or [])
            if self._proactive_component_is_image(component)
        )
        if image_count:
            photo_caption = ""
            if "：" in str(original_summary or "") or ":" in str(original_summary or ""):
                photo_caption = _single_line(
                    re.split(r"[:：]", str(original_summary), maxsplit=1)[-1],
                    220,
                )
            image_label = "图片" if image_count == 1 else f"{image_count} 张图片"
            if photo_caption and photo_caption not in {"发图", "图片", "photo_text"}:
                parts.append(f"{image_label}：{photo_caption}")
            else:
                parts.append(f"{image_label}已发送")

        voice_count = sum(
            1 for component in (extra_components or []) if isinstance(component, Record)
        )
        if voice_count:
            parts.append("语音消息已发送" if voice_count == 1 else f"{voice_count} 条语音消息已发送")

        other_count = sum(
            1
            for component in (extra_components or [])
            if not self._proactive_component_is_image(component)
            and not isinstance(component, Record)
        )
        if other_count:
            parts.append(f"{other_count} 个附加消息组件已发送")
        return _single_line("；".join(parts), 500)

    def _reconcile_proactive_delivery_metadata(
        self,
        *,
        text: str,
        image_path: str = "",
        extra_components: list[Any] | None = None,
        action: str = "message",
        action_summary: str = "",
        delivery_complete: bool = True,
    ) -> tuple[str, str, bool]:
        delivered_photo = bool(image_path) or self._proactive_components_contain_image(extra_components)
        if delivery_complete:
            return action or "message", action_summary, delivered_photo

        action_parts = [part.strip() for part in str(action or "").split("+") if part.strip()]
        removed_media = False
        if not delivered_photo and "photo_text" in action_parts:
            action_parts = [part for part in action_parts if part != "photo_text"]
            removed_media = True
        delivered_voice = any(isinstance(component, Record) for component in (extra_components or []))
        if not delivered_voice and "voice" in action_parts:
            action_parts = [part for part in action_parts if part != "voice"]
            removed_media = True
        if removed_media and text and "message" not in action_parts:
            action_parts.insert(0, "message")
        actual_action = "+".join(action_parts) or ("message" if text else action or "message")
        actual_summary = self._build_actual_proactive_delivery_summary(
            text=text,
            image_path=image_path,
            extra_components=extra_components,
            original_summary=action_summary,
        )
        return actual_action, actual_summary or "主动消息仅部分送达。", delivered_photo

    def _build_proactive_archive_assistant_text(
        self,
        *,
        text: str,
        image_path: str = "",
        extra_components: list[Any] | None = None,
        action_summary: str = "",
        photo_subject_owner: str = "",
    ) -> str:
        original_is_receipt = self._is_proactive_delivery_receipt_text(text)
        message_text = sanitize_llm_segment_control_tokens(
            self._visible_text_without_tts_reading(text, limit=1000)
        )
        attachment_notes: list[str] = []
        history_image_count = 0
        history_record_count = 0
        if image_path:
            history_image_count += 1
            photo_caption = ""
            if "：" in str(action_summary or "") or ":" in str(action_summary or ""):
                photo_caption = _single_line(re.split(r"[:：]", str(action_summary), maxsplit=1)[-1], 220)
            if photo_caption and photo_caption not in {"发图", "图片", "photo_text"}:
                attachment_notes.append(f"图片画面：{photo_caption}")
            normalized_owner = _normalize_photo_subject_owner(photo_subject_owner)
            if normalized_owner:
                attachment_notes.append(f"图片主体：{_photo_subject_owner_prompt_label(normalized_owner)}")
        if extra_components:
            tts_notes: list[str] = []
            note_builder = getattr(self, "_tts_component_log_note", None)
            image_components = [
                comp
                for comp in extra_components
                if self._proactive_component_is_image(comp)
            ]
            for comp in extra_components:
                if isinstance(comp, Record) and callable(note_builder):
                    note = _single_line(note_builder(comp), 220)
                    if note:
                        tts_notes.append(note)
            if image_components:
                history_image_count += len(image_components)
                photo_caption = ""
                if "：" in str(action_summary or "") or ":" in str(action_summary or ""):
                    photo_caption = _single_line(re.split(r"[:：]", str(action_summary), maxsplit=1)[-1], 220)
                if photo_caption and photo_caption not in {"发图", "图片", "photo_text"}:
                    attachment_notes.append(f"图片画面：{photo_caption}")
                normalized_owner = _normalize_photo_subject_owner(photo_subject_owner)
                if normalized_owner:
                    attachment_notes.append(f"图片主体：{_photo_subject_owner_prompt_label(normalized_owner)}")
            if tts_notes:
                attachment_notes.extend(tts_notes[:3])
            record_count = sum(1 for comp in extra_components if isinstance(comp, Record))
            history_record_count += record_count
            other_count = len(extra_components) - len(image_components) - record_count
            if other_count > 0:
                attachment_notes.append(f"随消息发送了 {other_count} 个附加消息组件")
        if attachment_notes:
            suffix = "（" + ",".join(attachment_notes) + "）"
            message_text = f"{message_text}{suffix}" if message_text else suffix
        media_marker = _format_history_media_marker(
            images=history_image_count,
            records=history_record_count,
        )
        if media_marker:
            message_text = f"{message_text}\n{media_marker}" if message_text else media_marker
        if message_text:
            return message_text
        if original_is_receipt:
            return ""
        return _single_line(action_summary, 160) or "主动向用户发送了一条消息。"

    async def _archive_proactive_message_to_conversation(
        self,
        *,
        user: dict[str, Any],
        user_prompt: str,
        assistant_response: str,
        umo: str = "",
    ) -> bool:
        umo = str(umo or user.get("umo") or "").strip()
        if not umo or not assistant_response:
            return False
        visible_assistant_response = sanitize_llm_segment_control_tokens(
            _strip_outbound_control_blocks(
                assistant_response,
                enabled=bool(runtime_persona_setting(self, "enable_framework_error_leak_guard", True)),
                tts_enabled=bool(runtime_persona_setting(self, "enable_tts_enhancement", False)),
            )
        )
        if not visible_assistant_response:
            return False
        for attempt in range(4):
            try:
                safe_user_prompt = str(user_prompt or "").strip()
                archive_context_only = not safe_user_prompt or self._proactive_archive_context_text(
                    safe_user_prompt
                )
                assistant_msg_obj = AssistantMessageSegment(content=visible_assistant_response)

                async def _write():
                    conv_id = await self._ensure_conversation_id_for_umo(umo, title="Private Companion 主动消息")
                    if not conv_id:
                        return False
                    conversation_manager = self.context.conversation_manager
                    if archive_context_only:
                        conversation = await conversation_manager.get_conversation(umo, conv_id)
                        if conversation is None:
                            return False
                        raw_history = getattr(conversation, "history", "[]")
                        if isinstance(raw_history, str):
                            history = json.loads(raw_history or "[]")
                        elif isinstance(raw_history, list):
                            history = list(raw_history)
                        else:
                            history = []
                        history.append(assistant_msg_obj.model_dump())
                        await conversation_manager.update_conversation(umo, conv_id, history=history)
                    else:
                        await conversation_manager.add_message_pair(
                            cid=conv_id,
                            user_message=UserMessageSegment(content=safe_user_prompt),
                            assistant_message=assistant_msg_obj,
                        )
                    return True

                written = await self._conversation_db_operation("archive_proactive_message", _write)
                if not written:
                    logger.warning("主动消息存档失败: 无法获取或创建 AstrBot 会话 history umo=%s", _single_line(umo, 140))
                    return False
                if attempt > 0:
                    logger.info("主动消息写入 AstrBot 会话历史成功: %s retry=%s", umo, attempt)
                else:
                    logger.info("已将主动消息写入 AstrBot 会话历史: %s", umo)
                return True
            except Exception as e:
                text = str(e or "").lower()
                if ("database is locked" in text or "sqlite3.operationalerror" in text) and attempt < 3:
                    await asyncio.sleep(0.25 * (attempt + 1))
                    continue
                logger.warning("主动消息写入会话历史失败: %s", e)
                return False
        return False

    def _format_story_plan_for_prompt(self) -> str:
        plan = self.data.get("daily_story_plan", {})
        if not isinstance(plan, dict) or plan.get("date") != _today_key():
            return "（暂无）"
        lines = []
        now_minutes = self._environment_now_minutes()
        events = plan.get("today_events", [])
        if isinstance(events, list) and events:
            nearby_events = [
                item for item in events
                if isinstance(item, dict) and self._story_item_relevant_to_now(item, now_minutes)
            ][:6]
            if nearby_events:
                lines.append("附近可能发生：")
                for item in nearby_events:
                    lines.append(f"- {item.get('window', '')}｜{item.get('event', '')}｜{item.get('mood', '')}")
        proactive = plan.get("proactive_events", [])
        if isinstance(proactive, list) and proactive:
            nearby_proactive = [
                item for item in proactive
                if isinstance(item, dict) and self._story_item_relevant_to_now(item, now_minutes, future_minutes=240)
            ][:6]
            if nearby_proactive:
                lines.append("附近主动计划：")
                for item in nearby_proactive:
                    lines.append(
                        f"- {item.get('window', '')}｜{item.get('reason', '')}｜{item.get('action', 'message')}｜"
                        f"{item.get('why', '')}｜{item.get('topic', '')}｜{item.get('motive', '')}｜"
                        f"{item.get('scene', '')}｜{item.get('tone', '')}｜{item.get('impulse', '')}"
                    )
        long_term = plan.get("long_term_events", [])
        if isinstance(long_term, list) and long_term:
            lines.append("长线事件：")
            for item in long_term[:4]:
                if isinstance(item, dict):
                    lines.append(
                        f"- {item.get('title', '')}｜{item.get('status', '')}｜"
                        f"{item.get('tendency', '')}｜{item.get('next_hint', '')}"
                    )
        return "\n".join(lines) if lines else "（暂无）"
