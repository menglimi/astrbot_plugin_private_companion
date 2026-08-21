"""Optional direct bridge to the NAI image plugin.

When ``photo_generation_backend`` is ``nai``, generation requests are routed
here instead of the standalone image companion service.  The request and
response shapes stay identical, so commands, tool calls and proactive flows
keep their existing delivery contract.
"""
from __future__ import annotations

from typing import Any

from astrbot.api import logger

from .helpers import _single_line
from .external_bridge_resolver import resolve_external_bridge
from .persona_config import runtime_persona_setting


class NAIImageBridgeMixin:
    def _nai_image_api(self) -> Any | None:
        return resolve_external_bridge(
            self,
            cache_key="nai_image",
            module_names=(
                "data.plugins.astrbot_plugin_nai_image.main",
                "astrbot_plugin_nai_image.main",
            ),
            getter_name="get_nai_image_api",
            star_name="astrbot_plugin_nai_image",
        )

    def _nai_image_selected(self, operation: str = "") -> bool:
        """Return whether the configured photo backend is the NAI direct link."""
        selected = (
            str(runtime_persona_setting(self, "photo_generation_backend", "auto") or "").strip().lower()
            == "nai"
        )
        if not selected:
            return False
        image_api_getter = getattr(self, "_image_companion_api", None)
        try:
            image_api = image_api_getter() if callable(image_api_getter) else None
            claims = getattr(image_api, "claims_model_profile", None)
            if callable(claims):
                claimed = (
                    claims("nai", operation=str(operation or "").strip().lower())
                    if str(operation or "").strip()
                    else claims("nai")
                )
                if claimed:
                    return False
        except Exception as exc:
            logger.warning(
                "[PrivateCompanion] 查询统一 NAI 路线所有权失败，保留官方直连: error=%s",
                _single_line(exc, 120),
            )
        return True

    def _nai_image_status(self) -> dict[str, Any]:
        api = self._nai_image_api()
        getter = getattr(api, "capability_status", None) if api is not None else None
        if not callable(getter):
            return {
                "installed": False,
                "enabled": False,
                "available": False,
                "reason": "nai_image_unavailable",
                "backends": {},
            }
        try:
            status = getter(self)
        except Exception as exc:
            logger.warning(
                "[PrivateCompanion] NAI 生图能力查询失败: error=%s",
                _single_line(exc, 160),
            )
            return {
                "installed": True,
                "enabled": False,
                "available": False,
                "reason": "status_query_failed",
                "backends": {},
            }
        return dict(status) if isinstance(status, dict) else {
            "installed": True,
            "enabled": False,
            "available": False,
            "reason": "invalid_status",
            "backends": {},
        }

    def _nai_image_available(self) -> bool:
        return bool(self._nai_image_status().get("available"))

    async def _nai_image_maintenance(self) -> dict[str, Any]:
        api = self._nai_image_api()
        maintainer = getattr(api, "maintenance", None) if api is not None else None
        if not callable(maintainer):
            return {}
        try:
            result = await maintainer(self)
        except Exception as exc:
            logger.warning(
                "[PrivateCompanion] NAI 生图后台维护失败: error=%s",
                _single_line(exc, 160),
            )
            return {}
        return dict(result) if isinstance(result, dict) else {}

    async def _nai_image_generate(self, **request: Any) -> tuple[str, str, str]:
        """Delegate an image request to the NAI plugin's direct interface.

        The host keeps the historical request/response shape so commands and
        delivery order remain unchanged, while the NAI plugin executes the
        image backend locally.
        """
        api = self._nai_image_api()
        generator = getattr(api, "generate_for_companion", None) if api is not None else None
        if not callable(generator):
            return (
                "NAI 生图",
                "",
                "生图后端已选择 NAI 直连，请安装并启用 NAI 生图插件 astrbot_plugin_nai_image。",
            )
        try:
            response = await generator(self, dict(request))
        except Exception as exc:
            logger.warning(
                "[PrivateCompanion] NAI 生图插件调用异常: workflow=%s error=%s",
                _single_line(request.get("workflow_kind"), 40),
                _single_line(exc, 160),
            )
            return (
                "NAI 生图",
                "",
                "NAI 生图插件暂时不可用，请检查该插件状态和生图排障记录。",
            )
        if not isinstance(response, dict) or response.get("handled") is not True:
            return (
                "NAI 生图",
                "",
                "NAI 生图插件当前未接管请求，请确认插件已启用。",
            )
        metadata = response.get("metadata")
        self._nai_image_generation_metadata = (
            dict(metadata) if isinstance(metadata, dict) else {}
        )
        return (
            _single_line(response.get("backend"), 80),
            _single_line(response.get("image_path"), 1000),
            _single_line(response.get("note"), 500),
        )

    def _nai_image_last_metadata(self) -> dict[str, Any]:
        value = getattr(self, "_nai_image_generation_metadata", None)
        return dict(value) if isinstance(value, dict) else {}
