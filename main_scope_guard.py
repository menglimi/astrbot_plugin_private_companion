# -*- coding: utf-8 -*-
"""scope_guard 域。

由 tools/split_main_domain_v2.py 从 main.py 机械抽取（4 个方法 / 86 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPlugin）。
"""
from __future__ import annotations

from .conversation_injection_plan import get_conversation_injection_plan
from .group_context_interception import intercept_astrbot_group_context
from .helpers import _single_line
from .main_shared import _multi_persona_event_context
from .persona_config import runtime_persona_setting
from .private_scope_isolation import GROUP_SCOPE_MARKERS
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest

from .logging_util import get_module_logger

logger = get_module_logger(__name__)

class PrivateCompanionPluginScopeGuardMixin:
    """scope_guard 域（从 PrivateCompanionPlugin 拆出）。"""

    @filter.on_llm_request()
    @_multi_persona_event_context
    async def inject_tts_enhancement_request_fallback(self, event: AstrMessageEvent, req: ProviderRequest, *args, **kwargs):
        """TTS 请求规则独立兜底，避免被状态注入链路早退顺手跳过。"""
        if self is None or not self.enabled:
            return
        if self._stop_group_llm_reply_if_blocked(event, source="llm_request_tts_fallback"):
            return
        if self._proactive_only_blocks_passive_event(event, "enable_tts_enhancement"):
            return
        await self.apply_tts_enhancement_request(event, req)

    def _finalize_passive_reply_tool_boundary(self, event: AstrMessageEvent) -> list[str]:
        if not bool(getattr(event, "_private_companion_passive_reply_tool_boundary", False)):
            return []
        req = getattr(event, "_private_companion_passive_reply_request", None)
        getter = getattr(event, "get_extra", None)
        if callable(getter):
            try:
                req = getter("provider_request") or req
            except Exception:
                pass
        return self._append_passive_reply_tool_boundary(event, req) if req is not None else []

    @filter.on_llm_request(priority=-252000)
    @_multi_persona_event_context
    async def intercept_native_astrbot_group_context(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        *args,
        **kwargs,
    ):
        """Prefer the plugin group context while preserving AstrBot records."""
        if self is None or req is None or not bool(getattr(self, "enabled", False)):
            return
        if bool(getattr(event, "is_private_chat", lambda: False)()):
            return
        if not bool(runtime_persona_setting(self, "intercept_astrbot_group_context", True)):
            return
        if not bool(runtime_persona_setting(self, "enable_group_history_injection", True)):
            return
        marker = "<!-- private_companion_group_context_v1 -->"
        if not self._request_has_managed_prompt_marker(req, marker):
            return
        result = intercept_astrbot_group_context(event, req)
        logger.info(
            "已拦截 AstrBot 群聊对话注入: session=%s history=%s icl=%s",
            _single_line(getattr(event, "unified_msg_origin", ""), 140) or "unknown",
            result.get("history_messages", 0),
            result.get("group_icl_removed", 0),
        )

    @filter.on_llm_request(priority=-259000)
    @_multi_persona_event_context
    async def enforce_private_request_scope_isolation(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        *args,
        **kwargs,
    ):
        """Prune group-only plan blocks and residues before private dispatch."""
        if self is None or req is None or not bool(getattr(self, "enabled", False)):
            return
        try:
            if not bool(getattr(event, "is_private_chat", lambda: False)()):
                return
        except Exception:
            if ":FriendMessage:" not in str(
                getattr(event, "unified_msg_origin", "") or ""
            ):
                return

        plan = get_conversation_injection_plan(req, create=False)
        if plan is not None:
            try:
                if plan.remove_markers(
                    f"<!-- {marker} -->" for marker in GROUP_SCOPE_MARKERS
                ):
                    plan.render_into(req)
            except Exception as exc:
                logger.warning(
                "私聊请求的群作用域注入计划裁剪失败: session=%s error=%s",
                    _single_line(getattr(event, "unified_msg_origin", ""), 120)
                    or "unknown",
                    _single_line(exc, 160),
                )
        self._sanitize_private_companion_prompt_artifacts_in_request(event, req)
