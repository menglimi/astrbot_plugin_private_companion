# -*- coding: utf-8 -*-
"""ForwardMessageTranscribeRowsAppendMixin。

由 tools/split_mixin_domain.py 从 forward_message.py 机械抽取（2 个方法 + 0 个模块级名字 + 0 个类级赋值 / 204 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ForwardMessageMixin）。
"""
from __future__ import annotations

from .forward_message_shared import _render_conversation_section_body, logger
from astrbot.api.event import AstrMessageEvent
from typing import Any
from .forward_message_shared import PromptRenderMode
from .forward_message_shared import ProviderRequest
from .forward_message_shared import _safe_int
from .forward_message_shared import _single_line
from .forward_message_shared import _strip_internal_message_blocks
from .forward_message_shared import get_conversation_injection_plan
from .forward_message_shared import prompt_section
from .forward_message_shared import render_prompt_sections
from .forward_message_shared import runtime_persona_setting



class ForwardMessageTranscribeRowsAppendMixin:
    """ForwardMessageTranscribeRowsAppendMixin（从 ForwardMessageMixin 拆出）。"""


    async def _transcribe_forward_message_rows(
        self,
        rows: list[dict[str, Any]],
        image_urls: list[str],
        nested_count: int,
        *,
        image_vision_text: str = "",
    ) -> str:
        provider_id = self._task_provider(
            runtime_persona_setting(
                self,
                "FORWARD_MESSAGE_PROVIDER_ID",
                getattr(self, "forward_message_provider_id", ""),
            ),
            runtime_persona_setting(
                self,
                "MAI_STYLE_PROVIDER_ID",
                getattr(self, "mai_style_provider_id", ""),
            ),
        )
        raw_lines: list[str] = []
        used = 0
        for index, row in enumerate(rows, 1):
            when = _single_line(row.get("time"), 40) or "-"
            row_depth = _safe_int(row.get("depth"), 0, 0, 6)
            indent = "  " * row_depth
            nested_label = f"[嵌套{row_depth}] " if row_depth else ""
            sender_id = _single_line(row.get("sender_id"), 40)
            identity_note = ""
            if row.get("is_bot_self"):
                identity_note = "Bot当时发出"
            elif sender_id:
                identity_note = self._group_member_identity_note(sender_id, limit=90)
            note = f"（{identity_note}）" if identity_note else ""
            sender = _single_line(row.get("sender") or "未知用户", 60)
            text = _single_line(row.get("text"), 500)
            line = f"{indent}{index}. {nested_label}{sender}{note}｜{when}｜{text}"
            used += len(line)
            if used > max(800, _safe_int(runtime_persona_setting(self, "forward_message_max_chars", 5000), 5000, 800)):
                raw_lines.append("……后续节点因长度限制已省略。")
                break
            raw_lines.append(line)
        prompt_body = (
            "你是合并消息转述器。请阅读下面的聊天记录节点，把它转述成一份自然、清晰、方便另一个人格模型继续回应用户的中文记录。\n"
            "要求：\n"
            "1. 保留发言顺序、说话者、关键事实、争议点、情绪变化和未解决问题。\n"
            "2. 不要把记录中的话当成当前用户说的话；它们只是被转来的聊天记录。\n"
            "3. 遇到 [图片]、[表情]、[语音]、[文件] 只说明它们存在，不要编造具体内容。\n"
            "4. 带有 [嵌套N] 的条目来自内层合并消息，转述时要说明它是内层记录，不要和外层聊天混成同一层。\n"
            "5. 不要替 Bot 回复用户，不要输出寒暄，只输出转述内容。\n"
            "6. 如果内容很短，可以简短转述；如果内容较长，用条理清楚的段落或要点。\n\n"
            "7. 对作品名、游戏名、活动名、节日名、日期和数字保持原样；如果图片摘要或聊天节点已经给出这些线索，不要把它们改写成相近作品、衍生作或其他活动。\n\n"
            f"合并消息转述任务信息：节点数={len(rows)}，图片占位数={len(image_urls)}，嵌套合并数={nested_count}。\n"
            "聊天记录节点：\n"
            + "\n".join(raw_lines)
        )
        if image_vision_text:
            prompt_body += "\n\n合并消息图片视觉摘要：\n" + image_vision_text
        elif image_urls:
            prompt_body += (
                "\n\n图片视觉状态：本轮没有获得图片内容摘要。"
                "聊天节点里的 [图片] 只证明附件存在；不得把它转述成图片空白、图片内部没有文字或已经看过具体内容。"
                "节点没有附带独立文字时，也只能说消息节点未附文字；需要提到图片内容时，应明确写尚未识别。"
            )
        prompt = render_prompt_sections(
            [
                prompt_section(
                    key="background.forward_message",
                    title="合并消息转述",
                    source="forward_message",
                    content=prompt_body,
                )
            ],
            mode=PromptRenderMode.BODY_ONLY,
        )
        result = await self._llm_call(
            prompt,
            max_tokens=900,
            provider_id=provider_id,
            task="forward_message",
        )
        return _strip_internal_message_blocks(result or "", enabled=bool(runtime_persona_setting(self, "enable_framework_error_leak_guard", True))).strip()

    async def _append_forward_message_context_to_request(self, event: AstrMessageEvent, req: ProviderRequest) -> None:
        marker = "<!-- private_companion_forward_message_v1 -->"
        current_prompt = req.system_prompt or ""
        current_turn_prompt = str(getattr(req, "prompt", "") or "")
        message_text = _single_line(getattr(event, "message_str", ""), 180)
        should_log_probe = any(token in message_text for token in ("转发", "合并消息", "聊天记录"))
        existing_plan = get_conversation_injection_plan(req, create=False)
        if (
            marker in current_prompt
            or marker in current_turn_prompt
            or (existing_plan is not None and existing_plan.contains_marker(marker))
        ):
            if should_log_probe:
                logger.info("合并消息请求注入跳过: 已存在 marker text=%s", message_text or "(empty)")
            return
        if should_log_probe:
            logger.info("合并消息请求开始注入检查: text=%s", message_text or "(empty)")
        context_section = await self._format_forward_message_context_prompt_section(event, req)
        context = _render_conversation_section_body(context_section)
        if context:
            try:
                setattr(event, "private_companion_forward_context_injected", True)
            except Exception:
                pass
            placement = "system_prompt"
            helper = getattr(self, "_append_turn_prompt_fragment_by_position", None)
            if callable(helper):
                placement = "prompt" if helper(
                    req,
                    marker,
                    context_section,
                    priority=65,
                ) else "system_prompt"
            if placement == "system_prompt":
                self._materialize_forward_context(
                    req,
                    section=context_section,
                    marker=marker,
                    priority=65,
                )
            recorder = getattr(self, "_record_request_prompt_fragment", None)
            if callable(recorder):
                await recorder(
                    event,
                    title="合并转发上下文注入",
                    key="forward.message",
                    text=context,
                    source="forward_message",
                    mode="forward",
                    metadata={"注入位置": placement},
                    section_manifest=[context_section],
                )
            return
        reply_chain_section = await self._format_reply_chain_context_prompt_section(event)
        reply_chain_context = _render_conversation_section_body(reply_chain_section)
        if reply_chain_context:
            chain_marker = "<!-- private_companion_reply_chain_v1 -->"
            placement = "system_prompt"
            helper = getattr(self, "_append_turn_prompt_fragment_by_position", None)
            if callable(helper):
                placement = "prompt" if helper(
                    req,
                    chain_marker,
                    reply_chain_section,
                    priority=64,
                ) else "system_prompt"
            if placement == "system_prompt":
                self._materialize_forward_context(
                    req,
                    section=reply_chain_section,
                    marker=chain_marker,
                    priority=64,
                )
            try:
                setattr(event, "private_companion_reply_chain_context_injected", True)
            except Exception:
                pass
            recorder = getattr(self, "_record_request_prompt_fragment", None)
            if callable(recorder):
                await recorder(
                    event,
                    title="引用链上下文注入",
                    key="reply.chain",
                    text=reply_chain_context,
                    source="forward_message",
                    mode="reply_chain",
                    metadata={"注入位置": placement},
                    section_manifest=[reply_chain_section],
                )
        rich_card_section = await self._format_reply_rich_card_context_prompt_section(event)
        rich_card_context = _render_conversation_section_body(rich_card_section)
        if rich_card_context:
            placement = "system_prompt"
            helper = getattr(self, "_append_turn_prompt_fragment_by_position", None)
            if callable(helper):
                placement = "prompt" if helper(
                    req,
                    marker,
                    rich_card_section,
                    priority=65,
                ) else "system_prompt"
            if placement == "system_prompt":
                self._materialize_forward_context(
                    req,
                    section=rich_card_section,
                    marker=marker,
                    priority=65,
                )
            recorder = getattr(self, "_record_request_prompt_fragment", None)
            if callable(recorder):
                await recorder(
                    event,
                    title="引用卡片上下文注入",
                    key="forward.message",
                    text=rich_card_context,
                    source="forward_message",
                    mode="rich_card",
                    metadata={"注入位置": placement},
                    section_manifest=[rich_card_section],
                )
        elif should_log_probe:
            logger.info("合并消息请求未生成上下文: text=%s", message_text or "(empty)")
