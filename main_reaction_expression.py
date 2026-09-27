# -*- coding: utf-8 -*-
"""反应表情域。

由 tools/split_main_domain.py 从 main.py 机械抽取（18 个方法 / 718 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPlugin）。
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import time
from .conversation_prompt_section import PromptRenderMode, exact_text, prompt_section, render_prompt_sections
from .helpers import _path_text, _safe_float, _single_line
from .main_shared import _OneBotReactionImage, _multi_persona_event_context
from .persona_config import runtime_persona_setting
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image, Plain, Record
from typing import Any

from .logging_util import get_module_logger

logger = get_module_logger(__name__)

class PrivateCompanionPluginReactionExpressionMixin:
    """反应表情域（从 PrivateCompanionPlugin 拆出）。"""

    @filter.event_message_type(filter.EventMessageType.ALL, priority=9500)
    @_multi_persona_event_context
    async def handle_reactive_poke(self, event: AstrMessageEvent, *args, **kwargs):
        """被动戳一戳：用户戳 Bot 时响应文字/LLM 回复，并可能反戳。"""
        if self is None or not self.enabled:
            return
        if not self._is_onebot_poke_notice_event(event):
            return
        if not runtime_persona_setting(self, "enable_reactive_poke", False):
            return
        raw = self._event_raw_payload(event)
        sender_id = str(raw.get("user_id") or "").strip()
        target_id = str(raw.get("target_id") or "").strip()
        bot_id = str(raw.get("self_id") or getattr(event, "self_id", "") or "").strip()
        if not sender_id or not target_id or target_id != bot_id:
            return
        trigger_prob = runtime_persona_setting(self, "reactive_poke_trigger_probability", 1.0)
        try:
            trigger_prob = float(trigger_prob)
        except Exception:
            trigger_prob = 1.0
        if random.random() > trigger_prob:
            logger.debug("[PrivateCompanion] 被动戳一戳未达触发概率: %.2f", trigger_prob)
            return
        event.should_call_llm(False)
        normal_prob = runtime_persona_setting(self, "reactive_poke_normal_reply_probability", 0.3)
        try:
            normal_prob = float(normal_prob)
        except Exception:
            normal_prob = 0.3
        normal_replies = runtime_persona_setting(self, "reactive_poke_normal_replies", None)
        if not isinstance(normal_replies, list) or not normal_replies:
            normal_replies = [
                "嗯？有人戳我吗？",
                "哎呀，被发现了~",
                "戳我干嘛呀~",
                "在呢在呢~",
                "唔…再戳就不理你了哦",
            ]
        if random.random() < normal_prob:
            response = random.choice(normal_replies)
            await event.send(event.plain_result(response))
            logger.debug("[PrivateCompanion] 被动戳一戳已回复预设文字: %s", _single_line(response, 60))
            return
        prompts = runtime_persona_setting(self, "reactive_poke_prompts", None)
        if not isinstance(prompts, list) or not prompts:
            prompts = [
                "有人戳了戳你，请你回一句俏皮的话。",
                "你被人戳了一下，请给出你的反应。",
            ]
        poke_prompt = random.choice(prompts)
        llm_text = await self._reactive_poke_call_llm(event, poke_prompt)
        if llm_text:
            llm_text = str(llm_text).strip()
        if llm_text:
            await event.send(event.plain_result(llm_text))
        else:
            response = random.choice(normal_replies)
            await event.send(event.plain_result(response))
        group_id = str(raw.get("group_id") or "").strip()
        await self._reactive_poke_maybe_poke_back(event, sender_id, group_id)

    async def _reactive_poke_call_llm(self, event: AstrMessageEvent, prompt: str) -> str | None:
        """调用 LLM 生成被动戳一戳回复，复用统一 LLM 通道（token 预算/超时/回退）。"""
        user_section = prompt_section(
            key="background.reactive_poke.user_prompt",
            title="戳一戳回复任务",
            source="user_config",
            content=exact_text(str(prompt or "").strip()),
        )
        system_section = prompt_section(
            key="background.reactive_poke.system",
            title="戳一戳回复边界",
            source="main",
            content=(
                "你是一位正在陪伴用户的 AI 角色。用户刚戳了戳你，请按当前人格"
                "只回复一句自然、简短、符合语气的回应。不要输出 JSON、Markdown、"
                "XML 标签、占位符或任何解释。"
            ),
        )
        try:
            return await self._llm_call(
                render_prompt_sections(
                    [user_section],
                    mode=PromptRenderMode.BODY_ONLY,
                ),
                max_tokens=120,
                task="reactive_poke_reply",
                system_prompt=render_prompt_sections(
                    [system_section],
                    mode=PromptRenderMode.BODY_ONLY,
                ),
                timeout_key="reactive_poke",
                timeout_seconds=15.0,
            )
        except Exception as e:
            logger.error("[PrivateCompanion] 被动戳一戳 LLM 调用失败: %s", _single_line(e, 160))
            return None

    async def _reactive_poke_maybe_poke_back(self, event: AstrMessageEvent, user_id: str, group_id: str) -> None:
        """根据概率决定是否反戳。"""
        back_prob = runtime_persona_setting(self, "reactive_poke_back_probability", 0.1)
        super_prob = runtime_persona_setting(self, "reactive_poke_super_poke_probability", 0.01)
        try:
            back_prob = float(back_prob)
        except Exception:
            back_prob = 0.1
        try:
            super_prob = float(super_prob)
        except Exception:
            super_prob = 0.01
        action_rand = random.random()
        if action_rand >= back_prob + super_prob:
            return
        is_super = action_rand < super_prob
        times = runtime_persona_setting(
            self,
            "reactive_poke_super_poke_times" if is_super else "reactive_poke_back_times",
            5 if is_super else 1,
        )
        try:
            times = int(times)
        except Exception:
            times = 5 if is_super else 1
        interval = runtime_persona_setting(self, "reactive_poke_interval", 1.0)
        try:
            interval = float(interval)
        except Exception:
            interval = 1.0
        back_prompts = runtime_persona_setting(self, "reactive_poke_back_prompts", None)
        if isinstance(back_prompts, list) and back_prompts:
            back_prompt = random.choice(back_prompts)
            llm_text = await self._reactive_poke_call_llm(event, back_prompt)
            if llm_text:
                llm_text = str(llm_text).strip()
            if llm_text:
                await event.send(event.plain_result(llm_text))
        client = self._resolve_aiocqhttp_client()
        if client is None:
            return
        if not user_id.isdigit():
            return
        for i in range(times):
            try:
                await self._send_single_poke(client, user_id=user_id, group_id=group_id)
                if i + 1 < times:
                    await asyncio.sleep(interval)
            except Exception as e:
                logger.error("[PrivateCompanion] 被动反戳失败: %s", _single_line(e, 160))
                break

    def _reaction_expression_delivery_mode(self) -> str:
        raw_mode = _single_line(
            runtime_persona_setting(self, 'reaction_expression_delivery_mode', "separate_after"),
            32,
        )
        normalizer = getattr(
            self,
            "_normalize_reaction_expression_delivery_mode",
            None,
        )
        if callable(normalizer):
            try:
                return normalizer(raw_mode)
            except Exception:
                pass
        mode = raw_mode.lower()
        if mode not in {"separate_after", "same_message", "separate_before"}:
            return "separate_after"
        return mode

    def _reaction_expression_image_format(self) -> str:
        image_format = _single_line(
            runtime_persona_setting(self, 'reaction_expression_image_format', "image"),
            24,
        ).lower()
        return image_format if image_format in {"image", "qq_emoji"} else "image"

    @staticmethod
    def _is_reaction_image_component(component: Any) -> bool:
        return isinstance(component, Image) or bool(
            getattr(component, "_private_companion_reaction_expression", False)
            and callable(getattr(component, "toDict", None))
        )

    def _build_reaction_image_component(
        self,
        event: AstrMessageEvent | None,
        image_path: str,
    ) -> Any:
        format_getter = getattr(self, "_reaction_expression_image_format", None)
        image_format = (
            format_getter()
            if callable(format_getter)
            else _single_line(
                runtime_persona_setting(self, 'reaction_expression_image_format', "image"),
                24,
            ).lower()
        )
        platform_getter = getattr(self, "_platform_kind_for_event", None)
        platform_kind = (
            platform_getter(event)
            if image_format == "qq_emoji" and callable(platform_getter)
            else "generic"
        )
        if image_format == "qq_emoji" and platform_kind == "onebot":
            try:
                return _OneBotReactionImage(image_path)
            except Exception as exc:
                logger.warning(
                    "QQ表情格式组件构建失败,回退普通图片: error_type=%s",
                    type(exc).__name__,
                )
        try:
            component = Image.fromFileSystem(image_path)
        except AttributeError:
            component = Image.from_file_system(image_path)
        try:
            object.__setattr__(
                component,
                "_private_companion_reaction_expression",
                True,
            )
        except Exception:
            pass
        return component

    async def _send_reaction_expression_component_separately(
        self,
        event: AstrMessageEvent,
        component: Any,
    ) -> bool:
        sender = getattr(event, "send", None)
        result_builder = getattr(event, "chain_result", None)
        if not callable(sender) or not callable(result_builder) or component is None:
            return False
        try:
            send_result = await sender(result_builder([component]))
            return send_result is not False
        except Exception as exc:
            logger.warning(
                "表情图片单独投递失败: mode=%s error_type=%s",
                self._reaction_expression_delivery_mode(),
                type(exc).__name__,
            )
            return False

    @staticmethod
    def _reaction_expression_flatten_delivery_components(
        components: Any,
    ) -> list[Any]:
        flattened: list[Any] = []

        def visit(component: Any) -> None:
            if component is None:
                return
            class_name = component.__class__.__name__.strip().lower()
            nested = getattr(component, "content", None)
            if class_name == "node" and isinstance(nested, (list, tuple)):
                for item in nested:
                    visit(item)
                return
            nodes = getattr(component, "nodes", None)
            if class_name == "nodes" and isinstance(nodes, (list, tuple)):
                for item in nodes:
                    visit(item)
                return
            flattened.append(component)

        raw_components = getattr(components, "chain", components)
        if isinstance(raw_components, (list, tuple)):
            for item in raw_components:
                visit(item)
        return flattened

    @staticmethod
    def _reaction_expression_delivery_signature(component: Any) -> tuple[str, ...] | None:
        if isinstance(component, Plain):
            text = str(getattr(component, "text", "") or "").strip()
            return ("plain", text) if text else None
        if isinstance(component, Record):
            reference = _single_line(
                getattr(component, "file", "")
                or getattr(component, "url", ""),
                1000,
            )
            return (
                "record",
                reference,
                _single_line(getattr(component, "text", ""), 1000),
            )
        if PrivateCompanionPluginReactionExpressionMixin._is_reaction_image_component(component):
            reference = _single_line(
                getattr(component, "file", "")
                or getattr(component, "url", "")
                or getattr(component, "path", ""),
                1000,
            )
            if reference and not reference.startswith(("http://", "https://")):
                reference = os.path.normcase(os.path.normpath(reference))
            return ("image", reference) if reference else None
        return None

    def _install_reaction_expression_delivery_tracker(
        self,
        event: AstrMessageEvent,
        pending: dict[str, Any],
    ) -> None:
        existing = getattr(
            event,
            "_private_companion_reaction_expression_delivery_tracker",
            None,
        )
        if isinstance(existing, dict):
            if not existing.get("restored"):
                pending["delivery_tracker"] = existing
                return
            try:
                delattr(
                    event,
                    "_private_companion_reaction_expression_delivery_tracker",
                )
            except Exception:
                pass
        original_send = getattr(event, "send", None)
        if not callable(original_send):
            return
        tracker: dict[str, Any] = {
            "original_send": original_send,
            "successful_signatures": [],
            "restored": False,
        }

        async def tracked_send(message: Any) -> Any:
            result = await original_send(message)
            if result is False:
                return result
            signatures = tracker.get("successful_signatures")
            if isinstance(signatures, list):
                for item in self._reaction_expression_flatten_delivery_components(
                    message
                ):
                    signature = self._reaction_expression_delivery_signature(item)
                    if signature is not None:
                        signatures.append(signature)
            return result

        tracker["tracked_send"] = tracked_send
        try:
            setattr(event, "send", tracked_send)
            setattr(
                event,
                "_private_companion_reaction_expression_delivery_tracker",
                tracker,
            )
            pending["delivery_tracker"] = tracker
        except Exception:
            return

    def _reaction_expression_primary_reply_confirmed(
        self,
        event: AstrMessageEvent,
        pending: dict[str, Any] | None = None,
        *,
        require_segmented_complete: bool = False,
    ) -> bool:
        tracker = (
            pending.get("delivery_tracker")
            if isinstance(pending, dict)
            else None
        )
        if not isinstance(tracker, dict):
            tracker = getattr(
                event,
                "_private_companion_reaction_expression_delivery_tracker",
                None,
            )
        if not isinstance(tracker, dict):
            return bool(getattr(event, "_has_send_oper", False))
        successful = list(tracker.get("successful_signatures") or [])
        result = event.get_result()
        chain = list(getattr(result, "chain", []) or []) if result is not None else []
        expected_sources: list[Any] = [chain]
        if require_segmented_complete:
            segmented_chunks = getattr(
                event,
                "_private_companion_reaction_expression_expected_primary_chunks",
                None,
            )
            if isinstance(segmented_chunks, list) and segmented_chunks:
                expected_sources = segmented_chunks
        elif isinstance(pending, dict):
            primary_chunk = pending.get("primary_chunk")
            if isinstance(primary_chunk, list) and primary_chunk:
                expected_sources = [primary_chunk]
        expected: list[tuple[str, ...]] = []
        for source in expected_sources:
            for item in self._reaction_expression_flatten_delivery_components(source):
                signature = self._reaction_expression_delivery_signature(item)
                if signature is None or signature[0] == "image":
                    continue
                expected.append(signature)
        if not expected:
            return False
        for signature in expected:
            try:
                successful.remove(signature)
            except ValueError:
                return False
        return True

    def _reaction_expression_image_delivery_confirmed(
        self,
        event: AstrMessageEvent,
        pending: dict[str, Any],
    ) -> bool:
        tracker = pending.get("delivery_tracker")
        component = pending.get("component")
        signature = self._reaction_expression_delivery_signature(component)
        if not isinstance(tracker, dict) or signature is None:
            return False
        return signature in list(tracker.get("successful_signatures") or [])

    @staticmethod
    def _reaction_expression_attachment_present(
        chain: list[Any],
        component: Any,
        pending: dict[str, Any],
    ) -> bool:
        if component is None:
            return False
        flattened = PrivateCompanionPluginReactionExpressionMixin._reaction_expression_flatten_delivery_components(
            chain
        )
        if any(item is component for item in flattened):
            return True
        expected_path = os.path.normcase(
            os.path.normpath(_path_text(pending.get("image_path"), 1000))
        )
        if not expected_path:
            return False
        for item in flattened:
            if not PrivateCompanionPluginReactionExpressionMixin._is_reaction_image_component(item):
                continue
            for attr in ("file", "path", "url"):
                raw_value = _path_text(getattr(item, attr, ""), 1000)
                if not raw_value or raw_value.startswith(("http://", "https://")):
                    continue
                if os.path.normcase(os.path.normpath(raw_value)) == expected_path:
                    return True
        return False

    @filter.after_message_sent(priority=9000)
    @_multi_persona_event_context
    async def settle_reaction_expression_attachment_after_send(
        self, event: AstrMessageEvent, *args, **kwargs
    ):
        """Deliver or settle a reaction only after the primary reply is confirmed."""
        if self is None or not self.enabled:
            return
        pending = getattr(
            event,
            "_private_companion_reaction_expression_pending_attachment",
            None,
        )
        if not isinstance(pending, dict) or pending.get("settled"):
            return
        delivery_mode = _single_line(
            pending.get("delivery_mode"),
            32,
        ).lower() or self._reaction_expression_delivery_mode()
        if delivery_mode not in {"separate_after", "same_message", "separate_before"}:
            delivery_mode = "separate_after"
        if delivery_mode == "separate_before":
            await self._settle_reaction_expression_attachment_data(
                pending,
                sent=False,
                reason="delivery_not_started",
            )
            return

        component = pending.get("component")
        result = event.get_result()
        chain = list(getattr(result, "chain", []) or []) if result is not None else []
        primary_sent = self._reaction_expression_primary_reply_confirmed(
            event,
            pending,
            require_segmented_complete=True,
        )
        if delivery_mode == "separate_after":
            if pending.get("delivery_started"):
                return
            pending["delivery_started"] = True
            if not primary_sent:
                await self._settle_reaction_expression_attachment_data(
                    pending,
                    sent=False,
                    reason="primary_not_delivered",
                )
                return
            sent = await self._send_reaction_expression_component_separately(
                event,
                component,
            )
            await self._settle_reaction_expression_attachment_data(
                pending,
                sent=sent,
                reason="delivered" if sent else "delivery_failed",
            )
            return

        attachment_present = self._reaction_expression_attachment_present(
            chain,
            component,
            pending,
        )
        tracker = pending.get("delivery_tracker")
        if isinstance(tracker, dict):
            sent = self._reaction_expression_image_delivery_confirmed(
                event,
                pending,
            )
        else:
            sent = primary_sent and attachment_present
        reason = (
            "delivered"
            if sent
            else "delivery_failed"
            if primary_sent and attachment_present
            else "attachment_removed"
            if not attachment_present
            else "platform_not_sent"
        )
        await self._settle_reaction_expression_attachment_data(
            pending,
            sent=sent,
            reason=reason,
        )

    @filter.after_message_sent(priority=9500)
    @_multi_persona_event_context
    async def release_reaction_expression_segmented_remainder_after_send(
        self, event: AstrMessageEvent, *args, **kwargs
    ):
        """Finish all text bubbles before a separate-after reaction is released."""
        if self is None or not self.enabled:
            return
        pending = getattr(
            event,
            "_private_companion_reaction_expression_segmented_remainder",
            None,
        )
        if not isinstance(pending, dict) or pending.get("started"):
            return
        chunks = pending.get("chunks")
        if not isinstance(chunks, list) or not chunks:
            return
        if not self._reaction_expression_primary_reply_confirmed(event, pending):
            return
        pending["started"] = True
        try:
            await self._send_segmented_llm_chain_remainder(
                event,
                chunks,
                previous_segment=_single_line(pending.get("previous_segment"), 500),
                source="reaction_expression",
                started_at=_safe_float(pending.get("started_at"), time.time(), 0.0),
            )
            pending["completed"] = self._reaction_expression_primary_reply_confirmed(
                event,
                require_segmented_complete=True,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            pending["completed"] = False
            logger.warning(
                "表情正文分段补发失败: session=%s error=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120)
                or "unknown",
                _single_line(exc, 160),
            )

    @filter.after_message_sent(priority=6000)
    @_multi_persona_event_context
    async def cleanup_reaction_expression_delivery_tracker_after_send(
        self, event: AstrMessageEvent, *args, **kwargs
    ):
        """Restore the adapter send method after all ordered follow-ups are released."""
        if self is None:
            return
        self._restore_reaction_expression_delivery_tracker(event)
        for attr_name in (
            "_private_companion_reaction_expression_segmented_remainder",
            "_private_companion_reaction_expression_expected_primary_chunks",
        ):
            try:
                delattr(event, attr_name)
            except Exception:
                pass

    @filter.llm_tool(name="pc_find_reaction_image")
    @_multi_persona_event_context
    async def pc_find_reaction_image(
        self,
        event: AstrMessageEvent,
        query: str = "",
        search_context: str = "",
        meme_only: bool = True,
        send: bool = True,
        caption: str = "",
        purpose: str = "",
        emotion: str = "",
        intensity: int = 0,
        spontaneous: bool = False,
        candidate_queries: str = "",
        **kwargs: Any,
    ) -> str:
        """从 Private Companion 自有表情包素材库检索并发送一张已有图片。

        Args:
            query(string): 表情或图片需求，例如“震惊又无语的反应图”。
            search_context(string): 可选，当前对话语境或希望表达的情绪。
            meme_only(boolean): 是否只检索标记为表情包的图片，默认 true。
            send(boolean): 是否直接发送到当前会话，默认 true。
            caption(string): send=true 时必填；与图片一起发送的完整可见正文，图片不能替代正文。
            purpose(string): 自发表情实验的沟通用途，例如安慰、轻吐槽或分享开心；显式找图时可留空。
            emotion(string): 自发表情实验希望传达的情绪。
            intensity(int): 自发表情实验的表达强度，0-5。
            spontaneous(boolean): 是否为模型在普通闲聊中自主选择的表情表达，默认 false；用户明确要求找图时不要开启。
            candidate_queries(string): 自发表情实验的少量候选检索说法，可用分号分隔或传 JSON 字符串数组。
        """
        if self is None or self._proactive_only_blocks_passive_event(event, "pc_tools"):
            if self is not None:
                self._log_reaction_expression_event(
                    event,
                    stage="decision",
                    decision="skip",
                    reason="proactive_only",
                    scope=self._reaction_expression_scope(event),
                    status="disabled",
                    found=False,
                    sent=False,
                )
            return json.dumps(
                {
                    "status": "disabled",
                    "success": False,
                    "found": False,
                    "sent": False,
                    "message": "主动消息专用模式下不可使用图库表情工具。",
                },
                ensure_ascii=False,
            )
        # AstrBot may inject a host-owned ``context`` keyword. Keep the public
        # schema on ``search_context`` so host context objects never become
        # model-controlled search text; only a direct legacy string can fill an
        # absent search_context value.
        legacy_context = kwargs.get("context")
        if not search_context and isinstance(legacy_context, str):
            search_context = legacy_context
        inbound_text = str(getattr(event, "message_str", "") or "")
        if (
            self._reaction_expression_opt_out_requested(inbound_text)
            and not self._reaction_expression_explicit_request_matches(inbound_text)
        ):
            return json.dumps(
                {
                    "status": "skipped",
                    "success": True,
                    "found": False,
                    "sent": False,
                    "decision": "skip",
                    "skip_reason": "explicit_opt_out",
                    "message": "用户本轮明确要求不发表情包",
                    "must_not_claim_sent": True,
                    "final_response_instruction": "尊重用户边界，只继续自然文字回复。",
                },
                ensure_ascii=False,
            )
        reaction_authorization = self._reaction_expression_authorization(event)
        if reaction_authorization and not reaction_authorization.get("authorized"):
            return json.dumps(
                self._reaction_expression_skip_result(
                    _single_line(reaction_authorization.get("reason"), 80)
                    or "not_authorized",
                    event=event,
                ),
                ensure_ascii=False,
            )
        if reaction_authorization and reaction_authorization.get("consumed"):
            return json.dumps(
                self._reaction_expression_skip_result(
                    "authorization_consumed",
                    event=event,
                ),
                ensure_ascii=False,
            )
        send_requested = self._reaction_expression_bool_arg(send, True)
        visible_caption = self._sanitize_photo_tool_caption(caption, limit=500)
        if send_requested and not visible_caption:
            return json.dumps(
                self._reaction_expression_skip_result(
                    "missing_visible_caption",
                    event=event,
                    message="发送表情包前需要同时提供一条完整的可见正文",
                ),
                ensure_ascii=False,
            )
        if send_requested:
            caption = visible_caption
        spontaneous_call = self._reaction_expression_bool_arg(
            spontaneous, False
        ) or bool(reaction_authorization.get("authorized"))
        if spontaneous_call:
            return await self._pc_reaction_expression_impl(
                event,
                query=query,
                context=search_context,
                meme_only=meme_only,
                send=send,
                caption=visible_caption,
                purpose=purpose,
                emotion=emotion,
                intensity=intensity,
                candidate_queries=candidate_queries,
            )
        return await self._pc_find_reaction_image_impl(
            event,
            query=query,
            search_context=search_context,
            meme_only=meme_only,
            send=send,
            caption=caption,
        )
