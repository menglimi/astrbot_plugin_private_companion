# -*- coding: utf-8 -*-
"""photo_tool 域。

由 tools/split_main_domain_v2.py 从 main.py 机械抽取（6 个方法 / 225 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPlugin）。
"""
from __future__ import annotations

import re
from .conversation_injection_plan import PLACEMENT_TOOL_CONTRACT, get_conversation_injection_plan
from .conversation_prompt_section import exact_text, prompt_heading_ref, prompt_section, render_prompt_content
from .helpers import _single_line
from .main_shared import _PHOTO_TOOL_PROMPT_FORMAT_MARKER, _multi_persona_event_context
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest
from copy import copy
from typing import Any

from .logging_util import get_module_logger

logger = get_module_logger(__name__)

class PrivateCompanionPluginPhotoToolMixin:
    """photo_tool 域（从 PrivateCompanionPlugin 拆出）。"""

    @staticmethod
    def _tool_set_has_named_tool(tool_set: Any, tool_name: str) -> bool:
        get_tool = getattr(tool_set, "get_tool", None)
        if callable(get_tool):
            try:
                return get_tool(tool_name) is not None
            except Exception:
                pass
        tools = getattr(tool_set, "tools", None)
        if isinstance(tools, list):
            return any(_single_line(getattr(tool, "name", ""), 120) == tool_name for tool in tools)
        return False

    @staticmethod
    def _tool_set_tool_names(tool_set: Any) -> list[str]:
        names: list[str] = []
        tools = getattr(tool_set, "tools", None)
        if isinstance(tools, list):
            for tool in tools:
                name = _single_line(getattr(tool, "name", ""), 120)
                if name and name not in names:
                    names.append(name)
        return names

    def _remove_sensitive_screen_tools_from_request(self, event: AstrMessageEvent, req: ProviderRequest) -> list[str]:
        tool_set = getattr(req, "func_tool", None)
        if tool_set is None:
            return []
        allow_owner_private = self._is_owner_private_event(event)
        if allow_owner_private:
            return []
        sensitive_tools = {"screen_peek", "screen_usage_context"}
        names = self._tool_set_tool_names(tool_set)
        if not names:
            names = [name for name in sensitive_tools if self._tool_set_has_named_tool(tool_set, name)]
        removed: list[str] = []
        remove_tool = getattr(tool_set, "remove_tool", None)
        for name in names:
            if name not in sensitive_tools:
                continue
            try:
                if callable(remove_tool):
                    remove_tool(name)
                else:
                    tools = getattr(tool_set, "tools", None)
                    if isinstance(tools, list):
                        tool_set.tools = [tool for tool in tools if _single_line(getattr(tool, "name", ""), 120) != name]
                    else:
                        continue
                removed.append(name)
            except Exception as exc:
                logger.warning(
                    "移除敏感屏幕工具失败: tool=%s session=%s error=%s",
                    name,
                    _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                    _single_line(exc, 160),
                )
        if removed:
            try:
                setattr(event, "_private_companion_removed_sensitive_tools", removed)
            except Exception:
                pass
            logger.info(
                "已移除非主人私聊场景的敏感屏幕工具: session=%s sender=%s tools=%s",
                _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                self._safe_event_sender_id(event) or "-",
                ",".join(removed),
            )
        return removed

    def _scope_photo_generation_tool_for_request(
        self,
        req: ProviderRequest,
        event: AstrMessageEvent | None = None,
    ) -> bool:
        """Remove the Image tool when this request lacks runtime permission."""
        if req is None or self._user_photo_generation_prompt_enabled(event):
            return False
        tool_set = getattr(req, "func_tool", None)
        if tool_set is None:
            return False
        tools = getattr(tool_set, "tools", None)
        if isinstance(tools, list):
            filtered = [
                tool
                for tool in tools
                if _single_line(getattr(tool, "name", ""), 120) != "pc_generate_photo"
            ]
            if len(filtered) == len(tools):
                return False
            try:
                request_tool_set = copy(tool_set)
                request_tool_set.tools = filtered
                req.func_tool = request_tool_set
                return True
            except Exception as exc:
                logger.debug(
                    "请求级移除未就绪生图工具失败: %s",
                    _single_line(exc, 120),
                )
                return False

        try:
            request_tool_set = copy(tool_set)
            remove_tool = getattr(request_tool_set, "remove_tool", None)
            if not callable(remove_tool):
                return False
            remove_tool("pc_generate_photo")
            req.func_tool = request_tool_set
            return True
        except Exception as exc:
            logger.debug(
                "兼容请求级移除未就绪生图工具失败: %s",
                _single_line(exc, 120),
            )
            return False

    def _annotate_photo_tool_prompt_format_for_request(self, req: ProviderRequest) -> bool:
        """Attach the selected prompt syntax to this request's photo tool schema."""
        if not self._photo_generation_runtime_available():
            return False
        tool_set = getattr(req, "func_tool", None) if req is not None else None
        get_tool = getattr(tool_set, "get_tool", None) if tool_set is not None else None
        if not callable(get_tool):
            return False
        try:
            tool = get_tool("pc_generate_photo")
        except Exception:
            return False
        if tool is None or not bool(getattr(tool, "active", True)):
            return False

        instruction_getter = getattr(self, "_photo_tool_prompt_format_instruction", None)
        if not callable(instruction_getter):
            return False
        instruction = re.sub(
            r"\s+",
            " ",
            str(instruction_getter() or ""),
        ).strip()
        if not instruction:
            return False
        marker = _PHOTO_TOOL_PROMPT_FORMAT_MARKER
        description = re.sub(
            rf"\n*\s*{re.escape(marker)}.*?{re.escape(marker)}\s*",
            "",
            str(getattr(tool, "description", "") or ""),
            flags=re.DOTALL,
        ).strip()
        annotated = (
            f"{description}\n\n{marker}\n"
            f"{render_prompt_content(prompt_heading_ref('提示词表达方式'))}"
            f"prompt 参数必须按下述格式书写：{instruction}\n"
            f"{marker}"
        ).strip()
        contract_section = prompt_section(
            key="tool.photo.prompt_format",
            title="提示词表达方式",
            source="photo_tool",
            content=exact_text(annotated),
        )

        def record_contract() -> None:
            plan = get_conversation_injection_plan(req)
            if plan is None:
                return
            plan.add(
                section=contract_section,
                marker=marker,
                priority=10,
                placement=PLACEMENT_TOOL_CONTRACT,
                materialized=True,
                merge_policy="replace",
            )

        tools = getattr(tool_set, "tools", None)
        if isinstance(tools, list):
            try:
                for index, existing in enumerate(tools):
                    if existing is tool:
                        request_tool = copy(tool)
                        request_tool.description = annotated
                        request_tools = list(tools)
                        request_tools[index] = request_tool
                        request_tool_set = copy(tool_set)
                        request_tool_set.tools = request_tools
                        req.func_tool = request_tool_set
                        record_contract()
                        return True
            except Exception as exc:
                logger.debug(
                    "pc_generate_photo 请求工具描述标注失败: %s",
                    _single_line(exc, 120),
                )
                return False

        # Older request-local wrappers may expose get_tool() without a tools list.
        if getattr(tool, "handler", None) is None:
            try:
                tool.description = annotated
                record_contract()
                return True
            except Exception as exc:
                logger.debug(
                    "pc_generate_photo 兼容工具描述标注失败: %s",
                    _single_line(exc, 120),
                )
        return False

    @filter.on_llm_request(priority=-251000)
    @_multi_persona_event_context
    async def annotate_photo_tool_prompt_format(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        *args,
        **kwargs,
    ):
        """Expose prompt-format guidance whenever the photo tool is available."""
        if self is None or req is None or not bool(getattr(self, "enabled", False)):
            return
        try:
            if self._scope_photo_generation_tool_for_request(req, event):
                return
            self._annotate_photo_tool_prompt_format_for_request(req)
        except Exception as exc:
            logger.debug(
                "pc_generate_photo 工具提示词格式标注失败: %s",
                _single_line(exc, 120),
            )
