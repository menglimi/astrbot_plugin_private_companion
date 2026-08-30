# -*- coding: utf-8 -*-
"""Required bridge to the standalone image companion plugin.

The public request shape deliberately mirrors the former private generator so
existing commands, tool calls and proactive flows keep their exact delivery
contract while image execution moves out of the core companion package.
"""
from __future__ import annotations

from typing import Any

from astrbot.api import logger

from .helpers import _single_line
from .external_bridge_resolver import (
    invalidate_external_bridge_cache,
    resolve_external_bridge,
)


class ImageCompanionBridgeMixin:
    def _image_companion_api(self) -> Any | None:
        return resolve_external_bridge(
            self,
            cache_key="image_companion",
            module_names=(
                "data.plugins.astrbot_plugin_image_companion.main",
                "astrbot_plugin_image_companion.main",
            ),
            getter_name="get_image_companion_api",
            star_name="astrbot_plugin_image_companion",
            prefer_module_getter=True,
        )

    def _image_companion_required(self) -> bool:
        """Return whether this object is the production companion host."""
        return self.__class__.__name__ == "PrivateCompanionPlugin" or any(
            base.__name__ == "PrivateCompanionPlugin"
            for base in getattr(self.__class__, "__mro__", ())
        )

    def _image_companion_status(self) -> dict[str, Any]:
        """Return the split service status without importing its runtime."""
        api = self._image_companion_api()
        getter = getattr(api, "capability_status", None) if api is not None else None
        if not callable(getter):
            return {
                "installed": False,
                "enabled": False,
                "available": False,
                "reason": "image_companion_unavailable",
                "backup_external_note": "image_companion_unavailable",
                "backends": {},
            }
        try:
            status = getter(self)
        except Exception as exc:
            logger.warning(
                "[PrivateCompanion] 独立生图能力查询失败: error=%s",
                _single_line(exc, 160),
            )
            return {
                "installed": True,
                "enabled": False,
                "available": False,
                "reason": "status_query_failed",
                "backup_external_note": "status_query_failed",
                "backends": {},
            }
        return dict(status) if isinstance(status, dict) else {
            "installed": True,
            "enabled": False,
            "available": False,
            "reason": "invalid_status",
            "backup_external_note": "invalid_status",
            "backends": {},
        }

    def _image_companion_available(self) -> bool:
        return bool(self._image_companion_status().get("available"))

    def _image_companion_backend_available(self, backend: str) -> bool:
        status = self._image_companion_status()
        backends = status.get("backends")
        return bool(backends.get(backend)) if isinstance(backends, dict) else False

    def _image_companion_load_state(self, *, force_refresh: bool = False) -> dict[str, Any]:
        api = self._image_companion_api()
        getter = getattr(api, "local_load_state", None) if api is not None else None
        if not callable(getter):
            return {"enabled": False, "available": False, "busy": False, "reason": "独立生图插件不可用"}
        try:
            state = getter(self, force_refresh=force_refresh)
        except Exception as exc:
            logger.warning(
                "[PrivateCompanion] 独立生图负载查询失败: error=%s",
                _single_line(exc, 160),
            )
            return {"enabled": False, "available": False, "busy": False, "reason": "负载状态查询失败"}
        return dict(state) if isinstance(state, dict) else {}

    async def _image_companion_maintenance(self) -> dict[str, Any]:
        api = self._image_companion_api()
        maintainer = getattr(api, "maintenance", None) if api is not None else None
        if not callable(maintainer):
            return {}
        try:
            result = await maintainer(self)
        except Exception as exc:
            logger.warning(
                "[PrivateCompanion] 独立生图后台维护失败: error=%s",
                _single_line(exc, 160),
            )
            return {}
        return dict(result) if isinstance(result, dict) else {}

    async def _image_companion_generate(self, **request: Any) -> tuple[str, str, str]:
        """Delegate every image request to the split plugin.

        The host keeps the historical request/response shape so commands and
        delivery order remain unchanged, but it no longer executes an image
        backend locally.
        """
        api = self._image_companion_api()
        generator = getattr(api, "generate_for_companion", None) if api is not None else None
        if not callable(generator):
            return (
                "独立生图服务",
                "",
                "生图能力已拆分，请安装并启用“我会画给你看”插件 astrbot_plugin_image_companion。",
            )
        try:
            response = await generator(self, dict(request))
        except Exception as exc:
            logger.warning(
                "[PrivateCompanion] 独立生图插件调用异常: workflow=%s error=%s",
                _single_line(request.get("workflow_kind"), 40),
                _single_line(exc, 160),
            )
            return (
                "独立生图服务",
                "",
                "“我会画给你看”暂时不可用，请检查该插件状态和生图排障记录。",
            )
        if not isinstance(response, dict) or response.get("handled") is not True:
            return (
                "独立生图服务",
                "",
                "“我会画给你看”当前未接管请求，请确认插件已启用。",
            )
        metadata = response.get("metadata")
        self._image_companion_generation_metadata = (
            dict(metadata) if isinstance(metadata, dict) else {}
        )
        return (
            _single_line(response.get("backend"), 80),
            _single_line(response.get("image_path"), 1000),
            _single_line(response.get("note"), 500),
        )

    def _image_companion_last_metadata(self) -> dict[str, Any]:
        value = getattr(self, "_image_companion_generation_metadata", None)
        return dict(value) if isinstance(value, dict) else {}

    async def _image_companion_test_endpoint(
        self,
        endpoint: dict[str, Any],
        prompt: str,
    ) -> dict[str, Any]:
        api = self._image_companion_api()
        tester = getattr(api, "test_endpoint", None) if api is not None else None
        if not callable(tester):
            # Plugin updates can leave a valid-looking older API object in the
            # module cache. Re-resolve once from AstrBot's live registry before
            # reporting the extension as unavailable.
            invalidate_external_bridge_cache(self, "image_companion")
            refreshed_api = self._image_companion_api()
            refreshed_tester = (
                getattr(refreshed_api, "test_endpoint", None)
                if refreshed_api is not None
                else None
            )
            api = refreshed_api
            if callable(refreshed_tester):
                tester = refreshed_tester
        if not callable(tester):
            if api is not None:
                return {
                    "ok": False,
                    "message": "已检测到“我会画给你看”，但当前运行实例缺少在线 API 测试接口；请分别重载两个插件或完整重启 AstrBot。",
                }
            return {"ok": False, "message": "请安装并启用“我会画给你看”后再测试在线图片 API。"}
        return await tester(self, dict(endpoint or {}), str(prompt or ""))
