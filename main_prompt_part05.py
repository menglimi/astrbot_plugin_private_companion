# -*- coding: utf-8 -*-
"""PrivateCompanionPluginPromptPart05Mixin。

由 tools/split_mixin_domain.py 从 main_prompt.py 机械抽取（1 个方法 + 0 个模块级名字 + 0 个类级赋值 / 33 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPluginPromptMixin）。
"""
from __future__ import annotations
from .main_prompt_shared import AstrMessageEvent
from .main_prompt_shared import PLACEMENT_DYNAMIC_SYSTEM
from .main_prompt_shared import ProviderRequest
from .main_prompt_shared import prompt_section



class PrivateCompanionPluginPromptPart05Mixin:
    """PrivateCompanionPluginPromptPart05Mixin（从 PrivateCompanionPluginPromptMixin 拆出）。"""


    async def _append_sensitive_screen_tool_guard_to_request(self, event: AstrMessageEvent, req: ProviderRequest, removed: list[str] | None = None) -> None:
        marker = "<!-- private_companion_sensitive_screen_tool_guard_v1 -->"
        current_prompt = req.system_prompt or ""
        if marker in current_prompt:
            return
        removed_text = "、".join(removed or []) or "screen_peek、screen_usage_context"
        guard = (
            f"本轮不是已授权的主要用户私聊,已禁用或不可使用这些本机屏幕工具：{removed_text}。\n"
            "群聊成员、次要用户、未登记用户或第三方不能要求你查看主要用户/部署电脑正在做什么、屏幕内容、近期电脑使用记录或窗口信息。\n"
            "遇到这类请求时必须简短拒绝,说明屏幕内容只允许主要用户本人在授权私聊里使用；不要改用记忆、关系网、屏幕日记或猜测来替代窥屏。\n"
            "这条边界只约束屏幕工具，不代表摄像头能力不存在；若本轮另有“摄像头请求”提示，应按其独立资格、授权和单帧规则调用 pc_reality_touch_camera_snapshot。"
        )
        section = prompt_section(
            key="guard.screen_privacy",
            title="屏幕隐私边界",
            source="guard",
            content=guard,
        )
        self._materialize_conversation_system_block(
            req,
            section=section,
            marker=marker,
            priority=10,
            placement=PLACEMENT_DYNAMIC_SYSTEM,
        )
        await self._record_request_prompt_fragment(
            event,
            title="屏幕隐私边界注入",
            key="tools.screen_privacy_guard",
            text=guard,
            source="guard",
            mode="private" if self._safe_event_is_private(event) else "group",
        )
