# -*- coding: utf-8 -*-
"""Bridge for the optional creative/content companion plugin."""
from __future__ import annotations

from typing import Any

from astrbot.api import logger

from .helpers import _single_line
from .external_bridge_resolver import resolve_external_bridge


class ContentCompanionBridgeMixin:
    def _content_companion_api(self) -> Any | None:
        return resolve_external_bridge(
            self,
            cache_key="content_companion",
            module_names=(
                "data.plugins.astrbot_plugin_content_companion.main",
                "astrbot_plugin_content_companion.main",
            ),
            getter_name="get_content_companion_api",
            star_name="astrbot_plugin_content_companion",
            prefer_module_getter=True,
        )

    def _content_companion_status(self) -> dict[str, Any]:
        api = self._content_companion_api()
        getter = getattr(api, "status", None) if api is not None else None
        if not callable(getter):
            return {"installed": False, "enabled": False, "available": False, "reason": "content_companion_unavailable"}
        try:
            value = getter()
        except Exception as exc:
            logger.warning("[PrivateCompanion] 独立创作能力查询失败: %s", _single_line(exc, 160))
            return {"installed": True, "enabled": False, "available": False, "reason": "status_query_failed"}
        return dict(value) if isinstance(value, dict) else {"installed": True, "enabled": False, "available": False}

    def _content_companion_available(self) -> bool:
        return bool(self._content_companion_status().get("available"))

    def _content_companion_qzone_available(self) -> bool:
        status = self._content_companion_status()
        return bool(isinstance(status.get("qzone"), dict) and status["qzone"].get("enabled"))

    async def _content_companion_call(self, operation: str, *args: Any, **kwargs: Any) -> Any:
        api = self._content_companion_api()
        handler = getattr(api, operation, None) if api is not None else None
        if not callable(handler):
            return None
        try:
            self._content_companion_delegating = True
            return await handler(self, *args, **kwargs)
        except Exception as exc:
            logger.warning("[PrivateCompanion] 独立创作操作失败: operation=%s error=%s", operation, _single_line(exc, 160))
            return None
        finally:
            self._content_companion_delegating = False

    async def _maybe_advance_creative_projects(self) -> None:
        if not getattr(self, "_content_companion_delegating", False) and self._content_companion_available():
            result = await self._content_companion_call("advance_creative_projects")
            if result is not None:
                return
        return await super()._maybe_advance_creative_projects()

    async def _maybe_start_creative_project(self, *, idle_checked: bool = False) -> bool:
        if not getattr(self, "_content_companion_delegating", False) and self._content_companion_available():
            result = await self._content_companion_call("maybe_start_creative_project", idle_checked=idle_checked)
            if result is not None:
                return bool(result)
        return bool(await super()._maybe_start_creative_project(idle_checked=idle_checked))

    async def _generate_creative_project(self, source: dict[str, str]) -> Any:
        if not getattr(self, "_content_companion_delegating", False) and self._content_companion_available():
            result = await self._content_companion_call("generate_creative_project", source)
            if result is not None:
                return result
        return await super()._generate_creative_project(source)

    async def _generate_creative_chunk(self, project: dict[str, Any], budget: int) -> str:
        if not getattr(self, "_content_companion_delegating", False) and self._content_companion_available():
            result = await self._content_companion_call("generate_creative_chunk", project, budget)
            if result is not None:
                return str(result)
        return await super()._generate_creative_chunk(project, budget)

    async def _review_creative_chunk(self, *args: Any, **kwargs: Any) -> Any:
        if not getattr(self, "_content_companion_delegating", False) and self._content_companion_available():
            result = await self._content_companion_call("review_creative_chunk", *args, **kwargs)
            if result is not None:
                return result
        return await super()._review_creative_chunk(*args, **kwargs)

    async def _apply_creative_manual_edit(self, *args: Any, **kwargs: Any) -> Any:
        if not getattr(self, "_content_companion_delegating", False) and self._content_companion_available():
            result = await self._content_companion_call("apply_creative_manual_edit", *args, **kwargs)
            if result is not None:
                return result
        return await super()._apply_creative_manual_edit(*args, **kwargs)

    async def _rebuild_creative_memory_from_project(self, project_id: str) -> Any:
        if not getattr(self, "_content_companion_delegating", False) and self._content_companion_available():
            result = await self._content_companion_call("rebuild_creative_memory", project_id)
            if result is not None:
                return result
        return await super()._rebuild_creative_memory_from_project(project_id)

    async def _maybe_generate_creative_cover(self, project_id: str, *, force: bool = False) -> Any:
        if self._content_companion_available():
            result = await self._content_companion_call("maybe_generate_creative_cover", project_id, force=force)
            if result is not None:
                return result
        return await super()._maybe_generate_creative_cover(project_id, force=force)
