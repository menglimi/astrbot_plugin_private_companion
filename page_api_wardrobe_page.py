# -*- coding: utf-8 -*-
"""wardrobe_page 域。

由 tools/split_mixin_domain.py 从 page_api.py 机械抽取（4 个方法 + 0 个模块级名字 + 0 个类级赋值 / 73 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApi）。
"""
from __future__ import annotations

from pathlib import Path
from .page_api_shared import _page_api_host, _page_api_host_request as request
from typing import Any

from .logging_util import get_module_logger

logger = get_module_logger(__name__)



class PrivateCompanionPageApiWardrobePageMixin:
    """wardrobe_page 域（从 PrivateCompanionPageApi 拆出）。"""


    async def preview_wardrobe_outfit(self) -> dict[str, Any]:
        """Preview what the wardrobe would inject for one occasion.

        Read-only: never writes config and never calls the model, so the panel
        can refresh it freely while the administrator tunes the settings.
        """

        payload = await request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            return self._error("请求体必须是 JSON 对象")
        preview = getattr(self.plugin, "_wardrobe_outfit_preview", None)
        if not callable(preview):
            return self._error("当前插件实例不支持着装预览")
        # 缺省的 scene/weather 表示「用插件自动判定的值」；传空串表示这一轮没有场合上下文。
        # 场合只写进请求与种子，从不过滤候选，所以这里怎么填都不会藏起某件衣物。
        raw_scene = payload.get("scene")
        raw_weather = payload.get("weather")
        try:
            data = preview(
                scene=None if raw_scene is None else self._single_line(raw_scene, 20),
                weather=None if raw_weather is None else self._single_line(raw_weather, 120),
                seed=self._single_line(payload.get("seed"), 60),
            )
        except Exception as exc:
            logger.warning("着装预览失败: %s", self._single_line(exc, 160), exc_info=True)
            return self._error("着装预览失败，请稍后再试")
        return self._ok(data)

    def _wardrobe_page_local_path(self, value: Any) -> Path | None:
        """Allow only images already stored in the plugin's own asset directories."""

        path = Path(str(value or "")).expanduser()
        try:
            if not path.is_file() or path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
                return None
            resolved = path.resolve()
            data_root = Path(str(getattr(self.plugin, "data_dir", "") or ".")).expanduser().resolve()
            allowed_roots = (
                data_root / "photo_reference_images",
                data_root / "photo_reference_assets",
            )
            if not any(resolved == root or root in resolved.parents for root in allowed_roots):
                return None
            return resolved
        except (OSError, ValueError):
            return None

    async def get_wardrobe_intent(self) -> dict[str, Any]:
        """Read the session outfit intent for the wardrobe panel.

        Read-only: it only asks the plugin for the author's dialogue_outfit_override
        snapshot, so the panel can show what this session asked the character to wear.
        """

        reader = getattr(self.plugin, "_wardrobe_intent_snapshot", None)
        if not callable(reader):
            return self._error("当前插件实例不支持穿衣意图")
        try:
            snapshot = reader()
        except Exception as exc:
            logger.warning("穿衣意图读取失败: %s", self._single_line(exc, 160), exc_info=True)
            return self._error("读取穿衣意图失败，请稍后再试")
        return self._ok({"intent": snapshot if isinstance(snapshot, dict) else {}})

    async def clear_wardrobe_intent(self) -> dict[str, Any]:
        """Clear the session outfit intent so the daily rotation takes over again."""

        clearer = getattr(self.plugin, "_wardrobe_clear_intent", None)
        if not callable(clearer):
            return self._error("当前插件实例不支持穿衣意图")
        try:
            cleared = bool(clearer())
        except Exception as exc:
            logger.warning("穿衣意图清除失败: %s", self._single_line(exc, 160), exc_info=True)
            return self._error("清除穿衣意图失败，请稍后再试")
        return self._ok({"cleared": cleared})
