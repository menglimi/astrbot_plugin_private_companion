# -*- coding: utf-8 -*-
"""ProactiveMessageOutboundDeliveryPart03Mixin。

由 tools/split_mixin_domain.py 从 proactive_message_outbound_delivery.py 机械抽取（6 个方法 + 0 个模块级名字 + 0 个类级赋值 / 554 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveMessageOutboundDeliveryMixin）。
"""
from __future__ import annotations

from .proactive_message_outbound_delivery_shared import _host_Image, logger
from .proactive_message_outbound_delivery_shared import Any
from .proactive_message_outbound_delivery_shared import Image
from .proactive_message_outbound_delivery_shared import Plain
from .proactive_message_outbound_delivery_shared import Record
from .proactive_message_outbound_delivery_shared import SimpleNamespace
from .proactive_message_outbound_delivery_shared import _ProactiveSendOutcome
from .proactive_message_outbound_delivery_shared import _path_text
from .proactive_message_outbound_delivery_shared import _safe_int
from .proactive_message_outbound_delivery_shared import _single_line
from .proactive_message_outbound_delivery_shared import asyncio
from .proactive_message_outbound_delivery_shared import component_kind
from .proactive_message_outbound_delivery_shared import component_order_from_owner
from .proactive_message_outbound_delivery_shared import component_strategies_from_owner
from .proactive_message_outbound_delivery_shared import inspect
from .proactive_message_outbound_delivery_shared import json
from .proactive_message_outbound_delivery_shared import os
from .proactive_message_outbound_delivery_shared import plan_component_chunks
from .proactive_message_outbound_delivery_shared import runtime_persona_setting



class ProactiveMessageOutboundDeliveryPart03Mixin:
    """ProactiveMessageOutboundDeliveryPart03Mixin（从 ProactiveMessageOutboundDeliveryMixin 拆出）。"""


    async def _send_media_proactive_chain(
        self,
        umo: str,
        text: str,
        image_path: str = "",
        *,
        extra_components: list[Any] | None = None,
        quote_message_id: str = "",
        disable_segmenting: bool = False,
        media_delivery_mode: str = "separate_after",
        require_complete_text_before_media: bool = False,
    ) -> _ProactiveSendOutcome:
        trigger_message_id = _single_line(quote_message_id, 120)
        delivered_segments: list[str] = []
        complete = True
        image_delivered = False
        extra_components_delivered = 0
        primary_complete = False
        failure_note = ""

        def outcome(*, note: str = "") -> _ProactiveSendOutcome:
            delivered_text = "\n".join(item for item in delivered_segments if item).strip()
            delivered = bool(delivered_text or image_delivered or extra_components_delivered)
            resolved_note = _single_line(note or failure_note, 240)
            return _ProactiveSendOutcome(
                delivered=delivered,
                complete=bool(delivered and complete and not resolved_note),
                delivered_text=delivered_text,
                image_delivered=image_delivered,
                extra_components_delivered=extra_components_delivered,
                note=resolved_note,
                primary_complete=primary_complete,
            )

        outbound_components = [
            component for component in (extra_components or []) if component is not None
        ]
        has_prebuilt_voice = any(
            isinstance(component, Record) for component in outbound_components
        )
        if self._contains_inline_image_tag(text):
            image_path = ""
            outbound_components = []
        if text:
            await self._maybe_send_input_status(umo, text)
        if media_delivery_mode == "same_message":
            platform_supports = getattr(self, "_platform_supports", None)
            platform_quote = not callable(platform_supports) or platform_supports(
                "reply_quote",
                umo=umo,
            )
            if quote_message_id and not platform_quote:
                logger.info(
                    "当前平台不支持主动引用，正文与表情同链发送已降级为普通发送: umo=%s",
                    _single_line(umo, 140),
                )
                quote_message_id = ""
            recalled_message_id = self._should_cancel_reply_for_recalled_message_ids(
                trigger_message_id
            )
            if recalled_message_id:
                logger.info(
                    "触发消息已撤回，取消主动正文与表情同链发送: umo=%s message_id=%s",
                    umo,
                    recalled_message_id,
                )
                complete = False
                return outcome(note="触发消息已撤回")
            combined_chain = self._build_outbound_chain(
                text,
                image_path,
                extra_components=outbound_components,
            )
            combined_chain = self._with_optional_reply(
                combined_chain,
                quote_message_id,
            )
            sent = await self._send_chain_components(umo, combined_chain)
            if sent:
                delivered_segments.append(text)
                image_delivered = bool(image_path and os.path.exists(image_path))
                extra_components_delivered = len(outbound_components)
                primary_complete = True
            else:
                complete = False
            return outcome(
                note="" if sent else "主动正文与表情同链发送未被平台接受"
            )
        platform_supports = getattr(self, "_platform_supports", None)
        platform_segmented = self._segmented_platform_allows(umo=umo)
        platform_quote = not callable(platform_supports) or platform_supports("reply_quote", umo=umo)
        if quote_message_id and not platform_quote:
            logger.info(
                "当前平台不支持主动引用，已降级为普通发送: umo=%s",
                _single_line(umo, 140),
            )
            quote_message_id = ""
        splitter = getattr(self, "_split_llm_controlled_text_for_event", None)
        if (
            callable(splitter)
            and bool(runtime_persona_setting(self, "enable_llm_controlled_segmenting", False))
            and not disable_segmenting
            and platform_segmented
            and self._segmented_scope_allows_umo(umo)
        ):
            segments = splitter(None, text, umo=umo)
        else:
            segments = self._split_proactive_text(
                text,
                umo=umo,
                image_path="",
                extra_components=None,
                disable_segmenting=disable_segmenting or not platform_segmented or not self._segmented_scope_allows_umo(umo),
            )
        if len(segments) > 1:
            logger.info(
                "主动媒体文本已分段: umo=%s segments=%s lengths=%s",
                _single_line(umo, 140),
                len(segments),
                [len(segment) for segment in segments],
            )
        if quote_message_id and segments and self._quote_skip_reason_for_short_reply(segments[0]):
            quote_message_id = ""

        image_exists = bool(image_path and os.path.exists(image_path))
        path_image_component: Any | None = None
        if image_exists:
            image_chain = self._build_outbound_chain("", image_path)
            path_image_component = next(
                (component for component in image_chain if isinstance(component, Image)),
                None,
            )
            image_exists = path_image_component is not None

        leading_components: list[Any] = []
        trailing_components: list[Any] = []
        for component in outbound_components:
            if component_kind(component) in {"voice", "at", "reply"}:
                leading_components.append(component)
            else:
                trailing_components.append(component)

        source_chain: list[Any] = list(leading_components)
        if segments:
            source_chain.append(Plain(text))
        source_chain.extend(trailing_components)
        if path_image_component is not None:
            source_chain.append(path_image_component)
        if quote_message_id:
            source_chain = self._with_optional_reply(source_chain, quote_message_id)

        strategies = component_strategies_from_owner(self)
        strategies["reaction"] = (
            "inline" if media_delivery_mode == "same_message" else "separate"
        )
        chunks, _changed, _split_changed, _full_text = plan_component_chunks(
            source_chain,
            plain_type=Plain,
            split_text=lambda _value: list(segments),
            strategies=strategies,
            component_order=component_order_from_owner(self),
            classify=component_kind,
        )

        primary_components: list[Plain] = []
        for chunk in chunks:
            for component_index, component in enumerate(chunk):
                if not isinstance(component, Plain) or len(primary_components) >= len(segments):
                    continue
                segment_index = len(primary_components)
                segment_component = self._proactive_plain_segment_component(
                    segments[segment_index],
                    full_text=text,
                    index=segment_index,
                    count=len(segments),
                    suppress_tts=has_prebuilt_voice,
                )
                try:
                    object.__setattr__(
                        segment_component,
                        "_private_companion_proactive_primary_text",
                        True,
                    )
                except Exception:
                    pass
                chunk[component_index] = segment_component
                primary_components.append(segment_component)

        has_media = bool(outbound_components or image_exists)
        if has_media:
            logger.info(
                "主动媒体已按组件策略规划: text_segments=%s chunks=%s image=%s extra_components=%s strategies=%s",
                len(segments),
                len(chunks),
                image_exists,
                len(outbound_components),
                strategies,
            )
        if not chunks:
            complete = False
            return outcome(note="主动正文与媒体均为空")

        remaining_extra_components = list(outbound_components)
        delivered_primary_count = 0

        def chunk_primary_texts(chunk: list[Any]) -> list[str]:
            return [
                str(getattr(component, "text", "") or "").strip()
                for component in chunk
                if isinstance(component, Plain)
                and bool(
                    getattr(
                        component,
                        "_private_companion_proactive_primary_text",
                        False,
                    )
                )
                and str(getattr(component, "text", "") or "").strip()
            ]

        for chunk_index, chunk in enumerate(chunks):
            primary_texts = chunk_primary_texts(chunk)
            chunk_has_reaction = any(
                component_kind(component) == "reaction" for component in chunk
            )
            primary_complete = bool(
                segments and delivered_primary_count >= len(segments)
            )
            if (
                require_complete_text_before_media
                and chunk_has_reaction
                and not primary_complete
            ):
                complete = False
                return outcome(note="主动正文未完整送达，已跳过表情图片")

            recalled_message_id = self._should_cancel_reply_for_recalled_message_ids(
                trigger_message_id
            )
            if recalled_message_id:
                logger.info(
                    "触发消息已撤回，停止主动组件发送: umo=%s message_id=%s chunk=%s/%s",
                    umo,
                    recalled_message_id,
                    chunk_index + 1,
                    len(chunks),
                )
                complete = False
                return outcome(note=f"第 {chunk_index + 1} 条发送前触发消息已撤回")

            try:
                sent = await self._send_chain_components(umo, chunk)
            except Exception as exc:
                has_delivered_content = bool(
                    delivered_segments or image_delivered or extra_components_delivered
                )
                has_future_primary = any(
                    chunk_primary_texts(candidate)
                    for candidate in chunks[chunk_index + 1 :]
                )
                if not primary_texts and has_future_primary:
                    complete = False
                    failure_note = failure_note or (
                        f"第 {chunk_index + 1} 条组件发送失败：{_single_line(exc, 160)}"
                    )
                    logger.warning(
                        "主动前置组件发送失败，继续发送正文: umo=%s chunk=%s error=%s",
                        _single_line(umo, 140),
                        chunk_index + 1,
                        _single_line(exc, 180),
                    )
                    continue
                if not has_delivered_content:
                    raise
                complete = False
                logger.warning(
                    "主动组件部分送达后后续发送失败，不再整条重试: umo=%s chunk=%s error=%s",
                    _single_line(umo, 140),
                    chunk_index + 1,
                    _single_line(exc, 180),
                )
                return outcome(
                    note=f"第 {chunk_index + 1} 条发送失败：{_single_line(exc, 160)}"
                )

            if not sent:
                complete = False
                failure_note = failure_note or f"第 {chunk_index + 1} 条未被平台接受"
                continue

            if primary_texts:
                delivered_segments.extend(primary_texts)
                delivered_primary_count += len(primary_texts)
            if path_image_component is not None and any(
                component is path_image_component for component in chunk
            ):
                image_delivered = True
            for sent_component in chunk:
                matched_index = next(
                    (
                        index
                        for index, candidate in enumerate(remaining_extra_components)
                        if sent_component is candidate
                    ),
                    -1,
                )
                if matched_index >= 0:
                    remaining_extra_components.pop(matched_index)
                    extra_components_delivered += 1

            primary_complete = bool(
                segments and delivered_primary_count >= len(segments)
            )
            if primary_texts and any(
                chunk_primary_texts(candidate)
                for candidate in chunks[chunk_index + 1 :]
            ):
                await asyncio.sleep(
                    await self._calc_segmented_proactive_interval(primary_texts[-1], umo=umo)
                )

        primary_complete = bool(
            segments and delivered_primary_count >= len(segments)
        )
        return outcome()

    @staticmethod
    def _normalize_reaction_expression_delivery_mode(value: Any) -> str:
        mode = str(value or "separate_after").strip().lower().replace("-", "_")
        aliases = {
            "after": "separate_after",
            "separate": "separate_after",
            "separate_after_text": "separate_after",
            "inline": "same_message",
            "current_chain": "same_message",
            "same_chain": "same_message",
            "before": "separate_before",
            "separate_before_text": "separate_before",
        }
        normalized = aliases.get(mode, mode)
        if normalized in {"separate_after", "same_message", "separate_before"}:
            return normalized
        return "separate_after"

    def _build_proactive_reaction_event(
        self,
        *,
        umo: str,
        user_id: str,
        visible_text: str,
    ) -> Any:
        extras: dict[str, Any] = {}
        event = SimpleNamespace(
            unified_msg_origin=umo,
            message_str=visible_text,
            extras=extras,
        )
        event.get_sender_id = lambda: user_id
        event.get_message_str = lambda: visible_text
        event.is_private_chat = lambda: True
        event.get_extra = lambda key: extras.get(key)
        event.set_extra = lambda key, value: extras.__setitem__(key, value)
        return event

    async def _prepare_proactive_reaction_attachment(
        self,
        umo: str,
        visible_text: str,
    ) -> tuple[Any | None, dict[str, Any] | None]:
        entry = self._pop_proactive_reaction_intent(umo)
        intent = entry.get("intent") if isinstance(entry.get("intent"), dict) else {}
        user_id = _single_line(entry.get("user_id"), 160)
        if (
            not intent
            or not user_id
            or not self._proactive_reaction_expression_enabled("message")
        ):
            return None, None
        sticker_only = self._proactive_reaction_intent_allows_sticker_only(intent)
        visible_checker = getattr(self, "_reaction_expression_has_visible_text", None)
        if callable(visible_checker) and not visible_checker(visible_text) and not sticker_only:
            return None, None

        event = self._build_proactive_reaction_event(
            umo=_single_line(umo, 240),
            user_id=user_id,
            visible_text=str(visible_text or ""),
        )
        preauthorize = getattr(self, "_preauthorize_reaction_expression_prompt", None)
        prepare = getattr(self, "_pc_reaction_expression_impl", None)
        settle = getattr(self, "_settle_reaction_expression_attachment_data", None)
        if not callable(preauthorize) or not callable(prepare) or not callable(settle):
            return None, None
        try:
            if not await preauthorize(event):
                return None, None
            raw_prepared = await prepare(
                event,
                query=_single_line(intent.get("provider_query"), 500),
                context=_single_line(intent.get("context"), 1000)
                or _single_line(visible_text, 700),
                meme_only=True,
                send=True,
                purpose=_single_line(intent.get("purpose"), 120),
                emotion=_single_line(intent.get("emotion"), 80),
                intensity=_safe_int(intent.get("intensity"), 0, 0, 5),
                candidate_queries=intent.get("candidate_queries", []),
                attach_only=True,
            )
            prepared = json.loads(raw_prepared)
        except Exception as exc:
            pending = getattr(
                event,
                "_private_companion_reaction_expression_pending_attachment",
                None,
            )
            if isinstance(pending, dict):
                await settle(pending, sent=False, reason="attachment_prepare_failed")
            logger.warning(
                "主动表情附件准备失败,继续发送纯文字: error_type=%s",
                type(exc).__name__,
            )
            return None, None
        if not isinstance(prepared, dict) or prepared.get("decision") != "attach":
            return None, None

        pending = getattr(
            event,
            "_private_companion_reaction_expression_pending_attachment",
            None,
        )
        image_path = _path_text(prepared.get("path"), 1000)
        if not isinstance(pending, dict) or not image_path or not os.path.isfile(image_path):
            if isinstance(pending, dict):
                await settle(pending, sent=False, reason="attachment_file_missing")
            return None, None
        pending["sticker_only"] = sticker_only
        try:
            builder = getattr(self, "_build_reaction_image_component", None)
            if callable(builder):
                image_component = builder(event, image_path)
            else:
                try:
                    image_component = _host_Image().fromFileSystem(image_path)
                except AttributeError:
                    image_component = _host_Image().from_file_system(image_path)
        except Exception as exc:
            await settle(pending, sent=False, reason="attachment_component_failed")
            logger.warning(
                "主动表情图片组件构建失败,继续发送纯文字: error_type=%s",
                type(exc).__name__,
            )
            return None, None

        pending["attached"] = True
        pending["component"] = image_component
        runtime_logger = getattr(self, "_log_reaction_expression_event", None)
        if callable(runtime_logger):
            runtime_logger(
                event,
                stage="attachment",
                decision="accepted",
                reason="attachment_appended",
                scope="private",
                found=True,
                sent=False,
                image_id=prepared.get("image_id"),
                confidence=prepared.get("confidence"),
                cache_hit=prepared.get("cache_hit"),
                latency_ms=prepared.get("lookup_latency_ms"),
                match_basis=pending.get("match_basis"),
            )
        return image_component, pending

    async def _settle_proactive_reaction_attachment(
        self,
        pending: dict[str, Any] | None,
        *,
        sent: bool,
        reason: str,
    ) -> None:
        if not isinstance(pending, dict):
            return
        settle = getattr(self, "_settle_reaction_expression_attachment_data", None)
        if not callable(settle):
            return
        try:
            await settle(pending, sent=sent, reason=reason)
        except Exception as exc:
            # Delivery state is authoritative. A bookkeeping failure must not
            # make the caller retry content that the platform already received.
            logger.warning(
                "主动表情发送结算失败,不改变消息投递结果: "
                "sent=%s reason=%s error_type=%s",
                bool(sent),
                _single_line(reason, 80),
                type(exc).__name__,
            )

    async def _proactive_persona_delivery_allowed(self, target_umo: str) -> bool:
        """Revalidate the scheduled persona immediately before platform I/O."""
        validator = getattr(self, "_validate_proactive_persona_delivery", None)
        if not callable(validator):
            return True

        multi_persona = bool(getattr(self, "enable_multi_persona_mode", False))
        active_getter = getattr(self, "_active_persona_scope", None)
        scheduled_persona_id = ""
        if callable(active_getter):
            try:
                scheduled_persona_id = str(active_getter() or "").strip()
            except Exception:
                scheduled_persona_id = ""
        if not multi_persona and not scheduled_persona_id:
            effective_getter = getattr(self, "_effective_plugin_persona_id", None)
            if callable(effective_getter):
                try:
                    scheduled_persona_id = str(effective_getter() or "").strip()
                except Exception:
                    scheduled_persona_id = ""
        if not multi_persona and not scheduled_persona_id:
            primary_getter = getattr(self, "_primary_persona_id", None)
            try:
                scheduled_persona_id = str(
                    primary_getter()
                    if callable(primary_getter)
                    else getattr(self, "plugin_specific_persona_id", "")
                ).strip()
            except Exception:
                scheduled_persona_id = ""

        try:
            result = validator(target_umo, scheduled_persona_id)
            if inspect.isawaitable(result):
                result = await result
            allowed = bool(result.get("ok")) if isinstance(result, dict) else bool(result)
        except Exception as exc:
            logger.warning(
                "主动消息最终人格一致性校验失败: multi=%s persona=%s umo=%s error=%s",
                multi_persona,
                _single_line(scheduled_persona_id, 96) or "-",
                _single_line(target_umo, 140) or "-",
                _single_line(exc, 160),
            )
            return not multi_persona

        if allowed:
            return True
        reason_code = _single_line(result.get("reason_code"), 80) if isinstance(result, dict) else ""
        action = _single_line(result.get("action"), 40) if isinstance(result, dict) else ""
        logger.warning(
            "主动投递因人格不一致被取消: multi=%s persona=%s umo=%s action=%s reason=%s",
            multi_persona,
            _single_line(scheduled_persona_id, 96) or "-",
            _single_line(target_umo, 140) or "-",
            action or "blocked",
            reason_code or "validator_rejected",
        )
        return False
