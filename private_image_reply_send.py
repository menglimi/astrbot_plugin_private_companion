# -*- coding: utf-8 -*-
"""PrivateImageReplySendMixin。

由 tools/split_mixin_domain.py 从 private_image.py 机械抽取（26 个方法 + 0 个模块级名字 + 0 个类级赋值 / 756 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateImageMixin）。
"""
from __future__ import annotations

import asyncio
import re
from .conversation_prompt_section import PromptRenderMode, PromptSection, prompt_section, render_prompt_sections
from .helpers import _safe_float, _single_line, _strip_internal_message_blocks
from .persona_config import runtime_persona_setting
from .private_image_shared import _private_image_host, logger
from .segmented_message import (
    component_kind,
    component_order_from_owner,
    component_strategies_from_owner,
    plan_component_chunks,
)
from astrbot.api.event import AstrMessageEvent
from astrbot.api.message_components import Plain
from typing import Any



class PrivateImageReplySendMixin:
    """PrivateImageReplySendMixin（从 PrivateImageMixin 拆出）。"""


    def _normalize_private_image_reply_text(self, text: str) -> str:
        cleaned = str(text or "").strip()
        if not cleaned:
            return ""
        cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
        if "\n" not in cleaned and re.search(r"[\u4e00-\u9fff][ \t]+[\u4e00-\u9fff]", cleaned):
            # Some providers use spaces as short-message pauses. Preserve that intent for manual sends.
            cleaned = re.sub(r"(?<=[\u4e00-\u9fff…！？?！~～])\s+(?=[\u4e00-\u9fff])", "\n", cleaned)
        return cleaned.strip()

    def _restore_private_image_framework_tts_reply(
        self,
        reply: str,
        framework_event: AstrMessageEvent,
    ) -> str:
        source = str(reply or "").strip()
        if "[[PCTTS:" not in source:
            return source
        restorer = getattr(self, "_restore_protected_tts_blocks", None)
        if not callable(restorer):
            return source
        try:
            return str(restorer(source, framework_event) or source).strip()
        except Exception as exc:
            logger.warning(
                "私聊单图主链 TTS 占位符恢复失败: %s",
                _single_line(exc, 120),
            )
            return source

    def _private_image_reply_ignores_vision_summary(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", str(text or ""))
        if not compact:
            return False
        markers = (
            "没看到图", "没看到图片", "没看见图", "没看见图片",
            "看不到图", "看不到图片", "看不见图", "看不见图片",
            "无法看到图", "无法看到图片", "不能看到图", "不能看到图片",
            "看不了图", "看不了图片", "图片没显示", "图没显示",
            "再发一次", "重新发一次", "重发一次",
        )
        return any(marker in compact for marker in markers)

    def _private_image_reply_drifts_to_stale_context(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", str(text or ""))
        if not compact:
            return False
        stale_markers = (
            "下午我会", "下午陪你", "五点放学", "放学就行", "放学之后",
            "到时候叫我", "到时候喊我", "ちゃんと付き合う", "午後",
            "路上拍的吗", "路上拍的", "天色不错", "天色还不错", "天色好像还不错",
            "你走到哪里", "你走到哪儿", "走到哪里啦", "走到哪儿啦",
            "香草冰激凌", "冰激凌买到了没", "冰淇淋买到了没",
        )
        image_markers = (
            "图", "图片", "画面", "漫画", "这个", "这张", "大腿", "夹头",
            "好笑", "离谱", "表情", "梗", "幽灵", "月亮",
        )
        return any(marker in compact for marker in stale_markers) and any(marker in compact for marker in image_markers)

    def _private_image_fallback_reply_prompt_section(
        self,
        *,
        vision_text: str,
        reply_objective: str = "",
    ) -> PromptSection:
        if vision_text:
            content = (
                "用户只发了一张图片。请用当前私聊人格短句回应，不要提模型、插件、视觉转述或路径。\n"
                "除非用户明确问图片内容，否则不要把摘要逐项复述成看图报告；像正常聊天一样评价、接梗、回应情绪或追问重点，最多提一个显眼细节。\n"
                "如果最近对话上下文里用户明确要求这张/下一张图只回复某句话或不要回复其他内容,必须优先照做。\n"
                f"{self._private_image_identity_disambiguation_instruction()}\n"
                f"{reply_objective}\n"
                f"图片内容摘要：{vision_text}"
            )
        else:
            content = (
                "用户只发了一张图片。当前没有可靠视觉摘要,你也没有直接看到图片内容。\n"
                "请按当前私聊人格只回复一句自然短句；不要猜测画面、人物、表情、文字、场景、天气或截图内容。\n"
                "如果最近对话上下文里用户明确要求这张/下一张图只回复某句话或不要回复其他内容,必须优先照做。\n"
                "不要续写聊天历史里的旧约定、旧主动消息、旧 TTS 文本或旧图片摘要。\n"
                "没有明确回复限制时,只自然说明这边没识出来/没看清,请用户补一句想让你看哪里；不要复读固定模板。"
            )
        return prompt_section(
            key="background.private_image_only_fallback",
            title="私聊单图兜底回复",
            source="private_image",
            content=content,
        )

    def _private_image_strict_retry_prompt_section(
        self,
        *,
        vision_text: str,
        reply_objective: str = "",
    ) -> PromptSection:
        content = (
            "用户只发了一张图片，前一次回复为空或清洗后没有可发送内容。\n"
            "现在必须只输出一条可以直接发给用户的纯文本短回复，不能留空。\n"
            "不要输出 TTS/XML 标签、占位符、JSON、Markdown 代码块、工具调用、内部错误、处理过程或解释。\n"
            "保持当前私聊人格和关系语气；不要复述旧聊天、旧主动消息或旧图片摘要。\n"
            "如果最近上下文明确规定这张/下一张图片只能回复某句话，优先严格照做。\n"
        )
        if vision_text:
            content += (
                "除非用户明确询问图片内容，否则不要逐项汇报画面；自然评价、接梗、回应情绪或追问一个重点。\n"
                f"{self._private_image_identity_disambiguation_instruction()}\n"
                f"{reply_objective}\n"
                f"图片内容摘要：{vision_text}"
            )
        else:
            content += (
                "当前没有可靠视觉摘要，不要猜测画面、人物、文字、天气或场景。"
                "自然说明这次没看清，并请用户补一句想让你看哪里。"
            )
        return prompt_section(
            key="background.private_image_only_strict_retry",
            title="私聊单图强约束重试",
            source="private_image",
            content=content,
        )

    async def _generate_private_image_fallback_reply(
        self,
        *,
        vision_text: str,
        reply_objective: str = "",
        system_prompt: str = "",
        user_id: str = "",
    ) -> tuple[str, str]:
        if vision_text:
            max_tokens = 160
            max_chars = 500
            source = "fallback_llm"
        else:
            max_tokens = 120
            max_chars = 300
            source = "fallback_llm_no_vision"
        prompt = render_prompt_sections(
            [
                self._private_image_fallback_reply_prompt_section(
                    vision_text=vision_text,
                    reply_objective=reply_objective,
                )
            ],
            mode=PromptRenderMode.BODY_ONLY,
        )
        raw_reply = await self._llm_call(
            prompt,
            max_tokens=max_tokens,
            task="private_image_only_fallback",
            system_prompt=str(system_prompt or "").strip() or None,
        )
        reply = _single_line(
            _strip_internal_message_blocks(
                raw_reply or "",
                enabled=bool(runtime_persona_setting(self, "enable_framework_error_leak_guard", True)),
                tts_enabled=bool(runtime_persona_setting(self, "enable_tts_enhancement", False)),
            ),
            max_chars,
        )
        if reply and self._private_image_reply_is_internal_error(reply):
            logger.warning(
                "私聊单图兜底 LLM 返回内部错误文本,已丢弃: user=%s source=%s preview=%s",
                user_id,
                source,
                _single_line(reply, 180),
            )
            reply = ""
        return reply, source

    async def _generate_private_image_strict_retry_reply(
        self,
        *,
        vision_text: str,
        reply_objective: str = "",
        system_prompt: str = "",
        user_id: str = "",
    ) -> tuple[str, str]:
        source = "strict_retry_llm" if vision_text else "strict_retry_llm_no_vision"
        prompt = render_prompt_sections(
            [
                self._private_image_strict_retry_prompt_section(
                    vision_text=vision_text,
                    reply_objective=reply_objective,
                )
            ],
            mode=PromptRenderMode.BODY_ONLY,
        )
        try:
            raw_reply = await self._llm_call(
                prompt,
                max_tokens=120,
                task="private_image_only_strict_retry",
                system_prompt=str(system_prompt or "").strip() or None,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "私聊单图强约束重试失败: user=%s error=%s",
                user_id,
                _single_line(exc, 160),
            )
            return "", source
        reply = _single_line(
            _strip_internal_message_blocks(
                raw_reply or "",
                enabled=bool(runtime_persona_setting(self, "enable_framework_error_leak_guard", True)),
                tts_enabled=bool(runtime_persona_setting(self, "enable_tts_enhancement", False)),
            ),
            300,
        )
        if reply and self._private_image_reply_is_internal_error(reply):
            logger.warning(
                "私聊单图强约束重试返回内部错误文本,已丢弃: user=%s preview=%s",
                user_id,
                _single_line(reply, 180),
            )
            reply = ""
        return reply, source

    @staticmethod
    def _private_image_neutral_visible_reply() -> str:
        return "这张图我收到了，但刚才没能稳稳接住。你想让我重点看哪里？"

    @staticmethod
    def _private_image_framework_response_text(resp: Any) -> str:
        if resp is None:
            return ""
        completion = str(getattr(resp, "completion_text", "") or "").strip()
        if completion:
            return completion
        result_chain = getattr(resp, "result_chain", None)
        chain = getattr(result_chain, "chain", None)
        if chain is None and isinstance(result_chain, list):
            chain = result_chain
        if not isinstance(chain, list):
            return ""
        parts: list[str] = []
        for item in chain:
            if isinstance(item, dict):
                component_type = str(item.get("type") or item.get("component_type") or "").strip().lower()
                if component_type and component_type not in {"plain", "text"}:
                    continue
                item_text = str(item.get("text") or item.get("content") or "").strip()
            else:
                component_type = item.__class__.__name__.strip().lower()
                if component_type not in {"plain", "text"} and not hasattr(item, "text"):
                    continue
                item_text = str(getattr(item, "text", "") or "").strip()
            if item_text:
                parts.append(item_text)
        return "\n".join(parts).strip()

    def _record_private_image_llm_usage_safely(self, **kwargs: Any) -> None:
        try:
            self._record_llm_usage(**kwargs)
        except Exception as exc:
            logger.warning(
                "私聊单图用量统计失败,不影响回复发送: %s",
                _single_line(exc, 160),
            )

    def _record_user_recent_group_message_from_observation(
        self,
        *,
        group_id: str,
        sender_id: str,
        sender_name: str,
        text: str,
        scene: dict[str, Any] | None = None,
        message_id: str = "",
        ts: float | None = None,
    ) -> None:
        user_id = str(sender_id or "").strip()
        if not user_id:
            return
        users = self.data.get("users")
        configured_ids = set(self._configured_target_ids()) if callable(getattr(self, "_configured_target_ids", None)) else set()
        if not isinstance(users, dict):
            return
        if user_id not in users and user_id not in configured_ids:
            return
        user = self._get_user(user_id)
        now = _private_image_host._now_ts() if ts is None else float(ts or 0)
        recent = user.setdefault("recent_group_messages", [])
        if not isinstance(recent, list):
            recent = []
            user["recent_group_messages"] = recent
        recent.append(
            {
                "ts": now,
                "group_id": _single_line(group_id, 40),
                "sender_name": _single_line(sender_name, 40),
                "text": _single_line(text, 180),
                "message_id": _single_line(message_id, 120),
                "talking_to": _single_line((scene or {}).get("talking_to"), 40) if isinstance(scene, dict) else "",
                "scene_trigger": _single_line((scene or {}).get("trigger"), 40) if isinstance(scene, dict) else "",
            }
        )
        cutoff = now - 2 * 3600
        kept = [
            item for item in recent
            if isinstance(item, dict) and _safe_float(item.get("ts"), 0) >= cutoff
        ]
        user["recent_group_messages"] = kept[-8:]

    def _format_recent_group_messages_for_private_image_prompt_body(self, user_id: str) -> str:
        if not user_id:
            return ""
        try:
            user = self._get_user(user_id)
        except Exception:
            return ""
        recent = user.get("recent_group_messages")
        if not isinstance(recent, list):
            return ""
        now = _private_image_host._now_ts()
        items = [
            item for item in recent
            if isinstance(item, dict) and 0 <= now - _safe_float(item.get("ts"), 0) <= 20 * 60
        ][-4:]
        if not items:
            return ""
        lines: list[str] = []
        for item in items:
            elapsed = self._format_elapsed(max(0, now - _safe_float(item.get("ts"), 0)))
            group_id = _single_line(item.get("group_id"), 40)
            text = _single_line(item.get("text"), 160)
            if text:
                lines.append(f"- {elapsed}前｜群 {group_id}｜{text}")
        if not lines:
            return ""
        lines.append("使用方式：这比私聊压缩历史更新，只作为当前用户近况和语气背景；当前回复仍然优先回应这张图片。")
        return "\n".join(lines)

    def _format_recent_group_messages_for_private_image_prompt(self, user_id: str) -> str:
        return render_prompt_sections(
            [self._format_recent_group_messages_for_private_image_prompt_section(user_id)],
            mode=PromptRenderMode.LABELED_BLOCK,
        )

    def _format_recent_group_messages_for_private_image_prompt_section(
        self,
        user_id: str,
    ) -> PromptSection:
        return prompt_section(
            key="private_image.recent_group_context",
            title="用户刚刚在群里的近况",
            source="private_image",
            content=self._format_recent_group_messages_for_private_image_prompt_body(user_id),
        )

    def _trim_private_image_stale_context_tail(self, text: str) -> str:
        cleaned = str(text or "").strip()
        if not cleaned:
            return ""
        stale_patterns = (
            r"\s*<tts\b[^>]*>[^<]*(?:午後|付き合う)[^<]*</tts>\s*[^。！？!?]*?(?:下午|五点|放学|陪你)[^。！？!?\n]*[。！？!?]?",
            r"\s*(?:另外|还有|それと|顺便)[，,、\s]*[^。！？!?\n]*(?:下午|五点|放学|到时候|陪你)[^。！？!?\n]*[。！？!?]?",
            r"\s*[^。！？!?\n]*(?:下午我会|下午陪你|五点放学|放学就行|到时候叫我|到时候喊我)[^。！？!?\n]*[。！？!?]?",
        )
        trimmed = cleaned
        for pattern in stale_patterns:
            trimmed = re.sub(pattern, "", trimmed, flags=re.IGNORECASE).strip()
        trimmed = re.sub(r"\n{3,}", "\n\n", trimmed).strip()
        return trimmed or cleaned

    def _private_image_reply_misses_content_question(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", str(text or ""))
        if not compact:
            return False
        if self._private_image_reply_ignores_vision_summary(text):
            return True
        source_only_markers = (
            "从哪搞的", "从哪弄的", "哪搞的", "哪弄的", "哪里搞的", "哪里弄的",
            "哪来的", "哪里来的", "出处", "来源", "你怎么突然发这个", "怎么突然发这个",
        )
        content_markers = (
            "图里", "图片里", "画面", "可见", "内容", "漫画", "截图", "照片", "文字",
        )
        return any(marker in compact for marker in source_only_markers) and not any(marker in compact for marker in content_markers)

    def _private_image_content_answer_from_vision(self, vision_text: str, *, user_text: str = "") -> str:
        visible = self._private_image_visible_line(vision_text)
        image_type = self._private_image_type_line(vision_text)
        intent = self._private_image_intent_line(vision_text)
        visible_value = re.sub(r"^可见内容[：:]\s*", "", _single_line(visible, 180)).strip()
        type_value = re.sub(r"^图片类型[：:]\s*", "", _single_line(image_type, 80)).strip()
        intent_value = re.sub(r"^图像表达意图[：:]\s*", "", _single_line(intent, 140)).strip()
        parts: list[str] = []
        if type_value and visible_value:
            parts.append(f"图里大概是{type_value}：{visible_value}")
        elif visible_value:
            parts.append(f"图里大概是：{visible_value}")
        elif type_value:
            parts.append(f"图里像是{type_value}。")
        if intent_value:
            parts.append(f"它主要是在表达{intent_value}")
        answer = "；".join(parts).strip("；")
        if not answer:
            return ""
        if self._private_image_user_asks_content(user_text):
            answer += "。"
        return answer

    async def _send_private_image_reply_text(self, event: AstrMessageEvent, reply: str) -> str:
        text = self._normalize_private_image_reply_text(reply)
        if not text:
            return ""
        chain = await self._private_image_reply_chain(text, event)
        if not chain:
            return ""
        scope_getter = getattr(self, "_segmented_setting", None)
        scope_value = (
            scope_getter("scope", event=event, default="proactive_only")
            if callable(scope_getter)
            else self._private_image_setting("segmented_proactive_scope", "proactive_only")
        )
        scope_checker = getattr(self, "_segmented_scope_allows_event", None)
        scope_allowed = bool(scope_checker(event)) if callable(scope_checker) else True
        should_segment = bool(self._private_image_setting("enable_segmented_proactive_reply", False)) and (
            str(scope_value or "") == "all_llm"
        ) and scope_allowed
        try:
            outbound_chains = self._private_image_split_reply_chain(
                chain,
                should_segment=should_segment,
                event=event,
            )
        except TypeError:
            outbound_chains = self._private_image_split_reply_chain(
                chain,
                should_segment=should_segment,
            )
        if not outbound_chains:
            return ""
        if len(outbound_chains) <= 1:
            await self._send_private_image_reply_chain(event, outbound_chains[0])
            return self._private_image_chain_text(outbound_chains[0]) or self._private_image_context_assistant_message(text)
        logger.info("私聊单图回复按手动链路分段发送: segments=%s", len(outbound_chains))
        remainder_started_at = _private_image_host._now_ts()
        await self._send_private_image_reply_chain(event, outbound_chains[0])
        first_text = self._private_image_chain_text(outbound_chains[0])
        remainder = self._send_private_image_reply_remainder_chains(
            event,
            outbound_chains[1:],
            previous_text=first_text,
            started_at=remainder_started_at,
        )
        task_creator = getattr(self, "_create_lifecycle_background_task", None)
        if callable(task_creator):
            task = task_creator(remainder, label="private_image_reply_remainder")
            if task is None:
                close = getattr(remainder, "close", None)
                if callable(close):
                    close()
        else:
            task = asyncio.create_task(remainder, name="private-companion-private-image-remainder")
            tasks = getattr(self, "_private_image_background_tasks", None)
            if not isinstance(tasks, set):
                tasks = set()
                self._private_image_background_tasks = tasks
            tasks.add(task)

            def consume(done_task: asyncio.Task) -> None:
                try:
                    done_task.result()
                except asyncio.CancelledError:
                    pass
                except Exception as exc:
                    logger.warning(
                        "private image remainder task failed: %s",
                        _single_line(exc, 160),
                    )
                finally:
                    tasks.discard(done_task)

            task.add_done_callback(consume)
        return first_text or self._private_image_context_assistant_message(text)

    async def _private_image_reply_chain(self, text: str, event: AstrMessageEvent) -> list[Any]:
        normalized = str(text or "").strip()
        restorer = getattr(self, "_restore_protected_tts_blocks", None)
        if callable(restorer) and "[[PCTTS:" in normalized:
            try:
                normalized = str(restorer(normalized, event) or normalized).strip()
            except Exception:
                pass
        placeholder_cleaner = getattr(self, "_sanitize_orphan_tts_placeholders", None)
        if callable(placeholder_cleaner) and "[[PCTTS:" in normalized:
            cleaned = placeholder_cleaner(normalized)
            if cleaned != normalized:
                logger.warning(
                    "私聊单图发送前清理孤儿 TTS 占位符: before=%s after=%s",
                    _single_line(normalized, 120),
                    _single_line(cleaned, 120),
                )
                normalized = cleaned
        normalizer = getattr(self, "_normalize_tts_tags", None)
        if callable(normalizer) and re.search(r"</?t{2,}s\b", normalized, flags=re.IGNORECASE):
            try:
                normalized = str(normalizer(normalized) or normalized).strip()
            except Exception:
                pass
        has_tts_block = bool(re.search(r"<tts\b[^>]*>.*?</tts>", normalized, flags=re.IGNORECASE | re.DOTALL))
        if has_tts_block and bool(self._private_image_setting("enable_tts_enhancement", False)):
            processor = getattr(self, "_process_tts_tags", None)
            if callable(processor):
                fallback_plain = re.sub(r"</?t{2,}s\b[^>]*>", "", normalized, flags=re.IGNORECASE).strip()
                try:
                    chain = await processor(normalized, event, fallback_plain=fallback_plain)
                except Exception as exc:
                    logger.warning("私聊单图 TTS 组件生成失败,回退文本发送: %s", _single_line(exc, 120))
                    chain = []
                cleaned_chain = self._private_image_clean_reply_chain(chain)
                if cleaned_chain:
                    return cleaned_chain
                if fallback_plain:
                    return [Plain(fallback_plain)]
        visible_text = re.sub(r"</?t{2,}s\b[^>]*>", "", normalized, flags=re.IGNORECASE).strip() if has_tts_block else normalized
        return [Plain(visible_text)] if visible_text else []

    @staticmethod
    def _private_image_chain_text(chain: list[Any]) -> str:
        return _single_line(" ".join(str(getattr(comp, "text", "") or "") for comp in chain if isinstance(comp, Plain)), 260)

    @staticmethod
    def _private_image_clean_reply_chain(chain: list[Any]) -> list[Any]:
        cleaned: list[Any] = []
        for comp in chain or []:
            if isinstance(comp, Plain):
                text = str(getattr(comp, "text", "") or "").strip()
                text = re.sub(r"\[\[PCTTS:[^\]]*\]\]", "", text).strip()
                if text:
                    cleaned.append(Plain(text))
                continue
            cleaned.append(comp)
        return cleaned

    def _private_image_split_reply_chain(
        self,
        chain: list[Any],
        *,
        should_segment: bool,
        event: AstrMessageEvent | None = None,
    ) -> list[list[Any]]:
        llm_splitter = getattr(self, "_split_llm_controlled_text_for_event", None)
        if should_segment and callable(llm_splitter) and bool(
            runtime_persona_setting(self, "enable_llm_controlled_segmenting", False)
        ):
            split_text = lambda text: llm_splitter(event, text)
        elif should_segment:
            split_text = lambda text: self._split_proactive_text(text, event=event)
        else:
            split_text = lambda text: [part.strip() for part in str(text or "").splitlines() if part.strip()]
        chunks, _changed, _split_changed, _full_text = plan_component_chunks(
            chain,
            plain_type=Plain,
            split_text=split_text,
            strategies=component_strategies_from_owner(self),
            component_order=component_order_from_owner(self),
            classify=component_kind,
        )
        return chunks

    async def _send_private_image_reply_chain(self, event: AstrMessageEvent, chain: list[Any]) -> None:
        if not chain:
            return
        try:
            result = event.chain_result(chain)
        except Exception:
            result = self._build_result_from_chain(chain)
        try:
            await event.send(result)
        except Exception as exc:
            logger.warning(
                "图片回复发送返回异常，为避免平台已接收后重复发送，本轮不再重试: session=%s error=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                _single_line(exc, 180),
            )
            raise

    async def _send_private_image_reply_remainder_chains(
        self,
        event: AstrMessageEvent,
        chains: list[list[Any]],
        *,
        previous_text: str = "",
        started_at: float | None = None,
    ) -> list[str]:
        prev = previous_text
        total = len([item for item in chains if item])
        sent_index = 0
        sent_texts: list[str] = []
        scope_getter = getattr(self, "_event_scope_key", None)
        scope = ""
        if callable(scope_getter):
            try:
                scope = _single_line(scope_getter(event), 160)
            except Exception:
                scope = ""
        if not scope:
            scope = _single_line(getattr(event, "unified_msg_origin", ""), 160) or "unknown"
        lock_getter = getattr(self, "_segmented_remainder_lock", None)
        lock = lock_getter(scope) if callable(lock_getter) else asyncio.Lock()
        async with lock:
            for chain in chains:
                if not chain:
                    continue
                sent_index += 1
                try:
                    wait_for = prev or self._private_image_chain_text(chain)
                    delay = await self._calc_segmented_proactive_interval(wait_for, event=event)
                    if delay > 0:
                        await asyncio.sleep(delay)
                    await self._send_private_image_reply_chain(event, chain)
                    sent_text = self._private_image_chain_text(chain)
                    if sent_text:
                        sent_texts.append(sent_text)
                    logger.info(
                        "私聊单图剩余片段已发送: index=%s/%s preview=%s",
                        sent_index,
                        total,
                        self._private_image_chain_text(chain) or chain[0].__class__.__name__,
                    )
                    prev = self._private_image_chain_text(chain) or prev
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning(
                        "私聊单图剩余片段发送失败: error=%s",
                        _single_line(exc, 160),
                        exc_info=True,
                    )
                    return sent_texts
        return sent_texts

    async def prepare_keyword_model_router_image_caption(
        self, event: AstrMessageEvent
    ) -> str:
        """在主 Provider 创建前提供本轮图片转述，供关键词路由插件匹配。"""
        existing_fields = (
            "private_companion_image_caption_route_text",
            "private_companion_delayed_image_vision_text",
            "private_companion_reply_image_vision_text",
        )
        for field_name in existing_fields:
            existing = _single_line(getattr(event, field_name, ""), 8000)
            if existing:
                setattr(event, "private_companion_image_caption_route_text", existing)
                return existing

        if not bool(getattr(self, "enabled", False)):
            return ""
        try:
            if not bool(getattr(event, "is_private_chat", lambda: False)()):
                return ""
            resolver = getattr(self, "_private_user_id_for_event", None)
            user_id = (
                resolver(event)
                if callable(resolver)
                else self._canonical_private_user_id(str(event.get_sender_id()))
            )
        except Exception:
            return ""
        raw_users = self.data.get("users", {}) if isinstance(getattr(self, "data", None), dict) else {}
        user = raw_users.get(user_id) if user_id and isinstance(raw_users, dict) else None
        profile_checker = getattr(self, "_private_passive_profile_available", None)
        if callable(profile_checker):
            profile_available = bool(profile_checker(user_id, user)) if isinstance(user, dict) else False
        else:
            target_checker = getattr(self, "_is_target_private_user", None)
            profile_available = bool(
                isinstance(user, dict) and callable(target_checker) and target_checker(user_id, user)
            )
        if not profile_available:
            return ""
        feature_checker = getattr(self, "_feature_enabled_or_temp_unlocked", None)
        if callable(feature_checker) and not feature_checker(
            "enable_private_image_self_recognition"
        ):
            return ""

        key = self._semantic_buffer_key(f"private:{user_id}", user_id)
        buffers = getattr(self, "_semantic_message_buffers", None)
        buffer = buffers.get(key) if isinstance(buffers, dict) else None
        buffered_images: list[str] = []
        vision_text = ""
        if isinstance(buffer, dict):
            max_age = max(30.0, self._message_debounce_seconds("image") + 30.0)
            updated_ts = _safe_float(
                buffer.get("updated_ts"), buffer.get("first_ts"), 0
            )
            if _private_image_host._now_ts() - updated_ts <= max_age:
                buffered_images = [
                    str(item)
                    for item in (buffer.get("images") or [])[:5]
                    if str(item or "").strip()
                ]
                image_limit = self._private_image_vision_text_limit(
                    len(buffered_images)
                )
                vision_text = _single_line(buffer.get("vision_text"), image_limit)
                vision_task = buffer.get("vision_task")
                if not vision_text and isinstance(vision_task, asyncio.Task):
                    try:
                        if vision_task.done():
                            vision_text = self._completed_private_image_vision_task_text(
                                vision_task
                            )
                        else:
                            timeout = self._private_image_vision_wait_budget_seconds()
                            if timeout > 0:
                                vision_text = _single_line(
                                    await asyncio.wait_for(
                                        asyncio.shield(vision_task), timeout=timeout
                                    ),
                                    image_limit,
                                )
                    except asyncio.TimeoutError:
                        logger.warning(
                            "关键词模型路由等待图片转述超时: user=%s",
                            user_id,
                        )
                    except Exception as exc:
                        logger.debug(
                            "关键词模型路由读取图片转述失败: user=%s error=%s",
                            user_id,
                            _single_line(exc, 120),
                        )
                if vision_text:
                    buffer["vision_text"] = vision_text

        source_field = "private_companion_delayed_image_vision_text"
        if not vision_text and not buffered_images:
            finder = getattr(self, "_find_reply_image_sources_for_event", None)
            transcriber = getattr(self, "_transcribe_private_inbound_images", None)
            if callable(finder) and callable(transcriber):
                try:
                    reply_sources = await finder(event)
                    if reply_sources:
                        image_limit = self._private_image_vision_text_limit(
                            len(reply_sources)
                        )
                        inbound_text = _single_line(
                            getattr(event, "message_str", ""), 800
                        )
                        vision_text = _single_line(
                            await transcriber(
                                reply_sources,
                                umo=str(
                                    getattr(event, "unified_msg_origin", "") or ""
                                ),
                                user_text=inbound_text,
                                force_contextual=self._private_image_user_has_specific_vision_request(
                                    inbound_text
                                ),
                            ),
                            image_limit,
                        )
                        source_field = "private_companion_reply_image_vision_text"
                except Exception as exc:
                    logger.debug(
                        "关键词模型路由预取引用图片转述失败: user=%s error=%s",
                        user_id,
                        _single_line(exc, 120),
                    )

        if not vision_text:
            return ""
        setattr(event, source_field, vision_text)
        setattr(event, "private_companion_image_caption_route_text", vision_text)
        logger.info(
            "图片转述已提供给关键词模型路由: user=%s source=%s preview=%s",
            user_id,
            source_field,
            _single_line(vision_text, 160),
        )
        return vision_text
