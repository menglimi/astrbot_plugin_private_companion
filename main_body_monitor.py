# -*- coding: utf-8 -*-
"""body_monitor 域。

由 tools/split_main_domain_v2.py 从 main.py 机械抽取（3 个方法 / 19 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPlugin）。
"""
from __future__ import annotations

from typing import Any

class PrivateCompanionPluginBodyMonitorMixin:
    """body_monitor 域（从 PrivateCompanionPlugin 拆出）。"""

    async def _pull_body_monitor_candidates(self) -> dict[str, Any]:
        integration = getattr(self, "_body_monitor_integration", None)
        if integration is None:
            return {}
        return await integration.poll()

    def _body_monitor_integration_status_view(self) -> dict[str, Any]:
        integration = getattr(self, "_body_monitor_integration", None)
        if integration is None:
            return {
                "enabled": bool(getattr(self, "enable_body_monitor_integration", False)),
                "state": "initializing",
                "status": "initializing",
            }
        return integration.status_view()

    def _format_body_monitor_health_prompt(self, user: dict[str, Any], *, reason: str = "") -> str:
        integration = getattr(self, "_body_monitor_integration", None)
        if integration is None:
            return ""
        return integration.format_health_prompt(user, reason=reason)
