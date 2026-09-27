# -*- coding: utf-8 -*-
"""天气定位与图片接口域。

由 tools/split_mixin_domain.py 从 command_handlers.py 机械抽取（12 个方法 + 0 个模块级名字 + 0 个类级赋值 / 325 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 CommandHandlersMixin）。
"""
from __future__ import annotations

import asyncio
from .helpers import _flat_get, _safe_int, _set_into_config, _single_line
from .persona_config import runtime_persona_setting
from typing import Any



class CommandHandlersQweatherImageApiMixin:
    """天气定位与图片接口域（从 CommandHandlersMixin 拆出）。"""


    async def _qweather_location_command_text(self, action: str, value: Any = "") -> str:
        """View or persist the shared QWeather city used by weather and alerts."""

        configured = _single_line(runtime_persona_setting(self, 'weather_location', ""), 180).replace("，", ",").strip()
        if action in {"查看城市", "当前城市", "天气城市"}:
            snapshot_getter = getattr(self, "_qweather_location_snapshot", None)
            snapshot = snapshot_getter() if callable(snapshot_getter) else {}
            label = _single_line(snapshot.get("label"), 120) if isinstance(snapshot, dict) else ""
            location_id = _single_line(snapshot.get("location_id"), 40) if isinstance(snapshot, dict) else ""
            lines = [f"当前绑定城市：{configured or '未绑定'}"]
            if label:
                lines.append(f"和风匹配地点：{label}")
            if location_id:
                lines.append(f"LocationID：{location_id}")
            lines.append("绑定方式：陪伴 绑定城市 <城市|区县,城市|LocationID>")
            return "\n".join(lines)

        if action in {"解绑城市", "清除城市"}:
            if not configured:
                return "当前没有绑定城市。\n绑定方式：陪伴 绑定城市 <城市|区县,城市|LocationID>"
            saved = await self._persist_qweather_location_setting("")
            if not saved:
                return "解绑城市失败：配置未能保存，原城市仍然保留。"
            return "已清除绑定城市。若仍配置了天气经纬度，天气功能会继续使用经纬度作为兜底。"

        query = _single_line(value, 180).replace("，", ",").strip()
        if not query:
            return (
                "请这样使用：陪伴 绑定城市 <城市|区县,城市|LocationID>\n"
                "同名区县建议写成“朝阳区,北京”。"
            )
        if str(runtime_persona_setting(self, 'weather_source', "qweather") or "qweather").strip().lower() != "qweather":
            return "当前天气来源不是和风天气。请先在功能开关的天气设置中选择和风天气，再绑定城市。"
        host_getter = getattr(self, "_qweather_alert_api_host", None)
        token_getter = getattr(self, "_qweather_alert_token", None)
        if not callable(host_getter) or not host_getter():
            return "和风天气 API Host 尚未配置，无法查询城市。"
        if not callable(token_getter) or not token_getter():
            return "和风天气 API 凭据尚未配置，无法查询城市。"

        command_lock = getattr(self, "_qweather_location_command_lock", None)
        if not isinstance(command_lock, asyncio.Lock):
            command_lock = asyncio.Lock()
            self._qweather_location_command_lock = command_lock
        async with command_lock:
            lookup = getattr(self, "_fetch_qweather_location_lookup", None)
            resolved = await lookup(query) if callable(lookup) else {}
            if (
                not isinstance(resolved, dict)
                or resolved.get("lat") is None
                or resolved.get("lon") is None
            ):
                return (
                    f"没有从和风天气匹配到“{query}”，原城市未修改。\n"
                    "请尝试填写“区县,城市”或 LocationID，并检查 Host 与 API 凭据。"
                )
            if not await self._persist_qweather_location_setting(query, resolved=resolved):
                return "绑定城市失败：配置未能保存，原城市仍然保留。"

        label = _single_line(resolved.get("label"), 120) or query
        location_id = _single_line(resolved.get("location_id"), 40)
        result = f"已绑定城市：{label}"
        if location_id:
            result += f"\nLocationID：{location_id}"
        return result

    async def _persist_qweather_location_setting(
        self,
        value: str,
        *,
        resolved: dict[str, Any] | None = None,
    ) -> bool:
        config = getattr(self, "config", None)
        if config is None:
            return False
        previous_runtime = str(runtime_persona_setting(self, 'weather_location', "") or "")
        previous_config = _flat_get(config, "weather_location", previous_runtime)
        self.weather_location = value
        if not _set_into_config(config, "weather_location", value):
            self.weather_location = previous_runtime
            return False
        if not await self._save_config_if_possible():
            self.weather_location = previous_runtime
            _set_into_config(config, "weather_location", previous_config)
            return False

        data = getattr(self, "data", None)

        def invalidate() -> None:
            if not isinstance(data, dict):
                return
            data["qweather_location"] = {}
            data.pop("daily_weather", None)
            data["weather_alerts"] = {}
            data["weather_alert_awareness"] = {}
            saver = getattr(self, "_save_data_sync", None)
            if callable(saver):
                saver(sections=set(), deleted_sections={"daily_weather"})

        data_lock = getattr(self, "_data_lock", None)
        if isinstance(data_lock, asyncio.Lock):
            async with data_lock:
                invalidate()
        else:
            invalidate()

        if value and isinstance(resolved, dict):
            store = getattr(self, "_store_qweather_location", None)
            if callable(store):
                await store(resolved)
        return True

    def _feature_on_text(self, value: Any) -> str:
        return "开启" if bool(value) else "关闭"

    def _image_api_runtime_value(self, attr_name: str, default: Any = "") -> Any:
        return getattr(self, attr_name, default)

    def _image_api_format_runtime_pair(self, *, backup: bool = False) -> str:
        prefix = "backup_" if backup else ""
        platform = _single_line(self._image_api_runtime_value(f"{prefix}external_image_api_platform", "auto"), 30) or "auto"
        model = _single_line(self._image_api_runtime_value(f"{prefix}external_image_api_model", ""), 80) or "未配置"
        size = _single_line(self._image_api_runtime_value(f"{prefix}external_image_api_size", ""), 40) or "未配置"
        timeout = _safe_int(self._image_api_runtime_value(f"{prefix}external_image_api_timeout_seconds", 180), 180, 20, 600)
        base_url = str(self._image_api_runtime_value(f"{prefix}external_image_api_base_url", "") or "").strip()
        key = str(self._image_api_runtime_value(f"{prefix}external_image_api_key", "") or "").strip()
        ready = bool(base_url and key and str(self._image_api_runtime_value(f"{prefix}external_image_api_model", "") or "").strip())
        return (
            f"{'备选' if backup else '主用'}："
            f"{'可用' if ready else '未完整'}｜平台 {platform}｜模型 {model}｜尺寸 {size}｜超时 {timeout}s"
        )

    def _image_api_endpoint_queue_for_command(self) -> list[dict[str, Any]]:
        normalizer = getattr(self, "_normalize_external_image_api_endpoints", None)
        configured = getattr(self, "external_image_api_endpoints", [])
        endpoints = normalizer(configured) if callable(normalizer) else configured
        if isinstance(endpoints, list) and endpoints:
            return [endpoint for endpoint in endpoints if isinstance(endpoint, dict)]
        return []

    def _image_api_endpoint_ready_for_command(self, endpoint: dict[str, Any]) -> bool:
        return bool(
            endpoint
            and endpoint.get("enabled", True)
            and str(endpoint.get("base_url") or "").strip()
            and str(endpoint.get("api_key") or "").strip()
            and str(endpoint.get("model") or "").strip()
        )

    def _image_api_endpoint_status_line(self, endpoint: dict[str, Any], index: int) -> str:
        name = _single_line(endpoint.get("name"), 34) or f"在线 API {index + 1}"
        platform = _single_line(endpoint.get("platform") or "auto", 24) or "auto"
        model = _single_line(endpoint.get("model") or "", 64) or "未配置"
        size = _single_line(endpoint.get("size") or "1024x1024", 32) or "1024x1024"
        timeout = _safe_int(endpoint.get("timeout_seconds"), 180, 20, 600)
        if not bool(endpoint.get("enabled", True)):
            state = "已关闭"
        else:
            state = "可用" if self._image_api_endpoint_ready_for_command(endpoint) else "未完整"
        return f"{index + 1}. {name}：{state}｜平台 {platform}｜模型 {model}｜尺寸 {size}｜超时 {timeout}s"

    def _sync_legacy_image_api_config_from_command_endpoints(self, endpoints: list[dict[str, Any]]) -> None:
        normalizer = getattr(self, "_normalize_external_image_api_endpoints", None)
        normalized = normalizer(endpoints) if callable(normalizer) else list(endpoints or [])
        first = normalized[0] if len(normalized) >= 1 and isinstance(normalized[0], dict) else {}
        second = normalized[1] if len(normalized) >= 2 and isinstance(normalized[1], dict) else {}

        def endpoint_complete(endpoint: dict[str, Any]) -> bool:
            return self._image_api_endpoint_ready_for_command(endpoint)

        updates = {
            "external_image_api_platform": first.get("platform", "auto") if first else "auto",
            "EXTERNAL_IMAGE_API_BASE_URL": first.get("base_url", "") if first else "",
            "EXTERNAL_IMAGE_API_KEY": first.get("api_key", "") if first else "",
            "EXTERNAL_IMAGE_API_MODEL": first.get("model", "") if first else "",
            "external_image_api_size": first.get("size", "1024x1024") if first else "1024x1024",
            "external_image_api_timeout_seconds": _safe_int(first.get("timeout_seconds"), 180, 20, 600) if first else 180,
            "external_image_api_custom_headers": first.get("custom_headers", "") if first else "",
            "enable_backup_external_image_api": endpoint_complete(second),
            "backup_external_image_api_platform": second.get("platform", "auto") if second else "auto",
            "BACKUP_EXTERNAL_IMAGE_API_BASE_URL": second.get("base_url", "") if second else "",
            "BACKUP_EXTERNAL_IMAGE_API_KEY": second.get("api_key", "") if second else "",
            "BACKUP_EXTERNAL_IMAGE_API_MODEL": second.get("model", "") if second else "",
            "backup_external_image_api_size": second.get("size", "1024x1024") if second else "1024x1024",
            "backup_external_image_api_timeout_seconds": _safe_int(second.get("timeout_seconds"), 180, 20, 600) if second else 180,
            "backup_external_image_api_custom_headers": second.get("custom_headers", "") if second else "",
        }
        attr_map = {
            "external_image_api_platform": "external_image_api_platform",
            "EXTERNAL_IMAGE_API_BASE_URL": "external_image_api_base_url",
            "EXTERNAL_IMAGE_API_KEY": "external_image_api_key",
            "EXTERNAL_IMAGE_API_MODEL": "external_image_api_model",
            "external_image_api_size": "external_image_api_size",
            "external_image_api_timeout_seconds": "external_image_api_timeout_seconds",
            "external_image_api_custom_headers": "external_image_api_custom_headers",
            "enable_backup_external_image_api": "enable_backup_external_image_api",
            "backup_external_image_api_platform": "backup_external_image_api_platform",
            "BACKUP_EXTERNAL_IMAGE_API_BASE_URL": "backup_external_image_api_base_url",
            "BACKUP_EXTERNAL_IMAGE_API_KEY": "backup_external_image_api_key",
            "BACKUP_EXTERNAL_IMAGE_API_MODEL": "backup_external_image_api_model",
            "backup_external_image_api_size": "backup_external_image_api_size",
            "backup_external_image_api_timeout_seconds": "backup_external_image_api_timeout_seconds",
            "backup_external_image_api_custom_headers": "backup_external_image_api_custom_headers",
        }
        for key, value in updates.items():
            setattr(self, attr_map[key], value)
            self._set_image_api_config_value(key, value)

    def _image_api_command_status_text(self) -> str:
        endpoints = self._image_api_endpoint_queue_for_command()
        if endpoints:
            lines = [
                "在线生图 API 当前队列：",
                *[self._image_api_endpoint_status_line(endpoint, index) for index, endpoint in enumerate(endpoints[:12])],
                "切换优先级：陪伴 切换生图API（交换前两条）",
            ]
            return "\n".join(lines)
        enabled_backup = bool(runtime_persona_setting(self, 'enable_backup_external_image_api', False))
        return (
            "在线生图 API 当前配置：\n"
            f"{self._image_api_format_runtime_pair(backup=False)}\n"
            f"{self._image_api_format_runtime_pair(backup=True)}\n"
            f"备选自动兜底：{self._feature_on_text(enabled_backup)}\n"
            "切换主备：陪伴 切换生图API"
        )

    def _set_image_api_config_value(self, key: str, value: Any) -> bool:
        config = getattr(self, "config", None)
        if config is None:
            return False
        try:
            saved = _set_into_config(config, key, value, allow_flat_fallback=False)
        except TypeError:
            saved = _set_into_config(config, key, value)
        if not saved:
            saved = _set_into_config(config, key, value)
        return bool(saved)

    async def _swap_external_image_api_command_text(self, *, force: bool = False) -> str:
        endpoints = self._image_api_endpoint_queue_for_command()
        if endpoints:
            if len(endpoints) < 2:
                return "在线生图 API 队列少于 2 条，无法交换优先级。"
            second = endpoints[1] if isinstance(endpoints[1], dict) else {}
            missing = []
            if not bool(second.get("enabled", True)):
                missing.append("第二条 API 已关闭")
            if not str(second.get("base_url") or "").strip():
                missing.append("第二条 API 地址")
            if not str(second.get("api_key") or "").strip():
                missing.append("第二条 API Key")
            if not str(second.get("model") or "").strip():
                missing.append("第二条图片模型")
            if missing and not force:
                return (
                    "第二条在线生图 API 不可用，暂不切换："
                    + "、".join(missing)
                    + "\n需要先到拓展页补齐队列，或确认风险后使用：陪伴 切换生图API 强制"
                )
            changed = list(endpoints)
            changed[0], changed[1] = changed[1], changed[0]
            normalizer = getattr(self, "_normalize_external_image_api_endpoints", None)
            changed = normalizer(changed) if callable(normalizer) else changed
            self.external_image_api_endpoints = changed
            self._set_image_api_config_value("external_image_api_endpoints", changed)
            self._sync_legacy_image_api_config_from_command_endpoints(changed)
            if not await self._save_config_if_possible():
                self.external_image_api_endpoints = endpoints
                self._set_image_api_config_value("external_image_api_endpoints", endpoints)
                self._sync_legacy_image_api_config_from_command_endpoints(endpoints)
                return "在线生图 API 优先级交换失败：配置未能保存，已恢复原顺序。"
            return "已交换在线生图 API 队列前两项。\n" + self._image_api_command_status_text()

        pairs = (
            ("external_image_api_platform", "backup_external_image_api_platform", "external_image_api_platform", "backup_external_image_api_platform"),
            ("external_image_api_base_url", "backup_external_image_api_base_url", "EXTERNAL_IMAGE_API_BASE_URL", "BACKUP_EXTERNAL_IMAGE_API_BASE_URL"),
            ("external_image_api_key", "backup_external_image_api_key", "EXTERNAL_IMAGE_API_KEY", "BACKUP_EXTERNAL_IMAGE_API_KEY"),
            ("external_image_api_model", "backup_external_image_api_model", "EXTERNAL_IMAGE_API_MODEL", "BACKUP_EXTERNAL_IMAGE_API_MODEL"),
            ("external_image_api_size", "backup_external_image_api_size", "external_image_api_size", "backup_external_image_api_size"),
            ("external_image_api_timeout_seconds", "backup_external_image_api_timeout_seconds", "external_image_api_timeout_seconds", "backup_external_image_api_timeout_seconds"),
            ("external_image_api_custom_headers", "backup_external_image_api_custom_headers", "external_image_api_custom_headers", "backup_external_image_api_custom_headers"),
        )
        current: dict[str, Any] = {}
        for primary_attr, backup_attr, _, _ in pairs:
            current[primary_attr] = getattr(self, primary_attr, "")
            current[backup_attr] = getattr(self, backup_attr, "")

        backup_missing = []
        if not str(current.get("backup_external_image_api_base_url") or "").strip():
            backup_missing.append("备选在线 API 地址")
        if not str(current.get("backup_external_image_api_key") or "").strip():
            backup_missing.append("备选在线 API Key")
        if not str(current.get("backup_external_image_api_model") or "").strip():
            backup_missing.append("备选在线图片模型")
        if backup_missing and not force:
            return (
                "备选在线图片 API 未配置完整，暂不切换："
                + "、".join(backup_missing)
                + "\n需要先到拓展页填写备选 API，或确认风险后使用：陪伴 切换生图API 强制"
            )

        old_primary_complete = bool(
            str(current.get("external_image_api_base_url") or "").strip()
            and str(current.get("external_image_api_key") or "").strip()
            and str(current.get("external_image_api_model") or "").strip()
        )

        for primary_attr, backup_attr, primary_key, backup_key in pairs:
            primary_value = current.get(primary_attr)
            backup_value = current.get(backup_attr)
            if primary_attr.endswith("_platform"):
                normalizer = getattr(self, "_normalize_external_image_api_platform", None)
                if callable(normalizer):
                    primary_value = normalizer(primary_value)
                    backup_value = normalizer(backup_value)
            if primary_attr.endswith("_timeout_seconds"):
                primary_value = _safe_int(primary_value, 180, 20, 600)
                backup_value = _safe_int(backup_value, 180, 20, 600)
            setattr(self, primary_attr, backup_value)
            setattr(self, backup_attr, primary_value)
            self._set_image_api_config_value(primary_key, backup_value)
            self._set_image_api_config_value(backup_key, primary_value)

        old_backup_enabled = bool(runtime_persona_setting(self, 'enable_backup_external_image_api', False))
        self.enable_backup_external_image_api = old_primary_complete
        self._set_image_api_config_value("enable_backup_external_image_api", old_primary_complete)
        if not await self._save_config_if_possible():
            for primary_attr, backup_attr, primary_key, backup_key in pairs:
                setattr(self, primary_attr, current.get(primary_attr))
                setattr(self, backup_attr, current.get(backup_attr))
                self._set_image_api_config_value(primary_key, current.get(primary_attr))
                self._set_image_api_config_value(backup_key, current.get(backup_attr))
            self.enable_backup_external_image_api = old_backup_enabled
            self._set_image_api_config_value("enable_backup_external_image_api", old_backup_enabled)
            return "主/备在线生图 API 交换失败：配置未能保存，已恢复原配置。"
        return "已交换主/备在线生图 API。\n" + self._image_api_command_status_text()
