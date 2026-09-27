# -*- coding: utf-8 -*-
"""reality_touch 域。

由 tools/split_mixin_domain.py 从 page_api.py 机械抽取（3 个方法 + 0 个模块级名字 + 0 个类级赋值 / 62 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApi）。
"""
from __future__ import annotations

from .page_api_shared import _page_api_host, _page_api_host_request as request
from typing import Any

from .logging_util import get_module_logger

logger = get_module_logger(__name__)



class PrivateCompanionPageApiRealityTouchMixin:
    """reality_touch 域（从 PrivateCompanionPageApi 拆出）。"""


    async def get_reality_touch(self) -> dict[str, Any]:
        bridge_getter = getattr(self.plugin, "_reality_companion_api", None)
        bridge = bridge_getter() if callable(bridge_getter) else None
        linked_snapshotter = getattr(bridge, "page_snapshot", None) if bridge is not None else None
        if callable(linked_snapshotter):
            try:
                return self._ok(self._normalize_reality_touch_snapshot(linked_snapshotter()))
            except Exception as exc:
                logger.error("获取现实触及联动状态失败: %s", exc, exc_info=True)
                return self._exception_error("获取现实触及联动状态失败")
        return self._error(
            "现实触及已由“我会来到你身边”管理，请先安装并启用 astrbot_plugin_reality_companion。",
            status_code=503,
        )

    async def update_reality_touch(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        bridge_getter = getattr(self.plugin, "_reality_companion_api", None)
        bridge = bridge_getter() if callable(bridge_getter) else None
        linked_action = getattr(bridge, "page_action", None) if bridge is not None else None
        if callable(linked_action):
            try:
                result = await linked_action(payload)
                if not isinstance(result, dict) or not result.get("ok"):
                    return self._error(
                        self._single_line(result.get("message"), 240)
                        if isinstance(result, dict)
                        else "现实触及联动操作失败"
                    )
                snapshot = result.get("data") if isinstance(result.get("data"), dict) else {}
                if isinstance(result.get("result"), dict):
                    snapshot["action_result"] = result["result"]
                snapshot = self._normalize_reality_touch_snapshot(snapshot)
                snapshot["message"] = self._single_line(result.get("message"), 240) or "现实触及联动操作已完成"
                return self._ok(snapshot)
            except Exception as exc:
                logger.error("更新现实触及联动失败: %s", exc, exc_info=True)
                return self._exception_error("更新现实触及联动失败")
        return self._error(
            "现实触及已由“我会来到你身边”管理，请先安装并启用 astrbot_plugin_reality_companion。",
            status_code=503,
        )

    @staticmethod
    def _normalize_reality_touch_snapshot(snapshot: Any) -> dict[str, Any]:
        """Normalize snapshots from old and new Reality Companion bridges.

        Older embedded MiHome snapshots expose auth/login/device fields but do
        not include the newer ``available`` marker.  Keep an explicit false
        authoritative while deriving availability from the capability payload
        when the marker is absent.
        """
        normalized = dict(snapshot) if isinstance(snapshot, dict) else {}
        mihome = normalized.get("mihome")
        if not isinstance(mihome, dict):
            return normalized
        mihome = dict(mihome)
        if "available" not in mihome:
            mihome["available"] = any(
                key in mihome
                for key in ("auth", "login", "devices", "mappings", "tool_settings")
            )
        normalized["mihome"] = mihome
        return normalized
